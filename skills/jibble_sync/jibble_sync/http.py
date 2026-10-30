"""Small JSON-over-HTTPS helper with retries. Standard library only."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request


class HttpError(Exception):
    def __init__(self, status: int, body: str, method: str, url: str):
        # The URL is trimmed to its path so query values never reach logs.
        self.status = status
        self.body = body
        self.method = method
        self.path = urllib.parse.urlsplit(url).path
        super().__init__(f"{method} {self.path} -> HTTP {status}: {body[:300]}")


def encode_params(params: dict | None) -> str:
    if not params:
        return ""
    clean = {k: v for k, v in params.items() if v is not None}
    # OData needs $, (), quotes and commas left readable; spaces become %20.
    return urllib.parse.urlencode(clean, quote_via=urllib.parse.quote, safe="$(),'/:")


def request(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    json_body=None,
    form: dict | None = None,
    retries: int = 4,
    timeout: int = 60,
):
    """Send a request and return parsed JSON (or None for empty bodies).

    Retries on 429 and 5xx with backoff, honouring Retry-After.
    """
    query = encode_params(params)
    if query:
        url = url + ("&" if "?" in url else "?") + query
    data = None
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    if json_body is not None:
        data = json.dumps(json_body).encode()
        hdrs["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"

    attempt = 0
    while True:
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if (e.code == 429 or e.code >= 500) and attempt < retries:
                wait = e.headers.get("Retry-After")
                delay = float(wait) if wait and wait.replace(".", "", 1).isdigit() else 2 ** attempt
                time.sleep(min(delay, 60))
                attempt += 1
                continue
            raise HttpError(e.code, body, method, url) from None
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(2 ** attempt)
                attempt += 1
                continue
            raise
