# Running Doppler setup for the Hermes Docker container - line by line

Open a NEW TERMINAL on your PC. Type each block in order. Everything here runs on
the VPS; nothing touches your local machine.

================================================================================
PART 0 - Get onto the VPS and confirm Doppler exists on the HOST
================================================================================
ssh vps

# On the VPS (host), check Doppler is installed:
doppler --version

# If it prints a version -> good, continue.
# If "command not found" -> install it first:
#   curl -sLf --retry 3 https://cli.doppler.com/install.sh | sh
# then re-run:  doppler --version

================================================================================
PART 1 - Open a shell INSIDE the container
================================================================================
docker exec -it hermes-gateway bash
# (if bash is not found, use:   docker exec -it hermes-gateway sh )

================================================================================
PART 2 - Check if Doppler is installed INSIDE the container
================================================================================
which doppler

#  IF it prints a path (e.g. /usr/bin/doppler or /usr/local/bin/doppler):
#      doppler login
#      -> copy the URL it prints, open in browser, log in, paste token back.
#
#  IF it prints nothing / "command not found":
#      -> the container does NOT have its own Doppler CLI. Exit the container and
#         use PART 3 (the clean host-mount method). Type:
#      exit

================================================================================
PART 3 - THE CLEAN METHOD (secrets mount into the container automatically)
         Run this from the VPS HOST prompt (after you typed 'exit')
================================================================================
cd /opt/ses/.hermes
doppler secrets download --no-file --format env > .env
chmod 600 .env
docker restart hermes-gateway

# WHY THIS WORKS:  /opt/ses/.hermes  is bind-mounted into the container at  /opt/data
# So the .env you just wrote on the host appears at  /opt/data/.env  inside the
# container. Hermes reads it after the restart. No Doppler needed inside the
# container at all.

================================================================================
PART 4 - VERIFY the container picked it up
================================================================================
docker logs --tail 30 hermes-gateway
# Expect: gateway online, no auth/401/key-not-found errors.
# Optional check that your .env has the key names (not values):
grep -c '=' /opt/ses/.hermes/.env
