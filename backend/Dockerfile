# syntax=docker/dockerfile:1.7
# ---------- build stage ----------
FROM python:3.12-slim AS build
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Build deps for some Python wheels (statsmodels needs gfortran headers
# transitively for older scipy, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

# Install Python deps first for layer caching
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev || uv sync --no-install-project --no-dev

# Now the app
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev || uv sync --no-dev

# ---------- runtime stage ----------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Minimal runtime libs (libpq + libgomp for some scientific wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libgomp1 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy in the prebuilt venv + app
COPY --from=build /app /app

# Drop privileges
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# Apply pending DB migrations (alembic upgrade head) before serving, then exec
# uvicorn. Fail-closed: a failed migration aborts startup so the deploy fails
# loudly and the previous healthy version keeps serving — code never runs ahead
# of its schema. See backend/entrypoint.sh.
CMD ["sh", "/app/entrypoint.sh"]
