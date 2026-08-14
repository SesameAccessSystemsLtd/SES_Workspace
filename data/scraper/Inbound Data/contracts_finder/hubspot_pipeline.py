"""
hubspot_pipeline.py — SesameAccess HubSpot CRM Integration
===========================================================
Handles all HubSpot operations for the Contracts Finder pipeline.

WORKFLOW (no Marketing Hub required):
  1. This script pushes contacts into HubSpot with council_outreach = TO_EMAIL
  2. email_sender.py reads that queue and sends the actual email externally (SMTP)
  3. email_sender.py updates council_outreach = EMAILED after each successful send
  4. Two HubSpot workflows (created, currently disabled — enable them):
       - "Council outreach - qualified responders"
           → trigger: reply / engagement → adds contact to engaged static list
       - "Council outreach - remove not interested from follow-up"
           → trigger: council_outreach = NOT_Interested → removes from lists

Deduplication strategy:
  1. Check local seen_contacts_log.json (fast local cache)
  2. Check HubSpot for existing contact — if council_outreach is already EMAILED
     or NOT_Interested, skip re-queuing
  3. 90-day cooldown window (configurable)
"""

import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

from config import (
    HUBSPOT_API_KEY, HUBSPOT_PORTAL_ID,
    HUBSPOT_PROSPECTS_LIST,
    HUBSPOT_CUSTOM_PROPS, OUTREACH_COOLDOWN_DAYS,
    COUNCIL_OUTREACH_PROP, COUNCIL_OUTREACH_TO_EMAIL,
    COUNCIL_OUTREACH_EMAILED, COUNCIL_OUTREACH_NOT_INT,
    SEEN_CONTACTS_FILE, SESAME_COMPANY_NAME, SESAME_WEBSITE, SESAME_PRODUCTS,
)

log = logging.getLogger("cf_hubspot")

# ─────────────────────────────────────────────
# HubSpot API session
# ─────────────────────────────────────────────
HS_BASE     = "https://api.hubapi.com"
HS_HEADERS  = lambda: {
    "Authorization": f"Bearer {HUBSPOT_API_KEY}",
    "Content-Type":  "application/json",
}


def _get(path: str, params: dict = None) -> dict:
    resp = requests.get(f"{HS_BASE}{path}", headers=HS_HEADERS(), params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _post(path: str, body: dict) -> dict:
    resp = requests.post(f"{HS_BASE}{path}", headers=HS_HEADERS(), json=body, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _patch(path: str, body: dict) -> dict:
    resp = requests.patch(f"{HS_BASE}{path}", headers=HS_HEADERS(), json=body, timeout=20)
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────────────────────────
# Local contact outreach log
# ─────────────────────────────────────────────
def load_seen_contacts() -> dict:
    """Returns {email: iso_date_string} of last outreach dates."""
    p = Path(SEEN_CONTACTS_FILE)
    if p.exists():
        return json.loads(p.read_text()).get("contacts", {})
    return {}


def save_seen_contacts(log_dict: dict) -> None:
    p = Path(SEEN_CONTACTS_FILE)
    data = json.loads(p.read_text()) if p.exists() else {}
    data["contacts"] = log_dict
    data["last_updated"] = datetime.utcnow().isoformat()
    p.write_text(json.dumps(data, indent=2))


def is_in_cooldown(email: str, seen: dict) -> bool:
    """Return True if this email was contacted within the cooldown period."""
    if not email or email not in seen:
        return False
    last = datetime.fromisoformat(seen[email]).date()
    return (date.today() - last).days < OUTREACH_COOLDOWN_DAYS


# ─────────────────────────────────────────────
# Custom property setup (run once)
# ─────────────────────────────────────────────
def ensure_custom_properties() -> None:
    """
    Create any missing custom contact properties in HubSpot.
    Safe to call on every run — skips properties that already exist.
    """
    if not HUBSPOT_API_KEY:
        log.warning("HUBSPOT_API_KEY not set — skipping property setup")
        return

    log.info("Checking HubSpot custom contact properties…")
    try:
        existing = _get("/crm/v3/properties/contacts")
        existing_names = {p["name"] for p in existing.get("results", [])}
    except Exception as e:
        log.error(f"Could not fetch existing properties: {e}")
        return

    prop_type_map = {
        "cf_contract_ref":   ("string",  "textarea"),
        "cf_buyer_org":      ("string",  "text"),
        "cf_contract_title": ("string",  "text"),
        "cf_contract_value": ("number",  "number"),
        "cf_deadline":       ("string",  "text"),
        "cf_published":      ("string",  "text"),
        "cf_last_outreach":  ("string",  "text"),
        "cf_outreach_count": ("number",  "number"),
        "cf_source":         ("string",  "text"),
        "cf_enriched_by":    ("string",  "text"),
        "cf_status":         ("enumeration", "select"),
    }

    for prop_name, label in HUBSPOT_CUSTOM_PROPS.items():
        if prop_name in existing_names:
            continue  # Already exists
        field_type, field_sub = prop_type_map.get(prop_name, ("string", "text"))
        body = {
            "name":        prop_name,
            "label":       label,
            "type":        field_type,
            "fieldType":   field_sub,
            "groupName":   "contactinformation",
        }
        if field_sub == "select":
            body["options"] = [
                {"label": "Prospect",   "value": "prospect",   "displayOrder": 0},
                {"label": "Interested", "value": "interested", "displayOrder": 1},
                {"label": "Excluded",   "value": "excluded",   "displayOrder": 2},
                {"label": "Customer",   "value": "customer",   "displayOrder": 3},
            ]
        try:
            _post("/crm/v3/properties/contacts", body)
            log.info(f"  Created property: {prop_name}")
        except Exception as e:
            log.warning(f"  Could not create {prop_name}: {e}")


# ─────────────────────────────────────────────
# Company (council) management
# ─────────────────────────────────────────────
def find_or_create_company(buyer_org: str, buyer_address: str = "") -> Optional[str]:
    """
    Search HubSpot for an existing company matching buyer_org.
    If not found, create it. Returns HubSpot company ID.
    """
    if not buyer_org:
        return None
    try:
        search_body = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "name",
                    "operator":     "EQ",
                    "value":        buyer_org,
                }]
            }],
            "properties": ["name", "hs_object_id"],
            "limit": 1,
        }
        result = _post("/crm/v3/objects/companies/search", search_body)
        hits = result.get("results", [])
        if hits:
            cid = hits[0]["id"]
            log.info(f"  Company found: {buyer_org} (id={cid})")
            return cid

        # Create new company
        new_co = _post("/crm/v3/objects/companies", {
            "properties": {
                "name":    buyer_org,
                "address": buyer_address,
                "type":    "PROSPECT",
                "description": f"Local council / public sector buyer found via Contracts Finder",
            }
        })
        cid = new_co["id"]
        log.info(f"  Company created: {buyer_org} (id={cid})")
        return cid

    except Exception as e:
        log.error(f"  Company lookup/create error for '{buyer_org}': {e}")
        return None


# ─────────────────────────────────────────────
# Contact management
# ─────────────────────────────────────────────
def find_contact_by_email(email: str) -> Optional[dict]:
    """Return HubSpot contact dict if email already exists, else None."""
    try:
        result = _get(
            f"/crm/v3/objects/contacts/{email}",
            params={
                "idProperty": "email",
                "properties": f"email,{COUNCIL_OUTREACH_PROP},cf_last_outreach,cf_outreach_count",
            }
        )
        return result
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return None
        raise


def create_or_update_contact(lead: dict, company_id: Optional[str] = None) -> Optional[str]:
    """
    Create a new HubSpot contact from a lead dict, or update if already exists.

    Sets council_outreach = TO_EMAIL to queue the contact for external email send.
    If the contact already exists and is already EMAILED or NOT_Interested, the
    council_outreach property is NOT overwritten — protecting against re-queuing.

    Contact fields pushed: name, email, company, phone, job title, plus all
    Contracts Finder custom properties and a deal name for context.

    Returns HubSpot contact ID, or None on failure.
    """
    email = lead.get("contact_email", "")
    if not email:
        log.warning(f"  No email for lead from {lead.get('buyer_org')} — skipping contact creation")
        return None

    # Split name into first / last
    full_name  = (lead.get("contact_name") or "").strip()
    name_parts = full_name.split(" ", 1)
    firstname  = name_parts[0] if name_parts else ""
    lastname   = name_parts[1] if len(name_parts) > 1 else ""

    # Deal / project name: use contract title as the deal context
    deal_name = lead.get("contract_title", "")[:200]

    props = {
        # ── Core contact fields ──────────────────────────────────────
        "email":             email,
        "firstname":         firstname,
        "lastname":          lastname,
        "phone":             lead.get("contact_phone", "") or "",
        "jobtitle":          lead.get("contact_title", "") or "",  # Apollo-enriched contacts
        "company":           lead.get("buyer_org", ""),
        "hs_lead_status":    "NEW",

        # ── Council outreach workflow trigger ────────────────────────
        # This is what drives the entire send → track → respond pipeline.
        # email_sender.py reads TO_EMAIL → sends → sets EMAILED
        COUNCIL_OUTREACH_PROP: COUNCIL_OUTREACH_TO_EMAIL,

        # ── Contracts Finder custom properties ───────────────────────
        "cf_contract_ref":   lead.get("contract_ref", ""),
        "cf_buyer_org":      lead.get("buyer_org", ""),
        "cf_contract_title": deal_name,
        "cf_published":      lead.get("published_date", ""),
        "cf_deadline":       lead.get("deadline", ""),
        "cf_source":         lead.get("notice_url", ""),
        "cf_enriched_by":    lead.get("found_via", "notice"),
    }
    if lead.get("contract_value"):
        props["cf_contract_value"] = str(lead["contract_value"])

    # Internal HubSpot note — full context for the contact record
    note_text = (
        f"Source: Contracts Finder ({lead.get('scrape_date', date.today().isoformat())})\n"
        f"Contract: {lead.get('contract_title', '')}\n"
        f"Ref: {lead.get('contract_ref', '')}\n"
        f"Value: £{lead.get('contract_value', 'unknown')}\n"
        f"Deadline: {lead.get('deadline', 'unknown')}\n"
        f"Matched keyword: {lead.get('matched_keyword', '')}\n"
        f"Contact found via: {lead.get('found_via', 'notice')}\n"
        f"Notice URL: {lead.get('notice_url', '')}\n\n"
        f"SesameAccess products relevant: {SESAME_PRODUCTS}"
    )

    try:
        existing = find_contact_by_email(email)

        if existing:
            contact_id = existing["id"]
            existing_props = existing.get("properties", {})
            current_status = existing_props.get(COUNCIL_OUTREACH_PROP, "")

            # Protect contacts already in terminal states
            if current_status in (COUNCIL_OUTREACH_NOT_INT,):
                log.info(f"  SKIP update: {email} is already NOT_Interested")
                return contact_id

            # If already EMAILED, update metadata but don't reset to TO_EMAIL
            safe_update = {k: v for k, v in props.items()
                           if k not in (COUNCIL_OUTREACH_PROP, "cf_last_outreach", "cf_outreach_count")}
            if current_status not in (COUNCIL_OUTREACH_EMAILED,):
                # Not yet sent — safe to re-queue
                safe_update[COUNCIL_OUTREACH_PROP] = COUNCIL_OUTREACH_TO_EMAIL

            _patch(f"/crm/v3/objects/contacts/{contact_id}", {"properties": safe_update})
            log.info(f"  Contact updated: {email} (id={contact_id}, status={current_status})")

        else:
            result     = _post("/crm/v3/objects/contacts", {"properties": props})
            contact_id = result["id"]
            log.info(f"  Contact created: {email} queued as TO_EMAIL (id={contact_id})")

            # Attach context note to the new contact record
            try:
                _post("/crm/v3/objects/notes", {
                    "properties": {
                        "hs_note_body":      note_text,
                        "hs_timestamp":      str(int(datetime.utcnow().timestamp() * 1000)),
                        "hs_attachment_ids": "",
                    },
                    "associations": [{
                        "to":    {"id": contact_id},
                        "types": [{"associationCategory": "HUBSPOT_DEFINED",
                                   "associationTypeId": 202}],
                    }]
                })
            except Exception as ne:
                log.debug(f"  Note attachment failed (non-critical): {ne}")

            # Create a Deal for the contract so it's visible in pipeline view
            if deal_name:
                try:
                    deal_result = _post("/crm/v3/objects/deals", {
                        "properties": {
                            "dealname":   f"{lead.get('buyer_org', '')} — {deal_name[:100]}",
                            "dealstage":  "appointmentscheduled",
                            "pipeline":   "default",
                            "leadsource": "Contracts Finder",
                            "cf_contract_ref": lead.get("contract_ref", ""),
                        }
                    })
                    deal_id = deal_result["id"]
                    # Associate deal with contact
                    _post(f"/crm/v3/objects/deals/{deal_id}/associations/contacts/{contact_id}/deal_to_contact", {})
                    log.info(f"  Deal created: {deal_name[:60]} (id={deal_id})")
                except Exception as de:
                    log.debug(f"  Deal creation failed (non-critical): {de}")

        # Associate contact with company
        if company_id:
            try:
                _post(f"/crm/v3/objects/contacts/{contact_id}/associations/companies/{company_id}/contact_to_company", {})
            except Exception:
                pass  # Association may already exist

        return contact_id

    except requests.HTTPError as e:
        body = ""
        try:
            body = e.response.json()
        except Exception:
            body = e.response.text[:500]
        log.error(f"  Contact create/update error for {email}: HTTP {e.response.status_code} — {body}")
        return None
    except Exception as e:
        log.error(f"  Contact create/update error for {email}: {e}")
        return None


# ─────────────────────────────────────────────
# List management
# ─────────────────────────────────────────────
_list_id_cache: dict[str, str] = {}

def get_or_create_list(list_name: str) -> Optional[str]:
    """Return HubSpot static list ID, creating if needed."""
    if list_name in _list_id_cache:
        return _list_id_cache[list_name]
    try:
        # Search existing lists
        result = _get("/contacts/v1/lists", params={"count": 250})
        for lst in result.get("lists", []):
            if lst.get("name") == list_name:
                lid = str(lst["listId"])
                _list_id_cache[list_name] = lid
                return lid

        # Create new static list
        new_list = _post("/contacts/v1/lists", {
            "name":       list_name,
            "dynamic":    False,
            "portalId":   HUBSPOT_PORTAL_ID,
        })
        lid = str(new_list["listId"])
        _list_id_cache[list_name] = lid
        log.info(f"  Created HubSpot list: '{list_name}' (id={lid})")
        return lid

    except Exception as e:
        log.error(f"  List lookup/create error for '{list_name}': {e}")
        return None


def add_contact_to_list(contact_id: str, list_name: str) -> bool:
    """Add a HubSpot contact to a named static list."""
    list_id = get_or_create_list(list_name)
    if not list_id:
        return False
    try:
        _post(f"/contacts/v1/lists/{list_id}/add", {"vids": [contact_id]})
        return True
    except Exception as e:
        log.warning(f"  Could not add contact {contact_id} to list '{list_name}': {e}")
        return False


# ─────────────────────────────────────────────
# Email send queue (replaces sequence enrolment)
# ─────────────────────────────────────────────
def get_send_queue(limit: int = 100) -> list[dict]:
    """
    Return HubSpot contacts where council_outreach = TO_EMAIL.
    These are queued for external email send via email_sender.py.
    """
    try:
        search_body = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": COUNCIL_OUTREACH_PROP,
                    "operator":     "EQ",
                    "value":        COUNCIL_OUTREACH_TO_EMAIL,
                }]
            }],
            "properties": [
                "email", "firstname", "lastname", "company", "jobtitle",
                COUNCIL_OUTREACH_PROP, "cf_contract_title", "cf_buyer_org",
                "cf_source", "cf_last_outreach", "cf_outreach_count",
            ],
            "limit": limit,
        }
        result = _post("/crm/v3/objects/contacts/search", search_body)
        contacts = result.get("results", [])
        log.info(f"  Send queue: {len(contacts)} contact(s) with TO_EMAIL status")
        return contacts
    except Exception as e:
        log.error(f"  Could not fetch send queue: {e}")
        return []


def mark_as_emailed(contact_id: str, email: str, seen: dict) -> None:
    """
    Update council_outreach = EMAILED and record outreach date.
    Called by email_sender.py after a successful send.
    """
    today = date.today().isoformat()
    seen[email] = today
    try:
        existing = find_contact_by_email(email)
        current_count = 0
        if existing:
            try:
                current_count = int(
                    existing.get("properties", {}).get("cf_outreach_count", 0) or 0
                )
            except (ValueError, TypeError):
                current_count = 0

        _patch(f"/crm/v3/objects/contacts/{contact_id}", {
            "properties": {
                COUNCIL_OUTREACH_PROP: COUNCIL_OUTREACH_EMAILED,
                "cf_last_outreach":    today,
                "cf_outreach_count":   str(current_count + 1),
            }
        })
        log.info(f"  Marked EMAILED: {email}")
    except Exception as e:
        log.warning(f"  Could not mark {email} as EMAILED: {e}")


# ─────────────────────────────────────────────
# Outreach log (local cache — fast dedup check)
# ─────────────────────────────────────────────
def record_queued(email: str, seen: dict) -> None:
    """
    Record that this email has been queued (TO_EMAIL set).
    This prevents the scraper from re-queuing the same contact on the
    next run before the email has actually been sent.
    """
    seen[email] = date.today().isoformat()


# ─────────────────────────────────────────────
# Engagement-based status updates (webhook / polling)
# ─────────────────────────────────────────────
def mark_as_interested(contact_id: str, email: str, reason: str = "") -> None:
    """
    Move a contact to the 'Interested' list and update their status.
    Call this when HubSpot reports an email open, click, or reply.
    """
    try:
        _patch(f"/crm/v3/objects/contacts/{contact_id}", {
            "properties": {
                "cf_status":      "interested",
                "hs_lead_status": "IN_PROGRESS",
            }
        })
        add_contact_to_list(contact_id, HUBSPOT_INTERESTED_LIST)
        log.info(f"  Marked as interested: {email}" + (f" ({reason})" if reason else ""))
    except Exception as e:
        log.error(f"  Could not mark {email} as interested: {e}")


def get_engaged_contacts(days_back: int = 7) -> list[dict]:
    """
    Poll HubSpot for contacts whose cf_last_outreach was within days_back
    and whose engagement properties suggest interest (opened, clicked, replied).
    Returns a list of contact dicts.
    """
    cutoff = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    try:
        search_body = {
            "filterGroups": [{
                "filters": [
                    {"propertyName": "cf_status",        "operator": "EQ",  "value": "prospect"},
                    {"propertyName": "hs_email_open",     "operator": "GT",  "value": "0"},
                ]
            }],
            "properties": ["email", "cf_status", "hs_email_open", "hs_email_click", "cf_outreach_count"],
            "limit": 100,
        }
        result  = _post("/crm/v3/objects/contacts/search", search_body)
        engaged = result.get("results", [])
        log.info(f"  Found {len(engaged)} engaged contacts")
        return engaged
    except Exception as e:
        log.error(f"  Could not poll engaged contacts: {e}")
        return []


# ─────────────────────────────────────────────
# Master push function
# ─────────────────────────────────────────────
def push_leads_to_hubspot(leads: list[dict]) -> dict:
    """
    Main entry point: push a list of scraper lead dicts into HubSpot.
    Handles dedup, company creation, contact creation, list membership,
    and sequence enrolment.

    Returns summary stats dict.
    """
    if not HUBSPOT_API_KEY:
        log.error("HUBSPOT_API_KEY is not set — cannot push to HubSpot")
        return {"error": "No API key"}

    ensure_custom_properties()

    seen       = load_seen_contacts()
    stats      = {"pushed": 0, "skipped_cooldown": 0, "skipped_no_email": 0,
                  "errors": 0, "enrolled_in_sequence": 0}
    processed_ids = []

    for lead in leads:
        email = lead.get("contact_email", "")

        # ── No email → skip (Apollo fallback may fill this in before calling here)
        if not email:
            stats["skipped_no_email"] += 1
            log.info(f"  No email: {lead.get('buyer_org')} / {lead.get('contract_title', '')[:40]}")
            continue

        # ── Cooldown check (local cache) ──────────────────────────────
        if is_in_cooldown(email, seen):
            stats["skipped_cooldown"] += 1
            log.info(f"  COOLDOWN: {email} queued within {OUTREACH_COOLDOWN_DAYS} days — skip")
            continue

        log.info(f"\nProcessing: {email} ({lead.get('buyer_org')})")

        # ── Company ───────────────────────────────────────────────────
        company_id = find_or_create_company(lead.get("buyer_org", ""), lead.get("buyer_address", ""))
        time.sleep(0.3)

        # ── Contact (sets council_outreach = TO_EMAIL) ────────────────
        contact_id = create_or_update_contact(lead, company_id)
        if not contact_id:
            stats["errors"] += 1
            continue
        time.sleep(0.3)

        # ── List membership ───────────────────────────────────────────
        add_contact_to_list(contact_id, HUBSPOT_PROSPECTS_LIST)
        time.sleep(0.2)

        # NOTE: do NOT record in cooldown cache here — cooldown is only set
        # by email_sender.py after the email is actually sent via Elastic Email.
        # Recording at push time was blocking the sender from sending them.

        stats["pushed"] += 1
        processed_ids.append(contact_id)
        lead["hubspot_pushed"]     = True
        lead["hubspot_contact_id"] = contact_id
        time.sleep(0.5)

    # Persist updated contact log
    save_seen_contacts(seen)

    log.info("\n" + "=" * 50)
    log.info("HUBSPOT PUSH SUMMARY")
    for k, v in stats.items():
        log.info(f"  {k}: {v}")
    log.info("=" * 50)

    return stats


# ─────────────────────────────────────────────
# Engagement sweep (call on a schedule)
# ─────────────────────────────────────────────
def run_engagement_sweep(days_back: int = 7) -> None:
    """
    Check for contacts who have engaged with emails and promote them to
    the 'Interested' list in HubSpot. Run daily or after each campaign.
    """
    if not HUBSPOT_API_KEY:
        log.warning("HUBSPOT_API_KEY not set — cannot run engagement sweep")
        return

    log.info("Running engagement sweep…")
    engaged = get_engaged_contacts(days_back=days_back)
    for contact in engaged:
        cid   = contact["id"]
        email = contact.get("properties", {}).get("email", "")
        opens = contact.get("properties", {}).get("hs_email_open", 0)
        mark_as_interested(cid, email, reason=f"{opens} email open(s)")
