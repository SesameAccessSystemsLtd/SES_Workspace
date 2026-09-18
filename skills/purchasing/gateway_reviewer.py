#!/usr/bin/env python3
"""
gateway_reviewer.py — Recurring email-gateway performance reviewer.

Complements qa_scorecard.py (which does the full TP/FP/TN/FN accuracy analysis
and emits an HTML report). This reviewer answers the two "where should the
pipeline improve" questions Giles asked, in a concise digest:

  1. GATEWAY PERFORMANCE — how well the matcher/sheet has been doing in the
     review window (processed vs pending, decision mix, per-doc-type signal).
  2. MANUAL ASUANA-UPDATE HOTSPOTS (the NEW, high-value part) — every 'M'
     (Amend) row with an amendment note is evidence that Giles manually edited
     task info the pipeline should have handled. Aggregating these surfaces the
     *specific places* (doc-type + sender domain + the amendment language) where
     he keeps having to switch away and fix a task by hand — i.e. where an
     automation/capture would remove the labour-intensive task-switching.

Reads:
  - data/qa/qa_history.json  (ground-truth decisions from the sheet's Processed tab)
  - data/pending_approval_queue.json  (current live pending items)

Output: digest + hotspots to stdout (watchdog-cron friendly). No writes.
Usage:
  python gateway_reviewer.py [--days N] [--top 6]
"""
import argparse, datetime as dt, json, os, re
from collections import Counter, defaultdict
from pathlib import Path

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
QA_HIST = WS / "data" / "qa" / "qa_history.json"
PENDING = WS / "data" / "pending_approval_queue.json"

def load(path):
    try:
        d = json.load(open(path, encoding="utf-8"))
        return d.get("items") if isinstance(d, dict) else d
    except Exception:
        return []

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="review window (days back from today)")
    ap.add_argument("--top", type=int, default=6, help="how many hotspot groups to list")
    args = ap.parse_args()

    hist = load(QA_HIST)
    pending = load(PENDING)
    cutoff = dt.date.today() - dt.timedelta(days=args.days)

    # ---- 1. Gateway performance over the window ----
    window = []
    for r in hist:
        pa = (r.get("processed_at") or r.get("received") or "")[:10]
        try:
            d = dt.date.fromisoformat(pa)
        except Exception:
            continue
        if d >= cutoff:
            window.append(r)
    dec = Counter(r.get("decision") for r in window)
    doc = Counter(r.get("doc_type") or r.get("document_type") for r in window)
    not_matched = sum(1 for r in window if str(r.get("matched_task") or "").strip() == "NOT MATCHED" or not (r.get("matched_task") or "").strip())

    print(f"=== EMAIL GATEWAY REVIEW (last {args.days}d, {len(window)} processed) ===")
    print(f"  decisions: A={dec.get('A',0)}  M={dec.get('M',0)}  R={dec.get('R',0)}")
    print(f"  pending now: {len(pending)}")
    if window:
        print("  doc types:", ", ".join(f"{k}={v}" for k, v in doc.most_common()))
    if not_matched:
        print(f"  note: {not_matched}/{len(window)} processed rows had no matched task (matcher missed / manual triage)")

    # ---- 2. Manual Asana-update hotspot detection (Amend rows) ----
    amend = [r for r in window if r.get("decision") == "M"]
    if not amend:
        print("\n  No AMEND rows in window — no clear manual-update hotspots detected.")
    else:
        # Group by (doc_type, sender_domain); surface amendment-note language.
        groups = defaultdict(lambda: {"n": 0, "notes": [], "subjects": []})
        for r in amend:
            key = (r.get("doc_type") or r.get("document_type") or "?", r.get("sender_domain") or "?")
            groups[key]["n"] += 1
            note = (r.get("amendment_notes") or "").strip()
            if note:
                groups[key]["notes"].append(note[:80])
            groups[key]["subjects"].append((r.get("subject") or "")[:50])
        ranked = sorted(groups.items(), key=lambda kv: -kv[1]["n"])[: args.top]

        print(f"\n=== MANUAL ASUANA-UPDATE HOTSPOTS ({len(amend)} AMEND rows) ===")
        print("  These are where you edited task info by hand — targets for pipeline takeover:\n")
        for (doc_type, domain), g in ranked:
            print(f"  • {doc_type or '?'} / {domain or '?'}  ({g['n']}x)")
            for note in g["notes"][:2]:
                print(f"      amend: {note}")
            if not g["notes"]:
                print("      (amend notes empty — pipeline proposed weakly, you corrected)")
            for s in g["subjects"][:1]:
                print(f"      e.g. {s}")

    print("\n(Sources: qa_history decisions + pending queue. No writes made.)")

if __name__ == "__main__":
    main()
