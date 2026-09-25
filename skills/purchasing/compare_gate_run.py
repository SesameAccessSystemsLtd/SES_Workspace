"""
compare_gate_run.py — does Hermes see what Claude saw?

Joins the Claude review of the drop folder (invoice-review-2026-09-24.csv, produced from the
Dropbox connector + Asana connector on 24 Sep 2026) with the local gate's own shadow output
(data/invoice_gate_shadow_rows.json, written by `06_invoice_processor.py run` in shadow mode)
and reports, file by file, whether the two agree on: text extracted, PO, matched task, invoice
number, gross, verdict.

Run on the PC after `06 run` (shadow), from skills/purchasing:
    ..\..\.venv\Scripts\python.exe compare_gate_run.py
    ..\..\.venv\Scripts\python.exe compare_gate_run.py --claude data\invoice-review-2026-09-24.csv --hermes ..\..\data\invoice_gate_shadow_rows.json

Exit code 0 = every PO-bearing file agrees on task + invoice number; 1 otherwise. Read-only.
"""
from __future__ import annotations
import argparse, csv, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

def norm(s): return (s or "").strip().lower()
def money(x):
    try: return round(float(x), 2)
    except (TypeError, ValueError): return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude", default=str(HERE / "data" / "invoice-review-2026-09-24.csv"))
    ap.add_argument("--hermes", default=str(HERE.parents[1] / "data" / "invoice_gate_shadow_rows.json"))
    a = ap.parse_args()

    claude = {r["file"]: r for r in csv.DictReader(open(a.claude, encoding="utf-8"))}
    hp = Path(a.hermes)
    if not hp.exists():
        print(f"no Hermes shadow rows at {hp} — run `06_invoice_processor.py run` with GATEWAY_SHADOW=1 first"); sys.exit(2)
    hermes = {r["original_name"]: r for r in json.loads(hp.read_text(encoding="utf-8"))}

    cols = ["file", "text", "po", "task", "invoice", "gross", "verdict"]
    print(f"{'file':<48} {'text':<5} {'po':<5} {'task':<5} {'inv':<5} {'gross':<6} {'verdict':<22} notes")
    bad = 0; seen = 0
    for name, c in sorted(claude.items()):
        h = hermes.get(name)
        if not h:
            print(f"{name[:48]:<48} {'-':<5} {'-':<5} {'-':<5} {'-':<5} {'-':<6} {'NOT SEEN BY HERMES':<22}"); bad += 1; continue
        seen += 1
        c_text = c["verdict"] != "NO_TEXT"
        h_text = bool(h.get("doc_gross") is not None or h.get("invoice_number") or h.get("po_number"))
        po_ok  = norm(c["po"]) == norm(h.get("po_number")) if c["po"] else (not h.get("po_number"))
        task_ok = (c["task_gid"] or "") == (h.get("task_gid") or "")
        inv_ok = norm(c["invoice_no"]).split("|")[0] == norm(h.get("invoice_number")) if c["invoice_no"] else True
        gross_ok = money(c["gross"]) == money(h.get("doc_gross")) if c["gross"] else True
        cv = c["verdict"]; hv = (h.get("verdict") or "").upper()
        verdict_ok = (cv == "AUTO" and hv == "AUTO") or (cv == "CHECK" and hv == "CHECK") or cv in ("NOT_OURS", "ALREADY_SIGNED_OFF", "NO_TEXT")
        tick = lambda b: "ok" if b else "XX"
        notes = []
        if not po_ok: notes.append(f"po hermes={h.get('po_number')}")
        if not task_ok: notes.append(f"task hermes={h.get('task_gid')}")
        if not inv_ok: notes.append(f"inv hermes={h.get('invoice_number')}")
        if not gross_ok: notes.append(f"gross hermes={h.get('doc_gross')}")
        if not verdict_ok: notes.append(f"verdict hermes={hv}")
        if c["po"] and not (task_ok and inv_ok): bad += 1
        print(f"{name[:48]:<48} {tick(c_text==h_text):<5} {tick(po_ok):<5} {tick(task_ok):<5} {tick(inv_ok):<5} {tick(gross_ok):<6} {cv+'/'+hv:<22} {'; '.join(notes)}")
    print(f"\n{seen}/{len(claude)} files seen by Hermes; {bad} disagreement(s) on PO-bearing files")
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    main()
