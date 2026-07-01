#!/usr/bin/env python3
"""
doc_to_audio — turn document(s) into narrated MP3(s) and publish them to a podcast show.

ONE CLI over a shared TTS engine. --format picks what happens to your text — and the
formats split into two camps:

  POST-PROCESS (your authored text is spoken; the CLI never rewrites it):
      emphasize : (default) add «…» emphasis to the authored text, words preserved
                  VERBATIM — a constrained LLM pass that is verified annotation-only,
                  so it can add emphasis but can never reword. The right mode when the
                  writing is already done (e.g. authored on a SOTA model outside).
      read      : speak the doc exactly as written; emphasis only from «…» you typed.
  REWRITE (an LLM authors new text — your files are NOT spoken verbatim; these prompt
  for confirmation, see --yes):
      pm    : distill the source(s) into a one-voice product-doctrine briefing.
      exec  : distill into a tight, jargon-free one-voice executive recap.
      scrub : one-voice LLM cleanup (drop paths/code/cross-refs), no summarizing.
      dialogue : TWO voices — an LLM writes a two-host script; each host pinned to one
                 Kokoro voice (--female-voice / --male-voice), with --minutes / --brief.

CARDINALITY — the same rule for every format:
  • default  : each input doc is its own episode  → N docs in,  N MP3s out.
  • --combine: merge all inputs into ONE episode  → N docs in,  1 MP3 out.
So a series in either voice is just `doc_to_audio ep1.md ep2.md --format pm`
(two one-voice episodes) or `--format dialogue` (two two-voice episodes). Build a
single multi-source episode by pre-assembling its file, or with --combine.

The shared engine (chunking, «…» emphasis pacing, pron overrides, per-chunk PCM
synthesis, ffmpeg→MP3, podcast-server publish, Railway secret pulls) lives in
doc_to_speech.py; the dialogue script generation + render lives in dialogue.py.

Every episode publishes to a podcast show on the StrongPrompt podcast server
(services/podcast) — the default --show is `tech` (Healing Journey Tech Review).
The MP3 PUTs onto the show's volume and appears in that show's RSS feed, with a
brief Sonnet-written episode description as the `<base>.md` sidecar. (oxp.files was
retired as a target at cutover; its podcast content migrated to the `tech` show.)

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

  # Publish an authored episode to a specific show (default show is `tech`):
  uv run python scripts/doc_to_audio.py ep.md --show clinical --name ep.mp3 --title "…"

  # Local TTS (services/tts on :8102, auth-off in dev), no publish:
  uv run python scripts/doc_to_audio.py DOC.md --local-tts --no-upload --out /tmp/o.mp3

Auth: TTS prod is an HS256 JWT aud="tts" (AUTH_REGISTRY scenario 4); the podcast upload
is an HS256 bearer aud="podcast-upload" signed with the show server's PODCAST_UPLOAD_SECRET
(see services/podcast/app.py _verify_upload). The LLM routes by model id (see
doc_to_speech.llm_chat): pm/exec/dialogue/emphasize/description default to the native
Anthropic API (`~/.config/keys.json → ANTHROPIC_API_KEY`, uses Anthropic credits);
scrub uses Gemini via OpenRouter. TTS/podcast secrets pull live from Railway; --local-tts
skips the TTS secret, --no-upload skips the podcast secret, --dry-run skips synth.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
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
    _epoch_to_pacific_iso,
    _list_show_episodes,
    _get_show_meta,
    _next_recut_name,
    _normalize_and_chunk,
    _now_pacific_iso,
    _process_doc,
    _resolve_podcast_target,
    _resolve_tts_endpoint,
    _safe_mp3_name,
    _split_shownotes,
    _wait_for_tts_ready,
    normalize_markdown,
)
from dialogue import (
    DEFAULT_BRIEF,
    DEFAULT_SCRIPT_MODEL,
    _script_preview,
    build_or_load_script,
    process_dialogue,
)

ONE_VOICE_FORMATS = ("read", "emphasize", "scrub", "pm", "exec")
WRITING_FORMATS = ("pm", "exec", "dialogue")  # these REWRITE the source with an LLM

# Empirical speaking rates (words/min) incl. inter-turn/section gaps, at the default
# 0.9 speed — for the preflight's *approximate* length estimate only (not exact).
_WPM_ONE_VOICE = 200
_WPM_TWO_VOICE = 185


def _voice_gender(voice: str) -> str:
    return "F" if voice.startswith("af_") else "M" if voice.startswith("am_") else "?"


def _fmt_mmss(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _preflight_stats(args, is_dialogue: bool, src: Path) -> tuple[int, int, bool]:
    """(word_count, emphasis_spans, emphasis_added_at_synth) for one source, with NO LLM call.
    Words come from the normalized speakable text (the emphasis pass preserves words verbatim);
    emphasis spans are counted in the text that will actually be synthesized."""
    if is_dialogue and getattr(args, "script_in", None):
        data = json.loads(Path(args.script_in).read_text(encoding="utf-8"))
        turns = data.get("turns", [])
        text = " ".join(str(t.get("text", "")) for t in turns)
        return len(text.split()), text.count("«"), False
    raw = src.read_text(encoding="utf-8")
    raw, _notes = _split_shownotes(raw)
    speakable, _h1 = normalize_markdown(raw)
    words = len(speakable.split())
    # emphasize ADDS «…» during synth (so 0 pre-marked here is expected); read/dialogue are verbatim.
    added = args.format == "emphasize"
    return words, speakable.count("«"), added


def _hosts_line(src: Path) -> str:
    """The 'transcription reviewed (no host names)' field — reuses the speaker-name lint so
    there is one source of truth for what counts as anonymous."""
    try:
        from lint_podcast_speaker_names import _check as _speaker_check
        from lint_podcast_speaker_names import DRAMATIZATIONS, TRANSCRIPTS
    except Exception:
        return "(lint unavailable)"
    try:
        rel = src.resolve().relative_to(TRANSCRIPTS.resolve()).as_posix()
    except ValueError:
        rel = None
    if rel in DRAMATIZATIONS:
        return "✓ dramatization — named characters intentional"
    result = _speaker_check(src)
    if result is None:
        return "✓ anonymous — no host names"
    reason, labels = result
    return f"⚠ {reason}: {labels}"


def _prosody_rows(args, is_dialogue: bool, src: Path) -> list[tuple[str, str]]:
    """PREFLIGHT prosody warnings, computed from the spoken text with NO synth: short «…»
    emphasis spans (island effect) and homographs to verify (the 'lives'→/laɪvz/ long-i bug).
    One source of truth with the standalone `lint_podcast_prosody`."""
    try:
        from lint_podcast_prosody import EMPH_MIN_WORDS, _spoken, scan
    except Exception:
        return []
    if is_dialogue and getattr(args, "script_in", None):
        data = json.loads(Path(args.script_in).read_text(encoding="utf-8"))
        spoken = " ".join(str(t.get("text", "")) for t in data.get("turns", []))
    else:
        raw, _notes = _split_shownotes(src.read_text(encoding="utf-8"))
        spoken = _spoken(raw)
    short, homs = scan(spoken)
    rows: list[tuple[str, str]] = []
    if short:
        rows.append(("⚠ Emphasis", f"{len(short)} short span(s) <{EMPH_MIN_WORDS}w — whole "
                                    f"clauses only: {', '.join(short[:3])}"))
    if homs:
        rows.append(("⚠ Pronounce", "verify w/ espeak, fix via --pron/reword: " + ", ".join(homs)))
    return rows


def _preflight(args, episodes, is_dialogue: bool, *, recut_before, base_url, secret) -> None:
    """Print a formatted publish summary BEFORE the billed synth — source, target, new/recut,
    words, voices, ~length, channel + show title/description, episode, anonymity check, emphasis,
    and the feed link. A pure read: no synth, no LLM."""
    grp = episodes[0]
    src = grp[0] if isinstance(grp, list) else grp
    extra_eps = len(episodes) - 1

    if args.recut and not args.force_redownload:
        mode = "RECUT — replace in place (same GUID + pubDate)"
    elif args.recut:
        mode = "RECUT — new GUID (subscribers re-download)"
    else:
        mode = "NEW episode"

    if is_dialogue:
        cast = None
        if getattr(args, "script_in", None):
            cast = (json.loads(Path(args.script_in).read_text(encoding="utf-8")).get("voices") or None)
        if cast:  # multi-speaker script carries its own speaker→voice map
            voices = f"{len(cast)} — " + " · ".join(
                f"{name}→{v} ({_voice_gender(v)})" for name, v in cast.items())
        else:
            fv, mv = args.female_voice, args.male_voice
            voices = (f"2 — {args.female_name}→{fv} ({_voice_gender(fv)}) · "
                      f"{args.male_name}→{mv} ({_voice_gender(mv)})")
    else:
        voices = f"1 — {args.voice} ({_voice_gender(args.voice)})"
    if "dramatization" in _hosts_line(src).lower():
        voices += " · dramatization"

    words, emph, emph_added = _preflight_stats(args, is_dialogue, src)
    est = _fmt_mmss(words / (_WPM_TWO_VOICE if is_dialogue else _WPM_ONE_VOICE) * 60)
    emph_str = f"{emph} «…» span(s)" + (" · more added at synth" if emph_added else
                                        (" (flat)" if emph == 0 else ""))

    meta = None
    if base_url and secret and args.show:
        meta = _get_show_meta(args.show, base_url=base_url, secret=secret)
    channel = args.show
    if meta and meta.get("title"):
        channel = f"{args.show} — {meta['title']}"
    show_desc = (meta or {}).get("description") or "(run live for server lookup)"
    if len(show_desc) > 64:
        show_desc = show_desc[:61] + "…"
    feed = (meta or {}).get("feed_url") or (f"…/{args.show}/<code>/feed.xml  (confirm show in admin)"
                                            if not args.no_upload else "(--no-upload)")

    def _row(label, value):
        print(f"  {label:<11} {value}")

    bar = "─" * 60
    print(f"\n{bar}\n  PODCAST PREFLIGHT — review before TTS synth\n{bar}")
    _row("Source", str(src) + (f"  (+{extra_eps} more)" if extra_eps else ""))
    _row("Target", (args.name or "(auto-named)"))
    _row("Mode", mode)
    _row("Channel", channel)
    if meta:
        _row("Show desc", show_desc)
    _row("Episode", f"\"{args.title}\"" if args.title else "(title from filename)")
    _row("Voices", voices)
    _row("Words", f"{words:,}        Est. length  ~{est}")
    _row("Emphasis", emph_str)
    for warn in _prosody_rows(args, is_dialogue, src):
        _row(*warn)
    _row("Hosts", _hosts_line(src))
    _row("Notes", "full transcript + any citations footer → show notes"
         if not args.no_transcript else "blurb only (--no-transcript)")
    _row("Feed", feed)
    print(bar)


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
        description="Turn document(s) into narrated MP3(s) (one- or two-voice) and publish them to a podcast show.",
    )
    p.add_argument("docs", nargs="+", type=Path, help="Source document(s): markdown or text.")
    p.add_argument("--format", choices=["emphasize", "read", "scrub", "pm", "exec", "dialogue"],
                   default="emphasize",
                   help="POST-PROCESS (don't rewrite): emphasize (default) = add «…» emphasis to "
                        "your authored text, words preserved verbatim; read = speak exactly as "
                        "written. REWRITE (LLM authors): pm/exec distill a one-voice briefing; "
                        "dialogue writes a two-host script; scrub = LLM cleanup. The rewriting "
                        "formats (pm/exec/dialogue) prompt for confirmation — see --yes.")
    p.add_argument("--combine", action="store_true",
                   help="Merge ALL input docs into ONE episode (N→1). Default: each doc is its "
                        "own episode (N→N). Same rule for every format.")
    p.add_argument("--show", default="tech",
                   help="Podcast show slug to publish to — the synth target (default: tech, the "
                        "Healing Journey Tech Review feed). The MP3 PUTs onto the show's volume and "
                        "appears in that show's RSS feed; a brief Sonnet-written episode description "
                        "rides along as the `<base>.md` sidecar the feed shows as <description>. "
                        "Other shows: clinical, sales, general. (oxp.files was retired at cutover.)")
    p.add_argument("--recut", action="store_true",
                   help="Recut an EXISTING episode in place: require --name to already exist in the "
                        "show and OVERWRITE its three artifacts (mp3 + .md + -transcript.md) under the "
                        "exact same base — never a duplicate. Fails loud if the name isn't already in "
                        "the show (catches typos that would strand a stray episode). Same filename = "
                        "same feed GUID, so the fix reaches NEW subscribers silently; prior downloaders "
                        "keep their copy (see --force-redownload). Preserves the original publish date.")
    p.add_argument("--force-redownload", action="store_true",
                   help="With --recut: publish the corrected episode under a bumped name (e.g. "
                        "<base>-r2.mp3) — a NEW feed GUID, so it appears as a fresh episode and EXISTING "
                        "subscribers re-download it. The original is left in place (hide/delete it via "
                        "the admin if desired). Use when the correction must reach people who already "
                        "downloaded; a fresh publish date is stamped.")

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
    two.add_argument("--male-voice", default="am_liam", help="Kokoro voice for the male host (default am_liam; am_eric is the documented backup).")
    two.add_argument("--minutes", type=float, default=3.0, help="Target spoken length per episode (default 3).")
    two.add_argument("--brief", default=DEFAULT_BRIEF, help="Editorial brief: angle/tone for the episode.")
    two.add_argument("--model", default=DEFAULT_SCRIPT_MODEL, help=f"Dialogue-script model (default: {DEFAULT_SCRIPT_MODEL}).")
    two.add_argument("--script-in", type=Path, help="Load a pre-made script JSON; skip the LLM (single episode).")
    two.add_argument("--script-out", type=Path, help="Save the generated script JSON here.")
    two.add_argument("--turn-gap", type=float, default=0.45, help="Silence between speakers, seconds.")
    two.add_argument("--sub-gap", type=float, default=0.15, help="Silence between sub-chunks of one turn.")

    # ── Shared synthesis / packaging ──
    p.add_argument("--speed", type=float, default=0.9,
                   help="0.5–2.0 (default 0.9 — a touch under natural pace; reads as measured, not rushed).")
    p.add_argument("--language", default="en-us")
    p.add_argument("--max-chars", type=int, default=700, help="Per-synth chunk cap, < TTS's 800.")
    p.add_argument("--emphasis-gap", type=float, default=0.25,
                   help="Silence bracketing each «…» emphasis span — before AND after, seconds "
                        "(default 0.25). The span is spoken at the normal speed; the pauses set it "
                        "apart. 0 disables emphasis bracketing. Whole clauses, never single words.")
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
    p.add_argument("--podcast-url",
                   help="Override the podcast server base URL (default: pulled from Railway). Point at "
                        "a local server for testing; the upload token still comes from Railway, so the "
                        "local server must share PODCAST_UPLOAD_SECRET.")
    p.add_argument("--warmup-timeout", type=float, default=300.0,
                   help="Seconds to wait for the TTS service to wake from serverless sleep (0 = skip).")
    p.add_argument("--out", type=Path, help="Also write the MP3 to this local path (single episode only).")
    p.add_argument("--no-upload", action="store_true", help="Skip the podcast publish (needs --out).")
    p.add_argument("--no-transcript", action="store_true",
                   help="Skip the episode description sidecar published beside each MP3.")
    p.add_argument("--dry-run", action="store_true",
                   help="No synth/upload. One-voice: chunk preview. Dialogue: generate + print the script.")
    p.add_argument("-y", "--yes", action="store_true",
                   help="Skip the confirmation prompt that the rewriting formats (pm/exec/dialogue) "
                        "show before an LLM re-authors your text. Required for non-interactive runs.")
    args = p.parse_args()

    is_dialogue = args.format == "dialogue"
    # Map the format onto the one-voice pipeline's flags (it reads args.narrative/args.scrub/emphasize).
    args.narrative = args.format if args.format in ("pm", "exec") else None
    args.scrub = args.format == "scrub"
    args.emphasize = args.format == "emphasize"

    # Guard: the rewriting formats REWRITE the source with an LLM (even a dry-run invokes it
    # to preview), so authored files would not be spoken verbatim. Confirm before doing that
    # (fail-closed when non-interactive). A verbatim `dialogue --script-in` is NOT a rewrite —
    # it skips this gate and is covered by the pre-synth preflight confirm instead.
    _rewrites = args.format in WRITING_FORMATS and not (is_dialogue and getattr(args, "script_in", None))
    if _rewrites and not args.yes:
        kind = "two-host dialogue script" if is_dialogue else f"{args.format} briefing"
        msg = (f"⚠️  --format {args.format} REWRITES your text — an LLM authors a {kind}, so your "
               f"files will NOT be spoken verbatim.\n"
               f"    For authored files, use --format emphasize (adds emphasis + TTS, no rewriting).\n"
               f"    Proceed with rewriting? [y/N] ")
        try:
            if not sys.stdin.isatty():
                _die(f"--format {args.format} rewrites the source; pass --yes to confirm in a "
                     f"non-interactive run, or use --format emphasize / read for authored files.")
            if input(msg).strip().lower() not in ("y", "yes"):
                _die("Aborted. Use --format emphasize (or read) to keep your authored text verbatim.")
        except (EOFError, KeyboardInterrupt):
            _die("Aborted.")

    for doc in args.docs:
        if not doc.exists():
            _die(f"Document not found: {doc}")
    if not (0.5 <= args.speed <= 2.0):
        _die("--speed must be between 0.5 and 2.0")

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
             ("female-voice", args.female_voice, "af_nova"), ("male-voice", args.male_voice, "am_liam"),
             ("minutes", args.minutes, 3.0), ("brief", args.brief, DEFAULT_BRIEF),
             ("model", args.model, DEFAULT_SCRIPT_MODEL), ("script-in", args.script_in, None),
             ("script-out", args.script_out, None)],
            f"--format dialogue option; not valid with --format {args.format}",
        )

    # The podcast show is the synth target; an empty slug has nowhere to publish.
    if not args.no_upload and not (args.show and args.show.strip()):
        _die("--show needs a podcast show slug (e.g. tech, clinical, sales, general).")

    # Recut targets one existing episode by exact name — single-episode, must publish.
    if args.force_redownload and not args.recut:
        _die("--force-redownload only applies with --recut.")
    if args.recut:
        if not args.name:
            _die("--recut needs --name = the exact existing base (e.g. tkr-generic-ep1.mp3).")
        if args.no_upload:
            _die("--recut replaces a published episode; it can't combine with --no-upload.")
        if args.combine or len(args.docs) != 1:
            _die("--recut is single-episode: pass exactly one source doc.")

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
                    f"[{i+1}/{len(chunks)}]{' «emph»' if isinstance(c, Emph) else ''} {c}"
                    for i, c in enumerate(chunks[:3])
                )
                print(preview)
                if len(chunks) > 3:
                    print(f"\n… (+{len(chunks) - 3} more chunks)")
        return

    # Resolve endpoint + secrets ONCE; the token is shared across every episode.
    tts_url, tts_token = _resolve_tts_endpoint(args)
    _wait_for_tts_ready(tts_url, timeout=args.warmup_timeout)
    secret, base_url = (None, None)
    if not args.no_upload:
        secret, base_url = _resolve_podcast_target(args)

    # Every publish stamps a Pacific-time <pubDate>. A recut is the one case that overrides it
    # (preserve the original date) — and the one case that pre-flights the target's existence.
    published_at = _now_pacific_iso()
    recut_before = None
    if args.recut:
        target = _safe_mp3_name(Path("recut"), args.name)
        recut_before = {e["name"]: e for e in _list_show_episodes(args.show, base_url=base_url, secret=secret)}
        if target not in recut_before:
            _die(f"--recut: no episode named {target!r} in show {args.show!r} — refusing to create a "
                 f"stray. Existing: {sorted(recut_before) or '(none)'}")
        if args.force_redownload:
            args.name = _next_recut_name(target, set(recut_before))
            _log(f"♻️  --force-redownload: publishing as {args.name!r} — new GUID, so existing "
                 f"subscribers re-download. Original {target!r} left in place (hide/delete via admin).")
            # fresh episode → fresh publish date (already 'now')
        else:
            prior = recut_before[target]
            published_at = prior.get("published_at") or _epoch_to_pacific_iso(prior["mtime"])
            _log(f"♻️  recut: overwriting {target!r} in place — same GUID (new subscribers get the fix; "
                 f"prior downloaders keep the old copy). Preserving publish date {published_at}.")

    # Preflight: show exactly what the (billed) synth will publish, then confirm. Interactive
    # only — a non-tty run prints the panel and proceeds (so background/CI publishing is
    # unchanged); pass --yes to skip the prompt in a tty.
    _preflight(args, episodes, is_dialogue, recut_before=recut_before, base_url=base_url, secret=secret)
    if not args.yes and sys.stdin.isatty():
        if input("\n  Proceed with synthesis? [y/N] ").strip().lower() not in ("y", "yes"):
            _die("Aborted at preflight — nothing synthesized or published.")

    if is_dialogue:
        # Sequential episodes: each is its own LLM call + multi-turn synth.
        for ep_docs in episodes:
            process_dialogue(
                ep_docs, args, tts_url=tts_url, tts_token=tts_token,
                secret=secret, base_url=base_url, published_at=published_at,
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
                        secret=secret, base_url=base_url, chunk_concurrency=1,
                        published_at=published_at,
                    )
                    for doc in ep_inputs
                ]
                for fut in concurrent.futures.as_completed(futs):
                    fut.result()  # _die() in a worker propagates here and exits
        else:
            _process_doc(
                ep_inputs[0], args, tts_url=tts_url, tts_token=tts_token,
                secret=secret, base_url=base_url, chunk_concurrency=args.concurrency,
                published_at=published_at,
            )

    # Recut verification: confirm a REPLACE (item count unchanged, target's size changed) or,
    # for --force-redownload, an ADD (one new item). Fails loud if the shape is unexpected.
    if args.recut and recut_before is not None:
        after = {e["name"]: e for e in _list_show_episodes(args.show, base_url=base_url, secret=secret)}
        if args.force_redownload:
            if args.name not in after or len(after) != len(recut_before) + 1:
                _die(f"recut verify: expected one new episode {args.name!r}; "
                     f"before={len(recut_before)} after={len(after)}.")
            _log(f"✅ recut verified: added {args.name!r} ({after[args.name]['size']} bytes); "
                 f"{len(after)} episodes total.")
        else:
            tgt = _safe_mp3_name(Path("recut"), args.name)
            if len(after) != len(recut_before):
                _die(f"recut verify: item count changed ({len(recut_before)}→{len(after)}) — "
                     f"expected an in-place replace, not an add.")
            old_sz, new_sz = recut_before[tgt]["size"], after[tgt]["size"]
            _log(f"✅ recut verified: replaced {tgt!r} in place — {len(after)} episodes (unchanged), "
                 f"enclosure {old_sz}→{new_sz} bytes.")

    if base_url:
        _log(f"   show {args.show!r} → {base_url}/{args.show}/<code>/feed.xml — episode is now in the feed")


if __name__ == "__main__":
    main()
