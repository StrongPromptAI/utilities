"""Feed-cache correctness — the rendered feed is cached, but must NEVER go stale.

`build_feed` caches its XML keyed on a change-signature (folder stat + DB rows/podcast fields) so an
unchanged show skips the expensive read-every-transcript + render-to-HTML pass. These tests pin the
two things that make that safe: (1) a cache HIT does not re-render, and (2) every input that can
change the feed — a transcript edit on the volume, an episode meta change in the DB — busts it.

Run: cd services/podcast && uv run pytest test_feed_cache.py -q
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


@pytest.fixture()
def show(tmp_path, monkeypatch):
    """A two-episode 'tech' show on a temp volume + an in-memory DB. `storage.AUDIO_ROOT` is read
    once at import, so per-test isolation requires patching the module global directly (setting the
    env only affects the FIRST import) — patch it so each test gets its own tmp volume."""
    import storage
    monkeypatch.setattr(storage, "AUDIO_ROOT", tmp_path)
    folder = tmp_path / "tech"
    folder.mkdir()
    for n in (1, 2):
        (folder / f"ep{n}.mp3").write_bytes(b"x" * 1000)
        (folder / f"ep{n}-transcript.md").write_text(f"# Ep {n}\n\n**Host A:** hello _world_ {n}.\n")
        (folder / f"ep{n}.md").write_text(f"blurb {n}")

    # Import after the env is set; reset the module cache between tests.
    import feed as F
    from models import Base, Episode, Podcast
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    F._FEED_CACHE.clear()
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    session = Session(eng)
    pod = Podcast(slug="tech", folder="tech", title="Tech", description="d",
                  access="public", code=None)
    session.add(pod)
    session.add(Episode(podcast_slug="tech", filename="ep1.mp3", sort_order=1))
    session.commit()
    return F, session, pod, folder


def _count_renders(F, monkeypatch):
    calls = {"n": 0}
    real = F._transcript_html
    monkeypatch.setattr(F, "_transcript_html", lambda md: (calls.__setitem__("n", calls["n"] + 1) or real(md)))
    return calls


def test_cache_hit_does_not_rerender(show, monkeypatch):
    F, session, pod, _ = show
    calls = _count_renders(F, monkeypatch)
    x1 = F.build_feed(session, pod, "http://h")
    after_first = calls["n"]
    x2 = F.build_feed(session, pod, "http://h")
    assert x1 == x2, "cache hit must return byte-identical XML"
    assert calls["n"] == after_first, "cache hit must not re-render any transcript"
    assert after_first == 2, "first build renders both episodes' transcripts"


def test_transcript_edit_busts_cache(show, monkeypatch):
    F, session, pod, folder = show
    calls = _count_renders(F, monkeypatch)
    x1 = F.build_feed(session, pod, "http://h")
    time.sleep(0.01)  # ensure a distinct mtime
    (folder / "ep2-transcript.md").write_text("# Ep 2 EDITED\n\nnew _text_.\n")
    x2 = F.build_feed(session, pod, "http://h")
    assert x2 != x1, "a transcript edit on the volume must change the feed"
    assert calls["n"] > 2, "a file change must trigger a re-render (cache busted)"
    assert "EDITED" in x2


def test_db_meta_change_busts_cache(show, monkeypatch):
    F, session, pod, _ = show
    from models import Episode
    x1 = F.build_feed(session, pod, "http://h")
    assert "ep1.mp3" in x1
    session.query(Episode).filter_by(filename="ep1.mp3").one().hidden = True
    session.commit()
    x2 = F.build_feed(session, pod, "http://h")
    assert "ep1.mp3" not in x2, "hiding an episode must bust the cache and drop it from the feed"
    assert "ep2.mp3" in x2


def test_base_url_change_busts_cache(show):
    F, session, pod, _ = show
    a = F.build_feed(session, pod, "http://a")
    b = F.build_feed(session, pod, "http://b")
    assert a != b, "enclosure URLs embed base_url; a different base must not serve a cached feed"
