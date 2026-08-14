"""
bom_push.py — Hermes-triggered wrapper for the traditional BOM import pipeline.
================================================================================
Pushes a "Karol Generate Entire BOM" control task through the SAME route as every
other BOM import (run_mrp_pipeline.py, Stages 1-5), with the correct source
control, assignment and target project baked in from a per-job manifest.

It is a THIN wrapper — it does not re-implement any pipeline logic. It just:
  1. Loads a manifest (jobs/<job>.json) with the resolved, validated inputs.
  2. Calls run_mrp_pipeline.py with those inputs, embedding:
       --dropbox-url   (the Dropbox BOM folder  = source of the parts)
       --asana-url     (the control task        = source control / audit link)
  3. Defaults to --stop-after 4 (the HITL staging gate — NO Asana writes).
  4. On --commit (only after Giles approves the Stage 4 Excel) runs Stage 5,
     then assigns the ALL BOMS root task and adds it to the correct project(s).

HITL is preserved exactly: this script NEVER commits unless --commit is passed,
and commit reuses the Stage 4 file the human reviewed.

Usage (run by Hermes / Telegram command, or by hand):
    python bom_push.py --job 1365_serjeants_inn                 # preflight + Stage 4
    python bom_push.py --job 1365_serjeants_inn --preflight     # checks only
    python bom_push.py --job 1365_serjeants_inn --commit        # after approval
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent
JOBS_DIR = BASE / "jobs"
RUNNER = BASE / "run_mrp_pipeline.py"
STATE_FILE = BASE / "pipeline_last_run.json"
DEFAULT_BUILD_ORDERS = "848657415032331"   # standard Stage 5 target
TG_CHAT = "8551261362"                       # Giles


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_manifest(job: str) -> dict:
    path = JOBS_DIR / f"{job}.json"
    if not path.exists():
        sys.exit(f"ERROR: manifest not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9]+", "_", (s or "").strip()).strip("_").lower() or "bom"


def _manifest_from_payload(payload_path: str) -> dict:
    """Build + persist a job manifest from a tracker dispatch payload, so a click
    can push a BOM with no hand-written jobs/<job>.json. parts_folder may be blank
    — bom_push resolves it from dropbox_url via the standalone Dropbox API at run."""
    with open(payload_path, encoding="utf-8") as f:
        pl = json.load(f)
    control = pl.get("control_name") or pl.get("control") or str(pl.get("job") or "bom")
    project_name = (pl.get("project_name")
                    or control.replace("Karol Generate Entire BOM - ", "").strip()
                    or control)
    asana_url = pl.get("asana_url") or (
        f"https://app.asana.com/0/0/{pl['control_gid']}" if pl.get("control_gid") else "")
    m = {
        "job_id": _slug(project_name) or _slug(control),
        "project_name": project_name,
        "control_task_gid": pl.get("control_gid"),
        "asana_url": asana_url,
        "dropbox_url": pl.get("dropbox_url"),
        "parts_folder": pl.get("parts_folder"),   # bridge injects it; else resolved at run
        "target_project_gid": "848657415032331",
        "job_project_gid": None,
        "assignee_gid": pl.get("assignee_gid", "1142108324926610"),
        "excluded_cost_centres": ["Steel Profile"],
        "notes": f"Auto-built from tracker dispatch payload {Path(payload_path).name}.",
    }
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    (JOBS_DIR / f"{m['job_id']}.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    return m


def _load_token() -> str | None:
    tok = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN") or os.environ.get("ASANA_PAT")
    if not tok:
        cfg = BASE / "ASANAconfig.json"
        if cfg.exists():
            data = json.load(open(cfg, encoding="utf-8"))
            tok = data.get("ASANA_PERSONAL_ACCESS_TOKEN") or data.get("ASANA_PAT")
    return tok


def _tg(text: str) -> None:
    """Notify the Purchasing Telegram TOPIC (t.me/c/3780215894/2). Never raises."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "purchasing"))
        from notify_telegram import send_purchasing_topic
        send_purchasing_topic(text)
    except Exception as e:
        print(f"[warn] telegram notify skipped: {e}", file=sys.stderr)


def _resolve_parts_folder(dropbox_url: str | None) -> Path | None:
    """Resolve a Dropbox folder URL -> local synced path via the standalone
    Dropbox API (no MCP connector). Best-effort; returns None on any failure."""
    if not dropbox_url:
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dropbox"))
        from dropbox_api import resolve_to_local
        p = resolve_to_local(dropbox_url)
        return p if p and Path(p).exists() else None
    except Exception as e:
        print(f"[warn] Dropbox resolve failed ({e}); falling back to manifest path", file=sys.stderr)
        return None


def _base_args(m: dict) -> list[str]:
    args = [
        "--project-name", m["project_name"],
        "--parts-folder", m["parts_folder"],
        "--dropbox-url",  m["dropbox_url"],   # source: Dropbox Bill of Materials
        "--asana-url",    m["asana_url"],     # source control: the control task
    ]
    # Standard imports omit --project-gid (defaults to Build Orders). Only pass it
    # when the manifest overrides the target to something other than Build Orders.
    tgt = m.get("target_project_gid") or DEFAULT_BUILD_ORDERS
    if tgt != DEFAULT_BUILD_ORDERS:
        args += ["--project-gid", str(tgt)]
    return args


def _run(extra: list[str]) -> int:
    cmd = [sys.executable, str(RUNNER)] + extra
    print("»", " ".join(cmd))
    return subprocess.run(cmd).returncode


def _post_commit(m: dict, token: str | None) -> None:
    """After Stage 5: assign the ALL BOMS root and add it to the job project.
    Defensive — logs and moves on; never aborts the run."""
    if not token:
        print("[warn] no Asana token — skipping assign/project step")
        return
    try:
        import requests
        headers = {"Authorization": f"Bearer {token}"}
        target = m.get("target_project_gid") or DEFAULT_BUILD_ORDERS
        want = f"ALL BOMS - {m['project_name']}".lower()
        r = requests.get(f"https://app.asana.com/api/1.0/projects/{target}/tasks",
                         headers=headers,
                         params={"opt_fields": "name,created_at", "limit": 100}, timeout=30)
        r.raise_for_status()
        roots = [t for t in r.json().get("data", []) if want in t.get("name", "").lower()]
        if not roots:
            print("[warn] ALL BOMS root not found post-commit — assign/project skipped")
            return
        root = sorted(roots, key=lambda t: t.get("created_at", ""))[-1]  # newest
        gid = root["gid"]
        if m.get("assignee_gid"):
            requests.put(f"https://app.asana.com/api/1.0/tasks/{gid}",
                         headers=headers, json={"data": {"assignee": m["assignee_gid"]}}, timeout=30)
            print(f"  assigned root {gid} -> {m['assignee_gid']}")
        if m.get("job_project_gid"):
            requests.post(f"https://app.asana.com/api/1.0/tasks/{gid}/addProject",
                          headers=headers, json={"data": {"project": m["job_project_gid"]}}, timeout=30)
            print(f"  added root {gid} -> job project {m['job_project_gid']}")
    except Exception as e:
        print(f"[warn] post-commit assign/project step failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Hermes-triggered BOM push (traditional route).")
    ap.add_argument("--job", help="Manifest name in jobs/ (without .json)")
    ap.add_argument("--payload", help="Dispatch payload .json; builds a manifest on the fly")
    ap.add_argument("--commit", action="store_true", help="Run Stage 5 (after Stage 4 approval)")
    ap.add_argument("--preflight", action="store_true", help="Validate inputs only")
    ap.add_argument("--stage4-file", default=None, help="Override the reviewed Stage 4 xlsx")
    args = ap.parse_args()

    if args.payload:
        m = _manifest_from_payload(args.payload)
    elif args.job:
        m = _load_manifest(args.job)
    else:
        sys.exit("ERROR: provide --job <manifest> or --payload <json>")
    token = _load_token()

    # Resolve the parts-folder from the Dropbox link if the manifest path is
    # missing or not synced yet — via the standalone Dropbox API (no MCP needed).
    if not m.get("parts_folder") or not Path(m["parts_folder"]).exists():
        resolved = _resolve_parts_folder(m.get("dropbox_url"))
        if resolved:
            m["parts_folder"] = str(resolved)

    base = _base_args(m)

    if not m.get("parts_folder") or not Path(m["parts_folder"]).exists():
        _tg(f"⚠ BOM push {m['project_name']}: parts folder not found (Dropbox not synced / unresolved).")
        sys.exit(f"ERROR: parts-folder not found on host: {m.get('parts_folder')}")

    # Preflight only ---------------------------------------------------------
    if args.preflight:
        sys.exit(_run(base + ["--preflight"]))

    # Commit (Stage 5) — only after human approved the Stage 4 Excel ----------
    if args.commit:
        stage4 = args.stage4_file
        if not stage4 and STATE_FILE.exists():
            st = json.load(open(STATE_FILE, encoding="utf-8"))
            # Guard: only reuse the state file if it belongs to THIS job and is
            # awaiting commit. Prevents committing another job's Stage 4 when the
            # state file is stale (e.g. left over from a different manifest).
            if st.get("status") == "awaiting_commit" and st.get("project_name") == m["project_name"]:
                stage4 = st.get("stage4_file")
            elif st.get("project_name") != m["project_name"]:
                sys.exit(
                    f"ERROR: pipeline_last_run.json is for '{st.get('project_name')}', "
                    f"not '{m['project_name']}'. Run Stage 4 for this job first, or pass "
                    f"--stage4-file explicitly.")
        if not stage4 or not Path(stage4).exists():
            sys.exit("ERROR: no reviewed Stage 4 file to commit. Run Stage 4 first and approve it.")
        rc = _run(base + ["--stage4-file", stage4, "--commit"])
        if rc == 0:
            _post_commit(m, token)
            _tg(f"✅ Committed BOM push: {m['project_name']} (source: {m['asana_url']})")
        else:
            _tg(f"❌ Commit FAILED for {m['project_name']} (exit {rc}).")
        sys.exit(rc)

    # Default: preflight, then Stage 4 (HITL gate, no Asana writes) -----------
    if _run(base + ["--preflight"]) != 0:
        _tg(f"BOM push {m['project_name']}: preflight FAILED — see logs.")
        sys.exit(1)
    rc = _run(base + ["--stop-after", "4"])
    if rc == 0 and STATE_FILE.exists():
        st = json.load(open(STATE_FILE, encoding="utf-8"))
        _tg(f"🧾 <b>I have processed this to Stage 4 — please look here:</b>\n"
            f"Project: {m['project_name']}\n"
            f"Review file: {st.get('stage4_file')}\n"
            f"Control task: {m.get('asana_url', '')}\n"
            f"Apply SKIP/HOLD/FORCE_ORDER overrides, then reply to approve commit.")
    sys.exit(rc)


if __name__ == "__main__":
    main()
