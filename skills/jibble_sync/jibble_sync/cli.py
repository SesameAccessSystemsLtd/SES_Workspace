"""Command line: python -m jibble_sync <run|check|report> ...

Secrets come from the environment (Doppler / GitHub secrets):
  JIBBLE_KEY_ID, JIBBLE_API_KEY           Jibble API key ID and secret
  ASANA_TOKEN                             personal access token of the admin sync account
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
from .jibble import Jibble
from .reports import Reports
from .timesync import TimeSync

HERE = Path(__file__).resolve().parent.parent


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
            users = asana.users_by_email(cfg["asana"]["workspace"])
            n = rep.payroll(args.d0, args.d1, args.out, users)
        else:
            n = rep.manufacturing(args.d0, args.d1, args.out, args.client)
        log(f"Wrote {n} rows to {args.out}")
        return 0

    users = asana.users_by_email(cfg["asana"]["workspace"])
    lines = [f"## Jibble sync ({'dry run' if dry else 'full'})"]
    failed = False
    if args.only in (None, "holiday"):
        try:
            hs = HolidaySync(cfg, asana, jibble, log).run(users)
            lines.append("**Holiday:** " + ", ".join(f"{k} {v}" for k, v in hs.items()))
            failed |= hs.get("errors", 0) > 0
        except Exception as e:
            failed = True
            lines.append(f"**Holiday failed:** {type(e).__name__}: {str(e)[:300]}")
    if args.only in (None, "time"):
        ts = TimeSync(cfg, asana, jibble, log)
        try:
            st = ts.run(users)
            lines.append("**Time:** " + ", ".join(f"{k} {v}" for k, v in sorted(st.items())))
            failed |= st.get("task_errors", 0) > 0
        except Exception as e:
            failed = True
            lines.append(f"**Time failed:** {type(e).__name__}: {str(e)[:300]}")
        if ts.exceptions:
            lines.append("**Exceptions:**")
            lines += [f"- {x}" for x in ts.exceptions[:50]]
    for line in lines:
        log(line)
    summary(lines)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
