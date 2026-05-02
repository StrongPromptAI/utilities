# TTS service — Kokoro-82M ONNX text-to-speech
# Model weights baked to explicit path at build time.
FROM python:3.12-slim

WORKDIR /app

# espeak-ng is the phonemizer backend used by misaki / kokoro-onnx for English.
# libsndfile1 is required by soundfile (transitive dep). curl bakes the model.
RUN apt-get update \
    && apt-get install -y --no-install-recommends espeak-ng libsndfile1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port $PORT"]
