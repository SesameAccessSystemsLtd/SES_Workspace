# SAS Procurement & BOM Platform — VPS Migration Readiness Assessment

**Date:** 2026-08-17
**Author:** Hermes (system architect)
**Scope:** 8 pipeline stages, deployment to VPS host, failure modes, data routing, validation tests.

---

## Executive summary

The platform is **partially built and highly fragmented**: 9 of the ~20 ops.db tables are actively
used, but the full procurement value chain is **not closed end-to-end**. Several stages that define
the "procurement platform" (outbound RFQ drafts, cost push into analytics, delivery pre-flight
check-ins, grouped QC follow-up emails) **do not exist** — the code, tables, and crons to support
them are absent.

The critical architectural fact: **SES runs a "process → Asana (source of truth) → ops.db (mirror/analytics)" model.**
Any VPS migration must preserve this: **Asana = the system of record for work; ops.db = the analytics shadow
DB; the portal = the human view; REQUESTS/ = the only write channel.** The VPS adds always-on webhook
listeners; it does not change the write model.

**Data-routing rule that governs every stage below:**
- **Push to Asana** (via REQUESTS→SAP only): things humans act on and that must survive device loss —
  part assigned to pack, part "needs order", RFQ → vendor task linking, order created, QC verdict.
- **Keep offline in ops.db (analytics portal only)**: everything numeric/aggregate that changes cost of
  delivery — unit costs, supplier history scoring, quote returned prices, delivery variance, SQR send/receive
  counters. Asana custom fields carry *status* and *one canonical value*; they are NOT a database for analytics.

| Stage | Current status |
|---|---|
| 1. Drawing-to-Part assignment | In Progress (tools exist; not fully auto-wired) |
| 2. Picking/Verification/Requisition | In Progress (runs on cron; gaps in dedupe/lead-time) |
| 3. Supplier Discovery & Historical Lookup | Blocked (supplier contact data not populated) |
| 4. RFQ Dispatch & SQR Tracking | Blocked (no outbound email path at all) |
| 5. Cost Push & Selection Logic | Blocked (`po_lines`/unit-cost unbuilt) |
| 6. Order Lifecycle & Delivery Pre-flight | In Progress (delivery truth present; pre-flight unbuilt) |
| 7. QC Exception & Issue Tracking | In Progress (9,020 issues tracked; grouped email unbuilt) |
| 8. VPS Migration Readiness | In Progress (skill + webhook pattern exist; endpoints/state gap) |

---

## Stage 1 — Drawing-to-Part Assignment

**Current Status: In Progress.**
Two routes exist and are validated: `legacy_bom_pack_ingest.py` (design-team Legacy Excel — PRIORITY per
Giles 2026-08-17) and `run_drawing_pipeline.py` (drawing-PDF fallback, 4 stages). Both write
`Build Packs reference` field (`1211660742155293`) + `parts_control.build_assembly_pack_gid`, and are
gated by REQUESTS→SAP (never direct writes).

**Identified Edge Cases & Gaps:**
- Pack auto-assignment is NOT wired to cron — only *detection* runs (`BOM Release Watcher` `10c8bd2d0738`).
  The overnight chain expected (legacy → pack → drawing-fallback) is manual on Giles's go.
- **Timing dependency:** pack tools act only on parts already in `parts_control`; a BOM committed today has
  rows only after the next **01:00** mirror sync. So an overnight chain must run at ~**02:00**, resolving only
  *yesterday's-or-earlier* commits.
- `-` in pack filenames (`1037.1014.M.Gate+Support_rev2`) break `resolve_pack` → file skipped as `not_found`.
- `.DRAWING` is a *sibling* task, not a parent (fixed 2026-07-23); PDF extraction is suppressed by `--dry-run`.
- Phantom GIDs (BOM commit Stage-5 timeout) → `/could not fetch task` 404s on pack write.
- **Missing-metadata flagging:** no automatic flag when a `parts_control` row lacks any pack link after the sync.

**Proposed Solution (minimalist):**
```mermaid
legacy_bom_pack_ingest --scan  →  review JSON (HITL)  →  REQUESTS (pack_assign)  →  SAP writes
                  ↓ (residue unassigned)
run_drawing_pipeline --stop-after 3  →  REQUESTS (pack_assign)  →  SAP writes
```
Add a single **"unassigned parts" net** (`05_reconcile_pack_association.py`, currently PAUSED) that flags every
`parts_control` row with `association_status IS NULL` for its job — this is the "missing metadata" detector.

**Data Routing:**
- Asana custom field `Build Packs reference` (GID) + description append (`{qty} off in Assembly Pack {pack}`) —
  push to Asana.
- `parts_control.build_assembly_pack_gid`, `association_status` — mirror in ops.db **along with** the Asana write
  (append-only, never overwrite an existing link).

**Testing & Validation (local before VPS):**
1. `legacy_bom_pack_ingest.py --scan` on one project (e.g. 1037) → confirm the stage JSON has correct pack GIDs.
2. Confirm `build_assembly_packs` has the pack + its `.DRAWING` sibling (run `--stop-after 1`).
3. Stage to `--stop-after 3` WITHOUT `--dry-run`; verify `parts_control.build_assembly_pack_gid` populated.
4. Verify `execute_requests.py` handles the `pack_assign` action type (currently does NOT — gap).
5. Dry-run the 05 net; confirm it flags exactly the genuinely-unassigned residue (not NBSS/weld fixings).

---

## Stage 2 — Picking, Verification & Requisition

**Current Status: In Progress.**
`parts_control` (2,548 rows), `stock_inventory` (235), `ordering_queue` (120), `audit_parts_control_routing.py`,
`ordering_compile.py` (`Ordering - Compile and Create Order Tasks`, 08:00), `arrived_stock_processor.py`
(`Ordering - Arrived Stock Processor`, hourly). Crons exist and run.

**Identified Edge Cases & Gaps:**
- **Duplicate part requests:** `ordering_queue` can hold the same `sesame_pn` for the same job/vendor across
  multiple passes — no dedupe-on-insert guard.
- **Missing lead times:** `suppliers.typical_lead_times_days` is NULL for most rows (sync pulls only name+tags).
- **Unverified specs pre-RFQ:** a part with no vendor mapping or unmatchable PN (`(2Way-Spr)` etc.) goes to ORDER
  silently with no "spec unverified" flag.
- Parts in `stock_inventory` that are below `trigger_order_qty` are only caught by the 08:00 compile, not live.

**Proposed Solution:**
- Add a unique index on `ordering_queue (sesame_pn, source_project_gid, vendor_name_normalised)` to enforce dedupe,
  or add a "confirmed vs proposed" status so duplicates surface at review.
- Populate lead times from supplier portals/Asana supplier tasks (same enrichment that fixes Stage 3).
- Add a `spec_verified` bit on `ordering_queue` rows fed by the Golden Index match confidence; flag
  `PICK`/`ORDER` rows at confidence < 100%.

**Data Routing:**
- Asana: `parts_control` stock ACTION enum (PICK / ORDER), vendor assignment, Order Numbers task.
- ops.db: `ordering_queue` (dedupe + spec-verified + lead-time expected), `stock_inventory`.

**Testing & Validation:**
1. Run `ordering_compile.py` twice on the same state → confirm no new duplicate `ordering_queue` rows.
2. Pick a low-stock part → confirm the 08:00 compile proposes it; verify the Order Numbers task is staged via
   REQUESTS, not written directly.
3. `arrived_stock_processor.py`: mark an order arrived → confirm `parts_control` stock value + `ordered_at`
   cleared / `arrived_at` set, idempotently (re-run yields no double-count).

---

## Stage 3 — Supplier Discovery & Historical Lookup

**Current Status: Blocked.**
`suppliers` = 430 rows but `contact_email`, `contact_phone`, `primary_domain` are NULL for the vast majority
(the known data gap, memory entry). `sync_suppliers.py` pulls only name + tags. The RC-call phone → supplier →
order inference cannot run.

**Identified Edge Cases & Gaps:**
- **Ambiguous part numbers** — no vendor-part-number ↔ Sesame-PN mapping table to disambiguate identical refs.
- **Missing vendor history** — no per-supplier score/lead-time/quote-response history (needs `supplier_deliveries`
  + a quote-history table, both currently empty/absent).
- Vendor-name inconsistency: `vendor_name_normalised` exists but 13+ spellings for one supplier historically.

**Proposed Solution (lean):**
- Extend `sync_suppliers.py` to pull **Email + Phone + Payment** custom fields from the
  `1 - Supplier List / Engagement` project (`414986125671548`). This is the single highest-leverage fix.
- Add a `quote_history` table (SQR ref → price → date → selected?) and score suppliers on response time + variance.
- Add a `vendor_pn_map` table: Sesame-PN → supplier's PN → supplier GID for disambiguation.

**Data Routing:**
- **Offline (ops.db) by default:** supplier scores, history, lead times, price variance. High-level supplier
  *status tag* (active/manual/non-procured) can go to Asana as a tag, but the analytics stay offline.
- Do NOT push supplier scoring to Asana — it's an analytics concern, not a work-tracking one.

**Testing & Validation:**
1. Run extended `sync_suppliers.py` → confirm `contact_email/phone` populated counts jump several hundred.
2. Probe the RC-call inference: a RingCentral "With: Adam/Nikki - Thameside 01784 449326" note now resolves to the
  Thameside supplier via phone → its open orders via PO lookup.

---

## Stage 4 — RFQ Dispatch & SQR Tracking

**Current Status: Blocked — NO outbound email path exists.**
The email pipeline (01 fetch → 02 parse → 03 match → 04 sheet HITL → 05 approve) is **inbound-only**.
`procurement_tasks` (4,031 rows) stores `sqr_number`, and the matcher tracks SQR refs — but there is no code that
**generates a draft RFQ email** and no SMTP/Graph send path. Quote-review is via Google Sheets HITL + the QA portal.

**Identified Edge Cases & Gaps:**
- No draft-RFQ generation at all (the core requirement here).
- No SQR **issuance**; SQRs come FROM suppliers (inbound). A "quote we sent / quote returned / received count"
  ledger requires SQR to be tied to a sent RFQ — not currently modelled.
- Email creds: Graph = `PURCHASING_API_KEY` (Doppler prd); AADSTS7000215 was rotated. Fetch works; send path unused.

**Proposed Solution (must satisfy the "drafts for review" hard rule):**
```mermaid
RFQ-needed task → 02/03 build RFQ payload → 04 write DRAFT to Outlook Drafts (Graph)
                                              ↑ human reviews + clicks Send in Outlook
SQR returned → inbound 01 fetch → 03 match to SQR → QA sheet HITL → quote parsed
```
- Add a **`rfq_outbox` table** (or reuse `procurement_tasks` with an `rfq_status` col): SQR ref, supplier, part list,
  draft_gid, sent_at, received_at. **Track sent vs received quote counts per SQR** from this.
- ALL outbound emails are created as **Outlook Drafts** (Graph `POST /me/messages` with `isDraft=true`) — human
  reviews and clicks Send. Never auto-send.

**Data Routing:**
- Asana: SQR custom field on the quote task, linking the RFQ → vendor task.
- ops.db: `rfq_outbox` (SQR ledger: sent/received counts), parsed returned quotes.
- **Email drafts live in Outlook, not Asana** — Asana stores the SQR link + status tag only.

**Testing & Validation:**
1. Drive one RFQ-needed task → confirm a **Draft** appears in Outlook (not sent) with correct part list + SQR ref.
2. Simulate the returned quote: put a SQR-matched email through 01→03 → confirm it lands in the QA sheet HITL
   and the `rfq_outbox` "received" tick increments.
3. Assert the send path is impossible without a human action (no code path calls `send` automatically).

---

## Stage 5 — Cost Push & Selection Logic

**Current Status: Blocked.** `purchase_orders` (7 rows), `po_lines` (0 rows) — the cost/unit-price layer is unbuilt.

**Identified Edge Cases & Gaps:**
- **No unit-cost storage.** Row-level costs from returned quotes aren't persisted anywhere.
- **No vendor comparison/selection logic** — nothing scores "quote A vs B" to pick a preferred vendor.
- **Asana field pollution risk:** if unit costs were pushed to Asana custom fields per part, they'd clutter tasks.

**Proposed Solution (lean, offline-first):**
- Populate `po_lines` from parsed quote line-items (the QA pipeline already parses quotes — feed it here).
- Add a `quote_comparison` view: per part, all returned quotes (supplier, unit price, qty, lead time) → rank.
- Compute **weighted cost** (price + shipping + lead-time risk) offline; push ONLY the chosen vendor + a "cost ok"
  status to Asana, never the full matrix.

**Data Routing (explicit answer to your question):**
- **Itemized unit costs stay STRICTLY offline in ops.db `po_lines` / analytics portal** — NOT in Asana task fields.
- Asana gets: selected vendor task link, Price Ex VAT custom field (the single canonical number Giles approves),
  and a status (quote-received / selected). The analytics portal holds the full cost picture + history.

**Testing & Validation:**
1. Parse a real returned quote → confirm `po_lines` row created per line item with unit price + PN.
2. For a part with 2+ quotes, confirm the comparator ranks them and surfaces a recommendation (never auto-selects).
3. Confirm no Asana field carries the full cost matrix (only the canonical Price Ex VAT).

---

## Stage 6 — Order Lifecycle & Delivery Pre-flight

**Current Status: In Progress.** Delivery *truth* = live Asana **Order Numbers** (`854650811917168`) task `due_on`
(expected arrival). `purchase_orders.expected_delivery` + `supplier_deliveries` are empty — the analytics mirror is
unpopulated. **Pre-flight check-in emails are not built.**

**Identified Edge Cases & Gaps:**
- Expected-vs-confirmed confusion: `due_on` is *expected*, not confirmed. No `confirmed_delivery` signal pipeline.
- **Lead-time slip detection:** nothing recomputes "due date approaching / already past, not confirmed" to trigger
  a check-in.
- No automated follow-up email drafts (the requirement).

**Proposed Solution:**
- Populate `supplier_deliveries` + `purchase_orders.confirmed_delivery` from Order Numbers + inbound dispatch
  confirmations (the email matcher already handles `dispatch_note` — extend to capture conf delivery date).
- Add a **pre-flight watcher** (cron): for orders with `due_on` within `N` days and no `confirmed_delivery`, emit a
  **drafted** follow-up email (Outlook Draft, per Stage 4 rule), listing the PO + parts, for human send.

**Data Routing:**
- Asana: `due_on` (the authoritative expected date), confirmed-delivery status field.
- ops.db: `supplier_deliveries` variance (days_late), `purchase_orders.confirmed_delivery`.

**Testing & Validation:**
1. Take an open Order Number with due_on in 3 days, no confirmation → the pre-flight watcher drafts a check-in.
2. Re-run → idempotent (no duplicate draft for same order within the window).
3. Confirm the draft reviews the PO + linked parts + SQR but is not auto-sent.

---

## Stage 7 — QC Exception & Issue Tracking

**Current Status: In Progress.** `qc_issues` = **9,020 rows** — the richest, most complete table in the DB,
synced from Asana QC tasks with `associated_po_number`, `associated_sqr_number`, `parts_control_gid`,
`signed_off_status`, `date_out_of_project`, `impact`, `order_value`.

**Identified Edge Cases & Gaps:**
- **Grouped follow-up email not built** — the requirement to select all flagged parts → one drafted email with
  attachments → linking thread history back to parent part / Asana task.
- `qc_issues` duplicates mask real counts (same issue may appear across sync passes) — need dedupe by
  `asana_task_gid` + `last_synced_at`.
- No "flag parts requiring follow-up" toggle that persists a selection for batch email.

**Proposed Solution (manual trigger, exactly as you asked):**
- Add a **portal view** listing un-signed-off QC issues grouped by supplier. A "Select flagged parts → Draft follow-up
  email" button builds ONE Outlook Draft (allow attachments) whose body lists each part + its Asana task link +
  PO/SQR. Thread history is preserved by linking the draft to the supplier's chain (reply-to the original PO thread).
- Manual trigger — never auto-send. This is a HITL grouping + drafting surface, not automation.

**Data Routing:**
- Asana: keep `signed_off_status`, `associated_po_number` etc. (source of truth for QC work).
- ops.db `qc_issues`: dedupe + analytics (impact, order_value, date_out_of_project) — the offline aggregation.

**Testing & Validation:**
1. Open the QC portal view → select 3 un-signed-off issues for the same supplier → "Draft" → confirm ONE Outlook
   Draft with all 3 parts + links + attachments, no auto-send.
2. Confirm each part row hyperlinks to its parent Asana task (`parts_control_gid` → task URL).
3. Re-select/deselect → confirm the draft reflects the final selection idempotently.

---

## Stage 8 — VPS Migration Readiness

**Current Status: In Progress.** The `vps-hosting-and-multi-instance` skill is battle-tested (2026-08-18). Key facts:
- Hostinger Hermes template (KVM 2 / 8GB) is the sweet spot; `nousresearch/hermes-agent` is stateless, all state in
  one `/opt/data` bind-mount.
- Migration = copy code (git) + live state (scp `ops.db`) + rebuild secrets (.env from Doppler) + own bot token.

**Identified Edge Cases & Gaps:**
- **Webhook endpoints not yet defined/enumerated.** The `os-portal` skill references `hookdeck/webhook-skills`
  (158 webhook patterns incl. Asana) and the Asana webhook protocol (X-Hook-Secret handshake, HMAC signature,
  batched events, heartbeats) — but **no public receiver exists yet**. The `bom_dispatch_server.py` (127.0.0.1:8765)
  localhost listener is the placeholder.
- **Environment variables inventory** not consolidated into one VPS `.env` spec.
- **Fallback logging** not defined (what happens if VPS is down — messages queue? who owns retry?).
- **D6 (one live writer):** must NOT run the same purchasing/Asana cron on both local and VPS — racing writes.
  VPS owns always-on watchers + cron (shadow DB); local stays interactive/dev.

**Proposed Solution (VPS as always-on, webhook receiver):**
- Deploy a **public webhook receiver** on the VPS (the same listener pattern as `bom_dispatch_server.py` but
  internet-exposed behind Tailscale/SSH, NEVER public). It receives Asana `X-Hook-Signature`-verified events and
  writes REQUESTS files — Asana writes still go REQUESTS→SAP only.
- Define the env-var set (from the D4 list in the VPS skill): DEEPSEEK, ASANA_PAT, PURCHASING_*/EMAIL_API_KEY,
  HUBSPOT, GEMINI, DROPBOX, DOPPLER, plus the new Graph-send secret for RFQ drafts.
- Fallback logging: write all webhook events to a VPS-side log + `asana_write_log`; if a job can't run, emit the
  error to System Updates (no silent failure), and ensure the scheduler retries.

**Data Routing:**
- Webhooks → REQUESTS → SAP (Asana writes unchanged). ops.db shadow lives on VPS (`/opt/ses/workspace/db/ops.db`).
- Portal HTML generated on the VPS and served (replaces the localhost/file:// caveat entirely — the PNA block and
  `bom_dispatch_server` hosting disappear once the page is served from the VPS).

**Testing & Validation (local before deploy):**
1. Verify `ops.db` has no WAL/SHM sidecars → `scp` copy to VPS.
2. Stand up the public receiver behind Tailscale; confirm Asana webhook handshake (responds to X-Hook-Secret).
3. Send a test Asana event → confirm it lands as a REQUESTS file and `execute_requests.py` picks it up.
4. Confirm cron ownership: only VPS runs the purchasing/Asana crons; local is paused. Verify no double-writing
   via `asana_write_log` (each GID written once, by one actor).

---

## Prioritized Action Plan — Top 3 to execute today

**1. Unblock Supplier Discovery (Stage 3) — highest leverage, unblocks stages 4, 5, 6.**
Extend `sync_suppliers.py` to pull Email/Phone/Payment from `1 - Supplier List / Engagement`
(`414986125671548`). Populate `suppliers.contact_email/phone/domain`. This unblocks the RC-call → supplier →
order inference and gives RFQ drafts a recipient address. *(Offline DB; ~1 script change + rerun.)*

**2. Implement outbound RFQ as Outlook Drafts + SQR ledger (Stage 4) — the core missing capability.**
Build a `rfq_outbox` step: generate an RFQ **Draft** (Graph `POST /me/messages`, `isDraft=true`, never auto-send)
from RFQ-needed tasks, tagged with a new SQR ref, tracked sent/received per SQR in ops.db. Wire the returned-quote
matcher to tick "received". *(Offline ledger; Outlook Drafts; ~2 script additions + a DB table.)*

**3. Close the pack-assignment automation gap (Stage 1) — completes the drawing pipeline.**
Add the `pack_assign` action type to `execute_requests.py` (currently it only handles due_date/append_notes/ORDER
status), and wire the overnight chain at ~02:00 (legacy → reconcile → drawing `--dry-run`) AFTER the 01:00 mirror
sync, gated REQUESTS-only. Add the unassigned-parts net to flag missing metadata. *(REQUESTS→SAP; ops.db mirror.)*

---
*Full skill/script/table references and the live state the assessment draws from are in the Hermes skills:
`os-portal`, `drawing-pipeline`, `email-purchasing-pipeline`, `purchasing-order-framework`,
`data-architecture-evaluation`, `vps-hosting-and-multi-instance`. Recommended hosting location:
`SES_Workspace/docs/sas-procurement-bom-vps-readiness.md`.*
