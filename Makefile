.PHONY: bootstrap db-up dev dev-api dev-web test test-api test-web lint migrate seed down

COMPOSE := docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.dev.yml

# Migrations run as the owning role, which is exempt from RLS and so can
# backfill across tenants. The API never connects as this one.
MIGRATION_ROLE := keenpay_migration
DB_NAME        := keenpay

bootstrap: migrate seed

dev-monitoring:
	docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.dev.yml -f deploy/compose/docker-compose.monitoring.yml up --build

# Datastores only. Enough to run the integration suite without building the
# application images.
db-up:
	$(COMPOSE) up -d postgres redis

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

# Applied through the running container, so the only local dependency is
# Docker. Ordered by filename and stopped at the first error: a migration that
# half-applied and reported success is worse than one that refused.
migrate:
	@for f in db/migrations/*.sql; do \
		echo "applying $$f"; \
		$(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 \
			-U $(MIGRATION_ROLE) -d $(DB_NAME) < "$$f" || exit 1; \
	done

seed:
	@$(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 \
		-U $(MIGRATION_ROLE) -d $(DB_NAME) < db/seeds/dev_products.sql

down:
	$(COMPOSE) down
