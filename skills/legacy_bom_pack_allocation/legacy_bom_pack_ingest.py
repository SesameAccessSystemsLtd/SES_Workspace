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
def _normalise_stem(file_stem: str) -> str:
    """Strip trailing revision/version/suffix markers from a legacy-BOM file stem
    so it can match the Build Schedule pack_reference.

    Langley 1037 showed two failure modes (2026-08-17):
      * '1037.1014.M.Gate+Support_rev2'  -> should match '1037.1014.M.Gate+Support.'
      * '1037.9990.A.Cover_rev3'         -> should match '1037.9990.A.Cover.'
    We strip a trailing '_rev<N>' / '_v<N>' / '-rev<N>' (case-insensitive) and any
    trailing '.' so the bare pack name is the match key. 'Gate+Support' keeps its
    '+' because that is part of the real pack name.
    """
    s = file_stem.strip().rstrip(".")
    s = re.sub(r"[-_]?(?:rev|v|r)\s*\d+$", "", s, flags=re.I).rstrip(".")
    return s


def resolve_pack(conn: sqlite3.Connection, file_stem: str) -> dict:
    """Return {status, gid, pack_reference, candidates:[...]} for a file stem.

    status ∈ resolved | ambiguous | not_found
    """
    base = _normalise_stem(file_stem)
    pat = _like_escape(base) + "%"
    rows = conn.execute(
        """SELECT asana_task_gid, pack_reference, pack_name, project_name
             FROM build_assembly_packs
            WHERE pack_reference LIKE ? ESCAPE '\\'""",
        (pat,),
    ).fetchall()
    # Only true prefix matches on the normalised base (LIKE is a coarse pre-filter).
    cand = [r for r in rows if (r["pack_reference"] or "").startswith(base)]
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
    """Return [{item, sesame_pn, description, vendor, cost, stock, orderable, reason, confidence, qty}]."""
    raw = pd.read_excel(xlsx_path, header=None, dtype=str)
    hdr = _find_header(raw)
    if hdr is None:
        return []
    df = pd.read_excel(xlsx_path, header=hdr, dtype=str).fillna("")
    df.columns = [str(c).strip() for c in df.columns]

    def g(r, c):
        # Accept the exact column name or the case-insensitive variant.
        if c in r.keys():
            return str(r.get(c, "")).strip()
        for k in r.keys():
            if k and k.strip().upper() == c.upper():
                return str(r[k]).strip()
        return ""

    def gqty(r):
        # QTY column is 'ITEM QTY' / 'Item QTY' / 'QTY' — first one present.
        for c in r.keys():
            if not c:
                continue
            u = str(c).strip().upper()
            if u in ("ITEM QTY", "QTY", "ITEMQTY", "ITEM QTY "):
                return str(r[c]).strip()
        return ""

    out = []
    for _, r in df.iterrows():
        pn = g(r, "PART NUMBER")
        if not pn:
            continue
        desc = g(r, "DESCRIPTION")
        vendor, cost, stock = g(r, "VENDOR"), g(r, "COST CENTER"), g(r, "STOCK NUMBER")
        qty_raw = gqty(r)
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
            "qty": qty_raw,
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


def _pack_prefix(pn: str) -> str | None:
    """First three dot-components of a part number, e.g. '1037.9991.C.001' -> '1037.9991.C'.

    Returns None for part numbers that don't encode a pack (too short / NBSS-style
    generic PNs like '1037.034.001' which have no pack component)."""
    parts = (pn or "").split(".")
    return ".".join(parts[:3]) if len(parts) >= 4 else None


def _pack_candidates(rows, pre: str) -> list:
    """Filter raw build_assembly_packs rows to Build Schedule packs whose
    pack_reference starts with the given prefix (excluding .DRAWING variants)."""
    return [r for r in rows
            if (r["pack_reference"] or "").startswith(pre)
            and ".DRAWING" not in (r["pack_reference"] or "").upper()]


def resolve_pack_by_pn(conn: sqlite3.Connection, sesame_pn: str) -> dict:
    """Resolve a part's pack from its part-NUMBER prefix (for consolidated BOM files).

    Consolidated files (e.g. Langley 'bellows+tapeswitch+trims') contain parts from
    MANY packs, so the filename can't name a single pack. We match each part's prefix
    to a Build Schedule pack_reference.

    Matching strategy (tiered, never guesses):
      1. Try the FULL normalized pack code as a prefix first. For '1037.9991.C.001'
         we first look for an exact pack base '1037.9991.C.001.' (its own PTFE pack);
         only if that's absent do we fall back to the coarse 3-component prefix
         '1037.9991.C'. This disambiguates C.001 vs C.002 (which ARE separate packs).
      2. Revision markers in the PN (.01/.02 etc.) are stripped for the base match,
         so '1037.1014.M.115.01' still matches pack '1037.1014.M'.$base = first 4 components
    Status ∈ resolved | ambiguous | not_found."""
    parts = [(p or "").strip() for p in (sesame_pn or "").split(".")]
    if len(parts) < 4:
        return {"status": "not_found", "gid": None, "pack_reference": None,
                "candidates": []}
    # Job + pack-rev + item base. rev-normalise item trailing digits (e.g. .115.01 -> .115)
    # is handled by looking up the 4-component base when the full PN has >=5 parts.
    def _find(pre) -> dict:
        pat = _like_escape(pre) + "%"
        rows = conn.execute(
            """SELECT asana_task_gid, pack_reference, pack_name, project_name
                 FROM build_assembly_packs
                WHERE pack_reference LIKE ? ESCAPE '\\'
                  AND project_name = ?""",
            (pat, BUILD_SCHEDULE_PROJECT),
        ).fetchall()
        cand = _pack_candidates(rows, pre)
        if len(cand) == 1:
            return {"status": "resolved", "gid": cand[0]["asana_task_gid"],
                    "pack_reference": cand[0]["pack_reference"],
                    "candidates": [dict(r) for r in cand]}
        if len(cand) == 0:
            return {"status": "not_found", "gid": None, "pack_reference": None,
                    "candidates": [dict(r) for r in cand]}
        return {"status": "ambiguous", "gid": None, "pack_reference": None,
                "candidates": [dict(r) for r in cand]}

    # Tier 1: first 4 components as the specific pack base (e.g. '1037.9991.C.001').
    pre4 = ".".join(parts[:4])
    r4 = _find(pre4)
    if r4["status"] == "resolved":
        return r4
    if r4["status"] == "ambiguous":
        return r4
    # Tier 2: fall back to the 3-component family (catches Bellows/TapeSwitch/Wheelstop
    # where the pack is the family, or the item's 4th segment is a revision not a pack).
    pre3 = _pack_prefix(sesame_pn)
    if pre3 and pre3 != pre4:
        r3 = _find(pre3)
        return r3
    return r4


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
        rows = classify_rows(f)
        orderable = [r for r in rows if r["orderable"]]
        excluded = [r for r in rows if not r["orderable"]]
        finfo.update({"rows": len(rows), "orderable": len(orderable),
                      "excluded": len(excluded)})

        # Per-file pack resolution: a resolved filename gives a pack for every row;
        # a NOT_FOUND filename is treated as a CONSOLIDATED file, so each orderable
        # part's pack is resolved from its own part-number prefix instead.
        per_file_pack = pack if pack["status"] == "resolved" else None

        if pack["status"] not in ("resolved", "not_found"):
            finfo["candidate_packs"] = pack["candidates"]
            report["flags"].append({"file": f.name, "issue": f"pack_{pack['status']}",
                                     "candidates": pack["candidates"]})
            report["files"].append(finfo)
            continue

        held = 0
        for r in orderable:
            # Determine the target pack: from the filename, or per-part for consolidated.
            if per_file_pack is not None:
                use_pack = per_file_pack
                how_pack = "filename"
            else:
                use_pack = resolve_pack_by_pn(conn, r["sesame_pn"])
                how_pack = "pn_prefix"
                if use_pack is None or use_pack["status"] != "resolved":
                    # Part's own pack can't be resolved -> flag (held), not guessed.
                    if use_pack and use_pack["status"] == "ambiguous":
                        report["flags"].append({
                            "file": f.name, "issue": "part_pack_ambiguous",
                            "part": r["sesame_pn"], "candidates": use_pack["candidates"]})
                    continue

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
                if st == "linked" and cur == use_pack["gid"]:
                    action = "already_correct"
                elif st == "linked_by_ref" or (cur and cur != use_pack["gid"]):
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
                    "new_pack_gid": use_pack["gid"],
                    "pack_reference": use_pack["pack_reference"],
                    "source_file": f.name,
                    "confidence": r["confidence"],
                    "qty": r.get("qty", ""),
                    "action": action,
                })
        finfo["held_not_in_parts_control"] = held
        if pack["status"] == "not_found":
            # A consolidated file that failed to resolve to one pack isn't an error —
            # mark it as handled via per-part resolution so it's distinguishable.
            finfo["pack_status"] = "resolved_per_part"
            finfo["pack_gid"] = None
            finfo["pack_reference"] = "[consolidated — per-part]"
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
def _note_line(qty, pack_name):
    """Description line e.g. '2 off in Assembly Pack 1037.1014.M.Gate+Support.'"""
    q = (qty or "").strip()
    return f"{q} off in Assembly Pack {pack_name}"


def _already_has_pack_note(notes, pack_reference):
    return f"Assembly Pack {pack_reference}" in (notes or "")


def _asana_get(token, task_gid):
    import requests
    r = requests.get(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}",
        headers={"Authorization": f"Bearer {token}"},
        params={"opt_fields": "name,notes"}, timeout=30)
    if r.status_code != 200:
        return None
    return r.json().get("data", {})


def _asana_put(token, task_gid, payload):
    import requests
    r = requests.put(
        f"https://app.asana.com/api/1.0/tasks/{task_gid}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"data": payload}, timeout=30)
    return r.status_code in (200, 201), ("" if r.status_code in (200, 201) else r.text[:200])


def commit(stage: dict, db_path: str, pause: float = 0.35) -> dict:
    import db_writer
    import requests
    from asana_batch_updater import push_updates, get_asana_token

    actionable = [c for c in stage["candidates"] if c["action"] in ("migrate", "new")]
    if not actionable:
        return {"db_written": 0, "asana_ok": 0, "asana_fail": 0, "note": "nothing actionable"}

    token = get_asana_token()
    if not token:
        raise SystemExit("ERROR: ASANA_PERSONAL_ACCESS_TOKEN not found — cannot commit.")

    # 1) Build Packs reference field via the batch API (sets the pack GID on each task).
    updates = [{
        "task_gid": c["part_task_gid"],
        "task_name": c.get("part_name") or c["sesame_pn"],
        "custom_field_gid": BUILD_PACKS_REF_GID,
        "new_enum_gid": c["new_pack_gid"],     # value for the reference field = pack task GID
    } for c in actionable]
    ok, fail = push_updates(updates, token, dry_run=False)

    # 2) Description append + multi-pack marker. A part can appear on MORE THAN ONE
    #    pack (Giles 2026-08-17): append '{qty} off in Assembly Pack {pack}' per pack,
    #    and prepend '*' to the task name when the part maps to 2+ packs.
    #    Note: the reference field can only hold ONE pack GID; the DESCRIPTION holds
    #    the full multi-pack list. Dedup-safe (never re-appends an existing pack line).
    by_task: dict[str, list] = {}
    for c in actionable:
        by_task.setdefault(c["part_task_gid"], []).append(c)

    notes_ok = notes_fail = 0
    for task_gid, cands in by_task.items():
        task_data = _asana_get(token, task_gid)
        if task_data is None:
            notes_fail += 1
            continue
        current_notes = task_data.get("notes", "") or ""
        current_name = task_data.get("name", "")
        multi_pack = len(cands) > 1

        payload = {}
        # Description: append each missing pack's note line, joined by ' | '.
        missing = [c for c in cands
                   if not _already_has_pack_note(current_notes, c["pack_reference"])]
        if missing:
            note_lines = [_note_line(c.get("qty"), c.get("pack_reference") or c["new_pack_gid"])
                          for c in missing]
            combined = " | ".join(note_lines)
            separator = "\n---\n" if current_notes.strip() else ""
            payload["notes"] = current_notes + separator + combined

        # Multi-pack: prepend '*' to the task name (only once).
        if multi_pack and not current_name.startswith("*"):
            payload["name"] = "*" + current_name

        if payload:
            success, msg = _asana_put(token, task_gid, payload)
            if success:
                notes_ok += 1
            else:
                notes_fail += 1
        time.sleep(pause)

    # 3) DB mirror via the sole gateway (targeted UPDATE) + audit log.
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
            "asana_ok": ok, "asana_fail": fail,
            "notes_ok": notes_ok, "notes_fail": notes_fail}


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


_WSTRACE_DATA = _WORKSPACE / "skills" / "drawing_pipeline" / "data"


def _stage_out(stage_file: str | None, folder: Path) -> Path:
    """Resolve where a scan's review/stage JSON should be written.

    --stage-file (explicit) wins. Otherwise default to the workspace drawing_pipeline
    data dir (canonical audit archive for stage + review files), keyed by the source
    Legacy-BOM folder name so concurrent/future scans for different jobs don't collide.
    Never write the review JSON inside the (read-only / guardrailed) Dropbox source.
    """
    if stage_file:
        return Path(stage_file)
    return _WSTRACE_DATA / f"{folder.name}_pack_allocation_scan.json"


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
    # Audit/review JSON belongs in the workspace drawing_pipeline data dir (canonical
    # archive for stage + review files), NOT inside the source Legacy BOM folder (which
    # is Dropbox / read-only and never an audit target). A --stage-file explicitly given
    # wins; otherwise default to <workspace>/skills/drawing_pipeline/data/<folder>.json.
    out = _stage_out(args.stage_file, folder)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nStage file written: {out}")
        print("Review it, then: --commit --stage-file <that file>")
    except OSError as e:
        # Fall back to a path that is certain to be writable (never Guess-Dropbox).
        alt = Path.cwd() / f"{folder.name}_pack_allocation_scan.json"
        alt.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\n(stage dir not writable: {e}) Stage file written: {alt}")


if __name__ == "__main__":
    main()
