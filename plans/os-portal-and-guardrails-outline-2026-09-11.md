# OS Portal Hosting + External-Write Guardrails — Outlined Approach

**Staged:** 2026-09-11 · **Owner:** Giles + Hermes · **Status:** OUTLINE FOR MONDAY REVIEW — no deploy executed, no Asana task created yet
**Companion:** `plans/os-portal-hosting-plan-2026-09-11.md` (technical recipe) · `docs/` OS-portal design docs

> This is the *approach* document Giles asked for **before** undertaking the work. It outlines
> four threads: (A) capture the insight in local reports, (B) an AI Dev Work task for Monday,
> (C) the YAML + deploy approach, (D) two new guardrails to impose. Nothing here is executed.

---

## THREAD A — Capture the insight in local reports

**Where reports live:** `SES_Workspace/reports/` (audit/summary reports) and `SES_Workspace/docs/`
(design/register docs). Both already hold OS-portal and webhook material.

**Approach:**
1. Write **`SES_Workspace/reports/os-portal-hosting-assessment-2026-09-11.md`** — the full insight:
   - Verified VPS architecture (Traefik front door, hooks.sesameaccess.com receiver v2, Cloudflare DNS).
   - The hosting design (portal.yml + static server) + Cloudflare Access IP/OTP policy.
   - The live-ness decision (see Thread C) and the "portal ↔ webhook talk directly" item.
   - Evidence: what was read, what was NOT changed.
2. Cross-link it from the existing `plans/os-portal-hosting-plan-2026-09-11.md` (technical recipe)
   so there is ONE narrative: assessment report (why) + plan (how).
3. Do **not** duplicate into `docs/` — link to it instead, to avoid a second source of truth.

**Guardrail:** reports are local-workspace artefacts. No VPS writes. No SES_Brain writes (see D2).

---

## THREAD B — AI Dev Work task for Monday

**Destination:** Giles Ai Dev Work project `1147424298774210`, section **🔌 VPS / Webhooks** `1216357087488035`
(the task is a VPS-hosting/deployment item). Fields: Area=VPS/Webhooks, Priority=High, Status=Not Started,
Effort=M. Assigned to Giles.

**Approach:**
1. Draft the task body (scope = deploy the portal.yml + static server + Cloudflare Access; decide hostname;
   resolve the live-ness question; wiring portal↔webhook).
2. Route as an **Asana write via REQUESTS** (guardrails) OR create directly on Giles's explicit "yes" this
   session (he asked for it — but the destination is unambiguous, so confirm GID then create).
3. Reference `plans/os-portal-hosting-plan-2026-09-11.md` + the Thread A report in the notes.
4. Do NOT create it in the SAS Edge Cases intake — this is a **dev/process-build** task, which is the
   Ai Dev Work pattern (per guardrails exception: dev/process-change items → Ai Dev Work).

**Deliverable:** one task "Deploy SES OS Portal to VPS (Traefik route + Cloudflare Access) — portal.yml staged".

---

## THREAD C — YAML + deploy approach (DECISION: run local, push to host)

**Giles's decision (captured):** *Run the portal generation LOCALLY and PUSH updates to the hosted server.*
**Do NOT** run the portal-refresh as a cron on the VPS. Rationale: all source data is the **local** `ops.db`;
a VPS cron would fork the source of truth and cause confusion.

**Approach (staged, execute Monday):**
1. **Local build (unchanged):** the existing local cron jobs regenerate `data/os_status/*.html` from local `ops.db`.
   Locally, add a curated staging step: copy the 7 current dashboards into `data/os_portal/` (no dated archives,
   no `.hier_cache.json`) — see `data/os_portal_preview_20260911/` for the cur(sated shape proven today.
2. **Push step (new):** a small local script pushes `data/os_portal/` → the VPS serve dir (rsync/scp over the
   existing key). Runs on the same local schedule AFTER the refresh jobs. Idempotent.
3. **VPS serve step (new, ONE-TIME setup):** static server (nginx/caddy container, host-networked, loopback)
   serving the pushed dir + a `portal.yml` Traefik route. Hot-reload, no gateway restart.
4. **Cloudflare:** `os.sesameaccess.com` → VPS (orange-cloud) + Access policies (IP-allowlist office + email-OTP Giles).
5. **Verification:** fetch the public hostname, confirm 200 + Access challenge; confirm push is idempotent;
   confirm a data change locally appears on the host after one push cycle.

**Key property:** the VPS is a **dumb target** — it receives pushed HTML and serves it. It never generates
portal content. This keeps the local DB as the single source of truth (Giles's stated concern).

**Staging deliverable:** the `portal.yml` + push script + setup runbook written and reviewed, but NOT executed
until Monday's go-ahead.

---

## THREAD D — New guardrails

### D1 — External-write identification (VPS / non-local paths)

**Problem Giles raised:** any access to the VPS via SSH needs to be **clearly identified**, so we never write
to the wrong database. Default assumption must be **LOCAL**; writes external to the local workspace must be
explicitly flagged.

**Approach (outline):**
1. **Extend `PATH_CONFIG.yaml` with an explicit `external:` section** (currently only `dropbox:` read-only
   exists; VPS targets are implicit). Proposed keys:
   ```yaml
   external:
     vps_host_alias: "vps"          # ssh alias, not the raw IP
     vps_workspace:  "/opt/ses/workspace"      # may contain a DIFFERENT ops.db
     vps_db:         "/opt/ses/workspace/db/ops.db"
     vps_portal_dir: "/opt/ses/portal"
     write_policy:   "push-only"    # local is source of truth; VPS receives pushes
   ```
2. **Write-guard rule (goes in CLAUDE.md + a skill):** any script/agent that writes to a path NOT under
   `workspace_root` MUST (a) log a banner identifying the target as EXTERNAL + the remote host, and (b) name
   the target DB explicitly. No silent cross-boundary writes.
3. **DB-of-record banner:** any DB write prints `WRITE TARGET: <path> (LOCAL|EXTERNAL:<host>)` before the write.
4. **Audit:** a lint/grep check that flags scripts referencing `vps`/`/opt/ses`/`scp`/`rsync` path writes
   without the external marker.

**Why it matters:** the VPS has its own `/opt/ses/workspace/db/ops.db`. A path bug could silently write the
wrong DB. The guardrail makes "local" the default and makes "external" loud.

### D2 — SES_Brain write guardrail

**Problem Giles raised:** attention given to SES_Brain recently; possibly files saved there; needs a guardrail.

**Verified current state:** SES_Brain write rule already exists (CLAUDE.md): **read anywhere; write ONLY to
`raw\` and `projects\[name]\`**; `knowledge\ systems\ agents\ capsules\` are human-promoted only.
The only recent write found = `SES_Brain/raw/2026-09-07_control_panel_drawing_house_rules_DRAFT.md`
— i.e. it landed in the **permitted** `raw\` zone. So no rule was broken, but the rule needs to be
**enforced/tested**, not just stated.

**Approach (outline):**
1. **Codify** the SES_Brain write rule into `PATH_CONFIG.yaml` (a `ses_brain:` policy block: writable dirs =
   `raw`, `projects/*`; everything else read-only) so scripts can check programmatically.
2. **Pre-write check:** any agent/script about to write under `ses_brain` validates the path is inside the
   allowed set, else refuses + logs to Edge Cases.
3. **Audit pass:** list all files written to SES_Brain in the last 30 days; confirm each is in `raw/` or
   `projects/`; flag any that aren't for Giles to promote-or-remove.
4. **Distinguish the two brains clearly** (also in memory): `SES_Brain` (personal Obsidian vault, local) vs
   `SES_Brain_POC` (company, on OneDrive). Both are read-mostly; both restrict writes.

---

## Open decisions for Monday
1. Hostname: `os.sesameaccess.com` vs `portal.sesameaccess.com` vs `ops.`?
2. Push cadence: after each refresh run, or on a fixed interval?
3. Cloudflare Access: confirm office IP range + Giles email for the OTP fallback policy.
4. Which 7 dashboards are the portal (vs including dated snapshots)?
5. Do the AI Dev Work task now (this session) or stage the draft only?

## Explicitly NOT done (pending Giles's go-ahead after this outline)
- No VPS writes, no Traefik route added, no Cloudflare change.
- No deploy executed; YAML/scripts staged as drafts only.
- No Asana task created yet (Thread B is drafted, awaiting "create it").
