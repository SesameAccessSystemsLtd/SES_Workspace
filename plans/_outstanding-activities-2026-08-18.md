# VPS Hermes Deployment — OUTSTANDING ACTIVITIES
Date-stamped: 2026-08-18  (follow-ups for after this terminal chat)

====================================================================
DECISIONS / ACTIONS ONLY YOU CAN DO
====================================================================
1. [SECURITY - ROTATE GEMINI KEY]
   The local config.yaml at
   C:\Users\GilesHugo\AppData\Local\hermes\config.yaml contains an
   EMBEDDED LIVE Gemini API key (provider block, appears as api_key).
   It was NOT copied to the VPS (deliberate). Because it existed in
   config plaintext, consider ROTATING it in your Google/Vertex console.

2. [LIVE-WRITER POLICY - ops.db]  (DECISION)
   The VPS now holds an AUTHORITATIVE SHADOW copy of ops.db (969.8MB).
   Local still has the working copy. Decide the direction BEFORE the two
   diverge:
     - VPS-authoritative (always-on) + local pushes summaries to it, OR
     - VPS is a read-only shadow; local remains the live writer.
   This is the single most important follow-up. Pick one.

3. [DOPPLER - source of truth for VPS secrets]
   You planned a new Doppler project vps_hermes. We pulled DEEPSEEK_API_KEY,
   TELEGRAM_BOT_ID, TELEGRAM_BOT_API from it (works). BEST PRACTICE:
   also store/freeze the values in Doppler and document that the VPS .env
   is fed FROM Doppler, never exported to git. Confirm the VPS .env should
   be the render of vps_hermes/prd.

4. [DOPPLER CLI quote bug / naming]
   In Doppler you stored the Telegram token SPLIT as TELEGRAM_BOT_ID +
   TELEGRAM_BOT_API (not a single TELEGRAM_BOT_TOKEN). That's fine but
   unusual. Decide whether to keep split or combine.

5. [Telegram GROUP - confirm working]
   channel_prompts + allowed_chats merged for: SESAME group
   (-1003780215894), Richard's group (topic -1003927793083:1), etc.
   Test sending a message in the SESAME group and confirm the VPS agent
   responds as the SES ops brain (topic-aware). If the main SESAME group
   id differs from what's configured, update allowed_chats.

6. [Hostinger web terminal decision]
   We did NOT use the Hostinger web terminal (no browser access from my
   side). Everything was done over SSH. No action needed unless you had
   another reason to open the web terminal.

====================================================================
OPTIONAL / HYGIENE  (non-blocking)
====================================================================
7. [cosmetic config warning]
   The container logs "config predates version 12" each start. Harmless.
   Fix: backup /opt/data/config.yaml then `hermes setup` or manually set
   _config_version: 12 after reviewing changelog. I can do this next chat.

8. [.env duplicate key]
   .env files has TWO TELEGRAM_CHAT_ID lines (one blank, one valued).
   Dotenv last-wins so it's functionally fine; clean-up is optional.

9. [rsync for delta DB re-sync]
   For future incremental ops.db syncs (instead of re-copying 970MB),
   install rsync on this Windows box (winget) or use the VPS-side approach.
   Not needed now.

====================================================================
LOG OF THIS SESSION'S KEY EVENTS (audit)
====================================================================
- Set up SSH keypair vps-1903976 (ed25519, comment vps-1903976) via real
  ssh-keygen.exe through Python (terminal's bash was broken - WSL no-distro).
- Verified connection to root@191.215.40.116 (srv1903976).
- Hardened VPS: created sasops sudo user + pubkey, disabled root pw login,
  disabled password auth globally (scoped pw for sasops later), ufw deny
  default + allow 22 only.
- Pulled nousresearch/hermes-agent:latest.
- Initialised /opt/ses/.hermes profile; set model=deepseek.
- Validated Telegram bot @SES_Production_bot via getMe.
- Reconstructed Telegram token from Doppler TELEGRAM_BOT_ID+API; wired
  DEEPSEEK_API_KEY from Doppler vps_hermes/prd into VPS .env.
- Started gateway; approved pairing code D8QGXW47 -> Gis (8551261362).
- Set TELEGRAM_ALLOWED_USERS/CHAT_ID = 8551261362.
- Migrated SOUL.md, memories/, skills/, cron/ to /opt/ses/.hermes.
- Merged telegram block (allowed_chats + channel_prompts) into VPS config.
- Copied ops.db (969.8MB, integrity OK) + crm.db to /opt/ses/workspace/db.
- Recreated gateway container adding /opt/ses/workspace -> /workspace mount.
- Set terminal.cwd = /workspace.
- Gateway Up, 0 restarts, Telegram pairing approved, workspace visible.

====================================================================
SKILLS SAVED FOR RECURABILITY
====================================================================
- system/vps-bootstrap-hardening   (SSH VPS hardening + sshd drop-in gotcha)
- system/hermes-on-vps-docker      (provision Hermes in Docker on a VPS)
====================================================================

====================================================================
ADDED LATER SAME DAY - LOCK-DOWN FOLLOW-UPS
====================================================================
10. [DONE-this-session] VPS instance silenced: cron clear (0 jobs,
    backup jobs.json.bak_20260819_141210), telegram DM-only (allowed_chats
    = 8551261362, channel_prompts cleared). Gateway restarted.
11. [CHECK - CLOCK SKEW] VPS date showed `Wed Aug 19 14:11 UTC 2026`
    while the terminal session calendar shows Aug 18 2026. Mount the
    discrepancy: check `timedatectl` on the VPS; it should be UTC/NTP-
    synced. If it's genuinely ahead of your local by a day, review cron
    timing assumptions before re-enabling jobs.
12. [RESTORE CRON] To bring back the scheduled work later: restore
    /opt/ses/.hermes/cron/jobs.json from jobs.json.bak_20260819_141210
    and restart the gateway - but ONLY after you've decided the live
    writer + designated-bot policy (item #2 above) and confirmed the
    instance is the one that should run them.
13. [RESTORE TELEGRAM CHANNELS] To re-enable group/channel participation
    later: re-merge the telegram allowed_chats + channel_prompts block
    (saved in the status report section 3). Not until this bot is
    designated and the local bot is retired/separate.
====================================================================
