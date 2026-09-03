# KeenPay API image.
#
# Build context is the REPO ROOT (deploy/compose sets `context: ../..`),
# which is why every COPY below is prefixed with `api/`.
#
# Two stages: dependencies are compiled in the builder and only the
# installed packages are carried into the runtime image, so build tools
# never ship to production.

# --- stage 1: build dependencies ---------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY api/pyproject.toml api/pyproject.toml
COPY api/ api/

RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && cd api \
    && pip install --no-cache-dir -e .

# --- stage 2: runtime --------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

# postgresql-client: lets the container run psql for schema work.
# curl: required by the HEALTHCHECK below.
RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY api/ /app/

# Run as a non-root user. A container that is compromised should not have
# root in its own filesystem.
RUN useradd -m -u 1000 keenpay && chown -R keenpay:keenpay /app
USER keenpay

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health/live || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
