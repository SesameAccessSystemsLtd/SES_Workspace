---
From: claude-copilot
To: asana_agent
Type: asana_write
Status: COMPLETE
Completed: 2026-09-04T12:31:28.280461+00:00
Note: 1037.1014.M.062: pack ref set; 1037.1014.M.062: desc + '1 off in Assembly Pack 1037.1014.M.Gate+Support.'; 1037.1014.M.114: pack ref set; 1037.1014.M.114: desc + '1 off in Assembly Pack 1037.1014.M.Gate+Support.'; 1037.1014.M.115: pack ref set; 1037.1014.M.115: desc + '1 off in Assembly Pack 1037.1014.M.Gate+Support.'; 1037.1014.M.116: pack ref set; 1037.1014.M.116: desc + '2 off in Assembly Pack 1037.1014.M.Gate+Support.'; 1037.1014.M.117: pack ref set; 1037.1014.M.117: desc + '1 off in Assembly Pack 1037.1014.M.Gate+Support.'
Priority: normal
Created: 2026-09-04T12:30:00
---

## Request
Action: pack_assign

## Context
1037 Langley Street pack-association backfill. These 5 parts sat unassociated
since import; 05_reconcile_pack_association.py resolved each to exactly ONE
pack (reason: single_pack) and the links were committed to ops.db on
2026-09-04. This request applies the same links Asana-side.

Origin: legacy BOM handover 2026-08-12 recorded status='no_candidates' and
stopped silently — see run_history/edge_cases/bom-pack-reconciliation-failures-2026-09-04.md

NOTE: 1037.1014.M.062 already carries the "REMOVED" title marker and is
complete. Pack is assigned for traceability only. Do NOT re-open it.

Payload in companion JSON.
