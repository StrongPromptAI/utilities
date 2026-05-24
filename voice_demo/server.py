"""Local shared-svcs voice demo harness.

Serves a small static test page on an assigned test port and provides same-origin
helpers for browser calls that would otherwise hit CORS on local shared services.
"""

from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parent
TTS_SPEECH_URL = "http://127.0.0.1:8102/v1/audio/speech"
TTS_HEALTH_URL = "http://127.0.0.1:8102/health"
STT_HEALTH_URL = "http://127.0.0.1:8101/health"

app = FastAPI(title="shared-svcs-voice-demo")


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "shared-svcs-voice-demo"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "index.html", media_type="text/html")


@app.get("/styles.css")
def styles() -> FileResponse:
    return FileResponse(ROOT / "styles.css", media_type="text/css")


@app.get("/main.ts")
def main_ts() -> FileResponse:
    return FileResponse(ROOT / "main.ts", media_type="application/javascript")


@app.get("/stt/health")
def stt_health() -> Response:
    try:
        upstream = requests.get(STT_HEALTH_URL, timeout=3)
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail=f"STT unavailable: {exc}") from exc
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "text/plain"),
    )


@app.get("/tts/health")
def tts_health() -> Response:
    try:
        upstream = requests.get(TTS_HEALTH_URL, timeout=3)
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail=f"TTS unavailable: {exc}") from exc
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "text/plain"),
    )


@app.post("/tts/speech")
async def tts_speech(request: Request) -> Response:
    body = await request.body()
    try:
        upstream = requests.post(
            TTS_SPEECH_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            timeout=90,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail=f"TTS unavailable: {exc}") from exc

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
    )
