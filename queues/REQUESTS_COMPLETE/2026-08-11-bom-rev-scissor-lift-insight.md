---
From: hermes
To: hermes
Type: asana_write
Status: COMPLETE
Completed: 2026-08-11T16:58:01.229603+00:00
Note: task created 1217376120243649 (verified in Edge Cases section)
Priority: normal
Created: 2026-08-11
---

## Request
Action: create_task
Project GID: 1147424298774210  (Giles Ai Dev Work — ⚠️ Edge Cases section 1216357153626617)
Task GID: (none — create)

## Summary
Log the BOM revision-reconciliation insight discovered while building/validating
bom_rev_reconcile.py against the America One (1588) project:

1. Scissor lift (1588.1025.A.*) is a SEPARATE BOM family from Entire System — distinct
   Asana control task; the 1025.* parts are NOT in the "2.Entire System - America No.1"
   control task subtree (verified live). Running the rev engine against the wrong control
   task flags every 1025.* part as false ADD/REMOVED.
2. Multi-rev BOM xlsx (Rev1/2/3) are cumulative snapshots; Column M (Notes) is the version
   of truth ("Removed <date>[ in revN]" / "Changed <date>"), not qty.
3. Steel Profile BOM (1216623324565758, c=True) has no part tasks but still needs Giles
   to trigger its processing workflow — do not skip.
4. GID discrepancy: memory held 1214604066893979 as "Edge Cases project" but it resolves
   to "Institutional Ticketing System / Edge cases" (a personal project), NOT the SAS
   intake. Canonical edge-case target is Giles Ai Dev Work → ⚠️ Edge Cases (1216357153626617).

## Context
Discovered 2026-08-11 while building bom_rev_reconcile.py per the BOM_Push_Workflow_Assessment
and its 4 policy decisions. Validated against real 1588 Machining Rev3 file. This matters
for future BOM revisions and for correct control-task targeting.
