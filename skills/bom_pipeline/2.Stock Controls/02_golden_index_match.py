"""
Stage 2 — Golden Index Matcher
================================
Matches each part number from the consolidated BOM against the Golden Index CSV
to find the corresponding KAN number and Asana stock task URL.

Usage:
    python 02_golden_index_match.py \
        --input compiled_parts_list_with_assembly.csv \
        --golden-index "Stock Controls/Merged_Golden_Index_Rev2.csv" \
        --output bom_with_kan.csv \
        --threshold 80

Output adds columns: Mapped KAN, Asana Stock Name, Asana Task GID,
                     Asana URL (Golden), Match Confidence, Match Type, Index Match Status

Matching order per BOM line:
  1. PRIMARY  — the wired match column (default: `stock number`, the vendor
     stock value) via exact stock-key -> substring -> fuzzy.
  2. FALLBACK — if the primary column is blank or finds nothing, recover an
     in-house NBSS stock code and match it EXACTLY (no fuzzy). Two sources,
     tried in order:
       (a) cost centre == 'NBSS' -> strip the project prefix off the structured
           `part number` and prepend 'NBSS.' ('1573.008' -> 'NBSS.008').
           Preferred: gated on a structured column, robust, no false triggers.
       (b) otherwise recover an 'NBSS.<n>' code from the description text.
     In-house Sesame parts leave `stock number` blank but are identifiable this
     way. Rows recovered are tagged Match Type '... (nbss-fallback)'.
The fallback is a safety net; design BOMs should still populate `stock number`
(see DESIGN_TEAM_BOM_DATA_GUIDE.md).
"""

import argparse
import os
import re
import sqlite3
import sys
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

# Golden Index source of truth (Rev6). The CSV is retained only as a fallback.
GOLDEN_TABLE = "golden_index_rev6"


def _pathconfig_db() -> str:
    """Canonical ops.db via SES_Workspace/lib/path_config.py (single source of
    truth — PATH_CONFIG.yaml; follows the DB to a VPS). Empty string if the
    helper can't be imported, so the literal fallbacks below still apply."""
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
        from path_config import p as _p
        return _p("db.ops")
    except Exception:
        return ""


_DB_CANDIDATES = [
    _pathconfig_db(),                                 # canonical (PATH_CONFIG.yaml)
    r"C:\Users\GilesHugo\SES_Workspace\db\ops.db",    # resilient literal fallback
    str(Path(__file__).resolve().parents[3] / "db" / "ops.db"),
]


def resolve_db_path(explicit: str | None = None) -> str | None:
    """Return the first ops.db that exists (explicit path wins)."""
    for c in [explicit, *_DB_CANDIDATES]:
        if c and os.path.exists(c):
            return os.path.abspath(c)
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalise(s: str) -> str:
    """Lowercase, strip whitespace, collapse internal spaces (used for fuzzy)."""
    return re.sub(r'\s+', ' ', str(s).strip().lower())


_RS_PREFIX = re.compile(r'^(RS\s+STOCK\s+NO|RS\s+STOCK|STOCK\s+NO|RS)\b[:.\s-]*', re.I)


def stock_key(s: str) -> str:
    """
    Strong, formatting-insensitive key for EXACT stock-number matching.
    Mirrors the Golden Index build normalisation so index and BOM agree:
    upper-case, drop RS/'Stock No' prefixes, remove all non-alphanumerics.
      'RS Stock No 724-2677' -> '7242677'   ==  '724-2677' -> '7242677'
      'RS: 690-3292'         -> '6903292'   ==  '690-3292' -> '6903292'
    """
    v = str(s or '').upper().replace('\xa0', ' ').strip()
    v = _RS_PREFIX.sub('', v)
    return re.sub(r'[^A-Z0-9]', '', v)


_NBSS_RE = re.compile(r'\bNBSS[\s:._-]*(\d{1,4})', re.I)


def extract_nbss_code(text: str) -> str:
    """
    Recover an in-house NBSS stock code from free text (BOM description / item /
    part number). In-house Sesame parts leave the `stock number` column blank
    but carry 'NBSS.126 ...' in their description; the Golden Index stores these
    as 'NBSS.126'. Returns a normalised 'NBSS.<3-digit>' code, or '' if none.
      'NBSS.126 Sprocket Pin Motor' -> 'NBSS.126'
      'NBSS008 Stair Wheel'          -> 'NBSS.008'
    Zero-pads to the 3-digit NBSS convention so 'NBSS.8' / 'NBSS8' / 'NBSS.008'
    all resolve to the same stock code.
    """
    m = _NBSS_RE.search(str(text or ''))
    return f"NBSS.{m.group(1).zfill(3)}" if m else ''


_PROJECT_PREFIX = re.compile(r'^\d{4}[.\-_ ](.+)$')


def nbss_from_costcentre(row, col_lc: dict) -> str:
    """
    Robust in-house recovery (primary fallback). When the BOM line's cost
    centre is 'NBSS', derive the generic stock code from the structured
    `part number` by stripping the 4-digit project prefix (e.g. '1573.') and
    prepending 'NBSS.'. This maps straight onto the Golden Index NBSS keys.
      cost centre 'NBSS' + part number '1573.008' -> 'NBSS.008'
      cost centre 'NBSS' + part number '1573.105' -> 'NBSS.105'
    Preferred over description parsing: gated on a structured column, uses the
    clean part number, and won't fire on non-NBSS-cost-centre lines (e.g. a
    machined-to-length 'NBSS.121' whose cost centre is 'Machining').
    Returns the candidate code, or '' if the row is not an NBSS cost-centre line.
    """
    cc_col = (col_lc.get('cost center') or col_lc.get('cost centre')
              or col_lc.get('cost_center'))
    pn_col = col_lc.get('part number') or col_lc.get('part_number')
    if not cc_col or not pn_col:
        return ''
    if str(row.get(cc_col, '') or '').strip().upper() != 'NBSS':
        return ''
    pn = str(row.get(pn_col, '') or '').strip()
    m = _PROJECT_PREFIX.match(pn)
    tail = (m.group(1) if m else pn).strip('. ')
    if not tail:
        return ''
    if tail.isdigit():          # keep the 3-digit NBSS convention (8 -> 008)
        tail = tail.zfill(3)
    return f"NBSS.{tail}"


def similarity(a: str, b: str) -> int:
    """Return 0-100 similarity score between two strings."""
    return int(SequenceMatcher(None, normalise(a), normalise(b)).ratio() * 100)


def extract_gid_from_url(url: str) -> str:
    """
    Extract Asana task GID from URL.
    Handles formats:
        https://app.asana.com/1/{workspace}/{gid}
        https://app.asana.com/0/{project}/{gid}
        https://app.asana.com/0/{project}/{gid}/f
    Strips trailing .0 from float-parsed values.
    """
    if not url or pd.isna(url):
        return ''
    url = str(url).strip()
    # Remove float suffix (e.g. '1234567890.0')
    url = re.sub(r'\.0$', '', url)
    # Take the last numeric segment from the path
    parts = url.rstrip('/f').rstrip('/').split('/')
    for part in reversed(parts):
        clean = re.sub(r'\.0$', '', part)
        if re.match(r'^\d{10,}$', clean):
            return clean
    return ''


def load_golden_index(path: Path) -> pd.DataFrame:
    """Load and normalise the Golden Index CSV (fallback source)."""
    df = pd.read_csv(path, dtype=str).fillna('')
    df.columns = [c.strip() for c in df.columns]
    # Add a normalised key column for matching (lookup key is Stock_Number)
    df['_norm_cad_pn'] = df['Stock_Number'].apply(normalise)
    df['_stock_key'] = df['Stock_Number'].apply(stock_key)
    # Extract GID from URL
    df['Asana Task GID'] = df['Asana_URL'].apply(extract_gid_from_url)
    return df


def load_golden_index_db(db_path: str) -> pd.DataFrame:
    """
    Load the Golden Index from the ops.db source of truth (golden_index_rev6),
    returning a DataFrame with exactly the columns the matcher expects. The
    lookup key is the vendor stock number: BOM stock value -> KAN.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            f"SELECT kan_number, stock_number, description, asana_task_gid "
            f"FROM {GOLDEN_TABLE}"
        ).fetchall()
    finally:
        conn.close()
    df = pd.DataFrame(
        rows, columns=["Mapped_KAN", "Stock_Number", "Asana_Name", "Asana Task GID"]
    ).fillna('')
    df["Asana_URL"] = df["Asana Task GID"].apply(
        lambda g: f"https://app.asana.com/0/0/{g}" if g else ""
    )
    df["_norm_cad_pn"] = df["Stock_Number"].apply(normalise)
    df["_stock_key"] = df["Stock_Number"].apply(stock_key)
    return df


def match_part(part_number: str, golden: pd.DataFrame, threshold: int):
    """
    Try to match a part number against the Golden Index.
    Returns a dict of match columns, or None if no match above threshold.

    Strategy:
      1. Exact match on normalised CAD PN
      2. Substring match (either direction)
      3. Fuzzy similarity above threshold
    """
    norm_part = normalise(part_number)
    if not norm_part:
        return None

    # 0. Strong exact on the formatting-insensitive stock key (Rev6).
    #    Robust to 'RS Stock No', colons, dots, dashes — matches the index build.
    part_key = stock_key(part_number)
    if part_key:
        for _, row in golden.iterrows():
            if row.get('_stock_key') and row['_stock_key'] == part_key:
                return _build_match(row, 100, 'Exact')

    best_score = 0
    best_row = None
    best_type = ''

    for _, row in golden.iterrows():
        cad = row['_norm_cad_pn']
        if not cad:
            continue

        # 1. Exact
        if norm_part == cad:
            return _build_match(row, 100, 'Exact')

        # 2. Substring
        if norm_part in cad or cad in norm_part:
            score = 90
            if score > best_score:
                best_score = score
                best_row = row
                best_type = 'Substring'
            continue

        # 3. Fuzzy
        score = similarity(norm_part, cad)
        if score > best_score:
            best_score = score
            best_row = row
            best_type = 'Fuzzy'

    if best_row is not None and best_score >= threshold:
        return _build_match(best_row, best_score, best_type)

    return None


def match_exact_key(candidate: str, golden: pd.DataFrame):
    """
    EXACT stock-key match only (no substring / no fuzzy). Used by the fallback
    for recovered codes (e.g. an NBSS code pulled from the description) so the
    fallback can only ever produce a precise hit, never a loose false positive.
    """
    k = stock_key(candidate)
    if not k:
        return None
    for _, row in golden.iterrows():
        if row.get('_stock_key') and row['_stock_key'] == k:
            return _build_match(row, 100, 'Exact')
    return None


def _build_match(row, score: int, match_type: str) -> dict:
    return {
        'Matched Stock_Number': row.get('Stock_Number', ''),  # Rev4 col C — cross-ref key
        'Mapped KAN': row.get('Mapped_KAN', ''),              # Rev4 col I
        'Asana Stock Name': row.get('Asana_Name', ''),        # Rev4 col J
        'Asana Task GID': row.get('Asana Task GID', ''),
        'Asana URL (Golden)': row.get('Asana_URL', ''),       # Rev4 col M
        'Match Confidence': score,
        'Match Type': match_type,
        'Index Match Status': 'MATCHED',
    }


def _empty_match() -> dict:
    return {
        'Matched Stock_Number': '',
        'Mapped KAN': '',
        'Asana Stock Name': '',
        'Asana Task GID': '',
        'Asana URL (Golden)': '',
        'Match Confidence': 0,
        'Match Type': '',
        'Index Match Status': 'UNMATCHED',
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Stage 2 — Golden Index Matcher')
    parser.add_argument('--input', '-i', required=True, help='Consolidated BOM CSV (from Stage 1)')
    parser.add_argument('--golden-index', '-g', default=None,
                        help='Path to Golden Index CSV (fallback if --from-db not used)')
    parser.add_argument('--from-db', action='store_true',
                        help='Load the Golden Index from ops.db (golden_index_rev6) — source of truth.')
    parser.add_argument('--db', default=None, help='Path to ops.db (auto-detected if omitted).')
    parser.add_argument('--output', '-o', default='bom_with_kan.csv', help='Output CSV filename')
    parser.add_argument('--threshold', '-t', type=int, default=80, help='Minimum match confidence 0-100 (default 80)')
    parser.add_argument('--part-col', default=None,
                        help='BOM column to match on. Default: "stock number" with --from-db (Rev6), '
                             'else "part number" (legacy CSV).')
    args = parser.parse_args()

    # Rev6 matches the BOM's vendor stock value; legacy CSV matched the CAD part number.
    if args.part_col is None:
        args.part_col = 'stock number' if args.from_db else 'part number'

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}")
        sys.exit(1)

    print(f"Loading BOM: {input_path}")
    bom = pd.read_csv(input_path, dtype=str).fillna('')

    # Case-insensitive column find
    part_col = next((c for c in bom.columns if c.lower() == args.part_col.lower()), None)
    if part_col is None:
        print(f"ERROR: Column '{args.part_col}' not found in BOM. Available: {list(bom.columns)}")
        sys.exit(1)

    # ---- Golden Index source: DB (source of truth) or CSV (fallback) ----
    if args.from_db or not args.golden_index:
        db_path = resolve_db_path(args.db)
        if not db_path:
            print("ERROR: --from-db requested (or no CSV given) but no ops.db found.")
            sys.exit(1)
        print(f"Loading Golden Index from DB: {db_path} :: {GOLDEN_TABLE}")
        golden = load_golden_index_db(db_path)
    else:
        golden_path = Path(args.golden_index)
        if not golden_path.exists():
            print(f"ERROR: Golden Index file not found: {golden_path}")
            sys.exit(1)
        print(f"Loading Golden Index from CSV: {golden_path}")
        golden = load_golden_index(golden_path)
    print(f"  {len(golden)} index entries loaded.\n")

    # Text columns used to recover an in-house NBSS stock code when the
    # `stock number` column is blank (design BOMs leave it empty for
    # Sesame-made parts; the NBSS code lives in the description instead).
    col_lc = {c.lower(): c for c in bom.columns}
    text_cols = [col_lc[c] for c in ('description', 'item', 'part number', 'part_number')
                 if c in col_lc]

    results = []
    matched = 0
    fallback = 0
    unmatched = 0

    for idx, row in bom.iterrows():
        part_number = str(row[part_col]).strip()
        # Primary: the vendor stock value (the wired match column).
        match = match_part(part_number, golden, args.threshold) if part_number else None

        # Fallback (when the primary column is blank or found nothing): recover
        # an in-house NBSS stock code and match it EXACTLY (precise — no fuzzy),
        # so it can only ever produce a correct hit. Two sources, in order:
        #   1. cost centre == 'NBSS' -> derive from the structured part number
        #      (robust, preferred; see nbss_from_costcentre)
        #   2. otherwise recover an 'NBSS.<n>' code from the description text
        #      (safety net for NBSS references outside an NBSS cost centre)
        if not match:
            nbss = nbss_from_costcentre(row, col_lc)
            if not nbss:
                nbss = extract_nbss_code(' '.join(str(row[c]) for c in text_cols))
            if nbss:
                m2 = match_exact_key(nbss, golden)
                if m2:
                    m2['Match Type'] = f"{m2['Match Type']} (nbss-fallback)"
                    m2['Matched Stock_Number'] = m2.get('Matched Stock_Number') or nbss
                    match = m2
                    fallback += 1

        if match:
            matched += 1
        else:
            match = _empty_match()
            unmatched += 1
            print(f"  [UNMATCHED] Row {idx + 2}: '{part_number}'")
        results.append(match)

    result_df = pd.DataFrame(results)
    output_df = pd.concat([bom.reset_index(drop=True), result_df.reset_index(drop=True)], axis=1)
    output_df.to_csv(output_path, index=False)

    print(f"\n{'='*55}")
    print(f"  Matched:   {matched}  (of which via NBSS fallback: {fallback})")
    print(f"  Unmatched: {unmatched}")
    print(f"  Total:     {len(bom)}")
    print(f"  Output:    {output_path}")
    print(f"{'='*55}")


if __name__ == '__main__':
    main()
