"""
Whisper service — OpenAI-compatible REST transcription for shared-svcs.

Source: utilities/services/whisper/

Endpoints:
  GET  /health                      — 200 ready, 503 loading, 500 error
  POST /v1/audio/transcriptions     — multipart file → {"text": str, ...}

Auth: JWT HS256, Bearer in Authorization header. Required claims: exp, iss, aud="stt".
      Same audience as streaming STT — one token type for both WS and REST transcription.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

import jwt as _jwt
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Response, UploadFile

app = FastAPI(title="whisper")

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/app/models/whisper"))
OMP_NUM_THREADS = int(os.environ.get("OMP_NUM_THREADS", "2"))

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_SECRET_PREV = os.environ.get("JWT_SECRET_PREV")
_JWT_OPTIONS = {"require": ["exp", "iss", "aud"]}

_ready: bool = False
_load_error: Optional[str] = None
_model = None


def _log(msg: str) -> None:
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def _validate_token(token: str) -> dict:
    errors = []
    for secret in filter(None, [JWT_SECRET, JWT_SECRET_PREV]):
        try:
            return _jwt.decode(token, secret, algorithms=["HS256"], audience="stt", options=_JWT_OPTIONS)
        except _jwt.PyJWTError as e:
            errors.append(e)
    raise errors[0]


def require_stt_token(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        return _validate_token(authorization[7:])
    except _jwt.PyJWTError:
        raise HTTPException(401, "invalid token")


def _load_model() -> None:
    global _ready, _model, _load_error
    try:
        from faster_whisper import WhisperModel
        if not (MODELS_DIR / "model.bin").exists():
            raise RuntimeError(f"whisper model.bin missing at {MODELS_DIR} — image was not built correctly")
        _log(f"[whisper] Loading faster-whisper from {MODELS_DIR}...")
        _model = WhisperModel(str(MODELS_DIR), device="cpu", compute_type="int8", num_workers=1, cpu_threads=OMP_NUM_THREADS)
        _ready = True
        _log("[whisper] Ready")
    except Exception as exc:
        _load_error = str(exc)
        _log(f"[whisper] Load failed: {exc}")


@app.on_event("startup")
async def startup() -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _load_model)


@app.get("/health")
def health() -> Response:
    if _load_error:
        return Response(content=_load_error, status_code=500, media_type="text/plain")
    if not _ready:
        return Response(content="loading", status_code=503, media_type="text/plain")
    return Response(
        content='{"status":"ok","model":"whisper"}',
        status_code=200,
        media_type="application/json",
    )


def _transcribe_blocking(audio_path: str, response_format: str, language: Optional[str]) -> dict:
    segments_iter, info = _model.transcribe(
        audio_path,
        language=language,
        beam_size=1,
        vad_filter=True,
    )
    segments = list(segments_iter)
    text = "".join(s.text for s in segments).strip()

    if response_format == "verbose_json":
        return {
            "text": text,
            "language": info.language,
            "duration": info.duration,
            "segments": [
                {"id": i, "start": s.start, "end": s.end, "text": s.text}
                for i, s in enumerate(segments)
            ],
        }
    if response_format == "text":
        return {"_plain_text": text}
    return {"text": text}


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    response_format: str = Form("json"),
    language: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    _: dict = Depends(require_stt_token),
):
    if not _ready:
        raise HTTPException(503, "whisper model loading")

    suffix = Path(file.filename or "audio").suffix or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, _transcribe_blocking, tmp_path, response_format, language
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if response_format == "text":
        return Response(content=result["_plain_text"], media_type="text/plain")
    return result
