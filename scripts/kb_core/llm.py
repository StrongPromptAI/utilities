"""LLM client dispatch.

Two entry points coexist during the plan-26-5-21 transition:

- `complete_with_fallback(prompt, ...)` — the new path. Reads primary/backup
  from kb_config. Tries primary; on HTTP 5xx / timeout / 429, falls back to
  backup once. Any other error raises loudly. Used by `summarize.py`.

- `complete(prompt, ...)` — legacy path. Routes to LM Studio (local) or z.ai
  (cloud Anthropic-compatible) based on `LM_STUDIO_URL`. Used by harvest.py,
  quotes.py, synthesis.py — modules slated for deletion in Phase 4.

Provider strings supported by the new path:
  "openrouter" — base_url = https://openrouter.ai/api/v1, key from openrouter
  "requesty"   — base_url = https://router.requesty.ai/v1, key from requesty
  "anthropic"  — direct Anthropic Messages API, key from ANTHROPIC_API_KEY
  "openai"     — direct OpenAI, key from OPENAI_API_KEY (not currently used)
  "zai"        — z.ai Anthropic-compatible, key from z.ai
"""

import json
import os
from typing import Tuple

import httpx
from openai import APIError, APIStatusError, APITimeoutError, OpenAI

from .config import (
    BACKUP_LLM_MODEL,
    BACKUP_LLM_PROVIDER,
    BACKUP_LLM_URL,
    LM_STUDIO_URL,
    PRIMARY_LLM_MODEL,
    PRIMARY_LLM_PROVIDER,
    PRIMARY_LLM_URL,
    SUMMARY_MODEL,
)


# ---------------------------------------------------------------------------
# New path — complete_with_fallback (plan 26-5-21)
# ---------------------------------------------------------------------------

_KEYS_PATH = os.path.expanduser("~/.config/keys.json")


def _load_key(provider: str) -> str:
    keys = json.load(open(_KEYS_PATH))
    if provider == "openrouter":
        return keys["openrouter"]
    if provider == "requesty":
        return keys["requesty"]
    if provider == "anthropic":
        return keys["ANTHROPIC_API_KEY"]
    if provider == "openai":
        return keys["OPENAI_API_KEY"]
    if provider == "zai":
        return keys["z.ai"]
    raise ValueError(f"Unknown LLM provider: {provider!r}")


def _is_transient(exc: Exception) -> bool:
    """5xx, timeout, or 429 — try the backup once."""
    if isinstance(exc, APITimeoutError):
        return True
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code >= 500 or exc.status_code == 429
    return False


def _call_anthropic(url: str, model: str, prompt: str, max_tokens: int, key: str) -> str:
    import anthropic

    kwargs = {"api_key": key}
    if url:
        kwargs["base_url"] = url
    client = anthropic.Anthropic(**kwargs)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


def _call_openai_compat(url: str, model: str, prompt: str, max_tokens: int, temperature: float, key: str) -> str:
    client = OpenAI(base_url=url, api_key=key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message.content.strip()


def _call_one(provider: str, url: str, model: str, prompt: str, max_tokens: int, temperature: float) -> str:
    key = _load_key(provider)
    if provider in ("anthropic", "zai"):
        return _call_anthropic(url, model, prompt, max_tokens, key)
    if provider in ("openrouter", "requesty", "openai"):
        return _call_openai_compat(url, model, prompt, max_tokens, temperature, key)
    raise ValueError(f"Unknown LLM provider: {provider!r}")


def complete_with_fallback(
    prompt: str,
    *,
    max_tokens: int = 4000,
    temperature: float = 0.3,
) -> Tuple[str, str]:
    """Single-turn completion against primary; falls back to backup on transient errors.

    Returns (content, model_used). The model_used string is the model identifier
    that actually produced the response — callers persist this on `meeting_summaries`.

    Raises on non-transient failures from primary, or any failure from backup.
    """
    if not PRIMARY_LLM_URL or not PRIMARY_LLM_MODEL or not PRIMARY_LLM_PROVIDER:
        raise RuntimeError(
            "Primary LLM not configured. Run plan 26-5-21 Phase 1 migration to add "
            "primary_llm_url / primary_llm_model / primary_llm_provider to kb_config."
        )

    try:
        content = _call_one(
            PRIMARY_LLM_PROVIDER, PRIMARY_LLM_URL, PRIMARY_LLM_MODEL,
            prompt, max_tokens, temperature,
        )
        return content, PRIMARY_LLM_MODEL
    except Exception as primary_exc:
        if not _is_transient(primary_exc):
            raise
        if not BACKUP_LLM_URL or not BACKUP_LLM_MODEL or not BACKUP_LLM_PROVIDER:
            raise RuntimeError(
                f"Primary failed with transient error ({primary_exc}) and no backup configured."
            ) from primary_exc
        print(f"[llm] primary failed ({type(primary_exc).__name__}); trying backup {BACKUP_LLM_MODEL}")
        content = _call_one(
            BACKUP_LLM_PROVIDER, BACKUP_LLM_URL, BACKUP_LLM_MODEL,
            prompt, max_tokens, temperature,
        )
        return content, BACKUP_LLM_MODEL


# ---------------------------------------------------------------------------
# Legacy path — complete() (deleted in plan 26-5-21 Phase 4)
# ---------------------------------------------------------------------------

def _is_zai(url: str) -> bool:
    return "api.z.ai" in (url or "")


def _zai_complete(prompt: str, max_tokens: int) -> str:
    import anthropic
    key = json.load(open(_KEYS_PATH))["z.ai"]
    client = anthropic.Anthropic(api_key=key, base_url="https://api.z.ai/api/anthropic")
    resp = client.messages.create(
        model=SUMMARY_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


def complete(prompt: str, max_tokens: int = 400, temperature: float = 0.3) -> str:
    """Legacy single-turn completion. Routes to LM Studio or z.ai."""
    if _is_zai(LM_STUDIO_URL):
        return _zai_complete(prompt, max_tokens=max_tokens)

    client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")
    response = client.chat.completions.create(
        model=SUMMARY_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return response.choices[0].message.content.strip()
