# syntax=docker/dockerfile:1.9
FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files and stdout flushing buffer issues
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# Install OS-level dependencies for PDF processing (pypdf)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Create a dedicated non-root application user and group.
RUN groupadd --system appuser \
    && useradd --system --gid appuser --no-create-home --shell /usr/sbin/nologin appuser

# Create runtime directories (used even with local storage) and grant the
# non-root user exclusive ownership.  Only the application's own data
# directories are made writable; the rest of the filesystem stays
# root-owned and non-writable.
RUN mkdir -p /app/data /app/uploads /app/company_evidence /app/exports /app/data/indexes \
    && chown -R appuser:appuser /app/data /app/uploads /app/company_evidence /app/exports

EXPOSE 8000

# The application reads all configuration from environment variables.
# Secrets (DATABASE_URL, AUTH_SECRET, STRIPE_*, S3_*) must be provided
# at deployment time via the runtime environment.
#
# The container runs as the unprivileged `appuser` (UID 1000).  The
# application process is never root; temporary files created by the S3
# storage adapter land in /tmp, which is world-writable by default.
USER appuser:appuser

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]