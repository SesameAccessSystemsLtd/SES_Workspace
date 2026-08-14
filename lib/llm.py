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

import os
import logging
import requests as _requests

log = logging.getLogger(__name__)

DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Default models per provider
DEEPSEEK_MODEL   = "deepseek-chat"
GEMINI_MODEL     = "gemini-2.0-flash"
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
    return resp.json()["choices"][0]["message"]["content"]


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
