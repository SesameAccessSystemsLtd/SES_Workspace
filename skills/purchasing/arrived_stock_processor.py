"""
arrived_stock_processor.py — Process arrived stock from Parts Control.

Reads Parts Control tasks where:
  - Arrived Stock Qty field > 0
  - arrived_reset_pending = 0 in ordering_queue (idempotency guard)

Two paths:
  - item_type='stock'      -> update STOCK QTY in ops.db + write title update REQUESTS/
  - item_type='consumable' -> update stock_qty in ordering_queue only (no title update)

After processing: write reset REQUESTS/ to zero Arrived Stock Qty field,
set arrived_reset_pending = 1 until SAP confirms.

Run every 30 min Mon-Fri 08:00-18:00 via cron.
"""

import os
import re
import sys
import sqlite3
import requests
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)

sys.path.insert(0, str(Path(__file__).parent))
from db_writer import (
    update_ordering_queue_status,
    update_arrived_reset_pending,
    write_ordering_edge_case,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from path_config import p as _p

BASE         = "https://app.asana.com/api/1.0"
DB_PATH      = _p("db.ops")
REQUESTS_DIR = _p("queues.requests")

# GIDs
PARTS_CONTROL_PROJECT    = "1211660707080749"
ARRIVED_STOCK_QTY_FIELD  = "1216071767931108"
STOCK_QTY_FIELD          = "1126782469949969"
SESAME_PN_FIELD          = "1213228090549470"

KANBAN_PROJECT      = "632603478911321"
CONSUMABLES_PROJECT = "1200068443534209"

TASK_FIELDS = (
    "gid,name,projects.gid,"
    "custom_fields.gid,"
    "custom_fields.number_value,"
    "custom_fields.text_value"
)


def _headers():
    token = os.getenv("ASANA_PERSONAL_ACCESS_TOKEN")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_cf_number(task, field_gid):
    for cf in task.get("custom_fields", []):
        if cf.get("gid") == field_gid:
            return cf.get("number_value")
    return None


def _get_cf_text(task, field_gid):
    for cf in task.get("custom_fields", []):
        if cf.get("gid") == field_gid:
            return cf.get("text_value")
    return None


def _infer_item_type(task):
    project_gids = {p.get("gid") for p in task.get("projects", [])}
    if CONSUMABLES_PROJECT in project_gids:
        return "consumable"
    return "stock"


def fetch_arrived_tasks():
    """Return all Parts Control tasks where Arrived Stock Qty > 0."""
    url = f"{BASE}/projects/{PARTS_CONTROL_PROJECT}/tasks"
    params = {
        "opt_fields": TASK_FIELDS,
        "limit": 100,
    }
    results = []
    while url:
        r = requests.get(url, headers=_headers(), params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("data", []))
        next_page = data.get("next_page")
        url = next_page.get("uri") if next_page else None
        params = {}

    arrived = [
        t for t in results
        if (_get_cf_number(t, ARRIVED_STOCK_QTY_FIELD) or 0) > 0
    ]
    return arrived


def get_queue_row(task_gid):
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM ordering_queue WHERE asana_task_gid = ?", (task_gid,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_current_title_qty(task_name):
    """Parse [N off] from title. Returns current N or None if not found."""
    m = re.search(r"\[(\d+(?:\.\d+)?)\s*off\]", task_name, re.IGNORECASE)
    return float(m.group(1)) if m else None


def build_new_title(task_name, new_total_qty):
    qty_str = str(int(new_total_qty)) if new_total_qty == int(new_total_qty) else str(new_total_qty)
    new_tag = f"[{qty_str} off]"
    m = re.search(r"\[\d+(?:\.\d+)?\s*off\]", task_name, re.IGNORECASE)
    if m:
        return task_name[:m.start()] + new_tag + task_name[m.end():]
    return f"{task_name} {new_tag}"


def write_title_update_request(task_gid, new_title, vendor, run_ts):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    fname = f"{ts}-arrived-title-update-{task_gid}.md"
    content = f"""---
From: ops_agent
To: asana_agent
Type: asana_write
Status: PENDING
Priority: normal
Created: {datetime.now(timezone.utc).isoformat()}
---

## Request
Action: update_task_title
Task GID: {task_gid}
New Title: {new_title}

## Context
Arrived stock processor — updating [N off] stock qty in title.
Vendor: {vendor}  Run: {run_ts}
"""
    fpath = os.path.join(REQUESTS_DIR, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content)
    return fpath


def write_arrived_reset_request(task_gid, run_ts):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    fname = f"{ts}-arrived-reset-{task_gid}.md"
    content = f"""---
From: ops_agent
To: asana_agent
Type: asana_write
Status: PENDING
Priority: normal
Created: {datetime.now(timezone.utc).isoformat()}
---

## Request
Action: reset_custom_field
Task GID: {task_gid}
Field GID: {ARRIVED_STOCK_QTY_FIELD}
Value: 0

## Context
Arrived stock processor — resetting Arrived Stock Qty to 0 after processing.
Run: {run_ts}
"""
    fpath = os.path.join(REQUESTS_DIR, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content)
    return fpath


def main():
    run_ts = datetime.now(timezone.utc).isoformat()
    print(f"arrived_stock_processor — {run_ts}")

    tasks = fetch_arrived_tasks()
    print(f"Parts Control tasks with Arrived Stock Qty > 0: {len(tasks)}")

    processed = skipped = errors = 0

    for task in tasks:
        gid  = task.get("gid", "")
        name = task.get("name", "")
        arrived_qty = _get_cf_number(task, ARRIVED_STOCK_QTY_FIELD) or 0
        item_type = _infer_item_type(task)

        queue_row = get_queue_row(gid)

        # Idempotency guard
        if queue_row and queue_row.get("arrived_reset_pending") == 1:
            print(f"  SKIP (reset pending): {name}")
            skipped += 1
            continue

        print(f"  PROCESS: {name} | arrived={arrived_qty} | type={item_type}")

        try:
            if item_type == "stock":
                current_qty = get_current_title_qty(name) or 0
                new_total   = current_qty + arrived_qty
                new_title   = build_new_title(name, new_total)

                if new_title == name:
                    write_ordering_edge_case({
                        "run_timestamp": run_ts,
                        "script": "arrived_stock_processor",
                        "stage": "title_update",
                        "asana_task_gid": gid,
                        "task_name": name,
                        "vendor_name": queue_row.get("vendor_name") if queue_row else None,
                        "issue_type": "title_parse_fail",
                        "description": f"Could not build new title — original: '{name}', arrived: {arrived_qty}",
                        "raw_data": None,
                    }, db_path=DB_PATH)

                vendor = queue_row.get("vendor_name") if queue_row else ""
                write_title_update_request(gid, new_title, vendor or "", run_ts)

            # Both types: update ordering_queue + write reset request
            if queue_row:
                update_ordering_queue_status(
                    gid, "arrived",
                    arrived_qty=arrived_qty,
                    arrived_at=run_ts,
                    db_path=DB_PATH,
                )
            else:
                # Task in Parts Control but not in ordering_queue — still process
                write_ordering_edge_case({
                    "run_timestamp": run_ts,
                    "script": "arrived_stock_processor",
                    "stage": "queue_lookup",
                    "asana_task_gid": gid,
                    "task_name": name,
                    "vendor_name": None,
                    "issue_type": "stock_record_not_found",
                    "description": "Arrived qty found but no ordering_queue row — run audit_parts_control_routing.py",
                    "raw_data": None,
                }, db_path=DB_PATH)

            write_arrived_reset_request(gid, run_ts)
            update_arrived_reset_pending(gid, 1, db_path=DB_PATH)

            processed += 1

        except Exception as exc:
            print(f"    ERROR: {exc}")
            write_ordering_edge_case({
                "run_timestamp": run_ts,
                "script": "arrived_stock_processor",
                "stage": "processing",
                "asana_task_gid": gid,
                "task_name": name,
                "vendor_name": None,
                "issue_type": "sap_write_fail",
                "description": str(exc),
                "raw_data": None,
            }, db_path=DB_PATH)
            errors += 1

    print(f"\nDone. Processed: {processed}  Skipped: {skipped}  Errors: {errors}")


if __name__ == "__main__":
    main()
