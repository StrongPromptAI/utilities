"""Brand-language guard for Healing-Journey / Eva podcast transcripts.

The Healing Journey brand (thj `symlink_docs/project/BRANDING.md`) bans a family of
words for operational reasons — the *app/install* family (app wars: nothing to install
for anyone in the care team; the service is entered via an invitation link in the
browser), *marketplace* words (they commodify the invitation-only, familial care-team
relationship), and *bot/chatbot* words (they turn Eva — who has a name — into a
technology category to evaluate). See `transcripts/BRAND_LANGUAGE.md` for the distilled
card + approved replacements.

This lint fails when a transcript that is *about* the Healing Journey / Eva / DME uses
one of those words in a spoken line. Scoping is by content signal (the file mentions
"Eva" or "Healing Journey"), so non-thj shows (e.g. the real-estate round table) are
never touched, and a new thj episode is covered automatically with no allowlist to edit.

Deliberately NOT banned here: `AI`, `agent`, `system`, `tool` — BRANDING forbids those
only in *patient-facing* prose, and these podcasts are stakeholder-facing (they describe
how the system works to DME SMEs; §11 itself says "generic AI" / "generic engine"). Hard-
failing them would be false-positive noise.

Run: uv run python scripts/lint_podcast_brand_words.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS = ROOT / "symlink_docs" / "podcast" / "transcripts"

# A file is thj-brand content if it names the assistant or the service.
BRAND_SIGNAL = re.compile(r"\b(Eva|Healing Journey)\b")

# Docs, not episodes: the brand-language card (which lists the banned words by design)
# and any README. Skipped so they don't self-flag. Paths relative to TRANSCRIPTS.
DOC_FILES = {"BRAND_LANGUAGE.md"}

# HARD-BAN: the app-wars family only. These are operationally forbidden and almost never
# legitimate in Healing-Journey content, even negated — exactly the class that bit us
# ("you open the app"). Word-boundary, case-insensitive. Value = why + the fix.
#
# Deliberately NOT hard-banned (kept in BRAND_LANGUAGE.md for human judgment, not regex):
# platform / software / marketplace / shop / chatbot / bot — all context-dependent. BRANDING
# itself uses "software-business commoditization" (§2) and "a chatbot that will answer any
# health question" (§11) to describe the *problem/competitor*, and "family does not price-shop"
# / "Not a marketplace" (§3) are legitimate negated uses. A regex can't tell Eva-the-service
# from the competitor being contrasted, so hard-failing them is false-positive noise.
FORBIDDEN = {
    "app": "app wars — say 'open the chat' / 'pull up Eva' / 'the invitation link'",
    "apps": "app wars — say 'open the chat' / 'pull up Eva'",
    "application": "app wars — say 'the service' / 'open the chat'",
    "install": "nothing to install — say 'accept the invitation' / 'tap the link'",
    "download": "nothing to download — say 'accept the invitation'",
}
FORBIDDEN_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in FORBIDDEN) + r")\b", re.IGNORECASE)

# Legacy episodes that predate this lint and still carry an app-wars word. DELIBERATE,
# DOCUMENTED bridge (global CLAUDE.md § Fail Fast) — fix on their next recut, then delete
# the entry. Do not add new entries; a new script with these words must fail. Paths rel to
# TRANSCRIPTS. (Populated after the first scan; the equipment-corpus series is already clean.)
GRANDFATHERED: set[str] = {
    "sales/HealingJourneyPodcast_EP1.md",
    "sales/HealingJourneyPodcast_EP4.md",
    "sales/HealingJourneyPodcast_EP5.md",
}

SHOWNOTES = "<!-- shownotes -->"
COMMENT_OPEN, COMMENT_CLOSE = "<!--", "-->"


def _spoken_lines(text: str) -> list[tuple[int, str]]:
    """Lines that are actually spoken: drop HTML-comment blocks (production headers,
    fact-check ledgers) and everything after the shownotes sentinel."""
    out: list[tuple[int, str]] = []
    in_comment = False
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s == SHOWNOTES:
            break
        if in_comment:
            if COMMENT_CLOSE in s:
                in_comment = False
            continue
        if s.startswith(COMMENT_OPEN) and COMMENT_CLOSE not in s:
            in_comment = True
            continue
        # strip a single-line comment's content
        line = re.sub(r"<!--.*?-->", "", line)
        out.append((n, line))
    return out


def _check(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if not BRAND_SIGNAL.search(text):
        return []  # not a Healing-Journey / Eva episode — out of scope
    hits: list[str] = []
    for n, line in _spoken_lines(text):
        for m in FORBIDDEN_RE.finditer(line):
            word = m.group(1).lower()
            hits.append(f"  L{n}: “{m.group(1)}” — {FORBIDDEN[word]}")
    return hits


def main() -> int:
    if not TRANSCRIPTS.is_dir():
        print(f"SKIP — no transcripts dir at {TRANSCRIPTS}")
        return 0
    failures: list[str] = []
    legacy: list[str] = []
    scanned = in_scope = 0
    for path in sorted(TRANSCRIPTS.rglob("*.md")):
        rel = path.relative_to(TRANSCRIPTS).as_posix()
        if path.name in DOC_FILES or path.name == "README.md":
            continue  # brand-language card / READMEs are docs, not episodes
        scanned += 1
        text = path.read_text(encoding="utf-8")
        if not BRAND_SIGNAL.search(text):
            continue
        in_scope += 1
        hits = _check(path)
        if hits:
            (legacy if rel in GRANDFATHERED else failures).append(f"{rel}:\n" + "\n".join(hits))

    for msg in legacy:
        print(f"legacy (grandfathered — fix on next recut):\n{msg}")
    if failures:
        print("\nFAIL — Healing-Journey brand words (see transcripts/BRAND_LANGUAGE.md):")
        for f in failures:
            print(f)
        return 1
    print(f"PASS — {scanned} transcript(s) scanned, {in_scope} in brand scope; language is clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
