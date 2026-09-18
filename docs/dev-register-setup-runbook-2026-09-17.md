# Runbook — install and test the dev register + project cycle view

**For:** CLI Hermes (local shell, SSH to VPS, git-bash/MSYS on `giles-new`).
**Date:** 2026-09-17 · **Decision:** `docs/dev-work-register-decision-2026-09-17.md`

Everything here is **local and reversible**. Nothing touches the VPS, DNS, or
Cloudflare. Two Asana writes happen, both gated behind a dry run (step 6).

Notation: `PY = C:/Users/GilesHugo/SES_Workspace/.venv/Scripts/python.exe`,
`WS = C:/Users/GilesHugo/SES_Workspace`.

---

## Prompt 1 — verify the environment (read-only, do first)

> Run these and report the raw output of each. Change nothing.
>
> ```
> cd C:/Users/GilesHugo/SES_Workspace
> .venv/Scripts/python.exe skills/os_portal/project_cycle.py --probe
> .venv/Scripts/python.exe skills/os_portal/dev_register.py probe
> .venv/Scripts/python.exe -c "import requests, sqlite3; print('deps ok')"
> ```
>
> `project_cycle.py --probe` prints the real `ops.db` schema. `dev_register.py probe`
> checks the register dir, `~/.hermes/.env`, whether `ASANA_PERSONAL_ACCESS_TOKEN`
> resolves, and whether the four Asana custom-field GIDs still match the live
> project (`custom_field_drift` must read `none`).
>
> Report all three verbatim. If `custom_field_drift` lists anything, stop — the
> Asana schema has changed and the config block in `dev_register.py` needs updating
> before any sync.

**Expected:** `project_cycle.py --probe` lists `build_assembly_packs`,
`parts_control`, `procurement_tasks`, `production_impact`, `suppliers` with row
counts. `asana_token_present: true`. `custom_field_drift: "none"`.

**Resolved 2026-09-17 (probe run 1):** all five tables present —
`build_assembly_packs` 2641 · `parts_control` 2976 · `procurement_tasks` 4148 ·
`production_impact` 410 · `suppliers` 432. `asana_token_present: true`,
`custom_field_drift: none`. Two corrections were made to `project_cycle.py` as a
result and are already applied:

- Packs expose completion as **`is_completed`**, not `completed`. The old code did
  not crash — it fell through to the never-over-claim branch and counted every pack
  as open, which would have kept S7 permanently red with no visible cause.
- `parts_control` also carries `is_completed` (+ `completed_at`). Stage gating now
  counts **live parts only**; completed parts without a pack or order are history,
  not blockers. Totals are still reported as `parts` / `parts_live` / `parts_done`.

If any of these column names change in future, do not patch locally — report it.

---

## Prompt 2 — first real run of the cycle view (read-only)

> ```
> cd C:/Users/GilesHugo/SES_Workspace
> .venv/Scripts/python.exe skills/os_portal/project_cycle.py
> .venv/Scripts/python.exe skills/os_portal/project_cycle.py --json > data/os_status/project-cycle.json
> ```
>
> Report the table. Then sanity-check three rows against what you know to be true:
> pick one project you believe is complete, one mid-flight, one that has only just
> had its BOM imported, and say whether the stage bar matches reality. The script
> connects read-only (`mode=ro`) — it cannot modify `ops.db`.

**This is the acceptance test that matters.** If the bars disagree with reality, the
stage rules are wrong and must be corrected before the page is built on top of them.

**Run 1 outcome (2026-09-17):** gate held, defect A found and fixed, defect B open.
See `docs/project-cycle-gate-findings-2026-09-17.md`. Re-run this prompt after the
fix and confirm that no project with `parts=0` shows a green stage, and that closed
projects now offer "▶ Push the full project report to Asana".

---

## Prompt 2b — reconcile the `parts` count (read-only, resolves defect B)

> `project_cycle.py` reports `parts=538, noPack=282` for project 1588. Ground-truth
> queries returned `tot=219, done=213, nopack=94` — which is itself impossible
> (213 done out of 219 leaves 6 live parts, so 94 without a pack cannot be right).
> The two counts are measuring different populations. Settle it with ONE query and
> report the raw output:
>
> ```sql
> SELECT
>   COUNT(*)                                                   AS by_prefix_total,
>   SUM(CASE WHEN is_completed THEN 1 ELSE 0 END)              AS by_prefix_done,
>   SUM(CASE WHEN NOT is_completed AND (build_assembly_pack_gid IS NULL
>            OR build_assembly_pack_gid='') THEN 1 ELSE 0 END) AS live_no_pack,
>   COUNT(DISTINCT sesame_pn)                                  AS distinct_pn,
>   COUNT(DISTINCT asana_task_gid)                             AS distinct_gid
> FROM parts_control
> WHERE sesame_pn LIKE '1588%';
> ```
>
> Then, separately: `PRAGMA table_info(parts_control);` — confirm whether a
> `project_code` column exists, and if it does run the same counts filtered by
> `project_code='1588'` and report both side by side.
>
> The specific question to answer: **is `parts_control` one row per part, or one row
> per part-per-pack/order?** If `distinct_pn` is materially below `by_prefix_total`,
> it is the latter and the `parts` column is counting rows, not parts — in which
> case the column is mislabelled and I will rename it and de-duplicate.
>
> Read-only. Change nothing.

---

## Prompt 3 — create the register and log a test thread

> ```
> cd C:/Users/GilesHugo/SES_Workspace
> mkdir -p queues/DEV_OPEN
> .venv/Scripts/python.exe skills/os_portal/dev_register.py log \
>   --objective "Test thread — confirm dev_register writes, lists and syncs correctly" \
>   --where "C:/Users/GilesHugo/SES_Workspace/skills/os_portal/dev_register.py" \
>   --channel "cli-hermes" --area "Documentation" --effort S --priority Low
> .venv/Scripts/python.exe skills/os_portal/dev_register.py list
> ```
>
> Then run the same `log` command a second time, unchanged, and confirm it reports
> `"resumed"` rather than creating a second thread — that is the de-duplication
> guard. Report both outputs and `cat` the created JSON file.

**Expected:** one file in `queues/DEV_OPEN/`, `events[]` with a single `open` entry,
and the second run returning `resumed`.

---

## Prompt 4 — test the state machine, including the human gate

> ```
> ID=<the id from prompt 3>
> .venv/Scripts/python.exe skills/os_portal/dev_register.py event $ID \
>   --status awaiting_signoff --note "test — ready for review"
> .venv/Scripts/python.exe skills/os_portal/dev_register.py event $ID --status closed
> ```
>
> The second command **must fail** with "closing is a human action". Confirm it does.
> Then `cat` the JSON and confirm `events[]` now has two entries and that the
> original `open` event is still present and unmodified — nothing is overwritten.

**This is the key regression test.** If a state change ever overwrites rather than
appends, the register has lost its purpose.

---

## Prompt 5 — test the nightly report without sending anything

> ```
> .venv/Scripts/python.exe skills/os_portal/dev_register.py check --dry-run
> .venv/Scripts/python.exe skills/os_portal/dev_register.py check --stale-hours 0 --dry-run
> ```
>
> The first should report the awaiting-sign-off thread. The second, with the stale
> threshold at zero, should additionally list every open thread — proving the
> staleness path works without waiting 48 hours. Neither sends Telegram. Report both.

---

## Prompt 6 — first Asana sync (the only write; dry run first)

> ```
> .venv/Scripts/python.exe skills/os_portal/dev_register.py sync --dry-run
> ```
>
> Report what it says it would create. **Stop there and wait for Giles to confirm**
> before running it for real. When confirmed:
>
> ```
> .venv/Scripts/python.exe skills/os_portal/dev_register.py sync
> .venv/Scripts/python.exe skills/os_portal/dev_register.py list
> ```
>
> Confirm the task appears in "Giles Ai Dev work" with Priority/Status/Effort/Area
> set and in the correct section, and that `asana_gid` is now stored in the local
> JSON. Then run `sync` a second time and confirm it creates nothing — idempotency.

---

## Prompt 7 — schedule it

> Add two scheduled jobs on the local machine (Task Scheduler, or the existing
> Hermes cron mechanism — use whichever already runs the portal refresh jobs, so
> there is one scheduling system, not two):
>
> - **Hourly, on the hour:** `dev_register.py sync`
> - **Nightly 07:00 local:** `dev_register.py check`
>
> Both must run under the workspace `.venv` python with the working directory set to
> `C:/Users/GilesHugo/SES_Workspace`, and both must append stdout+stderr to
> `run_history/dev_register.log`. Report the exact job definitions you created so
> they can be reviewed.
>
> Do **not** add these to the VPS. The register is local-first by design.

---

## Prompt 8 — install the Hermes rule

> Read `docs/HERMES_DEV_LOGGING_RULE.md`. Install the block between "paste from
> here" and "paste to here" into the prompt-injection script of every Hermes channel
> that can write code. Set `HERMES_CHANNEL` per channel so `--channel` attributes
> correctly.
>
> Then test it end to end: in one channel, ask Hermes to make a trivial code change,
> and confirm it logs a thread first. Report the thread it created. If it writes code
> without logging, the rule is not installed correctly in that channel — say which.

---

## What is explicitly NOT in this runbook

**No part of this runs on the VPS, and no DNS or Cloudflare change is required for
any of it.** Confirmed 2026-09-17. Both lanes — `project_cycle.py` and
`dev_register.py` — read the local `ops.db` and write local files under
`SES_Workspace`; the only network call either makes is outbound HTTPS to
`app.asana.com` and `api.telegram.org`, which already works from the local machine.
`os.sesameaccess.com` does not exist yet and nothing here needs it.

The VPS/Cloudflare work is a **separate, later question**: whether to publish the
read-only dashboards for colleagues. It is parked, and parking it costs nothing
because everything is local-first by design. When it is picked up again, the open
decisions are recorded in the 2026-09-17 conversation: orange-cloud proxy (Access
requires it), cert issued before the Access app or via an ACME bypass, SSL mode
Full (strict), origin locked to Cloudflare ranges, and the office IP
(140.228.68.193, static via Hyperoptic) as a convenience policy layered on top of
email-OTP, never as the only policy.

- Nothing on the VPS, nginx, Traefik, DNS or Cloudflare. Parked.
- No back-fill of the 48 existing Asana tasks. The register starts today.
- No change to `queues/REQUESTS/` or `execute_requests.py`. The dev register is a
  separate ledger with a different, append-only contract; do not merge them.

## Two items outstanding from the earlier review

Unrelated to this runbook, still open, both from the 2026-09-17 code review:

1. **A live Telegram bot token is hardcoded** in `skills/purchasing/notify_telegram.py`
   and is not covered by `.gitignore`. Rotate it via BotFather. Deleting the line
   does not remove it from git history.
2. **`PORTAL_AUTH_TOKEN` is unset**, so any website Giles visits can POST to
   `127.0.0.1:8765/dispatch` and fire a pipeline dispatch. Set it in `~/.hermes/.env`
   and restart `bom_dispatch_server.py`.
