"""
create_order_tasks.py — Create or append to NEEDS order tasks per vendor.

Reads vendor_groups.json (output of compile_vendor_groups.py).
For each vendor group:
  1. Check procurement_tasks in ops.db for an open task matching vendor (normalised)
  2. If found: write REQUESTS/ file (append_to_description) with new items
  3. If not found: write REQUESTS/ file (create_task_in_project) in NEEDS/ADDRESSING

Also writes edge cases for: vendor_fallback, sap_write_fail, no_task_created.

Usage:
    python create_order_tasks.py --scan    # preview what would be done
    python create_order_tasks.py --commit  # write REQUESTS/ files
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)

sys.path.insert(0, str(Path(__file__).parent))
from db_writer import (
    write_ordering_queue_item,
    update_ordering_queue_status,
    write_ordering_edge_case,
    normalise_vendor_name,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from path_config import p as _p

DB_PATH         = _p("db.ops")
# vendor_groups.json lives in the workspace skill dir (moved off .openclaw at DB cutover 2026-07-09).
GROUPS_FILE     = r"C:\Users\GilesHugo\SES_Workspace\skills\purchasing\vendor_groups.json"
REQUESTS_DIR    = _p("queues.requests")

NEEDS_PROJECT_GID  = "1207196303096568"
NEEDS_SECTION_GID  = "1207647263791647"  # ADDRESSING

# Part of the description block written to each order task
ORDER_TASK_HEADER = "== STOCK ORDER REQUEST =="


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


OPEN_ORDER_STATUSES = (
    "⏰On Order - Awaiting CONFIRMATION",   # ⏰
    "\U0001f4a2To be ordered / Requires attention",  # 💢
    "\U0001f69aOn Order - Delivery Date CONFIRMED",  # 🚚
    "Quote - Manual",
    "Quote - Auto Email",
    "\U0001f51cPARTIAL DELIVERY",   # 🔜
    "HOLD ON ORDER",
    "TO BE ASSESSED",
    "Awaiting Position",
    "PICKING  / checking",
)


def find_supplier_tag(vendor_normalised):
    """Return asana_tag_gid from suppliers table for this vendor, or None."""
    conn = _conn()
    row = conn.execute(
        "SELECT asana_tag_gid FROM suppliers WHERE vendor_name_normalised = ? AND asana_tag_gid IS NOT NULL LIMIT 1",
        (vendor_normalised,),
    ).fetchone()
    conn.close()
    return row["asana_tag_gid"] if row else None


def find_open_order_task(vendor_normalised):
    """Return GID of open procurement task for this vendor (normalised match), or None.

    Only matches tasks with an explicitly active order status — not None/arrived/cancelled.
    """
    placeholders = ",".join("?" * len(OPEN_ORDER_STATUSES))
    # TRIM(order_status) handles trailing spaces in Asana enum values
    conn = _conn()
    row = conn.execute(
        f"""SELECT asana_task_gid FROM procurement_tasks
            WHERE vendor_name_normalised = ?
              AND TRIM(order_status) IN ({placeholders})
            ORDER BY created_at DESC LIMIT 1""",
        (vendor_normalised, *OPEN_ORDER_STATUSES),
    ).fetchone()
    conn.close()
    return row["asana_task_gid"] if row else None


def build_description_block(group, run_ts):
    """Build the text block to append to an order task description."""
    vendor = group["vendor_name"]
    lines = [
        ORDER_TASK_HEADER,
        f"Compiled: {run_ts}",
        f"Vendor: {vendor}",
        "",
    ]
    for item in group["items"]:
        qty = item.get("order_qty")
        price = item.get("unit_price")
        pn_str = f" [{item['sesame_pn']}]" if item["sesame_pn"] else ""
        url = f"https://app.asana.com/0/0/{item['asana_task_gid']}/f"

        if qty is not None:
            qty_int = int(qty) if qty == int(qty) else qty
            qty_label = f"[{qty_int} off]"
        else:
            qty_label = "[qty TBC]"

        if price is not None and qty is not None:
            total = qty * price
            price_str = f"at £{price:,.2f} each = £{total:,.2f}"
        elif price is not None:
            price_str = f"at £{price:,.2f} each (qty TBC)"
        else:
            price_str = "price TBC"

        lines.append(f"  {item['task_name']}{pn_str} {qty_label} {price_str}")
        lines.append(f"  Task: {url}")
        lines.append("")
    if group.get("vendor_is_fallback"):
        lines.append("[NOTE: Vendor field was blank — fallback group applied. Update Vendor field in Parts Control to track supplier correctly.]")
    return "\n".join(lines)


def _requests_filename(action, vendor_slug):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    slug = re.sub(r"[^a-z0-9]+", "-", vendor_slug.lower())[:30]
    return f"{ts}-ordering-{action}-{slug}.md"


def write_create_request(group, description_block, supplier_tag_gid=None):
    vendor = group["vendor_name"]
    vendor_norm = group["vendor_name_normalised"]
    fallback_tag = group.get("fallback_tag_gid") or ""
    # Supplier tag takes priority over fallback; both can stack if needed
    tag_gid = supplier_tag_gid or fallback_tag
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    task_name = f"ORDER — {vendor} — {date_str}"
    tag_line = f"\nTag GIDs: {tag_gid}" if tag_gid else ""

    content = f"""---
From: ops_agent
To: asana_agent
Type: asana_write
Status: PENDING
Priority: normal
Created: {datetime.now(timezone.utc).isoformat()}
---

## Request
Action: create_task_in_project
Project GID: {NEEDS_PROJECT_GID}
Section GID: {NEEDS_SECTION_GID}
Task name: {task_name}{tag_line}

Description:
{description_block}

## Context
Auto-generated by create_order_tasks.py — new NEEDS order task for vendor: {vendor}
"""
    fname = _requests_filename("create", vendor)
    fpath = os.path.join(REQUESTS_DIR, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content)
    return fpath


def write_append_request(existing_task_gid, description_block, vendor):
    content = f"""---
From: ops_agent
To: asana_agent
Type: asana_write
Status: PENDING
Priority: normal
Created: {datetime.now(timezone.utc).isoformat()}
---

## Request
Action: append_to_description
Task GID: {existing_task_gid}

Description:
{description_block}

## Context
Auto-generated by create_order_tasks.py — appending items to existing NEEDS task for vendor: {vendor}
"""
    fname = _requests_filename("append", vendor)
    fpath = os.path.join(REQUESTS_DIR, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content)
    return fpath


def run(commit: bool):
    run_ts = datetime.now(timezone.utc).isoformat()
    print(f"{'COMMIT' if commit else 'SCAN'} — {run_ts}")

    if not os.path.exists(GROUPS_FILE):
        print(f"ERROR: {GROUPS_FILE} not found. Run compile_vendor_groups.py --commit first.")
        sys.exit(1)

    with open(GROUPS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    groups = data.get("vendor_groups", [])
    print(f"Vendor groups to process: {len(groups)}")

    created = appended = edge_cases = 0

    for group in groups:
        vendor = group["vendor_name"]
        vendor_norm = group["vendor_name_normalised"]
        items = group["items"]
        is_fallback = group.get("vendor_is_fallback", False)

        existing_gid = find_open_order_task(vendor_norm)
        description_block = build_description_block(group, run_ts)

        if existing_gid:
            action = "APPEND to existing"
            task_ref = existing_gid
        else:
            action = "CREATE new"
            task_ref = "(new)"

        print(f"  {vendor}: {action} task {task_ref} — {len(items)} item(s)"
              f"{' [FALLBACK]' if is_fallback else ''}")

        if not commit:
            if existing_gid:
                appended += 1
            else:
                created += 1
            continue

        try:
            if existing_gid:
                fpath = write_append_request(existing_gid, description_block, vendor)
                appended += 1
            else:
                supplier_tag = find_supplier_tag(vendor_norm)
                fpath = write_create_request(group, description_block, supplier_tag_gid=supplier_tag)
                created += 1
            print(f"    -> REQUESTS/ file: {os.path.basename(fpath)}")

            if is_fallback:
                for item in items:
                    write_ordering_edge_case({
                        "run_timestamp": run_ts,
                        "script": "create_order_tasks",
                        "stage": "order_creation",
                        "asana_task_gid": item["asana_task_gid"],
                        "task_name": item["task_name"],
                        "vendor_name": None,
                        "issue_type": "vendor_fallback",
                        "description": f"Ordered under fallback vendor '{vendor}' — update Vendor field in Parts Control",
                        "raw_data": None,
                    }, db_path=DB_PATH)
                    edge_cases += 1

            # Update ordering_queue status to 'ordered' for all items in this group
            now = datetime.now(timezone.utc).isoformat()
            for item in items:
                update_ordering_queue_status(
                    item["asana_task_gid"], "ordered",
                    ordered_at=now,
                    vendor_name_at_order=vendor,
                    vendor_is_fallback=1 if is_fallback else 0,
                    db_path=DB_PATH,
                )

        except Exception as exc:
            print(f"    ERROR: {exc}")
            write_ordering_edge_case({
                "run_timestamp": run_ts,
                "script": "create_order_tasks",
                "stage": "order_creation",
                "asana_task_gid": None,
                "task_name": vendor,
                "vendor_name": vendor,
                "issue_type": "no_task_created",
                "description": str(exc),
                "raw_data": json.dumps(group),
            }, db_path=DB_PATH)
            edge_cases += 1

    if commit:
        print(f"\nDone. Created: {created}  Appended: {appended}  Edge cases logged: {edge_cases}")
    else:
        print(f"\n[SCAN] Would create: {created + appended} REQUESTS/ file(s). Re-run with --commit.")


def main():
    parser = argparse.ArgumentParser(description="Create or append NEEDS order tasks per vendor.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan",   action="store_true")
    group.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    run(commit=args.commit)


if __name__ == "__main__":
    main()
