# VPS Hermes — Doppler self-management + Jibble + hardening (2026-08-20, part 2)

## Tier 2 change-watch cron — INSTALLED + VERIFIED
- Crontab (VPS root): `*/5 * * * * /root/vps-env-watch.sh`
- Watcher does: dry-run reconcile; if Doppler added/changed/rotated any whitelisted key
  -> applies (writes .env, restarts gateway) + ALERTS (so a rotation is never silent).
  If nothing changed -> silent. Logs to /var/log/vps-env-watch.log.
- /root/vps-env-watch.sh (VPS). Cron daemon active. Runs headless (clean env) OK.

## Jibble keys — PUSHED INTO VPS .env (verified)
Now in /opt/ses/.hermes/.env:
- JIBBLE_API_KEY  len=48
- JIBBLE_KEY_ID   len=36
(These came from Doppler vps_hermes/prd; values double-quoted in doppler, stripped.)

## Full .env now (names + lengths)
  API_SERVER_KEY=64  DEEPSEEK_API_KEY=35  JIBBLE_API_KEY=48  JIBBLE_KEY_ID=36
  TELEGRAM_BOT_TOKEN=46  TELEGRAM_CHAT_ID=10  TELEGRAM_ALLOWED_USERS=10
- .env owner uid 10000, mode 600. Gateway Up, 0 restarts.

## Reconcile script (whitelist model)
/root/reconcile_vps_env.py (VPS). Local copy: scripts/reconcile_vps_env.py.
KEY WHITELIST (edit `keep` list to add/remove): DEEPSEEK_API_KEY + JIBBLE pair now.
  + ASANA_PAT_MCP_KEY/ASANA_CLINET_ID only when env ASANA=1. Telegram token joined
  from doppler split (BOT_ID:BOT_API). API_SERVER_KEY preserved. DOPPLER_* dropped.
  Run: python3 /root/reconcile_vps_env.py                # dry-run (names+lengths+delta)
       python3 /root/reconcile_vps_env.py --apply        # write + restart
Other procs: /root/reconcile_vps_env.sh is a bash version (older).

## Hardening vs prompt-injection — done + remaining
DONE (verified):
- Built-in credential/threat guard ACTIVE (blocked a 'hermes_env' exfil payload in logs).
- .env owned uid 10000 / mode 600 (not world-readable).
- No .env or credentials in agent-visible /workspace.
REMAINING (needs a human to run the interactive wizard - hand off):
- `hermes egress` (iron-proxy) binary IS installed (v0.39.0 at /opt/data/bin/iron-proxy)
  but NOT configured (no CA/tokens/proxy.yaml - the setup wizard is interactive).
  When ready, in the container terminal: `hermes egress setup` (interactive).
  Effect: agent holds substitute tokens, so even under prompt-injection it can't ship
  real API keys out over the network. Credential src env, Docker enforce yes.

## Reproduce / self-manage loop
1. Add/rotate a secret in Doppler (dashboard).
2. Within ≤5 min, the VPS cron applies it + restarts + ALERTS you.
   (Or manually: ssh vps; python3 /root/reconcile_vps_env.py --apply )
3. To add a brand-new key the bot should use: add its NAME to the `keep` whitelist in
   /root/reconcile_vps_env.py, then it's picked up automatically thereafter.
