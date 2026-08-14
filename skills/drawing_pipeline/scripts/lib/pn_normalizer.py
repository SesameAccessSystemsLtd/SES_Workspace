"""
lib/pn_normalizer.py
Part number normalization for drawing BOM extraction.
Rules documented in PN_EDGE_CASES.md.

Design: keep the base form (whitespace-collapsed, leading-noise stripped) as
the highest-priority candidate so that PNs which EXACTLY match their PC
sesame_pn (including underscore specs like _M_M8_70daN, w-suffix welded parts,
_MIR mirror parts) are always tried first. Transformation rules are applied as
progressive fallbacks in order of increasing deviation from the raw text.
"""
import json
import re
from pathlib import Path

_ALIASES_PATH = Path(__file__).parent.parent.parent / "pn_aliases.json"


def load_aliases() -> dict:
    if _ALIASES_PATH.exists():
        with open(_ALIASES_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------------------
# Rule 2: multi-PN cell splitting
# ---------------------------------------------------------------------------

def split_multi_pn(cell: str, job_prefix: str) -> list[str]:
    """Rule 2: split cell containing multiple concatenated PNs.
    e.g. "1575.6000.A.0551575.6000.A.056" -> ["1575.6000.A.055", "1575.6000.A.056"]
    Also handles newline-concatenated forms like "1575.1005.B1w\\n1575.1005.B25w".
    """
    pat = re.escape(job_prefix) + r'\.'
    if len(re.findall(pat, cell)) > 1:
        parts = re.split(rf'(?={pat})', cell)
        return [p.strip() for p in parts if p.strip()]
    return [cell]


# ---------------------------------------------------------------------------
# Base normalizer (Rule 1 + whitespace)
# ---------------------------------------------------------------------------

def _base(raw_pn: str, job_prefix: str) -> str:
    """Rule 1: strip leading noise; collapse whitespace. No other transforms.
    This is the highest-priority candidate — exact match against PC sesame_pn.
    """
    pn = raw_pn.strip()
    # Rule 1: discard everything before the first occurrence of {job_prefix}.
    # Handles "11575.xxx" (extra digit) and "2 1575.xxx" (NBSS qty prefix) equally.
    idx = pn.find(job_prefix + ".")
    if idx > 0:
        pn = pn[idx:]
    elif idx < 0:
        return ""
    return re.sub(r'\s+', '', pn)


# ---------------------------------------------------------------------------
# Individual rule transforms (each returns new string or None if no change)
# ---------------------------------------------------------------------------

def _r3(pn: str):
    """Rule 3: truncate at first underscore."""
    u = pn.find("_")
    return pn[:u] if u > 0 else None


def _r4(pn: str):
    """Rule 4: truncate to first 4 dot-separated components."""
    parts = pn.split(".")
    return ".".join(parts[:4]) if len(parts) > 4 else None


def _r5(pn: str):
    """Rule 5: strip UPPERCASE trailing letters after digits in last component.
    Uppercase = description bleed (e.g. 019A from 'Angle', 102S from 'Sprocket').
    Lowercase w-suffix (welded) is preserved: B8w stays B8w.
    """
    parts = pn.split(".")
    last = parts[-1]
    fixed = re.sub(r'(\d+)[A-Z]+$', r'\1', last)
    if fixed and fixed != last:
        return ".".join(parts[:-1] + [fixed])
    return None


def _r6(pn: str):
    """Rule 6: zero-pad short purely-numeric last component to 3 digits.
    e.g. 1575.1016.1.3 -> 1575.1016.1.003
    """
    parts = pn.split(".")
    last = parts[-1]
    if re.fullmatch(r'\d+', last) and 0 < len(last) < 3:
        return ".".join(parts[:-1] + [last.zfill(3)])
    return None


def _rule8_variants(pn: str) -> list[str]:
    """Rule 8: generate progressive revision-stripping fallback candidates.
    Operates on the 3rd dot-component (revision field).
    e.g. "1575.1006.A1w.Bellows" -> ["1575.1006.A1.Bellows", "1575.1006.A.Bellows"]
    """
    parts = pn.split(".")
    if len(parts) < 3:
        return []
    rev = parts[2]
    variants = []

    # Step 1: strip trailing 'w' or '_MIR'
    step1 = re.sub(r'(w|_MIR)$', '', rev)
    if step1 and step1 != rev:
        variants.append(".".join(parts[:2] + [step1] + parts[3:]))
        # Step 2: strip trailing digits from the trimmed revision
        step2 = re.sub(r'\d+$', '', step1)
        if step2 and step2 != step1:
            variants.append(".".join(parts[:2] + [step2] + parts[3:]))
    else:
        # No w/_MIR — try stripping trailing digits directly
        step2 = re.sub(r'\d+$', '', rev)
        if step2 and step2 != rev:
            variants.append(".".join(parts[:2] + [step2] + parts[3:]))

    return variants


# ---------------------------------------------------------------------------
# candidates() — full fallback chain
# ---------------------------------------------------------------------------

def candidates(raw_pn: str, job_prefix: str, aliases: dict | None = None) -> list[str]:
    """Return lookup candidates in priority order.

    Priority (fewest transforms first):
      1. base (Rule 1 + whitespace only) — exact match
      2. Rule 5 on base (uppercase bleed strip)
      3. Rule 4 on base (truncate to 4 components)
      4. Rule 5 on Rule 4
      5. Rule 3 on base (strip at underscore)
      6. Rule 5 on Rule 3
      7. Rule 4 on Rule 3
      8. Rule 3 on Rule 4 (if different from 7)

    For each of the above:
      - Rule 8 variants (revision stripping)
      - Rule 6 variants (zero-pad short numeric last component)

    Rule 9 (aliases) applied to every candidate before adding.
    """
    if aliases is None:
        aliases = {}

    b = _base(raw_pn, job_prefix)
    if not b:
        return []

    seen: set[str] = set()
    result: list[str] = []

    def add(pn: str | None) -> None:
        if not pn:
            return
        pn = aliases.get(pn, pn)
        if pn not in seen:
            seen.add(pn)
            result.append(pn)

    def expand(pn: str) -> None:
        """Add pn plus its Rule 6 and Rule 8 variants."""
        add(pn)
        add(_r6(pn))
        for v in _rule8_variants(pn):
            add(v)
            add(_r6(v))

    # Build forms in increasing-transform order
    # 1. Base
    expand(b)

    # 2. Rule 5 on base
    r5b = _r5(b)
    if r5b:
        expand(r5b)

    # 3. Rule 4 on base (5+ component PNs truncated)
    r4b = _r4(b)
    if r4b:
        expand(r4b)
        # 4. Rule 5 on Rule 4
        r5r4b = _r5(r4b)
        if r5r4b:
            expand(r5r4b)

    # 5. Rule 3 on base (strip underscore)
    r3b = _r3(b)
    if r3b:
        expand(r3b)
        # 6. Rule 5 on Rule 3
        r5r3b = _r5(r3b)
        if r5r3b:
            expand(r5r3b)
        # 7. Rule 4 on Rule 3
        r4r3b = _r4(r3b)
        if r4r3b:
            expand(r4r3b)
    elif r4b:
        # Rule 3 on Rule 4 (different path when base needed R4 first)
        r3r4b = _r3(r4b)
        if r3r4b:
            expand(r3r4b)
            r5r3r4b = _r5(r3r4b)
            if r5r3r4b:
                expand(r5r3r4b)

    return result


# ---------------------------------------------------------------------------
# normalize() — convenience for producing a single stable key
# ---------------------------------------------------------------------------

def normalize(raw_pn: str, job_prefix: str) -> str:
    """Return the primary (highest-priority) candidate, or '' if unrecognised."""
    c = candidates(raw_pn, job_prefix)
    return c[0] if c else ""
