"""
02_extract_drawing_boms.py
For each drawing task in build_assembly_packs (ops.db), downloads the latest
attachment from Asana (Excel preferred, PDF fallback), extracts BOM rows,
and writes a flat CSV tagged with the pack_reference.

Output: data/{job_prefix}_drawing_boms.csv
  Columns: pack_reference, pack_gid, drawing_task_gid, part_number, qty,
            description, material, vendor, source_file

Rows are written incrementally (one pack at a time) so a mid-run failure
does not lose already-extracted data. Re-running will overwrite the CSV.

Usage:
  python 02_extract_drawing_boms.py --job-prefix 1575 [--dry-run]
"""

import argparse
import csv
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from lib.pdf_bom_extractor import extract_pdf

load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)

ASANA_TOKEN = os.getenv("ASANA_PERSONAL_ACCESS_TOKEN")
HEADERS     = {"Authorization": f"Bearer {ASANA_TOKEN}", "Accept": "application/json"}
BASE        = "https://app.asana.com/api/1.0"

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p
DB_PATH     = _p("db.ops")
SKILL_DIR   = Path(__file__).parent.parent
DATA_DIR    = SKILL_DIR / "data"
PAUSE       = 0.3
TIMEOUT_CONNECT = 15   # seconds — Asana API connect
TIMEOUT_DOWNLOAD = 120  # seconds — large PDFs can be slow

FIELDNAMES = [
    "pack_reference", "pack_gid", "drawing_task_gid",
    "part_number", "qty", "description", "material", "vendor", "source_file",
]


def get_attachments(drawing_task_gid: str) -> list[dict]:
    r = requests.get(
        f"{BASE}/tasks/{drawing_task_gid}/attachments",
        headers=HEADERS,
        params={"opt_fields": "name,created_at,download_url"},
        timeout=TIMEOUT_CONNECT,
    )
    r.raise_for_status()
    return r.json().get("data", [])


def pick_attachment(attachments: list[dict]) -> tuple[dict | None, str]:
    """
    Return (attachment, source_type) where source_type is 'excel' or 'pdf'.
    Prefers the most recent Excel file; falls back to most recent PDF.
    """
    excels = [a for a in attachments
              if a.get("name", "").lower().endswith((".xlsx", ".xls"))]
    pdfs   = [a for a in attachments
              if a.get("name", "").lower().endswith(".pdf")]

    for group in (excels, pdfs):
        group.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    if excels:
        return excels[0], "excel"
    if pdfs:
        return pdfs[0], "pdf"
    return None, ""


def download_file(download_url: str, dest_path: str) -> bool:
    r = requests.get(download_url, stream=True, timeout=TIMEOUT_DOWNLOAD)
    if r.status_code != 200:
        return False
    with open(dest_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=32768):
            f.write(chunk)
    return True


def extract_excel(path: str, job_prefix: str | None = None) -> list[dict]:
    """
    Extract BOM rows from an Excel workbook.
    Scans every sheet for a header row containing 'part number' + 'description'.
    """
    try:
        import openpyxl
    except ImportError:
        print("    WARN: openpyxl not installed — cannot parse Excel. Falling back skipped.")
        return []

    from lib.pdf_bom_extractor import (
        is_header_row, header_column_map, parse_raw_line
    )

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    all_rows = []

    for sheet in wb.worksheets:
        table = [
            [str(cell.value).strip() if cell.value is not None else None for cell in row]
            for row in sheet.iter_rows()
        ]
        extracted = []
        for ri, row in enumerate(table):
            if is_header_row(row):
                idx_map = header_column_map(row)
                for row2 in table[ri + 1:]:
                    if is_header_row(row2):
                        break
                    parsed = parse_raw_line(row2, idx_map, job_prefix)
                    if parsed:
                        extracted.append(parsed)
        all_rows.extend(extracted)

    wb.close()

    seen, unique = set(), []
    for r in all_rows:
        key = (r['part_number'], r['description'], r['qty'], r['material'], r['vendor'])
        if key not in seen and r['part_number']:
            seen.add(key)
            unique.append(r)
    return unique


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-prefix", required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="List tasks and attachments without extracting")
    args = parser.parse_args()
    job_prefix = args.job_prefix

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT asana_task_gid, pack_reference, pack_name
        FROM build_assembly_packs
        WHERE pack_reference LIKE ?
        ORDER BY pack_reference
    """, (f"{job_prefix}.%",))
    packs = [dict(r) for r in cur.fetchall()]
    # Keep connection open for sibling drawing-task lookups
    # conn.close() — no longer closed here; closed after the processing loop

    print(f"Assembly packs loaded: {len(packs)}")

    # Filter out .DRAWING tasks — Stage 2 processes assembly packs, not drawing tasks
    packs = [p for p in packs if not p.get("pack_name", "").endswith(".DRAWING")]
    print(f"  Non-DRAWING (assembly packs to process): {len(packs)}")

    out_path   = DATA_DIR / f"{job_prefix}_drawing_boms.csv"
    error_path = DATA_DIR / f"{job_prefix}_extraction_errors.csv"
    errors     = []
    total_rows = 0

    # Open CSV for incremental writing (overwrites any previous run)
    with open(out_path, "w", newline="", encoding="utf-8") as csv_f:
        writer = csv.DictWriter(csv_f, fieldnames=FIELDNAMES)
        writer.writeheader()

        for pack in packs:
            pack_gid  = pack["asana_task_gid"]
            pack_ref  = pack["pack_reference"]
            pack_name = pack["pack_name"]

            try:
                # Resolve drawing task: prefer DB sibling with .DRAWING in pack_name
                # (same pack_reference, other row has drawing task GID and PDF attachment)
                drawing_gid  = ""
                drawing_name = ""
                if pack_gid:  # look for a sibling drawing task in the DB
                    cur2 = conn.cursor()
                    # Exact sibling first: assembly pack "X." pairs with drawing "X.DRAWING".
                    # Without this pass, a MOD pack matches the 3-component prefix of its
                    # base pack and re-extracts the BASE drawing's BOM.
                    cur2.execute("""
                        SELECT asana_task_gid, pack_name
                        FROM build_assembly_packs
                        WHERE pack_reference = ?
                          AND pack_name LIKE '%.DRAWING'
                          AND asana_task_gid != ?
                        LIMIT 1
                    """, (f"{pack_ref}DRAWING", pack_gid))
                    drawing_row = cur2.fetchone()

                    if not drawing_row:
                        # Fallback: 3-component prefix. Needed where the only drawing task
                        # carries a MOD suffix the pack does not (e.g. pack
                        # "1588.1006.A.WheelStopPlatform-SS." -> "..._MOD1.DRAWING").
                        # ORDER BY keeps the choice deterministic across runs.
                        pack_parts = pack_ref.split(".")
                        base_ref = ".".join(pack_parts[:3]) if len(pack_parts) >= 3 else pack_ref
                        cur2.execute("""
                            SELECT asana_task_gid, pack_name
                            FROM build_assembly_packs
                            WHERE pack_reference LIKE ?
                              AND pack_name LIKE '%.DRAWING'
                              AND asana_task_gid != ?
                            ORDER BY length(pack_reference), pack_reference
                            LIMIT 1
                        """, (f"{base_ref}.%", pack_gid))
                        drawing_row = cur2.fetchone()
                        if drawing_row:
                            print(f"    NOTE: {pack_ref} resolved by prefix fallback "
                                  f"-> {drawing_row[1]}")

                    if drawing_row:
                        drawing_gid  = drawing_row[0]
                        drawing_name = drawing_row[1] or ""

                # Fallback 1: Asana parent (original method — works when drawing
                # task is the parent of the assembly pack task)
                if not drawing_gid:
                    r2 = requests.get(
                        f"{BASE}/tasks/{pack_gid}",
                        headers=HEADERS,
                        params={"opt_fields": "parent.gid,parent.name"},
                        timeout=TIMEOUT_CONNECT,
                    )
                    time.sleep(PAUSE)
                    task_data      = r2.json().get("data", {})
                    parent_task    = task_data.get("parent") or {}
                    drawing_gid    = parent_task.get("gid", "")
                    drawing_name   = parent_task.get("name", "")

                if not drawing_gid:
                    print(f"  WARN: {pack_ref} — no .DRAWING task in DB and no parent in Asana")
                    errors.append({"pack_reference": pack_ref, "drawing_gid": "",
                                   "reason": "no_parent_drawing_task"})
                    continue

                print(f"\n  {pack_ref}  drawing: {drawing_name} [{drawing_gid}]")

                attachments = get_attachments(drawing_gid)
                time.sleep(PAUSE)

                attachment, source_type = pick_attachment(attachments)

                if not attachment:
                    print(f"    WARN: no PDF or Excel attachment found")
                    errors.append({"pack_reference": pack_ref, "drawing_gid": drawing_gid,
                                   "reason": "no_attachment"})
                    continue

                suffix = ".xlsx" if source_type == "excel" else ".pdf"
                print(f"    {source_type.upper()}: {attachment['name']}  "
                      f"(created {attachment['created_at'][:10]})")

                if args.dry_run:
                    continue

                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp_path = tmp.name

                try:
                    ok = download_file(attachment["download_url"], tmp_path)
                    if not ok:
                        print(f"    ERROR: download failed (HTTP error)")
                        errors.append({"pack_reference": pack_ref, "drawing_gid": drawing_gid,
                                       "reason": "download_failed"})
                        continue

                    if source_type == "excel":
                        rows = extract_excel(tmp_path, job_prefix)
                        if not rows:
                            # Excel parse yielded nothing — try PDF fallback
                            pdfs = [a for a in attachments
                                    if a.get("name", "").lower().endswith(".pdf")]
                            pdfs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
                            if pdfs:
                                print(f"    Excel empty — trying PDF fallback: {pdfs[0]['name']}")
                                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp2:
                                    tmp2_path = tmp2.name
                                try:
                                    ok2 = download_file(pdfs[0]["download_url"], tmp2_path)
                                    if ok2:
                                        rows = extract_pdf(tmp2_path, job_prefix)
                                        source_type = "pdf_fallback"
                                finally:
                                    try:
                                        os.unlink(tmp2_path)
                                    except OSError:
                                        pass
                    else:
                        rows = extract_pdf(tmp_path, job_prefix)

                    print(f"    Extracted {len(rows)} parts  [source: {source_type}]")
                    for row_preview in rows[:3]:
                        print(f"      {row_preview['part_number']}  "
                              f"({row_preview['qty']} off)  "
                              f"{(row_preview['description'] or '')[:50]}")

                    for row in rows:
                        writer.writerow({
                            "pack_reference":   pack_ref,
                            "pack_gid":         pack_gid,
                            "drawing_task_gid": drawing_gid,
                            "part_number":      row["part_number"],
                            "qty":              row["qty"],
                            "description":      row["description"],
                            "material":         row["material"],
                            "vendor":           row["vendor"],
                            "source_file":      attachment["name"],
                        })
                    csv_f.flush()
                    total_rows += len(rows)

                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

            except requests.Timeout:
                print(f"    ERROR: request timed out for {pack_ref}")
                errors.append({"pack_reference": pack_ref, "drawing_gid": "",
                               "reason": "timeout"})
            except Exception as exc:
                print(f"    ERROR: {pack_ref} — {exc}")
                errors.append({"pack_reference": pack_ref, "drawing_gid": "",
                               "reason": str(exc)[:120]})

    # Write error log
    conn.close()
    with open(error_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["pack_reference", "drawing_gid", "reason"])
        w.writeheader()
        w.writerows(errors)

    if args.dry_run:
        print(f"\nDry-run complete — no extraction performed.")
    else:
        print(f"\nDone.")
        print(f"  Total rows extracted:  {total_rows}")
        print(f"  Errors/skipped packs:  {len(errors)}")
        print(f"  BOM CSV:               {out_path}")
        print(f"  Error log:             {error_path}")


if __name__ == "__main__":
    main()
