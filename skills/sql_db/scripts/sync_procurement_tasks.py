"""
sync_procurement_tasks.py — Sync Quote Control + Order Numbers → ops.db:procurement_tasks

Sources:
  Quote Control   GID: 1207766004159548   (quotes / SQR numbers)
  Order Numbers   GID: 854650811917168    (purchase orders / PO numbers)
Target:  ops.db table: procurement_tasks
Writer:  db_writer.upsert_procurement_tasks()

Replaces the legacy sync_asana_to_db.py → openclaw.db:active_purchasing_tasks path.
Safe to re-run — idempotent upsert. Never deletes rows.

Usage:
    python sync_procurement_tasks.py
    python sync_procurement_tasks.py --dry-run
"""

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "purchasing"))

from sync_utils import (
    AsanaFetchError,
    paginate_tasks,
    get_custom_field,
    now_utc,
    setup_logging,
)
import db_writer

QUOTE_CONTROL_GID   = "1207766004159548"
ORDER_NUMBERS_GID   = "854650811917168"

# Custom field GIDs
GID_PRICE_EX_VAT  = "854330340727040"
GID_DELIVERY_DATE = "1208706270294053"
GID_ORDER_STATUS  = "1143955334207040"
GID_VENDOR        = "854127216887083"
GID_SQR           = "1211774738554014"

OPT_FIELDS = (
    "gid,name,parent.gid,"
    "custom_fields.gid,custom_fields.type,custom_fields.resource_subtype,"
    "custom_fields.display_value,custom_fields.number_value,custom_fields.text_value,"
    "custom_fields.enum_value.gid,custom_fields.enum_value.name"
)

# lib/ is on sys.path via the db_writer import above
from path_config import p as _p
DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")


def _extract_po(name: str) -> str | None:
    m = re.search(r'(2\d{4}(?:-\d+)?)', name)
    return m.group(1) if m else None


def _extract_sqr(name: str) -> str | None:
    m = re.search(r'(SQR-\d+)', name, re.IGNORECASE)
    return m.group(1).upper() if m else None


def _parse_price(raw) -> float | None:
    if raw is None:
        return None
    try:
        cleaned = re.sub(r'[^\d.]', '', str(raw))
        return float(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None


def task_to_row(task: dict, project_gid: str, project_name: str,
                doc_type: str, sync_ts: str) -> dict:
    name = task.get("name", "").strip()
    return {
        "asana_task_gid": task["gid"],
        "task_name":      name,
        "document_type":  doc_type,
        "po_number":      _extract_po(name),
        "sqr_number":     get_custom_field(task, GID_SQR) or _extract_sqr(name),
        "price_ex_vat":   _parse_price(get_custom_field(task, GID_PRICE_EX_VAT)),
        "order_date":     None,
        "due_date":       get_custom_field(task, GID_DELIVERY_DATE),
        "order_status":   get_custom_field(task, GID_ORDER_STATUS),
        "vendor_name":    get_custom_field(task, GID_VENDOR),
        "vendor_gid":     None,
        "source_project": project_name,
        "is_subtask":     1 if task.get("parent") else 0,
        "last_synced_at": sync_ts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Quote Control + Order Numbers → ops.db:procurement_tasks")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log = setup_logging("sync_procurement_tasks")
    sync_ts = now_utc()
    all_rows = []

    projects = [
        (QUOTE_CONTROL_GID, "Quote Control",  "quote"),
        (ORDER_NUMBERS_GID, "Order Numbers",  "purchase_order"),
    ]
    failed: list[str] = []

    for project_gid, project_name, doc_type in projects:
        log.info("Fetching from %s (%s)", project_name, project_gid)
        try:
            tasks = paginate_tasks(project_gid, OPT_FIELDS)
        except AsanaFetchError as exc:
            # Transient Asana outage on one project must not sink the whole
            # pipeline — the approval-sheet writer runs downstream of this.
            # Upsert is idempotent, so the next run backfills what we skip.
            log.error("  %s unavailable, skipping this project: %s", project_name, exc)
            failed.append(project_name)
            continue
        log.info("  %d tasks", len(tasks))
        all_rows.extend(
            task_to_row(t, project_gid, project_name, doc_type, sync_ts)
            for t in tasks if t.get("name")
        )

    if failed and len(failed) == len(projects):
        # Nothing reachable at all — a real outage. Fail loudly.
        log.critical("All %d project fetches failed (%s) — aborting without write",
                     len(projects), ", ".join(failed))
        sys.exit(1)

    log.info("Total rows prepared: %d", len(all_rows))

    if args.dry_run:
        import json
        print(json.dumps(all_rows[:5], indent=2, default=str))
        return

    result = db_writer.upsert_procurement_tasks(all_rows, source="sync_procurement_tasks",
                                                 db_path=args.db_path)
    log.info("Done — written: %d  skipped: %d  errors: %d",
             result["written"], result["skipped"], result["errors"])

    if failed:
        # Exit 0 deliberately: downstream stages still have useful work to do.
        # The partial sync is recorded here so it is not silently swallowed.
        log.warning("PARTIAL SYNC — %d/%d projects skipped this run: %s. "
                    "Rows for those projects retain their previous last_synced_at; "
                    "the next run will backfill them.",
                    len(failed), len(projects), ", ".join(failed))


if __name__ == "__main__":
    main()
