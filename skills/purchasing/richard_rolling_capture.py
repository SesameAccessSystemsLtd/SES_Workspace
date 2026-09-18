"""Richard rolling-60-day flagged-issue capture, fixed-benchmark report and weekly focus generator.
============================================================================
Corpus & scope
--------------
Pulls tasks that carry either the 'Purchasing Procedure Issue' tag
(gid 1163007497828518) or the 'Process Issue' tag (gid 1164351154905410) and are
*assigned to Richard* (gid 357614200440807) - the same corpus as the 2026-08-13
procedural performance assessment (318 -> 316 tasks today).

Only tasks whose Richard(tagged) task record carries the tags matter; we then pull
each task's stories/comments and **qualify an issue ONLY by a human-Giles comment
(created_by gid 1142108324926610) whose created_at falls INSIDE the rolling 60-day
window** (today - 60d). Recency is judged on the comment, NEVER on task.modified_at
(a mirror sync / cron re-touches it). The automated service account
"Giles's Service Account" (gid 1210751448247658) HTML `<body>…'Purchasing update'…`
boilerplate is EXCLUDED.

Efficiency guard: we only fetch stories for tagged-Richard tasks whose
modified_at is inside the window (a human-Giles comment post necessarily updates
the task's modified_at, so this is a lossless superset optimisation).

Classification maps each qualifying human comment onto the FIXED 9-category scheme
corresponding 1:1 to the 2026-08-13 benchmark table. Per the benchmark note, a mere
*kitting/completion on a standard part where completion IS the arrival evidence* is
NOT a defect, and delivery-chases / supplier-chases are NOT process failures here -
only genuine procedure/process flags are counted as issues (see BENIGN at bottom).

Read-only on Asana. The ONLY thing this module ever WRITES is a local REQUESTS
queue file (--monday path); it NEVER POSTs to Asana itself.

Flags
-----
  --scan                 live pull; print per-issue records + per-category counts,
                         write a CSV artefact under skills/purchasing/data/.
  --report               compare current 60-day per-category counts against the
                         embedded FIXED 2026-08-13 baseline; print delta + trend
                         (whole window vs hard baseline; no evidence filtering).
  --scan --report        do both in one run (report consumes this run's scan).
  --monday               generate the ONE weekly Monday focus-task as a REQUESTS
                         queue file (format execute_requests.py consumes). Only
                         fires on Mondays unless --force. Requires --scan/report.
  --force                with --monday, allow generation on non-Monday (proof only).
  --dry-run              never write the CSV / REQUESTS artefacts; print to stdout.
  --note  "<text>"       override the name appended after the fixed prefix.

Examples
--------
  python richard_rolling_capture.py --scan                 # live scan + counts
  python richard_rolling_capture.py --scan --report        # scan then baseline report
  python richard_rolling_capture.py --monday --dry-run     # show the REQUESTS file
  python richard_rolling_capture.py --monday               # stage REQUESTS into queue

No secret is ever printed/logged. Token is read from ~/.hermes/.env
(ASANA_PERSONAL_ACCESS_TOKEN), fall back ~/.openclaw/.env.
"""
import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

# --------------------------------------------------------------------------- #
# Fixed constants (GIDs - Asana keys are always 'gid')
# --------------------------------------------------------------------------- #
TAG_PURCHASING_PROCEDURE_ISSUE = "1163007497828518"
TAG_PROCESS_ISSUE = "1164351154905410"
HUMAN_GILES_GID = "1142108324926610"          # giles@sesameaccess.com
SERVICE_ACCOUNT_GID = "1210751448247658"      # "Giles's Service Account" boilerplate author
RICHARD_GID = "357614200440807"               # Richard Smalldridge

# Richard / Giles 1:1 project + Action items section (verified live 2026-09-04)
PROJECT_11_GID = "1204237507645072"
SECTION_ACTION_ITEMS_GID = "1204237507645083"

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
QUEUE_DIR = os.path.abspath(os.path.join(ROOT, "..", "..", "queues", "REQUESTS"))
BASE_URL = "https://app.asana.com/api/1.0"

# --------------------------------------------------------------------------- #
# FIXED BENCHMARK (embedded verbatim from the 2026-08-13 assessment,
# memory/richard-performance-assessment-2026-08-13.md - the 564 substantive
# procedural-issue record). This is the HARD baseline, never recomputed.
# order == rank in the original table.
# --------------------------------------------------------------------------- #
BENCHMARK_CATEGORIES = {
    "Delayed / overdue": 121,
    "Incomplete Asana admin": 89,
    "Stock status not updated": 88,
    "Missing/incorrect documentation": 68,
    "Wrong part/spec": 50,
    "QC check missed / not done": 49,
    "Order/invoice diligence failure": 40,
    "Communication/response gap": 30,
    "Stock/ordering error": 29,
}
BENCHMARK_TOTAL = sum(BENCHMARK_CATEGORIES.values())          # 564
# The assessment text says the 564 substantive flags concentrate in the last
# ~3 years of activity (2023-2026). We use that horizon only to derive a per-day
# run-rate from the cumulative baseline for a *comparable 60-day expectation* used
# for the trend read. It is stated explicitly, never silently scaled.
BASELINE_ACTIVE_YEARS = 3.0
BASELINE_ACTIVE_DAYS = int(BASELINE_ACTIVE_YEARS * 365.25)

# Order of display == benchmark order.
CATEGORY_ORDER = [
    "Delayed / overdue",
    "Incomplete Asana admin",
    "Stock status not updated",
    "Missing/incorrect documentation",
    "Wrong part/spec",
    "QC check missed / not done",
    "Order/invoice diligence failure",
    "Communication/response gap",
    "Stock/ordering error",
]

# --------------------------------------------------------------------------- #
# Heuristic keyword classifier -> each category. Human Giles wording observed in
# the real 60-day window (2026-07-06 -> 2026-09-04) guided the lists.
# --------------------------------------------------------------------------- #
def norm(s):
    """Lowercase + collapse whitespace for substring keyword matching."""
    return re.sub(r"\s+", " ", s.lower().strip())


def strip_links(s):
    """Remove URLs / Asana link-noise so it never distorts keyword matching."""
    return re.sub(r"https?://\S+|app\.asana\.com|get_asset|focus=true", " ", norm(s))


def has_prose(s, min_len=3):
    """True if a comment carries actual prose beyond bare links / whitespace /
    punctuation. A Giles 'comment' that is only an asana profile/assets URL or an
    empty/link-only reply is NOT issue evidence and should never be captured."""
    t = re.sub(r"[^a-z0-9]+", "", strip_links(s))      # keep only alnum
    return len(t) >= min_len

CATEGORY_KEYWORDS = {
    # 1
    "Delayed / overdue": [
        "overdue", "lateness", "delay", "delayed", "still waiting", "still not here",
        "hasn't turned up", "not turned up", "not chased", "still outstanding",
        "has been waiting", "backlog", "situation with", "where are they",
        "where is it", "not arrived",
    ],
    # 2
    "Incomplete Asana admin": [
        "not marked complete", "mark complete", "not complete", "sub task should be ",
        "sub should be completed", "subtasks not", "sub tasks not", "why are none",
        "not ticked", "not booked in", "not closed out", "still open", "fields blank",
        "not updated on asana", "completed premature", "closed too early",
        "nothing marked", "update when items come in", "when received", "fundamental to",
        "status not set", "not flagged in asana", "outstanding in",
    ],
    # 3
    "Stock status not updated": [
        "stock status", "stock not updated", "not updated when", "qty not reflected",
        "stock level", "haven't booked", "not booked out", "stock location",
        "not reflected", "stock has not", "taken from the stock shelf",
    ],
    # 4
    "Missing/incorrect documentation": [
        "no invoice", "missing invoice", "missing doc", "no paperwork", "no photo",
        "no delivery note", "no proof", "doc not attached", "no trace",
        "no record", "paperwork", "not attached",
    ],
    # 5
    "Wrong part/spec": [
        "wrong size", "wrong spec", "wrong part", "wrong type", "does not fit",
        "30mm not", "m12 vs", "wrong item", "they do not fit", "wrong dimension",
        "tight tolerance", "wrong material", "revised shaft",
    ],
    # 6
    "QC check missed / not done": [
        "pass qc", "qc check", "qc missed", "qc not done", "before qc", "no qc",
        "pass without qc", "qc sign", "signed off",
        "how did the ones in stock pass qc",
    ],
    # 7
    "Order/invoice diligence failure": [
        "processed the invoice", "invoice was processed", "processed withou", "without questioning",
        "against this po", "wrong po", "processed under", "processed this order under",
        "be diligent", "please be diligent", "invoice (incorrectly)", "diligence",
        "was not processed", "sat in drafts",
    ],
    # 8
    "Communication/response gap": [
        "didn't respond", "no reply", "not confirmed", "did you confirm",
        "failed to flag", "not flagged to me", "didn't tell", "no update",
        "you confirmed", "need a response",
    ],
    # 9
    "Stock/ordering error": [
        "wrong quantity", "wrong qty", "orderd 12 off", "ordered 12 off and",
        "only used 4", "12 off and have only used", "redundant qty", "redundant",
        "failed to reorder", "failed to order", "ordered the wrong number",
        "ordered the wrong qty", "not picked or flagged to order", "flagged for me to order",
        "picking task was not", "missed as being flagged", "over order", "over-order",
        "overshoot", "won't use", "dont need that many",
    ],
}

# "definitely benign / not a procedure defect" - these are ROUTINE/housekeeping so
# do not get classed as a Richard procedural defect. A standard parts completion
# where arrival IS the evidence, or a supplier chase / confirmation ask, is not a
# process failure (mirrors the benchmark's 'Other (benign)' 578 bucket).
BENIGN_MARKERS = [
    "confirmation", "confirm", "review", "please assess", "look at",
    "just confirming", "checking", "fyi", "thanks", "thank you", "acknowledged",
    "for awareness", "inv-", "order confirmation",
]


def classify(text):
    """Return a (category | 'Other-benign' | 'Unclassified') single best-effort
    label for ONE human-Giles issue comment.

    A category keyword hit wins over ANY benign/carry-on wording - Giles routinely
    attaches polite reinforcers ('please assess...', 'pls confirm...') to a genuine
    defect, so benign words must never downgrade a real catch. A text is treated as
    benign ONLY when no category matched at all AND a routine/carry-on marker is
    present (stock checks, confirmations, chase acks, FYIs) mirroring the benchmark's
    'Other (benign)' 578 bucket. Empty/bare-link notes default to Unclassified.
    """
    t = norm(text)
    if not has_prose(t):          # bare-link / empty / no prose -> no issue evidence
        return "Unclassified"
    # strip URLs/links so they never distort keyword matches
    t_no = strip_links(text)
    scores = {}
    for cat in CATEGORY_ORDER:
        # score = number of DISTINCT keywords matched (dedupes any residual dup
        # keywords and prevents one literal phrase inflating a category's weight)
        matched = [kw for kw in CATEGORY_KEYWORDS[cat]
                   if re.search(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", t_no)]
        if matched:
            scores[cat] = len(set(matched))
    if scores:
        # tie-break by earliest category in the fixed benchmark order
        return max(scores, key=lambda c: (scores[c], -CATEGORY_ORDER.index(c)))

    if any(b in t for b in BENIGN_MARKERS):
        return "Other-benign"
    return "Unclassified"


# --------------------------------------------------------------------------- #
# Asana client (read-only)
# --------------------------------------------------------------------------- #
class Asana:
    def __init__(self, token):
        self.h = {"Authorization": "Bearer " + token}
        self.sess = None

    def get(self, path, params):
        url = path if path.startswith("http") else BASE_URL + "/" + path.lstrip("/")
        r = self._http("get", url, params=params)
        return r

    def _http(self, method, url, params=None, json=None):
        import requests
        r = requests.request(method, url, headers=self.h, params=params,
                             json=json, timeout=25)
        if r.status_code >= 300:
            raise RuntimeError("HTTP %s %s: %s" % (r.status_code, url.split("?")[0], r.text[:200]))
        return r

    def paginate(self, path, params):
        out, p = [], dict(params)
        url = path
        while True:
            r = self.get(url, p)
            d = r.json()
            out.extend(d.get("data", []) or [])
            np_ = d.get("next_page") or {}
            if np_.get("offset"):
                url = BASE_URL + np_["path"]
                p = {}
            else:
                break
        return out


def load_token():
    import requests  # noqa - probe token presence cheaply not needed
    home = os.path.expanduser("~")
    for env_path in (os.path.join(home, ".hermes", ".env"),
                     os.path.join(home, ".openclaw", ".env")):
        try:
            with open(env_path, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.startswith("ASANA_PERSONAL_ACCESS_TOKEN"):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except FileNotFoundError:
            continue
    raise RuntimeError("ASANA_PERSONAL_ACCESS_TOKEN not found in ~/.hermes/.env or ~/.openclaw/.env")


# --------------------------------------------------------------------------- #
# CAPTURE  (rolling 60-day)
# --------------------------------------------------------------------------- #
def scan(api, days=60, quiet=False):
    """Return (issues, per_category_counts).
    issues: list of dicts {task_gid, task_name, project, comment text (trim),
             comment date, category}. Deduplicated per (task, comment date)."""
    wstart = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    if not quiet:
        print("# Rolling-60d window start:", wstart, "(today", datetime.now(timezone.utc).date().isoformat() + ")")

    # 1) all tagged tasks carrying either tag, assigned to Richard
    tasks = {}
    for tag in (TAG_PURCHASING_PROCEDURE_ISSUE, TAG_PROCESS_ISSUE):
        rows = api.paginate("/tags/%s/tasks" % tag,
                            {"limit": 100, "opt_fields": "name,modified_at,completed,assignee.gid"})
        for it in rows:
            a = (it.get("assignee") or {}).get("gid")
            if a and str(a) == RICHARD_GID:
                g = it.get("gid")
                if g not in tasks:
                    tasks[g] = {"name": it.get("name") or "", "modified_at": it.get("modified_at") or "",
                                "tags": []}
                tasks[g]["tags"].append(tag)
    if not quiet:
        print("# Richard-assigned tagged tasks:", len(tasks))

    # 2) prefilter to those touched in window (superset: comment post updates mod)
    candidates = [g for g, t in tasks.items() if (t.get("modified_at") or "")[:10] >= wstart]
    if not quiet:
        print("#   touched inside 60d (modified_at pre-filter):", len(candidates))

    # 3) pull stories per candidate; keep qualifying human-Giles comments
    issues = []
    seen = set()
    for g in candidates:
        st = api.paginate("/tasks/%s/stories" % g,
                          {"limit": 100, "opt_fields": "created_at,created_by.gid,created_by.name,type,text"})
        for s in st:
            if s.get("type") != "comment":
                continue
            cb = s.get("created_by") or {}
            gid = str(cb.get("gid") or "")
            created = (s.get("created_at") or "")[:10]
            txt = (s.get("text") or "").strip()
            if gid == SERVICE_ACCOUNT_GID:
                continue                      # service-account author excluded outright
            if gid != HUMAN_GILES_GID:
                continue                      # only human Giles flags qualify an issue
            if not txt or created < wstart:   # recency gate on the comment date
                continue
            low = txt.lower()
            if "<body>" in low or "purchasing update" in low:
                continue                      # defensive boilerplate strip even if gid wrong
            if not has_prose(txt):
                continue                      # bare-link / empty reply is NOT issue evidence
            key = (g, created)
            if key in seen:
                continue
            seen.add(key)
            cat = classify(txt)
            issues.append({"task_gid": g,
                           "task_name": (tasks[g].get("name") or "")[:80],
                           "project": "",                      # filled below
                           "comment_text": flatten(truncate(txt, 400)),
                           "comment_date": created,
                           "category": cat})

    # Resolve the real project(s) each qualifying task lives in (one light call per
    # distinct task - cheap because the qualifying set is small).
    proj_cache = {}
    for it in issues:
        g = it["task_gid"]
        if g not in proj_cache:
            try:
                t = api.get("/tasks/%s" % g, {"opt_fields": "memberships.project.name"})
                proj_cache[g] = ", ".join(
                    (m.get("project") or {}).get("name", "") for m
                    in (t.json().get("data", {}).get("memberships") or []))
            except RuntimeError:
                proj_cache[g] = ""
        it["project"] = proj_cache[g]

    counts = {c: 0 for c in CATEGORY_ORDER}
    for it in issues:
        if it["category"] in counts:
            counts[it["category"]] += 1
    if not quiet:
        print("# In-window human-Giles comments:", len(issues))
    return issues, counts


def truncate(s, n):
    return s[:n] + ("..." if len(s) > n else "")


def flatten(s):
    """Collapse every interior run of whitespace (incl. newlines) to a single
    space so each issue is ONE clean row in CSV / display, never a cell spanning
    multiple lines with stray blank rows."""
    return " ".join(str(s).split())


# --------------------------------------------------------------------------- #
# REPORT  (fixed-benchmark comparison)
# --------------------------------------------------------------------------- #
def report(issues, counts, quiet=False, out=sys.stdout):
    w = out.write
    total_cur = sum(counts.values())
    w("\n== Rolling-60d vs FIXED 2026-08-13 baseline ==\n")
    w("%-34s %8s %9s %10s %10s  %s\n" % ("Category", "60d", "baseline", "expected60", "delta", "direction"))
    w("-" * 92 + "\n")
    direction_summary = []
    for cat in CATEGORY_ORDER:
        cur = counts.get(cat, 0)
        base = BENCHMARK_CATEGORIES[cat]
        run_rate = base / float(BASELINE_ACTIVE_DAYS)
        exp60 = round(run_rate * 60, 1)
        delta = cur - exp60
        if delta > 0.4:
            direc = "WORSE"
        elif delta < -0.4:
            direc = "better"
        else:
            direc = "flat"
        direction_summary.append((cat, cur, base, exp60, delta, direc))
        w("%-34s %8d %9d %10.1f %+10.1f  %s\n"
          % (cat, cur, base, exp60, delta, direc))
    w("-" * 92 + "\n")
    w("%-34s %8d %9d %16s\n" % ("TOTAL", total_cur, BENCHMARK_TOTAL, ""))
    w("\nDirection is current-60d vs the run-rate the fixed baseline implies for a "
      "60-day slice\n(baseline count spread over %.0f years of substantively active "
      "flagging, ~%d days). Total-60d raw delta vs the all-time cumulative baseline "
      "is not\nan apples-to-apples number - direction is the useful signal.\n"
      % (BASELINE_ACTIVE_YEARS, BASELINE_ACTIVE_DAYS))
    return direction_summary, total_cur


# --------------------------------------------------------------------------- #
# MONDAY  (stage a focus-task REQUESTS file, DO NOT write Asana)
# --------------------------------------------------------------------------- #
MONDAY_NAME = "Weekly - Richard issues review & focus"


def is_monday(d):
    return d.weekday() == 0  # Mon = 0


def build_monday_notes(counts, direction_summary, total_cur, window_start, horizon):
    lines = []
    lines.append("Weekly benchmark comparison - Richard procedural-issue review")
    lines.append("== ==")
    lines.append("Fixed baseline: 2026-08-13 assessment (9 categories / %d flags)." % BENCHMARK_TOTAL)
    lines.append("Rolling 60-day window: %s -> %s (issues flagged by Giles in human comments)."
                 % (window_start, (datetime.now(timezone.utc).date() + timedelta(days=0)).isoformat()))
    lines.append("Total in-window issues classified: %d" % total_cur)
    lines.append("")
    lines.append("%-5s %-36s %6s %8s %8s" % ("", "Category", "60d", "bl", "Δvs exp"))
    for cat, cur, base, exp60, delta, direc in direction_summary:
        arrow = {"WORSE": "\u2191", "better": "\u2193", "flat": "\u2192"}[direc]
        lines.append("%-5s %-36s %6d %8d %6.1f %s"
                     % (arrow, cat[:36], cur, base, delta, direc))
    wors = [(c, d) for (c, _, _, _, d, dr) in direction_summary if dr == "WORSE"] or \
           [(c, d) for (c, _, _, _, d, dr) in direction_summary if dr in ("WORSE", "flat")]
    alt = "No category is running WORSE than the fixed benchmark run-rate." if not wors \
        else "Focus this week (running at/above the fixed benchmark run-rate): %s" % ", ".join(
            "%s (+%.1f)" % (c, d) for c, d in wors[:3])
    lines.append("")
    lines.append("Bottom line: %s" % alt)
    lines.append("")
    lines.append("Purpose: Richard/Giles 1:1 - assess flagged procedural issues reviewed in the")
    lines.append("rolling 60-day sweep; choose the top focus category to action this week.")
    return "\n".join(lines)


def generate_monday_request(token, counts, direction_summary, total_cur, days=60,
                            out_dir=None, force=False, dry=False, note=None):
    today = datetime.now(timezone.utc).date()
    if not is_monday(today) and not force:
        print("Not a Monday (%s). Use --force to stage a preview." % today.isoformat())
        return None
    wstart = (today - timedelta(days=days)).isoformat()
    notes = build_monday_notes(counts, direction_summary, total_cur, wstart, today.isoformat())
    name = MONDAY_NAME + ((" - " + note) if note else "")

    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    fname = "%s-%s.md" % (stamp, "weekly-richard-issues-review")

    # Body carries the create_task payload the executor understands. The companion
    # .json holds assignee + memberships(section) + due_on + notes body verbatim.
    md = ("---\n"
          "From: hermes\n"
          "To: asana_agent\n"
          "Type: asana_write\n"
          "Status: PENDING\n"
          "Priority: normal\n"
          "Created: %s\n"
          "---\n"
          "\n"
          "## Request\n"
          "Action: create_task\n"
          "Project GID: %s   (Richard/Giles 1:1)\n"
          "Task name: %s\n"
          "\n"
          "## Summary\n"
          "Create a single recurring Monday focus task named \"%s\" assigned to Richard\n"
          "(user gid %s) in project Richard / Giles 1:1 (Action items section, gid %s),\n"
          "due on %s. The notes carry the rolling-60d-vs-fixed-benchmark comparison and\n"
          "this week's focus category.\n"
          "\n"
          "## Guardrail assessment\n"
          "- Not the forbidden Project Status Overview GID (%s) - this is a normal\n"
          "  1:1 action item.\n"
          "- create (not delete) - safe.\n"
          "- Assignee Richard; NOTE: posting body carries NO sas-edge-case or other\n"
          "  content that belongs in the SES edge-case intake.\n"
          "\n"
          "## Context\n"
          "Weekly cadence (Mondays). Generation is by cron; execution stays gated on\n"
          "the REQUESTS processor + Giles approval.\n"
          % (now_iso, PROJECT_11_GID, name, name,
             RICHARD_GID, SECTION_ACTION_ITEMS_GID, today.isoformat(),
             "1192686469436889"))

    companion = {
        "task_name": name,
        "project_gid": PROJECT_11_GID,
        "memberships": [{"project": PROJECT_11_GID, "section": SECTION_ACTION_ITEMS_GID}],
        "assignee": RICHARD_GID,
        "due_on": today.isoformat(),
        "notes": notes,
        "action": "create_task",
    }

    if dry:
        print("== REQUESTS .md (DRY-RUN preview - not written to queue) ==\n")
        print(md)
        print("\n== companions.json (preview) ==\n")
        print(json.dumps(companion, indent=2))
        print("\n== notes body (preview, first 1600 chars) ==\n")
        print(notes[:1600])
        return None

    d = out_dir or QUEUE_DIR
    os.makedirs(d, exist_ok=True)
    md_path = os.path.join(d, fname)
    json_path = md_path[:-3] + ".json"
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(companion, fh, indent=2)
    print("WROTE REQUESTS .md:  %s" % md_path)
    print("WROTE companion json: %s" % json_path)
    return md_path


# --------------------------------------------------------------------------- #
def write_csv(issues, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["task_gid", "task_name", "project",
                                           "comment_text", "comment_date", "category"])
        w.writeheader()
        for it in issues:
            w.writerow(it)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Richard rolling-60d issue capture + benchmark report + Monday focus generator")
    ap.add_argument("--scan", action="store_true", help="live scan + classify")
    ap.add_argument("--report", action="store_true", help="compare vs fixed baseline")
    ap.add_argument("--monday", action="store_true", help="stage the weekly Monday focus REQUESTS file")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--dry-run", action="store_true", help="never write artefacts (CSV/REQUESTS); print")
    ap.add_argument("--force", action="store_true", help="allow --monday on a non-Monday")
    ap.add_argument("--note", default=None, help="optional suffix for the task name (default none)")
    a = ap.parse_args(argv)

    if not (a.scan or a.report or a.monday):
        ap.error("nothing to do - pass --scan, --report and/or --monday")

    token = load_token()
    api = Asana(token)

    scanset = None
    counts = {}
    if a.scan or a.report or a.monday:
        issues, counts = scan(api, days=a.days, quiet=False)
        scanset = issues
        # report uses counts from the scan above (no evidence filtering)

    if not a.dry_run and scanset and a.scan:
        os.makedirs(DATA_DIR, exist_ok=True)
        p = write_csv(scanset, os.path.join(DATA_DIR, "richard_60d_issues_%s.csv" %
                                            datetime.now().date().isoformat()))
        print("WROTE CSV:", p)
    elif scanset and a.scan and a.dry_run:
        print("\n(Dry-run) per-issue records:\n")
        for it in scanset:
            print("  %-10s %-10s %-34s %s" % (it["comment_date"], it["category"][:10],
                                               it["task_name"][:34], it["task_gid"]))

    direction_summary = total_cur = None
    if (a.report or a.monday) and counts:
        direction_summary, total_cur = report(scanset or [], counts)

    if a.monday:
        generate_monday_request(token, counts or {}, direction_summary or [],
                                total_cur or 0, days=a.days, force=a.force,
                                dry=a.dry_run, note=a.note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
