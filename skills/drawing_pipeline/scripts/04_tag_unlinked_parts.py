"""
04_tag_unlinked_parts.py
Applies a tag to all Parts Control tasks that exist in parts_control but have
no build_assembly_pack_gid assigned (i.e. not linked to any assembly pack).

Tag GID: 1214056716982132  ("Needs Build Pack Assignment" or similar)

Usage:
  python 04_tag_unlinked_parts.py --job-prefix 1670 [--dry-run]

Idempotent — if a task already has the tag, Asana returns 200 silently.
"""

import argparse
import csv
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)

ASANA_TOKEN     = os.getenv("ASANA_PERSONAL_ACCESS_TOKEN")
HEADERS         = {"Authorization": f"Bearer {ASANA_TOKEN}",
                   "Accept": "application/json",
                   "Content-Type": "application/json"}
BASE            = "https://app.asana.com/api/1.0"
UNLINKED_TAG    = "1214056716982132"
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p
DB_PATH         = _p("db.ops")
SKILL_DIR       = Path(__file__).parent.parent
DATA_DIR        = SKILL_DIR / "data"
PAUSE           = 0.3


BUILD_PACKS_REF_GID = "1211660742155293"


def get_build_pack_ref(task_gid: str) -> str | None:
    """Return current Build Packs reference field value, or None on error."""
    r = requests.get(
        f"{BASE}/tasks/{task_gid}",
        headers=HEADERS,
        params={"opt_fields": f"custom_fields.gid,custom_fields.display_value"},
        timeout=15,
    )
    if r.status_code != 200:
        return None
    for cf in r.json().get("data", {}).get("custom_fields", []):
        if cf.get("gid") == BUILD_PACKS_REF_GID:
            return cf.get("display_value") or None
    return None


def add_tag(task_gid: str) -> tuple[bool, str]:
    r = requests.post(
        f"{BASE}/tasks/{task_gid}/addTag",
        headers=HEADERS,
        json={"data": {"tag": UNLINKED_TAG}},
        timeout=15,
    )
    if r.status_code in (200, 204):
        return True, "ok"
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


def main():
    parser = argparse.ArgumentParser(description="Tag unlinked parts_control tasks")
    parser.add_argument("--job-prefix", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT asana_task_gid, sesame_pn, part_name
        FROM parts_control
        WHERE sesame_pn LIKE ?
          AND build_assembly_pack_gid IS NULL
          AND asana_task_gid IS NOT NULL
        ORDER BY sesame_pn
        """,
        (f"{args.job_prefix}.%",),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    print(f"Unlinked parts_control rows for {args.job_prefix}: {len(rows)}")
    if not rows:
        print("Nothing to tag.")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_path = DATA_DIR / f"{args.job_prefix}_tag_unlinked_log.csv"
    log_rows = []
    stats = {"tagged": 0, "already_linked": 0, "error": 0}
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        gid = row["asana_task_gid"]
        pn  = row["sesame_pn"]
        print(f"  {pn}  [{gid}]", end="")

        if args.dry_run:
            print("  (dry-run — skipping Asana check)")
            log_rows.append({"asana_task_gid": gid, "sesame_pn": pn,
                             "result": "dry_run", "timestamp": now})
            continue

        # Pre-check: skip if Build Pack ref already set in Asana (DB may be stale)
        existing_ref = get_build_pack_ref(gid)
        time.sleep(PAUSE)
        if existing_ref:
            print(f"  SKIP (already has Build Pack ref in Asana: {existing_ref})")
            stats["already_linked"] += 1
            log_rows.append({"asana_task_gid": gid, "sesame_pn": pn,
                             "result": f"skip:already_linked:{existing_ref}", "timestamp": now})
            continue

        ok, msg = add_tag(gid)
        time.sleep(PAUSE)

        if ok:
            print("  tagged")
            stats["tagged"] += 1
            log_rows.append({"asana_task_gid": gid, "sesame_pn": pn,
                             "result": "ok", "timestamp": now})
        else:
            print(f"  ERROR: {msg}")
            stats["error"] += 1
            log_rows.append({"asana_task_gid": gid, "sesame_pn": pn,
                             "result": f"error:{msg[:80]}", "timestamp": now})

    with open(log_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["asana_task_gid", "sesame_pn", "result", "timestamp"])
        w.writeheader()
        w.writerows(log_rows)

    print(f"\n{'DRY RUN - ' if args.dry_run else ''}Summary:")
    print(f"  Tagged:          {stats['tagged']}")
    print(f"  Already linked:  {stats['already_linked']}  (skipped — Asana ref already set)")
    print(f"  Errors:          {stats['error']}")
    print(f"  Log:     {log_path}")


if __name__ == "__main__":
    main()
