"""
03_proposal_engine.py — turn parsed emails into reviewable proposals.

Rebuilt 2026-09-18. For every parsed email/call it produces ONE queue item with:
  * triage      — supplier_doc | supplier_call | non_supplier_call | noise | unknown
  * a match     — via matcher.py (supplier directory + learned rules + closed-task guard),
                  with confidence, basis, reasons and alternatives
  * proposed field changes, guarded: never "Delivery Date CONFIRMED" on an
    arrived/closed task; never overwrite an existing price; supplier quote/order
    refs are recorded on the task description so they can be tracked (Giles 2026-08-24)
  * a clean, plain-text Asana comment written for a reader who has NOT seen the
    email: what it says, what Giles must do, the key facts, then the audit line.
    No HTML mention tags (they post as literal text — see execute_requests.py).
  * a suggested decision (A / R / CHECK) with the reason, so Giles can bulk-accept
    the obvious rows and spend his attention on the CHECKs.

Rules kept from the estate: every email goes to the sheet (no pre-sifting);
no Asana writes from here; ops.db is written ONLY through db_writer.py
(email_documents + email_line_items — the local memory the matcher reads back).
Input : data/parsed_insights.json     Output: data/pending_approval_queue.json
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[2] / "lib"))

from gateway_common import (DATA, RuleStore, SupplierDirectory, fmt_date,  # noqa: E402
                            load_json, save_json, shadow_mode)
from matcher import ARRIVED_STATUSES, CLOSED_STATUSES, Matcher  # noqa: E402

PARSED_INSIGHTS_PATH = DATA / "parsed_insights.json"
PENDING_APPROVAL_QUEUE_PATH = DATA / "pending_approval_queue.json"

DOC_LABEL = {"quote": "Quote", "order_confirmation": "Order confirmation", "invoice": "Invoice",
             "dispatch_note": "Dispatch note", "update": "Update", "call_notes": "Call notes", "other": "Email"}

CONFIRMED_STATUS = "🚚On Order - Delivery Date CONFIRMED"

# Giles 2026-09-24: the task DESCRIPTION belongs to the Asana AI teammate (it compiles the quote summary
# there and drafts the outbound email). Hermes writes COMMENTS and FIELDS only; supplier refs, lead time
# and price context travel in the comment's Key facts line. Set True to restore the old description notes.
DESCRIPTION_NOTES = False
NOREPLY = re.compile(r"^(no-?reply|do-?not-?reply|donotreply|mailer-daemon|notifications?|alerts?|bounce)", re.I)
_SUPPLIERS = None   # set in main() so contact_line can look up the supplier's email on file


def extract_domain_from_sender(sender_email):
    m = re.search(r"@([a-zA-Z0-9.-]+)", sender_email or "")
    return m.group(1).lower() if m else None


def extract_email_and_name(sender):
    email, name = None, ""
    m = re.search(r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+)", sender or "")
    if m:
        email = m.group(1)
        pre = (sender or "").split("<")[0].strip()
        if pre and "@" not in pre:
            name = pre
    return email, name


def normalise_ref(value):
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


# --------------------------------------------------------------- SQR ledger hook (kept)
SQR_DOC_TYPES = {"quote": "quote_ref", "order_confirmation": "confirmation", "invoice": "invoice"}


def _hook_quote_ledger(item, insights, sender_email):
    try:
        doc_type = insights.get("document_type")
        if doc_type not in SQR_DOC_TYPES:
            return ""
        from match_quote_to_sqr import process_quote
        ref = insights.get("supplier_quote_ref") or insights.get("supplier_order_ref") or insights.get("po_number") or ""
        _, sender_name = extract_email_and_name(item.get("sender") or sender_email or "")
        result = process_quote(
            sender_email=sender_email or "", sender_name=sender_name or item.get("sender_name") or "",
            quote_ref=(ref if doc_type == "quote" else ""),
            confirmation=(ref if doc_type == "order_confirmation" else ""),
            invoice=(insights.get("supplier_invoice_ref") or ref if doc_type == "invoice" else ""),
            explicit_sqr=insights.get("sqr_number") or "")
        return f"[SQR-ledger] {result['note']}" if result.get("matched") else ""
    except Exception as exc:  # hook must never break the flow
        return f"[SQR-ledger] hook error: {exc}"


# --------------------------------------------------------------- proposals
def _money(v):
    try:
        return f"£{float(v):,.2f}"
    except (TypeError, ValueError):
        return None


def propose_changes(insights: dict, task: dict | None, doc_type: str) -> tuple[list[str], list[str]]:
    """→ (action_lines for SAP/executor, notes for the human). Action phrases are the exact
    strings execute_requests.parse_actions understands — do not reword them."""
    actions, notes = [], []
    if not task:
        return actions, notes
    status = (task.get("task_status") or "").strip()
    closed = bool(task.get("task_completed")) or status in {s.strip() for s in CLOSED_STATUSES}
    arrived = status in {s.strip() for s in ARRIVED_STATUSES}

    # delivery date + status
    dd = insights.get("delivery_date")
    due = task.get("task_due") or ""
    if dd and doc_type in ("order_confirmation", "dispatch_note", "update", "call_notes"):
        if closed or arrived:
            notes.append(f"Task is already {status or 'closed'} — no date/status change proposed (delivery {fmt_date(dd)} noted)")
        else:
            try:
                d_new = datetime.strptime(dd, "%Y-%m-%d").date()
                d_cur = datetime.strptime(due[:10], "%Y-%m-%d").date() if due else None
            except ValueError:
                d_new, d_cur = None, None
            if d_new and d_cur and d_cur == d_new:
                notes.append(f"Task due date already {fmt_date(dd)}")
            elif d_new and d_cur and d_cur > d_new and doc_type == "dispatch_note":
                notes.append(f"Delivery {fmt_date(dd)} is earlier than task due {fmt_date(due)} — left as is")
            else:
                actions.append(f"Update delivery date to {dd}")
            if status != CONFIRMED_STATUS.strip() and doc_type in ("order_confirmation", "dispatch_note"):
                actions.append("Update ORDER Status to 'On Order - Delivery Date Confirmed'")
            elif status == CONFIRMED_STATUS.strip():
                notes.append("Status already Delivery Date CONFIRMED")

    # price
    price = insights.get("total_price")
    if price is not None and doc_type in ("quote", "order_confirmation", "invoice"):
        cur = task.get("task_price_ex_vat")
        label = DOC_LABEL.get(doc_type, "Price")
        line = f"{label}: {_money(price)} ex VAT"
        if insights.get("carriage") is not None:
            line += f" + carriage {_money(insights['carriage'])}"
        if cur is not None and abs(float(cur) - float(price)) < 0.01:
            notes.append(f"Price {_money(price)} already on task")
        elif cur is not None:
            notes.append(f"Task already has Price Ex VAT {_money(cur)} — supplier states {_money(price)}; field not overwritten")
            if DESCRIPTION_NOTES:
                actions.append(f"Note added to description: {line} ({fmt_date(insights.get('_received', ''))})")
        else:
            actions.append(f"Update 'Price Ex VAT' to £{float(price):.2f}")  # no thousands separator: the executor regex reads it
            if DESCRIPTION_NOTES:
                actions.append(f"Note added to description: {line} ({fmt_date(insights.get('_received', ''))})")

    # supplier references — tracked on the description (Giles 2026-08-24)
    refs = []
    if insights.get("supplier_quote_ref"):
        refs.append(f"Supplier quote ref {insights['supplier_quote_ref']}")
    if insights.get("supplier_order_ref"):
        refs.append(f"Supplier order ref {insights['supplier_order_ref']}")
    if insights.get("supplier_invoice_ref"):
        refs.append(f"Supplier invoice {insights['supplier_invoice_ref']}")
    if insights.get("lead_time"):
        refs.append(f"Lead time {insights['lead_time']}")
    if refs and DESCRIPTION_NOTES:
        actions.append("Note added to description: " + "; ".join(refs))

    if insights.get("has_issues") and insights.get("issue_summary"):
        actions.append(f"Flag issue: {insights['issue_summary']}")
    return actions, notes


# --------------------------------------------------------------- comment
def key_facts(insights: dict, match: dict) -> list[str]:
    f = []
    if insights.get("delivery_date"):
        f.append(f"delivery {fmt_date(insights['delivery_date'])}")
    if insights.get("lead_time"):
        f.append(f"lead time {insights['lead_time']}")
    if insights.get("total_price") is not None:
        f.append(f"{_money(insights['total_price'])} ex VAT" + (f" + carriage {_money(insights['carriage'])}" if insights.get("carriage") is not None else ""))
    for k, lab in (("supplier_quote_ref", "their quote"), ("supplier_order_ref", "their order"), ("supplier_invoice_ref", "invoice")):
        if insights.get(k):
            f.append(f"{lab} {insights[k]}")
    if insights.get("po_number"):
        f.append(f"PO {insights['po_number']}")
    if insights.get("sqr_number"):
        f.append(insights["sqr_number"])
    for j in insights.get("job_numbers") or []:
        if not any(j in x for x in f):
            f.append(f"job {j}")
    return f


def _supplier_email(match: dict | None) -> str:
    if not match or not match.get("supplier_gid") or _SUPPLIERS is None:
        return ""
    s = _SUPPLIERS.by_gid.get(str(match["supplier_gid"])) or {}
    return (s.get("email") or "").split(",")[0].strip()


def contact_line(item: dict, insights: dict, match: dict | None = None) -> str:
    """ONE line telling the Asana AI teammate who to write back to (Giles 2026-09-24).
       email  → 'Contact: Tracey Spear <TraceySpear@appliedautomation.co.uk>'
       noreply sender → the supplier's email on file instead, saying so
       call   → 'Contact: Amanda · 01388 770360 · sales@stephensongobin.com (on file)'"""
    call = insights.get("call") or {}
    on_file = _supplier_email(match)
    if call:
        who = insights.get("contact_name") or ""
        phones = ", ".join(f"{p[:5]} {p[5:]}" if len(p) == 11 else p for p in (call.get("phones") or []))
        phones = phones or (f"ext {call['extension']}" if call.get("extension") else "")
        bits = [b for b in (who, phones, f"{on_file} (on file)" if on_file else "") if b]
        return "Contact: " + " · ".join(bits) if bits else ""
    email, name = extract_email_and_name(item.get("sender") or "")
    name = insights.get("requested_by") or item.get("sender_name") or name or ""
    if email and NOREPLY.match(email.split("@")[0]):
        return f"Contact: {on_file} (on file — sender was {email})" if on_file else f"Contact: none usable (sender was {email})"
    if name and email and name.lower() != email.lower():
        return f"Contact: {name} <{email}>"
    return f"Contact: {email}" if email else ""


def build_comment(item: dict, insights: dict, match: dict, actions: list[str], notes: list[str]) -> str:
    doc_type = insights.get("document_type") or "other"
    received = item.get("receivedDateTime") or ""
    supplier = match.get("supplier_name") or insights.get("company_name") or (item.get("sender_domain") or "unknown sender")
    who = insights.get("requested_by") or insights.get("contact_name") or item.get("sender_name") or ""
    call = insights.get("call") or {}

    if doc_type == "call_notes":
        when = f"{call.get('call_date')} {call.get('call_time')}".strip() if call.get("call_date") else fmt_date(received)
        contact = call.get("contact_label") or who or (", ".join(call.get("phones") or []) or "unknown number")
        head = f"Call with {contact}" + (f" ({supplier})" if supplier and supplier.lower() not in contact.lower() else "") + f" — {when}"
    else:
        head = f"{DOC_LABEL.get(doc_type, 'Email')} from {supplier}" + (f" ({who})" if who else "") + f" — {fmt_date(received)}"

    lines = [head]
    if doc_type != "call_notes":
        lines.append(f"Subject: {item.get('subject', '')}")
    cl = contact_line(item, insights, match)
    if cl:
        lines.append(cl)
    lines.append("")
    lines.append((insights.get("summary") or "").strip() or "(no summary extracted)")
    if doc_type == "call_notes" and insights.get("key_inference") and "nothing actionable" not in insights["key_inference"].lower():
        lines.append(f"Key inference: {insights['key_inference']}")
    items = [a for a in (insights.get("action_items") or []) if a]
    if items:
        lines.append("")
        lines.append("Action for Giles:")
        lines += [f"- {a}" for a in items]
    elif insights.get("action_required"):
        lines.append("")
        lines.append("Action for Giles: reply needed (see summary)")
    else:
        lines.append("")
        lines.append("Action for Giles: none — information only")
    if insights.get("has_issues") and insights.get("issue_summary"):
        lines.append(f"Issue: {insights['issue_summary']}")
    facts = key_facts(insights, match)
    if facts:
        lines.append("Key facts: " + " · ".join(facts))
    if actions:
        lines.append("Proposed: " + "; ".join(a.replace("Note added to description: ", "note ") for a in actions))
    elif notes:
        lines.append("Proposed: no field change (" + "; ".join(notes) + ")")
    else:
        lines.append("Proposed: no field change")
    lines.append("")
    basis = match.get("matched_by") or "no match"
    lines.append(f"Email gateway · matched via {basis} · received {received[:16].replace('T', ' ')} UTC")
    return "\n".join(lines)


def build_unmatched_comment(item: dict, insights: dict, match: dict, triage: str) -> str:
    doc_type = insights.get("document_type") or "other"
    supplier = match.get("supplier_name") or insights.get("company_name") or item.get("sender_domain") or "unknown"
    lines = [f"NO AUTO-MATCH — {DOC_LABEL.get(doc_type, 'Email')} from {supplier} — {fmt_date(item.get('receivedDateTime', ''))}",
             f"Subject: {item.get('subject', '')}"]
    cl = contact_line(item, insights, match)
    if cl:
        lines.append(cl)
    lines.append("")
    lines.append((insights.get("summary") or "").strip() or "(no summary extracted)")
    if doc_type == "call_notes" and insights.get("key_inference"):
        lines.append(f"Key inference: {insights['key_inference']}")
    items = [a for a in (insights.get("action_items") or []) if a]
    if items:
        lines += ["", "Action for Giles:"] + [f"- {a}" for a in items]
    facts = key_facts(insights, match)
    if facts:
        lines.append("Key facts: " + " · ".join(facts))
    alts = match.get("alternatives") or []
    if alts:
        lines.append("Could be:")
        lines += [f"- {a['name'][:70]} [{(a.get('status') or 'no status').strip()}] {a['url']}" for a in alts]
    lines.append("")
    lines.append(f"Email gateway · {match.get('matched_by') or 'no task found'}")
    return "\n".join(lines)


# --------------------------------------------------------------- triage + suggestion
def triage_item(item: dict, insights: dict, rules: RuleStore) -> tuple[str, str]:
    """→ (triage, reason)."""
    domain = item.get("sender_domain") or ""
    subject = item.get("subject") or ""
    why = rules.noise_reason(domain, subject)
    if why:
        return "noise", why
    if insights.get("document_type") == "call_notes":
        call = insights.get("call") or {}
        ctx = rules.call_context(call.get("phones"), call.get("contact_label") or "")
        if ctx and ctx.get("action") == "drop":
            return "non_supplier_call", f"Giles: {ctx.get('note', 'drop')[:80]}"
        if call.get("is_internal"):
            return "non_supplier_call", "internal call (Sesame extension / colleague)"
        if insights.get("is_noise"):
            return "non_supplier_call", insights.get("noise_reason") or "not a supplier call"
        has_substance = any(insights.get(k) for k in ("po_number", "sqr_number", "delivery_date", "total_price",
                                                      "supplier_order_ref", "supplier_quote_ref", "action_items", "has_issues"))
        if insights.get("worth_tracking") is False and not has_substance and not ctx:
            return "non_supplier_call", "short call, nothing actionable"
        return "supplier_call", "call notes" + (f" · Giles: {ctx['note'][:60]}" if ctx else "")
    if insights.get("is_noise"):
        return "noise", insights.get("noise_reason") or "noise"
    if insights.get("document_type") in ("quote", "order_confirmation", "invoice", "dispatch_note", "update"):
        return "supplier_doc", insights["document_type"]
    return "unknown", "unclassified"


def suggest(triage: str, match: dict, actions: list[str], insights: dict) -> tuple[str, str]:
    """→ (suggested decision A|R|CHECK, reason). Giles still decides; this orders his attention."""
    if triage == "noise":
        return "R", f"noise — {insights.get('noise_reason') or 'tracking/marketing'}"
    if triage == "non_supplier_call":
        return "R", "not a supplier call"
    conf = match.get("confidence")
    if match.get("task_gid") and conf == "HIGH":
        if insights.get("has_issues") or insights.get("action_required"):
            return "A", "matched (high) — comment carries an action/issue for you"
        return "A", "matched (high)" + (" — field changes proposed" if actions else " — comment only")
    if match.get("task_gid") and conf == "MEDIUM":
        return "CHECK", f"probable match — {match.get('matched_by')}; confirm the task"
    if match.get("alternatives"):
        return "CHECK", f"{len(match['alternatives'])} candidate task(s) — pick one (paste its link into Asana Link, decision M)"
    if triage == "supplier_call":
        return "CHECK", "supplier call but no open order found — paste the task link, or R if nothing to record"
    return "CHECK", "no task found — paste the task link (M) or reject (R); a Feedback note teaches the gateway"


# --------------------------------------------------------------- ops.db persistence
PARSER_VERSION = "2026-09-18.1"


def _json(v):
    import json
    return json.dumps(v, ensure_ascii=False) if v not in (None, "", []) else None


def persist_to_db(queue: list[dict]) -> str:
    """Write every queue item to email_documents (+ line items) via db_writer — the sole
    ops.db write gateway. Best effort: a DB problem is reported, never blocks the sheet."""
    try:
        import db_writer
    except Exception as e:
        return f"ops.db not written ({type(e).__name__}: {e})"
    docs, n_lines = [], 0
    from gateway_common import now_utc, safe_id, ARCHIVE_DIR
    now = now_utc()
    for q in queue:
        ins = q.get("insights") or {}
        call = ins.get("call") or {}
        docs.append({
            "email_message_id": q.get("email_id"), "internet_message_id": q.get("internet_message_id"),
            "conversation_id": q.get("conversation_id"), "received_at": q.get("received_at"), "folder": q.get("folder"),
            "sender_email": (extract_email_and_name(q.get("sender") or "")[0] or "").lower() or None,
            "sender_name": q.get("sender_name"), "sender_domain": q.get("sender_domain"), "subject": q.get("subject"),
            "document_type": ins.get("document_type"), "triage": q.get("triage"), "summary": ins.get("summary"),
            "action_required": int(bool(q.get("action_required"))), "action_items": _json(ins.get("action_items")),
            "has_issues": int(bool(ins.get("has_issues"))), "issue_summary": ins.get("issue_summary"),
            "po_number": ins.get("po_number"), "sqr_number": ins.get("sqr_number"), "job_numbers": _json(ins.get("job_numbers")),
            "supplier_quote_ref": ins.get("supplier_quote_ref"), "supplier_order_ref": ins.get("supplier_order_ref"),
            "supplier_invoice_ref": ins.get("supplier_invoice_ref"),
            "total_price_ex_vat": ins.get("total_price"), "carriage_ex_vat": ins.get("carriage"),
            "delivery_date": ins.get("delivery_date"), "lead_time": ins.get("lead_time"),
            "resolved_supplier_gid": q.get("supplier_gid"), "resolved_supplier": q.get("supplier_name") or None,
            "supplier_how": q.get("supplier_how"), "task_vendor_name": q.get("task_vendor"),
            "proposed_task_gid": q.get("asana_task_gid"), "proposed_task_name": q.get("asana_task_name"),
            "match_confidence": q.get("confidence"), "matched_by": q.get("matched_by"),
            "alternatives": _json([{"gid": a.get("gid"), "name": a.get("name"), "status": a.get("status")} for a in q.get("alternatives") or []]),
            "call_contact_label": call.get("contact_label"), "call_phone": ",".join(call.get("phones") or []) or None,
            "call_tasks": _json(call.get("tasks")), "attachment_names": _json(q.get("attachment_names")),
            "archive_path": str(ARCHIVE_DIR / (q.get("received_at") or "")[:7] / f"{safe_id(q.get('email_id'))}.json"),
            "parser_version": PARSER_VERSION, "last_synced_at": now,
        })
    try:
        res = db_writer.upsert_email_documents(docs)
        for q in queue:
            ins = q.get("insights") or {}
            lines = []
            for li in ins.get("line_items") or []:
                if not isinstance(li, dict):
                    continue
                qty, up = li.get("qty"), li.get("unit_price")
                try:
                    total = float(qty) * float(up) if qty not in (None, "") and up not in (None, "") else None
                except (TypeError, ValueError):
                    total = None
                lines.append({"received_at": q.get("received_at"), "document_type": ins.get("document_type"),
                              "supplier_gid": q.get("supplier_gid"), "supplier": q.get("supplier_name") or None,
                              "task_gid": q.get("asana_task_gid"), "po_number": ins.get("po_number"),
                              "part_ref": li.get("part_ref"), "description": li.get("description"),
                              "qty": qty, "unit_price": up, "line_total": total, "notes": li.get("notes"),
                              "last_synced_at": now})
            if lines:
                db_writer.replace_email_line_items(q.get("email_id"), lines)
                n_lines += len(lines)
        return f"ops.db: {res['written']} email_documents written, {res['skipped']} unchanged, {n_lines} line items"
    except Exception as e:
        return f"ops.db write failed ({type(e).__name__}: {e})"


# --------------------------------------------------------------- main
def main():
    data = load_json(PARSED_INSIGHTS_PATH, None)
    if data is None:
        print("No insights found.")
        return
    if not data:
        print("No emails to process.")
        save_json(PENDING_APPROVAL_QUEUE_PATH, [])
        return

    global _SUPPLIERS
    rules = RuleStore()
    suppliers = SupplierDirectory(rules=rules)
    _SUPPLIERS = suppliers
    matcher = Matcher(suppliers=suppliers, rules=rules)
    if not matcher.conn:
        print(f"WARNING: ops.db not readable at {matcher.db_path} — matching will find nothing")

    queue = []
    for item in data:  # ALL emails flow through (Giles 2026-08-13) — never pre-sift
        insights = dict(item.get("insights") or {})
        insights["_received"] = item.get("receivedDateTime") or ""
        sender_email, _ = extract_email_and_name(item.get("sender", "") or "")
        sender_domain = extract_domain_from_sender(item.get("sender", ""))
        item["sender_domain"] = sender_domain
        doc_type = insights.get("document_type") or "other"

        triage, triage_why = triage_item(item, insights, rules)
        if triage in ("noise", "non_supplier_call"):
            match = {"confidence": "NONE", "matched_by": "not attempted (" + triage_why + ")", "alternatives": [], "reasons": []}
        else:
            res = matcher.match(sender_email=sender_email or "", sender_domain=sender_domain or "",
                                subject=item.get("subject") or "", body_text=item.get("body_text") or "",
                                insights=insights, doc_type=doc_type)
            match = res.to_dict()

        ledger_note = _hook_quote_ledger(item, insights, sender_email) if triage == "supplier_doc" else ""
        if ledger_note:
            match["reasons"].append(ledger_note)

        task = match if match.get("task_gid") else None
        actions, notes = propose_changes(insights, task, doc_type)
        if task:
            comment = build_comment(item, insights, match, actions, notes)
            proposed_action = "; ".join([f"({match['matched_by']})"] + actions + [f"Note: {n}" for n in notes]) if (actions or notes) else f"({match['matched_by']}); Comment only"
        elif triage in ("noise", "non_supplier_call"):
            comment = ""
            proposed_action = f"No action — {triage_why}"
        else:
            comment = build_unmatched_comment(item, insights, match, triage)
            proposed_action = "Task not found — " + (match.get("matched_by") or "no candidates") + \
                (f". Doc: {doc_type}. PO: {insights.get('po_number')}. SQR: {insights.get('sqr_number')}. Supplier: {match.get('supplier_name') or sender_domain}")
        suggestion, why = suggest(triage, match, actions, insights)

        queue.append({
            "email_id": item.get("email_id"),
            "internet_message_id": item.get("internet_message_id"),
            "conversation_id": item.get("conversation_id"),
            "subject": item.get("subject"),
            "sender": item.get("sender"),
            "sender_domain": sender_domain,
            "received_at": item.get("receivedDateTime") or "",
            "folder": item.get("folder"),
            "insights": {k: v for k, v in insights.items() if not k.startswith("_")},
            "triage": triage,
            "triage_reason": triage_why,
            "supplier_name": match.get("supplier_name") or "",
            "supplier_gid": match.get("supplier_gid"),
            "supplier_how": match.get("supplier_how"),
            "task_vendor": match.get("task_vendor"),
            "sender_name": item.get("sender_name"),
            "attachment_names": item.get("attachment_names") or [],
            "asana_task_gid": match.get("task_gid"),
            "asana_task_name": match.get("task_name"),
            "asana_task_url": match.get("task_url"),
            "task_status": match.get("task_status"),
            "confidence": match.get("confidence"),
            "matched_by": match.get("matched_by"),
            "match_reasons": match.get("reasons") or [],
            "alternatives": match.get("alternatives") or [],
            "proposed_action": proposed_action,
            "actions": actions,
            "comment_to_push": comment,
            "summary": ((f"[{insights['key_inference']}] " if insights.get("key_inference") else "") + (insights.get("summary") or "")),
            "action_required": bool(insights.get("action_required")) or bool(insights.get("action_items")),
            "action_items": insights.get("action_items") or [],
            "suggested_decision": suggestion,
            "suggested_reason": why,
            "status": "pending_review",
        })

    save_json(PENDING_APPROVAL_QUEUE_PATH, queue)
    print("  " + ("SHADOW: ops.db not written" if shadow_mode() else persist_to_db(queue)))
    counts = {}
    for q in queue:
        counts[q["triage"]] = counts.get(q["triage"], 0) + 1
    print(f"Staged {len(queue)} items for approval. Triage: {counts}. "
          f"Matched: {sum(1 for q in queue if q['asana_task_gid'])}.")


if __name__ == "__main__":
    main()
