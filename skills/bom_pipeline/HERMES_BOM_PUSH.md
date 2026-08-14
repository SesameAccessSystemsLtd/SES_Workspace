# Hermes capability: BOM Push (Karol Generate)

## What it does
Pushes a "Karol Generate Entire BOM" control task through the standard BOM import
route (`run_mrp_pipeline.py`, Stages 1–5). Same Golden Index / stock check /
staging as every other BOM. Embeds source control (control task + Dropbox BOM) and,
on commit, assigns the root and targets the right project.

## How to run
Entry point (use the SES venv python):
```
C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe ^
  C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\bom_push.py --job <manifest>
```
- Default (no flag) → preflight + **Stage 4 only. NO Asana writes.** Produces the
  staging Excel and Telegram-notifies Giles the path.
- `--commit` → Stage 5 (creates Asana tasks) + assigns root + adds to project.
  **Only after Giles approves the Stage 4 Excel.**
- `--preflight` → validate inputs and exit.

Current manifest: `--job 1365_serjeants_inn`

## HITL rule (hard)
Never run `--commit` without Giles's explicit go-ahead on the Stage 4 Excel.
Default behaviour writes nothing to Asana.

## Trigger
- No_agent Hermes cron job named exactly: **`BOM Push (Karol Generate)`**
  Command: the entry point above with `--job 1365_serjeants_inn`.
- Telegram: **`/run_bom_push`** (mapped in `telegram_commander.py`) fires that job.
- After registering/changing the job: `hermes gateway restart`.

## Where the files are (all under skills/bom_pipeline/)
| File | Purpose |
|---|---|
| `bom_push.py` | The runner (thin wrapper over `run_mrp_pipeline.py`) |
| `jobs/1365_serjeants_inn.json` | Per-job manifest (resolved inputs) |
| `run_mrp_pipeline.py` | The traditional pipeline it calls (Stages 1–5) |
| `pipeline_last_run.json` | Written at Stage 4; holds `stage4_file` + `awaiting_commit` |
| `BOM_PUSH_README.md` | Full usage + manifest field reference |
| `2.Stock Controls/Merged_Golden_Index_Rev4.csv` | Golden Index (stock → KAN) |

Also: `skills/purchasing/telegram_commander.py` maps `/run_bom_push`.

## Fixed facts
- Stage 5 target (standard): Build Orders `848657415032331`.
- Source BOM files: read-only from `C:\Users\GilesHugo\SAS Dropbox\` (never write/delete).
- Asana token: `ASANAconfig.json` in this folder (or `ASANA_PERSONAL_ACCESS_TOKEN`).
- Steel Profile cost centre is intentionally excluded (managed separately).

## New Karol BOM
Copy a manifest in `jobs/`, fill in project name, control-task URL, Dropbox URL and
the resolved `SAS Dropbox\…` parts folder, then run `--job <new_manifest>`.
