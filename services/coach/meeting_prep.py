"""meeting_prep — live public-review mining for the coach's mine_practice_reviews tool.

The canonical engine, ported from the roadmap chat (`hj_roadmap/app/backend/meeting_prep.py`).
Given a practice/doctor name, it mines public reviews into cited, validated patient-experience
FACTS — it does NOT synthesize the verdict or opener. That synthesis is the consumer's job:
in the coach, GLM (the agentic model) reads the tool_result facts block + the surgeon
economics it already retrieves and writes the strain read + opener in its streamed answer.

The load-bearing, drift-prone logic (search/extract prompts, the schema, the citation-
integrity check) lives here once. The coach-side cache wrapper + block rendering live in
`review_cache.py` (DB-aware) — kept OUT of this module so the engine stays self-contained.

The goal is not "scrape reviews" — it is evidence for ONE question: how much is this
reputation propped up by manual staff touchpoints, and is that model straining?

Pipeline (the "advanced" tier — best, expensive):
  search  → Grok web-grounded (`:online`) → freeform report + source-URL annotations.
            Web-grounded models cite well but fill strict JSON poorly → stays freeform.
  extract → a strong model turns the report into structured, themed signals, picking each
            source_url FROM THE REAL annotation list. We then drop any signal whose URL
            isn't in that set (citation integrity).

Reaches the LIVE WEB via OpenRouter — independent of the chat model (GLM/z.ai has no live
web search and doesn't need one; this tool is the web lane). Raises (never sys.exit /
never logs) — callers decide how to surface failures.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
KEYS_JSON = Path("~/.config/keys.json").expanduser()

# Model tiers. Constants here are the single default; callers may override per call.
DEFAULT_SEARCH_MODEL = "x-ai/grok-4.3:online"          # web-grounded live search ("advanced")
# Extract is NOT pure transcription: it cuts the freeform report into the five thesis themes
# + honest sentiment (esp. the subtle "praise crediting staff = touchpoint_dependence,
# positive" inversion). A weak model under-extracts the touchpoint signals and starves the
# read, so extract owns a strong model too — fidelity > cost, the fetch is cached.
DEFAULT_EXTRACT_MODEL = "anthropic/claude-opus-4.8"

# Evidence categories — all serving the touchpoint-dependence / reputation-strain question.
THEMES = (
    "touchpoint_dependence",   # praise crediting human attention → the labor cost behind the rating
    "communication_crack",     # can't reach office / no callback / rushed → erosion leading-indicator
    "postop_uncertainty",      # no follow-up / "didn't know what was normal" → the gap THJ fills
    "throughput_strain",       # waits growing / harder to book → the volume ceiling approaching
    "incumbent_tech",          # a named patient app/portal in use → competitive frame
    "other",
)


def _openrouter_key(api_key: str | None = None) -> str:
    """Env first (deploy), then keys.json (dev). Raise if neither — fail closed."""
    if api_key:
        return api_key
    env = os.environ.get("OPENROUTER_API_KEY")
    if env:
        return env
    if KEYS_JSON.exists():
        key = json.loads(KEYS_JSON.read_text()).get("openrouter")
        if key:
            return key
    raise RuntimeError("No OpenRouter key — set OPENROUTER_API_KEY or ~/.config/keys.json → openrouter.")


async def _chat(
    model: str,
    messages: list[dict],
    *,
    api_key: str,
    max_tokens: int,
    temperature: float,
    json_schema: dict | None = None,
    timeout: float = 120.0,
) -> tuple[dict, float]:
    """Single chat/completions call. Returns (message_dict, cost_usd). Raises on failure.

    Non-streaming on purpose: streaming almost always strips the `annotations`
    (the source URLs) we depend on.
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "usage": {"include": True},  # ask OpenRouter to return per-call cost
    }
    if json_schema is not None:
        body["response_format"] = {"type": "json_schema", "json_schema": json_schema}

    last_err: str | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(3):
            try:
                r = await client.post(OPENROUTER_URL, json=body, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    msg = data["choices"][0]["message"]
                    cost = (data.get("usage") or {}).get("cost", 0.0) or 0.0
                    return msg, float(cost)
                if r.status_code in (401, 403):
                    raise RuntimeError(f"OpenRouter auth rejected ({r.status_code}) for '{model}'.")
                if r.status_code == 404:
                    raise RuntimeError(f"Model '{model}' not found on OpenRouter.")
                last_err = f"{r.status_code}: {r.text[:200]}"
            except (httpx.TimeoutException, httpx.TransportError, KeyError, ValueError) as exc:
                last_err = repr(exc)
            if attempt < 2:
                import asyncio
                await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"OpenRouter call to '{model}' failed after 3 attempts: {last_err}")


def _extract_sources(message: dict) -> list[dict]:
    """Pull source URLs from a web-grounded reply's annotations.

    Handles both shapes: flat Perplexity-style `{url, title}` and OpenRouter's
    web-plugin `{type:'url_citation', url_citation:{url,title}}`.
    """
    sources: list[dict] = []
    seen: set[str] = set()
    for ann in message.get("annotations") or []:
        if not isinstance(ann, dict):
            continue
        url = ann.get("url")
        title = ann.get("title", "")
        nested = ann.get("url_citation")
        if not url and isinstance(nested, dict):
            url = nested.get("url")
            title = nested.get("title", "")
        if url and url not in seen:
            seen.add(url)
            sources.append({"url": url, "title": title or ""})
    return sources


async def _search_reviews(practice: str, city: str | None, *, model: str, api_key: str) -> tuple[str, list[dict], float]:
    """Return (freeform_report, sources, cost). Sources are the real annotation URLs."""
    where = f" in {city}" if city else ""
    prompt = (
        f'Research the public patient reviews for the orthopedic practice/surgeon "{practice}"{where}, '
        f"to answer ONE question for a sales brief: how much is this practice's reputation propped up "
        f"by manual staff effort, and is that model straining? Use only real, public sources "
        f"(Google reviews / Google Maps, Healthgrades, Yelp, US News).\n\n"
        f"Report, in this order:\n"
        f"1. IDENTITY: the exact practice/clinic and Google Business listing this resolves to "
        f"(the name may differ from the search term — a surgeon often practices under a group's name).\n"
        f"2. The Google star rating and total number of Google reviews for that listing. Add the "
        f"Healthgrades rating if readily available. State a value only if a source actually shows it; "
        f"if you cannot find the Google rating, say so explicitly.\n"
        f"3. EVIDENCE, quoted verbatim from real reviews, for each of the following — these matter "
        f"even when the overall rating is high:\n"
        f"   a. PRAISE THAT CREDITS HUMAN ATTENTION — patients thanking staff for personal calls, an "
        f"attentive nurse, hand-holding, walking them through every step. (This reveals the manual "
        f"labor behind a good rating — capture it even though it is positive.)\n"
        f"   b. COMMUNICATION FRICTION — couldn't reach the office, no callback, long holds/waits, "
        f"felt rushed, unanswered questions.\n"
        f"   c. POST-OP UNCERTAINTY — no follow-up, 'didn't know what was normal,' recovery confusion.\n"
        f"   d. THROUGHPUT STRAIN — waits getting longer, harder to book than before, 'used to be more "
        f"personal.'\n"
        f"   e. ANY NAMED patient app or portal the practice uses.\n\n"
        f"Be thorough: consult several sources (Google, Healthgrades, Yelp, US News, Vitals) and "
        f"surface as many DISTINCT real review snippets as you can across the categories — breadth "
        f"matters more than one example. Quote patients' own words. Do NOT invent, embellish, or "
        f"paraphrase reviews into fiction. If a category has no real review, leave it out. Report "
        f"only what the sources actually say."
    )
    msg, cost = await _chat(
        model, [{"role": "user", "content": prompt}],
        api_key=api_key, max_tokens=4096, temperature=0.2, timeout=120.0,
    )
    report = (msg.get("content") or "").strip()
    sources = _extract_sources(msg)
    if not report:
        raise RuntimeError(f"Search model '{model}' returned an empty report.")
    return report, sources, cost


_EXTRACT_SCHEMA = {
    "name": "review_signals",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "rating": {"type": ["string", "null"], "description": "Google star rating, e.g. '4.9', or null if not stated."},
            "review_count": {"type": ["string", "null"], "description": "Total Google review count, e.g. '430', or null."},
            "healthgrades_rating": {"type": ["string", "null"], "description": "Healthgrades rating or null."},
            "signals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "quote": {"type": "string", "description": "The patient's own words (verbatim from the report). No fabrication."},
                        "theme": {"type": "string", "enum": list(THEMES)},
                        "sentiment": {"type": "string", "enum": ["critical", "mixed", "positive"],
                                      "description": "What the quote actually expresses. Tag honestly; never label praise as a complaint."},
                        "source_url": {"type": "string", "description": "MUST be copied verbatim from the provided SOURCES list — the one backing this snippet."},
                    },
                    "required": ["quote", "theme", "sentiment", "source_url"],
                },
            },
        },
        "required": ["rating", "review_count", "healthgrades_rating", "signals"],
    },
}


async def _extract_signals(report: str, sources: list[dict], *, model: str, api_key: str) -> tuple[dict, float]:
    src_list = "\n".join(f"[{i}] {s['url']}  {s['title']}".rstrip() for i, s in enumerate(sources, 1)) or "(none)"
    system = (
        "You convert a web-research report on a practice's public reviews into structured, cited "
        "evidence for ONE assessment: how dependent the practice's reputation is on manual staff "
        "touchpoints, and whether that model is straining. Rules:\n"
        "(1) Quotes must be the patient's own words from the report — never invent or embellish.\n"
        "(2) Every signal's source_url MUST be one of the URLs in the provided SOURCES list, copied "
        "verbatim; if you cannot attribute a snippet to a listed source, omit it.\n"
        "(3) Classify each signal's `theme`:\n"
        "    • touchpoint_dependence — PRAISE that credits human attention (personal calls, an "
        "attentive nurse, hand-holding). Capture these even though positive: they reveal the manual "
        "labor behind the rating, which is the core thing we're measuring.\n"
        "    • communication_crack — couldn't reach office, no callback, holds/waits, felt rushed, "
        "unanswered questions.\n"
        "    • postop_uncertainty — no follow-up, 'didn't know what was normal,' recovery confusion.\n"
        "    • throughput_strain — waits getting longer, harder to book, 'used to be more personal.'\n"
        "    • incumbent_tech — a named patient app/portal in use.\n"
        "    • other — anything else worth noting.\n"
        "(4) Tag `sentiment` honestly by what the quote says (touchpoint_dependence is usually "
        "positive — that's expected and correct). NEVER relabel praise as a complaint.\n"
        "(5) Aim for 3–6 signals when the report supports it, spanning the categories present; never "
        "pad beyond what the report actually contains.\n"
        "(6) Use null for any rating/count the report does not actually state."
    )
    user = f"SOURCES:\n{src_list}\n\nREPORT:\n{report}"
    msg, cost = await _chat(
        model,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        api_key=api_key, max_tokens=2048, temperature=0, json_schema=_EXTRACT_SCHEMA, timeout=60.0,
    )
    try:
        parsed = json.loads(msg.get("content") or "{}")
    except ValueError as exc:
        raise RuntimeError(f"Extract model '{model}' did not return valid JSON.") from exc
    return parsed, cost


def _validate_signals(parsed: dict, sources: list[dict]) -> tuple[list[dict], int]:
    """Drop any signal whose source_url isn't a real annotation URL. Returns (kept, dropped)."""
    real = {s["url"] for s in sources}
    kept, dropped = [], 0
    for sig in parsed.get("signals") or []:
        if isinstance(sig, dict) and sig.get("source_url") in real and sig.get("quote"):
            kept.append(sig)
        else:
            dropped += 1
    return kept, dropped


async def fetch_review_insights(
    practice: str,
    city: str | None = None,
    *,
    search_model: str = DEFAULT_SEARCH_MODEL,
    extract_model: str = DEFAULT_EXTRACT_MODEL,
    api_key: str | None = None,
) -> dict:
    """Mine a practice's public reviews into cited patient-experience FACTS.

    Returns: {practice, city, rating, review_count, healthgrades_rating,
              signals:[{quote, theme, sentiment, source_url}], sources, dropped_signals, cost_usd}.
    Raises RuntimeError on hard failures (missing key, dead search, bad JSON).
    """
    if not practice or not practice.strip():
        raise ValueError("A practice/doctor name is required.")
    key = _openrouter_key(api_key)

    report, sources, c1 = await _search_reviews(practice, city, model=search_model, api_key=key)
    parsed, c2 = await _extract_signals(report, sources, model=extract_model, api_key=key)
    signals, dropped = _validate_signals(parsed, sources)

    return {
        "practice": practice,
        "city": city,
        "rating": parsed.get("rating"),
        "review_count": parsed.get("review_count"),
        "healthgrades_rating": parsed.get("healthgrades_rating"),
        "signals": signals,
        "sources": sources,
        "dropped_signals": dropped,
        "cost_usd": round(c1 + c2, 4),
    }
