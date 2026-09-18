# VPS Hermes Deployment — Status & Setup Report
Date-stamped: 2026-08-18 (deployment live & verified)

====================================================================
TL;DR
====================================================================
A fresh Hermes Agent instance (Docker) is LIVE on the VPS, wired to
DeepSeek, Telegram bot @SES_Production_bot, the SES workspace (ops.db
+ code), the SES SOUL/learnings/skills/cron, and the full Telegram
platform/group config. Bootable, running, pairing-approved, workspace
mounted. Outstanding items are listed in the sibling file
_outstanding-activities-2026-08-18.md.

====================================================================
1. VPS HOST
====================================================================
- Host: srv1903976, Ubuntu 24.04, kernel 6.8.0, 8GB RAM, ~90GB free
- SSH: 191.215.40.116. Alias `vps` configured in ~/.ssh/config.
- Users:
    root   = key-only (PermitRootLogin prohibit-password). NO root pw login.
    sasops = sudo user; KEY + PASSWORD login (password scoped to sasops
             only via a Match block). Password stored at
             plans/_vps_sasops_pw.txt (owner-only).
- Firewall (ufw): active, default deny incoming; ONLY 22/tcp open.
  80/443/8642/9119 are NOT internet-exposed. 8642 is loopback-only.
- SSH hardening: PasswordAuthentication no (global), root key-only,
  Pubkey auth only source of remote identity. Captured lessons in skills
  system/vps-bootstrap-hardening and system/hermes-on-vps-docker.

====================================================================
2. HERMES AGENT (Docker) — THE DEPLOYMENT
====================================================================
- Image: nousresearch/hermes-agent:latest (3.86GB)
- Container: hermes-gateway, --restart unless-stopped, Up, restarts=0.
- Persistent profile: /opt/ses/.hermes  -> mounted as /opt/data
- Model: deepseek (provider=deepseek, model=deepseek-chat,
         base_url=https://api.deepseek.com/v1)
- Secrets (.env, on VPS only): DEEPSEEK_API_KEY (from Doppler
  vps_hermes/prd), TELEGRAM_BOT_TOKEN (reconstructed from Doppler
  TELEGRAM_BOT_ID + TELEGRAM_BOT_API), TELEGRAM_ALLOWED_USERS + CHAT_ID
  (=8551261362), API_SERVER_KEY (auto).
- Telegram pairing: APPROVED user Gis / 8551261362.
- Config: telegram block merged (allowed_chats + 9 channel_prompts +
  auto_thread). See 3 below. terminal.cwd = /workspace.

====================================================================
3. WHAT WAS MIGRATED FROM LOCAL TO VPS  (durable content)
====================================================================
 Local source (C:\Users\GilesHugo\AppData\Local\hermes)
  -> VPS /opt/ses/.hermes
  SOUL.md                       -> present
  memories/MEMORY.md+USER.md    -> present (learnings)
  skills/(6 user dirs)          -> present (asana, asana-write-guardrails,
                                    graphify, operations, personal, system)
  cron/jobs.json (22 jobs)      -> present, all 22
  config.yaml telegram block    -> MERGED (NOT overwritten; local config was
                                    NOT bulk-copied because it holds a live
                                    Gemini key + Windows paths)

 Workspace (Layer A code + Layer B DB)
  C:\Users\GilesHugo\SES_Workspace\plans\.. plus
  Temp\ses_workspace_clean_push (5.4MB code) -> /opt/ses/workspace (6.7MB)
  SES_Workspace\db\ops.db (969.8MB)        -> /opt/ses/workspace/db/ops.db (integrity OK)
  SES_Workspace\db\crm.db (196KB)          -> /opt/ses/workspace/db/crm.db

====================================================================
4. WORKSPACE WITHIN DOCKER
====================================================================
- Container recreated with SECOND mount: /opt/ses/workspace -> /workspace
- Agent sees /workspace/db/ (ops.db + crm.db). WORKSPACE_VISIBLE confirmed.
- terminal.cwd = /workspace.

====================================================================
5. CONFIG MERGE DECISION (important)
====================================================================
Local config.yaml NOT bulk-copied to the VPS. Reason: it contains an
EMBEDDED LIVE GEMINI API KEY (under a config provider block) and
Windows-only settings (backend local, cwd C:/...). Copying it would have
shipped a secret and broken the VPS's working deepseek/gateway config.
Instead, only the telegram block was merged into the VPS config.

Action for Giles: consider ROTATING that Gemini key, since it lives in
plaintext config history locally.
====================================================================

====================================================================
6. LOCK-DOWN STATE (added later same day, user request)
====================================================================
To stop the VPS instance from acting before it's the designated bot:
- CRON: DISABLED. Imported 22 jobs cleared (backed up to
  /opt/ses/.hermes/cron/jobs.json.bak_20260819_141210). count now = 0.
- TELEGRAM: restricted to DM-ONLY. allowed_chats = 8551261362 (you).
  channel_prompts cleared (no group topic behavior). It CANNOT post to
  SESAME (-1003780215894) or Richard's group (-1003927793083) anymore.
- Gateway restarted to apply. Up, 0 restarts.
- NOTE: VPS clock showed `Wed Aug 19 14:11 UTC 2026` vs session date
  Aug 18. Flag for check - possible clock skew / date rollover.
====================================================================
