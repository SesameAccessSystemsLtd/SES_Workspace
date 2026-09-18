# Gate findings — project_cycle.py, runbook prompt 2

**Date:** 2026-09-17 · **Run by:** CLI Hermes against live `ops.db` (read-only)
**Verdict:** gate held, two defects found, one fixed, one open.
**Related:** `skills/os_portal/project_cycle.py`, `docs/dev-register-setup-runbook-2026-09-17.md`

162 projects. Row counts: `build_assembly_packs` 2641 · `parts_control` 2976 ·
`procurement_tasks` 4148 · `production_impact` 410 · `suppliers` 432.

Two of three sanity rows matched ground truth (1595 complete, 1588 mid-flight).
The third leg — a freshly-imported project — could not be tested, correctly
reported rather than invented: the newest imports (1569, 1591) are already
mid-cycle, and 1601/1602 have no BOM at all.

---

## Defect A — two-state stages hid work · FIXED 2026-09-17

**Symptom 1.** Projects 1005, 1365, 7003 rendered `#######` (all stages done) with
next action `—`.

**Symptom 2.** 15 projects with no BOM at all rendered `...#...` — S4 "Steel profile
in" green on a project with zero parts.

**Root cause, both symptoms:** stages had two states (done / open) where three are
needed. A stage with nothing to judge it by was being scored as *done*, and
`mark()` only emitted a next action when a stage was *not* done — so a fully-green
project produced no action at all, including the one action that matters most.

**Fix applied:**

1. Third state `nodata`, rendered `?`. A stage is assessable only when its evidence
   exists: S2/S3/S5/S6 require parts; S4 requires packs; S7 requires both. 1365 now
   reads `.??#???` with "No BOM imported — chase Karol", which is the truth.
2. `report_ready` flag. When S7 is done the next action becomes
   **"▶ Push the full project report to Asana"** rather than `—`. A closed cycle is
   not "nothing to do" — it is the one row where the report is worth pushing, which
   is the stated objective of the whole view.
3. `—` now means one thing only: every stage assessable, none open, report already
   pushed.

## Defect B — WRONG JOIN KEY (not a labelling issue) · FIXED 2026-09-17

Prompt 2b settled it, and inverted the hypothesis. `parts_control` **has a
`project_code` column** and `project_cycle.py` was not using it — attribution was by
the leading four digits of `sesame_pn`.

For project 1588:

| metric | by pn-prefix | by project_code |
|---|---|---|
| total rows | 538 | 219 |
| done | 212 | **213** |
| live rows without a pack | **282** | **6** |
| distinct part numbers | 212 | 212 |

Two things follow. First, `done` is *higher* under `project_code`, so the two sets
are **not nested** — prefix matching both over-counts (≈319 foreign rows sharing a
`1588.x` number) and under-counts (rows whose `project_code` is 1588 but whose part
number is not). Second, the S5 blocker count was inflated 47× (282 vs 6), which
would have sent the drawing pipeline at a project that is essentially mapped.

`parts_control` is **one row per part-per-operation** — Anodising, Laser Cut, Marine
Lacquer are separate rows for one part number, mirroring Asana's process tasks.
212 distinct parts across 219 rows. Not duplicates, by design.

**Fix applied:**

1. Attribution is by `project_code`. Where it is empty, the pn-prefix is used as a
   fallback and counted in a visible `fb` column — never silently merged.
2. `parts` = distinct part numbers; new `rows` column = process task rows. Stage
   gating counts **rows**, because a process row is the actionable Asana task.
   Both `rows_no_pack` and `parts_no_pack` are carried in the JSON.
3. New `--reconcile` mode quantifies the divergence across **all** projects, which
   answers CLI Hermes's stated caveat that only 1588 was verified. It reports rows
   with no `project_code`, rows where `project_code` disagrees with the prefix, and
   the worst-diverging projects in both directions.

**Corrections owed both ways:** my "213 of 219 is impossible" was wrong — it was
built on two prefix-contaminated figures, and 6 live parts is perfectly consistent.
CLI Hermes separately self-reported a parser bug in its first re-run check
(split-on-double-space testing the project code rather than the bar). Both are noted
so the provenance of every number above is clear.

## Defect B (original framing) — the `parts` column does not reconcile · SUPERSEDED

`project_cycle.py` buckets every `parts_control` row by the first four digits of
`sesame_pn`. For 1588 it reports `parts=538, noPack=282`. Ground-truth queries
returned `tot=219, done=213, nopack=94`.

Both cannot be right, **and the ground-truth figures are internally inconsistent**:
219 total with 213 done leaves 6 live parts, which cannot yield 94 without a pack.
So the comparison queries were counting different populations — most likely a
`project_code` filter against a `sesame_pn` prefix match, with `project_code` NULL
on many rows.

This does not affect stage *direction* (1588 is correctly blocked at S5) but it does
make the `parts` column untrustworthy next to `noPack`/`noOrd`. **Resolve before the
page is built**, because a headline count that disagrees with the detail is how a
dashboard loses credibility.

Resolution query is in the runbook, prompt 2b.

## Noted, not defects

- **S4 reads the `steel_profile` text column**, not `stage_of_production` (NULL for
  2492 of 2641 packs). Green means the marker is present, not that the steel BOM
  imported cleanly. Now documented in the code.
- **~400 packs carry `project_code = NULL`.** They are invisible to any
  project-keyed view, including this one. Separate data-health item — logged to the
  dev register, not fixed here.
- **No project reached S7 in the live run** beyond the handful noted. Worth watching
  whether packs are ever marked `is_completed` in practice, or simply accumulate.
