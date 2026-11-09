# Jibble ↔ Asana trial: handover (9 Nov 2026)

Branch `jibble-asana-sync`, PR #1 (open, unmerged; the user merges). Read `SKILL.md` for the design.

## Goal
Pull TeamUp/4.0 Service Schedule tasks and UK lift locations into Jibble, log what Jibble records
(clock-ins, time entries, time off) and map it into Asana. Trial ends about 12 Nov. Test users: Giles
(holiday test), Andy (TeamUp tasks only), Danny (same as Andy).

## Rules (non-negotiable)
- Secrets live in Doppler only (project `vps_hermes`, config `prd`). GitHub holds one read-only `DOPPLER_TOKEN`.
  Never print or commit keys; repo is public, so logs show counts and IDs only. Client addresses are never committed
  (`data/Sesame_Lift_Locations/` is gitignored; the location plan JSON is local-only).
- Doppler names: JIBBLE_KEY_ID, JIBBLE_API_KEY, ASANA_TOKEN (still to add), JIBBLE_SYNC_PEOPLE_JSON (private scope and email aliases).
- Before any write, reconcile against the live system (ids are deterministic uuid5, so re-runs are safe).
- SIS-first: Sesame requests start from the SIS-CURRENT pointer (SharePoint) via the M365 connector.

## State
Done: sync code, 39 tests passing, workflow `.github/workflows/jibble-sync.yml` (cron */15), Andy and Danny added to Jibble
Site Team (a member can be in only one group), Doppler JIBBLE_SYNC_PEOPLE_JSON saved.
**Not done: nothing has been imported into Jibble.** 203 locations and 37 projects are planned (local `jibble_plan.json`,
UK-wide, retired postcodes geocoded). The Windows PowerShell importer failed on the token request: first a 400, then a blank
error body. Cause unknown and unverified. Candidates: TLS 1.2 not forced in PowerShell 5.1, proxy, key formatting.
Key lengths look right (id 36, secret 48). Do not continue with PowerShell or .bat files.

## Next steps
1. Add a manual (`workflow_dispatch`) import mode to the Actions workflow, or run `jibble_push_plan.py` in a terminal with
   `doppler run -p vps_hermes -c prd -- python jibble_push_plan.py jibble_plan.json` (dry run first, then `--apply`).
   Print the HTTP status and Jibble's error body on failure (never the keys).
2. Token: POST https://identity.prod.jibble.io/connect/token, client_credentials. API base https://workspace.prod.jibble.io/v1.
3. Create ONE location, read it back, check the stored geofence radius, then load the rest; then client, then projects.
   Skip anything within 50 m of an existing location.
4. Radius: `radius_m` is now 50 (was 300). Risks: indoor GPS error of 20-50 m will cause missed clock-ins, and Jibble may require
   at least 300 m for geofence automation. If the readback shows otherwise, fall back to 100 m. Site Team signs in without
   geofence enforcement but still receives the locations.
5. Switch-on needs the user: ASANA_TOKEN (dedicated admin) in Doppler, read-only `DOPPLER_TOKEN` in GitHub, merge PR #1,
   optional dry run. Until then Asana won't update from clock-ins.
6. Then: Giles holiday end-to-end test; confirm Danny sees this week's projects (Wingate House / 25 Sackville St Tue 10 Nov,
   Hotel Indigo Wed 11, British Library Pigott Thu 13).

## Open questions for the user
Meaning of "P 1-3", contracted hours, workshop code 0006 vs 0009.
