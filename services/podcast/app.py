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

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

from db import SessionLocal, init_db
from feed import build_feed
from models import Episode, Podcast
from storage import artwork_path, audio_path, write_upload

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
    with SessionLocal() as session:
        show = _load_show(session, slug)
        body = await request.body()
        if not body:
            raise HTTPException(400, "empty body")
        try:
            write_upload(show.folder, name, body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        # An MP3 upload may carry its duration (doc_to_podcast computes it) — upsert
        # the episode row so the feed gets <itunes:duration> without an ffprobe pass.
        if name.lower().endswith(".mp3"):
            dur = request.headers.get("X-Duration-Seconds")
            if dur:
                ep = (
                    session.query(Episode)
                    .filter_by(podcast_slug=slug, filename=name)
                    .one_or_none()
                )
                if ep is None:
                    ep = Episode(podcast_slug=slug, filename=name)
                    session.add(ep)
                try:
                    ep.duration_seconds = int(float(dur))
                except ValueError:
                    pass
                session.commit()
    return {"written": name, "bytes": len(body)}


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
