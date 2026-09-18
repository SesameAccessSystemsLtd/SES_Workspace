# Standing rule — validate independently; Claude cannot see the database

**Date:** 2026-09-18 · **Applies to:** every agent acting on code or analysis Claude
produces for this workspace. **Status:** STANDING.

## The constraint

Claude (Cowork session, cloud container) can **read and write files** on the local
machine through the device bridge, but **cannot execute anything there** — no shell,
no python, no sqlite. `db/ops.db` is 1.2 GB and cannot be staged to the cloud
container. Therefore:

> **Claude has never run a query against `ops.db` and never will.**
> Every statement Claude makes about its schema, its contents or its counts is an
> inference from reading generator scripts — not an observation.

Claude also cannot see Asana, Telegram or the VPS state except through what an agent
reports back. Anything Claude asserts about live state is a hypothesis.

## The rule

1. **Treat Claude's code as a hypothesis, not a deliverable.** It compiles; that is
   all that is known about it until it runs against real data.
2. **Never accept Claude's predicted numbers as the expected result.** Derive the
   expectation from the database with your own query, then compare. If Claude says
   "confirm X drops to ~6", the correct action is to compute the true figure
   independently and report it — not to check whether it equals 6.
3. **A test fixture written by the same party that wrote the code proves nothing
   about a join key, a column name or an attribution rule.** Those can only be
   checked against the real table.
4. **Report disagreements as findings, not as errors to be smoothed over.** Every
   defect in the 2026-09-17/18 run was found this way and none other.
5. **Stop before any write** (Asana, ops.db, VPS, DNS) if a validation step
   disagrees with what Claude predicted. Report and wait.
6. **Say when you cannot verify something**, as was done for the fresh-import test
   leg and the non-existent trap row. Do not substitute a plausible answer.

## Errors this rule exists because of (2026-09-17 → 18)

Claude's, in order. All were found by independent validation, none by Claude.

| # | Error | Consequence |
|---|---|---|
| 1 | Assumed `build_assembly_packs.completed`; actual is `is_completed` | Silent-wrong: every pack counted open, S7 permanently red with no visible cause |
| 2 | Ignored `parts_control.is_completed` | Completed parts counted as live blockers across 2,976 rows |
| 3 | Stages had two states, so "no data" scored as *done* | 15 BOM-less projects showed S4 green |
| 4 | `mark()` emitted no action for a *completed* stage | Fully-green projects showed `—`, losing the report-push action — the stated objective of the view |
| 5 | Joined `parts_control` on `sesame_pn` prefix; never checked for a `project_code` column | 1588: 538 rows vs 219; S5 blocker 282 vs 6 |
| 6 | **Kept a prefix fallback after proving prefix matching wrong** | Reintroduced the same defect: 534 rows mis-attributed, 323 onto 1588. Worst error of the run — a disproved mechanism was retained "for visibility" |
| 7 | Tested against a synthetic DB built from Claude's own assumptions, including an invented row (`1588.9999.Z.001`) presented as proof | The fixture agreed with the code because both came from the same wrong assumptions |
| 8 | Asserted "213 of 219 is impossible" | Built on two prefix-contaminated figures; the real data was perfectly consistent |
| 9 | Predicted 1588's blocker would fall to ~6 after the join fix | It did not, because of #6 — a prediction stated as an expected result |
| 10 | Attribution change silently dropped the `0000` bucket | Orphan rows and packs became invisible to the view whose purpose is visibility |
| 11 | Counted rows with NULL `sesame_pn` as one distinct part each | Caught by `--verify` on its first run, before shipping |

Pattern: **Claude filled gaps in its knowledge with invention instead of saying it
did not know.** Every one of these was discoverable only by querying the database.

## The guard that now exists

`project_cycle.py --verify` recomputes every project's `rows`, `parts`,
`rows_no_pack` and `rows_no_order` with **independent SQL** and diffs it against what
the script's own logic produced. Exit 0 and `"PASS": true` means they agree; any
other exit lists the exact project and field that disagree.

This replaces Claude's judgement as the gate. It found error #11 on its first run.

**Before any change to attribution, stage rules or counting, `--verify` must pass on
the real `ops.db`.** A fixture passing is not evidence.
