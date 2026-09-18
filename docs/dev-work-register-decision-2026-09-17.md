# Decision record — where open development work lives

**Date:** 2026-09-17 · **Decided by:** Giles · **Status:** ADOPTED
**Supersedes:** the "auto-scan local folders" option discussed and rejected 2026-09-17.
**Related:** `skills/os_portal/dev_register.py`, `docs/HERMES_DEV_LOGGING_RULE.md`,
`docs/dev-register-setup-runbook-2026-09-17.md`, `skills/os_portal/project_cycle.py`.

---

## Problem

Development work forks across many sessions — Hermes/Telegram channels, Claude CLI,
Claude desktop, Codex, ChatGPT. Each leaves files in local folders. Nothing records
that a thread was opened, what "done" would look like, or whether it was ever closed
out. Result: work is started and silently abandoned, and Giles cannot see what is in
flight without remembering it.

This is the second of two lanes in the OS portal objective. The first lane is
pipeline state per project (`project_cycle.py`). Both lanes answer the same
question in different domains: **what is open, and what closes it?**

## Options considered

**A. Local HTML register only.** A page backed by local JSON. Fast, offline, no API
dependency. But it is another surface to maintain, it is invisible from a phone, it
has no notification path, and it dies with the machine.

**B. Asana only** — the existing project *"Giles Ai Dev work"*
(`1147424298774210`). Already has the right schema: Priority (Critical/High/Medium/
Low), Status (Not Started/In Progress/Blocked/Done/Cancelled), Effort (S/M/L/XL),
Area (7 values) and matching sections. Sesame already runs operationally through
Asana. Survives machine rebuilds; reachable from a phone; notifies.
Against it: an agent mid-session cannot always reach Asana (token absent, API down,
rate limited), and a logging rule that can fail at the moment of logging is a rule
that will silently not run. Also the project's own description currently reads
*"USED ONLY TO SORT AND ASSIGN SUBTASKS… ESSENTIALLY USED LIKE A CACHE"* — it is
not today intended as a durable register.

**C. Hybrid — ADOPTED.** Local append-only register is the write buffer and audit
trail; Asana is the system of record for triage and sign-off.

## Decision

**Asana `Giles Ai Dev work` is the system of record. `queues/DEV_OPEN/` is the
durable local buffer that feeds it.**

Rationale: this is the same shape as everything else that already works in this
workspace — `queues/REQUESTS/` buffers locally and `execute_requests.py` pushes to
Asana — and it is the same doctrine as `centre_of_truth.py`: write local first,
reconcile to live, never silently drop. An agent can always log; sync is a separate,
retryable step; a failed sync loses nothing.

### Consequences

1. **Agents log locally, never to Asana directly.** `dev_register.py log` writes
   `queues/DEV_OPEN/<id>.json`. No PAT needed at log time.
2. **`dev_register.py sync` creates the Asana task** and stores `asana_gid` back in
   the local record. Idempotent — a thread with a gid is never re-created.
3. **Only a human closes.** Agents may set `open`, `awaiting_signoff`, `blocked`.
   `closed`/`cancelled` require `--human`. Sign-off happens in Asana; the register
   mirrors it.
4. **Nothing is overwritten or moved.** Every change appends to `events[]`; state is
   derived from the last event. This is a deliberate departure from the
   `queues/REQUESTS/` pattern, which rewrites `Status:` in place and then moves the
   file — destroying the original record. Do not copy that pattern here.
5. **The local HTML page is a view, not a store.** It renders `DEV_OPEN/` beside the
   pipeline lane. It does not become a second source of truth.

### Open items this decision creates

- **The Asana project's stated purpose must change.** Its description says it is a
  cache. Either update the description to reflect that it is now the dev register,
  or create a sibling project and point `DEV_PROJECT_GID` at it. *Giles to decide —
  until then `dev_register.py` writes to `1147424298774210` as configured.*
- **Asana's Status field has no "Awaiting sign-off" option.** Until one is added,
  `awaiting_signoff` maps to `In Progress` and the sign-off signal is carried only by
  the nightly Telegram report. Adding an enum option is a one-click change; if made,
  add its gid to `STATUS_OPTS` in `dev_register.py`.
- **48 existing incomplete tasks** are already in the project, logged by hand. They
  have no register record. They are left alone — the register starts from today and
  back-fill is not attempted.

## Not decided here

Hosting of the OS portal on `os.sesameaccess.com` is a separate deployment question,
parked. It does not block either lane: both work locally first.
