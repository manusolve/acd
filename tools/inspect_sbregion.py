"""inspect_sbregion.py — Diagnostic CLI for SbRegion.Dat records.

Surfaces every record inside ``SbRegion.Dat`` so you can see exactly what
the binary contains for each language type (Rung NT, REGION NT, REGION AST,
REGION LE UID, ST, …).  This is a *read-only* inspection tool; it does not
modify any files.

Default mode is **quiet**: prints a one-line header, one sample record per
unique ``language_type``, any records matching ``--routine``, and finally the
two summary tables.  Pass ``--verbose`` to restore per-record dump for every
record up to ``--limit``.

Usage::

    python tools/inspect_sbregion.py --acd path/to/project.ACD
    python tools/inspect_sbregion.py --acd project.ACD --verbose
    python tools/inspect_sbregion.py --acd project.ACD --routine MainRoutine
    python tools/inspect_sbregion.py --acd project.ACD --json out.json
    python tools/inspect_sbregion.py --acd project.ACD --verbose --limit 20
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple


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


def _get_all_routines(cur) -> Dict[int, Tuple[str, str]]:
    """Return a mapping of {object_id: (routine_name, routine_type_str)}.

    Scans every ``RxRoutineCollection`` node in *comps* and parses the routine
    type byte at offset 0x30 from the record buffer — the same logic used by
    ``RoutineBuilder.build()`` in elements.py.
    """
    from acd.generated.comps.rx_generic import RxGeneric
    from acd.l5x.elements import routine_type_enum

    cur.execute("SELECT object_id FROM comps WHERE comp_name='RxRoutineCollection'")
    coll_ids = [row[0] for row in cur.fetchall()]

    routines: Dict[int, Tuple[str, str]] = {}
    for coll_id in coll_ids:
        cur.execute(
            "SELECT object_id, comp_name, record FROM comps WHERE parent_id=?",
            (coll_id,),
        )
        for oid, name, record in cur.fetchall():
            try:
                r = RxGeneric.from_bytes(bytes(record))
                type_idx = struct.unpack_from("<H", r.record_buffer, 0x30)[0]
                type_str = routine_type_enum(type_idx)
            except Exception:
                type_str = "<unknown>"
            routines[oid] = (name, type_str)
    return routines


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


def _print_language_type_summary(records: List[dict]) -> None:
    """Print aligned summary table grouped by language_type."""
    # Build stats: per language_type → count + Counter of parent routine types.
    stats: Dict[str, dict] = {}
    for rec in records:
        lang = rec.get("language_type") or "<unmapped>"
        parent_rtype = rec.get("parent_routine_type") or ""
        if lang not in stats:
            stats[lang] = {"count": 0, "rtypes": Counter()}
        stats[lang]["count"] += 1
        stats[lang]["rtypes"][parent_rtype] += 1

    if not stats:
        return

    # Format the "parent routine types" column.
    def _rtype_str(c: Counter) -> str:
        return "{" + ", ".join(f"{k}: {v}" for k, v in sorted(c.items())) + "}"

    rows = []
    for lang in sorted(stats.keys(), key=lambda x: -stats[x]["count"]):
        rows.append((lang, stats[lang]["count"], _rtype_str(stats[lang]["rtypes"])))

    # Column widths.
    col1 = max(len("language_type"), max(len(r[0]) for r in rows))
    col2 = max(len("records"), max(len(str(r[1])) for r in rows))
    col3 = max(len("parent routine types"), max(len(r[2]) for r in rows))

    sep = f"  {'-' * col1}  {'-' * col2}  {'-' * col3}"
    header = f"  {'language_type':<{col1}}  {'records':>{col2}}  {'parent routine types':<{col3}}"
    total = sum(s["count"] for s in stats.values())
    divider = f"  {'=' * (col1 + col2 + col3 + 4)}"

    print()
    print("=== Summary by language_type ===")
    print(header)
    print(sep)
    for lang, count, rtype_str in rows:
        print(f"  {lang:<{col1}}  {count:>{col2}}  {rtype_str:<{col3}}")
    print(divider)
    print(f"  Total: {total} records")
    print()


def _print_routine_type_summary(
    all_routines: Dict[int, Tuple[str, str]],
    records: List[dict],
) -> None:
    """Print aligned summary table grouped by parent routine type.

    The ``empty?`` column flags routine types that have *zero* SbRegion records
    — meaning their routine bodies may live in a different ``.Dat`` file.
    """
    # Count SbRegion records per routine type.
    rtype_record_count: Counter = Counter()
    for rec in records:
        rtype = rec.get("parent_routine_type")
        if rtype:
            rtype_record_count[rtype] += 1

    # Count distinct routines per type (across ALL routines, not just those
    # with SbRegion records, so FBD/SFC show up with 0).
    rtype_routine_count: Counter = Counter()
    for _oid, (_name, rtype) in all_routines.items():
        rtype_routine_count[rtype] += 1

    # Union of all known routine types.
    all_types = sorted(
        set(rtype_routine_count.keys()) | set(rtype_record_count.keys()),
        key=lambda t: -rtype_routine_count.get(t, 0),
    )

    if not all_types:
        return

    rows = []
    for rtype in all_types:
        n_routines = rtype_routine_count.get(rtype, 0)
        n_records = rtype_record_count.get(rtype, 0)
        empty_flag = "YES" if n_records == 0 else "no"
        rows.append((rtype or "<none>", n_routines, n_records, empty_flag))

    # Column widths.
    col1 = max(len("routine type"), max(len(r[0]) for r in rows))
    col2 = max(len("routines"), max(len(str(r[1])) for r in rows))
    col3 = max(len("sbregion records"), max(len(str(r[2])) for r in rows))
    col4 = max(len("empty?"), max(len(r[3]) for r in rows))

    sep = f"  {'-' * col1}  {'-' * col2}  {'-' * col3}  {'-' * col4}"
    header = (
        f"  {'routine type':<{col1}}  {'routines':>{col2}}  "
        f"{'sbregion records':>{col3}}  {'empty?':<{col4}}"
    )
    total_routines = sum(r[1] for r in rows)

    print("=== Summary by parent routine type ===")
    print(header)
    print(sep)
    for rtype, n_rout, n_rec, empty_flag in rows:
        print(
            f"  {rtype:<{col1}}  {n_rout:>{col2}}  {n_rec:>{col3}}  {empty_flag:<{col4}}"
        )
    print(sep)
    print(f"  Total routines: {total_routines}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect every record in SbRegion.Dat for an ACD project file. "
            "Pure read-only diagnostic — does not modify anything. "
            "Quiet mode by default; pass --verbose for per-record dump."
        )
    )
    parser.add_argument("--acd", required=True, metavar="PATH",
                        help="Path to the .ACD project file.")
    parser.add_argument("--routine", metavar="NAME",
                        help="Show all records whose parent routine matches NAME "
                             "(always shown, even in quiet mode).")
    parser.add_argument("--json", metavar="FILE",
                        help="Write the full record list to a JSON file.")
    parser.add_argument("--limit", type=int, default=10, metavar="N",
                        help="Stop after showing N records in --verbose mode "
                             "(0 = no limit; default: 10).")
    parser.add_argument("--verbose", action="store_true",
                        help="Dump every record up to --limit instead of quiet mode.")
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

    # region_map rows: (object_id, parent_id, unknown, seq_no)
    cur.execute("SELECT object_id, parent_id, unknown, seq_no FROM region_map")
    region_map_rows = cur.fetchall()

    # all_routines: object_id → (routine_name, routine_type_str)
    all_routines = _get_all_routines(cur)

    # -----------------------------------------------------------------------
    # Step 3: Parse every record in SbRegion.Dat.
    # -----------------------------------------------------------------------
    import os
    sbregion_path = os.path.join(temp_dir, "SbRegion.Dat")

    print(f"[inspect_sbregion] Parsing {sbregion_path} …", file=sys.stderr)
    records = _build_record_list(sbregion_path, name_lookup, region_map_rows)

    # -----------------------------------------------------------------------
    # Step 4: Annotate each record with parent routine name and type.
    # -----------------------------------------------------------------------
    for rec in records:
        parent_id = rec.get("parent_id")
        routine_info = all_routines.get(parent_id) if parent_id is not None else None
        rec["parent_routine_name"] = routine_info[0] if routine_info else _lookup_routine_name(parent_id, cur)
        rec["parent_routine_type"] = routine_info[1] if routine_info else None

    # -----------------------------------------------------------------------
    # Step 5: Optional JSON dump (uses ALL records before any filter).
    # -----------------------------------------------------------------------
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, default=str)
        print(f"[inspect_sbregion] JSON written to {args.json}", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Step 6: Header line.
    # -----------------------------------------------------------------------
    mode_note = "" if args.verbose else " (quiet mode; pass --verbose for per-record dump)"
    print(f"Inspected {len(records)} records from {acd_path}{mode_note}")

    # -----------------------------------------------------------------------
    # Step 7: Separate records for --routine filter (show regardless of mode).
    # -----------------------------------------------------------------------
    routine_filter_records: List[dict] = []
    if args.routine:
        filter_name = args.routine.lower()
        routine_filter_records = [
            r for r in records
            if (r.get("parent_routine_name") or "").lower() == filter_name
        ]

    # -----------------------------------------------------------------------
    # Step 8: Quiet mode — one sample per unique language_type.
    # Verbose mode — dump every record up to --limit.
    # -----------------------------------------------------------------------
    if args.verbose:
        limit = args.limit if args.limit > 0 else len(records)
        shown = 0
        for rec in records:
            if shown >= limit:
                remaining = len(records) - shown
                print(f"… {remaining} more record(s) not shown (use --limit 0 to see all).")
                break
            _print_record(rec, rec.get("parent_routine_name"))
            shown += 1
    else:
        # One sample record per unique language_type.
        seen_langs: set = set()
        for rec in records:
            lang = rec.get("language_type")
            if lang not in seen_langs:
                seen_langs.add(lang)
                print(f"\n--- Sample for language_type={lang!r} ---")
                _print_record(rec, rec.get("parent_routine_name"))

    # -----------------------------------------------------------------------
    # Step 9: Show --routine filter matches (all of them, always).
    # -----------------------------------------------------------------------
    if routine_filter_records:
        print(f"\n--- Records for routine {args.routine!r} ({len(routine_filter_records)} record(s)) ---")
        for rec in routine_filter_records:
            _print_record(rec, rec.get("parent_routine_name"))
    elif args.routine:
        print(f"\n(No records found for routine {args.routine!r})")

    # -----------------------------------------------------------------------
    # Step 10: Summary tables LAST so they're always visible on screen.
    # -----------------------------------------------------------------------
    _print_language_type_summary(records)
    _print_routine_type_summary(all_routines, records)

    return 0


if __name__ == "__main__":
    sys.exit(main())
