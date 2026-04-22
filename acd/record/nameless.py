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

    @staticmethod
    def parse_source_line(record_bytes: bytes) -> Optional[str]:
        """Return the UTF-16LE decoded source-text line from a nameless record blob,
        or None if the record is not an ST source-text record.

        Layout (offsets into record_bytes / record_buffer):
          0- 3  record_length (LE uint32)
          4- 7  flags
          8-11  object_id (owning routine)
         12-15  secondary id
         16-19  type/flags
         20-23  sub-length
         24-25  FF FE  (UTF-16LE BOM)
            26  FF     (flag byte)
            27  NN     (text length in UTF-16 code units)
          28+   UTF-16LE text (2 * NN bytes)
        """
        if len(record_bytes) < 28:
            return None
        if record_bytes[24:27] != b'\xff\xfe\xff':
            return None
        char_count = record_bytes[27]
        text_bytes = record_bytes[28:28 + char_count * 2]
        if len(text_bytes) != char_count * 2:
            return None
        try:
            return text_bytes.decode('utf-16-le')
        except UnicodeDecodeError:
            return None
