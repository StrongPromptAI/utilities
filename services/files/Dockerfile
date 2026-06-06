FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.111.0" \
    "uvicorn[standard]>=0.29.0" \
    "python-jose[cryptography]>=3.3.0" \
    "boto3>=1.34.0" \
    "httpx>=0.27.0" \
    "python-multipart>=0.0.9" \
    "tzdata"

COPY main.py templates.py ./

ENV PORT=8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $PORT"]
