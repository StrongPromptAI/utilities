# Whisper service — faster-whisper small.en OpenAI-compatible REST transcription
# Model weights baked to explicit path at build time.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake faster-whisper model to an explicit path. Runtime loads from disk only — no HF Hub calls.
# Rationale: runtime hf_hub_download / snapshot_download causes a metadata check against HF Hub.
# On rate-limit or network blip, it falls back to a full re-download concurrent with model loading
# and OOM-kills the container. Same failure mode documented in ~/repo_docs/skills/postgres/embedding.md.
RUN python -c "\
from huggingface_hub import snapshot_download; \
snapshot_download('Systran/faster-whisper-small.en', local_dir='/app/models/whisper'); \
import os; \
assert os.path.exists('/app/models/whisper/model.bin'), 'whisper model.bin missing'; \
print('Whisper model baked to /app/models/whisper')"

COPY app.py .

ENV PORT=8080
ENV MODELS_DIR=/app/models/whisper
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port $PORT"]
