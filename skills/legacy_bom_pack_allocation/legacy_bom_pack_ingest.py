"""
legacy_bom_pack_ingest.py — Allocate the correct build-pack GID to parts from a
design-team Legacy BOM folder, and dual-write it to ops.db + Asana.

This is a PACK-ALLOCATION step, NOT a BOM import. Parts are assumed already
imported into parts_control; this maps each one to its verified build-pack task
GID, replacing fragile free-text pack-name links.

Read this before changing it
-----------------------------
* One .xlsx per build pack. THE FILENAME IS THE PACK — every orderable row in a
  file belongs to that one pack. (Per-pack handover files have NO "Drawing Pack"
  separator rows, so build_lookup_map from the import pipeline does NOT apply.)
* Pack resolution: match the filename stem to build_assembly_packs.pack_reference
  and prefer the **Build Schedule** project pack; ignore the '.DRAWING'/Drawing
  Control variant. If 0 or ambiguous matches -> flag, skip file (never guess).
* Row classification (what counts as an orderable BOM item vs a design artifact):
  ORDERABLE  : PART NUMBER matches the Sesame pattern AND has a
               VENDOR or COST CENTER or STOCK NUMBER value.
  ARTIFACT   : "_MIR" software mirror; "Assembly by"/"Welded by"/"Sub Assembly"/
               "Welded Assembly" grouping; or no vendor/cost/stock (pure grouping).
               ISO/DIN fasteners fall out naturally (no vendor/cost/stock) and are
               intentionally left out (Giles, 2026-08-11).
* --scan is READ-ONLY (default). --commit applies ONLY the candidates in a stage
  file produced by a prior --scan, so a human has reviewed exactly what is written.

Dual-write on commit
--------------------
  DB    : db_writer.upsert_parts_control(...) sets build_assembly_pack_gid +
          association_status='linked' (logged to db_write_log). Sole gateway.
  Asana : asana_batch_updater.push_updates(...) sets the 'Build Packs reference'
          custom field (GID 1211660742155293, type reference) to the pack task GID.
  Audit : db_writer.log_asana_write(...) one row per task.

Usage
-----
  python legacy_bom_pack_ingest.py --parts-folder "<local Legacy BOM's folder>" --scan
  python legacy_bom_pack_ingest.py --parts-folder "<...>" --commit --stage-file <scan_json>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# ── path / dependency resolution (survives the move off C:\Users\GilesHugo) ──
_WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_WORKSPACE / "lib"))
sys.path.insert(0, str(_WORKSPACE / "skills" / "purchasing"))
sys.path.insert(0, str(_WORKSPACE / "skills" / "asana" / "scripts"))

try:
    from path_config import p as _p
    DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")
except Exception:
    _fallback = _WORKSPACE / "db" / "ops.db"
    DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or (
        str(_fallback) if _fallback.exists() else None
    )

BUILD_PACKS_REF_GID = "1211660742155293"   # Asana 'Build Packs reference' (type: reference)
BUILD_SCHEDULE_PROJECT = "Build Schedule"
SESAME_PN = re.compile(r'^\d{4}\.[\w.]+', re.I)
ARTIFACT_WORDS = ("assembly by", "welded by", "sub assembly", "welded assembly")
PACK_SOURCE = "legacy_bom_pack_allocation"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ── pack resolution ─────────────────────────────────────────────────────────
def resolve_pack(conn: sqlite3.Connection, file_stem: str) -> dict:
    """Return {status, gid, pack_reference, candidates:[...]} for a file stem.

    status ∈ resolved | ambiguous | not_found
    """
    pat = _like_escape(file_stem) + "%"
    rows = conn.execute(
        """SELECT asana_task_gid, pack_reference, pack_name, project_name
             FROM build_assembly_packs
            WHERE pack_reference LIKE ? ESCAPE '\\'""",
        (pat,),
    ).fetchall()
    # Only true prefix matches on the stem (LIKE is a coarse pre-filter).
    cand = [r for r in rows if (r["pack_reference"] or "").startswith(file_stem)]
    # Prefer Build Schedule packs, excluding the Drawing Control '.DRAWING' variant.
    bs = [
        r for r in cand
        if r["project_name"] == BUILD_SCHEDULE_PROJECT
        and ".DRAWING" not in (r["pack_reference"] or "").upper()
    ]
    all_ref = [dict(r) for r in cand]
    if len(bs) == 1:
        return {"status": "resolved", "gid": bs[0]["asana_task_gid"],
                "pack_reference": bs[0]["pack_reference"], "candidates": all_ref}
    if len(bs) == 0:
        return {"status": "not_found", "gid": None, "pack_reference": None,
                "candidates": all_ref}
    return {"status": "ambiguous", "gid": None, "pack_reference": None,
            "candidates": [dict(r) for r in bs]}


# ── BOM parse + classification ──────────────────────────────────────────────
def _find_header(df0: pd.DataFrame):
    for i, row in df0.iterrows():
        vals = [str(x).strip().upper() for x in row.tolist()]
        if "PART NUMBER" in vals:
            return i
    return None


def classify_rows(xlsx_path: Path) -> list[dict]:
    """Return [{item, sesame_pn, description, vendor, cost, stock, orderable, reason, confidence}]."""
    raw = pd.read_excel(xlsx_path, header=None, dtype=str)
    hdr = _find_header(raw)
    if hdr is None:
        return []
    df = pd.read_excel(xlsx_path, header=hdr, dtype=str).fillna("")
    df.columns = [str(c).strip() for c in df.columns]

    def g(r, c):
        return str(r.get(c, "")).strip()

    out = []
    for _, r in df.iterrows():
        pn = g(r, "PART NUMBER")
        if not pn:
            continue
        desc = g(r, "DESCRIPTION")
        vendor, cost, stock = g(r, "VENDOR"), g(r, "COST CENTER"), g(r, "STOCK NUMBER")
        dl = desc.lower()
        reason = None
        if "_MIR" in pn.upper():
            reason = "_MIR artifact"
        elif any(w in dl for w in ARTIFACT_WORDS):
            reason = "assembly/weld grouping"
        elif not SESAME_PN.match(pn):
            reason = "non-Sesame part number"
        elif not (vendor or cost or stock):
            reason = "no vendor/cost/stock (grouping/fastener)"
        orderable = reason is None
        confidence = "high"
        if orderable and not (vendor or stock) and cost:
            confidence = "med"
        out.append({
            "item": g(r, "ITEM"), "sesame_pn": pn, "description": desc[:60],
            "vendor": vendor, "cost": cost, "stock": stock,
            "orderable": orderable, "reason": reason, "confidence": confidence,
        })
    return out


# ── parts_control cross-check ───────────────────────────────────────────────
def _rev_strip(pn: str) -> str:
    return re.sub(r"\.\d+$", "", pn)


def match_parts_control(conn: sqlite3.Connection, sesame_pn: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT asana_task_gid, part_name, build_assembly_pack_gid, association_status, "
        "is_completed FROM parts_control WHERE sesame_pn = ?", (sesame_pn,)
    ).fetchall()
    if rows:
        return rows, "exact"
    base = _rev_strip(sesame_pn)
    if base != sesame_pn:
        rows = conn.execute(
            "SELECT asana_task_gid, part_name, build_assembly_pack_gid, association_status, "
            "is_completed FROM parts_control WHERE sesame_pn = ?", (base,)
        ).fetchall()
        if rows:
            return rows, "revision_normalised"
    return [], None


def build_candidates(conn: sqlite3.Connection, folder: Path) -> dict:
    """Scan a folder of per-pack xlsx and return the full candidate report."""
    files = sorted(list(folder.glob("*.xlsx")) + list(folder.glob("*.xls")))
    files = [f for f in files if not f.name.startswith("~$")]   # skip Excel lockfiles
    report = {"folder": str(folder), "generated_at": _now(), "files": [],
              "candidates": [], "flags": []}
    seen_tasks: dict[str, str] = {}   # task_gid -> source_file (dedupe across packs)

    for f in files:
        stem = f.stem
        pack = resolve_pack(conn, stem)
        finfo = {"file": f.name, "pack_status": pack["status"],
                 "pack_gid": pack["gid"], "pack_reference": pack["pack_reference"]}
        if pack["status"] != "resolved":
            finfo["candidate_packs"] = pack["candidates"]
            report["flags"].append({"file": f.name, "issue": f"pack_{pack['status']}",
                                     "candidates": pack["candidates"]})
            report["files"].append(finfo)
            continue

        rows = classify_rows(f)
        orderable = [r for r in rows if r["orderable"]]
        excluded = [r for r in rows if not r["orderable"]]
        finfo.update({"rows": len(rows), "orderable": len(orderable),
                      "excluded": len(excluded)})
        held = 0
        for r in orderable:
            matches, how = match_parts_control(conn, r["sesame_pn"])
            if not matches:
                held += 1
                continue
            for m in matches:
                gid = m["asana_task_gid"]
                if gid in seen_tasks:
                    continue
                seen_tasks[gid] = f.name
                cur = m["build_assembly_pack_gid"]
                st = m["association_status"]
                if st == "linked" and cur == pack["gid"]:
                    action = "already_correct"
                elif st == "linked_by_ref" or (cur and cur != pack["gid"]):
                    action = "migrate"          # text/other -> verified GID
                else:
                    action = "new"              # unassociated / NULL -> allocate
                report["candidates"].append({
                    "part_task_gid": gid,
                    "part_name": m["part_name"],
                    "sesame_pn": r["sesame_pn"],
                    "match": how,
                    "old_value": cur,
                    "old_status": st,
                    "new_pack_gid": pack["gid"],
                    "pack_reference": pack["pack_reference"],
                    "source_file": f.name,
                    "confidence": r["confidence"],
                    "action": action,
                })
        finfo["held_not_in_parts_control"] = held
        report["files"].append(finfo)

    c = report["candidates"]
    report["summary"] = {
        "files": len(files),
        "candidates": len(c),
        "migrate": sum(1 for x in c if x["action"] == "migrate"),
        "new": sum(1 for x in c if x["action"] == "new"),
        "already_correct": sum(1 for x in c if x["action"] == "already_correct"),
        "flags": len(report["flags"]),
    }
    return report


# ── commit (dual-write) ─────────────────────────────────────────────────────
def commit(stage: dict, db_path: str, pause: float = 0.35) -> dict:
    import db_writer
    from asana_batch_updater import push_updates, get_asana_token

    actionable = [c for c in stage["candidates"] if c["action"] in ("migrate", "new")]
    if not actionable:
        return {"db_written": 0, "asana_ok": 0, "asana_fail": 0, "note": "nothing actionable"}

    token = get_asana_token()
    if not token:
        raise SystemExit("ERROR: ASANA_PERSONAL_ACCESS_TOKEN not found — cannot commit.")

    # 1) Asana first (source of truth is the GID link). Batch API + 429 retry.
    updates = [{
        "task_gid": c["part_task_gid"],
        "task_name": c.get("part_name") or c["sesame_pn"],
        "custom_field_gid": BUILD_PACKS_REF_GID,
        "new_enum_gid": c["new_pack_gid"],     # value for the reference field = pack task GID
    } for c in actionable]
    ok, fail = push_updates(updates, token, dry_run=False)

    # 2) DB mirror via the sole gateway (targeted UPDATE) + audit log. push_updates
    #    returns aggregate counts only, so we mirror all actionable rows and rely on
    #    pack_association.py --list afterwards to reconcile association_status.
    now = _now()
    db_written = db_missing = 0
    for c in actionable:
        r = db_writer.update_parts_pack_allocation(
            c["part_task_gid"], c["new_pack_gid"],
            source=PACK_SOURCE, checked_at=now, db_path=db_path)
        db_written += r["written"]
        db_missing += r["skipped"]
        db_writer.log_asana_write({
            "task_gid": c["part_task_gid"],
            "task_name": c.get("part_name"),
            "action_type": "set_custom_field",
            "field_name": "build_packs_reference",
            "field_gid": BUILD_PACKS_REF_GID,
            "value_applied": c["new_pack_gid"],
            "result": "success",
            "error_detail": None,
            "email_id": None,
        }, source=PACK_SOURCE, db_path=db_path)
    return {"db_written": db_written, "db_missing": db_missing,
            "asana_ok": ok, "asana_fail": fail}


# ── CLI ─────────────────────────────────────────────────────────────────────
def _print_report(rep: dict) -> None:
    s = rep["summary"]
    print(f"\nLegacy BOM pack allocation — SCAN (read-only)")
    print(f"Folder: {rep['folder']}")
    print(f"Files: {s['files']} | Candidates: {s['candidates']} "
          f"(migrate {s['migrate']}, new {s['new']}, already_correct {s['already_correct']}) "
          f"| Flags: {s['flags']}")
    for f in rep["files"]:
        if f["pack_status"] != "resolved":
            print(f"  ⚠ {f['file']}: pack {f['pack_status']} — needs review, skipped")
            continue
        print(f"  ✓ {f['file']} → pack {f['pack_gid']} | rows {f['rows']} "
              f"orderable {f['orderable']} excluded {f['excluded']} "
              f"held {f.get('held_not_in_parts_control', 0)}")
    if rep["candidates"]:
        print("\n  candidate updates (first 20):")
        for c in rep["candidates"][:20]:
            print(f"    {c['action']:14} {c['sesame_pn']:30} task={c['part_task_gid']} "
                  f"→ {c['new_pack_gid']} [{c['match']},{c['confidence']}]")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parts-folder", required=True,
                    help="Local folder of per-pack Legacy BOM .xlsx files")
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument("--scan", action="store_true", help="Read-only scan (default)")
    ap.add_argument("--commit", action="store_true",
                    help="Apply candidates from --stage-file (dual-write DB + Asana)")
    ap.add_argument("--stage-file", default=None,
                    help="Path to write the scan JSON (scan) / read for commit")
    ap.add_argument("--pause", type=float, default=0.35)
    args = ap.parse_args()

    if not args.db_path or not Path(args.db_path).is_file():
        raise SystemExit(f"ERROR: ops.db not found at {args.db_path!r}")
    folder = Path(args.parts_folder)
    if not folder.is_dir():
        raise SystemExit(f"ERROR: --parts-folder not found: {folder}")

    if args.commit:
        stage_path = Path(args.stage_file) if args.stage_file else None
        if not stage_path or not stage_path.is_file():
            raise SystemExit("ERROR: --commit requires --stage-file from a reviewed --scan.")
        stage = json.loads(stage_path.read_text(encoding="utf-8"))
        res = commit(stage, args.db_path, args.pause)
        print(f"COMMIT done. DB written: {res['db_written']} "
              f"(missing: {res.get('db_missing', 0)}) | "
              f"Asana ok: {res.get('asana_ok')} fail: {res.get('asana_fail')}")
        return

    # default: scan
    conn = sqlite3.connect(args.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        report = build_candidates(conn, folder)
    finally:
        conn.close()
    _print_report(report)
    out = Path(args.stage_file) if args.stage_file else (folder / "_pack_allocation_scan.json")
    try:
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nStage file written: {out}")
        print("Review it, then: --commit --stage-file <that file>")
    except OSError as e:
        # Dropbox source folders are read-only; fall back to CWD.
        alt = Path.cwd() / "_pack_allocation_scan.json"
        alt.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\n(stage folder not writable: {e}) Stage file written: {alt}")


if __name__ == "__main__":
    main()
