#!/usr/bin/env python3
"""
build_golden_index.py
=====================================================================
Build a unified, deterministic "Golden Index" BOM.

GOAL
    Produce a single reliable mapping where each KAN number maps to
    exactly ONE stock number, plus:
        - all associated assembly pack(s)
        - all known Asana / Sesame part numbers

INPUTS (see INPUT_GOLDEN / INPUT_ASSEMBLY below)
    1. Golden Index Rev6 CSV
         KAN, Stock Number, Vendor (BOM), Description (BOM),
         Asana Task Name, Supplier URL, Asana Sesame PN,
         Asana Vendors Part No, Asana Vendor, MATCH
       KAN is the primary key (referenced from Asana). One KAN may
       appear on many rows (one per Asana part reference).

    2. Karol Master Match - Assembly Packs CSV
         Stock Number, Assembly pack, Description, Vendor, Web Link
       Maps a stock number to one or more assembly packs.

DESIGN PRINCIPLES
    * Determinism   - identical inputs always yield byte-identical
                      outputs. Every collection is sorted; no reliance
                      on dict / set iteration order.
    * No silent guessing - when a KAN genuinely maps to more than one
                      distinct stock number, it is FLAGGED and withheld
                      from the clean master (conflict policy = "flag").
    * Transparency  - formatting-only variants that we auto-merge are
                      logged so a human can audit every decision.
    * Validation first - the script's primary job is to surface data
                      quality problems, not to paper over them.

CONFLICT POLICY
    A KAN's candidate stock numbers are compared on a *normalised* key
    (see normalise_stock). If they all collapse to one key, the KAN is
    RESOLVED and a single canonical stock number is chosen
    deterministically. If two or more distinct normalised keys remain,
    the KAN is a TRUE CONFLICT: it goes to the conflicts report and is
    NOT written to the clean master.

OUTPUTS (written next to this script)
    golden_index_master.xlsx   multi-tab workbook (see build_workbook)
    golden_index_master.json   LLM-ready structure (resolved KANs only)

USAGE
    python3 build_golden_index.py \
        --golden   "Golden Index Rev 6   - Karol_Stock_Match.csv" \
        --assembly "KAROL MASTER MATCH - Assembly Packs.csv" \
        --outdir   .
    (all arguments optional; sensible defaults below)
=====================================================================
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Configuration / defaults
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GOLDEN = "Golden Index Rev 6   - Karol_Stock_Match.csv"
DEFAULT_ASSEMBLY = "KAROL MASTER MATCH - Assembly Packs.csv"
DEFAULT_RESOLUTIONS = "CONFLICT UPDATE FOR KAROL.csv"

# List delimiter used inside single spreadsheet cells (kept simple + safe).
LIST_SEP = " | "


# ---------------------------------------------------------------------------
# Normalisation helpers  (the heart of conflict detection)
# ---------------------------------------------------------------------------
# Prefixes that suppliers/typists add but which do not change identity, e.g.
#   "RS: 690-3292", "RS Stock No 724-2677", "Stock No: NBSS.013"
_PREFIX_RE = re.compile(r"^(RS\s+STOCK\s+NO|RS\s+STOCK|STOCK\s+NO|RS)\b[:.\s-]*", re.I)


def clean_text(value: str) -> str:
    """Trim, collapse internal whitespace, strip stray non-breaking spaces."""
    if value is None:
        return ""
    # 0xA0 non-breaking space appears in the source data.
    value = value.replace("\xa0", " ").replace("​", "")
    return re.sub(r"\s+", " ", value).strip()


def normalise_stock(value: str) -> str:
    """
    Reduce a stock number to a comparison key.

    Two raw stock strings that share a normalised key are treated as the
    SAME physical part (formatting variant). The rules are intentionally
    conservative - we only remove noise that is provably cosmetic:
        * upper-case
        * drop leading RS / "Stock No" style prefixes
        * remove every non-alphanumeric character (spaces, dots, dashes,
          colons, slashes)

    Examples
        "RS: 690-3292"      -> "6903292"
        "690-3292"          -> "6903292"      (same -> merge)
        "NBSS: 013"         -> "NBSS013"
        "NBSS.013"          -> "NBSS013"      (same -> merge)
        "700-0802"          -> "7000802"
        "10060610"          -> "10060610"     (different -> conflict)
    """
    v = clean_text(value).upper()
    v = _PREFIX_RE.sub("", v)
    v = re.sub(r"[^A-Z0-9]", "", v)
    return v


def split_part_numbers(raw: str) -> list[str]:
    """
    Split an Asana part-number cell into individual part numbers.

    The source encodes multiples with commas, e.g.
        "1004.B3.108   ,   431"  ->  ["1004.B3.108", "431"]
    Values are cleaned and de-duplicated while preserving determinism
    (sorted on return by the caller).
    """
    if not raw:
        return []
    parts = re.split(r"[,;/]", raw)
    return [clean_text(p) for p in parts if clean_text(p)]


def canonical_stock(raw_variants: list[str]) -> str:
    """
    Deterministically choose ONE display stock number from raw variants
    that all share a normalised key.

    Preference order (all deterministic):
        1. most frequent raw spelling
        2. shortest (fewest characters) - proxy for "least decorated"
        3. alphabetical
    """
    counts = Counter(raw_variants)
    return sorted(counts, key=lambda s: (-counts[s], len(s), s))[0]


# ---------------------------------------------------------------------------
# CSV loading  (encoding-robust)
# ---------------------------------------------------------------------------
def read_csv_rows(path: str) -> list[dict]:
    """
    Read a CSV into a list of dicts, robust to the encoding quirks in the
    source files (Latin-1 bytes + a UTF-8 BOM in the assembly file).
    Header names are stripped of whitespace and BOM.
    """
    last_err = None
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with open(path, newline="", encoding=enc) as fh:
                reader = csv.DictReader(fh)
                rows = []
                for row in reader:
                    rows.append(
                        {clean_text(k): clean_text(v) for k, v in row.items() if k is not None}
                    )
                return rows
        except UnicodeDecodeError as exc:  # pragma: no cover - defensive
            last_err = exc
            continue
    raise RuntimeError(f"Could not decode {path}: {last_err}")


# ---------------------------------------------------------------------------
# Assembly-pack index
# ---------------------------------------------------------------------------
def build_assembly_index(rows: list[dict]) -> tuple[dict, list[dict]]:
    """
    Return
        index : normalised_stock -> {
                    "packs": sorted[str],
                    "raw_stock": sorted[str],
                    "descriptions": sorted[str],
                }
        issues: rows with a blank stock number or blank assembly pack.
    """
    tmp = defaultdict(lambda: {"packs": set(), "raw_stock": set(), "descriptions": set()})
    issues = []
    for r in rows:
        stock = r.get("Stock Number", "")
        pack = r.get("Assembly pack", "")
        if not stock or not pack:
            issues.append(
                {
                    "raw_stock": stock,
                    "assembly_pack": pack,
                    "problem": "blank stock number" if not stock else "blank assembly pack",
                }
            )
            if not stock:
                continue
        key = normalise_stock(stock)
        tmp[key]["raw_stock"].add(stock)
        if pack:
            tmp[key]["packs"].add(pack)
        desc = r.get("Description", "")
        if desc:
            tmp[key]["descriptions"].add(desc)

    index = {
        k: {
            "packs": sorted(v["packs"]),
            "raw_stock": sorted(v["raw_stock"]),
            "descriptions": sorted(v["descriptions"]),
        }
        for k, v in tmp.items()
    }
    return index, issues


# ---------------------------------------------------------------------------
# Golden Index aggregation
# ---------------------------------------------------------------------------
def aggregate_golden(rows: list[dict]) -> tuple[dict, int]:
    """
    Group Golden Index rows by KAN.

    Returns (kan_map, duplicate_row_count) where kan_map is
        KAN -> {
            stock_variants   : Counter(raw stock -> count),
            norm_to_raw      : normalised_stock -> set(raw variants),
            asana_sesame_pn  : set,
            asana_vendor_pn  : set,
            asana_task_names : set,
            descriptions     : set,
            vendors          : set,
            match_types      : Counter,
            row_count        : int,
        }
    Fully blank-KAN rows are ignored (reported separately by caller).
    Exact duplicate rows are collapsed and counted.
    """
    seen_rows = set()
    duplicates = 0
    kan_map: dict[str, dict] = {}

    for r in rows:
        kan = r.get("KAN", "")
        if not kan:
            continue
        # exact-duplicate detection on the full row signature
        sig = tuple(sorted(r.items()))
        if sig in seen_rows:
            duplicates += 1
            continue
        seen_rows.add(sig)

        rec = kan_map.setdefault(
            kan,
            {
                "stock_variants": Counter(),
                "norm_to_raw": defaultdict(set),
                "asana_sesame_pn": set(),
                "asana_vendor_pn": set(),
                "asana_task_names": set(),
                "descriptions": set(),
                "vendors": set(),
                "match_types": Counter(),
                "row_count": 0,
            },
        )
        rec["row_count"] += 1

        stock = r.get("Stock Number", "")
        if stock:
            rec["stock_variants"][stock] += 1
            rec["norm_to_raw"][normalise_stock(stock)].add(stock)

        for pn in split_part_numbers(r.get("Asana Sesame PN", "")):
            rec["asana_sesame_pn"].add(pn)
        for pn in split_part_numbers(r.get("Asana Vendors Part No", "")):
            rec["asana_vendor_pn"].add(pn)

        for field, bucket in (
            ("Asana Task Name", "asana_task_names"),
            ("Description (BOM)", "descriptions"),
            ("Vendor (BOM)", "vendors"),
        ):
            val = r.get(field, "")
            if val:
                rec[bucket].add(val)

        m = r.get("MATCH", "")
        if m:
            rec["match_types"][m] += 1

    return kan_map, duplicates


# ---------------------------------------------------------------------------
# Conflict resolutions (human-supplied)
# ---------------------------------------------------------------------------
def read_resolutions(path: str) -> dict:
    """
    Read a human-completed conflict-resolution file into
        KAN -> chosen stock number (cleaned).

    Accepts any CSV that has a KAN column and a column whose header
    contains 'Resolution' (case-insensitive) - i.e. the 'Conflicts' tab
    exported to CSV, or the workbook's Conflicts sheet. Blank resolutions
    are ignored so partially-resolved files are safe to run.
    """
    if not path or not os.path.exists(path):
        return {}
    rows = read_csv_rows(path)
    if not rows:
        return {}
    # locate columns
    headers = list(rows[0].keys())
    kan_col = next((h for h in headers if h.strip().upper() == "KAN"), None)
    res_col = next((h for h in headers if "RESOLUTION" in h.upper()), None)
    verdict_col = next((h for h in headers if "VERDICT" in h.upper()), None)
    note_col = next((h for h in headers if "CHECK" in h.upper()), None)  # "Check result"
    if not kan_col or not res_col:
        raise RuntimeError(
            f"Resolutions file {path} must have 'KAN' and a 'Resolution' column; "
            f"found {headers}"
        )
    out = {}
    for r in rows:
        kan = clean_text(r.get(kan_col, ""))
        chosen = clean_text(r.get(res_col, ""))
        verdict = clean_text(r.get(verdict_col, "")).upper() if verdict_col else ""
        note = clean_text(r.get(note_col, "")) if note_col else ""
        # Keep an entry when there is a chosen value OR a verdict — a RED verdict
        # with a blank resolution must still quarantine the KAN.
        if kan and (chosen or verdict):
            out[kan] = {"chosen": chosen, "verdict": verdict, "note": note}
    return out


# ---------------------------------------------------------------------------
# Consolidation: split into resolved master vs conflicts, join packs
# ---------------------------------------------------------------------------
def _build_master_record(kan, rec, stock, raws, assembly_index, *, provenance):
    """Assemble one Master record for a KAN with a chosen stock number."""
    nk = normalise_stock(stock)
    packs_info = assembly_index.get(nk, {})
    record = {
        "kan": kan,
        "stock_number": stock,
        "stock_number_variants": raws,
        "assembly_packs": packs_info.get("packs", []),
        "asana_sesame_part_numbers": sorted(rec["asana_sesame_pn"]),
        "asana_vendor_part_numbers": sorted(rec["asana_vendor_pn"]),
        "asana_task_names": sorted(rec["asana_task_names"]),
        "description": _pick_description(rec["descriptions"]),
        "descriptions_all": sorted(rec["descriptions"]),
        "vendors": sorted(rec["vendors"]),
        "match_types": dict(rec["match_types"]),
        "source_row_count": rec["row_count"],
        "has_assembly_pack": bool(packs_info.get("packs")),
        "resolution_source": provenance,
    }
    return record


def consolidate(kan_map: dict, assembly_index: dict, resolutions: dict | None = None) -> dict:
    """
    Turn the aggregated KAN map into the final structures.

    resolutions : optional KAN -> chosen stock number (human-supplied).
                  A conflicted KAN that appears here is promoted into the
                  master using the chosen stock number.

    Returns dict with keys:
        master             : list[record]  (one stock number per KAN)
        conflicts          : list[record]  (UNRESOLVED multi-stock KANs)
        resolved_conflicts : list[record]  (audit of human resolutions applied)
        merges             : list[record]  (formatting variants auto-merged)
        no_stock           : list[str]     (KANs with no stock number at all)
    """
    resolutions = resolutions or {}
    master, conflicts, merges, no_stock, resolved_conflicts, needs_split = [], [], [], [], [], []

    for kan in sorted(kan_map, key=_kan_sort_key):
        rec = kan_map[kan]
        norm_keys = sorted(rec["norm_to_raw"])

        if not norm_keys:
            no_stock.append(kan)
            continue

        if len(norm_keys) > 1:
            candidates = sorted(
                {canonical_stock(sorted(rec["norm_to_raw"][nk])) for nk in norm_keys}
            )
            entry = resolutions.get(kan) or {}
            chosen = entry.get("chosen", "")
            verdict = (entry.get("verdict") or "").upper()
            note = entry.get("note", "")

            candidate_norms = {normalise_stock(c) for c in rec["stock_variants"]}
            matched = bool(chosen) and normalise_stock(chosen) in candidate_norms

            def _quarantine(reason):
                needs_split.append(
                    {
                        "kan": kan,
                        "reason": reason,
                        "verdict": verdict,
                        "candidate_stock_numbers": candidates,
                        "all_raw_variants": sorted(rec["stock_variants"]),
                        "match_types": dict(rec["match_types"]),
                        "row_count": rec["row_count"],
                        "asana_sesame_pn": sorted(rec["asana_sesame_pn"]),
                        "karol_resolution": chosen,
                        "karol_note": note,
                    }
                )

            # RED = design confirmed genuinely different parts wrongly sharing one
            # KAN. Must be split into separate KANs (a data decision) — withhold.
            if verdict == "RED":
                _quarantine("different_parts_RED")
                continue

            # Provenance guard: a chosen value that is NOT among the detected
            # candidates AND carries no human GREEN/AMBER sign-off is ungrounded
            # (possibly fabricated) — withhold, never guess. A GREEN/AMBER verdict
            # IS the human verification, so a reviewed value is trusted even when
            # it is a cleaned/reformatted spelling of a candidate.
            if chosen and not matched and verdict not in ("GREEN", "AMBER"):
                _quarantine("resolution_not_a_candidate")
                continue

            if chosen:
                # Human resolved (GREEN/AMBER, value IS a real candidate) - promote.
                record = _build_master_record(
                    kan, rec, chosen, [chosen], assembly_index,
                    provenance="conflict_resolution",
                )
                master.append(record)
                resolved_conflicts.append(
                    {
                        "kan": kan,
                        "chosen_stock_number": chosen,
                        "candidate_stock_numbers": candidates,
                        "matched_a_candidate": matched,
                        "has_assembly_pack": record["has_assembly_pack"],
                    }
                )
                continue
            # Still unresolved - flag and withhold (no guessing).
            conflicts.append(
                {
                    "kan": kan,
                    "candidate_stock_numbers": candidates,
                    "all_raw_variants": sorted(rec["stock_variants"]),
                    "match_types": dict(rec["match_types"]),
                    "row_count": rec["row_count"],
                    "asana_sesame_pn": sorted(rec["asana_sesame_pn"]),
                }
            )
            continue

        # RESOLVED automatically - single normalised identity.
        nk = norm_keys[0]
        raws = sorted(rec["norm_to_raw"][nk])
        stock = canonical_stock(list(rec["stock_variants"].elements()))
        if len(raws) > 1:
            merges.append(
                {
                    "kan": kan,
                    "chosen_stock_number": stock,
                    "merged_variants": raws,
                    "normalised_key": nk,
                }
            )
        record = _build_master_record(
            kan, rec, stock, raws, assembly_index, provenance="auto",
        )
        master.append(record)

    return {
        "master": master,
        "conflicts": conflicts,
        "resolved_conflicts": resolved_conflicts,
        "needs_split": needs_split,
        "merges": merges,
        "no_stock": no_stock,
    }


def _pick_description(descs: set) -> str:
    """Deterministic single description: longest, then alphabetical."""
    if not descs:
        return ""
    return sorted(descs, key=lambda s: (-len(s), s))[0]


def _kan_sort_key(kan: str):
    """Sort KAN-123 numerically where possible, else alphabetically."""
    m = re.match(r"^KAN[-\s]?(\d+)$", kan, re.I)
    return (0, int(m.group(1))) if m else (1, kan)


# ---------------------------------------------------------------------------
# Cross-file data-quality checks
# ---------------------------------------------------------------------------
def data_quality(consolidated: dict, assembly_index: dict, kan_map: dict) -> dict:
    """Compute the remaining quality signals for the report."""
    master = consolidated["master"]
    golden_norm_keys = {
        normalise_stock(r["stock_number"]) for r in master
    }
    # assembly stocks that never link to any resolved golden KAN
    unmatched_assembly = sorted(
        {
            raw
            for nk, info in assembly_index.items()
            if nk not in golden_norm_keys
            for raw in info["raw_stock"]
        }
    )
    missing_packs = [r["kan"] for r in master if not r["has_assembly_pack"]]
    return {
        "resolved_without_packs": sorted(missing_packs, key=_kan_sort_key),
        "assembly_stock_not_in_golden": unmatched_assembly,
    }


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------
def write_json(consolidated: dict, path: str) -> None:
    """Write the LLM-ready master (resolved KANs only) as sorted JSON."""
    payload = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "record_count": len(consolidated["master"]),
        "schema": {
            "kan": "primary key (string)",
            "stock_number": "single canonical stock number",
            "assembly_packs": "list of assembly pack codes",
            "asana_sesame_part_numbers": "list",
            "asana_vendor_part_numbers": "list",
            "resolution_source": "'auto' (single stock) or 'conflict_resolution' (human-chosen)",
        },
        "outstanding_conflicts": [c["kan"] for c in consolidated["conflicts"]],
        "needs_split": consolidated.get("needs_split", []),
        "kans_without_stock": consolidated["no_stock"],
        "records": consolidated["master"],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=False)


def build_workbook(consolidated: dict, quality: dict, stats: dict, path: str) -> None:
    """Write the multi-tab Excel workbook."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(bold=True, color="FFFFFF")
    wrap = Alignment(vertical="top", wrap_text=True)

    def add_sheet(title, headers, rows, widths=None):
        ws = wb.create_sheet(title)
        ws.append(headers)
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=c)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(vertical="center")
        for row in rows:
            ws.append(row)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws.max_row}"
        for i, _ in enumerate(headers, 1):
            letter = get_column_letter(i)
            w = (widths or {}).get(i, 22)
            ws.column_dimensions[letter].width = w
        for r in range(2, ws.max_row + 1):
            for c in range(1, len(headers) + 1):
                ws.cell(row=r, column=c).alignment = wrap
        return ws

    # --- Summary (first sheet) ---
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Golden Index - Consolidation Summary"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = "Generated (UTC)"
    ws["B2"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    summary_rows = [
        ("Total KANs in source", stats["total_kans"]),
        ("Resolved (1 KAN -> 1 stock)", stats["resolved"]),
        ("   of which via human conflict resolution", stats["resolved_conflicts"]),
        ("TRUE conflicts still outstanding", stats["conflicts"]),
        ("KANs with no stock number", stats["no_stock"]),
        ("Formatting variants auto-merged", stats["merges"]),
        ("Exact duplicate source rows collapsed", stats["duplicates"]),
        ("Resolved KANs WITHOUT an assembly pack", stats["resolved_without_packs"]),
        ("Assembly stocks not linked to any KAN", stats["assembly_not_in_golden"]),
        ("Assembly-file structural issues", stats["assembly_issues"]),
    ]
    ws["A4"] = "Metric"
    ws["B4"] = "Value"
    ws["A4"].font = ws["B4"].font = header_font
    ws["A4"].fill = ws["B4"].fill = header_fill
    for i, (k, v) in enumerate(summary_rows, start=5):
        ws.cell(row=i, column=1, value=k)
        ws.cell(row=i, column=2, value=v)
    note_row = 5 + len(summary_rows) + 1
    ws.cell(
        row=note_row,
        column=1,
        value=(
            "Conflict policy: KANs that map to >1 distinct (normalised) stock number are "
            "flagged in the 'Conflicts' tab and intentionally EXCLUDED from 'Master' until "
            "resolved by a human. Formatting-only variants (e.g. 'RS: 690-3292' vs '690-3292') "
            "are auto-merged and logged in 'Auto-merged variants'."
        ),
    )
    ws.cell(row=note_row, column=1).alignment = wrap
    ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=2)
    ws.column_dimensions["A"].width = 42
    ws.column_dimensions["B"].width = 60

    # --- Master ---
    master_headers = [
        "KAN",
        "Stock Number",
        "Assembly Packs",
        "# Packs",
        "Asana Sesame PNs",
        "Asana Vendor PNs",
        "Asana Task Names",
        "Description",
        "Vendors",
        "Match Types",
        "Source Rows",
    ]
    master_rows = [
        [
            r["kan"],
            r["stock_number"],
            LIST_SEP.join(r["assembly_packs"]),
            len(r["assembly_packs"]),
            LIST_SEP.join(r["asana_sesame_part_numbers"]),
            LIST_SEP.join(r["asana_vendor_part_numbers"]),
            LIST_SEP.join(r["asana_task_names"]),
            r["description"],
            LIST_SEP.join(r["vendors"]),
            ", ".join(f"{k}:{v}" for k, v in sorted(r["match_types"].items())),
            r["source_row_count"],
        ]
        for r in consolidated["master"]
    ]
    add_sheet(
        "Master",
        master_headers,
        master_rows,
        widths={1: 12, 2: 22, 3: 30, 4: 8, 5: 26, 6: 22, 7: 40, 8: 40, 9: 18, 10: 20, 11: 10},
    )

    # --- Conflicts ---
    conflict_headers = [
        "KAN",
        "Candidate Stock Numbers",
        "All Raw Variants",
        "Match Types",
        "Source Rows",
        "Asana Sesame PNs",
        "Resolution (fill in)",
    ]
    conflict_rows = [
        [
            c["kan"],
            LIST_SEP.join(c["candidate_stock_numbers"]),
            LIST_SEP.join(c["all_raw_variants"]),
            ", ".join(f"{k}:{v}" for k, v in sorted(c["match_types"].items())),
            c["row_count"],
            LIST_SEP.join(c["asana_sesame_pn"]),
            "",
        ]
        for c in consolidated["conflicts"]
    ]
    add_sheet(
        "Conflicts",
        conflict_headers,
        conflict_rows,
        widths={1: 12, 2: 30, 3: 40, 4: 20, 5: 10, 6: 26, 7: 24},
    )

    # --- Needs split (RED / unverifiable — withheld from master, for design) ---
    ns_headers = [
        "KAN", "Reason", "Verdict", "Candidate Stock Numbers",
        "Asana Sesame PNs", "Karol Resolution", "Karol Note", "Source Rows",
    ]
    ns_rows = [
        [
            n["kan"],
            n["reason"],
            n.get("verdict", ""),
            LIST_SEP.join(n["candidate_stock_numbers"]),
            LIST_SEP.join(n["asana_sesame_pn"]),
            n.get("karol_resolution", ""),
            n.get("karol_note", ""),
            n["row_count"],
        ]
        for n in consolidated.get("needs_split", [])
    ]
    add_sheet(
        "Needs split",
        ns_headers,
        ns_rows,
        widths={1: 12, 2: 24, 3: 9, 4: 30, 5: 26, 6: 20, 7: 55, 8: 10},
    )

    # --- Resolved conflicts (audit of human decisions applied) ---
    add_sheet(
        "Resolved conflicts",
        ["KAN", "Chosen Stock Number", "Candidate Stock Numbers", "Matched a Candidate", "Has Assembly Pack"],
        [
            [
                rc["kan"],
                rc["chosen_stock_number"],
                LIST_SEP.join(rc["candidate_stock_numbers"]),
                "yes" if rc["matched_a_candidate"] else "NO (new value)",
                "yes" if rc["has_assembly_pack"] else "no",
            ]
            for rc in consolidated["resolved_conflicts"]
        ],
        widths={1: 12, 2: 26, 3: 40, 4: 18, 5: 16},
    )

    # --- Auto-merged variants (audit) ---
    add_sheet(
        "Auto-merged variants",
        ["KAN", "Chosen Stock Number", "Merged Raw Variants", "Normalised Key"],
        [
            [m["kan"], m["chosen_stock_number"], LIST_SEP.join(m["merged_variants"]), m["normalised_key"]]
            for m in consolidated["merges"]
        ],
        widths={1: 12, 2: 24, 3: 40, 4: 20},
    )

    # --- Data quality ---
    dq_rows = []
    for kan in quality["resolved_without_packs"]:
        dq_rows.append([kan, "Resolved KAN has no assembly pack", ""])
    for stock in quality["assembly_stock_not_in_golden"]:
        dq_rows.append(["", "Assembly stock not linked to any KAN", stock])
    for kan in consolidated["no_stock"]:
        dq_rows.append([kan, "KAN has no stock number", ""])
    add_sheet(
        "Data quality",
        ["KAN", "Issue", "Detail"],
        dq_rows,
        widths={1: 12, 2: 42, 3: 30},
    )

    wb.save(path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the unified Golden Index BOM.")
    ap.add_argument("--golden", default=os.path.join(HERE, DEFAULT_GOLDEN))
    ap.add_argument("--assembly", default=os.path.join(HERE, DEFAULT_ASSEMBLY))
    ap.add_argument(
        "--resolutions",
        default=None,
        help="CSV of human conflict resolutions (KAN + Resolution columns). "
        f"If omitted, auto-detects '{DEFAULT_RESOLUTIONS}' next to the script.",
    )
    ap.add_argument("--outdir", default=HERE)
    args = ap.parse_args(argv)

    if not os.path.exists(args.golden):
        sys.exit(f"ERROR: golden file not found: {args.golden}")
    if not os.path.exists(args.assembly):
        sys.exit(f"ERROR: assembly file not found: {args.assembly}")

    # Resolutions: explicit path, else auto-detect the default file.
    res_path = args.resolutions
    if res_path is None:
        auto = os.path.join(HERE, DEFAULT_RESOLUTIONS)
        res_path = auto if os.path.exists(auto) else None
    resolutions = read_resolutions(res_path) if res_path else {}

    golden_rows = read_csv_rows(args.golden)
    assembly_rows = read_csv_rows(args.assembly)

    assembly_index, assembly_issues = build_assembly_index(assembly_rows)
    kan_map, duplicates = aggregate_golden(golden_rows)
    consolidated = consolidate(kan_map, assembly_index, resolutions)
    quality = data_quality(consolidated, assembly_index, kan_map)

    stats = {
        "total_kans": len(kan_map),
        "resolved": len(consolidated["master"]),
        "resolved_conflicts": len(consolidated["resolved_conflicts"]),
        "conflicts": len(consolidated["conflicts"]),
        "needs_split": len(consolidated["needs_split"]),
        "no_stock": len(consolidated["no_stock"]),
        "merges": len(consolidated["merges"]),
        "duplicates": duplicates,
        "resolved_without_packs": len(quality["resolved_without_packs"]),
        "assembly_not_in_golden": len(quality["assembly_stock_not_in_golden"]),
        "assembly_issues": len(assembly_issues),
    }

    os.makedirs(args.outdir, exist_ok=True)
    xlsx_path = os.path.join(args.outdir, "golden_index_master.xlsx")
    json_path = os.path.join(args.outdir, "golden_index_master.json")
    build_workbook(consolidated, quality, stats, xlsx_path)
    write_json(consolidated, json_path)

    # needs_split.csv — RED / unverifiable KANs withheld from master, handed to
    # design to split into separate KANs (or correct the stock number). Never
    # shipped to rev6, so the BOM matcher can't map to a known-wrong part.
    ns_path = os.path.join(args.outdir, "needs_split.csv")
    with open(ns_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "KAN", "Reason", "Verdict", "Candidate Stock Numbers",
            "All Raw Variants", "Asana Sesame PNs", "Karol Resolution",
            "Karol Note", "Source Rows",
        ])
        for n in consolidated.get("needs_split", []):
            w.writerow([
                n["kan"], n["reason"], n.get("verdict", ""),
                LIST_SEP.join(n["candidate_stock_numbers"]),
                LIST_SEP.join(n["all_raw_variants"]),
                LIST_SEP.join(n["asana_sesame_pn"]),
                n.get("karol_resolution", ""), n.get("karol_note", ""),
                n["row_count"],
            ])

    # Console report -------------------------------------------------------
    print("=" * 64)
    print("GOLDEN INDEX BUILD COMPLETE")
    print("=" * 64)
    for k, v in stats.items():
        print(f"  {k:34s}: {v}")
    if resolutions:
        print(f"\n  Applied {len(resolutions)} conflict resolution(s) from: {res_path}")
        new_vals = [rc for rc in consolidated["resolved_conflicts"] if not rc["matched_a_candidate"]]
        if new_vals:
            print("  NOTE - resolutions that introduced a NEW stock value (not among candidates):")
            for rc in new_vals:
                print(f"    {rc['kan']:10s} -> {rc['chosen_stock_number']!r}")
    if stats["conflicts"]:
        print("\n  TRUE CONFLICTS still outstanding (excluded from master):")
        for c in consolidated["conflicts"]:
            print(f"    {c['kan']:10s} -> {c['candidate_stock_numbers']}")
    if consolidated.get("needs_split"):
        print("\n  NEEDS SPLIT — withheld from master (RED / unverifiable), for design:")
        for n in consolidated["needs_split"]:
            print(f"    {n['kan']:10s} [{n['reason']}] -> {n['candidate_stock_numbers']}")
    print(f"\n  Workbook: {xlsx_path}")
    print(f"  JSON    : {json_path}")
    return stats


if __name__ == "__main__":
    main()
