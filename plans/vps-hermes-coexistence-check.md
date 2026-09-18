# Evaluation / guardrails for the VPS Hermes instance (coexistence with local)

Purpose: help the OTHER Hermes (setting up the VPS) do a good job WITHOUT breaking
two-instance coexistence. Written 2026-08-18 from the LIVE local config on
C:\Users\GilesHugo\AppData\Local\hermes\config.yaml + channel_directory.json + .env keys.
Values redacted - secrets never shown.

================================================================================
GROUND TRUTH - how local Hermes is actually configured (do NOT blindly copy)
================================================================================
PRIMARY MODEL: deepseek (deepseek-chat), base_url https://api.deepseek.com/v1
VISION/AUX:    provider gemini, model gemini-2.5-flash  (key: GEMINI_API_KEY family)
FALLBACKS:     anthropic claude-haiku-4-5-20251001 ; x_search uses grok-4.20-reasoning
TELEGRAM allowed_chats: 8551261362, -1003780215894, -1003927793083, 8342282335
TELEGRAM channel_prompts map (topic scoping):
  '1' General | '2' Purchasing (REQUESTS-queue writes, NO PO automation, ADDRESSING not
  'Needs Met') | '3' System Updates | '4' Personal Assistant | '5' Production
  '2522' America 1 Build (loads america1_build skill, scope-locked to job 1588)
channel_directory groups: SESAME/-1003780215894 (topics 1,2,3,4,5,2522,4038,4730);
  Purchasing Team/ -1003927793083 (topics 1,53)
ENV keys present locally (~24): DEEPSEEK_API_KEY, ASANA_PERSONAL_ACCESS_TOKEN,
  PURCHASING_* (CLIENT/SECRET/TENANT/API + EMAIL_API), HUBSPOT_* (API/PAT/PORTAL/SEQUENCE),
  ANTHROPIC_API_KEY, GEMINI_API_KEY(+_AGENTS), ELASTIC_EMAIL_*, DROPBOX_*, DOPPLER_*,
  AGENT_NOTIFY_EMAIL, TELEGRAM_ALLOWED_USERS.

================================================================================
THE SUBTLE DIFFERENCES THAT WILL BREAK COEXISTENCE (must differ on the VPS!)
================================================================================
D1. TELEGRAM BOT TOKEN - MUST BE DIFFERENT on the VPS.
    Two gateways polling the SAME bot token cannot coexist. If the VPS connects the same
    bot, it sees ALL the same chats and double-handles -> double replies, fights for
    updates. The VPS instance should use its OWN bot token (created via @BotFather) OR be
    scoped to zero allowed chats initially.

D2. TELEGRAM_ALLOWED_USERS / allowed_chats - MUST be DIFFERENT / narrower on the VPS.
    Local allows 4 chat entities. If the VPS uses a new bot, it starts EMPTY - it only
    answers the chats YOU explicitly wire to it. Do NOT copy the local allowed list
    unless you want the VPS to handle those topics.

D3. channel_prompts (per-topic scope) - COPY ONLY WHAT THE VPS OWNS.
    Decide ownership BEFORE wiring prompts: does the VPS own Purchasing/Production/
    System-Updates (the always-on watchers)? Own NOTHING (pure infra)? Two instances with
    the SAME prompt on the SAME topic = both answer = duplicate work. Assign each topic to
    exactly ONE instance.

D4. SECRETS - recreate .env from Doppler, DO NOT ship the local .env (it was flagged
    in the GitHub-block remediation). Use the SAME key NAMES; only the bot token +
    allowed-users differ. Never commit these.

D5. PATHS - the VPS is Linux + Docker; local scripts hardcode C:\Users\GilesHugo\...
    The VPS instance MUST use PATH_CONFIG.yaml (env-overridable workspace_root) +
    lib/path_config.py. It must NOT assume Windows paths. Cron jobs reference DBs via
    the VPS shadow-DB path, NOT a remote C:\...\db\ops.db.

D6. THE ONE-LIVE-WRITER RULE (most important). Only ONE instance may write to a given
    ops.db / REQUESTS queue / Asana pipeline at a time. Resolution:
       VPS = always-on watchers + cron (owns the live shadow DB on /opt/ses/workspace/db)
       Local = interactive shell + dev/test
    Never run the same purchasing/Asana cron on BOTH. Assign each cron/job to ONE owner.

D7. MODEL FEATURES - the VPS must have the SAME provider keys (deepseek, gemini for
    vision, anthropic fallback) or it silently fails on those tools. Confirm
    GEMINI_API_KEY (the live AQ.Ab8... one) lands in the VPS .env for vision_analyze.

================================================================================
WHAT "DOING A GOOD JOB" MEANS FOR THE OTHER HERMES (checklist to hand it)
================================================================================
GOOD = it:
  [ ] deploys the official nousresearch/hermes-agent docker image (Hostinger template)
  [ ] mounts /opt/data -> ~/.hermes-like state dir, per Docker docs (/opt/data is single
      source of truth; image is stateless)
  [ ] recreates .env from Doppler with the SAME key names as local (D4), own bot token
  [ ] does NOT copy the local Telegram allowed_chats/channel_prompts unless assigned (D2/D3)
  [ ] uses PATH_CONFIG.yaml + Linux paths, not hardcoded C:\ paths (D5)
  [ ] treats /opt/ses/workspace/db/ops.db as the VPS shadow DB (copy-in from a one-shot
      scp, NOT a live 2-way mount; no WAL so straight copy is fine)
  [ ] assigns cron ownership so it never double-runs local Purchasing/Asana jobs (D6)
  [ ] enables tool_loop_guardrails.hard_stop_enabled (unattended server) + dashboard only
      behind SSH/tailscale or with auth - never exposed
  [ ] reports back: image tag, ports bound, which chats it is wired to, which crons it owns

BAD = it (watch / correct):
  [ ] connects the SAME telegram bot token as local (double-handling)
  [ ] copies the local 5-topic + 2522 channel_prompts onto the same chats (double answers)
  [ ] ships the local .env or hardcoded C:\ paths
  [ ] runs purchasing/Asana crons while local does -> racing writes to ops.db/REQUESTS
  [ ] exposes the dashboard/API server publicly with no auth

================================================================================
HOW TO VERIFY / AUDIT THE VPS HERMES (when it's up)
================================================================================
- On the VPS: docker ps (one hermes container), docker logs -f hermes (gateway online),
  hermes status (Manager: s6), ls of the mount dir (config.yaml/.env/skills/cron).
- Telegram: `/start` the VPS bot OR just confirm it does NOT reply unless wired.
- Ask it for ITS OWN config summary: model, allowed_chats, channel_prompts keys, cron
  list, workspace_root. Compare to the owner map (D3/D6).
- Confirm it is NOT polling the local bot token (different bot username).

================================================================================
NEXT ACTION
================================================================================
Give this checklist to the other Hermes (paste into its channel) and ask it to confirm
items [ ] as it configures. If it shares its resolved config.yaml/env-key-names (values
redacted), I can review it against this list and flag drift. I do not need the secret
values to validate correctness.
