---
From: hermes
To: hermes
Type: asana_write
Status: COMPLETE
Completed: 2026-08-14T12:45:00
Note: created 12/12 subtasks + parent (1217539521989331) in SAS Edge Cases New Issues; verified section/custom fields
Priority: high
Created: 2026-08-14T12:40:00
---

## Request
Action: create_task (edge case) + create_task (12 subtasks)
Project GID: 1214604066893979  (Institutional Ticketing System / Edge cases, sesameaccess.com)
Section: New Issues (1214603906356078)
Custom fields:
  ACTOR (1216261763561518) = AGENT (1216261763561520)
  Issue Type (1214603906356101) = System Issue (1214603906356103)
  Priority (1214603906356092) = High (1214603906356094)
  Status (1214603906356688) = New (1214603906356689)
  Department Impacted (1215827953635963) = Operations (1215827953635970) + Purchasing (1215827953635965)

## Summary
Create ONE edge-case task in the SAS Edge Cases intake titled:
  "1588 Scissor Lift: 12 parts never imported at Stage 5 — missing from ALL BOMS hierarchy"

with notes describing (a) what happened and (b) future mitigation. Then create
12 SUBTASKS under it — one per missing part — each named with a MISSING tag and
the part number + description + qty + supplier, so Giles can assign each subtask
to the build pack as "missing".

## Subtasks (12)
1. MISSING 1588.1025.A.004 — AP-C3 weld plates (qty 8, Bearing Supplier)
2. MISSING 1588.1025.A.011 — Oil Filled Bronze Flanged Bush (qty 4, Bearing Supplier)
3. MISSING 1588.1025.A.020 — Oil Bearing (qty 14, Bearing Supplier)
4. MISSING 1588.1025.A.021 — Bearing NUTR 30 (qty 8, Bearing Supplier)
5. MISSING 1588.1025.A.036 — Ram.Bearing.Base (qty 2, Bearing Supplier)
6. MISSING 1588.1025.A.037 — Ram.Bearing.BaseTop (qty 2, Bearing Supplier)
7. MISSING 1588.1025.A.087 — Oleorama Hydraulic T Manifold (qty 1, Sesame)
8. MISSING 1588.1025.A.089 — G 1/4 Steel Blanking Plug (qty 4, RS Components)
9. MISSING 1588.1025.A.091 — Car belt (qty 2, Sesame)
10. MISSING 1588.1025.A.Actuator — Low-profile hydraulic cylinder stroke 141mm (qty 2, Hydraulics Supplier)
11. MISSING 1588.1008.Y.EasyChain[38mm] LowProfile — Easy Chain (qty 1, IGUS)
12. MISSING 1588.1400 30 EXTERNAL CIRCLIP FOR 30MM SHAFT (qty 20, Bearing Revolution)

## Context
Investigation (2026-08-14) found the 1588 America 1 Scissor Lift BOM import
(Stage 4 file ...1588_-_America_1_Scissor_BOM_20260702_1632) planned 59 parts;
12 buy-in parts from 1588.BOM.4.ScissorLift.Sesame.xlsx were silently dropped at
Stage 5 (Asana commit). The Bearing Supplier / IGUS / Hydraulics Supplier /
Bearing Revolution vendor-group tasks were never created. These parts have no
bom_requirements or parts_control rows and no Asana task — they must be flagged
as missing to the build pack. Mitigation: add a Stage 5 completeness check
(post-commit verify count / detect dropped buy-in vendor groups), add a
pre-commit vendor-group coverage warning, and log phantom-GID / partial-commit
recovery guidance. Giles instructed: file as edge case with parts as subtasks
assignable to the build pack as missing. DO NOT re-run BOM commit.
