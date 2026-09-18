# execute_requests.py — create_task fix brief (for deepseek-v4-pro implementation)

File to edit: C:/Users/GilesHugo/SES_Workspace/skills/purchasing/execute_requests.py

## Background
This is the REQUESTS-queue consumer that turns an approved PENDING file into an Asana write. There is a bug in its create_task path: it ignores the companion .json, drops assignee/due_on/section, and corrupts the project GID when the .md carries a trailing parenthetical. This brief gives the exact repair. Make the minimal, surgical changes described. Preserve the file's existing style, docstrings, and error-handling conventions.

## Bug 1 — REGEX eats the trailing parenthetical (project GID corruption)
In process_file(), the create_task branch currently does:
    proj_m = re.search(r"^Project GID:\s*(.+)$", content, re.MULTILINE)
    project_gid = proj_m.group(1).strip() if proj_m else ""
If the .md line is `Project GID: 1204237507645072   (Richard/Giles 1:1)`, group(1) captures the whole rest of the line INCLUDING ` (Richard/Giles 1:1)` and the trailing spaces before it. That corrupts the GID so the create POST 400s and falls into the My-Tasks-under-Giles fallback.
FIX: after capturing, strip/normalise so project_gid keeps ONLY a leading token that looks like a GID. The .md generator emits `Project GID: <gid>` possibly followed by `   (Some Name)`. Accept the first whitespace/comment-delimited token and require it to be digits (an Asana GID is a long integer). E.g. tokenize group(1) on whitespace and take the first token; if it contains a trailing parenthetical glued by spaces, the first token is already just the number. If the line is exactly `Project GID: 1204237507645072   (Richard/Giles 1:1)` the first whitespace token IS `1204237507645072`. Implement robustly (drop anything after whitespace-and-form of `(` too, defensively).

## Bug 2 — create_task ignores the companion .json (assignee / due_on / section / notes lost)
process_file() already loads the companion JSON into `payload` (lines 416-420). But the create_task branch (currently ~426-441) reads ONLY Project GID / Task name / Description from the .md and calls asana_create_task(project_gid, task_name, description), never reading payload. The .json for an endorsed create_task request carries: {action:'create_task', assignee:<user GID>, due_on:<YYYY-MM-DD>, project:<project GID>, section_gid:<section GID>, name:<name>, notes:<full body>}.
FIX: in the create_task branch, fall back to payload for each field when the .md doesn't carry it, and pass assignee / due_on / section through to asana_create_task:
    project_gid = <md Project GID, GID-sanitised> or payload.get('project_gid') or payload.get('project')
    task_name   = <md Task name> or payload.get('name') or 'Untitled Task'
    assignee    = payload.get('assignee')
    due_on      = payload.get('due_on')
    section_gid = payload.get('section_gid') or payload.get('section')
    description = payload.get('notes') if the .md has no Description section else the .md Description
Call the extended asana_create_task(project_gid, task_name, description, assignee=assignee, due_on=due_on, section_gid=section_gid).

## Bug 3 — asana_create_task() cannot set assignee / due_on / section
Current signature + body (lines 121-154): asana_create_task(project_gid, name, description="") POSTs {"data":{"name":name,"projects":[project_gid],"notes":description}}; on a 400 (project_gid is actually a My Tasks list) it falls back to workspace+assignee=GILES.
FIX:
1. Change signature to: def asana_create_task(project_gid, name, description="", assignee=None, due_on=None, section_gid=None):
2. Build the primary body with name, projects:[project_gid], notes:description, and ADD assignee (if provided) and due_on (if provided) — the create can assign + set due in the same POST. e.g. body = {"data":{"name":name,"projects":[project_gid],"notes":description}}; if assignee: body["data"]["assignee"]=assignee; if due_on: body["data"]["due_on"]=due_on.
3. After a successful create (status 200/201), read task gid. If section_gid was provided, ALSO add the task to that project section via the POST /tasks/{gid}/addProject endpoint with body {"data":{"project":project_gid,"section":section_gid}} (Asana's documented "add to a specific project section"). Capture and return that in the message (e.g. "task created: {gid} (section set)" vs "task created: {gid}" if section add failed/is skipped). Do NOT let a section-add failure fail the whole request — the task is already created; report it.
   IMPORTANT: verify the exact Asana section-add API against Asana's current docs (project section membership). If addProject with {project, section} is not the current supported shape and instead you must create then PUT ... use whichever Asana documents as correct for setting a new task into a specific section of a project it belongs to. Do NOT guess — confirm against the Asana API docs. If you cannot reach docs, prefer create-with-then-addProject(project+section) which is the long-stable pattern, and wrap in try/except.
4. The 400 My-Tasks fallback must remain ONLY when the task truly cannot be created as a project task (400 invalid object). Preserve that behaviour; it should never fire now that we sanitise project_gid, but keep it as a defensive last resort.

## The Monday generator (richard_monday_focus.py) already emits the RIGHT payload
Do NOT change richard_monday_focus.py. It already writes assignee:357614200440807 (Richard), due_on:<today>, section_gid:1204237507645083 (Action items), project 1204237507645072, and notes. It is the consumer (execute_requests.py) that must honour these — that is what this fix repairs. Leave the generator untouched.

## Verification (must do before finishing)
1. `PYTHONPATH="" C:/Users/GilesHugo/SES_Workspace/.venv/Scripts/python.exe -m py_compile C:/Users/GilesHugo/SES_Workspace/skills/purchasing/execute_requests.py` → must pass.
2. Confirm the regex change by reasoning/unit-testing process_file's create_task branch parsing against:
   sample .md content lines `Project GID: 1204237507645072   (Richard/Giles 1:1)` and assert project_gid == '1204237507645072'. You may write a tiny throwaway test script under skills/_tmp/ to prove it, then delete it.
3. Do NOT run execute_requests.py against the live queue (it would fire any PENDING file). Do NOT create any real Asana task. Do NOT live-test the section add.
4. State clearly what you changed (before/after per function) — report to your caller.

## Hard rules
- No credentials printed/logged.
- Edit only execute_requests.py. Do not touch the two richard_*.py files.
- Use write_file / patch / read_file / terminal only (execute_code may be blocked).
- Keep changes minimal and idiomatic to the file.
