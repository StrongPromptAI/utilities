# TTS service — Kokoro-82M ONNX text-to-speech
# Model weights baked to explicit path at build time.
FROM python:3.12-slim

WORKDIR /app

# espeak-ng is the phonemizer backend used by misaki / kokoro-onnx for English.
# libsndfile1 is required by soundfile (transitive dep). curl bakes the model.
RUN apt-get update \
    && apt-get install -y --no-install-recommends espeak-ng libsndfile1 curl \
    && rm -rf /var/lib/apt/lists/*

# requirements.txt is a pinned + hashed lock (uv pip compile from requirements.in).
# --require-hashes makes the build fail closed: exact versions, integrity-verified,
# no silent forward-resolution into a freshly-poisoned release. See ironworm_andfriends L2.
COPY requirements.txt .
RUN pip install --require-hashes --no-cache-dir -r requirements.txt

# Bake Kokoro-82M ONNX model + voices binary to explicit paths from the
# kokoro-onnx GitHub release (the canonical source per upstream README).
# Runtime loads from disk only — no network calls. Same poka-yoke as
# embed/whisper/stt: runtime downloads fight ORT init for memory and OOM.
RUN mkdir -p /app/models/kokoro \
    && curl -fsSL -o /app/models/kokoro/kokoro-v1.0.onnx \
       https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx \
    && curl -fsSL -o /app/models/kokoro/voices-v1.0.bin \
       https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin \
    && test -s /app/models/kokoro/kokoro-v1.0.onnx \
    && test -s /app/models/kokoro/voices-v1.0.bin \
    && echo "Kokoro model + voices baked to /app/models/kokoro"

COPY app.py .

ENV PORT=8080
ENV MODELS_DIR=/app/models/kokoro
# onnxruntime's intra-op thread pool is the memory driver. Unbounded, ORT sizes it to
# the CPU grant (cpu=32 → ~32 threads, each with a memory arena), so ONE synth peaked
# at 3.5–5 GB and OOM-killed the container. The real cap is TTS_INTRA_OP_THREADS,
# applied in app.py via SessionOptions.intra_op_num_threads on a session we build
# ourselves. NOTE: the pip onnxruntime wheel is NOT OpenMP-built, so OMP_NUM_THREADS
# does NOT cap that pool — relying on it (the prior setup) is what let the pool explode.
# OPENBLAS/MKL pins bound transitive BLAS use only; they don't touch ORT's pool. Request
# concurrency is gated separately at the FastAPI layer (TTS_MAX_CONCURRENCY).
ENV TTS_INTRA_OP_THREADS=4
ENV OPENBLAS_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port $PORT"]
