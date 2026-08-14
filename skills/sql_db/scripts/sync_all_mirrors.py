"""
sync_all_mirrors.py — Run all Asana → ops.db nightly sync scripts in sequence.

Order: stock_inventory → suppliers → build_schedule → parts_control
       → procurement_tasks → qc_issues

Exits with code 1 if any sync script exits with an error.
Called by the nightly cron job at 01:00 Europe/London Mon-Sun.

Usage:
    python sync_all_mirrors.py
    python sync_all_mirrors.py --dry-run   (passes --dry-run to every sub-script)
"""

import argparse
import subprocess
import sys
from pathlib import Path

PYTHON = r"C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\python.exe"
SCRIPTS_DIR = Path(__file__).parent

SYNC_SCRIPTS = [
    "sync_stock_inventory.py",
    "sync_suppliers.py",
    "sync_build_schedule.py",
    "sync_parts_control.py",
    "sync_procurement_tasks.py",
    "sync_qc_issues.py",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run all Asana mirror sync scripts")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    errors = []
    for script in SYNC_SCRIPTS:
        cmd = [PYTHON, str(SCRIPTS_DIR / script)]
        if args.dry_run:
            cmd.append("--dry-run")
        print(f"\n--- {script} ---", flush=True)
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            errors.append(script)

    if errors:
        print(f"\nFailed: {errors}", file=sys.stderr)
        sys.exit(1)
    print("\nAll mirror syncs completed successfully.")


if __name__ == "__main__":
    main()
