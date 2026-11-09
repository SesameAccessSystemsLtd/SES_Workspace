# Jibble ↔ Asana trial: handover (9 Nov 2026, updated after the import)

Branch `jibble-asana-sync`, PR #1 (open, unmerged; the user merges). Read `SKILL.md` for the design.

## Goal
Pull TeamUp/4.0 Service Schedule tasks and UK lift locations into Jibble, log what Jibble records
(clock-ins, time entries, time off) and map it into Asana. Trial ends about 12 Nov. Test users: Giles
(holiday test), Andy (TeamUp tasks only), Danny (same as Andy).

## Rules (non-negotiable)
- Secrets live in Doppler only: project **`openclaw`**, config **`prd`**. GitHub holds one read-only `DOPPLER_TOKEN`.
  Do **not** use `vps_hermes/prd` for Jibble: its Jibble keys are old and Jibble rejects them (`invalid_client`).
  Never print or commit keys; repo is public, so logs show counts and IDs only. Client addresses are never committed
  (`data/Sesame_Lift_Locations/` is gitignored; the location plan JSON is local-only).
- Doppler names: JIBBLE_KEY_ID, JIBBLE_API_KEY, ASANA_TOKEN (still to add), JIBBLE_SYNC_PEOPLE_JSON (private scope and email aliases).
- Before any write, reconcile against the live system (ids are deterministic uuid5, so re-runs are safe).
- SIS-first: Sesame requests start from the SIS-CURRENT pointer (SharePoint) via the M365 connector.

## State
Done: sync code, 39 tests passing, workflow `.github/workflows/jibble-sync.yml` (cron */15), Andy and Danny added to Jibble
Site Team (a member can be in only one group), Doppler JIBBLE_SYNC_PEOPLE_JSON saved.

**Imported into Jibble (9 Nov):** 203 locations, 37 projects and the client "Service & Repairs", 0 errors; a re-run of the
dry run finds nothing left to create (Jibble now 205 locations, 41 projects, 3 clients). Geofence radius 50 m; the first
location was read back and Jibble stored 50.0 m.

The earlier token failure was not TLS, proxy or key formatting: the importer was pointed at `vps_hermes`, whose Jibble keys
are dead. With `openclaw/prd` the token request returns 200. Do not use the PowerShell or .bat importers.

## Re-running the import (only if the plan changes)
`doppler run -p openclaw -c prd -- python jibble_push_plan.py jibble_plan.json --radius=50` (dry run), then add `--apply`.
The plan file still says `radius_m: 300`; always pass `--radius`. The importer stops after the first location if Jibble
stores a different radius, and creates no projects if the location check stops the run. Existing ids are skipped, so
changing the radius of locations already in Jibble needs a separate update, not a re-run.

## Next steps
1. Switch-on needs the user:
   - `ASANA_TOKEN` (dedicated Asana admin account) in `openclaw/prd`. `cli.py` falls back to
     `ASANA_PERSONAL_ACCESS_TOKEN`, which already exists there; do not rely on it unless it is confirmed to be the admin
     sync account (a person who tracks their own time must not be the sync user).
   - GitHub secret `DOPPLER_TOKEN`: a read-only service token for **`openclaw/prd`** (or a dedicated config that
     references it), not `vps_hermes`.
   - Merge PR #1, then an optional `workflow_dispatch` dry run. Until then Asana won't update from clock-ins.
2. Watch the 50 m radius. Coordinates are postcode centroids, not buildings; with indoor GPS error, clock-ins may miss
   their site. If they do, move to 100 m. Site Team signs in without geofence enforcement but still receives the locations.
3. Giles holiday end-to-end test; confirm Danny sees this week's projects (Wingate House / 25 Sackville St Tue 10 Nov,
   Hotel Indigo Wed 11, British Library Pigott Thu 13).

## Open questions for the user
Meaning of "P 1-3", contracted hours, workshop code 0006 vs 0009.
