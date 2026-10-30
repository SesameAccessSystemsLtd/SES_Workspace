# Skill: jibble_sync

Two-way sync between Jibble and Asana for the Jibble trial (replacing WorkSmarter, ClockRite and Everhour).
It runs every 15 minutes on GitHub Actions (`.github/workflows/jibble-sync.yml`) in full mode, alongside the existing systems.

## What it does

**Holidays (Jibble time off <-> Asana "Holiday for Employees")**

| Event | Result |
| --- | --- |
| New request in Jibble | Task created in Holiday for Employees, named `Name - Fri Nov 20th 2026 to Fri Nov 20th 2026`, Holiday STATUS = BEING REVIEWED, Leave Type from the policy, Days remaining from the Jibble balance |
| Holiday STATUS set to ACCEPTED (office or engineers) | Jibble request set to Approved; "Approved By" filled with whoever changed the status (from the task history) |
| Holiday STATUS set to REJECTED | Jibble request set to Rejected |
| Holiday STATUS set to CANCELLED HOLIDAY | Jibble request set to Cancelled; task moved to the Cancelled section |
| Request changed in Jibble (dates, times, policy) | Task renamed and re-dated, Holiday STATUS = MODIFIED - REQUIRES REVIEW, comment added, Jibble request back to Pending |
| Request cancelled in Jibble | Holiday STATUS = CANCELLED HOLIDAY, moved to Cancelled |
| Request approved directly in the Jibble app | Mirrored into Holiday STATUS |
| Request deleted in Jibble | Holiday STATUS = MODIFIED - REQUIRES REVIEW with a comment asking someone to check it (once) |

Writes happen in a safe order: Jibble first, then Asana, and the UID last. If any step fails the UID is left as it was,
so the next run retries rather than undoing a decision.

State between runs lives in the task's **UID for automation** field: `JIBBLE:<id>|h=<content hash>|j=<last Jibble status>|a=<last Asana status>`.
Do not edit that field by hand.

**Time (Jibble clock-ins -> Asana Actual time on build subtasks)**

- Monitored parts are listed in `config.json` (`asana.monitored_parts`). Each one, with its subtasks, is added to
  **Annual Production Scheduling (BETA TESTING)**, which carries Asana's Estimated time / Actual time fields.
- Estimated time is seeded from Estimated Hours where it is still empty. Actual Cost is left alone: it stays the place to log wasted time.
- In Jibble: Client = job (PROJECT NAME), Project = part, Activity = labour type. The sync creates missing clients,
  projects and the activities Cut, Paint, Drill and Rework.
- Each clocked block is matched to a subtask by activity (`jibble_sync/mapping.py`): Weld -> Welding / Fabrication,
  Assemble -> Assembly, QC -> QC, Electrics / Re-Wire -> Wiring, re-work or Modify -> Rework. When several subtasks match:
  open and assigned to that worker, then open, then the standard `Activity - <part>` name, then newest.
  No match -> booked to the part task and listed under Exceptions in the run summary.
- Asana gets one entry per worker, per task, per day, credited to the worker (the sync account is an Asana admin).
  Entries the sync created for other people in the last 21 days are reconciled every run, so edits and deletions in
  Jibble flow through. Entries anyone logs for themselves are never touched, including the sync account's own.
- Time for Jibble people with no Asana user (or whose Asana user is the sync account) is not written; it is listed
  under Exceptions. Use a dedicated admin account for `ASANA_TOKEN`, not a person who tracks their own time.
- Once time is booked to a subtask it stays there, even if subtasks are later completed or reassigned.
- Safety: if a run would delete more than half of the synced entries (and more than 10), deletes are held back
  and reported, in case Jibble returned incomplete data. Clocked blocks over 16 h (a missed clock-out) are skipped and reported.

**Reports (read-only, Everhour layout)** - run locally, never in Actions (the repo is public):

```bash
doppler run -- python -m jibble_sync report payroll --from 2026-10-26 --to 2026-11-25 --out payroll.csv
doppler run -- python -m jibble_sync report manufacturing --client "1555 - Fifth Ave 854" --from 2026-10-29 --out mfg.csv
```

Payroll adds approved time off as hours (default 9 h a day; per-person hours via the `JIBBLE_SYNC_PEOPLE_JSON` secret,
e.g. `{"hours_per_day": {"Michał Jurczykowski": 8}}`), skipping weekends and bank holidays and scaling part days by
Jibble's duration. Labels: `0010 Annual Leave`, `0006 - work from home`, and `0014 Bank Holiday` rows for everyone with
time in the period (dates in `config.json`, England and Wales, 2026-2027). Sick leave keeps its Jibble policy name.

## Secrets

| Name | What |
| --- | --- |
| `JIBBLE_KEY_ID` | Jibble API key ID (Doppler `vps_hermes/prd`) |
| `JIBBLE_API_KEY` | Jibble API key secret |
| `ASANA_TOKEN` | Personal access token of the Asana **admin** account the sync acts as |
| `JIBBLE_SYNC_PEOPLE_JSON` | Optional per-person contracted hours |

Without the three required secrets the workflow logs "Secrets not configured" and exits cleanly.

## Commands

```bash
cd skills/jibble_sync
python -m jibble_sync check              # read-only: both APIs answer
python -m jibble_sync run --dry-run      # print what would change
python -m jibble_sync run                # full mode
python -m jibble_sync run --only holiday # or --only time
python -m pytest tests                   # unit and flow tests
```

The Actions run can be switched to dry run from the Run workflow button, or for every run by setting the
repository variable `JIBBLE_SYNC_MODE` to `dry-run`.

## Keeping it running

GitHub turns off scheduled workflows in a public repository after 60 days without a commit. If the sync stops,
re-enable it from the Actions tab (or push any commit).

## Logs are public

This repository is public, so the run log and summary contain counts and IDs only: no names, dates or leave types,
and errors are logged as status and path without response bodies (`http.safe_err`). Keep it that way when changing the code.
