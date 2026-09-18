import os, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(r'C:/Users/GilesHugo/.hermes/.env'), override=True)
tok = os.getenv('ASANA_PERSONAL_ACCESS_TOKEN')
assert tok, "No token"
h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

notes = """⚠️ DECISION REQUIRED — FOR STEVE REVIEW ⚠️

Steve has asked Giles to STOP dev work. This task gives full visibility of the work and the number of decisions Giles is currently carrying, so Steve can review and explicitly green-light only what should proceed.

Giles will create a subtask for each item Steve approves. Please mark a decision on each: ✅ proceed / ⏸ hold / ⬜ not in scope.

(Work captured live from Asana, 2026-08-26.)

══════════════════════════════════════════════
PART A — EDGE CASES TO RESOLVE (10)
══════════════════════════════════════════════
A1. Langley 1037: 41 legacy-BOM parts not pack-mapped (https://app.asana.com/1/357939803556123/task/1217827390379336)
A2. Review procurement approval workflow bottleneck (https://app.asana.com/1/357939803556123/task/1214604066965414)
A3. ARCHITECTURE — multi-user / multi-Harness access-control direction (https://app.asana.com/1/357939803556123/task/1217449003623329)
A4. 1588 Scissor Lift: 12 parts never imported at Stage 5 (https://app.asana.com/1/357939803556123/task/1217539521989331)
A5. Fix inventory management system sync errors [IN PROGRESS] (https://app.asana.com/1/357939803556123/task/1214604066965415)
A6. Implement automated vendor onboarding [BLOCKED] (https://app.asana.com/1/357939803556123/task/1214604066965416)
A7. Initial review of existing edge cases backlog (https://app.asana.com/1/357939803556123/task/1216357441499723)
A8. Establish edge case -> fix task linkage pattern (https://app.asana.com/1/357939803556123/task/1216357256934410)
A9. Parts→Build Pack assignment — ingest design-team Legacy BOM spreadsheets (https://app.asana.com/1/357939803556123/task/1217333672548481)
A10. BOM Rev Reconciliation — scissor-lift vs entire-system split + Column M (https://app.asana.com/1/357939803556123/task/1217376120243649)

══════════════════════════════════════════════
PART B — DEV WORK (44 open, grouped by area)
══════════════════════════════════════════════
B1. Paths / Migration (5)
   · Make db_writer.py use path_config (fail fast if DB missing)
   · Audit and resolve 63+ .openclaw hardcoded paths across skills/
   · Migrate SAP (Asana writer) from OpenClaw into Hermes
   · Reconcile manifest cron entries with real script filenames
   · Correct migration_state flag and formally retire OpenClaw

B2. Data Health (6)
   · Build db_write_log rotation (90-day retain, archive, VACUUM)
   · Seed ops.db.golden_index from Golden Index CSV
   · Resolve stock_inventory vs golden_index overlap
   · Populate or document empty tables (po_lines, supplier_deliveries, etc.)
   · Eliminate dual-copy Golden Index CSV (split-brain hazard)
   · Map all suppliers to their phone numbers (centralised supplier phone index)

B3. Ordering / Inventory (5)
   · Build reorder evaluator (stock_qty <= trigger_order_qty -> ORDER FOR STOCK)
   · Populate po_lines from existing POs (line-level traceability)
   · Normalise vendor names with alias table
   · Fix arrived-stock double-count guard (idempotency for REQUESTS/)
   · Edge capture — Lasercut order propagation assessment

B4. VPS / Webhooks (4)
   · Create Linux VPS deployment plan
   · Build Asana webhook receiver
   · Replace nightly sync with cursor-based conflict-aware sync
   · Migrate Hermes config into SES_Workspace (portable setup)

B5. Documentation (4)
   · Reconcile purchasing/SKILL.md to current reality
   · Reconcile SYSTEM_MANIFEST.yaml against live schema
   · Update uploaded snapshots and design docs to current state
   · De-hardcode Gemini from 02_llm_parser.py and daily_brief.py

B6. Review / Feedback (4)
   · Establish monthly architecture review rhythm
   · Create and maintain Edge Cases log process
   · Build in-production validation check (deployment review step)
   · Document feedback loop: build -> UAT -> production -> monitor

B7. Giles-assigned active items (5)
   · Edge capture — Lasercut order propagation assessment
   · Free AI process recorder
   · Asana Triage Script — Claude home
   · Process Change — Rich procedural performance & Asana book-in workflow
   · Update for Steve on Giles Hermes Nav HTML system

══════════════════════════════════════════════
DECISION MATRIX
══════════════════════════════════════════════
| # | Item | STEVE'S DECISION |
|---|---|---|
| A1 | Langley 1037 legacy-BOM parts not pack-mapped | |
| A2 | Procurement approval workflow bottleneck | |
| A3 | Multi-user / multi-Harness access-control direction | |
| A4 | 1588 Scissor Lift: 12 parts never imported at Stage 5 | |
| A5 | Inventory sync errors (in progress) | |
| A6 | Automated vendor onboarding (blocked) | |
| A7 | Initial review of edge cases backlog | |
| A8 | Edge case -> fix linkage pattern | |
| A9 | Parts→Build Pack — legacy BOM spreadsheets | |
| A10 | BOM Rev Reconciliation | |
| B1 | Paths / Migration (5) | |
| B2 | Data Health (6) | |
| B3 | Ordering / Inventory (5) | |
| B4 | VPS / Webhooks (4) | |
| B5 | Documentation (4) | |
| B6 | Review / Feedback (4) | |
| B7 | Giles-assigned active items (5) | |
"""

project_gid = "1147424298774210"
section_gid = "1216357153233507"  # 👁️ Review / Feedback

payload = {
    "data": {
        "name": "Dev Workload for Decision — Work Giles is actively carrying (FOR STEVE REVIEW)",
        "notes": notes,
        "projects": [project_gid],
        "memberships": [{"project": project_gid, "section": section_gid}],
    }
}

r = requests.post("https://app.asana.com/api/1.0/tasks", headers=h, json=payload)
print("HTTP", r.status_code)
d = r.json()
if r.status_code in (200, 201):
    print("TASK_GID=", d["data"]["gid"])
    print("NAME=", d["data"]["name"])
    print("URL=", d["data"]["permalink_url"])
else:
    print("ERROR:", d)
