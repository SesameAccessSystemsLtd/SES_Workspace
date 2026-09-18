---
From: claude_copilot
To: asana_agent
Type: asana_write
Status: STAGED — NOT QUEUED. Move to queues/REQUESTS only on Giles's explicit go-ahead.
Priority: normal
Created: 2026-09-11
---

## Purpose

A single walkthrough order in the TESTING Order numbers project so Giles can see,
in Asana, exactly what the proposed PO ledger + pack allocation writes and when.

Nothing here consumes a real PO number. The reference is deliberately marked
EXAMPLE so it can never be mistaken for a live order or adopted by the ledger
watermark (the adopter ignores any reference that is not `NNNNN-<suffix>`).

Target project : TESTING Order numbers   (692537419044434)
Target section : Order number stock      (692537419044435)

## Request 1 — create the parent order task

Action: create_task_in_project
Project GID: 692537419044434
Section GID: 692537419044435

Task name: EXAMPLE 21629-1597 - Gapp Automation - 1597 - Forrest Road Entire System

Custom fields:
  Vendor (854127216887083)        = Gapp Automation
  PROJECT NAME (1202882645384174) = 597 Forrest Road

Description:
== WALKTHROUGH — PROPOSED PO LEDGER + PACK ALLOCATION ==
This is a demonstration task. It orders nothing and consumes no PO number.

STEP 1 — NUMBER RESERVED (instant, local, no network)
  reserve() returned  21629-1597
  sequential part     21629   next in the ledger, never issued twice
  suffix part         1597    project id, because this task carries the project
                              tag. Had it not, the suffix would be today's date
                              in house format: 11926 (11 Sep 26, day unpadded)
  ledger state        reserved
  bound to            THIS task — the number is applied to a task that already
                      exists. The system does not create the order task.

STEP 2 — NUMBER APPLIED TO THE TASK
  You type 21629-1597 into the task name exactly as you do today.
  Nothing about your ordering habit changes.

STEP 3 — ORDER LINES AND PACK ALLOCATION RECORDED
  Each part parented to this order becomes a po_lines row carrying its pack
  allocation. Per-pack quantities come from the assembly pack ingest.
  See the four subtasks — each demonstrates one real case.

STEP 4 — PO EMAILED, RECIPIENT CAPTURED
  The Sent Items sync sees the PO leave, records the address it actually went
  to, and moves the ledger row to confirmed.

STEP 5 — NIGHTLY RECONCILE
  Adopts any number typed without a reservation, flags mismatches, moves the
  counter to the high-water mark.

## Request 2 — subtask A — clean split, quantity resolves

Action: create_subtask
Parent: [parent task gid from Request 1]
Task name: 6 off 1597. 7110.E.014 - Sleeve bearing WSM-1618-08 - IGUS

Description:
6 off in Assembly Pack 1597.7110.E.Covers&Connectors.
3 off in Assembly Pack 1597.7110.B.Platform-MT.
3 off in Assembly Pack 1597.7110.J.Manual MT_Barrier.

-- allocation record (what the DB stores) --
ordered qty          6
allocated            3 + 3 = 6   RESOLVES
basis                bom          per-pack quantities came from the pack ingest
confidence           direct
status               OK — no flag, no comment, nobody is interrupted

NOTE ON THE FIRST LINE: the existing report reads a total line plus per-pack
lines. Only the per-pack lines are summed. This is the normal case and needs
no attention.

## Request 3 — subtask B — even split with a remainder

Action: create_subtask
Parent: [parent task gid from Request 1]
Task name: 7 off 1597. 1010.I.087 - Thrust washer - Bearing Supplier

Description:
4 off in Assembly Pack 1597.1010.I.ScissorLift_Single.
3 off in Assembly Pack 1597.1008.D1.MachineRoomCabinet.

-- allocation record --
ordered qty          7
allocated            4 + 3 = 7   RESOLVES
basis                even_remainder
confidence           assumed      <- the pack ingest did not state per-pack qty,
                                     so this was split evenly and the odd one
                                     given to the pack with the earlier
                                     production date
status               WRITTEN, flagged as assumed in the register — not urgent,
                     not interrupting, but visible and counted

## Request 4 — subtask C — quantity does NOT resolve

Action: create_subtask
Parent: [parent task gid from Request 1]
Task name: 5 off 1597. 1008.D1.120 - Drain Cock Type A 3/4 BSP - Bes

Description:
3 off in Assembly Pack 1597.1008.D1.MachineRoomCabinet.
3 off in Assembly Pack 1597.7110.I.MT.PitTrimPlatform.

-- allocation record --
ordered qty          5
pack ingest wants    3 + 3 = 6
shortfall            1
basis                bom
confidence           direct on each pack, but the total does not balance
status               WRITTEN ANYWAY. The comment is posted, the allocation is
                     stored, and an issue is raised. Work is never blocked by
                     a mismatch.

Assignee: Giles
Due date: today

## Request 5 — comment on subtask C

Action: add_comment
Task: [subtask C gid from Request 4]

Comment:
Allocation does not resolve — flagging rather than blocking.

  Ordered            5
  Pack ingest wants  6   (3 for 1597.1008.D1.MachineRoomCabinet, 3 for
                          1597.7110.I.MT.PitTrimPlatform)
  Shortfall          1

The comment and the allocation have been written as they stand, so the report
and the pack view are not held up. Likely causes, in the order they usually
turn out to be:

  1. Order quantity was reduced at the point of ordering (stock covered one)
  2. A pack quantity changed on a drawing revision after the order went out
  3. One pack's requirement was met from another order

Resolve by adjusting the order quantity, correcting the pack quantity, or
confirming that one pack is covered elsewhere. The allocation will re-check on
the next nightly run and this flag clears itself if the numbers balance.

## Request 6 — subtask D — item moved to another order

Action: create_subtask
Parent: [parent task gid from Request 1]
Task name: 2 off 1597. 1014.P.021 - Service hatch Angle 304/2B - MOVED

Description:
2 off in Assembly Pack 1597.1014.P.LowProfileAutoGlassGate.

-- what happened --
Supplier came back: not available on this order.
The line moved to PO 21630-1597 (different supplier).

-- what the system did --
rebind()             PO binding updated, previous binding kept in the audit trail
part_supply_events   one 'notable' event: procurement_task_gid changed
allocation           followed the part to the new order automatically
this task            left in place with the history visible

NOTE: this is the case that breaks a design keyed on the PO number alone. The
allocation follows the PART, not the order, so moving a line between orders does
not lose its pack assignment.

## Context

Prepared by Claude as a reviewable demonstration of the proposed process.
Creates 1 parent + 4 subtasks + 1 comment in a TEST project only.
No live order, no real PO number, no change to any live task.
