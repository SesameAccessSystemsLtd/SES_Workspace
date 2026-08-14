"""
dropbox_api.py — Standalone Dropbox API v2 resolver (NO MCP connector needed).

Runs straight out of a wrapper / Hermes cron with just an access token, so BOM
processing is autonomous — it does not depend on the Claude/Codex Dropbox MCP
connector (that only exists inside an interactive session).

Token: read from env (Doppler-injected on the host). Looks for, in order:
    DROPBOX_ACCESS_TOKEN, DROPBOX_TOKEN, DROPBOX_API_TOKEN
Expose one of those via Doppler (e.g. `doppler run -- python bom_push.py ...`).

What it gives you (the "super powerful" bit Giles wants):
    * shared_link_metadata(url)  -> name, path_lower, link_type
    * resolve_to_local(url)      -> local synced SAS Dropbox path (folder as parts-folder)
    * list_folder(url|path)      -> look inside a folder (entries: name, type, path)
    * download(path, dest)       -> pull a file out (read-only; never writes to Dropbox)

Read-only by policy. This module never creates, moves or deletes in Dropbox.

CLI (quick check on the host):
    python dropbox_api.py resolve "<dropbox folder url>"
    python dropbox_api.py list    "<dropbox folder url>"
"""
from __future__ import annotations

import os
import sys
import json
from pathlib import Path

import requests

API = "https://api.dropboxapi.com/2"
CONTENT = "https://content.dropboxapi.com/2"
# Local root of the synced SAS Dropbox on the host (env-overridable).
SAS_DROPBOX_ROOT = os.environ.get(
    "SAS_DROPBOX_ROOT", r"C:\Users\GilesHugo\SAS Dropbox")


def _token() -> str:
    # standard names first, then the Doppler/.env names Giles actually uses
    for var in ("DROPBOX_ACCESS_TOKEN", "DROPBOX_TOKEN", "DROPBOX_API_TOKEN",
                "DROPBOX_API_KEY", "DROPBOX_SECRET"):
        tok = os.environ.get(var)
        if tok:
            return tok
    raise SystemExit(
        "ERROR: no Dropbox token. Set DROPBOX_ACCESS_TOKEN (via Doppler).")


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json"}


def shared_link_metadata(url: str) -> dict:
    """Metadata for a shared link (folder or file): name, path_lower, link_type."""
    r = requests.post(f"{API}/sharing/get_shared_link_metadata",
                      headers=_headers(), json={"url": url}, timeout=30)
    r.raise_for_status()
    return r.json()


def resolve_to_local(url: str, root: str | None = None) -> Path:
    """Map a Dropbox shared-folder URL -> the local synced path on the host.

    Uses path_lower from the shared-link metadata joined to the local SAS Dropbox
    root. This is what the BOM wrapper feeds to run_mrp_pipeline as --parts-folder
    (no download needed — the host already has the folder synced)."""
    meta = shared_link_metadata(url)
    path_lower = (meta.get("path_lower") or "").lstrip("/")
    if not path_lower:
        raise RuntimeError(
            f"Shared link has no path_lower (not in this account's Dropbox?): {url}")
    root = root or SAS_DROPBOX_ROOT
    # path_lower is '/3. projects/...'; drop the leading team-space segment already
    # implied by the local root only if present. Keep it simple: join as-is.
    local = Path(root) / Path(*path_lower.split("/"))
    return local


def list_folder(url_or_path: str, recursive: bool = False) -> list[dict]:
    """List a folder's children. Accepts a shared-link URL or an in-account path.
    Returns entries with: name, type ('folder'|'file'), path_lower, size."""
    if url_or_path.startswith("http"):
        body = {"path": "", "shared_link": {"url": url_or_path}, "recursive": recursive}
    else:
        body = {"path": url_or_path, "recursive": recursive}
    out, cursor = [], None
    while True:
        if cursor:
            r = requests.post(f"{API}/files/list_folder/continue",
                              headers=_headers(), json={"cursor": cursor}, timeout=30)
        else:
            r = requests.post(f"{API}/files/list_folder",
                              headers=_headers(), json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        for e in data.get("entries", []):
            out.append({"name": e.get("name"),
                        "type": "folder" if e.get(".tag") == "folder" else "file",
                        "path_lower": e.get("path_lower"),
                        "size": e.get("size")})
        if not data.get("has_more"):
            break
        cursor = data.get("cursor")
    return out


def download(path_lower: str, dest: str | Path) -> Path:
    """Download a file by in-account path to dest (read-only pull)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = {"Authorization": f"Bearer {_token()}",
         "Dropbox-API-Arg": json.dumps({"path": path_lower})}
    with requests.post(f"{CONTENT}/files/download", headers=h, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
    return dest


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: python dropbox_api.py {resolve|list|meta} <url>")
    cmd, url = sys.argv[1], sys.argv[2]
    if cmd == "resolve":
        print(resolve_to_local(url))
    elif cmd == "meta":
        print(json.dumps(shared_link_metadata(url), indent=2))
    elif cmd == "list":
        for e in list_folder(url):
            print(f"  [{e['type']:6}] {e['name']}")
    else:
        sys.exit(f"unknown command: {cmd}")
