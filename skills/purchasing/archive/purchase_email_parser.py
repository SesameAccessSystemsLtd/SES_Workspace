"""
purchase_email_parser.py
Phase 1 — Email → Asana Order Status Proposal

Reads purchasing@sesameaccess.com inbox via Microsoft Graph.
Matches inbound supplier emails to open Asana order tasks.
Proposes field updates — does NOT write to Asana automatically.
Human reviews and approves before SAP executes any writes.

Usage:
    python purchase_email_parser.py [--days 3] [--write-proposals]

Output:
    Prints proposed updates to stdout.
    Optionally saves proposals to proposals.json for SAP to action.
"""

import base64
import json
import os
import re
import sys
import requests
import argparse
from datetime import datetime, date, timedelta, timezone
from dotenv import load_dotenv
from notify_telegram import send_alert, send_summary

# ── Paths ────────────────────────────────────────────────────────────────────
DOTENV_PATH   = r"C:\Users\GilesHugo\.openclaw\.env"
CONFIG_PATH   = r"C:\Users\GilesHugo\.openclaw\config.json"
SUPPLIERS_PATH = r"C:\Users\GilesHugo\.openclaw\workspace\data\suppliers.json"
PROPOSALS_PATH    = r"C:\Users\GilesHugo\.openclaw\workspace\data\email_proposals.json"
ALERT_STATE_PATH  = r"C:\Users\GilesHugo\.openclaw\workspace\data\parser_alert_state.json"

# ── Excluded sender domains (internal forwarding / non-supplier services) ────
EXCLUDED_DOMAINS = {
    "gmail.com", "hotmail.com", "outlook.com", "live.com",
    "yahoo.com", "yahoo.co.uk", "icloud.com",
    "ringcentral.com", "notify-uk.ringcentral.com",   # phone system call logs
    "sesameaccess.com",                               # internal / forwarded emails
}
# Sender local-parts that are always bounces/NDR — exclude regardless of domain
EXCLUDED_SENDER_LOCALPARTS = {"postmaster", "mailer-daemon"}

# ── Asana GIDs ───────────────────────────────────────────────────────────────
WORKSPACE_GID      = "357939803556123"
GOODS_IN_GID       = "358790955858777"
ORDER_NUMBERS_GID  = "854650811917168"

# Custom field GIDs on order tasks
ORDER_STATUS_GID   = "1143955334207040"
VENDOR_GID_CF      = "854127216887083"
SQR_GID_CF         = "1211774738554014"   # SQR formula field, e.g. "SQR-44393"
PRICE_GID          = "854330340727040"
ORDER_DATE_GID     = "1208706270294053"
DUE_DATE_GID       = "due_on"   # native field

# ORDER Status enum GIDs
STATUS = {
    "TO_BE_ASSESSED":         "1208159308935089",
    "QUOTE_AUTO":             "1144696382567993",
    "QUOTE_MANUAL":           "1208935736248203",
    "ON_ORDER_AWAITING":      "1143955334207043",
    "ON_ORDER_CONFIRMED":     "1203944084697376",
    "ARRIVED_QC":             "1171789515832142",
    "ARRIVED_INVOICES":       "1149418840110590",
    "PARTIAL":                "1143996800235967",
    "HOLD":                   "1143955334207044",
    "CANCELLED":              "1208706522991885",
}

# Statuses that mean the order is still active and matchable
OPEN_STATUSES = {
    STATUS["TO_BE_ASSESSED"],
    STATUS["QUOTE_AUTO"],
    STATUS["QUOTE_MANUAL"],
    STATUS["ON_ORDER_AWAITING"],
    STATUS["ON_ORDER_CONFIRMED"],
    STATUS["PARTIAL"],
}

# Tasks on hold are still matched — supplier activity on a held order is an alert
HOLD_STATUSES = {
    STATUS["HOLD"],
}

MATCHABLE_STATUSES = OPEN_STATUSES | HOLD_STATUSES

# Asana user GID for task assignment (Giles Hugo)
GILES_USER_GID = "1142108324926610"

# UK bank holidays (extend annually as needed)
_UK_BANK_HOLIDAYS = {
    date(2026, 5, 4),   date(2026, 5, 25),  date(2026, 8, 31),
    date(2026, 12, 25), date(2026, 12, 28),
    date(2027, 1, 1),   date(2027, 4, 2),   date(2027, 4, 5),
    date(2027, 5, 3),   date(2027, 5, 26),  date(2027, 8, 30),
}


def load_env():
    load_dotenv(DOTENV_PATH, override=True)
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    return cfg


def add_working_days(start_date, n):
    """Add n working days, skipping weekends and UK bank holidays."""
    d = start_date
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in _UK_BANK_HOLIDAYS:
            added += 1
    return d


def _parse_date_str(s):
    """
    Parse a date string in various formats. Returns a date object or None.
    Tries UK (DD/MM) before US (MM/DD) for ambiguous numeric dates.
    """
    # Strip day names ("Wednesday "), ordinals ("15th" → "15"), time ranges
    s = re.sub(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+between\s+.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+\d{1,2}:\d{2}.*$", "", s)
    s = s.strip()
    for fmt in ("%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%d/%m/%y",
                "%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def is_future_date(date_str):
    """
    Return True if date_str represents a date that is today or in the future.
    Dates more than 1 day in the past are rejected.
    Returns True for unparseable strings (pass through rather than silently drop).
    """
    if not date_str:
        return False
    d = _parse_date_str(date_str)
    if d is None:
        return True  # Can't parse — don't silently discard
    return d >= date.today()


def get_graph_token(cfg):
    """
    Acquire a Graph API token using client_credentials.
    Tries the env var app registration (Purchaisng_secret_ID) first.
    Falls back to config.json clientId if the env var auth returns a 400.
    """
    tenant_id     = os.environ.get("Purchasing_tenant_ID", "3bfd9e7d-219f-48db-bd9e-e63b3b990a1a")
    client_secret = os.environ.get("purchasing_Email_Api_Key") or cfg.get("clientSecret", "")
    token_url     = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    def _request(client_id):
        return requests.post(
            token_url,
            data={
                "client_id":     client_id,
                "client_secret": client_secret,
                "scope":         "https://graph.microsoft.com/.default",
                "grant_type":    "client_credentials",
            },
        )

    # Try env var client ID first (original app registration)
    env_client_id = os.environ.get("Purchaisng_secret_ID")
    if env_client_id:
        r = _request(env_client_id)
        if r.status_code == 200:
            return r.json()["access_token"]
        sys.stderr.write(
            f"[get_graph_token] env var client_id returned {r.status_code} — "
            "falling back to config.json clientId\n"
        )

    # Fall back to config.json clientId
    config_client_id = cfg.get("clientId", "")
    if not config_client_id:
        raise RuntimeError("No valid Graph API client_id in env vars or config.json")
    r = _request(config_client_id)
    r.raise_for_status()
    return r.json()["access_token"]


def derive_search_name(name):
    """
    Strip corporate suffixes and take the first meaningful segment.
    "Exact Lasercut Ltd" → "Exact Lasercut"
    "RS Components - Industrial" → "RS Components"
    Used to match the short-form Vendor field value stored on Asana order tasks.
    """
    # Remove trailing corporate suffixes
    name = re.sub(
        r"\s+(Ltd|Limited|PLC|Inc|Co\.|Corp|LLC|LLP|Group)\s*\.?$",
        "", name, flags=re.IGNORECASE,
    ).strip()
    # Take first segment before ' - '
    name = name.split(" - ")[0].strip()
    # Take first segment before '('
    name = name.split("(")[0].strip()
    return name


def load_suppliers():
    """Load supplier list. Build lookup dicts: domain → supplier, tag → supplier."""
    with open(SUPPLIERS_PATH, encoding="utf-8") as f:
        suppliers = json.load(f)

    domain_map = {}   # domain → supplier record
    tag_map    = {}   # normalised tag name → supplier record

    for s in suppliers:
        # Pre-compute a short search name and store it on the record
        s["search_name"] = derive_search_name(s.get("name", ""))

        # Domain map — handle multiple emails in one field
        raw_email = s.get("email", "")
        for addr in re.split(r"[,;\s]+", raw_email):
            addr = addr.strip().lower()
            if "@" in addr:
                domain = addr.split("@")[-1]
                if domain and domain not in EXCLUDED_DOMAINS:
                    domain_map[domain] = s

        # Tag map
        for tag in s.get("tags", []):
            tag_map[tag.strip().lower()] = s

    return suppliers, domain_map, tag_map


def fetch_recent_emails(graph_token, days=3):
    """Fetch inbound emails from the last N days, excluding self-sent."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {"Authorization": f"Bearer {graph_token}"}
    url = "https://graph.microsoft.com/v1.0/users/purchasing@sesameaccess.com/messages"
    params = {
        "$top": 500,
        "$filter": f"receivedDateTime ge {since}",
        "$select": "id,subject,from,receivedDateTime,body,bodyPreview,hasAttachments,isRead",
        "$orderby": "receivedDateTime desc",
    }
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    emails = r.json().get("value", [])

    # Filter to inbound only (not sent by purchasing themselves)
    inbound = [
        e for e in emails
        if e["from"]["emailAddress"]["address"].lower() != "purchasing@sesameaccess.com"
    ]
    return inbound


def fetch_pdf_from_email(graph_token, message_id):
    """
    Fetch PDF attachments from an email and extract text + references.
    Handles Aalco-style quotes where "Your ref" is blank in the subject
    but the SQR/PO number appears in the PDF body.

    Returns: (sqr_numbers, po_numbers, pdf_raw_text_combined)
    All lists are deduplicated. Empty strings/lists returned on failure.
    """
    try:
        from pdf_extractor import PDFExtractorSkill
    except ImportError:
        sys.stderr.write("[fetch_pdf] pdf_extractor not importable — skipping\n")
        return [], [], ""

    headers = {"Authorization": f"Bearer {graph_token}"}
    url = (
        "https://graph.microsoft.com/v1.0/users/purchasing@sesameaccess.com"
        f"/messages/{message_id}/attachments"
    )
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            return [], [], ""
        attachments = r.json().get("value", [])
    except Exception as exc:
        sys.stderr.write(f"[fetch_pdf] Error fetching attachments: {exc}\n")
        return [], [], ""

    extractor = PDFExtractorSkill()
    all_sqr, all_po, all_raw = [], [], []

    for att in attachments:
        if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
            continue
        content_type = att.get("contentType", "").lower()
        name = att.get("name", "").lower()
        if "pdf" not in content_type and not name.endswith(".pdf"):
            continue
        content_b64 = att.get("contentBytes", "")
        if not content_b64:
            continue
        try:
            pdf_bytes = base64.b64decode(content_b64)
            result    = extractor.extract_from_bytes(pdf_bytes)
            raw_text  = result.get("raw_text", "")
            all_raw.append(raw_text)
            all_sqr.extend(extract_sqr_numbers(raw_text))
            all_po.extend(extract_po_numbers(raw_text))
            # Also pick up structured fields the extractor found
            for ref in [result.get("po_number"), result.get("order_ref")]:
                if ref:
                    all_po.append(ref)
            sys.stderr.write(
                f"[fetch_pdf] Extracted from {att.get('name','?')}: "
                f"SQRs={extract_sqr_numbers(raw_text)} POs={extract_po_numbers(raw_text)}\n"
            )
        except Exception as exc:
            sys.stderr.write(f"[fetch_pdf] Error extracting {att.get('name','?')}: {exc}\n")

    return list(set(all_sqr)), list(set(all_po)), "\n".join(all_raw)


def clean_body(html):
    """Strip HTML, decode entities, collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", html)
    for ent, ch in [
        ("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"),
        ("&amp;", "&"), ("&#39;", "'"), ("&quot;", '"'),
    ]:
        text = text.replace(ent, ch)
    text = re.sub(r"\s{3,}", "\n", text).strip()
    return text


def extract_po_numbers(text):
    """Find Sesame PO numbers in email text (format: NNNNN-NNNN or NNNNN-YYMM)."""
    return re.findall(r"\b2\d{4}-\d{4}\b", text)


def extract_sqr_numbers(text):
    """Find SQR reference numbers in email text (format: SQR-NNNNN)."""
    return re.findall(r"\bSQR-\d{4,6}\b", text, re.IGNORECASE)


# ── Alert deduplication ───────────────────────────────────────────────────────

def _load_alert_state():
    """Load alert state from disk. Returns dict: {task_gid: {alert_type: iso_timestamp}}."""
    try:
        with open(ALERT_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_alert_state(state):
    with open(ALERT_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _should_alert(state, task_gid, alert_type, cooldown_hours=24):
    """Return True if we have NOT alerted for this (task_gid, alert_type) within cooldown_hours."""
    last = state.get(task_gid, {}).get(alert_type)
    if not last:
        return True
    last_dt = datetime.fromisoformat(last)
    return datetime.now(timezone.utc) - last_dt > timedelta(hours=cooldown_hours)


def _record_alert(state, task_gid, alert_type):
    if task_gid not in state:
        state[task_gid] = {}
    state[task_gid][alert_type] = datetime.now(timezone.utc).isoformat()


# ── Asana write helpers ───────────────────────────────────────────────────────

def post_asana_comment(asana_token, task_gid, text):
    """Post a comment (story) to an Asana task. Returns True on success."""
    try:
        r = requests.post(
            f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories",
            headers={"Authorization": f"Bearer {asana_token}"},
            json={"data": {"text": text}},
            timeout=10,
        )
        return r.status_code == 201
    except Exception as e:
        print(f"[post_asana_comment] {e}", file=sys.stderr)
        return False


def assign_asana_task(asana_token, task_gid, user_gid):
    """Assign an Asana task to a user. Returns True on success."""
    try:
        r = requests.put(
            f"https://app.asana.com/api/1.0/tasks/{task_gid}",
            headers={"Authorization": f"Bearer {asana_token}"},
            json={"data": {"assignee": user_gid}},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[assign_asana_task] {e}", file=sys.stderr)
        return False


def extract_vendor_order_refs(text):
    """
    Extract supplier-side order/confirmation references from email text.
    Covers Amazon order numbers and generic order/ref/confirmation patterns.
    Returns list of (label, value) tuples.
    """
    refs = []
    # Amazon: "Order # 026-5623919-2299535" or "order #026-..."
    for m in re.finditer(r"[Oo]rder\s*#\s*([\d\-]{10,25})", text):
        refs.append(("Amazon Order #", m.group(1).strip()))
    # Generic: "Order Ref: ABC123", "Confirmation No: XYZ"
    # Must start with a digit or uppercase letter, min 5 chars, not a common word
    _word_re = re.compile(r"^[A-Z]{3,}$")   # all-uppercase word = likely a real ref
    for m in re.finditer(
        r"\b(?:order\s+ref|our\s+ref|your\s+ref|ref(?:erence)?\s+no|"
        r"confirmation(?:\s+no)?|conf(?:irmation)?\s*no)[:\s#]+([A-Z0-9\-/]{5,20})\b",
        text, re.IGNORECASE,
    ):
        val = m.group(1).strip()
        # Skip Sesame PO numbers and plain English words
        if re.match(r"^\d{5}-\d{4}$", val):
            continue
        if re.match(r"^[A-Za-z]{1,8}$", val) and not _word_re.match(val.upper()):
            continue
        refs.append(("Supplier Ref", val))
    return refs


def extract_delivery_date(text):
    """
    Try to extract a delivery date from email text.
    Priority order:
      1. Relative day phrases ("2-3 days", "3 working days") → computed working-day date
      2. High-confidence explicit date formats
      3. Keyword-anchored patterns (ETA, delivery by, etc.)
    Only returns dates that are today or in the future.
    """
    # 1. Relative day phrases — compute from today using working days
    m = re.search(
        r"\b(\d+)\s*(?:[-\u2013to]+\s*(\d+))?\s+(?:working\s+)?(?:business\s+)?days?\b",
        text, re.IGNORECASE,
    )
    if m:
        n = int(m.group(2) or m.group(1))  # upper end of range
        n = min(n, 90)   # sanity cap
        target = add_working_days(date.today(), n)
        return f"{target.day} {target.strftime('%b %Y')}"

    # 2. High-confidence: explicit date formats
    date_patterns = [
        # "Wednesday 15th April 2026" / "15th April 2026"
        r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*"
        r"(\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\b",
        # "15/04/2026", "3/25/26" etc
        r"\b(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})\b",
    ]
    for pat in date_patterns:
        mo = re.search(pat, text, re.IGNORECASE)
        if mo:
            val = mo.group(1).strip()
            if is_future_date(val):
                return val

    # 3. Keyword-anchored patterns
    keyword_patterns = [
        r"\bETA[:\s]+([^\n,\.]{5,25})",
        r"\bdeliver(?:y|ed)?\s+by[:\s]+([^\n,\.]{5,25})",
        r"\bweek\s+commencing[:\s]+([^\n,\.]{5,25})",
        r"\bdespatch(?:ed)?\s+(?:on\s+)?([^\n,\.]{5,20})",
    ]
    _junk = re.compile(
        r"^(confirmation|confirmed|soon|asap|tbc|tbd|n/?a|arriving|item|your|we|the|"
        r"please|upon|once|when|after|within|below)\b",
        re.IGNORECASE,
    )
    for pat in keyword_patterns:
        mo = re.search(pat, text, re.IGNORECASE)
        if mo:
            val = mo.group(1).strip()
            if not _junk.match(val) and len(val) >= 5 and is_future_date(val):
                return val
    return None


def extract_price(text):
    """Try to extract a price from email text."""
    m = re.search(r"[£$]\s?([\d,]+\.?\d{0,2})", text)
    if m:
        return m.group(1).replace(",", "")
    m = re.search(r"([\d,]+\.?\d{2})\s*(?:ex\s*vat|excl|net|GBP)", text, re.IGNORECASE)
    if m:
        return m.group(1).replace(",", "")
    return None


def classify_email(text, subject):
    """
    Determine what kind of supplier email this is.
    """
    text_lower  = (text + " " + subject).lower()
    subject_low = subject.lower()

    if any(w in subject_low for w in ["undeliverable", "delivery failure", "mail delivery failed", "returned mail", "failed delivery"]):
        return "bounce"

    if any(w in text_lower for w in ["invoice", "inv no", "inv #", "vat invoice", "tax invoice"]):
        return "invoice"
        
    if any(w in text_lower for w in ["dispatch", "despatch", "shipped", "tracking", "collected", "delivery note"]):
        return "dispatch"
        
    # Quote wins over delay notice if the word quotation or quote is prominent
    if any(w in subject_low for w in ["quote", "quotation", "sales quote", "pricing", "estimate"]) or "quotation" in text_lower or "unit price" in text_lower:
        return "quote"
        
    if any(w in text_lower for w in ["delay", "lead time", "back order", "unavailable", "out of stock"]):
        return "delay_notice"
        
    if any(w in text_lower for w in ["thank you", "thanks", "received", "noted", "order confirmed", "acknowledged"]):
        return "acknowledgement"
    
    return "general"


def _parse_task_fields(t):
    """Extract ORDER Status and Vendor field values from a raw Asana task dict."""
    status_gid = None
    status_label = ""
    vendor_field_val = ""
    is_line_item = False
    for cf in t.get("custom_fields", []):
        if cf["gid"] == ORDER_STATUS_GID:
            ev = cf.get("enum_value")
            if ev:
                status_gid   = ev["gid"]
                status_label = ev["name"]
        if cf["gid"] == VENDOR_GID_CF:
            vendor_field_val = (cf.get("text_value") or "").strip()
        if "QTY" in cf.get("name", "").upper():
            if cf.get("number_value") is not None:
                is_line_item = True
    return status_gid, status_label, vendor_field_val, is_line_item


def _asana_search(asana_token, text_query, limit=20):
    """
    Run a workspace task search limited to Goods In + Order Numbers, incomplete tasks.
    Returns raw task list.
    """
    headers = {"Authorization": f"Bearer {asana_token}", "Accept": "application/json"}
    r = requests.get(
        f"https://app.asana.com/api/1.0/workspaces/{WORKSPACE_GID}/tasks/search",
        headers=headers,
        params={
            "text":         text_query,
            "projects.any": f"{GOODS_IN_GID},{ORDER_NUMBERS_GID}",
            "completed":    "false",
            "opt_fields":   "name,custom_fields,due_on,tags.name",
            "limit":        limit,
        },
    )
    if r.status_code != 200:
        return []
    return r.json().get("data", [])


def _filter_open(raw_tasks):
    """
    Filter raw Asana tasks to those with an open ORDER Status. Returns candidate dicts.
    Tasks with no ORDER Status set (null) are treated as matchable — they are in-flight
    orders that haven't had their status field populated yet (e.g. ABCO stock orders).
    """
    candidates = []
    seen_gids = set()
    for t in raw_tasks:
        if t["gid"] in seen_gids:
            continue
        seen_gids.add(t["gid"])
        status_gid, status_label, vendor_field_val, is_line_item = _parse_task_fields(t)
        if is_line_item:
            continue
        # Null status = matchable (task exists in search scope but status not yet set)
        if status_gid is not None and status_gid not in MATCHABLE_STATUSES:
            continue
        candidates.append({
            "gid":          t["gid"],
            "name":         t["name"],
            "status_gid":   status_gid,
            "status_label": status_label or "(no status)",
            "vendor_field": vendor_field_val,
            "due_on":       t.get("due_on"),
            "tags":         [tg["name"] for tg in t.get("tags", [])],
            "is_held":      status_gid in HOLD_STATUSES,
        })
    return candidates


def _asana_search_by_sqr(asana_token, sqr_number):
    """
    Search for tasks by SQR custom field value.
    Strategy A: custom field filter (may not work for formula fields).
    Strategy B: text search for the SQR string as fallback.
    """
    headers = {"Authorization": f"Bearer {asana_token}", "Accept": "application/json"}

    # Strategy A — custom field filter
    r = requests.get(
        f"https://app.asana.com/api/1.0/workspaces/{WORKSPACE_GID}/tasks/search",
        headers=headers,
        params={
            f"custom_fields.{SQR_GID_CF}.value": sqr_number,
            "completed":  "false",
            "opt_fields": "name,custom_fields,due_on,tags.name",
            "limit":      10,
        },
    )
    if r.status_code == 200 and r.json().get("data"):
        return r.json()["data"]

    # Strategy B — text search (catches SQR in task notes/descriptions, works for formula display)
    return _asana_search(asana_token, sqr_number, limit=10)


def get_candidates_for_email(asana_token, supplier, po_numbers, sqr_numbers=None):
    """
    Multi-strategy search for open Asana order tasks that could match this email.

    Strategy order:
      1. SQR number — highest signal; direct field match across all projects
      2. Direct PO number search — reliable when PO is in email
      3. Short search name (e.g. "Exact Lasercut" from "Exact Lasercut Ltd")
      4. First word only (e.g. "Exact") — catches tasks where Vendor field = first word
    All results are deduped and filtered to MATCHABLE_STATUSES.
    """
    search_name  = supplier.get("search_name", "")
    first_word   = search_name.split()[0] if search_name else ""
    sqr_numbers  = sqr_numbers or []

    raw = []

    # Strategy 1 — SQR field match (cross-project, highest signal)
    for sqr in sqr_numbers:
        raw += _asana_search_by_sqr(asana_token, sqr.upper())

    # Strategy 2 — PO numbers
    for po in po_numbers:
        raw += _asana_search(asana_token, po)

    # Strategy 3 — short supplier name
    if search_name and len(search_name) >= 3:
        raw += _asana_search(asana_token, search_name)

    # Strategy 4 — first word only
    if first_word and first_word != search_name and len(first_word) >= 4:
        raw += _asana_search(asana_token, first_word)

    return _filter_open(raw)


def match_task_to_email(candidates, po_numbers, subject, body_text):
    """
    Given a list of candidate Asana tasks and extracted PO numbers from email,
    return the best match and confidence level.
    """
    if not candidates:
        return None, "no_candidates"

    # Exact PO match (highest confidence)
    for po in po_numbers:
        for t in candidates:
            if po in t["name"]:
                return t, "po_match"

    # Subject keyword match against task name
    subj_words = set(re.findall(r"\b\w{4,}\b", subject.lower()))
    best, best_score = None, 0
    for t in candidates:
        task_words = set(re.findall(r"\b\w{4,}\b", t["name"].lower()))
        overlap = len(subj_words & task_words)
        if overlap > best_score:
            best, best_score = t, overlap

    if best_score >= 2:
        return best, "keyword_match"
    if len(candidates) == 1:
        return candidates[0], "only_candidate"

    return None, "ambiguous"


def build_proposal(email, supplier, task, match_reason, email_type,
                   po_numbers, delivery_date, price):
    """Build a structured proposal dict for human review."""
    proposed_updates = {}
    notes_to_append = []

    if email_type == "quote":
        # Quote received — move to manual review status if currently auto-quote
        if task["status_gid"] == STATUS["QUOTE_AUTO"]:
            proposed_updates["ORDER Status"] = {
                "field_gid": ORDER_STATUS_GID,
                "current": task["status_label"],
                "proposed_gid": STATUS["QUOTE_MANUAL"],
                "proposed_label": "Quote - Manual",
                "reason": "Supplier quote received — needs manual review",
            }
        if price:
            proposed_updates["Price Ex VAT"] = {
                "field_gid": PRICE_GID,
                "proposed_value": float(price),
                "reason": f"Price extracted from quote email: £{price}",
            }
        notes_to_append.append(
            f"Quote received {email['receivedDateTime'][:10]} from "
            f"{email['from']['emailAddress']['address']}"
            + (f" — £{price} ex VAT" if price else "")
        )

    elif email_type == "acknowledgement":
        # Supplier has acknowledged and confirmed a delivery date — update status only
        # if they are actually confirming (Awaiting CONFIRMATION → CONFIRMED)
        if "awaiting" in task["status_label"].lower() or task["status_gid"] == STATUS["ON_ORDER_AWAITING"]:
            proposed_updates["ORDER Status"] = {
                "field_gid": ORDER_STATUS_GID,
                "current": task["status_label"],
                "proposed_gid": STATUS["ON_ORDER_CONFIRMED"],
                "proposed_label": "On Order - Delivery Date CONFIRMED",
                "reason": "Supplier confirmed order with delivery date",
            }
        notes_to_append.append(f"Supplier acknowledged {email['receivedDateTime'][:10]}")

    elif email_type == "dispatch":
        # Dispatch = supplier has sent/confirmed shipment. Update to CONFIRMED + date.
        # NEVER mark as ARRIVED — that requires manual booking-in by a user.
        if task["status_gid"] not in {STATUS["ON_ORDER_CONFIRMED"], STATUS["PARTIAL"]}:
            proposed_updates["ORDER Status"] = {
                "field_gid": ORDER_STATUS_GID,
                "current": task["status_label"],
                "proposed_gid": STATUS["ON_ORDER_CONFIRMED"],
                "proposed_label": "On Order - Delivery Date CONFIRMED",
                "reason": "Dispatch notification received — delivery confirmed",
            }
        notes_to_append.append(f"Dispatch/delivery confirmation {email['receivedDateTime'][:10]}")

    elif email_type == "delay_notice":
        proposed_updates["ORDER Status"] = {
            "field_gid": ORDER_STATUS_GID,
            "current": task["status_label"],
            "proposed_gid": STATUS["HOLD"],
            "proposed_label": "HOLD ON ORDER",
            "reason": "Delay notice received from supplier",
        }
        notes_to_append.append(f"Delay notice received {email['receivedDateTime'][:10]}")

    # Only propose a due date if it is today or in the future
    if delivery_date and is_future_date(delivery_date):
        proposed_updates["Due date"] = {
            "field": "due_on",
            "proposed_value": delivery_date,
            "reason": f"Delivery date from email: '{delivery_date}'",
        }
        notes_to_append.append(f"Supplier indicated delivery: {delivery_date}")

    return {
        "email_id":       email["id"],
        "email_date":     email["receivedDateTime"][:16],
        "email_subject":  email["subject"],
        "email_from":     email["from"]["emailAddress"]["address"],
        "email_type":     email_type,
        "supplier_name":  supplier["name"],
        "supplier_gid":   supplier["gid"],
        "po_numbers_found": po_numbers,
        "match_reason":   match_reason,
        "task_gid":       task["gid"],
        "task_name":      task["name"],
        "task_status":    task["status_label"],
        "proposed_updates": proposed_updates,
        "notes_to_append":  notes_to_append,
        "requires_human_review": True,
    }


def main():
    parser = argparse.ArgumentParser(description="Purchase email parser — Phase 1")
    parser.add_argument("--days", type=int, default=3, help="How many days back to scan (default: 3)")
    parser.add_argument("--write-proposals", action="store_true", help="Save proposals to email_proposals.json")
    args = parser.parse_args()

    # ── Setup ────────────────────────────────────────────────────────────────
    cfg = load_env()
    graph_token  = get_graph_token(cfg)
    asana_token = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN") or os.environ.get("ASANA_PAT")
    if not asana_token:
        try:
            with open(r"C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\OneDrive Documents\LAB. SESAME\Asana Scripts\ASANAconfig.json") as f:
                asana_token = json.load(f).get("ASANA_PERSONAL_ACCESS_TOKEN")
        except:
            pass
    suppliers, domain_map, tag_map = load_suppliers()

    sys.stdout.buffer.write(
        f"Loaded {len(suppliers)} suppliers | {len(domain_map)} domains mapped\n".encode()
    )

    # ── Fetch emails ─────────────────────────────────────────────────────────
    emails = fetch_recent_emails(graph_token, days=args.days)
    sys.stdout.buffer.write(f"Found {len(emails)} inbound emails in last {args.days} days\n\n".encode())

    proposals      = []
    unmatched      = []
    alert_count    = 0
    invoice_count  = 0
    # Track task GIDs matched this run to detect cross-supplier conflicts
    task_match_log = {}   # task_gid → set of supplier names that matched it
    # Alert deduplication state
    alert_state    = _load_alert_state()

    for email in emails:
        sender_addr   = email["from"]["emailAddress"]["address"].lower().strip()
        sender_domain = sender_addr.split("@")[-1]
        subject       = email.get("subject", "")
        body_html     = email.get("body", {}).get("content", "")
        body_text     = clean_body(body_html)

        # ── Skip internal forwarding domains and bounce senders ──────────────
        if sender_domain in EXCLUDED_DOMAINS:
            continue
        sender_localpart = sender_addr.split("@")[0]
        if sender_localpart in EXCLUDED_SENDER_LOCALPARTS:
            continue

        # ── Match supplier ───────────────────────────────────────────────────
        supplier = domain_map.get(sender_domain)
        if not supplier:
            unmatched.append({
                "date":    email["receivedDateTime"][:10],
                "from":    sender_addr,
                "domain":  sender_domain,
                "subject": subject,
            })
            continue

        # ── Extract signals from email body + subject ────────────────────────
        combined_text = body_text + " " + subject
        po_numbers    = extract_po_numbers(combined_text)
        sqr_numbers   = extract_sqr_numbers(combined_text)
        vendor_refs   = extract_vendor_order_refs(combined_text)
        delivery_date = extract_delivery_date(body_text)
        price         = extract_price(body_text)
        email_type    = classify_email(body_text, subject)

        # ── PDF attachment extraction ────────────────────────────────────────
        # Runs for all emails with attachments — critical for suppliers like Aalco
        # who put "Your ref:" blank in the subject but include our SQR in the PDF.
        if email.get("hasAttachments"):
            pdf_sqr, pdf_po, pdf_raw = fetch_pdf_from_email(graph_token, email["id"])
            if pdf_sqr:
                sqr_numbers = list(set(sqr_numbers + pdf_sqr))
            if pdf_po:
                po_numbers = list(set(po_numbers + pdf_po))
            if pdf_raw:
                combined_text += " " + pdf_raw
                # Re-extract signals that may have been in the PDF only
                if not delivery_date:
                    delivery_date = extract_delivery_date(pdf_raw)
                if not price:
                    price = extract_price(pdf_raw)

        # ── Skip bounces, invoices (separate workflows) ───────────────────────
        if email_type == "bounce":
            sys.stdout.buffer.write(
                f"SKIP bounce/NDR: [{email['receivedDateTime'][:10]}] {subject[:60]}\n"
                .encode("utf-8", errors="replace")
            )
            continue
        if email_type == "invoice":
            invoice_count += 1
            sys.stdout.buffer.write(
                f"SKIP invoice (separate workflow): [{email['receivedDateTime'][:10]}] {subject[:60]}\n"
                .encode("utf-8", errors="replace")
            )
            continue

        # ── Find open Asana order tasks for this supplier ────────────────────
        candidates = get_candidates_for_email(asana_token, supplier, po_numbers, sqr_numbers)

        # ── Match email to task ──────────────────────────────────────────────
        task, match_reason = match_task_to_email(candidates, po_numbers, subject, body_text)

        # ── Output ───────────────────────────────────────────────────────────
        header = (
            f"{'='*70}\n"
            f"[{email['receivedDateTime'][:16]}] {subject}\n"
            f"FROM: {sender_addr}  →  SUPPLIER: {supplier['name']}\n"
            f"TYPE: {email_type}  |  POs: {po_numbers or 'none'}  |  "
            f"SQRs: {sqr_numbers or 'none'}  |  "
            f"Delivery: {delivery_date or 'none'}  |  Price: {price or 'none'}\n"
        )
        sys.stdout.buffer.write(header.encode("utf-8", errors="replace"))

        if task:
            proposal = build_proposal(
                email, supplier, task, match_reason, email_type,
                po_numbers, delivery_date, price
            )
            # Attach vendor refs to proposal for SAP to optionally log
            if vendor_refs:
                proposal["vendor_refs"] = vendor_refs
            proposals.append(proposal)

            # ── Auto-write to Asana for quote emails ──────────────────────────
            # Post a comment with the quote details and assign to Giles for review.
            # This is the one action we take automatically (no human pre-approval).
            if email_type == "quote":
                lines = [
                    f"Quote received from {supplier['name']}",
                    f"Date: {email['receivedDateTime'][:10]}",
                    f"Subject: {subject}",
                ]
                if price:
                    lines.append(f"Price: GBP {price} ex VAT")
                if delivery_date and is_future_date(delivery_date):
                    lines.append(f"Delivery indicated: {delivery_date}")
                for label, val in (vendor_refs or []):
                    lines.append(f"{label}: {val}")
                comment_text = "\n".join(lines)
                ok_c = post_asana_comment(asana_token, task["gid"], comment_text)
                ok_a = assign_asana_task(asana_token, task["gid"], GILES_USER_GID)
                if ok_c and ok_a:
                    sys.stdout.buffer.write(b"  [AUTO-WRITE] Quote comment posted + assigned to Giles\n")
                else:
                    sys.stdout.buffer.write(
                        f"  [AUTO-WRITE FAILED] comment={ok_c} assign={ok_a}\n".encode()
                    )

            sys.stdout.buffer.write(
                f"MATCHED TASK ({match_reason}): {task['name']}\n".encode("utf-8", errors="replace")
            )
            sys.stdout.buffer.write(
                f"  Current status: {task['status_label']}\n".encode()
            )
            if vendor_refs:
                sys.stdout.buffer.write(
                    f"  Vendor refs found: {vendor_refs}\n".encode("utf-8", errors="replace")
                )
            if proposal["proposed_updates"]:
                sys.stdout.buffer.write(b"  PROPOSED UPDATES:\n")
                for field, upd in proposal["proposed_updates"].items():
                    val = upd.get("proposed_label") or upd.get("proposed_value", "")
                    sys.stdout.buffer.write(
                        f"    {field}: {upd.get('current','?')} -> {val}  [{upd['reason']}]\n"
                        .encode("utf-8", errors="replace")
                    )
            else:
                sys.stdout.buffer.write(b"  No field updates proposed for this email type.\n")

            # ── Alert: supplier activity on a HELD order (deduplicated, 24h) ──
            if task.get("is_held"):
                if _should_alert(alert_state, task["gid"], "hold_activity"):
                    alert_count += 1
                    sys.stdout.buffer.write(
                        b"  *** ALERT: HOLD ON ORDER task has supplier activity ***\n"
                    )
                    send_alert(
                        subject=f"Activity on HELD order: {task['name'][:60]}",
                        detail=(
                            f"Supplier: {supplier['name']}\n"
                            f"Email: {email_type} from {sender_addr}\n"
                            f"Subject: {subject[:80]}\n"
                            f"POs: {', '.join(po_numbers) if po_numbers else 'none'}"
                        ),
                        task_name=task["name"],
                        task_gid=task["gid"],
                    )
                    _record_alert(alert_state, task["gid"], "hold_activity")
                else:
                    sys.stdout.buffer.write(
                        b"  (HELD order - alert already sent within 24h, skipping)\n"
                    )

            # ── Conflict detection: same task matched by DIFFERENT suppliers ──
            tgid = task["gid"]
            supplier_name = supplier["name"]
            if tgid not in task_match_log:
                task_match_log[tgid] = {supplier_name}
            else:
                task_match_log[tgid].add(supplier_name)
                if len(task_match_log[tgid]) == 2:   # only alert once per task per run
                    conflicting = " vs ".join(sorted(task_match_log[tgid]))
                    if _should_alert(alert_state, tgid, "cross_supplier_conflict"):
                        alert_count += 1
                        sys.stdout.buffer.write(
                            f"  *** ALERT: Cross-supplier conflict — {conflicting} ***\n"
                            .encode("utf-8", errors="replace")
                        )
                        send_alert(
                            subject=f"Cross-supplier conflict on: {task['name'][:60]}",
                            detail=(
                                f"Two different suppliers matched the same order task.\n"
                                f"Suppliers: {conflicting}\n"
                                f"Task status: {task['status_label']}\n"
                                f"Review — may indicate a supplier change or mismatch."
                            ),
                            task_name=task["name"],
                            task_gid=task["gid"],
                        )
                        _record_alert(alert_state, tgid, "cross_supplier_conflict")

        else:
            sys.stdout.buffer.write(
                f"NO TASK MATCH ({match_reason}) — {len(candidates)} candidates found\n"
                .encode()
            )
            if candidates:
                for c in candidates[:3]:
                    sys.stdout.buffer.write(
                        f"  Candidate: {c['name']} [{c['status_label']}]\n"
                        .encode("utf-8", errors="replace")
                    )
        sys.stdout.buffer.write(b"\n")

    # ── Summary ──────────────────────────────────────────────────────────────
    sys.stdout.buffer.write(
        f"\n{'='*70}\nSUMMARY\n"
        f"Emails scanned:              {len(emails)}\n"
        f"Proposals generated:         {len(proposals)}\n"
        f"Alerts raised:               {alert_count}\n"
        f"Invoices skipped:            {invoice_count}\n"
        f"Unmatched (no supplier):     {len(unmatched)}\n".encode()
    )

    # ── Telegram summary ─────────────────────────────────────────────────────
    send_summary({
        "emails_scanned":   len(emails),
        "proposals":        len(proposals),
        "alerts":           alert_count,
        "unmatched":        len(unmatched),
        "invoices_skipped": invoice_count,
    })

    # ── Telegram proposals — one message per proposal with Asana link ─────────
    from notify_telegram import send_telegram
    for p in proposals:
        updates_text = ""
        for field, upd in p.get("proposed_updates", {}).items():
            new_val = upd.get("proposed_label") or upd.get("proposed_value", "")
            updates_text += f"\n  <b>{field}:</b> {upd.get('current', '?')} → {new_val}"
        if not updates_text:
            updates_text = "\n  (No field changes — activity logged)"
        task_url = f"https://app.asana.com/0/{p['task_gid']}/{p['task_gid']}"
        msg = (
            f"📋 <b>[Proposal] {p['supplier_name']}</b>\n"
            f"<a href=\"{task_url}\">{p['task_name']}</a>\n"
            f"<i>{p['email_type']} · {p['email_date']}</i>"
            f"{updates_text}"
        )
        send_telegram(msg)

    # ── Save alert deduplication state ────────────────────────────────────────
    _save_alert_state(alert_state)

    if unmatched:
        sys.stdout.buffer.write(b"\nUNMATCHED SENDERS (no supplier record):\n")
        for u in unmatched:
            sys.stdout.buffer.write(
                f"  [{u['date']}] {u['domain']} — {u['subject'][:60]}\n"
                .encode("utf-8", errors="replace")
            )

    # ── Save proposals ────────────────────────────────────────────────────────
    if args.write_proposals and proposals:
        with open(PROPOSALS_PATH, "w", encoding="utf-8") as f:
            json.dump(proposals, f, indent=2, ensure_ascii=False)
        sys.stdout.buffer.write(
            f"\nProposals saved to {PROPOSALS_PATH}\n".encode()
        )
    elif proposals:
        sys.stdout.buffer.write(
            b"\nRun with --write-proposals to save proposals for SAP to action.\n"
        )


if __name__ == "__main__":
    main()
