# SKILLS_REGISTRY.md - Custom Skills Index

Central index for all custom skills available to the agent ecosystem.
When a new skill is created, add it here immediately.

## Core Skills (Bundled with OpenClaw)

- **weather:** Get weather forecasts.
- **gemini:** One-shot access to the Gemini model via its CLI.

---

## Custom Skills (`/workspace/skills/`)

- **skill: `legacy_bom_pack_allocation`**
  - **Location:** `C:\Users\GilesHugo\SES_Workspace\skills\legacy_bom_pack_allocation\`
  - **Description:** Allocate the correct build-pack GID to already-imported `parts_control` parts from a design-team Legacy BOM folder, replacing free-text pack links with verified GIDs. **Pack allocation, NOT BOM import** (that is `bom_pipeline`). Filename = pack; targets the Build Schedule pack, excludes the `.DRAWING` variant and design artifacts (`_MIR`, in-house welds, fasteners). `--scan` is read-only; `--commit` dual-writes ops.db (`build_assembly_pack_gid` via `db_writer`) + Asana `Build Packs reference` (via `asana_batch_updater`) from a reviewed stage file. HITL before any write.
  - **Entry points:** `legacy_bom_pack_ingest.py --parts-folder <folder> --scan | --commit --stage-file <scan.json>`; `legacy_bom_handover_watcher.py [--dry-run]` (Asana-only detect + Telegram notify, deduped via `legacy_bom_handover_log`).
  - **Interim scope:** watcher runs on the asana_agent heartbeat (Asana only, no Dropbox token); Dropbox link resolution + the ingest run in a Cowork session (has the Dropbox connector). Future: give the heartbeat a read-only Dropbox token to run end-to-end.
  - **Auth:** `ASANA_PERSONAL_ACCESS_TOKEN` from `.env`/`ASANAconfig.json`; DB via `PATH_CONFIG.yaml` (`db.ops`) or `OPENCLAW_OPS_DB_PATH`.
  - **See also:** `_drafts/LegacyBOM-Handover-Automation-Design.md`, `plans/PLAN-2026-08-11-legacy-bom-pack-allocation.md`, skill `EDGE_CASES.md`.

- **skill: `bom_pipeline`**
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\skills\bom_pipeline\`
  - **Description:** BOM-to-Asana import pipeline. Stages 1–4 consolidate, match, stock-check, and stage a BOM for HITL review. Stage 5 (--commit) creates task hierarchy in Asana. ALWAYS stop after Stage 4, present the Excel to Giles, wait for approval before committing. Entry point: `run_mrp_pipeline.py`. See `SKILL.md` for exact command templates.
  - **Entry point:** `& "C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\bom_pipeline\run_mrp_pipeline.py"`
  - **CRITICAL — do NOT use coding-agent or spawn Claude Code for this skill.** Run the pipeline script directly via shell. Spawning Claude Code introduces unreliable session tracking.
  - **Specialist Agents:** main (Commander Claw) — runs directly via shell. Asana writes via SAP only.
  - **Auth:** `ASANA_PERSONAL_ACCESS_TOKEN` from `.env` or `ASANAconfig.json` in skill folder.

- **skill: `email_sender`**
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\skills\email_sender\`
  - **Description:** Send a notification email directly to Giles from any agent. Single-message send only — not for bulk campaigns. Uses Elastic Email API. Subject is automatically prefixed with `[AgentName]`. All sends logged to `workspace/db/email_send_log.jsonl`.
  - **Auth:** `EMAIL_SERVICE_Elasticemail_API_KEY` (already set) + `AGENT_NOTIFY_EMAIL` (set to Giles's inbox in `.env`)
  - **Script:** `scripts/notify.py --subject "..." --body "..." --from-agent "AgentName"`
  - **Specialist Agents:** main (Commander Claw), personal_planner (Navigator), ops_agent (SAO), asana_agent (SAP)
  - **NOT for:** sesame_marketing bulk campaigns — use `elastic_email` for those

- **skill: `rc_transcripts`**
  - **Location:** `workspace/skills/rc_transcripts/`
  - **Description:** Reads shared mailbox `rc-notes@sesameaccess.onmicrosoft.com` via Microsoft Graph API. Parses RingCentral AI summary emails into structured records: contact name, summary, action items, key topics. Outputs per-call .txt files and an append-only JSON log. Reuses existing Graph app credentials — no new Azure AD app needed.
  - **Entry point:** `& "C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\rc_transcripts\rc_transcript_fetcher.py"`
  - **Auth:** `Purchaisng_secret_ID` + `purchasing_Email_Api_Key` from `.env` (same as email_reader)
  - **Outputs:** `workspace/data/rc_transcripts/*.txt`, `workspace/data/rc_transcripts_raw.json`
  - **Specialist Agents:** scraper (SAD) — scheduled runner; ops_agent (SAO) — CRM/Asana mapping

- **skill: `email_reader`**
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\skills\email_reader\`
  - **Description:** Read-only access to the purchasing@sesameaccess.com inbox via Microsoft Graph API (Azure AD OAuth 2.0). Methods: `read_emails()` (bodyPreview), `read_flagged_emails()` (full body), `read_emails_full_body()` (full body + hasAttachments flag), `get_email_attachments(message_id)` (fetch attachment bytes for PDF parsing).
  - **Auth:** Azure AD client credentials — config at `.openclaw/config.json`
  - **Specialist Agents:** scraper_agent, ops_agent, main

- **skill: `morning_reporter`**
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\skills\morning_reporter\`
  - **Description:** Automated morning report using the email_reader skill. Summarises unread/flagged purchasing emails.
  - **Depends on:** email_reader
  - **Specialist Agents:** main

- **skill: `hubspot-crm`**
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\hubspot-crm\`
  - **Description:** Interact with HubSpot CRM API. Fetch activity data, manage contacts, analyse marketing performance.
  - **Auth:** `HUBSPOT_API_KEY` environment variable
  - **Specialist Agents:** scraper_agent, sesame_marketing

- **skill: `sql_db`**
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\skills\sql_db\`
  - **Description:** Read and write structured data to the operational SQLite database. Supports SELECT queries, INSERT, UPDATE, and UPSERT. Used for cross-agent queryable history, trend analysis, and cross-referencing datasets.
  - **Auth:** None — local file-based SQLite
  - **DB path:** `workspace/db/ops.db` — **this is the ONLY active database**. `workspace/db/openclaw.db` is RETIRED (2026-05-08) — do NOT read from or write to it.
  - **Scripts:** `db_init.py` (setup), `db_query.py` (read), `db_write.py` (write/audit). All writes via `skills/purchasing/db_writer.py` — never raw sqlite3 INSERT/UPDATE outside that module.
  - **Specialist Agents:** scraper_agent (write), sesame_marketing (write campaigns), asana_agent (write task states), ops_agent (write purchase_orders, bom_requirements, supplier_deliveries, email_po_matches), main (read-only)
  - **Access policy:** See `workspace/skills/sql_db/SKILL.md`

- **skill: `pdf_extractor`**
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\skills\pdf_extractor\`
  - **Description:** Parse supplier PDF documents (delivery notes, order acknowledgements, despatch notes) to extract PO numbers, order references, supplier details, and line items. Returns structured JSON + raw_text fallback.
  - **Requires:** `pdfplumber` (`pip install pdfplumber`)
  - **Script:** `pdf_extractor.py` — accepts file path or base64-encoded bytes (for Graph API attachments)
  - **Specialist Agents:** ops_agent (exclusively)

- **skill: `asana`**
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\skills\asana\`
  - **Purpose:** Live read access to Asana projects and tasks via REST API. Commands: `task_counts`, `list_tasks`, `get_project`, `get_task`, `search_tasks`.
  - **Auth:** `ASANA_PERSONAL_ACCESS_TOKEN` environment variable (set in `.openclaw/.env`)
  - **Scripts:** `scripts/asana_query.py`
  - **Python:** `C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe`
  - **Specialist agents:** asana_agent (primary), main (read-only queries)
  - **Notes:** Read-only queries via `asana_query.py`. For placeholder renaming use `rename_placeholder.py` (see below).

- **skill: `asana` — rename_placeholder** _(write operation)_
  - **Script:** `scripts/rename_placeholder.py`
  - **Purpose:** Replace `[PROJECT NAME]` placeholder (any case variant) in task names across an entire project, recursively including all subtask depths. Dry run by default; `--commit` to apply. `--json` for structured output.
  - **Usage:** `python rename_placeholder.py --project-gid <GID> --replacement "1581 Admiralty House" [--commit] [--json]`
  - **Prerequisite:** Know the project GID (use `list_projects.py` in Asana Scripts TESTING folder to look up by name)
  - **Specialist agents:** asana_agent (SAP), main (SESAL)

- **skill: `asana-scripts`**
  - **Type:** Shell execution (Python)
  - **Location:** `C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\OneDrive Documents\LAB. SESAME\Asana Scripts\`
  - **Purpose:** Complex BOM processing, stock control sync, build schedule analysis from CSV exports.
  - **Auth:** `ASANA_PERSONAL_ACCESS_TOKEN` env var (also in `ASANAconfig.json` in scripts root)
  - **Specialist agents:** asana_agent
  - **Notes:** Read-only scripts may run with proposal; write-back scripts require explicit confirmation.

- **agent: `personal_planner`** _(Navigator — personal task & project manager)_
  - **Location:** `c:\Users\GilesHugo\.openclaw\agents\personal_planner\`
  - **Description:** Personal planning companion for Giles. Tracks projects, tasks, and progress. Reads/writes PROJECTS.md, TASKS.md, LOG.md, MEMORY.md within its own agent directory only. No shared workspace access.
  - **Tools:** File read/write only — no shell, no network, no database
  - **Used from:** IDE / direct session
  - **Specialist Agents:** personal_planner only (private, isolated)

- **skill: `lead_generator`**
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\skills\lead_generator\`
  - **Description:** Generate, enrich, export, and CRM-sync structured B2B lead lists for outbound marketing campaigns. Phase 1: synthetic sample data. Phase 2: Apollo.io enrichment. Phase 3: LinkedIn Sales Navigator. Phase 4: HubSpot CRM upsert. Outputs to OneDrive export path and `leads` SQLite table.
  - **Auth:** Phase 1 — none. Phase 2 — `APOLLO_API_KEY`. Phase 3 — `LINKEDIN_*`. Phase 4 — `HUBSPOT_API_KEY` (already in `.openclaw/.env`).
  - **Scripts:** `lead_generator.py` (source), `lead_enricher.py` (Apollo enrichment), `lead_exporter.py` (CSV/JSON export), `lead_hubspot_sync.py` (CRM sync)
  - **Output dataset:** `leads` (DATAMAP.md key)
  - **Specialist Agents:** scraper_agent (WRITE), sesame_marketing (READ via DATAMAP.md), main (READ via sql_db)
  - **Access policy:** See `workspace/skills/lead_generator/SKILL.md`

- **skill: `dev`**
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\skills\dev\`
  - **Description:** Governance protocol for all code development and build tasks. Defines three gates: PLAN (write plan, get approval), BUILD (hygiene rules), VALIDATE (post-build checklist). Includes optional one-shot QA subagent spawn for complex builds.
  - **Scripts:** `scripts/nightly_audit.py` — nightly codebase health check (skills drift, secrets, embedding opps, stale plans). Run at 23:00 by Forge. Output: `workspace/memory/nightly/YYYY-MM-DD.md`.
  - **Specialist Agents:** dev_agent (Forge) — primary owner. main (Commander Claw) — delegates all builds to Forge.

- **agent: `dev_agent`** _(Forge — Dev Specialist)_
  - **Location:** `c:\Users\GilesHugo\.openclaw\agents\dev_agent\`
  - **Description:** Dedicated development agent. Receives build tasks delegated by Commander Claw. Owns PLAN → BUILD → VALIDATE protocol. Runs nightly codebase audit at 23:00. Flags drift, bloat, hardcoded secrets, and embedding opportunities.
  - **Model:** gemini-2.5-pro → claude-sonnet-4-6
  - **Tools:** Full file read/write, shell execution (venv Python), email_sender for notifications
  - **Nightly audit:** `workspace/skills/dev/scripts/nightly_audit.py`
  - **Spawned by:** Commander Claw (main orchestrator)

- **skill: `elastic_email`**
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\skills\elastic_email\`
  - **Description:** Send individual or bulk marketing emails via Elastic Email v4 API. Supports `{{merge_tag}}` substitution, dry-run preview, campaign stats retrieval, and JSONL send logging. All sends must be pre-approved and pre-logged in `campaigns` table.
  - **Auth:** `ELASTIC_EMAIL_API_KEY` + `ELASTIC_EMAIL_FROM` env vars in `.openclaw/.env`
  - **Scripts:** `scripts/elastic_email_send.py`
  - **Specialist Agents:** sesame_marketing (FULL), main (READ stats only)
  - **Mandatory pre-send checklist:** See `SKILL.md`

- **skill: `drawing_pipeline`**
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\skills\drawing_pipeline\`
  - **Description:** For a given engineering project (identified by job prefix, e.g. `1575`), downloads assembly drawing PDFs from Asana Drawing Control, extracts part BOMs via pdfplumber, maps extracted parts to Parts Control tasks via `ops.db`, and populates the `Build Packs reference` custom field on each Asana task. Handles 9 part-number edge cases (see `PN_EDGE_CASES.md`). Multi-pack parts flagged with `*` prefix.
  - **Auth:** `ASANA_PERSONAL_ACCESS_TOKEN` env var (Asana write via SAP only)
  - **Entry point:** `scripts/run_drawing_pipeline.py --job-prefix <PREFIX> [--dry-run] [--stop-after N]`
  - **Run protocol:** See `SKILL.md` — always dry-run stages 1+2 first, then commit, then Asana write.
  - **Specialist Agents:** `asana_agent` (SAP) — sole executor of stage 4 (Asana write). `ops_agent` may run stages 1–3.
  - **DB:** `ops.db` — reads `parts_control`, writes `build_assembly_packs` + `parts_control.build_assembly_pack_gid`
  - **Confirmed run:** Jenners 1575 — 418 parts mapped, 373 Asana tasks updated (2026-05-08)

- **skill: `email_approval_dashboard`**
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\skills\purchasing\`
  - **Description:** Human-in-the-loop approval gate for purchasing email processing. Stage 4 (`04_sheet_writer.py`) writes proposed actions to a Google Sheet for Giles to review. Stage 5 (`05_sheet_approver.py`) reads his decisions (A/M/R) and creates REQUESTS/ files for SAP to execute. No direct Asana writes.
  - **Google Sheet:** `https://docs.google.com/spreadsheets/d/1fvoSKnvH0vdM_aZbo2_seY6AU3SZaXABaxRUKSxWVlo/`
  - **Auth:** Google service account JSON at `C:\Users\GilesHugo\.openclaw\google_service_account.json` — see `GOOGLE_SHEETS_SETUP.md`
  - **Scripts:** `04_sheet_writer.py` (writes queue to sheet), `05_sheet_approver.py` (reads approvals, creates REQUESTS/)
  - **Cron:** Sheet writer runs after pipeline (9–16 Mon–Fri). Approver runs every 30 min (9–17 Mon–Fri).
  - **Specialist Agents:** ops_agent (runs writer), main (runs approver)
  - **Archived:** `04_asana_executer.py`, `05_summary_reporter.py`, `manage_pending_approvals.py` → `archive/`

- **skill: `daily_brief`**
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\skills\daily_brief\`
  - **Description:** Navigator morning brief. Fetches all Asana tasks assigned to Giles, triages with Claude Haiku (URGENT/ACTION_MINE/DECISION_NEEDED/DELEGATABLE), runs Eisenhower Priority Audit on personal task project (GID `369446862164514`), generates HTML email, creates REQUESTS/ file for Eisenhower Asana task.
  - **Script:** `daily_brief.py` — run directly, no Doppler needed (reads from `.openclaw/.env`). `--dry-run` saves HTML preview without sending.
  - **Run command:** `& "C:\Users\GilesHugo\.openclaw\venv\Scripts\python.exe" "C:\Users\GilesHugo\.openclaw\workspace\skills\daily_brief\daily_brief.py"`
  - **Cron:** 07:00 daily (all days). Job ID: `d2e7f4a1-3c9b-4f8e-a0d6-5b2c8e1f7a93`
  - **Auth:** `ASANA_PERSONAL_ACCESS_TOKEN` + `ANTHROPIC_API_KEY` + `email_sender` skill
  - **Outputs:** HTML email to Giles + REQUESTS/ file for Eisenhower Asana task
  - **Specialist Agents:** main (runs cron), personal_planner (Navigator — owns skill)

- **skill: `azure_mail`** _(in development)_
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\skills\azure_mail\`
  - **Description:** Connects to Microsoft Graph to read emails and manage inbox items.
  - **Specialist Agent:** TBD — not yet assigned

- **skill: `building_conservation_scraper`** _(placeholder — not yet active)_
  - **Location:** `c:\Users\GilesHugo\.openclaw\workspace\skills\building_conservation_scraper\`
  - **Description:** Scrapes building conservation directories for marketing leads. Script exists but not yet wired to any agent or DATAMAP dataset. Development pending.
  - **Specialist Agent:** scraper (SAD) — when activated
  - **Status:** Script present, no active runs, no DATAMAP entry. Do not call until development is resumed.
