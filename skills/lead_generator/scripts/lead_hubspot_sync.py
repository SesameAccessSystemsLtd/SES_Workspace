"""
lead_hubspot_sync.py — Sync lead records to HubSpot CRM as contacts.

Phase 1: --dry-run is the default. Previews what would be upserted without making API calls.
Phase 4: Real upserts via POST /crm/v3/objects/contacts/batch/upsert. Dedup key: email.

Owned by: scraper_agent (SAD)
Also callable by: sesame_marketing (SAM) after confirming leads dataset is FRESH.

Usage:
    # Preview without API calls (safe):
    python lead_hubspot_sync.py --input leads_enriched.json --dry-run

    # Real sync (Phase 4 — requires HUBSPOT_API_KEY in ~/.hermes/.env):
    python lead_hubspot_sync.py --input leads_enriched.json --lifecycle-stage lead

    # Sync as marketing-qualified leads:
    python lead_hubspot_sync.py --input leads_enriched.json --lifecycle-stage marketingqualifiedlead

Output:
    Prints a JSON summary to stdout:
    { "total", "created", "updated", "skipped", "errors": [...], "synced_at", "dry_run" }

Exit code 0 on success, 1 on error.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# HubSpot batch upsert endpoint
HS_BATCH_UPSERT_URL = "https://api.hubapi.com/crm/v3/objects/contacts/batch/upsert"

# Max contacts per HubSpot batch request
HS_BATCH_SIZE = 100

# Mapping from lead dict keys to HubSpot contact property names
PROPERTY_MAP = {
    "contact_name": None,          # Split into firstname / lastname below
    "job_title": "jobtitle",
    "company_name": "company",
    "phone": "phone",
    "region": "lead_region",
    "industry": "lead_source_industry",
    "pain_points": "lead_pain_points",
    "source": "lead_source_channel",
    "apollo_id": "apollo_id",
    "linkedin_url": "linkedin_url",
}


def _build_hs_properties(lead: dict, lifecycle_stage: str) -> dict:
    """Convert a lead dict into HubSpot contact properties."""
    props = {"lifecyclestage": lifecycle_stage}

    # Split contact_name into firstname / lastname
    name = lead.get("contact_name", "")
    parts = name.strip().split(" ", 1)
    props["firstname"] = parts[0] if parts else ""
    props["lastname"] = parts[1] if len(parts) > 1 else ""

    for lead_key, hs_key in PROPERTY_MAP.items():
        if hs_key and lead.get(lead_key):
            props[hs_key] = str(lead[lead_key])

    return props


def _sync_batch(batch: list[dict], api_key: str, requests_module) -> dict:
    """
    Send one batch of contacts to HubSpot batch upsert endpoint.
    Returns { "created": int, "updated": int, "errors": list }.
    """
    inputs = []
    for lead in batch:
        if not lead.get("email"):
            continue
        inputs.append({
            "idProperty": "email",
            "id": lead["email"],
            "properties": _build_hs_properties(lead, lead.get("_lifecycle_stage", "lead")),
        })

    if not inputs:
        return {"created": 0, "updated": 0, "errors": []}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"inputs": inputs}

    resp = requests_module.post(HS_BATCH_UPSERT_URL, headers=headers, json=payload, timeout=30)

    if resp.status_code in (200, 201):
        results = resp.json().get("results", [])
        created = sum(1 for r in results if r.get("properties", {}).get("hs_object_id") and r.get("createdAt"))
        updated = len(results) - created
        return {"created": created, "updated": updated, "errors": []}

    # Partial or full error
    try:
        error_body = resp.json()
    except Exception:
        error_body = {"status": resp.status_code, "message": resp.text[:200]}

    log.error("HubSpot batch upsert error %d: %s", resp.status_code, error_body)
    return {"created": 0, "updated": 0, "errors": [error_body]}


def sync_to_crm(
    leads: list[dict],
    lifecycle_stage: str = "lead",
    dry_run: bool = True,
) -> dict:
    """
    Upsert leads to HubSpot as contacts. Dedup key: email.

    Args:
        leads:           List of lead dicts (from generate_leads or enrich_leads)
        lifecycle_stage: HubSpot lifecycle stage to assign (default: 'lead')
        dry_run:         If True, log what would be sent but make no API calls (default: True)

    Returns:
        {
          "total": int,
          "created": int,
          "updated": int,
          "skipped": int,          # leads with no email address
          "errors": list,
          "synced_at": str,        # ISO 8601
          "dry_run": bool
        }
    """
    valid_leads = [l for l in leads if l.get("email")]
    skipped = len(leads) - len(valid_leads)

    if skipped:
        log.warning("%d leads skipped — no email address.", skipped)

    if dry_run:
        log.info(
            "[DRY RUN] Would upsert %d contacts to HubSpot (lifecycle_stage=%s). "
            "No API calls made. Run without --dry-run to sync for real.",
            len(valid_leads),
            lifecycle_stage,
        )
        return {
            "total": len(leads),
            "created": 0,
            "updated": 0,
            "skipped": skipped,
            "would_sync": len(valid_leads),
            "errors": [],
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": True,
        }

    api_key = os.environ.get("HUBSPOT_API_KEY")
    if not api_key:
        raise ValueError(
            "HUBSPOT_API_KEY environment variable is not set. "
            "It should be present in ~/.hermes/.env."
        )

    try:
        import requests  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("requests library is required for HubSpot sync. It should be in the venv.")

    # Tag leads with lifecycle stage for batch builder
    for lead in valid_leads:
        lead["_lifecycle_stage"] = lifecycle_stage

    total_created = 0
    total_updated = 0
    all_errors = []

    # Chunk into batches of HS_BATCH_SIZE
    for start in range(0, len(valid_leads), HS_BATCH_SIZE):
        batch = valid_leads[start:start + HS_BATCH_SIZE]
        log.info("Syncing batch %d–%d of %d...", start + 1, start + len(batch), len(valid_leads))
        result = _sync_batch(batch, api_key, requests)
        total_created += result["created"]
        total_updated += result["updated"]
        all_errors.extend(result["errors"])

    synced_at = datetime.now(timezone.utc).isoformat()
    log.info(
        "HubSpot sync complete. created=%d updated=%d skipped=%d errors=%d",
        total_created, total_updated, skipped, len(all_errors),
    )

    return {
        "total": len(leads),
        "created": total_created,
        "updated": total_updated,
        "skipped": skipped,
        "errors": all_errors,
        "synced_at": synced_at,
        "dry_run": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync lead records to HubSpot CRM.")
    parser.add_argument("--input", required=True, help="Path to enriched leads JSON file.")
    parser.add_argument(
        "--lifecycle-stage",
        default="lead",
        help="HubSpot lifecycle stage to assign (e.g. lead, marketingqualifiedlead). Default: lead.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview without making API calls (default: True). Use --no-dry-run to sync for real.",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Disable dry-run and perform real HubSpot API calls.",
    )
    args = parser.parse_args()

    try:
        with open(args.input, encoding="utf-8") as f:
            leads = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR reading input: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(leads, list):
        print("ERROR: Input must be a JSON array of lead objects.", file=sys.stderr)
        sys.exit(1)

    try:
        result = sync_to_crm(leads, lifecycle_stage=args.lifecycle_stage, dry_run=args.dry_run)
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        log.error("Unexpected error during HubSpot sync: %s", exc)
        sys.exit(1)

    print(json.dumps(result, indent=2))

    if result.get("errors"):
        sys.exit(1)


if __name__ == "__main__":
    main()
