"""Admin panel — Starlette-Admin over the two tables, gated by the oidc-otp SSO.

OIDC integration (the careful bit, per the Phase-2 quick take):
  * `render_login` 302s to the IdP instead of rendering a username/password form.
  * the OIDC *callback* lives on the main app (outside the admin mount), verifies
    the id_token, and stores the email in the signed session.
  * `is_authenticated` only *reads* that session cookie — it never re-runs the
    OIDC dance per request (which would hammer the IdP on every page load).

Self-serve actions: "Sync episodes" (upsert a row per MP3 on the volume so the
admin table mirrors disk) and "Rotate feed code" (new code for a private show —
breaks existing subscribers, hence the confirmation).
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlencode

import httpx
import jwt as pyjwt
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette_admin import CustomView
from starlette_admin.actions import action
from starlette_admin.auth import AdminUser, AuthProvider
from starlette_admin.contrib.sqla import Admin, ModelView

from db import DB_PATH, SessionLocal, engine
from models import Episode, Podcast
from storage import list_audio

# ── config ──────────────────────────────────────────────────────────────────

OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "").rstrip("/")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
OIDC_REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
SESSION_HTTPS_ONLY = os.environ.get("SESSION_HTTPS_ONLY", "0") == "1"
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}

ADMIN_BASE = "/admin"


# ── shared logic (also unit-testable without the web layer) ─────────────────

def sync_episodes_for(slug: str) -> int:
    """Upsert an Episode row for every MP3 on the volume missing one. Returns the
    number of new rows. The feed already lazy-joins disk, so this only exists so
    the admin TABLE shows every file (editable). Never deletes rows."""
    created = 0
    with SessionLocal() as s:
        show = s.get(Podcast, slug)
        if show is None:
            return 0
        existing = {
            e.filename for e in s.query(Episode).filter_by(podcast_slug=slug).all()
        }
        for af in list_audio(show.folder):
            if af.name in existing:
                continue
            s.add(Episode(
                podcast_slug=slug,
                filename=af.name,
                published_at=datetime.fromtimestamp(af.mtime, tz=timezone.utc),
            ))
            created += 1
        s.commit()
    return created


def rotate_code_for(slug: str) -> str | None:
    """Mint a fresh code for a private show. Returns the new code (None if public)."""
    with SessionLocal() as s:
        show = s.get(Podcast, slug)
        if show is None or show.access != "private":
            return None
        show.code = secrets.token_urlsafe(32)
        s.commit()
        return show.code


# ── OIDC ────────────────────────────────────────────────────────────────────

def _authorize_url(state: str, nonce: str) -> str:
    q = urlencode({
        "client_id": OIDC_CLIENT_ID,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": OIDC_REDIRECT_URI,
        "state": state,
        "nonce": nonce,
    })
    return f"{OIDC_ISSUER}/authorize?{q}"


async def oidc_callback(request: Request) -> Response:
    """Main-app route (outside the admin mount). Exchanges the code, verifies the
    id_token, allowlists the email, stores it in the session, returns to /admin."""
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    if not code or not state or state != request.session.get("oidc_state"):
        return HTMLResponse("<h1>400</h1><p>state mismatch</p>", status_code=400)
    request.session.pop("oidc_state", None)

    async with httpx.AsyncClient(timeout=20.0) as client:
        tr = await client.post(f"{OIDC_ISSUER}/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": OIDC_REDIRECT_URI,
            "client_id": OIDC_CLIENT_ID,
            "client_secret": OIDC_CLIENT_SECRET,
        })
    if tr.status_code != 200:
        return HTMLResponse("<h1>502</h1><p>token exchange failed</p>", status_code=502)
    id_token = tr.json().get("id_token")
    if not id_token:
        return HTMLResponse("<h1>502</h1><p>no id_token</p>", status_code=502)

    try:
        jwks = pyjwt.PyJWKClient(f"{OIDC_ISSUER}/.well-known/jwks.json")
        key = jwks.get_signing_key_from_jwt(id_token).key
        claims = pyjwt.decode(
            id_token, key, algorithms=["RS256"],
            audience=OIDC_CLIENT_ID, issuer=OIDC_ISSUER,
        )
    except pyjwt.PyJWTError as exc:
        return HTMLResponse(f"<h1>401</h1><p>id_token invalid: {escape(str(exc))}</p>", status_code=401)

    email = (claims.get("email") or "").lower()
    if not email or email not in ADMIN_EMAILS:
        return HTMLResponse("<h1>403</h1><p>not an authorized admin</p>", status_code=403)

    request.session["user"] = email
    return RedirectResponse(f"{ADMIN_BASE}/", status_code=303)


class OIDCAuthProvider(AuthProvider):
    async def is_authenticated(self, request: Request) -> bool:
        email = request.session.get("user")
        if email and email.lower() in ADMIN_EMAILS:
            request.state.user = email
            return True
        return False

    def get_admin_user(self, request: Request) -> AdminUser:
        return AdminUser(username=getattr(request.state, "user", "admin"))

    async def render_login(self, request: Request, admin) -> Response:
        # No form — bounce straight to the IdP.
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(16)
        request.session["oidc_state"] = state
        return RedirectResponse(_authorize_url(state, nonce), status_code=303)

    async def logout(self, request: Request, response: Response) -> Response:
        request.session.clear()
        return response


# ── model views (with the self-serve actions) ──────────────────────────────

class PodcastView(ModelView):
    fields = ["slug", "title", "folder", "access", "code", "description",
              "author", "category", "language", "explicit", "visible"]
    actions = ["sync_episodes", "rotate_code", "delete"]

    @action(
        name="sync_episodes",
        text="Sync episodes from volume",
        confirmation="Create an episode row for every MP3 on disk that doesn't have one?",
        submit_btn_text="Sync",
    )
    async def sync_episodes(self, request: Request, pks: list) -> str:
        total = sum(sync_episodes_for(slug) for slug in pks)
        return f"Synced — {total} new episode row(s) across {len(pks)} show(s)."

    @action(
        name="rotate_code",
        text="Rotate feed code",
        confirmation="Rotating the code BREAKS every existing subscriber of this show. Continue?",
        submit_btn_class="btn-danger",
        submit_btn_text="Rotate",
    )
    async def rotate_code(self, request: Request, pks: list) -> str:
        rotated = [slug for slug in pks if rotate_code_for(slug)]
        return f"Rotated code for {len(rotated)} private show(s): {', '.join(rotated) or '—'}."


class EpisodeView(ModelView):
    fields = ["id", "podcast_slug", "filename", "title", "sort_order",
              "published_at", "duration_seconds", "hidden", "description"]


# ── /admin/volume — read-only window into the Railway volume ────────────────

class VolumeView(CustomView):
    async def render(self, request: Request, templates) -> Response:
        import shutil
        root = Path(DB_PATH).expanduser().parent
        rows = []
        total = 0
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            st = p.stat()
            total += st.st_size
            rel = p.relative_to(root)
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            rows.append(
                f"<tr><td><code>{escape(str(rel))}</code></td>"
                f"<td style='text-align:right'>{st.st_size:,}</td><td>{mtime}Z</td></tr>"
            )
        try:
            du = shutil.disk_usage(root)
            usage = f"{du.used // 1_000_000:,} MB used / {du.total // 1_000_000:,} MB"
        except OSError:
            usage = "n/a"
        body = (
            "<div style='font-family:system-ui;max-width:900px;margin:2rem auto'>"
            f"<h2>Volume — <code>{escape(str(root))}</code></h2>"
            f"<p>{len(rows)} file(s) · {total:,} bytes · disk {escape(usage)}</p>"
            "<table style='width:100%;border-collapse:collapse' cellpadding=6>"
            "<tr style='text-align:left;border-bottom:1px solid #ccc'>"
            "<th>path</th><th style='text-align:right'>bytes</th><th>modified</th></tr>"
            + "".join(rows) + "</table>"
            f"<p style='margin-top:1.5rem'><a href='{ADMIN_BASE}/'>← back to admin</a></p></div>"
        )
        return HTMLResponse(body)


# ── wiring ──────────────────────────────────────────────────────────────────

def setup_admin(app) -> None:
    """Mount the admin onto the FastAPI app + register OIDC callback + session."""
    # Session cookie (signed) — shared by the callback route AND the admin mount,
    # so it MUST live on the parent app, not just the admin sub-app.
    app.add_middleware(
        SessionMiddleware,
        secret_key=SESSION_SECRET or secrets.token_urlsafe(32),
        https_only=SESSION_HTTPS_ONLY,
        same_site="lax",
        max_age=14 * 24 * 3600,
    )
    app.add_route("/oidc/callback", oidc_callback, methods=["GET"])

    admin = Admin(
        engine,
        title="Podcast Admin",
        base_url=ADMIN_BASE,
        auth_provider=OIDCAuthProvider(),
    )
    admin.add_view(PodcastView(Podcast, label="Shows", icon="fa fa-podcast"))
    admin.add_view(EpisodeView(Episode, label="Episodes", icon="fa fa-music"))
    admin.add_view(VolumeView(label="Volume", icon="fa fa-hard-drive", path="/volume"))
    admin.mount_to(app)
