"""
compile_vendor_groups.py — Group queued ordering items by vendor, write staging JSON.

Reads all ordering_queue rows with status='grouped', groups by vendor name
(normalised), and writes a staging JSON file for create_order_tasks.py.

Items with no vendor get the appropriate fallback name + tag GID:
  - item_type='stock'      -> "SESAME BOM SUPPLIER"  (tag: 722775702060138)
  - item_type='consumable' -> "Consumables Supplier" (tag: 1126036408841509)

Usage:
    python compile_vendor_groups.py --scan    # print grouped summary, no file write
    python compile_vendor_groups.py --commit  # write vendor_groups.json to workspace
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from path_config import p as _p

DB_PATH    = _p("db.ops")
# Consumed by create_order_tasks.py at the same workspace path (moved off .openclaw 2026-07-09).
OUT_PATH   = r"C:\Users\GilesHugo\SES_Workspace\skills\purchasing\vendor_groups.json"

# Fallback vendor config
FALLBACK_VENDORS = {
    "stock":      {"name": "SESAME BOM SUPPLIER",  "tag_gid": "722775702060138"},
    "consumable": {"name": "Consumables Supplier", "tag_gid": "1126036408841509"},
}

# NEEDS project + section for new order tasks
NEEDS_PROJECT_GID   = "1207196303096568"
NEEDS_SECTION_GID   = "1207647263791647"  # ADDRESSING


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_queued_items():
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM ordering_queue WHERE status = 'grouped' ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def resolve_vendor(item):
    vendor_name = (item.get("vendor_name") or "").strip()
    vendor_normalised = (item.get("vendor_name_normalised") or "").strip()
    item_type = item.get("item_type") or "stock"

    if vendor_normalised:
        return vendor_normalised, vendor_name, None, False

    fallback = FALLBACK_VENDORS.get(item_type, FALLBACK_VENDORS["stock"])
    return (
        fallback["name"].lower(),   # normalised key
        fallback["name"],           # display name
        fallback["tag_gid"],        # tag to apply
        True,                       # is_fallback
    )


def compile_groups(items):
    groups = {}  # vendor_key -> group dict

    for item in items:
        vendor_key, vendor_display, fallback_tag_gid, is_fallback = resolve_vendor(item)

        if vendor_key not in groups:
            groups[vendor_key] = {
                "vendor_name": vendor_display,
                "vendor_name_normalised": vendor_key,
                "vendor_is_fallback": is_fallback,
                "fallback_tag_gid": fallback_tag_gid,
                "items": [],
            }

        groups[vendor_key]["items"].append({
            "asana_task_gid": item["asana_task_gid"],
            "task_name": item["task_name"],
            "sesame_pn": item["sesame_pn"],
            "item_type": item["item_type"],
            "order_qty": item["order_qty"],
            "unit_price": item.get("unit_price"),
            "stock_qty_at_detection": item["stock_qty_at_detection"],
            "parts_control_task_gid": item["parts_control_task_gid"],
            "vendor_is_fallback": is_fallback,
        })

    return groups


def format_summary(groups):
    lines = []
    for vendor_key, group in sorted(groups.items()):
        fb = " [FALLBACK]" if group["vendor_is_fallback"] else ""
        lines.append(f"  {group['vendor_name']}{fb} — {len(group['items'])} item(s)")
        for item in group["items"]:
            qty = f"x{item['order_qty']}" if item["order_qty"] else "qty=TBC"
            lines.append(f"      {item['task_name']} ({item['item_type']}) {qty}")
    return "\n".join(lines)


def run(commit: bool):
    run_ts = datetime.now(timezone.utc).isoformat()
    print(f"{'COMMIT' if commit else 'SCAN'} — {run_ts}")

    items = load_queued_items()
    print(f"ordering_queue items with status='grouped': {len(items)}")

    if not items:
        print("Nothing to compile.")
        return

    groups = compile_groups(items)
    print(f"Vendor groups: {len(groups)}")
    print(format_summary(groups))

    if not commit:
        print("\n[SCAN] No file written. Re-run with --commit to write vendor_groups.json.")
        return

    output = {
        "compiled_at": run_ts,
        "total_items": len(items),
        "vendor_groups": list(groups.values()),
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nWritten: {OUT_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Group ordering_queue items by vendor.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan",   action="store_true", help="Print summary only")
    group.add_argument("--commit", action="store_true", help="Write vendor_groups.json")
    args = parser.parse_args()
    run(commit=args.commit)


if __name__ == "__main__":
    main()
