# Doppler login on the VPS - dead simple, no Docker confusions

WHO/WHERE: This is a VPS HOST + Docker. Doppler login is done on the VPS HOST as root,
NOT inside the docker container. The container just reads the resulting .env file.

==============================================================================
STEP 1 - Get onto the VPS as root (host, not the container)
==============================================================================
On your PC:
    ssh root@<vps-ip>
# OR, if you normally `ssh vps`:
    ssh vps

You are now on the VPS HOST (the Linux machine). You are NOT inside docker yet.
If you see `(base)` or a shell prompt like `root@vps:~#` -> good, that's the host.

==============================================================================
STEP 2 - Install the Doppler CLI on the host (one time)
==============================================================================
# run this on the VPS host (root), copy-paste the whole line:
curl -sLf --retry 3 https://cli.doppler.com/install.sh | sh

# check it worked:
doppler --version

==============================================================================
STEP 3 - Log in (interactive - just follow the prompts)
==============================================================================
# run this on the VPS host (root):
doppler login

# It will print a URL. Copy it, open it in your browser, log in, and it gives
# you a token to paste back into the terminal. That's it - DO NOT use a
# personal token for this; the interactive login is the safe path.

==============================================================================
STEP 4 - Confirm you can see your project's secrets
==============================================================================
# still on the VPS host:
doppler setup --project openclaw --config prd
# then (shows KEY NAMES only in this doc - values stay on the VPS):
doppler secrets | head

# This is just to confirm the connection works. You do NOT need to copy
# anything back to your PC.

==============================================================================
STEP 5 - Write the .env for the Hermes container
==============================================================================
# This builds the .env file the Hermes docker instance will read.
# Run it from the Hermes state dir - ask your VPS agent WHERE the container's
# .env lives, or use the path from the compose file (usually /opt/ses/.hermes-stage
# or ~/.hermes). Adjust the path below if it differs:

cd /opt/ses/.hermes-stage          # <-- change to your actual .env dir if different
doppler secrets download --no-file --format env > .env
chmod 600 .env

==============================================================================
STEP 6 - Tell the container to pick it up
==============================================================================
# Restart the Hermes container so it re-reads the .env:
docker restart hermes-gateway

# Check it's happy:
docker logs --tail 30 hermes-gateway

==============================================================================
THE SIMPLE RULE
==============================================================================
  doppler login  ->  on the VPS HOST as root (Step 3)
  docker exec   ->  for running hermes commands (e.g. --version), NOT for doppler

If your VPS agent "set up the Doppler infrastructure", it may have ALREADY done
Step 3 for you. Then you can just check: from the VPS host, `doppler secrets get
DEEPSEEK_API_KEY --project openclaw --config prd` - if it returns a value (not an
error), Step 3 is done and you can go straight to Step 5.
