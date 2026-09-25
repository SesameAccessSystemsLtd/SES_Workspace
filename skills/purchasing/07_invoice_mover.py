"""
07_invoice_mover.py — post-process a human sign-off: rename + file every invoice PDF for the
task, and (when enabled) bring the Asana task to its terminal state.

    python 07_invoice_mover.py            # nightly (e.g. 01:30, after the mirror sync)
    python 07_invoice_mover.py --dry-run

TRIGGER — the human sets Signed OFF to one of:
    "Signed Off "                                    → job done and correct: file the PDFs, finish the task
    "Signed off - requires Ai to act on comments"    → same, PLUS read the comments left since the AI review,
                                                       store them as feedback, acknowledge, then set "Signed Off "
The AI value "Signed off by AI - AWAITING APPROVAL " never triggers anything here.

WHAT HAPPENS PER TASK (all invoices on the task are handled together)
  1. every invoice_documents row for the task (INBOX / AI SIGNED OFF / 2. invoice queries, not yet moved)
     is renamed to  <PO> - <Vendor> - <description> - <that invoice no>.pdf  and moved to
     '3. INVOICES - Signed Off'. One file per invoice. A PDF that contains several invoices is split
     by page so each invoice gets its own correctly named file (pypdf; falls back to one file named
     "<a> _ <b> _ <c>" if the split cannot be made safely).
  2. task title → "<PO> - <Vendor> - <description> - <inv1> / <inv2> / <inv3>"  (every invoice number on
     the PO, ascending, deduplicated; any invoice numbers already on the end of the title are folded in,
     never lost). Invoice Number field → the same list (union with whatever a human already typed).
  3. a description line: "Signed off by <who> on <date> · filed: <link> [· <link>]".
  4. "requires Ai to act on comments": comments posted after the "AI invoice review" marker are written
     to data/gateway_feedback.jsonl (learn.py's input) and echoed back in one acknowledgement comment;
     then Signed OFF is set to "Signed Off " so the task ends in the same state as a manual sign-off.
  5. MAPPED USING AI is left alone if set; set to "NOT MAPPED " when the machine never proposed this task.

GUARDS (all must be satisfied before anything changes)
  GATEWAY_SHADOW=1            → everything runs dry
  INVOICE_FILING_ENABLED=1    → allows the Dropbox rename/move (existing flag)
  INVOICE_POSTPROCESS_ENABLED=1 → allows the Asana title / Invoice Number / Signed OFF / description
                                 requests (new flag; without it only the move + link comment happen, as before)
Asana writes go through queues/REQUESTS → SAP (estate rule). The two custom-field writes need SAP's
`set_custom_field` action (see execute_requests_set_custom_field.patch); until that is applied those
requests will be stamped FAILED by SAP and nothing else is affected.
Nothing is deleted. A file that is not where we filed it is reported, not hunted for.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
proc = __import__("06_invoice_processor")
from gateway_common import now_utc, append_jsonl, DATA  # noqa: E402

DB_PATH, REQUESTS_DIR = proc.DB_PATH, proc.REQUESTS_DIR
FOLDER_INBOX = "INBOX"                                   # rows backfilled from the top-level drop folder
SIGNED_HUMAN = "Signed Off"
SIGNED_COMMENTS = "Signed off - requires Ai to act on comments"
TRIGGERS = {SIGNED_HUMAN, SIGNED_COMMENTS}
FIELD_SIGNED_OFF = "887966711229634"; OPT_SIGNED_OFF_HUMAN = "887966711229635"
FIELD_INVOICE_NUMBER = "855654251787627"
FIELD_MAPPED = "1218748889949686"; OPT_NOT_MAPPED = "1218748889949689"
POSTPROCESS = os.environ.get("INVOICE_POSTPROCESS_ENABLED", "0") == "1"
FEEDBACK_LOG = DATA / "gateway_feedback.jsonl"
REVIEW_MARK = "AI invoice review"
PO_HEAD = re.compile(r"^\s*(\d{5}\s*-\s*\d{4,8})\s*-\s*")


# --------------------------------------------------------------------------- pure helpers (unit-tested)
def invoice_list(*sources: str | None) -> list[str]:
    """Union of invoice numbers from any mix of ' / '-, '|'- or ','-separated strings, order kept, deduped."""
    out: list[str] = []
    for s in sources:
        for tok in re.split(r"\s*[/|,]\s*", s or ""):
            tok = tok.strip()
            if tok and tok.upper() not in {t.upper() for t in out}:
                out.append(tok)
    return out


def title_base(task_name: str, known_invoices: list[str]) -> str:
    """Strip any trailing ' - <invoice no>' segments that match a known invoice number (or a slash list of them)."""
    parts = [p.strip() for p in re.split(r"\s+-\s+", task_name.strip())]
    known = {k.upper() for k in known_invoices}
    while len(parts) > 2:
        last = parts[-1]
        toks = [t.strip().upper() for t in re.split(r"\s*/\s*", last) if t.strip()]
        if toks and all(t in known for t in toks):
            parts.pop(); continue
        break
    return " - ".join(parts).rstrip(" -")


def title_with_invoices(task_name: str, invoices: list[str]) -> str:
    base = title_base(task_name, invoices)
    return f"{base} - {' / '.join(invoices)}" if invoices else base


def filed_name_for(task_name: str, invoice_no: str, known_invoices: list[str]) -> str:
    """'<PO> - <Vendor> - <description> - <invoice no>.pdf' — one file per invoice."""
    return proc.safe_filename(f"{title_base(task_name, known_invoices)} - {invoice_no}") + ".pdf"


def split_plan(page_texts: list[str], invoices: list[str]) -> dict[str, list[int]] | None:
    """Map each invoice number to the page indexes that belong to it. Pages are assigned to the most
    recently seen invoice number; None if any invoice is never found or a page precedes the first."""
    plan: dict[str, list[int]] = {inv: [] for inv in invoices}
    current = None
    for i, t in enumerate(page_texts):
        hits = [inv for inv in invoices if inv and inv in (t or "")]
        if hits:
            current = hits[0]
        if current is None:
            return None
        plan[current].append(i)
    return plan if all(plan[inv] for inv in invoices) else None


# --------------------------------------------------------------------------- Asana reads
def signed_off_by(task_gid: str) -> tuple[str | None, str | None]:
    """(name, ISO date) of the story that set Signed OFF to a trigger value. GET only."""
    try:
        import requests, asana_read as ar
        r = requests.get(f"{ar.BASE}/tasks/{task_gid}/stories", headers=ar._headers(), timeout=20,
                         params={"opt_fields": "created_at,created_by.name,resource_subtype,text,new_enum_value.name,custom_field.name", "limit": 100})
        best = (None, None)
        for s in r.json().get("data", []) if r.status_code == 200 else []:
            cf = (s.get("custom_field") or {}).get("name", "")
            nv = (s.get("new_enum_value") or {}).get("name", "")
            if cf.strip() == "Signed OFF" and nv.strip() in TRIGGERS:
                best = ((s.get("created_by") or {}).get("name"), s.get("created_at"))
        return best
    except Exception:
        return (None, None)


def comments_since_review(task_gid: str) -> list[dict]:
    try:
        import requests, asana_read as ar
        r = requests.get(f"{ar.BASE}/tasks/{task_gid}/stories", headers=ar._headers(), timeout=20,
                         params={"opt_fields": "created_at,created_by.name,resource_subtype,text", "limit": 100})
        st = r.json().get("data", []) if r.status_code == 200 else []
    except Exception:
        return []
    mark = None; out = []
    for s in st:
        if s.get("resource_subtype") != "comment_added": continue
        txt = s.get("text") or ""
        if REVIEW_MARK in txt: mark = s["created_at"]; continue
        if mark and s["created_at"] > mark and "Richard —" not in txt[:10]:
            out.append({"by": (s.get("created_by") or {}).get("name"), "at": s["created_at"], "text": txt.strip()})
    return out


# --------------------------------------------------------------------------- REQUESTS/ writers
def _request(kind: str, task_gid: str, task_name: str, body_md: str, payload: dict, tag: str) -> str:
    rid = str(uuid.uuid4()); os.makedirs(REQUESTS_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    md = os.path.join(REQUESTS_DIR, f"{ts}-ops_agent-invoice_mover-{tag}-{rid[:8]}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(f"---\nFrom: ops_agent\nTo: asana_agent\nType: asana_write\nStatus: PENDING\nPriority: low\n"
                f"Created: {datetime.now(timezone.utc).isoformat()}\n---\n\n## Request\nAction: {kind}\n"
                f"Task GID: {task_gid}\nTask Name: {task_name}\nTask URL: https://app.asana.com/0/0/{task_gid}\n{body_md}\n## Context\nNightly invoice mover (post-process after human sign-off).\n")
    with open(md[:-3] + ".json", "w", encoding="utf-8") as jf:
        json.dump({"request_id": rid, "source": "invoice_mover", "task_gid": task_gid, "action": kind,
                   **payload, "created_at": datetime.now(timezone.utc).isoformat()}, jf, indent=2)
    return rid

def req_comment(gid, name, text):      return _request("update_task", gid, name, f"\nDetails:\nComment only.\n\nComment To Push:\n{text}\n", {"action": "comment_only", "comment_to_push": text}, "comment")
def req_title(gid, name, new_title):   return _request("update_task_title", gid, name, f"New Title: {new_title}\n", {"new_title": new_title}, "title")
def req_describe(gid, name, block):    return _request("append_to_description", gid, name, f"\nDescription:\n{block}\n", {"description_block": block}, "describe")
def req_field(gid, name, field, value):return _request("set_custom_field", gid, name, f"Field GID: {field}\nValue: {value}\n", {"field_gid": field, "value": value}, "field")


# --------------------------------------------------------------------------- file ops
def move_or_split(row, task_name: str, invoices: list[str], dry_run: bool) -> list[tuple[str, str]]:
    """Return [(final_name, url)] for this row. Splits a multi-invoice PDF when it can do so safely."""
    src_dir = proc.INVOICE_BASE if row["filed_folder"] == FOLDER_INBOX else proc.INVOICE_BASE / row["filed_folder"]
    src = src_dir / row["filed_name"]
    dst_dir = proc.INVOICE_BASE / proc.FOLDER_SIGNED
    own = invoice_list(row["invoice_number"])
    if not src.exists():
        print(f"  ! {row['filed_folder']}/{row['filed_name']} not found (moved by hand?) — recorded, no move")
        return [(row["filed_name"], row["dropbox_url"] or "")]
    if len(own) > 1:                                             # one PDF, several invoices → try to split
        try:
            from pypdf import PdfReader, PdfWriter
            rd = PdfReader(str(src)); texts = [(p.extract_text() or "") for p in rd.pages]
            plan = split_plan(texts, own)
        except Exception as e:
            plan = None; print(f"    (split not attempted: {e})")
        if plan:
            outs = []
            for inv, pages in plan.items():
                name = filed_name_for(task_name, inv, invoices); dst = dst_dir / name
                if dry_run or not proc.FILING_ENABLED:
                    print(f"  DRY-RUN would split pages {pages} → {proc.FOLDER_SIGNED}/{name}")
                else:
                    dst_dir.mkdir(parents=True, exist_ok=True); w = PdfWriter()
                    for i in pages: w.add_page(rd.pages[i])
                    with open(dst, "wb") as fh: w.write(fh)
                outs.append((name, proc.dropbox_web_url(f"{proc.INVOICE_BASE_REL}/{proc.FOLDER_SIGNED}", name)))
            if not (dry_run or not proc.FILING_ENABLED):
                keep = dst_dir / ("_combined originals") ; keep.mkdir(exist_ok=True)
                shutil.move(str(src), str(keep / src.name))     # never delete: park the combined original
            return outs
        name = filed_name_for(task_name, " _ ".join(own), invoices)  # could not split safely → one file, all numbers
    else:
        name = filed_name_for(task_name, own[0] if own else Path(row["filed_name"]).stem, invoices)
    dst = dst_dir / name
    if dry_run or not proc.FILING_ENABLED:
        print(f"  DRY-RUN would move {row['filed_folder']}/{row['filed_name']} → {proc.FOLDER_SIGNED}/{name}")
    else:
        dst_dir.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            stem, ext = os.path.splitext(name); dst = dst_dir / f"{stem} ({row['file_sha256'][:6]}){ext}"
        shutil.move(str(src), str(dst))
    return [(dst.name, proc.dropbox_web_url(f"{proc.INVOICE_BASE_REL}/{proc.FOLDER_SIGNED}", dst.name))]


# --------------------------------------------------------------------------- main
def main(dry_run: bool) -> None:
    from gateway_common import shadow_mode
    if shadow_mode():
        print("SHADOW mode: mover runs dry (nothing moved, nothing written)"); dry_run = True
    if not os.path.exists(DB_PATH):
        print("ops.db not found"); return
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""SELECT file_sha256, task_gid, task_name, invoice_number, filed_folder, filed_name, dropbox_url, verdict
                               FROM invoice_documents
                               WHERE task_gid IS NOT NULL AND filed_folder IN (?, ?, ?) AND moved_to_signed_off_at IS NULL
                               ORDER BY task_gid, invoice_date, seen_at""",
                            (proc.FOLDER_AUTO, proc.FOLDER_CHECK, FOLDER_INBOX)).fetchall()
    except sqlite3.OperationalError:
        print("invoice_documents table not present yet"); return
    if not rows:
        print("Nothing awaiting sign-off."); return
    from asana_read import get_task_fields
    by_task: dict[str, list] = {}
    for r in rows: by_task.setdefault(r["task_gid"], []).append(r)

    done = skipped = 0
    for gid, trs in by_task.items():
        fields = get_task_fields(gid)
        if not fields:
            print(f"  ? task {gid} unreadable — skipped"); skipped += 1; continue
        so = (fields.get("signed_off") or "").strip()
        if so not in TRIGGERS:
            skipped += 1; continue
        task_name = fields.get("name") or trs[0]["task_name"] or ""
        invoices = invoice_list(*[r["invoice_number"] for r in trs], fields.get("invoice_number"))
        print(f"\n{task_name[:70]}  ←  {so}   invoices: {invoices}")

        # 1. files
        filed: list[tuple[str, str]] = []
        for r in trs:
            outs = move_or_split(r, task_name, invoices, dry_run)
            filed += outs
            if not dry_run:
                try:
                    import db_writer
                    db_writer.update_invoice_document(r["file_sha256"], {
                        "human_signed_off_at": now_utc(), "moved_to_signed_off_at": now_utc(),
                        "filed_folder": proc.FOLDER_SIGNED, "filed_name": outs[0][0], "dropbox_url": outs[0][1]}, source="invoice_mover")
                except Exception as e:
                    print(f"  (ops.db not updated: {e})")

        # 2–5. Asana, via SAP
        who, when = signed_off_by(gid)
        when_txt = (when or now_utc())[:10]
        links = " · ".join(u for _, u in filed if u)
        line = f"Signed off by {who or 'a human'} on {when_txt} · filed: {links}"
        if dry_run:
            print(f"  DRY-RUN would append: {line}")
            if POSTPROCESS: print(f"  DRY-RUN would set title: {title_with_invoices(task_name, invoices)}")
            continue
        req_describe(gid, task_name, line)
        if POSTPROCESS:
            new_title = title_with_invoices(task_name, invoices)
            if new_title != task_name: req_title(gid, task_name, new_title)
            joined = " / ".join(invoices)
            if invoices and joined != (fields.get("invoice_number") or "").strip():
                req_field(gid, task_name, FIELD_INVOICE_NUMBER, joined)
            if not fields.get("mapped_using_ai") and not any((r["verdict"] or "") in ("AUTO", "CHECK") for r in trs):
                req_field(gid, task_name, FIELD_MAPPED, OPT_NOT_MAPPED)
            if so == SIGNED_COMMENTS:
                cs = comments_since_review(gid)
                for c in cs:
                    append_jsonl(FEEDBACK_LOG, {"kind": "invoice_signoff_comment", "task_gid": gid, "po": (PO_HEAD.match(task_name) or [None, None])[1],
                                                "invoices": invoices, "by": c["by"], "at": c["at"], "text": c["text"], "harvested_at": now_utc()})
                ack = ("Comments read and stored for the invoice gate to learn from:\n" +
                       "\n".join(f"— {c['by']} {c['at'][:10]}: {c['text'][:200]}" for c in cs) if cs else
                       "Sign-off asked the AI to act on comments, but no comment was found after the AI review — nothing stored.")
                req_comment(gid, task_name, ack)
                req_field(gid, task_name, FIELD_SIGNED_OFF, OPT_SIGNED_OFF_HUMAN)
        else:
            req_comment(gid, task_name, f"Invoice filed to {proc.FOLDER_SIGNED} after human sign-off.\nOpen: {links}\n\nEmail gateway · invoice mover")
        done += 1
    print(f"\nDone: {done} task(s) post-processed, {skipped} still awaiting a human sign-off.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true")
    main(ap.parse_args().dry_run)
