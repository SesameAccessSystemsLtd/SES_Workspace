"""
harvest_invoice_feedback.py — pull Richard's replies off the 24 Sep review tasks.

Reads the comment stream (GET only, asana_read credentials) on every task in the review CSV,
finds replies posted AFTER the "AI invoice review" comment, and extracts the structured line
(AGREE / WRONG: ... / CHANGED: ...). Also reads the live Payment and MAPPED USING AI fields so
the sheet shows what was finally set. Writes data/invoice-feedback-<date>.csv. Read-only.

    ..\..\.venv\Scripts\python.exe harvest_invoice_feedback.py
"""
from __future__ import annotations
import csv, re, sys
from datetime import datetime, timezone
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).resolve().parent))
import asana_read as ar

HERE = Path(__file__).resolve().parent
REVIEW = HERE / "data" / "invoice-review-2026-09-24.csv"
MARK = "AI invoice review"
LINE = re.compile(r"\b(AGREE|WRONG|CHANGED)\b\s*:?\s*(.*)", re.I)

def stories(gid):
    r = requests.get(f"{ar.BASE}/tasks/{gid}/stories", headers=ar._headers(), timeout=20,
                     params={"opt_fields": "created_at,created_by.name,type,resource_subtype,text", "limit": 100})
    return r.json().get("data", []) if r.status_code == 200 else []

def main():
    rows = [r for r in csv.DictReader(open(REVIEW, encoding="utf-8")) if r["task_gid"]]
    out = []
    for r in rows:
        gid = r["task_gid"]; live = ar.get_task_fields(gid) or {}
        st = stories(gid); marker_at = None; replies = []
        for s in st:
            if s.get("resource_subtype") != "comment_added": continue
            if MARK in (s.get("text") or ""): marker_at = s["created_at"]; continue
            if marker_at and s["created_at"] > marker_at:
                m = LINE.search(s.get("text") or "")
                replies.append({"by": (s.get("created_by") or {}).get("name"), "at": s["created_at"][:16],
                                "tag": m.group(1).upper() if m else "FREE", "text": (m.group(2) if m else s.get("text") or "").strip()[:300]})
        out.append({**{k: r[k] for k in ("file", "verdict", "po", "supplier", "invoice_no", "gross", "asana_task", "task_gid", "mapped_using_ai", "payment_check")},
                    "live_payment": live.get("payment"), "live_mapped": live.get("mapped_using_ai"),
                    "live_signed_off": live.get("signed_off"), "live_invoice_number": live.get("invoice_number"),
                    "replies": len(replies),
                    "feedback": " || ".join(f"{x['by']} {x['at']} {x['tag']}: {x['text']}" for x in replies)})
    dest = HERE / "data" / f"invoice-feedback-{datetime.now(timezone.utc):%Y-%m-%d}.csv"
    with open(dest, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    tagged = sum(1 for o in out if o["replies"])
    print(f"{len(out)} tasks · {tagged} with replies · written {dest}")
    for o in out:
        if o["replies"]: print(f"  {o['po']:<13} {o['feedback'][:120]}")

if __name__ == "__main__":
    main()
