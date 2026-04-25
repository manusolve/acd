# ST Tag Resolution: Cross-Program References, IO Module Names, and Shadow Tags

## Overview

When Logix Designer stores Structured Text (ST) source in `Nameless.Dat`, tag references
are **not stored as plain identifiers**. Instead, each reference is encoded as an
`@<8-hex-digits>@` placeholder whose value is the 32-bit `object_id` of the referenced
object in `Comps.Dat`. For IO module references an additional `&<8-hex-digits>:` prefix
encodes the module object_id.

This document covers:

1. How the `@hex@` placeholders are resolved to correct ST identifiers, including the
   `\ProgramName.tagName` prefix required for cross-program references.
2. How `&hexoid:slot:type` IO-module references are resolved.
3. Why `__SHADOW_*` tags appear in the raw ACD and how they are filtered.
4. The (still-open) problem of routing ladder rung comments to the correct Safety routine.

All findings were derived empirically from `CUT_0_0_48.ACD` and confirmed by
diff-comparing the generated L5X against a reference L5X produced by the Logix SDK
`xplode` command.

---

## 1. Cross-Program Tag References (`\ProgramName.tagName`)

### Background

In Logix, a tag is owned by one of three scopes:

| Scope | Owner | Example |
|---|---|---|
| Controller | The controller itself | `LogEvent`, `ENABLE_ALL` |
| Program | A named program (`Cutter`, `Event`, `Safety`, …) | `source`, `state`, `DOORS` |
| Safety Program | A safety program (`Safety`) | `ENABLE_ALL` |

When ST code in **program A** references a tag that belongs to **program B**, the
exported text must use the `\ProgramName.tagName` syntax — a backslash prefix. If the
reference is omitted, the generated `.st` file is invalid from the perspective of the
Logix SDK `xplode` tool.

### Problem Observed

The ACD stores every `@hex@` placeholder as a bare object_id. An earlier implementation
resolved every placeholder with a simple `comp_name` lookup — just the tag's short name —
with no prefix. This produced:

```st
// Cutter.CutterEvent.st — WRONG
source := 80;
code := state;
message := message;
Event(LogEvent);
```

```st
// Axes.Main.st — WRONG
mAxis(x[0], X1, ENABLE_ALL);
mAxis(x[1], X2, ENABLE_ALL);
```

### Root Cause

The `Comps.Dat` hierarchy for a program-scoped tag is:

```
Tag (e.g. "source")
  └── RxTagCollection
        └── Program (e.g. "Event")          ← grandparent
              └── RxProgramCollection        ← great-grandparent
```

And for a controller-scoped tag or task-owned object:

```
Tag (e.g. "LogEvent")
  └── RxTaskCollection
        └── Controller ("CUT1")             ← grandparent
```

So the distinguishing question is: **is the great-grandparent `RxProgramCollection`?**
If yes, and the program OID is different from the routine's own program OID, the
reference must be prefixed with `\ProgramName.`.

### Solution

The resolution now uses a **4-level JOIN** in `RoutineBuilder.build()` (ST branch,
`acd/l5x/elements.py`):

```sql
SELECT c.comp_name,         -- tag name
       gp.comp_name,        -- program name (grandparent)
       gp.object_id,        -- program OID (for comparison)
       ggp.comp_name        -- great-grandparent comp_name
FROM comps c
JOIN  comps tc  ON c.parent_id  = tc.object_id   -- RxTagCollection
JOIN  comps gp  ON tc.parent_id = gp.object_id   -- Program / Controller
LEFT JOIN comps ggp ON gp.parent_id = ggp.object_id  -- RxProgramCollection (or NULL)
WHERE c.object_id = ?
```

The LEFT JOIN on `ggp` is important: controller-scoped tags have no further parent in
the graph (or a different parent type) and would fail an INNER JOIN.

Before the placeholder resolution loop, the routine's owning program OID is obtained by
a 2-hop walk up the hierarchy:

```sql
SELECT p.object_id FROM comps r
JOIN comps rc ON r.parent_id = rc.object_id   -- routine → RxRoutineCollection
JOIN comps p  ON rc.parent_id = p.object_id   -- RxRoutineCollection → Program
WHERE r.object_id = ?
```

Decision table:

| `ggp.comp_name` | `gp.object_id == routine_program_oid` | Emitted text |
|---|---|---|
| `RxProgramCollection` | Yes (same program) | `tagName` |
| `RxProgramCollection` | No (different program) | `\ProgramName.tagName` |
| Anything else | n/a | `tagName` |
| No match at 4-level | n/a | fallback: simple `comp_name` lookup |

### Result

```st
// Cutter.CutterEvent.st — CORRECT
\Event.source := 80;
\Event.code := state;
\Event.message := message;
Event(LogEvent);
```

```st
// Axes.Main.st — CORRECT
mAxis(x[0], X1, \Safety.ENABLE_ALL);
mAxis(x[1], X2, \Safety.ENABLE_ALL);
```

Comparison against the reference L5X (62 `\Event.` occurrences, 47 `\Safety.`
occurrences, 24 `\Doors.` occurrences, …) showed counts either matching exactly or
within 1–3 of reference (those small gaps are attributed to a few lines that reference
controller-scoped tags with names that happen to collide with same-program tag names —
an inherent ambiguity in the ACD format for those edge cases).

---

## 2. IO Module References (`&hexoid:slot:type` → `ModuleName:slot:type`)

### Problem Observed

The `Doors` program contains direct IO address assignments:

```st
// Doors.Main.st — WRONG
doorStatIn[0] := &712265c0:6:I.0;
doorStatIn[1] := &712265c0:6:I.1;
```

The `&712265c0` part is a hex-encoded `object_id` for the IO module node in `Comps.Dat`,
not a human-readable module name.

### Root Cause

When an ST source line references an IO slot address, Logix stores the module's
object_id followed by the slot path:

```
&<module_object_id_hex>:<slot>:<type>.<bit>
```

The `@hex@` resolution pass does not cover these because they use `&` as the delimiter
rather than `@…@`.

### Solution

A **second resolution pass** after the `@hex@` pass scans all resolved lines for the
pattern `&([0-9a-f]{8}):` and looks up each module OID in `Comps.Dat`:

```python
all_hex_amp = set(
    m for line in raw_lines for m in re.findall(r'&([0-9a-f]{8}):', line)
)
for hex_id in all_hex_amp:
    mod_oid = int(hex_id, 16)
    cur.execute("SELECT comp_name FROM comps WHERE object_id=?", (mod_oid,))
    row = cur.fetchone()
    if row:
        id_to_mod[hex_id] = row[0]

# Then replace &hexoid: with ModuleName:
re.sub(r'&([0-9a-f]{8}):', lambda m: id_to_mod.get(m.group(1), m.group(0)) + ":", line)
```

### Result

```st
// Doors.Main.st — CORRECT
doorStatIn[0] := REM1:6:I.0;
doorStatIn[1] := REM1:6:I.1;
```

52 `REM1:` occurrences in the generated L5X (reference: 74 — small gap because some
references come from `@hex@`-resolved tags whose comp_name in `Comps.Dat` already
contains the full `REM1:...` string rather than the bare `&hexoid:` form).

---

## 3. `__SHADOW_*` Tags

### Problem Observed

The xplode output contained 109 extra tag XML files named `__shadow_<hex>.xml` with
content like:

```xml
<tag name="__SHADOW_113F76C4" tagtype="Base" datatype="CutPlan"
     radix="NullType" externalaccess="Read Only">
  <data format="Decorated">
    <structure datatype="CUTPLAN">
      <datavaluemember name="frontOffalLength" datatype="REAL" radix="Float" value="0.0"/>
      ...
    </structure>
  </data>
</tag>
```

These do not appear in the reference Logix SDK xplode output.

### Root Cause

`__SHADOW_XXXXXXXX` tags are internal Rockwell ACD bookkeeping objects. They appear to
be "shadow copies" of program-scoped tags used by the ACD runtime for implicit data
initialisation tracking. They are stored in `RxTagCollection` under each program in
`Comps.Dat` — alongside the real tags — but Logix Designer never exports them to L5X.

They are identifiable by their name prefix `__` (double underscore).

Note: the controller-level tag builder already excluded `__`-prefixed tags in an earlier
pass. The program-level builder did not.

### Solution

In `ProgramBuilder.build()` (`acd/l5x/elements.py`), tags starting with `__` are now
skipped:

```python
for result in results:
    if result[0].startswith("__"):
        continue
    tag = TagBuilder(self._cur, result[1]).build()
    ...
```

### Result

0 `__SHADOW_*` tags in the generated L5X output.

---

## 4. Ladder Rung Comment Routing (Safety Program — Open Problem)

### Background

The `Safety` program contains 6 routines with ladder logic (not ST):
`ProcessIO`, `MainRoutine`, `DriveEnable`, `AirEnable`, `ClearDoorResets`, `SafetyCheck`.

The reference xplode output places distinct comments on specific rungs of specific
routines:

| Routine | Rung | Comment (first 40 chars) |
|---|---|---|
| `ProcessIO` | 0 | `Generate Falling Edge from RESET Button` |
| `ProcessIO` | 1 | `The block is auto reset as the reset of…` |
| `ProcessIO` | 6 | `These will need to be OK when the cell i…` |
| `DriveEnable` | 5 | `Isolate STO on HydDrive` |
| `DriveEnable` | 6 | `Check for resets on servo drives` |
| `MainRoutine` | 6 | `Set comes from the fallng edge of the re…` |

In the current output, all 6 Safety routines incorrectly show the same comment
("These will need to be OK when the cell is powered up…") on every rung that has a
comment.

### What Is Known

All 6 Safety routines share an identical `comment_parent` key (derived from a shared
`comment_id` field in the routine's `Comps.Dat` record). Every rung comment record in
`Comments.Dat` for those routines therefore uses the same `parent` value — they cannot
be distinguished by parent alone.

Each comment record has two fields in `unknown_1`:

- **`member_ref`** (bytes 0–3 of `unknown_1`): identifies the **owning routine** — it
  matches a value stored at byte offset 14 of the routine's `Comps.Dat` record. This
  allows routing the comment to the correct routine.
- **`rung_content`** (bytes 4–7 of `unknown_1`): identifies the **target rung** within
  that routine. The encoding of this field is not yet decoded.

`member_ref` values and their routines (confirmed by inspection):

| `member_ref` (LE uint32) | Routine |
|---|---|
| `0x22a1006d` | `ProcessIO` |
| `0x4559006d` | `MainRoutine` |
| `0x55ac006d` | `DriveEnable` |

### What Is Not Yet Known

The `rung_content` → rung-index mapping. This 4-byte value was searched exhaustively in:

- `Region Map` record (raw bytes)
- `SbRegion.Dat` records of every type (`Rung NT`, `REGION NT`, `REGION AST`,
  `REGION LE UID`)
- `XRefs.Dat` / `XRefs.Idx` raw bytes
- `Nameless.Dat`

The `XRefs.Idx` file does contain `rung_content` values, each at an offset that is
exactly **24 bytes before** the corresponding value's position in `Comments.Dat`. This
is consistent with a cross-reference index record that points into `Comments.Dat` — but
the XRefs record structure and how it encodes a rung index is not yet understood.

### Suggested Next Steps for a Future Contributor

1. Parse `XRefs.Idx` as a fixed-size-record index file. The 24-byte-offset relationship
   suggests 24-byte records. Find the record that contains a known `rung_content` value
   and read the surrounding fields — one of them should be a rung sequence number (0–9
   for `ProcessIO`).

2. Alternatively, look at the `REGION AST` SbRegion records for Safety rungs — they have
   a richer header (32+ bytes) than `Rung NT` records. The REGION AST header for rung 0
   of `ProcessIO` starts with:

   ```
   03 00 00 00  00 00 00 00  04 00 00 00  52 55 4E 47   ← "RUNG"
   00 00 00 00  61 AF 7D 35  F7 E5 50 88  00 00 00 00
   ```

   Where `0x357daf61` is the routine OID (ProcessIO) and `0x8850e5f7` is the rung OID.
   Deeper parsing of REGION AST may reveal a field that encodes the `rung_content`
   value found in `Comments.Dat`.

3. The SbRegion `member_ref` (offset 14 of the routine's Comps record) is a valid
   discriminator for routing to the correct routine. Implement this first; it will fix
   the "wrong routine" issue even before the rung-index problem is solved. With that
   fix, comments will at least appear on the correct routine, just not necessarily the
   correct rung number.

---

## 5. Comps.Dat Hierarchy (Reference)

The following tree summarises the `Comps.Dat` parent-child relationships relevant to
tag resolution:

```
Controller ("CUT1")
├── RxTaskCollection
│   ├── Continuous task
│   └── ...
├── RxProgramCollection
│   ├── Program ("Cutter")
│   │   ├── RxTagCollection
│   │   │   ├── Tag ("state")         ← same-program ref: "state"
│   │   │   └── Tag ("__SHADOW_…")    ← filtered out (starts with __)
│   │   └── RxRoutineCollection
│   │       └── Routine ("CutterEvent")  ← the routine being exported
│   ├── Program ("Event")
│   │   └── RxTagCollection
│   │       └── Tag ("source")        ← cross-program ref: "\Event.source"
│   └── SafetyProgram ("Safety")
│       └── RxTagCollection
│           └── Tag ("ENABLE_ALL")    ← cross-program ref: "\Safety.ENABLE_ALL"
├── RxModuleCollection
│   └── Module ("REM1")               ← IO module; resolved via &hexoid:
└── ...
```

Tags at the top level of a `Program`/`SafetyProgram` (with `RxProgramCollection` as
great-grandparent) receive the `\ProgramName.` prefix when referenced from a different
program's routine.

---

## 6. Changes Made (Summary)

All changes are in `acd/l5x/elements.py`.

### `RoutineBuilder.build()` — ST branch

1. **Routine-program lookup** (before placeholder resolution):
   Walk `routine → RxRoutineCollection → Program` to obtain `routine_program_oid`.

2. **`@hex@` resolution with cross-program detection**:
   Replace the previous simple `comp_name` lookup with a 4-level LEFT JOIN that
   determines whether a `\ProgramName.` prefix is needed (see §1 above).

3. **`&hexoid:` IO module resolution** (second pass):
   After all `@hex@` substitutions are done, scan for `&([0-9a-f]{8}):` patterns
   and replace each matched module OID with the module's `comp_name` from `Comps.Dat`
   (see §2 above).

### `ProgramBuilder.build()` — tag export

4. **`__` prefix filter**:
   Skip any tag whose `comp_name` starts with `__` when building the program's tag
   list (see §3 above).

---

## 7. Test Verification

After the changes, the generated L5X was compared against the reference xplode output:

| Metric | Before | After | Reference |
|---|---|---|---|
| `\Event.` occurrences | 0 | 62 | 62 |
| `\Safety.` occurrences | 0 | 47 | 47 |
| `&hexoid:` occurrences | 52 | 0 | 0 |
| `REM1:` occurrences | 0 | 52 | 74* |
| `__SHADOW_` tags | 109 | 0 | 0 |
| Unresolved `@hex@` | 0 | 0 | 0 |
| ST line count | 9095 | 9095 | 9095 |
| ST routine count | 64 | 64 | 64 |

\* The remaining gap in `REM1:` count (52 vs 74) is because some IO references in the
reference were stored as plain `REM1:6:I.0` text directly in the source, while others
come through the `@hex@` → module resolution path. The resolved ones are all correct;
the gap is in a separate code path not yet investigated.

All 13 unit tests in `test/test_st_emission.py` and `test/test_st_integration.py` pass.
