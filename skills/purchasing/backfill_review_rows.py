"""
backfill_review_rows.py — register the 24 Sep hand-gated drop-folder files in invoice_documents so
07_invoice_mover.py post-processes them when Richard signs off.

The 28 files were gated through the Dropbox + Asana connectors (data/invoice-review-2026-09-24.csv),
not by 06, so ops.db has no rows for them. This reads the CSV, hashes each file from the local SAS
Dropbox copy (the real SHA-256 — same duplicate guard as 06), and upserts one invoice_documents row
per file with filed_folder = 'INBOX' (top level of 'Invoices - to be signed off'). Rows for files
that are NOT_OURS / NO_TEXT / duplicates are skipped. Idempotent. Honours GATEWAY_SHADOW (dry).

    ..\..\.venv\Scripts\python.exe backfill_review_rows.py --dry-run
    ..\..\.venv\Scripts\python.exe backfill_review_rows.py
"""
from __future__ import annotations
import argparse, csv, hashlib, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
proc = __import__("06_invoice_processor")
from gateway_common import now_utc, shadow_mode

CSV = Path(__file__).resolve().parent / "data" / "invoice-review-2026-09-24.csv"
SKIP = {"NOT_OURS", "NO_TEXT"}
DUPLICATES = {"Invoice_00626763_23Sep2026.pdf", "Invoice_00626765_23Sep2026.pdf"}   # same bytes as _00626764

def main(dry: bool):
    dry = dry or shadow_mode()
    rows = []
    for r in csv.DictReader(open(CSV, encoding="utf-8")):
        if r["verdict"] in SKIP or r["file"] in DUPLICATES or not r["task_gid"]:
            continue
        p = proc.INVOICE_BASE / r["file"]
        if not p.exists():
            print(f"  ! not at top level any more: {r['file']} — skipped (moved by hand?)"); continue
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        rows.append({
            "file_sha256": sha, "source": "dropbox", "source_ref": str(p.relative_to(proc.DROPBOX_ROOT)),
            "original_name": r["file"], "seen_at": "2026-09-24T07:40:00Z", "sender_domain": "",
            "supplier": r["supplier"].split(" (")[0], "po_number": r["po"], "task_gid": r["task_gid"], "task_name": r["asana_task"],
            "invoice_number": " / ".join(t for t in r["invoice_no"].split("|") if t), "doc_gross": float(r["gross"]) if r["gross"] else None,
            "verdict": "AUTO" if r["verdict"] == "AUTO" else "CHECK", "conditions": "{}", "flags": '["claude-review-2026-09-24"]',
            "filed_folder": "INBOX", "filed_name": r["file"], "filed_at": None, "dropbox_url": r["dropbox_link"],
            "gate_version": "claude-2026-09-24", "last_synced_at": now_utc(),
        })
    print(f"{len(rows)} rows to register")
    for x in rows: print(f"  {x['po_number']:<13} {x['invoice_number']:<32} {x['original_name'][:50]}")
    if dry:
        print("DRY-RUN — nothing written"); return
    import db_writer
    res = db_writer.upsert_invoice_documents(rows, source="claude_backfill")
    print(res)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true")
    main(ap.parse_args().dry_run)
