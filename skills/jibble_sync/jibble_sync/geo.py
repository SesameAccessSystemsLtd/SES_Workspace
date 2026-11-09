"""UK postcodes: find them in text and turn them into coordinates (postcodes.io, no key needed)."""
from __future__ import annotations

import re

from .http import request

POSTCODES_IO = "https://api.postcodes.io/postcodes"
_PC = re.compile(r"\b([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})\b")


def find_postcode(*texts: str | None) -> str | None:
    """First full UK postcode in the given texts, normalised to 'SW1A 1AA'."""
    for text in texts:
        if not text:
            continue
        m = _PC.search(text.upper())
        if m:
            return f"{m.group(1)} {m.group(2)}"
    return None


def lookup(postcodes: list[str], http=request) -> dict[str, dict]:
    """{postcode: {'latitude', 'longitude', 'region', 'admin_district'}} for the ones postcodes.io knows."""
    out: dict[str, dict] = {}
    unique = sorted({p for p in postcodes if p})
    for i in range(0, len(unique), 100):  # the bulk endpoint takes 100 at a time
        resp = http("POST", POSTCODES_IO, json_body={"postcodes": unique[i:i + 100]}) or {}
        for row in resp.get("result") or []:
            r = row.get("result")
            if r:
                out[row["query"]] = {k: r.get(k) for k in ("latitude", "longitude", "region", "admin_district")}
    return out
