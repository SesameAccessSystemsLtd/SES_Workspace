# Kill-Switch Token: Doppler-sourced + restart-proof supervision
Date: 2026-09-11  |  Status: COMPLETE and VERIFIED

## What was done

### 1. Token rotated into Doppler (value never handled by Giles)
- Generated a fresh 48-char url-safe token directly in Doppler.
- Project: `vps_hermes` / config `prd`  (the project the VPS reconcile actually reads).
- Name: `SAS_CONTROL_TOKEN`
- Removed from `openclaw/prd` where it was mistakenly created first, so it lives in
  exactly ONE place.
- Verified: `doppler secrets get SAS_CONTROL_TOKEN --project vps_hermes --config prd` -> PRESENT.

### 2. Reconcile script fixed - TWO latent bugs found
`/root/reconcile_vps_env.py` (host) is the bridge Doppler -> container `.env`.

Bug A - key not whitelisted. `SAS_CONTROL_TOKEN` was absent from BOTH:
  - the `keep` list  (so it was never READ from Doppler)
  - the `order` list (so it was never WRITTEN to .env even if read)
Fixed: added to both.

Bug B - DESTRUCTIVE drop logic (pre-existing, dangerous).
The script computed `removed = [k for k in live if k not in new]` and would DELETE any
live key not in its managed set. Dry-run proved it would have deleted
`ASANA_PAT_MCP_KEY` and `ASANA_CLIENT_ID` (they are gated behind an `ASANA=1` env flag
that was not set) - breaking the container's Asana MCP.
Fixed: reconcile now NEVER auto-deletes. Unmanaged keys are reported and PRESERVED.
The writer explicitly carries forward every live key it is not managing.

Backups: `/root/reconcile_vps_env.py.bak.*` and `.bak2.*`

### 3. Applied
`python3 /root/reconcile_vps_env.py --apply` -> wrote .env, restarted container.
Result: `SAS_CONTROL_TOKEN` (len 48) in the container env, sourced from Doppler.
`ASANA_PAT_MCP_KEY` and `ASANA_CLIENT_ID` confirmed PRESERVED.

## Verification (all observed, not assumed)

| Check | Result |
|---|---|
| Doppler has the token | `vps_hermes/prd` PRESENT |
| Wrong project cleaned | `openclaw/prd` absent |
| Container .env carries it | len=48 |
| v2 reads it | receiver log `token_set=True` |
| Kill switch pauses | http=200, `paused=True, by="kill-switch link"` |
| Kill switch resumes | http=200 -> `paused=False, mode=detect` |
| Never arms | `armed=False` throughout |
| Public endpoint unaffected | v1 still serving, 9/9 guardrails |
| Drill after fix | **26/26** (proven when live v2 stopped - see below) |

## Supervision: survives restart (this was the key gap)

I was WRONG earlier that the watchdog was missing - I searched by the wrong filename.
`/opt/data/scripts/os_dash_watch.sh` exists and includes a section 3 that supervises v2.

Mechanism:
  Hermes cron job `2cfeb3ed9fc2` "OS Dashboard watchdog", `*/5 * * * *`, no_agent,
  runs `os_dash_watch.sh`, which restarts v2 if `http://127.0.0.1:8099/health` is not ok.

PROVEN by killing v2 and watching recovery:
  v2 pid before: 470  ->  killed  ->  http=000
  watchdog: "SAS webhook receiver restarted OK"
  v2 pid after: 583   ->  ok=True, mode=detect, armed=False
  token_set=True survived the restart (Doppler-sourced - no manual step)

Persistence (all on the bind mount `/opt/ses/.hermes` == container `/opt/data`, so an
image upgrade does NOT lose them):
  scripts/os_dash_watch.sh, sas-hooks/svc-run.sh, os_dashboard/tools/sas_hook_receiver.py,
  cron/jobs.json
Nothing of ours lives in `/etc/s6-overlay` (image-side), which is deliberate.

Residual limitation, stated honestly: worst-case downtime is up to ~5 minutes, set by the
watchdog interval. Tighten by changing the cron expr if that matters.

## Tidy-up
- The stale plaintext token file (`sas-hooks/kill-switch-token.txt`) held the SUPERSEDED
  value (nothing reads it in code). Moved to
  `sas-hooks/archive/kill-switch-token.SUPERSEDED-20260911.txt` (0600, dir 0700).
- Doppler is now the single source of truth for this credential.

## Still open (unchanged, nothing armed)
- Public endpoint still serves v1. Cutover to v2 NOT done.
- AUTO mode: still NO-GO (write path does not exist; matcher/sticky-field issues from the audit).
- Minor: drill references rule id `inhouse-to-stock-action`; rules.json says
  `inhouse-stock-action`. Reconcile before arming.
- The kill-switch URL should be kept as PLAIN TEXT (not a hyperlink in a shared Asana task):
  Asana can rewrite/truncate the trailing `&confirm=1`.
