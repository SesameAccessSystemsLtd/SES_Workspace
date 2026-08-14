"""
run_drawing_pipeline.py — Drawing Pipeline Orchestrator

Usage:
  python run_drawing_pipeline.py --job-prefix 1575 [--dry-run] [--stop-after N]

Stages:
  1  01_populate_assembly_packs  Asana Drawing Control -> ops.db build_assembly_packs
  2  02_extract_drawing_boms     Download PDFs -> pdfplumber -> data/{job}_drawing_boms.csv
  3  02_map_parts_to_packs       drawing_boms.csv + sesame_pn -> parts_control.build_assembly_pack_gid
  4  03_write_to_asana           parts_control -> Asana Build Packs reference field (SAP only)

--stop-after 1  review build_assembly_packs table before PDF extraction
--stop-after 2  review drawing_boms.csv before DB mapping
--stop-after 3  review DB mapping before writing to Asana
"""

import argparse
import subprocess
import sys
from pathlib import Path

PYTHON  = r"C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe"
SCRIPTS = Path(__file__).parent


def run_stage(script: str, extra_args: list[str]) -> int:
    cmd = [PYTHON, str(SCRIPTS / script)] + extra_args
    print(f"\n{'='*60}")
    print(f"RUNNING: {script}")
    print(f"{'='*60}")
    result = subprocess.run(cmd)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description="Drawing pipeline orchestrator")
    parser.add_argument("--job-prefix", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--reset", action="store_true",
        help="Pass --reset to Stage 3 (02_map_parts_to_packs): clears all "
             "build_assembly_pack_gid for this job before re-mapping. "
             "Required when re-running after a late-added drawing task.",
    )
    parser.add_argument("--stop-after", type=int, default=3,
                        help="Stop after stage N (1=populate, 2=map, 3=write, 4=asana-write)")
    args = parser.parse_args()

    flags = ["--job-prefix", args.job_prefix]
    if args.dry_run:
        flags.append("--dry-run")

    stages = [
        (1, "01_populate_assembly_packs.py"),   # Asana -> build_assembly_packs table
        (2, "02_extract_drawing_boms.py"),       # Download PDFs + pdfplumber -> drawing_boms.csv
        (3, "02_map_parts_to_packs.py"),         # drawing_boms.csv -> parts_control.build_assembly_pack_gid
        (4, "03_write_to_asana.py"),             # parts_control -> Asana Build Packs reference field
    ]

    for stage_num, script in stages:
        if stage_num > args.stop_after:
            print(f"\nStopped after stage {args.stop_after} as requested.")
            break
        # Stage 3 (02_map_parts_to_packs) accepts --reset; no other stage does
        stage_flags = flags + (["--reset"] if args.reset and stage_num == 3 else [])
        rc = run_stage(script, stage_flags)
        if rc != 0:
            print(f"\nStage {stage_num} failed (exit {rc}). Stopping.")
            sys.exit(rc)

    print(f"\nPipeline complete.")


if __name__ == "__main__":
    main()
