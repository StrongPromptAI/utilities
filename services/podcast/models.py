"""SQLAlchemy models — the editable metadata overlay.

The bucket-free design: the Railway volume holds both this SQLite DB
(`/data/podcast.db`) and the audio (`/data/audio/<folder>/*.mp3`). These tables
carry only *editable overrides* — title, description, ordering, publish date,
duration, hidden — on top of whatever MP3s actually exist on disk. A feed is the
on-disk listing of a show's folder LEFT-JOINed with its episode rows; an MP3
with no row still appears, with defaults derived at build time.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


_PACIFIC = ZoneInfo("America/Los_Angeles")


def _fmt_pacific(dt: datetime | None) -> str:
    """A stored (UTC) datetime → human string in US Pacific with a PDT/PST label, for admin
    display only. Storage stays UTC; HTTP/feed timestamps stay UTC/GMT. Naive values are read
    as UTC (SQLite drops tzinfo on round-trip)."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_PACIFIC).strftime("%b %-d, %Y %-I:%M %p %Z")


class Base(DeclarativeBase):
    pass


class Podcast(Base):
    """One show. `folder` is the on-volume audio dir (`/data/audio/<folder>/`).

    `access` is the fail-closed gate: 'private' requires a `code` and the feed
    URL carries it (`/{slug}/{code}/feed.xml`); 'public' has `code = NULL` and a
    codeless feed URL (`/{slug}/feed.xml`).
    """

    __tablename__ = "podcasts"

    slug: Mapped[str] = mapped_column(String, primary_key=True)
    folder: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str] = mapped_column(String, nullable=False, default="")
    artwork_key: Mapped[str | None] = mapped_column(String, nullable=True)
    access: Mapped[str] = mapped_column(String, nullable=False, default="private")
    code: Mapped[str | None] = mapped_column(String, nullable=True)
    language: Mapped[str] = mapped_column(String, nullable=False, default="en-us")
    category: Mapped[str] = mapped_column(String, nullable=False, default="Technology")
    explicit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    episodes: Mapped[list[Episode]] = relationship(
        back_populates="podcast", cascade="all, delete-orphan"
    )

    def __str__(self) -> str:  # Starlette-Admin row label
        return f"{self.title} ({self.slug})"

    @property
    def feed_url(self) -> str:
        """Copy-pasteable subscribe URL — code-bearing for private shows, codeless
        for public. Surfaced read-only in the admin so the private link is one glance."""
        base = os.environ.get("PODCAST_PUBLIC_BASE", "").rstrip("/")
        if self.access == "public":
            return f"{base}/{self.slug}/feed.xml"
        return f"{base}/{self.slug}/{self.code}/feed.xml" if self.code else ""


class Episode(Base):
    """Editable overrides for one MP3 in a show's folder.

    NULL columns fall back to derived defaults at feed-build time:
    title←filename, description←transcript `.md` sidecar, published_at←file mtime,
    duration_seconds←supplied at upload (doc_to_podcast computes it) or ffprobe.
    `sort_order` set → explicit `<itunes:episode>` ordering; else newest-first by
    published_at.
    """

    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("podcast_slug", "filename", name="uq_show_file"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    podcast_slug: Mapped[str] = mapped_column(
        ForeignKey("podcasts.slug", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # "Last rendered" — stamped when the audio file is (re)uploaded (a cut/recut). Distinct from
    # published_at (the listener-facing original publication date, preserved across recuts): this
    # is the admin-only signal that the file changed. NULL until the episode is (re)published
    # through this service.
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    podcast: Mapped[Podcast] = relationship(back_populates="episodes")

    @property
    def updated_at_pacific(self) -> str:
        """`updated_at` ('last rendered') in US Pacific (PDT/PST) for the admin column — the stored
        value is UTC; this only localizes the display."""
        return _fmt_pacific(self.updated_at)

    def __str__(self) -> str:
        return self.title or self.filename
