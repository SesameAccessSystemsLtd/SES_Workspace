---
From: hermes
To: hermes
Type: asana_write
Status: COMPLETE
Priority: high
Completed: 2026-08-26T14:35:00Z
Note: Task 1217865087720060 created in Review/Feedback (Giles Ai Dev Work)
Created: 2026-08-26T14:30:00Z
---

## Request
Action: create_task
Project GID: 1147424298774210
Section GID: 1216357153233507   # 👁️ Review / Feedback (confirmed 2026-08-26)

## Summary
Create a single compilation task in the Giles Ai Dev Work project that lists EVERYTHING Giles is currently working on across **Edge Cases to resolve** and **Dev Work**, split by area, with a visible **"DECISION REQUIRED"** comment block in the description.

Purpose: Steve has asked Giles to **stop dev work**. Giles will use this task to give Steve full visibility of how much work and how many decisions are in flight, so Steve can review and explicitly **green-light only what should proceed**.

A blank **DECISION** matrix is embedded in the description for Steve to mark per-item: ✅ proceed / ⏸ hold / ⬜ not in scope. Giles will then create subtasks against each approved decision.

## Contents (from live Asana, 2026-08-26)

### PART A — EDGE CASES TO RESOLVE (external institutional intake + Ai Dev Work Edge Cases)
A1. Langley 1037: 41 legacy-BOM parts not pack-mapped (1217827390379336)
A2. Review procurement approval workflow bottleneck (1214604066965414)
A3. ARCHITECTURE — multi-user / multi-Harness access-control direction (1217449003623329)
A4. 1588 Scissor Lift: 12 parts never imported at Stage 5 (1217539521989331)
A5. Fix inventory management system sync errors — IN PROGRESS (1214604066965415)
A6. Implement automated vendor onboarding — BLOCKED (1214604066965416)
A7. Initial review of existing edge cases backlog (1216357441499723)
A8. Establish edge case -> fix task linkage pattern (1216357256934410)
A9. Parts→Build Pack assignment — ingest design-team Legacy BOM spreadsheets (1217333672548481)
A10. BOM Rev Reconciliation — scissor-lift vs entire-system split + Column M (1217376120243649)

### PART B — DEV WORK (Giles Ai Dev Work project — 44 open tasks, grouped by area)
B1. Paths / Migration (5) — db_writer path_config fail-fast; 63+ .openclaw hardcoded paths; migrate SAP (Asana writer) into Hermes; reconcile manifest cron entries; retire OpenClaw (migration_state)
B2. Data Health (6) — db_write_log rotation (90-day/VACUUM); seed ops.db.golden_index; stock_inventory vs golden_index overlap; populate empty tables (po_lines, supplier_deliveries); eliminate dual-copy Golden Index CSV; map supplier phone numbers
B3. Ordering / Inventory (5) — reorder evaluator (trigger ORDER FOR STOCK); po_lines from POs; vendor-name alias table; arrived-stock double-count guard; lasercut order propagation assessment (edge capture)
B4. VPS / Webhooks (4) — Linux VPS deployment plan; Asana webhook receiver; cursor-based conflict-aware sync; migrate Hermes config into SES_Workspace
B5. Documentation (4) — reconcile purchasing/SKILL.md; reconcile SYSTEM_MANIFEST; update snapshots/design docs; de-hardcode Gemini from 02_llm_parser + daily_brief
B6. Review / Feedback (4) — monthly architecture review rhythm; Edge Cases log process; in-production validation check; feedback loop build→UAT→prod→monitor
B7. Giles-assigned active items (5) — Edge capture lasercut propagation; Free AI process recorder; Asana Triage Script; Process Change — Rich; Update for Steve on Hermes Nav HTML (Steven-assigned)

### Decision matrix (for Steve, embedded in description)
| # | Item | Steve's decision (✅ proceed / ⏸ hold / ⬜ none) |
|---|---|---|
| A1–A10, B1–B7 | ... | ... |

## Context
Requested by Giles after Steve asked him to stop dev work. Giles wants Steve to see the scale of in-flight work + decision load, then green-light items to proceed. Giles will create subtasks per approved decision. Targeted write: single task in Giles Ai Dev Work project.
