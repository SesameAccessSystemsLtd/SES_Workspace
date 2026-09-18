---
From: hermes
To: hermes
Type: asana_write
Status: COMPLETE
Completed: 2026-09-07T00:00:01
Note: Edge-case task 1218237034241470 created in SAS Edge Cases (New Issues). HTTP 201.
Priority: normal
Created: 2026-09-07T00:00:00
---
## Request
Action: create_task
Project GID: 1214604066893979  (SAS Edge Cases intake)
Section GID: 1214603906356078  (New Issues)
Task Name: DRAWINGS importer search must use FULL drawing-pack name (e.g. 1595.1008.D1) - abbreviated search returns nothing
Assignee: Giles (1142108324926610)
Custom fields: ACTOR=HUMAN, Issue Type=System Issue, Priority=Medium, Status=New, Department Impacted=[Purchasing]

## Summary
Capture Giles's update (2026-09-07) as a SAS edge case: pack naming is now the FULL drawing-pack
name (e.g. "Assembly Pack 1595.1008.D1"), superseding the abbreviated code ("1595.1008.D"). The
Asana CSV DRAWINGS importer (Dropbox xlsm, "ASANA CSV 3 - DRAWINGS Importer - With Delay
Destroyer.xlsm") still searches by the abbreviated pack code, so it returns nothing for these
packs. Need to amend the importer's search/report logic to use the full pack name.

## Context
Giles: "create an edge case - we are now using the full drawing pack name rather than the
abbreviated ... what used to be found using 'Assembly Pack 1595.1008.D' now has 'Assembly Pack
1595.1008.D1' and we need to amend the drawing importer in order to correctly put this into the
search report - otherwise it pulls up nothing. This is an update to [Dropbox xlsm path]."
Destination = canonical SAS Edge Cases intake (sesameaccess.com), New Issues section.
