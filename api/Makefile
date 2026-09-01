.PHONY: help dev test lint type clean

help:
@echo "KeenPay API — development targets"
@echo ""
@echo "  make dev        Run API locally (uvicorn hot reload)"
@echo "  make test       Run pytest with coverage"
@echo "  make lint       Run ruff check (non-fix mode)"
@echo "  make lint-fix   Run ruff --fix + ruff format"
@echo "  make type       Run type checking (pyright/mypy — stub for now)"
@echo "  make clean      Remove .pytest_cache, __pycache__, .coverage"

dev:
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

test:
pytest tests/ -v --cov=. --cov-report=term-missing --cov-fail-under=0

lint:
ruff check .

lint-fix:
ruff check . --fix
ruff format .

type:
@echo "Type checking not yet configured (pyright/mypy TBD)"

clean:
rm -rf .pytest_cache __pycache__ .coverage .ruff_cache
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
