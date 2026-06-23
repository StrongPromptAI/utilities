"""Convert a `**Speaker:** text` dialogue `.md` into the two-voice turns JSON.

The single source of truth for the `.md` → `.json` parse (the README used to inline
a heredoc copy, which drifted and let `##` headings get folded into a turn — so a
voice read the heading aloud). This drops the structure that is NOT spoken dialogue:

  * the `# title` line (captured into "title"),
  * `## … ######` section headings,
  * `---` horizontal dividers,

and stops at the `<!-- shownotes -->` sentinel so the citations footer never folds
into the last turn. `«…»` emphasis marks in turn text are preserved verbatim.

Usage: python scripts/md_to_turns.py <path/to/episode.md>   # writes episode.json beside it
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SPEAK = re.compile(r"^\*\*(.+?):\*\*\s*(.*)$")
HEADING = re.compile(r"^#{1,6}\s")
DIVIDER = re.compile(r"^-{3,}$")
SHOWNOTES = "<!-- shownotes -->"


def to_turns(md_text: str) -> dict:
    title: str | None = None
    turns: list[dict] = []
    cur: dict | None = None
    for line in md_text.splitlines():
        s = line.strip()
        if s == SHOWNOTES:
            break
        if title is None and line.startswith("# "):
            title = line[2:].strip()
            continue
        if HEADING.match(line) or DIVIDER.match(s):
            continue  # structure, not spoken dialogue — never fold into a turn
        m = SPEAK.match(line)
        if m:
            if cur:
                turns.append(cur)
            cur = {"speaker": m.group(1).strip(), "text": m.group(2).strip()}
        elif cur is not None and s:
            cur["text"] = (cur["text"] + " " + s).strip()
    if cur:
        turns.append(cur)
    return {"title": title, "turns": turns}


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/md_to_turns.py <episode.md>", file=sys.stderr)
        return 2
    md = Path(sys.argv[1])
    out = to_turns(md.read_text(encoding="utf-8"))
    md.with_suffix(".json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {md.with_suffix('.json').name}: {len(out['turns'])} turns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
