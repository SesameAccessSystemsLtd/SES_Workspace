"""
05_sheet_approver.py — Email Gateway sheet processor (Pending → REQUESTS/ → Processed).

Reads Pending rows where Giles has set DECISION, creates REQUESTS/ files for SAP
(the only Asana writer), archives the rows to Processed, and — new in 2026-09-18 —
records every decision as FEEDBACK and lets learn.py turn corrections into rules.

Decision values (case-insensitive):
  A = Approve   — use proposed action as-is
  M = Amend     — use Amendment Notes (and/or a corrected Asana Link) instead
  R = Reject    — no Asana action, just archive
  S = Same as Suggested — take the gateway's suggestion (A or R; a CHECK suggestion is skipped)
  (blank) + GATEWAY_AUTO_APPROVE=1 — HIGH-confidence rows suggested A are pushed automatically after a
  grace period; Outcome = AUTO-APPROVED, DECISION = "A (auto)". See _auto_eligible().

What teaches the gateway (see learn.py):
  * a corrected Asana Link (differs from Proposed Task GID)  → sender/phone → task rule,
    and domain/label → supplier rule when the task's vendor is known
  * R with Feedback/Amendment mentioning noise / stop / ignore  → noise-domain rule
  * Feedback mentioning "closed" / "wrong task"                 → blocked-task rule
  * any Feedback text                                          → kept as a note and logged
Columns are addressed by header name; Processed gets the same columns + Outcome,
Request ID, Request Status, Final Task GID, Learned.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from gateway_common import DATA, FEEDBACK_LOG, append_jsonl, load_json, now_utc, save_json, shadow_mode  # noqa: E402

# ---- auto-approve (Giles 2026-09-24: "items with a match can be pushed automatically; I handle edge cases")
# A row is pushed without a human decision only when ALL hold:
#   GATEWAY_AUTO_APPROVE=1 · Suggested = A · Confidence = HIGH · a task is matched and the Asana Link
#   still points at the proposed task · nobody has typed in DECISION / Amendment Notes / Feedback ·
#   the row has sat on Pending for at least AUTO_GRACE_MINUTES (so there is always a window to stop it).
# At most AUTO_MAX_PER_RUN rows per run (runaway guard). Everything else waits for Giles as before.
AUTO_APPROVE = os.environ.get("GATEWAY_AUTO_APPROVE", "0") == "1"
AUTO_GRACE_MINUTES = int(os.environ.get("GATEWAY_AUTO_GRACE_MINUTES", "30"))
AUTO_MAX_PER_RUN = int(os.environ.get("GATEWAY_AUTO_MAX_PER_RUN", "40"))
AUTO_SEEN_PATH = DATA / "auto_approve_seen.json"      # email_id -> first time 05 saw the row undecided


def _auto_eligible(row: dict, seen: dict, now: datetime) -> tuple[bool, str]:
    if (row.get("Suggested") or "").strip().upper() != "A":
        return False, "suggestion not A"
    if (row.get("Confidence") or "").strip().upper() != "HIGH":
        return False, "confidence not HIGH"
    link_gid = _extract_task_gid(row.get("Asana Link", ""))
    prop_gid = (row.get("Proposed Task GID") or "").strip()
    if not link_gid or not prop_gid or link_gid != prop_gid:
        return False, "no task, or link changed by a human"
    if any((row.get(k) or "").strip() for k in ("Amendment Notes", "Feedback")):
        return False, "human has written on the row"
    first = seen.get(row.get("Email ID", ""))
    if not first:
        return False, "first sighting — grace period starts"
    age_min = (now - datetime.strptime(first, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 60
    if age_min < AUTO_GRACE_MINUTES:
        return False, f"in grace period ({age_min:.0f}/{AUTO_GRACE_MINUTES} min)"
    return True, "HIGH match, suggested A, grace elapsed"
from sheet_common import (PENDING_HEADERS, PENDING_TAB, PROCESSED_HEADERS, PROCESSED_TAB,  # noqa: E402
                          Tab, get_spreadsheet)

try:
    from path_config import p as _p
    REQUESTS_DIR = _p("queues.requests")
except Exception:
    REQUESTS_DIR = str(Path(__file__).resolve().parents[2] / "queues" / "REQUESTS")


def _extract_task_gid(asana_url: str) -> str | None:
    """Task GID from any Asana permalink form (…/0/0/GID, …/project/P/task/GID?focus=true)."""
    if not asana_url:
        return None
    u = asana_url.split("?")[0].rstrip("/")
    parts = u.split("/")
    if "task" in parts:
        cand = parts[parts.index("task") + 1] if parts.index("task") + 1 < len(parts) else ""
        return cand if cand.isdigit() else None
    return parts[-1] if parts and parts[-1].isdigit() else None


def already_has_request(email_id: str) -> bool:
    if not os.path.isdir(REQUESTS_DIR):
        return False
    for fname in os.listdir(REQUESTS_DIR):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(REQUESTS_DIR, fname), "r", encoding="utf-8") as f:
                if json.load(f).get("email_id") == email_id:
                    return True
        except Exception:
            continue
    return False


def create_request_file(email_id, subject, task_gid, task_name, asana_url, action_text, comment_text=""):
    """Write a REQUESTS/ file for SAP to process (format unchanged — SAP parses it).
    In shadow mode the file goes to queues/REQUESTS_SHADOW/ instead, so SAP never sees it."""
    global REQUESTS_DIR
    if shadow_mode() and not REQUESTS_DIR.rstrip("\\/").endswith("REQUESTS_SHADOW"):
        REQUESTS_DIR = os.path.join(os.path.dirname(REQUESTS_DIR.rstrip("\\/")), "REQUESTS_SHADOW")
    os.makedirs(REQUESTS_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")
    unique_id = str(uuid.uuid4())[:8]
    filepath = os.path.join(REQUESTS_DIR, f"{ts}-ops_agent-email_approval-{unique_id}.md")
    comment_section = f"Comment To Push:\n{comment_text}\n\n" if comment_text else ""
    content = f"""---
From: ops_agent
To: asana_agent
Type: asana_write
Status: PENDING
Priority: normal
Created: {datetime.now(timezone.utc).isoformat()}
---

## Request
Action: update_task
Task GID: {task_gid or 'NOT_MATCHED'}
Task Name: {task_name}
Task URL: {asana_url}

Details:
{action_text}

{comment_section}## Context
Purchasing email approved by Giles via Email Approval Dashboard (Google Sheet).
Original email subject: {subject}
Email ID: {email_id}

If Task GID is NOT_MATCHED, SAP must locate the correct task manually using
the subject line and proposed action context before applying any updates.
"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


def create_json_request_file(md_filepath, payload):
    json_path = md_filepath[:-3] + ".json"
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(payload, jf, ensure_ascii=False, indent=2, default=str)
    return json_path


def _feedback_record(row: dict, decision: str, outcome: str, final_gid: str | None) -> dict:
    return {
        "logged_at": now_utc(),
        "email_id": row.get("Email ID", ""),
        "received": row.get("Received", ""),
        "subject": row.get("Subject", ""),
        "sender": row.get("Sender", ""),
        "sender_domain": row.get("Sender Domain", ""),
        "doc_type": row.get("Document Type", ""),
        "triage": row.get("Triage", ""),
        "supplier": row.get("Supplier", ""),
        "confidence": row.get("Confidence", ""),
        "suggested": row.get("Suggested", ""),
        "proposed_task_gid": row.get("Proposed Task GID", "") or _extract_task_gid(row.get("Asana Link", "")) or "",
        "matched_task": row.get("Matched Task", ""),
        "decision": decision,
        "outcome": outcome,
        "final_task_gid": final_gid or "",
        "amendment_notes": row.get("Amendment Notes", ""),
        "feedback": row.get("Feedback", ""),
        "proposed_action": row.get("Proposed Action", ""),
    }


def main():
    try:
        sh = get_spreadsheet()
    except RuntimeError as e:  # credentials / library missing: stop and say so, never retry silently
        print(f"ERROR: {e}")
        sys.exit(1)
    pending = Tab(sh, PENDING_TAB, PENDING_HEADERS)
    processed = Tab(sh, PROCESSED_TAB, PROCESSED_HEADERS)

    rows = pending.read_rows()
    if not rows:
        print("Pending tab is empty — nothing to process.")
        return

    to_archive = []   # (row dict, outcome, request_id, final_gid)
    feedback = []
    created = rejected = skipped = auto_done = 0
    now = datetime.now(timezone.utc)
    seen = load_json(AUTO_SEEN_PATH, {}) or {}
    live_ids = set()

    for row in rows:
        email_id = row.get("Email ID", "")
        if not email_id:
            continue
        live_ids.add(email_id)
        decision = (row.get("DECISION") or "").strip().upper()[:1]
        auto = False
        if not decision and AUTO_APPROVE and not row.get("Processed At"):
            seen.setdefault(email_id, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
            ok, _why = _auto_eligible(row, seen, now)
            if ok and auto_done < AUTO_MAX_PER_RUN:
                decision, auto = "A", True
                auto_done += 1
        if not decision:
            continue
        if row.get("Processed At"):
            continue
        if decision == "S":
            sugg = (row.get("Suggested") or "").strip().upper()
            if sugg not in ("A", "R"):
                print(f"  Row {row['_row']}: S but suggestion is '{sugg}' — needs a real decision. Subject: {row.get('Subject','')[:60]}")
                skipped += 1
                continue
            decision = sugg
        if decision not in ("A", "M", "R"):
            print(f"  Row {row['_row']}: unknown DECISION '{decision}' — skipping.")
            skipped += 1
            continue

        subject = row.get("Subject", "")
        task_name = row.get("Matched Task", "")
        asana_url = row.get("Asana Link", "")
        proposed = row.get("Proposed Action", "")
        amendment = row.get("Amendment Notes", "")
        comment = row.get("Comments", "")
        task_gid = _extract_task_gid(asana_url)
        proposed_gid = row.get("Proposed Task GID", "")
        corrected = bool(task_gid and proposed_gid and task_gid != proposed_gid) or bool(task_gid and not proposed_gid and task_name == "NOT MATCHED")

        if decision == "R":
            rejected += 1
            request_id = str(uuid.uuid4())
            to_archive.append((row, "REJECTED", request_id, task_gid if corrected else ""))
            feedback.append(_feedback_record(row, "R", "REJECTED", task_gid if corrected else None))
            continue

        if decision == "A":
            action = proposed
            if corrected:
                action = f"(Giles corrected the task link — original proposal was for task {proposed_gid or 'none'})\n{proposed}"
            outcome = "AUTO-APPROVED" if auto else "APPROVED"
            if auto:
                row["DECISION"] = "A (auto)"
        else:  # M
            if not amendment and not task_gid:
                print(f"  Row {row['_row']}: AMEND but no Amendment Notes and no Asana link — skipping. Subject: {subject[:60]}")
                skipped += 1
                continue
            if not amendment and task_gid:
                action = f"(Giles corrected the task link)\n{proposed}" if corrected else proposed
            elif not task_gid:
                action = f"AMENDMENT (task not auto-matched — SAP must locate manually):\n{amendment}"
            else:
                action = f"AMENDMENT (Giles overrode proposed action):\nOriginal: {proposed}\nAmendment: {amendment}"
            outcome = "AMENDED"

        if already_has_request(email_id):
            print(f"  Row {row['_row']}: a prior REQUESTS/ file exists for this email — creating a new request anyway. Subject: {subject[:60]}")
        if not task_gid:
            print(f"  Row {row['_row']}: {outcome} but task not matched — SAP will need to locate manually. Subject: {subject[:60]}")
        request_id = str(uuid.uuid4())
        filepath = create_request_file(email_id, subject, task_gid, task_name, asana_url, action, comment)
        create_json_request_file(filepath, {
            "request_id": request_id, "source": "email_approval_sheet", "row_index": row["_row"],
            "email_id": email_id, "subject": subject, "task_gid": task_gid, "task_name": task_name,
            "asana_url": asana_url, "action": action, "comment_to_push": comment, "decision": decision,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        created += 1
        print(f"  {outcome} row {row['_row']}: REQUESTS file -> {os.path.basename(filepath)}")
        to_archive.append((row, outcome, request_id, task_gid or ""))
        feedback.append(_feedback_record(row, decision, outcome, task_gid))

    if AUTO_APPROVE:
        archived_ids = {r.get("Email ID", "") for r, *_ in to_archive}
        save_json(AUTO_SEEN_PATH, {k: v for k, v in seen.items() if k in live_ids and k not in archived_ids})
        waiting = sum(1 for k in live_ids if k in seen and k not in archived_ids)
        print(f"  auto-approve: {auto_done} pushed this run (cap {AUTO_MAX_PER_RUN}); {waiting} undecided row(s) being watched")

    if not to_archive:
        print("No decided rows found. Nothing to process.")
        return

    # Learn from the decisions BEFORE archiving so the 'Learned' column can say what changed.
    learned_by_email = {}
    try:
        import learn
        learned_by_email = learn.apply_feedback(feedback)
    except Exception as e:
        print(f"  (learning step skipped: {type(e).__name__}: {e})")

    db_writer = None
    if shadow_mode():
        print("  SHADOW: REQUESTS files written to queues/REQUESTS_SHADOW (SAP will not act); ops.db not written")
    else:
        try:
            import db_writer
        except Exception as e:
            print(f"  (ops.db decisions not recorded: {type(e).__name__}: {e})")

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    archived = []
    for row, outcome, request_id, final_gid in to_archive:
        r = {h: row.get(h, "") for h in processed.header}
        r["Processed At"] = now_str
        r["Outcome"] = outcome
        r["Request ID"] = request_id
        r["Request Status"] = "PENDING" if outcome != "REJECTED" else ""
        r["Final Task GID"] = final_gid
        r["Learned"] = "; ".join(learned_by_email.get(row.get("Email ID", ""), []))
        archived.append(r)
        if db_writer:
            try:
                db_writer.update_email_document_decision(
                    row.get("Email ID", ""), (row.get("DECISION") or "").strip().upper()[:1], outcome,
                    final_gid or None, feedback=row.get("Feedback", ""), learned=r["Learned"])
            except Exception as e:
                print(f"  (ops.db decision not recorded for row {row['_row']}: {e})")
    processed.append_rows(archived)
    for fb in feedback:
        fb["learned"] = learned_by_email.get(fb["email_id"], [])
        append_jsonl(FEEDBACK_LOG, fb)
    pending.delete_rows([row["_row"] for row, *_ in to_archive])

    n_learned = sum(1 for v in learned_by_email.values() if v)
    print(f"\nDone: {created} REQUESTS file(s) created, {rejected} rejected, {skipped} skipped, "
          f"{len(to_archive)} rows archived to Processed ({auto_done} auto-approved). Rules learned from {n_learned} row(s).")


if __name__ == "__main__":
    main()
