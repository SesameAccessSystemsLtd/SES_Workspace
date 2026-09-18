"""
improvement_engine.py — Spot the edge cases, assess them, stage an action, then
prove the action worked.

The loop
--------
    harvest   → every known signal source is scanned for friction
    cluster   → identical friction collapses onto one case by signature
    assess    → a case with enough weight is promoted for a decision
    stage     → a decided case records the action and its baseline metric
    validate  → the metric is re-measured; only a measured move closes a case

Nothing closes on assertion. A case moves to 'proven' only when its metric has
actually moved in the right direction and held. That is what makes this the same
machinery used to validate the amendments it proposes.

Design notes
------------
* Read-only against Asana. All writes go to ops.db.
* Every harvester is independent and failure-isolated — one broken source cannot
  stop the run.
* "Actual Cost" on a build pack is unplanned engineer hours, not money. It is the
  closest thing the business has to a direct friction meter, so it is the
  north-star metric here.

Usage:
    python improvement_engine.py --harvest              # scan and cluster
    python improvement_engine.py --snapshot             # capture metrics
    python improvement_engine.py --validate             # test staged actions
    python improvement_engine.py --report               # ranked friction register
    python improvement_engine.py --run                  # harvest + snapshot + validate
    python improvement_engine.py --stage 12 --action "..." --type automate \\
        --owner Purchasing --metric vendor_repair_events --target 0
"""

import argparse
import json
import re
import sqlite3
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from path_config import p as _p

try:  # console safety on Windows cp1252 terminals
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DB_PATH       = _p("db.ops")
REQUESTS_FAIL = Path(_p("queues.requests_failed"))
CRON_JOBS     = Path(r"C:\Users\GilesHugo\AppData\Local\hermes\cron\jobs.json")

# How much one occurrence of each category is worth. Weight reflects how much
# future work the category creates, not how annoying it is.
CATEGORY_WEIGHT = {
    "vendor_data_repair":     3.0,
    "vendor_name_collision":  2.5,
    "vendor_process_placeholder": 3.5,
    "readiness_conflict":     4.5,
    "vendor_field_misuse":    3.0,
    "vendor_missing":         2.0,
    "supplier_contact_gap":   1.5,
    "part_order_unlinked":    4.0,
    "pack_association_gap":   2.0,
    "lasercut_not_assessed":  4.0,
    "order_no_due_date":      2.5,
    "order_overdue_no_chase": 3.0,
    "ordering_queue_stale":   2.0,
    "pipeline_failure":       5.0,
    "request_unmatched":      2.5,
    "revision_churn":         3.5,
    "pack_blocked":           4.0,
}
DEFAULT_WEIGHT = 1.0

# Half-life in days for recency weighting of the friction score.
RECENCY_HALF_LIFE = 30.0

MAX_EXAMPLES = 12


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _norm_vendor(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


# ---------------------------------------------------------------------------
# Harvesters — each returns a list of observation dicts
# ---------------------------------------------------------------------------

def h_ordering_edge_cases(conn) -> list[dict]:
    """The edge cases the ordering scripts record. Empty until 2026-09-09 because
    write_ordering_edge_case raised on a missing bind parameter every time."""
    out = []
    for r in conn.execute("""
        SELECT id, run_timestamp, script, stage, asana_task_gid, task_name,
               vendor_name, issue_type, description
          FROM ordering_edge_cases
         WHERE COALESCE(resolved, 0) = 0
    """):
        out.append({
            "signature": f"ordering_edge:{r['issue_type']}:{r['script']}",
            "category": r["issue_type"] or "ordering_edge",
            "title": f"{r['issue_type']} in {r['script']}",
            "description": r["description"],
            "source": "ordering_edge_cases",
            "entity_gid": r["asana_task_gid"],
            "observed_at": r["run_timestamp"],
            "detail": r["task_name"],
        })
    return out


def h_vendor_data_repair(conn) -> list[dict]:
    """A vendor name changed on a part that was already in the system. That is a
    data-repair action someone had to perform by hand."""
    out = []
    for r in conn.execute("""
        SELECT entity_gid, project_code, old_value, new_value, observed_at
          FROM part_supply_events
         WHERE entity_type = 'part' AND field = 'vendor_name'
           AND old_value IS NOT NULL AND old_value <> ''
           AND observed_at >= datetime('now', '-90 day')
    """):
        same = _norm_vendor(r["old_value"]) == _norm_vendor(r["new_value"])
        out.append({
            "signature": ("vendor_repair:spelling" if same else
                          f"vendor_repair:reassign:{_norm_vendor(r['new_value'])[:24]}"),
            "category": "vendor_data_repair",
            "title": ("Vendor spelling corrected by hand" if same else
                      f"Part reassigned to vendor '{r['new_value']}'"),
            "description": f"{r['old_value']} → {r['new_value']}",
            "source": "part_supply_events",
            "entity_gid": r["entity_gid"],
            "project_code": r["project_code"],
            "observed_at": r["observed_at"],
            "detail": f"{r['old_value']} → {r['new_value']}",
        })
    return out


def _process_placeholders() -> set[str]:
    """Vendor values that name a PROCESS, not a company.

    skills/purchasing/vendor_normalise.csv maps these to the real suppliers that
    can perform them — "Laser Cut" resolves to eleven actual firms. They are not
    misspellings and must not be reported as such; the fix for a placeholder is
    supplier selection, not data cleaning.
    """
    path = Path(__file__).parent / "vendor_normalise.csv"
    out: set[str] = set()
    if not path.exists():
        return out
    try:
        import csv
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                src = (row.get("source_vendor") or "").strip()
                if src and not src.startswith("#"):
                    out.add(_norm_vendor(src))
    except Exception:
        pass
    return out


def h_vendor_process_placeholder(conn) -> list[dict]:
    """A live part whose vendor names a process, so no supplier is actually chosen."""
    placeholders = _process_placeholders()
    if not placeholders:
        return []
    out = []
    for (v, n) in conn.execute("""
        SELECT vendor_name, COUNT(*) FROM parts_control
         WHERE COALESCE(is_completed,0)=0 AND vendor_name IS NOT NULL AND vendor_name <> ''
         GROUP BY vendor_name
    """):
        if _norm_vendor(v) in placeholders:
            out.append({
                "signature": "vendor_process_placeholder", "mode": "state",
                "category": "vendor_process_placeholder",
                "title": "Live parts carry a process name instead of a supplier",
                "description": "No supplier is selected yet, so the part cannot be "
                               "chased, costed or lead-timed",
                "source": "vendor_normalise",
                "detail": f"{v} ({n} live part(s))",
            })
    return out


def h_readiness_conflict(conn) -> list[dict]:
    """A pack whose declared readiness contradicts its own parts."""
    try:
        rows = conn.execute("""
            SELECT pack_gid, pack_name, project_code, readiness_conflict
              FROM production_impact
             WHERE run_at = (SELECT MAX(run_at) FROM production_impact)
               AND readiness_conflict IS NOT NULL
        """).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{
        "signature": "readiness_conflict", "mode": "state",
        "category": "readiness_conflict",
        "title": "Pack readiness contradicts its own parts",
        "description": "Production is reading a state the parts do not support",
        "source": "production_impact",
        "entity_gid": r["pack_gid"],
        "project_code": r["project_code"],
        "detail": f"{r['pack_name']}: {r['readiness_conflict']}",
    } for r in rows]


def h_vendor_collisions(conn) -> list[dict]:
    """Two spellings of one vendor. Each pair is a permanent trap until fixed.

    Process placeholders are excluded — see h_vendor_process_placeholder.
    """
    placeholders = _process_placeholders()
    groups: dict[str, set] = {}
    for table, col in (("parts_control", "vendor_name"),
                       ("procurement_tasks", "vendor_name")):
        for (v,) in conn.execute(
            f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL AND {col} <> ''"
        ):
            groups.setdefault(_norm_vendor(v), set()).add(v)
    out = []
    for key, spellings in groups.items():
        if len(spellings) > 1 and key and key not in placeholders:
            # One case, many observations. A separate case per vendor would bury
            # everything else in the register at identical scores.
            out.append({
                "signature": "vendor_name_collision", "mode": "state",
                "category": "vendor_name_collision",
                "title": "One vendor recorded under several spellings",
                "description": "Each collision group is a permanent matching trap "
                               "for chase, RFQ and supplier performance",
                "source": "vendor_scan",
                "detail": " | ".join(sorted(spellings)),
            })
    return out


def h_vendor_field_misuse(conn) -> list[dict]:
    """The Vendor field holding something that is plainly not a vendor."""
    bad = re.compile(r"^\s*\d+\s*(off|packs?|x)\b|^\s*\d{3,4}\.", re.I)
    out = []
    for (v,) in conn.execute(
        "SELECT DISTINCT vendor_name FROM parts_control "
        "WHERE vendor_name IS NOT NULL AND vendor_name <> ''"
    ):
        if bad.search(v):
            out.append({
                "signature": "vendor_field_misuse", "mode": "state",
                "category": "vendor_field_misuse",
                "title": "Part description entered in the Vendor field",
                "description": "Vendor values that are quantities or part numbers",
                "source": "vendor_scan",
                "detail": v,
            })
    return out


def h_part_order_unlinked(conn) -> list[dict]:
    """Parts claiming to be on order with no order task behind the claim."""
    row = conn.execute("""
        SELECT COUNT(*) AS n FROM parts_control
         WHERE COALESCE(is_completed,0) = 0
           AND (procurement_task_gid IS NULL OR procurement_task_gid = '')
           AND LOWER(COALESCE(stock_action,'')) LIKE '%on order%'
    """).fetchone()
    if not row["n"]:
        return []
    return [{
        "signature": "part_order_unlinked", "mode": "state",
        "category": "part_order_unlinked",
        "title": "Parts marked on order with no order task linked",
        "description": f"{row['n']} live part(s) assert an order state with no order behind it",
        "source": "parts_control",
        "detail": f"count={row['n']}",
    }]


def h_pack_association_gap(conn) -> list[dict]:
    row = conn.execute("""
        SELECT COUNT(*) AS n FROM parts_control
         WHERE COALESCE(is_completed,0) = 0
           AND (build_assembly_pack_gid IS NULL OR build_assembly_pack_gid = '')
    """).fetchone()
    if not row["n"]:
        return []
    return [{
        "signature": "pack_association_gap", "mode": "state",
        "category": "pack_association_gap",
        "title": "Live parts not associated with a build pack",
        "description": f"{row['n']} live part(s) cannot be attributed to a pack",
        "source": "parts_control",
        "detail": f"count={row['n']}",
    }]


def h_lasercut_not_assessed(conn) -> list[dict]:
    out = []
    try:
        rows = conn.execute("""
            SELECT asana_task_gid, pack_name, project_code
              FROM build_assembly_packs
             WHERE COALESCE(is_completed,0) = 0
               AND (steel_profile IS NULL OR LOWER(steel_profile) LIKE 'not assessed%')
               AND project_code IS NOT NULL
        """).fetchall()
    except sqlite3.OperationalError:
        return []
    for r in rows:
        out.append({
            "signature": "lasercut_not_assessed", "mode": "state",
            "category": "lasercut_not_assessed",
            "title": "Build pack has no steel-profile assessment",
            "description": "Lasercut scope unknown, so impact cannot be forecast before delivery",
            "source": "build_assembly_packs",
            "entity_gid": r["asana_task_gid"],
            "project_code": r["project_code"],
            "detail": r["pack_name"],
        })
    return out


def h_order_no_due_date(conn) -> list[dict]:
    row = conn.execute("""
        SELECT COUNT(*) AS n FROM procurement_tasks
         WHERE COALESCE(is_completed,0) = 0
           AND (due_date IS NULL OR due_date = '')
           AND LOWER(COALESCE(order_status,'')) LIKE '%on order%'
    """).fetchone()
    if not row["n"]:
        return []
    return [{
        "signature": "order_no_due_date", "mode": "state",
        "category": "order_no_due_date",
        "title": "Open orders with no expected delivery date",
        "description": f"{row['n']} open order(s) cannot be chased or forecast against",
        "source": "procurement_tasks",
        "detail": f"count={row['n']}",
    }]


def h_chase_not_sent(conn) -> list[dict]:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM overdue_chase_log WHERE status = 'draft'"
    ).fetchone()
    if not row["n"]:
        return []
    return [{
        "signature": "chase_draft_unsent", "mode": "state",
        "category": "order_overdue_no_chase",
        "title": "Overdue chase drafts generated but never sent",
        "description": f"{row['n']} draft(s) waiting — the detection half works, the closing half does not",
        "source": "overdue_chase_log",
        "detail": f"count={row['n']}",
    }]


def h_ordering_queue_stale(conn) -> list[dict]:
    row = conn.execute("""
        SELECT COUNT(*) AS n FROM ordering_queue
         WHERE status = 'grouped'
           AND julianday('now') - julianday(grouped_at) > 30
    """).fetchone()
    if not row["n"]:
        return []
    return [{
        "signature": "ordering_queue_stale", "mode": "state",
        "category": "ordering_queue_stale",
        "title": "Ordering queue lines grouped but never ordered",
        "description": f"{row['n']} line(s) older than 30 days still sitting in 'grouped'",
        "source": "ordering_queue",
        "detail": f"count={row['n']}",
    }]


def h_supplier_contact_gap(conn) -> list[dict]:
    row = conn.execute("""
        SELECT COUNT(*) AS n FROM (
            SELECT DISTINCT p.vendor_name
              FROM procurement_tasks p
             WHERE COALESCE(p.is_completed,0) = 0
               AND p.vendor_name IS NOT NULL AND p.vendor_name <> ''
               AND NOT EXISTS (
                   SELECT 1 FROM supplier_contacts sc
                    WHERE LOWER(sc.supplier_name) = LOWER(p.vendor_name)
                      AND sc.email IS NOT NULL AND sc.email <> ''
               )
        )
    """).fetchone()
    if not row["n"]:
        return []
    return [{
        "signature": "supplier_contact_gap_active", "mode": "state",
        "category": "supplier_contact_gap",
        "title": "Vendors on live orders with no email on file",
        "description": f"{row['n']} vendor(s) currently carrying open orders cannot be chased automatically",
        "source": "supplier_contacts",
        "detail": f"count={row['n']}",
    }]


def h_pipeline_failures(conn) -> list[dict]:
    """A scheduled job that ends in error is friction that repeats every night."""
    if not CRON_JOBS.exists():
        return []
    try:
        data = json.loads(CRON_JOBS.read_text(encoding="utf-8"))
    except Exception:
        return []
    jobs = data if isinstance(data, list) else data.get("jobs", data)
    if isinstance(jobs, dict):
        jobs = list(jobs.values())
    out = []
    for j in jobs:
        if j.get("last_status") == "error":
            err = str(j.get("last_error") or "")[:400]
            out.append({
                "signature": f"cron_failure:{j.get('id')}", "mode": "state",
                "category": "pipeline_failure",
                "title": f"Scheduled job failing: {j.get('name')}",
                "description": err,
                "source": "hermes_cron",
                "observed_at": j.get("last_run_at"),
                "detail": err,
            })
    return out


def h_unmatched_requests(conn) -> list[dict]:
    if not REQUESTS_FAIL.exists():
        return []
    out = []
    for f in sorted(REQUESTS_FAIL.glob("*.md")):
        try:
            head = f.read_text(encoding="utf-8", errors="replace")[:600]
        except Exception:
            continue
        reason = "unknown"
        if "not matched" in head.lower() or "NOT_MATCHED" in head:
            reason = "task_not_matched"
        out.append({
            "signature": f"request_failed:{reason}", "mode": "state",
            "category": "request_unmatched",
            "title": "Email approval could not be matched to a task",
            "description": "Approved action could not be executed and was left in REQUESTS_FAILED",
            "source": "requests_failed",
            "observed_at": f.name[:19].replace("T", " ").replace("-", ":")[:10],
            "detail": f.name,
        })
    return out


def h_revision_churn(conn) -> list[dict]:
    """A part revision changing after the part was already on order means money and
    time already committed against a superseded drawing."""
    out = []
    try:
        rows = conn.execute("""
            SELECT e.entity_gid, e.project_code, e.old_value, e.new_value, e.observed_at
              FROM part_supply_events e
              JOIN parts_control p ON p.asana_task_gid = e.entity_gid
             WHERE e.field = 'rev_number'
               AND e.old_value IS NOT NULL AND e.old_value <> ''
               AND p.procurement_task_gid IS NOT NULL
        """).fetchall()
    except sqlite3.OperationalError:
        return []
    for r in rows:
        out.append({
            "signature": "revision_after_order",
            "category": "revision_churn",
            "title": "Drawing revision changed after the part was ordered",
            "description": f"rev {r['old_value']} → {r['new_value']}",
            "source": "part_supply_events",
            "entity_gid": r["entity_gid"],
            "project_code": r["project_code"],
            "observed_at": r["observed_at"],
            "detail": f"{r['old_value']} → {r['new_value']}",
        })
    return out


def h_blocked_packs(conn) -> list[dict]:
    try:
        rows = conn.execute("""
            SELECT pack_gid, pack_name, project_code, risk_reason
              FROM production_impact
             WHERE run_at = (SELECT MAX(run_at) FROM production_impact)
               AND risk_band = 'blocked'
        """).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{
        "signature": "pack_blocked", "mode": "state",
        "category": "pack_blocked",
        "title": "Build pack blocked by outstanding parts",
        "description": r["risk_reason"],
        "source": "production_impact",
        "entity_gid": r["pack_gid"],
        "project_code": r["project_code"],
        "detail": r["pack_name"],
    } for r in rows]


HARVESTERS = [
    h_ordering_edge_cases, h_vendor_data_repair, h_vendor_collisions,
    h_vendor_process_placeholder, h_readiness_conflict,
    h_vendor_field_misuse, h_part_order_unlinked, h_pack_association_gap,
    h_lasercut_not_assessed, h_order_no_due_date, h_chase_not_sent,
    h_ordering_queue_stale, h_supplier_contact_gap, h_pipeline_failures,
    h_unmatched_requests, h_revision_churn, h_blocked_packs,
]


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

def friction_score(occurrences: int, category: str, last_seen: str) -> float:
    weight = CATEGORY_WEIGHT.get(category, DEFAULT_WEIGHT)
    age_days = 0.0
    dt = None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(str(last_seen)[:19], fmt).replace(tzinfo=timezone.utc)
            break
        except (ValueError, TypeError):
            continue
    if dt:
        age_days = max(0.0, (datetime.now(timezone.utc) - dt).days)
    recency = 0.5 ** (age_days / RECENCY_HALF_LIFE)
    return round(occurrences * weight * (0.25 + 0.75 * recency), 2)


def upsert_case(conn, obs: dict, run_count: int = 1) -> int:
    """Record one observation against its case.

    run_count is how many times this signature was seen in THIS run. A harvester
    that reports standing state (a backlog of 43 unassessed packs) sets
    mode='state' and its occurrences become that current count — otherwise the
    same backlog would inflate the score a little more every night. A harvester
    that reports discrete events (a vendor was renamed) leaves mode unset and its
    occurrences accumulate, which is the correct behaviour for events.
    """
    now = _now()
    seen = obs.get("observed_at") or now
    sig = obs["signature"]
    row = conn.execute(
        "SELECT id, occurrences, sources, example_gids, state FROM improvement_cases "
        "WHERE signature = ?", (sig,)
    ).fetchone()

    if row:
        occurrences = (run_count if obs.get("mode") == "state"
                       else row["occurrences"] + 1)
        sources = set(json.loads(row["sources"] or "[]")) | {obs["source"]}
        examples = json.loads(row["example_gids"] or "[]")
        if obs.get("entity_gid") and obs["entity_gid"] not in examples:
            examples = (examples + [obs["entity_gid"]])[:MAX_EXAMPLES]
        state = "new" if row["state"] == "dormant" else row["state"]
        conn.execute("""
            UPDATE improvement_cases
               SET occurrences = ?, last_seen_at = ?, sources = ?, example_gids = ?,
                   friction_score = ?, state = ?, updated_at = ?
             WHERE id = ?
        """, (occurrences, seen, json.dumps(sorted(sources)), json.dumps(examples),
              friction_score(occurrences, obs["category"], seen), state, now, row["id"]))
        case_id = row["id"]
    else:
        cur = conn.execute("""
            INSERT INTO improvement_cases
                (signature, category, title, description, first_seen_at, last_seen_at,
                 occurrences, sources, example_gids, friction_score, state)
            VALUES (?,?,?,?,?,?,1,?,?,?, 'new')
        """, (sig, obs["category"], obs["title"], obs.get("description"), seen, seen,
              json.dumps([obs["source"]]),
              json.dumps([obs["entity_gid"]] if obs.get("entity_gid") else []),
              friction_score(1, obs["category"], seen)))
        case_id = cur.lastrowid

    conn.execute("""
        INSERT INTO improvement_observations
            (case_id, observed_at, source, entity_gid, project_code, detail, raw)
        VALUES (?,?,?,?,?,?,?)
    """, (case_id, seen, obs["source"], obs.get("entity_gid"),
          obs.get("project_code"), obs.get("detail"), None))
    return case_id


def harvest(conn, verbose: bool = True) -> dict:
    # Each run replaces that run's observation set for a case, so occurrences
    # counts distinct sightings rather than re-counting the same open backlog.
    conn.execute("DELETE FROM improvement_observations WHERE observed_at >= ?",
                 (datetime.now(timezone.utc).strftime("%Y-%m-%d"),))
    stats = {}
    for fn in HARVESTERS:
        name = fn.__name__.removeprefix("h_")
        try:
            obs = fn(conn)
        except Exception:
            if verbose:
                print(f"  {name}: FAILED\n{traceback.format_exc(limit=2)}")
            stats[name] = "error"
            continue
        per_sig: dict[str, int] = {}
        for o in obs:
            per_sig[o["signature"]] = per_sig.get(o["signature"], 0) + 1
        for o in obs:
            upsert_case(conn, o, run_count=per_sig[o["signature"]])
        stats[name] = len(obs)
        if verbose:
            print(f"  {name}: {len(obs)}")
    conn.commit()
    return stats


# ---------------------------------------------------------------------------
# Metrics — the validation spine
# ---------------------------------------------------------------------------

METRICS: dict[str, str] = {
    # north star: unplanned engineer hours logged against build packs
    "unplanned_hours_90d": """
        SELECT COALESCE(SUM(unplanned_hours), 0) FROM build_assembly_packs
         WHERE unplanned_hours IS NOT NULL
    """,
    "vendor_repair_events_30d": """
        SELECT COUNT(*) FROM part_supply_events
         WHERE field = 'vendor_name' AND old_value IS NOT NULL AND old_value <> ''
           AND observed_at >= datetime('now','-30 day')
    """,
    "parts_unlinked": """
        SELECT COUNT(*) FROM parts_control
         WHERE COALESCE(is_completed,0)=0
           AND (procurement_task_gid IS NULL OR procurement_task_gid='')
    """,
    "parts_unassociated": """
        SELECT COUNT(*) FROM parts_control
         WHERE COALESCE(is_completed,0)=0
           AND (build_assembly_pack_gid IS NULL OR build_assembly_pack_gid='')
    """,
    "packs_lasercut_unassessed": """
        SELECT COUNT(*) FROM build_assembly_packs
         WHERE COALESCE(is_completed,0)=0
           AND (steel_profile IS NULL OR LOWER(steel_profile) LIKE 'not assessed%')
    """,
    "open_orders_no_due_date": """
        SELECT COUNT(*) FROM procurement_tasks
         WHERE COALESCE(is_completed,0)=0
           AND (due_date IS NULL OR due_date='')
           AND LOWER(COALESCE(order_status,'')) LIKE '%on order%'
    """,
    "chase_drafts_unsent": "SELECT COUNT(*) FROM overdue_chase_log WHERE status='draft'",
    "readiness_conflicts": """
        SELECT COUNT(*) FROM production_impact
         WHERE run_at=(SELECT MAX(run_at) FROM production_impact)
           AND readiness_conflict IS NOT NULL
    """,
    "packs_schedulable_undated": """
        SELECT COUNT(*) FROM production_impact
         WHERE run_at=(SELECT MAX(run_at) FROM production_impact)
           AND schedulable=1 AND production_due_on IS NULL
    """,
    "ordering_queue_stale": """
        SELECT COUNT(*) FROM ordering_queue
         WHERE status='grouped' AND julianday('now')-julianday(grouped_at) > 30
    """,
    "packs_at_risk": """
        SELECT COUNT(*) FROM production_impact
         WHERE run_at=(SELECT MAX(run_at) FROM production_impact)
           AND risk_band IN ('at_risk','blocked')
    """,
    "packs_forecast_only": """
        SELECT COUNT(*) FROM production_impact
         WHERE run_at=(SELECT MAX(run_at) FROM production_impact)
           AND due_date_source LIKE 'forecast%'
    """,
    "edge_cases_open": """
        SELECT COUNT(*) FROM improvement_cases
         WHERE state IN ('new','assessing','staged','validating')
    """,
    "pipeline_failures": """
        SELECT COUNT(*) FROM improvement_cases
         WHERE category='pipeline_failure' AND state IN ('new','assessing','staged')
    """,
}


def snapshot(conn, source: str = "improvement_engine") -> int:
    ts = _now()
    n = 0
    for key, sql in METRICS.items():
        try:
            value = conn.execute(sql).fetchone()[0]
        except sqlite3.OperationalError:
            continue
        conn.execute("""
            INSERT INTO metric_snapshots (metric_key, captured_at, value, unit, source)
            VALUES (?,?,?,?,?)
        """, (key, ts, float(value or 0), "count", source))
        n += 1
    conn.commit()
    return n


def latest_metric(conn, key: str) -> float | None:
    row = conn.execute(
        "SELECT value FROM metric_snapshots WHERE metric_key=? "
        "ORDER BY captured_at DESC LIMIT 1", (key,)
    ).fetchone()
    return row["value"] if row else None


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

def stage(conn, case_id: int, action: str, action_type: str, owner: str,
          metric_key: str, target: float | None) -> None:
    """Record the decision, capture the baseline, and start validating."""
    baseline = latest_metric(conn, metric_key)
    if baseline is None:
        snapshot(conn)
        baseline = latest_metric(conn, metric_key)
    conn.execute("""
        UPDATE improvement_cases
           SET state='validating', proposed_action=?, action_type=?, owner=?,
               metric_key=?, baseline_value=?, baseline_at=?, target_value=?,
               staged_at=?, updated_at=?
         WHERE id=?
    """, (action, action_type, owner, metric_key, baseline, _now(), target,
          _now(), _now(), case_id))
    conn.commit()
    print(f"Case {case_id} staged. Metric '{metric_key}' baseline = {baseline}, target = {target}")


def validate(conn, hold_days: int = 14) -> list[str]:
    """A staged action becomes 'proven' only when its metric has moved the right
    way and stayed there for hold_days. Otherwise it stays in validating, or is
    flagged as not working."""
    notes = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=hold_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for c in conn.execute("""
        SELECT id, title, metric_key, baseline_value, target_value, staged_at
          FROM improvement_cases WHERE state='validating' AND metric_key IS NOT NULL
    """).fetchall():
        current = latest_metric(conn, c["metric_key"])
        if current is None:
            continue
        baseline = c["baseline_value"] or 0.0
        target = c["target_value"] if c["target_value"] is not None else 0.0
        improved = current <= target if target <= baseline else current >= target

        # Has it held? Check the worst reading since staging.
        readings = [r["value"] for r in conn.execute("""
            SELECT value FROM metric_snapshots
             WHERE metric_key=? AND captured_at >= ?
        """, (c["metric_key"], c["staged_at"])).fetchall()]
        held = bool(readings) and (max(readings) <= target if target <= baseline
                                   else min(readings) >= target)
        long_enough = c["staged_at"] <= cutoff

        if improved and held and long_enough:
            conn.execute("""
                UPDATE improvement_cases
                   SET state='proven', validated_at=?, updated_at=?,
                       validation_note=?
                 WHERE id=?
            """, (_now(), _now(),
                  f"{c['metric_key']}: {baseline} → {current} (target {target}), "
                  f"held {hold_days}d across {len(readings)} readings", c["id"]))
            notes.append(f"PROVEN  #{c['id']} {c['title']} — {baseline} → {current}")
        elif long_enough and not improved:
            conn.execute("""
                UPDATE improvement_cases
                   SET validation_note=?, updated_at=?
                 WHERE id=?
            """, (f"NOT WORKING after {hold_days}d — {c['metric_key']}: "
                  f"{baseline} → {current}, target {target}", _now(), c["id"]))
            notes.append(f"STALLED #{c['id']} {c['title']} — {baseline} → {current}, target {target}")
        else:
            notes.append(f"WATCH   #{c['id']} {c['title']} — {baseline} → {current}")
    conn.commit()
    return notes


def report(conn, limit: int = 30) -> None:
    print(f"\n{'ID':>4}  {'SCORE':>7}  {'N':>4}  {'STATE':11} {'CATEGORY':22} TITLE")
    print("-" * 118)
    for r in conn.execute("""
        SELECT id, friction_score, occurrences, state, category, title, last_seen_at
          FROM improvement_cases
         WHERE state NOT IN ('proven','rejected','dormant')
         ORDER BY friction_score DESC LIMIT ?
    """, (limit,)):
        print(f"{r['id']:>4}  {r['friction_score']:>7.2f}  {r['occurrences']:>4}  "
              f"{r['state']:11} {r['category'][:22]:22} {r['title'][:52]}")

    proven = conn.execute(
        "SELECT id, title, validation_note FROM improvement_cases WHERE state='proven'"
    ).fetchall()
    if proven:
        print("\nProven — measured, not asserted:")
        for r in proven:
            print(f"  #{r['id']} {r['title']}\n      {r['validation_note']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Edge case → assessment → action → proof")
    ap.add_argument("--db-path", default=DB_PATH)
    ap.add_argument("--harvest", action="store_true")
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--run", action="store_true", help="harvest + snapshot + validate + report")
    ap.add_argument("--stage", type=int, metavar="CASE_ID")
    ap.add_argument("--action")
    ap.add_argument("--type", dest="action_type",
                    choices=["eliminate", "simplify", "automate", "delegate", "retain"])
    ap.add_argument("--owner")
    ap.add_argument("--metric")
    ap.add_argument("--target", type=float)
    ap.add_argument("--hold-days", type=int, default=14)
    args = ap.parse_args()

    conn = _connect(args.db_path)

    if args.stage:
        if not (args.action and args.action_type and args.metric):
            sys.exit("--stage requires --action, --type and --metric")
        stage(conn, args.stage, args.action, args.action_type,
              args.owner or "Purchasing", args.metric, args.target)
        conn.close()
        return

    if args.harvest or args.run:
        print("Harvesting:")
        harvest(conn)
    if args.snapshot or args.run:
        print(f"Captured {snapshot(conn)} metric snapshot(s)")
    if args.validate or args.run:
        for line in validate(conn, args.hold_days):
            print("  " + line)
    if args.report or args.run or not any(
        [args.harvest, args.snapshot, args.validate, args.stage]
    ):
        report(conn)

    conn.close()


if __name__ == "__main__":
    main()
