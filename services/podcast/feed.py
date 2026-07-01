"""RSS builder — on-disk audio listing ⟕ SQLite overrides → iTunes-tagged XML.

A feed item exists for every MP3 on disk; the episode row (if any) overrides the
derived defaults. Enclosure URLs point back at this server's own `ep/` route
(code-bearing for private shows) so audio is always fetched live off the volume.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

from sqlalchemy.orm import Session

from models import Episode, Podcast
from storage import AudioFile, folder_signature, list_audio

# Rendered-feed cache: slug → (signature, xml). The feed is expensive to build (read every
# transcript off the volume + render each markdown→HTML on every request — ~20 s on a big show),
# but it changes only when a file or an episode row changes. So we cache the built XML keyed on a
# cheap signature (folder stat + the DB rows/podcast fields the feed renders); an unchanged show
# returns the cached XML in O(stat) instead of re-reading + re-rendering. Per-process (a module
# global); a multi-worker deploy just caches independently, still correct.
_FEED_CACHE: dict[str, tuple] = {}


def _feed_signature(podcast: Podcast, rows: dict, base_url: str) -> tuple:
    """Everything the feed's bytes depend on, cheaply: the folder's stat signature (no content
    reads), the podcast-channel fields, the per-episode DB overrides, and base_url (enclosure URLs
    embed it + the private code). If this is unchanged, the built XML is byte-identical."""
    pod = (podcast.slug, podcast.code, podcast.access, podcast.title, podcast.description,
           podcast.author, podcast.language, podcast.category, podcast.explicit)
    eps = tuple(sorted(
        (fn, e.hidden, e.sort_order, e.title, e.description, e.duration_seconds,
         e.published_at.isoformat() if e.published_at else None)
        for fn, e in rows.items()
    ))
    return (base_url, pod, eps, folder_signature(podcast.folder))


def _derive_title(filename: str) -> str:
    stem = filename[:-4] if filename.lower().endswith(".mp3") else filename
    return stem.replace("_", " ").strip()


def _md_inline(text: str) -> str:
    """Inline markdown → HTML on ALREADY entity-escaped text. `xml.sax.saxutils.escape`
    only touches `& < >`, so the markdown markers (`* _ [ ]`) survive to be converted here,
    and the tags we insert (added after escaping) stay literal. Order matters: links, then
    bold (`**`), then italics (`_`/`*`) so the double-star isn't eaten by the single-star rule."""
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![A-Za-z0-9_])_([^_\n]+)_(?![A-Za-z0-9_])", r"<em>\1</em>", text)
    text = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])", r"<em>\1</em>", text)
    return text


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")


def _transcript_html(md: str) -> str:
    """Render a transcript markdown body to the HTML <content:encoded> carries — headings
    (`#`→<h2> … `######`→<h6>, capped), `**bold**`/`_italic_`/`*italic*`, `[text](url)` links,
    and `- ` bullet blocks → <ul>; every other blank-line-separated block → a <p>. Without this,
    podcast apps render the raw `**`/`##`/`_` characters. Text is entity-escaped first (it sits in
    CDATA but must still be valid HTML) and any literal `]]>` is defanged so it can't close it early."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", md.strip()) if b.strip()]
    parts: list[str] = []
    for b in blocks:
        lines = b.split("\n")
        h = _HEADING.match(b) if len(lines) == 1 else None
        if h:
            level = min(len(h.group(1)) + 1, 6)  # `#`→h2, `##`→h3, …
            parts.append(f"<h{level}>{_md_inline(escape(h.group(2).strip()))}</h{level}>")
        elif all(_BULLET.match(ln) for ln in lines):
            items = "".join(
                f"<li>{_md_inline(escape(_BULLET.match(ln).group(1).strip()))}</li>" for ln in lines
            )
            parts.append(f"<ul>{items}</ul>")
        else:
            parts.append(f"<p>{_md_inline(escape(' '.join(b.split())))}</p>")
    return "".join(parts).replace("]]>", "]]&gt;")


def _rfc822(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt)


def _duration_hms(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def _episode_base(podcast: Podcast, base_url: str) -> str:
    base = base_url.rstrip("/")
    if podcast.access == "public":
        return f"{base}/{podcast.slug}"
    return f"{base}/{podcast.slug}/{podcast.code}"


def build_feed(session: Session, podcast: Podcast, base_url: str) -> str:
    """Cached RSS build. Fetches the (cheap) DB rows, computes a change-signature, and returns the
    cached XML if nothing the feed depends on has changed — otherwise reads the transcripts and
    renders. Correctness rides on the signature capturing every input (`_feed_signature`)."""
    rows = {
        e.filename: e
        for e in session.query(Episode).filter_by(podcast_slug=podcast.slug).all()
    }
    sig = _feed_signature(podcast, rows, base_url)
    hit = _FEED_CACHE.get(podcast.slug)
    if hit is not None and hit[0] == sig:
        return hit[1]
    xml = _render_feed(podcast, base_url, rows)
    _FEED_CACHE[podcast.slug] = (sig, xml)
    return xml


def _render_feed(podcast: Podcast, base_url: str, rows: dict) -> str:
    """The actual (expensive) build: read every transcript off the volume and render it to HTML.
    Only called on a cache miss. `rows` is the already-fetched {filename: Episode} override map."""
    files: list[AudioFile] = list_audio(podcast.folder)

    ep_base = _episode_base(podcast, base_url)
    self_url = f"{ep_base}/feed.xml"

    # Build (sort_key, item_xml) tuples, then order: explicit sort_order first
    # (ascending), else newest-first by pubDate.
    items: list[tuple] = []
    any_order = any(rows.get(f.name) and rows[f.name].sort_order is not None for f in files)

    for f in files:
        row = rows.get(f.name)
        if row is not None and row.hidden:
            continue
        title = (row.title if row and row.title else None) or _derive_title(f.name)
        desc = (row.description if row and row.description else None) or (f.sidecar or "")
        pub_dt = (row.published_at if row and row.published_at else None) or datetime.fromtimestamp(
            f.mtime, tz=timezone.utc
        )
        enclosure = f"{ep_base}/ep/{quote(f.name)}"

        parts = [
            "<item>",
            f"<title>{escape(title)}</title>",
            f'<guid isPermaLink="false">{escape(podcast.slug)}:{escape(f.name)}</guid>',
            f"<pubDate>{escape(_rfc822(pub_dt))}</pubDate>",
            f"<enclosure url={quoteattr(enclosure)} length=\"{f.size}\" type=\"audio/mpeg\"/>",
        ]
        if desc:
            parts.append(f"<description>{escape(desc)}</description>")
            parts.append(f"<itunes:summary>{escape(desc)}</itunes:summary>")
        # Full transcript → rich show notes. A DB description override is authoritative for the
        # short blurb but does NOT replace the transcript; the `<base>-transcript.md` sidecar is
        # the only transcript source, so apps that render <content:encoded> show the full text.
        if f.transcript:
            parts.append(f"<content:encoded><![CDATA[{_transcript_html(f.transcript)}]]></content:encoded>")
        if row and row.duration_seconds:
            parts.append(f"<itunes:duration>{_duration_hms(row.duration_seconds)}</itunes:duration>")
        if row and row.sort_order is not None:
            parts.append(f"<itunes:episode>{row.sort_order}</itunes:episode>")
        parts.append(f"<itunes:explicit>{'true' if podcast.explicit else 'false'}</itunes:explicit>")
        parts.append("</item>")

        order_key = (
            (0, row.sort_order) if (row and row.sort_order is not None)
            else (1, -pub_dt.timestamp())
        )
        items.append((order_key, "".join(parts)))

    items.sort(key=lambda t: t[0])
    items_xml = "".join(x for _, x in items)

    title = escape(podcast.title)
    desc = escape(podcast.description or "")
    author = escape(podcast.author or podcast.title)
    image_url = f"{base_url.rstrip('/')}/artwork/{podcast.slug}"

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" '
        'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:atom="http://www.w3.org/2005/Atom">'
        "<channel>"
        f"<title>{title}</title>"
        f"<atom:link href={quoteattr(self_url)} rel=\"self\" type=\"application/rss+xml\"/>"
        f"<link>{escape(base_url.rstrip('/'))}</link>"
        f"<description>{desc}</description>"
        f"<language>{escape(podcast.language)}</language>"
        f"<itunes:author>{author}</itunes:author>"
        f"<itunes:summary>{desc}</itunes:summary>"
        f"<itunes:category text={quoteattr(podcast.category)}/>"
        f'<itunes:image href={quoteattr(image_url)}/>'
        f"<itunes:explicit>{'true' if podcast.explicit else 'false'}</itunes:explicit>"
        + items_xml
        + "</channel></rss>"
    )
