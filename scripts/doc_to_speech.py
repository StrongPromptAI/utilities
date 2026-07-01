#!/usr/bin/env python3
"""
doc_to_speech — the shared TTS engine + one-voice (monologue) render pipeline.

This module is a LIBRARY, not a CLI — the command lives in doc_to_audio.py. It holds
the engine both render formats share (markdown→speakable normalization, chunking, «…»
emphasis pacing, pronunciation overrides, per-chunk PCM synth, ffmpeg→MP3, podcast-server
publish + episode-description pass, Railway secret pulls) plus the one-voice pipeline
(_normalize_and_chunk, _process_doc, _publish_episode). dialogue.py adds the two-voice
pipeline; doc_to_audio.py drives either.

Pipeline:
  read doc → normalize markdown to *speakable* prose → chunk to ≤ N chars
  → synthesize each chunk via the shared-svcs TTS service (Kokoro) as raw PCM
  → concatenate (with a short pause between chunks) → ffmpeg transcode to MP3
  → PUT to a podcast show's volume (services/podcast) + a brief description sidecar.

This is an on-demand driver — run it per document; nothing is deployed. It is the
first production consumer of services/tts.

Why each step exists (the gaps between the raw TTS service and "MP3 in the feed"):
  • TTS emits WAV/PCM, never MP3            → ffmpeg transcodes (MP3 is ~12× smaller
                                              and plays on every car head unit).
  • TTS caps input at 800 chars/request     → chunk sentence-aware, synth, concat.
  • Markdown read verbatim is unlistenable  → strip code/tables/link-syntax/headings
                                              into spoken prose first.

Usage:
  uv run python scripts/doc_to_speech.py <doc.md> [options]

  # Full run against prod TTS, upload to the "briefings" folder (the default):
  uv run python scripts/doc_to_speech.py PLAN.md --speed 1.1

  # Several docs at once — each becomes its own MP3, up to --concurrency synthesized
  # in parallel (one doc per worker; the server's TTS_MAX_CONCURRENCY is the backstop):
  uv run python scripts/doc_to_speech.py A.md B.md C.md --concurrency 3

  # Strip file paths / names / scaffolding via a cheap LLM so you hear only the meat:
  uv run python scripts/doc_to_speech.py PLAN.md --scrub --speed 1.1

  # Distill one or more docs into a NARRATIVE script instead of reading them. 'pm' = a
  # product-doctrine briefing for the PM (synthesizes, surfaces decisions/tradeoffs/open
  # questions); 'exec' = tight business recap. Auto-named "<doc>-pm.mp3"/"-exec.mp3":
  uv run python scripts/doc_to_speech.py HEALING_JOURNEY.md --narrative pm

  # See exactly what will be spoken — no TTS (with --scrub or --narrative, shows the
  # LLM-distilled text; this is the fastest way to tune the narrative before synth):
  uv run python scripts/doc_to_speech.py PLAN.md --narrative pm --dry-run

  # Use a locally-running TTS (services/tts on :8102, auth-off in dev):
  uv run python scripts/doc_to_speech.py PLAN.md --local-tts

  # Produce a local MP3 only, skip the podcast publish:
  uv run python scripts/doc_to_speech.py PLAN.md --no-upload --out /tmp/doc.mp3

Auth:
  • TTS prod  — HS256 JWT, aud="tts", signed with the shared-svcs JWT_SECRET.
  • podcast   — HS256 bearer, aud="podcast-upload", signed with the show server's
                PODCAST_UPLOAD_SECRET (services/podcast/app.py _verify_upload).
  Both secrets are pulled live from Railway (Railway GraphQL API); nothing is written
  to disk. --local-tts skips the TTS secret; --no-upload skips the podcast secret.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import requests

# ── Railway coordinates (mirrors CLAUDE.md § Railway / § Shared Services) ────────

RAILWAY_GRAPHQL = "https://backboard.railway.com/graphql/v2"
RAILWAY_CONFIG = Path("~/.railway/config.json").expanduser()
KEYS_JSON = Path("~/.config/keys.json").expanduser()

SHARED_SVCS = {
    "project": "504e0aec-fb69-443b-9786-139b5fe50e0a",
    "env": "1ea8ab63-10af-4b83-b562-68a4a5c4f670",
    "tts_service": "02ff6d94-a49c-464e-b1e0-44f6933d5209",
}
# StrongPrompt podcast server (services/podcast) — the synth-publish target. A show is a
# slug; the MP3 + a `<base>.md` description sidecar PUT straight onto its volume. This is the
# sole sink: oxp.files was retired as a doc_to_audio target at cutover (its podcast content
# migrated to the `tech` show; oxp.files is now OrthoXpress-client-only).
PODCAST = {
    "project": "f4451750-12a8-4cff-9bc8-1796a9c15508",
    "env": "844d5562-ac1d-4a22-b249-986be610a0a5",
    "service": "5a1fc29d-3556-4fd3-b039-dd9cb0d43ec7",
}

PROD_TTS_URL = "https://shared-svcs-tts.up.railway.app"
LOCAL_TTS_URL = "http://localhost:8102"
PODCAST_FALLBACK_URL = "https://podcast-production-31c9.up.railway.app"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_SCRUB_MODEL = "google/gemini-3.5-flash"  # cheap mechanical cleanup — OpenRouter (no native Gemini)
DEFAULT_EXEC_MODEL = "claude-sonnet-4-6"  # native Anthropic (Anthropic credits); post-processing, not SOTA authoring
SCRUB_SEG_CHARS = 6000   # scrub output ≈ input size → cap input to bound output tokens
EXEC_SEG_CHARS = 24000   # exec recap output ≪ input → input can be larger (most docs → one pass)
EMPHASIS_SEG_CHARS = 4000  # emphasis must reproduce its input verbatim → small segments reproduce reliably

KOKORO_SAMPLE_RATE = 24000  # Kokoro's native PCM rate (see services/tts/app.py)
CHARS_PER_SECOND = 20  # ~Kokoro speech rate; converts the max-pause-gap (seconds) to a char budget


def _log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _die(msg: str) -> "None":
    _log(f"❌ {msg}")
    sys.exit(1)


# ── Railway secret pull (read-only; secrets stay in memory) ──────────────────────

def _railway_token() -> str:
    if RAILWAY_CONFIG.exists():
        tok = json.loads(RAILWAY_CONFIG.read_text()).get("user", {}).get("apiToken")
        if tok:
            return tok
    if KEYS_JSON.exists():
        tok = json.loads(KEYS_JSON.read_text()).get("railway_main")
        if tok:
            return tok
    _die(
        "No Railway API token. Expected ~/.railway/config.json → user.apiToken "
        "or ~/.config/keys.json → railway_main."
    )


def _railway_vars(project: str, env: str, service: str) -> dict:
    token = _railway_token()
    query = (
        "query { variables(projectId: \"%s\", environmentId: \"%s\", serviceId: \"%s\") }"
        % (project, env, service)
    )
    r = requests.post(
        RAILWAY_GRAPHQL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": query},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    if "errors" in body:
        _die(f"Railway GraphQL error: {body['errors']}")
    return body["data"]["variables"]


def _openrouter_key() -> str:
    if KEYS_JSON.exists():
        key = json.loads(KEYS_JSON.read_text()).get("openrouter")
        if key:
            return key
    _die("No OpenRouter key. Expected ~/.config/keys.json → openrouter.")


def _anthropic_key() -> str:
    if KEYS_JSON.exists():
        key = json.loads(KEYS_JSON.read_text()).get("ANTHROPIC_API_KEY")
        if key:
            return key
    _die("No ANTHROPIC_API_KEY for a native-Anthropic model. Expected ~/.config/keys.json → ANTHROPIC_API_KEY.")


# ── One chat-completion call, routed by model id ─────────────────────────────────
#
# A BARE model id (no "/", e.g. "claude-opus-4-8") hits the NATIVE Anthropic Messages
# API and draws on Anthropic usage credits. A router-prefixed id ("google/…",
# "anthropic/…") goes to OpenRouter. So the Anthropic-authored work (pm/exec narrative,
# dialogue script) uses credits directly; Gemini (scrub) stays on OpenRouter. The two
# wire shapes differ — native puts `system` top-level and rejects sampling params on
# Opus 4.7/4.8, and the text lands in content[].text, not choices[].message.content.

def llm_chat(
    *, system: str, user: str, model: str, max_tokens: int, temperature: float, label: str,
) -> str:
    """Run one chat completion and return the response text. Retries transient/5xx 3×;
    _die()s on auth, 4xx, or hard failure. Routes native-Anthropic vs OpenRouter by model id."""
    native = "/" not in model
    if native:
        url = ANTHROPIC_API_URL
        headers = {
            "x-api-key": _anthropic_key(),
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        # Native Messages API: `system` is a top-level field (NOT a system-role message),
        # and Opus 4.7/4.8 reject temperature/top_p — omit sampling params entirely.
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
    else:
        url = OPENROUTER_URL
        headers = {"Authorization": f"Bearer {_openrouter_key()}", "Content-Type": "application/json"}
        body = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    provider = "Anthropic" if native else "OpenRouter"
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=180)
            if r.status_code == 200:
                data = r.json()
                if native:
                    out = "".join(
                        b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
                    ).strip()
                else:
                    out = data["choices"][0]["message"]["content"].strip()
                if not out:
                    _die(f"{label} LLM returned empty content")
                return out
            if r.status_code in (401, 403):
                _die(f"{provider} auth rejected ({r.status_code}): {r.text[:200]}")
            if r.status_code < 500:
                _die(f"{label} LLM error {r.status_code} ({provider}): {r.text[:300]}")
            last_err = f"{r.status_code}: {r.text[:200]}"
        except (requests.RequestException, KeyError, ValueError) as exc:
            last_err = str(exc)
        if attempt < 2:
            time.sleep(1.0 * (attempt + 1))
    _die(f"{label} LLM failed after 3 attempts ({provider}): {last_err}")


# ── LLM passes: audience-specific rewrites of the speakable prose ─────────────────
#
# Two audiences, two system prompts, one request path (_llm_pass):
#   technical (default) — optional --scrub: content-preserving cleanup, full reading.
#   exec               — always: a short, jargon-free executive recap.

_SCRUB_SYSTEM = (
    "You are preparing a document to be read aloud as audio for a LISTENER — someone "
    "absorbing the ideas, not studying a file. Rewrite the text so it flows naturally "
    "when spoken. DELETE outright (do not read aloud, do not naturalize) anything that "
    "only matters to a reader looking at the source: file paths, file names, directory "
    "names, URLs, code and CLI fragments, cross-reference scaffolding ('see X.md "
    "section Y', 'per the registry', '(Call 2026-02-06)'), inline citations and ID tags, "
    "and document-metadata headers — status lines, version stamps, 'Date:' / 'Updated:' "
    "/ 'Last updated:' lines, project tags, and the like. A listener never needs a "
    "document's update date or a section cross-reference; drop them silently. Spell out "
    "any remaining abbreviation the way it would be said aloud. Preserve ALL substantive "
    "ideas, arguments, and detail — do NOT summarize, shorten, or editorialize the actual "
    "content. Do not add commentary, headings, preamble, or markers. Output only the "
    "cleaned spoken text."
)

_EXEC_SYSTEM = (
    "You are turning an internal product/strategy document into a short EXECUTIVE audio "
    "recap for a senior, non-technical business audience (founders, executives, "
    "investors) who have little time and no patience for jargon. Produce a tight spoken "
    "recap, NOT a full reading.\n\n"
    "Do:\n"
    "- Open with the business takeaway. Boost and lead with business model, "
    "pricing / monetization strategy, branding and positioning, market insight, the "
    "competitive seam being entered, and the assumptions the strategy rests on.\n"
    "- Translate any technical, clinical, or product-internal jargon into plain language "
    "an executive would actually use; drop terms that don't carry business meaning.\n"
    "- Be substantially shorter than the source — keep only what a busy executive needs "
    "to grasp the thesis, the bet, and the open decisions.\n"
    "- Mark emphasis SPARINGLY for the ear: wrap a small number of the single most "
    "load-bearing lines in guillemets, «like this» (a whole clause or sentence, never a "
    "single word, never a whole paragraph; only a handful in the whole recap), so they are "
    "set apart by a brief pause for weight. Used rarely they land; used often they deaden.\n\n"
    "Do NOT:\n"
    "- Mention or read aloud ANY file path, file name, document name, section pointer, "
    "URL, code, or cross-reference scaffolding (e.g. 'see X dot M D', 'per the registry'). "
    "Remove them entirely — convey the idea, never the pointer.\n"
    "- Dwell on implementation detail, step-by-step procedure, or fine-grained clinical / "
    "technical specifics unless they directly carry business meaning.\n"
    "- Invent numbers or prices. Use only figures present in the source; if the source "
    "defers a price, say it is still open rather than inventing one.\n"
    "- Add headings, bullets, labels, preamble, or meta-commentary. Output ONLY the recap "
    "as flowing prose meant to be heard, not seen."
)

_PM_SYSTEM = (
    "You are turning one or more internal product-doctrine documents into a spoken "
    "NARRATIVE BRIEFING for the PRODUCT MANAGER who owns this product — a smart, "
    "technical-enough listener who wants the *thinking*, not a recital. Produce flowing "
    "spoken prose that distills and connects the ideas, NOT a section-by-section reading.\n\n"
    "Do:\n"
    "- Find the throughline and lead with it: what this product/area IS, the core model, "
    "and why it's shaped this way.\n"
    "- Synthesize ACROSS the material — weave related ideas from different parts into one "
    "narrative; draw out the design decisions, the tradeoffs, the tensions, and the open "
    "questions a PM needs to weigh.\n"
    "- Surface what's load-bearing and what's still unsettled, so the listener can tell "
    "what to tune. Name the boundary cases and the 'this must never happen' rules in plain "
    "terms.\n"
    "- Keep a product vocabulary: product concepts, user/patient experience, design "
    "reasoning. Translate engineering and clinical jargon into plain language; keep a term "
    "only if it carries product meaning.\n"
    "- Mark emphasis SPARINGLY for the ear. Wrap a SMALL number of the single most "
    "load-bearing lines in guillemets, «like this», so they are set apart by a brief pause "
    "for weight. Rules: wrap a WHOLE clause or sentence, never a single word; at most one per "
    "few paragraphs and only a handful in the whole briefing (well under one line in "
    "twenty); include the trailing punctuation inside the marks. Used rarely they land; "
    "used often they deaden — when in doubt, leave it unmarked.\n\n"
    "Do NOT:\n"
    "- Read document structure aloud, or mention file names, section pointers, headings, "
    "dates, status tags, citations, or URLs — convey the idea, never the pointer.\n"
    "- Over-summarize into a teaser — the PM wants the 'why', not just the 'what'. Be "
    "substantial; this is a briefing, not a blurb.\n"
    "- Over-mark emphasis, wrap a bare word or a whole paragraph in «…», or use the "
    "guillemets for anything other than the rare load-bearing line.\n"
    "- Add headings, bullets, labels, or meta-commentary. Output ONLY the narrative as "
    "flowing prose meant to be heard."
)

# Emphasis-ONLY post-processing: the text is already authored (e.g. on a SOTA model
# outside the CLI). The model must NOT write — it may only wrap a few load-bearing clauses
# in «…». A verification step (below) guarantees this: if the returned text differs from
# the input by anything other than inserted guillemets, the markers are discarded and the
# input is kept verbatim. So this pass can add emphasis but can never reword.
_EMPHASIZE_SYSTEM = (
    "You are a post-processor that marks emphasis for a text-to-speech reading. You are "
    "given a FINISHED, authored passage of spoken prose. Your ONLY job is to wrap a SMALL "
    "number of the single most load-bearing lines in guillemets «like this», so the narrator "
    "sets them apart by a brief pause for weight.\n\n"
    "ABSOLUTE RULES:\n"
    "- Reproduce the passage VERBATIM — every word, number, punctuation mark, and line break "
    "EXACTLY as given. Do NOT rewrite, reword, summarize, shorten, expand, correct, translate, "
    "or reorder anything. The text is final and authored by someone else.\n"
    "- The ONLY characters you may ADD anywhere are the guillemets « and ». Add nothing else — "
    "no other punctuation, no markdown, no commentary, no headings, no labels.\n"
    "- Wrap a WHOLE clause or sentence, never a single word, never a whole paragraph. Mark at "
    "most a handful in the entire passage — well under one line in twenty; include the trailing "
    "punctuation inside the marks. When in doubt, leave it unmarked.\n"
    "- Output ONLY the passage, verbatim, with the guillemets inserted."
)


# A SHORT podcast episode description (show notes), written by the in-CLI Sonnet tier from
# the finished spoken script. Lands as the `<base>.md` sidecar the feed turns into
# <description> — a purpose-written blurb, NOT the truncated transcript.
_DESCRIBE_SYSTEM = (
    "You are writing a SHORT podcast episode description (show notes) for ONE episode, given "
    "its full spoken script. Write 1–3 sentences, under about 60 words, that tell a "
    "prospective listener what the episode covers and why it's worth hearing. Present tense. "
    "Do NOT open with a cliché like 'In this episode'. No markdown, headings, bullet points, "
    "hashtags, emoji, or quotation marks around the whole thing. Output ONLY the description text."
)


def _episode_description(text: str, *, model: str, label: str = "description") -> str:
    """One LLM call → a brief podcast episode description from the spoken script. The script's
    lead carries the gist, so a long episode is capped on input to bound tokens. Markup the model
    might emit despite the instruction is stripped (the feed shows it as plain text)."""
    src = text.strip()
    if len(src) > EXEC_SEG_CHARS:
        src = src[:EXEC_SEG_CHARS]
    out = llm_chat(
        system=_DESCRIBE_SYSTEM, user=src, model=model,
        max_tokens=400, temperature=0.3, label=label,
    )
    return strip_llm_markup(out).strip()


def _emphasis_text_preserved(original: str, annotated: str) -> bool:
    """True iff `annotated` is `original` with only «…» guillemets inserted — i.e. the model
    added emphasis and changed nothing else (whitespace-normalized). The guarantee that the
    emphasize pass is annotation-only, never a rewrite."""
    strip = lambda s: re.sub(r"\s+", " ", s.replace("«", "").replace("»", "")).strip()
    return strip(original) == strip(annotated)


def _emphasis_pass(text: str, *, model: str, label: str = "emphasis") -> str:
    """Mark «…» emphasis on already-authored prose WITHOUT rewriting it. Segmented small so
    each call reproduces its input reliably; every segment is verified annotation-only and, if
    the model altered the words, its markers are dropped and the segment kept verbatim (one
    retry first). The text can gain emphasis but can never be reworded by this pass."""
    out_parts: list[str] = []
    marked = altered = 0
    for seg in _segment(text, EMPHASIS_SEG_CHARS):
        annotated = None
        for _ in range(2):
            cand = llm_chat(
                system=_EMPHASIZE_SYSTEM, user=seg, model=model,
                max_tokens=max(1024, len(seg) // 3 + 512), temperature=0.0, label=label,
            )
            if _emphasis_text_preserved(seg, cand):
                annotated = cand
                break
        if annotated is None:
            altered += 1
            annotated = seg  # model reworded — keep authored text verbatim, no emphasis here
        elif "«" in annotated:
            marked += 1
        out_parts.append(annotated)
    if altered:
        _log(f"⚠️  emphasis: {altered} segment(s) came back reworded — kept verbatim (no emphasis) to protect your text")
    _log(f"✍️  emphasis: annotated {marked} segment(s); text preserved verbatim")
    return "\n\n".join(out_parts)


def _segment(text: str, seg_chars: int) -> list[str]:
    """Split on paragraph breaks, packing into ≤ seg_chars segments so each LLM call's
    output stays well under the model's max-tokens (no truncation)."""
    segs, cur = [], ""
    for para in text.split("\n\n"):
        if cur and len(cur) + 2 + len(para) > seg_chars:
            segs.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        segs.append(cur)
    return segs


def _llm_pass(
    text: str, *, system: str, model: str,
    seg_chars: int, max_tokens: int, label: str,
) -> str:
    """Run one LLM rewrite over the speakable prose, segmenting so each call's output
    never truncates. `system` selects the transform (content-preserving scrub vs. pm/exec
    recap); the provider (native Anthropic vs OpenRouter) is chosen by `model` in llm_chat."""
    out_parts: list[str] = []
    segments = _segment(text, seg_chars)
    for i, seg in enumerate(segments, 1):
        if len(segments) > 1:
            _log(f"   {label} segment {i}/{len(segments)}…")
        out_parts.append(
            llm_chat(system=system, user=seg, model=model, max_tokens=max_tokens,
                     temperature=0.0, label=label)
        )
    return "\n\n".join(out_parts)


# ── HS256 JWT (stdlib — same shape as shared_auth/token.py and the files session) ─

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _hs256_jwt(payload: dict, secret: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    sig = _b64url(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"


# ── Markdown → speakable prose ───────────────────────────────────────────────────

_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]*-{2,}[\s:|-]*\|?\s*$")


def _strip_inline(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)              # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)          # inline links → text
    text = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", text)         # reference links → text
    text = re.sub(r"`([^`]+)`", r"\1", text)                      # inline code → text
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)                # bold
    text = re.sub(r"__([^_]+)__", r"\1", text)                    # bold (underscore)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)                    # italic
    text = re.sub(r"~~([^~]+)~~", r"\1", text)                    # strikethrough
    text = re.sub(r"<[^>]+>", "", text)                           # raw HTML tags
    text = re.sub(r"https?://\S+", "", text)                      # bare URLs
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def strip_llm_markup(text: str) -> str:
    """Strip inline markup an LLM may emit despite a 'no markers' instruction — *emphasis*,
    **bold**, inline code, link/image syntax, bare URLs. Both front-ends apply this to LLM
    output: normalize_markdown only ran on the SOURCE, before the LLM rewrote it, so without
    this the TTS voices a literal asterisk. Shared so the fix can't drift between tools."""
    return _strip_inline(text)


def _ensure_sentence_end(s: str) -> str:
    s = s.strip()
    if s and s[-1] not in ".!?:;":
        s += "."
    return s


# Sentinel marking a major-topic boundary in speakable text. The synth loop turns
# it into a longer "take a breath" silence instead of a TTS call. BEL is never
# produced by markdown and survives strip()/_strip_inline untouched.
_SECTION_SENTINEL = "\u0007"


def normalize_markdown(
    text: str, *, code_cue: str = "Code block omitted.", section_breaks: bool = False
) -> tuple[str, str | None]:
    """Return (speakable_text, first_h1_title). Drops code/tables/markup.
    When section_breaks=True, inserts a _SECTION_SENTINEL before each level-1/2
    heading after the first, so the pipeline can pause between major topics."""
    title = None
    seen_heading = False
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if m:
        title = _strip_inline(m.group(1)).strip()

    text = _FRONTMATTER_RE.sub("", text)
    text = _FENCE_RE.sub(f"\n\n{code_cue}\n\n", text)

    out_lines: list[str] = []
    for raw in text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            out_lines.append("")
            continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):       # horizontal rule
            out_lines.append("")
            continue

        h = re.match(r"^(#{1,6})\s+(.*)$", stripped)            # heading → its own sentence
        if h:
            if section_breaks and seen_heading and len(h.group(1)) <= 2:
                out_lines.append("")                            # major topic boundary →
                out_lines.append(_SECTION_SENTINEL)             # "take a breath" pause
            seen_heading = True
            out_lines.append("")
            out_lines.append(_ensure_sentence_end(h.group(2)))
            out_lines.append("")
            continue

        if _TABLE_SEP_RE.match(stripped):                       # table separator row → drop
            continue
        if stripped.startswith("|") or (" | " in stripped and stripped.count("|") >= 2):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            cells = [c for c in cells if c]
            if cells:
                out_lines.append(_ensure_sentence_end(", ".join(cells)))
            continue

        stripped = re.sub(r"^>\s?", "", stripped)               # blockquote marker
        stripped = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", stripped)  # list marker
        out_lines.append(stripped)

    text = "\n".join(out_lines)
    text = _strip_inline(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, title


# ── Chunking (sentence-aware, ≤ max_chars) ───────────────────────────────────────

def _hard_split(s: str, max_chars: int) -> list[str]:
    out, cur = [], ""
    for word in s.split():
        if len(cur) + 1 + len(word) > max_chars:
            if cur:
                out.append(cur)
            cur = word
        else:
            cur = (cur + " " + word).strip()
    if cur:
        out.append(cur)
    return out


def chunk_text(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    cur = ""
    for para in re.split(r"\n{2,}", text):
        para = para.strip()
        if not para:
            continue
        if para == _SECTION_SENTINEL:                  # topic boundary → standalone chunk
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(_SECTION_SENTINEL)
            continue
        for sentence in re.split(r"(?<=[.!?:;])\s+", para):
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) > max_chars:
                if cur:
                    chunks.append(cur)
                    cur = ""
                chunks.extend(_hard_split(sentence, max_chars))
                continue
            if len(cur) + 1 + len(sentence) <= max_chars:
                cur = (cur + " " + sentence).strip()
            else:
                if cur:
                    chunks.append(cur)
                cur = sentence
    if cur:
        chunks.append(cur)
    return chunks


# ── Emphasis (frame a load-bearing span with silence — same tempo, no slowdown) ───
#
# Kokoro has no per-word prosody control — its only knobs are per-call speed and the
# silence we splice between calls. So "emphasis" is: isolate a marked span into its own
# synth call and BRACKET it with a short pause before AND after — at the normal speaking
# tempo. The bracketing silence reads as weight (the listener hears the clause set apart,
# anticipated and landed upon) the way a narrator pauses around a key line. We do NOT slow
# the span: slowing it down violated the surrounding tempo and sounded unnatural — the pause
# does the work without the drag. Mark spans in the source with guillemets «…» — never
# emitted by markdown, so they survive normalization untouched.

class Emph(str):
    """A chunk that gets bracketed by an --emphasis-gap pause before AND after it (synthesized
    at the normal speed — no slowdown). Subclasses str so it flows through the chunk list,
    len(), the pause-cap, and the dry-run preview unchanged — only the assembly treats it
    specially."""
    __slots__ = ()


_EMPHASIS_MARK = re.compile(r"«(.+?)»", re.DOTALL)


def split_emphasis_chunks(text: str, max_chars: int) -> list[str]:
    """Chunk `text` normally, but isolate each «…»-marked span into its own Emph chunk.
    The marker delimiters themselves are consumed (never voiced). Author spans at
    clause/sentence boundaries with trailing punctuation *inside* the span, so the split
    lands cleanly and the surrounding prose isn't fragmented mid-clause."""
    out: list[str] = []
    for i, seg in enumerate(_EMPHASIS_MARK.split(text)):
        if not seg.strip():
            continue
        seg_chunks = chunk_text(seg, max_chars)
        if i % 2 == 1:  # odd segments are the captured «…» interiors
            seg_chunks = [c if c == _SECTION_SENTINEL else Emph(c) for c in seg_chunks]
        out.extend(seg_chunks)
    return out


def enforce_pause_cap(chunks: list[str], max_chars: int) -> list[str]:
    """Guarantee no run of speech between pauses exceeds ~max_chars (a char proxy for
    seconds at Kokoro's rate). Walks the chunk list and inserts a _SECTION_SENTINEL at a
    sentence-aligned boundary whenever the accumulated stretch since the last pause would
    overflow. Composes with heading-based sentinels (which also reset the accumulator).
    This is the "no stretch longer than N seconds without a breath" rule, enforced
    structurally so it never depends on where headings happen to fall."""
    out: list[str] = []
    acc = 0
    for c in chunks:
        if c == _SECTION_SENTINEL:
            out.append(c)
            acc = 0
            continue
        if acc > 0 and acc + len(c) > max_chars:
            out.append(_SECTION_SENTINEL)
            acc = 0
        out.append(c)
        acc += len(c)
    return out


# ── Pronunciation overrides (Lever 2: text respelling before synth) ──────────────

PRON_OVERRIDES_PATH = Path(__file__).resolve().with_name("pron_overrides.json")


def load_pron_overrides(path: Path | None = None) -> dict[str, str]:
    """Load the word→respelling map from pron_overrides.json (the 'overrides' key).
    Missing file → empty map (feature is opt-out-by-absence). Kokoro's espeak
    phonemizer mishears some proper nouns/jargon; this rewrites them to a spelling
    it says correctly, deterministically, regardless of what the LLM wrote."""
    p = path or PRON_OVERRIDES_PATH
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    ov = data.get("overrides", {}) if isinstance(data, dict) else {}
    return {str(k): str(v) for k, v in ov.items() if k and not str(k).startswith("_")}


def apply_pron_overrides(text: str, overrides: dict[str, str]) -> str:
    """Whole-word, case-insensitive replacement, applied LONGEST-KEY-FIRST so a
    multi-word phrase ('live with') wins over a bare word ('live') — the lever for
    context-dependent homographs. Word boundaries keep 'irrevocable' from matching
    inside a larger token."""
    for word, spoken in sorted(overrides.items(), key=lambda kv: len(kv[0]), reverse=True):
        text = re.sub(rf"\b{re.escape(word)}\b", spoken, text, flags=re.IGNORECASE)
    return text


def resolve_pron_overrides(
    *, no_pron: bool, pron_files: list[Path], ad_hoc: list[str]
) -> dict[str, str]:
    """Assemble the active override map for one run — the single happy path shared by
    the one-voice and two-voice pipelines. Layers, later wins:

      1. the global default file scripts/pron_overrides.json (skipped if --no-pron),
      2. each --pron-file sidecar in order,
      3. ad-hoc --pron WORD=SPOKEN.

    The layering is the holistic homograph answer. The global file stays doctrine-pure
    (phrase-disambiguated entries only, NO bare homograph whose sense flips with context
    — 'content' the noun vs adjective, 'live' reside vs broadcast), because it colors
    EVERY project's audio. A bare-homograph override whose sense is fixed *within one
    doc-set* (clinical briefings: 'content' is always the noun, 'live(s)' always means
    reside) belongs in a per-DOMAIN --pron-file sidecar pointed at by that doc-set's
    runs — declared where the sense is known, never guessed globally. Position-triggered
    homographs (espeak flips 'content' to the adjective on a sentence-final '…content.',
    which no *phrase* key can catch) can ONLY be fixed this way."""
    overrides: dict[str, str] = {} if no_pron else load_pron_overrides()
    for f in pron_files:
        if not f.exists():
            _die(f"--pron-file not found: {f}")
        overrides.update(load_pron_overrides(f))
    for spec in ad_hoc:
        if "=" not in spec:
            _die(f"--pron expects WORD=SPOKEN, got {spec!r}")
        w, _, s = spec.partition("=")
        overrides[w.strip()] = s.strip()
    return overrides


# ── TTS synthesis ────────────────────────────────────────────────────────────────

def synthesize(
    chunk: str, *, tts_url: str, voice: str, speed: float, language: str, token: str | None
) -> bytes:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {
        "input": chunk,
        "voice": voice,
        "response_format": "pcm",  # raw s16le @ 24kHz mono — trivially concatenable
        "speed": speed,
        "language": language,
    }
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(
                f"{tts_url}/v1/audio/speech", headers=headers, json=body, timeout=120
            )
            if r.status_code == 200:
                return r.content
            if r.status_code in (401, 403):
                _die(f"TTS auth rejected ({r.status_code}): {r.text[:200]}")
            if r.status_code < 500:
                _die(f"TTS error {r.status_code}: {r.text[:200]}")
            last_err = f"{r.status_code}: {r.text[:200]}"
        except requests.RequestException as exc:
            last_err = str(exc)
        if attempt < 2:
            time.sleep(1.0 * (attempt + 1))
    _die(f"TTS failed after 3 attempts: {last_err}")


# ── Concurrent synthesis ─────────────────────────────────────────────────────────

# The TTS service gates concurrent synths at TTS_MAX_CONCURRENCY (default 4) and
# pins each Kokoro inference to a single thread, so ~4 in-flight requests saturate
# it; going wider just queues server-side. Match that default here.
TTS_CONCURRENCY_DEFAULT = 4


class _Synth:
    """A pending synth request inside an ordered parts list. `synthesize_ordered`
    resolves these concurrently; silence segments in the same list are plain bytes
    that need no network call and pass straight through."""

    __slots__ = ("text", "voice")

    def __init__(self, text: str, voice: str) -> None:
        self.text = text
        self.voice = voice


def synthesize_ordered(
    parts: list,
    *,
    tts_url: str,
    speed: float,
    language: str,
    token: str | None,
    concurrency: int = TTS_CONCURRENCY_DEFAULT,
    label: str = "synth",
) -> bytes:
    """Resolve every `_Synth` in `parts` concurrently (bounded pool), preserving the
    original order, and return the concatenated PCM.

    `requests.post` releases the GIL while it blocks on the network, so a thread
    pool — not asyncio — is enough to keep `concurrency` requests in flight. The
    client used to synth strictly one chunk at a time, leaving the multi-worker TTS
    service idle between calls; this exploits its existing concurrency gate.
    `synthesize()` already retries and `_die()`s on hard failure, so a worker that
    fails propagates that exit through `fut.result()`.
    """
    jobs = [(i, part) for i, part in enumerate(parts) if isinstance(part, _Synth)]
    total = len(jobs)
    if total == 0:
        return b"".join(parts)
    workers = max(1, min(concurrency, total))
    results: dict[int, bytes] = {}
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(
                synthesize, job.text, tts_url=tts_url, voice=job.voice,
                speed=speed, language=language, token=token,
            ): idx
            for idx, job in jobs
        }
        for fut in concurrent.futures.as_completed(futs):
            results[futs[fut]] = fut.result()
            done += 1
            _log(f"🗣️  [{done}/{total}] {label} ✓")
    results = _balance_voice_levels(parts, results)
    return b"".join(
        results[i] if isinstance(part, _Synth) else part
        for i, part in enumerate(parts)
    )


def _balance_voice_levels(parts: list, results: dict[int, bytes]) -> dict[int, bytes]:
    """Match each voice's loudness to the quietest voice so no speaker sits hotter
    than another in a multi-voice mix (Kokoro voices differ in inherent level — e.g.
    `am_liam` reads louder than `af_nova`). Per-voice RMS → gain toward the quietest
    voice, so it is pure attenuation and no chunk can clip; the downstream `loudnorm`
    pass in `pcm_to_mp3` restores absolute level. A no-op when every chunk shares one
    voice (one-voice path) or when levels already match."""
    synth_idx = [i for i, p in enumerate(parts) if isinstance(p, _Synth)]
    arrs = {i: np.frombuffer(results[i], dtype=np.int16).astype(np.float64) for i in synth_idx}
    agg: dict[str, list[float]] = {}  # voice -> [sum_of_squares, sample_count]
    for i in synth_idx:
        v = agg.setdefault(parts[i].voice, [0.0, 0])
        v[0] += float(np.dot(arrs[i], arrs[i]))
        v[1] += arrs[i].size
    rms = {vc: (ssq / n) ** 0.5 for vc, (ssq, n) in agg.items() if n and ssq > 0}
    if len(rms) < 2:
        return results  # one voice (or silence) — nothing to balance
    target = min(rms.values())
    gains = {vc: (target / r) for vc, r in rms.items()}
    _log("🔊 voice balance: " + ", ".join(f"{vc}×{gains[vc]:.2f}" for vc in sorted(gains)))
    out = dict(results)
    for i in synth_idx:
        g = gains.get(parts[i].voice, 1.0)
        if abs(g - 1.0) < 1e-3:
            continue
        out[i] = np.clip(arrs[i] * g, -32768, 32767).astype(np.int16).tobytes()
    return out


# ── Chunks → ordered parts list (shared by both front-ends) ──────────────────────

def _silence(seconds: float) -> bytes:
    """Raw s16le @ 24 kHz mono silence of `seconds` length (2 bytes/sample)."""
    return b"\x00" * (int(max(0.0, seconds) * KOKORO_SAMPLE_RATE) * 2)


def chunks_to_parts(
    chunks: list[str], voice: str, *,
    gap: float, section_gap: float,
    emphasis_gap: float = 0.0,
) -> list:
    """Turn a chunk list (plain strings, `_SECTION_SENTINEL`s, and `Emph` spans) into the
    ordered parts list `synthesize_ordered` consumes: `_Synth` markers interleaved with
    silence bytes. Shared by the one-voice pipeline (one voice per doc) and dialogue (one voice
    per turn) so emphasis + pacing behave identically in both — the drift-prone seam, made
    structural. `gap` is the normal inter-chunk pause, `section_gap` the longer topic-break; an
    `Emph` span is BRACKETED by an `emphasis_gap` beat before AND after it (same speaking tempo,
    no slowdown), which sets the clause apart by silence the way a narrator pauses around a key
    line. Everything synthesizes at the base voice/speed.
    """
    gap_b = _silence(gap)
    section_b = _silence(section_gap)
    emph_b = _silence(emphasis_gap)
    parts: list = []
    prev_synth = False          # did the previous emitted part synthesize a chunk?
    prev_chunk: str | None = None
    for chunk in chunks:
        if chunk == _SECTION_SENTINEL:
            parts.append(section_b)
            prev_synth = False                       # the section silence stands in for any gap
            continue
        if prev_synth:
            # Silence between two consecutive spoken chunks: a beat that brackets an emphasized
            # span (either side adjacent to an `Emph` uses `emph_b`, so the span is flanked
            # symmetrically — never the normal gap stacked on top); the normal `gap` otherwise.
            adjacent_emph = isinstance(chunk, Emph) or isinstance(prev_chunk, Emph)
            parts.append(emph_b if (adjacent_emph and emphasis_gap > 0) else gap_b)
        parts.append(_Synth(chunk, voice))           # base voice + speed — no slowdown, ever
        prev_synth = True
        prev_chunk = chunk
    return parts


# ── PCM → MP3 (ffmpeg) ───────────────────────────────────────────────────────────

def pcm_to_mp3(
    pcm: bytes, *, title: str | None, bitrate: str, volume: float = 1.0,
    loudness: float | None = -16.0,
) -> bytes:
    if not _which("ffmpeg"):
        _die("ffmpeg not found on PATH — install it (brew install ffmpeg).")
    fd, tmp = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "s16le", "-ar", str(KOKORO_SAMPLE_RATE), "-ac", "1", "-i", "pipe:0",
        ]
        # Filter chain: loudnorm to a target integrated loudness FIRST (EBU R128 — boosts
        # the quiet ~-28 LUFS Kokoro PCM to the podcast norm while compressing + limiting
        # peaks, which linear gain can't do without clipping), then an optional extra
        # linear trim with a soft limiter behind it.
        filters = []
        if loudness:
            filters.append(f"loudnorm=I={loudness}:TP=-1.5:LRA=11")
        if volume and volume != 1.0:
            filters.append(f"volume={volume},alimiter=limit=0.97")
        if filters:
            cmd += ["-af", ",".join(filters)]
        cmd += ["-codec:a", "libmp3lame", "-b:a", bitrate]
        if title:
            cmd += ["-metadata", f"title={title}"]
        cmd += ["-f", "mp3", tmp]
        proc = subprocess.run(cmd, input=pcm, capture_output=True)
        if proc.returncode != 0:
            _die(f"ffmpeg failed: {proc.stderr.decode(errors='replace')[:400]}")
        return Path(tmp).read_bytes()
    finally:
        Path(tmp).unlink(missing_ok=True)


def _which(name: str) -> bool:
    from shutil import which
    return which(name) is not None


# ── podcast server upload (PUT /upload/{slug}/{name}, see services/podcast/app.py) ─

def upload_to_podcast(
    data: bytes, *, filename: str, slug: str, base_url: str, secret: str,
    duration_seconds: float | None = None, published_at: str | None = None,
    content_type: str = "audio/mpeg",
) -> str:
    """PUT one file (MP3 or transcript sidecar) onto a podcast show's volume folder.
    Service-token auth: an HS256 bearer with aud="podcast-upload" signed by the show
    server's PODCAST_UPLOAD_SECRET. The producer already holds the bytes + duration, so
    this is a single direct PUT (the server's /import pull-path is migration-only). For an
    MP3, X-Duration-Seconds (integer seconds) and X-Published-At (ISO-8601 with offset) ride
    along so the feed gets <itunes:duration> + <pubDate> without ffprobe or a mtime fallback."""
    now = int(time.time())
    bearer = _hs256_jwt({"aud": "podcast-upload", "iat": now, "exp": now + 1800}, secret)
    headers = {"Authorization": f"Bearer {bearer}", "Content-Type": content_type}
    if duration_seconds is not None:
        headers["X-Duration-Seconds"] = str(int(round(duration_seconds)))
    if published_at is not None:
        headers["X-Published-At"] = published_at
    url = f"{base_url}/upload/{slug}/{filename}"
    # Retry transient network/5xx failures (a large MP3 over a slow uplink can time out
    # mid-write); auth and 4xx fail closed immediately. Mirrors synthesize()'s retry shape.
    last_err = None
    for attempt in range(3):
        try:
            # (connect, read) — generous read window so a large episode over a normal
            # uplink isn't cut off mid-transfer.
            r = requests.put(url, data=data, headers=headers, timeout=(30, 1800))
            if r.status_code == 200:
                return f"{slug}/{filename}"
            if r.status_code in (401, 403):
                _die(f"podcast upload auth rejected ({r.status_code}): {r.text[:200]}")
            if r.status_code < 500:
                # 404 here means the show slug isn't registered on the server — fail loud.
                _die(f"podcast upload failed ({r.status_code}) for show {slug!r}: {r.text[:200]}")
            last_err = f"{r.status_code}: {r.text[:200]}"
        except requests.RequestException as exc:
            last_err = str(exc)
        if attempt < 2:
            time.sleep(2.0 * (attempt + 1))
    _die(f"podcast upload failed after 3 attempts for show {slug!r}: {last_err}")


def set_podcast_episode_title(
    title: str, *, filename: str, slug: str, base_url: str, secret: str
) -> None:
    """Set an episode's polished feed `<title>` (Episode.title) after publish. A plain MP3 upload
    carries no feed title, so without this the feed shows the prettified filename. Non-fatal: the
    title is cosmetic, so a failure warns rather than aborting the (already-published) episode."""
    now = int(time.time())
    bearer = _hs256_jwt({"aud": "podcast-upload", "iat": now, "exp": now + 1800}, secret)
    url = f"{base_url}/show/{slug}/ep/{filename}/meta"
    try:
        r = requests.post(url, json={"title": title},
                          headers={"Authorization": f"Bearer {bearer}"}, timeout=(15, 60))
    except requests.RequestException as exc:
        _log(f"⚠️  feed-title set failed (network): {exc}")
        return
    if r.status_code != 200:
        _log(f"⚠️  feed-title set failed ({r.status_code}): {r.text[:160]} "
             f"(deploy podcast-server → main for the /meta endpoint)")


# Publish dates are stamped in Pacific time and sent as ISO-8601 with offset (the feed turns
# them into RFC-822 <pubDate>). Pacific is the house timezone for these shows.
_PACIFIC = ZoneInfo("America/Los_Angeles")


def _now_pacific_iso() -> str:
    """The current publish moment as ISO-8601 with the Pacific offset, e.g. 2026-06-20T14:30:00-07:00."""
    return datetime.now(_PACIFIC).replace(microsecond=0).isoformat()


def _epoch_to_pacific_iso(ts: float) -> str:
    """An epoch timestamp (a file mtime) → ISO-8601 in Pacific — used to pin a recut's original
    publish date when the episode row never carried an explicit one (it fell back to mtime)."""
    return datetime.fromtimestamp(ts, _PACIFIC).replace(microsecond=0).isoformat()


def _list_show_episodes(slug: str, *, base_url: str, secret: str) -> list[dict]:
    """GET /show/{slug}/episodes (service-token) → [{name, size, mtime}]. The on-disk *.mp3
    set the feed is built from — used to confirm a recut target exists and to verify a replace."""
    now = int(time.time())
    bearer = _hs256_jwt({"aud": "podcast-upload", "iat": now, "exp": now + 1800}, secret)
    r = requests.get(
        f"{base_url}/show/{slug}/episodes",
        headers={"Authorization": f"Bearer {bearer}"}, timeout=30,
    )
    if r.status_code in (401, 403):
        _die(f"podcast episode-list auth rejected ({r.status_code}): {r.text[:200]}")
    if r.status_code == 404:
        _die(f"show {slug!r} not found, or the server lacks /show/<slug>/episodes "
             f"(deploy podcast-server → main first).")
    if r.status_code != 200:
        _die(f"podcast episode-list failed ({r.status_code}): {r.text[:200]}")
    return r.json().get("episodes", [])


def _get_show_meta(slug: str, *, base_url: str, secret: str) -> dict | None:
    """GET /show/{slug} (service-token) → {title, description, feed_url, episode_count, …} for
    the publish preflight. Returns None (never fatal) if the server lacks the endpoint (older
    deploy) or the lookup fails — the preflight degrades to the slug rather than blocking a synth."""
    now = int(time.time())
    bearer = _hs256_jwt({"aud": "podcast-upload", "iat": now, "exp": now + 1800}, secret)
    try:
        r = requests.get(
            f"{base_url}/show/{slug}",
            headers={"Authorization": f"Bearer {bearer}"}, timeout=15,
        )
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    return r.json()


def _next_recut_name(mp3_name: str, existing: set[str]) -> str:
    """Bump `foo.mp3` → `foo-r2.mp3` (next free `-rN`) for --force-redownload — a NEW filename,
    hence a new feed GUID, so existing subscribers re-pull. Strips any current `-rN` so revisions
    don't stack (`foo-r2` → `foo-r3`, not `foo-r2-r2`)."""
    base = mp3_name[:-4] if mp3_name.lower().endswith(".mp3") else mp3_name
    m = re.match(r"^(.*)-r(\d+)$", base)
    root = m.group(1) if m else base
    n = 2
    while f"{root}-r{n}.mp3" in existing:
        n += 1
    return f"{root}-r{n}.mp3"


def _publish_episode(
    *, mp3: bytes, description_source: str, doc_name: str, filename: str, args,
    secret: str, base_url: str, duration_seconds: float | None,
    published_at: str | None = None, show_notes: str = "",
) -> str:
    """Publish one finished episode to its podcast show — the single upload sink shared by
    the one-voice (_process_doc) and two-voice (dialogue.process_dialogue) pipelines, so the
    publish seam can't drift between them. PUTs the MP3 onto the show's volume (it then appears
    in the show's RSS feed) and, unless --no-transcript, writes a brief Sonnet-authored episode
    description as the `<base>.md` sidecar the feed shows as <description> (from description_source,
    the episode's spoken script — NOT the truncated transcript)."""
    show = args.show
    published_at = published_at or _now_pacific_iso()
    _log(f"⬆️  [{doc_name}] publishing to podcast show {show!r} at {base_url} ({filename})…")
    loc = upload_to_podcast(
        mp3, filename=filename, slug=show, base_url=base_url,
        secret=secret, duration_seconds=duration_seconds, published_at=published_at,
    )
    _log(f"✅ {doc_name} → podcast/{loc}  (dur {int(round(duration_seconds or 0))}s, pub {published_at})")

    # Set the polished feed <title> (Episode.title) — a plain upload doesn't carry one, so without
    # this the feed shows the prettified filename. Uses --title when given.
    title = (getattr(args, "title", None) or "").strip()
    if title:
        set_podcast_episode_title(title, filename=filename, slug=show, base_url=base_url, secret=secret)
        _log(f"🏷️  {doc_name} → feed title: {title!r}")

    # Brief blurb (`<base>.md`) → the feed's short <description>. Always published — it's the
    # episode preview shown in app lists.
    sname = _podcast_sidecar_name(filename)
    _log(f"📝 [{doc_name}] writing episode description ({args.narrative_model})…")
    desc = _episode_description(description_source, model=args.narrative_model)
    upload_to_podcast(
        desc.encode("utf-8"), filename=sname, slug=show,
        base_url=base_url, secret=secret, content_type="text/markdown; charset=utf-8",
    )
    _log(f"📝 {doc_name} → podcast description: {show}/{sname}\n   “{desc}”")

    # Full transcript (`<base>-transcript.md`) → the feed's <content:encoded> rich show notes.
    # --no-transcript opts out of the transcript only (the blurb above still publishes).
    if not args.no_transcript:
        tname = _podcast_transcript_name(filename)
        # The transcript sidecar is the spoken script PLUS any `<!-- shownotes -->` footer (citations
        # / Sources) — the footer rides in the show notes (<content:encoded>) but was never voiced.
        transcript_md = description_source
        if show_notes:
            transcript_md = description_source.rstrip() + "\n\n" + show_notes.strip() + "\n"
        upload_to_podcast(
            transcript_md.encode("utf-8"), filename=tname, slug=show,
            base_url=base_url, secret=secret, content_type="text/markdown; charset=utf-8",
        )
        extra = f" (+{len(show_notes)} chars show-notes footer)" if show_notes else ""
        _log(f"📝 {doc_name} → podcast transcript: {show}/{tname}{extra}")
    return loc


def _podcast_sidecar_name(mp3_filename: str) -> str:
    """`foo.mp3` → `foo.md` — the brief-blurb sidecar the feed turns into <description>.
    The server matches it by `with_suffix('.md')` (storage.list_audio), so it must be the
    SAME base as the MP3, or the description won't attach."""
    base = mp3_filename[:-4] if mp3_filename.lower().endswith(".mp3") else mp3_filename
    return f"{base}.md"


def _podcast_transcript_name(mp3_filename: str) -> str:
    """`foo.mp3` → `foo-transcript.md` — the full-transcript sidecar the feed renders as
    <content:encoded> rich show notes. The server pairs it as `<stem>-transcript.md`
    (storage.list_audio) — distinct from the `<base>.md` blurb so both can coexist."""
    base = mp3_filename[:-4] if mp3_filename.lower().endswith(".mp3") else mp3_filename
    return f"{base}-transcript.md"


def _safe_mp3_name(doc_path: Path, override: str | None, audience: str = "technical") -> str:
    name = override or doc_path.stem
    if not override and audience in ("exec", "pm"):
        name = f"{name}-{audience}"     # sit beside the full reading, not clobber it
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    if not name:
        name = "document"
    if not name.lower().endswith(".mp3"):
        name += ".mp3"
    return name[:255]


# ── Per-document pipeline (shared by single- and multi-doc runs) ─────────────────

SHOWNOTES_SENTINEL = "<!-- shownotes -->"


def _split_shownotes(text: str) -> tuple[str, str]:
    """Split an authored source on a `<!-- shownotes -->` line into (spoken, show_notes).

    Everything AFTER the sentinel is Show Notes: NEVER synthesized (not read aloud) and NOT fed to
    the description-blurb pass — but appended to the published `-transcript.md` sidecar (→ the feed's
    <content:encoded>). It is the durable home for a citation / Sources footer that must ride in the
    show notes and survive every recut (the spoken script is the source of truth; a footer here is
    reproduced on every cut). No sentinel → (text, "").
    """
    out: list[str] = []
    notes: list[str] = []
    hit = False
    for ln in text.splitlines():
        if not hit and ln.strip().lower() == SHOWNOTES_SENTINEL:
            hit = True
            continue
        (notes if hit else out).append(ln)
    return ("\n".join(out).rstrip() + "\n", "\n".join(notes).strip())


def _normalize_and_chunk(doc: Path, args) -> tuple[list[str], str, str, str, int, int]:
    """Read a doc → speakable prose → optional LLM pass → pron overrides → chunks.

    Returns (chunks, title, transcript_md, show_notes, raw_chars, speakable_chars). No network
    beyond the optional LLM pass; safe to call from a worker thread (one per doc).
    """
    raw = doc.read_text(encoding="utf-8")
    raw, show_notes = _split_shownotes(raw)
    if show_notes:
        _log(f"📓 [{doc.name}] show-notes footer found ({len(show_notes)} chars) — kept for the "
             f"transcript sidecar (<content:encoded>), excluded from audio + the description blurb.")
    # Narrative modes distill the ideas, never read code aloud — drop the code cue.
    code_cue = "" if args.narrative else "Code block omitted."
    speakable, h1 = normalize_markdown(raw, code_cue=code_cue, section_breaks=True)
    if args.narrative:
        system = _PM_SYSTEM if args.narrative == "pm" else _EXEC_SYSTEM
        label = f"{args.narrative} narrative"
        _log(f"🎙️  [{doc.name}] {label} pass ({args.narrative_model})…")
        speakable = _llm_pass(
            speakable, system=system, model=args.narrative_model,
            seg_chars=EXEC_SEG_CHARS, max_tokens=8000, label=label,
        )
    elif args.scrub:
        _log(f"🧽 [{doc.name}] LLM scrub pass ({args.scrub_model})…")
        speakable = _llm_pass(
            speakable, system=_SCRUB_SYSTEM, model=args.scrub_model,
            seg_chars=SCRUB_SEG_CHARS, max_tokens=8000, label="scrub",
        )
    elif getattr(args, "emphasize", False):
        # Authored text in → emphasis markers added, words preserved verbatim (verified).
        _log(f"✍️  [{doc.name}] emphasis-only pass ({args.narrative_model}) — no rewriting…")
        speakable = _emphasis_pass(speakable, model=args.narrative_model)

    # The LLM can emit markdown emphasis (*word*, **bold**), inline code, or link
    # syntax despite the "no markers" instruction — and normalize_markdown only ran
    # on the SOURCE, before the LLM rewrote it. Re-strip inline markup from the LLM
    # output so Kokoro never voices a literal asterisk. Same _strip_inline the source
    # path already applies — one happy path, no second stripper to drift.
    if args.narrative or args.scrub:
        speakable = strip_llm_markup(speakable)

    # Title (used for the MP3 metadata AND the transcript heading).
    if args.title:
        title = args.title
    else:
        title = h1 or doc.stem
        if args.narrative:
            title = f"{title} — {args.narrative} narrative"

    # The full spoken script (`transcript_md`) — `_publish_episode` feeds it to the Sonnet
    # episode-description pass. Captured BEFORE pron respelling so it reads cleanly ('content',
    # not 'con-tent'). Read/emphasize mode → the source markdown (section headers intact,
    # frontmatter dropped); narrative mode → the distilled script under a title heading.
    if args.narrative:
        transcript_md = f"# {title}\n\n{speakable.strip()}\n"
    else:
        transcript_md = _FRONTMATTER_RE.sub("", raw).strip() + "\n"
    # The «…» marks steer synth; they are not punctuation a reader should see. Drop them
    # from the human-readable transcript (keep the words).
    transcript_md = transcript_md.replace("«", "").replace("»", "")

    # Pronunciation: deterministic whole-word/phrase respellings from pron_overrides.json
    # (Kokoro/espeak pronounces most long words correctly on its own; the overrides file
    # carries the exceptions — acronyms said as letters, numbers, and the rare botched
    # word/name). This is the single text-level pronunciation lever; for surgical IPA see
    # the kokoro-onnx phoneme path. (A prior LLM "normalize/respell" pass was removed: it
    # mangled long-but-ordinary words espeak already says right — e.g. "reconciliation"
    # → "reck-un-sil-ee-AY-shun" — making things worse, not better.)
    overrides = resolve_pron_overrides(
        no_pron=args.no_pron, pron_files=args.pron_file, ad_hoc=args.pron
    )
    if overrides:
        speakable = apply_pron_overrides(speakable, overrides)
        _log(f"🗣️  [{doc.name}] applied {len(overrides)} pronunciation override(s)")
    if args.save_script:
        # Write the full distilled/cleaned spoken text — the transcript of what gets voiced.
        # Single doc → exact path; multiple docs → infix the doc stem so they don't clobber.
        sp = args.save_script
        out_path = sp if len(args.docs) == 1 else sp.with_name(f"{sp.stem}.{doc.stem}{sp.suffix or '.txt'}")
        out_path.write_text(speakable, encoding="utf-8")
        _log(f"📝 [{doc.name}] saved spoken script → {out_path}")
    chunks = split_emphasis_chunks(speakable, args.max_chars)
    emph_chars = sum(len(c) for c in chunks if isinstance(c, Emph))
    spoken_chars = sum(len(c) for c in chunks if c != _SECTION_SENTINEL)
    if emph_chars and spoken_chars:
        pct = 100 * emph_chars / spoken_chars
        _log(f"✨ [{doc.name}] emphasis: {emph_chars}/{spoken_chars} chars ({pct:.0f}%) bracketed")
        if pct > 5:
            _log(f"⚠️  [{doc.name}] emphasis is {pct:.0f}% of spoken text (>5%) — it lands best kept rare.")
    if args.max_pause_gap > 0:
        before = sum(1 for c in chunks if c == _SECTION_SENTINEL)
        chunks = enforce_pause_cap(chunks, int(args.max_pause_gap * CHARS_PER_SECOND))
        added = sum(1 for c in chunks if c == _SECTION_SENTINEL) - before
        if added:
            _log(f"⏸️  [{doc.name}] inserted {added} extra pause(s) (max {args.max_pause_gap:g}s/stretch)")
    if not chunks:
        _die(f"Nothing speakable in {doc.name} (empty or all code/markup).")

    return chunks, title, transcript_md, show_notes, len(raw), sum(len(c) for c in chunks)


def _resolve_tts_endpoint(args) -> tuple[str, str | None]:
    """Resolve the TTS base URL and (for prod) a freshly-minted aud=tts token. Once
    per run — the token is shared across all docs."""
    if args.tts_url:
        return args.tts_url.rstrip("/"), None
    if args.local_tts:
        return LOCAL_TTS_URL, None
    _log("🔑 pulling shared-svcs JWT_SECRET from Railway…")
    secret = _railway_vars(
        project=SHARED_SVCS["project"], env=SHARED_SVCS["env"], service=SHARED_SVCS["tts_service"]
    ).get("JWT_SECRET")
    if not secret:
        _die("JWT_SECRET not found on the shared-svcs TTS service.")
    now = int(time.time())
    return PROD_TTS_URL, _hs256_jwt({"iss": "doc-to-speech", "aud": "tts", "exp": now + 1800}, secret)


def _wait_for_tts_ready(tts_url: str, *, timeout: float = 300.0) -> None:
    """Poll GET /health until the TTS service answers 200, then return.

    With Railway serverless sleep enabled, the prod TTS sleeps when idle and wakes on
    the first request (~30–40 s cold start to load the Kokoro model); /health returns
    503 'loading' until ready. Polling here both *wakes* the service and *gates* synth
    on readiness, so the real synth requests don't race the cold start (which would
    otherwise burn synthesize()'s 3 short retries and fail). No-op-fast when already
    awake — one 200 and we return. timeout<=0 skips the poll entirely.
    """
    if timeout <= 0:
        return
    deadline = time.time() + timeout
    waking = False
    while True:
        try:
            r = requests.get(f"{tts_url}/health", timeout=10)
            if r.status_code == 200:
                if waking:
                    _log("✅ TTS awake.")
                return
            # 503 (model loading) or a transient 5xx during boot — keep waiting.
        except requests.RequestException:
            pass  # connection refused/reset while the container spins up — keep waiting
        if time.time() >= deadline:
            _die(f"TTS not ready after {timeout:g}s at {tts_url}/health — still asleep or failing to boot.")
        if not waking:
            _log(f"⏳ waking TTS at {tts_url} (serverless cold start ~30–40s)…")
            waking = True
        time.sleep(3.0)


def _resolve_podcast_target(args) -> tuple[str, str]:
    """Resolve the podcast server's upload secret + base URL. Once per run. The synth
    target when --show is set; same trust boundary as the migrate client (PODCAST_UPLOAD_SECRET).
    The secret always comes from Railway; --podcast-url overrides only the base URL (point at a
    local server for testing — the token still validates if the local server shares the secret)."""
    _log("🔑 pulling podcast PODCAST_UPLOAD_SECRET from Railway…")
    pod_vars = _railway_vars(
        project=PODCAST["project"], env=PODCAST["env"], service=PODCAST["service"]
    )
    secret = pod_vars.get("PODCAST_UPLOAD_SECRET")
    if not secret:
        _die("PODCAST_UPLOAD_SECRET not found on the podcast service.")
    override = getattr(args, "podcast_url", None)
    base_url = (override or pod_vars.get("PODCAST_PUBLIC_BASE") or PODCAST_FALLBACK_URL).rstrip("/")
    return secret, base_url


def _process_doc(
    doc: Path, args, *, tts_url: str, tts_token: str | None,
    secret: str | None, base_url: str | None, chunk_concurrency: int,
    published_at: str | None = None,
) -> str | None:
    """Full pipeline for one document: normalize → synth → MP3 → publish. Returns the
    podcast location, or None when --no-upload. Endpoint/secrets are resolved once by
    the caller and passed in, so this is safe to run concurrently (one call per doc)."""
    chunks, title, transcript_md, show_notes, raw_len, total_chars = _normalize_and_chunk(doc, args)
    _log(f"📄 {doc.name}: {raw_len} raw → {total_chars} speakable → {len(chunks)} chunks")

    # Build the ordered parts list (silence bytes + _Synth markers). Normal gap after
    # each spoken chunk, unless the next is a section break (own longer silence) or last.
    parts = chunks_to_parts(
        chunks, args.voice, gap=args.gap, section_gap=args.section_gap,
        emphasis_gap=args.emphasis_gap,
    )
    pcm = synthesize_ordered(
        parts, tts_url=tts_url, speed=args.speed, language=args.language,
        token=tts_token, concurrency=chunk_concurrency, label=doc.stem,
    )
    seconds = len(pcm) / 2 / KOKORO_SAMPLE_RATE
    _log(f"🎚️  [{doc.name}] {seconds/60:.1f} min of audio → MP3 ({args.bitrate})…")

    mp3 = pcm_to_mp3(pcm, title=title, bitrate=args.bitrate, volume=args.volume,
                     loudness=args.loudness)
    _log(f"💿 [{doc.name}] MP3: {len(mp3)/1_000_000:.1f} MB")

    if args.out:
        args.out.write_bytes(mp3)
        _log(f"💾 wrote {args.out}")
    if args.no_upload:
        if not args.out:
            _die("--no-upload given but no --out path — nothing would be saved.")
        return None

    filename = _safe_mp3_name(doc, args.name, args.narrative)
    return _publish_episode(
        mp3=mp3, description_source=transcript_md, doc_name=doc.name, filename=filename,
        args=args, secret=secret, base_url=base_url, duration_seconds=seconds,
        published_at=published_at, show_notes=show_notes,
    )


# The CLI lives in doc_to_audio.py. This module is the shared TTS engine +
# monologue (one-voice) render pipeline, imported by doc_to_audio and dialogue.
