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

import os

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from storage import MEDIA_TYPES, figure_path, safe_name, write_figure

UPLOAD_SECRET = os.environ.get("COACH_UPLOAD_SECRET", "")

app = FastAPI(title="coach", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "coach"}


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
