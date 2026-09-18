#!/usr/bin/env python3
"""
validate_bom_matching.py
=====================================================================
Measure how well the Rev6 Golden Index (ops.db: golden_index_rev6)
maps real BOM lines, by replaying every historical Stage-1 BOM in
`1.BOM IMPORT into Asana/pipeline_runs/` through the matching logic.

It reports, per BOM and in total:
  * lines, and lines that actually carry a 'stock number' value
  * EXACT matches on the strong stock key (RS/dot/dash-insensitive)
  * FUZZY matches (>= threshold) that exact missed
  * unmatched (=> ORDER)
and writes a markdown + CSV summary next to this script.

This is a measurement tool only — it never writes to Asana or ops.db.
=====================================================================
"""
from __future__ import annotations
import csv, glob, os, re, sqlite3, sys
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "..", "skills", "bom_pipeline",
                    "1.BOM IMPORT into Asana", "pipeline_runs")
def _pathconfig_db() -> str:
    """Canonical ops.db via SES_Workspace/lib/path_config.py (PATH_CONFIG.yaml)."""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(HERE, "..", "lib"))
        from path_config import p as _p
        return _p("db.ops")
    except Exception:
        return ""


DB_CANDIDATES = [
    _pathconfig_db(),                                 # canonical (PATH_CONFIG.yaml)
    r"C:\Users\GilesHugo\SES_Workspace\db\ops.db",    # resilient literal fallback
    os.path.join(HERE, "..", "db", "ops.db"),
]
THRESHOLD = 80
_RS = re.compile(r'^(RS\s+STOCK\s+NO|RS\s+STOCK|STOCK\s+NO|RS)\b[:.\s-]*', re.I)


def stock_key(s):
    v = str(s or '').upper().replace('\xa0', ' ').strip()
    return re.sub(r'[^A-Z0-9]', '', _RS.sub('', v))


def normalise(s):
    return re.sub(r'\s+', ' ', str(s or '').strip().lower())


def sim(a, b):
    return int(SequenceMatcher(None, a, b).ratio() * 100)


def load_index(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute("SELECT kan_number, stock_number FROM golden_index_rev6").fetchall()
    con.close()
    exact = {}
    fuzzy = []
    for kan, stock in rows:
        k = stock_key(stock)
        if k:
            exact.setdefault(k, kan)
        fuzzy.append((normalise(stock), kan))
    return exact, fuzzy


def match(stockval, exact, fuzzy):
    k = stock_key(stockval)
    if k and k in exact:
        return exact[k], 100, "Exact"
    n = normalise(stockval)
    if not n:
        return None
    best = (None, 0, "")
    for gnorm, kan in fuzzy:
        if not gnorm:
            continue
        if n in gnorm or gnorm in n:
            sc = 90
        else:
            sc = sim(n, gnorm)
        if sc > best[1]:
            best = (kan, sc, "Fuzzy")
    return best if best[1] >= THRESHOLD else None


def main():
    db = next((c for c in DB_CANDIDATES if c and os.path.exists(c)), None)
    if not db:
        sys.exit("ops.db not found")
    exact, fuzzy = load_index(db)
    files = sorted(glob.glob(os.path.join(RUNS, "*", "stage1_consolidated.csv")))

    per_bom = []
    T = dict(lines=0, filled=0, exact=0, fuzzy=0, unmatched=0)
    for f in files:
        try:
            rows = list(csv.DictReader(open(f, encoding="utf-8-sig")))
        except Exception:
            continue
        if not rows:
            continue
        cols = {c.lower(): c for c in rows[0].keys()}
        sc = cols.get("stock number")
        if not sc:
            continue
        b = dict(lines=len(rows), filled=0, exact=0, fuzzy=0, unmatched=0)
        for r in rows:
            val = (r.get(sc) or "").strip()
            if not val:
                continue
            b["filled"] += 1
            m = match(val, exact, fuzzy)
            if not m:
                b["unmatched"] += 1
            elif m[2] == "Exact":
                b["exact"] += 1
            else:
                b["fuzzy"] += 1
        run = os.path.basename(os.path.dirname(f))
        per_bom.append((run, b))
        for k in T:
            T[k] += b[k]

    # de-dupe repeated runs of the same project (keep the run with most filled)
    # (report raw; duplicates are real re-runs)
    matched = T["exact"] + T["fuzzy"]
    rate = (matched / T["filled"] * 100) if T["filled"] else 0

    md = os.path.join(HERE, "bom_matching_report.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("# BOM Matching Validation — Rev6 Golden Index\n\n")
        fh.write(f"Index entries: **{len(exact)}** (golden_index_rev6) · "
                 f"BOM runs replayed: **{len(per_bom)}** · threshold {THRESHOLD}\n\n")
        fh.write("Matching is on the BOM **`stock number`** column (vendor stock value → KAN).\n\n")
        fh.write("## Totals\n\n")
        fh.write(f"- BOM lines: **{T['lines']}**\n")
        fh.write(f"- Lines carrying a stock number: **{T['filled']}**\n")
        fh.write(f"- Exact matches (strong stock key): **{T['exact']}**\n")
        fh.write(f"- Fuzzy matches (≥{THRESHOLD}): **{T['fuzzy']}**\n")
        fh.write(f"- Matched of stock-bearing lines: **{matched} / {T['filled']} = {rate:.1f}%**\n")
        fh.write(f"- Unmatched (→ ORDER): **{T['unmatched']}**\n\n")
        fh.write("Lines with no stock number are bespoke items that correctly route to ORDER.\n\n")
        fh.write("## Per BOM run\n\n")
        fh.write("| BOM run | lines | stock-filled | exact | fuzzy | unmatched |\n")
        fh.write("|---|---:|---:|---:|---:|---:|\n")
        for run, b in per_bom:
            fh.write(f"| {run[:44]} | {b['lines']} | {b['filled']} | "
                     f"{b['exact']} | {b['fuzzy']} | {b['unmatched']} |\n")

    csvp = os.path.join(HERE, "bom_matching_report.csv")
    with open(csvp, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["bom_run", "lines", "stock_filled", "exact", "fuzzy", "unmatched"])
        for run, b in per_bom:
            w.writerow([run, b["lines"], b["filled"], b["exact"], b["fuzzy"], b["unmatched"]])

    print("=" * 60)
    print("BOM MATCHING VALIDATION (Rev6, DB source of truth)")
    print("=" * 60)
    print(f"  index entries         : {len(exact)}")
    print(f"  BOM runs replayed     : {len(per_bom)}")
    print(f"  BOM lines             : {T['lines']}")
    print(f"  lines w/ stock number : {T['filled']}")
    print(f"  exact matches         : {T['exact']}")
    print(f"  fuzzy matches         : {T['fuzzy']}")
    print(f"  matched / stock-filled: {matched}/{T['filled']} = {rate:.1f}%")
    print(f"  unmatched (-> ORDER)  : {T['unmatched']}")
    print(f"\n  report: {md}")


if __name__ == "__main__":
    main()
