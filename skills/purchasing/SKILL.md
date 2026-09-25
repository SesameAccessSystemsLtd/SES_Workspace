# Purchasing Skill — Email Pipeline with Google Sheets HITL

> **Scope note.** This file covers the EMAIL pipeline only.
> The parts impact + improvement engine, the PO ledger and the validation suite
> are documented in **`PIPELINE_OPS.md`** in this folder. Paths below were
> corrected from `.openclaw` (retired 2026-08) to live locations on 2026-09-11.

5-stage pipeline. Runs on cron (hourly 09–16 Mon–Fri). Human approval gate via Google Sheets before any Asana write.

All scripts use the SES_Workspace venv:
```
C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe
```

Auth: `C:\Users\GilesHugo\.hermes\.env` (MS Graph, Gemini, Elastic Email) + `google_service_account.json` (Google Sheets).

---

## Pipeline Flow (rebuilt 2026-09-18)

```
MS Graph purchasing@ mailbox (Inbox + 1.3 Confirmations + INVOICES TO PROCESS + Archive)
      ↓  window from data/fetch_state.json watermark (read OR unread; weekend-safe)
01_email_fetcher.py     — fetch + PDF text; archive every email to data/email_archive/YYYY-MM/
      ↓
02_llm_parser.py        — readable text → LLM: summary, action for Giles, refs (Sesame PO/SQR/job,
                          supplier quote/order/invoice ref), price, carriage, delivery, issues, noise.
                          RingCentral call notes parsed first (label, phone, summary, recap, tasks).
      ↓
03_proposal_engine.py   — matcher.py (supplier directory + learned rules + closed-task guard)
                          → proposals + plain-text comment + Suggested decision → pending_approval_queue.json
      ↓
04_sheet_writer.py      — Pending tab (header-addressed; new review columns)
      ↓
    [Giles: DECISION = A / M / R / S(=take Suggested); Amendment Notes; Feedback; corrected Asana Link]
      ↓
05_sheet_approver.py    — REQUESTS/ files for SAP  →  learn.py turns the decision into rules
                          → row archived to Processed with what was learned
      ↓
SAP (asana_agent)       — processes REQUESTS/ → writes to Asana (still the ONLY Asana writer)
```

Shared modules: `gateway_common.py` (text cleaning, RingCentral parsing, supplier directory,
rules store, archive), `matcher.py` (task resolution), `sheet_common.py` (header-addressed sheet
access), `learn.py` (feedback → rules, Rules tab, scorecard). Regression test: `test_gateway.py`
(offline; run it after touching any of these).

### How matching works now
1. **Who** — sender domain → `data/suppliers.json` (412 suppliers, all domains); RingCentral calls →
   phone / contact-label rules, else fuzzy label → supplier name. No more guessing a company from a
   word in the domain.
2. **Which task** — SQR → PO (full, then base) → supplier + job number → supplier's own ref found on a
   task → the supplier's single open order → subject reference. Learned `sender→task` / `phone→task`
   rules short-circuit all of this.
3. **Never** a closed / arrived / cancelled task for quotes, confirmations, dispatch notes or updates
   (invoices may land on ARRIVED). Tasks Giles has blocked are skipped. Every result carries
   `Match Reasons` and up to 5 `Alternatives` with status.

### Sheet columns (Pending)
Original A–O unchanged. Added: **Triage** (supplier_doc / supplier_call / non_supplier_call / noise),
**Supplier**, **Confidence** (HIGH / MEDIUM / LOW / NONE), **Suggested** (A / R / CHECK), **Why**,
**Summary**, **Action Required**, **Alternatives**, **Feedback** (free text — teach the gateway),
**Proposed Task GID** (so a corrected Asana Link is detected), **Match Reasons**, **Sender**.
Processed = the same + Outcome, Request ID, Request Status, Final Task GID, **Learned**.
A **Rules** tab mirrors `data/gateway_rules.json`.

### What teaches the gateway (learn.py)
| You do this in the sheet | It learns |
|---|---|
| Paste the right task link into **Asana Link** (with A or M) | that sender / phone → that task (90 days); domain → supplier if new |
| **R** + Feedback saying noise / ignore / tracking / marketing | that domain is noise (never for a directory supplier) |
| Feedback saying closed / wrong task / already arrived / cancelled | the proposed task is blocked from future matches |
| Any other Feedback text | kept as a note in the Rules tab |
Backfill from history: `python learn.py --backfill`. Trend: `python learn.py --scorecard`.

### Local data model (ops.db, written only via db_writer.py) — added 2026-09-18
| Table | One row per | What it gives you |
|---|---|---|
| `email_documents` | inbound email / call | sender, resolved supplier **vs** the task's Vendor field, doc type, summary, action items, Sesame PO/SQR/job refs, the supplier's **quote / order / invoice numbers**, price, carriage, delivery date, lead time, proposed task + confidence + alternatives, then Giles' decision, final task and what was learned |
| `email_line_items` | extracted line | part_ref, description, qty, unit price, line total, linked task/PO/supplier; `sesame_part_no` / `parts_control_gid` reserved for the line→part mapping |
The matcher reads `email_documents` back: an invoice quoting a supplier order number lands on
the task the confirmation with that number went to. `02 --reparse` + `03` over the archive rebuilds
these tables after a prompt change. Mailbox read/unread state is never touched by the pipeline.

### RingCentral call notes
Every "Notes from your call with …" email is parsed: contact label + phone from the subject, the AI
summary / recap / tasks from the body. Supplier calls are matched like any email and the comment
carries the summary and the follow-ups; non-supplier calls (IT, utilities, internal extensions) get
Suggested = R with the reason. Nothing is posted without a sheet decision.

## Scripts

| Script | Layer | Input | Output |
|--------|-------|-------|--------|
| `01_email_fetcher.py` | Ingest | MS Graph API | raw email text + PDF bytes |
| `02_llm_parser.py` | Parse | raw email (text) | `data/parsed_insights.json` + archive |
| `matcher.py` | Match (lib) | refs + supplier + rules + ops.db | MatchResult |
| `03_proposal_engine.py` | Match | parsed_insights.json + ops.db | `data/pending_approval_queue.json` |
| `04_sheet_writer.py` | HITL write | pending_approval_queue.json | Google Sheet rows |
| `05_sheet_approver.py` | HITL read | Google Sheet decisions | `queues/REQUESTS/` files + Processed + feedback log |
| `learn.py` | Learn | feedback records / Processed tab | `data/gateway_rules.json`, Rules tab |
| `invoice_gate.py` | Gate (lib) | invoice text + PO index + live task | GateResult (7 conditions, hard stops) |
| `06_invoice_processor.py` | Intake/file/HITL | archive PDFs + Dropbox drop folder | Dropbox filing, Invoices tab, REQUESTS/ |
| `07_invoice_mover.py` | Nightly | invoice_documents + live Signed OFF | moves PDF to Signed Off, REQUESTS/ |
| `asana_read.py` | Read-only Asana | task gid | live custom fields (GET only) |
| `08_call_sweep.py` | Daily 18:30 | unmapped RingCentral rows on Pending | Processed (CLEARED-EOD), call_context rules |
| `test_gateway.py` | Test | — | offline regression (no LLM / sheet / Asana) |

---

## HITL Gate (Google Sheets)

**Sheet:** `https://docs.google.com/spreadsheets/d/1fvoSKnvH0vdM_aZbo2_seY6AU3SZaXABaxRUKSxWVlo/`

Giles marks each row:
- `A` — Approve: SAP executes as proposed
- `M` — Modify: SAP executes with manual override (Giles edits the row first)
- `R` — Reject: no action taken
- `S` — Same as Suggested: takes the gateway's A or R (a CHECK suggestion needs a real decision)
Sort/filter on **Suggested** and **Confidence**: bulk-S the R's, read the CHECKs.

`05_sheet_approver.py` runs every 30 min (09–17 Mon–Fri). Converts approved/modified rows into REQUESTS/ files. SAP processes REQUESTS/ at its 08:30 heartbeat and on-demand.

No Asana writes occur without a decision in the sheet.

---

## Data Files

| File | Path |
|------|------|
| `parsed_insights.json` | `workspace/skills/purchasing/data/` |
| `pending_approval_queue.json` | `workspace/skills/purchasing/data/` |

---

## Auth

| Service | Credential | Source |
|---------|-----------|--------|
| MS Graph (email) | `MS_GRAPH_CLIENT_ID`, `MS_GRAPH_CLIENT_SECRET`, `MS_GRAPH_TENANT_ID` | `.env` |
| Gemini (LLM parse) | `GEMINI_API_KEY` | `.env` |
| Google Sheets | service account JSON | `C:\Users\GilesHugo\.Hermes\Google_Service_Account.json` |
| Elastic Email (receipts) | `ELASTIC_EMAIL_API_KEY` | `.env` |
| Asana | `ASANA_PERSONAL_ACCESS_TOKEN` | `.env` — SAP only |

---

## Archived Scripts

`archive/04_asana_executer.py`, `archive/05_summary_reporter.py`, `archive/apply_proposals.py` — old direct-write approach, replaced by the Sheets HITL flow.

---

## Rules

- **ALL emails flow through the review queue** (Giles 2026-08-13). `03_proposal_engine.py` must NOT pre-sift by whether PO/SQR/price/date was extracted — the confidence model + portal views filter. Unmatched/no-id emails get a "Manual review — no task/id extracted" proposal. Do NOT reintroduce a `valid` filter that drops emails.
- Unmatched email gate: if no id was extracted, proposal reads "Manual review — ... Subject" (not a dead "PO: None, SQR: None").
- `normalise_ref()` helper added 2026-08-12 to `03_proposal_engine.py` — canonicalises SQR/PO refs (upper, alnum only) so `SQR 46386` (email) matches `SQR-46386` (DB). Do NOT regress to exact `=` string match on SQR.
- **Human comment bullets are clean natural English (Giles 2026-09-02).** Never emit `field GID: …` / `option GID: …` into the review/comment action text — the executor (`execute_requests.parse_actions`) resolves the ORDER Status enum and Price Ex VAT field to their GIDs from env constants when it sees the plain phrases (`Update ORDER Status to 'On Order - Delivery Date Confirmed'`, `Update 'Price Ex VAT' to £N`). GIDs are machine-implementation detail; the review comment must read cleanly for Giles.
- Stage 4 requires `google_service_account.json` — if credentials missing, pipeline stops and reports error. Do not retry automatically.
- Never call Asana write APIs from any script in this folder — route via REQUESTS/ to SAP.
- `db_writer.py` is the sole write gateway for ops.db — no raw sqlite3 calls.

---

## Parser quality + long-term accuracy (2026-08-12, Giles)

**Goal:** take a quote (e.g. Specialist Glass 250434) and match **line-item cost against the specific Asana tasks** — which are **subtasks** of the order, and/or referenced in the **description**, and/or **linked via dependencies**.

- **Current:** 03 only maps whole-email → one parent task + a price-note appended to the description. Line-item → subtask cost mapping is NOT built.
- To improve task identification *and* line-item accuracy, reinstate the parser review/extraction scorecard OpenClaw used: sample N emails, score `02` PO/SQR/price/`03` task-match accuracy against known truth.
- **Action items:**
  1. Extend `03` to map `line_items[]` → matching subtasks (by part_ref / description / dependency) and propose per-line cost updates.
  2. Add parser QA review script + rerun cadence.
  3. Keep SQR/PO normalisation front-and-centre — it's the highest-leverage fix for task finding so far.

---

## Invoice gate (built 2026-09-18) — invoices → Dropbox filing → Asana comment

```
invoice PDF  (email attachment archived by 01  |  dropped at top level of 'Invoices - to be signed off')
      ↓  06_invoice_processor.py run   (hourly, after 03)
seven-condition gate (invoice_gate.py) with LIVE task fields (asana_read.py, GET only)
   1 PO recognised in document text → one task   2 ORDER Status = ARRIVED   3 Signed OFF empty & not completed
   4 Invoice Number empty   5 supplier ≈ task Vendor   6 invoice number fits supplier's learned shape
   7 (Price Ex VAT + Carriage) × 1.2 printed on the document
   hard stops: PROFORMA · DEPOSIT · STATEMENT · CREDIT NOTE · DUPLICATE (supplier+number) · value > £2,000
      ↓
AUTO  → copied/moved to 'AI SIGNED OFF/<PO> - <Vendor> - <description> - <invoice no>.pdf'
CHECK → '2. invoice queries/…'  (+ a SUGGESTED task when a confirmation carried the same supplier ref)
      ↓  row on the sheet's **Invoices** tab (conditions, flags, Dropbox link, proposed comment)
Giles: DECISION A / M / R / S   →  06 approve (every 30 min)  →  REQUESTS/  →  SAP posts the COMMENT + link
      ↓  human sets Signed OFF in Asana
07_invoice_mover.py (nightly) → moves the PDF to '3. INVOICES - Signed Off', queues a follow-up comment with the new link
```

Phase 1 rule: the machine never sets Invoice Number or Signed OFF. Comment + link only. The PDF no longer
needs attaching to the task — the Dropbox link (`https://www.dropbox.com/home/<folder>?preview=<file>`,
works for any signed-in team member, no API token) is the attachment.
`INVOICE_FILING_ENABLED=1` in `.hermes/.env` turns Dropbox filing on; without it 06/07 run dry.
Local record: `invoice_documents` in ops.db (via db_writer) — duplicate guard, multi-invoice running
total, mover state. Trial + risk register: `INVOICE_GATE_TRIAL_2026-09-18.md`.

Cron (proposed): `06 run` hourly at :20 (after 03) · `06 approve` every 30 min with 05 · `07` nightly 01:30.
One-off: `python 06_invoice_processor.py init-sheet` writes the headings on the Invoices and RULES tabs.

### Auto-push (from 2026-09-24) — Giles handles edge cases only
`GATEWAY_AUTO_APPROVE=1`: a Pending row with **Suggested = A** and **Confidence = HIGH**, whose Asana Link
still points at the proposed task and on which nobody has typed a DECISION / Amendment Notes / Feedback,
is pushed by `05` after sitting on Pending for `GATEWAY_AUTO_GRACE_MINUTES` (default 30). Processed shows
DECISION `A (auto)`, Outcome `AUTO-APPROVED`. To stop one row: type anything in its Feedback or DECISION
within the grace window. Cap `GATEWAY_AUTO_MAX_PER_RUN` (default 40) per run.
`INVOICE_AUTO_COMMENT=1`: an Invoices-tab row with Verdict AUTO and **no flags** gets its gate comment +
Dropbox link posted the same way (comment only — Invoice Number / Signed OFF still never set by the machine).
Everything still goes through REQUESTS → SAP, so `GATEWAY_SHADOW=1` still wins and keeps it all dry.
Comment format is stable (heading · Subject · From: name <email> · summary · Action · Key facts · Proposed ·
"Email gateway · …" audit line) — the Asana inbound bot reads these, so change the layout deliberately.

### Division of labour with the Asana AI teammate (Giles 2026-09-24)
Hermes (this pipeline) processes what comes in from OUTSIDE — email, calls, invoices — and writes
**comments and fields only**. The Asana AI teammate owns the task **description** (it compiles the quote
summary there) and drafts the outbound email. So `03` no longer appends to the description
(`DESCRIPTION_NOTES = False`); supplier refs, lead time and price context ride in the comment's
**Key facts** line instead. The **Contact:** line is written for that bot: the person who sent it
(`Contact: Tracey Spear <TraceySpear@…>`); for noreply senders the supplier's email on file; for calls
the name, number and the supplier's email on file. Supplier history / lead-time context is held by the
Asana teammate itself, not pushed from here — comments stay short.

### RingCentral call notes — end-of-day sweep and steering
Calls are parsed into a summary plus a one-line **Key inference** (shown first in the Summary column and in the
comment). Calls with nothing in them (no ref, date, price, follow-up) are triaged as "short call, nothing
actionable". `08_call_sweep.py` runs daily ~18:30 and moves every undecided, unmapped RingCentral row to
Processed (`CLEARED-EOD`). **To steer**, write in the row's Feedback before the sweep: what the call is
about. "ignore / irrelevant / IT / personal…" → that number is dropped in future; anything else → kept, and
your note is handed to the call parser the next time that number rings; a pasted task link → future calls
from that number go to that task. Rules show in the RULES tab as `call_context`.

### Shadow mode (go-live state from 2026-09-18)
`GATEWAY_SHADOW=1` in `.hermes/.env`: every stage runs into the **sheet only**. No ops.db writes, no
Dropbox filing, and REQUESTS files go to `queues/REQUESTS_SHADOW/` (SAP never sees them), so nothing
reaches Asana. Learned rules still update. Decisions made in shadow are stamped "(shadow)" in Processed
At. Remove the flag when trusted; `06` keeps `invoice_gate_shadow_rows.json` so the invoice history can
be replayed into ops.db then.

## QA Portal — RETIRED 2026-09-18

`qa_server.py` / `qa_sync.py --push` no longer save decisions. The portal keyed saves on the sheet
ROW NUMBER while `05` deleted rows every 30 minutes, so a saved decision could land on the wrong
email; it also needed a hand-started server and a manual push. Review and feedback are in the
sheet only. `qa_scorecard.py` / `qa_sync.py` (read-only) remain for history; `learn.py --scorecard`
is the live measure.

## Rules (additions 2026-09-18)

- Comments are plain text, summary-first, no HTML / mention tags (they post as literal text).
- `gateway_common.py`, `matcher.py`, `sheet_common.py`, `learn.py` are the single implementations —
  do not re-implement domain→vendor guessing, header positions or rule handling in a stage script.
- `03` must not propose "Delivery Date CONFIRMED" on an arrived/closed task, must not overwrite an
  existing Price Ex VAT, and must record supplier quote/order refs on the description.
- Run `test_gateway.py` before and after changes. Raw emails live in `data/email_archive/` —
  `02_llm_parser.py --reparse YYYY-MM` re-parses a month into a side file after a prompt change.
