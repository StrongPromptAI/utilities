"""Cliché-filler guard for podcast transcripts: throat-clearing + payoff-bow openers.

A writing-craft check, the mechanical arm of `symlink_docs/podcast/transcripts/STYLE.md`.
It flags the filler tics that announce a point instead of making it — the ones that read as
padding to a listener and "just don't need to be there":

  1. **Throat-clearing preamble** — "Here's the problem / the key idea / the payoff / the one
     that trips people up", "Here's where I get stuck". A preamble that announces a point is
     not the point; open on the substance.
  2. **Payoff-bow** — "That's the whole point / win / picture / story", "which is the whole art".
     The sentence before already landed it; the bow just stamps "that was important".
  3. **Coaching-the-listener** — "the thing I most want your team to take/hold". State the point;
     let its weight show.
  4. **Reflex affirmation opener** — a turn that starts "Exactly." / "Precisely." / "Correct."
     every time. Vary it or fold it in so the rhythm doesn't flatten.

ADVISORY, never a hard fail — style is judgment-heavy. A *device* (a running phrase that pays
off later) or a *deliberate button* (an intentional closing echo) is craft, not filler, and only a
human can tell them apart (STYLE.md § "A device is not a tic"). So this prints warnings and always
returns 0 — same posture as the homograph check in `lint_podcast_prosody.py`. The value is surfacing
each hit at the `doc_to_audio` PREFLIGHT (a `⚠ Filler` row) before the billed synth, for an eyeball.

Run: uv run python scripts/lint_podcast_filler.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS = ROOT / "symlink_docs" / "podcast" / "transcripts"

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
SHOWNOTES = "<!-- shownotes -->"

# Curated, high-signal cliché openers. Case-INsensitive except the affirmation opener (which is
# capital-only, to catch only turn/sentence-initial "Exactly." — never mid-sentence "that's exactly").
# Each entry: (compiled regex, short reason). Keep the noun lists tight — a false positive on real
# prose ("the whole load", "the whole chain") erodes the signal, so the "whole ___" patterns only
# match the bow nouns, never literal-load idioms.
_CI = re.IGNORECASE
FILLER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bhere'?s the (thing|problem|deal|kicker|catch|point|key idea|key|payoff|"
                r"gap|part|rub|magic|beauty|hard part|one line|one thing|one that|distinction|"
                r"question|shape|reason)\b", _CI),
     "throat-clear ('Here's the ___') — open on the substance, not an announcement of it"),
    (re.compile(r"\bhere'?s where (i|we|you|it|the)\b", _CI),
     "throat-clear ('Here's where ___') — say it directly ('This is where ___', or just say it)"),
    (re.compile(r"\bthat'?s the whole (point|thing|deal|story|picture|win|series|game|idea|"
                r"reason|art|architecture|job|design|next)\b", _CI),
     "payoff-bow ('That's the whole ___') — the point already landed; drop the stamp"),
    (re.compile(r"\bwhich is the whole (art|point|thing|story|design|idea|reason)\b", _CI),
     "payoff-bow ('which is the whole ___') — cut the stamp; let the point stand"),
    (re.compile(r"\bthe (?:one )?thing I (?:most )?want (?:your team|you|the DME folks|listeners|"
                r"everyone) to (take|hold|hear|notice|get|catch)\b", _CI),
     "coaching-the-listener — state the point; its importance shows without announcing it"),
    (re.compile(r"\b(Exactly|Precisely|Correct)[.,]\s"),  # capital-only ⇒ turn/sentence-initial
     "reflex affirmation opener — vary it or fold it in so every turn doesn't start the same"),
]


def scan(text: str) -> list[tuple[str, str]]:
    """(matched snippet, reason) for each cliché-filler hit in a block of SPOKEN text."""
    hits: list[tuple[str, str]] = []
    for rx, reason in FILLER_PATTERNS:
        for m in rx.finditer(text):
            hits.append((m.group(0).strip(), reason))
    return hits


def _spoken(md_text: str) -> str:
    """Spoken portion of a transcript .md: drop the shownotes footer and every HTML comment
    (production headers / fact-check ledgers are never voiced and would false-positive)."""
    head = md_text.split(SHOWNOTES, 1)[0]
    return _COMMENT_RE.sub(" ", head)


def main() -> int:
    if not TRANSCRIPTS.is_dir():
        print(f"SKIP — no transcripts dir at {TRANSCRIPTS}")
        return 0
    scanned = 0
    flagged = 0
    for path in sorted(TRANSCRIPTS.rglob("*.md")):
        if "README" in path.name or path.name in {"BRAND_LANGUAGE.md", "STYLE.md"}:
            continue  # docs (they quote the banned openers by design), not episodes
        scanned += 1
        rel = path.relative_to(TRANSCRIPTS).as_posix()
        hits = scan(_spoken(path.read_text(encoding="utf-8")))
        if hits:
            flagged += 1
            print(f"  ⚠ filler — {rel}: {len(hits)} hit(s)")
            for snippet, reason in hits:
                print(f"      \"{snippet}\"  — {reason}")
    print(f"\n{scanned} transcript(s) scanned; {flagged} with cliché-filler warnings "
          f"(advisory — keep the ones doing real work, see STYLE.md § 'A device is not a tic').")
    return 0  # advisory: never blocks a publish


if __name__ == "__main__":
    raise SystemExit(main())
