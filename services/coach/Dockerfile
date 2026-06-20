FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.111.0" \
    "uvicorn[standard]>=0.30.0" \
    "PyJWT>=2.8.0" \
    "python-multipart>=0.0.9"

COPY app.py storage.py ./

# NOTE: no VOLUME instruction — Railway bans it. The /data/figures volume is
# attached via Railway service settings; COACH_FIGURES_ROOT defaults to it.
ENV PORT=8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port $PORT"]
