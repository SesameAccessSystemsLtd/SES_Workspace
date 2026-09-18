import os
import json
import requests
import re
from datetime import datetime

# Asana @Giles mention tag — renders as a live user chip in Asana story comments.
# Same value as GILES_MENTION_TAG in execute_requests.py so comment content that
# flows through asana_add_comment() posts cleanly (never the raw profile URL).
GILES_USER_GID      = "1142108324926610"  # REAL Giles user GID (verified 2026-09-01; old 369446862164512 was dead)
GILES_EMAIL         = "giles@sesameaccess.com"  # typed at end of comments; Asana auto-links it as a mailto address
GILES_MENTION_TAG   = f'<a data-asana-gid="{GILES_USER_GID}" data-asana-type="user">@Giles</a>'

def extract_domain_from_sender(sender_email):
    match = re.search(r"@([a-zA-Z0-9.-]+)", sender_email)
    if match: return match.group(1)
    return None

def extract_email_and_name(sender):
    """Pull the actual email address + display name from a raw sender string.
    Handles 'Name <email>', bare 'email', and 'Name/email' forms."""
    email = None
    name = ""
    m = re.search(r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+)", sender or "")
    if m:
        email = m.group(1)
        # Name = the part before '<' if present, else before '@'-domain, else empty.
        pre = (sender or "").split("<")[0].strip()
        if pre and "@" not in pre:
            name = pre.strip()
    return email, name

def extract_company_name_from_domain(domain):
    # Strip TLD suffixes and subdomain prefixes — e.g. noreply.aalco.co.uk → aalco
    skip = {'co', 'com', 'org', 'net', 'uk', 'io', 'eu', 'gov', 'edu', 'biz', 'info'}
    parts = domain.split('.')
    meaningful = [p for p in parts if p.lower() not in skip and len(p) > 2]
    return meaningful[-1] if meaningful else parts[0]

def normalise_ref(value):
    """Canonicalise order/quote refs for matching: keep alnum, uppercase.
    'SQR 46386' / 'SQR-46386' / 'sqr46386' → SQR46386."""
    if not value:
        return ''
    return ''.join(ch for ch in str(value).upper() if ch.isalnum())


def _findings_summary(insights, sender_domain):
    """One-line summary of what the parser extracted, for human review.
    Populated on every row so Giles can judge match quality even when no task
    was found or no field change is proposed (2026-08-17)."""
    parts = []
    if insights.get("document_type"): parts.append(f"type={insights['document_type']}")
    if insights.get("po_number"):      parts.append(f"PO={insights['po_number']}")
    if insights.get("sqr_number"):     parts.append(f"SQR={insights['sqr_number']}")
    if insights.get("total_price") is not None: parts.append(f"price=£{insights['total_price']}")
    if insights.get("delivery_date"):  parts.append(f"delivery={insights['delivery_date']}")
    if sender_domain:                  parts.append(f"vendor={extract_company_name_from_domain(sender_domain)}")
    if insights.get("has_issues"):     parts.append(f"ISSUE: {insights.get('issue_summary','')}")
    return ", ".join(parts) if parts else "no identifiers extracted"


# ---- P4 auto-hook: attach inbound quotes/confirmations/invoices to our SQR ledger ----
# (Giles 2026-08-27) Returned quotes arrive in a SEPARATE chain; match by domain/ref to
# the SQR we issued and hold the supplier's quote/confirmation/invoice + sender in ops.db.
SQR_DOC_TYPES = {"quote": "quote_ref", "order_confirmation": "confirmation", "invoice": "invoice"}

def _hook_quote_ledger(item, insights, sender_email):
    """Auto-call match_quote_to_sqr.process_quote when an inbound email is a
    quote, order_confirmation or invoice. Best-effort — never blocks the mail flow.
    Returns a short human note ('' if nothing to route)."""
    try:
        doc_type = insights.get("document_type")
        if doc_type not in SQR_DOC_TYPES:
            return ""
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(os.path.abspath(__file__)))
        from match_quote_to_sqr import process_quote

        ref = insights.get("po_number") or ""
        explicit_sqr = insights.get("sqr_number") or ""
        _, sender_name = extract_email_and_name(item.get("sender") or sender_email or "")
        result = process_quote(
            sender_email=sender_email or "", sender_name=sender_name,
            quote_ref=(ref if doc_type == "quote" else ""),
            confirmation=(ref if doc_type == "order_confirmation" else ""),
            invoice=(ref if doc_type == "invoice" else ""),
            explicit_sqr=explicit_sqr)
        if result.get("matched"):
            return f"[SQR-ledger] {result['note']}"
        return f"[SQR-ledger] no SQR match for {sender_email}"
    except Exception as exc:  # pragma: no cover - hook must never break the pipeline
        return f"[SQR-ledger] hook error: {exc}"



from dotenv import load_dotenv

load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)
load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)

ASANA_PERSONAL_ACCESS_TOKEN = os.getenv("ASANA_PERSONAL_ACCESS_TOKEN")
ASANA_WORKSPACE_GID = os.getenv("ASANA_WORKSPACE_GID", "357939803556123")
# Placeholder GID for "0 - Order Numbers" project. THIS NEEDS TO BE CONFIGURED.
ASANA_ORDER_NUMBERS_PROJECT_GID = os.getenv("ASANA_ORDER_NUMBERS_PROJECT_GID", "854650811917168") 
# Placeholder GID for "ORDER Status" custom field. THIS NEEDS TO BE CONFIGURED.
ASANA_ORDER_STATUS_FIELD_GID = os.getenv("ASANA_ORDER_STATUS_FIELD_GID", "1143955334207040")
ASANA_ORDER_STATUS_CONFIRMED_GID = os.getenv("ASANA_ORDER_STATUS_CONFIRMED_GID", "1143955334207043")
ASANA_PRICE_EX_VAT_FIELD_GID = os.getenv("ASANA_PRICE_EX_VAT_FIELD_GID", "854330340727040")


HEADERS = {"Authorization": f"Bearer {ASANA_PERSONAL_ACCESS_TOKEN}", "Accept": "application/json"}

PARSED_INSIGHTS_PATH = r"C:\Users\GilesHugo\SES_Workspace\data\parsed_insights.json"
PENDING_APPROVAL_QUEUE_PATH = r"C:\Users\GilesHugo\SES_Workspace\data\pending_approval_queue.json"

def search_asana_task(workspace_gid, document_type, po_number, sqr_number, sender_domain, subject=""):
    import sys as _sys
    _sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "lib")))
    from path_config import p as _p
    db_path = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")
    import sqlite3
    if not os.path.exists(db_path): return None, None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    matched_by = None
    row = None

    # Step 1: SQR match — applies to any document type
    if sqr_number:
        norm_sqr = normalise_ref(sqr_number)
        if norm_sqr:
            c.execute(
                # Compare on canonical form so 'SQR-46386' (DB) == 'SQR 46386' (email)
                "SELECT * FROM procurement_tasks "
                "WHERE UPPER(REPLACE(REPLACE(REPLACE(REPLACE(sqr_number,'-',''),' ',''),'#',''),'/','')) = ? "
                "AND sqr_number IS NOT NULL AND sqr_number != '' "
                "ORDER BY is_subtask ASC LIMIT 1",
                (norm_sqr,)
            )
            row = c.fetchone()
            if row: matched_by = "(Matched via SQR in Local DB)"

    # Step 2: Exact PO match
    if not row and po_number:
        c.execute("SELECT * FROM procurement_tasks WHERE po_number = ? ORDER BY is_subtask ASC", (po_number,))
        row = c.fetchone()
        if row: matched_by = "(Matched via PO Number in Local DB)"

    # Step 3: Base PO match — handles full "28883-1555" or bare "28883" (supplier often omits suffix)
    if not row and po_number:
        base_po = po_number.split('-')[0] if '-' in po_number else po_number
        c.execute(
            "SELECT * FROM procurement_tasks WHERE po_number LIKE ? ORDER BY is_subtask ASC LIMIT 1",
            (base_po + '-%',)
        )
        row = c.fetchone()
        if row: matched_by = f"(Matched via Base PO '{base_po}' in Local DB)"

    # Step 4: Job-number + vendor keyword — extract 4-digit job from PO suffix or subject
    if not row:
        job_num = None
        if po_number and '-' in po_number:
            m = re.search(r'-(\d{4})$', po_number)
            if m: job_num = m.group(1)
        if not job_num and subject:
            m = re.search(r'\b(1\d{3})\b', subject)
            if m: job_num = m.group(1)
        if job_num and sender_domain:
            company_name = extract_company_name_from_domain(sender_domain)
            c.execute(
                """SELECT * FROM procurement_tasks
                   WHERE task_name LIKE ?
                     AND (vendor_name LIKE ? OR task_name LIKE ?)
                     AND is_subtask = 0
                   ORDER BY last_synced_at DESC LIMIT 1""",
                ('%' + job_num + '%', '%' + company_name + '%', '%' + company_name + '%')
            )
            row = c.fetchone()
            if row: matched_by = f"(Job# '{job_num}' + Vendor '{company_name}' keyword match)"

    # Step 5: Vendor domain fallback — with multi-match guardrail
    if not row and sender_domain:
        company_name = extract_company_name_from_domain(sender_domain)
        # Exclude completed/arrived/cancelled tasks unless the email is specifically about that order
        c.execute("""
            SELECT * FROM procurement_tasks
            WHERE (vendor_name LIKE ? OR task_name LIKE ?) AND is_subtask = 0
              AND (order_status IS NULL OR order_status NOT IN (
                  '✅ARRIVED - MOVES TO INVOICES!!',
                  '🔥ARRIVED - NEED TO QC',
                  'CANCELLED ORDER ',
                  'ALL PICKED / No Further action ',
                  'No Further action required'
              ))
            ORDER BY last_synced_at DESC LIMIT 5
        """, ('%' + company_name + '%', '%' + company_name + '%'))
        results = c.fetchall()
        if results:
            if len(results) > 1:
                # Multiple candidates — don't auto-assign, flag for manual review
                task_names = [f"https://app.asana.com/0/0/{r['asana_task_gid']}" for r in results[:3]]
                matched_by = f"(Domain Fallback: '{company_name}', {len(results)} candidate(s)) Manual review needed: {'; '.join(task_names)}"
                row = None  # Don't auto-pick
            else:
                row = results[0]
                matched_by = f"(Domain Fallback: '{company_name}')"

    # Step 6: Subject reference fallback — some suppliers quote order refs in subject not body
    if not row and subject:
        # Try extracting order/quote numbers from subject (e.g. "Order #20256", "Quote 12345")
        ref_match = re.search(r'(?:Order|Quote|Ref|PO)\s*[#:]\s*([A-Za-z0-9_-]+)', subject, re.IGNORECASE)
        if ref_match:
            ref = ref_match.group(1)
            c.execute(
                "SELECT * FROM procurement_tasks WHERE (task_name LIKE ? OR po_number LIKE ?) AND is_subtask = 0 ORDER BY last_synced_at DESC LIMIT 1",
                ('%' + ref + '%', '%' + ref + '%')
            )
            row = c.fetchone()
            if row: matched_by = f"(Matched via Subject Reference '{ref}')"

    conn.close()

    if row:
        task = {
            "gid": row["asana_task_gid"],
            "name": row["task_name"],
            "permalink_url": f"https://app.asana.com/0/0/{row['asana_task_gid']}",
            "due_on": row["due_date"]
        }
        task['custom_fields'] = [{"name": "Price Ex VAT", "number_value": row["price_ex_vat"]}]
        return task, matched_by
    return None, None

def main():
    try:
        with open(PARSED_INSIGHTS_PATH, 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        print("No insights found.")
        return

    # ALL emails flow through for review (Giles 2026-08-13: do NOT pre-sift by
    # whether an identifier was extracted — the confidence model + portal views
    # handle filtering. Dropping unmatched emails silently hid real quotes/notes.)
    valid = list(data)

    if not valid:
        print("No emails to process.")
        with open(PENDING_APPROVAL_QUEUE_PATH, 'w') as f: json.dump([], f)
        return

    queue = []
    for item in valid:
        insights = item.get("insights", {})
        sender_domain = extract_domain_from_sender(item.get("sender", ""))
        document_type = insights.get("document_type")
        sqr = insights.get("sqr_number")

        task, matched_by = search_asana_task(ASANA_WORKSPACE_GID, document_type, insights.get("po_number"), insights.get("sqr_number"), sender_domain, subject=item.get("subject", ""))

        # P4 auto-hook: attach inbound quote/confirmation/invoice to the SQR ledger.
        sender_email, _ = extract_email_and_name(item.get("sender", ""))
        sqr_hook_note = _hook_quote_ledger(item, insights, sender_email)
        if sqr_hook_note:
            if not matched_by:
                matched_by = sqr_hook_note
            else:
                matched_by = f"{matched_by}; {sqr_hook_note}"
        
        action_text = []
        comment_to_push = ""

        if task:
            if matched_by: action_text.append(matched_by)

            # Date Comparison Rule
            extracted_delivery_date_str = insights.get("delivery_date")
            asana_due_date_str = task.get("due_on")
            
            proposed_date_update = True
            if extracted_delivery_date_str and asana_due_date_str:
                try:
                    extracted_date = datetime.strptime(extracted_delivery_date_str, "%Y-%m-%d").date()
                    asana_date = datetime.strptime(asana_due_date_str, "%Y-%m-%d").date()
                    if asana_date > extracted_date:
                        proposed_date_update = False
                        action_text.append("Skipped date update: Asana date is later.")
                except ValueError as e:
                    print(f"Error parsing date for task {task.get('gid')}: {e}")
                    # If dates can't be parsed, proceed with update proposal
            
            if proposed_date_update and extracted_delivery_date_str:
                action_text.append(f"Update delivery date to {extracted_delivery_date_str}")
                # Status Rule: If a firm delivery date is extracted, propose changing ORDER Status.
                # Written in plain English for the human review comment — the executor resolves
                # this exact value to the ORDER Status custom field + option GID in code
                # (execute_requests.parse_actions). No raw GIDs leak into Giles' view.
                action_text.append("Update ORDER Status to 'On Order - Delivery Date Confirmed'")

            # Price Rule
            extracted_total_price = insights.get("total_price")
            asana_price_ex_vat = None
            for cf in task.get("custom_fields", []):
                if cf.get("name") == "Price Ex VAT" or cf.get("gid") == ASANA_PRICE_EX_VAT_FIELD_GID:
                    raw = cf.get("display_value") or cf.get("number_value")
                    if raw is not None:
                        try:
                            cleaned = re.sub(r'[^\d.]', '', str(raw))
                            if cleaned:
                                asana_price_ex_vat = float(cleaned)
                        except (ValueError, TypeError):
                            pass
                    break

            if extracted_total_price is not None:
                doc_type_label = (insights.get("document_type") or "Price update").title()
                company = extract_company_name_from_domain(sender_domain).title() if sender_domain else "Supplier"
                date_str = (item.get("receivedDateTime") or "")[:10]
                note_line = f"{doc_type_label} from {company} - £{extracted_total_price:.2f} ({date_str})"
                if asana_price_ex_vat is not None and abs(extracted_total_price - asana_price_ex_vat) < 0.01:
                    action_text.append("Skipped price update: Extracted price matches Asana 'Price Ex VAT'.")
                elif asana_price_ex_vat is not None:
                    # Field already has value — never overwrite; append to description instead
                    action_text.append(f"Note added to description: {note_line}")
                else:
                    # First price — set the custom field and record in description.
                    # Plain-English for the review comment; the executor maps the exact
                    # 'Price Ex VAT' phrase to the custom-field GID in code.
                    action_text.append(f"Update 'Price Ex VAT' to £{extracted_total_price:.2f}")
                    action_text.append(f"Note added to description: {note_line}")

            if insights.get("has_issues"): action_text.append(f"Flag issue: {insights['issue_summary']}")

            comment_items = [a for a in action_text if not a.startswith("(Matched") and not a.startswith("(Job#") and not a.startswith("(Domain Fallback:") and not a.startswith("Skipped")]
            if insights.get("has_issues") and not any(a.startswith("Flag issue:") for a in comment_items):
                comment_items.append(f"Flag issue: {insights['issue_summary']}")
            # Always build a comment for review — even when no field change is
            # proposed. A blank Comments cell left Giles nothing to evaluate or
            # push (2026-08-17). When there is no action, surface an
            # acknowledgement plus the findings so match quality is still visible.
            if not comment_items:
                comment_items = [
                    f"Acknowledged {document_type or 'email'} — no field change proposed "
                    f"({_findings_summary(insights, sender_domain)})"
                ]
            email_subject = item.get("subject", "")
            received_timestamp = item.get("receivedDateTime", "")
            email_id = item.get("email_id", "")
            sender_email, _ = extract_email_and_name(item.get("sender", "") or "")
            task_ref = f"Task GID: {task.get('gid') if task else 'n/a'}"
            task_url = (task.get("permalink_url") or "") if task else ""
            # Content-first layout (Giles 2026-09-01): lead with the substance
            # (document type + subject + proposed action bullets), then tracking
            # (reference / match / GID / URL), then giles@sesameaccess.com at the
            # very bottom (Asana auto-links it as a mailto address).
            tracking_lines = [
                f"Reference: {insights.get('po_number') or insights.get('sqr_number') or 'n/a'} · "
                f"Received: {received_timestamp}",
                f"Match basis: {matched_by or 'n/a'}",
                f"{task_ref} · {email_id}",
            ]
            if task_url:
                tracking_lines.append(f"{task_url}")
            comment_to_push = (
                f"{GILES_MENTION_TAG} {document_type or 'Email'}: {email_subject}\n"
                + "\n".join(f"- {a}" for a in comment_items)
                + "\n\n"
                + "\n".join(tracking_lines)
                + f"\n\n{GILES_EMAIL}"
            )
        else:
            # Unmatched — still surface it (Giles 2026-08-13): no identifier, manual review.
            has_id = any([insights.get("po_number"), insights.get("sqr_number"),
                         insights.get("total_price"), insights.get("delivery_date")])
            if has_id:
                action_text.append(
                    f"Task not found. Document Type: {document_type}. "
                    f"PO: {insights.get('po_number')}. SQR: {insights.get('sqr_number')}. "
                    f"Sender Domain: {sender_domain}")
            else:
                action_text.append(
                    f"Manual review — no task/id extracted. Subject: '{item.get('subject','')[:90]}' "
                    f"(doc type: {document_type or 'unknown'}, sender: {sender_domain or 'unknown'})")
            # Populate a review comment for unmatched rows too. matched_by can
            # carry domain-fallback candidate links even when no task was auto-
            # picked — surface that so Giles can locate the right task by hand.
            # Content-first + email-at-bottom, same as the matched branch.
            email_id_u = item.get("email_id", "")
            sender_email_u, _ = extract_email_and_name(item.get("sender", "") or "")
            comment_to_push = (
                f"{GILES_MENTION_TAG} NO AUTO-MATCH - manual review.\n"
                f"Email: {item.get('subject','')}\n"
                f"Sender: {sender_email_u or sender_domain or 'unknown'}\n"
                f"Received: {item.get('receivedDateTime','')}\n"
                f"Found: {_findings_summary(insights, sender_domain)}"
                + (f"\nMatch attempt: {matched_by}" if matched_by else "")
                + f"\nEmail ID: {email_id_u}"
                + f"\n\n{GILES_EMAIL}"
            )

        queue.append({
            "email_id": item.get("email_id"),
            "subject": item.get("subject"),
            "sender_domain": sender_domain,
            "received_at": item.get("receivedDateTime") or "",
            "insights": insights,
            "asana_task_gid": task.get("gid") if task else None,
            "asana_task_name": task.get("name") if task else None,
            "asana_task_url": task.get("permalink_url") if task else None,
            "proposed_action": "; ".join(action_text),
            "comment_to_push": comment_to_push,
            "status": "pending_review"
        })

    with open(PENDING_APPROVAL_QUEUE_PATH, 'w') as f:
        json.dump(queue, f, indent=2)
    print(f"Staged {len(queue)} items for approval.")

if __name__ == '__main__':
    main()
