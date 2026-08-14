"""
lead_enricher.py — Enrich lead records via Apollo.io contact verification.

Phase 1 (current): Stub — passes leads through unchanged with enrichment_status="skipped".
Phase 2: Calls Apollo.io /api/v1/people/match per lead to verify email, add linkedin_url, etc.

Owned by: scraper_agent (SAD)

Usage:
    # Phase 1 — pass-through (no API calls):
    python lead_enricher.py --input leads_raw.json --output leads_enriched.json

    # Phase 2 — real enrichment (requires APOLLO_API_KEY in env):
    python lead_enricher.py --input leads_raw.json --output leads_enriched.json --enrich-limit 50

Output:
    Writes enriched lead JSON to --output path.
    Prints a summary JSON to stdout: { "total", "enriched", "not_found", "skipped", "errors" }

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


def enrich_leads(
    leads: list[dict],
    fields: list[str] | None = None,
    enrich_limit: int | None = None,
) -> list[dict]:
    """
    Enrich a list of lead dicts with Apollo.io data.

    Phase 1: Returns leads unchanged with enrichment_status="skipped".
    Phase 2: Calls Apollo.io /people/match for each lead (up to enrich_limit).

    Args:
        leads:         List of lead dicts from generate_leads()
        fields:        Optional list of Apollo fields to pull. None = defaults.
        enrich_limit:  Max number of leads to enrich (caps Apollo credit spend).
                       Leads beyond the limit receive enrichment_status="skipped".

    Returns:
        List of lead dicts with added keys:
        apollo_id, verified_email, linkedin_url, enrichment_status
    """
    api_key = os.environ.get("APOLLO_API_KEY")

    if not api_key:
        log.info(
            "APOLLO_API_KEY not set — running in stub mode. "
            "All leads will have enrichment_status='skipped'. "
            "Set APOLLO_API_KEY in ~/.hermes/.env to enable real enrichment."
        )
        return _enrich_stub(leads)

    # Phase 2 — real Apollo enrichment
    try:
        import requests  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("requests library is required for Apollo enrichment. It should be in the venv.")

    limit = enrich_limit if enrich_limit is not None else len(leads)
    enriched = []

    for i, lead in enumerate(leads):
        if i >= limit:
            enriched.append({**lead, "enrichment_status": "skipped"})
            continue
        enriched.append(_enrich_single_apollo(lead, api_key, requests))

    return enriched


def _enrich_stub(leads: list[dict]) -> list[dict]:
    """Phase 1: return leads unchanged, marking enrichment as skipped."""
    result = []
    for lead in leads:
        result.append({
            **lead,
            "apollo_id": lead.get("apollo_id", ""),
            "verified_email": lead.get("verified_email", ""),
            "linkedin_url": lead.get("linkedin_url", ""),
            "enrichment_status": "skipped",
        })
    return result


def _enrich_single_apollo(lead: dict, api_key: str, requests_module) -> dict:
    """Call Apollo.io /people/match for a single lead. Returns enriched lead dict."""
    url = "https://api.apollo.io/api/v1/people/match"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }
    payload = {
        "first_name": lead.get("contact_name", "").split()[0] if lead.get("contact_name") else "",
        "last_name": lead.get("contact_name", "").split()[-1] if lead.get("contact_name") else "",
        "organization_name": lead.get("company_name", ""),
        "email": lead.get("email", ""),
        "reveal_personal_emails": False,
    }

    try:
        resp = requests_module.post(url, headers=headers, json=payload, timeout=15)

        remaining = resp.headers.get("x-rate-limit-remaining", "unknown")
        log.debug("Apollo match call. Rate limit remaining: %s", remaining)

        if resp.status_code == 404:
            return {**lead, "apollo_id": "", "verified_email": "", "linkedin_url": "", "enrichment_status": "not_found"}

        resp.raise_for_status()
        person = resp.json().get("person") or {}

        if not person:
            return {**lead, "apollo_id": "", "verified_email": "", "linkedin_url": "", "enrichment_status": "not_found"}

        return {
            **lead,
            "apollo_id": person.get("id", ""),
            "verified_email": person.get("email", lead.get("email", "")),
            "linkedin_url": person.get("linkedin_url", ""),
            "enrichment_status": "enriched",
        }

    except Exception as exc:
        log.warning("Apollo enrichment failed for %s: %s", lead.get("email", "unknown"), exc)
        return {**lead, "apollo_id": "", "verified_email": "", "linkedin_url": "", "enrichment_status": "error"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich lead records via Apollo.io.")
    parser.add_argument("--input", required=True, help="Path to raw leads JSON file.")
    parser.add_argument("--output", required=True, help="Path to write enriched leads JSON.")
    parser.add_argument(
        "--enrich-limit",
        type=int,
        default=None,
        help="Max leads to enrich (caps Apollo credit spend). Default: all leads.",
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
        enriched = enrich_leads(leads, enrich_limit=args.enrich_limit)
    except Exception as exc:
        print(f"ERROR during enrichment: {exc}", file=sys.stderr)
        sys.exit(1)

    # Write output
    try:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(enriched, f, indent=2, ensure_ascii=False)
        log.info("Enriched leads written to: %s", args.output)
    except OSError as exc:
        print(f"ERROR writing output: {exc}", file=sys.stderr)
        sys.exit(1)

    # Summary to stdout
    statuses = [lead.get("enrichment_status", "skipped") for lead in enriched]
    summary = {
        "total": len(enriched),
        "enriched": statuses.count("enriched"),
        "not_found": statuses.count("not_found"),
        "skipped": statuses.count("skipped"),
        "errors": statuses.count("error"),
        "output_path": args.output,
        "enriched_at": datetime.now(timezone.utc).isoformat(),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
