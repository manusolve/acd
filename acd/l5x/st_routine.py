import re
from dataclasses import dataclass
from sqlite3 import Cursor
from typing import List

from acd.record.nameless import NamelessRecord


@dataclass
class StRoutineBuilder:
    _cur: Cursor
    _routine_object_id: int

    def build(self) -> List[str]:
        """Return the ordered list of ST source lines for the given routine.

        Records in the nameless table are keyed by parent_id (== the owning
        routine's object_id, stored at bytes 8-11 of the record blob).  SQLite
        preserves insertion order for rows with equal parent_id values, which
        matches the file order written by DbExtract, which in turn matches the
        original line order in the ST source.
        """
        self._cur.execute(
            "SELECT record FROM nameless WHERE parent_id = ?",
            (self._routine_object_id,),
        )
        lines: List[str] = []
        for (blob,) in self._cur.fetchall():
            text = NamelessRecord.parse_source_line(bytes(blob))
            if text is None:
                continue
            text = self._replace_tag_references(text)
            lines.append(text)
        return lines

    def _replace_tag_references(self, text: str) -> str:
        """Resolve @xxxxxxxx@ tag-ID placeholders to comp names."""
        for tag in re.findall(r"@[0-9a-fA-F]{8}@", text):
            hex_id = tag[1:-1]
            try:
                oid = int(hex_id, 16)
            except ValueError:
                continue
            self._cur.execute(
                "SELECT comp_name FROM comps WHERE object_id=?", (oid,)
            )
            row = self._cur.fetchone()
            if row:
                text = text.replace(tag, row[0])
        return text
