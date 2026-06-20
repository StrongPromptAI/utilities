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
from storage import AudioFile, list_audio


def _derive_title(filename: str) -> str:
    stem = filename[:-4] if filename.lower().endswith(".mp3") else filename
    return stem.replace("_", " ").strip()


def _transcript_html(md: str) -> str:
    """Render a transcript markdown body to the minimal HTML <content:encoded> carries:
    a leading `# Title` → <h2>, every other blank-line-separated block → a <p> with its
    wrapped lines joined. Text is entity-escaped (it sits inside CDATA but must still be
    valid HTML), and any literal `]]>` is defanged so it can't close the CDATA early."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", md.strip()) if b.strip()]
    parts: list[str] = []
    for b in blocks:
        if b.startswith("# "):
            parts.append(f"<h2>{escape(b[2:].strip())}</h2>")
        else:
            parts.append(f"<p>{escape(' '.join(b.split()))}</p>")
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
    rows = {
        e.filename: e
        for e in session.query(Episode).filter_by(podcast_slug=podcast.slug).all()
    }
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
