"""
bom_rev_reconcile.py — BOM Revision-Reconciliation Engine (READ-ONLY diff).

The BOM-side sibling of 05_reconcile_pack_association.py. Given a "Karol
Generate Entire BOM" control task that has been un-ticked and reassigned to
Giles with an updated description (new rev BOM in Dropbox), this engine walks
the ALREADY-IMPORTED parts in Asana (vendor order tasks listed in the control
task description) and diffs them against a NEW-REV BOM. It produces a
proposed change-set ONLY. Nothing is written until --commit is explicitly
approved by Giles after reviewing the HITL output.

DESIGN RULES (per Giles, 2026-08-11):
  1. REMOVED marker: ADD "REMOVED IN REV x" to the task TITLE (keep the
     existing part number intact) + add comment "REMOVED IN REV x" + mark the
     task complete. Never replace the part number in the title.
  2. AMBIGUOUS cases are flagged (@giles), never guessed — they surface in the
     xlsx and the HITL summary for human review.
  3. Trigger signal (responsibility of the watcher, not this engine): a control
     task that is BOTH un-ticked AND assigned to Giles AND had its description
     changed since the last state. This engine does not detect triggers; it
     diffs on demand.
  4. Genuinely new parts are proposed as SUBTASKS under the vendor task they
     match (by vendor / cost-centre), or under a NEW vendor task to be created.

Report-only by default. --commit is a future feature and is NOT implemented
here (writes flow through the importer + REQUESTS queue -> SAP).

Usage:
  python bom_rev_reconcile.py --control-task 1216588901129430 --new-bom <xlsx>
  python bom_rev_reconcile.py --control-task 1216588901129430 --new-bom <xlsx> --output-dir <dir>

Outputs (read-only):
  <output-dir>/proposed_changes.xlsx    — one row per part (current -> proposed action -> reason)
  <output-dir>/proposed_summary.txt     — plain-language summary, @giles for ambiguous
Env:
  ASANA_PERSONAL_ACCESS_TOKEN read from ~/.hermes/.env
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

HOME = Path(os.path.expanduser("~"))


def load_token() -> str:
    for ep in [HOME / ".hermes" / ".env", HOME / ".openclaw" / ".env"]:
        try:
            for line in open(ep, encoding="utf-8"):
                if line.startswith("ASANA_PERSONAL_ACCESS_TOKEN"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except FileNotFoundError:
            continue
    raise RuntimeError("no ASANA_PERSONAL_ACCESS_TOKEN found")


TOKEN = load_token()
H = {"Authorization": f"Bearer {TOKEN}"}
BASE = "https://app.asana.com/api/1.0"

# Cost-centre -> vendor-group display name prefix. Used only to propose a new
# vendor group parent for genuinely-new parts when no matching existing vendor
# task is found. Extend as needed; ambiguity falls through to human review.
COST_CENTRE_VENDOR = {
    "Laser Cut": "Laser Cut",
    "Engraving": "Engraving",
    "Machining": "Machining",
    "Finishes": "Finishes",
    "Anodising": "Anodising",
    "MayTec": "MayTec",
    "NBSS": "NBSS",
    "Sesame": "Sesame",
    "Steel Profile": "Steel Profile",
}


def api_get(path: str, params: dict | None = None) -> dict:
    r = requests.get(f"{BASE}{path}", headers=H, params=params)
    r.raise_for_status()
    return r.json()["data"]


def task_name(gid: str) -> str:
    return api_get(f"/tasks/{gid}", {"opt_fields": "name"})["name"]


def walk_task_tree(control_gid: str) -> list[dict]:
    """Walk the vendor task URLs in the control task description, collecting
    the part subtasks. Returns a list of {'vendor_gid','vendor_name','gid',
    'name','qty','pn','completed'} for each part-level subtask."""
    ctl = api_get(f"/tasks/{control_gid}", {"opt_fields": "name,notes"})
    notes = ctl.get("notes") or ""
    vendor_gids = [m for m in re.findall(r"task/(\d+)", notes) if m != control_gid]

    # Dedup, preserving order
    seen, ordered = set(), []
    for g in vendor_gids:
        if g not in seen:
            seen.add(g)
            ordered.append(g)

    parts: list[dict] = []
    for vgid in ordered:
        try:
            vt = api_get(f"/tasks/{vgid}", {"opt_fields": "name,completed,name_is_hidden"})
        except Exception:
            continue
        vname = vt.get("name", "")
        # Only descend one level of subtasks; a "part" task is any child whose
        # name carries a qty+PN shape. Vendor-group headers may nest further.
        try:
            subs = api_get(f"/tasks/{vgid}/subtasks", {"opt_fields": "name,completed"})
        except Exception:
            subs = []
        for s in subs:
            name = s.get("name", "")
            # A part task name looks like "1 off 1588. 1006.A.078 - desc..."
            m = re.match(r"^\s*(\d+)\s+off\s+(.+?)\s*[—-]\s*(.*)$", name) or \
                re.match(r"^\s*(\d+)\s+off\s+(.+?)\s*$", name)
            if m:
                parts.append({
                    "vendor_gid": vgid,
                    "vendor_name": vname,
                    "gid": s["gid"],
                    "name": name,
                    "qty": int(m.group(1)),
                    "pn": normalize(m.group(2).strip()),
                    "raw_pn": m.group(2).strip(),
                    "completed": s.get("completed", False),
                })
        time.sleep(0.2)
    return parts


# Anchor on the full 4-digit job prefix. The stray space ('1588. 1006') and
# leading material ('Anodising 1588.') are tolerated; capture stops at the
# first ' - ' / ' -' / end so the tail is only the PN, never the description.
PN_IN_NAME = re.compile(r"\b(\d{4})\s?\.\s?([A-Za-z0-9]+(?:\.\s?[A-Za-z0-9\-_]+)+)(?=\s+[—-]|\s*$)")


def normalize(raw: str) -> str:
    """Extract a normalised Sesame part number from a part-task name OR a BOM row.

    Part-task names carry prefix material like '2 off Anodising 1588. 1006.A26w
    - Welded assembly' or '1 off 1588. 1016.H1 - SWLPlate... Engraving'. The job
    prefix (1588.) is the anchor: everything before it is noise. Returns the
    whitespace-collapsed full-format PN, or '' if none can be recovered.
    """
    raw = raw or ""
    m = PN_IN_NAME.search(raw)
    if m:
        return m.group(1) + "." + re.sub(r"\s+", "", m.group(2))
    # No full job prefix visible; fall back to whitespace collapse (BOM cells
    # may already be clean PNs).
    return re.sub(r"\s+", "", raw)


def load_new_bom(xlsx_path: str) -> list[dict]:
    """Load the NEW-REV BOM xlsx (single sheet) AND recognise the revision
    control columns.

    HEADER CONTRACT (verified against the real 1588 Machining Rev3 xlsx):
      A Item QTY | B Item | C Description | D Part Number | E REV |
      ... | L Cost Center | M Notes | ...
    Column M (Notes) is the AUTHORITATIVE change signal — it holds strings
    like 'Removed 13.07.2026', 'Removed 14.07.2026 in rev2', or
    'Changed 27.07.2026'. Column E (REV) carries the revision level. QTY is a
    cross-check (0 => removed, corroborated by the note).

    Returns a dict keyed on normalised PN -> {'qty','raw_pn','cost_centre',
    'notes','rev','removed_in','changed_on'}.
    """
    try:
        import openpyxl
    except ImportError:
        sys.exit("openpyxl required — install in SES_Workspace venv")

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    headers = [str(c.value).strip().lower() if c.value else "" for c in ws[1]]
    # Column index to full header text, for stable note/rev detection.
    header_map = {i: str(c.value).strip() if c.value else "" for i, c in enumerate(ws[1])}

    pn_col = next((i for i, h in enumerate(headers)
                   if any(k in h for k in ("sesame part", "part number", "part no", "part_number", "stock number", "pn ")) or (h == "pn" or h.startswith("part"))), None)
    qty_col = next((i for i, h in enumerate(headers) if h == "item qty" or (h and "qty" in h)), None)
    cc_col = next((i for i, h in enumerate(headers) if any(k in h for k in ("cost", "vendor", "centre", "group"))), None)
    notes_col = next((i for i, h in enumerate(headers) if h == "notes" or (h and "note" in h)), None)
    rev_col = next((i for i, h in enumerate(headers) if h in ("rev", "revision")), None)
    desc_col = next((i for i, h in enumerate(headers) if h in ("description", "desc")), None)
    if pn_col is None:
        sys.exit(f"cannot find part-number column in {xlsx_path}; headers={headers}")

    if notes_col is None:
        print("  WARNING: no Notes column found — fall back to qty-only heuristics")

    parts: dict[str, dict] = {}
    for row in ws.iter_rows(min_row=2):
        pn = str(row[pn_col].value).strip() if row[pn_col].value else ""
        if not pn or normalize(pn) == "":
            continue
        qty = 1
        if qty_col is not None and row[qty_col].value is not None:
            try:
                qty = int(float(row[qty_col].value))
            except (ValueError, TypeError):
                qty = 1
        cost_centre = ""
        if cc_col is not None and row[cc_col].value:
            cost_centre = str(row[cc_col].value).strip()
        notes = ""
        if notes_col is not None and row[notes_col].value:
            notes = str(row[notes_col].value).strip()
        rev = ""
        if rev_col is not None and row[rev_col].value:
            rev = str(row[rev_col].value).strip()
        desc = ""
        if desc_col is not None and row[desc_col].value:
            desc = str(row[desc_col].value).strip()

        parts[normalize(pn)] = {
            "pn": normalize(pn),
            "raw_pn": pn,
            "qty": qty,
            "cost_centre": cost_centre,
            "notes": notes,
            "rev": rev,
            "desc": desc,
        }
    return parts


def propose_placement(cost_centre: str, vendor_tasks: dict[str, str]) -> tuple[str, str | None]:
    """Given a new BOM part's cost centre and the observed vendor-group tasks,
    propose which vendor task it should sit under (decision #4). Falls back to
    a substring match against vendor task names, then to a NEW vendor task."""
    if not vendor_tasks:
        return (cost_centre or "Unknown"), None
    cc = (cost_centre or "").lower()
    # 1) exact-ish match on cost centre against a vendor task name
    for gid, vname in vendor_tasks.items():
        if cc and cc in vname.lower():
            return vname, gid
    # 2) match the vendor word embedded in the cost centre text
    for gid, vname in vendor_tasks.items():
        for word in cc.split():
            if len(word) > 2 and word in vname.lower():
                return vname, gid
    # 3) no match — propose a new vendor task named after the cost centre
    return (cost_centre or "Unknown"), None


_NOTE_DATE = re.compile(r"\b(Removed|Changed)\b\s*(\d{2}\.\d{2}\.\d{4})?\s*(?:in\s+rev\s*(\d+))?", re.I)


def reparse_notes(notes: str) -> dict:
    """Interpret Column M (Notes) — the authoritative change signal.

    Expected forms (verified against real 1588 files):
      'Removed 13.07.2026'             -> removed, rev None
      'Removed 14.07.2026 in rev2'     -> removed, rev 2
      'Changed 27.07.2026'             -> changed, date set
      'Removed 13.07.2026 in rev3'     -> removed, rev 3
    """
    notes = (notes or "").strip()
    removed = bool(re.search(r"Removed", notes, re.I))
    changed = bool(re.search(r"Changed", notes, re.I))
    m = _NOTE_DATE.search(notes)
    date = m.group(2) if m and m.group(2) else ""
    rev = int(m.group(3)) if m and m.group(3) else None
    return {"removed": removed, "changed": changed, "date": date, "rev": rev}


def classify(old: list[dict], new: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Diff old (Asana parts) against new (BOM parts, dict keyed on PN).

    Column M (Notes) is the version of truth: a part with a 'Removed <date>'
    note is removed (even if a row remains); a part with a 'Changed <date>'
    note is an update; the REV column + .01/.02 suffix encode a rename. QTY is
    a cross-check, never the primary signal.
    """
    old_by_pn: dict[str, dict] = defaultdict(list)
    for o in old:
        old_by_pn[o["pn"]].append(o)

    proposed, ambiguous = [], []
    vendor_tasks: dict[str, str] = {o["vendor_gid"]: o["vendor_name"]
                                    for o in old if o.get("vendor_gid")}

    for o in old:
        pn = o["pn"]
        if pn in new:
            n = new[pn]
            meta = reparse_notes(n.get("notes", ""))
            if meta["removed"] or n.get("qty", 1) == 0:
                rev_label = f"IN REV {meta['rev']}" if meta["rev"] else ""
                date_txt = f"({meta['date']})" if meta.get("date") else ""
                proposed.append(_row(o, "REMOVED",
                                     f"note: '{n.get('notes')}' {' '.join(x for x in [rev_label, date_txt] if x)}",
                                     None, removed=True))
            elif meta["changed"] or n.get("qty") != o["qty"]:
                proposed.append(_row(o, "UPDATE_QTY",
                                     f"qty {o['qty']} → {n['qty']}" + (f" note: '{n.get('notes')}'" if n.get("notes") else ""),
                                     n["qty"]))
            else:
                proposed.append(_row(o, "NO_CHANGE", "", n["qty"]))
        else:
            # Not in new BOM by exact PN. Check renumber heuristic: same part
            # family with a .01/.02 suffix in new BOM.
            base = re.sub(r"\.\d{1,2}$", "", pn)
            ren = [np for np in new if np.startswith(base + ".")]
            if ren:
                if len(ren) == 1:
                    proposed.append(_row(o, "RENUMBER",
                                         f"retitle to {ren[0]} (was {pn})", new[ren[0]]["qty"], new_pn=ren[0]))
                else:
                    ambiguous.append(_row(o, "RENUMBER_AMBIGUOUS",
                                          f"multiple renumber candidates: {ren}", None))
            else:
                proposed.append(_row(o, "REMOVED",
                                     "no longer in new BOM", None, removed=True))

    # New parts not in old Asana set
    old_pn_set = set(old_by_pn.keys())
    for pn, n in new.items():
        if pn in old_pn_set:
            continue
        # Skip parts the new BOM itself already marks removed — nothing to add.
        meta = reparse_notes(n.get("notes", ""))
        if meta["removed"] or n.get("qty", 1) == 0:
            continue
        target_vendor, target_vendor_gid = propose_placement(
            n.get("cost_centre", ""), vendor_tasks)
        proposed.append({
            "gid": "",
            "pn": pn,
            "name": f"{n['qty']} off {n['raw_pn']}",
            "qty": n["qty"],
            "action": "ADD",
            "reason": (f"new in rev; place under '{target_vendor}'"
                       + ("" if target_vendor_gid else " (NEW vendor task)")),
            "new_pn": pn,
            "removed": False,
            "target_vendor": target_vendor,
            "target_vendor_gid": target_vendor_gid,
        })

    return proposed, ambiguous


def _row(o, action, reason, new_qty, new_pn=None, removed=False):
    return {
        "gid": o["gid"],
        "pn": o["pn"],
        "name": o["name"],
        "qty": o["qty"],
        "action": action,
        "reason": reason,
        "new_pn": new_pn,
        "removed": removed,
    }


def write_output(proposed, ambiguous, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        import openpyxl
        from openpyxl.styles import Font
    except ImportError:
        sys.exit("openpyxl required")

    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "Proposed Changes"
    headers = ["Asana GID", "PN", "Current Name", "Qty", "Action",
               "Reason", "New PN", "Needs Human?"]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in proposed:
        ws.append([r.get("gid"), r.get("pn"), r.get("name"), r.get("qty"),
                   r.get("action"), r.get("reason"), r.get("new_pn"), "YES" if r.get("action") in ("RENUMBER_AMBIGUOUS",) else "no"])
    for r in ambiguous:
        ws.append([r.get("gid"), r.get("pn"), r.get("name"), r.get("qty"),
                   r.get("action"), r.get("reason"), r.get("new_pn"), "YES"])
    ws2 = wb.create_sheet("Ambiguous")
    ws2.append(headers)
    for c in ws2[1]:
        c.font = Font(bold=True)
    for r in ambiguous:
        ws2.append([r.get("gid"), r.get("pn"), r.get("name"), r.get("qty"),
                    r.get("action"), r.get("reason"), r.get("new_pn"), "YES"])

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    xlsx = output_dir / f"proposed_changes_{stamp}.xlsx"
    wb.save(xlsx)

    # Summary text with @giles for ambiguous
    counts = defaultdict(int)
    for r in proposed + ambiguous:
        counts[r["action"]] += 1
    lines = ["BOM Revision Reconciliation — proposed change-set", f"{datetime.now(timezone.utc).isoformat()}"]
    lines.append("")
    for k in ["NO_CHANGE", "UPDATE_QTY", "RENUMBER", "REMOVED", "ADD", "RENUMBER_AMBIGUOUS"]:
        if counts[k]:
            lines.append(f"  {k:<22} {counts[k]}")
    if ambiguous:
        lines.append("")
        lines.append("⚠️  @giles — the following need your decision before I proceed:")
        for r in ambiguous:
            lines.append(f"    - {r.get('pn')} : {r.get('reason')}")
    txt = output_dir / f"proposed_summary_{stamp}.txt"
    txt.write_text("\n".join(lines), encoding="utf-8")
    return xlsx, txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--control-task", required=True, help="Asana GID of the 'Karol Generate Entire BOM' control task")
    ap.add_argument("--new-bom", required=True, help="Path to the new-rev BOM xlsx (Stage 1 consolidated output)")
    ap.add_argument("--output-dir", default=None)
    ap.add_argument("--commit", action="store_true", help="NOT IMPLEMENTED — writes require the importer + REQUESTS queue; this engine is read-only")
    args = ap.parse_args()
    if args.commit:
        sys.exit("--commit is not implemented: writes flow through the BOM importer + REQUESTS queue -> SAP. Engine is read-only.")

    control = args.control_task.strip()
    new_bom_path = Path(args.new_bom).expanduser()
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else Path(__file__).parent / "data" / "bom_rev"
    control_tasks = [
        ("1216622257106819", "ALL BOMS - 1588 - America 1 Entire"),
        ("1216588901129430", "2.Entire System - America No.1"),
        ("1216622583567863", "INHOUSE Machining - 1588"),
    ]  # not used for lookup; control passed explicitly

    print(f"control task : {control}")
    print(f"new BOM      : {new_bom_path}")
    if not new_bom_path.exists():
        sys.exit(f"new BOM file not found: {new_bom_path}")

    print("loading new BOM ...")
    new = load_new_bom(str(new_bom_path))
    print(f"  new BOM rows : {len(new)}")

    print("walking Asana vendor task tree ...")
    old = walk_task_tree(control)
    print(f"  Asana part tasks : {len(old)}")

    proposed, ambiguous = classify(old, new)
    xlsx, txt = write_output(proposed, ambiguous, output_dir)
    print(f"\nREPORT ONLY — no writes ({datetime.now(timezone.utc).isoformat()})")
    counts = defaultdict(int)
    for r in proposed + ambiguous:
        counts[r["action"]] += 1
    for k, v in counts.items():
        print(f"  {k:<22} {v}")
    print(f"\n  {xlsx}")
    print(f"  {txt}")


if __name__ == "__main__":
    main()
