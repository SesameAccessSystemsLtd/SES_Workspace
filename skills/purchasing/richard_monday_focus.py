#!/usr/bin/env python
"""Weekly Monday focus-task generator for Richard - REQUESTS-queue entrypoint.
============================================================================
Standalone caller for the `--monday` path of `richard_rolling_capture.py`.

Every Monday it generates ONE task destined for the Richard / Giles 1:1 project
(Action items section) that is assigned to Richard and due today. The creation
never touches Asana directly - it is staged as a REQUESTS queue file (header
`Status: PENDING` + `To: asana_agent`) plus a companion `.json`, which the
execute_requests.py processor (cron eb59aca7658d / manual) turns into the Asana
create - gated on Giles approval.

Read-only on Asana apart from generating the local REQUESTS file.

Usage
-----
  # Dry-run preview (default; never writes anything, safe to test on any day):
  PYTHONPATH="" python richard_monday_focus.py

  # Real staging into queues/REQUESTS/ (only meaningful/safe on an actual Monday;
  # a non-Monday real run refuses to write the file unless --force is supplied):
  PYTHONPATH="" python richard_monday_focus.py --go --force

  # Point the staged output somewhere other than the live queue (e.g. a proof dir):
  PYTHONPATH="" python richard_monday_focus.py --out /path/to/dir

Environment prefix is required because the workspace python needs PYTHONPATH=""
to avoid a stray cp311 path on this box. Run with:
  C:/Users/GilesHugo/SES_Workspace/.venv/Scripts/python.exe
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import richard_rolling_capture as rrc  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Weekly Monday focus-task generator (REQUESTS-queue staging only)")
    ap.add_argument("--go", action="store_true",
                    help="real mode: write the REQUESTS file (default dry-run / preview only)")
    ap.add_argument("--force", action="store_true",
                    help="with --go, write even on a non-Monday (staging/recovery)")
    ap.add_argument("--out", default=None,
                    help="output directory for the REQUESTS .md+json "
                         "(default: queues/REQUESTS when --go, print-only without)")
    ap.add_argument("--note", default=None, help="optional suffix appended to the task name")
    a = ap.parse_args(argv)

    # The @monday generation ALWAYS requires a fresh 60-day scan + baseline report
    # so the task notes carry the current benchmark summary.
    dry = not a.go
    out_dir = a.out
    if dry:
        out_dir = None

    token = rrc.load_token()
    api = rrc.Asana(token)
    issues, counts = rrc.scan(api, days=60)
    direction_summary, total_cur = rrc.report(issues, counts)

    # force semantics:
    #  - a *dry/preview* run is always allowed to print its REQUESTS file (no write
    #    to Asana or the queue), regardless of the week day -> force=True.
    #  - a *go* (real) write into queues/REQUESTS/ is gated to an actual Monday
    #    unless --force is explicitly supplied.
    rrc.generate_monday_request(token, counts, direction_summary,
                                total_cur, days=60,
                                out_dir=out_dir,
                                force=a.force if not dry else True,
                                dry=dry, note=a.note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
