r"""test_execute_requests_order_status.py — ORDER Status label resolution + read-back in
execute_requests.py. Fake Asana only: no network, no queue files, no ops.db.
Regression for 2026-09-25: 'Delivery Date Confirmed' requests were written as option
1143955334207043 ('⏰On Order - Awaiting CONFIRMATION') instead of 1203944084697376.
Run:  ..\..\.venv\Scripts\python.exe test_execute_requests_order_status.py"""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import execute_requests as m  # noqa: E402

FIELD = "1143955334207040"
AWAITING = "1143955334207043"
CONFIRMED = "1203944084697376"
# Live ORDER Status options read 2026-09-25 (subset), including the traps: a disabled
# 'Awaiting Confirmation' look-alike and emoji-prefixed labels.
LIVE = [
    {"gid": AWAITING, "name": "⏰On Order - Awaiting CONFIRMATION", "enabled": True},
    {"gid": CONFIRMED, "name": "🚚On Order - Delivery Date CONFIRMED", "enabled": True},
    {"gid": "1143996800235964", "name": "Awaiting Confirmation", "enabled": False},
    {"gid": "1171789515832142", "name": "🔥ARRIVED - NEED TO QC", "enabled": True},
    {"gid": "1216591485746812", "name": "AWAITING DISPATCH ", "enabled": True},
]


def eq(a, b, label):
    assert a == b, f"{label}\n  got : {a!r}\n  want: {b!r}"
    print(f"  ok  {label}")


def raises(fn, needle, label):
    try:
        fn()
    except m.EnumResolutionError as exc:
        assert needle in str(exc), f"{label}\n  error: {exc}\n  want substring: {needle!r}"
        print(f"  ok  {label}")
        return
    raise AssertionError(f"{label}: no EnumResolutionError raised")


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


class FakeAsana:
    """Stands in for the `requests` module inside execute_requests."""
    RequestException = OSError

    def __init__(self, options=LIVE, field_name="ORDER Status", field_status=200, readback=None):
        self.options, self.field_name, self.field_status = options, field_name, field_status
        self.readback = readback  # force what the task reads back as (None = echo the PUT)
        self.task_enum, self.calls = {}, []

    def writes(self):
        return [c for c in self.calls if c[0] in ("PUT", "POST")]

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url))
        if "/custom_fields/" in url:
            return Resp(self.field_status, {"data": {"name": self.field_name, "resource_subtype": "enum",
                                                      "enum_options": self.options}})
        gid = url.rsplit("/", 1)[1]
        want = self.readback or self.task_enum.get(gid)
        opt = next((o for o in self.options if o["gid"] == want), None)
        return Resp(200, {"data": {"notes": "", "custom_fields": [
            {"gid": FIELD, "enum_value": {"gid": opt["gid"], "name": opt["name"]} if opt else None}]}})

    def put(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("PUT", url, json))
        cf = (json.get("data") or {}).get("custom_fields") or {}
        if FIELD in cf:
            self.task_enum[url.rsplit("/", 1)[1]] = cf[FIELD]
        return Resp(200, {"data": {}})

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        return Resp(201, {"data": {}})


def use(fake):
    m.requests = fake
    m._ENUM_FIELDS.clear()
    return fake


RS_DETAILS = ("(PO 21669-1582 (exact)); Update delivery date to 2026-09-22; Update ORDER Status to "
              "'On Order - Delivery Date Confirmed'; Update 'Price Ex VAT' to £20.97")
JULY_DETAILS = ("(Matched via PO Number in Local DB); Update delivery date to 2026-07-07; Update ORDER Status "
                "custom field (field GID: 1143955334207040) to 'On Order - Delivery Date Confirmed' "
                "(option GID: 1143955334207043); Skipped price update: Extracted price matches Asana 'Price Ex VAT'.")


def run_request(tmp, name, details, comment="Order confirmation from RS Components"):
    md = Path(tmp) / f"{name}.md"
    md.write_text("---\nFrom: ops_agent\nTo: asana_agent\nType: asana_write\nStatus: PENDING\n---\n\n"
                  "## Request\nAction: update_task\nTask GID: 1218701127739635\n\n"
                  f"Details:\n{details}\n\nComment To Push:\n{comment}\n\n## Context\ntest\n", encoding="utf-8")
    ok, msg = m.process_file(str(md))
    return ok, msg, md.read_text(encoding="utf-8")


# -- parse_actions: labels in, no option GIDs invented ------------------------------------
ops = m.parse_actions(RS_DETAILS)
eq([o for o in ops if o["type"] == "enum_label"],
   [{"type": "enum_label", "gid": FIELD, "field_name": "ORDER Status",
     "label": "On Order - Delivery Date Confirmed", "claimed_enum_gids": []}],
   "parse: 03 wording -> label op, no GID guessed")
eq([o for o in m.parse_actions(JULY_DETAILS) if o["type"] == "enum_label"][0]["claimed_enum_gids"], [AWAITING],
   "parse: pre-Sep inline option GID kept only as a claim")
eq(m.parse_actions("(PO 1 (exact)); Note: Status already Delivery Date CONFIRMED"), [],
   "parse: 'Status already Delivery Date CONFIRMED' note is not a status change")
eq(m.parse_actions("AMENDMENT (Giles overrode proposed action):\nOriginal: Update ORDER Status to "
                   "'On Order - Delivery Date Confirmed'\nAmendment: Update ORDER Status to 'ARRIVED - NEED TO QC'")[0]["type"],
   "invalid", "parse: two different ORDER Status labels -> invalid, not first-wins")
eq(m.parse_actions("please set ORDER Status to confirmed")[0]["type"], "invalid",
   "parse: ORDER Status mentioned without a quoted label -> invalid")
eq(m.parse_actions("Amendment: update order status to 'arrived - need to qc'")[0]["label"], "arrived - need to qc",
   "parse: hand-typed lower-case amendment still yields a label to resolve")

# -- label normalisation --------------------------------------------------------------------
eq(m._norm_label("On Order - Delivery Date Confirmed"), m._norm_label("🚚On Order - Delivery Date CONFIRMED"),
   "norm: emoji/case/punctuation ignored")
assert m._norm_label("Awaiting Confirmation") != m._norm_label("⏰On Order - Awaiting CONFIRMATION")
print("  ok  norm: 'Awaiting Confirmation' stays distinct from 'On Order - Awaiting CONFIRMATION'")

# -- resolve_enum_option against the live field ---------------------------------------------
use(FakeAsana())
eq(m.resolve_enum_option(FIELD, "On Order - Delivery Date Confirmed", "ORDER Status"),
   (CONFIRMED, "🚚On Order - Delivery Date CONFIRMED"), "resolve: Confirmed -> 1203944084697376 (THE regression)")
eq(m.resolve_enum_option(FIELD, "On Order - Awaiting CONFIRMATION", "ORDER Status")[0], AWAITING,
   "resolve: Awaiting -> 1143955334207043")
eq(sum(1 for c in m.requests.calls if "/custom_fields/" in c[1]), 1, "resolve: live field read once per run (cached)")
raises(lambda: m.resolve_enum_option(FIELD, "Awaiting Confirmation", "ORDER Status"), "is disabled",
       "resolve: disabled option refused")
raises(lambda: m.resolve_enum_option(FIELD, "On Order - Shipped", "ORDER Status"), "matches no live option",
       "resolve: unknown label fails loudly")
use(FakeAsana(options=LIVE + [{"gid": "999", "name": "On order - delivery date confirmed!", "enabled": True}]))
raises(lambda: m.resolve_enum_option(FIELD, "On Order - Delivery Date Confirmed", "ORDER Status"), "matches 2 options",
       "resolve: two look-alike options -> ambiguous, fails loudly")
use(FakeAsana(field_name="Stock ACTION"))
raises(lambda: m.resolve_enum_option(FIELD, "On Order - Delivery Date Confirmed", "ORDER Status"), "not 'ORDER Status'",
       "resolve: field GID pointing at another field is refused")

# -- process_file end to end ----------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    fake = use(FakeAsana())
    ok, msg, md = run_request(tmp, "rs", RS_DETAILS)
    eq(ok, True, "process: RS confirmation COMPLETE")
    put = [c for c in fake.calls if c[0] == "PUT"][0][2]["data"]
    eq(put["custom_fields"][FIELD], CONFIRMED, "process: PUT sends 1203944084697376, not 1143955334207043")
    eq(put["due_on"], "2026-09-22", "process: due date still written alongside")
    assert "ORDER Status read back 'On Order - Delivery Date CONFIRMED'" in msg, msg
    print("  ok  process: note records the read-back label")
    msg.encode("cp1252")
    print("  ok  process: note is cp1252-safe (cron stdout cannot crash on the emoji label)")
    assert "Status: COMPLETE" in md

    fake = use(FakeAsana(readback=AWAITING))
    ok, msg, md = run_request(tmp, "readback", RS_DETAILS)
    eq(ok, False, "process: read-back showing Awaiting -> FAILED")
    assert "read-back MISMATCH" in msg and "Status: FAILED" in md, msg
    print("  ok  process: mismatch named in the FAILED note")

    fake = use(FakeAsana())
    ok, msg, md = run_request(tmp, "july", JULY_DETAILS)
    eq((ok, fake.writes()), (False, []), "process: old inline 043 GID -> FAILED with NOTHING written")
    assert "label and GID disagree" in msg, msg

    fake = use(FakeAsana())
    ok, msg, md = run_request(tmp, "unknown", "Update ORDER Status to 'On Order - Shipped'; Update delivery date to 2026-10-01")
    eq((ok, fake.writes()), (False, []), "process: unresolvable label -> FAILED, no date/comment half-applied")
    assert msg.startswith("nothing written") and "Status: FAILED" in md, msg

    fake = use(FakeAsana(field_status=500))
    ok, msg, md = run_request(tmp, "offline", RS_DETAILS)
    eq((ok, fake.writes()), (False, []), "process: field unreadable -> FAILED, nothing written (no fallback GID)")

    fake = use(FakeAsana())
    ok, msg, md = run_request(tmp, "price", "(PO 1 (exact)); Update 'Price Ex VAT' to £12.50")
    eq(ok, True, "process: request without ORDER Status still COMPLETE")
    eq([c for c in fake.calls if "/custom_fields/" in c[1]], [], "process: no field lookup when no status requested")

print("ALL OK")
