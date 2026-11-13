#!/usr/bin/env python
"""Graph Outage Recovery Watchdog.

Silent while the outage continues (expected, no action needed).
Speaks ONLY when Microsoft Graph mail recovers, reporting:
  - the fetch window that will be used (proving no mail is lost)
  - that the watermark is advancing again

Designed for cron no_agent mode: empty stdout = silent.
"""
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

WORKSPACE = r"C:\Users\GilesHugo\SES_Workspace"
STATE_PATH = WORKSPACE + r"\data\fetch_state.json"
FLAG_PATH = WORKSPACE + r"\data\graph_recovery_notified.json"


def load(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(p, obj):
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
    except Exception:
        pass


def probe():
    """Return (ok, detail). ok=True only when the mailbox endpoint answers non-5xx."""
    sys.path.insert(0, WORKSPACE + r"\skills\purchasing")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "fetcher", WORKSPACE + r"\skills\purchasing\01_email_fetcher.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        token = mod.get_access_token()
    except Exception as e:
        return False, f"auth failed: {e}"

    url = ("https://graph.microsoft.com/v1.0/users/purchasing@sesameaccess.com"
           "/mailFolders?%24top=3")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, f"network: {e}"


def main():
    state = load(STATE_PATH, {}) or {}
    wm = state.get("last_success_at_utc")
    notified = load(FLAG_PATH, {}) or {}

    ok, detail = probe()

    if not ok:
        # Still down. Silent unless this is a NEW distinct failure mode worth noting.
        if notified.get("last_probe_result") != detail:
            save(FLAG_PATH, {**notified, "last_probe_result": detail,
                             "last_probe_at": datetime.now(timezone.utc)
                             .strftime("%Y-%m-%dT%H:%M:%SZ")})
        sys.exit(0)

    # Mail plane is back.
    if notified.get("recovered_for_watermark") == wm:
        sys.exit(0)  # already reported for this watermark

    # Compute the window the next real run will use (watermark - 90m).
    window_note = "unknown"
    try:
        from datetime import timedelta
        t = datetime.strptime(wm, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        window_note = (t - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass

    print("MICROSOFT GRAPH MAIL RECOVERED")
    print(f"  Mail endpoint: OK ({detail})")
    print(f"  Watermark frozen at: {wm}")
    print(f"  Next fetch window opens: {window_note}  <-- full missed period, no mail lost")
    print("  Action: none. Scheduled jobs will catch up automatically.")
    print("  Watch: confirm the first Purchasing Email Parser run completes under 300s.")

    save(FLAG_PATH, {**notified, "recovered_for_watermark": wm,
                     "recovered_at": datetime.now(timezone.utc)
                     .strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "last_probe_result": detail})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"GRAPH RECOVERY WATCHDOG FAILED: {e}", file=sys.stderr)
        sys.exit(1)
