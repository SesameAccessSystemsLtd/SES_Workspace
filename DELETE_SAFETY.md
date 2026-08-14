# DELETE SAFETY — Asana Task Deletion Protocol

**STATUS: MANDATORY. No exceptions. Applies to ALL agents, ALL scripts, ALL automation.**

---

## Why This Rule Exists

On or around 2026-04-15, a number of key infrastructure tasks were deleted from Asana
without Giles's knowledge or review. These tasks contained critical configuration,
process records, and project state that is difficult or impossible to recover.

Asana task deletion is **irreversible**. There is no recycle bin. Once deleted, the task,
its history, its comments, its subtasks, and all associated data are gone permanently.

---

## THE RULE

### NO AGENT OR SCRIPT MAY DELETE AN ASANA TASK WITHOUT ALL THREE OF THE FOLLOWING:

1. **Intent declared** — the agent must explicitly state, in plain English:
   - Which tasks it intends to delete (names + GIDs)
   - Which project(s) they belong to
   - Why it believes they should be deleted

2. **CSV exported** — before any deletion, run:
   ```
   "C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe"
   "C:\Users\GilesHugo\.openclaw\workspace\skills\asana\scripts\asana_delete_with_safeguard.py"
   --gids <gid1> <gid2> ...
   ```
   This exports a CSV to `workspace/db/pending_deletes_YYYY-MM-DD_HHMMSS.csv`.
   The script will NOT delete anything without `--confirmed`.

3. **Explicit confirmation from Giles** — Giles must say "yes, delete" or equivalent
   AFTER seeing the CSV. The word "confirmed" alone is not sufficient — he must
   acknowledge the specific task list.

### The confirmation workflow:

```
Step 1 — Agent declares intent (in chat/Telegram):
  "I plan to delete the following N tasks from [project]: [list]. Running safeguard export now."

Step 2 — Agent runs asana_delete_with_safeguard.py WITHOUT --confirmed:
  → Exports CSV to workspace/db/pending_deletes_YYYY-MM-DD_HHMMSS.csv
  → Prints full task list to stdout
  → STOPS. Does not delete.

Step 3 — Agent tells Giles:
  "CSV exported to [path]. Please review. Reply 'yes delete [task count] tasks' to confirm."

Step 4 — Giles reviews CSV and replies with explicit confirmation.

Step 5 — Agent runs asana_delete_with_safeguard.py WITH --confirmed:
  → Deletes tasks
  → Logs each deletion to workspace/db/delete_log.jsonl
  → Reports count to Giles
```

---

## Hard Constraints

- **Never skip the CSV step.** Even for a single task.
- **Never interpret "clean up", "remove stale", "tidy" as permission to delete.** Always ask.
- **Never delete tasks during a batch job** (BOM import, stock sync, etc.) — write operations
  in batch scripts must only CREATE or UPDATE, never delete.
- **If uncertain whether a task should be deleted — don't.** Archive it or flag it.
- **Subtasks count.** Deleting a parent task deletes all subtasks. Declare the full count.

---

## The Safe Alternative to Deletion

Before suggesting deletion, consider:
- **Mark complete** — if the task is done, not delete-worthy
- **Move to ARCHIVE section** — if it's stale but worth keeping as a record
- **Remove from project** — if it's in the wrong project but still valid elsewhere
- **Add a note** — if it needs context before anyone decides to delete it

Deletion is almost never the right first move.

---

## Enforcement

This protocol is referenced in:
- `workspace/Safety_first.md`
- `agents/asana_agent/IDENTITY.md` (SAP — sole Asana writer)
- `agents/asana_agent/TOOLS.md`

Any script that calls the Asana DELETE endpoint without going through
`asana_delete_with_safeguard.py` is in violation of this protocol.

---
*Established: 2026-04-16 following task deletion incident on 2026-04-15*
