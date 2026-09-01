FROM python:3.12-slim

WORKDIR /app

# Build context: repo root
COPY api/pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]" || pip install -e .

COPY api/ .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
