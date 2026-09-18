# VPS Hermes Doppler-ruled .env — APPLIED + VERIFIED 2026-08-20

## What was done
- The VPS /opt/ses/.hermes/.env is now generated FROM Doppler project vps_hermes/prd.
- Reconcile script lives BOTH places:
    VPS:  /root/reconcile_vps_env.py
    Local copy: scripts/reconcile_vps_env.py
- It downloads Doppler prd, strips doppler's value double-quotes + CR, restores the
  JOINED telegram token (doppler stores it split as TELEGRAM_BOT_ID + TELEGRAM_BOT_API),
  adds the Hermes-only keys (CHAT_ID, ALLOWED_USERS), preserves the auto-generated
  API_SERVER_KEY, writes atomically (chown 10000:10000, chmod 600), restarts the gateway.

## Verified result (post-apply)
- DEEPSEEK_API_KEY  len=35, matches live=True
- TELEGRAM_BOT_TOKEN len=46, matches live=True  (shape_ok)
- TELEGRAM_CHAT_ID / ALLOWED_USERS = 8551261362 (you)
- API_SERVER_KEY present (preserved)
- Asana keys intentionally EXCLUDED (VPS is read-only lockdown; SAP remains sole asana writer)
- .env owner uid 10000, mode 600
- Gateway restarted, Up, 0 restarts, s6 supervision on.

## Reproduce later (from the VPS after `ssh vps`)
    python3 /root/reconcile_vps_env.py             # dry-run (shows names+lengths)
    python3 /root/reconcile_vps_env.py --apply     # write + restart

## To add Asana keys later (only when you approve)
Edit /root/reconcile_vps_env.py to also copy ASANA_PAT_MCP_KEY and ASANA_CLINET_ID
from the doppler download into new{}, then re-run --apply. Note doppler spells it
ASANA_CLINET_ID (typo lives in doppler itself; keep it as-is).

## Key decision record (for audit)
- Doppler stored telegram token SPLIT; Hermes needs JOINED. Reconciled on host.
- Doppler env format double-quotes values; script strips them.
- The two earlier "extra" Doppier metadata keys (DOPPLER_PROJECT/CONFIG/ENVIRONMENT)
  are intentionally dropped (not secrets, not needed by Hermes).
