# BOM Pipeline Edge Case: Duplicate Stage 5 Imports

## Affected: 1588 America 1 Scissor BOM | Date: 2026-07-02

## What Happened

Stage 5 was run 8 times. Each run created a fresh `ALL BOMS` root task in Build Orders — 8 duplicate task trees.

## Why

**Stage 5 has no idempotency check.** Every `--commit` creates a new root `ALL BOMS - {project}` task from scratch. There is no pre-flight check for an existing root with the same project name.

**Compounded by sandbox timeout.** Each run succeeded in Asana (API calls went through) but the Hermes sandbox killed the script before it could report completion. Retries created fresh roots from the same manifest.

## Fix (Shortest Path)

Before creating the root task in `BOM_IMPORT_4_Asana_v4.py` (or `run_mrp_pipeline.py`), query Build Orders for an existing task named `ALL BOMS - {project}`. If found, print a warning and exit cleanly.

~10 lines of Python. Solves the problem entirely.

## Fix (Medium-Term)

Write a marker file `pipeline_runs/{run}/.stage5_completed` after successful commit. Orchestrator checks for it before running Stage 5 again.

## Fix (Long-Term)

Make Stage 5 idempotent: find existing root by project name, add only missing subtasks. Turn "create" into "sync".
