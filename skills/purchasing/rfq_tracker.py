#!/usr/bin/env python3
"""
rfq_tracker.py — sent/received tracking for the RFQ outbox (Stage P4).

Companion to create_rfq_draft.py. Tracks SQR sent-vs-received and reports counts.

Functions:
    mark_received(sqr_number)  — mark an outbound RFQ's SQR as "received" once a quote
                                 email for it arrives (call from the inbound matcher).
    mark_sent(sqr_number)      — set sent_at when Giles actually sends the draft (or
                                 a human marks the PO/order placed).
    status_report()            — print sent/received counts per supplier + per SQR.

Safe / idempotent writes only (never deletes, never sends email).
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
sys.path.insert(0, str(WS / "lib"))
try:
    from path_config import p as _p
    DEFAULT_DB_PATH = _p("db.ops")
except Exception:
    DEFAULT_DB_PATH = str(WS / "db" / "ops.db")

CONN = None


def _c():
    global CONN
    if CONN is None:
        CONN = sqlite3.connect(DEFAULT_DB_PATH)
        CONN.row_factory = sqlite3.Row
        CONN.execute("""
            CREATE TABLE IF NOT EXISTS rfq_outbox (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                sqr_number       TEXT UNIQUE,
                supplier_name    TEXT, supplier_email TEXT, subject TEXT,
                body_html TEXT, part_summary TEXT, draft_message_id TEXT,
                draft_web_link TEXT, sent_at TEXT, received_at TEXT,
                status TEXT DEFAULT 'draft', created_by TEXT, created_at TEXT,
                supplier_quote_ref TEXT, confirmation_number TEXT,
                invoice_number TEXT, quote_sender_email TEXT, quote_sender_name TEXT
            )
        """)
        CONN.commit()
    return CONN


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mark_received(sqr_number: str, note: str = "") -> dict:
    """Mark an outbound SQR as received (a quote email arrived for it)."""
    c = _c()
    row = c.execute("SELECT * FROM rfq_outbox WHERE sqr_number=?", (sqr_number,)).fetchone()
    if not row:
        return {"ok": False, "reason": "SQR not in rfq_outbox",
                "sqr": sqr_number}   # inbound SQR never issued by us — not ours to track
    c.execute("UPDATE rfq_outbox SET received_at=?, status=? WHERE sqr_number=?",
              (_now(), "received", sqr_number))
    c.commit()
    return {"ok": True, "sqr": sqr_number, "status": "received"}


def mark_sent(sqr_number: str) -> dict:
    c = _c()
    row = c.execute("SELECT * FROM rfq_outbox WHERE sqr_number=?", (sqr_number,)).fetchone()
    if not row:
        return {"ok": False, "reason": "SQR not in rfq_outbox", "sqr": sqr_number}
    c.execute("UPDATE rfq_outbox SET sent_at=?, status=? WHERE sqr_number=?",
              (_now(), "sent", sqr_number))
    c.commit()
    return {"ok": True, "sqr": sqr_number, "status": "sent"}


def status_report() -> str:
    c = _c()
    rows = c.execute("SELECT * FROM rfq_outbox ORDER BY id").fetchall()
    if not rows:
        return "rfq_outbox is empty — no outbound RFQs issued yet."
    # Per-supplier sent/received counts
    per = {}
    for r in rows:
        s = r["supplier_name"] or "?"
        e = per.setdefault(s, {"sent": 0, "received": 0, "total": 0})
        e["total"] += 1
        if r["sent_at"] or r["status"] in ("sent", "received"):
            e["sent"] += 1
        if r["received_at"]:
            e["received"] += 1
    lines = [f"RFQ SQR ledger: {len(rows)} outbound RFQ(s)."]
    lines.append("Per supplier — sent / received / total:")
    for s, e in sorted(per.items()):
        lines.append(f"  {s}: sent {e['sent']} / received {e['received']} / total {e['total']}")
    lines.append("Per SQR:")
    for r in rows:
        st = r["status"]
        rc = "received " + (r["received_at"] or "")
        fields = []
        for k, label in [("supplier_quote_ref", "quote_ref"),
                          ("confirmation_number", "confirm"),
                          ("invoice_number", "invoice")]:
            if r[k]:
                fields.append(f"{label}={r[k]}")
        if r["quote_sender_email"]:
            fields.append(f"sender={r['quote_sender_email']}")
        extra = " · " + " · ".join(fields) if fields else ""
        lines.append(f"  {r['sqr_number']} [{st}] {r['supplier_name']}: {rc or 'awaiting quote'}{extra}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mark-received", help="SQR number to mark received")
    ap.add_argument("--mark-sent", help="SQR number to mark sent")
    ap.add_argument("--report", action="store_true", help="Print sent/received status")
    args = ap.parse_args()
    if args.mark_received:
        print(mark_received(args.mark_received))
    elif args.mark_sent:
        print(mark_sent(args.mark_sent))
    elif args.report:
        print(status_report())
    else:
        print(status_report())


if __name__ == "__main__":
    main()
