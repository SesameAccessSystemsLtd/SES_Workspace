#!/usr/bin/env python3
"""
stage_pack_proposals.py — Stage P1 overnight pack-assignment REQUESTS.

The overnight pack chain design is REQUESTS-gated: the orchestrator COMPUTES candidate
part → pack assignments in offline scan mode and stages a single required-approval
REQUESTS file (pack_assign) for SAP to apply. It never writes Asana or ops.db itself.
Running it does two things:
  1. Scans ops.db:parts_control for unassigned parts (via unassigned_parts_net logic).
  2. Matches each to a verified build pack in ops.db:build_assembly_packs by job prefix
     + pack_reference prefix; UNAMBIGUOUS matches are bundled into one pack_assign
     REQUESTS proposal; ambiguous/no-pack parts are flagged to the review file, never
     guessed.

To be run AFTER the 01:00 mirror sync (mirror sync populates parts_control for commits
from the previous day). Schedule at ~02:00. On Giles's approval SAP applies the proposal.

Usage:
  python stage_pack_proposals.py [--out REQUESTS_DIR] [--dry-run]
Read-only except writing a REQUESTS proposal + a review CSV.
"""
import argparse
import csv
import datetime
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
sys.path.insert(0, str(WS / "lib"))
try:
    from path_config import p as _p
    REQUESTS_DIR = Path(_p("queues.requests"))
    DEFAULT_DB_PATH = _p("db.ops")
except Exception:
    REQUESTS_DIR = WS / "queues" / "REQUESTS"
    DEFAULT_DB_PATH = str(WS / "db" / "ops.db")

BUILD_JOBS = ["1031", "1037", "1366", "1365", "1373", "1535", "1536", "1538", "1539",
              "1545", "1555", "1572", "1575", "1577", "1578", "1579", "1580", "1581",
              "1582", "1588", "1591", "1594", "1595", "1669", "1670"]

REVIEW_NAME = "pack-assignment-review.csv"
OUT_REVIEW  = WS / "data" / "os_status"


def load_packs(conn):
    """{job: [ {gid, name, ref} ]} from build_assembly_packs (live pack tasks)."""
    packs = defaultdict(list)
    for r in conn.execute(
        "SELECT asana_task_gid, pack_name, pack_reference FROM build_assembly_packs"
    ).fetchall():
        gid, name, ref = r[0], r[1] or "", r[2] or ""
        ref = ref or name or ""
        job = ref[:4] if ref[:4].isdigit() else (name[:4] if name[:4].isdigit() else "")
        if job and job in BUILD_JOBS:
            packs[job].append({"gid": gid, "name": name, "ref": ref})
    return packs


def match_pack(packs_by_job, job, sesame_pn):
    """Return [(gid, name, confidence)] for a part. Unambiguous = exactly one candidate
    whose pack_reference is a PREFIX of the part's pack code."""
    cands = packs_by_job.get(job, [])
    if not cands:
        return [], "no_packs_for_job"
    # part pack code = first 3 components, e.g. 1595.1014 -> 1595.1014
    code = ".".join(sesame_pn.split(".")[:3]) if "." in sesame_pn else sesame_pn
    # exact-prefix matches on pack_reference (strip trailing '.')
    exact = [c for c in cands if c["ref"].rstrip(".").startswith(code) or code.rstrip(".").startswith(c["ref"].rstrip("."))]
    if exact:
        uniq = set(e["gid"] for e in exact)
        if len(uniq) == 1:
            return [(exact[0]["gid"], exact[0]["name"], "exact")], "unique"
        return [(e["gid"], e["name"], "exact") for e in exact], "ambiguous"
    # Fuzzy: same job, pick the pack whose ref shares the most leading tokens.
    if cands:
        scored = []
        for c in cands:
            ccode = c["ref"].rstrip(".").split(".")
            overlap = sum(1 for a, b in zip(ccode, code.split(".")) if a == b)
            scored.append((overlap, c["gid"], c["name"]))
        top = max(scored, key=lambda x: x[0])
        if top[0] >= 1:
            return [(top[1], top[2], "fuzzy")], "fuzzy"
    return [], "no_match"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument("--out", default=REQUESTS_DIR)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    packs_by_job = load_packs(conn)

    # unassigned distinct parts per job
    rows = conn.execute("""
        SELECT sesame_pn, build_assembly_pack_gid, vendor_name, asana_task_gid
        FROM parts_control
        WHERE sesame_pn IS NOT NULL AND sesame_pn != ''
    """).fetchall()
    by_pn = {}
    for r in rows:
        pn = r["sesame_pn"]
        if pn not in by_pn:
            by_pn[pn] = {"linked": bool(r["build_assembly_pack_gid"]),
                         "vendor": r["vendor_name"], "gid": r["asana_task_gid"]}
        else:
            by_pn[pn]["linked"] = by_pn[pn]["linked"] or bool(r["build_assembly_pack_gid"])

    confirmed, ambiguous, nopack = [], [], []
    for pn, d in by_pn.items():
        if d["linked"] or not d["gid"]:
            continue
        job = pn[:4]
        if job not in BUILD_JOBS:
            continue
        cands, how = match_pack(packs_by_job, job, pn)
        if how == "unique":
            confirmed.append((pn, d["gid"], d["vendor"], cands[0][0], cands[0][1]))
        elif how == "ambiguous":
            ambiguous.append((pn, d["gid"], d["vendor"], [c[1] for c in cands]))
        else:
            nopack.append((pn, d["gid"], d["vendor"], how))

    print(f"Unassigned distinct parts: {len(confirmed)} confirmed · {len(ambiguous)} ambiguous · {len(nopack)} no-pack")
    if args.dry_run:
        print("DRY RUN — no REQUESTS proposal staged.")
        for p in confirmed[:10]:
            print(f"   {p[0]} -> pack {p[3]} ({p[4]})")
        return

    # Write a REQUESTS pack_assign proposal (unambiguous only).
    if confirmed:
        parts = [{"task_gid": gid, "sesame_pn": pn, "qty": None, "part_name": vendor}
                 for pn, gid, vendor, _, _ in confirmed]
        # Group by pack to create one file per pack.
        by_pack = defaultdict(list)
        for pn, gid, vendor, pgid, pname in confirmed:
            by_pack[pgid].append({"task_gid": gid, "sesame_pn": pn, "qty": None,
                                  "part_name": vendor, "pack_name": pname})
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M")
        n_files = 0
        for pgid, plist in by_pack.items():
            pack_name = plist[0]["pack_name"]
            fname = f"{ts}-hermes-overnight-pack-{pack_name[:20]}.md"
            payload = {"action": "pack_assign", "pack_gid": pgid, "pack_name": pack_name,
                       "parts": plist}
            md = (f"---\nFrom: hermes (overnight pack chain)\nTo: asana_agent\n"
                  f"Type: pack_assign\nAction: pack_assign\nStatus: PENDING\n"
                  f"Priority: normal\nCreated: {ts}\n---\n\n"
                  f"## Request\n\n**Action:** pack_assign\n\nAssign {len(plist)} part(s) to "
                  f"assembly pack **{pack_name}** ({pgid}). This is the staged overnight "
                  f"proposal — review before applying.\n\n"
                  f"## Context\nUnambiguous pack match from parts_control (no prior link). "
                  f"Computed by stage_pack_proposals.py after the 01:00 mirror sync.\n")
            (out_dir / fname).write_text(md, encoding="utf-8")
            (out_dir / fname[:-3] + ".json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
            n_files += 1
        print(f"Staged {n_files} REQUESTS pack_assign proposal(s) in {out_dir}")
    else:
        print("No unambiguous unassigned parts to stage.")

    # Review CSV for ambiguous + no-pack (flagged for Giles, never guessed).
    amb_rows = [{"sesame_pn": p[0], "vendor": p[2], "candidate_packs": " | ".join(p[3])}
                for p in ambiguous]
    np_rows = [{"sesame_pn": p[0], "vendor": p[2], "reason": p[3]} for p in nopack]
    os.makedirs(OUT_REVIEW, exist_ok=True)
    if amb_rows or np_rows:
        with open(OUT_REVIEW / REVIEW_NAME, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["sesame_pn", "vendor", "candidate_packs", "reason"])
            w.writeheader()
            for r in amb_rows:
                r["reason"] = "ambiguous"
                w.writerow(r)
            for r in np_rows:
                r["candidate_packs"] = ""
                w.writerow(r)
        print(f"Review file (ambiguous/no-pack): {OUT_REVIEW / REVIEW_NAME}")


if __name__ == "__main__":
    main()
