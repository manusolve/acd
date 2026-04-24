import struct
from dataclasses import dataclass
from sqlite3 import Cursor
from typing import Optional

from acd.database.dbextract import DatRecord


@dataclass
class NamelessRecord:
    _cur: Cursor
    dat_record: DatRecord

    def __post_init__(self):
        entry = NamelessRecord.parse(self.dat_record)
        if entry is not None:
            self._cur.execute("INSERT INTO nameless VALUES (?, ?, ?)", entry)

    @staticmethod
    def parse(dat_record: DatRecord) -> Optional[tuple]:
        if dat_record.identifier != 64250:
            return None
        buf = dat_record.record.record_buffer
        identifier = struct.unpack("I", buf[8:12])[0]
        object_identifier = struct.unpack_from("<I", buf, 0x0C)[0]
        return (object_identifier, identifier, buf)


def parse_source_line(buf: bytes) -> Optional[str]:
    """Decode the UTF-16LE text from a nameless ST-source-line record blob.

    Each ST source-line record has a 16-byte header followed by one
    ``FF FE FF <len_chars>`` frame encoding the source text in UTF-16LE.
    Returns the decoded string, or ``None`` if the frame cannot be found.
    """
    for i in range(0x10, len(buf) - 3):
        if buf[i] == 0xFF and buf[i + 1] == 0xFE and buf[i + 2] == 0xFF:
            length = buf[i + 3]
            end = i + 4 + length * 2
            if end > len(buf):
                return None
            return buf[i + 4:end].decode("utf-16-le", errors="replace")
    return None
