FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.111.0" \
    "uvicorn[standard]>=0.29.0" \
    "sqlalchemy>=2.0" \
    "starlette-admin>=0.14.0" \
    "PyJWT[crypto]>=2.8.0" \
    "itsdangerous>=2.1.0" \
    "httpx>=0.27.0" \
    "tzdata"

COPY app.py db.py models.py seed.py storage.py feed.py admin.py ./

# NOTE: no VOLUME instruction — Railway bans it. The /data volume is attached
# via Railway service settings; PODCAST_DB_PATH/PODCAST_AUDIO_ROOT default to it.
ENV PORT=8000
# seed is insert-only, so running it on every boot is safe (creates missing
# shows/episodes on the volume without clobbering admin edits).
CMD ["sh", "-c", "python seed.py && uvicorn app:app --host 0.0.0.0 --port $PORT"]
