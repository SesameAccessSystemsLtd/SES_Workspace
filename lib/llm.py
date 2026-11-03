"""
lib/llm.py — SES LLM provider chain

Priority: DeepSeek → Gemini → Anthropic

Usage:
    from lib.llm import complete

    text = complete(
        prompt="Analyse these emails...",
        system="You are a procurement assistant.",
        max_tokens=2048,
        temperature=0.1,
    )
"""

# Must run before any provider SDK import (google.genai / anthropic pull
# pydantic_core). If the launching interpreter can't load pydantic_core, this
# re-execs the whole command under the SES_Workspace venv.
#
# Coverage is scoped to scripts that import `llm`: today the gateway parser
# (skills/purchasing/02_llm_parser.py) and the daily brief
# (skills/daily_brief/daily_brief.py), plus any future importer automatically.
# Scripts that call providers WITHOUT importing `llm` are NOT covered here and
# must guard themselves.
import os
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
# Guard the IMPORT only — a missing venv_guard must not break importing llm. The
# guard CALL is intentionally left OUTSIDE the except so its errors (including a
# failed re-exec) surface rather than being silently swallowed.
try:
    from venv_guard import ensure_venv_interpreter as _ensure_venv_interpreter
except Exception:
    _ensure_venv_interpreter = None
if _ensure_venv_interpreter is not None:
    _ensure_venv_interpreter()

import logging
import requests as _requests

log = logging.getLogger(__name__)

DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Local usage telemetry (these providers are called OUTSIDE Hermes, so their tokens
# are invisible to Hermes state.db — this JSONL makes their cost measurable).
USAGE_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "llm_usage_log.jsonl")


def _log_usage(provider, model, usage):
    """Append one usage record. Best-effort — never break a call over logging."""
    try:
        import json as _json, datetime as _dt
        rec = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "provider": provider,
            "model": model,
            "prompt_tokens": (usage or {}).get("prompt_tokens")
                or (usage or {}).get("input_tokens") or 0,
            "completion_tokens": (usage or {}).get("completion_tokens")
                or (usage or {}).get("output_tokens") or 0,
            "cache_hit_tokens": (usage or {}).get("prompt_cache_hit_tokens") or 0,
            "cache_miss_tokens": (usage or {}).get("prompt_cache_miss_tokens") or 0,
        }
        os.makedirs(os.path.dirname(USAGE_LOG), exist_ok=True)
        with open(USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec) + "\n")
    except Exception as e:
        log.warning("llm: usage log failed: %s", e)

# Default models per provider
DEEPSEEK_MODEL   = "deepseek-chat"
GEMINI_MODEL     = "gemini-2.5-flash"   # gemini-2.0-flash was retired (404) — verified live 2026-11-03
ANTHROPIC_MODEL  = "claude-haiku-4-5-20251001"


def _try_deepseek(prompt: str, system: str, max_tokens: int, temperature: float) -> str:
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")  # read at call time (import-order safe)
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY not set")
    resp = _requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    _log_usage("deepseek", DEEPSEEK_MODEL, data.get("usage"))
    return data["choices"][0]["message"]["content"]


def _try_gemini(prompt: str, system: str, max_tokens: int, temperature: float) -> str:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # read at call time (import-order safe)
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    from google import genai
    from google.genai import types as gtypes
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=gtypes.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            temperature=temperature,
        ),
    )
    try:
        um = response.usage_metadata
        _log_usage("gemini", GEMINI_MODEL, {
            "prompt_tokens": getattr(um, "prompt_token_count", 0),
            "completion_tokens": getattr(um, "candidates_token_count", 0),
        })
    except Exception:
        pass
    return response.text.strip()


def _try_anthropic(prompt: str, system: str, max_tokens: int, temperature: float) -> str:
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")  # read at call time (import-order safe)
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    _log_usage("anthropic", ANTHROPIC_MODEL, getattr(response, "usage", None))
    return response.content[0].text.strip()


_CHAIN = [
    ("deepseek",  _try_deepseek),
    ("gemini",    _try_gemini),
    ("anthropic", _try_anthropic),
]


def complete(
    prompt: str,
    system: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.1,
) -> str:
    """
    Call the LLM chain: DeepSeek → Gemini → Anthropic.
    Returns the text response. Raises RuntimeError if all providers fail.
    """
    last_err = None
    for name, fn in _CHAIN:
        try:
            result = fn(prompt, system, max_tokens, temperature)
            if name != "deepseek":
                log.info("llm: used fallback provider '%s'", name)
            return result
        except Exception as e:
            log.warning("llm: provider '%s' failed: %s", name, e)
            last_err = e

    raise RuntimeError(f"All LLM providers failed. Last error: {last_err}")
