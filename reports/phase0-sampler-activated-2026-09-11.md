# Phase 0 Sampler Activated — 2026-09-11

## The step
Restarted the container-side receiver so the Phase 0 (D3) raw-payload sampler loads.

Why a restart was needed: the on-disk code was newer (20:38) than the running process
(started 14:53). The spool code was written but the live process predated it.

Sequence run (inside the container):
  pkill -f 'sas_hook_receiver.py'
  nohup /opt/data/sas-hooks/svc-run.sh >> /opt/data/sas-hooks/receiver.log 2>&1 &
  sleep 3 ; curl -s http://127.0.0.1:8099/health

## Result
BEFORE: PID 583, no spool block in /health
AFTER : PID 4580, and /health now includes:
  "spool": {"files": 0, "bytes": 0}
Exactly ONE instance running.

Verified expected shape:
  version=2.0  mode=detect  armed=false  paused=false  +  spool block   -> CONFIRMED

## Spool confirmed filling
Posted one correctly SIGNED event (200 {"ok":true,"accepted":1}).
  /opt/data/sas-hooks/spool/raw-2026-09-11.jsonl created (465 bytes)
  Record shape: ts, event_gid, resource_gid, action, change_field, raw(payload)
  /health spool block then read {"files": 1, "bytes": 465}   -> CONFIRMED

## Retention (from the code)
  SAS_RAW_SPOOL_DIR  default /opt/data/sas-hooks/spool
  SAS_RAW_SPOOL_DAYS default 7    (files older than this pruned)
  SAS_RAW_SPOOL_MAX_MB default 64 (per-file size cap; stops appending when exceeded)
  prune runs every 200 spool writes, plus on startup.
Spool failures are caught and logged (SPOOL ERROR) so they cannot break event handling.

## Safety: unchanged, and re-verified after the restart
  no ARM flag present                        -> writes impossible
  svc-run.sh still exports SAS_MODE=detect   -> mode=detect
  health reports armed=false, mode=detect
  writes_done=0
The restart did NOT alter the arming model. Four gates still required for any write.

## Resilience: spool survives a crash
Killed the process, ran the watchdog, it restarted and health came back with
  version=2.0 mode=detect armed=false spool={"files":1,"bytes":465}
i.e. the watchdog starts the receiver via svc-run.sh, so the spool env is applied
automatically on recovery. Sampler is not dependent on a manual start.

## Public endpoint unaffected
  https://hooks.sesameaccess.com/health -> version=2.0, mode=detect, armed=false,
  and the same spool block. Cutover intact.
  Asana webhook 1218404848667646 active, failure_count=None.

## Note
Restarting resets the in-memory stats counters (events_received etc.) to zero.
That is expected - they are process-lifetime counters, not historical totals.
Historical data lives in hooks.db (webhook_events, write_audit) and now the spool.
