# Unblocking the SES_Workspace GitHub push (secret-scan blocked)

Status: READY FOR YOUR APPROVAL - no changes made yet. Giles, 2026-08-14.

--------------------------------------------------------------------------------
THE PROBLEM (confirmed, read-only)
--------------------------------------------------------------------------------
The initial commit `bd05355 "Initial commit"` swept in 1,995 files wholesale,
including live data + generated artifacts that GitHub's secret scanner refuses.
`.gitignore` alone can't fix it because the bad files are already IN that commit.

GOOD NEWS: the push was REJECTED, so the remote
(https://github.com/SesameAccessSystemsLtd/SES_Workspace) has NOTHING.
This is a "build the first clean commit" job, NOT a history-rewrite-after-leak
job. No filter-repo, no force-push, no file deletion.

-------------------------------------------------------------------------------
WHAT IS BLOCKING (what's in the committed tree)
-------------------------------------------------------------------------------
Keep (source / harness-neutral)          Count   Why
  skills/ (source)        ...            729     <- but NOT the pipeline_runs artifacts
  memory/                 141                      obsidian notes -> decide if private
  queues/                 138                      DO NOT COMMIT if they hold live tokens
  golden_index/            33                      derived index - review
  plans/, docs/, reference/, reports/, views/, lib/ (path_config!), *.md shims
  categorize.py, scripts/

EXCLUDE (sensitive / generated / build junk)
  db/                            81   live crm.db, email_send_log.jsonl, backups, fuse_hidden
  data/                          61   processed ids, asana_write_log, approval queues, stage5 scripts
  graphify-out/                  649  generated AST cache
  _copilog/graph/ + graphifi     109  build cache
  _copilot/PATH_CONFIG.yaml      1    hardcoded C:/Users/GilesHugo/... Windows paths
  pipeline run artifacts in skills/**/pipeline_runs/  ~700  BOM CSVs/xlsx/manifests

KNOWN HARDCODED SECRETS / HAZARDS IN COMMITTED FILES:
  data/_run_stage5.py, _stage5_runner.py
     - Dropbox share link + rlkey  (live token)
     - Asana project/task GIDs      (operational)
     - invokes pipeline with --commit  (would run a live MRP commit if executed)
  lib/path_config.py + _copilot/PATH_CONFIG.yaml
     - hardcoded C:/Users/GilesHugo/... absolute paths (couples repo to your PC)

-------------------------------------------------------------------------------
THE FIX WE RECOMMEND (NEEDS YOUR "GO")
-------------------------------------------------------------------------------
1. Write a proper .gitignore:
     db/ data/ graphify-out/ _copilot/graph/ *.log *.jsonl .fuse_hidden*
     .env* __pycache__ .venv/ skills/**/pipeline_runs/ *_stage5*
2. Clean-rebuild the initial commit WITHOUT deleting any files on disk:
     git rm -r --cached .     # untrack everything from the index (files STAY on disk)
     git add <curated set>    # add ONLY the source/harness-neutral files
     git commit --amend -m "Initial import of SES_Workspace (clean, no data/secrets)"
3. Handle the hardcoded paths + stage5 scripts: scrub to placeholders OR exclude,
   per your review.
4. git push  (the scanner should now pass)
5. Verify with git log --stat + a pre-push secret scan.

WORKING TREE UNTOUCHED. No file is deleted anywhere. Only git's tracked set changes.

--------------------------------------------------------------------------------
TWO DECISIONS I NEED FROM YOU
--------------------------------------------------------------------------------
1. Scope of repo contents (above keep/exclude map) - OK, or adjust?
2. The two stage5 --commit scripts + path_config: scrub to placeholders, or exclude?

Say "GO" and I execute exactly the approved plan. Nothing changed yet.
