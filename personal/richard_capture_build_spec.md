# Build spec — Richard rolling-60-day issues benchmark + weekly 1:1 focus task

Handed to deepseek-v4-pro subagent. Build real, runnable Python in SES_Workspace.

## Environment (Windows, git-bash, MSYS paths work)
- Python: `C:/Users/GilesHugo/SES_Workspace/.venv/Scripts/python.exe` — ALWAYS prefix `PYTHONPATH=""` and use this interpreter (avoids cp311 numpy path leak).
- Asana PAT: read from `~/.hermes/.env` key `ASANA_PERSONAL_ACCESS_TOKEN` (read via python open(); never print/log it). If not there, fall back to `~/.openclaw/.env`.
- Reference skill for the pull pattern + pitfalls: `C:/Users/GilesHugo/SES_Workspace/../../AppData/Local/hermes/skills/operations/purchasing-issues-review/SKILL.md` — READ THIS FIRST. It documents the exact tag GIDs and the recency/author filters.
- Reference data/baseline: `C:\Users\GilesHugo\SES_Workspace\memory\richard-performance-assessment-2026-08-13.md` (original 9-category breakdown = the FIXED benchmark) and `C:\Users\GilesHugo\SES_Workspace\memory\process-change-scope-richard-2026-08-13.md`.

## What to build (3 pieces, all read-only on Asana except #3's creation)

### 1. Rolling-60-day flagged-issue pull (`skills/purchasing/richard_rolling_capture.py`)
- Window = last 60 calendar days from today (compute `today - 60d`, ISO string).
- Pull tasks carrying the process tags: `Purchasing Procedure Issue` tag = GID `1163007497828518`; `Process Issue` tag = GID `1164351154905410`.
- For each tagged task, pull stories/comments. Qualify by **Giles's comment `created_at` falling inside the window** (human Giles = gid `1142108324926610`), NOT task `modified_at` (mirror sync churns it). EXCLUDE the service account "Giles's Service Account" (gid `1210751448247658`) HTML `<body>` "Purchasing update" boilerplate.
- Output: a per-issue record {task_gid, task_name, project, comment text, comment date, category}.
- Classify each issue into a category. Only classify bespoke-relevant process failures; for standard parts where completion IS the arrival evidence, a completion is NOT a defect.

### 2. Benchmark comparison (`richard_rolling_capture.py --report`)
Compare the rolling 60-day window counts per category against the FIXED baseline (the original 9-category / 564-flag breakdown from the 13-Aug assessment; embed the baseline table as a constant in the script). Report per-category current-60d vs baseline, plus overall delta and the trend (better/worse/flat). No filtering of evidence — show the whole window vs the hard baseline.

### 3. Weekly Monday focus task (create via REQUESTS, DO NOT write Asana directly)
A mode `--monday` (or a separate caller) that on every Monday generates ONE task:
- Project: **Richard / Giles 1:1** = `1204237507645072`, section **Action items** = `1204237507645083`.
- Assignee: **Richard** user gid = `357614200440807`.
- Due date = today (the Monday).
- Name: something like "Weekly — Richard issues review & focus" (make it identifiable).
- Notes/comments carry the benchmark comparison summary (current 60-day vs baseline, top categories that need focus this week).
- **Asana creation must go through the REQUESTS queue** (`SES_Workspace/queues/REQUESTS/`): write a PENDING `.md` (+ companion `.json` as needed) in the format `execute_requests.py` consumes: header must contain `Status: PENDING` AND `To: asana_agent`. On the section/project/assignee/due it does create_task. See `C:\Users\GilesHugo\AppData\Local\hermes\skills\asana-write-guardrails\SKILL.md` for the REQUESTS format + the "add_comment/Comment To Push" rule. The GENERATION of the REQUESTS file is what the cron does; the ACTUAL Asana write stays gated on the processor + Giles approval. Make sure the script only WRITES a local REQUESTS file — never POSTs to Asana itself.

## Deliverables
- The two scripts above, runnable with `--scan`/`--report`/`--monday`-style flags.
- A short README note in the file explaining how each is invoked.
- Run `--scan --report` once with `--dry-run` to prove it works against live Asana, capturing counts. Show me the run output.
- Do NOT create/live-fire the Monday task. Only stage/prove.

## Hard constraints
- Never print/log the Asana PAT or any secret.
- `execute_code` is blocked in this environment — use terminal() + write_file/read_file only.
- Asana writes (the Monday task) go ONLY via REQUESTS files, never direct API POST to tasks.
- Use `gid` (not `id`) keys on every Asana object.
