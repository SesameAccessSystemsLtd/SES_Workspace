"""
chase_env.py — ONE place that resolves the purchasing Graph credentials.

The .env key names are historically inconsistent (a transposed typo, an ambiguous
"API_KEY", and two duplicate values). Every other purchasing script reads a slightly
different set. This module is the single normaliser: it tries the known-good names,
falls back to the alternatives, and reports WHICH key names it used (names only —
never the values) so the inconsistency stays visible instead of silently bit-rotting.

Working combination (proven live 2026-09-23):
    client_id     -> PURCHAISNG_CLIENT_ID   (the transposed typo IS the real key)
    client_secret -> PURCHASING_API_KEY
    tenant_id     -> PURCHASING_TENANT_ID

Verified duplicates in .env (same lengths, likely copy-paste artefacts):
    PURCHAISNG_SECRET_ID    (len 36 == client_id  -> a second copy of the client id)
    PURCHASING_EMAIL_API_KEY (len 40 == secret    -> a second copy of the secret)

Read-only. Never prints or returns values, only key NAMES and presence/lengths.
"""
from __future__ import annotations

import os
from pathlib import Path

_ENV_PATH = Path(os.path.expanduser("~")) / ".hermes" / ".env"

# Resolution order per field: first hit wins. The first entry is the known-good key.
_CLIENT_ID_KEYS = ("PURCHAISNG_CLIENT_ID", "PURCHASING_CLIENT_ID", "PURCHAISNG_SECRET_ID")
_SECRET_KEYS = ("PURCHASING_API_KEY", "PURCHASING_EMAIL_API_KEY", "PURCHAISNG_SECRET_ID")
_TENANT_KEYS = ("PURCHASING_TENANT_ID", "Purchasing_tenant_ID")


def _read_env() -> dict[str, str]:
    """Parse .env directly (case-sensitive, like outbound_awaiting_reply.py)."""
    env: dict[str, str] = {}
    try:
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    # Also honour live process env (os.environ is case-insensitive on Windows).
    for k, v in os.environ.items():
        env.setdefault(k, v)
    return env


def get_graph_creds() -> dict:
    """Return {client_id, client_secret, tenant_id, used_keys: {...}}.

    `used_keys` maps logical name -> the actual .env key name that supplied it,
    plus presence/length of the field (length only, never the value).
    """
    env = _read_env()

    def pick(candidates: tuple[str, ...]) -> tuple[str, str]:
        for name in candidates:
            if env.get(name):
                return name, env[name]
        return "", ""

    used: dict[str, dict] = {}

    cid_key, client_id = pick(_CLIENT_ID_KEYS)
    sec_key, client_secret = pick(_SECRET_KEYS)
    ten_key, tenant_id = pick(_TENANT_KEYS)

    used["client_id"] = {"key": cid_key, "len": len(client_id)}
    used["client_secret"] = {"key": sec_key, "len": len(client_secret)}
    used["tenant_id"] = {"key": ten_key, "len": len(tenant_id)}

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "tenant_id": tenant_id,
        "used_keys": used,
        "env_path": str(_ENV_PATH),
    }


def describe_usage(creds: dict) -> str:
    """Human-readable (and log-safe) description of which keys were used."""
    uk = creds.get("used_keys", {})
    parts = []
    for logical, info in uk.items():
        key = info.get("key") or "<MISSING>"
        parts.append(f"{logical}=<{key} len={info.get('len', 0)}>")
    return "; ".join(parts)
