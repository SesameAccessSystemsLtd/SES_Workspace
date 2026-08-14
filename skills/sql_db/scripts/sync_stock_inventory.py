"""
sync_stock_inventory.py — Sync Asana KANBAN + Consumables → ops.db:stock_inventory

Sources:
  KANBAN stock project  GID: 632603478911321
  Consumables project   GID: 1200068443534209
Target:  ops.db table: stock_inventory
Writer:  db_writer.upsert_stock_inventory()

Stock quantities are the authoritative numbers from Asana.
stock_inventory is a READ-ONLY mirror for agents — stock qty changes must go
to Asana first via SAP; this sync then reflects them back.

Usage:
    python sync_stock_inventory.py
    python sync_stock_inventory.py --dry-run
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "purchasing"))

from sync_utils import paginate_tasks, get_custom_field, now_utc, setup_logging
import db_writer

KANBAN_GID      = "632603478911321"
CONSUMABLES_GID = "1200068443534209"

# Custom field GIDs — from asana_field_registry
GID_SESAME_PN      = "1213228090549470"
GID_STOCK_QTY      = "1126782469949969"
GID_TRIGGER_QTY    = "1198179728033183"
GID_MIN_STOCK_QTY  = "1205084645800516"
GID_VENDOR         = "854127216887083"
GID_STOCK_ACTION   = "1199111659373956"

OPT_FIELDS = (
    "gid,name,parent.gid,"
    "custom_fields.gid,custom_fields.type,custom_fields.resource_subtype,"
    "custom_fields.display_value,custom_fields.number_value,custom_fields.text_value,"
    "custom_fields.enum_value.gid,custom_fields.enum_value.name"
)

# lib/ is on sys.path via the db_writer import above
from path_config import p as _p
DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")


def task_to_row(task: dict, stock_project: str, sync_ts: str) -> dict | None:
    sesame_pn = get_custom_field(task, GID_SESAME_PN)
    if not sesame_pn:
        return None  # can't insert without the unique part number

    raw_qty = get_custom_field(task, GID_STOCK_QTY)
    try:
        stock_qty = float(raw_qty) if raw_qty is not None else 0.0
    except (ValueError, TypeError):
        stock_qty = 0.0

    raw_trig = get_custom_field(task, GID_TRIGGER_QTY)
    try:
        trigger_qty = float(raw_trig) if raw_trig is not None else None
    except (ValueError, TypeError):
        trigger_qty = None

    raw_min = get_custom_field(task, GID_MIN_STOCK_QTY)
    try:
        min_qty = float(raw_min) if raw_min is not None else None
    except (ValueError, TypeError):
        min_qty = None

    return {
        "asana_task_gid":     task["gid"],
        "sesame_pn":          sesame_pn,
        "kan_number":         task.get("name", "").strip(),
        "description":        None,
        "stock_qty":          stock_qty,
        "trigger_order_qty":  trigger_qty,
        "min_stock_qty":      min_qty,
        "stock_location":     None,
        "vendor_part_number": get_custom_field(task, GID_VENDOR),
        "stock_action":       get_custom_field(task, GID_STOCK_ACTION),
        "supplier_type":      None,
        "drawing_url":        None,
        "stock_project":      stock_project,
        "is_subtask":         1 if task.get("parent") else 0,
        "last_synced_at":     sync_ts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync KANBAN + Consumables → ops.db:stock_inventory")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log = setup_logging("sync_stock_inventory")
    sync_ts = now_utc()
    all_rows = []
    skipped_no_pn = 0

    for project_gid, project_name in [
        (KANBAN_GID,      "KANBAN"),
        (CONSUMABLES_GID, "Consumables"),
    ]:
        log.info("Fetching from %s (%s)", project_name, project_gid)
        tasks = paginate_tasks(project_gid, OPT_FIELDS)
        log.info("  %d tasks", len(tasks))
        for t in tasks:
            if not t.get("name"):
                continue
            row = task_to_row(t, project_name, sync_ts)
            if row:
                all_rows.append(row)
            else:
                skipped_no_pn += 1

    log.info("Rows prepared: %d  (skipped — no sesame_pn: %d)", len(all_rows), skipped_no_pn)

    if args.dry_run:
        import json
        print(json.dumps(all_rows[:5], indent=2, default=str))
        return

    result = db_writer.upsert_stock_inventory(all_rows, source="sync_stock_inventory",
                                               db_path=args.db_path)
    log.info("Done — written: %d  skipped: %d  errors: %d",
             result["written"], result["skipped"], result["errors"])


if __name__ == "__main__":
    main()
