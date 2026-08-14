---
From: hermes
To: hermes
Type: asana_write
Status: COMPLETE
Completed: 2026-08-13T23:06:47
Note: Task created gid 1217475713606870 in Giles Ai Dev work > Data Health, Area=Data Health, Priority=Medium. exit ok.
Priority: normal
Created: 2026-08-13T16:25:25
---

## Request
Action: create_task
Project GID: 1147424298774210   (Giles Ai Dev Work)
Section GID: 1216357256092340   (🗄️ Data Health)
Custom fields: Area = Data Health (1216356954621557:1216357256092400 check), Priority = Medium
Task name: Map all suppliers to their phone numbers (centralised supplier phone index)

## Summary
Create a task in the Giles Dev project (Data Health section) to build a centralised
supplier → phone-number index, consolidating three sources:

1. **Asana supplier directory** — the supplier directory inside Asana (mirrored to the
   local ops.db `suppliers` table; currently `contact_phone` is empty for 0/429 rows).
2. **RingCentral** — call-note metadata carries caller numbers (e.g. "Thameside 01784 449326"),
   held in the shared Sesame Access contacts repository.
3. **Shared Sesame Access contacts repository** — where many supplier phone numbers live.

Goal: pull the phone data down into the local `suppliers` table so we can:
- accurately link a caller phone number → supplier
- then supplier → its open orders / parts / Asana tasks
- use it in the RC-notes pipeline to infer "this call is about supplier X → likely one of
  these orders" and propose task/reference/funds/parts updates.

Found via: purchasing email/RC-notes work 2026-08-13. Ralph the parser QA + RC-notes
mapping surfaced that supplier phone numbers are absent from ops.db, blocking the
phone-number → supplier → order inference.

## Context
Giles instructed 2026-08-13: "raise a task within the Giles Dev project to look at mapping
all of our suppliers to their phone numbers." This is a data-health build that unblocks the
RC-notes phone matching and improves quote/order task finding. No supplier phone data exists
in ops.db today (0/429 rows), so this is a data consolidation + ingestion task first.
