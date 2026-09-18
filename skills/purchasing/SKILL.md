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

## Pipeline Flow

```
MS Graph inbox
      ↓
01_email_fetcher.py     — fetch unread emails + PDF attachments
      ↓
02_llm_parser.py        — Gemini extracts PO numbers, supplier, dates → parsed_insights.json
      ↓
03_proposal_engine.py   — match against ops.db → pending_approval_queue.json
      ↓
04_sheet_writer.py      — write proposals to Google Sheet (HITL gate)
      ↓
    [Giles reviews rows in sheet — marks A / M / R]
      ↓
05_sheet_approver.py    — reads decisions → creates REQUESTS/ files for SAP
      ↓
SAP (asana_agent)       — processes REQUESTS/ → writes to Asana
```

---

## Scripts

| Script | Layer | Input | Output |
|--------|-------|-------|--------|
| `01_email_fetcher.py` | Ingest | MS Graph API | raw email text + PDF bytes |
| `02_llm_parser.py` | Parse | raw email | `data/parsed_insights.json` |
| `03_proposal_engine.py` | Match | parsed_insights.json + ops.db | `data/pending_approval_queue.json` |
| `04_sheet_writer.py` | HITL write | pending_approval_queue.json | Google Sheet rows |
| `05_sheet_approver.py` | HITL read | Google Sheet decisions | `workspace/REQUESTS/` files |

---

## HITL Gate (Google Sheets)

**Sheet:** `https://docs.google.com/spreadsheets/d/1fvoSKnvH0vdM_aZbo2_seY6AU3SZaXABaxRUKSxWVlo/`

Giles marks each row:
- `A` — Approve: SAP executes as proposed
- `M` — Modify: SAP executes with manual override (Giles edits the row first)
- `R` — Reject: no action taken

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

## QA Portal (built 2026-08-13)

Interactive **Live QA Action Portal** for the parser decisions + notes, replacing the static report for day-to-day action.

**Files:**
- `qa_scorecard.py` — weekly static HTML report (`data/qa/latest.html` + dated copies) — precision/recall/F1 + error taxonomy + surfaced FP/FN from `Processed` tab.
- `qa_sync.py` — gateway: `--portal` generates interactive `data/qa/qa_portal.html` (inline live+history data, confidence-coloured); `--push` writes saved actions to the Google Sheet; default writes `qa_live.json` + `qa_history.json`.
- `qa_server.py` — tiny localhost HTTP server (`127.0.0.1:8611`) to serve the portal + accept `POST /save` → `data/qa/qa_actions.json`.
- `qa_portal_template.html` — source template the sync embeds data into.

**Confidence model** (in `qa_sync.py`): HIGH = task matched; MEDIUM = NOT MATCHED on quote/order_confirmation (actionable, needs human pick); LOW = NOT MATCHED on dispatch/invoice/update/other (noise). DONE = decision made.

**Key constraint — browser can't write files over file:///**: notes/decisions require the small server (`python qa_server.py`). A static file:// open of `qa_portal.html` can render/filter but Save falls back to a download. Nav tile intentionally points at the server URL. Start the server, pick a row, A/M/R + note, Save → `qa_actions.json` → `qa_sync.py --push` → sheet (DECISION col K, notes col L, Processed At col M).

**Ground truth:** `Processed` tab cols E=Document Type, G=Matched Task, K=DECISION (A/M/R).
