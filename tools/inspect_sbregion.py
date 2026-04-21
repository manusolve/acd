"""inspect_sbregion.py — Diagnostic CLI for SbRegion.Dat records.

Surfaces every record inside ``SbRegion.Dat`` so you can see exactly what
the binary contains for each language type (Rung NT, REGION NT, REGION AST,
REGION LE UID, ST, …).  This is a *read-only* inspection tool; it does not
modify any files.

Usage::

    python tools/inspect_sbregion.py --acd path/to/project.ACD
    python tools/inspect_sbregion.py --acd project.ACD --routine MainRoutine
    python tools/inspect_sbregion.py --acd project.ACD --json out.json
    python tools/inspect_sbregion.py --acd project.ACD --limit 50
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hex_preview(data: bytes, max_bytes: int = 64) -> str:
    """Return a short hex dump of *data* (at most *max_bytes* bytes)."""
    chunk = data[:max_bytes]
    pairs = [f"{b:02x}" for b in chunk]
    suffix = f" … (+{len(data) - max_bytes} more)" if len(data) > max_bytes else ""
    return " ".join(pairs) + suffix


def _decode_text(buf: bytes) -> Optional[str]:
    """Try to decode *buf* as UTF-16-LE (the encoding used by Rung NT / REGION NT)."""
    try:
        return buf.decode("utf-16-le").rstrip("\x00")
    except Exception:
        return None


def _resolve_tag_refs(text: str, name_lookup: Dict[int, str]) -> str:
    """Replace ``@XXXXXXXX@`` hex placeholders with their comp names."""
    for tag in re.findall(r"@[A-Fa-f0-9]+@", text):
        try:
            tag_id = int(tag[1:-1], 16)
        except ValueError:
            continue
        name = name_lookup.get(tag_id)
        if name is not None:
            text = text.replace(tag, name)
    return text


# ---------------------------------------------------------------------------
# Core inspection logic
# ---------------------------------------------------------------------------

def _build_record_list(
    sbregion_dat_path: str,
    name_lookup: Dict[int, str],
    region_map_rows: List[tuple],
) -> List[dict]:
    """Parse every record in ``SbRegion.Dat`` and return a list of dicts."""
    from acd.database.dbextract import DbExtract
    from acd.generated.sbregion.fafa_sbregions import FafaSbregions

    # Build object_id → parent_id mapping from region_map rows so we can
    # annotate each record with its parent routine / program.
    obj_to_parent: Dict[int, int] = {row[0]: row[1] for row in region_map_rows}

    dat = DbExtract(sbregion_dat_path).read()
    records: List[dict] = []

    for idx, dat_record in enumerate(dat.records.record):
        identifier = dat_record.identifier
        entry: dict = {
            "record_index": idx,
            "identifier": identifier,
            "identifier_hex": f"0x{identifier:04X}",
            "record_length": dat_record.len_record,
        }

        if identifier != 0xFAFA:  # 64250 — the only SbRegion record type
            entry["language_type"] = None
            entry["object_id"] = None
            entry["parent_id"] = None
            entry["text"] = None
            entry["raw_hex"] = _hex_preview(dat_record.record.record_buffer
                                             if hasattr(dat_record.record, "record_buffer")
                                             else b"")
            records.append(entry)
            continue

        try:
            r = FafaSbregions.from_bytes(dat_record.record.record_buffer)
        except Exception as exc:
            entry["language_type"] = f"<parse error: {exc}>"
            entry["object_id"] = None
            entry["parent_id"] = None
            entry["text"] = None
            entry["raw_hex"] = _hex_preview(dat_record.record.record_buffer)
            records.append(entry)
            continue

        lang = r.header.language_type
        obj_id = r.header.identifier
        parent_id = obj_to_parent.get(obj_id)

        entry["language_type"] = lang
        entry["object_id"] = obj_id
        entry["parent_id"] = parent_id
        entry["sb_regions"] = r.header.sb_regions
        entry["buffer_length"] = r.len_record_buffer
        entry["raw_hex"] = _hex_preview(r.record_buffer)

        # Decode text for text-bearing record types.
        text: Optional[str] = None
        if lang in ("Rung NT", "REGION NT"):
            raw_text = _decode_text(r.record_buffer)
            if raw_text is not None:
                text = _resolve_tag_refs(raw_text, name_lookup)
        elif lang == "REGION LE UID":
            if len(r.record_buffer) >= 4:
                uid = struct.unpack("<I", r.record_buffer[-4:])[0]
                text = f"<UID: 0x{uid:08X}>"
        else:
            # Try a UTF-16-LE decode as a best-effort heuristic for unknown types.
            attempt = _decode_text(r.record_buffer)
            if attempt and attempt.isprintable():
                text = attempt

        entry["text"] = text
        records.append(entry)

    return records


def _lookup_routine_name(parent_id: Optional[int], cur) -> Optional[str]:
    """Return the comp_name for *parent_id* from the SQLite comps table."""
    if parent_id is None or cur is None:
        return None
    cur.execute("SELECT comp_name FROM comps WHERE object_id=?", (parent_id,))
    row = cur.fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _print_record(rec: dict, routine_name: Optional[str]) -> None:
    parent_label = (
        f"parent_id={rec['parent_id']} ({routine_name})"
        if routine_name
        else (f"parent_id={rec['parent_id']}" if rec["parent_id"] is not None else "parent_id=<none>")
    )
    print(f"--- Record #{rec['record_index']} ---")
    print(f"  identifier   : {rec['identifier_hex']} ({rec['identifier']})")
    print(f"  language_type: {rec.get('language_type', 'n/a')}")
    print(f"  object_id    : {rec.get('object_id', 'n/a')}")
    print(f"  {parent_label}")
    if rec.get("buffer_length") is not None:
        print(f"  buffer_length: {rec['buffer_length']}")
    if rec.get("text") is not None:
        # Truncate very long texts for console readability.
        display = rec["text"]
        if len(display) > 200:
            display = display[:200] + f" … (+{len(rec['text']) - 200} more chars)"
        print(f"  text         : {display!r}")
    print(f"  raw_hex      : {rec.get('raw_hex', '')}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect every record in SbRegion.Dat for an ACD project file. "
            "Pure read-only diagnostic — does not modify anything."
        )
    )
    parser.add_argument("--acd", required=True, metavar="PATH",
                        help="Path to the .ACD project file.")
    parser.add_argument("--routine", metavar="NAME",
                        help="Only show records whose parent routine matches NAME.")
    parser.add_argument("--json", metavar="FILE",
                        help="Write the full record list to a JSON file.")
    parser.add_argument("--limit", type=int, default=0, metavar="N",
                        help="Stop after showing N records (0 = no limit).")
    args = parser.parse_args(argv)

    acd_path = Path(args.acd)
    if not acd_path.exists():
        print(f"ERROR: ACD file not found: {acd_path}", file=sys.stderr)
        return 1

    # Use a temporary directory so we leave no artifacts behind.
    temp_dir = tempfile.mkdtemp(prefix="inspect_sbregion_")
    try:
        return _run(acd_path, temp_dir, args)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _run(acd_path: Path, temp_dir: str, args) -> int:
    # -----------------------------------------------------------------------
    # Step 1: Build the full ExportL5x state.  This populates comps,
    # region_map, and rungs tables exactly the same way the real parser does.
    # -----------------------------------------------------------------------
    print(f"[inspect_sbregion] Loading {acd_path} …", file=sys.stderr)
    from acd.l5x.export_l5x import ExportL5x

    exporter = ExportL5x(str(acd_path), temp_dir)
    cur = exporter._cur

    # -----------------------------------------------------------------------
    # Step 2: Build supporting data structures for annotation.
    # -----------------------------------------------------------------------
    # name_lookup: object_id → comp_name (for tag-ref resolution)
    name_lookup: Dict[int, str] = exporter._id_to_name

    # region_map rows: (object_id, parent_id, unknown, seq_no, record)
    cur.execute("SELECT object_id, parent_id, unknown, seq_no FROM region_map")
    region_map_rows = cur.fetchall()

    # -----------------------------------------------------------------------
    # Step 3: Parse every record in SbRegion.Dat.
    # -----------------------------------------------------------------------
    # ExportL5x writes extracted files into temp_dir directly.
    import os
    sbregion_path = os.path.join(temp_dir, "SbRegion.Dat")

    print(f"[inspect_sbregion] Parsing {sbregion_path} …", file=sys.stderr)
    records = _build_record_list(sbregion_path, name_lookup, region_map_rows)

    # -----------------------------------------------------------------------
    # Step 4: Annotate each record with its parent routine name.
    # -----------------------------------------------------------------------
    for rec in records:
        rec["parent_routine_name"] = _lookup_routine_name(rec.get("parent_id"), cur)

    # -----------------------------------------------------------------------
    # Step 5: Apply filters.
    # -----------------------------------------------------------------------
    if args.routine:
        filter_name = args.routine.lower()
        records = [
            r for r in records
            if (r.get("parent_routine_name") or "").lower() == filter_name
        ]

    # -----------------------------------------------------------------------
    # Step 6: Optional JSON dump.
    # -----------------------------------------------------------------------
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, default=str)
        print(f"[inspect_sbregion] JSON written to {args.json}", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Step 7: Console summary.
    # -----------------------------------------------------------------------
    # Tally by language_type for the header summary.
    from collections import Counter
    tally: Counter = Counter(r.get("language_type") for r in records)
    print(f"\n{'='*60}")
    print(f"SbRegion.Dat  —  {len(records)} record(s)")
    for lang, count in sorted(tally.items(), key=lambda x: -(x[1])):
        print(f"  {lang!s:<30}  {count}")
    print(f"{'='*60}\n")

    limit = args.limit if args.limit > 0 else len(records)
    shown = 0
    for rec in records:
        if shown >= limit:
            remaining = len(records) - shown
            print(f"… {remaining} more record(s) not shown (use --limit 0 to see all).")
            break
        _print_record(rec, rec.get("parent_routine_name"))
        shown += 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
