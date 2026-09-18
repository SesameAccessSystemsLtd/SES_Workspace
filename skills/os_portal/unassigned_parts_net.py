#!/usr/bin/env python3
"""
unassigned_parts_net.py — Stage P1 "unassigned-parts net".

Finds parts_control rows with NO build pack link (dedup'd by sesame_pn — a part
that has at least one linked row is considered linked). This is the "missing
metadata" detector for the drawing-to-part assignment stage: every part that
should belong to a pack but has no pack reference is surfaced for verification.

Filtered to real build jobs (numeric 4-digit project prefix in sesame_pn). Parts
with a part number that legitimately never links to a pack (e.g. welded fixings,
ISO/DIN fasteners, non-procured hardware) will show here — that is EXPECTED and is
reviewed by Giles, not auto-fixed. The report is read-only; fixing is via REQUESTS
(pack_assign) after Giles confirms the intended pack.

Output: data/os_status/unassigned-parts-<job>.csv or a summary to stdout.
Usage:
  python unassigned_parts_net.py [--out DIR] [--min N]      # summary + CSV

Read-only — never writes to Asana or ops.db.
"""
import argparse
import csv
import datetime
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
sys.path.insert(0, str(WS / "lib"))
try:
    from path_config import p as _p
    DEFAULT_DB_PATH = _p("db.ops")
except Exception:
    DEFAULT_DB_PATH = str(WS / "db" / "ops.db")

# Real build jobs (historical) — expand as new projects land.
BUILD_JOBS = ["1031", "1037", "1366", "1365", "1373", "1535", "1536", "1555",
              "1572", "1575", "1577", "1578", "1579", "1580", "1582", "1588",
              "1591", "1594", "1595", "1669", "1670"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument("--out", default=WS / "data" / "os_status")
    ap.add_argument("--min", type=int, default=1, help="only report jobs with >= N unassigned parts")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    job_cols = ",".join(f"'j{j}'" for j in BUILD_JOBS)
    rows = conn.execute(f"""
        SELECT sesame_pn, build_assembly_pack_gid, association_status, is_completed,
               vendor_name, procurement_task_gid, asana_task_gid
        FROM parts_control
        WHERE sesame_pn LIKE '15%' OR sesame_pn LIKE '14%' OR sesame_pn LIKE '16%'
           OR sesame_pn LIKE '13%' OR sesame_pn LIKE '10%'
    """).fetchall()

    # Asana link (the live source of truth) — a task GID that isn't null/empty.
    def has_link(r):
        return bool(r["build_assembly_pack_gid"])

    # Dedup by sesame_pn: linked if ANY row linked.
    by_pn = defaultdict(lambda: {"linked": False, "rows": []})
    for r in rows:
        key = r["sesame_pn"] or ""
        if not key:
            continue
        by_pn[key]["linked"] = by_pn[key]["linked"] or has_link(r)
        by_pn[key]["rows"].append(r)

    # Group unassigned by job prefix.
    unassigned_by_job = defaultdict(list)
    for pn, d in by_pn.items():
        if d["linked"]:
            continue
        m = pn[:4]
        if m in BUILD_JOBS:
            # count unassigned distinct parts (not duplicate rows)
            if pn not in [u["sesame_pn"] for u in unassigned_by_job[m]]:
                unassigned_by_job[m].append({
                    "sesame_pn": pn,
                    "vendor": d["rows"][0]["vendor_name"],
                    "asana_task_gid": d["rows"][0]["asana_task_gid"],
                })

    # Report
    today = datetime.date.today().strftime("%Y-%m-%d")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    all_rows = []
    for job in sorted(unassigned_by_job):
        items = unassigned_by_job[job]
        if len(items) < args.min:
            continue
        print(f"Job {job}: {len(items)} unassigned part(s) (no pack link)")
        for it in sorted(items, key=lambda x: x["sesame_pn"]):
            print(f"   {it['sesame_pn']:<24} {it['vendor'] or '-'}")
            all_rows.append({"job": job, "sesame_pn": it["sesame_pn"],
                             "vendor": it["vendor"], "asana_task_gid": it["asana_task_gid"]})

    if all_rows:
        csv_path = out_dir / f"unassigned-parts-{today}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["job", "sesame_pn", "vendor", "asana_task_gid"])
            w.writeheader()
            w.writerows(all_rows)
        print(f"\nWrote {len(all_rows)} unassigned parts to {csv_path}")
    else:
        print("No unassigned parts in the scanned build jobs.")


if __name__ == "__main__":
    main()
