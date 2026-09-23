"""test_extract_di.py — unit tests for the pure parts of extract_di (no network)."""
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_di as di

def test_parse_response():
    p = json.loads((Path(__file__).parent / "fixtures" / "di_sample_response.json").read_text())
    r = di.parse_response(p, "abc")
    assert r.invoice_id.value == "SI-10912" and r.invoice_id.confidence == 0.97
    assert r.purchase_order.value == "PO21114-1670"
    assert r.invoice_total.value == 31.51 and r.currency == "GBP"
    assert r.subtotal.value == 26.26 and r.total_tax.value == 5.25
    assert r.pages == 2 and "SI-10912" in r.content
    h = r.hints(); assert h["invoice_number"] == "SI-10912" and h["printed_gross"] == 31.51
    print("parse_response            ok")

def test_policy():
    assert di.should_escalate("", True, False)[0]                       # scan on a PO doc → yes
    assert not di.should_escalate("", False, False)[0]                  # scan, no PO, unknown → overhead route
    assert di.should_escalate("", False, True)[0]                       # scan, known supplier → yes
    assert not di.should_escalate("text", False, True)[0]               # no PO → no
    assert di.should_escalate("text", True, True, ["6 no invoice number found"])[0]
    assert di.should_escalate("text", True, True, ["7 no printed gross matches"])[0]
    assert not di.should_escalate("text", True, True, ["2 ORDER Status not ARRIVED"])[0]   # gate refused for a business reason → no
    assert not di.should_escalate("text", True, True, [])[0]
    print("should_escalate           ok")

def test_analyze_modes(tmp: Path):
    di.CACHE_DIR = tmp / "di_cache"; di.USAGE_PATH = tmp / "di_usage.json"
    os.environ.pop("AZURE_DI_ENDPOINT", None); os.environ.pop("AZURE_DI_KEY", None); os.environ["DI_MODE"] = "live"
    r = di.analyze(b"%PDF-1.4 fake"); assert r.source == "refused" and "configured=False" in r.refused_reason
    os.environ["AZURE_DI_ENDPOINT"] = "https://x.cognitiveservices.azure.com"; os.environ["AZURE_DI_KEY"] = "k"
    os.environ["DI_MODE"] = "off";  assert di.analyze(b"%PDF").source == "refused"
    os.environ["DI_MODE"] = "dry";  assert di.analyze(b"%PDF").source == "dry"
    big = b"0" * (di.F0_MAX_BYTES + 1); assert "exceeds F0" in di.analyze(big).refused_reason
    # cache hit path: seed the cache and confirm no live call is attempted
    p = json.loads((Path(__file__).parent / "fixtures" / "di_sample_response.json").read_text())
    import hashlib; sha = hashlib.sha256(b"cached-doc").hexdigest()
    di.save_json(di.CACHE_DIR / f"{sha}.json", p)
    r = di.analyze(b"cached-doc"); assert r.source == "cache" and r.invoice_id.value == "SI-10912"
    # monthly cap
    di.save_json(di.USAGE_PATH, {di._month_key(): {"pages": 449, "calls": 0, "refused": 0, "cached": 0}})
    assert "monthly page cap" in di.analyze(b"another").refused_reason
    print("analyze modes/cache/cap   ok")

if __name__ == "__main__":
    test_parse_response(); test_policy()
    with tempfile.TemporaryDirectory() as t: test_analyze_modes(Path(t))
    print("ALL OK")
