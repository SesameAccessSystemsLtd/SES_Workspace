# SAS OS — Human-in-the-Loop Operations Portal

**Status:** SCOPING (v0 — architecture map, nothing built yet)
**Author:** Hermes w/ Giles · **Created:** 2026-08-12
**Draft location:** `SES_Workspace/docs/os_portal/` — this file is the starting point.

---

## 1. The thesis (what this actually is)

The OS portal is **not** "a prettier Asana." Asana presents every task as equal
noise across siloed projects. The portal is the **human layer of the agentic
system** — the place where Giles (and later the team) **reviews, approves,
adjusts, and pushes back on** what the agents and pipelines are about to do.

Its core value-add, distinct from raw Asana data:

| Value | What it means |
|---|---|
| **Cross-referencing** | Join parts × build packs × suppliers × orders — Asana can't answer "which pack is blocked by which un-ordered part from which supplier, with what lead time?" |
| **Signals, not walls** | Distinguish real blockers / nearly-done / background log items. Example: QC project has ±9,000 rows, only ~16 genuinely open. |
| **Computed status** | Not just "current state" but "should have been fixed 5 days ago": days in `needs_order`, overdue orders, stock below trigger. |
| **One pane of glass** | Replace "open 4 Asana projects + memory-log + email + a hand-written report" with one page. |
| **The decision surface (HITL)** | Where approvals happen — replacing the Google Sheet / scattered Telegram / memory-log loops. **The main reason to build this.** |

**Non-negotiable requirements (every pane):**
1. Every data row hyperlinks back to its source Asana task. The portal is context, Asana is the source of truth — clicking through is always one click away.
2. **Every pane carries a feedback channel.** "This is wrong," "this changed," "this should happen differently" must be capturable in-place, logged, and fed back into improving the portal and the underlying pipelines. **Mistakes will happen — the portal must capture and learn from them, not just display.**

---

## 1b. The feedback / continuous-improvement layer (governing principle #2)

Giles's principle, stated plainly: *"be clear mistakes will happen — we need to be
capturing mistakes or changes that want to be made, and continually improving
these systems."* This makes **feedback a first-class architectural layer**, not a
nice-to-have.

Every data pane carries interaction affordances:

- **"This is wrong"** → capture what's wrong + the row it refers to → log to a
  `feedback` / `portal_audit` record → route to the owning pipeline for correction.
- **"This should change"** → a *proposal* (see §3 HITL) rather than a silent edit.
- **Every correction feeds a ledger** that shows: what the portal once displayed,
  what was wrong, what was fixed, and the fix's source. This builds the
  audit/history that Phase 3 (trends + Delay Destroyer) will draw on.

This is what separates the portal from a vanity dashboard: each displayed figure
must be **auditable to its source** (which DB, which Asana task, which pipeline
wrote it, when). The `db_write_log` / `asana_write_log` / `propagator_audit_log`
tables already exist to back this — the portal should expose them, not hide them.

---

## 2. The six components (scoping — logged, not all to build)

| # | Component | Pane focus | Data source | Urgency |
|---|---|---|---|---|
| 1 | **Project / Build status snapshot** | Per-project % completion, # open packs, # overdue by assignee, ready-to-build % | Asana Build Schedule (live truth) + ops.db (joins) | **FOUNDATION — build first** |
| 2 | **Delay Destroyer lens** | Historical issue categorisation → where failures/opportunities cluster → what to work on | Needs a clean categorisation schema before rendering. Value is real but design-heavy. | Medium — deliberate |
| 3 | **Agentic-development scale/graph** | Where Sesame is on the path into the agentic world, evolving as the platform matures | Concept/north-star page, no live data yet | Low ops urgency |
| 4 | **Email purchasing HITL** | Approve / accept / modify / reject parsed email outputs (moves OFF the Google Sheet) | `parsed_insights.json` + `pending_approval_queue.json` | **HIGH — probed next** |
| 5 | **Task-update proposal HITL** | Meeting/phone outcomes proposing Asana updates, shown for review before writing | Requires a "proposal" queue (new) + REQUESTS→SAP write path | **HIGH** |
| 6 | **Escaped/lost task recovery** | Proposals to push forward tasks that got forgotten/lost | Requires a computable definition of "lost" | Medium |

**Cross-cutting:** components 4 and 5 share the same **HITL interaction pattern**
(propose → review → accept / adjust / reject → write). This is why exploring HITL
layouts for email first is the right probe: it establishes the UI/state pattern
every other loop reuses.

---

## 3. The HITL layer (the lynchpin)

**The governing interaction** — the thing we're actually scoping:

> A proposed action is shown to Giles with its **rationale + source link**, and
> Giles responds **accept / modify / reject**. If accepted, the portal queues the
> write via the existing `REQUESTS → SAP` protocol (never a direct Asana write).

**Email example (the one you gave, generalised):**

```
Tasks in 1588 overdue & faceplates delayed — due next week.
⟶ PROPOSE: move pack task '1566' due date → mid-next-week (after faceplate delivery, ~+1 day goods-in)
   [Accept] [Edit value] [Reject]   ← live HTML buttons
```

**Design principle:** we do **NOT** know which layout works best yet. So we build
**multiple example layouts for the same email-HITL flow** and let Giles pick /
mix. This is explicitly a **scoping / exploration deliverable**, not a replacement
for the Google Sheet yet. The Sheet stays until a layout demonstrably beats it.

---

## 4. Data sources per pane (what actually backs these)

Grounded in `ops.db` schema (23 tables) + live Asana projects.

| Table / source | Holds | Backs pane |
|---|---|---|
| `parts_control` (2,196) | per-part stock_action (PLEASE PICK / on order / project ORDER / lasercut), vendor, build-pack link | 1, 2 |
| `build_assembly_packs` (2,488) | pack refs/names — ⚠️ **insight fields NULL**, truth = Asana Build Schedule | 1 |
| `bom_requirements` (1,169) | import state (`needs_order`/`in_stock`) | 1 |
| `procurement_tasks` (3,966) | POs, SQRs, prices, due dates | 1, 4 |
| `suppliers` (429) | vendor master, lead times, risk profile | 1, 2 |
| `stock_inventory` (119) | kanban stock, min/trigger levels | 1 (stock alerts) |
| `email_po_matches` (22) | email→PO matching history + confidence | 4 |
| `asana_write_log` / `propagator_audit_log` / `db_write_log` | audit trails | 3 (trends) |
| **Asana Build Schedule** (852956628490855) | pack open/completed/due — **live truth** | 1 |
| **Asana Order Numbers** (854650811917168) | PO `due_on` = **expected** arrival | 1, 4 |
| **Asana QC** (737971594126804) | open QC blockers (of ±9k historical) | 2 |
| `giles_tasks` (553) | triaged task list | 1, 6 |
| `parsed_insights.json` + `pending_approval_queue.json` | email-parser output for approval | 4 |

**Critical architecture rule (else we reproduce a false-readiness trap):**
`build_assembly_packs` insight columns are **all NULL** — the portal must read
pack open/completed/due truth from **live Asana Build Schedule**, and use
`ops.db` only for joins / computation / history. Same for deliveries: reliable
signal = Asana Order Numbers task `due_on` (expected, not confirmed — the DB
`purchase_orders.expected_delivery` and `supplier_deliveries` are empty).

---

## 5. Hosting — the open question (options on this machine)

**Data source — CONFIRMED 2026-08-12, single live DB:**
- `SES_Workspace/db/ops.db` **is the live database**: 908 MB, modified today,
  `db_write_log` at 2,851,607 rows with max write today 16:20:54 — pipelines
  write here right now.
- The old `.openclaw/workspace/db/ops.db` **no longer exists** (OpenClaw retired).
  The PATH_CONFIG section-2 note describing SES_Workspace/ops.db as a "frozen
  fork" and pointing live data at `.openclaw` is **STALE — must be corrected**.
  There is only ONE ops.db; the portal reads it, and it is genuinely fresh with
  America 1 (1588: 538 parts, 30 packs, 537 BOM lines — synced/imported today).
- Dropbox is **read-only** per guardrails — not a write/host target.

**⚠️ ACTION REQUIRED (advice, not done):** update `_copilot/PATH_CONFIG.yaml`
(lines 19–26) to drop the `.openclaw` "live data" claim and mark
`SES_Workspace/db/ops.db` as canonical live. Until corrected, any script trusting
that note will point reads at a nonexistent DB. Recommend doing this before the
portal refresh pipeline ships.

Verified available on this machine: **Python `http.server`**, **Node v24.3.0**.
Sesame AI fleet harness (CoWork-OS) is in `deploying`.

| Option | What it is | Fit so far | Notes |
|---|---|---|---|
| **Static HTML + cron refresh** | Python/Node script regenerates an HTML file on a schedule (`data/reports/...`); Giles opens it / refresh in browser | **v1 — simplest, matches how the America 1 report already works** | Builds on the exact pattern already proven. No server, no deps. |
| **`http.server` (local)** | Tiny server serving static files; auto opens at `localhost` | Easy next step for "live" feel + HITL buttons | Same scripts, wrapped in a server. Fine for single-user Giles. |
| **Node (Vite/Express) app** | Proper dev server / single-page app with HTML buttons + POST route for HITL actions | Right once HITL needs write-back + state | Node v24 present. Heavier; defer until HITL design chosen. |
| **Sesame AI (CoWork-OS)** | Fleet GUI harness (deploying) | The eventual team-facing home | Watch this deployment; OS portal should be designed to embed here later. |
| **Cloud (VPS/S3)** | Always-on, team-accessible anywhere | Later, per SYSTEM_MANIFEST VPS migration path | Overkill for v1. Backed by a thought-out static-first design so migration is cheap. |

**Recommendation for v1:** stay **static HTML, regenerated on a cron + served as a
file.** It reuses the America 1 report pattern, zero new infra, and everything we
learn about layout/HITL-in-HTML transfers directly to whatever host comes next.
The hosting answer and the layout answers are **decoupled** if we keep the portal
as plain static HTML + vanilla JS — which is a deliberate simplicity choice.

---

## 6. Build order (proposed)

| Step | Deliverable | Why |
|---|---|---|
| **D · NOW (scoping)** | This doc + HITL layout exploration | You said scope first — agreed. Locks architecture + surfaces layout options. |
| **1** | **Live America 1 status page** — take the existing `america-one-production-report-2026-08-03.html`, wire it to a refresh script that pulls live, make project-switchable across all jobs | The foundation shell every other pane reuses; safe (read-only); proves the live-refresh pattern. |
| **2** | **Email HITL — multiple example layouts** (approve/adjust/reject surfaces side-by-side for the same parsed email queue) | Answers "what works best" with hands-on examples; runs alongside the Google Sheet, doesn't replace it. Establishes the HITL UI state pattern for panes 4–6. |
| **3** | Pick the winning email-HITL layout → wire to `pending_approval_queue.json` → then to `REQUESTS→SAP` for real writes | The loop actually closes. |
| 4+ | Task-update proposal HITL (pane 5) → Delay Destroyer (pane 2, after categorisation schema) → agentic-scale graph (pane 3) → task recovery (pane 6) | Expand once the shell + HITL pattern are proven. |

---

## 7. Open questions / decisions needed

1. **Hosting:** confirm the static-HTML-for-v1 recommendation before I scaffold the refresh pipeline. (My call: yes — cheapest, proved, decoupled.) — noted, Giles green-lit static HTML in 2026-08-12 chat.
2. **Live DB source:** ✅ **RESOLVED 2026-08-12** — single live DB `SES_Workspace/db/ops.db`; `.openclaw` fork gone. Portal reads this one. PATH_CONFIG doc correction still pending (see §5).
3. **Email HITL scope:** confirm it runs **alongside** the Google Sheet during the layout-exploration phase (not replacing it). You indicated yes — flagging as a decision.
4. **America 1 status page:** should it be **project-switchable across all jobs** in v1, or stay 1588-only initially? (Recommend: project-switchable — the shell cost is the same.)
5. **Delay Destroyer:** needs a categorisation schema (the "loosely categorizes" categories formalised) before it can be rendered usefully. Park until after HITL is proven.
6. **Feedback layer (NEW, governing principle #2):** confirm the feedback/audit ledger becomes standard on every pane as specified in §1b.

---

## 8. Done / not done

- [x] Thesis + value-add articulated
- [x] Six components named & scored
- [x] HITL lynchpin + email example captured
- [x] Data sources grounded in real schema + live Asana GIDs
- [x] Hosting options mapped to real available tooling
- [x] Build order proposed
- [x] **♻️ Feedback / continuous-improvement layer added (§1b) — governing principle #2**
- [x] **✅ Live DB source VERIFIED — single live DB `SES_Workspace/db/ops.db`; no .openclaw fork; America 1 present & fresh**
- [ ] PATH_CONFIG.yaml stale `.openclaw` note corrected (advised — pending)
- [x] **✅ v1 status page SHIPPED** — `skills/os_portal/build_os_status.py` → `data/os_status/index.html` (live Asana Build Schedule + Order Numbers + ops.db procurement; project-switchable; Asana hyperlinks every row; feedback gutters; reads single live DB `SES_Workspace/db/ops.db`)
- [x] **✅ Cron registered** — job `c1d757352bb7` 'OS Portal — Status Snapshot Refresh' 09:00/12:00/15:00 Mon–Fri, no_agent (quiet-on-success)
- [ ] Email HITL example layouts built
- [ ] HITL write-back via REQUESTS→SAP tested
- [ ] Feedback ledger capture loop proven on one pane
