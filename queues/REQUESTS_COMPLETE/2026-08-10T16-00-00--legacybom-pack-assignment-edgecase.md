---
From: hermes
To: hermes
Type: asana_write
Status: COMPLETE
Priority: normal
Completed: 2026-08-10T16:07:00Z
Note: task created gid=1217333672548481 in Edge Cases section, custom fields set
Created: 2026-08-10T16:00:00Z
---

## Request
Action: create_task
Project GID: 1147424298774210        # Giles Ai Dev Work
Section GID: 1216357153626617        # ⚠️ Edge Cases
Task Name: Parts→Build Pack assignment — ingest design-team Legacy BOM spreadsheets
Custom Fields:
  Priority: Medium    (1216356798129224)
  Status: Not Started (1216356799734181)
  Effort: L           (1216357254285784)
  Area: Edge Cases    (1216356954621564)

## Summary
Create a development task under Edge Cases proposing a process to assign build-pack GIDs to
PARTS CONTROL parts from the design team's Legacy BOM spreadsheets
(`SAS Dropbox\3. Projects\America St No.1\2.2. Assembly Drawings\Legacy BOM's\`).

- One `.xlsx` per pack; filename = pack name; `PARTS LIST` sheet with ITEM/QTY/PART NUMBER/VENDOR/COST CENTER.
- Only rows that are **orderable BOM items** (part number + vendor/cost-centre/stock) get mapped.
- Auto-EXCLUDE design artifacts: assembly groupings, "Assembly/Welded by Sesame", `_MIR` subtrees.
- Candidate GID list is staged for Giles review (HITL) before any Asana write.
- Migrates free-text pack names → verified pack task GIDs (fixes the 1143 unassociated / 959 text-link base).

## Context
Giles instructed (2026-08): review the Legacy BOM's folder and propose a process to reliably map
BOM items to build packs, saved as a development edge case. Initial data assessment: of 2146 parts_control
rows, 44 linked by GID, 959 by text name, 1143 unassociated (424 open). Full process write-up:
`SES_Workspace\_drafts\LegacyBOM-Pack-Assignment-Process.md`.

## Guardrail assessment
- Duplicate check: no existing task with this name (assumed; verify before commit).
- Forbidden GID: none targeted (project 1147424298774210 is active).
- Destructive: no — creates a single planning task.
- HITL: pending Giles approval to execute.
