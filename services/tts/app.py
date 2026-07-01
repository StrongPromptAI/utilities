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
import collections
import importlib.metadata
import io
import json
import os
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Optional

import jwt as _jwt
import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

app = FastAPI(title="tts")

ENVIRONMENT = (
    os.environ.get("ENVIRONMENT")
    or os.environ.get("RAILWAY_ENVIRONMENT")
    or "development"
)
_IS_PROD = ENVIRONMENT in ("production", "staging")

# Model cache location. Prod bakes the model into the image at /app/models/kokoro. In dev there is
# no /app (and it's read-only on a Mac), so default to a writable user-cache dir the app can download
# the model INTO — otherwise a fresh dev boot dies with `Errno 30: Read-only file system: '/app'`.
# This is what makes local synth work out of the box (the doc-to-audio default). MODELS_DIR env
# overrides either way.
_DEFAULT_MODELS_DIR = "/app/models/kokoro" if _IS_PROD else str(Path.home() / ".cache" / "kokoro")
MODELS_DIR = Path(os.environ.get("MODELS_DIR", _DEFAULT_MODELS_DIR))
KOKORO_MODEL_PATH = MODELS_DIR / "kokoro-v1.0.onnx"
KOKORO_VOICES_PATH = MODELS_DIR / "voices-v1.0.bin"
KOKORO_SAMPLE_RATE = 24000  # Kokoro's native output rate

if _IS_PROD:
    JWT_SECRET = os.environ["JWT_SECRET"]
    JWT_SECRET_PREV = os.environ.get("JWT_SECRET_PREV")
else:
    JWT_SECRET = os.environ.get("JWT_SECRET", "localdev")
    JWT_SECRET_PREV = None

_JWT_OPTIONS = {"require": ["exp", "iss", "aud"]}

# Voice allowlist. Prod pins its own via TTS_VOICE_ALLOWLIST env. Dev defaults to the doc-to-audio
# podcast roster (the two-voice defaults af_nova + am_liam and their auditioned backups) so local
# synth of a two-voice episode works without setting the env first. Prod's env override is unaffected.
_DEFAULT_ALLOWLIST = (
    "af_heart" if _IS_PROD else "af_heart,af_nova,af_sarah,am_liam,am_eric,am_adam"
)
VOICE_ALLOWLIST = set(
    v.strip() for v in os.environ.get("TTS_VOICE_ALLOWLIST", _DEFAULT_ALLOWLIST).split(",") if v.strip()
)
DEFAULT_VOICE = os.environ.get("TTS_DEFAULT_VOICE", "af_heart" if _IS_PROD else "af_nova")
MAX_INPUT_CHARS = int(os.environ.get("TTS_MAX_INPUT_CHARS", "800"))
MAX_CONCURRENCY = int(os.environ.get("TTS_MAX_CONCURRENCY", "4"))

# onnxruntime intra-op thread cap. Without it, ORT sizes its intra-op pool to the
# CPU grant (cpu=32 on Railway → ~32 threads, each with a memory arena), so a SINGLE
# synth peaked at 3.5–5 GB and OOM-killed the container. The pip onnxruntime wheel is
# not OpenMP-built, so OMP_NUM_THREADS is ignored — SessionOptions.intra_op_num_threads
# is the only reliable knob. Capped here and threaded into the InferenceSession we
# build ourselves (see _load_kokoro). inter_op is pinned to 1 (single graph, serial).
INTRA_OP_THREADS = int(os.environ.get("TTS_INTRA_OP_THREADS", "4"))
MIN_SPEED = 0.5
MAX_SPEED = 2.0

_ready: bool = False
_load_error: Optional[str] = None
_kokoro = None
_kokoro_pkg_version = "unknown"

# Concurrency limiter: Kokoro inference is single-threaded per call; queueing
# beyond a handful of concurrent requests degrades latency for everyone.
_synth_semaphore: Optional[asyncio.Semaphore] = None
_in_flight: int = 0

# Serializes phonemization across concurrent synth threads. Kokoro phonemizes via
# espeak-ng (a C library, reached through `phonemizer`) that holds PROCESS-GLOBAL
# state — concurrent phonemize() calls interleave inside that state and corrupt each
# other's output, so two requests can come back with scrambled/cross-contaminated
# phonemes (audible as garbled, overlapping speech) or trip a shape-mismatch in
# Kokoro's per-batch np.concatenate (a 500). ONNX InferenceSession.run() IS
# thread-safe, so we lock ONLY the espeak step (a single up-front call in
# Kokoro.create) and leave concurrent inference — the expensive, parallel-safe part
# — untouched. Lock-the-resource, not the whole request: makes the race impossible
# for every client (doc_to_speech, gitnexus, …), not just one script.
_phonemize_lock = threading.Lock()

# Ring buffer of recent synthesis end timestamps for short-window load reporting.
_recent_synths: collections.deque = collections.deque(maxlen=512)
_LOAD_WINDOW_SECONDS = 60.0


def _log(msg: str) -> None:
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def _log_event(event: str, **fields) -> None:
    payload = {"event": event, **fields}
    sys.stderr.write(json.dumps(payload, separators=(",", ":")) + "\n")
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
    """Workaround two upstream kokoro-onnx bugs in `_create_audio`, both of which
    only bite on the v1.0 ONNX model + our usage:

    1. SPEED DTYPE: in the "input_ids" branch the speed array is hardcoded to
       np.int32, but the v1.0 model declares speed as tensor(float), so every
       synthesis raises `INVALID_ARGUMENT: Unexpected input data type. Actual:
       tensor(int32), expected: tensor(float)`. (kokoro-onnx Issue #155.)

    2. MULTI-BATCH SHAPE: the model returns audio shaped (1, N). For a chunk whose
       phonemes exceed MAX_PHONEME_LENGTH (510), `create()` splits into several
       batches and `np.concatenate`s the per-batch results along axis 0 — which
       fails ("array dimensions except for the concatenation axis must match") the
       moment two batches differ in length, i.e. almost always. It only "works" for
       single-batch text (one array, nothing to concat) or the rare case of equal
       batch lengths, which is why short chunks never hit it and long ones 500. We
       flatten each batch to 1-D so concatenation is length-agnostic; downstream
       only ever reads `samples.size` / `.tobytes()`, so 1-D is the correct shape.
    """
    import kokoro_onnx as _kk

    def patched(self, phonemes: str, voice, speed: float):
        import numpy as _np

        if len(phonemes) > _kk.MAX_PHONEME_LENGTH:
            phonemes = phonemes[: _kk.MAX_PHONEME_LENGTH]
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
        return _np.asarray(audio).reshape(-1), _kk.SAMPLE_RATE

    _kk.Kokoro._create_audio = patched


def _serialize_phonemize(kokoro) -> None:
    """Wrap the instance's tokenizer.phonemize in `_phonemize_lock` so the espeak-ng
    global state is never touched by two synth threads at once. Kokoro.create() calls
    phonemize exactly once up front, so this serializes only the unsafe step and leaves
    the ONNX inference loop concurrent. See `_phonemize_lock` for why this is required.
    """
    tok = kokoro.tokenizer
    inner = tok.phonemize

    def locked(*args, **kwargs):
        with _phonemize_lock:
            return inner(*args, **kwargs)

    tok.phonemize = locked


def _load_kokoro() -> None:
    global _ready, _kokoro, _load_error, _kokoro_pkg_version
    try:
        _patch_kokoro_speed_dtype()
        from kokoro_onnx import Kokoro

        try:
            _kokoro_pkg_version = importlib.metadata.version("kokoro-onnx")
        except importlib.metadata.PackageNotFoundError:
            _kokoro_pkg_version = "unknown"

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

        _log(
            f"[tts] Loading Kokoro from {MODELS_DIR}... "
            f"(kokoro-onnx={_kokoro_pkg_version}, intra_op_threads={INTRA_OP_THREADS})"
        )
        # Build the ONNX session ourselves with a bounded intra-op thread pool, then
        # hand it to Kokoro.from_session — Kokoro(model, voices) builds its own session
        # with no thread control, which is what let ORT spawn ~32 threads and OOM. This
        # is the supported public API (Kokoro.from_session); no monkeypatch needed for
        # threads (the speed-dtype patch above is a separate, still-required fix).
        import onnxruntime as _ort

        so = _ort.SessionOptions()
        so.intra_op_num_threads = INTRA_OP_THREADS
        so.inter_op_num_threads = 1
        sess = _ort.InferenceSession(
            str(KOKORO_MODEL_PATH), sess_options=so, providers=["CPUExecutionProvider"]
        )
        _kokoro = Kokoro.from_session(sess, str(KOKORO_VOICES_PATH))
        _serialize_phonemize(_kokoro)
        _ready = True
        _log("[tts] Ready")

    except Exception as exc:
        _load_error = str(exc)
        _log(f"[tts] Load failed: {exc}")


@app.on_event("startup")
async def startup() -> None:
    global _synth_semaphore
    _synth_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _load_kokoro)


def _recent_synth_count(now: float) -> int:
    cutoff = now - _LOAD_WINDOW_SECONDS
    while _recent_synths and _recent_synths[0] < cutoff:
        _recent_synths.popleft()
    return len(_recent_synths)


@app.get("/health")
def health() -> Response:
    if _load_error:
        return Response(content=_load_error, status_code=500, media_type="text/plain")
    if not _ready:
        return Response(content="loading", status_code=503, media_type="text/plain")
    now = time.time()
    body = {
        "status": "ok",
        "model": "kokoro-82m-v1.0",
        "version": _kokoro_pkg_version,
        "voices_allowed": sorted(VOICE_ALLOWLIST),
        "sample_rate": KOKORO_SAMPLE_RATE,
        "load": {
            "in_flight": _in_flight,
            "synths_last_60s": _recent_synth_count(now),
            "max_concurrency": MAX_CONCURRENCY,
        },
        "rate_limit_remaining": MAX_CONCURRENCY - _in_flight,
        "license": "Apache-2.0",
        "attribution": "hexgrad/Kokoro-82M",
    }
    return Response(
        content=json.dumps(body, separators=(",", ":")),
        status_code=200,
        media_type="application/json",
    )


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
    global _in_flight

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

    assert _synth_semaphore is not None
    async with _synth_semaphore:
        _in_flight += 1
        t_start = time.perf_counter()
        loop = asyncio.get_event_loop()
        try:
            samples, sample_rate = await loop.run_in_executor(
                None, _synthesize_blocking, text, req.voice, req.speed, req.language
            )
        except Exception as exc:
            _log_event("synth_error", error_type=type(exc).__name__, error=str(exc))
            _in_flight -= 1
            raise HTTPException(500, f"synthesis failed: {exc}")

        synth_ms = (time.perf_counter() - t_start) * 1000.0
        # Kokoro returns shape (1, N); samples.size gives the true frame count.
        audio_seconds = samples.size / sample_rate
        rtf = (synth_ms / 1000.0) / audio_seconds if audio_seconds > 0 else 0.0
        _recent_synths.append(time.time())
        _in_flight -= 1

    _log_event(
        "synth",
        voice=req.voice,
        chars=len(text),
        synth_ms=round(synth_ms, 1),
        audio_seconds=round(audio_seconds, 3),
        rtf=round(rtf, 3),
        format=fmt,
    )

    pcm = _samples_to_pcm_s16le(samples)
    if fmt == "pcm":
        return Response(
            content=pcm,
            media_type="audio/L16; rate=" + str(sample_rate) + "; channels=1",
        )
    return Response(content=_pcm_to_wav(pcm, sample_rate), media_type="audio/wav")
