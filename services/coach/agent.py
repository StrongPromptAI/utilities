"""Agentic coach core — GLM 5.2 via z.ai (Anthropic Messages), tool-calling loop.

The model orchestrates retrieval: it emits `tool_use` blocks, we run the tools
(`tools.py`, fail-closed to the coach read boundary) and return `tool_result`, looping
until it emits the final answer (streamed to the rep). Principle-driven, DME-first
persona (COACH_BRAIN §3); the floor (both sales methods + DME mandate + the value-prop
spine) rides every turn.

Embed-source-agnostic: the caller passes `embed_fn(query) -> vector` + a dict-row DB
connection, so runtime (shared-svcs nomic 768d) and tests (local ONNX) share one path
and one vector space (the space build_brain ingested into).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import AsyncGenerator, Callable

import httpx

import review_cache
import tools

ZAI_URL = "https://api.z.ai/api/anthropic/v1/messages"
MODEL = os.environ.get("COACH_MODEL", "glm-5.2")
MAX_TOOL_HOPS = 6

# The value-prop spine (floor). Local: the THJ registry file. Deploy: this file is NOT
# in the container — the floor must be loaded from the DB (a coach_floor row) at startup.
# (TODO Phase 3 deploy: persist the floor in kb Postgres; load it here instead of a path.)
VALUE_REGISTRY_PATH = os.environ.get(
    "COACH_VALUE_REGISTRY_PATH", "~/repo_docs/thj/registries/STAKEHOLDER_VALUE_REGISTRY.md"
)

_PERSONA = """You are a sales coach for DME (durable medical equipment) reps selling post-surgical \
recovery equipment to surgeons, clinics, and DME providers. You have two jobs: make the rep better \
at winning and growing accounts, and help them understand how the buyer (surgeon, DME provider, PT, \
patient) actually thinks.

How you work:
- This is a CONVERSATION, not a memo. Lead with the single most useful point in a sentence or two, \
then stop. Offer to go deeper instead of dumping a framework. Plain text only — no markdown, no \
bullet lists, no headers.
- Reason your own way to the answer. There is no fixed procedure to follow.
- Every example, script, and analogy you give MUST be DME-based — surgeons and clinics, DME providers \
and reps, payer/PECOS/coverage conversations, territory growth. Never quote a generic business example \
as-is; translate it into DME.

Two sales methods, both always available — YOU decide how to blend them for where THIS doctor sits:
- WIN-NEW (Conversations That Win the Complex Sale): for a prospect you don't yet have — provoke the \
status quo, surface an unconsidered need, build "why change."
- GROW-EXISTING (The Expansion Sale): for an account you already serve — reinforce the value you've \
delivered; don't disrupt a relationship that's working. The in-between "why evolve" case: reinforce \
first, then introduce one unconsidered need.
If you can't tell whether the doctor is a new prospect or an existing account and it changes the move, \
ask one quick question before coaching.

Tools — call them when you need grounding; don't guess at specifics:
- search_value_prop: the Healing Journey (THJ) value proposition + stakeholder/buyer intelligence. Use \
for "how do I pitch/position THJ", "what does a DME provider care about", buyer psychology.
- search_method: the two sales books above, for deeper method.
- search_podcast: the team's own Sales podcast — how we actually talk about selling THJ.
- mine_practice_reviews: live public-review intelligence for a SPECIFIC named doctor/practice the rep \
is about to meet. Call it the moment a real practice/surgeon name is in play — it pulls the live Google/\
Healthgrades rating and cited verbatim patient quotes, and hands you a framing guide to write the rep a \
reputation read + DME opener. If the rep wants meeting prep but hasn't named a practice, ASK which \
practice and city first — never call this with a guessed or generic name, and never invent reviews.

Inviolable grounding rules:
- Never fabricate a THJ feature, price, fact, or a citation. If retrieval misses, say "I don't have \
that here" — don't deny it exists and don't invent it.
- THJ facts come from search_value_prop; general method from search_method. Don't present a generic \
framework as a THJ fact.

A quick value-prop reference is in <value_registry> below; call search_value_prop for depth."""

TOOL_DEFS = [
    {
        "name": "search_value_prop",
        "description": "Search the Healing Journey (THJ) value proposition + stakeholder/buyer intelligence. Use for pitching/positioning THJ and for how a given buyer thinks.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "what to look up"}}, "required": ["query"]},
    },
    {
        "name": "search_method",
        "description": "Search the two sales-method books (win-new + grow-existing) for deeper technique.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "what to look up"}}, "required": ["query"]},
    },
    {
        "name": "search_podcast",
        "description": "Search the team's own Sales podcast — how we actually talk about selling THJ, incl. takes on competitors (Force Therapeutics, Ask Hoag).",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "what to look up"}}, "required": ["query"]},
    },
    {
        "name": "mine_practice_reviews",
        "description": "Live public-review intelligence for a SPECIFIC named doctor/practice the rep is about to meet. Pulls the live Google/Healthgrades rating + cited verbatim patient quotes from public sources (slow, ~40s on a fresh practice), and returns a framing guide to write a reputation read + DME opener. Only call with a real practice/surgeon name the rep gave — never a guess; ask for the name first if missing.",
        "input_schema": {"type": "object", "properties": {
            "practice": {"type": "string", "description": "the specific doctor or practice name the rep named"},
            "city": {"type": "string", "description": "the city, if the rep mentioned one (helps disambiguate)"},
        }, "required": ["practice"]},
    },
]


def load_floor() -> str:
    p = Path(VALUE_REGISTRY_PATH).expanduser()
    return p.read_text(encoding="utf-8") if p.exists() else ""


def build_system(floor_text: str | None = None) -> str:
    """Compose the system prompt. floor_text injected by the service (loaded from the
    coach_floor DB row in prod); falls back to the local registry file for the CLI."""
    floor = floor_text if floor_text is not None else load_floor()
    return _PERSONA + (f"\n\n<value_registry>\n{floor}\n</value_registry>" if floor else "")


_SEARCH_TOOLS = {
    "search_value_prop": tools.search_value_prop,
    "search_method": tools.search_method,
    "search_podcast": tools.search_podcast,
}


def _run_search_tool(name: str, args: dict, embed_fn: Callable[[str], list[float]], conn) -> str:
    """Fast vector-search tool → a tool_result string. Synchronous (no progress to surface)."""
    query = (args or {}).get("query", "").strip()
    if not query:
        return "(no query provided)"
    rows = _SEARCH_TOOLS[name](conn, embed_fn(query))
    if not rows:
        return "(no results — say you don't have that here; do not invent)"
    return "\n\n".join(f"[{r['title']} · {r.get('audience') or r['category']}]\n{r['text']}" for r in rows)


async def _run_tool_streaming(name: str, args: dict, embed_fn: Callable[[str], list[float]], conn):
    """Dispatch a tool, yielding progress events then exactly one result event.

    Yields {"type":"progress","text":...} (0+) then {"type":"result","text":...} (1). The
    fast search tools emit no progress (a single result); mine_practice_reviews streams its
    own scrape→reconcile→craft progress while the slow live fetch runs (review_cache)."""
    if name in _SEARCH_TOOLS:
        yield {"type": "result", "text": _run_search_tool(name, args, embed_fn, conn)}
    elif name == "mine_practice_reviews":
        async for ev in review_cache.mine_streaming(args):
            yield ev
    else:
        yield {"type": "result", "text": f"(unknown tool {name})"}


async def run_agent(
    user_message: str,
    history: list[dict] | None = None,
    *,
    embed_fn: Callable[[str], list[float]],
    conn,
    zai_key: str,
    system: str | None = None,
    model: str = MODEL,
) -> AsyncGenerator[dict, None]:
    """Stream the coach's answer as typed events. Runs the Anthropic tool-use loop
    internally; yields {"type":"delta","text":...} for answer text and
    {"type":"progress","text":...} for slow-tool phase updates (mine_practice_reviews).
    Tool calls happen between/around the deltas; the SSE layer maps each event type."""
    system = system if system is not None else build_system()
    messages: list[dict] = list(history or []) + [{"role": "user", "content": user_message}]
    headers = {"Authorization": f"Bearer {zai_key}", "anthropic-version": "2023-06-01", "Content-Type": "application/json"}

    for _ in range(MAX_TOOL_HOPS):
        payload = {"model": model, "max_tokens": 4096, "system": system, "messages": messages, "tools": TOOL_DEFS, "stream": True}
        text_parts: list[str] = []
        tool_uses: list[dict] = []
        cur_tool: dict | None = None
        stop_reason: str | None = None

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            async with client.stream("POST", ZAI_URL, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    try:
                        ev = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    et = ev.get("type")
                    if et == "content_block_start":
                        cb = ev.get("content_block", {})
                        if cb.get("type") == "tool_use":
                            cur_tool = {"id": cb["id"], "name": cb["name"], "json": ""}
                    elif et == "content_block_delta":
                        d = ev.get("delta", {})
                        if d.get("type") == "text_delta":
                            text_parts.append(d["text"])
                            yield {"type": "delta", "text": d["text"]}
                        elif d.get("type") == "input_json_delta" and cur_tool is not None:
                            cur_tool["json"] += d.get("partial_json", "")
                    elif et == "content_block_stop":
                        if cur_tool is not None:
                            tool_uses.append(cur_tool)
                            cur_tool = None
                    elif et == "message_delta":
                        stop_reason = ev.get("delta", {}).get("stop_reason") or stop_reason

        # Record this assistant turn (text + any tool_use blocks).
        assistant_content: list[dict] = []
        if text_parts:
            assistant_content.append({"type": "text", "text": "".join(text_parts)})
        for tu in tool_uses:
            assistant_content.append({"type": "tool_use", "id": tu["id"], "name": tu["name"], "input": json.loads(tu["json"] or "{}")})
        if assistant_content:
            messages.append({"role": "assistant", "content": assistant_content})

        if stop_reason != "tool_use" or not tool_uses:
            return  # final answer streamed

        # Execute tools → tool_result, loop. Each tool may emit progress (forwarded to the
        # rep) before its single result; we run them sequentially (usually one per hop).
        results = []
        for tu in tool_uses:
            args = json.loads(tu["json"] or "{}")
            result_text = ""
            async for ev in _run_tool_streaming(tu["name"], args, embed_fn, conn):
                if ev["type"] == "result":
                    result_text = ev["text"]
                else:
                    yield ev  # progress → out to the SSE layer
            results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": result_text})
        messages.append({"role": "user", "content": results})
