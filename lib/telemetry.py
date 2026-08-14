"""
SES_Workspace telemetry — run logging, failure records, request log.
Silent on all failures — never crashes the calling script.

Usage (simple):
    from telemetry import log_run
    log_run("my_script.py", pipeline="purchasing", exit_code=0, summary="3 emails parsed")

Usage (context manager — captures timing automatically):
    from telemetry import timed_run
    with timed_run("my_script.py", pipeline="purchasing"):
        main()
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path resolution — reads PATH_CONFIG if available, falls back to defaults
# ---------------------------------------------------------------------------

def _default_path(key: str, fallback: str) -> str:
    try:
        import sys
        _lib = Path(__file__).resolve().parent
        if str(_lib) not in sys.path:
            sys.path.insert(0, str(_lib))
        from path_config import p
        return p(key)
    except Exception:
        return fallback

_WS = Path(__file__).resolve().parent.parent  # SES_Workspace/

TELEMETRY_DB_PATH: str = _default_path(
    "db.telemetry",
    str(_WS / "db" / "run_log.db"),
)
FAILURES_DIR: str = _default_path(
    "run_history.failures",
    str(_WS / "run_history" / "failures"),
)
REQUESTS_LOG_PATH: str = _default_path(
    "memory.session_log",
    str(_WS / "memory" / "session_log.jsonl"),
)

# ---------------------------------------------------------------------------
# DB initialisation
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at        TEXT    NOT NULL,
    script        TEXT    NOT NULL,
    pipeline      TEXT,
    job_ref       TEXT,
    exit_code     INTEGER NOT NULL DEFAULT 0,
    duration_ms   INTEGER,
    rows_affected INTEGER,
    summary       TEXT,
    error_msg     TEXT,
    harness       TEXT
);

CREATE TABLE IF NOT EXISTS system_health (
    component          TEXT PRIMARY KEY,
    checked_at         TEXT NOT NULL,
    status             TEXT NOT NULL,
    last_ok_at         TEXT,
    consecutive_errors INTEGER DEFAULT 0,
    note               TEXT
);
"""

def _ensure_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_run(
    script: str,
    pipeline: str | None = None,
    job_ref: str | None = None,
    exit_code: int = 0,
    duration_ms: int | None = None,
    rows_affected: int | None = None,
    summary: str | None = None,
    error_msg: str | None = None,
    harness: str | None = None,
    db_path: str = TELEMETRY_DB_PATH,
) -> None:
    """Append one row to run_log. Silent on failure."""
    try:
        _ensure_db(db_path)
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO run_log
                   (run_at, script, pipeline, job_ref, exit_code,
                    duration_ms, rows_affected, summary, error_msg, harness)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (now, script, pipeline, job_ref, exit_code,
                 duration_ms, rows_affected, summary, error_msg, harness),
            )
        if exit_code == 0:
            _update_health(script, "ok", db_path)
        else:
            _update_health(script, "error", db_path, note=error_msg)
    except Exception:
        pass  # telemetry must never crash callers


def write_failure_record(
    script: str,
    pipeline: str,
    error_output: str,
    exit_code: int = 1,
    duration_ms: int | None = None,
    context: dict | None = None,
    failures_dir: str = FAILURES_DIR,
    db_path: str = TELEMETRY_DB_PATH,
) -> str | None:
    """Write a structured failure file and log to run_log. Returns file path or None."""
    try:
        Path(failures_dir).mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        slug = script.replace(".py", "").replace("/", "_").replace("\\", "_")
        filename = f"{ts}_{slug}.json"
        record = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "script": script,
            "pipeline": pipeline,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "error_output": error_output,
            "context": context or {},
            "resolution": "",  # agent fills this in after fixing
        }
        fpath = Path(failures_dir) / filename
        fpath.write_text(json.dumps(record, indent=2), encoding="utf-8")
        log_run(
            script=script,
            pipeline=pipeline,
            exit_code=exit_code,
            duration_ms=duration_ms,
            error_msg=error_output[:500],
            db_path=db_path,
        )
        return str(fpath)
    except Exception:
        return None


def append_request_log(
    request_file: str,
    action: str,
    status: str,
    task_gid: str | None = None,
    detail: str | None = None,
    log_path: str = REQUESTS_LOG_PATH,
) -> None:
    """Append one line to session_log.jsonl for every processed REQUESTS/ file."""
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "harness": os.environ.get("HERMES_PROFILE", "hermes"),
            "type": "asana_request",
            "request_file": request_file,
            "action": action,
            "status": status,
            "task_gid": task_gid,
            "detail": detail,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _update_health(
    component: str,
    status: str,
    db_path: str,
    note: str | None = None,
) -> None:
    try:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(db_path) as conn:
            existing = conn.execute(
                "SELECT consecutive_errors FROM system_health WHERE component=?",
                (component,),
            ).fetchone()
            if status == "ok":
                errors = 0
                last_ok = now
            else:
                errors = (existing[0] + 1) if existing else 1
                last_ok = None
            conn.execute(
                """INSERT INTO system_health
                   (component, checked_at, status, last_ok_at, consecutive_errors, note)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(component) DO UPDATE SET
                     checked_at=excluded.checked_at,
                     status=excluded.status,
                     last_ok_at=COALESCE(excluded.last_ok_at, last_ok_at),
                     consecutive_errors=excluded.consecutive_errors,
                     note=excluded.note""",
                (component, now, status, last_ok, errors, note),
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def timed_run(
    script: str,
    pipeline: str | None = None,
    harness: str | None = None,
    db_path: str = TELEMETRY_DB_PATH,
):
    """
    Context manager that logs run timing automatically.

    with timed_run("my_script.py", pipeline="purchasing"):
        rows = main()
    # Logs exit_code=0, duration_ms=<elapsed>

    On unhandled exception: logs exit_code=1, re-raises.
    """
    t0 = time.monotonic()
    try:
        yield
        elapsed = int((time.monotonic() - t0) * 1000)
        log_run(script=script, pipeline=pipeline, exit_code=0,
                duration_ms=elapsed, harness=harness, db_path=db_path)
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        log_run(script=script, pipeline=pipeline, exit_code=1,
                duration_ms=elapsed, error_msg=str(exc),
                harness=harness, db_path=db_path)
        raise
