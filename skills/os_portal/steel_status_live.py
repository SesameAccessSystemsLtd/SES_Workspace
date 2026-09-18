#!/usr/bin/env python3
"""steel_status_live.py — resolve Build Schedule STEEL PROFILE status against LIVE Asana.

WHY THIS EXISTS (Giles, 2026-09-12)
  The ops.db `build_assembly_packs.steel_profile` mirror is KNOWN-STALE for this
  field (flagged in steel_profile_outstanding.py, verified 2026-09-07). Measured
  2026-09-12, the divergence was severe in both directions:

    | STEEL PROFILE        | live Asana (incomplete) | ops.db mirror |
    |----------------------|-------------------------|---------------|
    | Not needed           | 80                      | 103           |
    | All Cut              | 49                      | 64            |
    | blank                | 45                      | (unusable)    |
    | All in ready to cut  | 16                      | 2             |
    | Not Assessed         | 12                      | 68            |

  A dashboard built on the mirror therefore reported the wrong requirement --
  it showed 69 "blank status" packs carrying 693 m when in truth the live field
  is well-populated. Against live Asana only 7 packs genuinely need material.

  So: ALWAYS resolve the steel status from live Asana. ops.db is a fallback
  only, and the page says so when it is used.

WHAT IT RETURNS
  { pack_key(UPPER short ref) -> {"status", "completed", "gid", "name"} }

  Only INCOMPLETE tasks are returned as statuses the dashboard counts; completed
  packs are surfaced separately (a completed pack is done, not outstanding).

MATCHING (same rule as the builder)
  Build Schedule task names are the LONG pack ref ('1588.1004.B.MainStairFrame.')
  while steel_parts.pack_ref is SHORT ('1588.1004.B'). The key is the first
  three dot-segments, upper-cased. 'DRAWING' rows are drawing-control tasks,
  not build packs, and are excluded.

CACHING
  A successful pull is cached to data/os_status/.steel_status_cache.json with a
  TTL (default 15 min) so repeated dashboard builds do not hammer the API. A
  FAILED pull falls back to a stale cache if present rather than silently
  degrading to the known-bad mirror -- and reports that it did.
"""
import os, json, time, sqlite3
from datetime import datetime, timezone

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

WS = os.path.expanduser("~/SES_Workspace")
CACHE = os.path.join(WS, "data", "os_status", ".steel_status_cache.json")
PROJECT_BUILD_SCHEDULE = "852956628490855"   # 3.1 Build Schedule
FIELD_STEEL = "1142754335683355"
TTL_SECONDS = 15 * 60


def _token():
    for ep in (os.path.expanduser("~/.hermes/.env"), os.path.expanduser("~/.openclaw/.env")):
        try:
            for line in open(ep):
                if line.startswith("ASANA_PERSONAL_ACCESS_TOKEN"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except FileNotFoundError:
            continue
    return os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN", "")


def pack_key(ref):
    parts = [p for p in (ref or "").strip().rstrip(".").split(".") if p]
    return ".".join(parts[:3]).upper() if len(parts) >= 3 else None


def _fetch_live():
    """Pull every Build Schedule task and reduce to pack_key -> status."""
    if requests is None:
        raise RuntimeError("requests not available")
    tok = _token()
    if not tok:
        raise RuntimeError("no ASANA_PERSONAL_ACCESS_TOKEN")
    H = {"Authorization": f"Bearer {tok}"}
    tasks, off = [], None
    while True:
        params = {
            "opt_fields": "name,gid,completed,custom_fields.gid,custom_fields.display_value",
            "limit": 100,
        }
        if off:
            params["offset"] = off
        r = requests.get(f"https://app.asana.com/api/1.0/projects/{PROJECT_BUILD_SCHEDULE}/tasks",
                         headers=H, params=params, timeout=45)
        r.raise_for_status()
        j = r.json()
        tasks += j.get("data", [])
        off = (j.get("next_page") or {}).get("offset")
        if not off:
            break
        time.sleep(0.1)

    out = {}
    for t in tasks:
        name = (t.get("name") or "").strip()
        if "DRAWING" in name.upper():
            continue
        k = pack_key(name)
        if not k:
            continue
        status = ""
        for cf in t.get("custom_fields", []):
            if cf.get("gid") == FIELD_STEEL:
                status = cf.get("display_value") or ""
                break
        prev = out.get(k)
        # A pack appears as both a drawing-ish and a build row; prefer the row
        # that actually carries a steel status, and the incomplete one.
        if prev is None or ((not (prev["status"] or "").strip()) and status.strip()):
            out[k] = {"status": status, "completed": bool(t.get("completed")),
                      "gid": t.get("gid"), "name": name}
    return out


def load_live(force=False):
    """Return (map, meta). map is pack_key -> {status, completed, gid, name}.

    meta describes where the data came from so the page can be honest about it:
      {"source": "live"|"cache"|"stale-cache"|"unavailable",
       "fetched_at": iso, "age_s": n, "error": str|None}
    """
    now = time.time()
    cached = None
    if os.path.exists(CACHE):
        try:
            cached = json.load(open(CACHE, encoding="utf-8"))
        except Exception:
            cached = None

    if not force and cached:
        age = now - cached.get("_fetched_ts", 0)
        if age < TTL_SECONDS:
            return cached.get("data", {}), {
                "source": "cache", "fetched_at": cached.get("_fetched_at"),
                "age_s": int(age), "error": None,
            }

    try:
        data = _fetch_live()
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump({"_fetched_ts": now,
                   "_fetched_at": datetime.now(timezone.utc).isoformat(),
                   "data": data}, open(CACHE, "w", encoding="utf-8"))
        return data, {"source": "live",
                      "fetched_at": datetime.now(timezone.utc).isoformat(),
                      "age_s": 0, "error": None}
    except Exception as e:
        if cached:
            return cached.get("data", {}), {
                "source": "stale-cache", "fetched_at": cached.get("_fetched_at"),
                "age_s": int(now - cached.get("_fetched_ts", 0)), "error": str(e),
            }
        return {}, {"source": "unavailable", "fetched_at": None,
                    "age_s": None, "error": str(e)}


if __name__ == "__main__":
    import sys
    m, meta = load_live(force="--force" in sys.argv)
    print(f"source={meta['source']} age={meta['age_s']}s error={meta['error']}")
    print(f"{len(m)} packs resolved")
    from collections import Counter
    c = Counter()
    for k, v in m.items():
        if v["completed"]:
            c["(pack completed)"] += 1
        else:
            c[(v["status"] or "").strip() or "(blank)"] += 1
    for k, v in c.most_common():
        print(f"   {v:5}  {k!r}")
