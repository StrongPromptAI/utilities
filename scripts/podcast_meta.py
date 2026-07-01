"""Podcast episode meta helper — hide / unhide / set sort_order / set title without a recut.

The recut `--force-redownload` flow is a THREE-step move (doc-to-audio SKILL): (1) recut bumps to
`-rN`, then you must (2) hide the just-superseded prior version so the feed doesn't show duplicates,
and (3) re-set `sort_order` on the fresh `-rN` (a new-GUID episode carries none). Steps 2–3 are meta
POSTs to `/show/{slug}/ep/{name}/meta` — this is the reusable, scriptable arm for them (and for
cleaning up orphan partial uploads a flaky-server 502 can leave behind).

Usage:
    uv run python scripts/podcast_meta.py list <slug> [name-substr]
    uv run python scripts/podcast_meta.py hide <slug> <name.mp3> [<name2.mp3> ...]
    uv run python scripts/podcast_meta.py show <slug> <name.mp3> ...        # unhide
    uv run python scripts/podcast_meta.py order <slug> <name.mp3> <N>
    uv run python scripts/podcast_meta.py title <slug> <name.mp3> "<title>"

`hidden`/`sort_order` live in the server DB (not the on-disk file), so these are pure metadata
writes — no re-synth, no re-upload, audio untouched. Same `aud="podcast-upload"` bearer as uploads.
"""
from __future__ import annotations

import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import doc_to_speech as d  # noqa: E402
import requests  # noqa: E402


def _resolve():
    return d._resolve_podcast_target(SimpleNamespace(podcast_url=None))


def _post_meta(slug: str, name: str, body: dict, *, base_url: str, secret: str) -> None:
    now = int(time.time())
    bearer = d._hs256_jwt({"aud": "podcast-upload", "iat": now, "exp": now + 1800}, secret)
    url = f"{base_url}/show/{slug}/ep/{name}/meta"
    r = requests.post(url, json=body, headers={"Authorization": f"Bearer {bearer}"}, timeout=(15, 120))
    ok = "✅" if r.status_code == 200 else "❌"
    print(f"  {ok} {name} ← {body}  ({r.status_code}) {'' if r.status_code == 200 else r.text[:160]}")
    if r.status_code != 200:
        raise SystemExit(1)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, slug = argv[0], argv[1]
    secret, base = _resolve()
    if cmd == "list":
        substr = argv[2] if len(argv) > 2 else ""
        eps = d._list_show_episodes(slug, base_url=base, secret=secret)
        for e in sorted(eps, key=lambda x: x["name"]):
            if substr in e["name"]:
                print(f"  {e['name']:44} {e.get('size',0):>10}b  "
                      f"transcript={e.get('has_transcript')}  pub={e.get('published_at')}")
        return 0
    if cmd == "hide":
        for name in argv[2:]:
            _post_meta(slug, name, {"hidden": True}, base_url=base, secret=secret)
        return 0
    if cmd == "show":
        for name in argv[2:]:
            _post_meta(slug, name, {"hidden": False}, base_url=base, secret=secret)
        return 0
    if cmd == "order":
        name, n = argv[2], int(argv[3])
        _post_meta(slug, name, {"sort_order": n}, base_url=base, secret=secret)
        return 0
    if cmd == "title":
        name, title = argv[2], argv[3]
        _post_meta(slug, name, {"title": title}, base_url=base, secret=secret)
        return 0
    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
