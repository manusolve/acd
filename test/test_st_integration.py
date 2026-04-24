"""Integration tests for ST routine source-line extraction against ST.ACD.

These tests exercise the full builder path (ExportL5x -> RoutineBuilder ->
nameless recursive CTE) rather than synthetic Routine objects.  They require
the ST.ACD resource file to be present in the resources/ directory.
"""
import os
import re

import pytest

from acd.l5x.export_l5x import ExportL5x

RESOURCES_DIR = os.path.join(os.path.dirname(__file__), "..", "resources")
ST_ACD = os.path.join(RESOURCES_DIR, "ST.ACD")


@pytest.fixture(scope="module")
def st_controller(tmp_path_factory):
    tmp = str(tmp_path_factory.mktemp("st_acd"))
    yield ExportL5x(ST_ACD, tmp).controller


def _st_routines(controller):
    return [r for prog in controller.programs for r in prog.routines if r.type == "ST"]


def test_st_routines_have_source_lines(st_controller):
    """Every ST routine must have at least one source line populated."""
    st = _st_routines(st_controller)
    assert len(st) > 0, "No ST routines found in ST.ACD"
    empty = [r.name for r in st if not getattr(r, "st_lines", None)]
    assert empty == [], f"ST routines with empty st_lines: {empty}"


def test_st_content_in_xml(st_controller):
    """to_xml() for each ST routine must contain <STContent>."""
    st = _st_routines(st_controller)
    missing = [r.name for r in st if "<STContent>" not in r.to_xml()]
    assert missing == [], f"ST routines missing <STContent> in XML: {missing}"


def test_no_self_closed_st_routines(st_controller):
    """No ST routine should produce a self-closed element (empty body)."""
    st = _st_routines(st_controller)
    self_closed = [r.name for r in st if re.search(r'<Routine[^>]+Type="ST"\s*/>', r.to_xml())]
    assert self_closed == [], f"ST routines with self-closed XML: {self_closed}"


def test_tabs_preserved(st_controller):
    """Tab indentation in ST source must appear verbatim in the XML output."""
    project_xml = st_controller.to_xml()
    assert "\t" in project_xml, "No tab characters found in exported XML"


def test_tag_placeholders_resolved(st_controller):
    """@XXXXXXXX@ object-ID placeholders must be resolved to tag names."""
    st = _st_routines(st_controller)
    for r in st:
        for line in getattr(r, "st_lines", []):
            assert not re.search(r"@[0-9a-f]{8}@", line), (
                f"Unresolved placeholder in {r.name!r}: {line!r}"
            )


def test_st_routine_content(st_controller):
    """At least one ST routine should contain known ST assignment syntax."""
    st = _st_routines(st_controller)
    all_source = "\n".join(line for r in st for line in getattr(r, "st_lines", []))
    assert ":=" in all_source, "':=' not found in any ST routine source"
    assert any(
        keyword in all_source
        for keyword in ("if ", "case ", "for ", "while ", "end_if", "end_case", "end_for")
    ), "No common ST control-flow keywords found in source"
