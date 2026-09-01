.PHONY: bootstrap dev dev-api dev-web test test-api test-web lint migrate seed down

COMPOSE := docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.dev.yml

bootstrap:
	@echo "Applying schema..."
	psql "$(DATABASE_URL)" -f docs/SCHEMA.sql
	@echo "Seeding dev catalog..."
	psql "$(DATABASE_URL)" -f db/seeds/dev_products.sql

dev-monitoring:
	docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.dev.yml -f deploy/compose/docker-compose.monitoring.yml up --build

dev:
	$(COMPOSE) up --build

dev-api:
	cd api && uvicorn main:app --reload --host 0.0.0.0 --port 8000

dev-web:
	cd frontend && npm run dev

dev-worker:
	cd workers && python main.py

test: test-api test-web

test-api:
	cd api && pytest -v

test-api-cov:
	cd api && pytest -v --cov=. --cov-report=term-missing

test-api-security:
	cd api && pytest tests/security -v -m security

test-api-unit:
	cd api && pytest tests/unit -v

test-api-integration:
	cd api && pytest tests/integration -v

test-web:
	cd frontend && npm test

test-e2e:
	cd tests/e2e && npx playwright test

lint:
	cd api && ruff check . && ruff format --check .
	cd frontend && npm run lint

lint-security:
	cd api && bandit -r . -c pyproject.toml

audit-deps:
	cd api && pip-audit

migrate:
	@echo "Run pending SQL migrations from db/migrations/ in order"

seed:
	psql "$(DATABASE_URL)" -f db/seeds/dev_products.sql

down:
	$(COMPOSE) down
