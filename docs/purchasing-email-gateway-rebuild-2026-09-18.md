# Purchasing Email Gateway — rebuild handoff (logged 2026-09-18)

Source: Giles handoff note (2026-09-18). Written into SES_Workspace\skills\purchasing (in place)
+ data\gateway_rules.json. **Nothing has run on Giles's machine yet.** Offline regression test
passes 10/10; the parse → match → propose chain ran clean against yesterday's real emails with a
stubbed LLM.

## What changed

- **Matcher resolution order.** Resolves *who* first from the 412-supplier directory, then *which
  task* by: SQR → PO → supplier + job → the supplier's own reference on a task → their single open
  order. Never auto-matches closed/arrived/cancelled tasks (invoices excepted). Tasks marked
  "closed out" are blocked. Every row carries its reasons + up to 5 alternatives with status.
  The "IDL matches Midland" substring bug class is gone.
- **RingCentral handled properly.** Contact label + phone from the subject; summary/recap/tasks from
  the body. Supplier calls match like emails; the proposed comment carries the summary +
  follow-ups. Hyperoptic-type calls arrive with Suggested = R and the reason.
- **Comments**: plain text, summary-first, no HTML tag.
- **Supplier quote/order refs** written to the task description so they're trackable.
- **Feedback loop is real.** `05` logs every decision; `learn.py` turns corrections into rules:
  a corrected Asana Link teaches sender/phone → task (90 days); "noise" on an R teaches a noise
  domain; "closed / wrong task" blocks the task; everything else kept as a note. Rules mirrored to
  a new **Rules** tab. Backfilling existing history yielded: 8 domain→task rules, 1 phone→task rule,
  5 blocked tasks, 5 noise domains.
- **New Pending columns**: Triage, Supplier, Confidence, Suggested, Why, Summary, Action Required,
  Alternatives, Feedback. Decision **S** accepts the suggestion (bulk-clear noise).
- **Fetching** is watermark-based (read or unread, weekend-safe); every email archived for
  re-parsing. The **QA portal's save path is retired.**

## Example comment (the previously-pasted case, now)

> "Dispatch note from Applied Automation (Tracey Spear) — 17 Sep 2026 … Action for Giles: none —
> information only … Key facts: delivery 18 Sep · their order JMK32765 · SQR-46913 · PO 21615-1597 …
> Proposed: no field change (task due 23 Sep is later; status already CONFIRMED)"

## To go live

1. `.venv\Scripts\python.exe skills\purchasing\test_gateway.py` — should print ALL PASS.
2. `python skills\purchasing\learn.py --backfill` then `--publish` — rebuilds rules from the live
   Processed tab and creates the Rules tab.
3. Let the hourly cron run; first pass adds the new columns to Pending/Processed. Watch the first
   two batches' Suggested/Why before trusting **S**.
4. `git commit` in SES_Workspace once happy — old versions are in git for revert.

## Honest caveats (both outstanding)

1. **New LLM prompts exercised only with a stub** — real DeepSeek/Gemini output may need a nudge
   (cheap to iterate via `--reparse`).
2. **Matcher assumes `procurement_tasks` has the columns seen in `db_writer.py`** — degrades
   gracefully if `notes` is absent, but description-based matching (the Amazon "order number in the
   description" case) only works once that column exists in the mirror.

## Files (verified on disk 2026-09-18)

- `skills/purchasing/matcher.py` (21 KB), `gateway_common.py` (19 KB), `learn.py` (15 KB),
  `test_gateway.py` (9 KB), `gateway_reviewer.py`
- `data/gateway_rules.json` (11 KB)

---

## ASSESSMENT UPDATE 2026-09-21 (Hermes) — BUILT + DEPLOYED + RUNNING

Verified live (not just handoff claims):
- **Wired into the live pipeline**: `03_proposal_engine.py` imports `matcher.Matcher` + `gateway_common`
  (RuleStore, SupplierDirectory). Hourly cron `75e173a5ac10` (wrapper `email_pipeline.py`:
  sync_procurement_tasks → 01 fetch → 02 parse → 03 propose → 04 sheet writer) runs it.
- **Running clean today 11:01**: Parsed OK 7 / Failed 0; 7 email_documents + 5 line items;
  Triage {supplier_doc 3, noise 3, non_supplier_call 1}, Matched 1;
  Sheet updated 7 new, Suggested {CHECK 2, R 4, A 1}. RingCentral calls flowing; noise domains auto-ignored.
- **New columns live** in the pending queue + `04_sheet_writer`/`05_sheet_approver`.
- **Feedback loop live**: `learn.py` rules + `publish_rules` (Rules tab). `data/gateway_rules.json` v2 =
  8 domain→task, 2 phone→task, 5 blocked, 5 noise (updated 18 Sep 13:30 — post-handoff).
- **`test_gateway.py` prints ALL PASS** (ran 2026-09-21) — go-live step 1 done.
- **QA portal save path retired** (`qa_sync --push` prints retired message).

Caveats:
1. Stub-only LLM prompts → **effectively resolved** (real model ran 7/7 clean today).
2. `procurement_tasks.notes`/`description` column → **STILL OPEN** (neither column exists in the mirror);
   description-based matching (Amazon-style "order number in the description") blocked until added.

Still to confirm before closeout: watch first two batches' Suggested/Why before trusting S;
`git commit` (working tree uncommitted — only 3 commits exist, none for this work);
confirm the Rules tab populated on the live sheet.

Asana: task `1218624364429122` — assessment comment posted, Giles added as follower,
**due 2026-09-25** to re-assess.
