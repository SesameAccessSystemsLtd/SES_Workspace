# Jibble -> Asana task mapping: workshop scope (reference)

Scoped 17 Nov 2026, during the Jibble trial. This records how workshop time (build parts, machining, general
and uni work) should reach the right Asana task, what the live data looks like, and the decisions still open,
so the work does not have to be rediscovered. **It describes a plan: only the parts marked "built" exist in code.**
Service visits are already built and described in `SKILL.md`.

Jibble API reference: https://docs.api.jibble.io/

## 1. Job numbers change between build and service

A job's number depends on its stage:

| Stage | Where | Name shape | Example |
| --- | --- | --- | --- |
| Build | 3.1 Build Schedule, `PROJECT NAME` field, part names | 4-digit build number first | `1573 Kings Road 60-62`, parts `1573.1004.B.MainStairFrame.` |
| Service contract | 4.0 Service Schedule (and the Jibble service projects imported from it) | New service number first, build number moved to the **end** | `1087 - Langley St KPF - 1037` (build 1037) |
| Accounts | Sage | Sometimes a third number, written in brackets | `1482 - Wingate House - 1359 (1393 on Sage)` |

Consequences:

- A Jibble **build** project and a Jibble **service** project for the same lift are different projects with
  different leading numbers. The only link between them is the build number at the end of the 4.0 name.
- Never key anything on "the first number in the name" without knowing which stage the task is in.
- `works.py` (`_address_by_job_number`) already uses this to find an install's site: it looks for the 4.0 record
  whose name has the build number as one of its ` - ` parts. **Known gap:** a part like `1359 (1393 on Sage)`
  does not equal `1359`, so those records are missed. Fix by comparing the leading number of each part.
- `1456` (uni) and `0006` (general workshop duties) are internal job numbers, not customer jobs.

## 2. What is in 3.1 Build Schedule (snapshot 17 Nov 2026)

184 open tasks in `3.1 Build Schedule` (project `852956628490855`), classified by name:

| Kind | Count | Name pattern | Example task | Map time? |
| --- | --- | --- | --- | --- |
| Build part | 77 (12 jobs: 1573 x18, 1555 x11, 1588 x10, 1601/1597/1580 x8, 1595 x5, 1569 x3, 1582/1575 x2, 1054/1594 x1) | `JJJJ.DDDD.<pos>.<Name>.` with labour subtasks | `1218779412436896` `1573.1004.B.MainStairFrame.` | Yes: by activity to a subtask |
| Machining line | 10 | `N off JJJJ. DDDD.X.NNN - <desc> Machining INHOUSE`, no subtasks | `1220514692868498` `4 off 1601. 8000.A.069 - Slider Machining INHOUSE` | Yes (see 4.2) |
| General workshop | 14 | `0006 - General Workshop Duties <first name>` (one per person, section "Workshop General Tasks:") | `1199559872528825` | Yes (see 4.3) |
| Uni | 3 | `1456 Uni Time <first name>` (section "Holidays") | `1219925182247318` | Yes (see 4.3) |
| Holiday | 13 | `<Name> - Fri Nov 20th 2026 to ...` (section "Holidays") | `1220427621991027` | No: handled by the holiday sync |
| Other | 67 | `QC CONTROL - <job>`, `<job>.<Name>_EngineeringDrawing.`, `*.DRAWING` parents, one-offs, blank names | `1218274535026920` | No |

Every build part sits under a `*.DRAWING` parent task; time goes to the part's **subtasks**, never the DRAWING task.
Parts are multi-homed (Build Schedule section per job, the job's "Sales Task Manufacturing" project, overdue
control, impact assessment).

**Subtask names** (first word before ` - `) on 60 open parts:
`assemble` 70, `qc` 57, `weld` 56, `galv` 13, `qc b4 galv` 13, `paint` 3, `zinc plating` 3, `prep galv` 2,
`now powder coating` 3, `cut cement board` 2, `anodising` 2, `nickel plating` 1, `cut/drill` 1, `polish/countersink...` 1,
`wire test panel...` 1, `0009` 3 (a job number used as a name: no activity).
Names contain non-breaking spaces (`QC\xa0 B4 Galv -\xa0...`); `mapping._norm` handles them because `str.split()`
treats `\xa0` as whitespace.

- Galv, zinc, powder coating, anodising and plating are **subcontract** processes: no Jibble activity.
- `Prep Galv` is in-house labour with no activity today (proposed: `Prep`).
- `QC B4 Galv` maps to QC already (the name starts with "qc").

## 3. Jibble model and what the trial confirmed

- **Client -> Project -> Activity.** No tasks under projects (on Jibble's roadmap), so the sync picks the Asana task.
- One project belongs to exactly one client. Clients are not shown in the mobile app.
- Projects have `assignees`, `projectLeads`, `labels`, `code`, `locationId`, `status`. Assignees limit who sees a
  project. Archiving keeps history; deleting does not. Never delete.
- Activities have `assignedGroups`: an activity can be offered to one group only (Workshop vs Site Team).
- Automated (geofence) clock-ins carry no project or activity, so they cannot be mapped to a task.
- The engineer's app has a search box on the project list, so typing `1573.1004` finds a part among dozens.
- The sync runs every 15 minutes and creates missing projects each run. A "morning push" is therefore not needed:
  a new part in Asana appears in Jibble within 15 minutes.

Live Jibble on 17 Nov 2026:

- Clients: `Service & Repairs` (37 service projects), `SESAME`, `HRP Group`.
- Groups: `Managment`, `Workshop`, `Site Team`.
- Test projects to archive once the real ones exist: `Tower of London`, `Tower of london BUILD`,
  `1999. Lift pack`, `Workshop`.
- Activities: `Welding / Fabrication`, `Assembly ` and `QC ` (trailing spaces; harmless, names are compared stripped),
  `Wiring`, `Servicing FULL`, `1 off Service`, `CALLOUT`, `1245.Build pack trial stair pack`. The last one is a project
  entered as an activity: archive it. `Cut`, `Paint`, `Drill`, `Rework` are in `config.json` but not created yet,
  because the sync has not run since the Asana service account lacked scopes.

## 4. Mapping by kind of work

### 4.1 Build parts: one Jibble project per part (built; discovery not built)

Built today (`timesync.py`, `mapping.py`): Client = job (`PROJECT NAME`), Project = part (id derived from the part's
task gid), Activity = labour type. A clocked block goes to the part subtask whose name starts with that activity,
preferring a subtask already holding that person's time that day, then open and assigned to them, then open, then
newest. No match: booked to the part task and reported.

Not built: the parts are hand-listed in `config.json` (`asana.monitored_parts`, 11 gids). To keep Jibble current:

- **Discover parts each run:** open tasks in 3.1 Build Schedule whose name matches `^\d{4}\.\d{3,4}\.`, not ending
  `.DRAWING`, with at least one subtask. Keep `monitored_parts` for extras (e.g. the test task `1220026961278842`).
- **Client name:** `PROJECT NAME` when it starts with a 4-digit number; else the part's Build Schedule section name;
  else `fallback_client`. On 17 Nov, 8 parts had `PROJECT NAME` blank and one held `MODIFY - and Update the AUTOMATION!!`.
- **Archive** the Jibble project when the part is completed or leaves the Build Schedule. Time already clocked still
  maps, because the project id is derived from the task gid.
- Each discovered part and its subtasks are added to the beta project `1202196173874070` (where Actual time lives).
  At 77 parts that is roughly 350 tasks multi-homed into the beta project.

Job-level projects (one per job, 12 projects) were considered and rejected: one person often holds the same activity
on several parts of one job (e.g. Weld on most 1573 parts), so the sync could not tell which part the time was for.

### 4.2 Machining: one Jibble project per machining line (recommended, not built)

Machining lines have **no subtasks**, and their assignee is the person who raised them, not the machinist, so
neither the activity nor the assignee can pick a line out of its parent (`Machining - <job> ...` /
`Thamside Machining - <job> ...`, which also lives in PARTS CONTROL).

- **Recommended:** each open machining line becomes a Jibble project (name = the task name, client = job), activity
  `Machining`. The machinist searches the job or part code. The time goes to the line task itself: exact, no guessing.
  10 open lines on 17 Nov; they are short-lived, so archiving on completion keeps the list short.
  Discovery: open tasks in 3.1 Build Schedule whose name contains `machining` and `inhouse`, job number from
  `off\s+(\d{4})\.`. Code change: a part with no subtasks books straight to itself without logging an exception.
- **Alternative:** one project per machining parent, time on the parent. Simpler list, but time per machined part is lost.

### 4.3 General workshop and uni work (not built)

One Jibble project `0006 - General Workshop` under client `SESAME`, offered to the Workshop group, with activities:
`General Work`, `Workshop Maintenance`, `Machining` (unplanned, not on a line), `QC Task`, `Uni Work`.

| Activity on the General project | Asana task |
| --- | --- |
| `Uni Work` | That person's open `1456 Uni Time ...` task (matched by assignee) |
| Any other | Decision needed (below) |

**Decision for Giles:**

- **Option 1 (matches today):** all general time goes to the person's own open `0006 - General Workshop Duties`
  task, matched by assignee. The activity is kept in Jibble but lost in Asana (Asana time entries have no note).
- **Option 2 (recommended):** one shared Asana task per activity in "Workshop General Tasks:" (e.g.
  `0006 - Workshop Maintenance`, `0006 - General Work`). Each time entry is credited to the worker, so Asana keeps
  both who and what. The per-person tasks can stay for planning. The shared tasks are created by a person or SAP
  (agents do not create Asana tasks directly).

Whichever option: no matching task -> not written, listed under Exceptions. Never match on the first name in the
task title.

Data issues found on 17 Nov: one person has two open `0006` tasks (`1203516523837354`, `1202994343459184`), one `0006`
task has no assignee (`1203656030497148`), and `1209363168453189` is named without the `0006` prefix.

### 4.4 Not mapped

Holidays (holiday sync), `QC CONTROL - <job>` tasks, `_EngineeringDrawing` and `.DRAWING` tasks, blank-named tasks
and social events. No Jibble project points at them, so no time can be clocked against them.

## 5. Proposed activities and who sees them

| Activity | Group | Maps to |
| --- | --- | --- |
| Welding / Fabrication, Assembly, QC, Wiring, Cut, Paint, Drill, Rework | Workshop | Part subtask by name (built) |
| Prep (new) | Workshop | `Prep Galv` subtasks (alias `prep`) |
| Machining (new) | Workshop | Machining line (4.2) or general (4.3) |
| General Work, Workshop Maintenance, QC Task (new) | Workshop | 4.3 |
| Uni Work (new) | Workshop and Site Team (apprentices) | Person's `1456 Uni Time` task |
| Servicing FULL, 1 off Service, CALLOUT | Site Team | TeamUp visit (built, see SKILL.md) |

A site engineer working a day in the workshop needs the Workshop activities. Either put them in both groups or give
those people visibility of the workshop projects. Jibble suggested an agent that swaps a person's project list when
they clock in at the workshop. It is possible, but fragile: not recommended until the simple setup is proven.

## 6. Open questions (verify before building)

1. Archive endpoint: how a project is archived through the API (status change on `Projects`?). Check the docs link above.
2. Can project `assignees` be a group, or only people? Decides how workshop projects are kept off Site Team phones.
3. `QC Task`: what work is this? A QC with no part, or the `QC CONTROL - <job>` tasks? Decides its target.
4. Option 1 or 2 for general work (4.3).
5. Machining per line or per parent (4.2).

## 7. Build steps when approved

1. `config.json`: add `build_discovery` (project gid, part pattern, machining pattern) and the new activities.
2. `timesync.load_parts`: discover parts and machining lines; keep `monitored_parts` as extras.
3. Client naming rule (4.1); no exception for a subtask-less part.
4. General project routing (4.3) by person and activity.
5. Archive Jibble projects for completed or removed parts.
6. Set `assignedGroups` on activities (section 5).
7. Tests for each, then a local `run --dry-run` before switching on.

## How this snapshot was taken

Read-only scripts (kept outside the repo because the output carries names): open tasks of 3.1 Build Schedule with
`completed_since=now`, classified by name; subtasks of up to 60 parts; Jibble `Clients`, `Projects`, `Activities`,
`Groups`. Re-run the same queries to refresh the numbers in section 2.
