# Process Change Scope — Richard's Procedural Performance & Asana Workflow

**Status:** DRAFT for Giles's evaluation — assumptions to be reviewed
**Priority:** HIGH — action next week (Richard away for 2 weeks)
**Source findings:** `memory/richard-performance-assessment-2026-08-13.md`
**Prepared:** 2026-08-13

---

## 1. Why this is priority

Richard's most recent review (23.4.2026, next due 25 Oct) set 4 objectives — **none demonstrated as closed**, and 3 explicitly marked "not seen." The procedural-issue record (1,142 comments across 318 tagged tasks, 2019–2026) shows a **chronic, low-severity pattern** concentrated in exactly the areas those objectives target: admin discipline, stock-status updates, and chasing follow-through.

**He's away 2 weeks = the window to redesign the process around the gaps without disruption.** The goal is not to reprimand — it's to change how Asana is *used* so the book-in loop can't silently break.

---

## 2. The findings (what the data says)

Across the 564 substantive flagged comments:

| Rank | Category | Count | Root behaviour |
|---|---|---|---|
| 1 | Delayed / overdue | 121 | chasing discipline — deliveries/QC/quotes not followed through |
| 2 | Incomplete Asana admin | 89 | task closed while subtasks/fields still open |
| 3 | Stock status not updated | 88 | arrived/picked but not booked-in / stock state not reflected |
| 4 | Missing documentation | 68 | invoice/quote/delivery photo not attached |
| 5 | Wrong part/spec | 50 | wrong size/type picked or ordered |
| 6 | QC check missed | 49 | passed/built before QC sign-off |
| 7 | Order/invoice diligence | 40 | invoice processed without questioning |
| 8 | Comm/response gap | 30 | (smallest — he does respond) |
| 9 | Stock/ordering error | 29 | redundant/wrong qty |

**Key read:** the dominant cluster is **"book-in loop breaks silently"** — items arrive or get picked but the Asana state never gets closed out (categories 2+3+4+1 = ~366, 65% of the record). This is a *process/Asana-design* problem as much as an individual habit problem.

---

## 3. Initial scope of change proposed (to be evaluated)

Below are the candidate changes. **Each carries assumptions I'm making — you must evaluate the impact of each.**

---

### Change A — Enforce a single "book-in checklist" workflow
**What:** One Task Type / template for goods-in: on arrival, the workflow requires a tick-off sequence in order: *Delivered → QC checked → Stock updated → Parts moved + task closed*. The task cannot be marked complete until each step is ticked.
**Assumptions to evaluate:**
- **A1.** The current goods-in process genuinely *has* a defined QC step per part (vs parts frequently bypassing QC). If QC isn't always required, the mandatory-QC tick will be wrong.
- **A2.** One person (Richard) is the right owner of book-in for consumables *and* build parts. If book-in is shared, a single-task checklist may not match who does which step.
- **A3.** Asana can enforce ordered tick-off via subtasks/checkbox that can't be completed out of sequence. If it can't, we rely on discipline again (the very problem).

### Change B — Make "stock status" a required, visible field on the task (not a free-text comment)
**What:** A custom field `Stock State` (Arrived / Picked / Kitted / Booked-In / QC'd) that must be set, plus an automation that flags any task stuck in a state >N days.
**Assumptions to evaluate:**
- **B1.** Which fields already exist vs need creating — creating fields affects all parts tasks, so it must not collide with procurement/purchasing views.
- **B2.** Who is allowed to change the field — if multiple roles do, we need permissions, not just a field.
- **B3.** The "stuck >N days" N value — a bad default flags everything and trains people to ignore it.

### Change C — Block premature task completion
**What:** Rule/automation: a task with open subtasks or a required field blank cannot be marked Complete (block or warn).
**Assumptions to evaluate:**
- **C1.** There are cases where closing the parent is legitimate while a subtask remains (e.g. a drawing issued with a follow-up). Hard-blocking risks false friction.
- **C2.** This requires a rules engine / automation layer (Flowsana or Asana workflows) — that's a paid/configuration change with setup time.

### Change D — Chasing / overdue hygiene
**What:** A daily overdue report per goods-in task owner; auto-assigned "chase" subtasks when a delivery date passes.
**Assumptions to evaluate:**
- **D1.** Delivery dates are reliably set at order time (they often aren't — many were blank). If D1 is false, overdue flags fire wrongly.
- **D2.** Ownership of "chase the supplier" sits with the buyer (Richard) even when the part is for a project site — cross-project ownership model.

### Change E — Archive/re-tag the old 2019–2022 history
**What:** Re-tag or archive the historically-tagged tasks so the active Procedural Issue flag reflects recent behaviour only.
**Assumptions to evaluate:**
- **E1.** The tag was consistent meaning over 7 years (it likely wasn't — earlier entries are broader/generic).
- **E2.** Archiving won't lose the audit trail you need for HR/discipline history.

---

## 4. The core tension to decide

The record is 65% "book-in loop silently breaks." Two philosophies:
- **(i) Tighten the process** (A+B+C) — make it impossible to skip steps. Higher setup, changes how everyone uses Asana, more fields/rules.
- **(ii) Tighten visibility + one owner** (B partial + D) — don't force the process, but surface every un-closed loop every day to the owner. Lower disruption, but still needs the owner to close loops.

**My provisional recommendation:** start with **(ii) — visibility + D**, because it fixes the 65% without a big Asana redesign, then add **(A)** as a thin checklist once the overdue-report proves the loop is the issue. Avoid full (C) hard-blocking in the first pass.

---

## 5. Questions I need you to answer (impact of assumptions)

1. Is QC truly a required step on every goods-in, or only build-critical parts? (affects A1, C)
2. Does book-in sit with one person today? (affects A2, D2)
3. Which existing Asana custom fields / Flowsana workflows already exist so we reuse vs build? (affects B, C, D)
4. Are you comfortable archiving pre-2023 tagged history? (affects E)
5. Is the priority to *prevent* recurrence (bigger Asana change) or *surface* it faster (lighter change)? (the core tension)

---

*This document and the accompanying Asana edge-case task capture the findings + initial scope. Next action: Giles evaluates the assumptions, then we lock the change scope for implementation while Richard is away.*
