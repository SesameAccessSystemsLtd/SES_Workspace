"""
08_call_sweep.py — end-of-day clear-down of RingCentral call notes that were not mapped to a task.

    python 08_call_sweep.py            # daily, ~18:30 after the last 05 run
    python 08_call_sweep.py --dry-run  # list what would be cleared / learned

Giles 2026-09-24: "at the end of every day clear any RingCentral that haven't been mapped … I can steer
by telling you what it's about, and in future you can work out whether it's worth looking for."

Each undecided RingCentral row on Pending that is NOT an auto-push candidate (i.e. not a HIGH match
suggested A) is moved to Processed with Outcome:
  CLEARED-EOD            — nobody wrote on it; the Summary column keeps the key inference, nothing learned
  CLEARED-EOD (steered)  — Giles wrote in Feedback / Amendment Notes / pasted a task link; learn.py turns it
                           into call context for that number:
                             'drop' words (irrelevant, ignore, personal, IT, broadband…) → future calls from the
                                    number are triaged as non-supplier and suggested R straight away
                             anything else → 'keep' with his note, which the call parser is given next time
                             a task link → calls from that number go to that task (90 days)
Rows with a DECISION are left to 05. Sheet + local rules only: no REQUESTS, no Asana, so it is safe in shadow.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from gateway_common import FEEDBACK_LOG, append_jsonl, now_utc, shadow_mode  # noqa: E402
from sheet_common import PENDING_HEADERS, PENDING_TAB, PROCESSED_HEADERS, PROCESSED_TAB, Tab, get_spreadsheet  # noqa: E402

RC_DOMAIN = "ringcentral.com"


def _gid(url: str) -> str:
    u = (url or "").split("?")[0].rstrip("/")
    parts = u.split("/")
    if "task" in parts and parts.index("task") + 1 < len(parts):
        return parts[parts.index("task") + 1]
    return parts[-1] if parts and parts[-1].isdigit() else ""


def sweep(dry_run: bool = False) -> dict:
    sh = get_spreadsheet()
    pending = Tab(sh, PENDING_TAB, PENDING_HEADERS)
    processed = Tab(sh, PROCESSED_TAB, PROCESSED_HEADERS)
    rows = pending.read_rows()

    targets = []
    for r in rows:
        if (r.get("Sender Domain") or "").lower() != RC_DOMAIN or not r.get("Email ID"):
            continue
        if (r.get("DECISION") or "").strip() or r.get("Processed At"):
            continue                                  # a human decided → 05's job
        auto_candidate = (r.get("Suggested") or "").upper() == "A" and (r.get("Confidence") or "").upper() == "HIGH"
        if auto_candidate and not (r.get("Feedback") or r.get("Amendment Notes")):
            continue                                  # mapped → 05 auto-push handles it
        targets.append(r)

    if not targets:
        print("No unmapped RingCentral rows to clear.")
        return {"cleared": 0, "steered": 0}

    feedback, archived = [], []
    for r in targets:
        text = " ".join(x for x in (r.get("Feedback"), r.get("Amendment Notes")) if (x or "").strip())
        link = _gid(r.get("Asana Link", ""))
        prop = (r.get("Proposed Task GID") or "").strip()
        steered = bool(text) or (link and link != prop)
        if steered:
            feedback.append({
                "logged_at": now_utc(), "email_id": r["Email ID"], "received": r.get("Received", ""),
                "subject": r.get("Subject", ""), "sender": r.get("Sender", ""), "sender_domain": RC_DOMAIN,
                "doc_type": "call_notes", "triage": r.get("Triage", ""), "decision": "M" if link and link != prop else "R",
                "outcome": "CLEARED-EOD (steered)", "proposed_task_gid": prop, "final_task_gid": link if link != prop else "",
                "amendment_notes": r.get("Amendment Notes", ""), "feedback": r.get("Feedback", ""),
            })
        print(f"  {'STEER' if steered else 'clear'}  {r.get('Subject','')[:70]}" + (f"  ← {text[:60]}" if text else ""))

    if dry_run:
        print(f"(dry-run) would clear {len(targets)} row(s), learning from {len(feedback)}")
        return {"cleared": len(targets), "steered": len(feedback)}

    learned = {}
    if feedback:
        try:
            import learn
            learned = learn.apply_feedback(feedback)
        except Exception as e:
            print(f"  (learning skipped: {type(e).__name__}: {e})")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    steered_ids = {f["email_id"] for f in feedback}
    for r in targets:
        a = {h: r.get(h, "") for h in processed.header}
        a["DECISION"] = "R (eod)"
        a["Processed At"] = stamp + (" (shadow)" if shadow_mode() else "")
        a["Outcome"] = "CLEARED-EOD (steered)" if r["Email ID"] in steered_ids else "CLEARED-EOD"
        a["Learned"] = "; ".join(learned.get(r["Email ID"], []))
        archived.append(a)
    processed.append_rows(archived)
    for f in feedback:
        f["learned"] = learned.get(f["email_id"], [])
        append_jsonl(FEEDBACK_LOG, f)
    pending.delete_rows([r["_row"] for r in targets])
    n_learn = sum(1 for v in learned.values() if v)
    print(f"Done: {len(targets)} RingCentral row(s) cleared, {len(feedback)} steered, rules learned from {n_learn}.")
    return {"cleared": len(targets), "steered": len(feedback)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    sweep(ap.parse_args().dry_run)
