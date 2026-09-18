# SAS Webhook Receiver v2 — Deployment Findings & Outstanding Work

**Date:** 2026-09-15
**Author:** Hermes (orchestration)
**Status:** Phases B & C COMPLETE — deployment successful and verified. Phases D-G outstanding.
**Scope:** `sas_hook_receiver.py` v2, `hooks.sesameaccess.com` Asana webhook receiver, SAS VPS (`srv1903976` / 191.215.40.116)

---

## 1. Executive summary

A webhook receiver delivering Asana events to `hooks.sesameaccess.com` was running on a **stale build from 11 Sep 2026** inside the `hermes-gateway` Docker container, as **root**, launched ad-hoc via `nohup`. The stale build was emitting a continuous SQLite error (`Error binding parameter 7: type 'dict' is not supported`) and was loading `rules_version 2` instead of the version on disk.

A controlled deployment was executed on 2026-09-15:
- Spool and archive directories were migrated from `root` to `hermes` (uid 10000)
- The receiver was restarted **once**, **as `hermes`**, via its own frozen runner script
- All 9 health acceptance checks passed; the new build is live and serving production traffic
- Verified with **real traffic** (13 events) and **zero** SQLite errors

**One open risk remains** (see section 6.1): the supervising cron watchdog restarts the receiver as **root**, which will silently revert the ownership fix on any future restart.

---

## 2. The actual architecture (verified facts)

There are **TWO** receivers on **TWO** different systems. Only one serves public traffic.

### v2 - LIVE (serves `hooks.sesameaccess.com`)

| Property | Value |
|---|---|
| Location | Inside Docker container `hermes-gateway` |
| Container ID | `ef8a0f9c9c4c` (image `nousresearch/hermes-agent`) |
| Container IP | `172.17.0.2` (bridge network) |
| Container restart policy | `unless-stopped` |
| Docker healthcheck | **none** |
| Effective user | `hermes` (uid 10000) - was `root` before 2026-09-15 |
| PID | Container 29627 (was 4678 before) |
| Started | 2026-09-15 14:59:22 UTC |
| Command | `python3 /opt/data/os_dashboard/tools/sas_hook_receiver.py` |
| Container source path | `/opt/data/os_dashboard/tools/sas_hook_receiver.py` |
| Host source path | `/opt/ses/.hermes/os_dashboard/tools/sas_hook_receiver.py` (same file, bind mount) |
| Listening | `0.0.0.0:8099` in-container; host reaches it at `172.17.0.2:8099` |
| Runner script | `/opt/data/sas-hooks/svc-run.sh` (host: `/opt/ses/.hermes/sas-hooks/svc-run.sh`) |
| Database | `/opt/data/sas-hooks/hooks.db` (WAL mode) |
| Spool | `/opt/data/sas-hooks/spool/raw-YYYY-MM-DD.jsonl` (7-day retention, 64 MB/day cap) |
| Rules | `/opt/data/os_dashboard/tools/rules.json` |

### v1 - DORMANT (rollback only, serves no public traffic)

| Property | Value |
|---|---|
| Location | VPS **HOST**, native process, systemd-managed |
| Unit | `sas-webhook-receiver.service` (enabled, active, `Restart=always` / 5s) |
| User | `root` |
| Main PID | 312766 |
| Command | `/usr/bin/python3 /opt/ses/webhook-receiver/receiver.py` |
| Listening | `127.0.0.1:8088` |
| Health | No `version` field (v1 build), `mode: detect` |
| Source hash | `c539e1aeade7fd888e3b211ba76aa06d9f9b69922213eab9c81b695ed88d642c` |

### Public traffic path

```
Asana --> Internet --> Traefik (traefik-traefik-1, host netns, owns :80/:443)
          --> file-provider route /docker/traefik/dynamic/hooks.yml
          --> http://172.17.0.2:8099  (container v2, port 8099)
```

- TLS: Let's Encrypt via HTTP-01 resolver. Chain verified from off-box: `Verify return code: 0 (ok)`
- `hooks.sesameaccess.com` = **DNS-only** A record -> 191.215.40.116 (apex is Cloudflare-proxied - do not confuse)
- ufw allows 22/80/443. 80/443 were opened because they were the blocker (Traefik was listening but ufw was dropping).

---

## 3. Supervision inventory

### v2 (container) - supervised by a Hermes cron job

| Item | Detail |
|---|---|
| Job | `2cfeb3ed9fc2` - "OS Dashboard watchdog" |
| Schedule | `*/5 * * * *` (every 5 minutes) |
| Mode | `no_agent` (script only) |
| Script | `/opt/data/scripts/os_dash_watch.sh` |
| Restart mechanism | Section 3: health-checks 8099; if down, runs `nohup /opt/data/sas-hooks/svc-run.sh >> receiver.log 2>&1 &` |
| **PROBLEM** | The restart line has **no `-u`**, so it restarts as **root** (see section 6.1) |
| Worst-case downtime | ~5 minutes |
| s6-overlay | Manages only `dashboard` and `main-hermes`. **No s6 service for the receiver.** |
| Docker healthcheck | None |

### Host-level supervision (does NOT touch v2)

| Unit | Purpose | State |
|---|---|---|
| `sas-webhook-receiver.service` | v1 receiver (rollback) | active, enabled |
| `sas-hooks-route-sync.service` + `.timer` | Re-points Traefik route at the container's CURRENT IP:8099 | active; `OnBootSec=45s`, every 2 min |
| `/root/vps-env-watch.sh` (root crontab, `*/5`) | Doppler -> `.env` reconcile | active. **This is the authority for `SAS_CONTROL_TOKEN`** |

### Other container cron jobs (read/report only - cannot restart the receiver)

| ID | Name | Schedule |
|---|---|---|
| `56658ad1777c` | Webhook liveness + arming monitor | `7,37 * * * *` |
| `9c83cd4a3336` | Asana change audit (write ledger) | `13 * * * *` |
| `6873d0b39eae` | Phase 0 Observer (webhook watch & listen) | every 10 min |
| `ccdd4a2b441f` | Live sync - Asana -> live.db (hourly) | `23 7-19 * * 1-5` |
| `15749c3ab376` | Live sync - Asana -> live.db (daily) | `20 2 * * *` |
| `4ae38b903b0a` | Supplier mirror refresh | `10 3 * * *` |

---

## 4. Root cause analysis

### 4.1 The SQLite dict-binding error

**Symptom:** `WORKER ERROR: ProgrammingError: Error binding parameter 7: type 'dict' is not supported`
**Volume:** 2,624 occurrences, first at 2026-09-14T08:03:10, roughly 1-3 per second during event bursts.

**Location in code:** the `INSERT INTO webhook_events(...)` in `process_events()`, parameter index 7 = the `actor` field. The `webhook_events.actor` column is TEXT; a Python dict cannot be bound to it. Asana's event payload delivers `actor` as a JSON object.

**Conclusion - the error is a defect of the STALE BUILD, not of the current source.**

Evidence:
1. **Isolated replay test (B1).** 4,000 real spooled events were replayed through the *current* source in a scratch copy of the database, with the HTTP write path stubbed and `SAS_ASANA_TOKEN` blank. Result: **4,000 processed OK, 0 failures.** Dedupe verified: replaying the same set produced 1,818 correct `events_duplicate` detections, no exceptions.
2. **Spool field census.** Across 7,509 spooled records, `actor` is not present as a top-level field at all - only `event_gid`, `resource_gid`, `action`, `change_field`. So `ev.get("actor")` returns `None`, which binds fine.
3. **Live DB rows.** `actor` values present are `'drill'`, `'cutover-test'`, `None` - all scalars.
4. **Post-deployment proof.** After restarting onto the current source, real traffic arrived (13 events) and **zero** new dict-binding errors occurred.

The running process predated the current source by ~3.5 days, so the exact failing statement could not be recovered from the old build. It is now moot: the current build does not exhibit the defect.

### 4.2 The stale-process problem

| Marker | Stale build (before) | Current build (after) |
|---|---|---|
| `secret_count` in `/health` | **absent** | **present = 1** |
| `rules_version` in `/health` | `2` | `5` |
| Process start | 2026-09-11 20:59:28 | 2026-09-15 14:59:22 |
| Source mtime | - | 2026-09-15 12:03:41 |

`secret_count` is a field only the new build emits - its absence was the proof of staleness, and its presence is the proof of successful deployment.

### 4.3 The ownership trap

Before deployment, the spool directory and raw files were **root-owned** (`0:0`) while everything else in `sas-hooks/` was hermes-owned. Because the receiver samples raw payloads into that directory, **a restart as `hermes` would have broken spool writes** while the DB continued to work - a confusing partial failure. This was why the process had to keep running as root. Fixed by the migration in section 5.

Direct evidence (probe as hermes, before the fix):
```
SPOOL_NOT_WRITABLE_BY_HERMES
DB_WRITABLE / DIR_WRITABLE / LOG_WRITABLE
```

### 4.4 Why the original instruction failed

A handoff instruction (via Telegram) asked for a host-level restart:
```
pkill -f 'sas_hook_receiver.py'
su -s /bin/sh hermes -c 'nohup /opt/data/sas-hooks/svc-run.sh >> /opt/data/sas-hooks/receiver.log 2>&1 &'
```

It was **not executable as written**, because it mixed container and host contexts:
- There is **no `hermes` user on the VPS host** - uid 10000 `hermes` exists only *inside* the container
- `/opt/data/...` is a **container** path (host equivalent is `/opt/ses/.hermes/...`)
- Running `pkill -f 'sas_hook_receiver.py'` on the host would have matched and killed the live container process, then failed to restart it (no such user, no such path) - leaving the public endpoint down until the watchdog's next 5-minute tick

The paths in that instruction are *correct for the container*; only the execution prefix was wrong. Correct form: `docker exec -u 10000:10000 hermes-gateway /opt/data/sas-hooks/svc-run.sh`

---

## 5. What was done (2026-09-15, all verified)

### Frozen deployment candidate

| Artefact | SHA-256 | Size | mtime (UTC) |
|---|---|---|---|
| `sas_hook_receiver.py` | `eb96158da0c87e8c39d3d347e1715b31a08c5735a66fee416a8558690b90a371` | 33,987 | 2026-09-15 12:03:41 |
| `rules.json` | `fa6581b0573453c18933a53949b51030e674a097963f0735f3f087e4c10c742e` | 2,911 | 2026-09-15 12:08:36 |
| `svc-run.sh` | `1b09692d3536efa546be69ac828c2b1beaadfc265616f2d6e1c3ab419be41c99` | 753 | 2026-09-11 12:07:03 |

- Declared rules version on disk: **5** (rule count 1, rule id `inhouse-to-stock-action`)
- Quiescence verified across two probes 60 minutes apart (12:03/12:08 unchanged at both 14:36 and 14:37)

> **Note:** the acceptance target was corrected mid-project. An earlier handoff stated "rules_version=4"; the actual frozen value is **5**. The live health endpoint had reported `2`.

### Phase B - permissions migration

| Step | Action | Result |
|---|---|---|
| B1 | Paused cron watchdog `2cfeb3ed9fc2` | Caught 47s before its 15:00:00 fire - prevented a mid-migration root restart |
| B1 | Stopped the stale process (pid 4678) | 0 instances, port 000 |
| B2 | `chown -R 10000:10000` + `chmod 750` on `spool/` and `archive/` | owners now `10000:10000` |
| B3 | Write tests **as hermes** | All writable; `SPOOL-ROUNDTRIP-OK` |
| B4 | Re-verified frozen hashes | Unchanged |

### Phase C - controlled restart

Single start, as hermes:
```
docker exec -u 10000:10000 -d hermes-gateway /opt/data/sas-hooks/svc-run.sh
```

**Health acceptance - 9/9 PASS:**
```
ok = true
version = 2.0
secret_count = 1          <- new-build marker
mode = detect
armed = false
paused = false
rules_version = 5
rule_count = 1
spool block present
```

**Public endpoint (verified from OFF the VPS):**
- TCP 443 OPEN
- `GET https://hooks.sesameaccess.com/health` -> **HTTP 200**, `secret_count: 1`, `rules_version: 5`
- TLS: `CN=hooks.sesameaccess.com`, issuer Let's Encrypt, `Verify return code: 0 (ok)`
- Unsigned POST `/webhooks/asana` -> **HTTP 401** (signature gate working publicly)

**Control path - tested with a VALID token for the first time:**

| Request | Result |
|---|---|
| `/control/state?token=<valid>` | 200 |
| `/audit/recent?token=<valid>` | 200 |
| `/events/recent?token=<valid>` | 200 |
| `/control/state?token=<bogus>` | 401 |
| `/control/state` (no token) | 401 |
| `control_denied` counter | 0 -> 2 (exactly the two bad attempts) |

This **resolves the 2026-09-11 finding** that the kill switch was broken (`SAS_CONTROL_TOKEN` not loaded). The token is now present in `/opt/data/.env` (48 chars) and `token_set=True` appears in the startup log.

**Monitoring window - the decisive test, with real traffic:**
```
T0    events_received=0   errors=0  db_rows=4726  spool_bytes=5,027,173
T100  events_received=1   errors=0  db_rows=4727  spool_bytes=5,027,662
T120  events_received=13  errors=0  db_rows=4735  spool_bytes=5,034,013

DELTA: events +13 | errors +0 | db_rows +9 | spool +6,840 bytes
writes_attempted = 0 | writes_done = 0
```
- 13 real events processed with **zero** SQLite errors
- DB rows advanced **and** spool bytes grew -> confirms both write paths work as hermes (validates Phase B)
- `writes_attempted = 0` with `mode=detect`, `armed=false` -> safety model intact

Watchdog restored; its 15:00:19 tick recorded "silent (empty output)" = correct no-op (found health already ok).

---

## 6. Outstanding work

### 6.1 HIGH - the watchdog will silently undo the ownership fix

**Problem:** `os_dash_watch.sh` section 3 restarts the receiver with a bare:
```sh
nohup /opt/data/sas-hooks/svc-run.sh >> /opt/data/sas-hooks/receiver.log 2>&1 &
```
No `-u`. So on the next crash/recovery, the receiver comes back **as root**, and subsequently-created spool files will be root-owned again - reintroducing the exact trap that was just cleared. The process still *works* (root can write hermes-owned dirs), so this fails silently and non-deterministically.

**Fix options:**
1. Minimal: add `-u 10000:10000` - i.e. `nohup su -s /bin/sh hermes -c '/opt/data/sas-hooks/svc-run.sh' >> ... 2>&1 &` or wrap in an s6 service
2. Preferred: move supervision to **s6-overlay** (the image already uses s6 for `dashboard` and `main-hermes`), and demote the cron watchdog to **reporter-only** (alert, never restart). This removes the 5-minute blind window and makes the runtime user deterministic.
3. Either way: the watchdog should assert **exactly one instance** before starting another.

**Effort:** S (option 1) / M (option 2)

### 6.2 MEDIUM - secret duplication and rotation

`SAS_CONTROL_TOKEN` exists in more than one place:

| Location | Notes |
|---|---|
| Doppler `vps_hermes/prd` | **The authority.** Reconciled to `.env` every 5 min by `/root/vps-env-watch.sh` |
| `/opt/data/.env` (= host `/opt/ses/.hermes/.env`) | Intended consumer copy |
| `/opt/data/sas-hooks/kill-switch-link.txt` | Full control URL **including the token**, mode 0600. **This value was read during the investigation and should be treated as exposed.** |
| `/opt/data/sas-hooks/archive/kill-switch-token.SUPERSEDED-20260911.txt` | 0600, labelled superseded |
| Referenced in several docs/tools | `webhook-receiver-v2-deployment.md`, `webhook-guardrails-and-audit.md`, `automated-write-gate.md`, `drill_webhook_receiver.py`, `phase0_replay_test.py`, `measure_load.py`, `inhouse_rule_test.py` |

**Actions:**
- Rotate `SAS_CONTROL_TOKEN` in **Doppler** (the authority), then restart so the link file regenerates
- Note: rotating invalidates the two local token copies. Confirm nothing depends on the old value first
- Decide the long-term home for the kill-switch link - a credential in a plaintext file next to an internet-facing service is worth retiring

**Effort:** S

### 6.3 MEDIUM - build markers for deterministic deployment verification

Today, "did the new build load?" was answerable only by comparing file hashes against process start time, or by noticing that `secret_count` was absent. That is fragile - the version string (`2.0`) did **not** distinguish builds.

**Action:** expose build identity in `/health`, e.g.:
```json
"build": {"source_sha256": "eb9615...", "rules_sha256": "fa6581...", "rules_version": 5, "started_at": "..."}
```
Then deployment verification becomes a single health assertion instead of a hash comparison.

**Effort:** S

### 6.4 MEDIUM - v1 retirement

`/opt/ses/webhook-receiver/` holds a complete parallel deployment (source, docs, tests, backup, kill-switch guides). It is **rollback-only** and serves no traffic.

**Action:** after a defined soak period on the new architecture, retire it - but first update its documentation to state plainly that it is rollback-only, so no future reader treats it as live.

**Effort:** S

> **Rollback gotcha:** `sas-hooks-route-sync.timer` rewrites `hooks.yml` from `docker inspect` every 2 minutes. **Any manual rollback that edits that file MUST stop the timer first**, or it will be silently undone within 2 minutes.

### 6.5 MEDIUM - documentation consolidation

Current docs are split across two worlds and neither is authoritative:

| Location | Contents |
|---|---|
| `/opt/ses/webhook-receiver/` (host) | `AUDIT-2026-09-11.md`, `DEPLOYMENT.md`, `GUARDRAIL-STATUS.md`, `GUARDRAILS.md`, `KILL-SWITCH-IDIOTS-GUIDE.md`, `KILLSWITCH-DOPPLER-SUPERVISION.md`, `PHASE0-SAMPLER-ACTIVATED.md`, `V1-V2-VERIFICATION.md`, `V2-CUTOVER-COMPLETE.md` |
| `/opt/ses/.hermes/sas-hooks/` (container) | `V1-V2-VERIFICATION-2026-09-11.md` |
| `/opt/ses/.hermes/os_dashboard/docs/` | `webhook-receiver-v2-deployment.md`, `webhook-guardrails-and-audit.md` |

**Action:** produce ONE authoritative page covering: which world owns what, the single deploy command, the single restart command, rollback, and the secret's single source of truth.

**Effort:** S

### 6.6 LOW - correct the Telegram agent's instruction template

The handoff instruction that started this used host-context `su` syntax for a container-native service. **Action:** correct the template so it emits container-native commands (`docker exec hermes-gateway ...`) and never host `su` lines for this service.

**Effort:** S

### 6.7 LOW - target architecture (agreed direction)

The end state agreed with Giles:

- **One codebase** - single source of truth for deployment
- **One deployment path** - edit, restart via the single runner, verify a build marker
- **One restart path** - `/opt/data/sas-hooks/svc-run.sh`, always as the same user
- **One supervisor** - s6 (preferred) or cron-as-reporter-only; never two restarters
- **One source of truth for secrets** - Doppler -> `.env`, nothing else
- **v1 retained as rollback only**
- Receiver runs **as hermes**; spool, DB and logs owned by hermes
- `/health` includes build markers so deployment verification is deterministic

**Host responsibilities (root, only where genuinely required):** ufw rules; the Traefik file-provider route + its sync timer (needs `docker inspect` for the container IP); the Doppler -> `.env` reconcile.
**Container responsibilities:** running the receiver, owning its DB, spool, logs, rules and kill-switch artefacts.

---

## 7. Operating reference - commands

### Restart the receiver (the ONE correct way, as hermes)
```bash
# always assert zero instances first
docker exec hermes-gateway sh -c 'ps -eo pid,args | grep -E "[s]as_hook_receiver" | awk "{print \$1}" | xargs -r kill'
sleep 2
docker exec -u 10000:10000 -d hermes-gateway /opt/data/sas-hooks/svc-run.sh
sleep 4
docker exec hermes-gateway sh -c 'curl -s http://127.0.0.1:8099/health'
```

### Verify
```bash
docker exec hermes-gateway sh -c 'ps -eo pid,user,args | grep -E "[s]as_hook_receiver"'   # exactly 1, user hermes
docker exec hermes-gateway sh -c 'curl -s http://127.0.0.1:8099/health'                    # secret_count present, rules_version 5
curl -s https://hooks.sesameaccess.com/health                                              # from OFF the VPS
```

### Safety invariants (must hold)
```bash
docker exec hermes-gateway sh -c 'ls -la /opt/data/sas-hooks/ARM /opt/data/sas-hooks/WRITE_HALT'  # both must NOT exist
# /health must show: mode=detect, armed=false
```

### Rollback to v1
```bash
systemctl stop sas-hooks-route-sync.timer          # MUST stop first, or the timer undoes you
# edit /docker/traefik/dynamic/hooks.yml -> url: "http://127.0.0.1:8088"
curl -s https://hooks.sesameaccess.com/health      # expect v1 payload (no "version" field)
```

### Files and paths

| Thing | Host path | Container path |
|---|---|---|
| Receiver source | `/opt/ses/.hermes/os_dashboard/tools/sas_hook_receiver.py` | `/opt/data/os_dashboard/tools/sas_hook_receiver.py` |
| Rules | `/opt/ses/.hermes/os_dashboard/tools/rules.json` | `/opt/data/os_dashboard/tools/rules.json` |
| Runner | `/opt/ses/.hermes/sas-hooks/svc-run.sh` | `/opt/data/sas-hooks/svc-run.sh` |
| DB | `/opt/ses/.hermes/sas-hooks/hooks.db` | `/opt/data/sas-hooks/hooks.db` |
| Spool | `/opt/ses/.hermes/sas-hooks/spool/` | `/opt/data/sas-hooks/spool/` |
| Log | `/opt/ses/.hermes/sas-hooks/receiver.log` | `/opt/data/sas-hooks/receiver.log` |
| Env | `/opt/ses/.hermes/.env` | `/opt/data/.env` |
| Traefik route | `/docker/traefik/dynamic/hooks.yml` | - |
| v1 (rollback) | `/opt/ses/webhook-receiver/` | - |

---

## 8. Lessons / pitfalls for future work

1. **A stale process serving while your change is inert is the signature failure of this whole area.** Compare file mtime against process start time, or better, use a field only the new build emits. `version: 2.0` did **not** distinguish builds - a build marker would have.
2. **A boolean about a credential is not the credential.** `secret_known: true` hid that the token had been missing; and the 11 Sep drill only proved the *no-token* case - a control must be tested with a **valid** credential.
3. **Never assume a service is unsupervised because a `find` came back empty.** The watchdog existed at `/opt/data/scripts/os_dash_watch.sh` - a path a `*sas*hook*` pattern could never match. Search by the claimed path, not a guessed pattern.
4. **Verify from off-box.** Listening != reachable. Loopback and hairpin results lie.
5. **Don't fix a hypothesised cause.** The actor-dict theory was plausible, matched the error text, and was **wrong** - an isolated replay disproved it in minutes. Reproduction beats reasoning about an error message.
6. **A restart alone is not a deployment.** Restarting proves the process came up; only real traffic proves it *works*. The 13-event window (DB rows + spool bytes both advancing, zero errors) is what actually validated the change.
7. **Ownership must match the runtime user before the restart, not after** - otherwise the failure is partial and confusing (DB fine, spool silently erroring).
8. **Host vs container is the recurring trap in this whole area.** `/opt/data/...` is container; `/opt/ses/.hermes/...` is host; uid 10000 `hermes` exists only in the container. Write the world into the command.

---

## 9. Change log

| Date | Change | By |
|---|---|---|
| 2026-09-11 | v2 built; v1->v2 cutover; Traefik route added; ufw 80/443 opened. v1 retained as rollback | Hermes (prior session) |
| 2026-09-15 | Frozen candidate `eb96158d` / rules `fa6581b0` (v5) | Hermes |
| 2026-09-15 | B1 isolated replay: 4,000 events, 0 failures - dict-defect attributed to stale build | Hermes |
| 2026-09-15 | Phase B: spool + archive migrated root -> hermes, chmod 750 | Hermes |
| 2026-09-15 | Phase C: single restart as hermes; 9/9 health checks; public + control path verified; 13-event monitor clean | Hermes |
| 2026-09-15 | Watchdog restored; 15:00:19 tick = correct no-op | Hermes |
