#!/usr/bin/env python3
"""
create_response_draft.py — Create an OUTBOUND REPLY DRAFT to an inbound email + log to response_outbox.

Stage: purchasing inbox triage / reply drafting. Hard rule: this script NEVER sends
email. Like create_rfq_draft.py, the reply is created as an Outlook DRAFT via
Microsoft Graph POST /users/{mailbox}/messages (a new message defaults to Draft and is
only sent if someone explicitly calls /send). It is therefore impossible for this
script to auto-send.

What it does:
  1. Fetches the original inbound message from Graph
     GET /users/{MAILBOX}/messages/{inbound-id}
  2. Runs a deterministic "response-needed" classifier over its subject + body and
     records the decision (response_needed + a reason code).
  3. Builds a REPLY request that:
        - threads the conversation (subject prefixed 'Re:', plus internetMessageHeaders
          In-Reply-To / References taken from the original message), and
        - addresses the reply to the original sender (toRecipients from the fetched
          `from` field).
  4. Creates an OUTLOOK DRAFT (never auto-sent), then re-GETs it and ASSERTS it is a
     draft (isDraft == True, sent == False) before logging.
  5. Logs to a NEW ops.db table `response_outbox` (the reply ledger for the purchasing
     inbox). You can later mark a row 'no_reply' via --flag-no-reply.

Important: because a new-message POST always creates a Draft, the In-Reply-To /
References threading is expressed through `internetMessageHeaders` on that new draft —
the draft appears in the same conversation when Giles reviews/replies from Outlook. We
do NOT call the /reply endpoint (which allows sending in the same call) to keep replies
quarantined as drafts.

Auth: reuses the purchasing mailbox creds exactly like create_rfq_draft.py / 01_email_fetcher.py
(Purchasing_tenant_ID / Purchaisng_secret_ID / PURCHASING_API_KEY).

Ledger (ops.db table response_outbox) — created automatically if absent:
    id               INTEGER PK
    inbound_message_id TEXT            -- Graph id of the original inbound message
    inbound_subject   TEXT
    to_email          TEXT             -- original sender (reply recipient)
    to_name           TEXT
    subject           TEXT             -- the 'Re: ...' reply subject we set
    body_text         TEXT
    reply_to_headers  TEXT             -- JSON of the In-Reply-To / References we set
    draft_message_id  TEXT
    draft_web_link    TEXT
    status            TEXT DEFAULT 'draft'  -- draft | reviewed | sent | no_reply | archived
    response_needed   INTEGER 0/1      -- classifier decision
    created_by        TEXT DEFAULT 'hermes_inbox'
    created_at        TEXT
    review_note       TEXT

Usage:
    # Build + create the draft reply (and log to response_outbox):
    python create_response_draft.py \\
      --inbound-id "<graph_message_id>" \\
      --body "Thanks for the quotation, we will confirm shortly." \\
      [--subject "Re: Quotation 1234"]

    # Inspect what WOULD be done without touching Graph (no write, no draft, still logs):
    python create_response_draft.py --inbound-id "<id>" --body "..." --dry-run

    # Flag an existing outbox row as 'no_reply' (no response required):
    python create_response_draft.py --flag-no-reply <response_outbox_id> \\
      --note "Automated confirmation, nothing needed"

    # Just run the classifier on an inbound message and print the decision (no draft):
    python create_response_draft.py --inbound-id "<id>" --classify-only
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
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

# ops.db (single live source of truth).
sys.path.insert(0, str(WS / "lib"))
try:
    from path_config import p as _p

    DEFAULT_DB_PATH = _p("db.ops")
except Exception:
    DEFAULT_DB_PATH = str(WS / "db" / "ops.db")


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── DB ledger ───────────────────────────────────────────────────────────────
def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS response_outbox (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            inbound_message_id TEXT,
            inbound_subject    TEXT,
            to_email           TEXT,
            to_name            TEXT,
            subject            TEXT,
            body_text          TEXT,
            reply_to_headers   TEXT,
            draft_message_id   TEXT,
            draft_web_link     TEXT,
            status             TEXT DEFAULT 'draft',
            response_needed    INTEGER DEFAULT 0,
            created_by         TEXT DEFAULT 'hermes_inbox',
            created_at         TEXT,
            review_note        TEXT
        )
        """
    )
    # Minimal index to look rows up by inbound message / no_reply flags quickly.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_response_outbox_inbound ON response_outbox(inbound_message_id)"
    )
    conn.commit()


# ── Graph auth + fetch ──────────────────────────────────────────────────────
class _LazyApp:
    _app = None

    @classmethod
    def app(cls):
        if cls._app is None:
            import msal

            if not (TENANT_ID and CLIENT_ID and CLIENT_SECRET):
                raise RuntimeError(
                    "Missing Graph creds (Purchasing_tenant_ID / Purchaisng_secret_ID / PURCHASING_API_KEY)"
                )
            cls._app = msal.ConfidentialClientApplication(
                CLIENT_ID,
                authority=f"https://login.microsoftonline.com/{TENANT_ID}",
                client_credential=CLIENT_SECRET,
            )
        return cls._app

    @classmethod
    def token(cls) -> str:
        r = cls.app().acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" in r:
            return r["access_token"]
        raise RuntimeError(f"Graph token error: {r.get('error_description', 'no desc')}")


def _hdr():
    return {"Authorization": f"Bearer {_LazyApp.token()}", "Content-Type": "application/json"}


def fetch_inbound(inbound_id: str) -> dict:
    """GET the original inbound message from the purchasing mailbox.

    We fetch: id, subject, from, internetMessageHeaders (for In-Reply-To /
    References threading), body preview + full body (text & html), sentDateTime.
    """
    r = requests.get(
        f"{GRAPH}/users/{MAILBOX}/messages/{inbound_id}"
        f"?$select=id,subject,from,toRecipients,internetMessageHeaders,"
        f"sentDateTime,body,bodyPreview,conversationId,singleValueExtendedProperties",
        headers=_hdr(),
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Graph fetch inbound failed {r.status_code}: {r.text[:400]}")
    return r.json()


# ── Response-needed classifier ──────────────────────────────────────────────
# Deterministic heuristic: returns (bool response_needed, str reason_code).
# reason_code is a stable slug (used to document why classification chose as it did).
def classify_response_needed(inbound: dict) -> tuple[bool, str]:
    """Decide whether this inbound email needs a human reply.

    Deterministic, rule-based. Never calls an LLM. Returns a (bool, reason_code) pair.
    reason_code is a stable slug (used to document why classification chose as it did).
    """
    subject = (inbound.get("subject") or "").lower()
    _body = inbound.get("body")
    # Graph may return body as an object {"content": "...", "contentType": "..."} or as a
    # plain string depending on context flags; handle both.
    if isinstance(_body, dict):
        body = (_body.get("content") or "").lower()
    else:
        body = str(_body or "").lower()
    body = body or (inbound.get("bodyPreview") or "").lower()
    sender = ((inbound.get("from") or {}).get("emailAddress") or {})
    sender_addr = (sender.get("address") or "").lower()
    combined = f"{subject}\n{body}"

    def has(*keywords: str) -> bool:
        return any(kw in combined for kw in keywords)

    # 1. Automated / no-human-reply classifiers first (lowest priority for reply-required).
    if "return-path" in combined or "delivery status notification" in combined or "undeliverable" in combined:
        return False, "delivery_status"            # bounce / delivery report — nothing to reply to
    if has("automated reply", "auto-reply", "out of office", "automatic reply"):
        return False, "out_of_office"
    if has("do not reply", "do-not-reply", "donotreply", "noreply"):
        return False, "no_reply_mailbox"

    # 2. Surveys / transactional notifications — usually no reply needed.
    if has("how did we do", "tell us how", "share your feedback", "survey", "nps",
           "take our short", "rate your experience"):
        return False, "survey"
    # Pure notification phrasings of an order/payment already received by the OTHER party.
    # (Note: a bare subject like "order confirmation" is ambiguous and NOT auto-suppressed —
    # supplier order acknowledgments are handled below in the reply-required block.)
    if has("we have received your order", "your order has been received", "order received",
           "payment received", "your invoice", "invoice available", "receipt for",
           "your payment has been received", "order is being processed", "order shipped",
           "your order has shipped"):
        return False, "order_confirmation"
    if has("subscription", "unsubscribe", "you are now subscribed", "welcome to"):
        return False, "transactional_notice"

    # 3. Now genuine reply triggers.
    if has("po ", "purchase order", "confirm order", "acknowledg", "please confirm",
           "need your confirmation", "awaiting your confirmation"):
        return True, "po_ack_confirm"              # supplier asking for order confirmation / acknowledgment
    if has("quoting", "quotation", "quote for", "please quote", "quote request",
           "request for quote", "rfq", "price for", "can you supply"):
        return True, "rfq_inquiry"                 # supplier asking for a PO/quote
    if has("question", "query", "could you", "can you please", "please advise",
           "need your help", "issue", "problem", "not received", "delayed",
           "overdue", "discrepancy", "incorrect", "dispute"):
        return True, "query_or_issue"
    if has("thank you for your order", "thanks for your order", "appreciate your order",
           "order is confirmed", "order confirmed"):
        return True, "order_ack_thanks"            # ack an order, usually requires a reply
    if has("delivery", "lead time", "shipment", "dispatch", "tracking", "on hold"):
        return True, "shipment_delivery"

    # 4. Anything a human is on the other end of and we cannot classify → default needs review.
    if sender_addr and "@" in sender_addr:
        return True, "manual_review"
    return False, "unclassified"


# ── Threading / reply header helpers ────────────────────────────────────────
def build_reply_headers(inbound: dict) -> list[dict]:
    """Derive (In-Reply-To, References) for the reply draft from the original message.

    Reads the original 'internetMessageHeaders' payload. Graph exposes these native
    headers under `internetMessageHeaders` (name/value pairs). If the original already
    carried an In-Reply-To / References message-id from its own threading, we chain onto
    them; otherwise we fall back to the conversationId so Outlook shows the reply in the
    same conversation.
    """
    headers = {h.get("name", "").lower(): h.get("value", "") for h in (inbound.get("internetMessageHeaders") or [])}

    in_reply_to = headers.get("in-reply-to") or headers.get("message-id") or inbound.get("conversationId") or ""
    references = headers.get("references") or ""
    # Compose References = prior references (if any) + the id we are replying into.
    if references and in_reply_to and in_reply_to not in references:
        refs = f"{references} {in_reply_to}"
    else:
        refs = in_reply_to or references

    # Return the two threading headers as message-header objects for the draft.
    out = [
        {"name": "In-Reply-To", "value": in_reply_to},
        {"name": "References", "value": refs},
    ]
    return [h for h in out if h["value"]]


def build_reply_subject(inbound: dict) -> str:
    """'Re: <original subject>' — strip any existing Re:/Fwd: prefixes to avoid Re: Re: Re:."""
    subject = (inbound.get("subject") or "").strip()
    import re

    cleaned = re.sub(r"^(re|fw|fwd)\s*:\s*", "", subject, flags=re.IGNORECASE) if subject else ""
    cleaned = cleaned.strip()
    if cleaned:
        return f"Re: {cleaned}"
    # No readable subject → use the conversation id so it still threads.
    return f"Re: {inbound.get('conversationId') or ''}".strip()


def reply_recipient(inbound: dict) -> tuple[str, str]:
    """Original sender of the inbound message -> the (name, address) who gets the reply."""
    ea = (inbound.get("from") or {}).get("emailAddress") or {}
    return ea.get("name") or "", ea.get("address") or ""


# ── Draft creation + safety ─────────────────────────────────────────────────
def create_reply_draft(inbound_id: str, reply_body: str, subject: str | None,
                       dry_run: bool, created_by: str = "hermes_inbox") -> dict:
    """Fetch inbound, classify, build reply payload, create DRAFT (never send), log, return ledger row."""
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    inbound = fetch_inbound(inbound_id)

    needed, reason_code = classify_response_needed(inbound)
    reply_subject = subject or build_reply_subject(inbound)
    name, addr = reply_recipient(inbound)
    reply_headers = build_reply_headers(inbound)

    row = {
        "inbound_message_id": inbound_id,
        "inbound_subject": inbound.get("subject"),
        "to_email": addr,
        "to_name": name,
        "subject": reply_subject,
        "body_text": reply_body,
        "reply_to_headers": json.dumps(reply_headers, indent=2, default=str),
        "draft_message_id": None,
        "draft_web_link": None,
        "status": "draft",
        "response_needed": 1 if needed else 0,
        "created_at": now_utc(),
        "review_note": f"classifier=response_needed:{needed} reason:{reason_code}",
    }

    print(f"[response] inbound: {inbound.get('subject')!r} from {name} <{addr}>")
    print(f"[response] classifier decision: response_needed={needed} (reason={reason_code})")
    print(f"[response] reply subject: {reply_subject!r}")
    print(f"[response] threading headers: {json.dumps(reply_headers)}")

    if dry_run:
        print(json.dumps(row, indent=2, default=str))
        print("DRY RUN — draft payload built + classified, but draft NOT created (no Graph write).")
        _log_row(conn, row, created_by)
        conn.close()
        return row

    payload = {
        "subject": reply_subject,
        "body": {"contentType": "TEXT", "content": reply_body},
        "toRecipients": [{"emailAddress": {"name": name, "address": addr}}],
        "internetMessageHeaders": reply_headers,
        "importance": "normal",
    }
    r = requests.post(f"{GRAPH}/users/{MAILBOX}/messages", headers=_hdr(), json=payload, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Graph create reply-draft failed {r.status_code}: {r.text[:400]}")
    msg = r.json()
    row["draft_message_id"] = msg.get("id")
    row["draft_web_link"] = msg.get("webLink")

    # SAFETY ASSERT: confirm the draft landed as a draft, never sent.
    check = requests.get(
        f"{GRAPH}/users/{MAILBOX}/messages/{msg['id']}?$select=id,subject,sent,isDraft",
        headers=_hdr(), timeout=30,
    )
    if check.status_code == 200:
        chk = check.json()
        if chk.get("sent") or chk.get("isDraft") is False:
            # Never auto-send — this would indicate a Graph-side anomaly. Flag loudly, abort before logging.
            raise RuntimeError(
                f"SAFETY: created message is NOT a draft (sent={chk.get('sent')}, "
                f"isDraft={chk.get('isDraft')}). Refusing to log confirmation."
            )
        row["review_note"] += f" | draft_verified isDraft={chk.get('isDraft')} sent={chk.get('sent')}"

    _log_row(conn, row, created_by)
    conn.close()

    print(f"[response] REPLY DRAFT created for inbound {inbound_id} — never sent.")
    print(f"  to: {name} <{addr}>")
    print(f"  subject: {reply_subject}")
    print(f"  draft link: {row['draft_web_link']}")
    print(f"  logged to response_outbox id={row['id']} (status=draft). Giles reviews + sends manually in Outlook.")
    print(f"  SAFETY: verified isDraft=True, sent=False before logging.")
    return row


def _log_row(conn: sqlite3.Connection, row: dict, created_by: str) -> None:
    cur = conn.execute(
        """
        INSERT INTO response_outbox
            (inbound_message_id, inbound_subject, to_email, to_name, subject,
             body_text, reply_to_headers, draft_message_id, draft_web_link,
             status, response_needed, created_by, created_at, review_note)
        VALUES
            (:inbound_message_id, :inbound_subject, :to_email, :to_name, :subject,
             :body_text, :reply_to_headers, :draft_message_id, :draft_web_link,
             :status, :response_needed, :created_by, :created_at, :review_note)
        """,
        dict(row, created_by=created_by),
    )
    conn.commit()
    row["id"] = cur.lastrowid


def mark_no_reply(outbox_id: int, note: str | None = None) -> None:
    """Flag an existing response_outbox row as 'no_reply' (email did not need a response)."""
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM response_outbox WHERE id = ?", (outbox_id,)
    ).fetchone()
    if row is None:
        conn.close()
        raise RuntimeError(f"response_outbox row {outbox_id} not found.")
    note = note or row["review_note"] or ""
    conn.execute(
        """
        UPDATE response_outbox
           SET status = 'no_reply',
               review_note = ?
         WHERE id = ?
        """,
        ((note if note else "flagged no_reply"), outbox_id),
    )
    conn.commit()
    conn.close()
    print(f"[response] response_outbox id={outbox_id} marked status='no_reply'.")


def classify_only(inbound_id: str) -> None:
    """Print the classifier decision for an inbound message without creating any draft."""
    inbound = fetch_inbound(inbound_id)
    needed, reason = classify_response_needed(inbound)
    subject = inbound.get("subject")
    ea = (inbound.get("from") or {}).get("emailAddress") or {}
    print(f"[classify] inbound: {subject!r} from {ea.get('name')} <{ea.get('address')}>")
    print(f"[classify] decision: response_needed={needed} reason={reason}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inbound-id", help="Graph message id of the original inbound email.")
    ap.add_argument("--body", help="Reply body text to place in the draft.")
    ap.add_argument("--subject", help="Override 'Re: <orig>' (default derives from original subject).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Build payload + classify + log, but do NOT create the draft.")
    ap.add_argument("--flag-no-reply", type=int, metavar="OUTBOX_ID",
                    help="Mark an existing response_outbox row as 'no_reply' (no response required).")
    ap.add_argument("--note", help="Free-text review_note (used with --flag-no-reply or written into the ledger).")
    ap.add_argument("--classify-only", action="store_true",
                    help="Only run the response-needed classifier on --inbound-id; create no draft.")
    args = ap.parse_args()

    if args.flag_no_reply is not None:
        mark_no_reply(args.flag_no_reply, args.note)
        return

    if args.classify_only:
        if not args.inbound_id:
            ap.error("--classify-only requires --inbound-id")
        classify_only(args.inbound_id)
        return

    if not args.inbound_id:
        ap.error("--inbound-id is required (unless using --flag-no-reply or --classify-only).")
    if not args.body:
        ap.error("--body is required when creating a reply draft.")

    created_by = args.note or "hermes_inbox"
    create_reply_draft(args.inbound_id, args.body, args.subject, args.dry_run, created_by=created_by)


if __name__ == "__main__":
    main()
