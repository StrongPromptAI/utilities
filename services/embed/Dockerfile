# Embed service — nomic-embed-text-v1.5 ONNX embeddings
# Model weights baked into image (no runtime volume needed)
FROM python:3.12-slim

WORKDIR /app

# Install the shared embed package from packages/
COPY packages/embed/ /tmp/embed-pkg/
RUN pip install /tmp/embed-pkg/ && rm -rf /tmp/embed-pkg/

# Install service dependencies
COPY services/embed/requirements.txt .
RUN pip install -r requirements.txt

# Pre-cache model weights + tokenizer during build
RUN python -c "\
from huggingface_hub import hf_hub_download; \
from transformers import AutoTokenizer; \
hf_hub_download('nomic-ai/nomic-embed-text-v1.5', 'onnx/model.onnx'); \
AutoTokenizer.from_pretrained('nomic-ai/nomic-embed-text-v1.5', trust_remote_code=True); \
print('Model cached')"

COPY services/embed/app.py .

ENV PORT=8080
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port $PORT"]
