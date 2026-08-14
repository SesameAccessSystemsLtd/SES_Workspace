"""
03_write_to_asana.py
Per matched parts_control task, writes four things to Asana in a single PUT:
  1. Build Packs reference (GID 1211660742155293) = assembly pack task GID
  2. Notes — appends "{qty} off in Assembly Pack {pack_name}" (dedup-safe)
  3. URL field (GID 1210822477699621) = "#DRAWING_URL_TBC" placeholder
  4. Task name — prepends "*" if part appears in more than one assembly pack

IMPORTANT: Run inside SAP (asana_agent) context only.

Usage:
  python 03_write_to_asana.py --job-prefix 1575 [--dry-run] [--limit N]

Output:
  data/{job_prefix}_write_log.csv  — per-task result
  data/{job_prefix}_skipped.csv    — tasks not updated and why
"""

import argparse
import csv
import os
import re
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)

ASANA_TOKEN          = os.getenv("ASANA_PERSONAL_ACCESS_TOKEN")
HEADERS              = {"Authorization": f"Bearer {ASANA_TOKEN}",
                        "Accept": "application/json",
                        "Content-Type": "application/json"}
BASE                 = "https://app.asana.com/api/1.0"

BUILD_PACKS_REF_GID  = "1211660742155293"   # reference field → assembly pack task GID
# URL_FIELD_GID intentionally omitted — field 1210822477699621 is on assembly pack tasks,
# not Parts Control tasks. Dropbox URL pipeline will handle this separately.

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p
DB_PATH              = _p("db.ops")
SKILL_DIR            = Path(__file__).parent.parent
DATA_DIR             = SKILL_DIR / "data"
PAUSE                = 0.3  # seconds between API calls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_qty(part_name: str, order_qty: float | None) -> int:
    """Extract BOM qty from task name prefix 'N off ...' (space or tab delimited).
    Falls back to order_qty if parse fails."""
    m = re.match(r"^(\d+)[\s\t]+off", part_name or "")
    if m:
        return int(m.group(1))
    if order_qty is not None:
        return int(order_qty)
    return 0


def pack_note_line(qty: int, pack_name: str) -> str:
    return f"{qty} off in Assembly Pack {pack_name}"


def already_has_note(existing_notes: str, pack_reference: str) -> bool:
    """Return True if notes already contain a reference to this pack."""
    return f"Assembly Pack {pack_reference}" in (existing_notes or "")


def asana_get_task(task_gid: str) -> dict | None:
    r = requests.get(
        f"{BASE}/tasks/{task_gid}",
        headers=HEADERS,
        params={"opt_fields": "name,notes,custom_fields.gid,custom_fields.display_value"}
    )
    if r.status_code != 200:
        return None
    return r.json().get("data", {})


def asana_put_task(task_gid: str, payload: dict) -> tuple[bool, str]:
    r = requests.put(f"{BASE}/tasks/{task_gid}", headers=HEADERS, json={"data": payload})
    if r.status_code == 200:
        return True, "ok"
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


def get_current_field_value(task_data: dict, field_gid: str) -> str | None:
    for cf in task_data.get("custom_fields", []):
        if cf.get("gid") == field_gid:
            return cf.get("display_value")
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-prefix", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # All matched parts for this job, grouped by task GID (a task can only
    # appear once currently, but multi-pack support is built in)
    cur.execute("""
        SELECT pc.asana_task_gid, pc.sesame_pn, pc.part_name, pc.order_qty,
               pc.build_assembly_pack_gid,
               bap.pack_reference, bap.pack_name
        FROM parts_control pc
        JOIN build_assembly_packs bap ON pc.build_assembly_pack_gid = bap.asana_task_gid
        WHERE pc.sesame_pn LIKE ?
          AND pc.build_assembly_pack_gid IS NOT NULL
    """, (f"{args.job_prefix}.%",))
    rows = cur.fetchall()
    conn.close()

    # Group by task GID — detect multi-pack parts
    by_task: dict[str, list] = defaultdict(list)
    for r in rows:
        by_task[r["asana_task_gid"]].append(dict(r))

    tasks = list(by_task.items())
    if args.limit:
        tasks = tasks[:args.limit]

    # Load skip list from existing write log
    log_path     = DATA_DIR / f"{args.job_prefix}_write_log.csv"
    skipped_path = DATA_DIR / f"{args.job_prefix}_skipped.csv"
    already_done = set()
    if log_path.exists():
        with open(log_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("result") == "ok":
                    already_done.add(row["asana_task_gid"])

    print(f"Tasks to process: {len(tasks)}  already done: {len(already_done)}")
    pending = [(gid, packs) for gid, packs in tasks if gid not in already_done]
    print(f"Pending:          {len(pending)}")

    log_rows     = []
    skipped_rows = []
    stats        = {"ok": 0, "skipped": 0, "error": 0}
    now          = datetime.now(timezone.utc).isoformat()

    for task_gid, packs in pending:
        pn        = packs[0]["sesame_pn"]
        part_name = packs[0]["part_name"]
        order_qty = packs[0]["order_qty"]
        multi_pack = len(packs) > 1

        # Build the combined note line (handles single or multiple packs)
        note_lines = []
        for p in packs:
            qty = extract_qty(part_name, order_qty)
            note_lines.append(pack_note_line(qty, p["pack_name"]))
        combined_note = " | ".join(note_lines)

        print(f"  {pn}  [{task_gid}]")
        print(f"    note  : {combined_note}")
        print(f"    ref   : {packs[0]['build_assembly_pack_gid']}")
        if multi_pack:
            print(f"    name  : prepend '*' (multi-pack)")

        if args.dry_run:
            log_rows.append({
                "asana_task_gid": task_gid, "sesame_pn": pn,
                "pack_gids": "|".join(p["build_assembly_pack_gid"] for p in packs),
                "result": "dry_run", "timestamp": now
            })
            continue

        # GET current task state
        task_data = asana_get_task(task_gid)
        time.sleep(PAUSE)
        if not task_data:
            print(f"    ERROR: could not fetch task")
            stats["error"] += 1
            skipped_rows.append({"asana_task_gid": task_gid, "sesame_pn": pn,
                                  "reason": "fetch_failed", "pack_note": combined_note})
            continue

        current_notes = task_data.get("notes", "") or ""
        current_name  = task_data.get("name", "")
        current_ref   = get_current_field_value(task_data, BUILD_PACKS_REF_GID)

        # Determine what needs updating
        payload = {}

        # 1. Build Packs reference — set to first (or only) pack GID
        #    Multi-pack: Asana reference field only holds one GID; note carries full list
        if current_ref != packs[0]["build_assembly_pack_gid"]:
            payload["custom_fields"] = payload.get("custom_fields", {})
            payload["custom_fields"][BUILD_PACKS_REF_GID] = packs[0]["build_assembly_pack_gid"]

        # 2. Notes — append pack line if not already present
        needs_note = not already_has_note(current_notes, packs[0]["pack_reference"])
        if needs_note:
            separator = "\n---\n" if current_notes.strip() else ""
            payload["notes"] = current_notes + separator + combined_note

        # 3. Task name — prepend '*' for multi-pack parts
        if multi_pack and not current_name.startswith("*"):
            payload["name"] = "*" + current_name

        if not payload:
            print(f"    no changes needed")
            stats["skipped"] += 1
            skipped_rows.append({"asana_task_gid": task_gid, "sesame_pn": pn,
                                  "reason": "already_up_to_date", "pack_note": combined_note})
            log_rows.append({"asana_task_gid": task_gid, "sesame_pn": pn,
                             "pack_gids": packs[0]["build_assembly_pack_gid"],
                             "result": "ok", "timestamp": now})
            continue

        success, msg = asana_put_task(task_gid, payload)
        time.sleep(PAUSE)

        if success:
            print(f"    ok")
            stats["ok"] += 1
            log_rows.append({"asana_task_gid": task_gid, "sesame_pn": pn,
                             "pack_gids": "|".join(p["build_assembly_pack_gid"] for p in packs),
                             "result": "ok", "timestamp": now})
        else:
            print(f"    ERROR: {msg}")
            stats["error"] += 1
            skipped_rows.append({"asana_task_gid": task_gid, "sesame_pn": pn,
                                  "reason": f"api_error:{msg[:80]}", "pack_note": combined_note})

    # Write logs
    if log_rows:
        write_header = not log_path.exists()
        with open(log_path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["asana_task_gid", "sesame_pn", "pack_gids", "result", "timestamp"])
            if write_header:
                w.writeheader()
            w.writerows(log_rows)

    if skipped_rows:
        with open(skipped_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["asana_task_gid", "sesame_pn", "reason", "pack_note"])
            w.writeheader()
            w.writerows(skipped_rows)

    print(f"\n{'DRY RUN - ' if args.dry_run else ''}Summary:")
    print(f"  Written:  {stats['ok']}")
    print(f"  Skipped:  {stats['skipped']}")
    print(f"  Errors:   {stats['error']}")
    if skipped_rows or args.dry_run:
        print(f"  Skipped/unresolved log: {skipped_path if not args.dry_run else '(dry-run, no log written)'}")
    print(f"  Unresolved from stage 2: {DATA_DIR / f'{args.job_prefix}_unresolved.csv'}")


if __name__ == "__main__":
    main()
