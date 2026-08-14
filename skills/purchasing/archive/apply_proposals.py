"""
apply_proposals.py — Phase 2: Persist accepted email proposals to ops.db

Reads email_proposals.json (written by purchase_email_parser.py --write-proposals),
applies accepted proposals to ops.db, and archives the processed file.

What it writes:
  - email_po_matches  : one row per processed email (idempotent on email_message_id)
  - purchase_orders   : upserts header record when a PO number is known
  - po_lines          : updates line_status where asana_task_gid matches

What it does NOT do:
  - Write to Asana (SAP handles that via the proposals JSON)
  - Assign PO numbers (manual only)
  - Send emails

Usage:
    # Dry run — shows what would be written, nothing committed
    python apply_proposals.py

    # Commit to ops.db
    python apply_proposals.py --commit

    # Also queue Asana field updates for SAP to action
    python apply_proposals.py --commit --queue-asana

    # Process a specific proposals file
    python apply_proposals.py --proposals-file path/to/email_proposals.json --commit
"""

import argparse
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
PROPOSALS_PATH  = Path(r"C:\Users\GilesHugo\.openclaw\workspace\data\email_proposals.json")
ARCHIVE_DIR     = Path(r"C:\Users\GilesHugo\.openclaw\workspace\data\proposals_archive")
ASANA_QUEUE     = Path(r"C:\Users\GilesHugo\.openclaw\workspace\data\asana_write_queue.json")
DB_PATH         = Path(os.environ.get(
    "OPENCLAW_OPS_DB_PATH",
    r"C:\Users\GilesHugo\.openclaw\workspace\db\ops.db",
))

# ── Email type -> status mapping ───────────────────────────────────────────────
# purchase_orders.status values: pending_review | acknowledged | overdue | complete | cancelled
# po_lines.line_status values:   ordered | confirmed | dispatched | received | short | cancelled

PO_STATUS_MAP = {
    "acknowledgement": "acknowledged",
    "dispatch":        "acknowledged",   # PO header stays acknowledged; line moves to dispatched
    "delay_notice":    None,             # no header status change
    "quote":           None,
    "general":         None,
}

LINE_STATUS_MAP = {
    "acknowledgement": "confirmed",
    "dispatch":        "dispatched",
    "delay_notice":    None,
    "quote":           None,
    "general":         None,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def load_proposals(path: Path) -> list[dict]:
    if not path.exists():
        print(f"No proposals file found at {path}")
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def already_applied(conn: sqlite3.Connection, email_message_id: str) -> bool:
    row = conn.execute(
        "SELECT id FROM email_po_matches WHERE email_message_id = ?",
        (email_message_id,),
    ).fetchone()
    return row is not None


def upsert_purchase_order(conn: sqlite3.Connection, proposal: dict, commit: bool) -> int | None:
    """
    Upsert a purchase_orders record for the PO number found in this proposal.
    Returns the PO row id if found/created, or None if no PO number available.
    """
    po_numbers = proposal.get("po_numbers_found", [])
    if not po_numbers:
        return None

    po_number = po_numbers[0]  # primary PO; multi-PO emails rare
    new_status = PO_STATUS_MAP.get(proposal.get("email_type"))

    existing = conn.execute(
        "SELECT id, status FROM purchase_orders WHERE po_number = ?",
        (po_number,),
    ).fetchone()

    if existing:
        po_id, current_status = existing
        # Only advance status, never regress it
        status_order = ["pending_review", "acknowledged", "overdue", "complete", "cancelled"]
        if new_status and status_order.index(new_status) > status_order.index(current_status):
            print(f"  PO {po_number}: status {current_status} -> {new_status}")
            if commit:
                conn.execute(
                    "UPDATE purchase_orders SET status = ?, asana_task_gid = ?, updated_at = ? WHERE id = ?",
                    (new_status, proposal.get("task_gid"), now_iso(), po_id),
                )
        else:
            print(f"  PO {po_number}: exists (id={po_id}), status unchanged ({current_status})")
        return po_id
    else:
        print(f"  PO {po_number}: creating new record (supplier: {proposal.get('supplier_name')})")
        if commit:
            conn.execute(
                """INSERT INTO purchase_orders
                   (po_number, supplier_name, asana_task_gid, source_email_id, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    po_number,
                    proposal.get("supplier_name", ""),
                    proposal.get("task_gid"),
                    proposal.get("email_id"),
                    new_status or "pending_review",
                    now_iso(), now_iso(),
                ),
            )
            return conn.execute(
                "SELECT id FROM purchase_orders WHERE po_number = ?", (po_number,)
            ).fetchone()[0]
        return None


def reconcile_po_price(conn, asana_task_gid, extracted_price, source_bom_or_email, commit):
    import requests
    ASANA_TOKEN = "***REMOVED_ASANA_PAT***"
    HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}", "Accept": "application/json"}
    GILES_GID = "369446862164512"
    PRICE_EX_VAT_GID = "854330340727040"
    ORDER_QTY_GID = "1201447044882111"
    ORDER_VALUE_GID = "1204913986258520"
    
    r = requests.get(f"https://app.asana.com/api/1.0/tasks/{asana_task_gid}?opt_fields=name,custom_fields.name,custom_fields.display_value,dependents", headers=HEADERS)
    if r.status_code != 200: return
    task_data = r.json()['data']
    
    current_price_ex_vat = 0.0
    for cf in task_data.get('custom_fields', []):
        if cf.get('gid') == PRICE_EX_VAT_GID and cf.get('display_value'):
            try: current_price_ex_vat = float(cf.get('display_value').replace(',', ''))
            except ValueError: pass

    dependents = [d['gid'] for d in task_data.get('dependents', [])]
    sum_of_lines = 0.0
    for dep_gid in dependents:
        dep_r = requests.get(f"https://app.asana.com/api/1.0/tasks/{dep_gid}?opt_fields=custom_fields", headers=HEADERS)
        if dep_r.status_code == 200:
            dep_data = dep_r.json()['data']
            qty = 0.0
            unit_price = 0.0
            for cf in dep_data.get('custom_fields', []):
                if cf.get('gid') == ORDER_QTY_GID and cf.get('display_value'):
                    try: qty = float(cf.get('display_value'))
                    except ValueError: pass
                if cf.get('gid') == PRICE_EX_VAT_GID and cf.get('display_value'):
                    try: unit_price = float(cf.get('display_value').replace(',', ''))
                    except ValueError: pass
            sum_of_lines += (qty * unit_price)

    comment_html = ""
    if current_price_ex_vat == 0.0:
        if sum_of_lines > 0:
            if abs(extracted_price - sum_of_lines) < 0.01:
                comment_html = f"<body><a data-asana-gid=\"{GILES_GID}\">Giles</a> Quote/Order received for {extracted_price:.2f}. I have updated the PO 'Price Ex VAT'. This perfectly matches the sum of the line items.</body>"
            elif extracted_price > sum_of_lines:
                comment_html = f"<body><a data-asana-gid=\"{GILES_GID}\">Giles</a> Quote/Order received for {extracted_price:.2f}. I updated the PO 'Price Ex VAT'. Note: This is higher than the sum of the line items ({sum_of_lines:.2f}). This may be due to carriage, additional items, or missing line item prices. Please review.</body>"
            else:
                comment_html = f"<body><strong>WARNING:</strong> <a data-asana-gid=\"{GILES_GID}\">Giles</a> Quote/Order received for {extracted_price:.2f}. I updated the PO 'Price Ex VAT', but this is LOWER than the sum of your line items ({sum_of_lines:.2f}). Please review.</body>"
        else:
            comment_html = f"<body><a data-asana-gid=\"{GILES_GID}\">Giles</a> Quote/Order received for {extracted_price:.2f}. I updated the PO 'Price Ex VAT'. The line items do not currently have unit prices.</body>"
            
        if commit:
            requests.put(f"https://app.asana.com/api/1.0/tasks/{asana_task_gid}", headers=HEADERS, json={"data": {"custom_fields": {PRICE_EX_VAT_GID: extracted_price}}})
    else:
        if abs(current_price_ex_vat - extracted_price) < 0.01:
            pass # Silent success
        else:
            comment_html = f"<body><strong>WARNING:</strong> <a data-asana-gid=\"{GILES_GID}\">Giles</a> Order confirmation received for {extracted_price:.2f}, but the Asana 'Price Ex VAT' is currently {current_price_ex_vat:.2f}. Please review the discrepancy.</body>"

    if commit and comment_html:
        requests.post(f"https://app.asana.com/api/1.0/tasks/{asana_task_gid}/stories", headers=HEADERS, json={"data": {"html_text": comment_html}})


def update_po_lines(conn: sqlite3.Connection, proposal: dict, commit: bool):
    """
    Update po_lines.line_status for lines matching this proposal's Asana task GID.
    Only advances status (never regresses).
    """
    new_line_status = LINE_STATUS_MAP.get(proposal.get("email_type"))
    task_gid = proposal.get("task_gid")

    if not new_line_status or not task_gid:
        return

    status_order = ["ordered", "confirmed", "dispatched", "received", "short", "cancelled"]

    rows = conn.execute(
        "SELECT id, line_status FROM po_lines WHERE asana_task_gid = ?",
        (task_gid,),
    ).fetchall()

    if not rows:
        print(f"  po_lines: no rows found for task GID {task_gid} — skipping line update")
        return

    for row_id, current_status in rows:
        if current_status in status_order and new_line_status in status_order:
            if status_order.index(new_line_status) > status_order.index(current_status):
                print(f"  po_lines id={row_id}: {current_status} -> {new_line_status}")
                if commit:
                    ts = now_iso()
                    confirmed_at = ts if new_line_status == "confirmed" else None
                    dispatched_at = ts if new_line_status == "dispatched" else None
                    conn.execute(
                        """UPDATE po_lines
                           SET line_status = ?,
                               confirmed_at = COALESCE(confirmed_at, ?),
                               received_at  = COALESCE(received_at, ?),
                               updated_at   = ?
                           WHERE id = ?""",
                        (new_line_status, confirmed_at, dispatched_at, ts, row_id),
                    )
            else:
                print(f"  po_lines id={row_id}: already at {current_status}, no change")


def insert_email_match(conn: sqlite3.Connection, proposal: dict, po_id: int | None, commit: bool):
    """Record this email->task match in email_po_matches."""
    po_numbers = proposal.get("po_numbers_found", [])
    print(f"  email_po_matches: recording match (confidence: {proposal.get('match_reason','?')})")
    if commit:
        conn.execute(
            """INSERT INTO email_po_matches
               (email_message_id, email_subject, received_at, supplier_name,
                extracted_po_number, asana_task_gid_matched, asana_task_name,
                match_confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                proposal.get("email_id"),
                proposal.get("email_subject"),
                proposal.get("email_date"),
                proposal.get("supplier_name"),
                po_numbers[0] if po_numbers else None,
                proposal.get("task_gid"),
                proposal.get("task_name"),
                "exact" if proposal.get("match_reason") == "po_match" else "partial",
                now_iso(),
            ),
        )


def build_asana_queue_entry(proposal: dict) -> dict | None:
    """Format a write request for SAP to execute against Asana."""
    updates = proposal.get("proposed_updates", {})
    notes = proposal.get("notes_to_append", [])
    if not updates and not notes:
        return None
    return {
        "task_gid":         proposal.get("task_gid"),
        "task_name":        proposal.get("task_name"),
        "source":           "purchase_email_parser",
        "email_id":         proposal.get("email_id"),
        "email_subject":    proposal.get("email_subject"),
        "email_type":       proposal.get("email_type"),
        "proposed_updates": updates,
        "notes_to_append":  notes,
        "queued_at":        now_iso(),
        "applied":          False,
    }


def archive_proposals(path: Path):
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = ARCHIVE_DIR / f"email_proposals_{ts}.json"
    shutil.move(str(path), str(dest))
    print(f"\nProposals archived to: {dest}")


def main():
    parser = argparse.ArgumentParser(description="Apply email proposals to ops.db (Phase 2)")
    parser.add_argument("--proposals-file", default=str(PROPOSALS_PATH),
                        help="Path to email_proposals.json")
    parser.add_argument("--commit", action="store_true",
                        help="Write to ops.db (default: dry run)")
    parser.add_argument("--queue-asana", action="store_true",
                        help="Append Asana write requests to asana_write_queue.json for SAP")
    args = parser.parse_args()

    proposals_path = Path(args.proposals_file)
    proposals = load_proposals(proposals_path)
    if not proposals:
        return

    mode = "COMMIT" if args.commit else "DRY RUN"
    print(f"\n{'='*60}")
    print(f"apply_proposals.py  [{mode}]")
    print(f"Proposals to process: {len(proposals)}")
    print(f"ops.db: {DB_PATH}")
    print(f"{'='*60}\n")

    conn = sqlite3.connect(str(DB_PATH))
    asana_queue_entries = []
    skipped = applied = 0

    try:
        for i, proposal in enumerate(proposals, 1):
            email_id = proposal.get("email_id", "")
            print(f"[{i}/{len(proposals)}] {proposal.get('email_date','')} | "
                  f"{proposal.get('email_type','?')} | {proposal.get('supplier_name','?')}")
            print(f"  Subject: {proposal.get('email_subject','')[:70]}")
            print(f"  Task:    {proposal.get('task_name','(no match)')}")

            if already_applied(conn, email_id):
                print(f"  SKIP — already in email_po_matches\n")
                skipped += 1
                continue

            po_id = upsert_purchase_order(conn, proposal, args.commit)
            update_po_lines(conn, proposal, args.commit)
            insert_email_match(conn, proposal, po_id, args.commit)

            if args.queue_asana:
                entry = build_asana_queue_entry(proposal)
                if entry:
                    asana_queue_entries.append(entry)

            if args.commit:
                conn.commit()

            applied += 1
            print()

        # ── Asana queue ───────────────────────────────────────────────────────
        if args.queue_asana and asana_queue_entries:
            existing_queue = []
            if ASANA_QUEUE.exists():
                with open(ASANA_QUEUE, encoding="utf-8") as f:
                    existing_queue = json.load(f)
            existing_queue.extend(asana_queue_entries)
            if args.commit:
                with open(ASANA_QUEUE, "w", encoding="utf-8") as f:
                    json.dump(existing_queue, f, indent=2, ensure_ascii=False)
                print(f"Asana write queue updated: {len(asana_queue_entries)} entries added -> {ASANA_QUEUE}")
            else:
                print(f"[DRY RUN] Would append {len(asana_queue_entries)} entries to Asana write queue")

        # ── Summary ───────────────────────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"SUMMARY [{mode}]")
        print(f"  Applied: {applied}")
        print(f"  Skipped (already processed): {skipped}")
        if not args.commit:
            print("\n  Run with --commit to persist changes.")

        # ── Archive ───────────────────────────────────────────────────────────
        if args.commit and applied > 0:
            archive_proposals(proposals_path)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
