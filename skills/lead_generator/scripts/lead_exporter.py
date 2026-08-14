"""
lead_exporter.py — Export lead records to CSV or JSON files.

Reads lead dicts from a JSON input file (or stdin) and writes them to the
configured OneDrive export path. Prints the canonical output file path to stdout
so the calling agent can capture it for DATAMAP.md.

Usage:
    # Export to CSV (default)
    python lead_exporter.py --input leads_enriched.json

    # Export to JSON
    python lead_exporter.py --input leads_enriched.json --format json

    # Custom output directory
    python lead_exporter.py --input leads_enriched.json --output-dir "C:\\custom\\path"

    # Read from stdin (pipe from lead_generator.py)
    python lead_generator.py --industry construction --count 10 --mode sample | python lead_exporter.py

Output:
    Prints the canonical output file path to stdout.
    Also writes a timestamped archive file alongside the canonical latest file.

Exit code 0 on success, 1 on error.
"""

import argparse
import csv
import json
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

EXPORT_DIR = os.environ.get(
    "LEAD_EXPORT_DIR",
    r"C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\OneDrive Documents\LAB. SESAME\updates",
)

# Canonical CSV column order — matches SQLite leads table schema
CSV_FIELDNAMES = [
    "company_name",
    "contact_name",
    "job_title",
    "email",
    "phone",
    "company_size",
    "region",
    "industry",
    "pain_points",
    "source",
    "generated_at",
    "apollo_id",
    "verified_email",
    "linkedin_url",
    "enrichment_status",
    "hubspot_contact_id",
    "hubspot_sync_status",
    "campaign_tag",
]


def export_leads(
    leads: list[dict],
    format: str = "csv",
    output_path: str | None = None,
) -> str:
    """
    Write leads to disk.

    Args:
        leads:       List of lead dicts
        format:      'csv' (default) or 'json'
        output_path: Full file path to write. If None, auto-generates a path
                     in EXPORT_DIR using a timestamped canonical filename.

    Returns:
        The canonical file path written (also printed to stdout by the caller).
    """
    os.makedirs(EXPORT_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    ext = "csv" if format == "csv" else "json"

    if output_path is None:
        latest_path = os.path.join(EXPORT_DIR, f"leads_latest.{ext}")
        archive_path = os.path.join(EXPORT_DIR, f"leads_{ts}.{ext}")
    else:
        latest_path = output_path
        base, file_ext = os.path.splitext(output_path)
        archive_path = f"{base}_{ts}{file_ext}"

    if format == "csv":
        _write_csv(leads, latest_path)
        _write_csv(leads, archive_path)
    else:
        payload = json.dumps(leads, indent=2, ensure_ascii=False)
        for path in (latest_path, archive_path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(payload)

    log.info("Exported %d leads (%s) → %s", len(leads), format, latest_path)
    log.info("Archive copy written to: %s", archive_path)
    return latest_path


def _write_csv(leads: list[dict], path: str) -> None:
    """Write leads list to a CSV file at path."""
    # Determine fieldnames: canonical order first, then any extra keys
    extra_keys = []
    for lead in leads:
        for key in lead:
            if key not in CSV_FIELDNAMES and key not in extra_keys:
                extra_keys.append(key)
    fieldnames = [f for f in CSV_FIELDNAMES if f in {k for lead in leads for k in lead}] + extra_keys

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for lead in leads:
            writer.writerow({k: lead.get(k, "") for k in fieldnames})


def main() -> None:
    parser = argparse.ArgumentParser(description="Export lead records to CSV or JSON.")
    parser.add_argument("--input", help="Path to JSON input file. Omit to read from stdin.")
    parser.add_argument(
        "--format",
        default="csv",
        choices=["csv", "json"],
        help="Output format (default: csv)",
    )
    parser.add_argument("--output-path", help="Full output file path. Overrides auto-generated path.")
    parser.add_argument("--output-dir", default=EXPORT_DIR, help="Output directory (used if --output-path not set).")
    args = parser.parse_args()

    # Read input
    try:
        if args.input:
            with open(args.input, encoding="utf-8") as f:
                leads = json.load(f)
        else:
            raw = sys.stdin.read()
            if not raw.strip():
                print("ERROR: No input provided (stdin was empty and --input not set).", file=sys.stderr)
                sys.exit(1)
            leads = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR reading input: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(leads, list):
        print("ERROR: Input must be a JSON array of lead objects.", file=sys.stderr)
        sys.exit(1)

    # Resolve output dir override
    global EXPORT_DIR
    if args.output_dir != EXPORT_DIR:
        EXPORT_DIR = args.output_dir

    try:
        output_path = export_leads(leads, format=args.format, output_path=args.output_path or None)
    except Exception as exc:
        print(f"ERROR exporting leads: {exc}", file=sys.stderr)
        sys.exit(1)

    # Print canonical path to stdout for caller capture
    print(output_path)


if __name__ == "__main__":
    main()
