"""StrongPrompt podcast server.

Multi-show RSS over a Railway volume (no object storage). A show is a folder of
MP3s on the volume (`/data/audio/<folder>/`) plus an editable metadata row in
SQLite (`/data/podcast.db`). Three private (code-in-URL) shows + one public.

Routes:
  GET  /health
  GET  /{slug}/{code}/feed.xml      private feed (code in path; 404 on miss)
  GET  /{slug}/feed.xml             public feed (no code)
  GET  /{slug}/{code}/ep/{name}     private episode audio (Range-enabled)
  GET  /{slug}/ep/{name}            public episode audio
  GET  /{slug}/{code}/ep/{name}/transcript   private episode transcript (raw markdown)
  GET  /{slug}/ep/{name}/transcript          public episode transcript
  GET  /{slug}/{code}/ep/{name}/transcript.html  private transcript, rendered HTML
  GET  /{slug}/ep/{name}/transcript.html         public transcript, rendered HTML
  GET  /artwork/{slug}             channel cover art
  PUT  /upload/{slug}/{name}        service-token upload (headless producers)

Audio bytes stream straight off the volume via FileResponse (HTTP Range handled
by Starlette). Admin panel (Starlette-Admin) lands in Phase 3.
"""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from html import escape

import httpx
import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from db import SessionLocal, init_db
from feed import build_feed
from models import Episode, Podcast
from storage import (
    artwork_path,
    audio_path,
    delete_file,
    list_audio,
    transcript_path,
    write_upload,
    write_upload_stream,
)

UPLOAD_SECRET = os.environ.get("PODCAST_UPLOAD_SECRET", "")

_ART_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="podcast", lifespan=lifespan)


# ── helpers ─────────────────────────────────────────────────────────────────

def _base_url(request: Request) -> str:
    return (os.environ.get("PODCAST_PUBLIC_BASE") or str(request.base_url)).rstrip("/")


def _load_show(session, slug: str) -> Podcast:
    show = session.get(Podcast, slug)
    if show is None or not show.visible:
        raise HTTPException(404, "not found")
    return show


def _gate_private(show: Podcast, code: str) -> None:
    """Private shows require a matching code; 404 (not 403) so a wrong code
    can't confirm the route exists. Public shows must NOT be reached via the
    coded routes."""
    if show.access != "private" or not show.code:
        raise HTTPException(404, "not found")
    if not hmac.compare_digest(code, show.code):
        raise HTTPException(404, "not found")


def _require_public(show: Podcast) -> None:
    if show.access != "public":
        raise HTTPException(404, "not found")


def _serve_audio(show: Podcast, name: str) -> FileResponse:
    p = audio_path(show.folder, name)
    if p is None:
        raise HTTPException(404, "not found")
    # No content-disposition: attachment (don't pass filename) — serve as a normal, cacheable
    # podcast enclosure so clients add it to the library and KEEP it, instead of treating it as a
    # throwaway download. FileResponse still sets Accept-Ranges + emits 206 on Range: requests.
    return FileResponse(p, media_type="audio/mpeg",
                        headers={"Cache-Control": "public, max-age=604800"})


def _serve_transcript(show: Podcast, name: str) -> FileResponse:
    """Serve an episode's `<base>-transcript.md` sidecar as raw markdown. Same code/visibility
    gate as the audio route, so a private show's transcript is only reachable with the code —
    and as plain markdown it's a clean fetch target for an LLM session (no XML/HTML to parse)."""
    p = transcript_path(show.folder, name)
    if p is None:
        raise HTTPException(404, "no transcript")
    return FileResponse(p, media_type="text/markdown; charset=utf-8", filename=p.name)


def _render_transcript_page(name: str, md: str | None) -> str:
    """A standalone HTML page that renders the transcript markdown with marked.js (client-side,
    pinned major version) — readable prose, not raw `.md`. The raw markdown is HTML-escaped into a
    hidden node and read back via `textContent` (which decodes the entities → the original
    markdown), so nothing in the transcript can inject markup into the page before marked renders
    it. Missing transcript → a friendly message, not a blank page."""
    t = escape(name)
    if md is None:
        inner = '<div id="out"><p style="color:#888">No transcript for this episode.</p></div>'
        script = ""
    else:
        inner = f'<div id="md-src" hidden>{escape(md)}</div><div id="out"></div>'
        script = (
            '<script src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"></script>'
            '<script>document.getElementById("out").innerHTML='
            'marked.parse(document.getElementById("md-src").textContent);</script>'
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>Transcript — {t}</title>"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<style>"
        "body{font:16px/1.65 -apple-system,BlinkMacSystemFont,system-ui,sans-serif;"
        "max-width:46rem;margin:2.5rem auto;padding:0 1.25rem;color:#1a1a1a}"
        "h1{font-size:1.6rem;line-height:1.25}h2{font-size:1.3rem;margin-top:2rem;line-height:1.3}"
        "h3{font-size:1.1rem;margin-top:1.5rem}p{margin:1rem 0}a{color:#0b66c3}"
        "code{background:#f3f3f3;padding:.1em .3em;border-radius:3px}"
        "pre{background:#f6f8fa;padding:1rem;overflow:auto;border-radius:6px}"
        "blockquote{border-left:3px solid #ddd;margin:1rem 0;padding:.2rem 1rem;color:#555}"
        "</style></head><body>"
        f"{inner}{script}"
        "</body></html>"
    )


def _serve_transcript_html(show: Podcast, name: str) -> Response:
    """Render the `<base>-transcript.md` sidecar as a readable HTML page (the admin's
    'View transcript' link, opened in a new tab). Same code/visibility gate as the raw route."""
    p = transcript_path(show.folder, name)
    md = p.read_text(encoding="utf-8", errors="replace") if p is not None else None
    return HTMLResponse(_render_transcript_page(name, md))


def _feed_response(slug: str, code: str | None, request: Request) -> Response:
    with SessionLocal() as session:
        show = _load_show(session, slug)
        if code is None:
            _require_public(show)
        else:
            _gate_private(show, code)
        xml = build_feed(session, show, _base_url(request))
    return Response(content=xml, media_type="application/rss+xml",
                    headers={"Cache-Control": "no-store"})


# ── health ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "podcast"}


# ── upload (service-token; headless producers) ──────────────────────────────

def _verify_upload(request: Request) -> None:
    if not UPLOAD_SECRET:
        raise HTTPException(503, "upload not configured")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        jwt.decode(auth[7:], UPLOAD_SECRET, algorithms=["HS256"], audience="podcast-upload")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, f"invalid token: {exc}") from exc


@app.put("/upload/{slug}/{name}")
async def upload(slug: str, name: str, request: Request, _: None = Depends(_verify_upload)):
    # Resolve + validate the show, then release the DB session BEFORE the (possibly long,
    # large) transfer — don't hold a connection open while bytes stream in.
    with SessionLocal() as session:
        folder = _load_show(session, slug).folder
    # Stream the body straight to disk (constant memory) so large episodes don't OOM or
    # stall the app by buffering the whole file in RAM.
    try:
        _, nbytes = await write_upload_stream(folder, name, request.stream())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    # An MP3 upload may carry its duration + publish date (the producer computes/stamps them)
    # — upsert the episode row so the feed gets <itunes:duration> + <pubDate> without an
    # ffprobe pass or a mtime fallback.
    if name.lower().endswith(".mp3"):
        with SessionLocal() as session:
            _set_episode_meta(
                session, slug, name,
                request.headers.get("X-Duration-Seconds"),
                request.headers.get("X-Published-At"),
                mark_updated=True,   # the audio was just (re)written → stamp "last rendered"
            )
    return {"written": name, "bytes": nbytes}


def _coerce_int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _coerce_dt(v) -> "datetime | None":
    """Parse an ISO-8601 publish date (offset-aware, e.g. 2026-06-20T14:30:00-07:00) and
    normalize to UTC. SQLite doesn't preserve tzinfo on round-trip, so storing a fixed,
    known zone (UTC) keeps the instant stable — the feed renders it back (naive→UTC) as the
    correct moment, and a naive value (no offset) is read as already-UTC, not local."""
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_utc(dt) -> "str | None":
    """A stored datetime → explicit-UTC ISO string (so the episode list round-trips a recut's
    preserved publish date unambiguously, despite SQLite dropping tzinfo)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _set_episode_meta(
    session, slug: str, name: str, duration_seconds, published_at, *, mark_updated: bool = False
) -> None:
    """Upsert one episode row's duration + publish date from upload/import metadata. Each is
    set only when supplied, so a producer can send one without clobbering the other; a single
    row write covers both. `mark_updated` stamps `updated_at = now` — the audio was just
    (re)rendered. That's the admin-only "last rendered" signal; `published_at` stays the original
    publication date, so a recut is visible to the admin (file changed) WITHOUT resurfacing the
    episode in subscribers' feeds (same GUID + same date = a silent correction)."""
    secs = _coerce_int(duration_seconds)
    dt = _coerce_dt(published_at)
    if secs is None and dt is None and not mark_updated:
        return
    ep = session.query(Episode).filter_by(podcast_slug=slug, filename=name).one_or_none()
    if ep is None:
        ep = Episode(podcast_slug=slug, filename=name)
        session.add(ep)
    if secs is not None:
        ep.duration_seconds = secs
    if dt is not None:
        ep.published_at = dt
    if mark_updated:
        ep.updated_at = datetime.now(timezone.utc)
    session.commit()


class ImportRequest(BaseModel):
    source_url: str
    duration_seconds: int | None = None
    published_at: str | None = None


@app.post("/import/{slug}/{name}")
async def import_from_url(
    slug: str, name: str, req: ImportRequest, _: None = Depends(_verify_upload)
):
    """Server-side pull: the service fetches `source_url` (e.g. a presigned oxp.files
    URL) and writes it to the volume. Keeps large bytes off a slow client uplink —
    Railway↔origin is fast cloud-to-cloud."""
    with SessionLocal() as session:
        show = _load_show(session, slug)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
                r = await client.get(req.source_url, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"fetch failed: {exc}") from exc
        if r.status_code != 200 or not r.content:
            raise HTTPException(502, f"source returned {r.status_code}, {len(r.content)} bytes")
        try:
            write_upload(show.folder, name, r.content)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if name.lower().endswith(".mp3"):
            _set_episode_meta(session, slug, name, req.duration_seconds, req.published_at,
                              mark_updated=True)   # audio (re)written → stamp "last rendered"
    return {"written": name, "bytes": len(r.content)}


@app.delete("/upload/{slug}/{name}")
async def delete_upload(slug: str, name: str, _: None = Depends(_verify_upload)):
    """Service-token delete of one file from a show's volume folder."""
    with SessionLocal() as session:
        show = _load_show(session, slug)
    try:
        removed = delete_file(show.folder, name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"deleted": name, "removed": removed}


@app.get("/show/{slug}/episodes")
async def show_episodes(slug: str, _: None = Depends(_verify_upload)):
    """Service-token listing of a show's episodes (the same on-disk `*.mp3` set the feed is
    built from), with size + mtime. Lets a headless producer check an episode exists before a
    recut (fail loud on a typo'd name) and verify a replace afterwards (count unchanged, size
    changed) without needing the private feed code."""
    with SessionLocal() as session:
        show = _load_show(session, slug)
        rows = {
            e.filename: e
            for e in session.query(Episode).filter_by(podcast_slug=slug).all()
        }
        files = list_audio(show.folder)
        episodes = [
            {
                "name": f.name, "size": f.size, "mtime": f.mtime,
                "published_at": (
                    _iso_utc(rows[f.name].published_at) if rows.get(f.name) else None
                ),
                "duration_seconds": rows[f.name].duration_seconds if rows.get(f.name) else None,
                "has_transcript": f.transcript is not None,
            }
            for f in files
        ]
    return {"episodes": episodes}


class EpisodeMeta(BaseModel):
    title: str | None = None
    sort_order: int | None = None
    hidden: bool | None = None


@app.post("/show/{slug}/ep/{name}/meta")
async def set_episode_meta(
    slug: str, name: str, meta: EpisodeMeta, _: None = Depends(_verify_upload)
):
    """Service-token: set editable feed overrides (title, sort_order, hidden) on one episode
    WITHOUT re-uploading its audio. The feed item `<title>` is `Episode.title`; a plain CLI
    upload doesn't send one, so a published episode shows its prettified filename until this sets
    a real title. Upserts the row (an on-disk MP3 may not have an Episode row yet). The scriptable
    equivalent of the admin Episodes edit — only fields supplied are touched."""
    with SessionLocal() as session:
        _load_show(session, slug)  # 404s an unknown/hidden show
        ep = session.query(Episode).filter_by(podcast_slug=slug, filename=name).one_or_none()
        if ep is None:
            ep = Episode(podcast_slug=slug, filename=name)
            session.add(ep)
        if meta.title is not None:
            ep.title = meta.title
        if meta.sort_order is not None:
            ep.sort_order = meta.sort_order
        if meta.hidden is not None:
            ep.hidden = meta.hidden
        session.commit()
    return {"ok": True, "slug": slug, "name": name,
            "title": meta.title, "sort_order": meta.sort_order, "hidden": meta.hidden}


# ── artwork ─────────────────────────────────────────────────────────────────

@app.get("/artwork/{slug}")
async def artwork(slug: str):
    with SessionLocal() as session:
        show = _load_show(session, slug)
        folder = show.folder
    p = artwork_path(folder)
    if p is None:
        raise HTTPException(404, "no artwork")
    return FileResponse(p, media_type=_ART_MEDIA.get(p.suffix.lower(), "application/octet-stream"))


# ── feeds + episode audio ───────────────────────────────────────────────────

@app.get("/{slug}/{code}/feed.xml")
async def private_feed(slug: str, code: str, request: Request):
    return _feed_response(slug, code, request)


@app.api_route("/{slug}/{code}/ep/{name}", methods=["GET", "HEAD"])
async def private_audio(slug: str, code: str, name: str):
    with SessionLocal() as session:
        show = _load_show(session, slug)
        _gate_private(show, code)
        folder_show = show
    return _serve_audio(folder_show, name)


@app.api_route("/{slug}/{code}/ep/{name}/transcript", methods=["GET", "HEAD"])
async def private_transcript(slug: str, code: str, name: str):
    with SessionLocal() as session:
        show = _load_show(session, slug)
        _gate_private(show, code)
        folder_show = show
    return _serve_transcript(folder_show, name)


@app.get("/{slug}/{code}/ep/{name}/transcript.html")
async def private_transcript_html(slug: str, code: str, name: str):
    with SessionLocal() as session:
        show = _load_show(session, slug)
        _gate_private(show, code)
        folder_show = show
    return _serve_transcript_html(folder_show, name)


@app.get("/{slug}/feed.xml")
async def public_feed(slug: str, request: Request):
    return _feed_response(slug, None, request)


@app.api_route("/{slug}/ep/{name}", methods=["GET", "HEAD"])
async def public_audio(slug: str, name: str):
    with SessionLocal() as session:
        show = _load_show(session, slug)
        _require_public(show)
        folder_show = show
    return _serve_audio(folder_show, name)


@app.api_route("/{slug}/ep/{name}/transcript", methods=["GET", "HEAD"])
async def public_transcript(slug: str, name: str):
    with SessionLocal() as session:
        show = _load_show(session, slug)
        _require_public(show)
        folder_show = show
    return _serve_transcript(folder_show, name)


@app.get("/{slug}/ep/{name}/transcript.html")
async def public_transcript_html(slug: str, name: str):
    with SessionLocal() as session:
        show = _load_show(session, slug)
        _require_public(show)
        folder_show = show
    return _serve_transcript_html(folder_show, name)


# ── admin (Starlette-Admin + OIDC) — mounted at /admin, session middleware ──
# Registered last so the show/feed routes above stay first in match order.
from admin import setup_admin  # noqa: E402

setup_admin(app)
