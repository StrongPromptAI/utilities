"""Show-notes guard: two-voice podcast scripts must name their speakers.

A `**Speaker:**`-labeled script is a TWO-VOICE dialogue (see the doc-to-audio
skill). In two-voice the labels map the voice and are *never spoken* — but they
are NOT cosmetic: the source `.md` is reproduced verbatim as the episode's
`<base>-transcript.md` sidecar, which the feed renders into `<content:encoded>`
show notes. So a label like `**Host A:**` is invisible in the audio yet shows up
in every podcast app's show notes. Generic placeholders ("Host A", "Host B",
"Speaker 1") read as unfinished there; real names (Anna, Chris, Sara) read as a
produced show.

This lint fails if any podcast transcript source (`.md` turn labels or `.json`
turn speakers) uses a placeholder speaker name — for any file not in the legacy
grandfather set below.

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

# A speaker name is a PLACEHOLDER when it is one of these generic role words,
# optionally followed by a single disambiguating letter/number ("Host A",
# "Speaker 1", "Guest"). Real names ("Anna", "Sara", "Maya") never match — the
# match is anchored to the whole label.
PLACEHOLDER_RE = re.compile(
    r"^(?:host|co-?host|speaker|guest|voice|narrator|interviewer)(?:\s+[a-z0-9])?$",
    re.IGNORECASE,
)

# Legacy episodes that predate the real-names rule. DELIBERATE, DOCUMENTED bridge
# (global CLAUDE.md § Fail Fast): they ship today with "Host A/Host B" in their
# already-published show notes. End-condition: when one of these is next recut,
# rename its speakers to real names in the `.md` (+ regenerate the `.json`) and
# DELETE it from this set. Do not add new entries — new placeholder scripts must
# fail. Paths are relative to TRANSCRIPTS.
GRANDFATHERED = {
    "sales/HealingJourneyPodcast_EP1.md",
    "sales/HealingJourneyPodcast_EP1.json",
    "sales/HealingJourneyPodcast_EP3.md",
    "sales/HealingJourneyPodcast_EP3.json",
    "sales/HealingJourneyPodcast_EP5.md",
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


def main() -> int:
    if not TRANSCRIPTS.is_dir():
        print(f"SKIP — no transcripts dir at {TRANSCRIPTS}")
        return 0

    failures: list[str] = []
    grandfathered_hits: list[str] = []
    scanned = 0

    for path in sorted(TRANSCRIPTS.rglob("*")):
        if path.suffix == ".md":
            speakers = _md_speakers(path)
        elif path.suffix == ".json":
            speakers = _json_speakers(path)
        else:
            continue
        if not speakers:
            continue  # one-voice narration (no turn labels) — nothing to name
        scanned += 1

        placeholders = sorted(s for s in speakers if PLACEHOLDER_RE.match(s))
        if not placeholders:
            continue

        rel = path.relative_to(TRANSCRIPTS).as_posix()
        msg = f"{rel}: placeholder speaker name(s) {placeholders}"
        if rel in GRANDFATHERED:
            grandfathered_hits.append(msg)
        else:
            failures.append(msg)

    for msg in grandfathered_hits:
        print(f"  legacy (grandfathered, rename on next recut): {msg}")

    if failures:
        print("FAIL — two-voice scripts must name their speakers (placeholders leak into show notes):")
        for msg in failures:
            print("  " + msg)
        print("\nUse real host names in the `.md` turn labels (and regenerate the `.json`).")
        return 1

    print(f"PASS — {scanned} labeled script(s) scanned; no new placeholder speaker names.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
