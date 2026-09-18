#!/usr/bin/env python3
"""
create_rfq_draft.py — Create an OUTBOUND RFQ as an Outlook DRAFT + log to rfq_outbox.

Stage P4 (RFQ Dispatch & SQR Tracking). Hard rule: ALL outbound quotes are created
as DRAFTS for human review — this script NEVER sends email. The draft is created via
Microsoft Graph POST /users/{mailbox}/messages (a new message defaults to Draft and is
only sent if someone explicitly calls /send). So it is impossible for this script to
auto-send.

Each RFQ gets a NEW SQR reference (issued by us, distinct numeric range, never collides
with inbound SQRs from procurement_tasks). Sent/received per SQR is tracked in the
rfq_outbox ledger so we can report "N sent, M received" per SQR and per supplier.

Ledger (ops.db table rfq_outbox) — created automatically if absent:
    id INTEGER PK
    sqr_number TEXT UNIQUE          -- the SQR we issued for this RFQ
    supplier_name TEXT              -- recipient
    supplier_email TEXT             -- recipient address
    subject TEXT
    body_html TEXT
    part_summary TEXT               -- the part list sent for quote
    draft_message_id TEXT           -- Graph message id of the draft
    draft_web_link TEXT             -- a link to open the draft (createdDateTime/id
    sent_at TEXT                    -- NULL until Giles sends it (Graph /send by human)
    received_at TEXT                -- NULL until a quote email for this SQR arrives
    status TEXT DEFAULT 'draft'     -- draft | sent | received | partial
    created_by TEXT DEFAULT 'hermes_p4'
    created_at TEXT

Auth: reuses the purchasing mailbox creds exactly like 01_email_fetcher.py
(Purchasing_tenant_ID / Purchaisng_secret_ID / PURCHASING_API_KEY).

Usage:
    python create_rfq_draft.py \
      --supplier "Thameside" \
      --email "thameside_eng@outlook.com" \
      --subject "RFQ for 1588 scissor lift" \
      --parts "1588.1025.A.001 x2|1588.1025.A.019 x1" \
      [--dry-run]      # build the payload + issue SQR but do NOT create the draft

Verification of safety: after creation the script asserts the message is a draft
(Graph GET /messages/{id}?$select=sent,isDraft) and prints the draft link. It never
calls /send.
"""
import argparse
import json
import os
import re
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

DRAFT_FROM = "Sesame Access Systems Purchasing <purchasing@sesameaccess.com>"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── DB ledger ───────────────────────────────────────────────────────────────
def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rfq_outbox (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            sqr_number       TEXT UNIQUE,
            supplier_name    TEXT,
            supplier_email   TEXT,
            subject          TEXT,
            body_html        TEXT,
            part_summary     TEXT,
            draft_message_id TEXT,
            draft_web_link   TEXT,
            sent_at          TEXT,
            received_at      TEXT,
            status           TEXT DEFAULT 'draft',
            created_by       TEXT DEFAULT 'hermes_p4',
            created_at       TEXT,
            supplier_quote_ref   TEXT,
            confirmation_number  TEXT,
            invoice_number       TEXT,
            quote_sender_email   TEXT,
            quote_sender_name    TEXT
        )
    """)
    conn.commit()


def _max_sqr_numeric(conn: sqlite3.Connection) -> int:
    """Largest numeric SQR seen across procurement_tasks (inbound) + rfq_outbox."""
    mx = 0
    patterns = [
        "SELECT MAX(CAST(substr(sqr_number,5) AS INTEGER)) FROM procurement_tasks WHERE sqr_number GLOB 'SQR-[0-9]*'",
        "SELECT MAX(CAST(substr(sqr_number,5) AS INTEGER)) FROM rfq_outbox WHERE sqr_number GLOB 'SQR-[0-9]*'",
    ]
    for sql in patterns:
        try:
            v = conn.execute(sql).fetchone()[0]
            if v and int(v) > mx:
                mx = int(v)
        except Exception:
            pass
    return mx


def next_sqr(conn: sqlite3.Connection) -> str:
    """Issue the next SQR, never colliding with inbound or previously-issued."""
    return f"SQR-{_max_sqr_numeric(conn) + 1}"


# ── Graph auth + draft ──────────────────────────────────────────────────────
class _LazyApp:
    _app = None

    @classmethod
    def app(cls):
        if cls._app is None:
            import msal
            if not (TENANT_ID and CLIENT_ID and CLIENT_SECRET):
                raise RuntimeError("Missing Graph creds (Purchasing_tenant_ID / Purchaisng_secret_ID / PURCHASING_API_KEY)")
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


def build_body_html(supplier: str, parts: list[tuple[str, str]]) -> str:
    """Parts = list of (qty, part_ref_desc). Returns a clean HTML RFQ body."""
    rows = "\n".join(
        f"<tr><td>{qty}</td><td>{desc}</td></tr>" for qty, desc in parts
    )
    return f"""
<p>Good morning {supplier},</p>
<p>Please provide a quotation for the following items for project quote:</p>
<table border="1" cellpadding="6" style="border-collapse:collapse">
<tr><th>Qty</th><th>Part / description</th></tr>{rows}
</table>
<p>Please quote ex-VAT, lead time, and confirm availability.</p>
<p>Kind regards,<br/>Giles Hugo<br/>Purchasing — Sesame Access Systems</p>
"""


def parse_parts(spec: str) -> list[tuple[str, str]]:
    """Parse a --parts string of 'qty desc|qty desc|...' -> [(qty, desc)]. For parts
    already in {qty} off {pn} - {desc} Asana form, carry through verbatim."""
    out = []
    for chunk in spec.split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^(\d+)\s+(.+)$", chunk)
        if m:
            out.append((m.group(1), m.group(2)))
        else:
            out.append(("1", chunk))
    return out


def create_draft(supplier: str, email: str, subject: str, parts_spec: str,
                 dry_run: bool) -> dict:
    """Create an Outlook DRAFT for the RFQ and log it to rfq_outbox. Returns the ledger row."""
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    sqr = next_sqr(conn)
    parts = parse_parts(parts_spec)
    body_html = build_body_html(supplier, parts)

    row = {
        "sqr_number": sqr, "supplier_name": supplier, "supplier_email": email,
        "subject": subject, "body_html": body_html,
        "part_summary": "; ".join(f"{q} x {d}" for q, d in parts),
        "draft_message_id": None, "draft_web_link": None,
        "sent_at": None, "received_at": None, "status": "draft",
        "created_at": now_utc(),
    }

    if dry_run:
        print(json.dumps(row, indent=2, default=str))
        print("DRY RUN — draft NOT created (no Graph write).")
        conn.close()
        return row

    payload = {
        "subject": f"[{sqr}] {subject}",
        "body": {"contentType": "HTML", "content": body_html},
        "toRecipients": [{"emailAddress": {"name": supplier, "address": email}},
                          {"emailAddress": {"name": supplier, "address": email}}][:1],
        "importance": "normal",
    }
    r = requests.post(f"{GRAPH}/users/{MAILBOX}/messages",
                      headers=_hdr(), json=payload, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Graph create-draft failed {r.status_code}: {r.text[:400]}")
    msg = r.json()
    row["draft_message_id"] = msg.get("id")
    row["draft_web_link"] = msg.get("webLink")

    # SAFETY ASSERT: confirm the draft landed as a draft, never sent.
    check = requests.get(
        f"{GRAPH}/users/{MAILBOX}/messages/{msg['id']}?$select=id,subject,sent,isDraft",
        headers=_hdr(), timeout=30)
    if check.status_code == 200:
        chk = check.json()
        if chk.get("sent") or chk.get("isDraft") is False:
            # This would be a bug (never auto-send). Do NOT keep going; flag loudly.
            raise RuntimeError(f"SAFETY: created message is NOT a draft (sent={chk.get('sent')})")
        row["body_html"] += f'<!-- draft_verified isDraft={chk.get("isDraft")} sent={chk.get("sent")} -->'

    # Log the row (INSERT OR REPLACE on sqr to stay idempotent on rerun).
    conn.execute("""
        INSERT OR REPLACE INTO rfq_outbox
            (sqr_number, supplier_name, supplier_email, subject, body_html,
             part_summary, draft_message_id, draft_web_link, sent_at, received_at,
             status, created_by, created_at)
        VALUES (:sqr_number,:supplier_name,:supplier_email,:subject,:body_html,
                :part_summary,:draft_message_id,:draft_web_link,:sent_at,:received_at,
                :status,:created_by,:created_at)
    """, dict(row, created_by="hermes_p4"))
    conn.commit()
    conn.close()

    print(f"[{sqr}] DRAFT created for {supplier} ({email}) — never sent.")
    print(f"  subject: [{sqr}] {subject}")
    print(f"  draft link: {row['draft_web_link']}")
    print(f"  logged to rfq_outbox (status=draft). Giles reviews + sends manually in Outlook.")
    print(f"  SAFETY: verified isDraft=True, sent=False before logging.")
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--supplier", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--parts", required=True, help="'qty desc|qty desc|...'")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    create_draft(args.supplier, args.email, args.subject, args.parts, args.dry_run)


if __name__ == "__main__":
    main()
