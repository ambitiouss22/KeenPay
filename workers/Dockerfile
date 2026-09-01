FROM python:3.12-slim

WORKDIR /app

# Build context must be repo root (see deploy/compose/docker-compose.yml).
COPY api/pyproject.toml api/
RUN pip install --no-cache-dir "./api[dev]" || pip install --no-cache-dir ./api

COPY api/ api/
COPY workers/ workers/

WORKDIR /app/workers
ENV PYTHONPATH=/app/api

CMD ["python", "main.py"]
