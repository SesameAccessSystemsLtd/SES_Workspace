# Microsoft Graph Outage — Impact & Recovery Assessment

**Date:** 2026-11-13 10:46 UTC
**Prepared by:** Hermes
**Question asked:** Will these cron failures recover on their own when Microsoft returns, or does action need to be taken?

**Answer: no action required. Recovery is automatic and lossless.** Evidence below.

---

## 1. What is actually broken

Tested live against the real mailboxes, using the pipeline's own OAuth path:

| Probe | Result | Reading |
|---|---|---|
| `client_credentials` token acquisition | **OK** — token issued, 2,220 chars | Our app registration, secret and consent are **healthy** |
| `GET /v1.0/` (Graph root) | **200 OK** | Graph service itself is up |
| `GET /users/purchasing@…/mailFolders` | **502 Bad Gateway** | ❌ mail plane down |
| `GET /users/purchasing@…/messages` | **502 Bad Gateway** | ❌ mail plane down |
| `GET /users/rc-notes@…/messages` | **502 Bad Gateway** | ❌ mail plane down |

**Conclusion:** this is a **Microsoft Exchange Online mail-service outage**, not a credentials, permissions, or configuration problem on our side. Authentication works perfectly; the mailbox endpoints then return 502 with an empty `UnknownError` body and a Microsoft-side `request-id`. Both mailboxes (purchasing + rc-notes) are affected identically.

That matters because it means **there is nothing to fix here.** No token rotation, no app-registration change, no config edit. It recovers when Microsoft recovers.

---

## 2. Blast radius — exactly 3 jobs

| Job | ID | Cadence | Status |
|---|---|---|---|
| Purchasing Email Parser | `75e173a5ac10` | hourly 09–16 wkdays | ❌ failing since 13 Nov 09:02 |
| RingCentral Email Notes Sync | `fcdffdd011f5` | hourly 08–18 wkdays | ❌ failing since 13 Nov 08:00 |
| Chase — Shared Graph Fetch | `e5afe528769c` | hourly | ❌ failing since 12 Nov 19:00 |

**All other 45 cron jobs are unaffected.** Verified individually:

- `Email Approval Sheet Processor` — silent, **not** Graph-dependent (this was the predictable worry; it is clean)
- `Chase — 07:00 Morning Report` — silent ✓ (reads the local SQLite cache, does not call Graph itself)
- `Weekly Overdue Order Chase`, `Weekly Email Gateway Reviewer` — silent ✓ (weekly, next run not yet due)
- All Ordering / Invoice / OS Portal / Asana jobs — unaffected, none touch Microsoft

**Onset:** last good Graph run was **12 Nov 16:00–17:50**. First failure 12 Nov 19:00. Nothing was corrupted in between — `email_touchpoints` last row is 12 Nov 17:50, consistent with a clean stop.

---

## 3. Why recovery is automatic — the watermark design

This is the important part, and it is genuinely well-built. **We got lucky on design, not on luck.**

Three separate mechanisms protect the missed window:

**a) Watermarks only advance on success.**
`data/fetch_state.json` is frozen at `2026-11-12T16:02:10Z`. `chase_fetch.py` L404–405 sets `last_success_at_utc` *after* the network calls complete. Because every run since has thrown, **the watermark has never advanced.** On the first successful run it will fetch everything since 12 Nov 16:02 — the entire missed window, not just the last 90 minutes.

**b) Total failure aborts before any write.**
`01_email_fetcher.py` L428–438: if nothing is retrievable anywhere, it raises and `sys.exit(3)` **before** `save_processed_ids()` or the state write. So an outage cannot poison the ledger with a false "no new mail" result. A half-dead run also refuses to advance the watermark (L489–490: `Watermark NOT advanced — folder failures`).

**c) Dedup makes overlap harmless.**
`processed_email_ids.json` + `internetMessageId` dedup means re-fetching a wider window cannot double-process. Worst case it re-reads messages it has already seen and skips them.

**Net effect: the missed emails will arrive on the first good run, in full, with no duplicates and no manual backfill.**

---

## 4. The one real risk — and it is not the outage

**A long outage makes the recovery run large.** If Microsoft stays down for days, the catch-up run must pull the whole window in one go:

- The fetcher caps the inbox page at `$top=50` per request and pages, so volume is handled — but a multi-day backlog is a longer run.
- There is a **300s script timeout**. The parser already timed out once at 09:05 today on a *small* run.
- **Risk:** if the catch-up is big enough to exceed 300s, it fails → watermark stays frozen → it tries again next hour with an even bigger window → **death spiral**.

At ~2–8 purchasing emails/day this is unlikely to bite before roughly 5–7 days of outage. However, if the outage runs into next week, the safest manual action is a **one-off supervised backfill**, not waiting for the hourly job:

```
cd C:/Users/GilesHugo/SES_Workspace
.venv/Scripts/python.exe skills/purchasing/chase_fetch.py --days 7
```

This is only needed if the outage persists beyond ~3–4 days. **Not required today.**

---

## 5. What you will see while it is down

The failure philosophy is working as intended — you get a real error, not silence:

```
Status: script failed
Script exited with code 3
[skills\purchasing\01_email_fetcher.py] FAILED (exit 3)
  Inbox fetch error: 502 Server Error: Bad Gateway for url: https://graph.microsoft.com/...
FATAL [fetch]: ...
```

You will get this **hourly** from 3 jobs → roughly **27 alerts per working day**. That is excessive noise for a known, self-healing vendor outage, and it risks you tuning out a later real failure.

**Optional:** pause the 3 jobs until Microsoft is back, then resume. Recovery is identical because the watermark is frozen either way — pausing loses nothing. I have **not** paused them, since you asked whether action is needed (it isn't) and pausing would suppress the evidence that recovery worked.

---

## 6. Recommendation

| # | Action | Needed? |
|---|---|---|
| 1 | Fix credentials / token / app registration | **No** — auth verified healthy |
| 2 | Manual backfill | **No** — unless outage exceeds ~4 days |
| 3 | Pause the 3 jobs to cut alert noise | **Optional** — your call |
| 4 | Monitor for first successful run, verify watermark advances | **Yes** — cheap confirmation |
| 5 | Watch the first catch-up run for a 300s timeout | **Yes** — the only real failure mode |

**Bottom line: wait. Do nothing. It will recover itself and lose nothing.** The only thing worth doing is glancing at the first successful run after Microsoft returns, to confirm the watermark advanced and the catch-up stayed under 300s.

---

## 7. Jibble request — cancelled (done)

Cancelled at your instruction, **unexecuted**:

- `2026-10-02T16-30-hermes-jibble-geofence-clock-in-risk.md` → moved to `queues/REQUESTS/_cancelled/` with a cancellation record (status `CANCELLED`, 41 days old)
- **Never dispatched** — no `create_subtask` ever ran, no completed twin exists, so **nothing to undo in Asana**. Pure queue retirement.
- Reason recorded: stale Jibble API generation + login; new trial in place; sync pipeline running separately under `data/Sesame_Lift_Locations/jibble_import/`

**Also cleared:** `2026-11-03T14-09-stock_audit-stock_count_writeback-WB-20261103-140945.json` was a *completed* write-back result log (50 lines written, 6 attachments) sitting in the live queue. Moved to `REQUESTS_COMPLETE/`.

**The live `queues/REQUESTS/` queue is now empty** — nothing pending, so the nightly maintenance will stop re-escalating the Jibble item after 41 days of escalation.

---

## Appendix — jobs verified NOT affected

Ordering (4) · Invoice (9) · OS Portal (4) · Asana mirror/sync (2) · Nightly maintenance/memory/audit (3) · Improvement engines (2) · Steel/BOM/drawing (3) · Stock count intake · Richard daily · Navigator (disabled) · Telegram commander · Weekly graph rebuild · Token dashboard · Related fluid watch · Gateway watchdog · OOO digest · Impact engine.

None of these call `graph.microsoft.com` directly or via a shared module.
