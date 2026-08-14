"""
MRP Pipeline Runner — Stages 1–5
=================================
Orchestrates the full BOM-to-Asana import pipeline from a single command.

  Stage 1: BOM Consolidation    (1.BOM IMPORT into Asana/BOM_Consolidator_SLe_v4.py)
  Stage 2: Golden Index Match   (2.Stock Controls/02_golden_index_match.py)
  Stage 3: Stock Check          (2.Stock Controls/03_stock_check.py)
  Stage 4: Staging Export       (2.Stock Controls/04_staging_export.py)
  Stage 5: Asana Import         (1.BOM IMPORT into Asana/BOM_IMPORT_4_Asana_v4.py)

Usage:
    # Run all stages (Stage 5 dry-run by default — safe):
    python run_mrp_pipeline.py \\
        --project-name "1555 - RB Lock Update" \\
        --parts-folder "C:/path/to/bom/xlsx/folder" \\
        --dropbox-url  "https://www.dropbox.com/..." \\
        --asana-url    "https://app.asana.com/..."

    # Stop after Stage 4 (to review the Excel before committing):
    python run_mrp_pipeline.py --project-name "..." --parts-folder "..." --stop-after 4

    # Re-run Stage 5 on an existing Stage 4 file and commit to Asana:
    python run_mrp_pipeline.py \\
        --project-name "1555 - RB Lock Update" \\
        --parts-folder "..." \\
        --stage4-file  "1.BOM IMPORT into Asana/pipeline_runs/.../stage4_staging_review.xlsx" \\
        --commit

Required:
    --project-name   Short identifier, e.g. "1555 - RB Lock Update"
    --parts-folder   Folder containing the source BOM .xlsx files

Optional:
    --dropbox-url    Dropbox folder link (embedded in Asana task descriptions)
    --asana-url      Asana project/task URL (embedded in task descriptions)
    --legacy-folder  Override legacy BOM folder for drawing pack lookup
    --output-dir     Override output directory (auto-generated if omitted)
    --threshold      Golden Index fuzzy match threshold 0-100 (default: 80)
    --commit         Create Asana tasks in Stage 5 (default: dry-run only)
    --pause          Seconds between Asana API calls (default: 0.3)
    --stop-after     Stop after this stage: 1, 2, 3, or 4 (default: 5)
    --stage4-file    Provide an existing Stage 4 xlsx to skip Stages 1-4
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths — all relative to this script
# ---------------------------------------------------------------------------
BASE        = Path(__file__).parent
S1_DIR      = BASE / '1.BOM IMPORT into Asana'
S25_DIR     = BASE / '2.Stock Controls'
LEGACY_DEFAULT = BASE / '3.Drawing_BOM_Extractor' / 'Drawing_Legacy_BOMS'
GOLDEN_INDEX   = S25_DIR / 'Merged_Golden_Index_Rev4.csv'  # legacy fallback only
# Source the Golden Index from ops.db (golden_index_rev6) — single source of truth.
# Set to False to force the legacy CSV path.
USE_DB_GOLDEN_INDEX = True


def _slug(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_-]', '_', name).strip('_')


def _load_token() -> str | None:
    token = os.environ.get('ASANA_PERSONAL_ACCESS_TOKEN') or os.environ.get('ASANA_PAT')
    if not token:
        cfg = BASE / 'ASANAconfig.json'
        if cfg.exists():
            with open(cfg) as f:
                data = json.load(f)
            token = data.get('ASANA_PERSONAL_ACCESS_TOKEN') or data.get('ASANA_PAT')
    return token


def _check_existing_root(project_name: str, project_gid: str, token: str) -> list[dict]:
    """
    Query Asana Build Orders project for existing 'ALL BOMS - {project_name}' root tasks.
    Returns a list of dicts with gid, name, created_at for all matches.
    Returns empty list if no existing root found.
    """
    import requests as _req
    headers = {'Authorization': f'Bearer {token}'}
    url = f'https://app.asana.com/api/1.0/projects/{project_gid}/tasks'
    params = {'opt_fields': 'name,created_at,completed', 'limit': 100}
    try:
        r = _req.get(url, headers=headers, params=params)
        r.raise_for_status()
        tasks = r.json().get('data', [])
        search_name = f"ALL BOMS - {project_name}".lower()
        matches = []
        for t in tasks:
            t_name = t.get('name', '').lower()
            if search_name in t_name:
                matches.append({
                    'gid': t['gid'],
                    'name': t.get('name', ''),
                    'created_at': t.get('created_at', ''),
                    'completed': t.get('completed', False),
                })
        return matches
    except Exception as e:
        print(f"  [WARN] Could not check for existing BOM roots: {e}")
        return []


def _header(label: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")


def _run(label: str, cmd: list[str], cwd: Path, env: dict) -> int:
    _header(label)
    result = subprocess.run(cmd, cwd=str(cwd), env=env, text=True)
    return result.returncode


def _run_stage1(parts_folder: Path, legacy_folder: Path, out_csv: Path) -> int:
    """
    Stage 1 uses importlib so we can redirect OUTPUT_FILE without modifying the script.
    """
    _header('STAGE 1 — BOM Consolidation')
    try:
        spec = importlib.util.spec_from_file_location(
            'consolidator', S1_DIR / 'BOM_Consolidator_SLe_v4.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.OUTPUT_FILE = out_csv
        mod.run_logic(parts_folder, legacy_folder)
        return 0
    except SystemExit as e:
        return int(str(e)) if str(e).isdigit() else 1
    except Exception as e:
        print(f"ERROR in Stage 1: {e}")
        return 1


def main():
    parser = argparse.ArgumentParser(
        description='MRP Pipeline Runner — Stages 1–5',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--project-name',  required=True)
    parser.add_argument('--parts-folder',  required=True)
    parser.add_argument('--dropbox-url',   default='')
    parser.add_argument('--asana-url',     default='')
    parser.add_argument('--legacy-folder', default=None)
    parser.add_argument('--output-dir',    default=None)
    parser.add_argument('--threshold',     type=int, default=80)
    parser.add_argument('--commit',        action='store_true')
    parser.add_argument('--pause',         type=float, default=0.3)
    parser.add_argument('--stop-after',    type=int, default=5, choices=[1, 2, 3, 4, 5])
    parser.add_argument('--stage4-file',   default=None,
                        help='Skip Stages 1-4, use this existing Stage 4 xlsx directly')
    parser.add_argument('--project-gid',   default=None,
                        help='Override target Asana project GID (default: Build Orders 848657415032331). '
                             'Standard BOM imports: omit this — default is correct. '
                             'Long Lead BOM imports: provide the specific job project GID.')
    parser.add_argument('--preflight',    action='store_true',
                        help='Validate all inputs and print a summary. Does NOT run any pipeline stages. '
                             'Exit 0 = all clear. Exit 1 = problems found. Run this first to self-check.')
    parser.add_argument('--approve',      action='store_true',
                        help='Acknowledge pre-commit checks and proceed. Required when --commit is set and an '
                             'existing ALL BOMS root is found. Pass this to confirm you want to create a new '
                             'root alongside the existing one (safe when the existing root is stale/incomplete).')
    args = parser.parse_args()

    # -----------------------------------------------------------------------
    # Forbidden GID guard — hard abort before any work
    # -----------------------------------------------------------------------
    FORBIDDEN_PROJECT_GIDS = {
        '1192686469436889': (
            'Project Status Overview — this is the Asana project that hosts purchasing control tasks, '
            'NOT a job project. It must never be used as a BOM import target. '
            'For standard BOM imports omit --project-gid entirely (defaults to Build Orders 848657415032331). '
            'For Long Lead imports ask Giles for the correct job project GID.'
        ),
    }
    if args.project_gid and args.project_gid in FORBIDDEN_PROJECT_GIDS:
        print(f"\nERROR: --project-gid {args.project_gid} is FORBIDDEN.")
        print(f"Reason: {FORBIDDEN_PROJECT_GIDS[args.project_gid]}")
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Validate inputs
    # -----------------------------------------------------------------------
    parts_folder = Path(args.parts_folder)
    legacy_folder = Path(args.legacy_folder) if args.legacy_folder else LEGACY_DEFAULT
    token = _load_token()

    import re as _re
    errors = []
    if not parts_folder.exists():
        errors.append(f"--parts-folder not found: {parts_folder}")
    if not legacy_folder.exists():
        errors.append(f"Legacy BOM folder not found: {legacy_folder}")
    if not GOLDEN_INDEX.exists():
        errors.append(f"Golden Index not found: {GOLDEN_INDEX}")
    if not token:
        errors.append("No Asana token found — set ASANA_PERSONAL_ACCESS_TOKEN env var or check ASANAconfig.json")
    
    # Required input validation — flag any missing before proceeding
    if not args.project_name or not args.project_name.strip():
        errors.append("--project-name is missing or empty")
    if not args.dropbox_url or not args.dropbox_url.strip():
        errors.append("--dropbox-url is missing — must provide the Dropbox folder link")
    else:
        if not _re.match(r'^https?://', args.dropbox_url.strip()):
            errors.append(f"--dropbox-url does not look like a valid URL: {args.dropbox_url}")
    if not args.asana_url or not args.asana_url.strip():
        errors.append("--asana-url is missing — must provide the Asana control task URL")
    else:
        if not args.asana_url.strip().startswith('https://app.asana.com/'):
            errors.append(f"--asana-url does not look like a valid Asana URL: {args.asana_url}")

    # -----------------------------------------------------------------------
    # --preflight: print summary and exit (no stages run)
    # -----------------------------------------------------------------------
    if args.preflight:
        print(f"\n{'='*60}")
        print(f"  BOM PIPELINE PREFLIGHT CHECK")
        print(f"{'='*60}")
        print(f"  Project name  : {args.project_name}")
        print(f"  Parts folder  : {parts_folder}")
        print(f"    -> {'EXISTS ✓' if parts_folder.exists() else 'NOT FOUND ✗'}")
        print(f"  Dropbox URL   : {args.dropbox_url or '(not set)'}")
        print(f"  Asana URL     : {args.asana_url or '(not set)'}")
        proj_gid_display = args.project_gid or '848657415032331 (Build Orders — default, correct for standard BOM)'
        print(f"  Project GID   : {proj_gid_display}")
        print(f"  Golden Index  : {'EXISTS ✓' if GOLDEN_INDEX.exists() else 'NOT FOUND ✗'}")
        print(f"  Asana token   : {'FOUND ✓' if token else 'MISSING ✗'}")
        print(f"  Stop after    : Stage {args.stop_after}")
        print(f"  Commit mode   : {'COMMIT' if args.commit else 'DRY-RUN (safe)'}")
        if args.commit:
            approve_text = "YES - proceeding past idempotency checks" if args.approve else "NO - will stop if existing root found"
            print(f"  Approve flag  : {approve_text}")
        if args.stage4_file:
            sf = Path(args.stage4_file)
            print(f"  Stage 4 file  : {sf}")
            print(f"    -> {'EXISTS ✓' if sf.exists() else 'NOT FOUND ✗'}")
            if not sf.exists():
                errors.append(f"--stage4-file not found: {sf}")
        print(f"{'='*60}")
        if errors:
            print(f"  PREFLIGHT FAILED — {len(errors)} problem(s):")
            for e in errors:
                print(f"    ✗ {e}")
            print(f"{'='*60}\n")
            sys.exit(1)
        else:
            print(f"  ALL CHECKS PASSED — ready to run.")
            print(f"{'='*60}\n")
            sys.exit(0)

    # -----------------------------------------------------------------------
    # Non-preflight run: abort on any validation error
    # -----------------------------------------------------------------------
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Output directory
    # -----------------------------------------------------------------------
    date_str = datetime.now().strftime('%Y%m%d_%H%M')
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        out_dir = S1_DIR / 'pipeline_runs' / f"{_slug(args.project_name)}_{date_str}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Pre-run banner — always print resolved args so errors are visible before stages start
    print(f"\n{'='*60}")
    print(f"  BOM PIPELINE — {args.project_name}")
    print(f"{'='*60}")
    print(f"  Parts folder : {parts_folder}")
    print(f"  Dropbox URL  : {args.dropbox_url or '(not set)'}")
    print(f"  Asana URL    : {args.asana_url or '(not set)'}")
    print(f"  Project GID  : {args.project_gid or '848657415032331 (Build Orders — default)'}")
    print(f"  Stop after   : Stage {args.stop_after}")
    print(f"  Commit mode  : {'COMMIT — WRITING TO ASANA' if args.commit else 'DRY-RUN'}")
    print(f"  Outputs      : {out_dir}")
    print(f"{'='*60}")

    # -----------------------------------------------------------------------
    # Token (required from Stage 3 onward) — already loaded in validate block
    # -----------------------------------------------------------------------
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    if token:
        env['ASANA_PERSONAL_ACCESS_TOKEN'] = token

    # -----------------------------------------------------------------------
    # File paths for each stage output
    # -----------------------------------------------------------------------
    stage1_csv  = out_dir / 'stage1_consolidated.csv'
    stage2_csv  = out_dir / 'stage2_bom_with_kan.csv'
    stage3_csv  = out_dir / 'stage3_bom_stock_check.csv'
    stage4_xlsx = out_dir / 'stage4_staging_review.xlsx'
    stage5_csv  = out_dir / f'stage5_import_manifest_{date_str}.csv'

    # -----------------------------------------------------------------------
    # --stage4-file: skip straight to Stage 5
    # -----------------------------------------------------------------------
    if args.stage4_file:
        stage4_xlsx = Path(args.stage4_file)
        if not stage4_xlsx.exists():
            print(f"ERROR: --stage4-file not found: {stage4_xlsx}")
            sys.exit(1)
        print(f"\nSkipping Stages 1-4. Using: {stage4_xlsx}")
    else:
        # -------------------------------------------------------------------
        # Stage 1 — BOM Consolidation
        # -------------------------------------------------------------------
        rc = _run_stage1(parts_folder, legacy_folder, stage1_csv)
        if rc != 0 or not stage1_csv.exists():
            print("ERROR: Stage 1 failed. Aborting.")
            sys.exit(1)
        if args.stop_after == 1:
            print(f"\nStopped after Stage 1. Output: {stage1_csv}")
            return

        # -------------------------------------------------------------------
        # Stage 2 — Golden Index Match
        # -------------------------------------------------------------------
        # Golden Index source of truth is now ops.db (golden_index_rev6).
        # The Rev4 CSV remains only as an automatic fallback inside the matcher.
        stage2_cmd = [
            sys.executable, '02_golden_index_match.py',
            '--input',        str(stage1_csv),
            '--from-db',
            '--part-col',     'stock number',   # Rev6 matches the BOM's vendor stock value
            '--output',       str(stage2_csv),
            '--threshold',    str(args.threshold),
        ]
        if not USE_DB_GOLDEN_INDEX:
            # Legacy override: fall back to the CSV if DB sourcing is disabled.
            stage2_cmd = [
                sys.executable, '02_golden_index_match.py',
                '--input',        str(stage1_csv),
                '--golden-index', str(GOLDEN_INDEX),
                '--output',       str(stage2_csv),
                '--threshold',    str(args.threshold),
            ]
        rc = _run('STAGE 2 — Golden Index Match', stage2_cmd, cwd=S25_DIR, env=env)
        if rc != 0 or not stage2_csv.exists():
            print("ERROR: Stage 2 failed. Aborting.")
            sys.exit(1)
        if args.stop_after == 2:
            print(f"\nStopped after Stage 2. Output: {stage2_csv}")
            return

        # -------------------------------------------------------------------
        # Stage 3 — Stock Check
        # -------------------------------------------------------------------
        rc = _run('STAGE 3 — Stock Check', [
            sys.executable, '03_stock_check.py',
            '--input',  str(stage2_csv),
            '--output', str(stage3_csv),
            '--pause',  str(args.pause),
        ], cwd=S25_DIR, env=env)
        if rc != 0 or not stage3_csv.exists():
            print("ERROR: Stage 3 failed. Aborting.")
            sys.exit(1)
        if args.stop_after == 3:
            print(f"\nStopped after Stage 3. Output: {stage3_csv}")
            return

        # -------------------------------------------------------------------
        # Stage 4 — Staging Export
        # -------------------------------------------------------------------
        rc = _run('STAGE 4 — Staging Export', [
            sys.executable, '04_staging_export.py',
            '--input',  str(stage3_csv),
            '--output', str(stage4_xlsx),
        ], cwd=S25_DIR, env=env)
        if rc != 0 or not stage4_xlsx.exists():
            print("ERROR: Stage 4 failed. Aborting.")
            sys.exit(1)

        print(f"\n  Stage 4 review file: {stage4_xlsx}")
        if args.stop_after == 4:
            # Write state file so the commit step can resolve the correct path without guessing
            import json as _json
            state = {
                "project_name": args.project_name,
                "stage4_file": str(stage4_xlsx.resolve()),
                "run_dir": str(out_dir.resolve()),
                "timestamp": date_str,
                "status": "awaiting_commit",
            }
            state_path = BASE / "pipeline_last_run.json"
            state_path.write_text(_json.dumps(state, indent=2))
            print(f"  State saved: {state_path}")
            print("  Open the file, apply overrides (SKIP/HOLD/FORCE_ORDER), then re-run")
            print("  with --stage4-file <path> --commit when ready.")
            return

    # -----------------------------------------------------------------------
    # IDEMPOTENCY CHECK — Query for existing ALL BOMS root before Stage 5
    # -----------------------------------------------------------------------
    if args.commit:
        existing_roots = _check_existing_root(args.project_name, args.project_gid or '848657415032331', token)
        if existing_roots:
            print(f"\n{'='*60}")
            print(f"  ⚠️  EXISTING BOM ROOT(S) FOUND")
            print(f"{'='*60}")
            for root in existing_roots:
                print(f"  [{root['gid']}] {root['name']}")
                print(f"       Created: {root['created_at']}")
                print(f"       Completed: {root['completed']}")
            print(f"\n  --commit will create a NEW root alongside the existing one(s).")
            if args.approve:
                print(f"  ✓ --approve flag present — proceeding with Stage 5.\n")
            else:
                print(f"\n  To proceed, re-run with the --approve flag:")
                print(f"    --commit --approve")
                print(f"\n  To abort, remove --commit (dry-run).")
                print(f"{'='*60}\n")
                sys.exit(0)
        else:
            print(f"  [IDEMPOTENCY CHECK] No existing 'ALL BOMS - {args.project_name}' found — safe to proceed.")

    # -----------------------------------------------------------------------
    # Stage 4.5 - Revision Log
    # -----------------------------------------------------------------------
    if args.stage4_file:
        print(f"\n{'='*60}")
        print(f"  STAGE 4.5 - Revision Log")
        print(f"{'='*60}")
        
        # Extract rows with populated Mapped KAN
        try:
            import pandas as pd
            staging_df = pd.read_excel(stage4_xlsx, sheet_name='BOM Review', dtype=str).fillna('')
            mapped_kan_col = next((c for c in staging_df.columns if c.strip().lower() == 'mapped kan'), None)
            if mapped_kan_col:
                kan_rows = staging_df[staging_df[mapped_kan_col] != '']
                rev_log_path = out_dir / f"GI_revision_pending_{date_str}.xlsx"
                kan_rows.to_excel(rev_log_path, index=False)
                print(f"[STAGE 4.5] Revision log written: {rev_log_path.name} — review and run update_golden_index.py to apply.")
            else:
                print("WARNING: 'Mapped KAN' column not found in Stage 4 Excel.")
        except Exception as e:
            print(f"ERROR: Could not generate Stage 4.5 revision log: {e}")

    # -----------------------------------------------------------------------
    # Stage 5 - Asana Import
    # -----------------------------------------------------------------------
    mode = 'COMMIT' if args.commit else 'DRY-RUN'
    rc = _run(f'STAGE 5 — Asana Import ({mode})', [
        sys.executable, 'BOM_IMPORT_4_Asana_v4.py',
        '--input',        str(stage4_xlsx),
        '--project-name', args.project_name,
        '--dropbox-url',  args.dropbox_url,
        '--asana-url',    args.asana_url,
        '--output',       str(stage5_csv),
        '--sheet',        'BOM Review',
        '--pause',        str(args.pause),
        '--golden-index', '../2.Stock Controls/Merged_Golden_Index_Rev4.csv',
        *(['--project-gid', args.project_gid] if args.project_gid else []),
        *(['--commit'] if args.commit else []),
    ], cwd=S1_DIR, env=env)

    if rc != 0:
        print("ERROR: Stage 5 failed.")
        sys.exit(1)

    # Update state on successful commit
    if args.commit:
        state_path = BASE / "pipeline_last_run.json"
        if state_path.exists():
            try:
                import json as _json2
                state = _json2.loads(state_path.read_text())
                state["status"] = "committed"
                state["commit_timestamp"] = datetime.now().strftime('%Y%m%d_%H%M%S')
                state_path.write_text(_json2.dumps(state, indent=2))
                print(f"  State updated: {state_path} (status: committed)")
            except Exception:
                pass  # non-fatal — state file not critical

    print(f"\n{'='*60}")
    print(f"  Pipeline complete — {args.project_name}")
    print(f"  Manifest CSV : {stage5_csv}")
    if not args.commit:
        print()
        print("  Stage 5 ran in DRY-RUN mode. To push tasks to Asana, re-run with:")
        print(f"    python run_mrp_pipeline.py \\")
        print(f"        --project-name \"{args.project_name}\" \\")
        print(f"        --parts-folder \"{args.parts_folder}\" \\")
        print(f"        --dropbox-url  \"{args.dropbox_url}\" \\")
        print(f"        --asana-url    \"{args.asana_url}\" \\")
        print(f"        --stage4-file  \"{stage4_xlsx}\" \\")
        print(f"        --commit")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
