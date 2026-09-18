#!/usr/bin/env bash
# reconcile-vps-env.sh - build /opt/ses/.hermes/.env FROM doppler (no hand-written KEY=value)
# Dry-run by default; --apply to commit. Secrets never leave this host.
set -euo pipefail
PROJECT="vps_hermes"; CONFIG="prd"; ENV_DIR="/opt/ses/.hermes"; ENV_FILE="${ENV_DIR}/.env"; ENV_UID="10000"
DRYRUN=1; WANT_ASANA="${ASANA:-0}"
for a in "$@"; do [ "$a" = "--apply" ] && DRYRUN=0; done
command -v doppler >/dev/null 2>&1 || { echo "MISSING: doppler"; exit 1; }
tmp="$(mktemp /tmp/vpsenv.XXXXXX)"; trap 'rm -f "$tmp" /tmp/dop.env.reconcile' EXIT
doppler secrets download --project "$PROJECT" --config "$CONFIG" --format env --no-file > /tmp/dop.env.reconcile
chmod 600 /tmp/dop.env.reconcile; [ -s /tmp/dop.env.reconcile ] || { echo "ABORT: empty"; exit 1; }

# 1) start from doppler dump, dropping the split-token parts + doppler metadata
grep -E '^(DEEPSEEK_API_KEY|ASANA_PAT_MCP_KEY|ASANA_CLINET_ID)=' /tmp/dop.env.reconcile \
  | sed 's/\r$//' > "$tmp"
# 2) append the JOINED telegram token (doppler stores BOT_ID and BOT_API split)
TGID="$(sed -n 's/^TELEGRAM_BOT_ID=*** /tmp/dop.env.reconcile | tr -d '\r' | head -1)"
TGAP="$(sed -n 's/^TELEGRAM_BOT_API=//p' /tmp/dop.env.reconcile | tr -d '\r' | head -1)"
printf "TELEGRAM_BOT_TOKEN=%s:%s\n" "$TGID" "$TGAP" >> "$tmp"
# 3) Hermes-only keys (chat id + allowlist + keep existing API_SERVER_KEY)
printf "TELEGRAM_CHAT_ID=8551261362\n" >> "$tmp"
printf "TELEGRAM_ALLOWED_USERS=8551261362\n" >> "$tmp"
[ -f "$ENV_FILE" ] && grep '^API_SERVER_KEY=' "$ENV_FILE" | sed 's/\r$//' >> "$tmp" || echo "no current API_SERVER_KEY" >&2

echo "== proposed keys + lengths (values hidden) =="
awk -F='{v=$2; gsub(/\r/,"",v); printf "  %-26s len=%s\n", $1, length(v)}' "$tmp"
echo "== duplicate keys (should be empty) =="; awk -F='{print $1}' "$tmp" | sort | uniq -d; echo

[ -z "$TGAP" ] && { echo "ABORT: telegram missing"; exit 1; }
if [ "$DRYRUN" = "1" ]; then echo "DRY-RUN (nothing written). Re-run:  bash reconcile-vps-env.sh --apply"; exit 0; fi
cp "$tmp" "${ENV_FILE}.tmp"; chown "${ENV_UID}:${ENV_UID}" "${ENV_FILE}.tmp"; chmod 600 "${ENV_FILE}.tmp"; mv "${ENV_FILE}.tmp" "$ENV_FILE"
echo "wrote $ENV_FILE (mode 600, owner uid $ENV_UID)"; echo "== restart =="; docker restart hermes-gateway; sleep 3
echo "== keys now (names only) =="; awk -F='{print "  " $1}' "$ENV_FILE" | sort
echo "== verify logs =="; docker logs --tail 15 hermes-gateway | grep -iE "online|error|401|denied" | tail || true
