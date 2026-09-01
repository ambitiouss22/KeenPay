# KeenPay Infrastructure

Cloud-agnostic infrastructure for KeenPay. No AWS/GCP/Azure IaC — deploy via Docker Compose locally, platform env vars in production (Railway, Fly, Vercel).

## Layout

```
infra/
├── monitoring/
│   ├── prometheus/          # scrape config + alert rules
│   └── grafana/             # dashboards + datasource provisioning
├── postgres/
│   └── init.sql             # extensions, readonly role
├── redis/
│   └── redis.conf           # persistence + memory policy
└── backup/
    ├── backup_postgres.sh   # pg_dump with 7-day retention
    └── restore_postgres.sh
```

## Stacks

```bash
# Base: api + web + worker + postgres + redis
make dev

# + Prometheus + Grafana + exporters
docker compose \
  -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.dev.yml \
  -f deploy/compose/docker-compose.monitoring.yml \
  up
```

| Service | Port | Purpose |
|---------|------|---------|
| API | 8000 | FastAPI |
| Web | 3000 | Next.js |
| Prometheus | 9090 | Metrics |
| Grafana | 3001 | Dashboards (admin/admin) |
| Postgres | 5432 | Primary DB |
| Redis | 6379 | Cache, holds, rate limits |

## Health probes

| Endpoint | Use |
|----------|-----|
| `GET /api/v1/health` | Full component status |
| `GET /api/v1/health/live` | Liveness (process up) |
| `GET /api/v1/health/ready` | Readiness (DB + Redis) |
| `GET /metrics` | Prometheus scrape |

## Environment configs

Templates in `deploy/environments/`:

- `development.env` — mocks, dev routes, long JWT
- `staging.env` — Razorpay test, no dev routes
- `production.env` — short JWT, strict rate limits

Merge with `.env.example` — never commit real secrets.

## Backups

```bash
DATABASE_URL=postgresql://... ./infra/backup/backup_postgres.sh
./infra/backup/restore_postgres.sh backups/keenpay_YYYYMMDD.dump
```

## CI/CD

| Workflow | Trigger |
|----------|---------|
| `api-ci.yml` | api/** changes |
| `web-ci.yml` | frontend/** changes |
| `integration.yml` | main PRs |
| `security.yml` | bandit, pip-audit, secret scan |

## Production checklist

- [ ] Rotate `JWT_SECRET` (32+ random bytes)
- [ ] Set `APP_ENV=production`, `ENABLE_DEV_ROUTES=false`
- [ ] Apply `docs/SCHEMA.sql`
- [ ] Configure Razorpay live keys via platform secrets
- [ ] Enable HTTPS termination (nginx or platform)
- [ ] Schedule `backup_postgres.sh` daily
- [ ] Wire Prometheus alerts to on-call (PagerDuty/Slack webhook)
