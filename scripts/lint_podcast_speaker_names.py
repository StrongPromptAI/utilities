"""Anonymous-voice guard: podcast scripts must not bake in host identity.

A podcast episode is read by one or two **anonymous AI voices**, not by named
characters. The production choice is purely technical — one voice or two, and
which voice from the roster (main/backup, male/female) — never a persona. So:

  * One-voice scripts carry NO `**Speaker:**` turn labels. The one-voice path
    speaks the label aloud ("Host A:") AND prints it into the `<content:encoded>`
    show notes — an awkward artifact for what is just a TTS voice.
  * Two-voice scripts need a per-turn speaker key to alternate voices, but it
    must be a NEUTRAL voice-mapping token (`Host A`/`A`/`Voice 1`), never a
    personal name. A name (`Anna`, `Maya`) renders in the show notes and invites
    the script to have the voices "introduce themselves," which they must not.

This lint fails on a personal-name speaker label, or on any turn label in a
one-voice script — for files not in the legacy grandfather set below.

Run: python scripts/lint_podcast_speaker_names.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS = ROOT / "symlink_docs" / "podcast" / "transcripts"

# A turn label in the human-readable `.md` script: `**Speaker:** text…`.
LABEL_RE = re.compile(r"^\*\*(.+?):\*\*")
# Everything after this sentinel is the citations footer, not dialogue turns.
SHOWNOTES = "<!-- shownotes -->"

# A NEUTRAL voice-mapping token: a generic role word or a bare letter/number,
# optionally with a single disambiguator ("Host A", "A", "Voice 1", "Speaker").
# A personal name ("Anna", "Maya", "Ethan") never matches — the match is anchored
# to the whole label and names have more than one trailing letter.
NEUTRAL_RE = re.compile(
    r"^(?:host|co-?host|speaker|guest|voice|narrator|interviewer|[a-z0-9])(?:\s*[a-z0-9])?$",
    re.IGNORECASE,
)

# Episodes that predate the anonymous-voice rule and still ship with personal-name
# hosts in their published show notes. DELIBERATE, DOCUMENTED bridge (global
# CLAUDE.md § Fail Fast). End-condition: when one is next recut, drop the names
# (neutral turn markers, no self-introductions in the text) and DELETE it here.
# Do not add new entries — new named scripts must fail. Paths relative to TRANSCRIPTS.
GRANDFATHERED = {
    "sales/HealingJourneyPodcast_EP2.md",
    "sales/HealingJourneyPodcast_EP2.json",
    "sales/HealingJourneyPodcast_EP4.md",
    "sales/HealingJourneyPodcast_EP4.json",
    "tech/sales-mentoring-teaser.md",
    "tech/sales-mentoring-teaser.json",
}


def _md_speakers(path: Path) -> set[str]:
    speakers: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == SHOWNOTES:
            break
        m = LABEL_RE.match(line)
        if m:
            speakers.add(m.group(1).strip())
    return speakers


def _json_speakers(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return set()
    return {t.get("speaker", "").strip() for t in data.get("turns", []) if t.get("speaker")}


def _check(path: Path) -> tuple[str, list[str]] | None:
    """Return (reason, offending_labels) or None if the file is clean."""
    if path.suffix == ".md":
        speakers = _md_speakers(path)
    elif path.suffix == ".json":
        speakers = _json_speakers(path)
    else:
        return None
    if not speakers:
        return None  # one-voice prose with no turn labels — exactly right

    two_voice = path.suffix == ".json" or path.with_suffix(".json").exists()
    if not two_voice:
        # A labeled .md with no .json sibling: a one-voice script must be pure prose.
        return ("one-voice script carries turn labels (spoken aloud + shown in notes)",
                sorted(speakers))
    names = sorted(s for s in speakers if not NEUTRAL_RE.match(s))
    if names:
        return ("personal-name speaker labels (AI voices are anonymous — use a neutral marker)", names)
    return None


def main() -> int:
    if not TRANSCRIPTS.is_dir():
        print(f"SKIP — no transcripts dir at {TRANSCRIPTS}")
        return 0

    failures: list[str] = []
    grandfathered_hits: list[str] = []
    scanned = 0

    for path in sorted(TRANSCRIPTS.rglob("*")):
        if path.suffix not in (".md", ".json"):
            continue
        scanned += 1
        result = _check(path)
        if not result:
            continue
        reason, labels = result
        rel = path.relative_to(TRANSCRIPTS).as_posix()
        msg = f"{rel}: {reason}: {labels}"
        (grandfathered_hits if rel in GRANDFATHERED else failures).append(msg)

    for msg in grandfathered_hits:
        print(f"  legacy (grandfathered, fix on next recut): {msg}")

    if failures:
        print("FAIL — podcast scripts must use anonymous voices (no names, no labels in one-voice):")
        for msg in failures:
            print("  " + msg)
        print("\nOne-voice: remove the turn labels. Two-voice: use a neutral marker, not a name.")
        return 1

    print(f"PASS — {scanned} transcript file(s) scanned; voices stay anonymous.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
