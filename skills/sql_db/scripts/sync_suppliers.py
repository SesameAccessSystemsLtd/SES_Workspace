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


# Custom-field GIDs for the Suppliers project (414986125671548). Note:
# paginate_tasks does NOT return custom_field names (they are empty ''), so we
# must identify fields by GID — never by name.
CF_EMAIL        = "1109691357791838"   # Email (text)
CF_NUMBER       = "1109691357791817"   # Number(s) (text)
CF_CONTACT      = "1109691357791813"   # Contact Name(s) and details (text)
CF_PAYMENT      = "1118860373285177"   # Payment Type (enum)
CF_RISK         = "1200497508453823"   # Risk Profile (enum)
CF_LEAD         = "1200509982608920"   # Typical lead times (number)
CF_SUPPLIER_TYPE = "1205678172863621"  # Supplier Type (multi_enum)


def _cf(task: dict, cf_gid: str) -> str | None:
    """Return the display/text value of a custom field by GID."""
    for cf in task.get("custom_fields", []) or []:
        if cf.get("gid") == cf_gid:
            v = cf.get("display_value")
            if v in (None, "", "None"):
                return None
            return str(v)
    return None


def task_to_row(task: dict, sync_ts: str) -> dict:
    tags = task.get("tags", []) or []
    vendor_name = task.get("name", "").strip()
    # Supplier contact enrichment — map the Supplier project's custom fields
    # (project 414986125671548) into the analytics columns by GID.
    contact_phone = _cf(task, CF_NUMBER)
    contact_email = _cf(task, CF_EMAIL)
    contact_name = _cf(task, CF_CONTACT)
    payment_type = _cf(task, CF_PAYMENT)
    risk_profile = _cf(task, CF_RISK)
    lead = _cf(task, CF_LEAD)
    supplier_type = _cf(task, CF_SUPPLIER_TYPE)
    # primary_domain — derive the root domain from the first email address, if any
    primary_domain = None
    if contact_email:
        try:
            import re as _re
            m = _re.search(r"@([\w.\-]+)", contact_email)
            if m:
                primary_domain = m.group(1).lower()
        except Exception:
            primary_domain = None
    return {
        "asana_task_gid": task["gid"],
        "vendor_name":    vendor_name,
        "primary_domain": primary_domain,
        "contact_name":   contact_name,
        "contact_email":  contact_email,
        "contact_phone":  contact_phone,
        "payment_type":   payment_type,
        "risk_profile":   risk_profile,
        "typical_lead_times_days": lead,
        "supplier_type":  supplier_type,
        "asana_tag_gid":  _pick_primary_tag(vendor_name, tags),
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

    # (UNIQUE on primary_domain was dropped 2026-08-27 — multiple suppliers/contacts
    #  legitimately share a domain, e.g. @gmail.com. The old collision-blanking pass
    #  that guarded that constraint is removed; all domains now populate.)

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
