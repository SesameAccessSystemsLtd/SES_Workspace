# Scoping Mission — "Chat to the Truth": Simplifying Asana + an LLM Interface to a Source Repository

**Owner:** Giles Hugo (Purchasing Manager) · **Sponsor/steer:** Steve (SIS)
**Status:** SCOPING — discovery & diligence. No build commitment.
**Date:** 2026-09-14
**Companion context:** SIS Development Programme (`personal-assistant/references/sis-development-programme.md`), SAS OS Portal (`os-portal` skill).

---

## 1. The goal (restated)

Two linked aims:

1. **Simplicity of interface.** Understand how Asana is *actually* used across the business, and **trim out** projects/structure that don't earn their keep. One clean surface per person.
2. **Ask-the-truth.** The ability for a person to **chat to an LLM and get live, current, accurate data and context** from a **source repository** — not a stale export, not a summary, not a guess.

Working model from the Steve discussion: **one task per person per day** as the daily attention view — easy for the workshop (physical, visible work), hard for admin/management (defining what is *significant*). Steve steers; each person's tasks/attention are focused through their own chat.

---

## 2. Ground truth — what's actually in Asana today (measured 2026-09-14)

| Metric | Count |
|---|---|
| Total projects (workspace) | **869** |
| Archived | 248 |
| **Active** | **621** |
| — job-numbered boards (name starts with a job no.) | **264** |
| — control / system / named projects | **357** |

Observations:
- **~1 project per job** is the dominant pattern (264 job boards for however many live jobs) — most are **per-job, one-and-done**, and go dormant once the job ships. This is a *volume* problem, not a usage problem.
- 21+ job boards untouched since before 2026; many control projects sit at `modified 2026-02-25` (a bulk workspace event — so **`modified_at` alone is NOT a usage signal**; we must measure real task activity — see §6).
- Obvious cruft in the active list: `TESTING Order numbers`, `testing Zapier`, `FLOWSANA TEST - auto update`, `Flowsana Test - duration`, `BETA TESTING ASANA`, `NYC Marathon`, `Old Marketing Task`, `Crowd Funding`, `Dubai Big 5 Expo 25-28 Nov 19` (2019!).
- The **true operational core** is small and named: Order Numbers, Project Orders (1.3), Picking/Order control (1.1), Goods in (1.2), Purchasing Dept (1.4), Build Schedule (3.1), QC issues (1.0), Drawing Status (2.0), Parts Control, plus a handful of control registers (ECR, NCR, Suppliers, ISO, Holiday, HR).

**So the trim thesis is real:** the *overhead is the long tail of dead per-job boards and one-off test/side projects*, not the ~12 core operational projects everyone actually lives in.

---

## 3. The four distinct problems hiding inside "simplify Asana"

Don't let these blur — each needs its own answer:

1. **Structural sprawl** — hundreds of dormant job boards. (Archive/roll-up problem.)
2. **Semantic duplication** — the same fact living in several places (e.g. a part's status in a Parts Control task, in ops.db, in an order task, in a supplier email). (Single-source-of-truth problem.)
3. **Significance** — surfacing the *right* thing to the *right* person (the admin/management "what matters today" problem).
4. **Access/interface** — how a person asks a question and gets a trusted answer (the LLM-interface problem).

---

## 4. Pitfalls & considerations — the "chat to an LLM for live truth" idea

### 4.1 Data / correctness
- **"Live" is a claim you must be able to prove.** If the answer comes from a nightly mirror, it can be 24h stale — and the ops portal already proved a stale mirror can be *wrong by ~10×* (steel-profile column). Every answer must carry **provenance + as-of timestamp** ("live Asana", "cached 01:00", "email, unverified").
- **Source of record is unresolved.** Today Asana is the operational truth (stock qty, tasks, part fields); ops.db is a mirror. SIS P004 says **SharePoint = permanent source, Asana = adopted operational view**. Until that's decided, the LLM has *two* candidate truths and will contradict itself.
- **Identity.** One lift / part / person must resolve to ONE id everywhere. Sesame already has known collisions (part-number variants under a shared core number; the "333 vs 571" ram; duplicated Asana task GIDs). The LLM will confidently merge or split the wrong things.
- **Ambiguity & units.** "Outstanding", "delivered", "in stock", "on order" mean different things in different projects/tables. `order_status` vs `stock_action` is already a documented trap. The interface must fix a **canonical vocabulary** or it will answer the literal question, not the real one.
- **Permissions & sensitivity.** The repository contains HR, finance, contracts, personal email. An LLM with "access to everything" (Steve's ChatGPT) is a **data-governance decision**, not just a technical one — needs scoping of who can ask what, and auditability of what was read.
- **Hallucination on gaps.** The dangerous failure is not "I don't know" — it's a **plausible wrong number**. Requires a **refusal/uncertainty protocol**: if the source doesn't have it, say so, don't infer.

### 4.2 Trust / adoption
- **Trust is earned by falsifiability.** People adopt a tool that is *right* and *checkable*. Every answer must **click through to the underlying task/record** so anyone can verify — the portal already established this as non-negotiable.
- **The "everything interface" temptation.** The business ChatGPT account "has all the connectors" — but the exact risk Giles flagged is **becoming more complicated**. A broader tool that exposes everything equally re-creates the noise problem at the interface layer.
- **Admin/management significance is genuinely hard** — the one-task-per-day model needs a *significance definition* per role (what is "must know" vs "nice to know"). That's a policy conversation with Steve, not a build.

### 4.3 Interface choice (ChatGPT business vs Cowork vs current)
| Option | Strength | Risk |
|---|---|---|
| **ChatGPT business** | Moving fast; connectors; build ability; already where Steve's context lives | Consolidation risk (becomes "everything"); provider lock-in vs SIS P001 (provider-independent) |
| **Cowork** | Full GPT access; less complicated than current product | Maturity; same SOR/identity questions underneath |
| **Current product (Asana)** | Native, already adopted for ops | Sprawl, no cross-project reasoning, no natural-language truth |

**Key principle (SIS P001):** the *interface* must be replaceable without redesigning the *data layer*. Whatever we pick now, the answer should come from a **provider-independent data/reasoning contract**, not from one vendor's connector.

### 4.4 Hidden costs
- **Trim is not free** — archiving/merging projects breaks existing automations, dashboards, and links. Every cron/portal/script that reads a project GID is a dependency.
- **Governance creep** — an interface that can *ask* soon wants to *act* (update a task, send an email). Keep read-only until the write/HITL path is proven (the ops portal already models this).

---

## 5. The core architectural question (the fork in the road)

> Does purchasing ops move to **ops.db (or SharePoint) as operational source-of-record**, with **Asana demoted to the human UI layer** and SIS as the permanent record?
> *or*
> Do we **keep Asana as SOR** and accept its fragility/sprawl, layering an LLM interface on top?

This is the single decision that shapes everything else. It is a **Steve-level steer** (matches SIS P004). My recommendation: **run discovery without pre-deciding it**, but design the answer contract so either outcome is a swap, not a rewrite.

---

## 6. A good process for discovery & diligence

**Phase 0 — Frame the questions (½ day, with Steve).**
Agree the decision the scoping must enable: *interface choice* + *whether SOR moves*. Write the success statement ("a person asks X and reliably gets Y in <Z seconds, verifiable").

**Phase 1 — Measure real usage, not modified dates (read-only, ~1–2 days).**
- For every active project: **task count, last task activity, active members, dependency map** (which crons/scripts/portals read its GID).
- Classify projects: **Core ops** (keep) · **Live job boards** (keep-while-live, archive-on-ship) · **Registers** (keep) · **Dormant/sprawl** (archive) · **Test/cruft** (delete).
- Output: a **usage evidence table** + a proposed **keep / archive / delete** list. *(This is where the "trim" becomes defensible — evidence, not opinion.)*

**Phase 2 — Map the sources of record (read-only, ~2–3 days).**
- For each *type of fact* (part status, order/delivery, stock, project progress, service), identify **every** place it lives and which wins.
- Produce an **authority table** ("fact → canonical source → update cadence → staleness tolerance").
- Surface the identity/vocabulary gaps (§4.1) as an edge-case register.

**Phase 3 — Define the answer contract (design, ~1–2 days).**
- Spec the response shape every LLM answer must return: **value, source, as-of timestamp, confidence, click-through link, "unknown" path**.
- Pick 5–10 **real questions** (e.g. "what's outstanding on Langley and who's blocking it", "when does the 1595 steel land", "what needs Steve's decision this week") and write **gold answers** — the acceptance test.

**Phase 4 — Prototype on ONE question, ONE interface (build, timeboxed).**
- Wire **one** question end-to-end against the live source (the ops-portal stack already proves read-only live Asana→HTML).
- Prove/lose the "live + accurate + verifiable" claim before broadening. Do **not** build the trim or the multi-interface layer yet.

**Phase 5 — Diligence & decision (with Steve).**
- Run the gold-question set against the prototype; score accuracy, freshness, verifiability.
- Present: interface options trade-table (§4.3), SOR recommendation (§5), trim list (§6.1), governance/permissions scope.
- Decide: interface, SOR direction, trim plan. Only then commit to build.

**Phase 6 — Execute in slices.** Trim in safe batches (archive-first, never delete without the two-step protocol); expand the question set; keep every answer click-through-verifiable.

---

## 7. Open decisions for Steve / Giles

1. **Scope of this mission** — is it (a) Asana-simplification, (b) the LLM interface, or (c) both? *(doc assumes both, sequenced.)*
2. **Source-of-record direction** — Asana stays, or move to ops.db/SharePoint (SIS P004)? *(§5)*
3. **Interface bet** — ChatGPT business vs Cowork vs current — and are we willing to keep it **provider-independent** (P001)? *(§4.3)*
4. **Governance/access** — who can ask what; read-only vs write; audit requirements? *(§4.1)*
5. **Significance definition** — for admin/management, what is "must know today"? *(§4.2)*
6. **Trim appetite** — archive-only vs delete; who signs off? *(§6.1)*

---

## 8. Recommended next action

Run **Phase 1 (usage measurement, read-only)** now — it's the cheapest step and turns "Asana is a mess" into a defensible, sized trim list we can put in front of Steve. It needs no decisions from anyone and no writes. Everything else follows from it.

*Evidence base for this doc: live Asana workspace pull 2026-09-14 (869 projects / 621 active / 248 archived); SIS principles P001/P004/P009/P010/P012; ops-portal stale-mirror incident (2026-09-12).*
