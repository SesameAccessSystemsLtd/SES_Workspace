"""
07_invoice_mover.py — nightly: when a human has signed off in Asana, file the PDF to Signed Off.

    python 07_invoice_mover.py            # nightly (e.g. 01:30, after the mirror sync)
    python 07_invoice_mover.py --dry-run

For every invoice_documents row filed in 'AI SIGNED OFF' or '2. invoice queries' with a task and
not yet moved: read the task live (asana_read, GET only). If Signed OFF = 'Signed Off ' (the HUMAN
value — never the AI value), move the PDF to '3. INVOICES - Signed Off' and record the timestamps.
The Dropbox link in the earlier Asana comment stops resolving after the move, so a short follow-up
comment with the new link is queued via SAP.
Nothing is deleted; a file that is not where we filed it is reported, not hunted for.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
proc = __import__("06_invoice_processor")
from gateway_common import GILES_EMAIL, now_utc  # noqa: E402

DB_PATH, REQUESTS_DIR = proc.DB_PATH, proc.REQUESTS_DIR
SIGNED_HUMAN = "Signed Off"


def main(dry_run: bool) -> None:
    from gateway_common import shadow_mode
    if shadow_mode():
        print("SHADOW mode: mover runs dry (nothing moved, nothing written)")
        dry_run = True
    if not os.path.exists(DB_PATH):
        print("ops.db not found"); return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""SELECT file_sha256, task_gid, task_name, filed_folder, filed_name, dropbox_url
                               FROM invoice_documents
                               WHERE task_gid IS NOT NULL AND filed_folder IN (?, ?) AND moved_to_signed_off_at IS NULL""",
                            (proc.FOLDER_AUTO, proc.FOLDER_CHECK)).fetchall()
    except sqlite3.OperationalError:
        print("invoice_documents table not present yet"); return
    if not rows:
        print("Nothing awaiting sign-off."); return
    from asana_read import get_task_fields
    moved = skipped = 0
    for r in rows:
        fields = get_task_fields(r["task_gid"])
        if not fields:
            print(f"  ? task {r['task_gid']} unreadable — skipped"); skipped += 1; continue
        so = (fields.get("signed_off") or "").strip()
        if so != SIGNED_HUMAN:
            skipped += 1
            continue
        src = proc.INVOICE_BASE / r["filed_folder"] / r["filed_name"]
        dst_dir = proc.INVOICE_BASE / proc.FOLDER_SIGNED
        dst = dst_dir / r["filed_name"]
        if not src.exists():
            print(f"  ! {r['filed_folder']}/{r['filed_name']} not found (moved by hand?) — recording as signed off, no move")
        elif dry_run or not proc.FILING_ENABLED:
            print(f"  DRY-RUN would move {r['filed_folder']}/{r['filed_name']} → {proc.FOLDER_SIGNED}")
            continue
        else:
            dst_dir.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                stem, ext = os.path.splitext(r["filed_name"])
                dst = dst_dir / f"{stem} ({r['file_sha256'][:6]}){ext}"
            shutil.move(str(src), str(dst))
        new_url = proc.dropbox_web_url(f"{proc.INVOICE_BASE_REL}/{proc.FOLDER_SIGNED}", dst.name)
        try:
            import db_writer
            db_writer.update_invoice_document(r["file_sha256"], {
                "human_signed_off_at": now_utc(), "moved_to_signed_off_at": now_utc(),
                "filed_folder": proc.FOLDER_SIGNED, "filed_name": dst.name, "dropbox_url": new_url}, source="invoice_mover")
        except Exception as e:
            print(f"  (ops.db not updated: {e})")
        # follow-up comment with the final link, via SAP
        rid = str(uuid.uuid4())
        os.makedirs(REQUESTS_DIR, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")
        md = os.path.join(REQUESTS_DIR, f"{ts}-ops_agent-invoice_mover-{rid[:8]}.md")
        comment = (f"Invoice filed to {proc.FOLDER_SIGNED} after human sign-off.\nOpen: {new_url}\n\n"
                   f"Email gateway · invoice mover · {GILES_EMAIL}")
        with open(md, "w", encoding="utf-8") as f:
            f.write(f"---\nFrom: ops_agent\nTo: asana_agent\nType: asana_write\nStatus: PENDING\nPriority: low\n"
                    f"Created: {datetime.now(timezone.utc).isoformat()}\n---\n\n## Request\nAction: update_task\n"
                    f"Task GID: {r['task_gid']}\nTask Name: {r['task_name'] or ''}\nTask URL: https://app.asana.com/0/0/{r['task_gid']}\n\n"
                    f"Details:\nComment only — invoice moved to Signed Off folder.\n\nComment To Push:\n{comment}\n\n## Context\nNightly invoice mover.\n")
        with open(md[:-3] + ".json", "w", encoding="utf-8") as jf:
            json.dump({"request_id": rid, "source": "invoice_mover", "task_gid": r["task_gid"], "action": "comment_only",
                       "comment_to_push": comment, "created_at": datetime.now(timezone.utc).isoformat()}, jf, indent=2)
        moved += 1
        print(f"  moved {r['filed_name']} → {proc.FOLDER_SIGNED}")
    print(f"Done: {moved} moved, {skipped} still awaiting a human sign-off.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    main(ap.parse_args().dry_run)
