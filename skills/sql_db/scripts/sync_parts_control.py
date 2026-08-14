"""
sync_parts_control.py — Sync Asana Parts Control project → ops.db:parts_control

Source:  Asana project 1211660707080749 (Parts Control)
Target:  ops.db table: parts_control
Writer:  db_writer.upsert_parts_control()

Run nightly. Safe to re-run — idempotent upsert. Never deletes rows.

Usage:
    python sync_parts_control.py
    python sync_parts_control.py --db-path C:\\custom\\ops.db
    python sync_parts_control.py --dry-run   # print rows without writing
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "purchasing"))

from sync_utils import paginate_tasks, get_custom_field, now_utc, setup_logging
import db_writer

PROJECT_GID = "1211660707080749"  # Parts Control

# Custom field GIDs — sourced from asana_field_registry / bom_pipeline/CLAUDE.md
GID_SESAME_PN    = "1213228090549470"
GID_ORDER_QTY    = "1201447044882111"
GID_STOCK_ACTION = "1199111659373956"
GID_VENDOR       = "854127216887083"
GID_BUILD_PACKS  = "1211660742155293"  # reference field → build_assembly_pack task GID

OPT_FIELDS = (
    "gid,name,completed,parent.gid,memberships.project.gid,"
    "custom_fields.gid,custom_fields.type,custom_fields.resource_subtype,"
    "custom_fields.display_value,custom_fields.number_value,custom_fields.text_value,"
    "custom_fields.enum_value.gid,custom_fields.enum_value.name"
)

# lib/ is on sys.path via the db_writer import above
from path_config import p as _p
DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")


def task_to_row(task: dict, sync_ts: str) -> dict:
    build_pack_ref = get_custom_field(task, GID_BUILD_PACKS)
    return {
        "asana_task_gid":          task["gid"],
        "sesame_pn":               get_custom_field(task, GID_SESAME_PN),
        "part_name":               task.get("name", "").strip(),
        "order_qty":               get_custom_field(task, GID_ORDER_QTY),
        "stock_action":            get_custom_field(task, GID_STOCK_ACTION),
        "vendor_name":             get_custom_field(task, GID_VENDOR),
        "build_assembly_pack_gid": build_pack_ref,
        "procurement_task_gid":    None,
        "is_subtask":              1 if task.get("parent") else 0,
        "is_completed":            1 if task.get("completed") else 0,
        "project_name":            "Parts Control",
        "last_synced_at":          sync_ts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Parts Control → ops.db:parts_control")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true", help="Print rows without writing")
    args = parser.parse_args()

    log = setup_logging("sync_parts_control")
    sync_ts = now_utc()

    log.info("Fetching tasks from Parts Control project %s", PROJECT_GID)
    tasks = paginate_tasks(PROJECT_GID, OPT_FIELDS, all_completion_states=True)
    log.info("Fetched %d tasks (%d completed)",
             len(tasks), sum(1 for t in tasks if t.get("completed")))

    rows = [task_to_row(t, sync_ts) for t in tasks if t.get("name")]

    if args.dry_run:
        import json
        print(json.dumps(rows[:5], indent=2, default=str))
        log.info("Dry run — %d rows prepared, not written", len(rows))
        return

    result = db_writer.upsert_parts_control(rows, source="sync_parts_control",
                                             db_path=args.db_path)
    log.info("Done — written: %d  skipped: %d  errors: %d",
             result["written"], result["skipped"], result["errors"])


if __name__ == "__main__":
    main()
