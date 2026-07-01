"""Prosody pre-synth guard for podcast transcripts: emphasis-island + homograph checks.

Two failure modes that only show up when you *listen*, both cheap to catch before the
billed TTS synth:

  1. **Short emphasis spans.** Kokoro has no per-word stress; its only emphasis lever is
     spliced silence (a pause before AND after a «…» span). A span shorter than a clause
     therefore reads as an isolated island — a stutter, not stress. Emphasis spans must be
     whole clauses (≥ EMPH_MIN_WORDS words). Reported as a hard fail on transcript scan.

  2. **Homograph mispronunciation.** espeak-ng — Kokoro's phonemizer — mis-stresses several
     heteronyms by DEFAULT. The one that bit us: the verb "lives" reads /laɪvz/ (as in
     "saves lives") instead of /lɪvz/ — the "long-i sometimes, not other times" bug. These
     are context-dependent (espeak gets many right), so they are ADVISORY warnings, never a
     hard fail: verify each with `espeak-ng -v en-us --ipa -q "the whole phrase"` and fix
     with a targeted `--pron "lives=livz"` or a reword.

Same logic feeds the `doc_to_audio` PREFLIGHT panel (imported there), so these surface
automatically before every synth — this file is the standalone, whole-corpus runner.

Run: uv run python scripts/lint_podcast_prosody.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS = ROOT / "symlink_docs" / "podcast" / "transcripts"

# An emphasis span must be a whole clause. Fewer than this many words → island effect.
EMPH_MIN_WORDS = 4

# Heteronyms espeak commonly mis-reads in context (vowel-swap ones like "lives"/"wound"/
# "tear" are the highest risk — the "long-i sometimes" class — plus a few noun/verb stress
# pairs). Advisory only: the author verifies with espeak and fixes via --pron or a reword.
# Deliberately EXCLUDES words espeak reliably gets right by context (use, live, content,
# read-present, does, close) to keep the signal high.
HOMOGRAPHS = {
    "lives", "wound", "tear", "tears", "sow", "sows", "bow", "bows", "bass",
    "dove", "wind", "winds", "minute", "lead", "leads", "refuse", "record",
    "records", "present", "presents", "produce", "object", "objects", "subject",
    "subjects", "contract", "contracts", "invalid", "resume", "row", "rows",
}
# Legacy episodes with short spans that predate this rule — grandfathered (fix on next
# recut, then delete the entry). Do not add new entries; a new short span must fail.
GRANDFATHERED = {
    "sales/HealingJourneyPodcast_EP3.md",
    "tech/sales-mentoring-teaser.md",
}

_HOM_RE = re.compile(r"\b(" + "|".join(sorted(HOMOGRAPHS)) + r")\b", re.IGNORECASE)
_SPAN_RE = re.compile(r"«([^»]*)»")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
SHOWNOTES = "<!-- shownotes -->"


def scan(text: str) -> tuple[list[str], list[str]]:
    """(short_spans, homographs) for a block of SPOKEN text. `short_spans` are the offending
    «…» strings (< EMPH_MIN_WORDS words); `homographs` are the distinct flagged words, lower-cased."""
    short = [m.group(0) for m in _SPAN_RE.finditer(text)
             if len(m.group(1).split()) < EMPH_MIN_WORDS]
    homs = sorted({m.group(1).lower() for m in _HOM_RE.finditer(text)})
    return short, homs


def _spoken(md_text: str) -> str:
    """Spoken portion of a transcript .md: drop the shownotes footer and every HTML comment
    (production headers / fact-check ledgers carry «…» and homographs that are never voiced)."""
    head = md_text.split(SHOWNOTES, 1)[0]
    return _COMMENT_RE.sub(" ", head)


def main() -> int:
    if not TRANSCRIPTS.is_dir():
        print(f"SKIP — no transcripts dir at {TRANSCRIPTS}")
        return 0
    failures: list[str] = []
    legacy: list[str] = []
    warnings: list[str] = []
    scanned = 0
    for path in sorted(TRANSCRIPTS.rglob("*.md")):
        if "README" in path.name or path.name == "BRAND_LANGUAGE.md":
            continue  # docs (they quote «…» + banned words by design), not episodes
        scanned += 1
        rel = path.relative_to(TRANSCRIPTS).as_posix()
        short, homs = scan(_spoken(path.read_text(encoding="utf-8")))
        if short:
            msg = (f"{rel}: {len(short)} short emphasis span(s) (<{EMPH_MIN_WORDS} words) "
                   f"— whole clauses only: {short}")
            (legacy if rel in GRANDFATHERED else failures).append(msg)
        if homs:
            warnings.append(f"{rel}: homographs to verify (espeak / --pron): {', '.join(homs)}")

    for w in warnings:
        print(f"  ⚠ pronounce — {w}")
    for m in legacy:
        print(f"  legacy (grandfathered — fix on next recut): {m}")
    if failures:
        print("\nFAIL — emphasis islands (whole clauses only; see doc-to-audio SKILL):")
        for f in failures:
            print("  " + f)
        return 1
    print(f"\nPASS — {scanned} transcript(s) scanned; no short emphasis spans. "
          f"(homograph warnings above are advisory — verify with espeak.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
