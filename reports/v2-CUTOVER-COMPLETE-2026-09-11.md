# v2 CUTOVER — COMPLETE (2026-09-11)

## Summary
`hooks.sesameaccess.com` now serves **receiver v2** (container), replacing v1 (host).
Cutover done as a hot-reload — no Traefik restart, no container restart, no downtime.

## What changed
ONE file: `/docker/traefik/dynamic/hooks.yml`
  before: `http://127.0.0.1:8088`   (v1, host receiver)
  after : `http://172.17.0.2:8099`  (v2, container receiver)

Traefik `file.watch=true` picked it up within ~4 seconds.

## Verification (all measured, not assumed)
  /health              -> version 2.0, mode detect, armed false        PASS
  POST forged (unsigned)-> 401  (v1 returned 200)                     PASS
  POST bad signature    -> 401                                        PASS
  POST valid signature  -> 200 {"ok":true,"accepted":1}               PASS
  /control/state no token-> 401  (v1 had no control endpoints = 404)   PASS
  TLS                   -> valid Let's Encrypt, expires 10 Dec 2026    PASS
  kill-switch link (public, real token):
        pause  -> 200, paused=true
        resume -> 200, restored to detect                               PASS
  audit after signed event -> reason="mode=detect (not armed)"          PASS
  writes_done = 0 throughout                                           PASS

## Asana webhook
  Old webhook 1218390996958336 DELETED (its handshake secret belonged to v1).
  New webhook 1218404848667646 created -> 201, active=true,
  target https://hooks.sesameaccess.com/webhooks/asana, failure_count=None,
  last_success_at recorded at creation.
  Fresh X-Hook-Secret (32 chars) stored in v2. Only ONE webhook exists on the workspace.

### IMPORTANT BUG FOUND AND FIXED
v2 reported `secret_known: true` but the stored secret was `drill-hook-secret-abc` —
the DRILL's dummy secret, left in the DB from an earlier drill run. v2 would have
rejected every genuine Asana event with 401, and Asana would eventually have deleted
the webhook. Cleared before re-registration. **Lesson: `secret_known: true` does not
mean the secret is REAL — verify the fingerprint, not the flag.**

## Safety: arming is unchanged
A write requires ALL FOUR (sas_hook_receiver.py::can_write):
  1. not paused
  2. state mode == "arm"        (currently "detect")
  3. ARM flag file present      (currently absent)
  4. within rate caps
Cutover did NOT change any of these. **v2 cannot write to Asana.**

## Fragility FIXED: route self-heals
Routing to the container's bridge IP (172.17.0.2) could break if the container were
recreated and given a different IP — a silent failure.

Installed `sas-hooks-route-sync.service` + `.timer`:
  - reads the container's CURRENT IP and rewrites the route if it differs
  - runs on boot (+45s) and every 2 minutes
  - idempotent (silent when correct)
  - PROVEN: corrupted the route to 10.99.99.99, ran the sync, it restored the correct
    target and confirmed health 200.
  - no container modification; traefik hot-reloads.

## Rollback (v1 still running, nothing deleted)
  cp /opt/ses/webhook-receiver/cutover-backup-<stamp>/hooks.yml /docker/traefik/dynamic/hooks.yml
v1 service `sas-webhook-receiver` remains active+enabled on 127.0.0.1:8088.
NOTE: after any rollback, the Asana webhook should be re-registered so the handshake
secret matches whichever receiver is live (v1 stores it on the host, v2 in hooks.db).
Do NOT run both receivers against one webhook - only one may hold the secret.

## Operational commands
  state:        curl -s https://hooks.sesameaccess.com/health
  kill switch:  curl -s "https://hooks.sesameaccess.com/control/pause?token=CODE&confirm=1"
  resume:       curl -s -X POST "https://hooks.sesameaccess.com/control/resume?confirm=RESUME&token=CODE"
  v2 logs:      docker exec hermes-gateway tail -30 /opt/data/sas-hooks/receiver.log
  route sync:   systemctl status sas-hooks-route-sync.timer
  audit:        docker exec hermes-gateway python3 -c "import sqlite3;c=sqlite3.connect('/opt/data/sas-hooks/hooks.db');print(list(c.execute('SELECT rule_id,outcome,reason FROM write_audit ORDER BY rowid DESC LIMIT 10')))"

## Backups
  /opt/ses/webhook-receiver/cutover-backup-20260911-152049/  (route, receiver, rules, unit, ROLLBACK.md)
  /root/reconcile_vps_env.py.bak*  and  .bak2*

## STILL NOT DONE (deliberate)
- AUTO mode: NO-GO. The write path is still unimplemented and the matcher issues from
  the audit are unresolved. Do not arm until those are fixed and a shadow phase passes.
- No backfill of pre-existing INHOUSE-named tasks (webhooks fire on new events only).
- v1 left in place as rollback (do not delete yet).

## Corrections to earlier notes (I was wrong, recording for accuracy)
1. There IS a supervisor: os_dash_watch.sh, not missing. Earlier "no supervisor" claim
   was a bad filename search.
2. Rule ids DO match: rules.json has `inhouse-to-stock-action` and the drill reads it
   dynamically. The earlier "mismatch" note was wrong.
3. `rules.json` has no `mode` field - that is CORRECT. Mode lives in the DB state table,
   seeded from SAS_MODE env.
