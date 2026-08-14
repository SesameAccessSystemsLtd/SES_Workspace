"""
config.py — SesameAccess × Contracts Finder Pipeline
=====================================================
All tunable parameters live here. Edit this file to adjust keywords,
cooldowns, HubSpot settings, and API credentials.
"""

import os
from pathlib import Path

# Auto-load .env from the OpenClaw root (three levels up from this file).
# This makes the script runnable without manually sourcing the .env first.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parents[5] / ".env"
    load_dotenv(_env_path, override=False)  # override=False: real env vars take precedence
except ImportError:
    pass  # python-dotenv not installed — fall back to whatever is already in os.environ

# ─────────────────────────────────────────────
# CONTRACTS FINDER API  (OCDS endpoint)
# The old REST/2 JSON API returned 404 as of 2026.
# The live endpoint is the OCDS release-package search.
# ─────────────────────────────────────────────
CF_SEARCH_URL    = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
CF_NOTICE_BASE   = "https://www.contractsfinder.service.gov.uk/Notice"
CF_PAGE_SIZE     = 100   # max per cursor page (API cap = 100)
CF_MAX_PAGES     = 10    # safety cap — 10 × 100 = 1,000 results per keyword search
CF_REQUEST_DELAY = 1.5   # seconds between API calls (be polite)

# ─────────────────────────────────────────────
# KEYWORD FILTERING
# Must match ≥1 INCLUDE and 0 EXCLUDE to pass
# Matching is case-insensitive against title + description combined
# ─────────────────────────────────────────────
INCLUDE_KEYWORDS = [
    "disability lift",
    "disabled lift",
    "disabled access lift",
    "accessibility lift",
    "accessible lift",
    "platform lift",
    "step lift",
    "porch lift",
    "through-floor lift",
    "through floor lift",
    "vertical platform lift",
    "stairlift",
    "wheelchair lift",
    "mobility lift",
    "DFG",
    "disabled facilities grant",
    "disabled facilities",
    "step-free access",
    "step free access",
    "accessibility improvement",
    "accessibility works",
    "access improvement",
    "level access",
    "ramped access",
    "homelift",
    "home lift",
    "access lift",
]

# Contracts matching ANY of these are excluded even if an INCLUDE keyword also matches.
# This addresses the exact problem the user found: most "lift" results are passenger lift
# maintenance contracts which SesameAccess does NOT serve.
EXCLUDE_KEYWORDS = [
    "passenger lift",
    "passenger elevator",
    "goods lift",
    "goods elevator",
    "car lift",
    "vehicle lift",
    "scissor lift",
    "escalator",
    "lift shaft",
    "shaft lift",
    "traction lift",
    "hydraulic passenger",
    "mrl lift",          # Machine-Room-Less = passenger elevator
    "dumbwaiter",
    "firefighting lift",
    "evacuation lift",
]

# Primary search terms submitted to the Contracts Finder API.
# Each produces a separate paginated search to maximise recall.
SEARCH_TERMS = [
    "disability lift",
    "platform lift accessibility",
    "disabled facilities grant",
    "DFG disabled access",
    "step free access works",
    "wheelchair access lift",
    "accessibility platform lift",
]

# Only process contracts from these buyer types (substring match on buyer name)
COUNCIL_SIGNALS = [
    "council",
    "borough",
    "district",
    "metropolitan",
    "county",
    "unitary",
    "london borough",
    "city of",
    "nhs",
    "trust",
    "housing association",
    "registered provider",
    "almshouse",
    "homes ",  # e.g. "Homes England"
]

# ─────────────────────────────────────────────
# DEDUPLICATION
# ─────────────────────────────────────────────
OUTREACH_COOLDOWN_DAYS = 90   # Min days between emails to the same contact
SEEN_CONTRACTS_FILE    = "seen_contracts.json"   # Processed contract reference IDs
SEEN_CONTACTS_FILE     = "seen_contacts_log.json" # {email: last_outreach_ISO_date}

# ─────────────────────────────────────────────
# HUBSPOT
# ─────────────────────────────────────────────
# Primary: Private App Token (PAT) — broadest API scope
# Fallback: CRM Updater key (narrower scope, contacts/companies only)
# Both are in the parent .env file in the OpenClaw agents root.
# CRM_UPDATER key is tried first — the PAT key in .env is currently invalid (wrong format).
# To use a Private App Token, set HUBSPOT_PAT_API_KEY to a key beginning with "pat-na1-" or "pat-eu1-".
HUBSPOT_API_KEY     = (
    os.environ.get("HUBSPOT_API_KEY_CRM_UPDATER") or
    os.environ.get("HUBSPOT_PAT_API_KEY") or
    os.environ.get("HUBSPOT_API_KEY", "")
)
HUBSPOT_PORTAL_ID   = os.environ.get("HUBSPOT_PORTAL_ID", "")

# ── Council Outreach property (already created in HubSpot) ────────────────
# Internal name: council_outreach
# This property drives the entire outreach workflow.
COUNCIL_OUTREACH_PROP     = "council_outreach"
COUNCIL_OUTREACH_TO_EMAIL = "TO_EMAIL"       # Contact is queued — external script will send
COUNCIL_OUTREACH_EMAILED  = "EMAILED"        # Email has been sent
COUNCIL_OUTREACH_NOT_INT  = "NOT_Interested" # Remove from all follow-up (HubSpot workflow handles this)

# ── Static lists (already exist or will be created) ───────────────────────
# "Hubspot inport Council test 1.05.25" — the engaged/replied list (already created)
HUBSPOT_ENGAGED_LIST    = "Hubspot inport Council test 1.05.25"
HUBSPOT_PROSPECTS_LIST  = "Contracts Finder — Prospects"

# ── HubSpot workflows (already created, currently DISABLED — you must enable them) ──
# "Council outreach - qualified responders"          → reply detected → add to engaged list
# "Council outreach - remove not interested from follow-up" → NOT_Interested → remove from lists

# Custom contact properties (created on first run if missing)
HUBSPOT_CUSTOM_PROPS = {
    "cf_contract_ref":    "Contracts Finder — Contract Ref",
    "cf_buyer_org":       "Contracts Finder — Buyer Organisation",
    "cf_contract_title":  "Contracts Finder — Contract Title",
    "cf_contract_value":  "Contracts Finder — Estimated Contract Value",
    "cf_deadline":        "Contracts Finder — Tender Deadline",
    "cf_published":       "Contracts Finder — Published Date",
    "cf_last_outreach":   "Contracts Finder — Last Outreach Date",
    "cf_outreach_count":  "Contracts Finder — Outreach Count",
    "cf_source":          "Contracts Finder — Source URL",
    "cf_enriched_by":     "Contracts Finder — Contact Found Via",  # "notice" | "apollo"
    # Note: council_outreach is already created in HubSpot — not recreated here
}

# ─────────────────────────────────────────────
# OUTBOUND EMAIL — Elastic Email HTTP API
# ─────────────────────────────────────────────
# SesameAccess does not have HubSpot Marketing Hub, so emails are sent
# externally via Elastic Email. The pipeline sets council_outreach = TO_EMAIL,
# then email_sender.py reads that queue, sends via Elastic Email HTTP API,
# and sets council_outreach = EMAILED in HubSpot after each successful send.
#
# API key env var: EMAIL_SERVICE_Elasticemail_API_KEY
# Elastic Email dashboard: https://app.elasticemail.com
ELASTIC_EMAIL_API_KEY   = os.environ.get("EMAIL_SERVICE_Elasticemail_API_KEY", "")
ELASTIC_EMAIL_API_URL   = "https://api.elasticemail.com/v4/emails/transactional"
ELASTIC_EMAIL_FROM      = os.environ.get("ELASTIC_EMAIL_FROM", "alisonl@sesameaccess.com")
ELASTIC_EMAIL_FROM_NAME = os.environ.get("ELASTIC_EMAIL_FROM_NAME", "Alison | SesameAccess")
ELASTIC_EMAIL_SENDER_FIRSTNAME = os.environ.get("ELASTIC_EMAIL_SENDER_FIRSTNAME", "Alison")

# Reply-to address — alisonl@sesameaccess.com is monitored by HubSpot
# so replies will be tracked as engagement automatically
ELASTIC_EMAIL_REPLY_TO = os.environ.get("ELASTIC_EMAIL_REPLY_TO", ELASTIC_EMAIL_FROM)

# Bounce domain configured at bounce.sesameaccess.com
# Elastic Email handles bounce routing — no additional config needed here

# Email template subject and body are in email_sender.py
# Cooldown: don't re-send to same contact within this many days
EMAIL_COOLDOWN_DAYS = OUTREACH_COOLDOWN_DAYS  # reuse same value

# ─────────────────────────────────────────────
# APOLLO.IO (fallback contact enrichment)
# ─────────────────────────────────────────────
APOLLO_API_KEY = os.environ.get("APOLLO_API_KEY", "")
APOLLO_API_URL = "https://api.apollo.io/v1"

# Job title keywords used to find the right person at a council
COUNCIL_CONTACT_ROLES = [
    "disabled facilities grant",
    "DFG officer",
    "housing adaptations",
    "occupational therapist",
    "accessibility officer",
    "estates manager",
    "capital projects manager",
    "procurement officer",
    "commissioning officer",
    "housing improvement",
    "home improvement agency",
]

APOLLO_MAX_RESULTS_PER_ORG = 5  # Fetch top 5 contacts per org, pick best match

# ─────────────────────────────────────────────
# OUTPUT & LOGGING
# ─────────────────────────────────────────────
OUTPUT_CSV   = "contracts_finder_leads.csv"
LOG_DIR      = "logs"
LOCK_FILE    = ".pipeline_running.lock"

# ─────────────────────────────────────────────
# SESAMEACCESS CONTEXT (used in HubSpot notes)
# ─────────────────────────────────────────────
SESAME_COMPANY_NAME   = "SesameAccess"
SESAME_WEBSITE        = "https://www.sesameaccess.com"
SESAME_PRODUCTS       = (
    "disability access platform lifts, threshold lifts, step-free access solutions, "
    "porch lifts, through-floor lifts, and bespoke accessibility equipment for public "
    "buildings and residential DFG works — NOT passenger shaft lifts."
)
