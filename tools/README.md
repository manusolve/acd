# tools/

Developer utilities for inspecting and diagnosing ACD project files.

---

## `inspect_sbregion.py`

A standalone read-only diagnostic CLI that surfaces everything stored in
`SbRegion.Dat` for a given ACD project.  It is the foundation for
understanding what the binary format looks like for non-ladder (ST, FBD, SFC)
routines so that future work can extend `SbRegionRecord.parse()` and
`RoutineBuilder.build()` to emit populated `<STContent>` sections.

### What it does

1. Extracts the ACD databases to a temporary directory using the library's
   existing `ExtractAcdDatabase` / `Unzip` stack (cleaned up automatically on
   exit).
2. Builds the same minimal SQLite state that `ExportL5x` builds — `comps`,
   `region_map`, and `rungs` tables — by calling `ExportL5x` directly so that
   no parser logic is duplicated.
3. Enumerates **every** record in `SbRegion.Dat` via the `DbExtract` /
   `FafaSbregions` Kaitai-generated parser and, for each record, reports:
   - Record index and identifier (decimal + hex)
   - `language_type` string from the record header
   - `object_id` and `parent_id` (linked via `region_map`)
   - Parent routine name and type (looked up from `comps`)
   - Decoded text content (UTF-16-LE for `Rung NT` / `REGION NT`, with
     `@HEX@` tag-reference placeholders resolved to real names)
4. Prints two summary tables **at the end of output** so they are always
   visible without scrolling.

### Default output (quiet mode)

Without flags the tool prints a one-line header, **one sample record per
unique `language_type`**, any `--routine` matches, then the summary tables:

```
Inspected 1824 records from project.ACD (quiet mode; pass --verbose for per-record dump)

--- Sample for language_type='Rung NT' ---
  ...

=== Summary by language_type ===
  language_type              records  parent routine types
  -------------------------  -------  -------------------------------
  Rung NT                       1423  {RLL: 1423}
  ST NT                          387  {ST: 387}
  REGION NT                        2  {RLL: 2}
  <unmapped>                      12  {: 12}
  ===============================================================
  Total: 1824 records

=== Summary by parent routine type ===
  routine type       routines  sbregion records  empty?
  -----------------  --------  ----------------  ------
  RLL                      54              1425  no
  ST                       12               387  no
  FBD                       3                 0  YES
  SFC                       1                 0  YES
  -----------------  --------  ----------------  ------
  Total routines: 70
```

The **`empty?`** column in the second table is critical: it reveals whether a
routine type has *zero* SbRegion records associated, which would mean its body
lives in a different `.Dat` file not yet parsed.

### Usage

```
python tools/inspect_sbregion.py --acd path/to/project.ACD [options]
```

| Flag | Default | Description |
|---|---|---|
| `--acd PATH` | *(required)* | Path to the `.ACD` project file |
| `--verbose` | off | Dump every record up to `--limit` instead of quiet mode |
| `--limit N` | `10` | Max records shown in `--verbose` mode (0 = no limit) |
| `--routine NAME` | — | Show **all** records for the named routine (always, even in quiet mode) |
| `--json FILE` | — | Write the full record list to a JSON file (comprehensive, not reduced) |

### Typical workflow

```powershell
# Quick scan — 2 summary tables in < 50 lines of output
python tools\inspect_sbregion.py --acd C:\Projects\CUT.ACD

# Inspect one specific routine
python tools\inspect_sbregion.py --acd C:\Projects\CUT.ACD --routine MainRoutine

# Full dump capped at 20 records, plus JSON for programmatic analysis
python tools\inspect_sbregion.py --acd C:\Projects\CUT.ACD --verbose --limit 20 --json out.json
```

### Requirements

Install the library in editable mode from the repo root:

```powershell
C:\Python312\python.exe -m pip install --user -e .
```
