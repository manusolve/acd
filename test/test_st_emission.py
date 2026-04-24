"""Unit tests for ST routine XML emission (Routine.to_xml ST branch)."""
import re

import pytest

from acd.l5x.elements import Routine


def _make_st_routine(st_lines):
    """Helper: construct a Routine with type='ST' and the given st_lines list."""
    # Routine positional args: _name, name, type, rungs, _rung_ids, _rung_comments, st_lines, _st_source_ids
    return Routine("Logic", "Logic", "ST", [], [], {}, st_lines, [])


def test_st_routine_produces_stcontent():
    """A Routine with type='ST' and non-empty st_lines emits <STContent>."""
    lines = ["\tstate := 420;", "\t// 20251002"]
    r = _make_st_routine(lines)
    xml = r.to_xml()

    assert "<STContent>" in xml
    assert "</STContent>" in xml


def test_st_routine_line_numbers():
    """Each ST line is wrapped in <Line Number="N">...</Line> with correct indices."""
    lines = ["\tstate := 420;", "\t// 20251002"]
    r = _make_st_routine(lines)
    xml = r.to_xml()

    assert 'Number="0"' in xml
    assert 'Number="1"' in xml
    assert 'Number="2"' not in xml


def test_st_routine_cdata_wrapping():
    """ST line content is wrapped in CDATA sections."""
    lines = ["\tstate := 420;", "\t// 20251002"]
    r = _make_st_routine(lines)
    xml = r.to_xml()

    assert "<![CDATA[\tstate := 420;]]>" in xml
    assert "<![CDATA[\t// 20251002]]>" in xml


def test_st_routine_tabs_preserved():
    """Tabs inside ST source lines must be preserved verbatim (not escaped)."""
    lines = ["\tif condition then", "\t\tx := 1;", "\tend_if;"]
    r = _make_st_routine(lines)
    xml = r.to_xml()

    assert "\t" in xml
    assert "&#9;" not in xml  # must NOT be html-escaped


def test_st_routine_empty_lines_produces_no_stcontent():
    """An ST routine with no lines produces a well-formed element without STContent."""
    r = _make_st_routine([])
    xml = r.to_xml()

    assert "<STContent>" not in xml
    assert "STContent" not in xml
    # Should still be a valid element (not malformed)
    assert '<Routine Name="Logic" Type="ST">' in xml or '<Routine Name="Logic" Type="ST"/>' in xml


def test_rll_routine_unaffected():
    """RLL routines still emit RLLContent and are not affected by the ST branch."""
    rungs = ["XIC(Tag1)OTE(Tag2);"]
    r = Routine("Main", "Main", "RLL", rungs, [], {})
    xml = r.to_xml()

    assert "<RLLContent>" in xml
    assert "<STContent>" not in xml
    assert "XIC(Tag1)OTE(Tag2);" in xml


def test_rll_routine_with_st_fields_unaffected():
    """RLL routine with st_lines populated does not emit STContent."""
    rungs = ["XIC(Tag1)OTE(Tag2);"]
    r = Routine("Main", "Main", "RLL", rungs, [], {}, ["\tsome_st_line;"], [])
    xml = r.to_xml()

    assert "<RLLContent>" in xml
    assert "<STContent>" not in xml
