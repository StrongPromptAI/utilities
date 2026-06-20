#!/usr/bin/env python3
"""
dialogue — the two-voice (male + female) podcast render pipeline.

This module is a LIBRARY, not a CLI — the command lives in doc_to_audio.py (--format
dialogue). It builds on doc_to_speech (the shared engine): an LLM rewrites the source
docs into a two-host script of alternating turns, each host pinned to one Kokoro voice,
then `process_dialogue` renders one episode (synth → MP3 → upload + transcript), the
two-voice counterpart of doc_to_speech._process_doc.

Built on doc_to_speech: same Railway secret pulls, same per-chunk PCM synthesis,
same ffmpeg→MP3 transcode, same presigned oxp.files upload. The net-new piece is
*dialogue* — an LLM rewrites the source docs into a two-host script of alternating
turns, and each host is pinned to one Kokoro voice.

Why voice consistency is free here (the thing that plagues session-based podcast
tools): Kokoro synthesizes every turn from a fixed, deterministic voice vector. The
female host sounds identical in turn 1 and turn 100 — there is no cross-turn drift
to manage, because there is no state carried across turns. Pin the voice, done.

Pipeline:
  read docs → normalize markdown → LLM → dialogue script (JSON turns)
  → per turn: pick voice by speaker; if a turn exceeds the TTS 800-char cap,
    sub-chunk it (same voice) → synth each piece as raw 24 kHz mono PCM
  → concatenate (small gap within a turn, larger gap between speakers)
  → ffmpeg → MP3 → upload to oxp.files via a presigned PUT.

Entry point: `doc_to_audio.py --format dialogue` (the CLI). It resolves the TTS
endpoint + secrets once, then calls `process_dialogue` per episode. The public surface
is `build_or_load_script` (script from --script-in or the LLM) and `process_dialogue`
(render one episode → MP3 → oxp.files + transcript). Auth + flag semantics live in
doc_to_audio.py.

NOTE: the TTS service enforces a voice allowlist (TTS_VOICE_ALLOWLIST) in ALL modes,
not just prod. Both --female-voice and --male-voice must be in it, or synth 400s. The
prod default allowlist is "af_heart,af_nova,am_adam" — the af_nova/am_adam pair works.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests

# scripts/ is sys.path[0] when run as `python scripts/doc_to_audio.py`, so the sibling
# module imports directly. One happy path: the shared TTS/upload/secret/chunking engine
# lives in doc_to_speech; doc_to_audio resolves the endpoint + secrets and passes them in.
from doc_to_speech import (
    KOKORO_SAMPLE_RATE,
    OPENROUTER_URL,
    _die,
    _log,
    _openrouter_key,
    _safe_mp3_name,
    _transcript_name,
    _Synth,
    _silence,
    apply_pron_overrides,
    chunks_to_parts,
    normalize_markdown,
    pcm_to_mp3,
    resolve_pron_overrides,
    split_emphasis_chunks,
    strip_llm_markup,
    synthesize_ordered,
    upload_to_oxp,
)

DEFAULT_SCRIPT_MODEL = "anthropic/claude-opus-4.8"  # latest Opus; one short call
WORDS_PER_MINUTE = 145  # conversational Kokoro pace, leaving headroom for turn gaps
MAX_SOURCE_CHARS = 40000  # cap the LLM input; the brief steers focus, not the dump

# Podcasts always target sales reps and executives — never a clinical or technical
# audience — so the default brief is exec-oriented. Override with --brief for the rare
# technical episode. (Mirrors the exec lens in doc_to_speech.py's _EXEC_SYSTEM.)
DEFAULT_BRIEF = (
    "A relaxed two-host debrief that gives a busy executive or sales rep the business "
    "picture of the source material — fast. Lead with a hook and the bottom line. Boost "
    "the business model, pricing and monetization, branding and positioning, market "
    "insight, the competitive seam, and the assumptions the strategy rests on; skim past "
    "implementation and clinical/technical detail unless it carries business meaning. "
    "Keep it conversational and jargon-free, and end with a clean sign-off."
)


# ── Dialogue script generation (LLM → JSON turns) ────────────────────────────────

def _build_source(docs: list[Path]) -> str:
    parts: list[str] = []
    for d in docs:
        if not d.exists():
            _die(f"Source doc not found: {d}")
        speakable, _ = normalize_markdown(d.read_text(encoding="utf-8"))
        parts.append(f"### SOURCE: {d.name}\n\n{speakable.strip()}")
    src = "\n\n".join(parts)
    if len(src) > MAX_SOURCE_CHARS:
        src = src[:MAX_SOURCE_CHARS] + "\n\n[...source truncated...]"
    return src


def _script_system(female: str, male: str, minutes: float, brief: str) -> str:
    words = int(minutes * WORDS_PER_MINUTE)
    return f"""You write short, natural two-host podcast scripts meant to be read aloud by a text-to-speech engine.

THE TWO HOSTS (use these EXACT names as the speaker for every turn — no others):
- {female} — female host. Warm, curious; asks the sharp clarifying questions a smart non-expert would.
- {male} — male host. Close to the material; explains it clearly, with a little dry wit.

YOUR BRIEF FOR THIS EPISODE:
{brief}

HARD RULES:
- Ground EVERYTHING in the provided source material. Do not invent facts, numbers, names, or events the source does not support.
- Real conversation, not two monologues. They react to each other — "yeah", "right", "wait, what?", building on each other's points.
- Spoken English: contractions, short sentences, natural rhythm. No corporate phrasing.
- Open with a hook in the first one or two turns. End with a clean, short sign-off.
- About {words} words of spoken text total (~{minutes:g} minutes aloud). Tighter is better than padded — do NOT stuff filler to hit the count.
- Turns alternate but not robotically: a host may take two short turns in a row, or a one-word reaction. Each turn is 1–4 sentences.
- This is read by TTS, so write only what should be SPOKEN. NO stage directions, NO sound cues, NO markdown, NO asterisks, NO emoji, NO headings, NO host-name prefixes inside the text. (One narrow exception: the emphasis guillemets below.)
- Emphasis, used SPARINGLY: wrap the single most load-bearing line of a turn — a WHOLE clause or sentence, never a single word, never a whole turn — in guillemets «like this», so the TTS reads it a touch slower for weight. At most a few in the whole episode, never more than one per turn, often none. Used rarely they land; used often they deaden. Guillemets are the ONLY markup allowed.
- This is for a non-technical executive / sales audience: do NOT name or spell out file paths, file names, document names, section pointers, URLs, or code — convey the idea, never the pointer. Translate any technical, clinical, or product-internal jargon into plain business language, or drop it.
- Lead with the business angle and keep returning to it — the opportunity, the money, the positioning, the bet. Do not dwell on implementation or step-by-step detail. Never invent numbers or prices; use only figures the source supports, and if the source leaves a price open, say it's still open.
- For any term that must be spoken where a TTS would mangle it, write the spoken form ("JSON" as a word is fine; only spell out an acronym if it reads cleanly aloud).
- Output STRICT JSON ONLY — no prose, no code fences around it:
  {{"title": "<short episode title>", "turns": [{{"speaker": "{female}", "text": "..."}}, {{"speaker": "{male}", "text": "..."}}]}}"""


def _parse_script_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\n", "", t)
        t = re.sub(r"\n```$", "", t).strip()
    i, j = t.find("{"), t.rfind("}")
    if i == -1 or j == -1 or j < i:
        _die("script LLM did not return a JSON object")
    try:
        data = json.loads(t[i : j + 1])
    except json.JSONDecodeError as exc:
        _die(f"script JSON parse failed: {exc}\n--- raw ---\n{text[:600]}")
    turns = data.get("turns")
    if not isinstance(turns, list) or not turns:
        _die("script JSON missing a non-empty 'turns' array")
    for k, turn in enumerate(turns):
        if not isinstance(turn, dict) or "speaker" not in turn or "text" not in turn:
            _die(f"turn {k} malformed (need 'speaker' and 'text'): {turn!r}")
    return data


def generate_dialogue(
    source: str, *, model: str, api_key: str, female: str, male: str, minutes: float, brief: str
) -> dict:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "temperature": 0.7,
        "max_tokens": 4000,
        "messages": [
            {"role": "system", "content": _script_system(female, male, minutes, brief)},
            {
                "role": "user",
                "content": f"SOURCE MATERIAL:\n\n{source}\n\nWrite the podcast script now as strict JSON.",
            },
        ],
    }
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=180)
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"].strip()
                if not content:
                    _die("script LLM returned empty content")
                return _parse_script_json(content)
            if r.status_code in (401, 403):
                _die(f"OpenRouter auth rejected ({r.status_code}): {r.text[:200]}")
            last_err = f"{r.status_code}: {r.text[:200]}"
        except (requests.RequestException, KeyError, ValueError) as exc:
            last_err = str(exc)
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))
    _die(f"script LLM failed after 3 attempts: {last_err}")


# ── Voice mapping ────────────────────────────────────────────────────────────────

def _voice_for(
    speaker: str, *, female: str, male: str, female_voice: str, male_voice: str, prev_voice: str | None
) -> str:
    s = speaker.strip().lower()
    if s == female.lower():
        return female_voice
    if s == male.lower():
        return male_voice
    # Tolerant: a mislabeled speaker shouldn't tank a multi-minute render. Alternate
    # off the previous turn and warn loudly so it's visible in the log.
    fallback = male_voice if prev_voice == female_voice else female_voice
    _log(f"⚠️  unknown speaker {speaker!r} — falling back to {fallback}")
    return fallback


# ── PCM helpers ──────────────────────────────────────────────────────────────────

def _script_preview(data: dict) -> str:
    lines = [f"TITLE: {data.get('title', '(untitled)')}", ""]
    for turn in data["turns"]:
        lines.append(f"[{turn['speaker']}] {turn['text']}")
    return "\n".join(lines)


def _dialogue_markdown(data: dict, title: str) -> str:
    """Render the two-host dialogue as a renderable markdown transcript — a title heading
    and one bold speaker label per turn. Lands beside the MP3 in oxp.files."""
    lines = [f"# {title}", ""]
    for turn in data["turns"]:
        text = str(turn["text"]).strip().replace("«", "").replace("»", "")  # drop synth marks
        if not text:
            continue
        lines.append(f"**{str(turn['speaker']).strip()}:** {text}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower() or "podcast"


# ── Episode pipeline (the dialogue counterpart of doc_to_speech._process_doc) ─────

def build_or_load_script(docs: list[Path], args) -> dict:
    """Get the dialogue script for one episode — from a saved --script-in JSON, or by
    generating it from `docs` via the LLM (then saving to --script-out if asked). Shared by
    the dry-run preview and the full render so the LLM call is described in exactly one place."""
    if args.script_in:
        if not args.script_in.exists():
            _die(f"--script-in not found: {args.script_in}")
        script = _parse_script_json(args.script_in.read_text(encoding="utf-8"))
        _log(f"📜 loaded script from {args.script_in} ({len(script['turns'])} turns)")
    else:
        source = _build_source(docs)
        _log(f"📄 {len(docs)} doc(s) → {len(source)} source chars → generating script ({args.model})…")
        script = generate_dialogue(
            source, model=args.model, api_key=_openrouter_key(),
            female=args.female_name, male=args.male_name, minutes=args.minutes, brief=args.brief,
        )
        words = sum(len(str(t["text"]).split()) for t in script["turns"])
        _log(f"🎬 script: {script.get('title')!r} — {len(script['turns'])} turns, ~{words} words")
    if args.script_out:
        args.script_out.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
        _log(f"💾 wrote script → {args.script_out}")
    return script


def process_dialogue(
    docs: list[Path], args, *, tts_url: str, tts_token: str | None,
    files_secret: str | None, base_url: str | None,
) -> str | None:
    """Full two-voice pipeline for ONE episode: script → per-turn synth (one Kokoro voice
    per speaker, «…» emphasis, pron overrides) → MP3 → upload + dialogue transcript. Endpoint
    and secrets are resolved once by the caller and passed in (mirrors _process_doc), so a
    series of episodes shares one token. Returns the oxp.files location, or None on --no-upload."""
    script = build_or_load_script(docs, args)

    overrides = resolve_pron_overrides(no_pron=args.no_pron, pron_files=args.pron_file, ad_hoc=args.pron)
    if overrides:
        _log(f"🗣️  {len(overrides)} pronunciation override(s) active: {', '.join(sorted(overrides))}")

    turn_gap = _silence(args.turn_gap)
    prev_voice: str | None = None
    turns = [t for t in script["turns"] if str(t.get("text", "")).strip()]
    parts: list = []
    for i, turn in enumerate(turns, 1):
        speaker = str(turn["speaker"]).strip()
        text = strip_llm_markup(str(turn["text"]).strip())            # asterisk fix
        voice = _voice_for(
            speaker, female=args.female_name, male=args.male_name,
            female_voice=args.female_voice, male_voice=args.male_voice, prev_voice=prev_voice,
        )
        prev_voice = voice
        if overrides:
            text = apply_pron_overrides(text, overrides)
        subs = split_emphasis_chunks(text, args.max_chars)            # «…» emphasis
        _log(f"🎙️  [{i}/{len(turns)}] {speaker} ({voice}) — {len(text)} chars, {len(subs)} piece(s)")
        parts.extend(chunks_to_parts(
            subs, voice, gap=args.sub_gap, section_gap=args.sub_gap,
            emphasis_gap=args.emphasis_gap, emphasis_speed=args.emphasis_speed,
        ))
        if i < len(turns):
            parts.append(turn_gap)

    n_synth = sum(1 for p in parts if isinstance(p, _Synth))
    _log(f"🗣️  synthesizing {n_synth} piece(s), {args.concurrency}-wide…")
    pcm = synthesize_ordered(
        parts, tts_url=tts_url, speed=args.speed, language=args.language,
        token=tts_token, concurrency=args.concurrency,
    )
    seconds = len(pcm) / 2 / KOKORO_SAMPLE_RATE
    _log(f"🎚️  {seconds / 60:.1f} min of audio → MP3 ({args.bitrate})…")

    title = args.title or script.get("title") or "Podcast"
    mp3 = pcm_to_mp3(pcm, title=title, bitrate=args.bitrate, volume=args.volume, loudness=args.loudness)
    _log(f"💿 MP3: {len(mp3) / 1_000_000:.1f} MB")

    if args.out:
        args.out.write_bytes(mp3)
        _log(f"💾 wrote {args.out}")
    if args.no_upload:
        if not args.out:
            _die("--no-upload given but no --out path — nothing would be saved.")
        return None

    filename = _safe_mp3_name(Path(args.name or _slug(title)), args.name)
    _log(f"⬆️  uploading to {base_url} ({args.folder}/{filename})…")
    loc = upload_to_oxp(
        mp3, filename=filename, folder=args.folder, base_url=base_url,
        secret=files_secret, email=args.email,
    )
    _log(f"✅ {filename} → oxp.files: {loc}")

    if not args.no_transcript:
        tname = _transcript_name(filename)
        upload_to_oxp(
            _dialogue_markdown(script, title).encode("utf-8"), filename=tname,
            folder=args.folder, base_url=base_url, secret=files_secret, email=args.email,
            content_type="text/markdown; charset=utf-8",
        )
        _log(f"📝 transcript: {args.folder}/{tname}")
    return loc

