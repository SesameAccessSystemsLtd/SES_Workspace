"""
sync_suppliers.py — Sync Asana Suppliers project → ops.db:suppliers

Source:  Asana project 414986125671548 (Suppliers)
Target:  ops.db table: suppliers
Writer:  db_writer.upsert_suppliers()

Safe to re-run — idempotent upsert. Never deletes rows.

Usage:
    python sync_suppliers.py
    python sync_suppliers.py --dry-run
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "purchasing"))

from sync_utils import paginate_tasks, get_custom_field, now_utc, setup_logging
import db_writer

PROJECT_GID = "414986125671548"  # Suppliers

OPT_FIELDS = (
    "gid,name,"
    "custom_fields.gid,custom_fields.type,custom_fields.resource_subtype,"
    "custom_fields.display_value,custom_fields.number_value,custom_fields.text_value,"
    "custom_fields.enum_value.gid,custom_fields.enum_value.name,"
    "tags.gid,tags.name"
)

# lib/ is on sys.path via the db_writer import above
from path_config import p as _p
DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")


def _pick_primary_tag(task_name: str, tags: list) -> str | None:
    """Return the tag GID that best matches the supplier task name.

    Picks the tag with highest word-overlap to the task name, breaking ties
    by longest name. Category/generic tags (short word overlap) lose to
    supplier-specific ones.
    """
    if not tags:
        return None
    if len(tags) == 1:
        return tags[0]["gid"]
    name_words = set(task_name.lower().split())

    def score(tag):
        tag_words = set(tag.get("name", "").lower().split())
        overlap = len(name_words & tag_words)
        return (overlap, len(tag.get("name", "")))

    best = max(tags, key=score)
    return best["gid"]


def task_to_row(task: dict, sync_ts: str) -> dict:
    tags = task.get("tags", []) or []
    return {
        "asana_task_gid": task["gid"],
        "vendor_name":    task.get("name", "").strip(),
        "primary_domain": None,
        "contact_name":   None,
        "contact_email":  None,
        "contact_phone":  None,
        "payment_type":   None,
        "risk_profile":   None,
        "typical_lead_times_days": None,
        "supplier_type":  None,
        "asana_tag_gid":  _pick_primary_tag(task.get("name", ""), tags),
        "last_synced_at": sync_ts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Suppliers → ops.db:suppliers")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log = setup_logging("sync_suppliers")
    sync_ts = now_utc()

    log.info("Fetching tasks from Suppliers project %s", PROJECT_GID)
    tasks = paginate_tasks(PROJECT_GID, OPT_FIELDS)
    log.info("Fetched %d tasks", len(tasks))

    rows = [task_to_row(t, sync_ts) for t in tasks if t.get("name")]

    if args.dry_run:
        import json
        print(json.dumps(rows[:5], indent=2, default=str))
        log.info("Dry run — %d rows prepared, not written", len(rows))
        return

    result = db_writer.upsert_suppliers(rows, source="sync_suppliers", db_path=args.db_path)
    log.info("Done — written: %d  skipped: %d  errors: %d",
             result["written"], result["skipped"], result["errors"])


if __name__ == "__main__":
    main()
