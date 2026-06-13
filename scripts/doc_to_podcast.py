#!/usr/bin/env python3
"""
doc_to_podcast — turn one or more docs into a two-voice (male + female) podcast MP3
and land it in oxp.files.

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

Usage:
  uv run python scripts/doc_to_podcast.py DOC.md [DOC2.md ...] [options]

  # 3-minute two-host episode from several docs, uploaded to the briefings folder:
  uv run python scripts/doc_to_podcast.py a.md b.md --minutes 3 --brief "..." \
      --name episode.mp3

  # See the generated script only — no synthesis, no upload:
  uv run python scripts/doc_to_podcast.py a.md --dry-run

  # Synthesize against a locally-running TTS (services/tts on :8102, auth-off in
  # dev — start it with TTS_VOICE_ALLOWLIST including BOTH voices):
  uv run python scripts/doc_to_podcast.py a.md --local-tts --no-upload --out /tmp/ep.mp3

  # Re-run synthesis from a saved script without re-calling the LLM:
  uv run python scripts/doc_to_podcast.py --script-in script.json --local-tts ...

Auth (see symlink_docs/registries/AUTH_REGISTRY.md scenarios 4 + 5):
  • TTS prod   — HS256 JWT, aud="tts", signed with the shared-svcs JWT_SECRET.
  • oxp.files  — HS256 session bearer {sub,iat,exp}, signed with the files JWT_SECRET.
  • script LLM — OpenRouter key from ~/.config/keys.json.
  Secrets are pulled live (Railway GraphQL); nothing is written to disk. --local-tts
  skips the TTS secret; --no-upload skips the files secret; --script-in skips the LLM.

NOTE: the TTS service enforces a voice allowlist (TTS_VOICE_ALLOWLIST) in ALL modes,
not just prod. Both --female-voice and --male-voice must be in it, or synth 400s.
Prod default is "af_heart" only — add the male voice there, or use --local-tts with
the allowlist widened locally.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

# scripts/ is sys.path[0] when run as `python scripts/doc_to_podcast.py`, so the
# sibling module imports directly. One happy path: all the TTS/upload/secret
# plumbing lives in doc_to_speech and is reused verbatim here.
from doc_to_speech import (
    KOKORO_SAMPLE_RATE,
    LOCAL_TTS_URL,
    OPENROUTER_URL,
    OXP_FILES_FALLBACK_URL,
    OXP_KB,
    PROD_TTS_URL,
    SHARED_SVCS,
    TTS_CONCURRENCY_DEFAULT,
    _die,
    _hs256_jwt,
    _log,
    _openrouter_key,
    _railway_vars,
    _safe_mp3_name,
    _Synth,
    _wait_for_tts_ready,
    apply_pron_overrides,
    chunk_text,
    load_pron_overrides,
    normalize_markdown,
    pcm_to_mp3,
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
- This is read by TTS, so write only what should be SPOKEN. NO stage directions, NO sound cues, NO markdown, NO asterisks, NO emoji, NO headings, NO host-name prefixes inside the text.
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

def _silence(seconds: float) -> bytes:
    return b"\x00" * (int(seconds * KOKORO_SAMPLE_RATE) * 2)  # s16le mono


def _script_preview(data: dict) -> str:
    lines = [f"TITLE: {data.get('title', '(untitled)')}", ""]
    for turn in data["turns"]:
        lines.append(f"[{turn['speaker']}] {turn['text']}")
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        prog="doc_to_podcast",
        description="Turn one or more docs into a two-voice (male + female) podcast MP3, landed in oxp.files.",
    )
    p.add_argument("docs", nargs="*", type=Path, help="Source document(s): markdown or text.")
    p.add_argument("--folder", default="briefings", help="oxp.files folder (default: briefings).")

    # Hosts + voices.
    p.add_argument("--female-name", default="Maya", help="Female host name (the LLM uses it verbatim).")
    p.add_argument("--male-name", default="Ethan", help="Male host name.")
    p.add_argument("--female-voice", default="af_nova", help="Kokoro voice for the female host.")
    p.add_argument("--male-voice", default="am_adam", help="Kokoro voice for the male host.")
    p.add_argument("--speed", type=float, default=1.0, help="0.5–2.0 (default 1.0).")
    p.add_argument("--language", default="en-us")

    # Script generation.
    p.add_argument("--minutes", type=float, default=3.0, help="Target spoken length (default 3).")
    p.add_argument("--brief", default=DEFAULT_BRIEF, help="Editorial brief: the angle/tone for the episode.")
    p.add_argument("--model", default=DEFAULT_SCRIPT_MODEL, help=f"OpenRouter model (default: {DEFAULT_SCRIPT_MODEL}).")
    p.add_argument("--script-in", type=Path, help="Load a pre-made script JSON; skip the LLM entirely.")
    p.add_argument("--script-out", type=Path, help="Save the generated script JSON here.")

    # Synthesis / packaging.
    p.add_argument("--max-chars", type=int, default=700, help="Per-synth cap, < TTS's 800.")
    p.add_argument("--no-pron", action="store_true",
                   help="Disable the Lever-2 pronunciation overrides (scripts/pron_overrides.json).")
    p.add_argument("--pron", action="append", default=[], metavar="WORD=SPOKEN",
                   help="Ad-hoc pronunciation override, repeatable (e.g. --pron irrevocable=irrevohcable).")
    p.add_argument("--turn-gap", type=float, default=0.45, help="Silence between speakers, seconds.")
    p.add_argument("--sub-gap", type=float, default=0.15, help="Silence between sub-chunks of one turn.")
    p.add_argument("--concurrency", type=int, default=TTS_CONCURRENCY_DEFAULT,
                   help=f"Parallel synth requests in flight (default {TTS_CONCURRENCY_DEFAULT}, "
                        "matching the TTS service's TTS_MAX_CONCURRENCY). Higher just queues "
                        "server-side; 1 = sequential.")
    p.add_argument("--bitrate", default="64k", help="MP3 bitrate (default 64k, good for speech).")
    p.add_argument("--name", help="Output filename (without needing .mp3).")
    p.add_argument("--title", help="ID3 title (default: the script's title).")

    # TTS endpoint.
    p.add_argument("--local-tts", action="store_true", help="Use localhost:8102 (no token).")
    p.add_argument("--tts-url", help="Override the TTS base URL entirely.")
    p.add_argument("--warmup-timeout", type=float, default=300.0,
                   help="Seconds to wait for the TTS service to wake from serverless sleep before "
                        "synth (polls /health; cold start ~30–40s). 0 = skip the warmup poll.")

    # Output.
    p.add_argument("--email", default=os.environ.get("OXP_FILES_EMAIL", "doc-to-podcast@oxp.files"),
                   help="sub claim for the oxp.files bearer (shown in its activity log).")
    p.add_argument("--out", type=Path, help="Also write the MP3 to this local path.")
    p.add_argument("--no-upload", action="store_true", help="Skip the oxp.files upload.")
    p.add_argument("--dry-run", action="store_true", help="Generate + print the script; no synth, no upload.")
    args = p.parse_args()

    if not (0.5 <= args.speed <= 2.0):
        _die("--speed must be between 0.5 and 2.0")
    if not args.script_in and not args.docs:
        _die("Provide source doc(s), or --script-in to reuse a saved script.")

    # 1) Get the dialogue script — from a saved file or by generating one.
    if args.script_in:
        if not args.script_in.exists():
            _die(f"--script-in not found: {args.script_in}")
        script = _parse_script_json(args.script_in.read_text(encoding="utf-8"))
        _log(f"📜 loaded script from {args.script_in} ({len(script['turns'])} turns)")
    else:
        source = _build_source(args.docs)
        _log(f"📄 {len(args.docs)} doc(s) → {len(source)} source chars → generating script ({args.model})…")
        script = generate_dialogue(
            source, model=args.model, api_key=_openrouter_key(),
            female=args.female_name, male=args.male_name, minutes=args.minutes, brief=args.brief,
        )
        word_count = sum(len(str(t["text"]).split()) for t in script["turns"])
        _log(f"🎬 script: {script.get('title')!r} — {len(script['turns'])} turns, ~{word_count} words")

    if args.script_out:
        args.script_out.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
        _log(f"💾 wrote script → {args.script_out}")

    if args.dry_run:
        print(_script_preview(script))
        return

    # 2) Resolve TTS endpoint + token.
    if args.tts_url:
        tts_url, tts_token = args.tts_url.rstrip("/"), None
    elif args.local_tts:
        tts_url, tts_token = LOCAL_TTS_URL, None
    else:
        tts_url = PROD_TTS_URL
        _log("🔑 pulling shared-svcs JWT_SECRET from Railway…")
        secret = _railway_vars(
            project=SHARED_SVCS["project"], env=SHARED_SVCS["env"], service=SHARED_SVCS["tts_service"]
        ).get("JWT_SECRET")
        if not secret:
            _die("JWT_SECRET not found on the shared-svcs TTS service.")
        tts_token = _hs256_jwt({"iss": "doc-to-podcast", "aud": "tts", "exp": int(time.time()) + 1800}, secret)

    # Wake the service from serverless sleep (and gate synth on readiness) before synth.
    _wait_for_tts_ready(tts_url, timeout=args.warmup_timeout)

    # Pronunciation overrides: file (unless --no-pron) plus any ad-hoc --pron WORD=SPOKEN.
    overrides: dict[str, str] = {} if args.no_pron else load_pron_overrides()
    for spec in args.pron:
        if "=" not in spec:
            _die(f"--pron expects WORD=SPOKEN, got {spec!r}")
        w, _, s = spec.partition("=")
        overrides[w.strip()] = s.strip()
    if overrides:
        _log(f"🗣️  {len(overrides)} pronunciation override(s) active: {', '.join(sorted(overrides))}")

    # 3) Synthesize each turn with its host's voice; concatenate with gaps.
    turn_gap = _silence(args.turn_gap)
    sub_gap = _silence(args.sub_gap)
    prev_voice: str | None = None
    turns = [t for t in script["turns"] if str(t.get("text", "")).strip()]

    # Build the ordered parts list across all turns (each turn's sub-chunks separated
    # by sub-gaps, a turn-gap between speakers), then resolve every synth marker
    # concurrently while preserving order.
    parts: list = []
    for i, turn in enumerate(turns, 1):
        speaker = str(turn["speaker"]).strip()
        text = str(turn["text"]).strip()
        voice = _voice_for(
            speaker, female=args.female_name, male=args.male_name,
            female_voice=args.female_voice, male_voice=args.male_voice, prev_voice=prev_voice,
        )
        prev_voice = voice
        if overrides:
            text = apply_pron_overrides(text, overrides)
        subs = chunk_text(text, args.max_chars)
        _log(f"🎙️  [{i}/{len(turns)}] {speaker} ({voice}) — {len(text)} chars, {len(subs)} piece(s)")
        for k, sub in enumerate(subs):
            parts.append(_Synth(sub, voice))
            if k < len(subs) - 1:
                parts.append(sub_gap)
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
    mp3 = pcm_to_mp3(pcm, title=title, bitrate=args.bitrate)
    _log(f"💿 MP3: {len(mp3) / 1_000_000:.1f} MB")

    if args.out:
        args.out.write_bytes(mp3)
        _log(f"💾 wrote {args.out}")

    if args.no_upload:
        if not args.out:
            _die("--no-upload given but no --out path — nothing would be saved.")
        return

    # 4) Upload to oxp.files.
    filename = _safe_mp3_name(Path(args.name or _slug(title)), args.name)
    _log("🔑 pulling oxp-files JWT_SECRET + PUBLIC_BASE_URL from Railway…")
    files_vars = _railway_vars(
        project=OXP_KB["project"], env=OXP_KB["env"], service=OXP_KB["files_service"]
    )
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


def _slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower() or "podcast"


if __name__ == "__main__":
    main()
