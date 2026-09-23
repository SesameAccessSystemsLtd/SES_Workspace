"""
06_invoice_processor.py — invoice intake → seven-condition gate → Dropbox filing → Invoices tab → SAP.

    python 06_invoice_processor.py run       # hourly, after 03: gate new invoices, file PDFs, write the sheet
    python 06_invoice_processor.py approve   # every 30 min, with 05: read Invoices-tab decisions → REQUESTS/
    python 06_invoice_processor.py run --dry-run   # everything except Dropbox copies and sheet rows

Intakes
  email   — invoice PDFs the email gateway archived (01 keeps the bytes in data/email_archive/…/<id>/)
  dropbox — PDFs dropped at the top level of 'Invoices - to be signed off' (never subfolders)
Both are keyed by the PDF's SHA-256 so nothing is gated twice.

Filing (Giles 2026-09-18, reusing existing folders)
  AUTO  → 'AI SIGNED OFF/<PO> - <Vendor> - <task description> - <invoice no>.pdf'
  CHECK → '2. invoice queries/<best-effort same name>.pdf'
  A Dropbox web link (opens the preview for any team member, no API token needed) goes on the
  sheet row and into the Asana comment, so the PDF no longer needs attaching to the task.
  Email PDFs are COPIED from the archive. Dropbox-dropped PDFs are MOVED out of the drop folder.
  Nothing is ever deleted. INVOICE_FILING_ENABLED=1 in .env turns filing on; until then --dry-run
  behaviour applies to Dropbox even without the flag.

Asana (phase 1, agreed 2026-09-18): comment + link only, via SAP. Invoice Number / Signed OFF are
NOT set by the machine. The nightly mover (07) files the PDF to '3. INVOICES - Signed Off' once a
human has set Signed OFF.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[3] / ".hermes" / ".env", override=True)
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from gateway_common import (ARCHIVE_DIR, DATA, GILES_EMAIL, SupplierDirectory, fmt_date, load_json,  # noqa: E402
                            now_utc, save_json, shadow_mode)
from invoice_gate import (ARRIVED, InvoiceHistory, POIndex, SupplierProfiles, find_invoice_numbers,  # noqa: E402
                          run_gate)

try:
    from path_config import p as _p
    DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")
    REQUESTS_DIR = _p("queues.requests")
except Exception:
    DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or str(Path(__file__).resolve().parents[2] / "db" / "ops.db")
    REQUESTS_DIR = str(Path(__file__).resolve().parents[2] / "queues" / "REQUESTS")

GATE_VERSION = "2026-09-18.1"
DROPBOX_ROOT = Path(os.environ.get("SAS_DROPBOX_ROOT") or r"C:\Users\GilesHugo\SAS Dropbox")
INVOICE_BASE_REL = "4. External Operations/7.4 Purchasing Dept/Invoices - to be signed off"
INVOICE_BASE = DROPBOX_ROOT / INVOICE_BASE_REL
FOLDER_AUTO = "AI SIGNED OFF"
FOLDER_CHECK = "2. invoice queries"
FOLDER_SIGNED = "3. INVOICES - Signed Off"
FILING_ENABLED = os.environ.get("INVOICE_FILING_ENABLED", "0") == "1" and not shadow_mode()
STATE_PATH = DATA / "invoice_gate_state.json"      # sha256 -> {seen, verdict, filed}
INVOICES_TAB = "Invoices"
INVOICE_HEADERS = ["File SHA", "Seen", "Source", "Original Name", "Supplier", "PO", "Matched Task", "Asana Link",
                   "Invoice No", "Doc Gross", "Expected Gross", "Verdict", "Conditions", "Flags", "Suggested Task",
                   "Dropbox Link", "Filed To", "Comments", "DECISION", "Amendment Notes", "Feedback", "Processed At",
                   "Request ID"]


def dropbox_web_url(rel_folder: str, filename: str) -> str:
    """Opens the file preview for a signed-in team member. No API, no token, no expiry."""
    return f"https://www.dropbox.com/home/{quote(rel_folder)}?preview={quote(filename)}"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pdf_text(path: Path) -> str:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pdf_extractor"))
        from pdf_extractor import PDFExtractorSkill
        return (PDFExtractorSkill().extract_from_bytes(path.read_bytes()) or {}).get("raw_text", "") or ""
    except Exception as e:
        print(f"    (pdf text failed for {path.name}: {e})")
        return ""


def safe_filename(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]', "_", s).strip().rstrip(".")
    return re.sub(r"\s{2,}", " ", s)[:180]


def filed_name(task_name: str | None, invoice_no: str | None, original: str) -> str:
    """'<PO> - <Vendor> - <description> - <invoice no>.pdf' — today's hand-typed convention.
    The task name already carries '<PO> - <Vendor> - <description>'; an invoice number the
    human appended earlier is stripped so it is not doubled."""
    if not task_name:
        return safe_filename(Path(original).stem) + ".pdf"
    base = re.sub(r"\s*-\s*[A-Z]{0,4}[-/]?\d{3,12}[A-Z0-9]*\s*$", "", task_name.strip()) if invoice_no and task_name.strip().endswith(invoice_no) else task_name.strip()
    base = re.sub(r"pro\b", "", base, count=1) if re.match(r"^2\d{4}-\d{4,6}pro\b", base) else base  # '21223-1588pro' → '21223-1588'
    return safe_filename(f"{base} - {invoice_no}" if invoice_no else base) + ".pdf"


# ------------------------------------------------------------------ intakes
def intake_email(state: dict) -> list[dict]:
    """Invoice-type emails with archived PDF bytes, not yet gated."""
    items = []
    for month_dir in sorted(ARCHIVE_DIR.glob("20*")):
        for js in month_dir.glob("*.json"):
            doc = load_json(js, None) or {}
            ins = doc.get("insights") or {}
            if ins.get("document_type") != "invoice":
                continue
            for att in doc.get("attachment_paths") or []:
                p = Path(att)
                if not p.exists() or p.suffix.lower() != ".pdf":
                    continue
                sha = sha256_of(p)
                if sha in state:
                    continue
                items.append({"sha": sha, "path": p, "source": "email", "source_ref": doc.get("id"),
                              "sender_domain": (doc.get("sender") or "").split("@")[-1].lower(),
                              "original": p.name, "text": None, "email_id": doc.get("id")})
    return items


def intake_dropbox(state: dict) -> list[dict]:
    """PDFs at the TOP LEVEL of the 'to be signed off' folder — the ones Giles drops by hand."""
    items = []
    if not INVOICE_BASE.exists():
        print(f"  Dropbox folder not reachable: {INVOICE_BASE}")
        return items
    for p in sorted(INVOICE_BASE.iterdir()):
        if not p.is_file() or p.suffix.lower() != ".pdf":
            continue
        sha = sha256_of(p)
        if sha in state:
            continue
        items.append({"sha": sha, "path": p, "source": "dropbox", "source_ref": str(p.relative_to(DROPBOX_ROOT)),
                      "sender_domain": "", "original": p.name, "text": None, "email_id": None})
    return items


# ------------------------------------------------------------------ profiles from history
def build_profiles(db_path: str) -> SupplierProfiles:
    """Invoice-number shapes and vendor↔domain aliases from what we already know."""
    prof = SupplierProfiles()
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "invoice_documents" in tables:
            for r in conn.execute("SELECT supplier, sender_domain, invoice_number FROM invoice_documents WHERE invoice_number IS NOT NULL AND verdict='AUTO'"):
                prof.learn(r["supplier"] or r["sender_domain"], r["invoice_number"])
                prof.learn_vendor(r["sender_domain"], r["supplier"])
        if "procurement_tasks" in tables:
            cols = {c[1] for c in conn.execute("PRAGMA table_info(procurement_tasks)")}
            if "invoice_number" in cols:
                for r in conn.execute("SELECT vendor_name, invoice_number FROM procurement_tasks WHERE invoice_number IS NOT NULL AND invoice_number != ''"):
                    prof.learn(r["vendor_name"] or "", r["invoice_number"])
        if "email_documents" in tables:
            for r in conn.execute("SELECT sender_domain, task_vendor_name FROM email_documents WHERE task_vendor_name IS NOT NULL AND outcome IN ('APPROVED','AMENDED')"):
                prof.learn_vendor(r["sender_domain"], r["task_vendor_name"])
    except Exception as e:
        print(f"  (profiles partial: {e})")
    return prof


# ------------------------------------------------------------------ filing
def file_pdf(item: dict, folder: str, name: str, dry_run: bool) -> tuple[str | None, str]:
    """Copy (email) or move (dropbox) the PDF into <folder>/<name>. Returns (web_url, note)."""
    dest_dir = INVOICE_BASE / folder
    rel_folder = f"{INVOICE_BASE_REL}/{folder}"
    dest = dest_dir / name
    if dest.exists() and sha256_of(dest) != item["sha"]:
        stem, ext = os.path.splitext(name)
        name = f"{stem} ({item['sha'][:6]}){ext}"
        dest = dest_dir / name
    url = dropbox_web_url(rel_folder, name)
    if dry_run or not FILING_ENABLED:
        return url, f"DRY-RUN would {'move' if item['source'] == 'dropbox' else 'copy'} → {folder}/{name}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    if item["source"] == "dropbox":
        shutil.move(str(item["path"]), str(dest))
    else:
        shutil.copy2(str(item["path"]), str(dest))
    return url, f"{'moved' if item['source'] == 'dropbox' else 'copied'} → {folder}/{name}"


# ------------------------------------------------------------------ comment
def build_comment(res, item: dict, url: str | None, filed: str) -> str:
    t = res.task
    inv = res.chosen_invoice or "?"
    sup = (t.vendor if t and t.vendor else item.get("sender_domain") or "supplier").strip()
    gross = f"£{res.doc_gross:,.2f}" if res.doc_gross else "?"
    passed = sum(1 for v in res.conditions.values() if v[0])
    head = f"Invoice {inv} from {sup} — {gross} inc VAT — gate: {'PASS' if res.auto else 'CHECK'} ({passed}/7)"
    lines = [head]
    for k, (ok, why) in res.conditions.items():
        lines.append(f"- {'✓' if ok else '✗'} {k.split('_', 1)[1].replace('_', ' ')}: {why}")
    for h in res.hard_stops:
        lines.append(f"- ✗ STOP: {h}")
    if res.suggested_task:
        lines.append(f"- suggestion: {res.suggested_task['why']} https://app.asana.com/0/0/{res.suggested_task['gid']}")
    lines.append("")
    lines.append(f"Filed: {filed}")
    if url:
        lines.append(f"Open: {url}")
    if res.auto:
        lines.append("Next: set Signed OFF when happy — the nightly mover files it to 3. INVOICES - Signed Off.")
    else:
        lines.append("Next: resolve the ✗ items above; the PDF is in 2. invoice queries.")
    lines.append("")
    lines.append(f"Email gateway · invoice gate {GATE_VERSION} · {GILES_EMAIL}")
    return "\n".join(lines)


# ------------------------------------------------------------------ run
def cmd_run(dry_run: bool) -> None:
    state = load_json(STATE_PATH, {}) or {}
    items = intake_email(state) + intake_dropbox(state)
    if not items:
        print("No new invoice PDFs.")
        return
    print(f"{len(items)} new invoice PDF(s): {sum(1 for i in items if i['source']=='email')} email, {sum(1 for i in items if i['source']=='dropbox')} dropbox")

    suppliers = SupplierDirectory()
    index = POIndex.from_ops_db(DB_PATH) if os.path.exists(DB_PATH) else POIndex([])
    profiles = build_profiles(DB_PATH)
    history = InvoiceHistory(DB_PATH)
    try:
        import asana_read
        if not asana_read.TOKEN:
            raise RuntimeError("no ASANA_PERSONAL_ACCESS_TOKEN")
        live_task = asana_read.get_task_fields
    except Exception as e:
        print(f"  (live Asana read unavailable: {e}) — gate runs on the mirror only")
        live_task = None

    rows, db_rows = [], []
    for item in items:
        text = pdf_text(item["path"])
        if not text.strip():
            print(f"  ✗ no text from {item['original']} — CHECK (scan?)")
        # sender domain for dropbox drops: infer from the supplier directory by company tokens in the text
        sd = item["sender_domain"]
        if not sd:
            for s in suppliers.rows:
                comp = SupplierDirectory.short_name(s)
                if comp and len(comp) > 3 and re.search(rf"(?<![a-z]){re.escape(comp.lower())}(?![a-z])", text.lower()[:2500]):
                    sd = (s.get("domain") or "").lower()
                    break
        res = run_gate(text, sd, index, profiles, suppliers, file=item["original"], live=True,
                       live_task=live_task, history=history)
        verdict = "AUTO" if res.auto else "CHECK"
        folder = FOLDER_AUTO if res.auto else FOLDER_CHECK
        name = filed_name(res.task.name if res.task else (res.suggested_task or {}).get("name"), res.chosen_invoice, item["original"])
        url, note = file_pdf(item, folder, name, dry_run)
        comment = build_comment(res, item, url, f"{folder}/{name}")
        print(f"  {verdict:5} {item['original'][:50]} → {note}")

        t = res.task
        rows.append({
            "File SHA": item["sha"], "Seen": now_utc(), "Source": item["source"], "Original Name": item["original"],
            "Supplier": (t.vendor if t else "") or sd, "PO": t.po if t else "", "Matched Task": t.name if t else "NOT MATCHED",
            "Asana Link": f"https://app.asana.com/0/0/{t.gid}" if t else "",
            "Invoice No": res.chosen_invoice or "", "Doc Gross": f"{res.doc_gross:.2f}" if res.doc_gross else "",
            "Expected Gross": f"{t.expected_gross:.2f}" if t and t.expected_gross else "",
            "Verdict": verdict,
            "Conditions": "\n".join(f"{'✓' if ok else '✗'} {k.split('_',1)[1]}: {why}" for k, (ok, why) in res.conditions.items()),
            "Flags": ", ".join(res.flags + [h.split(' ')[0] for h in res.hard_stops]),
            "Suggested Task": f"{res.suggested_task['why']} https://app.asana.com/0/0/{res.suggested_task['gid']}" if res.suggested_task else "",
            "Dropbox Link": url or "", "Filed To": f"{folder}/{name}" if not dry_run else f"(dry-run) {folder}/{name}",
            "Comments": comment, "DECISION": "", "Amendment Notes": "", "Feedback": "", "Processed At": "", "Request ID": "",
        })
        db_rows.append({
            "file_sha256": item["sha"], "source": item["source"], "source_ref": item["source_ref"], "original_name": item["original"],
            "seen_at": now_utc(), "sender_domain": sd, "supplier": (t.vendor if t else None) or None,
            "po_number": t.po if t else None, "task_gid": t.gid if t else None, "task_name": t.name if t else None,
            "invoice_number": res.chosen_invoice, "doc_gross": res.doc_gross, "expected_gross": t.expected_gross if t else None,
            "verdict": verdict, "conditions": json.dumps(res.conditions, ensure_ascii=False),
            "flags": json.dumps(res.flags + res.hard_stops, ensure_ascii=False),
            "suggested_task_gid": (res.suggested_task or {}).get("gid"),
            "filed_folder": folder if (FILING_ENABLED and not dry_run) else None, "filed_name": name,
            "filed_at": now_utc() if (FILING_ENABLED and not dry_run) else None, "dropbox_url": url,
            "gate_version": GATE_VERSION, "last_synced_at": now_utc(),
        })
        state[item["sha"]] = {"seen": now_utc(), "verdict": verdict, "filed": f"{folder}/{name}", "dry_run": dry_run or not FILING_ENABLED}

    if dry_run:
        print("(dry-run: sheet and ops.db not written)")
        return
    if shadow_mode():
        print("  SHADOW: ops.db not written; Dropbox not touched; sheet row only")
        save_json(DATA / "invoice_gate_shadow_rows.json", db_rows)   # kept so the backfill can replay them later
    else:
        try:
            import db_writer
            r = db_writer.upsert_invoice_documents(db_rows)
            print(f"  ops.db invoice_documents: {r}")
        except Exception as e:
            print(f"  (ops.db not written: {e})")
    try:
        from sheet_common import Tab, get_spreadsheet
        tab = Tab(get_spreadsheet(), INVOICES_TAB, INVOICE_HEADERS)
        existing = tab.existing_ids("File SHA")
        new = [r for r in rows if r["File SHA"] not in existing]
        tab.append_rows(new)
        print(f"  Invoices tab: {len(new)} row(s) added")
    except Exception as e:
        print(f"  (Invoices tab not written: {e})")
    save_json(STATE_PATH, state)


# ------------------------------------------------------------------ approve
def cmd_approve() -> None:
    from sheet_common import Tab, get_spreadsheet
    tab = Tab(get_spreadsheet(), INVOICES_TAB, INVOICE_HEADERS)
    rows = tab.read_rows()
    done = 0
    for row in rows:
        dec = (row.get("DECISION") or "").strip().upper()[:1]
        if not dec or row.get("Processed At") or not row.get("File SHA"):
            continue
        if dec == "S":
            dec = "A" if row.get("Verdict") == "AUTO" else ""
            if not dec:
                print(f"  row {row['_row']}: S on a CHECK row — needs A/M/R"); continue
        if dec not in ("A", "M", "R"):
            continue
        gid = (row.get("Asana Link") or "").rstrip("/").split("/")[-1]
        request_id = str(uuid.uuid4())
        if shadow_mode():
            print(f"  SHADOW row {row['_row']}: {dec} recorded on the sheet only (no REQUESTS, no ops.db)")
            tab.ws.update_cell(row["_row"], tab.col("Processed At"), datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") + " (shadow)")
            done += 1
            continue
        if dec in ("A", "M") and gid.isdigit():
            comment = row.get("Comments") or ""
            if dec == "M" and row.get("Amendment Notes"):
                comment += f"\n\nGiles: {row['Amendment Notes']}"
            os.makedirs(REQUESTS_DIR, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")
            md = os.path.join(REQUESTS_DIR, f"{ts}-ops_agent-invoice_gate-{request_id[:8]}.md")
            with open(md, "w", encoding="utf-8") as f:
                f.write(f"""---
From: ops_agent
To: asana_agent
Type: asana_write
Status: PENDING
Priority: normal
Created: {datetime.now(timezone.utc).isoformat()}
---

## Request
Action: update_task
Task GID: {gid}
Task Name: {row.get('Matched Task','')}
Task URL: {row.get('Asana Link','')}

Details:
Comment only — invoice gate verdict + Dropbox link. Do NOT change Invoice Number or Signed OFF (phase 1).

Comment To Push:
{comment}

## Context
Invoice gate decision {dec} by Giles via the Email Gateway sheet (Invoices tab).
File: {row.get('Filed To','')}
""")
            with open(md[:-3] + ".json", "w", encoding="utf-8") as jf:
                json.dump({"request_id": request_id, "source": "invoice_gate_sheet", "row_index": row["_row"],
                           "file_sha256": row.get("File SHA"), "task_gid": gid, "task_name": row.get("Matched Task"),
                           "asana_url": row.get("Asana Link"), "action": "comment_only", "comment_to_push": comment,
                           "decision": dec, "created_at": datetime.now(timezone.utc).isoformat()}, jf, indent=2, ensure_ascii=False)
        elif dec in ("A", "M"):
            print(f"  row {row['_row']}: {dec} but no task link — paste the task link first")
            continue
        tab.ws.update_cell(row["_row"], tab.col("Processed At"), datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        tab.ws.update_cell(row["_row"], tab.col("Request ID"), request_id if dec != "R" else "")
        try:
            import db_writer
            db_writer.update_invoice_document(row["File SHA"], {"sheet_decision": dec, "request_id": request_id if dec != "R" else None,
                                                                 "task_gid": gid if gid.isdigit() else None})
        except Exception as e:
            print(f"  (ops.db decision not recorded: {e})")
        done += 1
        print(f"  row {row['_row']}: {dec} → {'REQUESTS file' if dec != 'R' else 'rejected'}")
    print(f"Done: {done} decision(s) processed.")


def cmd_init_sheet() -> None:
    """Write the headings on the Invoices tab (and make sure the RULES tab has its header row)."""
    from sheet_common import RULES_HEADERS, RULES_TAB, Tab, get_spreadsheet
    sh = get_spreadsheet()
    t = Tab(sh, INVOICES_TAB, INVOICE_HEADERS)
    print(f"  '{t.ws.title}': {len(t.header)} headings")
    r = Tab(sh, RULES_TAB, RULES_HEADERS)
    print(f"  '{r.ws.title}': {len(r.header)} headings")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "approve", "init-sheet"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "run":
        cmd_run(a.dry_run)
    elif a.cmd == "approve":
        cmd_approve()
    else:
        cmd_init_sheet()
