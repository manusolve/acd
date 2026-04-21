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
   - Parent routine name (looked up from `comps`)
   - Decoded text content (UTF-16-LE for `Rung NT` / `REGION NT`, with
     `@HEX@` tag-reference placeholders resolved to real names)
   - Raw hex preview of the record buffer (first 64 bytes)
4. Prints a summary table of record counts per `language_type`.

### Usage

```bash
# Basic — dump all records to stdout
python tools/inspect_sbregion.py --acd path/to/project.ACD

# Filter to a single routine by name
python tools/inspect_sbregion.py --acd project.ACD --routine MainRoutine

# Save the full record list as JSON (useful for offline analysis)
python tools/inspect_sbregion.py --acd project.ACD --json sbregion_dump.json

# Limit console output (still writes all records to --json if specified)
python tools/inspect_sbregion.py --acd project.ACD --limit 50

# Combine options
python tools/inspect_sbregion.py --acd project.ACD --routine Main --json out.json --limit 20
```

### Example output

```
============================================================
SbRegion.Dat  —  312 record(s)
  Rung NT                         200
  REGION NT                        80
  REGION AST                       20
  REGION LE UID                    12
============================================================

--- Record #0 ---
  identifier   : 0xFAFA (64250)
  language_type: Rung NT
  object_id    : 1234567
  parent_id=9876543 (MainRoutine)
  buffer_length: 128
  text         : 'XIC(MyTag)OTE(Output);'
  raw_hex      : 58 00 49 00 43 00 28 ...

--- Record #1 ---
  ...
```

### Notes

- This script is **pure inspection** — it reads files but never modifies them.
- The temporary directory is created with `tempfile.mkdtemp` and removed
  automatically when the script exits, regardless of errors.
- The `--json` output includes all fields for every record, including the raw
  hex preview, making it easy to diff dumps across ACD versions.
