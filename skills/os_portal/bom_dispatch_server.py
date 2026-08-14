"""
bom_dispatch_server.py — localhost dispatcher for the BOM-tracker action buttons.

The tracker is a static file:// page, so its buttons can't write to disk or call
Telegram. They POST their payload to this small local listener, which:

  1. compiles a human-readable prompt,
  2. posts it to the Purchasing Telegram TOPIC (so Giles SEES it appear), and
  3. drops an auditable REQUESTS .md + .json (the durable trigger consumed by
     execute_requests.py, which runs the push to Stage 4 — never commit).

No Asana writes happen here. The button stages a *dispatch*, not a write. Commit
stays a separate, Giles-approved step after he reviews the Stage 4 Excel.

Run (host, kept alive by Hermes):
    C:/Users/GilesHugo/SES_Workspace/.venv/Scripts/python.exe \
        skills/os_portal/bom_dispatch_server.py            # binds 127.0.0.1:8765

Health:  GET  http://127.0.0.1:8765/health
Dispatch: POST http://127.0.0.1:8765/dispatch  (body = the button payload JSON)

The core (compile_prompt / write_request / dispatch) is import-safe and unit-testable
without opening a socket or sending Telegram.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_WS = Path(__file__).resolve().parents[2]           # SES_Workspace
sys.path.insert(0, str(_WS / "lib"))
sys.path.insert(0, str(_WS / "skills" / "purchasing"))

# REQUESTS dir — via path_config, fallback to the known location.
try:
    from path_config import p as _p
    REQUESTS_DIR = Path(_p("queues.requests"))
except Exception:
    REQUESTS_DIR = _WS / "queues" / "REQUESTS"

PORT = int(os.environ.get("BOM_DISPATCH_PORT", "8765"))

# --- env file loader (mirrors os_status_refresh.py so the listener has the
#     Doppler-sourced secrets that live in ~/.hermes/.env without needing a
#     plaintext token in this file or an interactive doppler shell) ---
ENV_PATH = Path(r"C:/Users/GilesHugo/.hermes/.env")


def load_env() -> dict:
    env = dict(os.environ)
    if ENV_PATH.exists():
        try:
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                # strip surrounding quotes ("secret" vs secret)
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                env.setdefault(k, v)
        except OSError as e:
            print(f"[warn] could not read {ENV_PATH}: {e}", file=sys.stderr)
    return env


_env = load_env()          # load once at import so listeners/handlers see secrets
os.environ.update(_env)    # safe: setdefault already preserved real env precedence

# action -> (REQUESTS action_type, label)
ACTION_MAP = {
    "run_bom_pipeline":     ("bom_pipeline_push",   "BOM push (Stages 1–4)"),
    "run_drawing_pipeline": ("run_drawing_pipeline", "Drawing pipeline (pack allocation)"),
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")


def _resolve_locally(dropbox_url, pl=None):
    """Best-effort: Dropbox shared-folder URL -> local SAS Dropbox path. Returns
    a Path or None. CATCHES missing-token / import failures so the dispatch
    message still posts (bom_push re-resolves with the real Doppler token at run
    time). Uses the composite resolver: try the Dropbox API first (same-account
    folders get the exact path_lower path), then fall back to the local
    name-match resolver for Karol cross-account scl/fo links."""
    if not dropbox_url and not pl:
        return None
    try:
        sys.path.insert(0, str(_WS / "skills" / "dropbox"))
        # dropbox_api raises SystemExit (not Exception) when the token is absent —
        # wrap so a listener without the Doppler token still posts.
        def _api_resolve(url):
            try:
                from dropbox_api import resolve_to_local
                return resolve_to_local(url)
            except BaseException:   # noqa: BLE001 — same-account resolve is best-effort
                return None
        from local_dropbox_resolver import try_api_then_local
        # The button payload's project_name is the *job + sub-BOM* entity (e.g.
        # "1595 - 1.Long Leading Time"), NOT the company/project name that maps
        # to a local 3. Projects folder. The real project name lives in the
        # control task, e.g. "Karol Generate Entire BOM - 1595 - St George's House".
        # Derive it: strip the "Karol Generate Entire BOM - <job> - " prefix.
        ctrl = (pl or {}).get("control_name") or (pl or {}).get("control") or ""
        proj_for_resolver = (pl or {}).get("project_name")
        m = re.search(
            r"^Karol\s+Generate\s+Entire\s+BOM\s*[-–]\s*\d+\s*[-–]\s*(.+)$", ctrl)
        if m:
            proj_for_resolver = m.group(1).strip()
        return try_api_then_local(
            dropbox_url or "",
            job=(pl or {}).get("job") if pl else None,
            project_name=proj_for_resolver,
            bom_name=(pl or {}).get("sub_bom") if pl else None,
            resolve_to_local=_api_resolve,
        )
    except BaseException as e:   # noqa: BLE001 — resolve is best-effort, never fatal
        print(f"[warn] dropbox resolve failed ({e!r})", file=sys.stderr)
        return None


def _call_name(pl, local_path):
    """Pipeline call-name (e.g. 'serjeants_inn'). Prefer the Dropbox folder
    basename, else a clean slug of the sub-BOM. A sub-BOM name is prefixed with a
    number by Carol/Karol (e.g. '1.Long Leading Time') — strip the leading
    number and append 'bom' → 'long_leading_time_bom'."""
    src = ""
    if local_path:
        p = Path(str(local_path)) if not isinstance(local_path, Path) else local_path
        src = (p.name or "").strip()
    if not src:
        src = str(pl.get("sub_bom") or pl.get("control_name") or pl.get("control")
                  or pl.get("project_name") or pl.get("job") or "bom").strip()
    # strip a leading '<number>' or '<number>.' or '<number> ' token
    src = re.sub(r"^\s*\d+(?:[\.\s\-_]+)?", "", src).strip()
    slug = re.sub(r"[^a-z0-9]+", "_", src.lower()).strip("_") or "bom"
    # append '_bom' so the call name reads 'long_leading_time_bom' (unless it already ends in _bom/bom)
    if not slug.endswith("bom"):
        slug += "_bom"
    return slug


def compile_prompt(pl: dict) -> str:
    """Build the actionable run-command Giles sees in the topic. For a BOM push it
    resolves the Dropbox folder to the local parts-folder path + call name so the
    message is a REAL command (matching how Giles hand-triggers: 'Process to stage
    4 the BOM Import for X with url … control task is … path is C:\\…'), not a
    placeholder. Still stages via REQUESTS file (kept for now)."""
    action = pl.get("action", "")
    _, label = ACTION_MAP.get(action, ("", action or "action"))
    if action == "run_bom_pipeline":
        local_path = pl.get("parts_folder")
        call = _call_name(pl, local_path)
        parts_folder = f"<b>{local_path}</b>" if local_path else "(resolve at run from Dropbox link)"
        asana_url = f"https://app.asana.com/0/0/{pl.get('control_gid')}" if pl.get("control_gid") else "(none)"
        ctrl = pl.get("control_name") or pl.get("control") or ""
        proj = pl.get("project_name") or pl.get("job") or ""
        # Job + name as ONE entity (matches how Giles hand-triggers)
        job_entity = proj
        return (
            f"🚀 <b>Ready to import — call name <code>{call}</code></b>\n"
            f"Job + name: <b>{job_entity}</b>\n"
            f"Control task: {ctrl} · {asana_url}\n"
            f"Dropbox: {pl.get('dropbox_url','(none)')}\n"
            f"Parts folder: {parts_folder}\n\n"
            f"<b>Reply YES to run to Stage 4.</b> (Stops there — no Asana writes; "
            f"I'll post the Stage 4 review for your sign-off, then you approve the commit.)"
        )
    if action == "run_drawing_pipeline":
        return (
            f"🚀 <b>Drawing pipeline dispatched — {label}</b>\n"
            f"Job: {pl.get('job','')}\n"
            f"Parts without pack: {pl.get('no_pack_parts','?')}\n"
            f"Anchor: {pl.get('anchor','(job drawing)')}\n\n"
            f"Extracting + mapping packs (no Asana writes). "
            f"I'll report when the mapping is staged for your review."
        )
    return f"🚀 Dispatched: {label}\n{json.dumps(pl, indent=1)}"


def write_request(pl: dict, prompt: str, requests_dir: Path = REQUESTS_DIR) -> tuple[Path, Path]:
    """Write an auditable REQUESTS .md + companion .json. Returns (md, json)."""
    action_type, label = ACTION_MAP.get(pl.get("action", ""), ("dispatch_prompt", "dispatch"))
    requests_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    job = pl.get("job") or "bom"
    stem = f"{ts}-tracker-{action_type}-{job}-{uuid.uuid4().hex[:6]}"
    md_path = requests_dir / f"{stem}.md"
    json_path = requests_dir / f"{stem}.json"

    md = f"""---
From: bom_tracker
To: asana_agent
Type: pipeline_dispatch
Status: PENDING
Priority: normal
Created: {_now()}
---

## Request
Action: {action_type}
Job: {job}
Control GID: {pl.get('control_gid','')}
Dropbox: {pl.get('dropbox_url','')}

Details:
{prompt}

## Context
Dispatched from the BOM tracker action button. Runs preflight → Stage 4 only
(no Asana writes, no commit). Commit remains a separate Giles-approved step.
"""
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(pl, indent=2), encoding="utf-8")
    return md_path, json_path


def _send_topic(text: str) -> bool:
    try:
        from notify_telegram import send_purchasing_topic
        return send_purchasing_topic(text)
    except Exception as e:
        print(f"[warn] topic send failed: {e}", file=sys.stderr)
        return False


def dispatch(pl: dict, send: bool = True) -> dict:
    """Compile → resolve the Dropbox path (bridge-side, where the token lives) →
    (send topic) → write REQUESTS. Returns a truthful result dict.

    The bridge adds the resolved local `parts_folder` (path) to the payload so the
    queued command carries the exact path Giles would supply by hand — the browser
    button can't resolve a Dropbox path, but this host-side listener can."""
    if pl.get("action") not in ACTION_MAP:
        return {"ok": False, "error": f"unknown action: {pl.get('action')}"}
    # resolve dropbox_url -> local SAS Dropbox path; inject as parts_folder/path
    local = _resolve_locally(pl.get("dropbox_url"), pl=pl)
    if local is not None:
        pl = dict(pl)
        pl["parts_folder"] = str(local)
        pl["path"] = str(local)
    prompt = compile_prompt(pl)
    md_path, json_path = write_request(pl, prompt)
    sent = _send_topic(prompt) if send else None
    return {"ok": True, "action": pl["action"], "request_file": str(md_path),
            "topic_sent": sent, "prompt": prompt}


# ── HTTP layer ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        # Chrome Private Network Access (PNA): a file:// or non-public page that
        # fetches http://127.0.0.1 must get this header in the preflight, or the
        # browser blocks the request even though Access-Control-Allow-Origin is
        # *. This was the button-not-working root cause (verified live 2026-08-14).
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            return self._json(200, {"ok": True, "service": "bom_dispatch", "requests_dir": str(REQUESTS_DIR)})
        # Serve the OS portal HTML from the same origin so the action buttons
        # post same-origin (no CORS / Private Network Access issues at all).
        # Open in a browser as:  http://127.0.0.1:8765/bom-tracker.html
        if self.path.startswith("/"):
            rel = Path(self.path.lstrip("/")).name   # filename only, no traversal
            portal_root = _WS / "data" / "os_status"
            target = (portal_root / rel).resolve()
            if str(target).startswith(str(portal_root.resolve())) and target.is_file() and target.suffix.lower() in {".html", ".css", ".js", ".svg", ".png", ".jpg", ".ico"}:
                data = target.read_bytes()
                ctype = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                         ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
                         ".png": "image/png", ".jpg": "image/jpeg", ".ico": "image/x-icon"}.get(target.suffix.lower(), "application/octet-stream")
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/dispatch":
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            pl = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"bad payload: {e}"})
        try:
            res = dispatch(pl)
            self._json(200 if res.get("ok") else 400, res)
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})

    def log_message(self, *a):  # quiet
        pass


def main():
    # Idempotent: if the port is already bound, another instance is running —
    # exit cleanly so repeated launches (e.g. from the portal refresh) are no-ops.
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print(f"bom_dispatch_server: port {PORT} unavailable ({e}); assuming already up.")
        return
    print(f"bom_dispatch_server on http://127.0.0.1:{PORT}  (REQUESTS -> {REQUESTS_DIR})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
