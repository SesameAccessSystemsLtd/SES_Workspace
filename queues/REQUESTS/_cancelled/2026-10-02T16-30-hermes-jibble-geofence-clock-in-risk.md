---
From: hermes
To: sap
Type: asana_write
Status: CANCELLED
Priority: normal
Created: 2026-10-02T16:30:00+00:00
Cancelled: 2026-11-13T10:45:00+00:00
Cancelled-by: hermes (at Giles's instruction)
---

## Cancellation record

**Cancelled 2026-11-13** at Giles's direction, unexecuted.

**Reason:** This request targets a stale Jibble API generation and login. It is no longer relevant.
Giles now has a **new Jibble trial**, and the Jibble sync pipeline is running under its own
track (currently in test/development phase) — see `data/Sesame_Lift_Locations/jibble_import/`
(last worked 2026-11-09). The geofence feedback was written against the *old* trial
configuration and does not apply to the current trial.

**Never dispatched.** No `create_subtask` was ever performed — no completed twin exists in
`REQUESTS_COMPLETE/` (repeatedly confirmed by nightly maintenance, 2–13 Nov). Nothing to undo
in Asana; the subtask was never created. This is a pure queue retirement.

**Age at cancellation:** 41 days (created 2 Oct, cancelled 13 Nov).


## Request

**Action:** create_subtask
**Parent task GID:** 1220026961278842   (GILES TESTING JIBBLE - TIME TRACKER)
**Project GID:** 850066254549317   (1.4 Purchasing Dept)
**Assignee:** Giles (1142108324926610)

## Summary
Jibble feedback — geofenced clock-in blocks legitimate logging (risk)

## Body

## Product feedback — Jibble time tracker

**Raised by:** Giles Hugo
**Parent:** GILES TESTING JIBBLE - TIME TRACKER
**Type:** Risk / usability defect
**Date observed:** 2026-10-02

### Risk

If **locations are configured** and staff are **geofenced** to those locations, then
anyone physically **outside** the defined parameters/zones **cannot clock in**.

This needs to be a deliberate design decision, not an accident of configuration. The
geofence should restrict where work is *recorded as happening*, not prevent a legitimate
start/stop of the clock.

### Observed behaviour

Geofencing was **live in the trial** and the restriction is **hard**, not advisory.
Giles attempted to log in from outside a configured zone and **was denied — unable to
start the clock**.

### Consequence

- Legitimate work performed away from a configured location cannot be timed at all.
- Staff cannot start the clock, so the day's record is lost rather than flagged.
- Encourages workarounds (clocking in from a permitted zone, or editing entries later),
  which corrupts the time data the system exists to capture.

### Suggested handling

1. **Soft-fence, not hard-block.** Allow clock-in outside a zone but **flag the entry**
   (e.g. "clocked in outside [zone] — 0.4 miles away") so the exception is visible and
   reviewable rather than impossible.
2. If a hard block is required, **exempt named roles** (mobile engineers, drivers,
   site/survey visits) or add a **"travelling / off-site" clock-in** type.
3. **Log every denied attempt** with time, user, and location, so blocked clock-ins appear
   in reporting instead of silently disappearing.
4. Confirm whether the block is at **clock-in**, **clock-out**, or both — out-blocking is
   worse, because it strands an open shift.

### Open questions for the trial

- Which locations/zones are configured, and what is the radius/parameter on each?
- Is the block configurable per user, per project, or globally?
- Where do denied attempts surface — Jibble admin report, or not at all?
- Can an admin retrospectively add a missed entry from a denied attempt?

### Recommendation

Treat geofencing as a **visibility feature first**, enforcement second. In a business where
work is frequently off-site, a hard geofence will lose more time data than it protects.

---

## Context

Giles is trialling Jibble as a time tracker. Task `1220026961278842` is the live testing
task (3 subtasks, assigned to Giles, due 2026-10-30). This feedback records a real risk
identified during the trial: geofenced locations can prevent legitimate clock-in from
outside the configured zones, which loses time records rather than flagging them.

Filed as a **subtask** of the Jibble testing task at Giles's instruction. No changes to
the parent task itself.

## Notes for SAP

- Create as **subtask** of parent `1220026961278842`, do NOT create a standalone task.
- Parent is in 4 projects; inherit **1.4 Purchasing Dept** (`850066254549317`) if a project
  must be named, otherwise let the subtask inherit from the parent.
- Assign to Giles `1142108324926610`.
- If `create_subtask` is not a supported action, fall back to `create_task` with
  `parent: 1220026961278842` set explicitly in the payload — the subtask relationship is
  the requirement, not the action name.
