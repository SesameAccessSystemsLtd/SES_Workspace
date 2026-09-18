# SES Production Hermes - VPS first-boot handoff (2026-08-18)

STATUS: VPS fully provisioned. Only the interactive setup wizard is left.
Password login for sasops has been ENABLED (scoped: root stays key-only).

## Provisioned & verified on the VPS (root@191.215.40.116 / srv1903976)
- Hardened: sasops sudo user, PermitRootLogin prohibit-password, ufw allow 22 only.
  Docker 29.7.2 + Compose v5, git 2.43.
- Hermes image pulled: nousresearch/hermes-agent:latest (3.86GB)
- Persistent profile /opt/ses/.hermes auto-initialised (sessions/skills/cron/memories/
  config). model.provider=deepseek, base_url https://api.deepseek.com/v1,
  model.default=deepseek-chat.
- Telegram bot @SES_Production_bot verified live (getMe ok). Token stored in .env.
- Wizard runs over `ssh -tt` PTY (confirmed /dev/pts/0) OR Hostinger web terminal.

## LOGIN (password now enabled for sasops ONLY)
- User: sasops   Password: see C:\Users\GilesHugo\SES_Workspace\plans\_vps_sasops_pw.txt
  (owner-only file; if missing, re-set via: ssh root@vps 'passwd sasops')
- Root: key-only (vps-1903976). Root password login stays disabled.
- Also reachable by hostname alias `vps` if you add ~/.ssh/config Host vps.

## FINISHING STEP (pick the path you're comfortable with)

From THIS machine over SSH PTY (I can drive it if you give me the key):
  ssh -tt root@191.215.40.116
  docker run -it --rm -v /opt/ses/.hermes:/opt/data nousresearch/hermes-agent setup

From Hostinger web terminal (Docker Manager -> web terminal):
  (runs as root directly, no SSH needed)
  docker run -it --rm -v /opt/ses/.hermes:/opt/data nousresearch/hermes-agent setup

From a local terminal via sasops password:
  ssh sasops@191.215.40.116     (password from the file above)
  sudo docker run -it --rm -v /opt/ses/.hermes:/opt/data nousresearch/hermes-agent setup

## WIZARD INPUTS NEEDED (have these ready)
1. LLM provider  : deepseek
2. DeepSeek API key: the NEW key for the vps_hermes Doppler project (fresh, not local)
3. Model         : deepseek-chat (already the default)
4. Messaging     : telegram
5. Telegram bot token: 8036334348:... (SES_Production_bot) - already in .env, wizard may hold it
6. ADMIN Telegram numeric user ID: get yours by messaging @userinfobot
   (bot will only respond to this user)

## AFTER the wizard completes -> start the gateway daemon
  docker run -d --name hermes-gateway \
     --restart unless-stopped \
     -v /opt/ses/.hermes:/opt/data \
     -p 127.0.0.1:8642:8642 \
     nousresearch/hermes-agent gateway run
Verify:
  docker logs -f hermes-gateway      # expect "gateway online"
Then message @SES_Production_bot in Telegram to smoke-test.

## DOPPLER (source of truth for secrets on the VPS)
- Project vps_hermes: recreate DEEPSEEK_API_KEY + TELEGRAM_BOT_TOKEN there.
- Feed the VPS .env from Doppler (never commit). Real key never shipped in git.

## SECURITY NOTES
- Gateway binds 127.0.0.1:8642 = loopback; Hermes API NOT internet-exposed. Reach via
  SSH tunnel (ssh -L 8642:127.0.0.1:8642 vps) or Tailscale. Keep 8642/9119 out of ufw.
- ufw currently allows only 22. Password auth is scoped to sasops via a Match block;
  root remains key-only. After the wizard, consider disabling sasops password if unused.
