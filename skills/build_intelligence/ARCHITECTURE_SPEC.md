# 🏗️ Build Intelligence System — Architecture Spec (v0.1 draft)

**Status:** DRAFT — nothing built, no decisions locked
**Owner:** Giles Hugo
**Created:** 2026-07-17
**Location:** `SES_Workspace/skills/build_intelligence/` (separate from procurement)
**Related:** drawing_pipeline, build_assembly_packs (ops.db), Delay Destroyer project, Learning project

---

## 1. Purpose

Give each build engineer a personalised, per-project morning brief and end-of-day feedback loop, and give management (MD, Foreman, Giles) a live picture of build progress, hours spent vs estimate, and critical path — tuned to Sesame's actual workflow rather than a generic ChatGPT MCP view.

Competes with / improves on: ChatGPT MCP (Asana + Dropbox + Office 365 Graph) by being **optimised to ask the right questions** and **reliably obtain the correct parts per pack** regardless of task-structure variation.

---

## 2. Guiding Principles

1. **Engineer-first:** an engineer should be told "here's what you've got today, here's what parts are missing, here's what we learned on this before" — not expected to hunt through Asana.
2. **Robust to structure drift:** lead/pre-works/assembly/paint/electrics tasks get assigned to different people over time; the system must not break when the task tree changes.
3. **Human-confirmed scheduling:** MD/Foreman reassess "what's on tomorrow" — the morning brief reads their confirmation, not raw (often stale) Asana assignment.
4. **Feedback closes the loop:** end-of-day engineer check-in feeds back into Asana, captures hours, and feeds the schedule.
5. **Historical memory:** Delay Destroyer + Learning provide context so we don't repeat known problems.
6. **No fragile coupling:** source of truth boundaries respected; purchasing independent.

---

## 3. Core Concepts / Glossary

| Term | Meaning |
|---|---|
| **Build project** | A job, keyed by job number (e.g. `1588`, `1575`). Own build Telegram channel. |
| **Assembly pack** | A sub-assembly group under a project (pack_reference, e.g. `1575.6000.A.Stairs.`). |
| **Parts readiness** | Aggregate of part states within a pack (ready / ordered / missing / QC). |
| **Estimated hours** | Pre-set hours per pack — the performance baseline. |
| **Schedule confirmation** | MD/FM reassessing tomorrow's task list (state TBD — field or section). |
| **Check-in** | Engineer's end-of-day feedback (Done / Outstanding / hours). |
| **Delay Destroyer** | Asana project capturing delays by department: description → root cause → action; events. |
| **Learning** | Triggered from delays; currently purchasing-centric, future cross-department. |

---

## 4. Daily Workflow (target)

```
DAY BEFORE (evening)
  1. Build "tomorrow's tentative task list" per engineer (from Asana assignments/dates)
  2. NOTIFY MD + Foreman → they confirm/reassign which engineers work on what tomorrow
  3. Confirmation stored (capture mechanism TBD — see §6 open questions)

BUILD DAY (morning)
  4. Per-project build channel cron:
     - Read today's confirmed engineers + tasks
     - For each engineer: pull their packs for today
     - Compute parts readiness per pack (ready / ordered / missing / QC)
     - Attach historical insight from Delay Destroyer + Learning (if mapped)
     - NOTIFY engineer: "here's what's on today, here's parts missing, here's prior learnings"

END OF DAY (~16:00)
  5. NOTIFY engineer: "did everything get done, or is anything outstanding?"
  6. Engineer replies in their project channel
  7. Feedback captured → Asana comment on the task + hours attributed to project
  8. Update running schedule / performance vs estimate

ONGOING (management view)
  9. Visuals: hours used vs estimated, per pack / project
     - At target, over budget, under budget
  10. Critical-path analysis per project
  11. Incentives/motivation hooks from performance data
```

---

## 5. Skills & Components (proposed)

### Skill 1 — `pack_intelligence`
Query ops.db + Asana for a given pack → produce a clean parts-readiness breakdown.
- Reads `build_assembly_packs`, `parts_control`, `stock_inventory`
- Computes per-part state and pack aggregate
- **Depends on:** pack insight fields being populated (currently empty — see §7 blockers)
- **Build order:** FIRST — foundation for everything else

### Skill 2 — `schedule_confirmation`
Notify MD + Foreman with tomorrow's tentative list; capture confirmation.
- **Open decisions:** field vs section capture, who sees what
- **Dependency:** MD/Foreman workflow preference (deferred)

### Skill 3 — `morning_brief`
Per-project Telegram channel cron → engineer briefs.
- Reads confirmed schedule + pack_intelligence + historical insight
- **Depends on:** schedule_confirmation, pack_intelligence, historical mapping

### Skill 4 — `engineer_check_in`
End-of-day (16:00) notification → engineer feedback capture.
- Reply → Asana comment + hours attribution
- **Depends on:** feedback capture method (comment vs field), hours parsing

### Skill 5 — `hours_ledger`
Track hours per engineer / project / pack from check-ins.
- Store to ops.db (hours_ledger table TBD)
- **Depends on:** check-in parsing

### Skill 6 — `performance_visuals`
Estimated vs actual hours per pack/project; overdue/on-target; critical path.
- **Depends on:** estimated hours seeded, hours_ledger populated

### Skill 7 — `build_reporter` (optional wrapper)
Aggregate above into a single build-overview report / page.

### Supporting — `historical_context` (PARKED)
Map Delay Destroyer + Learning entries to part/pack context.
- **Explicitly parked:** Giles to provide the solicitation model for how to map historical feedback accurately to part context.

---

## 6. Known Variables & Open Decisions

| # | Question | Why it matters | Status |
|---|---|---|---|
| 1 | How is "confirmed for tomorrow" captured? (field vs section) | Morning brief reads this; must match MD/FM preference | OPEN — defer to MD/FM |
| 2 | Who is notified, and in which channel/manner? | Per-host preference | OPEN — defer to MD/FM |
| 3 | How are hours derived from check-in? | Free-text parse vs structured prompt | OPEN during build |
| 4 | Where does "engineer works X today" actually live? | Source of truth for morning brief | OPEN |
| 5 | Estimated-hours baseline per pack — who sets it? | Foundation of performance visuals | OPEN |
| 6 | How to map Delay Destroyer → part/pack? | Historical insight in briefs | PARKED (Giles to define) |
| 7 | Learning project cross-department distribution | Beyond purchasing-only | PARKED |
| 8 | Grouping granularity confirm | Job number = build project/channel | ✅ confirmed |

---

## 7. Known Blockers / Data Gaps (must solve before/alongside)

1. **`build_assembly_packs` insight fields are empty** (2,480 rows; status/readiness/stage/ai all NULL). Parts relationships exist but pack-level status is not populated. pack_intelligence depends on this.
2. **`stock_inventory.stock_location` empty** (119 rows, 0 location).
3. **Parts readiness** relies on `parts_control.stock_action` (populated) + `stock_inventory` (quantities present) — need the join to produce a reliable per-pack readiness.
4. **Delay Destroyer + Learning** have not been mapped to part/pack context yet.

---

## 8. Data Sources & Systems of Record

| Data | System | Notes |
|---|---|---|
| Build packs | `ops.db.build_assembly_packs` (+ Asana) | needs field population |
| Parts + stock action | `ops.db.parts_control` | pending order info |
| Stock quantities | `ops.db.stock_inventory` | read-only mirror of Asana |
| Task assignment/dates | Asana | unreliable for scheduling (open until complete) |
| Delays | Asana "Delay Destroyer" | by department; description/root cause/action |
| Learnings | Asana "Learning" project | currently purchasing-centric |
| Hours | TBD `ops.db.hours_ledger` | to be created by hours_ledger skill |

---

## 9. Recommended Build Order

1. **`pack_intelligence`** — prove it works against one live pack (dry-run, no writes). Foundation.
2. **Populate pack insight fields** addressing §7 blocker — the Golden Index/field-propagation work.
3. **`hours_ledger` + `engineer_check_in`** — close the feedback loop and hours capture.
4. **`schedule_confirmation`** — only after MD/FM workflow decision.
5. **`morning_brief`** — assemble confirmed schedule + pack intelligence + (eventual) historical context.
6. **`performance_visuals` + critical path** — once hours data accrues.
7. **`historical_context`** — once Delay Destroyer solicitation model is provided (parked).

---

## 10. Guardrails

- **Nothing built yet.** This is a spec, not an implementation.
- No notification/receiver decisions made — defer to MD/FM.
- Historical feedback mapping (Delay Destroyer → parts) is parked pending Giles's solicitation model.
- All new build-intelligence code lives under `SES_Workspace/skills/build_intelligence/` (or sibling), separate from procurement.
- ops.db writes go via db_writer; Asana writes via REQUESTS protocol.
