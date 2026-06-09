#!/usr/bin/env python3
"""
doc_to_speech — turn a markdown/text document into an MP3 and land it in oxp.files.

Pipeline:
  read doc → normalize markdown to *speakable* prose → chunk to ≤ N chars
  → synthesize each chunk via the shared-svcs TTS service (Kokoro) as raw PCM
  → concatenate (with a short pause between chunks) → ffmpeg transcode to MP3
  → upload to the oxp.files Tigris bucket via a presigned PUT.

This is an on-demand driver — run it per document; nothing is deployed. It is the
first production consumer of services/tts.

Why each step exists (the gaps between the raw TTS service and "MP3 in oxp.files"):
  • TTS emits WAV/PCM, never MP3            → ffmpeg transcodes (MP3 is ~12× smaller
                                              and plays on every car head unit).
  • TTS caps input at 800 chars/request     → chunk sentence-aware, synth, concat.
  • Markdown read verbatim is unlistenable  → strip code/tables/link-syntax/headings
                                              into spoken prose first.

Usage:
  uv run python scripts/doc_to_speech.py <doc.md> [options]

  # Full run against prod TTS, upload to the "briefings" folder (the default):
  uv run python scripts/doc_to_speech.py PLAN.md --speed 1.1

  # Strip file paths / names / scaffolding via a cheap LLM so you hear only the meat:
  uv run python scripts/doc_to_speech.py PLAN.md --scrub --speed 1.1

  # Executive recap for a non-technical leadership audience — shorter, low/no jargon,
  # business/pricing/branding/market-insight boosted over detail, all paths/filenames
  # scrubbed. Implies an LLM pass; output auto-named "<doc>-exec.mp3" so it sits beside
  # the full technical reading rather than clobbering it:
  uv run python scripts/doc_to_speech.py PREHAB.md --audience exec

  # See exactly what will be spoken — no synthesis (with --scrub or --audience exec,
  # shows the LLM-transformed text):
  uv run python scripts/doc_to_speech.py PLAN.md --scrub --dry-run

  # Use a locally-running TTS (services/tts on :8102, auth-off in dev):
  uv run python scripts/doc_to_speech.py PLAN.md --local-tts

  # Produce a local MP3 only, skip the oxp.files upload:
  uv run python scripts/doc_to_speech.py PLAN.md --no-upload --out /tmp/doc.mp3

Auth (see symlink_docs/registries/AUTH_REGISTRY.md scenarios 4 + 5):
  • TTS prod   — HS256 JWT, aud="tts", signed with the shared-svcs JWT_SECRET.
  • oxp.files  — HS256 session bearer {sub,iat,exp}, signed with the files JWT_SECRET.
  Both secrets are pulled live from Railway (Railway GraphQL API); nothing is written
  to disk. --local-tts skips the TTS secret; --no-upload skips the files secret.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

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
OXP_KB = {
    "project": "96a6d9dd-b680-4821-bee6-ed850a19074b",
    "env": "30bf77ef-ec92-472d-b92a-93e3806bd7e4",
    "files_service": "56aebab1-320e-48d2-9053-44cacc82c241",
}

PROD_TTS_URL = "https://shared-svcs-tts.up.railway.app"
LOCAL_TTS_URL = "http://localhost:8102"
OXP_FILES_FALLBACK_URL = "https://oxp.files.strongprompt.ai"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_SCRUB_MODEL = "google/gemini-3.5-flash"  # cheap; matches kb's backup LLM — mechanical cleanup
DEFAULT_EXEC_MODEL = "anthropic/claude-opus-4.8"  # exec recap needs judgment — matches doc_to_podcast's script model
SCRUB_SEG_CHARS = 6000   # scrub output ≈ input size → cap input to bound output tokens
EXEC_SEG_CHARS = 24000   # exec recap output ≪ input → input can be larger (most docs → one pass)

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
    _die("No OpenRouter key for --scrub. Expected ~/.config/keys.json → openrouter.")


# ── LLM passes: audience-specific rewrites of the speakable prose ─────────────────
#
# Two audiences, two system prompts, one request path (_llm_pass):
#   technical (default) — optional --scrub: content-preserving cleanup, full reading.
#   exec               — always: a short, jargon-free executive recap.

_SCRUB_SYSTEM = (
    "You are preparing a document to be read aloud as audio. Rewrite the text so it "
    "flows naturally when spoken. Remove or naturalize anything that is unlistenable: "
    "file paths, file names, directory names, URLs, code and CLI fragments, and "
    "cross-reference scaffolding (e.g. 'see X.md section Y', 'per the registry'). "
    "Preserve ALL substantive ideas, arguments, and detail — do NOT summarize, "
    "shorten, or editorialize the actual content. Do not add commentary, headings, "
    "preamble, or markers. Output only the cleaned text."
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
    "to grasp the thesis, the bet, and the open decisions.\n\n"
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
    text: str, *, system: str, model: str, api_key: str,
    seg_chars: int, max_tokens: int, label: str,
) -> str:
    """Run one LLM rewrite over the speakable prose, segmenting so each call's output
    never truncates. `system` selects the transform (content-preserving scrub vs.
    executive recap); the request shape is identical across audiences — one happy path."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    out_parts: list[str] = []
    segments = _segment(text, seg_chars)
    for i, seg in enumerate(segments, 1):
        if len(segments) > 1:
            _log(f"   {label} segment {i}/{len(segments)}…")
        body = {
            "model": model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": seg},
            ],
        }
        last_err = None
        for attempt in range(3):
            try:
                r = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=120)
                if r.status_code == 200:
                    out = r.json()["choices"][0]["message"]["content"].strip()
                    if not out:
                        _die(f"{label} LLM returned empty content")
                    out_parts.append(out)
                    break
                if r.status_code in (401, 403):
                    _die(f"OpenRouter auth rejected ({r.status_code}): {r.text[:200]}")
                last_err = f"{r.status_code}: {r.text[:200]}"
            except (requests.RequestException, KeyError, ValueError) as exc:
                last_err = str(exc)
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
        else:
            _die(f"{label} LLM failed after 3 attempts: {last_err}")
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


# ── PCM → MP3 (ffmpeg) ───────────────────────────────────────────────────────────

def pcm_to_mp3(pcm: bytes, *, title: str | None, bitrate: str) -> bytes:
    if not _which("ffmpeg"):
        _die("ffmpeg not found on PATH — install it (brew install ffmpeg).")
    fd, tmp = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "s16le", "-ar", str(KOKORO_SAMPLE_RATE), "-ac", "1", "-i", "pipe:0",
            "-codec:a", "libmp3lame", "-b:a", bitrate,
        ]
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


# ── oxp.files upload (presigned PUT, see services/files/main.py) ─────────────────

def upload_to_oxp(
    mp3: bytes, *, filename: str, folder: str, base_url: str, secret: str, email: str
) -> str:
    now = int(time.time())
    bearer = _hs256_jwt({"sub": email, "iat": now, "exp": now + 600}, secret)
    auth = {"Authorization": f"Bearer {bearer}"}

    r = requests.post(
        f"{base_url}/api/files/upload-url",
        headers={**auth, "Content-Type": "application/json"},
        json={"filename": filename, "folder": folder},
        timeout=30,
    )
    if r.status_code != 200:
        _die(f"upload-url failed ({r.status_code}): {r.text[:200]}")
    put_url = r.json()["url"]

    put = requests.put(put_url, data=mp3, headers={"Content-Type": "audio/mpeg"}, timeout=300)
    if put.status_code not in (200, 201):
        _die(f"presigned PUT failed ({put.status_code}): {put.text[:200]}")

    # Activity-log the upload (best-effort; non-fatal).
    try:
        requests.post(
            f"{base_url}/api/files/uploaded",
            headers={**auth, "Content-Type": "application/json"},
            json={"filename": filename, "folder": folder},
            timeout=30,
        )
    except requests.RequestException:
        pass

    loc = f"{folder}/{filename}" if folder else filename
    return loc


# ── Filename helpers (match services/files _safe_filename rules) ─────────────────

def _safe_mp3_name(doc_path: Path, override: str | None, audience: str = "technical") -> str:
    name = override or doc_path.stem
    if not override and audience == "exec":
        name = f"{name}-exec"          # sit beside the full reading, not clobber it
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    if not name:
        name = "document"
    if not name.lower().endswith(".mp3"):
        name += ".mp3"
    return name[:255]


# ── Main ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        prog="doc_to_speech",
        description="Convert a markdown/text document to an MP3 and land it in oxp.files.",
    )
    p.add_argument("doc", type=Path, help="Path to the source document (markdown or text).")
    p.add_argument("--folder", default="briefings",
                   help="oxp.files folder (default: briefings — the private podcast folder).")
    p.add_argument("--voice", default="af_nova", help="Kokoro voice (must be in TTS allowlist).")
    p.add_argument("--no-pron", action="store_true",
                   help="Disable the Lever-2 pronunciation overrides (scripts/pron_overrides.json).")
    p.add_argument("--speed", type=float, default=1.0, help="0.5–2.0 (default 1.0).")
    p.add_argument("--language", default="en-us")
    p.add_argument("--max-chars", type=int, default=700, help="Per-chunk cap, < TTS's 800.")
    p.add_argument("--gap", type=float, default=0.35, help="Silence between chunks, seconds.")
    p.add_argument("--section-gap", type=float, default=2.0,
                   help="Silence at major topic (heading) boundaries — the 'take a breath' pause, seconds.")
    p.add_argument("--max-pause-gap", type=float, default=150.0,
                   help="Hard cap: max seconds of speech between pauses. A breath is auto-inserted "
                        "at a sentence boundary to enforce it (0 disables). Default 150 = 2.5 min.")
    p.add_argument("--bitrate", default="64k", help="MP3 bitrate (default 64k, good for speech).")
    p.add_argument("--name", help="Output filename override (without needing .mp3).")
    p.add_argument("--title", help="ID3 title (default: doc's first H1, else filename).")
    p.add_argument("--audience", choices=["technical", "exec"], default="technical",
                   help="Who the reading is for. 'technical' (default): the full document, "
                        "read verbatim (use --scrub to also strip paths/scaffolding). "
                        "'exec': an LLM-generated executive recap — shorter, low/no jargon, "
                        "business/pricing/branding/market-insight boosted over detail, all "
                        "paths and file names scrubbed. 'exec' implies an LLM pass and "
                        "auto-suffixes the output filename with '-exec'.")
    p.add_argument("--scrub", action="store_true",
                   help="(technical audience) LLM cleanup pass: strip file paths/names, "
                        "code/CLI fragments, URLs, and cross-ref scaffolding so you hear only "
                        "the substance, WITHOUT summarizing. (Frontmatter + markdown are already "
                        "stripped deterministically; this is the polish pass. NOT PHI scrubbing — "
                        "that's `kb scrub`. Redundant with --audience exec, which already scrubs.)")
    p.add_argument("--scrub-model", default=DEFAULT_SCRUB_MODEL,
                   help=f"OpenRouter model for the --scrub pass (default: {DEFAULT_SCRUB_MODEL}).")
    p.add_argument("--exec-model", default=DEFAULT_EXEC_MODEL,
                   help=f"OpenRouter model for the --audience exec recap (default: {DEFAULT_EXEC_MODEL}).")
    p.add_argument("--local-tts", action="store_true", help="Use localhost:8102 (no token).")
    p.add_argument("--tts-url", help="Override the TTS base URL entirely.")
    p.add_argument("--email", default=os.environ.get("OXP_FILES_EMAIL", "doc-to-speech@oxp.files"),
                   help="sub claim for the oxp.files bearer (shown in its activity log).")
    p.add_argument("--out", type=Path, help="Also write the MP3 to this local path.")
    p.add_argument("--no-upload", action="store_true", help="Skip the oxp.files upload.")
    p.add_argument("--dry-run", action="store_true",
                   help="Normalize + chunk only; print a preview and exit. No network.")
    args = p.parse_args()

    if not args.doc.exists():
        _die(f"Document not found: {args.doc}")
    if not (0.5 <= args.speed <= 2.0):
        _die("--speed must be between 0.5 and 2.0")

    raw = args.doc.read_text(encoding="utf-8")
    # Exec recaps never read code aloud — drop the "Code block omitted." cue for them.
    code_cue = "" if args.audience == "exec" else "Code block omitted."
    speakable, h1 = normalize_markdown(raw, code_cue=code_cue, section_breaks=True)
    if args.audience == "exec":
        if args.scrub:
            _log("ℹ️  --scrub is redundant with --audience exec (the exec recap already strips paths/names).")
        _log(f"👔 exec recap pass ({args.exec_model}) — shorter, jargon-free, business-boosted, paths scrubbed…")
        speakable = _llm_pass(
            speakable, system=_EXEC_SYSTEM, model=args.exec_model,
            api_key=_openrouter_key(), seg_chars=EXEC_SEG_CHARS,
            max_tokens=4000, label="exec recap",
        )
    elif args.scrub:
        _log(f"🧽 LLM scrub pass ({args.scrub_model}) — paths/filenames/scaffolding…")
        speakable = _llm_pass(
            speakable, system=_SCRUB_SYSTEM, model=args.scrub_model,
            api_key=_openrouter_key(), seg_chars=SCRUB_SEG_CHARS,
            max_tokens=8000, label="scrub",
        )
    if not args.no_pron:
        overrides = load_pron_overrides()
        if overrides:
            speakable = apply_pron_overrides(speakable, overrides)
            _log(f"🗣️  applied {len(overrides)} pronunciation override(s)")
    chunks = chunk_text(speakable, args.max_chars)
    if args.max_pause_gap > 0:
        before = sum(1 for c in chunks if c == _SECTION_SENTINEL)
        chunks = enforce_pause_cap(chunks, int(args.max_pause_gap * CHARS_PER_SECOND))
        added = sum(1 for c in chunks if c == _SECTION_SENTINEL) - before
        if added:
            _log(f"⏸️  inserted {added} extra pause(s) to keep every stretch under {args.max_pause_gap:g}s")
    if not chunks:
        _die("Nothing speakable after normalization (document is empty or all code/markup).")

    if args.title:
        title = args.title
    else:
        title = h1 or args.doc.stem
        if args.audience == "exec":
            title = f"{title} — exec recap"
    total_chars = sum(len(c) for c in chunks)
    _log(f"📄 {args.doc.name}: {len(raw)} raw chars → {total_chars} speakable → {len(chunks)} chunks")

    if args.dry_run:
        _log(f"   title: {title!r}")
        preview = "\n\n".join(f"[{i+1}/{len(chunks)}] {c}" for i, c in enumerate(chunks[:3]))
        print(preview)
        if len(chunks) > 3:
            print(f"\n… (+{len(chunks) - 3} more chunks)")
        return

    # Resolve TTS endpoint + token.
    if args.tts_url:
        tts_url, tts_token = args.tts_url.rstrip("/"), None
    elif args.local_tts:
        tts_url, tts_token = LOCAL_TTS_URL, None
    else:
        tts_url = PROD_TTS_URL
        _log("🔑 pulling shared-svcs JWT_SECRET from Railway…")
        secret = _railway_vars(**{
            "project": SHARED_SVCS["project"],
            "env": SHARED_SVCS["env"],
            "service": SHARED_SVCS["tts_service"],
        }).get("JWT_SECRET")
        if not secret:
            _die("JWT_SECRET not found on the shared-svcs TTS service.")
        now = int(time.time())
        tts_token = _hs256_jwt({"iss": "doc-to-speech", "aud": "tts", "exp": now + 1800}, secret)

    # Synthesize each chunk → concatenate PCM. Normal gap between chunks; a longer
    # "take a breath" silence at section sentinels (major topic boundaries).
    gap = b"\x00" * (int(args.gap * KOKORO_SAMPLE_RATE) * 2)
    section_gap = b"\x00" * (int(args.section_gap * KOKORO_SAMPLE_RATE) * 2)
    n_synth = sum(1 for c in chunks if c != _SECTION_SENTINEL)
    pcm_parts: list[bytes] = []
    done = 0
    for i, chunk in enumerate(chunks):
        if chunk == _SECTION_SENTINEL:
            pcm_parts.append(section_gap)
            continue
        done += 1
        _log(f"🗣️  [{done}/{n_synth}] synth {len(chunk)} chars…")
        pcm_parts.append(
            synthesize(
                chunk, tts_url=tts_url, voice=args.voice, speed=args.speed,
                language=args.language, token=tts_token,
            )
        )
        # Normal inter-chunk gap, unless the next chunk is a section break (which
        # supplies its own longer silence) or this is the last real chunk.
        nxt = chunks[i + 1] if i + 1 < len(chunks) else None
        if done < n_synth and nxt != _SECTION_SENTINEL:
            pcm_parts.append(gap)
    pcm = b"".join(pcm_parts)
    seconds = len(pcm) / 2 / KOKORO_SAMPLE_RATE
    _log(f"🎚️  {seconds/60:.1f} min of audio → MP3 ({args.bitrate})…")

    mp3 = pcm_to_mp3(pcm, title=title, bitrate=args.bitrate)
    _log(f"💿 MP3: {len(mp3)/1_000_000:.1f} MB")

    if args.out:
        args.out.write_bytes(mp3)
        _log(f"💾 wrote {args.out}")

    if args.no_upload:
        if not args.out:
            _die("--no-upload given but no --out path — nothing would be saved.")
        return

    # Upload to oxp.files.
    filename = _safe_mp3_name(args.doc, args.name, args.audience)
    _log("🔑 pulling oxp-files JWT_SECRET + PUBLIC_BASE_URL from Railway…")
    files_vars = _railway_vars(**{
        "project": OXP_KB["project"],
        "env": OXP_KB["env"],
        "service": OXP_KB["files_service"],
    })
    files_secret = files_vars.get("JWT_SECRET")
    if not files_secret:
        _die("JWT_SECRET not found on the oxp-files service.")
    base_url = (files_vars.get("PUBLIC_BASE_URL") or OXP_FILES_FALLBACK_URL).rstrip("/")

    _log(f"⬆️  uploading to {base_url} ({args.folder}/{filename})…")
    loc = upload_to_oxp(
        mp3, filename=filename, folder=args.folder, base_url=base_url,
        secret=files_secret, email=args.email,
    )
    _log(f"✅ landed in oxp.files: {loc}")
    _log(f"   browse: {base_url}/?folder={args.folder}")


if __name__ == "__main__":
    main()
