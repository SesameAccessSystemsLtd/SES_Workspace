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
from datetime import datetime, timezone
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
# 1208706270294053 is "Order date " — NOT the expected delivery date. It was
# previously mapped to procurement_tasks.due_date, so every lateness figure in the
# database was really measuring days-since-ordered. The expected delivery date is
# the Asana task due date (task.due_on), which was not fetched at all.
GID_ORDER_DATE    = "1208706270294053"
GID_DAYS_DELIVERY = "1208706270294055"   # Days for delivery
GID_ORDER_STATUS  = "1143955334207040"
GID_VENDOR        = "854127216887083"
GID_SQR           = "1211774738554014"
GID_IMPACT        = "1148052247637687"
GID_PO_TASK       = "1213595302673057"
GID_PROJECT_NAME  = "1202882645384174"

OPT_FIELDS = (
    "gid,name,parent.gid,due_on,completed,completed_at,created_at,"
    "custom_fields.gid,custom_fields.type,custom_fields.resource_subtype,"
    "custom_fields.display_value,custom_fields.number_value,custom_fields.text_value,"
    "custom_fields.enum_value.gid,custom_fields.enum_value.name"
)

PROJECT_CODE_RE = re.compile(r"\b(1[0-9]{3})\b")

# Statuses that mean the order is still live in the supply chain.
_LIVE_STATUS = ("on order", "partial delivery", "awaiting dispatch",
                "need to qc", "to be ordered", "picking", "hold on order")
# Names that mark a placeholder or an idea rather than a real purchase.
_NON_ORDER_RE = re.compile(
    r"\b(holding task|place ?holder|test|improvement|idea|template|example)\b", re.I)

# An order older than this with no live status is treated as history, not backlog.
LEGACY_AFTER_DAYS = 180


def _project_code(*candidates: str | None) -> str | None:
    for text in candidates:
        if not text:
            continue
        m = PROJECT_CODE_RE.search(text)
        if m:
            return m.group(1)
    return None


def _scope(name: str, completed: bool, po_number: str | None, vendor: str | None,
           status: str | None, order_date: str | None, due_on: str | None) -> str:
    """live | legacy | improvement | unknown.

    Giles, 2026-09-09: the ~392 'open' orders the mirror reported are mainly
    legacy tasks and improvement ideas. This classifies them at sync time so
    operational views can filter to what is genuinely in the supply chain.
    """
    if _NON_ORDER_RE.search(name or ""):
        return "improvement"
    if completed:
        return "legacy"
    st = (status or "").lower()
    if any(h in st for h in _LIVE_STATUS):
        # Still live unless it is very old and has no forward-looking date.
        ref = (due_on or order_date or "")[:10]
        if ref:
            try:
                age = (datetime.now(timezone.utc)
                       - datetime.strptime(ref, "%Y-%m-%d").replace(tzinfo=timezone.utc)).days
                if age > LEGACY_AFTER_DAYS and not due_on:
                    return "legacy"
            except ValueError:
                pass
        return "live"
    if not po_number and not vendor:
        return "improvement"
    return "legacy" if not st else "unknown"

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
    name       = task.get("name", "").strip()
    po_number  = _extract_po(name)
    vendor     = get_custom_field(task, GID_VENDOR)
    status     = get_custom_field(task, GID_ORDER_STATUS)
    order_date = get_custom_field(task, GID_ORDER_DATE)
    due_on     = task.get("due_on")
    completed  = bool(task.get("completed"))
    days       = get_custom_field(task, GID_DAYS_DELIVERY)
    try:
        days = float(str(days).replace(",", "")) if days not in (None, "") else None
    except (TypeError, ValueError):
        days = None
    return {
        "asana_task_gid": task["gid"],
        "task_name":      name,
        "document_type":  doc_type,
        "po_number":      po_number,
        "sqr_number":     get_custom_field(task, GID_SQR) or _extract_sqr(name),
        "price_ex_vat":   _parse_price(get_custom_field(task, GID_PRICE_EX_VAT)),
        "order_date":     order_date,
        # Expected delivery = the Asana task due date. The "Order date " custom
        # field now lands in order_date where it belongs.
        "due_date":       due_on,
        "days_for_delivery": days,
        "order_status":   status,
        "impact":         get_custom_field(task, GID_IMPACT),
        "po_task_gid":    get_custom_field(task, GID_PO_TASK),
        "vendor_name":    vendor,
        "vendor_gid":     None,
        "source_project": project_name,
        "project_code":   _project_code(name, get_custom_field(task, GID_PROJECT_NAME)),
        "scope":          _scope(name, completed, po_number, vendor, status,
                                 order_date, due_on),
        "is_completed":   1 if completed else 0,
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
            tasks = paginate_tasks(project_gid, OPT_FIELDS, all_completion_states=True)
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
