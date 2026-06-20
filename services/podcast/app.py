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

import httpx
import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from db import SessionLocal, init_db
from feed import build_feed
from models import Episode, Podcast
from storage import artwork_path, audio_path, delete_file, list_audio, write_upload, write_upload_stream

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
    # FileResponse sets Accept-Ranges + emits 206 on Range: requests on its own.
    return FileResponse(p, media_type="audio/mpeg", filename=name)


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


def _set_episode_meta(session, slug: str, name: str, duration_seconds, published_at) -> None:
    """Upsert one episode row's duration + publish date from upload/import metadata. Each is
    set only when supplied, so a producer can send one without clobbering the other; a single
    row write covers both."""
    secs = _coerce_int(duration_seconds)
    dt = _coerce_dt(published_at)
    if secs is None and dt is None:
        return
    ep = session.query(Episode).filter_by(podcast_slug=slug, filename=name).one_or_none()
    if ep is None:
        ep = Episode(podcast_slug=slug, filename=name)
        session.add(ep)
    if secs is not None:
        ep.duration_seconds = secs
    if dt is not None:
        ep.published_at = dt
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
        if name.lower().endswith(".mp3") and (req.duration_seconds or req.published_at):
            _set_episode_meta(session, slug, name, req.duration_seconds, req.published_at)
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
            }
            for f in files
        ]
    return {"episodes": episodes}


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


# ── admin (Starlette-Admin + OIDC) — mounted at /admin, session middleware ──
# Registered last so the show/feed routes above stay first in match order.
from admin import setup_admin  # noqa: E402

setup_admin(app)
