# Embed service — nomic-embed-text-v1.5 ONNX embeddings
# Model weights baked into image at build time
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Pre-cache model weights + tokenizer during build
RUN python -c "\
from huggingface_hub import hf_hub_download; \
from transformers import AutoTokenizer; \
hf_hub_download('nomic-ai/nomic-embed-text-v1.5', 'onnx/model.onnx'); \
AutoTokenizer.from_pretrained('nomic-ai/nomic-embed-text-v1.5', trust_remote_code=True); \
print('Model cached')"

COPY nomic_embed.py .
COPY app.py .

ENV PORT=8080
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port $PORT"]
