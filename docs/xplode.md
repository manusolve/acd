# xplode: Version-Control-Friendly Filesystem Tree Export

## Overview

`xplode` writes a parsed ACD project as a **directory tree** in which every logical
element — DataType, Module, Tag, Program, Routine, AOI, and Task — is written to its
own file. The layout mirrors the output produced by the Rockwell Logix SDK `xplode`
command, so the resulting tree is interchangeable with the SDK's own explode output.

The motivation is source control. A monolithic `.L5X` export for a non-trivial project
is tens of thousands of lines in a single file, which makes `git diff` output almost
unusable and `git blame` meaningless. By shattering the project into one file per
element, each `git diff` shows exactly which routine, tag, or data type changed, and
`git blame` can track authorship at the routine level.

Crucially, `xplode` reads the binary ACD **directly from disk** — no running Studio
5000, no Rockwell SDK entitlement, and no COM automation are required.

> ST routines are written as plain `.st` text files (the raw source text, one file per
> routine, directly diff-able). All other routine types and all metadata are written as
> pretty-printed XML files.

---

## Quick start

```python
from acd.api import ExplodeAcdToTree

ExplodeAcdToTree("MyProject.ACD", "myproject-tree/").extract()
```

This produces `myproject-tree/RSLogix5000Content/…`. Commit that tree to Git. On the
next firmware revision, run the same command again and use `git diff` to see exactly
which routines, tags, or data types changed.

You can also call the lower-level function directly if you already hold a loaded
project object:

```python
from acd.api import load_acd
from acd.l5x.xplode import xplode

project = load_acd("MyProject.ACD")
xplode(project, "myproject-tree/")
```

`ExplodeAcdToTree(...).extract()` is simply a thin wrapper that calls `load_acd()`
followed by `xplode()`.

---

## Output tree layout

```
RSLogix5000Content/
    RSLogix5000Content.xml          -- controller stub (empty collections)
    export-options.yaml
    DataTypes/
        {TypeName}.xml
    Modules/
        {ModuleName}.xml
    Tags/
        {TagName}.xml               -- controller-scoped tags
    Tasks/
        {TaskName}.xml
    AddOnInstructionDefinitions/
        {AoiName}/
            {AoiName}.xml           -- AOI stub (Parameters + LocalTags; empty Routines)
            Routines/
                {RoutineName}.st    -- ST source text (plain text)
                {RoutineName}.xml   -- RLL / other routines (XML)
    Programs/
        {ProgramName}/
            {ProgramName}.xml       -- program stub (empty Tags + Routines)
            Tags/
                {TagName}.xml       -- program-scoped tags
            Routines/
                {RoutineName}.st
                {RoutineName}.xml
```

### Stub files

The root `RSLogix5000Content.xml`, each program's `{ProgramName}.xml`, and each AOI's
`{AoiName}.xml` are **stub** files. They contain the element's own attributes but with
empty child collections — for example a program stub carries `<Tags />` and
`<Routines />` rather than the actual tags and routines, because those are written as
separate files in the `Tags/` and `Routines/` sub-directories. This is what keeps each
file small and independently diff-able.

---

## How it works

`xplode` is defined in `acd/l5x/xplode.py`. The public entry points are:

- `ExplodeAcdToTree` — a dataclass in `acd/api.py` implementing the `Extract`
  interface, consistent with the other public API classes (`ConvertAcdToL5x`,
  `ExtractAcdDatabase`, etc.).
- `xplode(project, output_dir)` — the underlying function that walks a loaded
  `RSLogix5000Content` project and writes the tree.

The walk is straightforward: for each collection on the controller
(`data_types`, `modules`, `tags`, `tasks`, `aois`, `programs`) the function iterates
the elements, calls each element's existing `to_xml()` serialiser, and writes the
result to the appropriate path. Programs and AOIs recurse into their tags and routines.

Several details are handled specifically so that the output matches the Logix SDK
`xplode` format byte-for-byte:

### 1. Controller / program / AOI stubs

To emit a stub, `xplode` temporarily empties the element's child collections, calls
`to_xml()`, then restores the originals in a `finally` block. For the controller this
means `data_types`, `modules`, `tags`, `programs`, `tasks`, and `aois` are all cleared
before serialising the root `RSLogix5000Content.xml`.

### 2. Volatile timestamps are stripped

`project.export_date` and `controller.last_modified_date` change on every run. If they
were written to the output, every re-export would produce a spurious diff even when no
logic changed. `xplode` sets both to `None` before serialising the root stub and
restores them afterwards. `export-options.yaml` also sets `omit_export_date: true`.

### 3. ST routines as plain text

A routine of type `ST` is written as a `.st` file containing the raw source lines
joined by `\n`, with no XML wrapper and no trailing newline. Every other routine type
(RLL etc.) is written as XML.

### 4. Rung `Number` attributes are omitted

The monolithic L5X puts a `Number="N"` attribute on each `<Rung>`. The xplode format
omits it (rung order is implicit from file position). `xplode` strips the attribute
from RLL routine XML before writing.

### 5. XML formatting to match the SDK

The `_pretty_xml` helper normalises the XML so it is identical to the SDK's output:

- Starts every XML file with a UTF-8 BOM (`\ufeff`).
- Uses ` />` (a space before the slash) on every self-closing tag.
- Collapses `<Description>` / `<RevisionNote>` CDATA blocks back onto a single line
  (minidom otherwise expands them with surrounding whitespace).
- Removes the whitespace-only lines that minidom inserts.
- Uses `standalone="yes"` in the XML declaration for the root file only.

### 6. Filesystem-safe names

`_safe_name` replaces characters that are forbidden in Windows/Unix path components
(`<>:"/\|?*` and control characters) with `_`. Unnamed modules fall back to
`unnamed_element`, `unnamed_element_1`, and so on.

---

## Relationship to the `logix-git` workflow

The [`logix-git`](https://github.com/manusolve/logix-git) tooling wires Studio 5000 up
to Git via custom **Tools** menu entries. Its current pipeline is two stages:

1. **Stage 1 — ACD → L5X**: `acd2l5x.py` uses this library (`ConvertAcdToL5x`) to parse
   the binary ACD and emit a monolithic `.L5X`.
2. **Stage 2 — L5X → tree**: the Rockwell SDK `l5xplode explode` command shatters that
   `.L5X` into a directory tree that is committed to Git.

`xplode` collapses those two stages into one: it goes straight from the binary ACD to
the exploded tree, producing SDK-compatible output **without needing `l5xplode` (and
therefore without the Rockwell SDK / FactoryTalk Hub entitlement) on the machine**.
A `logix-git` "commit" step can call `ExplodeAcdToTree(...).extract()` directly and
then run `git add` + `git commit` on the resulting tree.

---

## Reference-parity verification

`xplode` output was validated against a reference tree produced by the Rockwell Logix
SDK `xplode` command from the same project. The test suite
(`test/test_xplode.py`) asserts, among other things, that:

- the root `RSLogix5000Content.xml` exists, declares `standalone="yes"`, and omits
  `ExportDate`;
- `export-options.yaml`, `DataTypes/`, `Tasks/`, and `Programs/` are produced;
- ST routines are written as plain `.st` files (no `<Routine>` / `<STContent>` wrapper);
- RLL routines are written as XML with `Type="RLL"` and **no** `Rung Number="…"`
  attribute;
- every XML file begins with a UTF-8 BOM;
- program stubs contain empty `<Tags />` and `<Routines />` sections;
- and, most strictly, every file produced matches the reference tree **byte-for-byte**.

The correct emission of ST routine bodies (tag-reference resolution, cross-program
`\Program.tag` prefixes, IO-module names, and shadow-tag filtering) is what makes the
byte-for-byte match possible. Those mechanisms are documented separately in
[`st_tag_resolution.md`](st_tag_resolution.md) and
[`st_to_ladder_bytecode.md`](st_to_ladder_bytecode.md).
