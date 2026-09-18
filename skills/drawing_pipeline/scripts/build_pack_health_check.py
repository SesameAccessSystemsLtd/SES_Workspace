"""
build_pack_health_check.py
Compares expected parts per build pack (from drawing_boms.csv) against
parts actually linked in parts_control (via build_assembly_pack_gid).

Matching key: the pack REFERENCE string (e.g. "1588.1003.A.HorizontalStairs.")
  - parts_control.build_assembly_pack_gid holds the pack reference string (linked_by_ref)
  - drawing_boms.csv pack_reference holds the same reference

For each project+pack:
  - expected  = distinct part numbers in the drawing BOM CSV for that pack_reference
  - linked    = parts_control rows whose build_assembly_pack_gid = pack_reference
  - matched   = expected parts that have a linked parts_control row
  - missing   = expected parts with NO linked parts_control row

Output: data/build_pack_health_check.csv  and printed summary.

Usage:
  python build_pack_health_check.py --all
  python build_pack_health_check.py --project 1588
"""
import argparse
import csv
import glob
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p

DATA_DIR = Path(r"C:\Users\GilesHugo\SES_Workspace\skills\drawing_pipeline\data")


def norm_pn(pn: str) -> str:
    """Normalise a part number for matching: strip whitespace."""
    return re.sub(r"\s+", "", pn or "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=None, help="e.g. 1588. Omit for all projects.")
    parser.add_argument("--all", action="store_true", help="Run for all projects with drawing_boms.csv")
    args = parser.parse_args()

    conn = sqlite3.connect(_p("db.ops"))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # All parts_control rows by build_assembly_pack_gid -> set of normalised pn.
    #
    # build_assembly_pack_gid historically held the pack REFERENCE string, but
    # GID is now the canonical format (see 06_normalise_pack_gid.py). Both
    # conventions can be present mid-migration, so resolve GIDs back to their
    # reference string here — everything downstream in this script keys on the
    # reference, so that stays the map key regardless of what the column holds.
    gid_to_ref = {}
    cur.execute("SELECT asana_task_gid, pack_reference FROM build_assembly_packs")
    for r in cur.fetchall():
        if r["asana_task_gid"] and r["pack_reference"]:
            gid_to_ref[r["asana_task_gid"].strip()] = r["pack_reference"].strip()

    linked_by_ref = defaultdict(set)
    cur.execute("SELECT sesame_pn, build_assembly_pack_gid, asana_task_gid, order_qty, part_name FROM parts_control")
    all_pc_refs = set()
    unresolved_gids = set()
    for r in cur.fetchall():
        ref = r["build_assembly_pack_gid"]
        pn = r["sesame_pn"]
        if ref and pn:
            ref = ref.strip()
            if ref.isdigit():
                resolved = gid_to_ref.get(ref)
                if resolved is None:
                    # A GID with no pack row — report rather than silently drop.
                    unresolved_gids.add(ref)
                    continue
                ref = resolved
            linked_by_ref[ref].add(norm_pn(pn))
            all_pc_refs.add(ref)

    if unresolved_gids:
        print(f"WARNING: {len(unresolved_gids)} pack GID(s) in parts_control have "
              f"no build_assembly_packs row: {sorted(unresolved_gids)[:10]}")

    # Build a lookup: given a CSV pack reference, find the matching parts_control
    # reference key. Handles both conventions:
    #   - exact match ("1588.1003.A.HorizontalStairs.")
    #   - CSV short code is a PREFIX of the PC full ref ("1575.1001.A" -> "1575.1001.A.ServiceHatches.DogLeg.")
    def resolve_pc_key(csv_ref: str) -> str | None:
        csv_ref = csv_ref.strip()
        if csv_ref in linked_by_ref:
            return csv_ref
        # Try prefix match (CSV short code -> PC full ref)
        for pc_ref in all_pc_refs:
            if pc_ref.startswith(csv_ref + ".") or pc_ref == csv_ref:
                return pc_ref
        return None

    # Expected parts per pack from drawing_boms.csv, keyed by pack_reference
    expected_by_pack = defaultdict(lambda: {"pns": set(), "rows": 0})
    bom_files = glob.glob(str(DATA_DIR / "*_drawing_boms.csv"))
    if args.project:
        bom_files = [f for f in bom_files if os.path.basename(f).startswith(args.project)]

    for bomf in bom_files:
        proj = os.path.basename(bomf).split("_")[0]
        with open(bomf, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ref = row.get("pack_reference", "").strip()
                pn = row.get("part_number", "")
                if ref and pn:
                    expected_by_pack[ref]["pns"].add(norm_pn(pn))
                    expected_by_pack[ref]["rows"] += 1
                    expected_by_pack[ref]["proj"] = proj

    rows_out = []
    print(f"{'Pack':<52} {'P':<5} {'Exp':>5} {'Lnk':>5} {'Mtch':>5} {'Miss':>5}  Status")
    print("-" * 100)
    for ref in sorted(expected_by_pack, key=lambda r: expected_by_pack[r]["proj"] + r):
        info = expected_by_pack[ref]
        exp = len(info["pns"])
        pc_key = resolve_pc_key(ref)
        linked = len(linked_by_ref.get(pc_key, set())) if pc_key else 0
        matched = len(info["pns"] & linked_by_ref.get(pc_key, set())) if pc_key else 0
        missing = exp - matched
        extra = (len(linked_by_ref.get(pc_key, set())) - matched) if pc_key else 0
        status = "OK" if missing == 0 else f"MISSING {missing}"
        print(f"{ref:<52} {info.get('proj',''):<5} {exp:>5} {linked:>5} {matched:>5} {missing:>5}  {status}")
        rows_out.append({
            "pack_reference": ref,
            "resolved_pc_ref": pc_key or "",
            "project": info.get("proj", ""),
            "expected_parts": exp,
            "linked_parts": linked,
            "matched_parts": matched,
            "missing_parts": missing,
            "extra_linked_parts": extra,
        })

    # Also log the actual missing part numbers for the worst packs
    out_path = DATA_DIR / "build_pack_health_check.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()) if rows_out else [
            "pack_reference", "project", "expected_parts", "linked_parts",
            "matched_parts", "missing_parts", "extra_linked_parts"])
        w.writeheader()
        w.writerows(rows_out)

    print(f"\nOutput: {out_path}")
    conn.close()


if __name__ == "__main__":
    main()
