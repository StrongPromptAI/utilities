# Embed service — nomic-embed-text-v1.5 ONNX embeddings
# Model weights baked into image at build time
FROM python:3.12-slim

WORKDIR /app

# Pin the HuggingFace cache to a known path so the build-time download
# and the runtime loader both resolve to the same directory.
ENV HF_HOME=/app/.cache/huggingface

COPY requirements.txt .
RUN pip install -r requirements.txt

# Pre-cache model weights + tokenizer during build.
# HF_HOME is already set above, so hf_hub_download writes to
# /app/.cache/huggingface/hub — the same location used at runtime.
RUN python -c "\
import os; \
os.makedirs('/app/.cache/huggingface', exist_ok=True); \
from huggingface_hub import hf_hub_download; \
from transformers import AutoTokenizer; \
path = hf_hub_download('nomic-ai/nomic-embed-text-v1.5', 'onnx/model.onnx'); \
print('ONNX model cached at:', path); \
AutoTokenizer.from_pretrained('nomic-ai/nomic-embed-text-v1.5', trust_remote_code=True); \
print('Tokenizer cached')"

COPY nomic_embed.py .
COPY app.py .

ENV PORT=8080
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port $PORT"]
