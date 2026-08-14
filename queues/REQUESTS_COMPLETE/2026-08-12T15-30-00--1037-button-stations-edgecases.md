---
From: hermes
To: hermes
Type: asana_write
Status: PENDING
Priority: normal
Created: 2026-08-12T15:30:00
---

## Request
Action: add_comment
Task GID: 1217366701125766
Task: "Bomb revisions being missed in process"

## Summary
Live test case (2026-08-12): processed new BOM "5.Button Stations" (subtask 1217311060107464) under COMPLETED primary "Karol Generate Entire BOM - 1037 - Langley Street" (1213147826874923). Confirmed the pipeline processes a new BOM subtask regardless of the closed parent. 22 parts staged (21 ORDER, 1 PICK). Captured 2 new edge cases to this task:
1. Closed-primary-with-new-live-BOM-subtask works, but creates an ALL BOMS root that sits alongside the legacy flat vendor-groups import (4.Gate and Support has no ALL BOMS root / no Parts Control integration) — structural inconsistency for the same project.
2. 4 parts duplicated across cost centres (1037.034.001 in Powder Coating+NBSS/Laser Cut; 1037.1016.1.003/.007/.X.001 in Powder Coating+Laser Cut) -> risk of duplicate Asana part tasks + double ordering if committed without dedupe overrides.

Per Giles directive: append these findings to this edge-case task as a comment. No task status change.

## Context
Giles: "lets use this as a live test case a run it through the process capturing and reporting any edge cases and updating the edge case task already created regarding this workflow."
