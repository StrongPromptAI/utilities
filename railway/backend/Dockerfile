FROM python:3.12-slim
WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files + local packages (referenced in pyproject.toml)
COPY pyproject.toml uv.lock* ./
COPY packages/ ./packages/
RUN uv sync --no-dev --no-install-project

# Copy source (dashboard API + kb_core library)
COPY scripts/ ./scripts/
COPY dashboard/ ./dashboard/

CMD ["sh", "-c", "uv run uvicorn dashboard.api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
