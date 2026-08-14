"""
sync_qc_issues.py — Sync Asana QC Issues project → ops.db:qc_issues

Source:  Asana project 902269769783650 (1.0 - QC Issues)
Target:  ops.db table: qc_issues
Writer:  db_writer.upsert_qc_issues()

QC records are permanent — a task that disappears from Asana is NOT deleted.
Its last_synced_at will stop updating, which flags staleness.

Usage:
    python sync_qc_issues.py
    python sync_qc_issues.py --dry-run
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "purchasing"))

from sync_utils import paginate_tasks, get_custom_field, now_utc, setup_logging
import db_writer

PROJECT_GID = "902269769783650"  # 1.0 - QC Issues

OPT_FIELDS = (
    "gid,name,"
    "custom_fields.gid,custom_fields.type,custom_fields.resource_subtype,"
    "custom_fields.display_value,custom_fields.number_value,custom_fields.text_value,"
    "custom_fields.enum_value.gid,custom_fields.enum_value.name"
)

# lib/ is on sys.path via the db_writer import above
from path_config import p as _p
DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")


def task_to_row(task: dict, sync_ts: str) -> dict:
    return {
        "asana_task_gid":         task["gid"],
        "issue_name":             task.get("name", "").strip(),
        "impact":                 None,
        "signed_off_status":      None,
        "order_qty":              None,
        "order_value":            None,
        "order_date":             None,
        "date_out_of_project":    None,
        "associated_po_number":   None,
        "associated_sqr_number":  None,
        "parts_control_gid":      None,
        "last_synced_at":         sync_ts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync QC Issues → ops.db:qc_issues")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log = setup_logging("sync_qc_issues")
    sync_ts = now_utc()

    log.info("Fetching tasks from QC Issues project %s", PROJECT_GID)
    tasks = paginate_tasks(PROJECT_GID, OPT_FIELDS)
    log.info("Fetched %d tasks", len(tasks))

    rows = [task_to_row(t, sync_ts) for t in tasks if t.get("name")]

    if args.dry_run:
        import json
        print(json.dumps(rows[:5], indent=2, default=str))
        log.info("Dry run — %d rows prepared, not written", len(rows))
        return

    result = db_writer.upsert_qc_issues(rows, source="sync_qc_issues", db_path=args.db_path)
    log.info("Done — written: %d  skipped: %d  errors: %d",
             result["written"], result["skipped"], result["errors"])


if __name__ == "__main__":
    main()
