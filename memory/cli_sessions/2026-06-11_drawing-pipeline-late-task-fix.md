# CLI Session — Drawing Pipeline: Late-Added Drawing Task Fix
**Date:** 2026-06-11
**Topic:** Drawing pipeline missed 6 parts for job 1670 because a drawing task was absent from Asana at run time

---

## What Prompted This Session

Drawing pipeline ran for job 1670 on 2026-06-09 and updated 164 parts successfully. Post-run review found 36 parts not updated in DB or Asana (DB now shows 32 with NULL build_assembly_pack_gid). Root cause traced to drawing task `1670.1055.A.Hand Held` not existing in Asana's Build Schedule project at the time of the run.

The task has since been added to Asana: GID `1215575033042714`, in Drawing Control project (`1142754335688166`).

User also flagged this as an edge case requiring pipeline-level handling for future occurrences.

---

## Diagnosis

**Root cause:** Stage 1 (`01_populate_assembly_packs.py`) scans only the Build Schedule project (`852956628490855`). A task that exists only in Drawing Control is silently absent from Stage 1 results — no exception, no warning, just not found. Stages 2, 3, and 4 never process that pack as a result.

**Parts affected:**
- `1670.1055.A.004`, `.030`, `.031`, `.032`, `.037`, `1670.1055.A17`
- All have `build_assembly_pack_gid = NULL` in `parts_control`
- `build_assembly_packs` table has zero entries for `1670.1055.*`

**Other 26 unlinked 1670 parts** (not 1055.A): separate issue — short NBSS codes, normalization misses, and a few parts whose DB state was cleared by a prior `--reset` run after Asana write. These are not caused by the missing drawing.

---

## Bot Proposals Reviewed

The bot made two proposals (v1 and v2). Both were reviewed and critiqued in session.

**Bot v1:** Add try/except to Stage 2 around PDF download. Wrong — the failure is in Stage 1, before Stage 2 ever runs.

**Bot v2:** Add try/except to Stage 1 + log to `_missing_assembly_tasks.txt` + `--reprocess-unscanned-tasks` orchestrator flag. Improved but still wrong:
- Try/except doesn't help — Stage 1 never raises an exception for a missing task
- Missing task detection requires DB comparison (parts_control vs build_assembly_packs), not error handling
- `--reprocess-unscanned-tasks` can't auto-query Asana by pack reference (no reliable search); needs a GID
- Neither proposal addressed project disambiguation (Drawing Control vs Build Schedule)

---

## Decisions Made

1. Detection belongs in Stage 1, after the scan, via DB comparison
2. Recovery requires a task GID as input (only reliable handle in Asana)
3. Project disambiguation is mandatory — Drawing Control task ≠ Build Schedule task
4. Stage 4 dedup (write_log) makes re-runs safe; no risk of double-writing

---

## Code Changes

### `scripts/01_populate_assembly_packs.py`
**Added `--task-gid <GID>` flag**
- Accepts any Asana task GID regardless of which project it belongs to
- Calls `GET /tasks/{gid}?opt_fields=memberships.project.gid` to determine project membership
- `classify_task_project()` returns one of: `build_schedule`, `drawing_control`, `both`, `other`
- Logs the classification clearly so the operator knows whether this was a true miss or not
- Upserts the task into `build_assembly_packs` in all cases

**Added `write_missing_packs_report()`**
- Runs after every Stage 1 execution (normal scan or `--task-gid`)
- Queries `parts_control` for all parts matching the job prefix
- Derives pack prefix (first 3 dot components) for each part
- Compares against `build_assembly_packs` entries
- Writes `data/{job}_missing_packs.csv` listing any pack prefix with no registered task
- Prints a warning to console if gaps found, with the `--task-gid` recovery command

### `scripts/run_drawing_pipeline.py`
**Added `--reset` flag**
- Passed to Stage 3 (`02_map_parts_to_packs.py`) only
- Stage 3 `--reset` clears all `build_assembly_pack_gid` values for the job before re-mapping
- Essential for clean re-runs after a late-added drawing (otherwise Stage 3 skips already-mapped parts)
- No other stage is affected

### `PN_EDGE_CASES.md`
**Added Edge Case 10: Late-Added Drawing Task**
- Documents the 1670.1055.A incident as the reference example
- Full step-by-step recovery command sequence
- Explains why Stage 4 re-runs are safe (write_log dedup)
- Key rule: Drawing Control task URL → extract GID → use `--task-gid`

---

## Recovery Procedure for 1670.1055.A (to be run)

```bash
# 1. Register the drawing task (Drawing Control GID):
python scripts/01_populate_assembly_packs.py --job-prefix 1670 --task-gid 1215575033042714
# Expect: INSERTED, project_type = drawing_control or both

# 2. Re-extract BOMs (pulls PDF from newly registered task):
python run_drawing_pipeline.py --job-prefix 1670 --stop-after 2

# 3. Re-map with reset (dry-run first):
python scripts/02_map_parts_to_packs.py --job-prefix 1670 --reset --dry-run
# Confirm 1670.1055.A.* appear as "matched", then live:
python scripts/02_map_parts_to_packs.py --job-prefix 1670 --reset

# 4. Write to Asana (dry-run first):
python scripts/03_write_to_asana.py --job-prefix 1670 --dry-run
# Confirm ~6 pending parts, 164 already-done. Then live:
python scripts/03_write_to_asana.py --job-prefix 1670
```

---

## Bot Knowledge Update Sent

Full prompt provided to user to update bot with:
- What was changed and why
- That try/except is wrong for this failure mode
- Project disambiguation logic and why it matters
- The `--task-gid` + `_missing_packs.csv` pattern
- Key rule: Drawing Control task URL → use `--task-gid`

---

## Files Changed Summary

| File | Change |
|------|--------|
| `.openclaw/workspace/skills/drawing_pipeline/scripts/01_populate_assembly_packs.py` | `--task-gid`, `classify_task_project()`, `write_missing_packs_report()` |
| `.openclaw/workspace/skills/drawing_pipeline/scripts/run_drawing_pipeline.py` | `--reset` flag, passes to Stage 3 only |
| `.openclaw/workspace/skills/drawing_pipeline/PN_EDGE_CASES.md` | Edge Case 10 added |
| `.openclaw/agents/personal_planner/LOG OF PROGRESS by CLAUDE.txt` | Change record appended |
| `.openclaw/agents/personal_planner/cli_sessions/` | New folder created for CLI session logs |
