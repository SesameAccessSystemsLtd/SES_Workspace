"""Command line: python -m jibble_sync <run|check|report> ...

Secrets come from the environment (Doppler / GitHub secrets):
  JIBBLE_KEY_ID, JIBBLE_API_KEY           Jibble API key ID and secret
  ASANA_TOKEN                             personal access token of the admin sync account
  EVERHOUR_API_KEY                        only when config time_target is everhour or both
Logs carry counts and IDs only (no names, dates or leave types), because the
GitHub Actions logs for this repository are public.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from .asana import Asana
from .holiday import HolidaySync
from .http import request, safe_err
from .jibble import Jibble
from .reports import Reports
from .targets import AsanaTarget, Everhour, EverhourTarget
from .timesync import TimeSync
from .works import ServiceWorks

HERE = Path(__file__).resolve().parent.parent


DOPPLER_DOWNLOAD = "https://api.doppler.com/v3/configs/config/secrets/download"
SECRET_NAMES = ("JIBBLE_KEY_ID", "JIBBLE_CLIENT_ID", "JIBBLE_API_KEY", "JIBBLE_CLIENT_SECRET", "ASANA_TOKEN",
                "ASANA_PERSONAL_ACCESS_TOKEN", "ASANA_PAT_MCP_KEY", "EVERHOUR_API_KEY", "JIBBLE_SYNC_PEOPLE_JSON")


def load_doppler(http=request, environ=os.environ, out=print) -> int:
    """Fetch the sync's secrets from Doppler at run time when DOPPLER_TOKEN is set.

    The token should be a read-only service token for a config holding only these
    secrets, so the API keys themselves live in Doppler alone. Values stay in this
    process; in GitHub Actions each one is masked so it can never appear in a log.
    """
    token = environ.get("DOPPLER_TOKEN")
    if not token:
        return 0
    data = http("GET", DOPPLER_DOWNLOAD, headers={"Authorization": f"Bearer {token}"}, params={"format": "json"}) or {}
    loaded = 0
    for name in SECRET_NAMES:
        value = data.get(name)
        if isinstance(value, str) and value and not environ.get(name):
            if environ.get("GITHUB_ACTIONS") == "true":
                for line in value.splitlines():
                    if line.strip():
                        out(f"::add-mask::{line}")
            environ[name] = value
            loaded += 1
    return loaded


def _env(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v.strip().strip('"')
    return None


def load_config(path: str | None) -> dict:
    cfg = json.loads(Path(path or HERE / "config.json").read_text())
    extra = _env("JIBBLE_SYNC_PEOPLE_JSON")  # private per-person hours, kept out of the repo
    if extra:
        cfg.setdefault("people", {}).update(json.loads(extra))
    return cfg


def clients(dry_run: bool, log):
    jid = _env("JIBBLE_KEY_ID", "JIBBLE_CLIENT_ID")
    jsecret = _env("JIBBLE_API_KEY", "JIBBLE_CLIENT_SECRET")
    token = _env("ASANA_TOKEN", "ASANA_PERSONAL_ACCESS_TOKEN", "ASANA_PAT_MCP_KEY")
    missing = [n for n, v in (("JIBBLE_KEY_ID", jid), ("JIBBLE_API_KEY", jsecret), ("ASANA_TOKEN", token)) if not v]
    if missing:
        return None, None, missing
    return Asana(token, dry_run=dry_run, log=log), Jibble(jid, jsecret, dry_run=dry_run, log=log), []


def email_aliases(cfg: dict) -> dict[str, str]:
    """Private map {jibble email: work email} for people whose Jibble login is a personal address."""
    return {k.strip().lower(): v.strip().lower() for k, v in ((cfg.get("people") or {}).get("email_aliases") or {}).items()}


def with_aliases(users: dict[str, str], cfg: dict) -> dict[str, str]:
    out = dict(users)
    for jibble_email, work_email in email_aliases(cfg).items():
        if work_email in users and jibble_email not in users:
            out[jibble_email] = users[work_email]
    return out


def time_targets(cfg: dict, asana, users: dict, dry_run: bool, log) -> list:
    """Where Jibble time is written: config time_target = asana (default), everhour or both."""
    kind = (cfg.get("time_target") or "asana").lower()
    targets = []
    if kind in ("asana", "both"):
        targets.append(AsanaTarget(asana, asana.me()["gid"], users))
    if kind in ("everhour", "both"):
        key = _env("EVERHOUR_API_KEY")
        if not key:
            raise RuntimeError("time_target needs EVERHOUR_API_KEY, which is not set")
        eh = Everhour(key, dry_run=dry_run, log=log)
        targets.append(EverhourTarget(eh, (cfg.get("everhour") or {}).get("users", []), date.today().isoformat(),
                                      aliases=email_aliases(cfg)))
    if not targets:
        raise RuntimeError(f"Unknown time_target '{kind}'")
    return targets


def summary(lines: list[str]):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jibble_sync")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="sync holidays and time (full mode unless --dry-run)")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--only", choices=["holiday", "time"])
    sub.add_parser("check", help="read-only: confirm both APIs answer")
    rp = sub.add_parser("report", help="Everhour-layout CSV exports (read-only)")
    rp.add_argument("kind", choices=["payroll", "manufacturing"])
    rp.add_argument("--from", dest="d0", default=(date.today() - timedelta(days=30)).isoformat())
    rp.add_argument("--to", dest="d1", default=date.today().isoformat())
    rp.add_argument("--client")
    rp.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    log = lambda msg: print(msg, flush=True)  # noqa: E731
    try:
        n = load_doppler()
        if n:
            log(f"Loaded {n} secret(s) from Doppler")
    except Exception as e:  # never print the response: it would hold the secrets
        log(f"Doppler secrets unavailable: {safe_err(e)}")
        summary([f"**Jibble sync failed:** Doppler secrets unavailable ({safe_err(e)})"])
        return 1
    cfg = load_config(args.config)
    dry = getattr(args, "dry_run", False) or args.cmd != "run"
    asana, jibble, missing = clients(dry, log)
    if missing:
        msg = f"Secrets not configured ({', '.join(missing)}); nothing to do."
        log(msg)
        summary([f"**Jibble sync skipped:** {msg}"])
        return 0

    if args.cmd == "check":
        me = asana.me()
        log(f"Asana OK as user {me['gid']}")
        log(f"Jibble OK: {len(jibble.people())} people, {len(jibble.projects())} projects, "
            f"{len(jibble.activities())} activities, {len(jibble.time_off_policies())} time-off policies")
        return 0

    if args.cmd == "report":
        rep = Reports(cfg, asana, jibble, log)
        if args.kind == "payroll":
            users = with_aliases(asana.users_by_email(cfg["asana"]["workspace"]), cfg)
            n = rep.payroll(args.d0, args.d1, args.out, users)
        else:
            n = rep.manufacturing(args.d0, args.d1, args.out, args.client)
        log(f"Wrote {n} rows to {args.out}")
        return 0

    users = with_aliases(asana.users_by_email(cfg["asana"]["workspace"]), cfg)
    lines = [f"## Jibble sync ({'dry run' if dry else 'full'})"]
    failed = False
    if args.only in (None, "holiday"):
        try:
            hs = HolidaySync(cfg, asana, jibble, log).run(users)
            lines.append("**Holiday:** " + ", ".join(f"{k} {v}" for k, v in hs.items()))
            failed |= hs.get("errors", 0) > 0
        except Exception as e:
            failed = True
            lines.append(f"**Holiday failed:** {safe_err(e)}")
    if args.only in (None, "time"):
        ts = TimeSync(cfg, asana, jibble, log)
        try:
            targets = time_targets(cfg, asana, users, dry, log)
            works = ServiceWorks(cfg, asana, log) if (cfg.get("works") or {}).get("enabled") else None
            st = ts.run(users, targets=targets, works=works)
            lines.append("**Time:** " + ", ".join(f"{k} {v}" for k, v in sorted(st.items())))
            failed |= any(v for k, v in st.items() if k.endswith("task_errors"))
        except Exception as e:
            failed = True
            lines.append(f"**Time failed:** {safe_err(e)}")
        if ts.exceptions:
            lines.append("**Exceptions:**")
            lines += [f"- {x}" for x in ts.exceptions[:50]]
    for line in lines:
        log(line)
    summary(lines)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
