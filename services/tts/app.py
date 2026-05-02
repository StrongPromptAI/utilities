"""
TTS service — Kokoro-82M ONNX text-to-speech.

Shared service for the shared-svcs Railway project.
Source: utilities/services/tts/

Endpoints:
  GET  /health             — 200 ready, 503 loading, 500 error (always unauthenticated)
  POST /v1/audio/speech    — OpenAI-compatible: JSON body → binary audio (wav or pcm s16le 24kHz mono)

Auth: JWT HS256 Bearer in Authorization header. Required claims: exp, iss, aud="tts".
Enforced only in prod/staging (ENVIRONMENT or RAILWAY_ENVIRONMENT set). Dev mode
accepts requests without an Authorization header — no local token setup required.

License: Kokoro-82M is Apache 2.0 (hexgrad/Kokoro-82M).
"""

import asyncio
import io
import os
import sys
import wave
from pathlib import Path
from typing import Optional

import jwt as _jwt
import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

app = FastAPI(title="tts")

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/app/models/kokoro"))
KOKORO_MODEL_PATH = MODELS_DIR / "kokoro-v1.0.onnx"
KOKORO_VOICES_PATH = MODELS_DIR / "voices-v1.0.bin"
KOKORO_SAMPLE_RATE = 24000  # Kokoro's native output rate

ENVIRONMENT = (
    os.environ.get("ENVIRONMENT")
    or os.environ.get("RAILWAY_ENVIRONMENT")
    or "development"
)
_IS_PROD = ENVIRONMENT in ("production", "staging")

if _IS_PROD:
    JWT_SECRET = os.environ["JWT_SECRET"]
    JWT_SECRET_PREV = os.environ.get("JWT_SECRET_PREV")
else:
    JWT_SECRET = os.environ.get("JWT_SECRET", "localdev")
    JWT_SECRET_PREV = None

_JWT_OPTIONS = {"require": ["exp", "iss", "aud"]}

VOICE_ALLOWLIST = set(
    v.strip() for v in os.environ.get("TTS_VOICE_ALLOWLIST", "af_heart").split(",") if v.strip()
)
DEFAULT_VOICE = os.environ.get("TTS_DEFAULT_VOICE", "af_heart")
MAX_INPUT_CHARS = int(os.environ.get("TTS_MAX_INPUT_CHARS", "800"))
MIN_SPEED = 0.5
MAX_SPEED = 2.0

_ready: bool = False
_load_error: Optional[str] = None
_kokoro = None


def _log(msg: str) -> None:
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def _validate_token(token: str) -> dict:
    errors = []
    for secret in filter(None, [JWT_SECRET, JWT_SECRET_PREV]):
        try:
            return _jwt.decode(token, secret, algorithms=["HS256"], audience="tts", options=_JWT_OPTIONS)
        except _jwt.PyJWTError as e:
            errors.append(e)
    raise errors[0]


def require_tts_token(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    if not _IS_PROD:
        return None
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        return _validate_token(authorization[7:])
    except _jwt.PyJWTError:
        raise HTTPException(401, "invalid token")


def _patch_kokoro_speed_dtype() -> None:
    """Workaround upstream kokoro-onnx bug: in the "input_ids" branch of
    `_create_audio`, the speed array is hardcoded to np.int32, but the v1.0 ONNX
    model declares speed as tensor(float). Without this patch, every synthesis
    raises `INVALID_ARGUMENT: Unexpected input data type. Actual: tensor(int32),
    expected: tensor(float)`. Tracked upstream as kokoro-onnx Issue #155.
    """
    import kokoro_onnx as _kk

    original = _kk.Kokoro._create_audio

    def patched(self, phonemes: str, voice, speed: float):
        import numpy as _np
        import time as _time

        if len(phonemes) > _kk.MAX_PHONEME_LENGTH:
            phonemes = phonemes[: _kk.MAX_PHONEME_LENGTH]
        start_t = _time.time()
        tokens = _np.array(self.tokenizer.tokenize(phonemes), dtype=_np.int64)
        voice = voice[len(tokens)]
        tokens = [[0, *tokens, 0]]
        input_names = [i.name for i in self.sess.get_inputs()]
        if "input_ids" in input_names:
            inputs = {
                "input_ids": tokens,
                "style": _np.array(voice, dtype=_np.float32),
                "speed": _np.array([speed], dtype=_np.float32),
            }
        else:
            inputs = {
                "tokens": tokens,
                "style": voice,
                "speed": _np.ones(1, dtype=_np.float32) * speed,
            }
        audio = self.sess.run(None, inputs)[0]
        return audio, _kk.SAMPLE_RATE

    _kk.Kokoro._create_audio = patched


def _load_kokoro() -> None:
    global _ready, _kokoro, _load_error
    try:
        _patch_kokoro_speed_dtype()
        from kokoro_onnx import Kokoro

        if not (KOKORO_MODEL_PATH.exists() and KOKORO_VOICES_PATH.exists()):
            if _IS_PROD:
                raise RuntimeError(
                    f"Baked Kokoro model missing in prod: expected {KOKORO_MODEL_PATH} "
                    f"and {KOKORO_VOICES_PATH}. Fix the Dockerfile bake step — do not "
                    "fall back to network download."
                )
            import urllib.request
            _log("[tts] Dev mode: baked model not found, downloading from GitHub release...")
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
            for name, dest in [
                ("kokoro-v1.0.onnx", KOKORO_MODEL_PATH),
                ("voices-v1.0.bin", KOKORO_VOICES_PATH),
            ]:
                if not dest.exists():
                    _log(f"[tts]   fetching {name}...")
                    urllib.request.urlretrieve(f"{base}/{name}", dest)

        _log(f"[tts] Loading Kokoro from {MODELS_DIR}...")
        _kokoro = Kokoro(str(KOKORO_MODEL_PATH), str(KOKORO_VOICES_PATH))
        _ready = True
        _log("[tts] Ready")

    except Exception as exc:
        _load_error = str(exc)
        _log(f"[tts] Load failed: {exc}")


@app.on_event("startup")
async def startup() -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _load_kokoro)


@app.get("/health")
def health() -> Response:
    if _load_error:
        return Response(content=_load_error, status_code=500, media_type="text/plain")
    if not _ready:
        return Response(content="loading", status_code=503, media_type="text/plain")
    body = (
        '{"status":"ok","model":"kokoro-82m-v1.0","voices_allowed":'
        + _json_array(sorted(VOICE_ALLOWLIST))
        + ',"sample_rate":'
        + str(KOKORO_SAMPLE_RATE)
        + ',"license":"Apache-2.0","attribution":"hexgrad/Kokoro-82M"}'
    )
    return Response(content=body, status_code=200, media_type="application/json")


def _json_array(items: list[str]) -> str:
    return "[" + ",".join('"' + i.replace('"', '\\"') + '"' for i in items) + "]"


class SpeechRequest(BaseModel):
    """OpenAI-compatible TTS request shape.

    `model` is accepted but ignored (single-model deployment, kept for OpenAI compat).
    `response_format` supports "wav" and "pcm". "pcm" returns raw 16-bit signed
    little-endian mono samples at 24 kHz (Kokoro's native rate).
    """

    input: str = Field(..., min_length=1)
    voice: str = DEFAULT_VOICE
    response_format: str = "wav"
    speed: float = 1.0
    model: Optional[str] = None
    language: str = "en-us"


def _samples_to_pcm_s16le(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _synthesize_blocking(text: str, voice: str, speed: float, language: str) -> tuple[np.ndarray, int]:
    samples, sample_rate = _kokoro.create(text, voice=voice, speed=speed, lang=language)
    return samples, sample_rate


@app.post("/v1/audio/speech")
async def speech(req: SpeechRequest, _: Optional[dict] = Depends(require_tts_token)):
    if not _ready:
        raise HTTPException(503, "tts model loading")

    text = req.input.strip()
    if not text:
        raise HTTPException(400, "input is empty")
    if len(text) > MAX_INPUT_CHARS:
        raise HTTPException(413, f"input exceeds {MAX_INPUT_CHARS} chars")

    if req.voice not in VOICE_ALLOWLIST:
        raise HTTPException(
            400,
            f"voice '{req.voice}' not in allowlist: {sorted(VOICE_ALLOWLIST)}",
        )

    if not (MIN_SPEED <= req.speed <= MAX_SPEED):
        raise HTTPException(400, f"speed must be between {MIN_SPEED} and {MAX_SPEED}")

    fmt = req.response_format.lower()
    if fmt not in ("wav", "pcm"):
        raise HTTPException(400, "response_format must be 'wav' or 'pcm'")

    loop = asyncio.get_event_loop()
    try:
        samples, sample_rate = await loop.run_in_executor(
            None, _synthesize_blocking, text, req.voice, req.speed, req.language
        )
    except Exception as exc:
        _log(f"[tts] synth error ({type(exc).__name__}): {exc}")
        raise HTTPException(500, f"synthesis failed: {exc}")

    pcm = _samples_to_pcm_s16le(samples)
    if fmt == "pcm":
        return Response(
            content=pcm,
            media_type="audio/L16; rate=" + str(sample_rate) + "; channels=1",
        )
    return Response(content=_pcm_to_wav(pcm, sample_rate), media_type="audio/wav")
