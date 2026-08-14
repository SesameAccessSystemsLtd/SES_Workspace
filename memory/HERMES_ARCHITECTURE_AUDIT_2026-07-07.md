# Hermes Component Architecture Audit
**Date:** 2026-07-07 · **Reviewer role:** systems architect / inventory-control / purchasing-systems / software-audit
**Scope reviewed:** `C:\Users\GilesHugo\.Hermes`, `C:\Users\GilesHugo\SES_Workspace`, `ops.db`, `crm.db`, and the four uploaded design/snapshot artefacts.

> **How to read this document.** Every claim is tagged with the evidence that supports it. Where the evidence is thin or contradictory, that is stated explicitly rather than resolved by assumption. The single most important finding contradicts the framing of the brief, so it is stated first.

---

## Executive Summary

**Hermes is not a purchasing or inventory system. Hermes is a general-purpose AI agent *harness*.** The directory `C:\Users\GilesHugo\.Hermes` is an unmodified checkout of the open-source **NousResearch/hermes-agent** project (`git remote`: `https://github.com/NousResearch/hermes-agent.git`; recent commits are upstream Nous commits about Telegram/Teams/desktop, nothing domain-specific). A full-text search of `.Hermes` for `sesame`, `golden_index`, `parts_control`, `bom` returned **zero** matches. Hermes supplies the runtime that other things run *inside*: a cron scheduler, a Telegram/Discord/Slack gateway, session memory, skill execution, and subagent delegation.

The actual purchasing / inventory / BOM / stock-control logic — the thing the brief describes — lives entirely in **`SES_Workspace/skills/`** as a collection of Python "skills," an operational SQLite database (`ops.db`), a file-based `REQUESTS/` queue, and a set of Asana projects. This body of automation was previously hosted by a different, older harness called **OpenClaw** (`C:\Users\GilesHugo\.openclaw`), and is mid-migration onto Hermes.

So the correct mental model is three layers:

1. **Hermes** — the always-on orchestration/execution harness (scheduler, gateway, agent core). *Generic. Swappable. Owns no business data.*
2. **SES automation skills** (in `SES_Workspace/skills`) — the domain logic: BOM import, stock sync, purchasing email parsing, ordering, edge-case logging. *This is the "system" the brief is really asking about.*
3. **Systems of record** — **Asana** (stock quantities, tasks, part master fields), **MS Graph / Office 365** (email), **Google Sheets** (human approval gate), **Dropbox** (source BOM files, read-only), and the **Golden Index CSV** (part-number master). `ops.db` is a **mirror/cache of Asana**, not a source of truth.

**Intended direction:** consolidate everything under Hermes, retire OpenClaw, and deploy Hermes to a Linux VPS so the cron/gateway layer runs unattended and can service Asana webhooks. The plumbing for this is real and partly working, but the migration is **materially incomplete and mislabelled as complete**, and several inventory-correctness and durability gaps remain open.

**Top risks (detail in the Risks section):** the sole DB write-gateway still points at the retired OpenClaw database path; `ops.db` has bloated to **437 MB** from an unrotated audit log; stock is Asana-authoritative with only an eventually-consistent mirror; there is **no automated reorder logic** despite the data model implying it; and the "sole Asana writer" (SAP) still lives in the retiring harness.

---

## 1. What Hermes Is

### 1.1 Current purpose (evidence-based)
Hermes is the **primary orchestration harness**: cron scheduler, always-on Telegram gateway, and agent runtime.

- `SES_Workspace/_copilot/SYSTEM_MANIFEST.yaml → harnesses.hermes`: *"Primary orchestration — cron scheduler, Telegram gateway, VPS always-on … status: active … environment: local_windows (migrate to vps_linux) … notes: Migration from OpenClaw in progress."*
- `.Hermes/AGENTS.md`: *"Hermes is a personal AI agent that runs the same agent core across a CLI, a messaging gateway … TUI, and an Electron desktop app … extended primarily through plugins and skills, not by growing the core."*
- `.Hermes/README.md` markets it as a self-improving agent runnable on "a $5 VPS," with a built-in cron scheduler and ~20 messaging backends.
- Its configuration and cron wrapper scripts live **outside** the workspace at `~/AppData/Local/hermes/` (nightly note 2026-07-07 created `~/AppData/Local/hermes/scripts/nightly_maintenance.py`).

### 1.2 Intended purpose
To be the **single always-on host** for all Sesame automations after OpenClaw is shut down, and ultimately to run on a Linux VPS servicing **Asana webhooks** (the stated end goal in the project brief). `SYSTEM_MANIFEST` lists Hermes as owning the cron jobs; OpenClaw is marked `status: retiring` — *"Shut down when all crons validated."*

### 1.3 What Hermes actually is — classification
Against the brief's menu, Hermes is a **workflow coordinator / execution harness / integration host**. It is explicitly **not**:
- a purchasing agent (that is the `purchasing` skill + SAP),
- an inventory controller (that is Asana + `stock_inventory` sync),
- a procurement orchestrator (that is the `ordering` skill set),
- a warehouse system (no such system exists; Asana projects stand in for it).

Hermes contributes scheduling, messaging, memory and agent execution. Every business behaviour is contributed by a **skill** it runs. This is by design — `AGENTS.md`: *"The core is a narrow waist; capability lives at the edges."*

### 1.4 Responsibilities that may belong elsewhere
Because the brief conflates "Hermes" with "the inventory system," there is a real risk of loading domain responsibilities onto the harness. **Data ownership, inventory correctness, and procurement policy must not live in Hermes.** They belong in the skills and in the systems of record. Hermes should own *only* scheduling, delivery, secrets injection, and process supervision.

---

## 2. Data Ownership

| Data | System of record | Where mirrored | Owner of writes |
|---|---|---|---|
| Stock quantities | **Asana** (KANBAN `632603478911321`, Consumables `1200068443534209`) | `ops.db.stock_inventory` (116 rows) | SAP → Asana; sync reflects back |
| Tasks / procurement / build packs | **Asana** | `ops.db` mirror tables | SAP (sole writer) |
| Part-number master (PN ↔ KAN ↔ Asana) | **Golden Index CSV** (`Merged_Golden_Index_Rev4.csv`) | `ops.db.golden_index` (**0 rows — never seeded**) | manual + `update_golden_index.py` |
| Inbound purchasing email | **MS Graph / O365** | `data/raw_purchasing_emails.json` | read-only ingest |
| Human approval decisions | **Google Sheet** (HITL) | `data/pending_approval_queue.json` | Giles marks A/M/R |
| Source BOM files | **Dropbox** (read-only) | — | never written |
| CRM / leads | `crm.db` (42 companies, 42 leads) | — | SAD / SAM |

**The authoritative statement of ownership is in `sync_stock_inventory.py`:**
> *"Stock quantities are the authoritative numbers from Asana. `stock_inventory` is a READ-ONLY mirror for agents — stock qty changes must go to Asana first via SAP; this sync then reflects them back."*

**Conclusions.**
- **Asana is the operational source of truth; `ops.db` is a cache.** This is a deliberate, documented boundary and is respected in the sync code.
- **Ownership is *mostly* clear but not uniformly enforced.** The write discipline is centralised through `skills/purchasing/db_writer.py` (*"Sole write gateway for ops.db. No script calls sqlite3 INSERT/UPDATE/DELETE directly"*) and Asana writes are funnelled through **SAP via the `REQUESTS/` queue**. That is good design.
- **Ownership *conflict* exists between two overlapping "part identity" tables** — `stock_inventory` and `golden_index` both carry `sesame_pn`/`sesame_part_no`, `kan_number`, `stock_qty`, `trigger_order_qty`, `stock_location`, `drawing_url`. Only one is populated. See §4 and §9.

**Information flow (current):**
```
Design team BOM (Dropbox) ─▶ bom_pipeline (Stages 1-5) ─▶ Asana Build Orders
MS Graph email ─▶ purchasing 01-05 ─▶ Google Sheet (HITL) ─▶ REQUESTS/ ─▶ SAP ─▶ Asana
Asana ──(nightly sync_*)──▶ ops.db mirror ──(reads)──▶ agents / daily brief / Telegram
Parts Control "Arrived Qty" ─▶ arrived_stock_processor ─▶ ops.db + REQUESTS/(reset) ─▶ SAP ─▶ Asana
```

---

## 3. BOM Processing

**Entry point:** `skills/bom_pipeline/run_mrp_pipeline.py`, a 5-stage orchestrator (documented in its own docstring and `CLAUDE.md`):

1. **Stage 1 — Consolidation** (`1.BOM IMPORT into Asana/BOM_Consolidator_SLe_v4.py`): merges source `.xlsx` BOM files from a parts folder (read from Dropbox).
2. **Stage 2 — Golden Index Match** (`2.Stock Controls/02_golden_index_match.py`): loads `Merged_Golden_Index_Rev4.csv` into a DataFrame and fuzzy-matches BOM part numbers (`SequenceMatcher`, default threshold 80, `--threshold` overridable) to assign KAN numbers and Asana identities.
3. **Stage 3 — Stock Check** (`03_stock_check.py`): checks matched parts against the KANBAN stock project.
4. **Stage 4 — Staging Export** (`04_staging_export.py`): writes a human-review Excel with an `Override / Reorder QTY` column (`SKIP | HOLD | FORCE_ORDER | <number>`). **This is a hard stop** — `CLAUDE.md`: *"Never run `--commit` without explicit Giles go-ahead. Always stop at Stage 4."*
5. **Stage 5 — Asana Import** (`BOM_IMPORT_4_Asana_v4.py`): creates Asana tasks in **Build Orders (`848657415032331`)**. Dry-run by default; `--commit` required to write.

**Validation:** part-number normalisation and prefix-strip rules are enforced in code (`CLAUDE.md` "Part number format rules": full number `{4-digit-project}.{cad_pn}` to Asana; prefix-stripped in the Golden Index). Preflight (`--preflight`) self-checks inputs before a run.

**Persistence / downstream effects of a BOM import:**
- Primary durable effect is **Asana task creation** in Build Orders (system of record).
- A **Stage 4.5 revision log** (`GI_revision_pending_*.md`) is written when the import discovers new/changed PN↔KAN mappings; these must be applied back to the Golden Index via `update_golden_index.py`. There are **~30 such pending revision files** in `graphify-out/converted/` dating Apr–Jul 2026 — evidence the Golden Index feedback loop is **manual and backlogged**.
- `ops.db.bom_requirements` now holds **261 rows** (schema: `build_ref`, `sesame_part_no`, `required_qty`, `stock_qty_at_import`, `status`, `po_id` FK). **This contradicts the uploaded "Stock Control Snapshot 20.05.2025":** that document stated *"This table is NEVER WRITTEN by Stage 5. Schema exists, rows do not."* Between that snapshot and today the table began being populated — so the snapshot is **stale**, and BOM→requirement persistence is now partially live.

**Relationships:** BOM parts relate to stock via Golden Index KAN mapping and Stage 3 stock check; to purchasing via `bom_requirements.po_id → purchase_orders(id)` and (intended) `po_lines.bom_req_id`; to assemblies via `build_assembly_packs` (2,395 rows). The **`po_lines` junction that would give line-level PO↔BOM traceability is still empty (0 rows)** — see §9.

---

## 4. Golden Index

**What it is (evidence):** the Golden Index is the **canonical part-number master** — the reconciled mapping from a Sesame/CAD part number to its KAN number, Asana task, drawing URL and stock identity. `bom_pipeline/CLAUDE.md` documents the Rev4 CSV format (16 columns; keys `Stock_Number`, `Mapped_KAN`, `Asana_Name`, `Asana_URL`, `Confidence`, `Match_Type`, `Provisional`). `db_init_ops.py` comment: *"KAN reference number … Set from golden_index mapping. Central to stock identity — must not be guessed or inferred from description."*

**Ownership:** the **CSV is the source of truth**, held in two locations that *must be kept in sync by hand*:
- skill copy: `skills/bom_pipeline/2.Stock Controls/Merged_Golden_Index_Rev4.csv`
- lab backup: `…/OneDrive - SESAME ACCESS AZURE/…/2.Stock Controls/Merged_Golden_Index_Rev4.csv`

`CLAUDE.md`: *"Both copies must stay in sync — after any update write to both."*

**Update process:** `update_golden_index.py` (upsert; *"Never hand-edit the CSV directly in code"*). New mappings surface as `GI_revision_pending_*.md` during BOM runs and are applied manually.

**Consumption:** read at BOM Stage 2 for matching. A DB mirror table `golden_index` exists (`seed_golden_index.py`, `db_writer.upsert_golden_index`) intended to make the master queryable from `ops.db`.

**What is incomplete / risky:**
- **The `ops.db.golden_index` table is EMPTY (0 rows).** The re-seed never happened. The uploaded "Ultimate Visibility SQL scheme" session confirms this was a known open item: *"Remaining items: the golden_index re-seed (awaiting your new mapping CSV)."* So today the Golden Index exists **only as a CSV**; nothing in `ops.db` can join to it.
- **Dual-copy CSV with manual sync** is a classic split-brain hazard — the two copies can silently diverge.
- **Backlogged revision files** mean the master and the live Asana state can drift between imports.
- **Naming does not settle its role:** "Golden Index" could be read as (a) a static PN dictionary, or (b) a living reconciliation ledger with confidence scoring. The evidence (`Confidence`, `Match_Type`, `Provisional` columns, revision-pending workflow) best supports **(b): a confidence-scored reconciliation master**, not a static lookup.

---

## 5. Inventory Behaviour

**Model:** inventory is held as **Asana custom fields** on tasks in the KANBAN and Consumables projects (STOCK QTY `1126782469949969`, Trigger Order QTY `1198179728033183`, min Stock QTY `1205084645800516`, Stock ACTION `1199111659373956`). `ops.db.stock_inventory` (116 rows) is a **read-only mirror**.

**Where quantities can change (discovered mechanisms):**
1. **Manual edit in Asana** by staff — the primary and authoritative path.
2. **Arrived stock** — `arrived_stock_processor.py` reads Parts Control tasks with `Arrived Stock Qty > 0`, updates `ops.db`, and files a `REQUESTS/` action for SAP to update the Asana STOCK QTY and zero the Arrived field. Runs every ~hour, Mon–Fri 08:00–18:00.
3. **Nightly mirror sync** — `sync_stock_inventory.py` pulls Asana → `ops.db` (reflects, never authors).
4. **BOM import** — creates order/requirement tasks but does not itself decrement stock.

**Reservations / allocations / consumption / returns:** **no dedicated mechanism found.** There is no reservation table, no allocation ledger, no consumption event, no returns path. `bom_requirements.status` (`unchecked → in_stock → needs_order → ordered → received`) is the closest thing to an allocation lifecycle, and it is coarse and per-import, not a running balance. **Stock is a single mutable number per part, not an event-sourced ledger.**

**Audit history:** every `ops.db` write is appended to **`db_write_log`** (table/row/action/source/fields_changed/timestamp). This gives a write trail *for the mirror*, but note (a) it does not capture the authoritative Asana-side changes, and (b) it has grown to **1,368,006 rows / ~437 MB** with **no rotation** (see §8/§9).

**Reconciliation:** the only reconciliation is the nightly Asana→mirror overwrite. There is no independent physical-count reconciliation or variance report.

---

## 6. Task & Workflow Relationships

**Asana is the workflow substrate.** Jobs/tasks are Asana tasks; "staging" is the Stage-4 Excel and (for ordering) the `ordering_queue` table; procurement triggers are Asana routing rules plus the ordering skills.

- **Job/task creation:** BOM Stage 5 (build tasks) and the purchasing/ordering skills (procurement tasks) create Asana tasks — always via SAP/`REQUESTS/` or the gated Stage-5 `--commit`.
- **Routing → procurement trigger:** a manually-authored **Asana rule** moves a KANBAN task set to `ORDER FOR STOCK` into the **Parts Control** project (`1211660707080749`). `compile_vendor_groups.py` + `create_order_tasks.py` then group by vendor and raise order tasks; `audit_parts_control_routing.py` (nightly 01:30) checks routing integrity. (Uploaded "Stock management deployment system" confirms: *"ORDER FOR STOCK → Parts Control."*)
- **Allocation/consumption logic:** effectively **absent** as automation (see §5). Allocation is implicit in Asana task movement and human action.
- **Completion:** `parts_control.is_completed` flag and `ordering_queue.status` transitions (`grouped → ordered → arrived`).

**Intended vs actual:** the uploaded deployment doc describes a clean, staged ordering pipeline with idempotency and vendor normalisation. The **build has since happened** — `arrived_stock_processor.py`, `compile_vendor_groups.py`, `create_order_tasks.py`, `log_edge_case_to_asana.py`, `audit_parts_control_routing.py` all exist, `ordering_queue` has 16 rows, and the `vendor_name_normalised` columns are present on `parts_control`/`procurement_tasks`. So the doc's headline *"build not started"* is **outdated**; the pipeline is deployed but only lightly exercised (16 queue rows, `ordering_edge_cases` = 0, several ordering crons `last_validated: null`).

---

## 7. Reordering Logic

**Automated reordering does not exist.** This is the clearest gap between the data model's implication and the code's behaviour.

- The **threshold data is captured**: `stock_inventory.trigger_order_qty` and `stock_inventory.min_stock_qty` are synced from Asana (`sync_stock_inventory.py` maps both fields).
- **No script consumes them.** A search across `skills/**.py` for any comparison of `stock_qty` against `trigger_order_qty`/`min_stock_qty` (`reorder`, `<= min`, `below`, threshold checks) found **no consumer** — only the *sync* that writes the thresholds in, and a *manual* `Override / Reorder QTY` column in the BOM Stage-4 staging export (human-entered `FORCE_ORDER`/quantity, not an automatic min-stock rule).
- Reordering today is therefore **human-triggered**: a person sets a KANBAN task's Stock ACTION to `ORDER FOR STOCK`, which fires the Asana routing rule into Parts Control, which the ordering skills then process.

**Conclusion:** the schema is *reorder-ready* but the automation is *reorder-absent*. The evidence does not support any claim that min-stock logic is running. Building the missing "threshold breach → raise ORDER FOR STOCK" evaluator is a well-scoped, high-value addition (see Recommendations).

---

## 8. Offline Operation (special attention)

**What genuinely works offline.** The local, durable pieces are:
- **SQLite** `ops.db` / `crm.db` in **WAL mode** (`db_writer._connect` sets `PRAGMA journal_mode=WAL`) — local reads/writes survive without a network.
- The **`REQUESTS/ → REQUESTS_COMPLETE/ → REQUESTS_FAILED/` file queue** — a durable, filesystem-backed **outbox**. Writes intended for Asana are staged as files and survive restarts/outages until SAP drains them. This is the single best offline-durability primitive in the system.
- `db_write_log` provides a local, replayable write trail for the mirror.

**What cannot work offline.** Everything authoritative is a cloud service: **Asana** (stock + tasks — the system of record), **MS Graph** (email ingest), **Google Sheets** (HITL approval gate), **Dropbox** (BOM sources), and the LLM parser (Gemini). Offline:
- Reads of stock/tasks return **only the last-synced mirror** — silently stale, with no staleness indicator surfaced to users.
- No new email parsing, no approval gate, no Golden Index-from-CSV changes if the OneDrive copy isn't local.
- **Writes queue but do not apply.** The mirror can drift arbitrarily far from Asana while offline; on reconnection the nightly sync **overwrites the mirror from Asana**, which can **silently discard** any local mirror-only reasoning done during the outage.

**What would fail during *extended* offline use.**
- **Arrived-stock double counting.** The uploaded deployment doc documents this precisely: `arrived_stock_processor` runs on a schedule; if it runs again before SAP drains the reset `REQUESTS/`, the same Arrived qty is counted twice. The mitigation (`arrived_reset_pending` flag) exists in schema and `db_writer.update_arrived_reset_pending`, but its correctness depends on SAP actually running — during an extended SAP/Asana outage, either processing stalls or the guard must hold potentially many pending resets. This is a **real, known correctness hazard**.
- **No conflict resolution.** Sync is last-writer-by-Asana; there is a `_is_stale()` guard on `last_synced_at` in `db_writer`, but no true merge/conflict strategy. Concurrent human Asana edits during a long outage win unconditionally on next sync.
- **No recovery/replay tooling** beyond re-running syncs and draining `REQUESTS/`. `db_write_log` is a log, not a rebuild mechanism.

**Architectural improvements required for robust offline / VPS operation:** a monotonic sync cursor per source (not just `last_synced_at` overwrite), an explicit staleness/served-from-cache indicator, idempotency keys on every `REQUESTS/` action (not only arrived-stock), a bounded/rotated `db_write_log`, and a documented reconciliation job. See Recommendations.

---

## 9. Architectural Gaps

**G1 — Migration is labelled complete but is not (Critical).** `PATH_CONFIG.yaml` declares `migration_state: complete`, yet **63 Python files in `skills/` still hardcode `.openclaw` paths**, and the sole DB write gateway does too:
- `skills/purchasing/db_writer.py`: `DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH", r"C:\Users\GilesHugo\.openclaw\workspace\db\ops.db")` — **defaults to the retired database location.**
- `arrived_stock_processor.py`, `daily_brief.py`, `asana_task_updater.py`, etc. hardcode `.openclaw\.env`, `.openclaw\venv`, and `.openclaw\workspace\skills\...`.
- `PATH_CONFIG` itself contradicts its own header comment (*"Currently pointing at .openclaw (live data)"* under a `db:` block that actually lists `SES_Workspace/db/...`), and `db_writer.py` does **not import `path_config.py`** at all despite the config's rule *"Scripts import lib/path_config.py … never hardcode paths."*
  → **Risk:** writes can land in the wrong `ops.db`; two divergent copies of the operational DB can coexist. This is the highest-priority correctness/consistency issue for the VPS move.

**G2 — `ops.db` bloat, no log rotation (Critical/High).** `db_write_log` = **1,368,006 rows**, DB file = **437 MB**. No prune/rotate/VACUUM anywhere in `skills`. On a `$5 VPS` this is a storage, backup, and open-time liability and will only grow.

**G3 — The "sole Asana writer" lives in the retiring harness (High).** `SYSTEM_MANIFEST` places **SAP** (and SAD/SAM/SESAL/Forge/Navigator) under `harness: openclaw`, `dir: .openclaw/agents/...`. The `REQUESTS/` *action handlers* (`execute_requests.py`) are in `SES_Workspace`, but the *agent process/heartbeat* that drains the queue is an OpenClaw construct not present in the workspace. The critical write path still depends on a system marked `retiring`.

**G4 — Manifest cron scripts don't map to workspace files (High).** Manifest cron entries reference `asana_mirror_sync.py`, `email_pipeline.py`, `ordering_compile.py`, `sheet_approver.py`, `parts_control_propagator.py`, `nightly_maintenance.sh` — **none of these filenames exist under `SES_Workspace`** (the real files are `sync_all_mirrors.py`, the numbered `0X_*.py`, `create_order_tasks.py`/`compile_vendor_groups.py`, `05_sheet_approver.py`, etc.). Either the crons point at `.openclaw`/`~/AppData/Local/hermes` copies or the manifest has drifted from reality. Both are deployment hazards.

**G5 — Empty/unused tables (data-model debt) (Medium).** In `ops.db`: `golden_index` (0), `po_lines` (0), `supplier_deliveries` (0), `scraper_runs` (0), `asana_task_states` (0), `asana_write_log` (0). Schema exists, behaviour does not — signals half-built features (line-level PO traceability, delivery tracking) and misleads future maintainers.

**G6 — Overlapping ownership of "part identity" (Medium).** `stock_inventory` vs `golden_index` overlap heavily (`sesame_pn`, `kan_number`, `stock_qty`, `trigger_order_qty`, `stock_location`, `drawing_url`). With `golden_index` empty, the *intended* master is inert and `stock_inventory` is doing double duty. Ambiguous which is canonical inside `ops.db`.

**G7 — No line-level PO structure (Medium).** `purchase_orders.part_numbers` is a *"flat text blob, no line-level detail"* (both uploaded snapshots flag this) and `po_lines` is empty. Receiving, short-shipment, and per-line reconciliation cannot be represented.

**G8 — No automated reorder (Medium).** Per §7.

**G9 — Fragile external couplings (Medium/Low).** Hardcoded Gemini in `02_llm_parser.py` and `daily_brief.py` (manifest `known_issues`); dual-copy Golden Index CSV; Google-Sheets-as-database HITL gate; free-text vendor names only partially normalised (`vendor_name_normalised` added but alias table still "future").

**G10 — Contradictory/stale documentation (Low but corrosive).** The two uploaded snapshots disagree with live schema (`bom_requirements` now written; `parts_control` grew 901→1,378); `purchasing/SKILL.md` still instructs use of the **`.openclaw` venv and `.openclaw/.env`**; nightly note claims `run_history/` "does not exist" while it does. Docs cannot currently be trusted without checking code.

---

## Current-State Architecture

```
                    ┌─────────────────────────────────────────────┐
                    │  HERMES  (NousResearch/hermes-agent, vanilla)│
                    │  cron scheduler · Telegram gateway · agent   │
                    │  core · memory · config @ ~/AppData/Local/   │
                    └───────────────┬─────────────────────────────┘
             runs skills / crons    │            (still parallel:)
                                    ▼                    ▲
        ┌───────────────────────────────────────┐   ┌───┴─────────────┐
        │  SES_Workspace/skills (domain logic)  │   │  OpenClaw (retiring) │
        │  bom_pipeline · purchasing · ordering │   │  hosts SAP/SAD/etc.  │
        │  sql_db sync · daily_brief · RC API   │   │  .openclaw/.env,venv │
        └───┬───────────────┬───────────────┬───┘   └──────────────────────┘
            │ read Dropbox   │ MS Graph email │ writes via REQUESTS/ queue
            ▼                ▼                ▼
     Golden Index CSV   Google Sheet     ┌──────── SAP (sole Asana writer) ───────┐
     (part master)      (HITL gate)      │  drains REQUESTS/ → Asana REST         │
            │                            └───────────────┬───────────────────────┘
            ▼                                             ▼
     ops.db  ◀───nightly sync (Asana → mirror)────  ASANA  (SYSTEM OF RECORD:
     (437 MB cache; db_writer sole gateway,          stock qty, tasks, part fields,
      but DEFAULT path → .openclaw!)                 build packs, routing rules)
```

## Intended-State Architecture

```
        ┌──────────────── HERMES on Linux VPS (always-on) ─────────────────┐
        │ cron · gateway · agent core · secrets · Asana WEBHOOK receiver    │
        └───────────────┬──────────────────────────────────────────────────┘
                        │ all paths via lib/path_config.py (no .openclaw)
                        ▼
        SES skills (BOM · purchasing · ordering · sync) — SAP folded in as a
        Hermes-hosted service, sole Asana writer, idempotent REQUESTS/ outbox
                        │
   Asana webhooks ──────┤ event-driven sync (cursor-based, not nightly overwrite)
                        ▼
        ops.db (slim, rotated audit log) mirror ◀──▶ ASANA (system of record)
        golden_index table seeded from CSV; po_lines populated; reorder evaluator
        (stock_qty ≤ trigger_order_qty → raise ORDER FOR STOCK) running on schedule
```

## Gap Analysis (current → intended)

| Dimension | Current | Intended | Gap |
|---|---|---|---|
| Harness | Hermes + OpenClaw in parallel | Hermes only, on VPS | Retire OpenClaw; migrate SAP; kill `.openclaw` paths |
| Paths | 63 files hardcode `.openclaw`; gateway DB default legacy | all via `path_config` | Refactor to config; delete legacy defaults |
| Sync | nightly full overwrite (Asana→mirror) | webhook/cursor-based, conflict-aware | Build webhook receiver + sync cursors |
| Audit log | 1.37M rows / 437 MB, unbounded | rotated/bounded | Add rotation + VACUUM |
| Reorder | manual only | threshold-driven automation | Build evaluator over `trigger_order_qty` |
| Part master | CSV only; DB table empty | seeded, queryable `golden_index` | Seed + keep in sync; single copy |
| PO detail | flat text blob; `po_lines` empty | line-level junction populated | Populate `po_lines`, wire receiving |
| Write path | SAP in retiring harness | SAP as Hermes service, idempotent | Migrate + add idempotency keys |
| Docs | stale/contradictory | code-true | Reconcile manifest/SKILLs to reality |

## Risks

- **Purchasing/operational:** wrong-DB writes (G1) could raise orders from stale stock, or lose them between two `ops.db` copies. No line-level PO structure (G7) blocks accurate receiving and short-shipment handling.
- **Inventory correctness:** Asana-authoritative stock with eventually-consistent mirror + no reorder automation + arrived-stock double-count window (§8) = quantities that can silently drift or be miscounted; no physical-count reconciliation to catch it.
- **Auditability:** good *intent* (immutable `db_write_log`, immutable `ordering_edge_cases`) undermined by 437 MB bloat and by the log covering only the *mirror*, not the authoritative Asana changes.
- **Offline/durability:** the `REQUESTS/` outbox is solid; everything else degrades to stale-read/queued-write with silent drift and Asana-wins conflict loss on reconnect.
- **Maintainability:** 63 legacy-path files, drifted manifest, contradictory docs, and empty half-built tables make safe change hard and onboarding slow.
- **Deployment (VPS goal):** Windows-path hardcoding throughout blocks a Linux VPS move outright; Hermes config lives outside the workspace; the webhook receiver the end-goal depends on does not yet exist.

## Recommendations (prioritised)

**Critical**
1. **Make DB path resolution single-sourced.** Remove the `.openclaw` default in `db_writer.py`; import `lib/path_config.py`; fail fast if the resolved `ops.db` is missing. Verify there is exactly one live `ops.db` and which copy has the real data before anything else. *(Fixes G1 — the top VPS blocker.)*
2. **Rotate/bound `db_write_log` and VACUUM `ops.db`.** Add a retention job (e.g. keep 90 days, archive the rest) and reclaim the ~437 MB. *(G2.)*
3. **Correct `migration_state` and reconcile the manifest.** Set it to `in_progress`; make every cron entry name a file that actually exists in the workspace, or move those files in. *(G1/G4/G10.)*

**High**
4. **Migrate SAP into Hermes as a first-class, idempotent service** and add idempotency keys to *all* `REQUESTS/` actions (not just arrived-stock). Then retire OpenClaw. *(G3, §8.)*
5. **Purge `.openclaw` hardcoding** across the 63 files; route env/venv/DB/CSV through config. Precondition for the Linux VPS. *(G1.)*
6. **Seed and maintain `ops.db.golden_index` from the CSV**, and collapse to a single authoritative CSV copy (drop the manual dual-copy). *(G5/G6/§4.)*

**Medium**
7. **Build the reorder evaluator:** scheduled job that flags `stock_qty ≤ trigger_order_qty` (KANBAN) / `≤ min_stock_qty` (Consumables) → raise `ORDER FOR STOCK` via `REQUESTS/`, with an edge-case log row. *(G8/§7.)*
8. **Populate `po_lines`** and wire receiving to line status; retire the flat `purchase_orders.part_numbers` blob. *(G7.)*
9. **Replace nightly-overwrite sync with cursor-based, conflict-aware sync** and add a served-from-cache/staleness indicator; this is also the foundation for the Asana **webhook** end-goal. *(§8.)*
10. **Normalise vendors properly** (alias table promised as "future") to stop duplicate order grouping. *(G9.)*

**Low**
11. De-hardcode Gemini (`02_llm_parser.py`, `daily_brief.py`) behind the model config. 
12. Reconcile stale docs (`purchasing/SKILL.md` venv, uploaded snapshots) to current reality; drop or annotate empty half-built tables.

---

## Proposed Hermes Responsibility Contract

*Derived from findings. Hermes = the harness, not the domain system. Domain responsibilities are explicitly excluded and assigned to skills/systems of record.*

**Inputs**
- Cron schedule definitions; inbound messaging (Telegram/etc.); Asana **webhook** callbacks (target state); secrets/credentials; skill invocations.

**Outputs**
- Scheduled/triggered execution of SES skills; message delivery (Telegram); process supervision, logs, and run telemetry; **no direct business-data writes**.

**Events**
- On schedule → run skill; on message → route to agent/skill; on Asana webhook → enqueue a sync/handler skill; on failure → alert (Telegram) and record.

**Data ownership**
- **Owns:** scheduler state, session/agent memory, harness config/secrets, run/telemetry logs.
- **Does not own:** stock quantities, tasks, part master, POs, CRM. Those are owned by **Asana / Golden Index CSV / MS Graph / crm.db**, mirrored (not mastered) in `ops.db`.

**System boundaries**
- Hermes talks to skills and the messaging gateway. Skills talk to systems of record. **All Asana writes go through the single SAP service via the `REQUESTS/` outbox.** All `ops.db` writes go through `db_writer.py`. Hermes never bypasses either.

**Non-responsibilities (explicit)**
- Not the inventory ledger, not the reorder decision engine, not the source of truth for any business entity, not the Asana writer, not the BOM matcher. If a change proposes putting any of these *inside* Hermes core, it is out of contract — it belongs in a skill or a system of record.

---

### Evidence index (key files)
`.Hermes/{README.md,AGENTS.md}`, `git remote` (NousResearch/hermes-agent) · `_copilot/SYSTEM_MANIFEST.yaml`, `_copilot/PATH_CONFIG.yaml`, `lib/path_config.py` · `skills/purchasing/{db_writer.py,execute_requests.py,arrived_stock_processor.py,SKILL.md}` · `skills/bom_pipeline/{run_mrp_pipeline.py,CLAUDE.md,SKILL.md}` · `skills/bom_pipeline/2.Stock Controls/{02_golden_index_match.py,update_golden_index.py,04_staging_export.py}` · `skills/sql_db/scripts/{sync_stock_inventory.py,seed_golden_index.py,db_init_ops.py}` · `ops.db` live schema + row counts · uploaded artefacts (PO Lines, Ultimate Visibility SQL scheme, Stock Control Snapshot, Stock management deployment system).

**Stated uncertainties:** `.openclaw` is not mounted in this session, so SAP's agent-process code and the true live-vs-mirror `ops.db` copy could not be inspected directly — conclusions about SAP and DB duplication rest on config, manifest, and the write-gateway default path, and should be confirmed on the host before acting on Recommendation 1.
