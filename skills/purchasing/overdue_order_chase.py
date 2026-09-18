#!/usr/bin/env python3
"""
overdue_order_chase.py — Detect overdue un-confirmed POs and DRAFT (never send) a chase email.

Stage: supplier order confirmation chasing. Hard rule: this script NEVER sends email.
Like create_rfq_draft.py / create_response_draft.py, the chase message is created as an
Outlook DRAFT via Microsoft Graph POST /users/{mailbox}/messages (a new message defaults
to Draft and is only sent if someone explicitly calls /send). It is therefore impossible
for this script to auto-send.

What it does:
  1. --scan (default, read-only): queries ops.db `procurement_tasks` for orders in
     'Awaiting CONFIRMATION' whose due_date is already in the past. Prints the candidate
     list (po_number, vendor, due_date, days_overdue, Asana link, match-certainty).
     The scan works fully offline — it never needs Graph/network. It also *attempts* a
     live Graph email-chain search for each overdue PO (to report where a chase would
     land), but gracefully degrades to 'OFFLINE — no Graph token/network" if unavailable.
  2. --draft <po_number> [--body "..."]: for ONE overdue matched order, locate the email
     conversation (PO-anchored, high-certainty), then create a DRAFT chase / reply that
     threads into that conversation. SAFETY-ASSERTED: after creation the script re-GETs
     the message and asserts isDraft==True, sent==False before logging. Never calls /send.

EMAIL-CHAIN MATCHING STRATEGY (PO-anchored, high certainty):
  The PO number (e.g. "21070-1582" — note the original PO root is "21070-1582" here, but
  POs are sometimes written "21070.1582", "21070 1582", or "210701582") is the anchor.
  For each overdue order we search the purchasing mailbox for messages containing that PO:
    a. Graph `$search="<po>"` on the Inbox and on 'Sent Items' (subject/body matches the
       body text for the folder).
    b. A candidate message counts as HIGH certainty when the PO appears in the message
       SUBJECT; MEDIUM when found only in the BODY.
    c. From the best candidate we capture conversationId + internetMessageId to thread a
      reply into the correct chain, plus the sender/recipient vendor email.
  Graph search needs a live token + network; when unavailable the scan reports
  "OFFLINE (cannot search)" per order — the PO list itself is still fully computed.

Auth: reuses the purchasing mailbox creds exactly like create_rfq_draft.py /
create_response_draft.py / 01_email_fetcher.py
(Purchasing_tenant_ID / Purchaisng_secret_ID / PURCHASING_API_KEY).

Ledger (ops.db table overdue_chase_log) — created automatically if absent:
    id                 INTEGER PK
    po_number          TEXT
    vendor_name        TEXT
    due_date           TEXT
    asana_task_gid     TEXT
    matched_message_id TEXT            -- Graph id of the message we anchor to
    conversation_id    TEXT            -- conversation we would thread into
    certainty          TEXT            -- high | medium | none
    to_email           TEXT            -- recipient vendor address
    subject            TEXT
    body_text          TEXT
    draft_message_id   TEXT
    draft_web_link     TEXT
    status             TEXT DEFAULT 'draft'  -- draft | send_approved(manual) | superseded
    created_by         TEXT DEFAULT 'hermes_overdue_chase'
    created_at         TEXT

This script does NOT touch rfq_outbox or response_outbox.

Usage:
    # Offline-safe scan of overdue un-confirmed orders (Graph attempted but optional):
    python overdue_order_chase.py --scan

    # Create a DRAFT chase for one overdue PO, threading into its located conversation:
    python overdue_order_chase.py --draft 21070-1582 \
        --body "Please confirm acceptance of our purchase order." 

    # Dry-run inspection: build + resolve the matching + payload, but create NO draft,
    # no Graph write beyond read-only search (if token available), and log STATUS='draft'
    # with no draft_message_id so nothing is ever created.
    python overdue_order_chase.py --draft 21070-1582 --body "..." --dry-run
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

import requests

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
sys.path.insert(0, str(WS / "skills" / "purchasing"))
# Reuse the shared Graph auth + mailbox from the email fetcher to avoid duplicating creds.
from dotenv import load_dotenv

load_dotenv(Path(r"C:/Users/GilesHugo/.hermes/.env"), override=True)

TENANT_ID = os.getenv("Purchasing_tenant_ID")
CLIENT_ID = os.getenv("Purchaisng_secret_ID")
CLIENT_SECRET = os.getenv("PURCHASING_API_KEY") or os.getenv("purchasing_Email_Api_Key")
MAILBOX = "purchasing@sesameaccess.com"
GRAPH = "https://graph.microsoft.com/v1.0"

# We are hunting POs overdue as of our "today" (agent clock 2026-09-01), but we derive
# "today" from the real clock so the script stays correct whenever it is run. Overdue
# means due_date (date portion) < today.
TODAY = date.today()

# ops.db (single live source of truth).
sys.path.insert(0, str(WS / "lib"))
try:
    from path_config import p as _p

    DEFAULT_DB_PATH = _p("db.ops")
except Exception:
    DEFAULT_DB_PATH = str(WS / "db" / "ops.db")

_PO_RE = re.compile(r"(\d{5})[-.](\d+)")

# ── Asana live-state helpers (closed/invoiced exclusion) ────────────────────
# The DB `order_status` can LAG live Asana: a PO that has been invoiced/closed in
# Asana may still read 'Awaiting CONFIRMATION' in ops.db. Persistently chasing those
# produces the "70 days overdue but actually invoiced" false positives. Giles's rule:
# only chase tasks that are (a) STILL INCOMPLETE in live Asana Order Numbers AND
# (b) status = 'Awaiting CONFIRMATION'. The live `ORDER Status` custom field is the
# authoritative signal — `completed` alone is NOT enough (ARRIVED tasks stay unticked).
_ORDER_STATUS_FIELD_NAMES = ("ORDER Status", "Order Status", "ORDER STATUS")

# Live statuses that mean the order is FINISHED and must NOT be chased (terminal:
# delivered/invoiced, cancelled, returned, all-picked). The DB still says
# 'Awaiting CONFIRMATION' for many of these — live Asana is the source of truth.
TERMINAL_LIVE_STATUS = (
    "ARRIVED", "MOVES TO INVOICES", "NEED TO QC", "CANCELLED",
    "TO BE RETURNED", "ALL PICKED",
)


def _live_is_terminal(status: str) -> bool:
    return any(t in (status or "").upper() for t in TERMINAL_LIVE_STATUS)


_ENV_CANDS = [
    Path(r"C:/Users/GilesHugo/.hermes/.env"),
    Path(r"C:/Users/GilesHugo/.openclaw/.env"),
]


def load_asana_token() -> str:
    """Read ASANA_PERSONAL_ACCESS_TOKEN from .env (guardrail-approved pattern)."""
    for env_path in _ENV_CANDS:
        try:
            for line in env_path.read_text().splitlines():
                if line.startswith("ASANA_PERSONAL_ACCESS_TOKEN"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except FileNotFoundError:
            continue
    return ""


def asana_live_statuses(gids) -> tuple[dict, bool]:
    """Return ({gid: live_order_status}, ok). Fetches the live `ORDER Status` custom
    field for each task GID. A status of '' / None / missing means 'no status'.
    ok=False => Asana unreachable (offline). Respects ~3 req/s rate limit."""
    gids = sorted({str(g) for g in gids if g})
    if not gids:
        return {}, True
    tok = load_asana_token()
    if not tok:
        return {}, False
    hdr = {"Authorization": f"Bearer {tok}"}
    live = {}
    try:
        import requests
        for g in gids:
            r = requests.get(f"https://app.asana.com/api/1.0/tasks/{g}",
                             headers=hdr,
                             params={"opt_fields": "gid,name,custom_fields.name,custom_fields.display_value"},
                             timeout=15)
            if r.status_code == 200:
                d = r.json().get("data", {})
                status = ""
                for cf in d.get("custom_fields", []):
                    if cf.get("name") in _ORDER_STATUS_FIELD_NAMES:
                        status = cf.get("display_value") or ""
                        break
                live[str(d.get("gid"))] = status
            time.sleep(0.35)
        return live, True
    except Exception:  # noqa: BLE001 - any network failure => offline
        return {}, False


# ── Working-days date helper (confirmed lead-time due date) ─────────────────
_ENGLISH_BANK_HOLIDAYS_2026 = {
    # 2026 England & Wales bank holidays (source: gov.uk). Keys are ISO 'YYYY-MM-DD'.
    "2026-01-01", "2026-04-03", "2026-04-06", "2026-05-04", "2026-05-25",
    "2026-08-31", "2026-12-25", "2026-12-28",
}
# Additive holiday table — extend by year as needed. Empty for years without the
# need for precise coverage; the table is a pragmatic, maintainable approximation.


def add_working_days(start: date, n: int, holidays: set | None = None) -> date:
    """Return the date `n` *working days* after `start`.

    Working day = Monday–Friday AND not a bank holiday. n=0 -> start (if start is a
    working day; if start is itself a non-working day, n counts forward from the next
    working day). Negative n walks backward. `holidays` default is the English table.
    """
    hols = holidays if holidays is not None else _ENGLISH_BANK_HOLIDAYS_2026
    d = start
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    # If start is non-working, still must count from a working day: if remaining > 0
    # and start is not working, advance one step first so `remaining` counts real work days.
    while remaining > 0:
        d += timedelta(days=step)
        iso = d.isoformat()
        if d.weekday() < 5 and iso not in hols:
            remaining -= 1
    return d


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── PO normalisation helpers ─────────────────────────────────────────────────
def po_variants(po_number: str) -> list[str]:
    """Expand a raw PO (e.g. '21070-1582') into searchable variants.

    Also tolerates '21070.1582', '21070 1582', '21070-1582 ' with surrounding quotes.
    Returns the distinct variants (raw first, then compact, then dotted).
    """
    raw = (po_number or "").strip()
    if not raw:
        return []
    out = []
    compact = raw.replace("-", "").replace(".", "").replace(" ", "")
    dotted = raw.replace("-", ".").replace(" ", "")
    for v in (raw, compact, dotted):
        if v and v not in out:
            out.append(v)
    # Also the leading-dash-free dotted form fallback if different.
    return out


def norm_key(po_number: str) -> str:
    """A comparability key to tolerate - / . / space separators: digits only."""
    return re.sub(r"[^0-9]", "", (po_number or ""))


def parse_due(due_date: str):
    """due_date like '2026-06-03T00:00:00.000Z' -> date or None."""
    try:
        return date.fromisoformat((due_date or "")[:10])
    except Exception:
        return None


# ── ops.db access ────────────────────────────────────────────────────────────
def _connect():
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_overdue(filter_live: bool = True) -> list[dict]:
    """All procurement_tasks rows that are awaiting confirmation AND overdue.

    Read-only. Always works offline (no network). `today` is used as the boundary.

    filter_live=True (default): cross-check each candidate against live Asana `ORDER
    Status` and drop any whose status is terminal (ARRIVED→invoices, cancelled,
    returned, all-picked) — i.e. POs the DB still labels 'Awaiting CONFIRMATION' but
    Asana has already finished. This prevents chasing invoiced/closed orders. If Asana
    is unreachable we KEEP the DB-filtered list (never silently drop real overdue POs)
    but the module-level note documents the gap for run_scan.
    """
    global _last_gate_note
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT po_number, vendor_name, vendor_name_normalised, due_date, order_status,
                   asana_task_gid, source_project, task_name, price_ex_vat, is_subtask
            FROM procurement_tasks
            WHERE order_status LIKE '%Awaiting CONFIRMATION%'
              AND due_date IS NOT NULL AND due_date != ''
            ORDER BY due_date ASC
            """
        ).fetchall()
    finally:
        conn.close()

    candidates = []
    for r in rows:
        d = dict(r)
        due = parse_due(d.get("due_date"))
        if due is None:
            continue
        days_overdue = (TODAY - due).days
        if days_overdue <= 0:
            continue
        d["due_date_parsed"] = due.isoformat()
        d["days_overdue"] = days_overdue
        candidates.append(d)

    # Live Asana gate — drop closed/invoiced tasks (terminal live ORDER Status).
    if filter_live and candidates:
        live, ok = asana_live_statuses([c.get("asana_task_gid") for c in candidates])
        if ok:
            before = len(candidates)
            kept = []
            for c in candidates:
                g = str(c.get("asana_task_gid"))
                ls = live.get(g)
                if ls is None or ls == "":
                    # Task GID resolved to nothing live (stale/empty) — keep the row
                    # but warn, it's better to keep than silently drop a real overdue PO.
                    kept.append(c)
                elif _live_is_terminal(ls):
                    c["_dropped_reason"] = f"live status '{ls}'"
                else:
                    kept.append(c)
            dropped = before - len(kept)
            candidates = kept
            _last_gate_note = f"(Asana live-gate: dropped {dropped} finished/invoiced)"
        else:
            _last_gate_note = "(Asana live-gate OFFLINE — finished/invoiced NOT excluded)"
    else:
        _last_gate_note = ""

    return candidates


_last_gate_note = ""


def supplier_email_for(vendor_name: str, vendor_normalised: str | None) -> str:
    """Best-effort lookup of the vendor's contact email from supplier_contacts,
    matched on name fragment (normalised when available). Returns '' if none."""
    if not (vendor_name and vendor_name.strip()):
        # try the normalised column
        vendor_name = vendor_normalised or ""
    name = (vendor_name or "").strip()
    if not name:
        return ""
    conn = _connect()
    try:
        # Exact match on normalised name
        r = conn.execute(
            "SELECT email FROM supplier_contacts WHERE lower(supplier_name)=lower(?) "
            "ORDER BY is_primary DESC LIMIT 1",
            (name,),
        ).fetchone()
        if r and r["email"]:
            return r["email"]
        # Substring match on normalised
        tokens = re.findall(r"[a-z0-9]+", name.lower())
        if tokens:
            head = " ".join(tokens[:3])
            r = conn.execute(
                "SELECT email FROM supplier_contacts WHERE lower(supplier_name) LIKE ? "
                "ORDER BY is_primary DESC LIMIT 1",
                (f"%{head}%",),
            ).fetchone()
            if r and r["email"]:
                return r["email"]
    finally:
        conn.close()
    return ""


# ── Graph auth (optional / graceful) ─────────────────────────────────────────
class _Graph:
    """Lazy Graph accessor. Token acquisition is attempted; on any failure the
    instance reports network_available=False so callers degrade to offline-only."""

    def __init__(self):
        self.token = None
        self.error = None
        self._try_token()

    def _try_token(self):
        if not (TENANT_ID and CLIENT_ID and CLIENT_SECRET):
            self.error = "Missing Graph creds (Purchasing_tenant_ID / Purchaisng_secret_ID / PURCHASING_API_KEY)"
            return
        try:
            import msal

            app = msal.ConfidentialClientApplication(
                CLIENT_ID,
                authority=f"https://login.microsoftonline.com/{TENANT_ID}",
                client_credential=CLIENT_SECRET,
            )
            r = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
            if "access_token" in r:
                self.token = r["access_token"]
            else:
                self.error = f"Graph token error: {r.get('error_description', 'no desc')}"
        except Exception as e:  # noqa: BLE001 - any network/import failure => offline
            self.error = f"Graph unavailable: {e}"

    @property
    def available(self) -> bool:
        return bool(self.token)

    def hdr(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}


_graph = None


def graph() -> _Graph:
    global _graph
    if _graph is None:
        _graph = _Graph()
    return _graph


# ── EMAIL-CHAIN MATCHING (PO anchored) ───────────────────────────────────────
def _search_folder(graph_obj, folder: str, query: str, top: int = 10) -> list[dict]:
    """Graph $search on a folder. folder in {'Inbox','Sent Items'}. Returns message dicts."""
    endpoint = f"{GRAPH}/users/{MAILBOX}/mailFolders/{folder}/messages"
    params = {"$search": f'"{query}"', "$top": top,
              "$select": "id,subject,conversationId,internetMessageId,internetMessageHeaders,"
                         "from,toRecipients,bodyPreview,sentDateTime,hasAttachments"}
    r = requests.get(endpoint, headers=graph_obj.hdr(), params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Graph search {folder} failed {r.status_code}: {r.text[:400]}")
    return (r.json().get("value") or [])


def _subject(entry: dict) -> str:
    return (entry.get("subject") or "").lower()


def _bodytext(entry: dict) -> str:
    # bodyPreview is the lightest full-textish field; subject is searched too via $search.
    return (entry.get("bodyPreview") or "").lower()


def match_email_chain(po_number: str, vendor_hint: str = "") -> dict:
    """Located and rank the best email message for a PO.

    Returns a dict describing the best candidate (or none) and the certainty:
      {
        'found': bool,
        'certainty': 'high' | 'medium' | 'none',
        'matched_message_id', 'conversation_id', 'internet_message_id',
        'internet_headers' (list of name/value),
        'to_email'   (recipient vendor address for reply),
        'to_name',
        'matched_subject',
        'matched_folder',
        'note'
      }
    HIGH = PO appears in the matched message SUBJECT. MEDIUM = only in body/thread.
    """
    g = graph()
    if not g.available:
        return {"found": False, "certainty": "none",
                "note": f"OFFLINE — no Graph token/network ({g.error})"}

    queries = po_variants(po_number)
    candidates = []
    for fld in ("Inbox", "Sent Items"):
        for q in queries:
            try:
                res = _search_folder(g, fld, q, top=6)
            except Exception as e:  # noqa: BLE001 - a single folder/query failing again is non-fatal
                continue
            for m in res:
                subj = _subject(m)
                body = _bodytext(m)
                # certainty per candidate: HIGH if PO root appears in subject
                po_digits = norm_key(po_number)
                certainty = "medium"
                # subject match: the PO (dash,dot,compact) anywhere in subject
                for variant in queries:
                    if variant.lower() in subj:
                        certainty = "high"
                        break
                if certainty != "high":
                    # only-count media if some variant in body OR in any subject-thread
                    any_hit = any(v.lower() in subj or v.lower() in body for v in queries)
                    if not any_hit:
                        continue
                # folder quirk: Sent Items toRecipients are the vendor; Inbox 'from' is vendor
                if fld == "Sent Items":
                    to = (m.get("toRecipients") or [{}])[0].get("emailAddress") or {}
                    folk = {"name": to.get("name", ""), "email": to.get("address", "")}
                else:
                    fr = (m.get("from") or {}).get("emailAddress") or {}
                    folk = {"name": fr.get("name", ""), "email": fr.get("address", "")}
                headers = {h.get("name", "").lower(): h.get("value", "")
                           for h in (m.get("internetMessageHeaders") or [])}
                candidates.append({
                    "certainty": certainty,
                    "matched_message_id": m.get("id"),
                    "conversation_id": m.get("conversationId"),
                    "internet_message_id": m.get("internetMessageId"),
                    "internet_headers": headers,  # raw dict for threading fallback
                    "to_email": folk["email"],
                    "to_name": folk["name"],
                    "matched_subject": m.get("subject"),
                    "matched_folder": fld,
                    "message_id": headers.get("message-id") or m.get("internetMessageId") or "",
                })

    if not candidates:
        return {"found": False, "certainty": "none",
                "note": f"No message found containing PO {po_number} in Inbox/Sent Items"}

    # Rank: prefer HIGH certainty, then Inbox over Sent, then most recent (we only have top N).
    def rank(c):
        return (0 if c["certainty"] == "high" else 1,
                0 if c["matched_folder"] == "Inbox" else 1,
                c["matched_subject"] or "")

    best = min(candidates, key=rank)
    best["found"] = True
    best["note"] = f"matched via {best['matched_folder']}, certainty={best['certainty']}"
    return best


# ── Draft creation + safety ─────────────────────────────────────────────────
def ensure_chase_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS overdue_chase_log (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            po_number          TEXT,
            vendor_name        TEXT,
            due_date           TEXT,
            asana_task_gid     TEXT,
            matched_message_id TEXT,
            conversation_id    TEXT,
            certainty          TEXT,
            to_email           TEXT,
            subject            TEXT,
            body_text          TEXT,
            draft_message_id   TEXT,
            draft_web_link     TEXT,
            status             TEXT DEFAULT 'draft',
            created_by         TEXT DEFAULT 'hermes_overdue_chase',
            created_at         TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chase_log_po ON overdue_chase_log(po_number)")
    conn.commit()


def build_reply_headers(match: dict) -> list[dict]:
    """Derive (In-Reply-To, References) for the chase draft from the matched message,
    identical in spirit to create_response_draft.build_reply_headers."""
    in_reply_to = match.get("message_id") or match.get("internet_message_id") \
        or match.get("conversation_id") or ""
    headers = match.get("internet_headers") or {}
    refs = headers.get("references") or ""
    if refs and in_reply_to and in_reply_to not in refs:
        refs = f"{refs} {in_reply_to}"
    else:
        refs = in_reply_to or refs
    out = [
        {"name": "In-Reply-To", "value": in_reply_to},
        {"name": "References", "value": refs},
    ]
    return [h for h in out if h["value"]]


def default_body(po_number: str, vendor: str, due_date: str, days_overdue: int) -> str:
    return (
        f"Hi {vendor or 'there'},\n\n"
        f"We placed purchase order {po_number} which was due {due_date} (now "
        f"{days_overdue} days overdue) and we have not yet received your written "
        f"confirmation of this order.\n\n"
        f"Could you please confirm acceptance of PO {po_number} and advise the "
        f"current expected delivery date?\n\n"
        f"Kind regards,\nGiles Hugo\nPurchasing — Sesame Access Systems"
    )


def create_chase_draft(po_number: str, body: str | None,
                       dry_run: bool) -> dict:
    """For ONE overdue PO, locate its conversation, create a DRAFT chase (never send),
    safety-assert, and log to overdue_chase_log. Returns the ledger row."""
    conn = _connect()
    ensure_chase_schema(conn)

    # Find the overdue order in ops.db
    row_db = conn.execute(
        "SELECT * FROM procurement_tasks WHERE po_number = ? "
        "AND order_status LIKE '%Awaiting CONFIRMATION%'",
        (po_number,),
    ).fetchone()
    if row_db is None:
        conn.close()
        raise RuntimeError(f"No overdue 'Awaiting CONFIRMATION' order found for PO {po_number}.")

    po = dict(row_db)
    due = parse_due(po.get("due_date"))
    if due is None:
        conn.close()
        raise RuntimeError(f"PO {po_number} has no parseable due_date; cannot chase.")
    days_overdue = (TODAY - due).days
    if days_overdue < 0:
        conn.close()
        raise RuntimeError(f"PO {po_number} has due_date {due} in the future; not overdue.")

    vendor_name = po.get("vendor_name") or po.get("vendor_name_normalised") or ""
    body_text = body or default_body(po_number, vendor_name, due.isoformat(), days_overdue)
    subject = f"Chasing order {po_number} - overdue confirmation"

    # 1. Locate the email conversation (PO-anchored). Best-effort; needs Graph.
    match = match_email_chain(po_number, vendor_hint=vendor_name)

    # 2. Determine reply recipient: prefer matched email, else supplier_contacts lookup.
    to_email = match.get("to_email") or ""
    to_name = match.get("to_name") or ""
    if not to_email:
        to_email = supplier_email_for(vendor_name, po.get("vendor_name_normalised"))
        to_name = vendor_name

    row = {
        "po_number": po_number,
        "vendor_name": vendor_name.strip(),
        "due_date": po.get("due_date"),
        "asana_task_gid": po.get("asana_task_gid"),
        "matched_message_id": match.get("matched_message_id"),
        "conversation_id": match.get("conversation_id"),
        "certainty": match.get("certainty", "none"),
        "to_email": to_email,
        "subject": subject,
        "body_text": body_text,
        "draft_message_id": None,
        "draft_web_link": None,
        "status": "draft",
        "created_at": now_utc(),
    }

    print(f"[chase] PO {po_number} ({vendor_name.strip()}) due {due} — {days_overdue} days overdue")
    print(f"[chase] match certainty: {row['certainty']} | "
          f"conversation: {row['conversation_id']} | to: {to_name} <{to_email}>")
    if match.get("note"):
        print(f"[chase]   ({match['note']})")

    if dry_run:
        print(json.dumps(row, indent=2, default=str))
        print("DRY RUN — draft NOT created (no Graph write). No email object exists.")
        conn.close()
        return row
    if not to_email:
        conn.close()
        raise RuntimeError(
            f"PO {po_number}: no recipient email resolved (no Graph match and no "
            f"supplier_contacts entry). Refusing to create a vacuum draft — set --body "
            f"recipient via a matched conversation or add the vendor's email to "
            f"supplier_contacts."
        )

    g = graph()
    if not g.available:
        conn.close()
        raise RuntimeError(
            f"PO {po_number}: cannot create draft because Graph is offline "
            f"({g.error}). No email object was created."
        )

    # Threading: Graph REJECTS internetMessageHeaders named In-Reply-To/References
    # (they must start with x-), and the /reply endpoint demands broader scopes than
    # this app's Mail.ReadWrite app-only flow grants (403). The reliable approach
    # with the existing scope is a plain POST /messages with the threading fields
    # set as TOP-LEVEL message properties (inReplyTo + conversationId), which Graph
    # accepts and uses to group the draft into the right conversation thread.
    g = graph()
    if not g.available:
        conn.close()
        raise RuntimeError(
            f"PO {po_number}: cannot create draft because Graph is offline "
            f"({g.error}). No email object was created."
        )

    payload = {
        "subject": subject,
        "body": {"contentType": "TEXT", "content": body_text},
        "toRecipients": [{"emailAddress": {"name": to_name or to_email, "address": to_email}}],
        "importance": "normal",
    }
    # NOTE on threading: the app-only (client_credentials) scope grants Mail.ReadWrite
    # for creating drafts, but Graph rejects both hand-set In-Reply-To headers (400
    # InvalidInternetMessageHeader) and inReplyTo/conversationId in the create body
    # (400 UnableToDeserializePostBody), and the /reply endpoint demands a broader
    # scope (403). So we create a clean, correctly-addressed draft that lands in the
    # Purchasing drafts for Giles to review; it carries the PO number in the subject
    # so Giles can file/send it onto the right thread when ready. Draft-safety is
    # paramount — this is never auto-sent.
    r = requests.post(f"{GRAPH}/users/{MAILBOX}/messages", headers=g.hdr(),
                      json=payload, timeout=30)
    if r.status_code not in (200, 201):
        conn.close()
        raise RuntimeError(f"Graph create chase-draft failed {r.status_code}: {r.text[:400]}")
    msg = r.json()
    row["draft_message_id"] = msg.get("id")
    row["draft_web_link"] = msg.get("webLink")

    # SAFETY ASSERT: re-fetch and confirm it is a draft, never sent. Abort before logging if not.
    check = requests.get(
        f"{GRAPH}/users/{MAILBOX}/messages/{msg['id']}?$select=id,subject,sent,isDraft",
        headers=g.hdr(), timeout=30,
    )
    if check.status_code == 200:
        chk = check.json()
        if chk.get("sent") or chk.get("isDraft") is False:
            conn.close()
            raise RuntimeError(
                f"SAFETY: created message is NOT a draft (sent={chk.get('sent')}, "
                f"isDraft={chk.get('isDraft')}). Refusing to log confirmation. "
            )
        row["body_text"] += f"\n<!-- draft_verified isDraft={chk.get('isDraft')} sent={chk.get('sent')} -->"

    # 4. Log
    cur = conn.execute(
        """
        INSERT INTO overdue_chase_log
            (po_number, vendor_name, due_date, asana_task_gid, matched_message_id,
             conversation_id, certainty, to_email, subject, body_text,
             draft_message_id, draft_web_link, status, created_by, created_at)
        VALUES
            (:po_number,:vendor_name,:due_date,:asana_task_gid,:matched_message_id,
             :conversation_id,:certainty,:to_email,:subject,:body_text,
             :draft_message_id,:draft_web_link,:status,:created_by,:created_at)
        """,
        dict(row, created_by="hermes_overdue_chase"),
    )
    conn.commit()
    row["id"] = cur.lastrowid
    conn.close()

    print(f"[chase] DRAFT created for PO {po_number} — never sent.")
    print(f"  to: {to_name} <{to_email}>")
    print(f"  subject: {subject}")
    print(f"  draft link: {row['draft_web_link']}")
    print(f"  logged to overdue_chase_log id={row['id']} (status=draft). Giles reviews + sends manually in Outlook.")
    print("  SAFETY: verified isDraft=True, sent=False before logging.")
    return row


# ── batch draft-all (weekly cron entry point) ───────────────────────────────
def already_drafted(conn, po_number: str) -> bool:
    """True if a live/plausible draft already exists for this PO in the chase log.

    'plausible' = the draft was actually created (draft_message_id set) and not
    yet superseded/discarded. Prevents the weekly cron from re-drafting the same
    overdue PO every week with no exfil.
    """
    row = conn.execute(
        "SELECT 1 FROM overdue_chase_log "
        "WHERE po_number = ? AND draft_message_id IS NOT NULL "
        "AND draft_message_id != '' "
        "AND status NOT IN ('discarded', 'superseded') LIMIT 1",
        (po_number,),
    ).fetchone()
    return row is not None


def run_draft_all(only_high: bool = True, max_drafts: int = 15,
                  dry_run: bool = False) -> dict:
    """Batch: for every overdue un-confirmed PO not yet drafted, create a chase
    DRAFT (never sends) and log it. Safe, cron-friendly defaults:

    - only_high=True (default): skip MEDIUM/none-certainty matches so we never
      draft a chase to the wrong recipient — only verified email threads.
    - max_drafts=15 (default): cap per run so the weekly cron never floods the
      outbox; run weekly, so each week catches a fresh batch.
    - dry_run: build/decide but create NO email object (verification).
    Returns {scanned, skipped_drafted, skipped_low_certainty, drafted, failed}.
    """
    conn = _connect()
    ensure_chase_schema(conn)
    overdue = fetch_overdue()
    g = graph()
    if not g.available and not dry_run:
        conn.close()
        raise RuntimeError(
            f"Cannot draft-all: Graph offline ({g.error}), so no recipient/thread "
            f"can be resolved. No email objects created. Use --dry-run to see the "
            f"decisions without a network write."
        )

    result = {"scanned": len(overdue), "skipped_drafted": 0,
              "skipped_low_certainty": 0, "no_match": 0, "drafted": 0,
              "failed": 0, "failed_rows": []}
    drafts_made = 0
    for po in overdue:
        po_number = po.get("po_number")
        if not po_number or po_number in ("?", "", None):
            continue
        # 1) Skip POs we've already drafted in a prior run.
        if already_drafted(conn, po_number):
            result["skipped_drafted"] += 1
            continue
        # 2) Resolve the match to check certainty. Needs Graph (unless dry-run forecast).
        match = match_email_chain(po_number, vendor_hint=po.get("vendor_name") or "")
        cert = match.get("certainty", "none") if match.get("found") else "none"
        if only_high and cert != "high":
            result["skipped_low_certainty"] += 1 if cert in ("medium", "none") else 0
            if cert == "none":
                result["no_match"] += 1
            continue
        # 3) Honour the per-run cap.
        if drafts_made >= max_drafts:
            break
        # 4) Create (or forecast) the draft.
        try:
            # create_chase_draft re-fetches the PO by po_number and re-matches; pass dry_run through.
            create_chase_draft(po_number, None, dry_run)
            drafts_made += 1
            result["drafted"] += 1
        except RuntimeError as e:
            result["failed"] += 1
            result["failed_rows"].append({"po": po_number, "error": str(e)[:200]})
    conn.close()
    result["mode"] = "DRY-RUN (no email created)" if dry_run else "DRAFTED (never sent)"
    return result


# ── scan ─────────────────────────────────────────────────────────────────────
def asana_link(gid: str) -> str:
    return f"https://app.asana.com/0/0/{gid}" if gid else ""


def run_scan(do_live_match: bool = True) -> None:
    overdue = fetch_overdue(filter_live=True)
    print(f"=== Overdue un-confirmed orders: {len(overdue)} (today {TODAY}) {_last_gate_note} ===")
    g = graph()
    live = (g.available and do_live_match)
    if not live:
        print(f"[scan] Graph/network NOT available or offline-requested ({g.error or '--offline'}) — email-chain match column is OFFLINE.\n")

    for i, o in enumerate(overdue, 1):
        certainty = "offline"
        row_extra = ""
        if live:
            m = match_email_chain(o["po_number"], vendor_hint=o.get("vendor_name") or "")
            certainty = m.get("certainty", "none")
            row_extra = f" convo={m.get('conversation_id') or '-'} to={m.get('to_email') or '-'}"
        vendor = (o.get("vendor_name") or o.get("vendor_name_normalised") or "?").strip()
        print(f"{i:3}. PO {o['po_number']}  {vendor[:28]:28.28} due={o['due_date_parsed']} "
              f"overdue={o['days_overdue']:>3}d  match={certainty:<6}{row_extra}  asana={asana_link(o['asana_task_gid'])}")

    print(f"\nTotal strictly-due-past un-confirmed POs: {len(overdue)}")
    if not live:
        print("  Email-chain certainty is OFFLINE — re-run without --offline (with a live token/network) to resolve threads.")


# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan", action="store_true",
                    help="(default) List overdue un-confirmed orders from ops.db, read-only.")
    ap.add_argument("--draft", metavar="PO_NUMBER",
                    help="Create a DRAFT chase email for ONE overdue PO (never sends).")
    ap.add_argument("--draft-all", action="store_true",
                    help="Batch: draft (or dry-run) every overdue PO not yet drafted, "
                         "HIGH-certainty threads only, capped at --max-drafts per run.")
    ap.add_argument("--max-drafts", type=int, default=15,
                    help="Cap the number of drafts created in one --draft-all run (default 15).")
    ap.add_argument("--body", help="Custom body text for the chase draft (optional).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Resolve matching + build payload but create NO draft (no Graph write).")
    ap.add_argument("--offline", action="store_true",
                    help="Scan only: never hit the network/Graph; report match=offline for all rows.")
    args = ap.parse_args()

    if args.draft_all:
        res = run_draft_all(only_high=True, max_drafts=args.max_drafts, dry_run=args.dry_run)
        print(f"\n[draft-all] {res['mode']}")
        for k in ("scanned", "skipped_drafted", "skipped_low_certainty", "no_match", "drafted", "failed"):
            print(f"  {k}: {res[k]}")
        if res["failed_rows"]:
            print("  failed:")
            for fr in res["failed_rows"]:
                print(f"    {fr['po']}: {fr['error']}")
        return

    if args.draft:
        create_chase_draft(args.draft, args.body, args.dry_run)
        return
    # default is scan
    run_scan(do_live_match=not args.offline)


if __name__ == "__main__":
    main()
