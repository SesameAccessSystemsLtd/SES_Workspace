# Hermes Evaluation — SES Data Architecture & Future-State Readiness

**Date:** 2026-08-13
**Evaluated by:** Hermes (Giles's orchestration layer)
**Based on:** Live inspection of `SES_Workspace/db/ops.db` (23 tables), `config.yaml`, `channel_directory.json`, `REQUESTS/` queue, and `SES_Brain` architecture docs.

---

## Scorecard

| # | Area | Readiness (1–10) |
|---|------|------------------|
| 1 | System of Record Architecture | **8** — strong ownership model, one ambiguity |
| 2 | Identity & Permissions | **3** — the critical gap; no identity layer exists |
| 3 | Multi-User Scaling | **5** — works for 1, needs work for N |
| 4 | Event-Driven Operations | **5** — good data, no relationship/index layer |
| 5 | Knowledge Graph Readiness | **4** — entities exist, relationships don't |
| 6 | Personalised Workspaces | **3** — no per-user ownership anywhere |
| 7 | Approval Engine Readiness | **4** — approval human-only today, not data-modelled |
| 8 | Cloud/VPS Deployment | **6** — single-file SQLite is the bottleneck |

**Overall Future-Proof Score: 5 / 10**

Not a redesign-from-scratch situation. The **core data is well-structured** and the **division of responsibility is sound**. The gaps are *structural additions* (identity, relationships, ownership), not reworks of what exists.

---

## 1. System of Record Architecture — 8/10 ✅ (mostly future-proofed)

The current separation is genuinely close to the target state:

| Layer | System | Role | Verdict |
|-------|--------|------|---------|
| Work management | **Asana** | Tasks, workflow, status | ✅ clear |
| Data | **ops.db / crm.db** | Numbers, structure, relationships | ✅ clear |
| Communication | **Telegram** | Channel/presentation only | ✅ clear |
| Presentation/action | **HTML portals** (os_status, bom_tracker) | Read/approve surfaces | ✅ clear |
| Orchestration | **Hermes** | Coordination, cron, oversight | ✅ clear |

**Confirmed strengths:**
- "SAP is the sole Asana writer" (ARCHITECTURE_DECISIONS 2026-04) is the right call and holds — single Asana token, single mutation point, `asana_write_log` captures every change.
- "SQLite as unified read layer" is correct — no agent-to-agent data chains.
- The REQUESTS/ file protocol cleanly separates *who proposes* from *who writes*.

**The one ambiguity:** **the Golden Index.** ARCHITECTURE_DECISIONS says it stays a CSV (Rev4). But `golden_index` table in ops.db is **empty (0 rows)** while parts/stock data lives in `parts_control` + `stock_inventory`. So the master part-number reference is *business logic (CSV)* while operational copies are *in DB*. That's workable but fragile — two competing "part number source of truth" candidates. Worth resolving explicitly.

---

## 2. Identity & Permissions — 3/10 🔴 (the critical gap)

**Finding: there is no identity layer anywhere in the data model.**

- No `users`, `roles`, `permissions`, `accounts`, `engineers`, `assignees`, or `actors` table exists (confirmed: querying for these returns nothing).
- Every audit table records **which script ran** (`db_write_log.source` = `sync_parts_control`, `bom_pipeline`, `ordering_pipeline`...) but **never *who* requested, approved, or executed**.
- `asana_write_log` has `source`, `action_type`, `value_applied`, `result` — but no `requested_by`, `approved_by`, `executed_by`.
- Current permission controls are **entirely config/env-string based**: `TELEGRAM_ALLOWED_USERS` (env), `telegram.allowed_chats` (config.yaml), the Asana REQUESTS protocol (file-based HITL). These authorise *chat ids and user ids at the Telegram layer* — they do **not** model *roles* or *action-level permissions*.

**Why this matters for the future-state request:** multi-engineer, multi-agent, service-account, per-user permissions, and "who requested/approved/executed" audit trails **cannot be built today** without adding tables. This is the single most important finding — everything in requirements 2, 3, 6, and 7 ultimately depends on it.

**Recommendation on YAML→DB evolution: Yes, evolve.** But do it incrementally:
- Keep the Telegram/chat allowlist in env/config (that's *transport* auth, fine where it is).
- Add **DB tables** for *business* identity: `users`, `roles`, `user_roles`, `permissions`, `role_permissions` (or a simpler `actors` + `actor_roles` to start).
- Wire audit columns (`actor_id`, `requested_by`, `approved_by`, `executed_by`) into the write logs.

---

## 3. Multi-User Scaling — 5/10 ⚠️ (works for 1, needs N)

Current state: **Giles (admin) + Richard (1 colleague via dedicated group)**. This is already beyond a single-user system and the Model-C architecture (dedicated group, same bot, scoped skill) is proven.

**Assessment of the models:**

| Model | Fit | Notes |
|-------|-----|-------|
| **One shared Hermes instance** (Model C: dedicated groups per user) | ✅ near-term | Works well up to ~5–10 users. Each user = a channel_prompt + a scoped skill + a chat allowlist entry. Cheap, proven (Richard). |
| **Multiple user-specific Hermes instances** (Model B) | ⚠️ only when needed | Hard isolation, but per-instance maintenance. Right for external vendors or departments that must not share anything. |
| **Hybrid** | ✅ long-term | Shared instance for trusted staff; separate instances only where isolation demands it. |

**The blocker:** scaling past a handful of users is not *hardware* — it's **identity and scoping**. Every new user today needs a hand-written skill + a config prompt + an env allowlist edit + a gateway restart. Without `users`/`roles`/project-ownership tables, you can't generate those views dynamically.

**Recommendation:** Stay on the **shared instance + hybrid later** path. The Richard rollout proves the pattern; the DB just needs the identity tables to make the Nth user cheap instead of hand-built.

---

## 4. Event-Driven Operations — 5/10 ⚠️

The **data** to answer impact questions exists. The **relationships** do not.

| Event | Data available? | Easy to query today? |
|-------|-----------------|----------------------|
| Part delayed → impacted jobs | ✅ `parts_control`→`build_assembly_pack_gid`→`build_assembly_packs`→`project_name` | ⚠️ joins exist but as loose `asana_task_gid` string columns, no FKs |
| Drawing revised → affected BOMs | ✅ `build_assembly_packs.url_to_drawing` + `bom_requirements.source_bom_file` | ⚠️ connection is via text, not a table |
| Delivery change → affected tasks | ⚠️ `purchase_orders.source_email_id`, `supplier_deliveries.po_id` | ⚠️ partial |
| Automated recommend + approve | ❌ no approval tables | ❌ |

**Root problem:** FK enforcement is **OFF** (SQLite default) and only **5 FK declarations exist**. The important cross-entity links (part→pack→project→task→PO) are `asana_task_gid` / text columns with no formal, indexed relationship. Joinable by convention, but not enforceable or self-describing. Relationships like "part delayed → job impacted" require string matching across tables rather than a graph traversal.

**Immediate additions that unlock event-driven operations:**
- Turn on `PRAGMA foreign_keys=ON` and backfill the 5 declared FKs (bom_requirements.po_id, po_lines, supplier_deliveries).
- **`project` table** (the missing root entity) — projects are currently just text (`project_name`, `source_project_gid` scattered across 4 tables).
- **`part_job_link`** (part→pack→project explicit edges) — the single highest-value addition.
- Index the join columns that are already there (raw `asana_task_gid` on `qc_issues.parts_control_gid`, etc.).

---

## 5. Knowledge Graph Readiness — 4/10 ⚠️

**Entities present (as tables):** Projects (implicit, text-only), Build Packs (`build_assembly_packs`), Parts (`parts_control`, `bom_requirements`, `stock_inventory`), POs (`purchase_orders`, `po_lines`, `procurement_tasks`), Tasks (Asana side), Suppliers (`suppliers`), Drawings (`url_to_drawing` columns), QC (`qc_issues`).

**Engineers: MISSING.** No engineer entity at all — not as a table, not as a column. This is a hard blocker for any "which engineer's job is affected" analysis.

**Relationships: largely absent.** Dependency chains are encoded as:
- `parts_control.build_assembly_pack_gid` → `build_assembly_packs.asana_task_gid` (part→pack)
- `build_assembly_packs.source_project_gid` (pack→project)
- Text `source_bom_file` matching (`1588.BOM.*`) for drawing↔BOM links
- `ordering_queue.parts_control_task_gid`/`order_task_gid` (part↔order)

**Can dependency chains be traversed both ways today?** Roughly *forwards* (part→pack→project→order) via joins, but **not reliably backwards** (project→alphanumeric-BOM→drawing→cost) because the link is text matching, not nodes+edges. `propagator_audit_log` stores `relationship = dependent|subtask|subtask+dependent` — a promising foundation — but it's a flat log, not a traversable graph.

**Without the part→pack→project edge table, bidirectional traversal and true project impact analysis are not possible.**

**Recommendation:** Add an explicit **`relationships`/`edges` table** (source_type, source_id, relation, target_type, target_id) mirroring what `propagator_audit_log.relationship` already captures conceptually. This becomes the knowledge-graph backbone. (Note: the separate `SES_Workspace/_copilot` graphify graph covers *file/code* relationships, not *operational* entity relationships — they are complementary, not the same.)

---

## 6. Personalised Operational Workspaces — 3/10 🔴

"**My** projects / risks / delayed parts / open actions" is **not expressible today**, because nothing has an owner.

- No `owner_id` / `assignee_id` / `engineer_id` on `build_assembly_packs`, `projects`, `parts_control`, `qc_issues`, or `bom_requirements`.
- `giles_tasks` is the *only* person-scoped table (it's a personal task mirror) — and it's hard-coded to one user.
- Production/suppliers/QC have no human owner (only `received_by` on `supplier_deliveries` — a name string, unlinked).

**Required additions to support "my views":**
1. An `engineers` / `users` table (same one as §2).
2. An `owner_id` column on the responsibility-bearing tables: `build_assembly_packs`, `qc_issues`, `parts_control` (or a `assignments` join table for many-to-many).
3. A per-user view/portal generator that filters by `owner_id` — which the existing `os_status_refresh.py` / HTML portal pattern already makes easy once ownership exists.

---

## 7. Approval Engine Readiness — 4/10 ⚠️

**Today, approval is 100% human-and-file-based** (the REQUESTS→SAP protocol, a HITL review sheet, the BOM commit "go ahead"). This is *correct* for now but is not *modelled*.

- No `approvals` table, no `recommendations` table, no `decisions` table.
- `ordering_edge_cases` (13 cols) is the closest thing — it captures anomalies and links to Asana edge cases (`resolved` flag), but it's a *log of exceptions*, not an *approval engine*.

**To support recommend→approve→execute with full audit:**
```sql
recommendations (id, created_at, recommended_by, action_type, payload_json,
                 status[pending/approved/rejected], confidence, reason)
approval_decisions (id, recommendation_id FK, decided_by, decision, decided_at, comment)
```
Then approvals map onto the existing write-log execution: after approval, the executor writes with `approved_by = <decider>`.

**Good news:** the guardrail philosophy (HITL for destructive ops, REQUESTS protocol, `asana_write_log` + `db_write_log`) is exactly the right *culture*. The upgrade is turning that process into *data*, so it can be reported on ("what did I approve last week?") and auto-routed.

---

## 8. Future Cloud/VPS Deployment — 6/10 ⚠️

**Bottleneck: single-file SQLite.** `ops.db` is already **920 MB** (915,597,056 bytes) with `db_write_log` at **2.89M rows**. SQLite is brilliant single-user but:

- **Concurrency:** SQLite locks the whole file on writes — multiple concurrent users write = waits/lock contention.
- **No authz layer:** file-level only; ANY process on the host can read/write the whole DB. In the cloud, that's your biggest permission risk (§2's missing identity layer becomes a *security* problem, not just a reporting problem).
- **Blob of a monolith:** the enormous `db_write_log` lives in the same file as the operational data — backups, VACUUM, and reads slow as it grows.
- **Deployment coupling:** paths are baked into skills/config on a single machine; `config.yaml` and `.env` are local-file-bound.

**What *is* cloud-ready:** the SQLite schema itself (SQL is SQL), the REQUESTS protocol, the skill/context separation, and the portal static-HTML pattern.

**Recommendations:**
- **Near-term (still on SQLite):** enable WAL mode (`PRAGMA journal_mode=WAL`) + `PRAGMA foreign_keys=ON`; move write-log tables out of the hot DB or archive old rows; add an `owner/dbn` field for row-level ownership; put the DB behind one service/API if multi-process.
- **Long-term:** the cleanest step is not a rewrite — it's migrating `ops.db` to **PostgreSQL** when you move to a real VPS. The schema and query logic port nearly as-is (SQLite→Postgres is low-friction); you gain real row-level authz, concurrency, and backups. Keep `ops.db` as the dev/offline copy.

---

## What is already well-structured (protect these)

1. **System-of-record separation** — Asana/DB/Telegram/Portal/Hermes division is sound and matches the target state.
2. **SAP sole Asana writer** + `asana_write_log` — clean single-mutation-point audit.
3. **SQLite unified read layer** — removes fragile agent chains.
4. **REQUESTS/ file protocol** + HITL guardrails — correct approval culture, just needs to become data.
5. **Index discipline on hot lookup columns** — good indexes on `sesame_pn`, `po_number`, `vendor`, `kan_number`, `pack_reference`.
6. **`propagator_audit_log.relationship`** — an embryonic edge/relationship model to build on.
7. **Portal generator pattern** (`os_status_refresh.py` → static HTML) that personalisation can reuse.

---

## Key risks & weaknesses (ranked)

1. **🔴 No identity layer** (§2) — blocks multi-engineer, permissions, "who did what" audit, personalised views, and approval automation. *The single highest-priority gap.*
2. **🟠 Loose textual relationships, FK enforcement off** (§4/§5) — no project/edge tables, so impact analysis and event-driven queries require fragile string matching.
3. **🟠 No owner on work-bearing entities** (§6) — nothing is "mine" today.
4. **🟠 920 MB single-file monolith with 2.9M-row log table** (§8) — concurrency + backup + performance risk as users grow.
5. **🟡 Golden Index ambiguity** (CSV vs DB) — two candidate part-number sources of truth.
6. **🟡 Engineers not modelled at all** — can't attribute work to people.

---

## Immediate improvements (do first — days, not weeks)

1. **Turn on `PRAGMA foreign_keys=ON` and `journal_mode=WAL`** on ops.db — free correctness + concurrency gains.
2. **Backfill the 5 existing FK relationships** and add the two obvious ones (`build_assembly_packs.project_name` → packed from a real project id).
3. **Add `owner_id` / `engineer_id` nullable column** to `build_assembly_packs`, `qc_issues`, `parts_control` — no backfill required; start attributing new rows. *This is the fastest unlock for §6.*
4. **Validate an impact query today** — write one "part delayed → which jobs?" query using existing joins to prove the data layer answers it before building new structure.
5. **Archive/purge old `db_write_log` rows** to a cold table (keep 90 days hot) to stop the monolith growing.
6. **Resolve the Golden Index question** — decide CSV or DB as single source, document it.

---

## Medium-term architectural changes (weeks–months)

1. **Introduce a `users` (or `actors`) + `roles` + `permissions` table set** — the spine for requirements 2/3/6/7. Keep Telegram/enable-lists in config; model *business* identity in DB.
2. **Add a real `projects` table** and a **`part_job_link` / `edges` relationship table** (source→relation→target). This is what turns the DB into a traversable dependency graph (§4/§5).
3. **Add audit identity columns** (`requested_by`, `approved_by`, `executed_by`) to `asana_write_log` / `db_write_log` / new `approval_decisions`.
4. **Verify engineer + personalized-portal generator** — filter `build_giles_worklist.py` by owner → becomes `build_engineer_worklist.py`.
5. **Separate the write-log/audit tables into a distinct (possibly separate-file) audit database** to shrink the hot ops.db.

---

## Long-term target architecture (months+)

```
┌─────────────┐   Asana (work mgmt SoR)          ┌──────────────┐
│  HERMES     │ ───────────────────────────────► │ Authentication │
│ (orchestrate)│      SAP sole writer             │  users/roles  │
└─────┬───────┘                                  │  permissions  │
      │ REQUESTS/HITL                             └──────┬───────┘
      ▼                                                │ owner_id
┌─────────────┐   ┌────────────────────────────┐        │
│  Telegram   │◄──┤ Operational DB (Postgres)  │◄───────┘
│ (comm layer)│   │  projects                  │
└─────┬───────┘   │  parts + part_job edges    │    ┌──────────────────┐
      │           │  packs, POs, suppliers     │    │ Edge/relation    │
      ▼           │  users/roles/perms         │    │ graph (impact    │
┌─────────────┐   │  approvals/audit           │───►│  analysis)       │
│ HTML portals│   └────────────────────────────┘    └──────────────────┘
│ (view/act)  │   ▲ read path                        (drives §4 §5 §6)
└─────────────┘   │
                  └── PostgreSQL (was ops.db SQLite)
```

- **Orchestration:** Hermes (shared instance) + hybrid for external isolation.
- **Data:** PostgreSQL as the cloud SoR; `ops.db` SQLite as dev/offline replica.
- **Approvals:** DB-backed (recommendations → approval_decisions → execute), keeping the HITL guardrail culture.
- **Graph/impact:** explicit edges table feeding event-driven alerts + personalised worklists.

---

## Bottom line

**A major database redesign is NOT required.** The current structure can evolve incrementally. The tables you have are the right tables; what's missing is:

- a **thin identity layer** (users/roles/permissions),
- **explicit ownership + relationship edges** (owner_id + part_job_link/projects), and
- turning your **existing approval habit into data** (recommendations/approval_decisions).

None of those start from scratch — they're additive columns and a few small tables on top of a sound core. The fastest wins (WAL + FK on, owner_id columns, archive the log table) can land this week; the identity + edge layer pays off across every remaining requirement.

**Overall future-proof score: 5/10 — strong foundation, add identity + relationships to reach 8–9.**
