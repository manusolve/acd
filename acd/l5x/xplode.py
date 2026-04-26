"""xplode: write a project as a filesystem tree for version control.

Each logical element (DataType, Module, Tag, Program, Routine, AOI, Task)
is written to its own file under ``output_dir/RSLogix5000Content/``,
mirroring the directory structure produced by the Logix SDK ``xplode``
command.

Tree layout::

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

ST routines are written as plain ``.st`` text files containing the raw
source lines joined by ``\\n``.  All other routine types and all metadata
(DataTypes, Tags, Modules, etc.) are written as pretty-printed XML files.

Rung ``Number`` attributes are **omitted** in the xplode format (matching
the Logix SDK xplode output) even though they appear in the monolithic L5X.
"""
from __future__ import annotations

import os
import re
import xml.dom.minidom
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acd.l5x.elements import RSLogix5000Content, Routine

_XML_DECL = '<?xml version="1.0" encoding="utf-8"?>'
_XML_DECL_STANDALONE = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>'
_BOM = "\ufeff"

_EXPORT_OPTIONS_YAML = (
    "serialization_format: Xml\n"
    "xml_attribute_per_line: false\n"
    "omit_export_date: true\n"
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pretty_xml(xml_str: str, standalone: bool = False) -> str:
    """Pretty-print *xml_str* and return a string with our standard declaration.

    The output:
    * Starts with a UTF-8 BOM (matching the Logix SDK xplode convention).
    * Uses a space before every self-closing ``/>`` (``<Tag ... />``) for
      consistent formatting across all Logix Designer XML tools.
    * Has no trailing newline.

    Args:
        xml_str: Raw XML string to pretty-print.
        standalone: If True, add ``standalone="yes"`` to the XML declaration
            (used only for the root RSLogix5000Content.xml file).
    """
    decl = _XML_DECL_STANDALONE if standalone else _XML_DECL
    try:
        dom = xml.dom.minidom.parseString(xml_str.encode("utf-8"))
        raw = dom.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
        lines = raw.splitlines()
        if lines and lines[0].startswith("<?xml"):
            lines[0] = decl
        # minidom adds a trailing blank line; strip it
        while lines and not lines[-1].strip():
            lines.pop()
        result = "\n".join(lines)
        # Ensure self-closing tags use " />" (space before slash)
        result = re.sub(r'(\S)/>', r'\1 />', result)
        # Collapse Description / RevisionNote CDATA sections back to single-line
        # minidom expands `<Description>\n<![CDATA[...]]>\n</Description>` with
        # whitespace nodes; collapse them to the compact form used by Logix exports.
        result = re.sub(
            r'<(Description|RevisionNote)>\s*<!\[CDATA\[(.*?)\]\]>\s*</(Description|RevisionNote)>',
            lambda m: f'<{m.group(1)}><![CDATA[{m.group(2)}]]></{m.group(1)}>',
            result,
            flags=re.DOTALL,
        )
        result = _BOM + result
        # Remove blank lines that minidom inserts from whitespace text nodes.
        # Run twice because minidom inserts whitespace-only lines adjacent to
        # empty lines (e.g. `\n    \n\n`), requiring two passes to collapse.
        result = re.sub(r'\n[ \t]*\n', '\n', result)
        result = re.sub(r'\n[ \t]*\n', '\n', result)
        # Collapse empty elements split across two lines:
        # `<Tag ...>\n  </Tag>` → `<Tag ...></Tag>`
        # Only collapse when the opening and closing tag names match to avoid
        # incorrectly joining parent elements with their children's closing tags.
        result = re.sub(
            r'<(\w+)([^>]*)>\n[ \t]*</(\w+)>',
            lambda m: (
                f'<{m.group(1)}{m.group(2)}></{m.group(1)}>'
                if m.group(1) == m.group(3)
                else m.group(0)
            ),
            result,
        )
        return result
    except Exception:
        return _BOM + f"{decl}\n{xml_str}"


def _write(path: str, content: str) -> None:
    """Write *content* to *path*, creating parent directories as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def _write_xml(path: str, xml_str: str) -> None:
    _write(path, _pretty_xml(xml_str))


def _strip_rung_number(xml_str: str) -> str:
    """Remove ``Number="N"`` attributes from ``<Rung>`` elements.

    The xplode tree format omits these numeric position attributes;
    they are present in the monolithic L5X but redundant (order is
    implicit from file position).
    """
    return re.sub(r'(<Rung)\s+Number="\d+"', r'\1', xml_str)


def _safe_name(name: str) -> str:
    """Sanitise *name* for use as a filesystem path component."""
    if not name:
        return "unnamed_element"
    # Replace characters forbidden on Windows and Unix filesystems
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)


def _routine_file(routine: "Routine") -> tuple[str, str]:
    """Return ``(extension, file_content)`` for *routine* in xplode format."""
    if routine.type == "ST":
        # Raw source text — no XML wrapper, no trailing newline
        return ".st", "\n".join(routine.st_lines)
    # RLL and all other types → XML, with Number attribute stripped
    xml_str = _strip_rung_number(routine.to_xml())
    return ".xml", _pretty_xml(xml_str)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def xplode(project: "RSLogix5000Content", output_dir: str) -> None:
    """Write *project* as a filesystem tree under *output_dir*.

    Args:
        project: A project loaded by :func:`acd.api.load_acd` or
            :class:`acd.api.ImportProjectFromFile`.
        output_dir: Destination directory.  The ``RSLogix5000Content``
            subtree is created inside this directory.  Existing files
            at conflicting paths are overwritten.
    """
    ctrl = project.controller
    if ctrl is None:
        return

    root = os.path.join(output_dir, "RSLogix5000Content")

    # --- Root controller stub XML ----------------------------------------
    # Temporarily empty all collection lists so project.to_xml() produces
    # a stub that references empty sections (<DataTypes />, etc.).
    _saved = {
        "data_types": ctrl.data_types,
        "modules": ctrl.modules,
        "tags": ctrl.tags,
        "programs": ctrl.programs,
        "tasks": ctrl.tasks,
        "aois": ctrl.aois,
    }
    orig_export_date = project.export_date
    orig_last_modified = ctrl.last_modified_date
    try:
        ctrl.data_types = []
        ctrl.modules = []
        ctrl.tags = []
        ctrl.programs = []
        ctrl.tasks = []
        ctrl.aois = []
        # Strip volatile timestamps — they change on every run and break diffs
        project.export_date = None
        ctrl.last_modified_date = None
        root_xml = project.to_xml()
    finally:
        for k, v in _saved.items():
            setattr(ctrl, k, v)
        project.export_date = orig_export_date
        ctrl.last_modified_date = orig_last_modified

    _write(
        os.path.join(root, "RSLogix5000Content.xml"),
        _pretty_xml(root_xml, standalone=True),
    )

    # --- export-options.yaml (inside RSLogix5000Content/) ----------------
    _write(os.path.join(root, "export-options.yaml"), _EXPORT_OPTIONS_YAML)

    # --- DataTypes -------------------------------------------------------
    for dt in ctrl.data_types:
        if getattr(dt, "_l5x_exclude", False):
            continue
        _write_xml(
            os.path.join(root, "DataTypes", f"{_safe_name(dt.name)}.xml"),
            dt.to_xml(),
        )

    # --- Modules ---------------------------------------------------------
    _unnamed_idx: dict[str, int] = {}
    for module in ctrl.modules:
        raw = module.name
        if not raw or raw == "?":
            key = "unnamed_element"
            idx = _unnamed_idx.get(key, 0)
            fname = key if idx == 0 else f"{key}_{idx}"
            _unnamed_idx[key] = idx + 1
        else:
            fname = _safe_name(raw)
        _write_xml(
            os.path.join(root, "Modules", f"{fname}.xml"),
            module.to_xml(),
        )

    # --- Controller-scoped tags ------------------------------------------
    for tag in ctrl.tags:
        if getattr(tag, "_l5x_exclude", False):
            continue
        _write_xml(
            os.path.join(root, "Tags", f"{_safe_name(tag.name)}.xml"),
            tag.to_xml(),
        )

    # --- Tasks -----------------------------------------------------------
    for task in ctrl.tasks:
        _write_xml(
            os.path.join(root, "Tasks", f"{_safe_name(task.name)}.xml"),
            task.to_xml(),
        )

    # --- AOIs ------------------------------------------------------------
    for aoi in ctrl.aois:
        aoi_dir = os.path.join(
            root, "AddOnInstructionDefinitions", _safe_name(aoi.name)
        )
        # AOI stub XML: keep Parameters + LocalTags; empty Routines
        orig_routines = aoi.routines
        try:
            aoi.routines = []
            aoi_xml = aoi.to_xml()
        finally:
            aoi.routines = orig_routines
        _write_xml(os.path.join(aoi_dir, f"{_safe_name(aoi.name)}.xml"), aoi_xml)

        for routine in aoi.routines:
            ext, content = _routine_file(routine)
            _write(
                os.path.join(aoi_dir, "Routines", f"{_safe_name(routine.name)}{ext}"),
                content,
            )

    # --- Programs --------------------------------------------------------
    for program in ctrl.programs:
        prog_dir = os.path.join(root, "Programs", _safe_name(program.name))

        # Program stub XML: empty Tags + Routines
        orig_tags = program.tags
        orig_routines = program.routines
        try:
            program.tags = []
            program.routines = []
            prog_xml = program.to_xml()
        finally:
            program.tags = orig_tags
            program.routines = orig_routines
        _write_xml(
            os.path.join(prog_dir, f"{_safe_name(program.name)}.xml"),
            prog_xml,
        )

        # Program-scoped tags
        for tag in program.tags:
            if getattr(tag, "_l5x_exclude", False):
                continue
            _write_xml(
                os.path.join(prog_dir, "Tags", f"{_safe_name(tag.name)}.xml"),
                tag.to_xml(),
            )

        # Routines
        for routine in program.routines:
            ext, content = _routine_file(routine)
            _write(
                os.path.join(
                    prog_dir, "Routines", f"{_safe_name(routine.name)}{ext}"
                ),
                content,
            )
