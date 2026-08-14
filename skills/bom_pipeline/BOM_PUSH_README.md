# BOM Push — Hermes-triggered wrapper (`bom_push.py`)

Pushes a **"Karol Generate Entire BOM"** control task through the **traditional
import route** (`run_mrp_pipeline.py`, Stages 1–5) with the correct source
control, assignment and target project baked in. It adds no new pipeline logic —
it's a thin wrapper so the Karol BOMs go through exactly what every other BOM
import uses.

## What it guarantees

- **Traditional route** — calls `run_mrp_pipeline.py`, same 5 stages, same Golden
  Index / stock check / staging.
- **Source control** — passes `--asana-url` (the control task) and `--dropbox-url`
  (the Dropbox BOM folder) so both are embedded in every imported task's
  description, exactly as the standard pipeline does.
- **Correct project** — Stage 5 targets **Build Orders `848657415032331`** by
  default (standard). Set `job_project_gid` in the manifest to also add the
  `ALL BOMS` root to a specific job/manufacturing project.
- **Assignment** — after commit, assigns the `ALL BOMS` root to `assignee_gid`.
- **HITL preserved** — defaults to `--stop-after 4` (staging Excel, **no Asana
  writes**). It only writes on `--commit`, reusing the Stage 4 file you reviewed.

## Files

- `bom_push.py` — the runner.
- `jobs/<job>.json` — one manifest per import (resolved, validated inputs).
  See `jobs/1365_serjeants_inn.json`.

## Manifest fields

| Field | Meaning |
|---|---|
| `project_name` | e.g. `1365 - Serjeants Inn` |
| `control_task_gid` / `asana_url` | the Karol control task (source control) |
| `dropbox_url` | the Dropbox BOM folder link |
| `parts_folder` | local `SAS Dropbox\…` path the link resolves to |
| `target_project_gid` | Stage 5 target (default Build Orders `848657415032331`) |
| `job_project_gid` | optional — auto-add root to a job project (else manual, as now) |
| `assignee_gid` | who the `ALL BOMS` root is assigned to on commit |
| `excluded_cost_centres` | e.g. `Steel Profile` (managed separately) |

## Run

```powershell
# Preflight + Stage 4 (review, no writes):
& "C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe" `
  "C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\bom_push.py" --job 1365_serjeants_inn

# After reviewing/approving the Stage 4 Excel — commit (Stage 5 + assign + project):
& "C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe" `
  "C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\bom_push.py" --job 1365_serjeants_inn --commit
```

## Hermes trigger

Registered as a **no_agent** Hermes cron job (trigger-only, like
`telegram_commander`):

- **Job name:** `BOM Push (Karol Generate)` (must match the name in
  `telegram_commander.py` → `COMMAND_MAP["/run_bom_push"]`).
- **Command:**
  `C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\bom_push.py --job 1365_serjeants_inn`
- Telegram: send **`/run_bom_push`** to fire it; the bot replies with the Stage 4
  Excel path. Approve, then run the `--commit` step.

After registering the job, restart the gateway: `hermes gateway restart`
(see `scripts/restart_hermes_gateway.sh`).

> For a new Karol BOM, copy the manifest, point `--job` at it (or extend the
> runner to take the control-task GID and resolve the manifest automatically).
