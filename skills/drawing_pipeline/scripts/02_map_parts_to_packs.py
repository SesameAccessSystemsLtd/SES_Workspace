"""
02_map_parts_to_packs.py
Reads data/{job_prefix}_drawing_boms.csv (output of 02_extract_drawing_boms.py)
and maps each extracted part to its parts_control row via sesame_pn lookup.

Normalization:
  All 9 rules from PN_EDGE_CASES.md are applied via lib/pn_normalizer.py.
  Rule 2 (multi-PN cell splitting) is applied per BOM row before indexing.
  Rules 8 and 9 generate fallback candidates for matching.

Matching:
  - Exact match on normalized sesame_pn first
  - Rule 8 fallbacks (revision stripping) tried in order
  - Rule 9 aliases applied to every candidate

Multi-pack:
  If same sesame_pn appears in 2+ packs, is_multi=True.
  Stage 3 will prepend '*' to task name and list all packs in notes.

Updates ops.db parts_control.build_assembly_pack_gid.
Writes:
  data/{job_prefix}_unresolved_{HHMMSS}.csv   — extracted parts with no PC match
  data/{job_prefix}_multi_pack_{HHMMSS}.csv   — parts found in more than one pack

Usage:
  python 02_map_parts_to_packs.py --job-prefix 1575 [--dry-run] [--reset]

--reset clears all build_assembly_pack_gid values for this job before mapping.
"""

import argparse
import csv
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib.pn_normalizer import candidates, load_aliases, split_multi_pn

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p
DB_PATH   = _p("db.ops")
SKILL_DIR = Path(__file__).parent.parent
DATA_DIR  = SKILL_DIR / "data"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-prefix", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true",
                        help="Clear existing build_assembly_pack_gid for this job before mapping")
    args = parser.parse_args()

    bom_path = DATA_DIR / f"{args.job_prefix}_drawing_boms.csv"
    if not bom_path.exists():
        print(f"ERROR: {bom_path} not found. Run 02_extract_drawing_boms.py first.")
        return

    aliases = load_aliases()
    print(f"Aliases loaded: {len(aliases)}")

    # Load drawing BOM CSV
    bom_rows = []
    with open(bom_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            bom_rows.append(row)
    print(f"Drawing BOM rows loaded: {len(bom_rows)}")

    # Build index: normalized_pn -> list of pack entries
    # Rule 2 (multi-PN cell) is applied here so each index key is a single PN.
    pn_to_packs: dict[str, list] = defaultdict(list)
    skipped_unrecognised = 0
    for row in bom_rows:
        raw_cell = row["part_number"]
        # Rule 2: split cell that contains multiple concatenated PNs
        for raw_pn in split_multi_pn(raw_cell, args.job_prefix):
            cands = candidates(raw_pn, args.job_prefix, aliases)
            if not cands:
                skipped_unrecognised += 1
                continue
            key = cands[0]  # primary normalized form as index key
            pn_to_packs[key].append({
                "pack_reference": row["pack_reference"],
                "pack_gid":       row["pack_gid"],
                "qty":            row["qty"],
                "description":    row["description"],
                "vendor":         row["vendor"],
                "raw_pn":         raw_pn,
            })

    print(f"Distinct normalized part numbers: {len(pn_to_packs)}")
    if skipped_unrecognised:
        print(f"  Skipped (unrecognised job prefix): {skipped_unrecognised}")
    multi_pack_pns = {pn: packs for pn, packs in pn_to_packs.items()
                      if len({p["pack_reference"] for p in packs}) > 1}
    print(f"  Multi-pack parts (before PC match): {len(multi_pack_pns)}")

    # Load parts_control index: whitespace-stripped sesame_pn -> row
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if args.reset:
        if not args.dry_run:
            cur.execute(
                "UPDATE parts_control SET build_assembly_pack_gid = NULL WHERE sesame_pn LIKE ?",
                (f"{args.job_prefix}.%",)
            )
            conn.commit()
            print(f"Reset build_assembly_pack_gid for all {args.job_prefix}.* parts")
        else:
            print(f"[dry-run] Would reset build_assembly_pack_gid for {args.job_prefix}.* parts")

    cur.execute(
        "SELECT id, asana_task_gid, sesame_pn, part_name, order_qty "
        "FROM parts_control WHERE sesame_pn LIKE ?",
        (f"{args.job_prefix}.%",)
    )
    pc_rows = {
        re.sub(r'\s+', '', r["sesame_pn"]): dict(r)
        for r in cur.fetchall()
        if r["sesame_pn"]
    }
    print(f"parts_control rows for {args.job_prefix}: {len(pc_rows)}")

    # Match drawing BOM entries to parts_control
    now = datetime.now(timezone.utc).isoformat()
    stats = {"matched": 0, "multi_pack": 0, "not_in_pc": 0}
    unresolved = []
    multi_pack_out = []

    for pn, pack_list in pn_to_packs.items():
        # pn is already normalized (primary candidate). Generate full candidate set
        # (includes Rule 8 fallbacks + aliases) for lookup against PC index.
        pc_row = None
        matched_candidate = None
        for c in candidates(pn, args.job_prefix, aliases):
            pc_row = pc_rows.get(c)
            if pc_row:
                matched_candidate = c
                break

        if pc_row is None:
            stats["not_in_pc"] += 1
            for p in pack_list:
                unresolved.append({
                    "part_number":    pn,
                    "raw_pn":         p["raw_pn"],
                    "pack_reference": p["pack_reference"],
                    "qty":            p["qty"],
                    "description":    p["description"],
                    "vendor":         p["vendor"],
                    "reason":         "not_in_parts_control",
                })
            continue

        # Determine primary pack (first by pack_reference sort order)
        distinct_packs = list({p["pack_reference"]: p for p in pack_list}.values())
        distinct_packs.sort(key=lambda p: p["pack_reference"])
        primary_pack = distinct_packs[0]
        is_multi = len(distinct_packs) > 1

        if is_multi:
            stats["multi_pack"] += 1
            for p in distinct_packs:
                multi_pack_out.append({
                    "sesame_pn":      pn,
                    "matched_as":     matched_candidate,
                    "asana_task_gid": pc_row["asana_task_gid"],
                    "pack_reference": p["pack_reference"],
                    "pack_gid":       p["pack_gid"],
                    "qty":            p["qty"],
                })

        if not args.dry_run:
            # last_synced_at belongs to the nightly mirror sync — it is how we tell
            # a live Asana task from a tombstone. Stamping it here made 44 dead rows
            # look freshly synced and stage 4 then PUT to task GIDs Asana had deleted.
            cur.execute(
                "UPDATE parts_control SET build_assembly_pack_gid = ? WHERE id = ?",
                (primary_pack["pack_gid"], pc_row["id"])
            )
        stats["matched"] += 1

    if not args.dry_run:
        conn.commit()
    conn.close()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    unresolved_path = DATA_DIR / f"{args.job_prefix}_unresolved.csv"
    multi_pack_path = DATA_DIR / f"{args.job_prefix}_multi_pack.csv"

    if not args.dry_run:
        with open(unresolved_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["part_number", "raw_pn", "pack_reference",
                                              "qty", "description", "vendor", "reason"])
            w.writeheader()
            w.writerows(unresolved)

        with open(multi_pack_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["sesame_pn", "matched_as", "asana_task_gid",
                                              "pack_reference", "pack_gid", "qty"])
            w.writeheader()
            w.writerows(multi_pack_out)

    print(f"\n{'DRY RUN - ' if args.dry_run else ''}Summary:")
    print(f"  Matched to parts_control:        {stats['matched']}")
    print(f"    of which multi-pack:           {stats['multi_pack']}")
    print(f"  On drawing but not in PC:        {stats['not_in_pc']}")
    print(f"    (expected: welded assy, fixings, non-procured)")
    print(f"  Unresolved CSV:  {unresolved_path}")
    print(f"  Multi-pack CSV:  {multi_pack_path}")


if __name__ == "__main__":
    main()
