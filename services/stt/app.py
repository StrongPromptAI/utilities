"""
STT service — sherpa-onnx streaming Zipformer speech-to-text.

Shared service for the shared-svcs Railway project.
Source: utilities/services/stt/

Endpoints:
  GET  /health       — 200 ready, 503 loading, 500 error
  WS   /transcribe   — first frame = JWT (aud=stt); then binary PCM int16 mono 16kHz → JSON transcripts

Auth: JWT HS256, required claims: exp, iss, aud="stt". Token sent as first WS text frame (never in URL).
"""

import asyncio
import faulthandler
import os
import re
import sys
import time
from pathlib import Path

import jwt as _jwt
import json as _json

import numpy as np
from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect

faulthandler.enable()

app = FastAPI(title="stt")

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/app/models"))
ORT_THREADS = int(os.environ.get("ORT_THREADS", "2"))
STT_SAMPLE_RATE = 16000

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_SECRET_PREV = os.environ.get("JWT_SECRET_PREV")
_JWT_OPTIONS = {"require": ["exp", "iss", "aud"]}

os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hf_cache"))

_stt_ready: bool = False
_load_error: str | None = None
_stt = None


def _log(msg: str) -> None:
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def _validate_token(token: str) -> dict:
    """Validate JWT with aud=stt. Returns decoded payload. Raises _jwt.PyJWTError on failure."""
    errors = []
    for secret in filter(None, [JWT_SECRET, JWT_SECRET_PREV]):
        try:
            return _jwt.decode(token, secret, algorithms=["HS256"], audience="stt", options=_JWT_OPTIONS)
        except _jwt.PyJWTError as e:
            errors.append(e)
    raise errors[0]


def _load_stt() -> None:
    global _stt_ready, _stt, _load_error

    try:
        import sherpa_onnx
        from huggingface_hub import snapshot_download

        MODELS_DIR.mkdir(parents=True, exist_ok=True)

        stt_dir = MODELS_DIR / "streaming-zipformer-en-2023-06-21"

        if not stt_dir.exists() or not (stt_dir / "encoder-epoch-99-avg-1.int8.onnx").exists():
            _log("[stt] Downloading streaming Zipformer model...")
            snapshot_download(
                repo_id="csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-21",
                local_dir=str(stt_dir),
                allow_patterns=[
                    "encoder-epoch-99-avg-1.int8.onnx",
                    "decoder-epoch-99-avg-1.int8.onnx",
                    "joiner-epoch-99-avg-1.int8.onnx",
                    "tokens.txt",
                ],
                local_dir_use_symlinks=False,
            )

        _log("[stt] Loading sherpa-onnx OnlineRecognizer...")
        _stt = sherpa_onnx.OnlineRecognizer.from_transducer(
            encoder=str(stt_dir / "encoder-epoch-99-avg-1.int8.onnx"),
            decoder=str(stt_dir / "decoder-epoch-99-avg-1.int8.onnx"),
            joiner=str(stt_dir / "joiner-epoch-99-avg-1.int8.onnx"),
            tokens=str(stt_dir / "tokens.txt"),
            num_threads=ORT_THREADS,
            sample_rate=STT_SAMPLE_RATE,
            feature_dim=80,
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=1.5,
            rule2_min_trailing_silence=0.4,
            rule3_min_utterance_length=300,
            decoding_method="greedy_search",
        )
        _stt_ready = True
        _log("[stt] Ready")

    except Exception as exc:
        _load_error = str(exc)
        _log(f"[stt] Load failed: {exc}")


@app.on_event("startup")
async def startup() -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _load_stt)


@app.get("/health")
def health() -> Response:
    if _load_error:
        return Response(content=_load_error, status_code=500, media_type="text/plain")
    if not _stt_ready:
        return Response(content="loading", status_code=503, media_type="text/plain")
    return Response(
        content='{"status":"ok","model":"stt"}',
        status_code=200,
        media_type="application/json",
    )


def _normalize_transcript(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:].lower() if len(text) > 1 else text.upper()
    text = re.sub(r'([.!?]\s+)([a-z])', lambda m: m.group(1) + m.group(2).upper(), text)
    return text


@app.websocket("/transcribe")
async def transcribe(ws: WebSocket) -> None:
    """Streaming speech-to-text.

    Auth: first text frame must be a valid JWT (aud=stt). Server closes with 4401 on failure.
    Keepalive: client sends text "ping" every 30s; server discards silently.
    Termination: server closes with code 1001 at token expiry; client should reconnect.

    Protocol (after auth):
      Client sends: binary frames of PCM int16 mono 16kHz audio
      Client sends: text "EOS" to signal end of stream
      Server sends: JSON {"text": str, "is_final": bool, "segment": int, "time_ms": float}
    """
    if not _stt_ready:
        await ws.close(code=1013, reason="STT model loading")
        return

    await ws.accept()

    # First frame must be the JWT — token never goes in the URL
    try:
        token = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
        payload = _validate_token(token)
    except asyncio.TimeoutError:
        await ws.close(code=4401, reason="auth timeout")
        return
    except _jwt.PyJWTError:
        await ws.close(code=4401, reason="invalid token")
        return

    # Schedule forced close at token expiry so long-lived connections can't outlive the token
    exp = payload["exp"]
    max_age = max(0, exp - int(time.time()))
    loop = asyncio.get_event_loop()

    async def _expire():
        await asyncio.sleep(max_age)
        try:
            await ws.close(code=1001, reason="token expired")
        except Exception:
            pass

    expiry_task = loop.create_task(_expire())

    stream = _stt.create_stream()
    segment = 0
    t0 = time.perf_counter()
    last_text = ""

    try:
        while True:
            msg = await ws.receive()

            if msg["type"] == "websocket.disconnect":
                break

            if "text" in msg:
                if msg["text"] == "ping":
                    continue
                if msg["text"] == "EOS":
                    tail_padding = np.zeros(int(STT_SAMPLE_RATE * 0.5), dtype=np.float32)
                    stream.accept_waveform(STT_SAMPLE_RATE, tail_padding)
                    while _stt.is_ready(stream):
                        _stt.decode_stream(stream)
                    text = _normalize_transcript(_stt.get_result(stream))
                    if text:
                        elapsed = (time.perf_counter() - t0) * 1000
                        await ws.send_text(_json.dumps({
                            "text": text, "is_final": True,
                            "segment": segment, "time_ms": round(elapsed, 1),
                        }))
                    break
                continue

            if "bytes" not in msg:
                continue

            audio_bytes = msg["bytes"]
            if not audio_bytes:
                continue

            samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            stream.accept_waveform(STT_SAMPLE_RATE, samples)

            while _stt.is_ready(stream):
                _stt.decode_stream(stream)

            text = _normalize_transcript(_stt.get_result(stream))

            if text and text != last_text:
                elapsed = (time.perf_counter() - t0) * 1000
                await ws.send_text(_json.dumps({
                    "text": text, "is_final": False,
                    "segment": segment, "time_ms": round(elapsed, 1),
                }))
                last_text = text

            if _stt.is_endpoint(stream):
                text = _normalize_transcript(_stt.get_result(stream))
                if text:
                    elapsed = (time.perf_counter() - t0) * 1000
                    await ws.send_text(_json.dumps({
                        "text": text, "is_final": True,
                        "segment": segment, "time_ms": round(elapsed, 1),
                    }))
                    segment += 1
                    last_text = ""
                _stt.reset(stream)
                t0 = time.perf_counter()

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        _log(f"[stt] Transcribe error ({type(exc).__name__}): {exc}")
    finally:
        expiry_task.cancel()
        del stream
