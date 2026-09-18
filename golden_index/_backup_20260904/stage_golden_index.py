#!/usr/bin/env python3
"""
stage_golden_index.py
=====================================================================
Turn the resolved KAN-grain Golden Index (golden_index_master.json)
into the artefacts the Hermes/MRP plumbing needs, cleanly separating
the two concerns Giles called out:

  CONCERN A — STOCK CHECK (the Golden Index core purpose)
      BOM stock value  ->  Golden Index  ->  KAN number  ->  identity.
      Only the stock_number -> KAN mapping matters here. Assembly packs
      and Sesame part numbers are NOT part of this path.

  CONCERN B — SESAME / ASANA TASK UPDATE (separate)
      Push per-KAN build-pack codes (as a groupable dropdown value) and
      Sesame part numbers onto the Asana stock task. Staged only; never
      written by this script.

WHAT THIS SCRIPT PRODUCES (all deterministic, next to the script)
  1. golden_index_rev6_lookup.csv
        Matcher-facing view at STOCK-NUMBER grain, using the SAME column
        contract the existing Stage 2 matcher (02_golden_index_match.py)
        and seeder (seed_golden_index.py) already read:
            Stock_Number, Mapped_KAN, Asana_URL, Confidence,
            Match_Type, Asana_TYPE_OF_STOCK
        => Stage 2 keeps working with no code change; only the data is
           reorganised.
  2. asana_sesame_task_updates.csv
        CONCERN-B staging: one row per KAN with the build-pack dropdown
        values and Sesame PNs to push, plus a best-effort link to the
        existing KANBAN Asana task (stock_inventory) so a human/agent can
        review before any write.
  3. ops.db table  golden_index_rev6   (only with --load-db)
        Additive KAN-grain table. Non-destructive: it does not touch the
        old (empty, incorrect) golden_index table. Reversible with
        --rollback (DROP TABLE golden_index_rev6).

USAGE
    python3 stage_golden_index.py                 # write CSVs only
    python3 stage_golden_index.py --load-db       # + create/populate table
    python3 stage_golden_index.py --rollback      # drop the rev6 table
    python3 stage_golden_index.py --db PATH ...   # override ops.db path
=====================================================================
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_JSON = os.path.join(HERE, "golden_index_master.json")
# ops.db location differs per host; try the known Windows + POSIX mounts.
_DB_CANDIDATES = [
    os.path.join(HERE, "..", "db", "ops.db"),
    r"C:\Users\GilesHugo\SES_Workspace\db\ops.db",
    "/sessions/eloquent-intelligent-curie/mnt/SES_Workspace/db/ops.db",
]
TABLE = "golden_index_rev6"
LIST_SEP = " | "


def norm(s: str) -> str:
    """Normalised comparison key (matches build_golden_index.normalise_stock)."""
    s = str(s or "").upper().replace("\xa0", " ").strip()
    s = re.sub(r"^(RS\s+STOCK\s+NO|RS\s+STOCK|STOCK\s+NO|RS)\b[:.\s-]*", "", s)
    return re.sub(r"[^A-Z0-9]", "", s)


def resolve_db(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    for c in _DB_CANDIDATES:
        if os.path.exists(c):
            return os.path.abspath(c)
    return None


def load_master(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["records"]


# ---------------------------------------------------------------------------
# CONCERN A — stock-number -> KAN lookup (matcher-compatible)
# ---------------------------------------------------------------------------
def write_lookup(records: list[dict], path: str) -> dict:
    """
    Emit the matcher-facing lookup at stock-number grain and detect any
    stock number that maps to more than one KAN (would make BOM matching
    ambiguous - flagged, never silently merged).
    """
    by_stock_norm: dict[str, set] = {}
    rows = []
    for r in records:
        stock = r["stock_number"]
        rows.append(
            {
                "Stock_Number": stock,
                "Mapped_KAN": r["kan"],
                "Asana_URL": "",  # populated by Concern-B linkage / future sync
                "Confidence": max(
                    (int(v) for v in r["match_types"].values()), default=""
                )
                if r["match_types"]
                else "",
                "Match_Type": ";".join(sorted(r["match_types"])),
                "Asana_TYPE_OF_STOCK": "KANBAN",
            }
        )
        by_stock_norm.setdefault(norm(stock), set()).add(r["kan"])

    rows.sort(key=lambda x: (x["Stock_Number"].upper(), x["Mapped_KAN"]))
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "Stock_Number",
                "Mapped_KAN",
                "Asana_URL",
                "Confidence",
                "Match_Type",
                "Asana_TYPE_OF_STOCK",
            ],
        )
        w.writeheader()
        w.writerows(rows)

    ambiguous = {k: sorted(v) for k, v in by_stock_norm.items() if len(v) > 1}
    return {"lookup_rows": len(rows), "ambiguous_stock_numbers": ambiguous}


# ---------------------------------------------------------------------------
# CONCERN B — Sesame/Asana task update staging
# ---------------------------------------------------------------------------
def link_kanban(records: list[dict], db_path: str | None) -> dict:
    """
    Best-effort link each KAN to an existing KANBAN Asana task
    (stock_inventory) so the Concern-B push has a target GID to review.
    Match on normalised stock number vs vendor_part_number/sesame_pn, or
    on any of the KAN's Sesame PNs. Returns {kan: {gid, how}}.
    """
    if not db_path or not os.path.exists(db_path):
        return {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    inv = con.execute(
        "SELECT asana_task_gid, sesame_pn, vendor_part_number, kan_number "
        "FROM stock_inventory"
    ).fetchall()
    con.close()
    by_norm = {}
    for gid, spn, vpn, kann in inv:
        for key in (spn, vpn, kann):
            k = norm(key)
            if k:
                by_norm.setdefault(k, gid)
    out = {}
    for r in records:
        cand = [norm(r["stock_number"])] + [norm(p) for p in r["asana_sesame_part_numbers"]]
        for c in cand:
            if c in by_norm:
                out[r["kan"]] = {"gid": by_norm[c], "how": "stock/sesame norm-match"}
                break
    return out


def write_concern_b(records: list[dict], links: dict, path: str) -> dict:
    rows = []
    linked = 0
    for r in sorted(records, key=lambda x: x["kan"]):
        link = links.get(r["kan"], {})
        if link:
            linked += 1
        rows.append(
            {
                "KAN": r["kan"],
                "Stock_Number": r["stock_number"],
                "KANBAN_Asana_Task_GID": link.get("gid", ""),
                "Link_Method": link.get("how", "UNLINKED - needs manual match"),
                "Build_Pack_Dropdown_Values": LIST_SEP.join(r["assembly_packs"]),
                "Sesame_Part_Numbers": LIST_SEP.join(r["asana_sesame_part_numbers"]),
                "Description": r["description"],
                "Review_Status": "PENDING",
            }
        )
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "KAN",
                "Stock_Number",
                "KANBAN_Asana_Task_GID",
                "Link_Method",
                "Build_Pack_Dropdown_Values",
                "Sesame_Part_Numbers",
                "Description",
                "Review_Status",
            ],
        )
        w.writeheader()
        w.writerows(rows)
    all_packs = sorted({p for r in records for p in r["assembly_packs"]})
    return {"rows": len(rows), "linked": linked, "distinct_pack_options": all_packs}


# ---------------------------------------------------------------------------
# DB load (additive, reversible)
# ---------------------------------------------------------------------------
DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    kan_number          TEXT PRIMARY KEY,
    stock_number        TEXT NOT NULL,
    stock_number_norm   TEXT NOT NULL,
    description         TEXT,
    vendor              TEXT,
    assembly_packs      TEXT,   -- delimited; Concern B only
    sesame_part_numbers TEXT,   -- delimited; Concern B only
    asana_task_gid      TEXT,
    match_types         TEXT,
    resolution_source   TEXT,
    source_row_count    INTEGER,
    updated_at          TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_stocknorm ON {TABLE}(stock_number_norm);
"""


def load_db(records: list[dict], links: dict, db_path: str) -> int:
    con = sqlite3.connect(db_path)
    now = datetime.now(timezone.utc).isoformat()
    try:
        con.executescript(DDL)
        con.execute(f"DELETE FROM {TABLE}")  # full refresh; table is ours alone
        for r in records:
            con.execute(
                f"INSERT INTO {TABLE} (kan_number, stock_number, stock_number_norm, "
                "description, vendor, assembly_packs, sesame_part_numbers, "
                "asana_task_gid, match_types, resolution_source, source_row_count, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    r["kan"],
                    r["stock_number"],
                    norm(r["stock_number"]),
                    r["description"],
                    LIST_SEP.join(r["vendors"]),
                    LIST_SEP.join(r["assembly_packs"]),
                    LIST_SEP.join(r["asana_sesame_part_numbers"]),
                    (links.get(r["kan"], {}) or {}).get("gid"),
                    ";".join(sorted(r["match_types"])),
                    r.get("resolution_source"),
                    r["source_row_count"],
                    now,
                ),
            )
        con.commit()
        return con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    finally:
        con.close()


def rollback_db(db_path: str) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(f"DROP TABLE IF EXISTS {TABLE}")
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Stage the Rev6 Golden Index plumbing.")
    ap.add_argument("--json", default=DEFAULT_JSON)
    ap.add_argument("--db", default=None, help="ops.db path (auto-detected if omitted)")
    ap.add_argument("--load-db", action="store_true", help="Create/populate golden_index_rev6")
    ap.add_argument("--rollback", action="store_true", help="Drop golden_index_rev6 and exit")
    ap.add_argument("--outdir", default=HERE)
    args = ap.parse_args(argv)

    db_path = resolve_db(args.db)
    if args.rollback:
        if not db_path:
            raise SystemExit("No ops.db found to roll back.")
        rollback_db(db_path)
        print(f"Dropped table {TABLE} from {db_path}")
        return

    records = load_master(args.json)
    links = link_kanban(records, db_path)

    lookup_path = os.path.join(args.outdir, "golden_index_rev6_lookup.csv")
    conb_path = os.path.join(args.outdir, "asana_sesame_task_updates.csv")
    a = write_lookup(records, lookup_path)
    b = write_concern_b(records, links, conb_path)

    loaded = None
    if args.load_db:
        if not db_path:
            raise SystemExit("--load-db requested but no ops.db found.")
        loaded = load_db(records, links, db_path)

    print("=" * 64)
    print("GOLDEN INDEX STAGING COMPLETE")
    print("=" * 64)
    print(f"  records (KAN grain)          : {len(records)}")
    print(f"  [A] matcher lookup rows      : {a['lookup_rows']}  -> {os.path.basename(lookup_path)}")
    print(f"      ambiguous stock->KAN     : {len(a['ambiguous_stock_numbers'])}")
    for k, v in a["ambiguous_stock_numbers"].items():
        print(f"        {k}: {v}")
    print(f"  [B] sesame-task update rows  : {b['rows']}  -> {os.path.basename(conb_path)}")
    print(f"      linked to KANBAN task    : {b['linked']} / {b['rows']}")
    print(f"      distinct build-pack opts : {len(b['distinct_pack_options'])}")
    if loaded is not None:
        print(f"  DB table {TABLE:22s}: {loaded} rows @ {db_path}")
    else:
        print(f"  DB                           : not loaded (pass --load-db)")
    return {"a": a, "b": b, "loaded": loaded}


if __name__ == "__main__":
    main()
