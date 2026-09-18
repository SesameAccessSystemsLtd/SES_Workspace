# PLAN-2026-08-18 - Cloud Orchestration Build-Out (webhooks + Asana) & Agent Self-Alignment

Status: UNCOMMITTED - plan for review by Giles
Author: Hermes (local), grounded in VPS state verified 2026-08-18 via SSH
Scope: Turn the live VPS Hermes agent into a slick, self-orienting, independently-monitoring
orchestrator that handles inbound webhooks, drives Asana updates (guardrail-compliant), keeps its
own local knowledge graph fresh, and manages memory effectively.

====================================================================
0. CURRENT STATE (verified today, not assumed)
====================================================================
LOCAL (this machine, C:\Users\GilesHugo)
- graphify INSTALLED (Python 3.13) and graph BUILT over the whole workspace:
    SES_Workspace/graphify-out/graph.json (2,805 nodes / 3,905 links), graph.html, GRAPH_REPORT.md,
    manifest.json (ready for incremental --update), cost.json. Scan root = SES_Workspace.
- Hermes runs native Windows; live ops.db at SES_Workspace/db/ops.db (~970MB). Local is today's working DB.
- Workspace IS a git repo (SES_Workspace/.git) but NO remote configured.

CLOUD (VPS srv1903976 = 191.215.40.116, ssh alias `vps`, Ubuntu 24.04, KVM, 8GB)
- Hermes Agent in Docker (container hermes-gateway, --restart unless-stopped), profile at /opt/ses/.hermes.
- Model deepseek (deepseek-chat). Telegram bot @SES_Production_bot, paired to Giles (8551261362).
- Workspace copy at /opt/ses/workspace - has plans/, memory/, skills/, db/, scripts/.
- ops.db shadow copy on VPS = 969.8MB, integrity confirmed. THIS IS THE OPEN DECISION (see sec 7, DECISION 1).
- SKILLS present: graphify skill copied, but graphify TOOL NOT INSTALLED and NO graphify-out/ on VPS.
- .hermes/memories = MEMORY.md (2.2KB) + USER.md (1.3KB). .hermes/plans is EMPTY.
- Cron on VPS = only 2 jobs (local has 22) - migration gap.

GAP vs "build out that manages webhooks and asana updates":
- NO inbound webhook receiver anywhere (local listeners bom_dispatch_server/qa_server are outbound HITL only).
- Asana write path = REQUESTS -> SAP (SAP now retired) - the cloud agent must write Asana THROUGH the
  HITL/REQUESTS gate, not directly (guardrail). Verified the write-guardrails skill is on the VPS.
- graphify knowledge not available to the cloud agent (not installed there).
- No self-alignment/onboarding file exists on the VPS (plans/ empty on .hermes, workspace/plans stale).

====================================================================
1. THE MANDATE (from Giles, 2026-08-18)
====================================================================
A telegram-reachable OPERATIONS agent on the cloud that will:
  a) handle & assess & align itself (read a stable alignment file each session);
  b) build local knowledge and know where things are saved (= graphify installed + updated on the VPS);
  c) manage memory effectively (lean startup, curated long-term, tight token budget);
  d) become slick at processing requests (fast, low-noise, right tool first time);
  e) monitor and act independently on the cloud (crons + webhooks + guardrail-compliant Asana writes).

====================================================================
2. THE DELIVERABLE BUILD (what actually gets built)
====================================================================
Phase A - Agent Self-Alignment (the "location to handle/assess/align itself")
  A1. Create AGENT_ALIGNMENT.md - the stable file the Telegram agent loads to orient. Primary copy:
        LOCAL:   SES_Workspace/plans/AGENT_ALIGNMENT.md
        CLOUD:   /opt/ses/workspace/plans/AGENT_ALIGNMENT.md   (synced)
      Contents: WHO the user is (brief), the current plan pointer, canonical key paths (local + VPS),
      memory protocol, write guardrails (SAP/REQUESTS/PO/hard restrictions), self-check checklist,
      "where things are saved" map, and the 3 daily questions (assess/align).
  A2. Wire it into the Telegram session so it loads automatically at message ingest:
        - Add a channel_prompt entry for the SESAME group / Purchasing topic instructing: "On session
          start, read /opt/ses/workspace/plans/AGENT_ALIGNMENT.md and orient before answering."
        - Set via: hermes config set 'telegram.channel_prompts.<thread_id>' "...read AGENT_ALIGNMENT.md..."
        - Verify the entry landed in VPS config.yaml; then restart the gateway (from a shell OUTSIDE
          the gateway - `docker restart hermes-gateway` on the VPS host works, not inside the container).
  A3. Copy AGENT_ALIGNMENT.md to the VPS via scp (or the clean-push git flow).

Phase B - Graphify local knowledge on the cloud  ("know where things are saved")
  B1. Install graphify into the VPS python env the Hermes container uses (venv or container python);
      on this Windows host it lives in Python 3.13 site-packages. VPS: pip install graphifyy.
  B2. Build the graph over /opt/ses/workspace (AST-only first; doc semantics via host-agent subagents
      per the graphify skill's large-corpus recipe - this is a HEADLESS path, so use the skill's
      cron/headless guidance; set GEMINI_API_KEY on the VPS for semantic extraction or accept AST-only).
        VPS: <python> -m graphify pipeline on /opt/ses/workspace  -> /opt/ses/workspace/graphify-out/
  B3. Add a cron on the VPS to rebuild incrementally (`--update`) on a schedule (e.g. daily 00:30),
      so the cloud agent's knowledge stays current without manual attention.
  B4. Confirm the cloud agent can query it: run graphify query / NetworkX traversal on VPS graph.json.

Phase C - Webhook receiver + Asana orchestration on the cloud  (the "online build out")
  C1. Stand up a public webhook endpoint on the VPS. SOURCE OF EXPERT PATTERNS (not a hard dependency):
      github.com/hookdeck/webhook-skills (MIT, active) - skills/asana-webhooks (X-Hook-Secret handshake,
      X-Hook-Signature HMAC-SHA256 verify, batched compact events + heartbeats, event actions
      added/changed/removed/deleted/undeleted), webhook-handler-patterns (verify->parse->handle
      idempotently, retry/backoff, DLQ, return codes), hookdeck-event-gateway (guaranteed delivery,
      replay, rate-limit, observability). Local pattern to reuse: bom_dispatch_server.py / qa_server.py
      (outbound HITL listeners) but those are NOT inbound webhooks - new receiver needed.
  C2. Webhook infrastructure (pick in sec 7 DECISION 2):
        - VPS-exposed endpoint (uvicorn/gunicorn + FastAPI or the Hermes http listener) + reverse proxy;
          firewall currently = 22 only, so open 80/443 (or a dedicated port) for the webhook path.
        - Hosted gateway (Hookdeck) for retries/replay/observability without opening the VPS.
  C3. Event -> Asana update path, GUARDRAIL-COMPLIANT ONLY:
        - Webhook event -> validate -> map to an Asana action PROPOSAL -> REQUESTS-style queue
          (NOT a direct write; SAP writer retired, so the review surface is the OS portal HITL).
        - Do NOT build a second direct Asana writer. Use REQUESTS/.md + JSON + executor that SAP
          consumed, now routed to the OS portal for accept/modify/reject.
        - PO numbers stay manual. Never "Needs Met" on active orders (use ADDRESSING). Idempotency
          requires an event-ID store (ops.db table or state file) - see webhook-handler-patterns.
  C4. Monitoring + independent action (overlaps Phase E): VPS crons for recurring checks
      (delivery-watch, supplier-email pipeline poll, BOM lifecycle, nightly audit) - reuse the local
      cron set (VPS currently has only 2 of the 22 local jobs; sync the remaining safe ones).

Phase D - Memory management hardening (slick + lean)
  D1. Keep the memory model: Hermes memory == culled durable facts only (preferences, environment
      quirks, conventions, corrections). Move workflows/procedures to SKILLS, not memory.
  D2. Daily ops log at SES_Workspace/memory/YYYY-MM-DD.md on local; mirror on VPS at /opt/ses/workspace/memory.
      Curate once a week: promote durable facts to MEMORY.md, drop the rest (it lives in daily logs).
  D3. Audit token cost on the VPS daily (graphify cost.json + cron msg volume). Alerts-only,
      no every-run reminders, per Giles's standing rule.

Phase E - Slick request processing & independent operation on the cloud
  E1. Skills-first dispatch: the cloud agent resolves EVERY request against its skill registry before
      generic reasoning (this is how local Hermes works; the VPS has the skills copied).
  E2. Right-tool-first: terminal (venv python) vs execute_code vs web - pick once, don't churn
      (local lessons in graphify Windows pitfalls + hermes-configuration mode-A/D). On Linux/VPS,
      terminal is native bash so Mode-D (WSL no-distro) does not apply.
  E3. Independent monitoring: define the first 3 autonomous crons on the VPS (watched without asking).
      Recommend: (1) daily graphify --update + knowledge-currency ping, (2) supplier-email /
      purchasing pipeline poll -> Telegram Purchasing topic alerts-only, (3) nightly ops audit
      (reuse Forge nightly audit as Hermes). Confirm each in sec 7 DECISION 3.
  E4. Verify exit/noise discipline: empty stdout == silent (watchdog pattern); errors ALWAYS surface
      with script name + exit code.

====================================================================
3. IMMEDIATE NEXT 3 ACTIONS (do these first)
====================================================================
  1. [DECISION] ops.db writer direction: VPS-authoritative vs local-authoritative (blocking for an
     independent cloud agent - see sec 7 DECISION 1). GILES MUST CHOOSE.
  2. Create AGENT_ALIGNMENT.md (this plan is the seed - the alignment file is the companion deliverable).
  3. Install graphify on the VPS + build the first graph (Phase B1/B2) - unblocks local knowledge.

====================================================================
4. REQUEST-INTENT MAPPING (what Giles asked -> what this plan does)
====================================================================
  "plan that needs to be done"               -> this document (sec 2 phases A-E, sec 3 ordering)
  "location for my telegram agent to handle,
   assess and align itself"                  -> AGENT_ALIGNMENT.md (local + VPS), auto-loaded
                                                via channel_prompt, sec 2 Phase A
  "install graphify so it builds local
   knowledge and knows where things are
   saved etc"                                -> already installed + built LOCALLY; to install/build
                                                on the VPS so the cloud agent has it, sec 2 Phase B
  "manage memory effectively"                -> sec 2 Phase D
  "become slick at processing requests"      -> sec 2 Phase E1/E2 (skills-first + right-tool-first)
  "build a highly responsive system that
   monitors and acts independently on the
   cloud"                                    -> sec 2 Phase C (webhooks+Asana) + Phase E3 (autonomous
                                                crons) on the VPS

====================================================================
5. PATHS OF RECORD (single source of truth per domain)
====================================================================
  Domain            | System of Record / path
  ------------------|--------------------------------------------------
  Operational DB    | local: SES_Workspace/db/ops.db (today's writer)
                     | VPS shadow: /opt/ses/workspace/db/ops.db
  CRM DB            | SES_Workspace/db/crm.db
  Tasks/projects    | Asana (via REQUESTS -> OS portal; SAP retired)
  Purchasing emails | ops.db email tables (VPS polls; see E3)
  BOM/MRP           | Golden Index Rev4 CSV + Asana (never hand-edit CSV)
  Knowledge (local) | C:\Users\GilesHugo\SES_Brain (company: OneDrive SES_Brain_POC)
  File index (graph)| LOCAL: SES_Workspace/graphify-out/graph.json (VERIFIED - NOT _copilot/graph)
                     | VPS:   /opt/ses/workspace/graphify-out/graph.json (to build)
  Agent alignment   | LOCAL + VPS: plans/AGENT_ALIGNMENT.md
  Keys/secrets      | LOCAL: C:\Users\GilesHugo\.Hermes\.env + AppData\Local\hermes\.env (Doppler-fed)
                     | VPS:   /opt/ses/.hermes/.env (Doppler vps_hermes/prd; never to git)

NOTE / CORRECTION: the SOP "file index" path _copilot\graph\graph.json does NOT exist; the real
graph is at SES_Workspace/graphify-out/graph.json. Do not trust the stale path.

====================================================================
6. GUARDRAILS THAT APPLY TO EVERYTHING ABOVE (hard, non-negotiable)
====================================================================
  - SAS Dropbox READ-ONLY (never write/delete/modify). No hardcoded paths.
  - Asana writes ONLY via REQUESTS -> review surface (SAP writer retired; use OS portal HITL).
    NEVER a direct second writer. PO numbers manual. Never "Needs Met" on active orders (ADDRESSING).
  - Max one agent hop (A->B only, never A->B->C). SQLite = read layer, check ops.db first.
  - Credentials never logged/echoed/in output. Never commit .env or secrets to git.
  - Destructive ops (task deletes, file deletes, commits) = HITL with explicit "go ahead" phrase.
  - Security/infra/credential work: educate-first + go-ahead before auto-acting.
  - Silent SUCCESS acceptable (no message); silent FAILURE never (always surface).
  - Model changes must not require workflow redesign; hardcoded-LLM scripts are debt to flag.

====================================================================
7. OPEN DECISIONS (need Giles) - DECISION 1 IS BLOCKING for independent cloud acting
====================================================================
  DECISION 1 [BLOCKING]  ops.db direction:
      A) VPS-authoritative (always-on cloud writes ops.db; local pushes summaries only)
      B) Local-authoritative (VPS = read-only shadow; local stays live writer, syncs to VPS)
      -> Determines where webhook events land and which DB the autonomous crons write.
  DECISION 2  Webhook hosting: open the VPS (port/reverse proxy) vs a hosted gateway (Hookdeck)
      vs stay localhost+HITL. Affects firewall + the URL Asana targets.
  DECISION 3  Which 3 autonomous crons to stand up FIRST on the VPS (sec E3). Confirm the list.
  DECISION 4  Graphify on the VPS: AST-only (free, no key) or semantic (needs a Gemini key in the
      VPS env for doc relations)? Local build used host-agent semantics.
  DECISION 5  Sync mechanism local <-> VPS workspace (git bare-remote push, rsync, or the clean-push
      flow already used). Affects how AGENT_ALIGNMENT.md + plans stay in sync.

====================================================================
9. DONE-WHEN LOOKS LIKE (verifiable)
====================================================================
  [ ] AGENT_ALIGNMENT.md exists local + VPS, and the Telegram session auto-loads it (a cold Telegram
      message from Giles triggers an alignment read before any answer).
  [ ] graphify installed on VPS; /opt/ses/workspace/graphify-out/graph.json exists; weekly --update cron.
  [ ] A webhook handshake completes against the VPS Asana integration (X-Hook-Secret echoed, stored);
      a real Asana event is received, signature-verified, and proposed to the OS portal for accept.
  [ ] No direct Asana write ever issues from the cloud agent (audit-log confirms all writes came
      through the review surface).
  [ ] 3 autonomous crons running on the VPS; silent-on-success, error-alert-on-fail verified.
  [ ] Memory: MEMORY.md stays under budget; daily logs exist; procedures live in skills not memory.
