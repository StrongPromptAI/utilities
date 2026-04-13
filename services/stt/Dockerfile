# STT service — sherpa-onnx streaming Zipformer speech-to-text
# Model weights baked into image at build time
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Pre-download model weights during build
RUN python -c "\
from huggingface_hub import snapshot_download; \
snapshot_download( \
    repo_id='csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-21', \
    local_dir='/app/models/streaming-zipformer-en-2023-06-21', \
    allow_patterns=['encoder-epoch-99-avg-1.int8.onnx','decoder-epoch-99-avg-1.int8.onnx','joiner-epoch-99-avg-1.int8.onnx','tokens.txt'], \
    local_dir_use_symlinks=False); \
print('STT model cached')"

COPY app.py .

ENV PORT=8080
ENV MODELS_DIR=/app/models
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port $PORT"]
