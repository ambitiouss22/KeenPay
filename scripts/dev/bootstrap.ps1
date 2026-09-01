$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location $Root

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created .env from .env.example — fill in API keys before running payments."
}

Write-Host "Starting Postgres + Redis..."
docker compose -f deploy/compose/docker-compose.yml up -d postgres redis

Start-Sleep -Seconds 3

$DatabaseUrl = if ($env:DATABASE_URL) { $env:DATABASE_URL } else { "postgresql://keenpay:keenpay@localhost:5432/keenpay" }
psql $DatabaseUrl -f docs/SCHEMA.sql
psql $DatabaseUrl -f db/seeds/dev_products.sql

Write-Host "Bootstrap complete."
