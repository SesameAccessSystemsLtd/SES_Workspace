# Hermes + Workspace on a Hostinger VPS - Setup Plan (rev 2)

Status: Revised after Hostinger research + design chat - Giles, 2026-08-14
Scope: Copy instance of Hermes + the SES workspace on a Hostinger VPS. Hostinger's
own Hermes Agent template (a Docker project with Hermes pre-installed) is the base;
the SES workspace + any future services run as SEPARATE compose services on the SAME VPS.
The workspace repo is HARNESS-AGNOSTIC (not Hermes-coupled) so a different agent harness
can drive the same knowledge workspace later.

================================================================================
0. DECISION - USE HOSTINGER'S BUNDLED TEMPLATE, SET UP THE REST SEPARATELY
================================================================================

Hostinger now ships an official HERMES AGENT application/template. Verified from their
own pages (hermes-agent app page + "How to set up Hermes Agent" tutorial):
- It is a READY-TO-USE DOCKER PROJECT: the official `nousresearch/hermes-agent` image,
  pre-deployed via Hostinger's Docker Manager. When you buy it, Hostinger installs the
  container automatically; you get a web terminal + Docker Manager + free WEEKLY backups.
- There is NO separate "Hermes workspace" package from Hostinger. "Hermes workspace" is
  just their marketing term for the data dir you'd configure yourself. So the three things
  you saw are really ONE bundled Docker-Hermes + everything-you-add.

Recommended: ORDER THE HERMES AGENT TEMPLATE (KVM 2) and set up the workspace + future
services SEPARATELY as additional compose services ON THE SAME VPS. Reasons:
- The template is exactly "Docker with Hermes in it by default" (your assumption = correct).
- It does NOT lock you in: it's a Docker project you can extend on the same VPS with the
  workspace checkout, a future watcher, Postgres, etc.
- It bundles usable conveniences (one-click updates, free weekly backups, web terminal /
  Docker Manager) that cost nothing extra and save setup effort.
- You keep FULL root SSH + Docker control, so nothing about the workspace is coupled to it.

Pricing (the Hermes Agent template itself, from Hostinger app page):
  KVM 1: 1 vCPU / 4GB / 50GB     $6.49/mo intro, renews $11.99 (67% off, 2yr)
  KVM 2: 2 vCPU / 8GB / 100GB    $8.79/mo intro, renews $14.99 (64% off, 2yr)  <- RECOMMEND
  KVM 4: 4 vCPU / 16GB / 200GB   $12.99/mo intro, renews $28.99
  KVM 8: 8 vCPU / 32GB / 400GB   $25.99/mo intro, renews $49.99
All plans include Docker Manager, container logs, one-click updates, AMD EPYC, NVMe,
1Gbps, public API, free domain year 1.
NEW OPTION: Hostinger also sells a MANAGED "Hermes Agent" service (they handle server +
maintenance; you get less control). We do NOT want managed - you need root + Docker +
your own compose services against the same box. SELF-HOSTED via the VPS template.
NOTE: these intro prices require committing to a term (renews higher). "Cancel anytime"
applies; price shown is total/term-divided. Buy the shortest term you can for a pilot.

Sizing validation from Hostinger's own tutorial: "Aim for a VPS plan with at least 2 CPU
cores and 8 GB of RAM. The container uses ~1GB RAM, or 2-4GB with browser automation."
=> KVM 2 confirmed as the right primary plan.

================================================================================
1. TARGET ARCHITECTURE (harness-agnostic workspace on one VPS)
================================================================================

                    HERMES (local, dev/test)        <- you strip this back later
                          |  push / pull via github
                          v
    GITHUB (2 separated repos, each harness-agnostic where it matters)
   -----------------------------------------------------------------
    REPO A: hermes-profile   (config, SOUL, skills, cron, memory TEMPLATES)
        - describes the HERMES harness's identity/learnings (NOT the workspace)
        - seeds the Hermes container's ~/.hermes on the VPS
        - can be dropped into ANY Hermes install (local or VPS)
    REPO B: ses-workspace    (scripts, data, golden_index, queues, plans, docs)
        - NO Hermes-specific coupling: plain data + code + schema DDL
        - a DIFFERENT harness (OpenClaw, etc.) can drive the SAME checkout
    -----------------------------------------------------------------
           |                                                   |
           +-------------------+-------------------------------+
                               v
    HOSTINGER VPS (KVM 2, Hermes Agent template = Docker+Hermes pre-[installed)
                               |
   +---------------------------+------------------------------------+
   |  DOCKER on the VPS:                                            |
   |                                                               |
   |  Service 1: hermes-agent  (from Hostinger's Hermes template)  |
   |     - the agent; reads/writes its state in ~/.hermes          |
   |  Service 2: workspace-sync (or a mount) -> reads Repo B      |
   |  Service 3: future watcher / DB / Graph connector (sidecar)   |
   +---------------------------------------------------------------+

KEY RULE - HARNESS-AGNOSTIC WORKSPACE:
The ses-workspace repo must NOT assume Hermes. No Hermes-only config, no Hermes-only
skill format. It is: ops.db schema/DDL, pipelines, data, docs, plans. Whichever harness
runs it on the VPS reads it as plain files + sqlite. This is what lets you swap OpenClaw,
the official Hermes image, or another tool onto the exact same workspace later.
(If you later want a harness-specific wrapper, that lives in repo A - the profile -
and is a thin convention, not the data itself.)

================================================================================
2. SETUP PATH ON HOSTINGER (template-first, then extend)
================================================================================

Step 0 - ORDER
  Buy the Hermes Agent template on KVM 2, near datacenter (London/UK for latency to you).
  Read the RENEWAL price before committing a term; pick the shortest term for a pilot.

Step 1 - SECURE the box (do BEFORE anything else)
  - Connect over SSH (ssh root@<ip>), NOT Hostinger's browser console (it mangles `:`/@/=).
  - Create a non-root user, enable public-key auth, disable root password login.
  - Firewall (ufw): allow only 22 (SSH); 443/80 only when you later expose HTTP.
    Do NOT open 8642 (Hermes API) or 9119 (Hermes dashboard) publicly.

Step 2 - The Hermes container (Hostinger did most of this)
  - Your order auto-deploys the Hermes Agent Docker project. In Docker Manager, open the
    web terminal, log in with the credentials you set at purchase.
  - Run the Hermes setup wizard (Quick setup): pick your LLM provider (deepseek / OpenRouter
    / Anthropic), paste the API key, choose the model, connect the messaging platform.
  - Sensitive data -> ~/.hermes/.env; non-sensitive -> ~/.hermes/config.yaml.
  - Verify: `hermes` in the web terminal shows the welcome banner + loaded skills.

Step 3 - Seed the PROFILE (repo A) into the new container
  - Point the container's ~/.hermes at your hermes-profile repo (clone or copy config.yaml,
    SOUL.md, skills/, cron/, memory templates). Skill/memory defs from the repo become the
    VPS instance's brain.
  - Secrets still live in ~/.hermes/.env (from Doppler), never in git.

Step 4 - Attach the WORKSPACE (repo B) as a harness-agnostic mount/service
  - Clone ses-workspace to /opt/workspace (or under the container's reachable path).
  - Add it as a second volume to the Hermes compose service so the agent + cron can read
    it; OR (cleaner, ultra-agnostic) run an rsync/cron task that keeps /opt/workspace in
    sync from Repo B, fully decoupled from Hermes.
  - ops.db: place the VPS copy as the AUTHORITATIVE SHADOW DB. SSH into the VPS or pull
    the file at any time (scp/sftp/rsync). Decide local-vs-VPS write policy (below).

Step 5 - Future services (sidecar pattern)
  - Add new compose services on the SAME docker network as Hermes when you need them:
    a watcher, Postgres, a Graph/SharePoint connector, etc. Each gets its own env keys.
    Nothing couples back to Hermes; the workspace stays the neutral hub.

================================================================================
3. CREDENTIALS INVENTORY (unchanged, restated for the template path)
================================================================================

A. HOSTINGER  - account login + 2FA; SSH keypair (public on VPS, private on your PC);
   optional Hostinger API key ONLY if you use their CI action (recommend SSH deploy key
   over the API for a single box).
B. GITHUB     - account; two PRIVATE repos; fine-grained PAT (you pushing local->remote);
   SSH deploy key for Actions->VPS (private half = a GH Actions SECRET).
C. HERMES     - deepseek (primary) key from your Doppler `openclaw` project; any tool keys
   (vision: GEMINI_KEY_SESAME_AI / Anthropic; Google/Dropbox) only if the VPS agent uses
   them. OWN Telegram bot token for the VPS instance (do not share the local bot's token -
   two gateways fight over one bot).
D. DATA-PLANE (travel as mount files, not git): Asana PAT; Microsoft 365 / SharePoint /
   Graph app credentials (client_id+secret+tenant) for SES_Brain_POC + future Graph; Dropbox
   token (read-only).
E. DB         - ops.db/crm.db are SQLite, ride with the workspace checkout, no creds.
   Future Postgres/MySQL gets its own .env.

SECURITY: secrets go in ~/.hermes/.env only (from Doppler). Never commit. A fresh .env per
instance.

================================================================================
4. SHADOW DB ON THE VPS (revised - your call confirmed)
================================================================================
- VPS holds the AUTHORITATIVE LIVE ops.db. It is the "shadow database" you SSH into or
  pull from at any time.
- Access patterns:
   - SSH in: sqlite3 /opt/workspace/db/ops.db "..."
   - Pull a copy: scp root@<vps>:/opt/workspace/db/ops.db .
   - A scheduled rsync/tar to object storage for backup.
- ONE live writer. Local is dev: if local also writes ops.db, decide a sync direction so the
  two copies don't diverge silently (e.g. local pushes its dumps TO the VPS as artifacts,
  VPS is the read-of-record for live state).
- WAL-friendly: sqlite over a mounted volume is fine for single-writer; don't point two
  Hermes gateways at the same ~/.hermes or the same ops.db.

================================================================================
5. WEBHOOKS
================================================================================
- PUSH-TO-DEPLOY: hostinger's template + GitHub Actions (or hostinger/deploy-on-vps action)
  already give you "git push -> VPS rollout" without hosting a listener. Use this for repo A
  and repo B updates.
- INBOUND arbitrary events: expose a small webhook endpoint on the box behind SSH/tailscale
  only; never publish 8642/9119.
- OUTBOUND: Hermes gateway channels already handle outbound (Telegram). Per-system webhooks
  (Asana/SharePoint/Graph) travel as app creds in the instance .env.
- SECURITY: dashboard + API server fail closed on public non-loopback bind without auth;
  keep them on 127.0.0.1 and reach via SSH tunnel/tailscale.

================================================================================
6. RECOMMENDED NEXT STEPS
================================================================================
1. Order the Hermes Agent template on KVM 2 (confirm renewal price, shortest pilot term).
2. After order: SSH in, create non-root user, pubkey auth, disable root password, ufw.
3. Open the web terminal (Docker Manager), run the setup wizard (provider + key + platform).
4. Make the two private repos (repo A hermes-profile, repo B ses-workspace) with .gitignore
   (no secrets, no live ops.db) + .env.example templates.
5. Seed repo A into ~/.hermes on the VPS; attach repo B as the neutral workspace mount.
6. Put the VPS copy of ops.db in place as the shadow DB; test SSH read + scp pull.
7. Wire push-to-deploy (Actions) for both repos; smoke-test.
8. Decide local vs VPS write policy; migrate always-on jobs to the VPS; strip local.

================================================================================
7. OPEN QUESTIONS
================================================================================
- Same Telegram bot on both instances vs dedicated for VPS? (Recommend dedicated.)
- OK with the Hostinger Hermes template's free weekly backups, or do you also want a nightly
  tar to object storage? (Recommend both.)
- When you "swap a different harness onto the same workspace" - which harness is the target?
  (OpenClaw/OC?) - this decides how thin repo A must stay.
- Buy KVM 2 at intro now, or pilot KVM 1 first?
- Local write policy for ops.db once VPS is authoritative - what keeps running locally?
