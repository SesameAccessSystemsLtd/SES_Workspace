"""
extract_di.py — Azure AI Document Intelligence escalation for the invoice gate (D → A).

Policy: TEXT FIRST. The local extractor (skills/pdf_extractor) and the seven-condition
gate run on every document. This module is called only when that path cannot produce a
confident answer, and only on documents worth the call:

  escalate when  (a) no text came back from the PDF (scan / image-only), or
                 (b) a PO resolved but the gate refused on "no invoice number found" or
                     "no printed gross matches" — i.e. the document is a real supplier
                     invoice on a known order and the only gap is field typing.
  never escalate (c) documents with no PO and no known supplier — those are the
                     overhead / no-PO class and go to their own list, not to OCR.

One DI call returns BOTH typed fields (InvoiceId, PurchaseOrder, VendorName, InvoiceTotal,
SubTotal, TotalTax, InvoiceDate, each with a confidence) AND the full text layer
(`content`). The gate is re-run on `content` so closed-set PO recognition works on OCR'd
text; the typed fields are passed as hints (candidate invoice number / printed gross).

Cost controls: every result is cached on the file SHA-256 (a document is never analysed
twice); a monthly page counter is kept so the free tier (F0: 500 pages/month, 4 MB and
first 2 pages per document) is never exceeded silently; DI_MODE=dry logs what WOULD be
sent without sending it.

Config (in C:/Users/GilesHugo/.hermes/.env — never in this file):
  AZURE_DI_ENDPOINT   e.g. https://<resource>.cognitiveservices.azure.com
  AZURE_DI_KEY        resource key
  DI_MODE             off | dry | live        (default off)
  DI_MONTHLY_PAGE_CAP integer, default 450    (headroom under F0's 500)
  DI_API_VERSION      default 2024-11-30      (verify against current docs before go-live)

No Asana writes, no sheet writes, no Dropbox writes here. Read-only against the network.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from gateway_common import DATA, load_json, save_json, now_utc
except Exception:                                  # sandbox / test-suite fallback
    DATA = Path(__file__).resolve().parents[2] / "data"
    def load_json(p, default=None):
        try: return json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception: return default
    def save_json(p, obj):
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        Path(p).write_text(json.dumps(obj, indent=1), encoding="utf-8")
    def now_utc(): return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

CACHE_DIR = DATA / "di_cache"
USAGE_PATH = DATA / "di_usage.json"
MODEL_ID = "prebuilt-invoice"

# F0 hard limits — a document outside these is refused locally, never sent.
F0_MAX_BYTES = 4 * 1024 * 1024
F0_MAX_PAGES_ANALYSED = 2


# ----------------------------------------------------------------------------- config
def di_mode() -> str:
    m = (os.environ.get("DI_MODE") or "off").strip().lower()
    return m if m in ("off", "dry", "live") else "off"

def di_configured() -> bool:
    return bool(os.environ.get("AZURE_DI_ENDPOINT")) and bool(os.environ.get("AZURE_DI_KEY"))

def monthly_cap() -> int:
    try: return int(os.environ.get("DI_MONTHLY_PAGE_CAP", "450"))
    except ValueError: return 450


# ----------------------------------------------------------------------------- policy
ESCALATE_REASONS = (
    "no invoice number",          # condition 6 failed for want of a candidate
    "no printed gross",           # condition 7 failed for want of a printed total
    "gross not found",
)

def should_escalate(text: str, has_po: bool, has_known_supplier: bool,
                    gate_reasons: list[str] | None = None) -> tuple[bool, str]:
    """Return (escalate?, why). Pure function — no I/O — so it is unit-testable."""
    if not text or not text.strip():
        if has_po or has_known_supplier:
            return True, "no text from local extractor (scan?) on a document with a PO/known supplier"
        return False, "no text and no PO/known supplier — overhead/no-PO route, not OCR"
    if not has_po:
        return False, "no PO resolved — not worth a DI call; overhead/no-PO route"
    reasons = " | ".join(gate_reasons or []).lower()
    for r in ESCALATE_REASONS:
        if r in reasons:
            return True, f"PO resolved but gate refused on '{r}' — field typing needed"
    return False, "local path sufficient"


# ----------------------------------------------------------------------------- result
@dataclass
class DIField:
    value: str | float | None
    confidence: float | None

@dataclass
class DIResult:
    sha256: str
    source: str                      # "cache" | "live" | "dry" | "refused"
    pages: int = 0
    content: str = ""                # full text layer — feed this back into run_gate()
    invoice_id: DIField = field(default_factory=lambda: DIField(None, None))
    purchase_order: DIField = field(default_factory=lambda: DIField(None, None))
    vendor_name: DIField = field(default_factory=lambda: DIField(None, None))
    invoice_date: DIField = field(default_factory=lambda: DIField(None, None))
    invoice_total: DIField = field(default_factory=lambda: DIField(None, None))
    subtotal: DIField = field(default_factory=lambda: DIField(None, None))
    total_tax: DIField = field(default_factory=lambda: DIField(None, None))
    currency: str | None = None
    refused_reason: str | None = None

    def hints(self) -> dict:
        """What the gate may use as candidates (never as authority)."""
        return {
            "invoice_number": self.invoice_id.value,
            "invoice_number_conf": self.invoice_id.confidence,
            "printed_gross": self.invoice_total.value,
            "printed_gross_conf": self.invoice_total.confidence,
            "purchase_order": self.purchase_order.value,
            "vendor_name": self.vendor_name.value,
        }

    def to_dict(self) -> dict:
        return asdict(self)


def _f(fields: dict, name: str) -> DIField:
    f = fields.get(name) or {}
    conf = f.get("confidence")
    if "valueCurrency" in f and isinstance(f["valueCurrency"], dict):
        return DIField(f["valueCurrency"].get("amount"), conf)
    for k in ("valueString", "valueDate", "valueNumber"):
        if k in f:
            return DIField(f[k], conf)
    return DIField(f.get("content"), conf)


def parse_response(payload: dict, sha256: str, source: str = "live") -> DIResult:
    """Pure: turn an analyzeResult payload into a DIResult. Tolerates missing fields."""
    ar = payload.get("analyzeResult") or payload
    docs = ar.get("documents") or [{}]
    fields = (docs[0] or {}).get("fields") or {}
    res = DIResult(sha256=sha256, source=source,
                   pages=len(ar.get("pages") or []),
                   content=ar.get("content") or "",
                   invoice_id=_f(fields, "InvoiceId"),
                   purchase_order=_f(fields, "PurchaseOrder"),
                   vendor_name=_f(fields, "VendorName"),
                   invoice_date=_f(fields, "InvoiceDate"),
                   invoice_total=_f(fields, "InvoiceTotal"),
                   subtotal=_f(fields, "SubTotal"),
                   total_tax=_f(fields, "TotalTax"))
    cur = (fields.get("InvoiceTotal") or {}).get("valueCurrency") or {}
    res.currency = cur.get("currencyCode")
    return res


# ----------------------------------------------------------------------------- usage ledger
def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")

def usage() -> dict:
    u = load_json(USAGE_PATH, {}) or {}
    return u.setdefault(_month_key(), {"pages": 0, "calls": 0, "refused": 0, "cached": 0})

def _bump(k: str, n: int = 1) -> None:
    u = load_json(USAGE_PATH, {}) or {}
    m = u.setdefault(_month_key(), {"pages": 0, "calls": 0, "refused": 0, "cached": 0})
    m[k] = m.get(k, 0) + n
    save_json(USAGE_PATH, u)


# ----------------------------------------------------------------------------- analyse
def analyze(pdf_bytes: bytes, sha256: str | None = None, *, why: str = "") -> DIResult:
    """Text-first escalation entry point. Returns a DIResult in every mode; check .source."""
    sha = sha256 or hashlib.sha256(pdf_bytes).hexdigest()
    cached = load_json(CACHE_DIR / f"{sha}.json", None)
    if cached:
        _bump("cached")
        return parse_response(cached, sha, source="cache")

    if len(pdf_bytes) > F0_MAX_BYTES:
        _bump("refused")
        return DIResult(sha256=sha, source="refused",
                        refused_reason=f"{len(pdf_bytes)} bytes exceeds F0 limit {F0_MAX_BYTES}")

    u = usage()
    if u["pages"] + F0_MAX_PAGES_ANALYSED > monthly_cap():
        _bump("refused")
        return DIResult(sha256=sha, source="refused",
                        refused_reason=f"monthly page cap {monthly_cap()} would be exceeded ({u['pages']} used)")

    mode = di_mode()
    if mode == "off" or not di_configured():
        return DIResult(sha256=sha, source="refused",
                        refused_reason=f"DI_MODE={mode}, configured={di_configured()}")
    if mode == "dry":
        print(f"    DI DRY: would analyse {sha[:12]} ({len(pdf_bytes)} bytes) — {why}")
        return DIResult(sha256=sha, source="dry", refused_reason="dry run")

    payload = _call_live(pdf_bytes)
    save_json(CACHE_DIR / f"{sha}.json", payload)
    res = parse_response(payload, sha, source="live")
    _bump("calls"); _bump("pages", max(1, min(res.pages, F0_MAX_PAGES_ANALYSED)))
    return res


def _call_live(pdf_bytes: bytes) -> dict:
    """POST …:analyze then poll Operation-Location. Kept small on purpose; verify
    DI_API_VERSION against the current Microsoft docs before switching DI_MODE=live."""
    import urllib.request, urllib.error
    endpoint = os.environ["AZURE_DI_ENDPOINT"].rstrip("/")
    key = os.environ["AZURE_DI_KEY"]
    ver = os.environ.get("DI_API_VERSION", "2024-11-30")
    url = f"{endpoint}/documentintelligence/documentModels/{MODEL_ID}:analyze?api-version={ver}"
    body = json.dumps({"base64Source": base64.b64encode(pdf_bytes).decode("ascii")}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Ocp-Apim-Subscription-Key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.status not in (200, 202):
            raise RuntimeError(f"DI analyze HTTP {r.status}")
        op = r.headers.get("Operation-Location")
    if not op:
        raise RuntimeError("DI analyze: no Operation-Location header")
    for _ in range(60):                                    # ≤ ~60 s
        time.sleep(1.0)
        rq = urllib.request.Request(op, headers={"Ocp-Apim-Subscription-Key": key})
        with urllib.request.urlopen(rq, timeout=60) as r:
            payload = json.loads(r.read().decode("utf-8"))
        st = (payload.get("status") or "").lower()
        if st == "succeeded":
            return payload
        if st in ("failed", "canceled"):
            raise RuntimeError(f"DI analyze {st}: {payload.get('error')}")
    raise TimeoutError("DI analyze did not complete in time")


# ----------------------------------------------------------------------------- cli
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Text-first DI escalation — status / dry-run one file")
    ap.add_argument("pdf", nargs="?", help="PDF to analyse (respects DI_MODE)")
    ap.add_argument("--status", action="store_true", help="print config + this month's usage")
    a = ap.parse_args()
    if a.status or not a.pdf:
        print(json.dumps({"mode": di_mode(), "configured": di_configured(),
                          "monthly_cap": monthly_cap(), "usage": usage()}, indent=1))
    if a.pdf:
        b = Path(a.pdf).read_bytes()
        r = analyze(b, why=f"cli {a.pdf}")
        d = r.to_dict(); d["content"] = (d["content"] or "")[:600] + ("…" if len(d["content"] or "") > 600 else "")
        print(json.dumps(d, indent=1, default=str))
