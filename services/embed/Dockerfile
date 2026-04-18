# Embed service — nomic-embed-text-v1.5 ONNX embeddings
# Model weights baked into image at build time
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Pre-cache model weights + tokenizer during build to explicit paths (not HF cache)
# Runtime loads directly from these paths — no HF Hub lookups at startup.
RUN python -c "\
from huggingface_hub import hf_hub_download, snapshot_download; \
hf_hub_download('nomic-ai/nomic-embed-text-v1.5', 'onnx/model.onnx', local_dir='/app/models/nomic'); \
snapshot_download('nomic-ai/nomic-embed-text-v1.5', local_dir='/app/models/tokenizer', allow_patterns=['tokenizer*','vocab.txt','*.json']); \
import os; \
assert os.path.exists('/app/models/nomic/onnx/model.onnx'), 'ONNX model missing'; \
assert os.path.exists('/app/models/tokenizer/tokenizer.json') or os.path.exists('/app/models/tokenizer/vocab.txt'), 'Tokenizer missing'; \
print('Model + tokenizer baked to /app/models')"

COPY nomic_embed.py .
COPY app.py .

ENV PORT=8080
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port $PORT"]
