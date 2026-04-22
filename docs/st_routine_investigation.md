# ST Routine Investigation

## Background

Structured Text (ST) routines in Rockwell Studio 5000 `.ACD` files were not
exported during L5X generation.  The exported XML contained empty
`<Routine Type="ST"/>` elements.  This document records the reverse-engineering
investigation that located the source text and the implementation that followed.

## Discovery

### What was tried first

The SbRegion.Dat file, which holds ladder (RLL) rung text, was the first
candidate.  It does contain compiled Neutral Text records for ST routines (e.g.
`MOV(50.0,@c19c693c@);`), but these are the **compiled** form, not the
original human-readable ST source.  They are not suitable as the export output.

### Where the source actually lives

A PowerShell search across all extracted ACD component files for source-unique
strings (`frontOffalLength`, `20251002`, `// param`, `standard value for
default`) found matches in **`Nameless.Dat`**:

```
HIT: 'frontOffalLength' as UTF-16BE in Nameless.Dat
HIT: '20251002'          as UTF-16BE in Nameless.Dat
HIT: '// param'          as UTF-16BE in Nameless.Dat
HIT: 'standard value for default' as UTF-16BE in Nameless.Dat
```

*(The "UTF-16BE" label was a search artefact — the file was searched as a flat
byte stream interpreted as big-endian.  The actual encoding is UTF-16LE with a
per-record BOM.  See format details below.)*

## Record Format

ST source text is stored in `Nameless.Dat` — one record per source line — with
the following layout.  Offsets are relative to the start of `record_buffer`
(i.e. after the 6-byte DAT container header: 2-byte `fa fa` identifier + 4-byte
length).

```
offset  bytes                 meaning
------  --------------------  ----------------------------------------
  0- 3  XX XX XX XX           record_length (LE uint32, includes header)
  4- 7  XX XX XX XX           flags (ignore)
  8-11  XX XX XX XX           object_id (LE uint32) — OWNING ROUTINE's object_id
 12-15  XX XX XX XX           secondary id (ignore)
 16-19  XX XX XX XX           type/flags (ignore)
 20-23  XX XX XX XX           sub-length (ignore)
 24-25  FF FE                 UTF-16LE BOM
    26  FF                    flag byte
    27  NN                    text length in UTF-16 code units (1 byte, 0–255)
  28+   XX XX ... XX XX       UTF-16LE text (2 × NN bytes)
```

### Concrete example

Record from `CUT_0_0_48.ACD` containing the line
`\t\t@a2558998@ \t\t:= -927.47;\t\t// 20251002` (39 code units, 106 bytes
total):

```
 0: 66 00 00 00   ← len=102 (+ 4-byte field itself = 106 in file)
 4: 02 00 00 01
 8: b7 d6 b8 e9   ← object_id = 0xE9B8D6B7 (owning ST routine)
12: a8 a5 65 96
16: d1 07 02 00
20: 77 fa 04 00
24: ff fe ff 27   ← BOM, flag, 0x27 = 39 chars
28: 09 00 09 00 40 00 61 00 32 00 ...   ← UTF-16LE "\t\t@a2558998@..."
```

## Database mapping

`nameless.Dat` records are loaded into the SQLite `nameless` table with schema:

```sql
CREATE TABLE nameless(object_id int, parent_id int, record BLOB NOT NULL)
```

The columns are populated as follows:

| column    | source                                    |
|-----------|-------------------------------------------|
| object_id | bytes 12–15 of record_buffer (secondary)  |
| parent_id | bytes  8–11 of record_buffer (owning ID)  |
| record    | entire record_buffer blob                 |

**`nameless.parent_id` is the owning routine's `object_id`** and joins directly
to `comps.object_id`.  To retrieve all source lines for a routine:

```sql
SELECT record FROM nameless WHERE parent_id = <routine_object_id>
```

Row insertion order is preserved by SQLite, which matches the file order
produced by `DbExtract`, which matches the original source line order.

## Tag references

Tag-ID placeholders in the source text use the format `@xxxxxxxx@` (8 hex
digits surrounded by `@`), identical to the format used in `SbRegion.Dat`
ladder records.  They are resolved by looking up `comps.comp_name` for the
matching `comps.object_id`.

## SbRegion.Dat records for ST routines

`SbRegion.Dat` does contain records associated with ST routines — for example,
`MOV(50.0,@c19c693c@);`.  These are the **compiled Neutral Text** form of the
routine, used internally by the PLC runtime.  They are **not** used as the ST
source output in the L5X export.  They remain useful as an audit trail but this
implementation does not emit them.

## Resolution

The following code changes implement ST routine source export:

### `acd/record/nameless.py`

Added `NamelessRecord.parse_source_line(record_bytes)` static method that
checks the `ff fe ff` BOM+flag pattern at offset 24, reads the 1-byte char
count at offset 27, and decodes the UTF-16LE text from offset 28.

### `acd/l5x/st_routine.py` (new)

`StRoutineBuilder(cur, routine_object_id).build()` queries
`nameless WHERE parent_id = ?`, filters records through `parse_source_line`,
resolves `@hex@` tag references, and returns an ordered list of source lines.

### `acd/l5x/elements.py`

* `Routine` dataclass gained a `_st_source: Optional[List[str]]` field.
* `RoutineBuilder.build()` calls `StRoutineBuilder` when `routine_type == "ST"`.
* `Routine.to_xml()` emits a populated `<STContent>` block when `_st_source` is
  non-empty, with one `<Line Number="N">` element per source line.

## Non-goals

* FBD and SFC routines are out of scope; they likely use a similar mechanism but
  require separate investigation.
* The RLL (ladder) code path is unchanged.
* No new runtime dependencies were added.

## Manual verification

Test the output against `CUT_0_0_48.ACD`.  Routines like `CutInFeed`,
`Diverter`, `GenCutPlan`, and `Measure` should each produce non-empty
`<STContent>` blocks in the generated L5X.  Compare structurally (not
byte-for-byte) against Rockwell's hand-exported reference L5X.
