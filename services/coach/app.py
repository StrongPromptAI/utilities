"""coach service — Phase 0 (graphics slice).

The first slice of the dedicated sales-coach service (plan 26-6-20). Today it
does ONE job: hold and serve the book/manual figures that the KB
`reference_docs` corpus cites, from a Railway volume in the kb project (internal
routing to Postgres + local-disk graphics → minimal egress). The chat backend
(`/api/chat*`), retrieval, widget, and the `coach.strongprompt.ai` CNAME are
grown onto this same service later (plan 26-6-20 Phases 1+).

Routes:
  GET  /health             liveness
  GET  /figures/{name}     serve a figure (FileResponse) from the volume
  POST /figures            upload a figure (HS256 service-token; headless ingest)

No bucket, no DB. Figures persist on the volume mounted at /data/figures.
"""
from __future__ import annotations

import json
import os
import os.path
import time

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from storage import MEDIA_TYPES, figure_path, safe_name, write_figure

import agent
import auth
import db
from embed import embed_query, make_stt_token

UPLOAD_SECRET = os.environ.get("COACH_UPLOAD_SECRET", "")
ZAI_API_KEY = os.environ.get("ZAI_API_KEY", "")

app = FastAPI(title="coach", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "coach"}


@app.get("/")
async def root():
    """Bare domain → the chat widget at /coach/ (the build is base-pathed there)."""
    return RedirectResponse(url="/coach/", status_code=307)


@app.get("/figures/{name}")
async def get_figure(name: str):
    try:
        p = figure_path(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if p is None:
        raise HTTPException(404, "not found")
    ext = p.suffix.lower()
    return FileResponse(p, media_type=MEDIA_TYPES.get(ext, "application/octet-stream"), filename=p.name)


# ── upload (service-token; headless ingest can't do an OIDC dance) ──────────

def _verify_upload(request: Request) -> None:
    if not UPLOAD_SECRET:
        raise HTTPException(503, "upload not configured")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        jwt.decode(auth[7:], UPLOAD_SECRET, algorithms=["HS256"], audience="coach-upload")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, f"invalid token: {exc}") from exc


@app.post("/figures")
async def upload_figure(file: UploadFile, _: None = Depends(_verify_upload)):
    try:
        name = safe_name(file.filename or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if name.rsplit(".", 1)[-1].lower() not in ("jpg", "jpeg", "png", "webp"):
        raise HTTPException(400, "only image figures (jpg/png/webp)")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    write_figure(name, data)
    return {"written": name, "bytes": len(data)}


# ── /api/chat — the agentic coach, OTP-gated, SSE-streamed ──────────────────
#
# Fail-closed gate: a valid coach_session JWT whose email is on coach_allowlist, or
# 401/403. The CNAME must not go live until this is in code (an ungated chat = an open,
# billable LLM surface). The model orchestrates retrieval via tools (agent.run_agent).

# embed_fn is swappable (tests inject local ONNX); default is the shared-svcs embed.
app.state.embed_fn = embed_query


# System prompt = persona + tunable behavior + value floor, composed from the coach_floor DB
# rows ('value_registry' + 'behavior'). Cached with a short TTL so a SQL UPDATE to either row
# takes effect within ~2 min — behavioral tuning is a data edit, not a redeploy (config-as-data,
# mirrors roadmap.settings). The hard integrity rules live in agent._PERSONA_CORE (code), not here.
_SYSTEM_TTL_SECONDS = 120
_system_cache: dict = {"text": None, "ts": 0.0}


def current_system(conn) -> str:
    """The current system prompt, rebuilt from coach_floor at most once per TTL. Fail-soft:
    on any DB error, reuse the last good prompt, or persona-only if we never loaded one."""
    now = time.time()
    if _system_cache["text"] is not None and now - _system_cache["ts"] <= _SYSTEM_TTL_SECONDS:
        return _system_cache["text"]
    try:
        floor = behavior = None
        with conn.cursor() as cur:
            cur.execute("SELECT key, content FROM coach_floor WHERE key IN ('value_registry', 'behavior')")
            for row in cur.fetchall():
                if row["key"] == "value_registry":
                    floor = row["content"]
                elif row["key"] == "behavior":
                    behavior = row["content"]
        _system_cache["text"] = agent.build_system(floor or "", behavior)
        _system_cache["ts"] = now
    except Exception as exc:  # noqa: BLE001
        print(f"[coach] system reload failed (using cached/persona): {exc!r}")
        if _system_cache["text"] is None:
            _system_cache["text"] = agent.build_system("", None)
    return _system_cache["text"]


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = (body.get("message") or "").strip()
    history = body.get("history") or []
    if not message:
        raise HTTPException(400, "empty message")
    if not ZAI_API_KEY:
        raise HTTPException(503, "coach LLM not configured")

    conn = db.get_conn()
    try:
        email = auth.email_from_request(request.headers.get("Authorization"), request.cookies.get(auth.COOKIE_NAME))
        if not email:
            raise HTTPException(401, "not authenticated")
        if not auth.is_allowed(email, conn):
            raise HTTPException(403, "not on the coach allowlist")
    except HTTPException:
        conn.close()
        raise

    embed_fn = request.app.state.embed_fn

    async def gen():
        try:
            async for ev in agent.run_agent(message, history, embed_fn=embed_fn, conn=conn, zai_key=ZAI_API_KEY, system=current_system(conn)):
                # Typed events: answer text → 'delta'; slow-tool phase updates → 'progress'
                # (the widget shows progress as a transient status, not appended to the answer).
                if ev["type"] == "delta":
                    yield f"data: {json.dumps({'delta': ev['text']})}\n\n"
                elif ev["type"] == "progress":
                    yield f"data: {json.dumps({'progress': ev['text']})}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            conn.close()

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── /api/stt-token — mint an STT token for the widget's voice WebSocket ─────
#
# Same fail-closed gate as /api/chat (allowlisted coach_session). Returns a short-lived
# aud="stt" JWT the browser sends as the first frame to shared-svcs STT.

@app.get("/api/stt-token")
async def stt_token(request: Request):
    conn = db.get_conn()
    try:
        email = auth.email_from_request(request.headers.get("Authorization"), request.cookies.get(auth.COOKIE_NAME))
        if not email:
            raise HTTPException(401, "not authenticated")
        if not auth.is_allowed(email, conn):
            raise HTTPException(403, "not on the coach allowlist")
    finally:
        conn.close()
    return {"token": make_stt_token()}


@app.get("/auth")
async def auth_login(token: str = ""):
    """Magic-link login: validate a minted coach_session JWT (allowlisted email), set the
    cookie, redirect to the widget. Interim issuance until OTP/oidc is wired (mint_coach_token.py)."""
    if not token:
        raise HTTPException(400, "missing token")
    email = auth.email_from_request(f"Bearer {token}", None)
    if not email:
        raise HTTPException(401, "invalid token")
    conn = db.get_conn()
    try:
        if not auth.is_allowed(email, conn):
            raise HTTPException(403, "not on the coach allowlist")
    finally:
        conn.close()
    resp = RedirectResponse(url="/coach/", status_code=302)
    resp.set_cookie(auth.COOKIE_NAME, token, max_age=30 * 86400, httponly=True, secure=True, samesite="lax")
    return resp


# ── /coach — the chat widget (built Vite SPA), served as static files ───────
# The multi-stage Docker build drops the compiled widget into COACH_STATIC_DIR. Mounted last
# so it can't shadow the API routes above. Absent in a bare Phase-0 image → simply not mounted.
_STATIC_DIR = os.environ.get("COACH_STATIC_DIR", "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/coach", StaticFiles(directory=_STATIC_DIR, html=True), name="coach-widget")
else:
    print(f"[coach] no widget build at {_STATIC_DIR!r} — /coach not mounted (API-only)")
