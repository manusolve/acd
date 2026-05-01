
![PyPI](https://img.shields.io/pypi/v/acd-tools?label=acd-tools)
![PyPI - Downloads](https://img.shields.io/pypi/dm/acd-tools)
![ACD Tools](https://github.com/hutcheb/acd/actions/workflows/acd-tools.yml/badge.svg)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=hutcheb_acd&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=hutcheb_acd)

## Rockwell ACD Project File Tools

The Rockwell `.ACD` file is an archive file that contains all the files used by RSLogix / Studio 5000 Logix Designer. It consists of version text files, compressed XML metadata, and several proprietary binary database files (`Comps.Dat`, `SbRegion.Dat`, `Comments.Dat`, `Nameless.Dat`).

This library parses those binary databases and exposes the project contents — controller tags, programs, ladder rungs, data types (UDTs), add-on instructions (AOIs), and hardware modules — as Python objects. It can also serialise the parsed project back to an **L5X XML file** that Studio 5000 can import.

> **Compatibility** — Tested against Studio 5000 firmware versions 20–35. Python 3.8+ is supported; Python 3.12+ is recommended.

---

### Installing

```bash
pip install acd-tools
```

---

### Quick start — parse an ACD file

```python
from acd.api import ImportProjectFromFile

project = ImportProjectFromFile("MyController.ACD").import_project()
controller = project.controller

# Basic controller info
print(controller._name)           # controller name
print(controller.serial_number)   # e.g. "16#AB12_3456"
print(controller.modified_date)

# Iterate controller-scoped tags
for tag in controller.tags:
    print(f"  {tag.name}  ({tag.data_type})  — {tag._comments}")

# Walk programs -> routines -> ladder rungs
for program in controller.programs:
    print(f"\nProgram: {program._name}")
    for routine in program.routines:
        print(f"  Routine: {routine._name}  [{routine.type}]")
        for i, rung in enumerate(routine.rungs):
            print(f"    Rung {i}: {rung}")

# Inspect user-defined data types
for udt in controller.data_types:
    member_names = [m.name for m in udt.members]
    print(f"UDT {udt.name}: {member_names}")

# Inspect add-on instructions
for aoi in controller.aois:
    print(f"AOI {aoi._name}: {len(aoi.routines)} routines, {len(aoi.tags)} params")

# Inspect hardware modules
for module in controller.map_devices:
    print(f"Module {module._name}: vendor={module.vendor_id} "
          f"type={module.product_type} code={module.product_code} slot={module.slot_no}")
```

---

### Load, modify and save an ACD file

`load_acd` / `save_acd` / `patch_rungs` provide a round-trip workflow for reading and writing ACD
files directly, without going through the L5X intermediate format.

**Load a project into memory:**

```python
from acd.api import load_acd, save_acd, patch_rungs

project = load_acd("MyController.ACD")
controller = project.controller
```

`load_acd` extracts and parses the ACD archive in a temporary directory (cleaned up automatically)
and returns an `RSLogix5000Content` object with the full controller tree.

**Modify ladder rung logic:**

```python
# Find the rung you want to change
routine = controller.programs[0].routines[0]
print(routine._rung_ids)   # list of integer object_ids, one per rung

# Build a change map and apply it
changes = {routine._rung_ids[0]: "XIC(MyTag)OTE(OutputTag);"}
patch_rungs(project, changes)
```

`patch_rungs` rewrites the rung text inside the in-memory `SbRegion.Dat` binary blob.  Tag names
in the new rung text are written as plain identifiers; the library resolves them back to the
internal `@hex@` object-ID placeholders automatically.

> **Note** — `_rung_ids` is an internal attribute on `Routine` that exposes the ordered list of
> rung object IDs needed by `patch_rungs`.  There is currently no higher-level public accessor.

**Write the modified project back to an ACD file:**

```python
save_acd(project, "MyController_modified.ACD")
```

The output ACD is byte-for-byte identical to the original for every embedded file that was not
modified, so Studio 5000 can open it normally.

---

### Explode ACD to a version-control-friendly folder tree

`ExplodeAcdToTree` (backed by `acd.l5x.xplode.xplode`) mirrors the directory structure
produced by the Rockwell Logix SDK `xplode` command.  Every logical element — DataType,
Module, Tag, Program, Routine, AOI, Task — is written to its own file, making the output
directly `git diff`-able across firmware versions.

> **Note** — This feature is currently on the `feat/fix-l5x-bonefide-serialization-gaps`
> branch and has not yet been merged to `main`.

```python
from acd.api import ExplodeAcdToTree

ExplodeAcdToTree("MyController.ACD", "myproject-tree/").extract()
```

The resulting tree looks like:

```
myproject-tree/
└── RSLogix5000Content/
    ├── RSLogix5000Content.xml          # controller stub (empty collections)
    ├── export-options.yaml
    ├── DataTypes/
    │   └── {TypeName}.xml
    ├── Modules/
    │   └── {ModuleName}.xml
    ├── Tags/
    │   └── {TagName}.xml               # controller-scoped tags
    ├── Tasks/
    │   └── {TaskName}.xml
    ├── AddOnInstructionDefinitions/
    │   └── {AoiName}/
    │       ├── {AoiName}.xml           # AOI stub (Parameters + LocalTags)
    │       └── Routines/
    │           ├── {RoutineName}.st    # ST routines as plain text
    │           └── {RoutineName}.xml   # RLL / other routines as XML
    └── Programs/
        └── {ProgramName}/
            ├── {ProgramName}.xml       # program stub (empty Tags + Routines)
            ├── Tags/
            │   └── {TagName}.xml       # program-scoped tags
            └── Routines/
                ├── {RoutineName}.st
                └── {RoutineName}.xml
```

ST routines are written as plain `.st` text files (raw source lines, no XML wrapper) so
they produce clean `git diff` output.  All other elements are written as pretty-printed XML.
Rung `Number` attributes are omitted to match the SDK `xplode` format.

Volatile timestamps (`ExportDate`, `LastModifiedDate`) are stripped from the root XML so
they do not create noise in version-controlled diffs.

You can also call the underlying function directly:

```python
from acd.api import load_acd
from acd.l5x.xplode import xplode

project = load_acd("MyController.ACD")
xplode(project, "myproject-tree/")
```

---

### Convert ACD to L5X

Export the parsed project as an L5X XML file (importable by Studio 5000):

```python
from acd.api import ConvertAcdToL5x

ConvertAcdToL5x("MyController.ACD", "MyController.L5X").extract()
```

The output is pretty-printed by default. Pass `pretty_print=False` for a compact single-line file:

```python
ConvertAcdToL5x("MyController.ACD", "MyController.L5X", pretty_print=False).extract()
```

> **Note** — The L5X serialisation captures tags, programs, routines (ladder rungs **and** Structured Text
> source), UDTs, AOIs, and hardware modules.  The ST source export resolves all internal `@hex@`
> object-ID placeholders to real tag names, including cross-program `\ProgramName.tagName` prefixes and
> IO-module address references (`ModuleName:slot:type`).  Internal `__SHADOW_*` bookkeeping tags are
> filtered out so the output matches what the Rockwell Logix SDK `xplode` tool produces.
>
> Hardware module metadata (catalog numbers, connection parameters) is not fully round-tripped because
> Rockwell stores those as opaque CIP identity records in the binary database rather than as strings.

---

### Extract raw database files

Unzip all embedded files (`.Dat`, `.XML`, etc.) to a directory for inspection:

```python
from acd.api import ExtractAcdDatabase

ExtractAcdDatabase("MyController.ACD", "output/").extract()
# output/ now contains Comps.Dat, SbRegion.Dat, Comments.Dat,
#   Nameless.Dat, QuickInfo.XML, TagInfo.XML, XRefs.Dat, ...
```

---

### Extract raw database records to files

Save every individual binary record from the Comps database as its own file,
useful for reverse-engineering the record format:

```python
from acd.api import ExtractAcdDatabaseRecordsToFiles

ExtractAcdDatabaseRecordsToFiles("MyController.ACD", "output/").extract()
```

---

### Dump Comps database as a navigable folder tree

Writes the entire Comps database as a directory tree where each node is a `.dat` file.
A log file records the CIP class and instance for each record:

```python
from acd.api import DumpCompsRecordsToFile

DumpCompsRecordsToFile("MyController.ACD", "output/").extract()
# Produces output/output.log  +  output/<comp_name>/<comp_name>.dat  (recursive)
```

---

### Low-level access via ExportL5x

For direct SQLite access to the parsed ACD databases:

```python
from acd.l5x.export_l5x import ExportL5x

export = ExportL5x("MyController.ACD")

# Raw SQLite cursor — full access to comps, rungs, region_map, comments, nameless tables
cur = export._cur
cur.execute("SELECT comp_name, object_id FROM comps WHERE parent_id=0 AND record_type=256")
row = cur.fetchone()
ctrl_name, ctrl_id = row[0], row[1]

# High-level objects
controller = export.controller
project    = export.project
```

---

### Project structure

```
acd/
├── api.py                  # Public API (ImportProjectFromFile, ConvertAcdToL5x, ExplodeAcdToTree, ...)
├── l5x/
│   ├── export_l5x.py       # ACD -> SQLite -> Python objects
│   ├── elements.py         # Dataclasses + Builder classes for all project elements
│   └── xplode.py           # Filesystem tree writer (mirrors Logix SDK xplode output)
├── database/               # Binary .Dat file reader
├── record/                 # Record parsers (Comps, SbRegion, Comments, Nameless)
├── generated/              # Kaitai Struct generated parsers (comps, comments, ...)
└── zip/                    # ACD archive extraction
```

---

### Running the tests

```bash
pip install -e ".[dev]"
pytest
```

---

### Developing

Sections of the code are generated from kaitai template (.ksy) files in the resources/templates folder.
These are generated during the install phase.
The python scripts which are generated are located in the acd/generated folder.

### Contributing

Contributions are welcome. Open an issue or pull request on GitHub.

The sample ACD file used by the tests is `resources/CuteLogix.ACD`.
