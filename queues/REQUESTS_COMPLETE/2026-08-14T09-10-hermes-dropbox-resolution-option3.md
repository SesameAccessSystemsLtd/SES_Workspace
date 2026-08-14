---
From: hermes
To: asana_agent (SAP)
Type: asana_write
Status: COMPLETE
Completed: 2026-08-14T09:30:34.969299+00:00
Note: task created (My Tasks): 1217485837798256
Priority: normal
Created: 2026-08-14T09:10:00Z
---

## Request
Action: create_task
Project GID: 1214604066893979  (SAS "Institutional Ticketing System / Edge cases" intake — canonical SAS infrastructure intake)
Task Name: Dropbox scl/fo link → local parts-folder resolution — design decision (Option 3 approved)
Assignee: (unassigned — Giles reviews)

## Summary (decision record)
Giles approved Option 3 for resolving Karol's cross-account Dropbox shared links
(scl/fo) to a local parts-folder path. Context and advisory below.

### Problem
Karol's sub-BOM shared links (`dropbox.com/scl/fo/...`) are owned by HIS personal
Dropbox account. A SAS app access token (a different account) authenticates and
returns the folder NAME but NO `path_lower` from get_shared_link_metadata, so the
Dropbox API `resolve_to_local()` cannot map the link to a local path. Verified live:
- token works (`list_folder('')` → 200)
- Karol link → status 200, `name: "1.Long Leading Time"`, `path_lower: None`
- the folder IS already synced locally under the standard convention.

### Options considered
1. **Strict local name-match** — fast but fragile to Asana-vs-local folder name
   drift (e.g. Asana "St George's House" → local "St George's House West"; Asana
   "Langley Street" → local "Langley street").
2. **Add Karol's folder to the token's account (API-only)** — REJECTED. Even if
   the folder resolves, `resolve_to_local()` maps the API `path_lower` onto the
   LOCAL synced root, which belongs to the SAS account, not the token account. It
   creates a parallel namespace nobody syncs locally.
3. **Job-number auto-find + small ambiguity lookup table (APPROVED)** — robust to
   the token-account mismatch and to folder-name drift; degrades gracefully.

### Implementation (done 2026-08-14)
- New `SES_Workspace\skills\dropbox\local_dropbox_resolver.py`:
  - `resolve_by_name(job, project_name, bom_name)` → local `3. Projects\<project>\…\2.3. Order\<sub_bom>` path
  - `try_api_then_local(...)` composite: Dropbox API first, then local name-match
  - `PROJECT_NAME_OVERRIDES` table for Asana→local folder name mismatches
    (seeded: St George's House → St George's House West)
- `bom_dispatch_server._resolve_locally()` now uses the composite resolver and
  derives the real project name from the control task
  ("Karol Generate Entire BOM - 1595 - St George's House" → "St George's House").
- Listener restarted on 127.0.0.1:8765 with the new code + `.env` token loader.

### Verified
`_resolve_locally` on the live 1595 payload resolves to
`C:\Users\GilesHugo\SAS Dropbox\3. Projects\St George's House West\2.3. Order\1.Long Leading Time`
(EXISTS on host: True). Also verified Langley 1037 → `…\Langley street\Design\2.3. Order\5.Button Stations`.

### Maintenance note
New Asana-project vs local-folder-name mismatches should be added to
`PROJECT_NAME_OVERRIDES` in `local_dropbox_resolver.py` as they surface (Giles or
whoever maintains the DDL should keep it current).

## Context
Giles requested the button/pipeline plumbing match his manual input shape (job+name
entity, control task, Dropbox URL, parts-folder path). This resolution approach was
the advisory decision; Option 3 chosen explicitly by Giles. Edge-case surface kept
here per the canonical SAS infrastructure intake so the decision is durable and
discoverable.
