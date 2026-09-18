#!/usr/bin/env python3
"""backfill_subcon_pack.py — CHG-3A (HITL-gated). DRY-RUN ONLY.

Diagnoses the subcon/finish pack-inheritance gap: parts_control rows whose
`build_assembly_pack_gid` is NULL but whose `vendor_name` is a finish/process
(Powder Coating / Engraving / Laser Cut / Machining / plating …) should inherit a
build pack from their parent blank part.

This script NEVER writes ops.db. It reads ops.db READ-ONLY and writes a CSV plan
of the proposed NULL -> pack assignments for Giles to review. Applying the UPDATE
is a separate, gated step (do not call db_writer here).

Usage:
    python backfill_subcon_pack.py [--db PATH] [--out CSV]
"""

import argparse
import csv
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
DEFAULT_DB = r"C:/Users/GilesHugo/SES_Workspace/db/ops.db"
DEFAULT_OUT = WS / "data" / "os_status" / "review" / "subcon_pack_backfill_plan.csv"

# Finish/process vendors whose NULL-pack rows are candidates for pack inheritance.
# (normalised lowercase; source/process names, NOT actual order-from vendors.)
FINISH_PROCESS_VENDORS = {
    "powder coating", "engraving", "laser cut", "lasercut", "machining",
    "anodising", "electroplating", "galvanising", "marine lacquer",
    "yellow zinc plating", "laser cut/machining", "electroplating magic wand",
    "machining bom", "laser cut bom", "lasercut bom",
}


def _pack_key(sesame_pn):
    m = re.match(r"^(\d{4}\.\d{1,4})", (sesame_pn or "").strip())
    return m.group(1).rstrip(".").lower() if m else None


def load(conn):
    """Return (finish_candidates, pn_to_packs, packkey_to_packs, pack_refs)."""
    c = conn
    c.row_factory = sqlite3.Row
    pn_to_packs = defaultdict(set)
    packkey_to_packs = defaultdict(set)
    candidates = []
    for r in c.execute(
            "SELECT asana_task_gid, sesame_pn, vendor_name, build_assembly_pack_gid "
            "FROM parts_control"):
        pn = (r["sesame_pn"] or "").strip()
        vendor = (r["vendor_name"] or "").strip()
        pack_gid = (r["build_assembly_pack_gid"] or "").strip()
        key = _pack_key(pn)
        if pack_gid:
            pn_to_packs[pn.lower()].add(pack_gid)
            if key:
                packkey_to_packs[key].add(pack_gid)
        elif vendor.lower() in FINISH_PROCESS_VENDORS:
            candidates.append({
                "asana_task_gid": r["asana_task_gid"],
                "sesame_pn": pn,
                "vendor_name": vendor,
                "pack_key": key,
            })
    pack_refs = {}
    for r in c.execute("SELECT asana_task_gid, pack_reference, pack_name FROM build_assembly_packs"):
        pack_refs[r["asana_task_gid"]] = (r["pack_reference"] or r["pack_name"] or r["asana_task_gid"])
    return candidates, pn_to_packs, packkey_to_packs, pack_refs


def plan(candidates, pn_to_packs, packkey_to_packs):
    """Return rows of {.., resolution, planned_pack_gids, note}."""
    rows = []
    for cand in candidates:
        pn = cand["sesame_pn"]
        exact = pn_to_packs.get(pn.lower(), set())
        key = cand["pack_key"]
        key_packs = packkey_to_packs.get(key, set()) if key else set()
        if exact:
            resolution = "direct"
            planned = sorted(exact)
            note = "parent blank (same sesame_pn) already pack-linked"
        elif key_packs:
            resolution = "ambiguous"
            planned = sorted(key_packs)
            note = (f"no exact sesame_pn match — attributing to ALL {len(key_packs)} pack(s) "
                    f"in pack-key {key} (attribute to all, never guess one)")
        else:
            resolution = "unresolved"
            planned = []
            note = "no parent pack found (no same-pn and no pack-key packs)"
        rows.append({
            "asana_task_gid": cand["asana_task_gid"],
            "sesame_pn": pn,
            "vendor_name": cand["vendor_name"],
            "pack_key": key or "",
            "resolution": resolution,
            "planned_pack_gids": planned,
            "note": note,
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description="CHG-3A subcon-pack backfill DRY-RUN")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    candidates, pn_to_packs, packkey_to_packs, pack_refs = load(conn)
    conn.close()

    rows = plan(candidates, pn_to_packs, packkey_to_packs)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["asana_task_gid", "sesame_pn", "vendor_name", "pack_key",
                    "resolution", "planned_pack_gids", "planned_pack_refs", "note"])
        for r in rows:
            refs = " | ".join(pack_refs.get(g, g) for g in r["planned_pack_gids"])
            w.writerow([r["asana_task_gid"], r["sesame_pn"], r["vendor_name"],
                        r["pack_key"], r["resolution"],
                        " ".join(r["planned_pack_gids"]), refs, r["note"]])

    from collections import Counter
    by_res = Counter(r["resolution"] for r in rows)
    print(f"DRY-RUN complete. {len(rows)} finish/process rows with NULL pack.")
    print(f"  resolution breakdown: {dict(by_res)}")
    print(f"  plan written to: {out}")


if __name__ == "__main__":
    main()
