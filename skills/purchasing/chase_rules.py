"""
chase_rules.py — the three chase rules, as PURE ops.db reads.

Rule B: outbound Sent Item, no inbound reply in the same conversation after
        3 working days (default). Internal recipients are filtered out.
Rule C: inbound supplier order-confirmation (Confirmation/Confirm category, or
        document_type='order_confirmation') with a supplier order ref but NO PO
        captured -> "order placed online, ref = X, no PO field".
Rule A: overdue un-confirmed orders (reuses overdue_order_chase.fetch_overdue's
        live-Asana gate). Recipient resolution: (1) Sent thread by PO, (2)
        supplier_contacts, (3) portal match by supplier + Confirmation category +
        supplier order ref.

Nothing here touches Graph or Asana directly; rule A's Asana live-gate is the
existing read helper inside overdue_order_chase.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta

# Reuse the proven helpers (PO variants, working-day maths, supplier_contacts
# lookup, and the overdue-order query with its live-Asana gate) instead of
# re-implementing them.
from overdue_order_chase import (
    add_working_days,
    fetch_overdue,
    po_variants,
    norm_key,
    supplier_email_for,
    _ENGLISH_BANK_HOLIDAYS_2026 as _HOLIDAYS,
)

INTERNAL_DOMAINS = {"sesameaccess.com", "sesame-access.com"}

# Category names that mark an inbound message as an order confirmation. The
# category is being renamed from "Confirm" (legacy) to "Confirmation"; both count.
CONFIRM_CATEGORIES = ("confirmation", "confirm")

# Rule B: only surface outbound no-reply within this many days (older = dead thread,
# and also keeps us safely inside the fetch backfill window so no reply is missed).
# 21d matches the design-note probe window; it is a judgement call, easily changed.
RULE_B_MAX_AGE_DAYS = 21
RULE_B_MIN_WORKING_DAYS = 3

# Outbound that is a calendar/meeting response, not a real enquiry — never chase these.
_MEETING_RESPONSE_RE = re.compile(r"^(accepted|declined|tentative)\s*:", re.I)

# Supplier order-ref patterns for the light fallback extractor (email_documents is
# the authoritative source; this is only used for category-only messages).
_GENERIC_REF_WORDS = {
    "confirmed", "confirmation", "acknowledgement", "acknowledgment", "acknowledged",
    "received", "instruction", "order", "orders", "delivery", "delivered", "note",
    "notes", "thank", "contact", "contacting", "support", "sales", "placed",
    "on", "its", "way", "from",
}
_REF_PATTERNS = [
    re.compile(r"\b(\d{3}-\d{7}-\d{7})\b"),                       # Amazon 203-4900256-4959544
    re.compile(r"\b(\d{2}-\d{5}-\d{5})\b"),                       # eBay 11-15176-75513
    re.compile(r"\border\s*#?\s*([A-Z0-9][A-Z0-9\-/#]{2,30})\s+(?:confirmed|placed|received)", re.I),
    re.compile(r"\b(?:order|orders)\s+([A-Z0-9][A-Z0-9\-/#]{4,30})\s+confirmed\b", re.I),
    re.compile(r"\bfrom\s+([A-Z]{1,3}\d{4,8})\b", re.I),          # e-Acknowledgement from O045015
    re.compile(r"\backnowledg?e?ment\s+(\d{4,})\b", re.I),        # Sales order acknowledgement 202748
    re.compile(r"\bconfirmation\s+([A-Z0-9][A-Z0-9\-/#]{3,30})\b", re.I),
    re.compile(r"\bref(?:erence)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/#]{3,30})\b", re.I),
]


def _clean_ref(raw: str) -> str:
    raw = (raw or "").strip().strip(" -/,#:")
    if not raw or len(raw) < 2:
        return ""
    if raw.lower() in _GENERIC_REF_WORDS:
        return ""
    return raw


def _extract_ref_fallback(subject: str, body: str) -> str:
    text = f"{subject or ''}\n{body or ''}"
    for pat in _REF_PATTERNS:
        m = pat.search(text)
        if m:
            ref = _clean_ref(m.group(1))
            if ref:
                return ref
    return ""


def _internal(email: str) -> bool:
    d = (email or "").split("@")[-1].lower()
    return d in INTERNAL_DOMAINS


def _parse_dt(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None


def working_days_since(iso: str, today: date | None = None) -> int | None:
    """Number of *working* days (Mon-Fri, minus bank holidays) elapsed since a date.

    Returns None if unparseable. 0 = same working day."""
    dt = _parse_dt(iso)
    if dt is None:
        return None
    d = dt.date()
    today = today or date.today()
    if today <= d:
        return 0
    # count working days strictly between d and today
    n = 0
    cur = d
    while cur < today:
        cur += timedelta(days=1)
        if cur.weekday() < 5 and cur.isoformat() not in _HOLIDAYS:
            n += 1
    return n


# ── Rule B ──────────────────────────────────────────────────────────────────
def rule_b(conn: sqlite3.Connection, min_working_days: int = RULE_B_MIN_WORKING_DAYS,
           max_age_days: int = RULE_B_MAX_AGE_DAYS) -> list[dict]:
    """Outbound enquiries with no inbound reply after `min_working_days` working days.

    One hit per conversation (deduped to the LATEST outbound send). Filters:
    internal-only recipients are skipped; only threads whose latest outbound is within
    the last `max_age_days` are considered (older = dead thread, and also keeps us
    safely inside the fetch backfill window so no reply is missed)."""
    rows = conn.execute(
        """
        SELECT conversation_id, subject, received_at, sent_at, to_recipients, web_link
        FROM email_touchpoints
        WHERE direction='outbound'
        ORDER BY COALESCE(received_at, sent_at) DESC
        """
    ).fetchall()

    today = date.today()
    seen: set[str] = set()
    hits: list[dict] = []
    for r in rows:
        conv = r["conversation_id"]
        if not conv or conv in seen:
            continue
        seen.add(conv)

        to = json.loads(r["to_recipients"] or "[]")
        external = [a for a in to if not _internal(a)]
        if not external:
            continue  # internal chatter (all recipients @sesameaccess*)

        sent_at = r["received_at"] or r["sent_at"]
        dt = _parse_dt(sent_at)
        if dt is None:
            continue
        if _MEETING_RESPONSE_RE.match(r["subject"] or ""):
            continue  # calendar response, not an enquiry
        if (today - dt.date()).days > max_age_days:
            continue
        wd = working_days_since(sent_at, today)
        if wd is None or wd < min_working_days:
            continue

        # any EXTERNAL inbound in this conversation AFTER the latest outbound send?
        reply = conn.execute(
            """
            SELECT 1 FROM email_touchpoints
            WHERE conversation_id = ? AND direction = 'inbound' AND is_internal = 0
              AND (received_at > ? OR sent_at > ?)
            LIMIT 1
            """,
            (conv, sent_at, sent_at),
        ).fetchone()
        if reply:
            continue
        hits.append({
            "subject": r["subject"] or "",
            "to": ", ".join(external),
            "sent_at": sent_at,
            "working_days_elapsed": wd,
            "web_link": r["web_link"] or "",
        })
    hits.sort(key=lambda h: h["working_days_elapsed"], reverse=True)
    return hits


# ── Rule C ──────────────────────────────────────────────────────────────────
def _extract_ref_fallback(subject: str, body: str) -> str:
    text = f"{subject or ''}\n{body or ''}"
    for pat in _REF_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip(" -/")
    return ""


def _has_po_match(conn: sqlite3.Connection, po_number: str | None,
                  subject: str, body: str, ref: str) -> bool:
    """True if a PO is already captured/matched for this confirmation."""
    # 1. parser already mapped a PO that exists as a procurement task
    if po_number:
        r = conn.execute(
            "SELECT 1 FROM procurement_tasks WHERE po_number = ? LIMIT 1",
            (po_number,),
        ).fetchone()
        if r:
            return True
    # 2. our PO number appears literally in subject/body (supplier quoted our PO)
    text = f"{subject or ''}\n{body or ''}"
    for m in re.finditer(r"\b(2\d{4})[-.]?(\d{3,6})?\b", text):
        p = m.group(0)
        r = conn.execute(
            "SELECT 1 FROM procurement_tasks WHERE po_number = ? LIMIT 1", (p,)
        ).fetchone()
        if r:
            return True
    return False


def rule_c(conn: sqlite3.Connection) -> list[dict]:
    """Inbound supplier order confirmations with a supplier ref but NO captured PO."""
    # Source 1 (cache): inbound messages categorised Confirmation/Confirm.
    # Source 2 (email_documents): the parser classified an order_confirmation.
    # Merge on Graph message id and dedupe.
    cats = conn.execute(
        """
        SELECT message_id, internet_message_id, subject, sender_email, sender_domain,
               body_preview, received_at, web_link, categories
        FROM email_touchpoints
        WHERE direction = 'inbound' AND is_internal = 0
        """
    ).fetchall()

    confirm_ids: dict[str, dict] = {}
    for c in cats:
        cset = [x.lower() for x in (json.loads(c["categories"] or "[]") or [])]
        if any(k in cset for k in CONFIRM_CATEGORIES):
            confirm_ids[c["message_id"]] = {
                "subject": c["subject"], "sender_email": c["sender_email"],
                "sender_domain": c["sender_domain"], "body": c["body_preview"],
                "received_at": c["received_at"], "web_link": c["web_link"],
                "supplier_ref": "", "po_number": None, "resolved_supplier": None,
                "document_type": None, "source": "category",
            }

    docs = conn.execute(
        """
        SELECT email_message_id, internet_message_id, subject, sender_email,
               sender_domain, summary, received_at, supplier_order_ref,
               po_number, resolved_supplier, document_type
        FROM email_documents
        WHERE document_type = 'order_confirmation'
        """
    ).fetchall()
    for d in docs:
        key = d["email_message_id"] or d["internet_message_id"]
        if key in confirm_ids:
            confirm_ids[key].update({
                "supplier_ref": d["supplier_order_ref"] or confirm_ids[key]["supplier_ref"],
                "po_number": d["po_number"] or confirm_ids[key]["po_number"],
                "resolved_supplier": d["resolved_supplier"],
                "document_type": d["document_type"],
                "source": confirm_ids[key]["source"] + "+parser",
            })
        else:
            confirm_ids[key] = {
                "subject": d["subject"], "sender_email": d["sender_email"],
                "sender_domain": d["sender_domain"],
                "body": d["summary"] or "",
                "received_at": d["received_at"],
                "web_link": "",
                "supplier_ref": d["supplier_order_ref"] or "",
                "po_number": d["po_number"],
                "resolved_supplier": d["resolved_supplier"],
                "document_type": d["document_type"],
                "source": "parser",
            }

    hits: list[dict] = []
    for key, c in confirm_ids.items():
        ref = c["supplier_ref"] or _extract_ref_fallback(c["subject"], c["body"])
        if not ref:
            continue  # nothing to capture
        if _has_po_match(conn, c["po_number"], c["subject"], c["body"], ref):
            continue  # already mapped -> not a gap
        hits.append({
            "subject": c["subject"] or "",
            "sender_email": c["sender_email"] or "",
            "supplier": c["resolved_supplier"] or c["sender_domain"] or "",
            "supplier_ref": ref,
            "received_at": c["received_at"] or "",
            "web_link": c["web_link"] or "",
            "source": c["source"],
        })
    hits.sort(key=lambda h: h["received_at"], reverse=True)
    return hits


# ── Rule A ──────────────────────────────────────────────────────────────────
def _sent_thread_by_po(conn: sqlite3.Connection, po: str) -> dict | None:
    """(1) Find an outbound Sent Item whose subject/body contains our PO number."""
    variants = po_variants(po)
    rows = conn.execute(
        """
        SELECT subject, body_preview, to_recipients, received_at, web_link
        FROM email_touchpoints
        WHERE direction = 'outbound'
        ORDER BY received_at DESC
        """
    ).fetchall()
    for r in rows:
        text = f"{r['subject'] or ''}\n{r['body_preview'] or ''}".lower()
        for v in variants:
            if v.lower() in text:
                to = json.loads(r["to_recipients"] or "[]")
                return {
                    "to_email": (to[0] if to else ""),
                    "subject": r["subject"],
                    "web_link": r["web_link"],
                    "certainty": "high" if v.lower() in (r["subject"] or "").lower() else "medium",
                }
    return None


def _portal_match(conn: sqlite3.Connection, vendor: str) -> dict | None:
    """(3) Find an inbound confirmation from this supplier with a supplier order ref."""
    if not vendor or not vendor.strip():
        return None
    v = vendor.strip().lower()
    # token-based name match against email_documents resolved_supplier + sender email
    tokens = re.findall(r"[a-z0-9]+", v)
    if not tokens:
        return None
    head = " ".join(tokens[:3])
    rows = conn.execute(
        """
        SELECT supplier_order_ref, resolved_supplier, sender_email, sender_domain,
               subject, received_at
        FROM email_documents
        WHERE supplier_order_ref IS NOT NULL AND supplier_order_ref != ''
          AND (lower(COALESCE(resolved_supplier,'')) LIKE ? OR lower(COALESCE(sender_email,'')) LIKE ?)
        ORDER BY received_at DESC
        """,
        (f"%{head}%", f"%{head}%"),
    ).fetchall()
    for r in rows:
        return {
            "supplier_ref": r["supplier_order_ref"],
            "supplier": r["resolved_supplier"] or r["sender_email"] or "",
            "subject": r["subject"],
            "received_at": r["received_at"],
        }
    return None


def rule_a(conn: sqlite3.Connection) -> tuple[list[dict], str]:
    """Overdue un-confirmed orders with recipient resolution (3 steps)."""
    overdue = fetch_overdue(filter_live=True)  # includes live-Asana gate
    from overdue_order_chase import _last_gate_note
    gate_note = _last_gate_note

    out: list[dict] = []
    for o in overdue:
        po = o.get("po_number") or ""
        vendor = (o.get("vendor_name") or o.get("vendor_name_normalised") or "").strip()
        if not po and not vendor:
            continue  # unidentifiable row — nothing to chase or resolve
        recipient = ""
        method = "none"
        ref = ""
        # (1) sent thread by PO
        m = _sent_thread_by_po(conn, po)
        if m and m["to_email"]:
            recipient = m["to_email"]
            method = f"sent-thread ({m['certainty']})"
        # (2) supplier_contacts
        if not recipient:
            e = supplier_email_for(vendor, o.get("vendor_name_normalised"))
            if e:
                recipient = e
                method = "supplier_contacts"
        # (3) portal match
        if not recipient:
            pm = _portal_match(conn, vendor)
            if pm:
                ref = pm["supplier_ref"]
                method = f"portal-match ref={ref}"
        out.append({
            "po": po,
            "vendor": vendor,
            "due": (o.get("due_date") or "")[:10],
            "days_overdue": o.get("days_overdue"),
            "recipient": recipient,
            "method": method,
            "supplier_ref": ref,
            "asana_gid": o.get("asana_task_gid"),
            "thread_link": (m or {}).get("web_link", "") if method.startswith("sent-thread") else "",
        })
    out.sort(key=lambda x: -x["days_overdue"])
    return out, gate_note
