# Skill: asana

Query live Asana project and task data using the Asana REST API.

## When to use
Use this skill whenever you need to read Asana data: project status, task lists, overdue tasks, task details, or keyword search. This is your primary Asana access method.

## Auth
Reads `ASANA_PERSONAL_ACCESS_TOKEN` from the environment (set in `.openclaw/.env`). No additional configuration needed.

## Script
`C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\asana_query.py`

**Important:** Always use the full Python path — bare `python` is not on PATH in the agent environment:
`C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe`

Requires: `requests` (already installed in venv).

## Commands

### Get project overview
```bash
"C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\asana_query.py" get_project <project_gid>
```
Returns: project name, status, due date, member count.

### Count tasks in a project
```bash
"C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\asana_query.py" task_counts <project_gid>
```
Returns: total, incomplete, completed, milestones counts.

### List all incomplete tasks
```bash
"C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\asana_query.py" list_tasks <project_gid> --incomplete-only
```

### List overdue tasks only
```bash
"C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\asana_query.py" list_tasks <project_gid> --overdue-only
```
Returns: tasks where due_on < today and not completed. Includes assignee, section, due date.

### Get a specific task
```bash
"C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\asana_query.py" get_task <task_gid>
```

### Search tasks by keyword
```bash
"C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\asana_query.py" search_tasks <project_gid> "keyword"
```

## Key Project GIDs
- Project Orders: `848657415032331`
- Goods In / Order Requests: `358790955858777`
- Build Schedule: `852956628490855`
- KANBAN (stock): `632603478911321`

## Output
All commands return JSON to stdout. Exit code 0 on success, non-zero on error (error details in stderr).

## Replace [PROJECT NAME] placeholders in task names

Script: `C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\rename_placeholder.py`

Recursively scans **all tasks and subtasks at any depth**. Case-insensitive: matches `[PROJECT NAME]`, `[Project Name]`, `[project name]`, etc. Dry run by default — safe to call first. Add `--commit` to apply.

```bash
# Dry run (see what would change)
"C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\rename_placeholder.py" --project-gid <project_gid> --replacement "1581 Admiralty House"

# Commit changes
"C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\rename_placeholder.py" --project-gid <project_gid> --replacement "1581 Admiralty House" --commit

# JSON output (for agent use)
"C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\rename_placeholder.py" --project-gid <project_gid> --replacement "1581 Admiralty House" --json --commit
```

**GID lookup:** Use `list_projects.py` in the Asana Scripts TESTING folder to find a project GID by name before running this.

**Performance note:** Recursive scan on a project with 400+ tasks takes ~90 seconds due to per-task subtask API calls.

## Limitations
- `asana_query.py` is read-only. Use `rename_placeholder.py` for placeholder renaming.
- Paginates automatically (up to 100 tasks per page).


### Auto-Tagging Suppliers (Short-Term SAP Logic)
Before migrating to the Cloud Run ACA engine, SAP can run the tagging automation manually or via heartbeat.
**Script:** C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\tagging_automation.py

**Usage:**
# Dry-run a specific task
"C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\tagging_automation.py" --task-gid <task_gid>

# Dry-run a whole project
"C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\tagging_automation.py" --project-gid <project_gid>

# Commit changes (add tags)
"C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\tagging_automation.py" --task-gid <task_gid> --commit
