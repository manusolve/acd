"""Unit tests for NamelessRecord.parse_source_line."""
import struct

from acd.record.nameless import NamelessRecord


def _make_record(char_count: int, text: str, *, bom_override: bytes = b'\xff\xfe\xff') -> bytes:
    """Build a minimal nameless record blob for testing."""
    text_bytes = text.encode('utf-16-le')
    # Header: record_length (4), flags (4), object_id (4), secondary_id (4),
    #         type_flags (4), sub_length (4) = 24 bytes total before BOM
    header = struct.pack('<IIIIII', 28 + len(text_bytes), 0, 0xDEADBEEF, 0, 0, 0)
    assert len(header) == 24
    return header + bom_override + bytes([char_count]) + text_bytes


def test_parse_source_line_known_record():
    """Test with the exact bytes from record #13195 in CUT_0_0_48.ACD.

    The expected line is: \\t\\t@a2558998@ \\t\\t:= -927.47;\\t\\t// 20251002
    (39 UTF-16 code units, 0x27 = 39)
    """
    # Raw hex of the record (106 bytes total per problem statement)
    raw_hex = (
        "66000000"          # record_length = 102 (0x66)
        "02000001"          # flags
        "b7d6b8e9"          # object_id = 0xE9B8D6B7 (LE)
        "a8a56596"          # secondary id
        "d1070200"          # type/flags
        "77fa0400"          # sub-length
        "fffeff27"          # BOM (ff fe) + flag (ff) + len=0x27=39
        # UTF-16LE text: "\t\t@a2558998@ \t\t:= -927.47;\t\t// 20251002"
        "0900"  # \t
        "0900"  # \t
        "4000"  # @
        "6100"  # a
        "3200"  # 2
        "3500"  # 5
        "3500"  # 5
        "3800"  # 8
        "3900"  # 9
        "3900"  # 9
        "3800"  # 8
        "4000"  # @
        "2000"  # (space)
        "0900"  # \t
        "0900"  # \t
        "3a00"  # :
        "3d00"  # =
        "2000"  # (space)
        "2d00"  # -
        "3900"  # 9
        "3200"  # 2
        "3700"  # 7
        "2e00"  # .
        "3400"  # 4
        "3700"  # 7
        "3b00"  # ;
        "0900"  # \t
        "0900"  # \t
        "2f00"  # /
        "2f00"  # /
        "2000"  # (space)
        "3200"  # 2
        "3000"  # 0
        "3200"  # 2
        "3500"  # 5
        "3100"  # 1
        "3000"  # 0
        "3000"  # 0
        "3200"  # 2
    )
    record_bytes = bytes.fromhex(raw_hex)
    assert len(record_bytes) == 28 + 39 * 2  # 28 header + 78 text = 106 bytes

    result = NamelessRecord.parse_source_line(record_bytes)
    assert result == "\t\t@a2558998@ \t\t:= -927.47;\t\t// 20251002"


def test_parse_source_line_short_buffer():
    """Buffers shorter than 28 bytes must return None."""
    assert NamelessRecord.parse_source_line(b"") is None
    assert NamelessRecord.parse_source_line(b"\x00" * 27) is None


def test_parse_source_line_wrong_bom():
    """Records without the ff fe ff BOM pattern at offset 24 return None."""
    buf = _make_record(5, "hello", bom_override=b'\x00\x00\x00')
    assert NamelessRecord.parse_source_line(buf) is None


def test_parse_source_line_length_mismatch():
    """A char_count that claims more bytes than are present returns None."""
    text = "hi"
    text_bytes = text.encode('utf-16-le')
    header = struct.pack('<IIIIII', 28 + len(text_bytes), 0, 0, 0, 0, 0)
    # Claim 100 chars but only provide 2 chars (4 bytes)
    buf = header + b'\xff\xfe\xff' + bytes([100]) + text_bytes
    assert NamelessRecord.parse_source_line(buf) is None


def test_parse_source_line_empty_line():
    """An ST source line that is empty (char_count=0) returns an empty string."""
    buf = _make_record(0, "")
    result = NamelessRecord.parse_source_line(buf)
    assert result == ""


def test_parse_source_line_roundtrip():
    """Text with tabs, spaces and special characters survives the encode/decode."""
    original = "\t\tif x > 0 then\t// check positive"
    buf = _make_record(len(original), original)
    result = NamelessRecord.parse_source_line(buf)
    assert result == original
