---
name: bom_pipeline
description: "Run the BOM-to-Asana import pipeline. Use when Giles asks to import a BOM, process a BOM, run the BOM pipeline, or push parts to Asana. Provides the exact PowerShell commands to run — do NOT use coding-agent or spawn Claude Code for this."
---

# BOM Pipeline Skill

Orchestrates the full BOM-to-Asana import pipeline. Use this for all BOM import requests.

## Trigger phrases
- "import a BOM", "process a BOM", "run the BOM pipeline", "push parts to Asana"
- "import [project name] BOM", "process long lead BOM"

---

## AGENT SELF-CHECK RULES — read before constructing any command

**Rule 0 — NEVER use coding-agent or spawn Claude Code for this skill.**
Run `run_mrp_pipeline.py` directly via bash/shell. Do NOT delegate to Claude Code, Codex, or any background coding agent. Spawning a coding agent creates unreliable session tracking — the pipeline will run but you will lose visibility and report false errors to Giles.

**Rule 1 — Template only. No invented arguments.**
Use ONLY the fill-in-the-blanks template below. Every argument in that template has a name and a placeholder. Do not add arguments not in the template. If you are uncertain whether an argument exists, run the script with `--help` and check the output — never guess.

**Rule 2 — Unrecognized argument error = stop and run `--help` immediately.**
If you see `error: unrecognized arguments: --xxx`, do NOT try a different argument name. Run:
```powershell
& "C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe" "C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\run_mrp_pipeline.py" --help
```
Read the output. Construct the command only from arguments listed there.

**Rule 3 — Asana URLs: paste as-is, extract nothing.**
`--asana-url` receives the full URL exactly as Giles provides it. Do not extract GIDs from it. Do not use any GID from the URL as `--project-gid`. The URL is metadata embedded in task descriptions — it does not change the import target project.

**Rule 4 — `--project-gid` for standard BOMs: omit entirely.**
Standard BOM imports always target Build Orders (`848657415032331`). This is the default. Do not pass `--project-gid` unless Giles explicitly provides a different project GID for a Long Lead import.

**Rule 5 — Run `--preflight` first, check for errors, then run the full pipeline.**
Run preflight, read the output. If any line contains `✗`, fix that input before proceeding. If all lines show `✓`, run the main pipeline. This is a self-check — no message to Giles required unless preflight fails and you cannot fix it.

---

## COMMAND TEMPLATE — Standard BOM

Copy this template. Fill in the four ALL_CAPS placeholders. Add nothing else.

**Step 1 — Preflight (self-check):**
```powershell
& "C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe" `
  "C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\run_mrp_pipeline.py" `
  --project-name "PROJECT_NAME_HERE" `
  --parts-folder "PARTS_FOLDER_PATH_HERE" `
  --dropbox-url  "DROPBOX_URL_HERE" `
  --asana-url    "ASANA_CONTROL_TASK_URL_HERE" `
  --preflight
```

**Step 2 — Run Stages 1–4 (no Asana writes):**
```powershell
& "C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe" `
  "C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\run_mrp_pipeline.py" `
  --project-name "PROJECT_NAME_HERE" `
  --parts-folder "PARTS_FOLDER_PATH_HERE" `
  --dropbox-url  "DROPBOX_URL_HERE" `
  --asana-url    "ASANA_CONTROL_TASK_URL_HERE" `
  --stop-after 4
```

**Step 2.5 — After Stage 4 completes: notify Giles on Telegram (MANDATORY).**
Read `pipeline_last_run.json`, extract `stage4_file`. Then send Giles a Telegram message (chat ID `8551261362`) with:
- Project name
- Stage 4 Excel path (so he can open it)
- What to do next: "Open the Excel, apply any SKIP/HOLD/FORCE_ORDER overrides, then reply to approve commit."

Do NOT silently finish and wait for Giles to ask. Send the message as part of the same turn.

**Step 2.6 — Resolve the Stage 4 file path (MANDATORY before Step 3):**

Read `pipeline_last_run.json` in the skill root:
```
C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\pipeline_last_run.json
```
The `stage4_file` field contains the absolute path to use as `--stage4-file`. Use this value verbatim. **Never reconstruct the path from memory, session history, or a previous message.** If the file does not exist or `status != "awaiting_commit"`, stop and tell Giles immediately — do not guess.

**Step 3 — Commit to Asana (only after Giles approves Stage 4 Excel):**
```powershell
& "C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe" `
  "C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\run_mrp_pipeline.py" `
  --project-name "PROJECT_NAME_HERE" `
  --parts-folder "PARTS_FOLDER_PATH_HERE" `
  --dropbox-url  "DROPBOX_URL_HERE" `
  --asana-url    "ASANA_CONTROL_TASK_URL_HERE" `
  --stage4-file  "VALUE_FROM_pipeline_last_run.json" `
  --commit
```

**Step 3 success check (MANDATORY):**
- Exit code 0 → confirm to Giles: "Committed successfully."
- Exit code non-zero → confirm to Giles: "Commit FAILED. Exit code: X. Last error lines: [paste from output]." Do NOT report success. Do NOT say "running in the background" — it failed.

> Allowed arguments for this runner: `--project-name`, `--parts-folder`, `--dropbox-url`, `--asana-url`, `--project-gid`, `--legacy-folder`, `--output-dir`, `--threshold`, `--commit`, `--pause`, `--stop-after`, `--stage4-file`, `--preflight`
> Any other argument → run `--help`, do not guess.

---

## Entry point
```
"C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe" "C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\run_mrp_pipeline.py"
```
All stage scripts are co-located — no path edits needed.

---

## Standard workflow (HITL at Stage 4)

### Step 1 — Run Stages 1–4 (dry run, no Asana writes)
```
"C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe" \
  "C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\run_mrp_pipeline.py" \
  --project-name "1555 - RB Lock Update" \
  --parts-folder "C:/path/to/vendor/bom/xlsx/folder" \
  --dropbox-url  "https://www.dropbox.com/..." \
  --asana-url    "https://app.asana.com/..." \
  --stop-after 4
```
Output: `pipeline_runs/{slug}_{date}/stage4_staging_review.xlsx`

### Step 2 — HUMAN REVIEW (only HITL checkpoint)
Tell Giles the path. He opens the Excel, reviews ORDER/PICK routing, adds overrides:
- `SKIP` — exclude entirely
- `HOLD` — exclude for now
- `FORCE_ORDER` — force to ORDER
- Numeric value — override the order quantity

### Step 3 — Commit to Asana
```
"C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe" \
  "C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\run_mrp_pipeline.py" \
  --project-name "1555 - RB Lock Update" \
  --parts-folder "C:/path/to/vendor/bom/xlsx/folder" \
  --dropbox-url  "https://www.dropbox.com/..." \
  --asana-url    "https://app.asana.com/..." \
  --stage4-file  "pipeline_runs/.../stage4_staging_review.xlsx" \
  --commit
```

---

## Long Lead BOM variant

Long Lead BOMs are a subset of a build — items ordered pre-contract due to lead times.
They follow the **same pipeline**, with two differences:

**1. Source Excel format**
The Long Lead BOM is a single combined Excel (not a folder of vendor files). It has:
- `Part Number` column
- `Cost Centre` column (col L) — must be populated or parts are dropped at Stage 1
- `Vendor` column (col I)
- `Item QTY`, `Description`, `Web Link` columns

Place the single Excel file in its own folder and pass that folder as `--parts-folder`.
Do NOT pass `--legacy-folder` (drawing pack lookup not applicable).

**2. Target project — always ask Giles**
Every BOM import has a different job/manufacturing project GID. There is no fixed GID to
hardcode. Always ask Giles for the project GID before running. He usually provides it
via the Asana project URL — extract the GID from the URL path.

Giles currently adds tasks to the job project manually after import. Do not attempt to
add tasks to the per-job project without explicit instruction.

**3. Post-import: set parent task**
After Stage 5 commits, set the root `ALL BOMS - {project}` task as a subtask of the
purchasing control task using asana_query.py or MCP. The purchasing control task GID is
provided by Giles per project (e.g. Jenners Edinburgh: `1213587365685012`).

### Example — Jenners Edinburgh Long Lead
```
--project-name "1575 - Jenners Edinburgh"
--parts-folder "C:/path/to/LongLeadFolder/"   ← folder containing the single xlsx
--dropbox-url  "https://www.dropbox.com/..."
--asana-url    "https://app.asana.com/1/357939803556123/project/1213587365588623/"
--project-gid  1213587365588623               ← Jenners Edinburgh project GID
--stop-after 4
```

---

## Stage map

| Stage | Script | Role |
|-------|--------|------|
| 1 | `1.BOM IMPORT into Asana/BOM_Consolidator_SLe_v4.py` | Consolidate vendor xlsx files |
| 2 | `2.Stock Controls/02_golden_index_match.py` | Match parts to Golden Index |
| 3 | `2.Stock Controls/03_stock_check.py` | Query KANBAN live stock levels |
| 4 | `2.Stock Controls/04_staging_export.py` | HITL staging review Excel |
| 5 | `1.BOM IMPORT into Asana/BOM_IMPORT_4_Asana_v4.py` | Create task hierarchy in Asana |

---

## Key IDs

| Resource | GID / URL |
|----------|-----------|
| Build Orders / Project Orders (standard target) | `848657415032331` |
| KANBAN project (stock check) | `632603478911321` |
| Parts Control project | `1211660707080749` |
| Sesame Part No custom field | `1213228090549470` |
| Project Status Overview (NOT a job project — do not use as import target) | `1192686469436889` |
| Jenners Edinburgh project | `1213587365588623` |
| Jenners purchasing control task | `1213587365685012` |

**Per-import project GID:** always confirm with Giles — each job has a unique GID. Extract from the Asana URL provided.

---

## Required inputs — collect before running

Before running any BOM import, confirm all of these with Giles:

| Input | What it is | Example |
|-------|-----------|---------|
| `--project-name` | Job name/number | `"1575 - Jenners Edinburgh"` |
| `--parts-folder` | Local path to folder containing BOM xlsx file(s) | `"C:/path/to/folder/"` |
| `--dropbox-url` | Dropbox link to the order folder | `"https://www.dropbox.com/..."` |
| `--asana-url` | Full URL of the purchasing control task — paste the whole URL as-is; it is embedded in task descriptions only, not used as the import target | `"https://app.asana.com/1/.../task/123"` |
| Purchasing control task GID | For post-import parent linking (extract from `--asana-url`) | `1213587365685012` |

**`--project-gid` — standard BOM imports: DO NOT pass this.** Tasks go to Build Orders (`848657415032331`) by default.
**Long Lead imports only**: pass `--project-gid` with the specific job project GID provided by Giles.

> **WARNING — `--asana-url` note:** The control task URL may contain `1192686469436889` (Project Status Overview) in its path. This is normal — it is where the control task lives. It does NOT affect the import target. Do NOT use `1192686469436889` as `--project-gid`.

These values are embedded in Asana task descriptions (dropbox URL, control task reference,
project name) and must be correct before running.

---

## Task description content

Each imported task's description must include:
- **Project name** — e.g. `1575 - Jenners Edinburgh`
- **Dropbox URL** — link to the order folder
- **Purchasing control task** — Asana URL of the control task

Stage 5 embeds `--dropbox-url` and `--asana-url` in the root task description automatically.
Vendor group tasks and line item tasks inherit context from their parent hierarchy.

---

## Project membership

Tasks are created in the **Build Orders / Project Orders** project (`848657415032331`) by default.

**Parts Control project** (`1211660707080749`): In principle, all parts with a QTY value
should also be added to Parts Control. This is **not yet verified** as an automated step —
Giles currently manages project membership manually. Do not add tasks to Parts Control
automatically without explicit instruction from Giles.

**Per-job project**: Adding tasks to the specific job/manufacturing project is done manually
by Giles after import.

---

## Stock check and Golden Index

**Stock check runs at Stage 3** — all BOM parts are checked against live KANBAN stock
levels before the staging review is produced. This applies to ALL BOM types including
Long Lead.

**Golden Index updates**: When Giles reviews the Stage 4 staging Excel and identifies a
part as a known stock item (assigns a KAN number), that mapping must be written back to
`Merged_Golden_Index_Rev4.csv` so the part number maps correctly on future imports.

Workflow:
1. Giles confirms a part number → KAN number mapping during Stage 4 review
2. Update `2.Stock Controls/Merged_Golden_Index_Rev4.csv` with the new row
3. Also update the copy in the lab at:
   `C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\OneDrive Documents\LAB. SESAME\Asana Scripts\2.Stock Controls\Merged_Golden_Index_Rev4.csv`
   so both copies stay in sync

The Golden Index is the authoritative lookup for stock part → KAN number mapping.
Keeping it updated means future imports auto-route those parts to PICK rather than ORDER.

---

## Rules

- **Always stop-after 4 first.** Present the stage4 Excel path to Giles. Wait for explicit approval before `--commit`.
- **Never run `--commit` without a direct go-ahead from Giles.**
- **Always confirm `--project-gid` with Giles** — it changes per job. Never guess or reuse a previous job's GID.
- Source BOM files may be read from `C:\Users\GilesHugo\SAS Dropbox\` — Dropbox is read-only. Never delete or modify files there.
- Use the SES_Workspace venv python (`C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe`), not bare `python`.
- The Golden Index CSV lives at: `2.Stock Controls/Merged_Golden_Index_Rev4.csv` (relative to this skill folder).
- Auth: reads `ASANA_PERSONAL_ACCESS_TOKEN` from env or `ASANAconfig.json` in this skill folder.

---

## Future enhancement — Stage 5 write ORDER: non-laser vendors first, Laser Cut parts LAST (Giles, 2026-09-23)

**Requirement (Giles directive):** when Stage 5 pushes the task hierarchy to Asana, the write order should be:

1. **Root** `ALL BOMS - {project}` first (mandatory — a child cannot be added before its parent exists in Asana).
2. **Cost-centre + vendor parent/group tasks** next, and the **vendor part-bearing tasks pushed early** — vendors should get their tasks first so long-lead procurement starts immediately.
3. **Laser Cut parts pushed LAST.** Laser-cut items take the **longest** to produce, so pushing them last means they don't hold up the faster vendor tasks in the queue/portal view.

**Hard constraint that bounds any reordering:** Asana requires the **parent task to exist before a child can be added**. So order can only be reordered *within* a level — you cannot push a vendor's part children before its vendor group task, nor a vendor group before the root. Correct shape:

```
root → (cost-centre/authority groups) → (vendor group tasks, non-laser first … laser last) → (part children, same grouping order)
```

**Current behaviour (as of 2026-09-23):** Stage 5 (`BOM_IMPORT_4_Asana_v4.py`) writes rows in **staging-CSV order** — there is **no explicit "laser last" sort**. The `HierarchyManager` creates parent/group tasks on demand before their children, so parent-before-child is already correct, but vendor-vs-laser ordering follows consolidation order, not the desired priority.

**What to implement when actioned:** add a deterministic sort of the Stage 5 write sequence by an ordering key:
- primary: hierarchy level (root=0, cost-centre=1, vendor group=2, part=3) — never violate parent-before-child
- secondary within vendor-group + part levels: a vendor priority where **`Laser Cut` sorts LAST**; all other vendors ahead in a stable order (alphabetical or a configured vendor priority list).

**Ordering key source:** the `vendor` / `cost center` columns already in `stage4_staging_review.xlsx`. "Laser Cut" appears as both a cost-centre (for lasercut sub-parts) and a vendor label — match case-insensitively on the vendor/authority field. Verify against a real staging file before shipping.

**Not yet built — captured requirement, not current behaviour.** Confirm with Giles before changing the write order (it's a change to the write-execution path), and re-verify parent-before-child still holds after any sort.

---

## Known edge cases

| Issue | Impact | Fix |
|-------|--------|-----|
| Cost Centre column empty in some rows | Parts silently dropped at Stage 1 | Verify all data rows have a Cost Centre value before running |
| Steel Profile BOM has no Cost Centre column | All Steel Profile parts dropped | Manually add Cost Centre column before running |
| `_MIR` parts | Won't match Golden Index — expected, go to ORDER | Not a bug |
| No Asana token | Stage 3 fails | Set `ASANA_PERSONAL_ACCESS_TOKEN` env var or check `ASANAconfig.json` |
| A leaked `PYTHONPATH` (uv-tool `hermes-agent\Lib\site-packages`) shadows the venv's numpy with a cp311 copy | Stage 1 fails: `Unable to import required dependency numpy` (ModuleNotFoundError `_multiarray_umath`) | Run with that var cleared: `env -u PYTHONPATH` before the SES_Workspace venv python — fixes it for a Python 3.13 venv |
