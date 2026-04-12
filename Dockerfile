# ==============================================================================
# LogMind — Multi-stage Dockerfile
# ==============================================================================

# ── Base image ───────────────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ── Development stage ────────────────────────────────────
FROM base AS development

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install -e ".[dev]"

# Copy source
COPY src/ ./src/
COPY configs/ ./configs/
COPY migrations/ ./migrations/
COPY alembic.ini ./

ENV PYTHONPATH=/app/src

# ── Production dependencies ──────────────────────────────
FROM base AS deps

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install ".[prod]"

# ── Production image ─────────────────────────────────────
FROM base AS production

# Copy installed packages from deps stage
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy application code
COPY src/ ./src/
COPY configs/ ./configs/
COPY migrations/ ./migrations/
COPY alembic.ini ./

ENV PYTHONPATH=/app/src

# Create non-root user
RUN addgroup --system appgroup && \
    adduser --system --ingroup appgroup appuser
USER appuser

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "logmind.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
