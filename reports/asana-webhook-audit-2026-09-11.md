# Asana Webhook Deployment — Full Audit & AUTO-Mode Readiness
Date: 2026-09-11  |  Auditor: Hermes  |  Status: LIVE in DETECT mode

## 1. EXECUTIVE SUMMARY

The webhook is registered, active, authenticated and receiving real Asana events.
The infrastructure layer (DNS, TLS, Traefik, firewall, reboot survival) is sound.

**The single most important finding: AUTO mode does not exist yet.**
`RECV_MODE` currently only controls a log string and a DB note. There is NO Asana
write code in the receiver at all (independently verified:
`grep -E 'app.asana.com|Bearer|urllib|requests'` returns nothing in the code).
Flipping RECV_MODE=auto today would change nothing and write nothing — the
business would believe automation was live while it silently did nothing.
That is a more dangerous state than a visible failure.

Additionally, the audit found that even once write code IS added, the receiver as
built would be **less reliable than Giles doing it by hand**, for three reasons:
the INHOUSE flag is sticky (no un-set path), the substring matcher can invert a
decision ("NOT INHOUSE"), and there is no idempotency, risking a write→event→write
feedback loop they cannot currently detect.

Recommendation: **NO-GO for AUTO mode now.** DETECT mode is safe and should stay on.

## 2. VERIFIED STATE (evidence, not assertion)

| Item | Result | Evidence |
|---|---|---|
| DNS hooks.sesameaccess.com | OK | resolves 191.215.40.116 |
| Inbound 80/443 | OK | external probe OPEN; nft counters accepting |
| TLS certificate | OK | LE chain verify return code 0; CN=hooks.sesameaccess.com |
| Cert state on disk | OK | acme.json 16000 bytes, live ACME state |
| Cert auto-renewal | OK | Traefik renews <30d remaining; no action needed |
| Traefik routers | OK | 1 file, no duplicate/conflicting rules |
| Docker/route conflict | OK | gateway has no traefik labels; no double-routing |
| Reboot survival | OK | traefik restart=unless-stopped; unit enabled; docker enabled |
| Rule hot-reload | OK | providers.file.watch=true |
| Receiver service | OK | systemd active + enabled; Restart=always |
| Webhook registration | OK | gid 1218390996958336, active=True, failure_count=None |
| Handshake secret | OK (now) | stored; perms corrected 644 -> 600 |
| Source == deployed | OK | sha256 identical: receiver fede6f06 -> 5a8d07ee (v2), rules 8b2851f0 -> 66d41672 |
| DETECT mode writes to Asana | NONE | asana_write_log unchanged by receiver (16 pre-existing rows, all 2026-08-12 legacy) |

## 3. RISK REGISTER

### CRITICAL
**C-1  No webhook signature verification.**
The receiver never reads `X-Hook-Signature` and never validates HMAC. The endpoint is
public. Anyone who finds the URL can POST a forged event with any `resource.gid` and a
`new_value` containing the trigger word. In DETECT this pollutes the log; in AUTO it
would drive REAL writes to arbitrary production tasks.
*Fix:* verify HMAC-SHA256 (`hmac.compare_digest`) over the raw body; reject on mismatch.
This is the single gating control for the whole trust model.

**C-2 (business) The INHOUSE field is sticky — no un-set path.**
The rule only ever SETS INHOUSE. If a part is later changed to buy-in and the name is
edited, the field still says INHOUSE. Name and field then disagree and downstream systems
read the field. Silent, and guaranteed to occur eventually.
*Fix:* decide and implement the un-set rule (or an explicit review workflow).

### HIGH
**H-1  Unauthenticated X-Hook-Secret overwrite.** Any POST with an X-Hook-Secret header
overwrites the stored secret and inserts a DB row, unauthenticated. Disk-DoS plus
credential corruption; becomes worse once signatures are added.
*Fix:* only accept a handshake when one is actually expected (pending-handshake state).

**H-2  Always-200 swallows failures.** The receiver returns 200 even when persistence
fails. Asana retries only on non-2xx, so any transient failure is permanent, silent
event loss.
*Fix:* return 5xx on failure so Asana retries; alert on failures.

**H-3  Bad rules.json → crash loop or per-request crash.** `load_rules()` is unguarded
and called outside try/except. One malformed deploy produces an infinite restart loop
(Restart=always/RestartSec=5) while /health still reports "ok".
*Fix:* validate on load; cache; make /health actually check DB + rules.

**H-4  Rule `enabled` flag was ignored.** Setting `enabled:false` had no effect — an
operator could not emergency-disable a rule. **FIXED today** (see section 5).

**H-5  Unbounded body / malformed JSON crashed handlers.** No size cap; valid-JSON
non-objects crashed the thread with no response. **FIXED today** (see section 5).

**H-6  Runs as root with no hardening.** No NoNewPrivileges/ProtectSystem/MemoryMax.
*Fix:* dedicated unprivileged user + systemd hardening directives.

### MEDIUM
**M-1** SQLite connections never explicitly closed (rely on refcounting); repeated
`PRAGMA journal_mode=WAL` can raise SQLITE_BUSY under concurrency.
**M-2** Dedup gap: `INSERT OR IGNORE` on a UNIQUE gid does not dedup NULL gids, and there
is no `acted`/idempotency marker, so it cannot give exactly-once behaviour once writes exist.
**M-3** Over-loose substring matching — "NOTINHOUSE", "inhousehold", "REVERT INHOUSE" all
match. Directly risks wrong production writes.
**M-4** No logrotate for receiver.log; unbounded growth of webhook_events/webhook_handshakes.
**M-5** No monitoring/alerting; /health returns ok unconditionally without touching DB or rules.
Access logging is disabled, so there is no source-IP audit trail.

### LOW
**L-1** Prefix path matching accepts arbitrary suffixes (`/webhooks/asanaXYZ`).
**L-2** No Content-Type handling; chunked bodies silently dropped.
**L-3** Hardcoded Asana GIDs unvalidated — a rotated/wrong GID fails silently.
**L-4** Uncaught `int()` on malformed Content-Length. **FIXED today.**

### WORKSTATION (item 8)
**W-1 (HIGH for dev ergonomics)** Hermes terminal is broken. ROOT CAUSE CORRECTED:
not a missing user PATH entry (as previously recorded) but **machine PATH ordering** —
`C:\Windows\system32` contains `bash.exe` (WSL stub) and is searched BEFORE the user
PATH, so it always shadows git-bash. Any user-PATH-only fix can never work, which
explains the recurrence. Backup of the previous PATH saved to
`SES_Workspace/memory/terminal_path_backup_2026-09-11.json`.
*Fix:* prepend `C:\Program Files\Git\bin` to the MACHINE path (needs admin + your OK).
Impact: `write_file`/`search_files`/`terminal` all fail, so agents silently fall back to
workarounds and files can appear "not written". Two subagents hit exactly this today.
Deployment risk itself is low (production runs on the VPS).

**W-2 (MEDIUM)** No git version control on the VPS (`/opt/ses/workspace` is not a git
tree). Deployed code is verified by hash only. Recommendation: track the receiver in git
so deploys are reproducible and reviewable.

## 4. OUTSTANDING ISSUES (nothing below is a live problem today)

1. AUTO write code does not exist — must be built and reviewed before go-live.
2. No signature verification (C-1) — must be added before AUTO.
3. Sticky-field / un-set policy undecided (C-2) — business decision required from Giles.
4. No logrotate (M-4) — trivial, but disk will eventually fill.
5. No monitoring/alerting (M-5) — required before AUTO.
6. Receiver runs as root (H-6).
7. No git tracking on VPS (W-2).
8. Terminal/machine PATH (W-1) — awaiting your approval for a machine-level edit.
9. Backfill: pre-existing INHOUSE-named tasks will never be set (webhooks fire on new
   events only) — decide whether a one-off backfill is wanted.
10. Flowsana cutover must be verified OFF before AUTO to avoid two writers.

## 5. FIXED DURING THIS AUDIT (deployed, hashes verified)

- Secret file permissions 644 -> 600 (and now re-applied atomically on every handshake).
- H-4: `enabled` flag now honoured — rules can be emergency-disabled.
- H-5: 1 MiB body cap -> oversized payloads return 413 (was OOM-as-root risk).
- H-5b: non-object JSON rejected with 400 (was uncaught thread crash).
- L-4: malformed Content-Length returns 400 (was uncaught ValueError).
- rules.json bumped to version 2.

Regression-tested after deploy: oversized -> 413, malformed -> 400, valid -> 200.
Detect mode confirmed: zero rows added to asana_write_log.

## 6. RECOMMENDED ACTIONS BEFORE AUTO MODE

Must-do (all are gates):
1. Implement the write path WITH read-before-write idempotency (no-op if already INHOUSE)
   to prevent the feedback loop.
2. Add signature verification (C-1).
3. Harden the matcher: explicit token (e.g. `[INHOUSE]`), negation guard, pinned spelling
   — agree the convention with Giles.
4. Add audit logging of old value -> new value per write, plus a kill switch, and TEST
   rollback.
5. Return non-2xx on write failure so Asana retries; alert on failures.
6. Monitoring: heartbeat, write success/failure, 429 detection, loop detection,
   and a DAILY DRIFT REPORT comparing name vs field in both directions.
7. Re-check project membership at write time (task may have moved).
8. Verify Flowsana is fully off; decide on backfill.
9. Infra: non-root, logrotate, isolated DB (avoid lock contention on the 970MB shared ops.db).

Staged rollout:
  Phase 1 (1-2 wks) shadow-write to a STAGING project, not PARTS CONTROL.
  Phase 2 (1 wk) narrow live pilot on a small part-number subset; daily reconciliation.
  Phase 3 (2 wks) full live with auto-halt + daily drift report.
  Phase 4 steady state: weekly drift report, documented owner beyond Giles.

## 7. GO / NO-GO

**NO-GO for AUTO mode.**

Reasons: (a) the write code does not exist, so "auto" is currently a no-op that would
create false confidence; (b) the highest-severity control (signature verification) is
absent on a public endpoint; (c) the sticky-flag and substring-matching behaviours would
make the automation LESS reliable than the manual process it replaces — which fails your
own test that automation is only worth doing if it is MORE reliable.

**DETECT mode: GO, keep it running.** It is safe by construction and is already
accumulating the evidence needed to design AUTO correctly.

Conditional GO for AUTO once: the section 6 must-do list is complete, the convention is
agreed with Giles, rollback is proven, and a clean shadow-write phase has passed.
