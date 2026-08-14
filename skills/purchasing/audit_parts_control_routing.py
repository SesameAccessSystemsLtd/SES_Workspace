"""
audit_parts_control_routing.py — Nightly catch-up for Parts Control routing.

Scans the Parts Control "To Be Addressed" section in Asana for tasks where
Stock ACTION = ORDER FOR STOCK that are NOT yet in ordering_queue (missed by
Asana rules or manually added). Upserts them into ordering_queue.

Triggered via SESAL "Update orders" or cron at 01:30 daily.

Usage:
    python audit_parts_control_routing.py --scan    # report only
    python audit_parts_control_routing.py --commit  # upsert to ordering_queue
"""

import os
import sys
import argparse
import sqlite3
import requests
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)

sys.path.insert(0, str(Path(__file__).parent))
from db_writer import write_ordering_queue_item, write_ordering_edge_case, normalise_vendor_name

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from path_config import p as _p

BASE = "https://app.asana.com/api/1.0"
DB_PATH = _p("db.ops")

# GIDs
PARTS_CONTROL_PROJECT  = "1211660707080749"
TO_BE_ADDRESSED_SECTION = "1211660707080750"
STOCK_ACTION_FIELD     = "1199111659373956"
ORDER_FOR_STOCK_ENUM   = "1199111659373959"
TRIGGER_ORDER_QTY_FIELD = "1198179728033183"
STOCK_QTY_FIELD        = "1126782469949969"
ORDER_QTY_FIELD        = "1201447044882111"
VENDOR_FIELD           = "854127216887083"
SESAME_PN_FIELD        = "1213228090549470"
PROJECT_NAME_FIELD     = "1202882645384174"

# Source project GIDs — used to infer item_type
KANBAN_PROJECT    = "632603478911321"
CONSUMABLES_PROJECT = "1200068443534209"

PRICE_EX_VAT_FIELD      = "854330340727040"   # on source stock/consumable tasks

TASK_FIELDS = (
    "gid,name,notes,projects.gid,"
    "custom_fields.gid,"
    "custom_fields.number_value,"
    "custom_fields.text_value,"
    "custom_fields.enum_value.gid,"
    "custom_fields.enum_value.name"
)


def _headers():
    token = os.getenv("ASANA_PERSONAL_ACCESS_TOKEN")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _get_all_pages(url, params):
    results = []
    while url:
        r = requests.get(url, headers=_headers(), params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("data", []))
        next_page = data.get("next_page")
        url = next_page.get("uri") if next_page else None
        params = {}
    return results


def _get_custom_field(task, field_gid):
    for cf in task.get("custom_fields", []):
        if cf.get("gid") == field_gid:
            ev = cf.get("enum_value")
            if ev:
                return {"enum_gid": ev.get("gid"), "enum_name": ev.get("name")}
            num = cf.get("number_value")
            if num is not None:
                return {"number": num}
            text = cf.get("text_value")
            if text:
                return {"text": text}
            return {}
    return {}


def _infer_item_type(task):
    project_gids = {p.get("gid") for p in task.get("projects", [])}
    if CONSUMABLES_PROJECT in project_gids:
        return "consumable"
    return "stock"


def fetch_to_be_addressed():
    """Return all tasks in Parts Control 'To Be Addressed' section."""
    url = f"{BASE}/sections/{TO_BE_ADDRESSED_SECTION}/tasks"
    params = {"opt_fields": TASK_FIELDS, "limit": 100}
    return _get_all_pages(url, params)


def already_in_queue(task_gid):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT id FROM ordering_queue WHERE asana_task_gid = ?", (task_gid,)
    ).fetchone()
    conn.close()
    return row is not None


def build_queue_row(task, run_ts):
    gid = task.get("gid", "")
    name = task.get("name", "")

    stock_action = _get_custom_field(task, STOCK_ACTION_FIELD)
    if stock_action.get("enum_gid") != ORDER_FOR_STOCK_ENUM:
        return None  # not awaiting order — skip

    vendor_raw = (_get_custom_field(task, VENDOR_FIELD) or {}).get("text")
    sesame_pn = (_get_custom_field(task, SESAME_PN_FIELD) or {}).get("text")
    order_qty = (_get_custom_field(task, ORDER_QTY_FIELD) or {}).get("number")
    stock_qty = (_get_custom_field(task, STOCK_QTY_FIELD) or {}).get("number")
    unit_price = (_get_custom_field(task, PRICE_EX_VAT_FIELD) or {}).get("number")
    item_type = _infer_item_type(task)

    return {
        "asana_task_gid": gid,
        "task_name": name,
        "sesame_pn": sesame_pn,
        "item_type": item_type,
        "source_project_gid": PARTS_CONTROL_PROJECT,
        "vendor_name": vendor_raw,
        "vendor_name_normalised": normalise_vendor_name(vendor_raw),
        "order_qty": order_qty,
        "unit_price": unit_price,
        "stock_qty_at_detection": stock_qty,
        "stock_action_value": "ORDER FOR STOCK",
        "parts_control_task_gid": gid,
        "status": "grouped",
    }


def run(commit: bool):
    run_ts = datetime.now(timezone.utc).isoformat()
    print(f"{'COMMIT' if commit else 'SCAN'} — {run_ts}")

    tasks = fetch_to_be_addressed()
    print(f"Parts Control 'To Be Addressed': {len(tasks)} task(s)")

    new_items = []
    skipped_no_action = 0

    for task in tasks:
        gid = task.get("gid", "")
        name = task.get("name", "")

        if already_in_queue(gid):
            continue

        row = build_queue_row(task, run_ts)
        if row is None:
            skipped_no_action += 1
            continue

        new_items.append(row)

    print(f"  Not in queue + ORDER FOR STOCK: {len(new_items)}")
    print(f"  Skipped (wrong action): {skipped_no_action}")

    if not new_items:
        print("Nothing new to route. Done.")
        return

    for row in new_items:
        print(f"  {'[WOULD ADD]' if not commit else '[ADDING]'} {row['task_name']} "
              f"({row['item_type']}) vendor={row['vendor_name'] or 'BLANK'}")
        if commit:
            write_ordering_queue_item(row, db_path=DB_PATH)
            if not row["vendor_name"]:
                from db_writer import write_ordering_edge_case
                write_ordering_edge_case({
                    "run_timestamp": run_ts,
                    "script": "audit_parts_control_routing",
                    "stage": "routing_catchup",
                    "asana_task_gid": row["asana_task_gid"],
                    "task_name": row["task_name"],
                    "vendor_name": None,
                    "issue_type": "vendor_fallback",
                    "description": "Vendor field blank in Parts Control — fallback tag will be applied at order creation",
                    "raw_data": None,
                }, db_path=DB_PATH)

    if commit:
        print(f"\nDone. {len(new_items)} item(s) added to ordering_queue.")
    else:
        print(f"\n[SCAN] {len(new_items)} item(s) would be added. Re-run with --commit to apply.")


def main():
    parser = argparse.ArgumentParser(description="Audit Parts Control routing — catch items missed by Asana rules.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan",   action="store_true", help="Dry run — report only")
    group.add_argument("--commit", action="store_true", help="Upsert new items into ordering_queue")
    args = parser.parse_args()
    run(commit=args.commit)


if __name__ == "__main__":
    main()
