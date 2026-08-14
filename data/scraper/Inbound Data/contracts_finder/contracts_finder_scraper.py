"""
contracts_finder_scraper.py — SesameAccess Pipeline
====================================================
Queries the UK Contracts Finder REST API for contract notices that may
require disability access lifts supplied by SesameAccess.

Returns a list of filtered, deduplicated lead dicts ready for
HubSpot ingestion.

Usage:
    python contracts_finder_scraper.py           # standalone run → CSV
    from contracts_finder_scraper import run_scrape  # programmatic
"""

import json
import os
import time
import logging
import csv
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import requests

from config import (
    CF_SEARCH_URL, CF_NOTICE_BASE, CF_PAGE_SIZE, CF_MAX_PAGES, CF_REQUEST_DELAY,
    INCLUDE_KEYWORDS, EXCLUDE_KEYWORDS, SEARCH_TERMS, COUNCIL_SIGNALS,
    SEEN_CONTRACTS_FILE, OUTPUT_CSV, LOG_DIR,
)

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
Path(LOG_DIR).mkdir(exist_ok=True)
log_path = Path(LOG_DIR) / f"scraper_{date.today().isoformat()}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("cf_scraper")


# ─────────────────────────────────────────────
# Deduplication helpers
# ─────────────────────────────────────────────
def load_seen_contracts() -> set:
    """Load the set of contract reference IDs already processed."""
    p = Path(SEEN_CONTRACTS_FILE)
    if p.exists():
        data = json.loads(p.read_text())
        return set(data.get("refs", []))
    return set()


def save_seen_contracts(seen: set) -> None:
    p = Path(SEEN_CONTRACTS_FILE)
    existing = json.loads(p.read_text()) if p.exists() else {}
    existing["refs"] = sorted(seen)
    existing["last_updated"] = datetime.utcnow().isoformat()
    p.write_text(json.dumps(existing, indent=2))


# ─────────────────────────────────────────────
# Keyword filtering
# ─────────────────────────────────────────────
def _haystack(notice: dict) -> str:
    """Combine title + description into one lowercase string for keyword matching."""
    tender = notice.get("tender", {})
    parts = [
        notice.get("title", ""),
        notice.get("description", ""),
        tender.get("title", ""),
        tender.get("description", ""),
    ]
    return " ".join(p for p in parts if p).lower()


def passes_keyword_filter(notice: dict, contact_email: str = "") -> tuple[bool, str]:
    """
    Returns (passes: bool, reason: str).

    Exclusion (hard reject): title/description matches any EXCLUDE_KEYWORD.
    Inclusion (two paths):
      1. Title/description matches an INCLUDE_KEYWORD, OR
      2. Contact email ends with .gov.uk — any public-sector council
         procurement contact is a valid outreach target regardless of
         whether their specific notice title mentions a lift.
    """
    hay = _haystack(notice)

    # Hard reject — exclude keywords take priority over everything
    for kw in EXCLUDE_KEYWORDS:
        if kw.lower() in hay:
            return False, f"excluded by keyword '{kw}'"

    # Path 1: keyword match in title/description
    for kw in INCLUDE_KEYWORDS:
        if kw.lower() in hay:
            return True, f"matched include keyword '{kw}'"

    # Path 2: government email domain — council procurement contact
    if contact_email and contact_email.lower().endswith(".gov.uk"):
        return True, "gov.uk email domain"

    return False, "no include keyword matched and no gov.uk email"


def is_relevant_buyer(buyer_name: str) -> bool:
    """Return True if the buyer name suggests a public-sector council or housing body."""
    if not buyer_name:
        return True  # Don't filter out unknown buyers
    low = buyer_name.lower()
    return any(sig.lower() in low for sig in COUNCIL_SIGNALS)


# ─────────────────────────────────────────────
# Contact extraction from notice
# ─────────────────────────────────────────────
def extract_contact(notice: dict) -> dict:
    """
    Pull contact details from the OCDS release.
    The OCDS format stores contacts in parties[] where role == 'buyer'.
    Falls back to the top-level buyer.contactPoint if present.
    Returns a dict with name, email, phone, url (any may be None).
    """
    # Primary: parties array (OCDS format)
    for party in notice.get("parties", []):
        if "buyer" in party.get("roles", []):
            cp = party.get("contactPoint", {})
            if cp.get("email"):
                return {
                    "contact_name":  cp.get("name"),
                    "contact_email": cp.get("email"),
                    "contact_phone": cp.get("telephone"),
                    "contact_url":   cp.get("url"),
                    "found_via":     "notice",
                }
    # Fallback: top-level buyer.contactPoint (legacy format)
    cp = notice.get("buyer", {}).get("contactPoint", {})
    return {
        "contact_name":  cp.get("name"),
        "contact_email": cp.get("email"),
        "contact_phone": cp.get("telephone"),
        "contact_url":   cp.get("url"),
        "found_via":     "notice" if cp.get("email") else "none",
    }


# ─────────────────────────────────────────────
# Notice → lead dict
# ─────────────────────────────────────────────
def notice_to_lead(notice: dict, matched_keyword: str, contact: dict = None) -> dict:
    """Convert a raw Contracts Finder notice dict into a clean lead dict."""
    buyer   = notice.get("buyer", {})
    tender  = notice.get("tender", {})
    value   = tender.get("value", {})
    period  = tender.get("tenderPeriod", {})
    if contact is None:
        contact = extract_contact(notice)

    # OCDS uses 'ocid' as the stable identifier; 'id' is the release ID
    ocid   = notice.get("ocid", "")
    ref_id = notice.get("id", ocid)
    # Notice URL: OCID ends with the numeric notice ID, e.g. ocds-b5fd17-000123456
    notice_num = ocid.split("-")[-1] if ocid else ref_id.split("-")[-1]

    return {
        # Contract metadata
        "contract_ref":    ocid or ref_id,
        "contract_title":  tender.get("title", "") or notice.get("title", ""),
        "description":     (tender.get("description", "") or notice.get("description", ""))[:500],
        "status":          tender.get("status", notice.get("status", "")),
        "published_date":  notice.get("date", notice.get("publishedDate", "")),
        "deadline":        period.get("endDate", ""),
        "contract_value":  value.get("amount"),
        "currency":        value.get("currency", "GBP"),
        "notice_url":      f"{CF_NOTICE_BASE}/{notice_num}" if notice_num else "",
        "matched_keyword": matched_keyword,

        # Buyer / council
        "buyer_org":       buyer.get("name", ""),
        "buyer_id":        buyer.get("id", ""),
        "buyer_address":   _format_address(buyer.get("address", {})),

        # Contact (from notice)
        **contact,

        # Pipeline metadata
        "scrape_date":     date.today().isoformat(),
        "hubspot_pushed":  False,
        "hubspot_contact_id": None,
    }


def _format_address(addr: dict) -> str:
    parts = [
        addr.get("streetAddress", ""),
        addr.get("locality", ""),
        addr.get("region", ""),
        addr.get("postalCode", ""),
        addr.get("countryName", ""),
    ]
    return ", ".join(p for p in parts if p)


# ─────────────────────────────────────────────
# API calls  (OCDS cursor-based pagination)
# ─────────────────────────────────────────────
def search_contracts_finder(keyword: str, published_from: Optional[str] = None) -> list[dict]:
    """
    Paginate through the Contracts Finder OCDS API for a given keyword.
    Uses cursor-based pagination via links.next.
    Returns a list of raw OCDS release dicts.
    """
    params = {
        "noticeSummary": keyword,
        "size":          CF_PAGE_SIZE,
    }
    if published_from:
        params["publishedFrom"] = published_from

    all_results = []
    next_url = CF_SEARCH_URL
    page = 0

    while next_url and page < CF_MAX_PAGES:
        page += 1
        try:
            # First page uses base URL + params; subsequent pages use links.next directly
            if page == 1:
                resp = requests.get(next_url, params=params, timeout=30)
            else:
                resp = requests.get(next_url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            log.error(f"API error for keyword '{keyword}' page {page}: {e}")
            break

        releases = data.get("releases", [])
        if not releases:
            log.info(f"  Keyword '{keyword}': page {page} empty — stopping")
            break

        all_results.extend(releases)
        log.info(f"  Keyword '{keyword}': page {page} → {len(releases)} releases (running total: {len(all_results)})")

        next_url = data.get("links", {}).get("next", "")
        if not next_url:
            break

        time.sleep(CF_REQUEST_DELAY)

    return all_results


# ─────────────────────────────────────────────
# Main scrape function
# ─────────────────────────────────────────────
def run_scrape(
    since_date: Optional[str] = None,
    respect_seen: bool = True,
) -> list[dict]:
    """
    Run the full Contracts Finder scrape.

    Args:
        since_date:    ISO date string (e.g. "2025-01-01") to limit to recent notices.
                       Pass None to fetch all time (not recommended for daily runs).
        respect_seen:  If True, skip contract refs already in seen_contracts.json.

    Returns:
        List of lead dicts, deduplicated and keyword-filtered.
    """
    log.info("=" * 60)
    log.info(f"SesameAccess × Contracts Finder scrape starting")
    log.info(f"Since: {since_date or 'all time'} | Respect seen: {respect_seen}")
    log.info("=" * 60)

    seen_refs = load_seen_contracts() if respect_seen else set()
    raw_notices: dict[str, dict] = {}  # ref_id → notice (dedup across search terms)

    # ── Collect raw notices across all search terms ──────────────────
    for term in SEARCH_TERMS:
        log.info(f"\nSearching: '{term}'")
        results = search_contracts_finder(term, published_from=since_date)
        for notice in results:
            ref = notice.get("id", notice.get("ocid", ""))
            if ref and ref not in raw_notices:
                raw_notices[ref] = notice
        log.info(f"  Running unique total: {len(raw_notices)}")
        time.sleep(CF_REQUEST_DELAY)

    log.info(f"\nTotal unique notices from API: {len(raw_notices)}")

    # ── Filter & convert ─────────────────────────────────────────────
    leads = []
    stats = {"total": len(raw_notices), "passed": 0, "excluded_kw": 0,
             "excluded_buyer": 0, "already_seen": 0, "duplicate_email": 0}

    for ref, notice in raw_notices.items():
        # Skip if we've processed this contract before
        if respect_seen and ref in seen_refs:
            stats["already_seen"] += 1
            continue

        # Extract contact first so we can pass email to keyword filter
        contact = extract_contact(notice)

        # Keyword filter (gov.uk email is an automatic pass if no exclude match)
        passes, reason = passes_keyword_filter(notice, contact.get("contact_email", "") or "")
        if not passes:
            stats["excluded_kw"] += 1
            log.debug(f"  SKIP [{ref[:20]}...] {reason}")
            continue

        # Buyer filter
        buyer_name = notice.get("buyer", {}).get("name", "")
        if not is_relevant_buyer(buyer_name):
            stats["excluded_buyer"] += 1
            log.debug(f"  SKIP [{ref[:20]}...] buyer '{buyer_name}' not council-type")
            continue

        # Build lead (pass pre-extracted contact to avoid double-parsing)
        matched_kw = reason.split("'")[1] if "'" in reason else reason
        lead = notice_to_lead(notice, matched_kw, contact=contact)
        leads.append(lead)
        seen_refs.add(ref)
        stats["passed"] += 1
        log.info(f"  ✓ PASS [{lead['buyer_org']}] {lead['contract_title'][:60]}")

    # ── Deduplicate by contact email ──────────────────────────────────
    # The same contact email can appear on multiple contract notices.
    # Keep only the first lead per email so they're never queued twice.
    seen_emails: set = set()
    deduped: list = []
    for lead in leads:
        email = (lead.get("contact_email") or "").strip().lower()
        if email:
            if email in seen_emails:
                log.info(f"  DUP EMAIL: {email} — duplicate across contracts, keeping first")
                stats["duplicate_email"] += 1
                continue
            seen_emails.add(email)
        deduped.append(lead)
    leads = deduped

    # ── Persist seen refs ─────────────────────────────────────────────
    if respect_seen:
        save_seen_contracts(seen_refs)

    # ── Log summary ──────────────────────────────────────────────────
    log.info("\n" + "=" * 60)
    log.info(f"SCRAPE SUMMARY")
    log.info(f"  Total unique from API : {stats['total']}")
    log.info(f"  Already seen (skip)   : {stats['already_seen']}")
    log.info(f"  Keyword filtered out  : {stats['excluded_kw']}")
    log.info(f"  Buyer filtered out    : {stats['excluded_buyer']}")
    log.info(f"  Duplicate email (skip): {stats['duplicate_email']}")
    log.info(f"  ✅ Passed to pipeline : {len(leads)}")
    log.info("=" * 60)

    return leads


# ─────────────────────────────────────────────
# CSV export (standalone use)
# ─────────────────────────────────────────────
def export_csv(leads: list[dict], path: str = OUTPUT_CSV) -> None:
    if not leads:
        log.info("No leads to export.")
        return
    keys = leads[0].keys()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(leads)
    log.info(f"Exported {len(leads)} leads → {path}")


# ─────────────────────────────────────────────
# Standalone entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scrape Contracts Finder for SesameAccess leads")
    parser.add_argument("--since", default=None, help="ISO date e.g. 2025-01-01 (default: 90 days ago)")
    parser.add_argument("--all-time", action="store_true", help="Fetch all time (ignores --since)")
    parser.add_argument("--no-dedup", action="store_true", help="Ignore seen_contracts.json")
    args = parser.parse_args()

    if args.all_time:
        since = None
    elif args.since:
        since = args.since
    else:
        from datetime import timedelta
        since = (date.today() - timedelta(days=90)).isoformat()

    leads = run_scrape(since_date=since, respect_seen=not args.no_dedup)
    export_csv(leads)
    print(f"\n✅ Done — {len(leads)} new leads. See {OUTPUT_CSV} and {log_path}")
