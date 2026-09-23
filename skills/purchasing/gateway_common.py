"""
gateway_common.py — shared helpers for the purchasing email gateway.

Owns the things every stage needs but none should re-implement:
  * paths + JSON helpers (atomic writes)
  * HTML → readable text
  * RingCentral "Notes from your call" parsing (subject + body sections)
  * supplier directory (data/suppliers.json) with domain / tag / name lookup
  * learned-rules store (data/gateway_rules.json) — the feedback loop's memory
  * the email archive (data/email_archive/) so any email can be re-parsed later

Rebuilt 2026-09-18 (Claude, for Giles). No Asana writes, no sheet writes here.
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
if not WS.exists():  # allow running the test-suite / sandbox from a repo copy
    WS = Path(__file__).resolve().parents[2]

DATA = WS / "data"
ARCHIVE_DIR = DATA / "email_archive"
RULES_PATH = DATA / "gateway_rules.json"
FEEDBACK_LOG = DATA / "gateway_feedback.jsonl"
SUPPLIERS_PATH = DATA / "suppliers.json"
FETCH_STATE_PATH = DATA / "fetch_state.json"

INTERNAL_DOMAINS = {"sesameaccess.com", "sesame-access.com"}
RINGCENTRAL_DOMAINS = {"ringcentral.com"}

# Senders whose mail is tracking / marketing / platform noise. Seeded from
# Giles' Reject history; learn.py extends this from feedback.
DEFAULT_NOISE_DOMAINS = {
    "bookingplatform.apc-overnight.com": "APC tracking — no action (Giles 2026-08-11)",
    "royalmail.com": "Royal Mail tracking",
    "thedx.co.uk": "DX tracking",
    "members.ebay.com": "eBay / Packlink tracking",
    "best4systems.co.uk": "marketing",
}

GILES_EMAIL = "giles@sesameaccess.com"

# GATEWAY_SHADOW=1 in .hermes/.env: run the whole pipeline INTO THE SHEET ONLY — no ops.db writes,
# no REQUESTS/ files (so nothing reaches Asana), no Dropbox filing. Learned rules (a local JSON)
# still update so the feedback loop can be watched. Giles 2026-09-18: go-live mode until trusted.
def shadow_mode() -> bool:
    return os.environ.get("GATEWAY_SHADOW", "0") == "1"


# --------------------------------------------------------------------------- io
def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: Path | str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path | str, obj) -> None:
    """Atomic write — a crash mid-write never leaves a half file behind."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def append_jsonl(path: Path | str, obj: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


def safe_id(email_id: str) -> str:
    """Graph message ids contain '/', '+', '=' — make a filesystem-safe stem."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", email_id or "")[-120:]


# --------------------------------------------------------------- html → text
_BLOCK_TAGS = r"br|p|div|tr|li|h[1-6]|table|blockquote|section|article"


def html_to_text(raw: str, max_chars: int | None = None) -> str:
    """Readable plain text from an HTML (or already-plain) email body."""
    if not raw:
        return ""
    s = raw
    s = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    s = re.sub(rf"(?i)</?(?:{_BLOCK_TAGS})[^>]*>", "\n", s)
    s = re.sub(r"(?i)</t[dh]>", "\t", s)
    s = re.sub(r"(?s)<[^>]+>", "", s)
    s = _html.unescape(s)
    s = s.replace("\u00a0", " ").replace("\u200b", "")
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = s.strip()
    if max_chars and len(s) > max_chars:
        s = s[:max_chars] + "\n…[truncated]"
    return s


def strip_quoted_history(text: str) -> str:
    """Drop the quoted thread below a reply so the parser sees the new message
    first; the full text is still archived. Conservative: only cuts on the
    standard Outlook/Gmail separators."""
    cut = None
    for pat in (r"\n-{3,}\s*Original Message\s*-{3,}", r"\nFrom:\s.+\nSent:\s", r"\nOn .{5,80} wrote:\n"):
        m = re.search(pat, text)
        if m and (cut is None or m.start() < cut):
            cut = m.start()
    return text[:cut].rstrip() if cut and cut > 200 else text


# ------------------------------------------------------------- RingCentral
RC_SUBJECT_RE = re.compile(
    r"^(?:Notes from your call with|New call from)\s*(?P<label>.*?)\s*"
    r"(?:on\s+(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+(?P<time>\d{1,2}:\d{2}))?\s*$",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"(?:\+44\s?\d{9,10}|\b0\d{3,4}\s?\d{3}\s?\d{3,4}\b|\b0\d{10}\b|\b\d{3}\b(?=\s*$))")


def normalise_phone(p: str) -> str:
    """'01388 770360' / '+441388770360' / '(01388) 770360' → '01388770360'."""
    d = re.sub(r"\D", "", p or "")
    if d.startswith("44") and len(d) >= 11:
        d = "0" + d[2:]
    return d


def parse_ringcentral(subject: str, body_text: str) -> dict:
    """Pull the useful structure out of a RingCentral AI call-notes email.

    Returns {} for non-RC subjects. Keys:
      contact_label  — the label as saved in Giles' RC contacts ("Amanda - Stephenson Gobin")
      phones         — list of normalised phone numbers found in the subject
      extension      — a 3-digit internal extension if that is all we have
      call_date/call_time
      summary, recap, tasks (list[str]), transcript_url
      is_internal    — true when the label points at a Sesame colleague / extension
    """
    m = RC_SUBJECT_RE.match((subject or "").strip())
    if not m:
        return {}
    label = m.group("label") or ""
    phones = []
    ext = None
    for p in PHONE_RE.findall(label):
        n = normalise_phone(p)
        if len(n) >= 10:
            phones.append(n)
        elif len(n) == 3:
            ext = n
    # strip the numbers off to leave the human label
    clean_label = PHONE_RE.sub("", label)
    clean_label = re.sub(r"[\s\-–,]+$", "", clean_label).strip(" -–,")
    clean_label = re.sub(r"^\s*[-–]\s*", "", clean_label).strip()

    out = {
        "contact_label": clean_label,
        "phones": sorted(set(phones)),
        "extension": ext,
        "call_date": m.group("date"),
        "call_time": m.group("time"),
        "summary": "",
        "recap": "",
        "tasks": [],
        "transcript_url": "",
        "is_internal": bool(ext) or bool(re.search(r"\bExt\.?\s*\d{3}\b|Steven Lyons|Purchasing\b", label, re.I)),
    }
    t = body_text or ""
    # Section split. The RC template is: "Notes ... Here's the notes ...:" <summary> "Recap" <recap> "Tasks" <tasks> "↗ View transcript"
    intro = re.search(r"Here[’']s the notes from your call with .*? at \d{1,2}:\d{2}:\s*", t, re.S) \
        or re.search(r"Here[’']s the notes from your call with .*?:\s*\n", t, re.S)
    start = intro.end() if intro else 0
    rec = re.search(r"\n\s*Recap\s*\n", t[start:])
    tas = re.search(r"\n\s*Tasks\s*\n", t[start:])
    end = re.search(r"↗\s*View transcript|Powered by RingCentral", t[start:])
    end_i = start + end.start() if end else len(t)
    if rec:
        out["summary"] = t[start:start + rec.start()].strip()
        rec_end = start + (tas.start() if tas else (end.start() if end else len(t) - start))
        out["recap"] = t[start + rec.end():rec_end].strip()
    else:
        out["summary"] = t[start:end_i].strip()
    if tas:
        tasks_txt = t[start + tas.end():end_i].strip()
        if tasks_txt and tasks_txt.lower() != "none":
            out["tasks"] = [x.strip(" -•\t") for x in re.split(r"\n|(?<=[.;])\s+(?=[A-Z])", tasks_txt) if x.strip(" -•\t")]
    url = re.search(r"https?://\S+transcript\S*", body_text or "", re.I)
    if url:
        out["transcript_url"] = url.group(0)
    return out


# ------------------------------------------------------------- suppliers
_STOP_TOKENS = {"ltd", "limited", "co", "uk", "plc", "the", "and", "group", "supplier", "suppliers",
                "orders", "order", "of", "for", "engineering", "services", "company", "inc"}


def name_tokens(name: str) -> set[str]:
    toks = re.findall(r"[a-z0-9]+", (name or "").lower())
    return {t for t in toks if t not in _STOP_TOKENS and len(t) > 2}


class SupplierDirectory:
    """data/suppliers.json — 412 suppliers from the Asana Supplier project, each
    with gid, name, domain(s), tags, sid. This is the authority for
    'which company is this email/call from'; never guess from a domain token."""

    def __init__(self, path: Path | str | None = None, rules: "RuleStore | None" = None):
        self.rows = load_json(path or SUPPLIERS_PATH, []) or []
        self.by_domain: dict[str, list[dict]] = {}
        self.by_gid: dict[str, dict] = {}
        for s in self.rows:
            self.by_gid[str(s.get("gid"))] = s
            for d in set([s.get("domain")] + list(s.get("all_domains") or [])):
                if d:
                    self.by_domain.setdefault(d.lower(), []).append(s)
        self.rules = rules

    @staticmethod
    def short_name(s: dict) -> str:
        """'Applied Automation Ltd - MayTec profiles' → 'Applied Automation'."""
        n = (s.get("name") or "").split(" - ")[0]
        n = re.sub(r"\s+(Ltd|Limited|PLC|UK)\.?\s*$", "", n, flags=re.I).strip()
        if n.isupper() and len(n) > 4:
            n = n.title()
        return n or (s.get("vendor_field") or "").strip()

    def by_email_domain(self, domain: str) -> list[dict]:
        domain = (domain or "").lower()
        hits = self.by_domain.get(domain, [])
        if not hits and "." in domain:  # noreply.aalco.co.uk → aalco.co.uk
            parent = domain.split(".", 1)[1]
            hits = self.by_domain.get(parent, [])
        if not hits and self.rules:
            gid = self.rules.domain_supplier(domain)
            if gid and gid in self.by_gid:
                hits = [self.by_gid[gid]]
        return hits

    def _idf(self):
        if not hasattr(self, "_idf_cache"):
            import math
            df: dict[str, int] = {}
            for s in self.rows:
                for t in name_tokens(s.get("name", "")):
                    df[t] = df.get(t, 0) + 1
            n = max(1, len(self.rows))
            self._idf_cache = {t: math.log((n + 1) / (c + 1)) + 0.1 for t, c in df.items()}
        return self._idf_cache

    def by_label(self, label: str) -> list[tuple[dict, float]]:
        """Fuzzy: an RC contact label / free-text company name → suppliers.
        Scores IDF-weighted coverage of the supplier's COMPANY tokens (the part
        of the name before ' - '); category tags are deliberately ignored
        because they are shared across whole supplier groups."""
        lt = name_tokens(label)
        if not lt:
            return []
        idf = self._idf()
        scored = []
        for s in self.rows:
            company = (s.get("name") or "").split(" - ")[0]
            ct = name_tokens(company) or name_tokens(s.get("name", ""))
            if not ct:
                continue
            inter = lt & ct
            if not inter:
                continue
            w_all = sum(idf.get(t, 1.0) for t in ct)
            w_hit = sum(idf.get(t, 1.0) for t in inter)
            score = w_hit / w_all if w_all else 0
            # a single generic token ('steel', 'laser') must not carry a match on its own
            if len(inter) == 1 and idf.get(next(iter(inter)), 5) < 2.5 and len(ct) > 1:
                score *= 0.5
            scored.append((s, round(score, 3)))
        scored.sort(key=lambda x: -x[1])
        return [x for x in scored if x[1] >= 0.6][:5]

    def by_phone(self, phone: str) -> dict | None:
        if not self.rules:
            return None
        gid = self.rules.phone_supplier(phone)
        return self.by_gid.get(gid) if gid else None


# ------------------------------------------------------------- learned rules
class RuleStore:
    """data/gateway_rules.json — everything the gateway has been TAUGHT.

    Shape (all keys optional):
      {
        "version": 3, "updated_at": "...",
        "domain_supplier":  {"jajoautomation.co.uk": {"supplier_gid": "…", "note": "…", "learned_from": "…"}},
        "phone_supplier":   {"01388770360": {"supplier_gid": "…", "label": "Amanda - Stephenson Gobin", ...}},
        "sender_task":      {"kerrywayltd@gmail.com": {"task_gid": "…", "until": "2026-12-31", ...}},
        "domain_task":      {"bfssheetmetal.co.uk": {"task_gid": "…", "until": "…"}}   # small suppliers with one live order,
        "phone_task":       {"07810558753": {"task_gid": "…", ...}},
        "domain_default_project": {...},
        "blocked_tasks":    {"1217770225935589": {"reason": "closed out — Giles 2026-07-09"}},
        "noise_domains":    {"bookingplatform.apc-overnight.com": {"reason": "…"}},
        "noise_subject_patterns": [{"pattern": "^Your Packlink parcel", "reason": "…"}],
        "notes":            [ {"text": "any emails from @jajoautomation.co.uk are JAJO PLC orders", "source": "…"} ]
      }
    Rules are additive and human-editable; learn.py writes them, matcher reads them.
    """

    def __init__(self, path: Path | str | None = None):
        # resolved at call time so tests / env overrides (GATEWAY_RULES_PATH) take effect
        self.path = Path(path or os.environ.get("GATEWAY_RULES_PATH") or RULES_PATH)
        self.data = load_json(self.path, None) or {"version": 0}
        for k in ("domain_supplier", "phone_supplier", "sender_task", "phone_task", "domain_task",
                  "blocked_tasks", "noise_domains", "label_supplier"):
            self.data.setdefault(k, {})
        self.data.setdefault("noise_subject_patterns", [])
        self.data.setdefault("notes", [])
        for d, why in DEFAULT_NOISE_DOMAINS.items():
            self.data["noise_domains"].setdefault(d, {"reason": why, "learned_from": "seed"})

    # -- reads
    def domain_supplier(self, domain: str) -> str | None:
        r = self.data["domain_supplier"].get((domain or "").lower())
        return r and r.get("supplier_gid")

    def phone_supplier(self, phone: str) -> str | None:
        r = self.data["phone_supplier"].get(normalise_phone(phone))
        return r and r.get("supplier_gid")

    def label_supplier(self, label: str) -> str | None:
        r = self.data["label_supplier"].get((label or "").strip().lower())
        return r and r.get("supplier_gid")

    def sender_task(self, email: str) -> dict | None:
        r = self.data["sender_task"].get((email or "").lower())
        return r if r and not _expired(r) else None

    def domain_task(self, domain: str) -> dict | None:
        r = self.data["domain_task"].get((domain or "").lower())
        return r if r and not _expired(r) else None

    def phone_task(self, phone: str) -> dict | None:
        r = self.data["phone_task"].get(normalise_phone(phone))
        return r if r and not _expired(r) else None

    def is_blocked_task(self, gid: str) -> str | None:
        r = self.data["blocked_tasks"].get(str(gid))
        return r and (r.get("reason") or "blocked")

    def noise_reason(self, domain: str, subject: str) -> str | None:
        r = self.data["noise_domains"].get((domain or "").lower())
        if r:
            return r.get("reason") or "noise sender"
        for p in self.data["noise_subject_patterns"]:
            try:
                if re.search(p["pattern"], subject or "", re.I):
                    return p.get("reason") or "noise subject"
            except re.error:
                continue
        return None

    # -- writes (learn.py)
    def put(self, bucket: str, key: str, value: dict) -> bool:
        key = key.strip().lower() if bucket != "phone_supplier" and bucket != "phone_task" else normalise_phone(key)
        cur = self.data.setdefault(bucket, {})
        if cur.get(key) == value:
            return False
        value = dict(value)
        value.setdefault("learned_at", now_utc())
        cur[key] = value
        return True

    def add_note(self, text: str, source: str) -> bool:
        if any(n.get("text") == text for n in self.data["notes"]):
            return False
        self.data["notes"].append({"text": text, "source": source, "learned_at": now_utc()})
        return True

    def save(self) -> None:
        self.data["version"] = int(self.data.get("version", 0)) + 1
        self.data["updated_at"] = now_utc()
        save_json(self.path, self.data)


def _expired(rule: dict) -> bool:
    until = rule.get("until")
    return bool(until) and until < datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ------------------------------------------------------------- archive
def archive_email(email: dict) -> Path:
    """Persist the fetched email (body, text, attachments text, metadata) so it
    can be re-parsed after a parser/matcher fix. One file per message."""
    ts = (email.get("date") or "")[:7] or "unknown"
    p = ARCHIVE_DIR / ts / f"{safe_id(email.get('id'))}.json"
    if not p.exists():
        save_json(p, email)
    return p


def archive_parse(email_id: str, received: str, insights: dict) -> None:
    ts = (received or "")[:7] or "unknown"
    p = ARCHIVE_DIR / ts / f"{safe_id(email_id)}.json"
    doc = load_json(p, {}) or {}
    doc["insights"] = insights
    doc["parsed_at"] = now_utc()
    save_json(p, doc)


def find_archived(email_id: str) -> dict | None:
    for p in ARCHIVE_DIR.glob(f"*/{safe_id(email_id)}.json"):
        return load_json(p, None)
    return None


# ------------------------------------------------------------- misc
# 4-digit Sesame job numbers (1037, 1595, 1597 …) — only when written the Sesame way:
# "1597 - Forrest Road", "1597.7110", "job 1597", "for 1597", "21615-1597"
JOB_RE = re.compile(r"(?:\b(?:job|project|for|re)\s*[:#]?\s*|-|\b)(1[0-9]{3})(?=\s*[-–.]\s*[A-Za-z0-9]|\b(?!\.\d))", re.I)
PO_RE = re.compile(r"\b(2\d{4})(?:-(\d{4}|\d{5,6}))?\b")  # 21615-1597 / 21615-70926 / bare 21615
SQR_RE = re.compile(r"\bSQR[\s\-#]*(\d{4,6})\b", re.I)


def extract_refs(text: str, subject: str = "") -> dict:
    """Deterministic reference extraction (runs alongside the LLM, never instead of it).
    Job numbers are taken from the subject, from PO suffixes, and from the body only
    in the 'NNNN - Name' / 'NNNN.' forms — a bare 4-digit number in a body is more
    often a price or a part of a phone number than a job."""
    text = text or ""
    pos = [m.group(0) for m in PO_RE.finditer(f"{subject}\n{text}")]
    pos = [p for p in pos if "-" in p or len(p) == 5]
    jobs = set(JOB_RE.findall(subject or ""))
    for p in pos:
        m = re.fullmatch(r"2\d{4}-(1\d{3})", p)
        if m:
            jobs.add(m.group(1))
    for m in re.finditer(r"\b(1[0-9]{3})\s*[-–.]\s*[A-Za-z]", text):
        jobs.add(m.group(1))
    return {
        "po_numbers": sorted(set(pos), key=lambda x: (-len(x), x)),
        "sqr_numbers": sorted({f"SQR-{m.group(1)}" for m in SQR_RE.finditer(f"{subject}\n{text}")}),
        "job_numbers": sorted(jobs),
    }


def fmt_date(iso: str) -> str:
    """'2026-09-17T10:19:12Z' → '17 Sep 2026'."""
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d %b %Y").lstrip("0")
    except Exception:
        return iso or ""
