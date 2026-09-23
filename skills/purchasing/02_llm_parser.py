"""
02_llm_parser.py — extract what matters from each fetched email.

Rebuilt 2026-09-18. Changes from the original:
  * The LLM sees readable TEXT (HTML stripped, quoted history trimmed), not 70 KB
    of marketing HTML. Cheaper, and nothing important is pushed past the token limit.
  * The schema now captures the CONTEXT Giles was missing: a 2-3 sentence summary,
    what is being asked of Sesame (action_required / action_items), who wrote, the
    supplier's own quote / order / invoice references, carriage, lead time, job
    numbers, and whether the mail is noise.
  * RingCentral call notes are parsed deterministically first (subject label,
    phone, summary / recap / tasks) and the LLM is only asked to classify the call
    and extract references from the notes.
  * Every parse is written into data/email_archive/ next to the raw email, so a
    parser or matcher fix can be re-run over history (python 02_llm_parser.py --reparse 2026-09).

Input : data/raw_purchasing_emails.json   Output: data/parsed_insights.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[3] / ".hermes" / ".env")
sys.path.insert(0, str(Path(__file__).parents[2] / "lib"))
sys.path.insert(0, str(Path(__file__).parent))

from llm import complete  # noqa: E402
from gateway_common import (ARCHIVE_DIR, DATA, archive_parse, extract_refs, html_to_text,  # noqa: E402
                            load_json, parse_ringcentral, save_json, strip_quoted_history)

INPUT_FILE = DATA / "raw_purchasing_emails.json"
OUTPUT_FILE = DATA / "parsed_insights.json"
MAX_BODY_CHARS = 12000     # ~3k tokens of body; PDFs appended after
MAX_PDF_CHARS = 9000

DOC_TYPES = ["quote", "order_confirmation", "invoice", "dispatch_note", "update", "call_notes", "other"]

SYSTEM = """You are the purchasing assistant for Sesame Access Systems (UK lift manufacturer). Giles runs purchasing.
You read ONE inbound supplier email (or a phone-call summary) and return a JSON object. Be literal: extract only what
is present, never invent. Sesame's own references: job numbers are 4 digits starting 1 (e.g. 1595, 1597);
purchase orders are 5 digits starting 2 plus a suffix (e.g. 21615-1597 or 21615-70926); quote requests are SQR-nnnnn.

Return exactly this JSON (no markdown):
{
 "document_type": one of %s,
 "summary": "2-3 plain sentences: what this email says and where things stand. Written for Giles, who has not read the email.",
 "action_required": true/false  (does the sender need something FROM Sesame, or must Giles do something?),
 "action_items": ["each thing Giles / Sesame must do, with any deadline", ...],
 "requested_by": "sender's name if signed, else null",
 "company_name": "the supplier / company the sender represents, as written, else null",
 "po_number": "Sesame PO as written (e.g. 21615-1597 or bare 21615) else null. Reject anything longer than 12 chars.",
 "sqr_number": "SQR-nnnnn else null",
 "job_numbers": ["4-digit Sesame job numbers mentioned, e.g. 1597"],
 "supplier_quote_ref": "the SUPPLIER's own quotation reference else null",
 "supplier_order_ref": "the SUPPLIER's own order / acknowledgement / sales order number else null",
 "supplier_invoice_ref": "invoice number else null",
 "total_price": goods value ex VAT as a number (if only an inc-VAT total is given divide by 1.2), else null,
 "carriage": carriage / delivery charge ex VAT as a number else null,
 "delivery_date": "YYYY-MM-DD estimated delivery or dispatch date (resolve 'tomorrow' etc. from the email date) else null",
 "lead_time": "lead time as written (e.g. '14 weeks') else null",
 "has_issues": true/false (delay, hold, substitution, wrong/unavailable part, price change, query needing an answer),
 "issue_summary": "one sentence if has_issues else null",
 "is_noise": true/false (courier tracking, marketing, newsletters, platform notifications with no purchasing content),
 "noise_reason": "why it is noise else null",
 "line_items": [{"part_ref": str|null, "description": str|null, "qty": int|null, "unit_price": float|null, "notes": str|null}]
}
document_type guide: quote = pricing/availability offered; order_confirmation = supplier acknowledges our PO;
invoice = payment requested; dispatch_note = goods shipped/collected; update = status, lead-time, price or product
change, or a question; call_notes = a phone-call summary; other = anything else.
""" % DOC_TYPES

CALL_SYSTEM = """You are the purchasing assistant for Sesame Access Systems. Below is an AI summary of a phone call Giles had.
Decide whether it is about purchasing/suppliers and extract references. Return exactly this JSON (no markdown):
{
 "is_supplier_call": true/false (a supplier, courier, subcontractor or quote/order discussion; false for IT, utilities, personal, internal Sesame colleagues),
 "company_name": "the external company on the call if identifiable else null",
 "contact_name": "the person spoken to if named else null",
 "summary": "2 sentences for Giles: what was agreed / where it stands",
 "action_items": ["each follow-up Sesame must do, with dates if said"],
 "action_required": true/false,
 "po_number": "Sesame PO mentioned else null", "sqr_number": "SQR-nnnnn else null",
 "job_numbers": ["4-digit Sesame job numbers mentioned"],
 "supplier_quote_ref": str|null, "supplier_order_ref": str|null,
 "delivery_date": "YYYY-MM-DD if a delivery/dispatch date was agreed else null",
 "lead_time": str|null, "total_price": number|null,
 "has_issues": true/false, "issue_summary": str|null
}"""


def _extract_json(raw):
    """Robustly pull a JSON object out of an LLM response (control tokens, fences, preamble)."""
    if not raw:
        return None
    s = raw.strip()
    s = re.sub(r"<\|[^|]*\|>", "", s)
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start:i + 1])
                except Exception:
                    return None
    return None


def _clean(result: dict, email: dict) -> dict:
    """Normalise types so downstream never trips on strings-in-number-fields."""
    out = dict(result or {})
    out["document_type"] = out.get("document_type") if out.get("document_type") in DOC_TYPES else "other"
    for k in ("total_price", "carriage"):
        v = out.get(k)
        if isinstance(v, str):
            v = re.sub(r"[^\d.]", "", v)
            out[k] = float(v) if v else None
    for k in ("action_items", "job_numbers", "line_items"):
        if not isinstance(out.get(k), list):
            out[k] = []
    out["job_numbers"] = [str(j) for j in out["job_numbers"] if re.fullmatch(r"1\d{3}", str(j))]
    po = out.get("po_number")
    if po and (len(str(po)) > 12 or not re.match(r"^2\d{4}", str(po))):
        out["po_number"] = None
    if out.get("sqr_number"):
        m = re.search(r"(\d{4,6})", str(out["sqr_number"]))
        out["sqr_number"] = f"SQR-{m.group(1)}" if m else None
    dd = out.get("delivery_date")
    if dd and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(dd)):
        out["delivery_date"] = None
    for k in ("summary", "issue_summary", "noise_reason", "requested_by", "company_name",
              "supplier_quote_ref", "supplier_order_ref", "supplier_invoice_ref", "lead_time", "contact_name"):
        if out.get(k) in ("", "null", "None"):
            out[k] = None
    # regex safety net: refs the model missed
    rx = extract_refs(email.get("body_text", "")[:6000], subject=email.get("subject", ""))
    if not out.get("sqr_number") and rx["sqr_numbers"]:
        out["sqr_number"] = rx["sqr_numbers"][0]
    if not out.get("po_number") and rx["po_numbers"]:
        out["po_number"] = rx["po_numbers"][0]
    for j in rx["job_numbers"]:
        if j not in out["job_numbers"]:
            out["job_numbers"].append(j)
    return out


def prepare_text(email: dict) -> str:
    """Readable body the LLM (and the matcher) will see."""
    body = email.get("body_text") or html_to_text(email.get("body_content") or email.get("full_text") or "")
    body = strip_quoted_history(body)[:MAX_BODY_CHARS]
    pdf = (email.get("pdf_text") or "")[:MAX_PDF_CHARS]
    if pdf:
        body += "\n\n--- PDF attachment text ---\n" + pdf
    atts = email.get("attachment_names") or []
    if atts:
        body += "\n\n[attachments: " + ", ".join(atts) + "]"
    return body


def parse_one(email: dict) -> dict | None:
    """Returns the insights dict for one raw email, or None if the LLM reply was unusable."""
    email.setdefault("body_text", html_to_text(email.get("body_content") or ""))
    subject = email.get("subject") or ""
    sender = email.get("sender") or ""
    text = prepare_text(email)
    domain = sender.split("@")[-1].lower()

    if domain == "ringcentral.com":
        rc = parse_ringcentral(subject, email["body_text"])
        if rc:
            prompt = (f"Call date: {rc.get('call_date')} {rc.get('call_time')}\nContact label from phonebook: {rc.get('contact_label') or '(none)'}\n"
                      f"Phone: {', '.join(rc.get('phones') or []) or rc.get('extension') or '?'}\n\n"
                      f"SUMMARY:\n{rc.get('summary')}\n\nRECAP:\n{rc.get('recap')}\n\nTASKS:\n" + ("\n".join(rc.get('tasks') or []) or "None"))
            raw = complete(prompt=prompt, system=CALL_SYSTEM, max_tokens=1200, temperature=0.1)
            r = _extract_json(raw) or {}
            r["document_type"] = "call_notes"
            r["is_noise"] = not r.get("is_supplier_call", False) if r else False
            r["noise_reason"] = None if r.get("is_supplier_call") else "call not about a supplier / order"
            r.setdefault("summary", rc.get("summary"))
            r["call"] = rc
            return _clean(r, email)

    prompt = (f"Email date: {email.get('date','')}\nFrom: {sender}\nSubject: {subject}\n"
              f"To: {', '.join(email.get('to') or [])}\n\n{text}")
    raw = complete(prompt=prompt, system=SYSTEM, max_tokens=2048, temperature=0.1)
    r = _extract_json(raw)
    if r is None:
        return None
    return _clean(r, email)


def parse_emails(emails: list[dict] | None = None, output_file: Path = OUTPUT_FILE) -> list[dict]:
    if emails is None:
        emails = load_json(INPUT_FILE, None)
        if emails is None:
            print(f"Error reading {INPUT_FILE}")
            return []
    if not emails:
        print("No raw emails to process.")
        save_json(output_file, [])
        return []

    insights, failed = [], []
    for email in emails:
        safe_subj = str(email.get("subject", ""))[:70].encode("ascii", "ignore").decode()
        print(f"  → {safe_subj}")
        try:
            result = parse_one(email)
        except Exception as e:  # provider outage etc. — record, never silently drop
            failed.append(safe_subj)
            print(f"    ⚠ parse error for '{safe_subj}': {type(e).__name__}: {e}")
            continue
        if result is None:
            failed.append(safe_subj)
            print(f"    ⚠ non-JSON LLM reply for '{safe_subj}' — skipped")
            continue
        rec = {
            "email_id": email["id"],
            "internet_message_id": email.get("internetMessageId"),
            "conversation_id": email.get("conversationId"),
            "subject": email.get("subject"),
            "sender": email.get("sender"),
            "sender_name": email.get("sender_name"),
            "to": email.get("to") or [],
            "folder": email.get("folder"),
            "receivedDateTime": email.get("date", ""),
            "attachment_names": email.get("attachment_names") or [],
            "body_text": email.get("body_text", "")[:20000],
            "insights": result,
        }
        insights.append(rec)
        try:
            archive_parse(email["id"], email.get("date", ""), result)
        except Exception as e:
            print(f"    (archive note skipped: {e})")

    save_json(output_file, insights)
    print(f"Parsed insights saved to {output_file}")
    print(f"Parsed OK: {len(insights)}  Failed: {len(failed)}  (of {len(emails)} raw)")
    if failed:
        print("Failed subjects: " + "; ".join(failed[:10]))
    return insights


def reparse_month(month: str) -> None:
    """Re-run the parser over archived emails for YYYY-MM (after a prompt change)."""
    files = sorted((ARCHIVE_DIR / month).glob("*.json"))
    emails = [e for e in (load_json(f, None) for f in files) if e and e.get("id")]
    print(f"Re-parsing {len(emails)} archived emails from {month}")
    parse_emails(emails, output_file=DATA / f"parsed_insights_reparse_{month}.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reparse", metavar="YYYY-MM", help="re-parse archived emails for a month into a side file")
    a = ap.parse_args()
    if a.reparse:
        reparse_month(a.reparse)
    else:
        parse_emails()
