"""
04_sheet_writer.py — Email Gateway sheet writer (Pending tab).

Reads pending_approval_queue.json and appends new items to the Pending tab,
addressed by header name (sheet_common.Tab). Skips items already present.

2026-09-18: one batched append instead of one API call per row; new review
columns — Triage, Supplier, Confidence, Suggested, Why, Summary, Action Required,
Alternatives, Feedback (free text Giles uses to teach the gateway), Proposed
Task GID (so a corrected Asana Link can be detected), Match Reasons, Sender.

Requires: C:/Users/GilesHugo/.hermes/Google_Service_Account.json
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(str(Path(__file__).parents[3] / ".hermes" / ".env"))
sys.path.insert(0, str(Path(__file__).parent))

from gateway_common import DATA, load_json  # noqa: E402
from sheet_common import PENDING_HEADERS, PENDING_TAB, Tab, get_spreadsheet  # noqa: E402

QUEUE_PATH = DATA / "pending_approval_queue.json"


def queue_item_to_row(item: dict) -> dict:
    insights = item.get("insights", {}) or {}
    has_issues = bool(insights.get("has_issues"))
    response_needed = "yes" if item.get("action_required") or has_issues else "no"
    alts = item.get("alternatives") or []
    alt_text = "\n".join(f"{a.get('name', '')[:60]} [{(a.get('status') or '').strip()}] {a.get('url', '')}" for a in alts)
    return {
        "Email ID": item.get("email_id", ""),
        "Received": item.get("received_at", ""),
        "Subject": item.get("subject", ""),
        "Sender Domain": item.get("sender_domain", ""),
        "Sender": item.get("sender", ""),
        "Document Type": insights.get("document_type", ""),
        "PO Number": insights.get("po_number") or "",
        "Matched Task": item.get("asana_task_name") or "NOT MATCHED",
        "Asana Link": item.get("asana_task_url") or "",
        "Proposed Task GID": item.get("asana_task_gid") or "",
        "Proposed Action": item.get("proposed_action", ""),
        "Comments": item.get("comment_to_push", ""),
        "DECISION": "",
        "Amendment Notes": "",
        "Processed At": "",
        "Response Needed": response_needed,
        "Response Status": ("has flagged issues" if has_issues else ("reply needed" if item.get("action_required") else "")),
        "Triage": item.get("triage", ""),
        "Supplier": item.get("supplier_name", ""),
        "Confidence": item.get("confidence", ""),
        "Suggested": item.get("suggested_decision", ""),
        "Why": item.get("suggested_reason", ""),
        "Summary": item.get("summary", ""),
        "Action Required": ("yes: " + " | ".join(item.get("action_items") or [])) if item.get("action_items")
                           else ("yes" if item.get("action_required") else "no"),
        "Alternatives": alt_text,
        "Feedback": "",
        "Match Reasons": "\n".join(item.get("match_reasons") or []),
    }


def main():
    queue = load_json(QUEUE_PATH, None)
    if queue is None:
        print("No pending approval queue found — skipping sheet write.")
        return
    pending = [i for i in queue if i.get("status") == "pending_review"]
    if not pending:
        print("No pending_review items in queue — nothing to write.")
        return

    try:
        sh = get_spreadsheet()
    except RuntimeError as e:  # credentials / library missing: stop and say so, never retry silently
        print(f"ERROR: {e}")
        sys.exit(1)
    tab = Tab(sh, PENDING_TAB, PENDING_HEADERS)
    existing = tab.existing_ids("Email ID")

    rows, skipped = [], 0
    for item in pending:
        eid = item.get("email_id", "")
        if eid in existing:
            skipped += 1
            continue
        rows.append(queue_item_to_row(item))
        existing.add(eid)
    added = tab.append_rows(rows)
    by_sugg = {}
    for r in rows:
        by_sugg[r["Suggested"]] = by_sugg.get(r["Suggested"], 0) + 1
    print(f"Sheet updated: {added} new item(s) added, {skipped} already present. Suggested: {by_sugg}")


if __name__ == "__main__":
    main()
