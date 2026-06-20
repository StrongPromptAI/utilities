#!/usr/bin/env python3
"""
doc_to_audio — turn document(s) into narrated MP3(s) and land them in oxp.files.

ONE CLI over a shared TTS engine. Two render formats, selected by --format:

  • ONE-VOICE (read | scrub | pm | exec) — a single narrator.
      read   : speak the doc as written (markdown stripped to prose).
      scrub  : + an LLM cleanup pass (drop paths/code/cross-refs), no summarizing.
      pm     : distill the source(s) into a product-doctrine briefing for the PM.
      exec   : distill into a tight, jargon-free executive recap.
  • TWO-VOICE (dialogue) — an LLM writes a two-host script; each host is pinned to
      one Kokoro voice (--female-voice / --male-voice), with --minutes / --brief.

CARDINALITY — the same rule for every format:
  • default  : each input doc is its own episode  → N docs in,  N MP3s out.
  • --combine: merge all inputs into ONE episode  → N docs in,  1 MP3 out.
So a series in either voice is just `doc_to_audio ep1.md ep2.md --format pm`
(two one-voice episodes) or `--format dialogue` (two two-voice episodes). Build a
single multi-source episode by pre-assembling its file, or with --combine.

The shared engine (chunking, «…» emphasis pacing, pron overrides, per-chunk PCM
synthesis, ffmpeg→MP3, presigned oxp.files upload, Railway secret pulls) lives in
doc_to_speech.py; the dialogue script generation + render lives in dialogue.py.

Usage:
  uv run python scripts/doc_to_audio.py DOC.md [DOC2.md ...] [options]

  # One-voice product briefing per doc (two episodes):
  uv run python scripts/doc_to_audio.py ep1.md ep2.md --format pm

  # Two-voice podcast, one episode merged from several sources:
  uv run python scripts/doc_to_audio.py a.md b.md --format dialogue --combine \
      --minutes 6 --name episode.mp3

  # Preview only (no synth/upload) — monologue chunks, or the dialogue script:
  uv run python scripts/doc_to_audio.py DOC.md --format pm --dry-run
  uv run python scripts/doc_to_audio.py DOC.md --format dialogue --dry-run

  # Local TTS (services/tts on :8102, auth-off in dev), no upload:
  uv run python scripts/doc_to_audio.py DOC.md --local-tts --no-upload --out /tmp/o.mp3

Auth (see symlink_docs/registries/AUTH_REGISTRY.md scenarios 4 + 5): TTS prod is an
HS256 JWT aud="tts"; oxp.files is an HS256 session bearer; the LLM (scrub/narrative/
dialogue) uses the OpenRouter key. Secrets pull live from Railway; --local-tts skips
the TTS secret, --no-upload skips the files secret, --dry-run skips synth.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import tempfile
from pathlib import Path

# scripts/ is sys.path[0] when run as `python scripts/doc_to_audio.py`, so the sibling
# modules import directly. doc_to_speech is the engine + one-voice pipeline; dialogue is
# the two-voice pipeline. One happy path: both render through the same engine.
from doc_to_speech import (
    DEFAULT_EXEC_MODEL,
    DEFAULT_SCRUB_MODEL,
    TTS_CONCURRENCY_DEFAULT,
    Emph,
    _die,
    _log,
    _normalize_and_chunk,
    _process_doc,
    _resolve_files_target,
    _resolve_tts_endpoint,
    _wait_for_tts_ready,
)
from dialogue import (
    DEFAULT_BRIEF,
    DEFAULT_SCRIPT_MODEL,
    _script_preview,
    build_or_load_script,
    process_dialogue,
)

ONE_VOICE_FORMATS = ("read", "scrub", "pm", "exec")


def _episode_doc(ep_docs: list[Path]) -> Path:
    """The single source Path for a one-voice episode. One doc → itself; several docs
    (a --combine episode) → a temp markdown file of their concatenated text, named after
    the first doc so auto-naming stays sensible."""
    if len(ep_docs) == 1:
        return ep_docs[0]
    text = "\n\n".join(d.read_text(encoding="utf-8") for d in ep_docs)
    tmpdir = Path(tempfile.mkdtemp(prefix="doc_to_audio."))
    combined = tmpdir / f"{ep_docs[0].stem}.md"
    combined.write_text(text, encoding="utf-8")
    return combined


def main() -> None:
    p = argparse.ArgumentParser(
        prog="doc_to_audio",
        description="Turn document(s) into narrated MP3(s) (one- or two-voice) and land them in oxp.files.",
    )
    p.add_argument("docs", nargs="+", type=Path, help="Source document(s): markdown or text.")
    p.add_argument("--format", choices=["read", "scrub", "pm", "exec", "dialogue"], default="read",
                   help="read/scrub/pm/exec = ONE voice; dialogue = TWO voices (host script). "
                        "pm/exec distill via LLM; scrub = LLM cleanup; read = speak as written.")
    p.add_argument("--combine", action="store_true",
                   help="Merge ALL input docs into ONE episode (N→1). Default: each doc is its "
                        "own episode (N→N). Same rule for every format.")
    p.add_argument("--folder", default="briefings", help="oxp.files folder (default: briefings).")

    # ── One-voice options (read/scrub/pm/exec) ──
    one = p.add_argument_group("one-voice (read/scrub/pm/exec)")
    one.add_argument("--voice", default="af_nova", help="Kokoro voice (must be in TTS allowlist).")
    one.add_argument("--gap", type=float, default=0.35, help="Silence between chunks, seconds.")
    one.add_argument("--section-gap", type=float, default=1.4,
                     help="Silence at major topic (heading) boundaries — the 'take a breath' pause.")
    one.add_argument("--max-pause-gap", type=float, default=150.0,
                     help="Hard cap: max seconds of speech between pauses (0 disables). Default 150.")
    one.add_argument("--scrub-model", default=DEFAULT_SCRUB_MODEL,
                     help=f"OpenRouter model for --format scrub (default: {DEFAULT_SCRUB_MODEL}).")
    one.add_argument("--narrative-model", default=DEFAULT_EXEC_MODEL,
                     help=f"OpenRouter model for --format pm/exec (default: {DEFAULT_EXEC_MODEL}).")
    one.add_argument("--save-script", type=Path,
                     help="Write the distilled/cleaned spoken text to this path (pairs with --dry-run).")

    # ── Two-voice options (dialogue) ──
    two = p.add_argument_group("two-voice (--format dialogue)")
    two.add_argument("--female-name", default="Maya", help="Female host name (LLM uses it verbatim).")
    two.add_argument("--male-name", default="Ethan", help="Male host name.")
    two.add_argument("--female-voice", default="af_nova", help="Kokoro voice for the female host.")
    two.add_argument("--male-voice", default="am_adam", help="Kokoro voice for the male host.")
    two.add_argument("--minutes", type=float, default=3.0, help="Target spoken length per episode (default 3).")
    two.add_argument("--brief", default=DEFAULT_BRIEF, help="Editorial brief: angle/tone for the episode.")
    two.add_argument("--model", default=DEFAULT_SCRIPT_MODEL, help=f"Dialogue-script model (default: {DEFAULT_SCRIPT_MODEL}).")
    two.add_argument("--script-in", type=Path, help="Load a pre-made script JSON; skip the LLM (single episode).")
    two.add_argument("--script-out", type=Path, help="Save the generated script JSON here.")
    two.add_argument("--turn-gap", type=float, default=0.45, help="Silence between speakers, seconds.")
    two.add_argument("--sub-gap", type=float, default=0.15, help="Silence between sub-chunks of one turn.")

    # ── Shared synthesis / packaging ──
    p.add_argument("--speed", type=float, default=1.0, help="0.5–2.0 (default 1.0).")
    p.add_argument("--language", default="en-us")
    p.add_argument("--max-chars", type=int, default=700, help="Per-synth chunk cap, < TTS's 800.")
    p.add_argument("--emphasis-speed", type=float, default=0.9,
                   help="Speed for «…»-marked emphasis spans (default 0.9 — ~10%% slower for weight). "
                        "Whole clauses, never single words.")
    p.add_argument("--emphasis-gap", type=float, default=0.25,
                   help="Silence just before each «…» emphasis span, seconds (default 0.25). 0 disables.")
    p.add_argument("--no-pron", action="store_true",
                   help="Disable the default-file pronunciation overrides (scripts/pron_overrides.json).")
    p.add_argument("--pron", action="append", default=[], metavar="WORD=SPOKEN",
                   help="Ad-hoc pronunciation override, repeatable (e.g. --pron lives=livz).")
    p.add_argument("--pron-file", action="append", default=[], type=Path, metavar="PATH",
                   help="Additional pron-overrides JSON merged on top (per-DOMAIN sidecar, e.g. "
                        "scripts/pron_overrides.clinical.json).")
    p.add_argument("--concurrency", type=int, default=TTS_CONCURRENCY_DEFAULT,
                   help=f"Max concurrent synth requests in flight (default {TTS_CONCURRENCY_DEFAULT}).")
    p.add_argument("--bitrate", default="64k", help="MP3 bitrate (default 64k, good for speech).")
    p.add_argument("--loudness", type=float, default=-16.0,
                   help="Target integrated loudness in LUFS via ffmpeg loudnorm (default -16; 0 disables).")
    p.add_argument("--volume", type=float, default=1.0,
                   help="Extra linear gain AFTER loudness normalization (default 1.0; tune --loudness instead).")
    p.add_argument("--name", help="Output filename (single episode only; without needing .mp3).")
    p.add_argument("--title", help="ID3 title (single episode only).")

    # ── TTS endpoint + output ──
    p.add_argument("--local-tts", action="store_true", help="Use localhost:8102 (no token).")
    p.add_argument("--tts-url", help="Override the TTS base URL entirely.")
    p.add_argument("--warmup-timeout", type=float, default=300.0,
                   help="Seconds to wait for the TTS service to wake from serverless sleep (0 = skip).")
    p.add_argument("--email", default=os.environ.get("OXP_FILES_EMAIL", "doc-to-audio@oxp.files"),
                   help="sub claim for the oxp.files bearer (shown in its activity log).")
    p.add_argument("--out", type=Path, help="Also write the MP3 to this local path (single episode only).")
    p.add_argument("--no-upload", action="store_true", help="Skip the oxp.files upload (needs --out).")
    p.add_argument("--no-transcript", action="store_true",
                   help="Skip the renderable .md transcript uploaded beside each MP3.")
    p.add_argument("--dry-run", action="store_true",
                   help="No synth/upload. One-voice: chunk preview. Dialogue: generate + print the script.")
    args = p.parse_args()

    is_dialogue = args.format == "dialogue"
    # Map the format onto the one-voice pipeline's flags (it reads args.narrative/args.scrub).
    args.narrative = args.format if args.format in ("pm", "exec") else None
    args.scrub = args.format == "scrub"

    for doc in args.docs:
        if not doc.exists():
            _die(f"Document not found: {doc}")
    if not (0.5 <= args.speed <= 2.0):
        _die("--speed must be between 0.5 and 2.0")
    if not (0.5 <= args.emphasis_speed <= 2.0):
        _die("--emphasis-speed must be between 0.5 and 2.0")

    # Cross-format arg hygiene: reject options that belong to the other format so a
    # mistaken flag fails loud instead of being silently ignored.
    def _reject(pairs, msg):
        for name, val, default in pairs:
            if val != default:
                _die(f"--{name} is a {msg}")
    if is_dialogue:
        _reject(
            [("voice", args.voice, "af_nova"), ("save-script", args.save_script, None),
             ("gap", args.gap, 0.35), ("section-gap", args.section_gap, 1.4),
             ("scrub-model", args.scrub_model, DEFAULT_SCRUB_MODEL),
             ("narrative-model", args.narrative_model, DEFAULT_EXEC_MODEL)],
            "one-voice option (read/scrub/pm/exec); not valid with --format dialogue",
        )
    else:
        _reject(
            [("female-name", args.female_name, "Maya"), ("male-name", args.male_name, "Ethan"),
             ("female-voice", args.female_voice, "af_nova"), ("male-voice", args.male_voice, "am_adam"),
             ("minutes", args.minutes, 3.0), ("brief", args.brief, DEFAULT_BRIEF),
             ("model", args.model, DEFAULT_SCRIPT_MODEL), ("script-in", args.script_in, None),
             ("script-out", args.script_out, None)],
            f"--format dialogue option; not valid with --format {args.format}",
        )

    # Cardinality: one episode per doc, or all docs combined into one.
    episodes = [list(args.docs)] if args.combine else [[d] for d in args.docs]
    if len(episodes) > 1 and (args.name or args.title or args.out):
        _die("--name/--title/--out apply to a single output episode; with multiple episodes "
             "each is auto-named. Use --combine to make one episode.")
    if args.no_upload and (len(episodes) > 1 or not args.out):
        _die("--no-upload needs a local --out path, which is single-episode only.")

    if args.dry_run:
        for ep_docs in episodes:
            if is_dialogue:
                print(_script_preview(build_or_load_script(ep_docs, args)))
            else:
                doc = _episode_doc(ep_docs)
                chunks, title, _t, raw_len, total = _normalize_and_chunk(doc, args)
                _log(f"📄 {doc.name}: {raw_len} raw → {total} speakable → {len(chunks)} chunks")
                _log(f"   title: {title!r}")
                preview = "\n\n".join(
                    f"[{i+1}/{len(chunks)}]{' «slow»' if isinstance(c, Emph) else ''} {c}"
                    for i, c in enumerate(chunks[:3])
                )
                print(preview)
                if len(chunks) > 3:
                    print(f"\n… (+{len(chunks) - 3} more chunks)")
        return

    # Resolve endpoint + secrets ONCE; the token is shared across every episode.
    tts_url, tts_token = _resolve_tts_endpoint(args)
    _wait_for_tts_ready(tts_url, timeout=args.warmup_timeout)
    files_secret, base_url = (None, None)
    if not args.no_upload:
        files_secret, base_url = _resolve_files_target(args)

    if is_dialogue:
        # Sequential episodes: each is its own LLM call + multi-turn synth.
        for ep_docs in episodes:
            process_dialogue(
                ep_docs, args, tts_url=tts_url, tts_token=tts_token,
                files_secret=files_secret, base_url=base_url,
            )
    else:
        ep_inputs = [_episode_doc(ep) for ep in episodes]
        if len(ep_inputs) > 1:
            # Episode-level parallelism (chunks sequential within each) so in-flight
            # synths ≈ --concurrency, the server's cap.
            workers = max(1, min(args.concurrency, len(ep_inputs)))
            _log(f"🎛️  {len(ep_inputs)} episodes, {workers} synthesizing in parallel…")
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                futs = [
                    ex.submit(
                        _process_doc, doc, args, tts_url=tts_url, tts_token=tts_token,
                        files_secret=files_secret, base_url=base_url, chunk_concurrency=1,
                    )
                    for doc in ep_inputs
                ]
                for fut in concurrent.futures.as_completed(futs):
                    fut.result()  # _die() in a worker propagates here and exits
        else:
            _process_doc(
                ep_inputs[0], args, tts_url=tts_url, tts_token=tts_token,
                files_secret=files_secret, base_url=base_url, chunk_concurrency=args.concurrency,
            )

    if base_url:
        _log(f"   browse: {base_url}/?folder={args.folder}")


if __name__ == "__main__":
    main()
