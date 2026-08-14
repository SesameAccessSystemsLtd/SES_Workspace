#!/usr/bin/env python3
"""
qa_sync.py — Live QA action portal data gateway.

Pulls the purchasing Google Sheet (Pending + Processed tabs) into local JSON
files that the interactive portal (qa_portal.html) renders, and pushes any
decisions/notes the user saved back to the sheet.

Files:
  data/qa/qa_live.json     — Pending-tab items with no decision yet (needs action).
  data/qa/qa_history.json  — all Processed-tab items (full history).
  data/qa/qa_actions.json  — user-saved decisions/notes (written by the portal,
                             read here and pushed to the sheet).

Confidence model (transparent, signal-based):
  HIGH     — task matched (G has a real task). Proposal already found a target.
  MEDIUM   — NOT MATCHED but doc type is quote/order_confirmation (actionable but
             matcher missed it — needs human pick).
  LOW      — NOT MATCHED on dispatch_note/invoice/update/other (likely noise, or
             info-only). Fast reject.
Colour: green=HIGH, amber=MEDIUM, red=LOW.

AUTHOR: Hermes (email processing). Built 2026-08-13.
"""

import argparse
import datetime as dt
import json
import os
import re

import gspread

SHEET_ID = "1fvoSKnvH0vdM_aZbo2_seY6AU3SZaXABaxRUKSxWVlo"
SERVICE_ACCOUNT_PATH = r"C:\Users\GilesHugo\.hermes\Google_Service_Account.json"
DATA_DIR = r"C:\Users\GilesHugo\SES_Workspace\data\qa"

LIVE_JSON = "qa_live.json"
HIST_JSON = "qa_history.json"
ACT_JSON = "qa_actions.json"

# Headers — must match the sheet layout (both tabs share first 13 columns).
HDR = [
    "email_id", "received", "subject", "sender_domain", "doc_type",
    "po_number", "matched_task", "asana_link", "proposed_action",
    "comments", "decision", "amendment_notes", "processed_at",
]

NBSP = "\u00a0"


def clean_cell(v):
    if v is None:
        return ""
    return (v or "").replace(NBSP, " ").strip()


def confidence(doc_type, matched, asana_link):
    m = (matched or "").strip().upper()
    is_matched = bool(m) and m != "NOT MATCHED"
    if is_matched:
        return "HIGH"
    actionable = doc_type in ("quote", "order_confirmation")
    return "MEDIUM" if actionable else "LOW"


def read_sheet_tab(tab_name):
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_PATH)
    ws = gc.open_by_key(SHEET_ID).worksheet(tab_name)
    vals = ws.get_all_values()
    return [r for r in vals[1:] if any((c or "").strip() for c in r)]


def normalize_rows(data):
    out = []
    for r in data:
        rec = {HDR[i]: clean_cell(r[i]) if i < len(r) else "" for i in range(len(HDR))}
        rec["_rowindex"] = len(out) + 2  # 1-based sheet row (header=1)
        out.append(rec)
    return out


def write_json(path, obj):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, path), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    return os.path.join(DATA_DIR, path)


def build(data_dir=DATA_DIR):
    live = normalize_rows(read_sheet_tab("Pending"))
    hist = normalize_rows(read_sheet_tab("Processed"))

    # Live = still no decision. Compute confidence.
    for rec in live:
        rec["confidence"] = confidence(
            rec["doc_type"], rec["matched_task"], rec["asana_link"])
        rec["decision"] = ""  # force blank for portal

    # History: keep as-is, attach confidence too.
    for rec in hist:
        d = (rec.get("decision") or "").strip().upper()
        rec["confidence"] = confidence(rec["doc_type"], rec["matched_task"], rec["asana_link"])
        rec["decision"] = d

    # Apply saved actions (decisions + notes) over the base data so the portal
    # reflects what has already been captured locally.
    try:
        with open(os.path.join(data_dir, ACT_JSON), encoding="utf-8") as f:
            actions = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        actions = []

    act_by_row = {a["row"]: a for a in actions}
    for rec in live:
        a = act_by_row.get(rec["_rowindex"])
        if a:
            rec["decision"] = a.get("decision", rec["decision"])
            rec["amendment_notes"] = a.get("note", rec.get("amendment_notes", ""))
        # Recompute confidence after any decision
        rec["confidence"] = confidence(rec["doc_type"], rec["matched_task"], rec["asana_link"])
        if rec["decision"]:
            rec["confidence"] = "DONE"

    lp = write_json(LIVE_JSON, {"generated": dt.datetime.now().isoformat(timespec="seconds"),
                                "items": live})
    hp = write_json(HIST_JSON, {"generated": dt.datetime.now().isoformat(timespec="seconds"),
                                "items": hist})
    return lp, hp, len(live), len(hist), len(actions)


PORTAL_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qa_portal_template.html")
PORTAL_OUT = os.path.join(DATA_DIR, "qa_portal.html")


def generate_portal(data_dir=DATA_DIR):
    """Emit qa_portal.html — self-contained interactive page with live+history inline."""
    live = normalize_rows(read_sheet_tab("Pending"))
    hist = normalize_rows(read_sheet_tab("Processed"))
    for rec in live + hist:
        rec["confidence"] = confidence(rec["doc_type"], rec["matched_task"], rec["asana_link"])
        d = (rec.get("decision") or "").strip().upper()
        if d:
            rec["decision"] = d
            rec["confidence"] = "DONE"
        else:
            rec["decision"] = ""

    # Merge any existing user actions so their state is preserved in the page.
    try:
        with open(os.path.join(data_dir, ACT_JSON), encoding="utf-8") as f:
            actions = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        actions = []
    act_by_row = {a["row"]: a for a in actions}

    data = {"generated": dt.datetime.now().isoformat(timespec="seconds"),
            "items": live}
    with open(os.path.join(data_dir, LIVE_JSON), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    # merge note/decision defaults from actions into live
    for rec in data["items"]:
        a = act_by_row.get(rec["_rowindex"])
        if a:
            rec["decision"] = a.get("decision", rec.get("decision", ""))
            rec["amendment_notes"] = a.get("note", rec.get("amendment_notes", ""))

    templ = open(PORTAL_TEMPLATE, encoding="utf-8").read()
    html = templ.replace("__QA_DATA__", json.dumps(data, ensure_ascii=False))
    with open(PORTAL_OUT, "w", encoding="utf-8") as f:
        f.write(html)
    return PORTAL_OUT, len(live), len(hist)


def push_actions(data_dir=DATA_DIR):
    """Push any saved decisions/notes in qa_actions.json back to the sheet."""
    try:
        with open(os.path.join(data_dir, ACT_JSON), encoding="utf-8") as f:
            actions = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("No actions to push.")
        return 0

    if not actions:
        print("No actions to push.")
        return 0

    gc = gspread.service_account(filename=SERVICE_ACCOUNT_PATH)
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet("Pending")

    pushed = 0
    for a in actions:
        row = a.get("row")
        decision = (a.get("decision") or "").strip()
        note = (a.get("note") or "").strip()
        if not row or not decision:
            continue
        # Write DECISION (col K=11) and Amendment Notes (col L=12)
        ws.update_cell(row, 11, decision[0].upper())   # A / M / R
        ws.update_cell(row, 12, note)
        # Stamp Processed At (col M=13)
        ws.update_cell(row, 13, dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
        pushed += 1
        print(f"  pushed row {row}: decision={decision}, note={note[:40]!r}")

    # Clear the pushed set so we don't double-push (they now live in the sheet).
    with open(os.path.join(data_dir, ACT_JSON), "w", encoding="utf-8") as f:
        json.dump([], f)
    print(f"Pushed {pushed} actions to Pending tab. Cleared qa_actions.json.")
    return pushed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true",
                    help="Push saved actions from qa_actions.json to the Google Sheet")
    ap.add_argument("--portal", action="store_true",
                    help="Generate the interactive qa_portal.html (inline live data)")
    ap.add_argument("--data-dir", default=DATA_DIR)
    args = ap.parse_args()

    if args.push:
        push_actions(args.data_dir)
    elif args.portal:
        out, n_live, n_hist = generate_portal(args.data_dir)
        print(f"Portal written: {out}")
        print(f"  inlined {n_live} live actionable rows (full history {n_hist} in qa_history.json).")
    else:
        lp, hp, n_live, n_hist, n_act = build(args.data_dir)
        print(f"Live: {n_live} actionable · History: {n_hist} · pending saved actions: {n_act}")
        print(f"  wrote {lp}")
        print(f"  wrote {hp}")
