# VPS Hermes — Doppler-ruled .env + terminal-into-container (verified 2026-08-20)

Everything here runs on the VPS (you already have `ssh vps` working). Nothing touches
your local PC. Verified against the live box on 2026-08-20.

====================================================================
WHAT I VERIFIED LIVE (so you trust the next steps)
====================================================================
- doppler CLI v3.76.5 IS installed and authed on the VPS host (root).
- Project `vps_hermes` exists; `prd` is the live config that has been fetched.
- Your LIVE .env ALREADY matches Doppler on the secrets that matter:
    DEEPSEEK_API_KEY      -> identical to doppler (sk-1008...)
    TELEGRAM_BOT_TOKEN    -> identical to doppler's BOT_ID:BOT_API joined
  So Doppler is already the source of truth for these. Good.
- The .env is NOT a pure doppler dump (and must not become one), because:
    * Doppler stores the Telegram token SPLIT  (TELEGRAM_BOT_ID + TELEGRAM_BOT_API)
    * Hermes needs it JOINED                 (TELEGRAM_BOT_TOKEN=ID:API)
    * .env also carries Hermes-only keys not in doppler: API_SERVER_KEY (auto-gen),
      TELEGRAM_ALLOWED_USERS, TELEGRAM_CHAT_ID.
- Found but NOT touched (flagged, needs your call):
    * ASANA_PAT_MCP_KEY + ASANA_CLINET_ID are in doppler prd but NOT in the live .env.
      Left out intentionally while the VPS is in read-only lockdown.
    * Stray container "infallible_archimedes" (old `docker run setup` leftover, CMD=[setup],
      running 45h, no ports). Harmless now but should be stopped+removed when you approve.

====================================================================
PART 1 - Open a terminal INSIDE the container
====================================================================
In any terminal on your PC:

    ssh vps
    docker exec -it hermes-gateway bash

Now inside the container. Test:
    pwd                              -> /opt/hermes (or /opt/data)
    ls /opt/data                     -> your profile (same as host /opt/ses/.hermes)
    ls /workspace                    -> your workspace (same as host /opt/ses/workspace)
    hermes config get model.default  -> deepseek-chat

Exit twice (container, then VPS). One-liner without a shell:
    ssh vps "docker exec hermes-gateway hermes --version"

====================================================================
PART 2 - Make the .env Doppler-RULED (safe, rebuildable, idempotent)
====================================================================
FROM THE VPS HOST after `ssh vps` (NOT inside the container).
The whole block is one paste. It sources secrets from doppler, reconstructs the
joined Telegram token, re-adds the Hermes-only keys, and swaps the file atomically.

cd /opt/ses
doppler secrets download --project vps_hermes --config prd --format env --no-file > /tmp/dop.env
chmod 600 /tmp/dop.env

# rebuild
{
  echo "DEEPSEEK_API_KEY=$(sed -n 's/^DEEPSEEK_API_KEY=//p' /tmp/dop.env | tr -d '\r' | head -1)"
  echo "TELEGRAM_BOT_TOKEN=$(sed -n 's/^TELEGRAM_BOT_ID=//p' /tmp/dop.env | tr -d '\r' | head -1):$(sed -n 's/^TELEGRAM_BOT_API=//p' /tmp/dop.env | tr -d '\r' | head -1)"
  echo "TELEGRAM_CHAT_ID=8551261362"
  echo "TELEGRAM_ALLOWED_USERS=8551261362"
  sed -n 's/^API_SERVER_KEY=.*/API_SERVER_KEY=KEEP_EXISTING/p' /opt/ses/.hermes/.env
} | sed '/^API_SERVER_KEY=KEEP_EXISTING$/d' > /tmp/new.env
# preserve the existing auto-generated API_SERVER_KEY (do NOT regenerate it)
grep '^API_SERVER_KEY=' /opt/ses/.hermes/.env >> /tmp/new.env
# if you want Asana too (only AFTER you approve - Part 4):
#   sed -n 's/^ASANA_PAT_MCP_KEY=.*/&/p' /tmp/dop.env >> /tmp/new.env
#   sed -n 's/^ASANA_CLINET_ID=.*/&/p'   /tmp/dop.env >> /tmp/new.env

echo "=== keys + lengths we are about to write (values hidden) ==="
awk -F'=' '{ n=$1; v=$2; gsub(/\r/,"",v); printf "  %-24s len=%-3s\n", n, length(v) }' /tmp/new.env

echo "=== dry-check duplicates ==="
awk -F'=' '{print $1}' /tmp/new.env | sort | uniq -d

# REVIEW the lengths+keys above; if they look right, commit (uncomment to run):
# cp /tmp/new.env /opt/ses/.hermes/.env.tmp
# chown 10000:10000 /opt/ses/.hermes/.env.tmp
# chmod 600    /opt/ses/.hermes/.env.tmp
# mv /opt/ses/.hermes/.env.tmp /opt/ses/.hermes/.env
# docker restart hermes-gateway

# verify after restart:
# docker logs --tail 25 hermes-gateway 2>&1 | grep -iE 'online|error|401|denied' | tail

rm -f /tmp/dop.env /tmp/new.env

====================================================================
PART 3 - (when YOU approve) remove the stray setup container
====================================================================
    docker stop infallible_archimedes
    docker rm   infallible_archimedes

====================================================================
PART 4 - (when YOU approve) wire Asana into the VPS Hermes
====================================================================
Uncomment the two sed lines in Part 2, re-run the block, restart. NOTE the Doppler
key is spelled ASANA_CLINET_ID (that typo is in doppler itself - keep it as-is).
Only do this if you want the VPS agent to talk to Asana. SAP remains the only
Asana WRITER wherever it runs.

====================================================================
KEY MENTAL MODELS (the "filing structure is different" question)
====================================================================
- HOST vs CONTAINER = two worlds. `ssh vps` = host. `docker exec -it hermes-gateway bash`
  = container. Same machine, two views.
- No separate container copy: /opt/ses/.hermes  <==bind mount==>  /opt/data
  and /opt/ses/workspace  <==bind mount==>  /workspace are the SAME folders.
  Open /opt/ses in VS Code/Cursor and you see exactly what the container sees.
- `doppler login` lives on the HOST. The container only reads the resulting .env.
- The weird folder list you saw = Cursor had opened / (root home), not /opt/ses.
  Open /opt/ses and you'll see .hermes + workspace.
