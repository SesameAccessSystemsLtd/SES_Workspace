"""Create the BOM importer / stock-qty source edge case in SAS Edge Cases (New Issues).
Giles approved "log" (2026-09-07). Direct API creation because execute_requests.py
create_task cannot honour section + custom-field payload. Logged to asana_write_log."""
import os, json, requests, time, sys
from datetime import datetime, timezone

home = os.path.expanduser("~")
tok = ""
for ep in [os.path.join(home, ".hermes", ".env"), os.path.join(home, ".openclaw", ".env")]:
    try:
        for line in open(ep):
            if line.startswith("ASANA_PERSONAL_ACCESS_TOKEN"):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
        if tok:
            break
    except FileNotFoundError:
        continue

h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

PROJECT = "1214604066893979"          # SAS Edge Cases (sesameaccess.com)
SECTION = "1214603906356078"          # New Issues

name = "BOM importer stock-qty source: ensure live STOCK QTY checked at commit + correct Trigger-field label"
notes = (
    "REPORTED by Giles 2026-09-07. \n\n"
    "Symptom suspected: golden-index lookup pulling stock qty from the wrong GID "
    "(should read STOCK QTY 1126782469949969).\n\n"
    "AUDIT (Hermes, verified 2026-09-07):\n"
    "- All live stock readers (03_stock_check.py, BOM_IMPORT_4_Asana_v4.py) DO use the correct "
    "STOCK QTY field 1126782469949969. Live-probed 4 KAN-matched tasks; importer staged Stock QTY "
    "equalled the live field (18/22/8), NOT the ORDER QTY field. The golden_index_rev6 table carries "
    "no stock-qty column (KAN<->stocknumber mapping only), so the golden index itself never supplied stock qty.\n\n"
    "TWO REAL GAPS FOUND + FIXED in code (2026-09-07):\n"
    "[1] Stage 5 only re-read LIVE STOCK QTY when a KAN was manually amended; PICK parts otherwise "
    "trusted stale Stage-3 stock -> could pick a part already out of stock between staging and commit. "
    "FIX: BOM_IMPORT_4_Asana_v4.py now always re-queries live 1126782469949969 at --commit for every "
    "PICK part with a KAN; falls back to PROJECT ORDER if it can't verify enough stock. Removed the now-"
    "dead kan_amended branch.\n"
    "[2] 03_stock_check.py mislabelled field 1198179728033183 as 'min Stock QTY'; it is actually the "
    "Trigger Order QTY (replenish threshold) on KANBAN (no separate min-stock field exists there). "
    "FIX: constant -> TRIGGER_QTY_GID; output column renamed 'min Stock QTY' -> 'Trigger Order QTY'; "
    "staging_export PRIORITY_COLS + docstring updated in-step. Behaviour/values unchanged - label only.\n\n"
    "ACTION: code fixes applied & compile-verified. This task logs the finding + fixes for audit. "
    "Re-run of a pipeline staging will confirm the corrected column + commit-time stock re-check."
)

payload = {
    "data": {
        "name": name,
        "notes": notes,
        "projects": [PROJECT],
        "custom_fields": {
            "1216261763561518": "1216261763561519",          # ACTOR=HUMAN (Giles reported)
            "1214603906356101": "1214603906356103",          # Issue Type=System Issue
            "1214603906356688": "1214603906356690",          # Status=In Progress (fix applied)
            "1214603906356092": "1214603906356094",          # Priority=High
            "1215827953635963": ["1215827953635965", "1215827953635964"],  # Dept=Purchasing, IT Systems
        },
        "memberships": [{"project": PROJECT, "section": SECTION}],
    }
}
r = requests.post("https://app.asana.com/api/1.0/tasks", headers=h, json=payload)
if r.status_code in (200, 201):
    d = r.json().get("data", {})
    gid = d.get("gid")
    print("CREATED task", gid, "|", d.get("name"))
    # write log
    log = {"timestamp": datetime.now(timezone.utc).isoformat(),
           "action": "create_task", "task_gid": gid, "project_gid": PROJECT,
           "section_gid": SECTION, "outcome": "success",
           "note": "Created BOM importer stock-qty edge case (ACTOR=HUMAN, System Issue, High)"}
    logp = os.path.join(home, "SES_Workspace", "data", "asana_write_log.jsonl")
    with open(logp, "a", encoding="utf-8") as f:
        f.write(json.dumps(log) + "\n")
    print("Logged to asana_write_log.jsonl")
else:
    print("FAILED", r.status_code, r.text[:500])
    sys.exit(1)
