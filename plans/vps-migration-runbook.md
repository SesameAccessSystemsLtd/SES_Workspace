# SES Workspace + Hermes -> Hostinger VPS migration runbook

Status: READY-TO-RUN version 1 - Giles, 2026-08-14
Rule that governs EVERY step:  COPY OUT ONLY - nothing on the live box is ever moved,
deleted, or edited. If a step would mutate C:\Users\GilesHugo\SES_Workspace or
~/.hermes, it is WRONG. Rerun is always safe (idempotent copies).

RECONCILIATION WITH THE PENDING GITHUB PUSH
- The GitHub push (GO A/B/C) is NOT required for the migration.
- This runbook migrates the working system to the VPS over SSH directly.
- Git/GitHub remains useful LATER for the harness-agnostic code layer + CI, but it is
  not a blocker. Decide it after the VPS is live.

================================================================================
0. PRE-FLIGHT (read this before touching anything)
================================================================================
Verified live footprint (2026-08-14):
  db/ops.db                958 MB   <- dominant artifact, the SHADOW DB on the VPS
  db/crm.db                192 KB
  ~/.hermes                ~389 MB  (sessions, memory, cron, skills, .env)
  AppData/Local/hermes    ~184 MB   (framework install; REBUILD, do not migrate)
  .venv                    ~99 MB   (REBUILD on VPS, do not migrate)
  graphify-out            ~14 MB    (cache; re-generate on VPS, do not migrate)

Tooling on your box (verified present):
  ssh, scp  = C:\Windows\System32\OpenSSH  (present)
  git       = present
  rsync     = NOT installed (recommend winget install -e --id ... or msys rsync, for
              delta re-sync of the 958MB DB later; first copy can use scp)
  docker    = not local (fine - Docker runs ON the VPS)

You WILL need (not yet available):
  - VPS IP, root password or SSH key, SSH reachability
  - Confirm Hostinger Hermes template deployed (or plain VPS + docker on it)

================================================================================
1. EXACT MIGRATION LAYERS
================================================================================
Layer A - CODE (harness-agnostic): skills/, scripts/, config-as-source
         -> via git (already staged; clone onto VPS). Small, versioned.
Layer B - LIVE STATE (artifact): ops.db, crm.db, .hermes session/memory/cron
         -> via scp / tar-over-ssh. Single point-in-time cold copy.
Layer C - NOT migrated, rebuilt on VPS: .venv, graphify-out, framework install,
         secrets (.env recreated from Doppler, never copied).

================================================================================
2. STEP-BY-STEP (run from a shell on your PC; <VPS> = root@<ip>)
================================================================================

STEP 1 - SSH reachability + key setup (one time)
  ssh root@<VPS-IP>
  # on the VPS: create user, add your pubkey, disable root password login, ufw
  #   adduser sasops && usermod -aG sudo sasops
  #   install pubkey, comment out PermitRootLogin, ufw allow 22 + optionally 443
  # exit
  (Do this over real SSH, NOT Hostinger's browser console - it mangles `:`/@/=.)

STEP 2 - Prepare the workspace target dir on the VPS
  ssh <VPS> "mkdir -p /opt/ses/workspace/db /opt/ses/workspace/skills-import \
             /opt/ses/.hermes-stage"

STEP 3 - Layer A: push the clean code snapshot to the VPS (copy OUT of live box).
  NOTE: use the ALREADY-STAGED clean repo at
        C:\Users\GilesHugo\AppData\Local\Temp\ses_workspace_clean_push
        (optionally trim scope first, see _PUSH_READY_STATE.md inside it)
  Option 3a - via git (if you ever GO A/B/C) -> then `git clone` on the VPS.
  Option 3b - via scp of a tar of the staged clean set:
        cd /c/Users/GilesHugo/AppData/Local/Temp/ses_workspace_clean_push
        tar czf - . | ssh <VPS> "tar xzf - -C /opt/ses/workspace/skills-import"
  (Skills-import is source-only; it does NOT create a second live DB.)

STEP 4 - Layer B1: migrate ops.db + crm.db (the shadow DB seed)
  scp "C:\Users\GilesHugo\SES_Workspace\db\ops.db"  <VPS>:/opt/ses/workspace/db/ops.db
  scp "C:\Users\GilesHugo\SES_Workspace\db\crm.db"  <VPS>:/opt/ses/workspace/db/crm.db
  # verify size + integrity on VPS:
  ssh <VPS> "ls -lh /opt/ses/workspace/db/ops.db && \
             python3 -c \"import sqlite3; c=sqlite3.connect('/opt/ses/workspace/db/ops.db');\
             print('integrity:',c.execute('pragma integrity_check').fetchone())\""
  (Safe: ops.db has no WAL/SHM sidecars, verify - a straight file copy is consistent.
   For a fully zero-downtime path later use sqlite3 .backup, but cold-copy is fine here.)
  IMPORTANT: do NOT copy .fuse_hidden* or db backups/logs. Exclude them.

STEP 5 - Layer B2: migrate Hermes state (~/.hermes) over SSH
  # strip .env from the tar (recreate secrets from Doppler on the VPS, never ship them)
  tar czf - --exclude='.env' --exclude='.env.*' \
      -C "C:\Users\GilesHugo\.hermes" . | ssh <VPS> "tar xzf - -C /opt/ses/.hermes-stage"
  ssh <VPS> "ls /opt/ses/.hermes-stage   # inspect: sessions/, memories/, skills/, cron/, config.yaml"

STEP 6 - Layer C: rebuild on the VPS (do NOT copy .venv / graphify-out / framework)
  On the VPS:
    - Deploy the Hermes docker image (Hostinger template already gives it) and mount
      /opt/ses/.hermes-stage as /opt/data (or copy into the mounted ~/.hermes).
    - Recreate /opt/ses/.hermes-stage/.env from Doppler: same keys (deepseek, tokens,
      Asana PAT, Graph creds). Own bot token if you want a separate instance.
    - pip/uv install the skill dependencies (Docker image pre-bundles Python+Node).
    - Re-run graphify if you want the search index (rebuild, not copied).
    - Point lib/path_config workspace_root at the VPS path.

STEP 7 - First boot smoke test on the VPS
  ssh <VPS> "docker compose up -d && docker logs -f hermes"   # expect gateway online
  - agent answers a prompt; cron registered; ops.db queries return rows.
  - (This is the "shadow DB" - SSH in anytime to sqlite3/query, or scp it down.)

STEP 8 - Post-migration hygiene (optional, later)
  - Install rsync on your box for incremental DB re-sync (delta, not 958MB always).
  - Decide the live-writer policy: VPS authoritative (always-on) vs local pushes to it.
  - Revisit the GitHub GO A/B/C for the harness-agnostic code layer + CI if desired.

================================================================================
3. ROLLBACK / SAFETY (nothing here can hurt the live box)
================================================================================
- Every step is a read/copy from the live box. If the VPS side goes wrong, you lose
  VPS work, not local. The live workspace is untouched throughout.
- To abort at any point: stop running the copy commands; nothing to undo locally.
- Keep the staged clean repo + this runbook as your reproducibility source.

================================================================================
4. WHAT I NEED FROM YOU TO EXECUTE (when you're ready)
================================================================================
1. Confirm the scope of Layer A (push the staged set as-is, or trim to code-only).
2. Provide VPS IP + confirm SSH reachability (or say "IP is ready").
3. Say whether the VPS instance should be a SEPARATE bot/instance or mirror local.
4. Confirm Python3 exists on the VPS for the sqlite integrity check (or I adapt).
================================================================================
