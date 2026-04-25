# ST → Ladder Bytecode: How Logix Stores Structured Text in ACD Files

## TL;DR

Rockwell Logix Designer compiles Structured Text (ST) routine bodies into a
**ladder-mnemonic intermediate representation** (Rockwell "Neutral Text") and
stores **that** as the executable form in `SbRegion.Dat`. The **original ST
source** — including comments, whitespace, and variable-name alignment — is
stored separately in `Nameless.Dat` as UTF-16LE text, one line per record.

Both forms are present in every ACD. The `.L5X` export and the Studio 5000 IDE
present the source form; the runtime uses the compiled form.

This is not documented in `hutcheb/acd` and is not obvious from the file
extensions. It was discovered empirically on `CUT_0_0_48.ACD`.

## Evidence

### ST source (user-visible, from Studio 5000)

```st
if isColletClamped then
    colletClampCount := colletClampCount + 1;
    if colletClampCount >= 2 then
        state := 420;
    else
        state := 405;
    end_if;
end_if;
```

### Compiled form found in `SbRegion.Dat` (five records, same `parent_id` 4148844970)

| record_index | language_type   | text (tag refs un-resolved)                    |
|--------------|-----------------|------------------------------------------------|
| 29954        | REGION NT       | `ADD(colletClampCount,1,colletClampCount);`    |
| 29955        | REGION LE UID   | `<UID: 0x98ADF1F7>`                            |
| 29956        | REGION NT       | `GEQ(colletClampCount,2) IRD() JMP(@eeb2522c@);` |
| 29957        | REGION LE UID   | `<UID: 0x0170BFFE>`                            |
| 29958        | REGION NT       | `MOV(420,state);`                              |

Note the use of ladder instructions (`ADD`, `GEQ`, `IRD`, `JMP`, `MOV`) even
though the source was ST. `IRD` (Immediate Rung Done) + `JMP(@hex@)` implements
the `if … then … end_if` control flow.

### Source form found in `Nameless.Dat` (one line per record)

Record #13195 (106 bytes, identifier `0xFAFA`):

```
 0: 66 00 00 00           record_length = 102
 4: 02 00 00 01           flags
 8: b7 d6 b8 e9           owning routine object_id = 0xE9B8D6B7
12: a8 a5 65 96
16: d1 07 02 00
20: 77 fa 04 00
24: ff fe ff 27           UTF-16LE BOM, flag byte 0xff, text length 0x27 (39 chars)
28: 09 00 09 00 40 00 …   UTF-16LE: "\t\t@a2558998@ \t\t:= -927.47;\t\t// 20251002"
```

The `20251002` in a `//` comment is what proved the file preserves original
source with comments intact.

## File roles summary

| File               | Role                                           | Encoding            | Per-routine granularity   |
|--------------------|------------------------------------------------|---------------------|---------------------------|
| `Comps.Dat`        | Routine/tag/program metadata, identifiers      | Mixed (UTF-8, LE)   | one record per object     |
| `SbRegion.Dat`     | Compiled Neutral Text (ladder instructions)    | UTF-16LE            | many records per routine  |
| `Nameless.Dat`     | **Original ST source text**                    | **UTF-16LE**        | **one record per source line** |
| `region_map.Dat`   | Maps SbRegion records → owning routines        | binary              | many rows per routine     |
| `Comments.Dat`     | Rung comments, tag descriptions                | UTF-16LE            | filtered by rung_content  |
| `TagInfo.XML`      | Tag types, scopes, initial values              | UTF-16LE XML        | whole-project             |

## Neutral Text instruction glossary (observed so far)

| Mnemonic           | Meaning                                          | ST equivalent            |
|--------------------|--------------------------------------------------|--------------------------|
| `MOV(src, dst)`    | Move/assign                                      | `dst := src;`            |
| `ADD(a, b, dst)`   | Add                                              | `dst := a + b;`          |
| `SUB(a, b, dst)`   | Subtract                                         | `dst := a - b;`          |
| `MUL(a, b, dst)`   | Multiply                                         | `dst := a * b;`          |
| `DIV(a, b, dst)`   | Divide                                           | `dst := a / b;`          |
| `GEQ(a, b)`        | Greater-or-equal predicate (rung input state)    | `a >= b`                 |
| `GRT(a, b)`        | Greater-than predicate                           | `a > b`                  |
| `LEQ(a, b)`        | Less-or-equal predicate                          | `a <= b`                 |
| `LES(a, b)`        | Less-than predicate                              | `a < b`                  |
| `EQU(a, b)`        | Equals predicate                                 | `a = b`                  |
| `NEQ(a, b)`        | Not-equal predicate                              | `a <> b`                 |
| `XIC(bit)`         | Examine If Closed (test bit true)                | `if bit then`            |
| `XIO(bit)`         | Examine If Open (test bit false)                 | `if not bit then`        |
| `IRD()`            | Immediate Rung Done — commits control state     | end of predicate region  |
| `JMP(@hex@)`       | Jump to label; label is hex-tagged              | `end_if;` / `else` branch |
| `JSR(@hex@)`       | Jump to subroutine                               | function call            |
| `DTOS(n, s)`       | DINT to String                                   | `s := DINT_TO_STRING(n);`|
| `TOS(n, s)`        | Number-to-String (float)                         | `s := REAL_TO_STRING(n);`|
| `RTO(…)`           | *(unconfirmed — seen once)*                      | ?                        |

Tag references appear as `@<8-hex-digits>@` placeholders and are resolved via
a reference table (see existing `SbRegionRecord._replace_tag_references`).

## Relevance to this project (`manusolve/acd`)

1. **ST export uses `Nameless.Dat`, not `SbRegion.Dat`.**
   This is handled in the PR on branch `feature/st-extraction-v1`.

2. **`SbRegion.Dat` remains the source of truth for ladder (RLL) routines.**
   Unchanged by this work.

3. **Reverse-engineering potential.** The `SbRegion.Dat` Neutral Text provides
   a verifiable compiled form of the ST source. A decompiler from Neutral Text
   back to ST would be useful for:
   - ACDs where `Nameless.Dat` is missing or corrupted.
   - Detecting drift between source and compiled form (compiler bug surface).
   - Lightweight diffing of "what actually runs" vs "what was written".

## Open research questions

### Q1 — Does every ST line have exactly one `Nameless.Dat` record?

Unclear whether multi-statement lines, continuation lines, or blank lines
generate separate records. Script idea:

```powershell
# Count Nameless.Dat records per routine, compare to line count of exported L5X ST body
```

### Q2 — Does Logix store intermediate forms for FBD and SFC similarly?

Function Block Diagram and Sequential Function Chart routines likely have a
bytecode form in `SbRegion.Dat` too, but the source-visual representation
(block positions, pin connections) must live elsewhere. Candidates:
`Nameless.Dat`, a separate `.Dat` not yet surveyed, or embedded in
`ProjectTemplate.ACD`.

Script idea: for a known FBD routine's `object_id`, query `nameless` and
`region_map` for records; decode `nameless` payload and look for XML/JSON/
visual layout hints.

### Q3 — `IRD()` and `JMP(@hex@)` control-flow semantics

The `@eeb2522c@` in `JMP(@eeb2522c@)` looks like a local jump label, not a tag
reference. Need to confirm:
- Are jump targets stored in the same reference table as tags?
- Do they resolve to a specific `SbRegion.Dat` record's object_id (allowing
  control-flow graph reconstruction)?
- Are `JMP` labels scoped per-routine or global?

Script idea: extract all `JMP(@hex@)` targets from one routine's SbRegion
records, then search `SbRegion.Dat` object_ids for matches.

### Q4 — Neutral Text grammar

Is there a published or leaked grammar? Known sources:
- Rockwell knowledgebase article 23341 ("Import/Export Neutral Text").
- The `PLCopen Pickup Guide` referenced in Logix help.
- Reverse-engineering from Studio 5000 `.L5X` import/export samples.

Building even a partial grammar would enable a Neutral Text parser as a
structured alternative to the current substring-based tag resolver.

### Q5 — Round-trip fidelity check

Take a Rockwell-exported L5X, re-import into Studio 5000 on a clean install,
save as ACD, then diff both `SbRegion.Dat` and `Nameless.Dat` against the
original. Any differences reveal where the "canonical" form is stored and
whether either file can be regenerated from the other.

### Q6 — Why `REGION LE UID` records exist

Records like `<UID: 0x98ADF1F7>` interleave with `REGION NT` records and
share the same `parent_id`. Hypothesis: per-statement edit-history IDs used
by Studio 5000's online edit-tracking feature. Confirming would enable:
- Reconstructing rung-by-rung change history from a single ACD.
- Ignoring them safely in export (current behaviour).

### Q7 — The `@hex@` namespace

Tag references (`@a2558998@`) and jump labels (`@eeb2522c@`) share the same
syntactic form but probably resolve in different tables. Script idea: for every
`@hex@` seen in `Nameless.Dat`, look up the hex as an object_id in `comps`
vs as a jump label; count resolution rate from each.

### Q8 — Comment preservation mechanism

The source form in `Nameless.Dat` keeps line comments (`//`) verbatim. Block
comments `(* ... *)` haven't been probed yet. Script idea: find a routine with
known block comments, inspect its `Nameless.Dat` records, confirm the entire
block sits in one record or spans multiple.

## Commands for further probing

All run against an already-extracted ACD at `C:\temp\acd-extracted\`.

### Count lines per routine

```python
import sqlite3
conn = sqlite3.connect('acd.db')  # the ExportL5x-created sqlite
cur = conn.cursor()
cur.execute("""
    SELECT c.comp_name, COUNT(n.object_id)
    FROM comps c
    JOIN nameless n ON n.object_id = c.object_id
    WHERE c.record_type = <ST routine type id>
    GROUP BY c.comp_name
    ORDER BY 2 DESC
""")
for name, n in cur.fetchall():
    print(f"{n:5d}  {name}")
```

### Extract all Neutral Text for one routine

```python
cur.execute("""
    SELECT s.rung
    FROM rungs s
    JOIN region_map rm ON rm.object_id = s.object_id
    WHERE rm.parent_id = (SELECT object_id FROM comps WHERE comp_name = 'Diverter')
    ORDER BY s.seq_number
""")
for (rung,) in cur.fetchall():
    print(rung)
```

### Search for un-decoded instruction mnemonics

```powershell
# Find every distinct "WORD(" prefix in SbRegion.Dat decoded text
C:\Python312\python.exe -c "
import re, sqlite3
conn = sqlite3.connect('acd.db')
mnemonics = set()
for (r,) in conn.execute('SELECT rung FROM rungs WHERE rung IS NOT NULL'):
    for m in re.finditer(r'([A-Z][A-Z0-9_]{1,5})\s*\(', r):
        mnemonics.add(m.group(1))
print(sorted(mnemonics))
"
```

Compare against the glossary above and add anything missing.

## References

- [Rockwell: ST language fundamentals](https://literature.rockwellautomation.com/idc/groups/literature/documents/pm/1756-pm007_-en-p.pdf)
- [Rockwell: L5X import/export reference](https://literature.rockwellautomation.com/idc/groups/literature/documents/rm/9324-rm003_-en-p.pdf)
- `hutcheb/acd` library source, esp. `acd/record/sbregion.py` and `acd/database/dbextract.py`
- Discovery PR: `manusolve/acd#<discovery-pr-number>`
- Implementation PR: `manusolve/acd#<extraction-pr-number>`