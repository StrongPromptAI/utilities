# ── Stage 1: build the chat widget (Vite/React/Tailwind) ───────────────────
FROM node:20-slim AS web
WORKDIR /web
# Install deps first (cache layer). The frontend's local .npmrc opts back into install
# scripts (ignore-scripts=false) — Vite/esbuild need them.
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build   # → /web/dist (base '/coach/')

# ── Stage 2: runtime (FastAPI) ─────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.111.0" \
    "uvicorn[standard]>=0.30.0" \
    "PyJWT>=2.8.0" \
    "python-multipart>=0.0.9" \
    "psycopg[binary]>=3.2" \
    "httpx>=0.27"

# Runtime modules only. build_brain.py / ingest_podcast.py / mint_coach_token.py are
# OPERATOR scripts (reuse kb_core, not in the image) — run locally, not in the container.
COPY app.py storage.py agent.py tools.py db.py embed.py auth.py meeting_prep.py review_cache.py ./

# The built widget — served by app.py at /coach (StaticFiles). COACH_STATIC_DIR=static.
COPY --from=web /web/dist ./static

# No VOLUME instruction — Railway bans it. /data/figures is attached via Railway service
# settings; COACH_FIGURES_ROOT defaults to it.
# --proxy-headers + --forwarded-allow-ips '*': the service sits behind Railway's TLS edge,
# so without these url_for()/redirects emit http:// → mixed-content-blocked (resolved decision #2).
ENV PORT=8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*'"]
