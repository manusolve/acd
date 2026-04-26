"""Tests for acd.l5x.xplode — filesystem tree export from an ACD project.

These tests exercise the full xplode path against the real CUT_0_0_48.ACD
resource and compare the output to the pre-generated reference tree stored in
resources/CUT_0_0_48_xplode_acd/.
"""
import os

import pytest

from acd.api import ExplodeAcdToTree

RESOURCES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "resources"
)
ACD_FILE = os.path.join(RESOURCES_DIR, "CUT_0_0_48_bonefide", "CUT_0_0_48.ACD")
REF_TREE = os.path.join(RESOURCES_DIR, "CUT_0_0_48_xplode_acd", "RSLogix5000Content")


def _skip_if_no_acd():
    if not os.path.isfile(ACD_FILE):
        pytest.skip("CUT_0_0_48.ACD resource not present")


@pytest.fixture(scope="module")
def xplode_output(tmp_path_factory):
    _skip_if_no_acd()
    out = str(tmp_path_factory.mktemp("xplode_out"))
    ExplodeAcdToTree(ACD_FILE, out).extract()
    return os.path.join(out, "RSLogix5000Content")


def test_xplode_produces_root_xml(xplode_output):
    """The root controller stub XML must be created."""
    assert os.path.isfile(os.path.join(xplode_output, "RSLogix5000Content.xml"))


def test_xplode_root_has_standalone(xplode_output):
    """The root XML declaration must include standalone='yes'."""
    with open(os.path.join(xplode_output, "RSLogix5000Content.xml"), encoding="utf-8") as f:
        first_line = f.readline()
    assert 'standalone="yes"' in first_line


def test_xplode_root_omits_export_date(xplode_output):
    """ExportDate must be omitted from the root XML (it changes every run)."""
    with open(os.path.join(xplode_output, "RSLogix5000Content.xml"), encoding="utf-8") as f:
        content = f.read()
    assert "ExportDate" not in content


def test_xplode_produces_export_options_yaml(xplode_output):
    """export-options.yaml must be written inside RSLogix5000Content/."""
    assert os.path.isfile(os.path.join(xplode_output, "export-options.yaml"))


def test_xplode_produces_data_types(xplode_output):
    """At least one DataType XML file must be produced."""
    dt_dir = os.path.join(xplode_output, "DataTypes")
    assert os.path.isdir(dt_dir)
    assert any(f.endswith(".xml") for f in os.listdir(dt_dir))


def test_xplode_produces_tasks(xplode_output):
    """Task XML files must be produced."""
    task_dir = os.path.join(xplode_output, "Tasks")
    assert os.path.isdir(task_dir)
    assert any(f.endswith(".xml") for f in os.listdir(task_dir))


def test_xplode_produces_programs(xplode_output):
    """Program sub-directories must be created."""
    prog_dir = os.path.join(xplode_output, "Programs")
    assert os.path.isdir(prog_dir)
    assert len(os.listdir(prog_dir)) > 0


def test_xplode_st_routines_as_plain_text(xplode_output):
    """ST routines must be written as plain .st files, not XML."""
    found = False
    for root, _, files in os.walk(xplode_output):
        for f in files:
            if f.endswith(".st"):
                found = True
                path = os.path.join(root, f)
                with open(path, encoding="utf-8") as fh:
                    content = fh.read()
                assert "<Routine" not in content, (
                    f"{f}: .st file should not contain XML Routine wrapper"
                )
                assert "<STContent>" not in content, (
                    f"{f}: .st file should not contain XML STContent wrapper"
                )
    assert found, "No .st files found in xplode output"


def test_xplode_rll_routines_as_xml(xplode_output):
    """RLL routines must be written as XML files without Number attribute on Rung."""
    found = False
    for root, _, files in os.walk(xplode_output):
        for f in files:
            path = os.path.join(root, f)
            if "Routines" in root and f.endswith(".xml"):
                found = True
                with open(path, encoding="utf-8") as fh:
                    content = fh.read()
                assert 'Type="RLL"' in content, (
                    f"{f}: Routine XML should declare Type=RLL"
                )
                # Rung Number attributes must be stripped
                import re
                assert not re.search(r'<Rung Number="\d+"', content), (
                    f"{f}: Rung elements must not have Number attribute in xplode format"
                )
                break  # one file is enough
    if not found:
        pytest.skip("No RLL routine XML files found in this project")


def test_xplode_xml_files_have_bom(xplode_output):
    """All XML files must start with a UTF-8 BOM."""
    checked = 0
    for root, _, files in os.walk(xplode_output):
        for f in files:
            if f.endswith(".xml"):
                path = os.path.join(root, f)
                with open(path, "rb") as fh:
                    header = fh.read(3)
                assert header == b"\xef\xbb\xbf", (
                    f"{f}: XML file missing UTF-8 BOM"
                )
                checked += 1
                if checked >= 20:
                    return
    assert checked > 0


def test_xplode_program_stubs_have_empty_sections(xplode_output):
    """Program stub XMLs must contain empty <Tags /> and <Routines />."""
    prog_dir = os.path.join(xplode_output, "Programs")
    found = False
    for prog_name in os.listdir(prog_dir):
        stub = os.path.join(prog_dir, prog_name, f"{prog_name}.xml")
        if os.path.isfile(stub):
            found = True
            with open(stub, encoding="utf-8-sig") as fh:
                content = fh.read()
            assert "<Tags />" in content or "<Tags/>" in content, (
                f"{stub}: program stub must have empty Tags section"
            )
            assert "<Routines />" in content or "<Routines/>" in content, (
                f"{stub}: program stub must have empty Routines section"
            )
    assert found, "No program stub XML files found"


def test_xplode_matches_reference_tree(xplode_output):
    """Every file produced must match the pre-generated reference tree byte-for-byte."""
    if not os.path.isdir(REF_TREE):
        pytest.skip("Reference tree not present")
    mismatches = []
    for dirpath, _, filenames in os.walk(REF_TREE):
        for fname in filenames:
            ref_path = os.path.join(dirpath, fname)
            rel = os.path.relpath(ref_path, REF_TREE)
            out_path = os.path.join(xplode_output, rel)
            if not os.path.isfile(out_path):
                mismatches.append(f"MISSING: {rel}")
                continue
            with open(ref_path, "rb") as rf, open(out_path, "rb") as of:
                if rf.read() != of.read():
                    mismatches.append(f"DIFFERS: {rel}")
    assert not mismatches, (
        f"{len(mismatches)} file(s) differ from reference:\n"
        + "\n".join(mismatches[:20])
    )
