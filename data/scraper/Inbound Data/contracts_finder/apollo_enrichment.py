"""
apollo_enrichment.py — SesameAccess Pipeline
=============================================
Fallback contact enrichment via Apollo.io People API.
Called when a Contracts Finder notice contains no contact email.

Strategy:
  1. Search Apollo for people at the buyer organisation
  2. Filter by job title keywords relevant to disability/accessibility procurement
  3. Return the best-matching contact (ranked by title relevance)
  4. Gracefully degrade — if Apollo unavailable or quota hit, return lead unchanged

Apollo API docs: https://apolloio.github.io/apollo-api-docs/
"""

import logging
import time
from typing import Optional

import requests

from config import (
    APOLLO_API_KEY, APOLLO_API_URL,
    COUNCIL_CONTACT_ROLES, APOLLO_MAX_RESULTS_PER_ORG,
)

log = logging.getLogger("cf_apollo")


# ─────────────────────────────────────────────
# Role relevance scoring
# ─────────────────────────────────────────────
# Title keywords ranked by how useful the contact is for SesameAccess outreach.
# Higher score = more relevant.
ROLE_SCORE_MAP = [
    # Most relevant — directly involved in DFG / housing adaptations
    (["disabled facilities grant", "dfg officer", "dfg manager"],             100),
    (["housing adaptations", "home adaptations", "adaptation officer"],        95),
    (["occupational therapist", "OT "],                                        90),
    (["home improvement", "improvement agency", "hia manager"],                85),

    # Relevant — procurement / capital projects that would commission lifts
    (["accessibility officer", "access officer", "inclusion officer"],         80),
    (["capital projects", "capital programme"],                                75),
    (["estates manager", "property manager", "facilities manager"],            70),
    (["commissioning officer", "commissioning manager"],                       65),
    (["procurement officer", "procurement manager", "procurement lead"],       60),
    (["portfolio project manager", "project manager", "programme manager"],    55),

    # Useful but generic — catch-all if above yield nothing
    (["housing officer", "housing manager", "housing director"],               40),
    (["director", "head of", "assistant director"],                            30),
]


def score_title(title: str) -> int:
    """Return a relevance score for a job title string."""
    if not title:
        return 0
    lower = title.lower()
    for keywords, score in ROLE_SCORE_MAP:
        for kw in keywords:
            if kw.lower() in lower:
                return score
    return 5  # Non-zero so we don't totally discard generic contacts


# ─────────────────────────────────────────────
# Apollo API helpers
# ─────────────────────────────────────────────
def _apollo_post(endpoint: str, body: dict) -> dict:
    url  = f"{APOLLO_API_URL}{endpoint}"
    hdrs = {
        "Content-Type":  "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key":     APOLLO_API_KEY,
    }
    resp = requests.post(url, headers=hdrs, json=body, timeout=20)
    resp.raise_for_status()
    return resp.json()


def search_people_at_org(
    organisation_name: str,
    max_results: int = APOLLO_MAX_RESULTS_PER_ORG,
) -> list[dict]:
    """
    Search Apollo for people at a named organisation.
    Returns a list of raw Apollo people dicts.
    """
    if not APOLLO_API_KEY:
        log.debug("APOLLO_API_KEY not set — skipping enrichment")
        return []

    body = {
        "q_organization_name":      organisation_name,
        "contact_email_status_cd":  ["verified", "likely_to_engage"],
        "person_titles":            COUNCIL_CONTACT_ROLES,
        "per_page":                 max_results,
        "page":                     1,
    }

    try:
        data    = _apollo_post("/people/search", body)
        people  = data.get("people", [])
        log.info(f"  Apollo: {len(people)} people found at '{organisation_name}'")
        return people
    except requests.HTTPError as e:
        if e.response.status_code == 422:
            # Apollo couldn't match the org name — try a broader search
            log.debug(f"  Apollo 422 for '{organisation_name}' — trying keyword search")
            return _broad_search(organisation_name, max_results)
        elif e.response.status_code == 429:
            log.warning("  Apollo rate limit hit — pausing 5 s")
            time.sleep(5)
            return []
        else:
            log.warning(f"  Apollo error ({e.response.status_code}): {e}")
            return []
    except Exception as e:
        log.warning(f"  Apollo exception: {e}")
        return []


def _broad_search(query: str, max_results: int) -> list[dict]:
    """Fallback: search Apollo by organisation keyword without title filter."""
    try:
        body = {
            "q_organization_name": query,
            "per_page":            max_results,
            "page":                1,
        }
        data = _apollo_post("/people/search", body)
        return data.get("people", [])
    except Exception:
        return []


# ─────────────────────────────────────────────
# Contact selection
# ─────────────────────────────────────────────
def pick_best_contact(people: list[dict]) -> Optional[dict]:
    """
    From a list of Apollo people dicts, pick the best contact for SesameAccess.
    Returns None if no usable contact found.
    """
    candidates = []
    for person in people:
        email = (
            person.get("email") or
            (person.get("contact", {}) or {}).get("email")
        )
        if not email or "@" not in email:
            continue  # Need a real email

        title  = person.get("title", "") or person.get("headline", "")
        sc     = score_title(title)
        candidates.append((sc, person, email))

    if not candidates:
        return None

    # Sort descending by relevance score
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_person, best_email = candidates[0]
    log.info(f"  Best contact: {best_person.get('name', '')} | {best_person.get('title', '')} | score={best_score}")
    return best_person


# ─────────────────────────────────────────────
# Lead enrichment
# ─────────────────────────────────────────────
def enrich_lead(lead: dict) -> dict:
    """
    Attempt to enrich a lead dict with Apollo contact data.
    Returns the lead dict, modified in place if a contact was found.
    If the lead already has a contact_email, return unchanged.
    """
    # Already have a contact — nothing to do
    if lead.get("contact_email"):
        return lead

    buyer_org = lead.get("buyer_org", "").strip()
    if not buyer_org:
        return lead

    log.info(f"  Apollo enrichment for: {buyer_org}")
    people  = search_people_at_org(buyer_org)
    contact = pick_best_contact(people)

    if not contact:
        log.info(f"  No Apollo contact found for '{buyer_org}'")
        return lead

    # Extract details from Apollo response
    email    = (contact.get("email") or
                (contact.get("contact", {}) or {}).get("email", ""))
    name     = contact.get("name", "")
    title    = contact.get("title", "") or contact.get("headline", "")
    phone    = contact.get("phone_numbers", [{}])[0].get("raw_number", "") \
               if contact.get("phone_numbers") else ""
    linkedin = contact.get("linkedin_url", "")
    city     = (contact.get("city", "") or
                (contact.get("contact", {}) or {}).get("city", ""))

    lead["contact_name"]    = name
    lead["contact_email"]   = email
    lead["contact_phone"]   = phone
    lead["contact_title"]   = title
    lead["contact_linkedin"] = linkedin
    lead["found_via"]        = "apollo"

    log.info(f"  ✓ Enriched: {name} <{email}> ({title})")
    time.sleep(0.5)  # Respect Apollo rate limits
    return lead


# ─────────────────────────────────────────────
# Batch enrichment
# ─────────────────────────────────────────────
def enrich_leads(leads: list[dict]) -> list[dict]:
    """
    Run Apollo enrichment on all leads missing a contact email.
    Returns the enriched list.
    """
    if not APOLLO_API_KEY:
        log.info("APOLLO_API_KEY not set — skipping bulk enrichment")
        return leads

    needs_enrichment = [l for l in leads if not l.get("contact_email")]
    already_have     = len(leads) - len(needs_enrichment)

    log.info(f"Apollo enrichment: {needs_enrichment and len(needs_enrichment)} leads need contact, "
             f"{already_have} already have email")

    # Deduplicate by buyer_org to avoid hammering Apollo with the same org
    seen_orgs: dict[str, Optional[dict]] = {}

    for lead in leads:
        if lead.get("contact_email"):
            continue

        org = lead.get("buyer_org", "").strip()
        if not org:
            continue

        # Reuse result if we already looked up this org this run
        if org in seen_orgs:
            cached = seen_orgs[org]
            if cached:
                lead["contact_name"]    = cached.get("name", "")
                lead["contact_email"]   = (cached.get("email") or
                                           (cached.get("contact", {}) or {}).get("email", ""))
                lead["contact_title"]   = cached.get("title", "")
                lead["found_via"]       = "apollo"
            continue

        # Fresh Apollo lookup
        people  = search_people_at_org(org)
        best    = pick_best_contact(people)
        seen_orgs[org] = best

        if best:
            lead = enrich_lead(lead)  # Modifies in place

    enriched_count = sum(1 for l in leads if l.get("contact_email") and l.get("found_via") == "apollo")
    log.info(f"Apollo enrichment complete: {enriched_count} new contacts found")
    return leads


# ─────────────────────────────────────────────
# Standalone test
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO)
    org = input("Organisation name to test Apollo lookup: ").strip()
    people = search_people_at_org(org, max_results=10)
    print(f"\nFound {len(people)} people:")
    for p in people:
        email = p.get("email") or (p.get("contact", {}) or {}).get("email", "N/A")
        print(f"  {p.get('name','?'):30s}  {p.get('title','?'):45s}  {email}")
    best = pick_best_contact(people)
    if best:
        print(f"\nBest match: {best.get('name')} ({best.get('title')}) — score {score_title(best.get('title',''))}")
