#!/usr/bin/env bash
# Run every CI gate locally, exactly as GitHub runs it.
#
# Run this before every push. If it is green, CI is green — the commands and
# the tool versions are the same ones the workflows use.
#
#   ./scripts/ci/verify-local.sh
#
# Two rules this script exists to enforce:
#
#   1. Never invoke a bare `ruff`. A `ruff` binary earlier on PATH shadows the
#      pip-installed one, so `ruff --version` can disagree with what pip
#      reports. Formatting with one version and gating on another is how the
#      lint job goes red with no code change. `python -m ruff` always resolves
#      to the installed package.
#
#   2. The pinned version lives in api/pyproject.toml and nowhere else. This
#      script reads it from there and refuses to run against anything else,
#      so the pin cannot silently drift out of sync with CI.

set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

RED=$'\033[31m'; GREEN=$'\033[32m'; YEL=$'\033[33m'; DIM=$'\033[2m'; OFF=$'\033[0m'
FAILED=()
PY="${PYTHON:-python3}"

step() { printf '\n%s=== %s ===%s\n' "$YEL" "$1" "$OFF"; }
ok()   { printf '%s  PASS%s  %s\n' "$GREEN" "$OFF" "$1"; }
bad()  { printf '%s  FAIL%s  %s\n' "$RED" "$OFF" "$1"; FAILED+=("$1"); }

# --- ruff version gate ------------------------------------------------------
step "ruff version"

PINNED=$(grep -oE '"ruff==[0-9]+\.[0-9]+\.[0-9]+"' api/pyproject.toml | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
if [ -z "$PINNED" ]; then
    bad "could not read the pinned ruff version from api/pyproject.toml"
    printf '%sExpected a line like:  "ruff==0.16.5",%s\n' "$DIM" "$OFF"
    exit 1
fi

INSTALLED=$($PY -m ruff --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)

if [ "$INSTALLED" != "$PINNED" ]; then
    printf 'pinned=%s installed=%s — installing the pinned version\n' "$PINNED" "${INSTALLED:-none}"
    $PY -m pip install -q "ruff==$PINNED" || { bad "could not install ruff==$PINNED"; exit 1; }
    INSTALLED=$($PY -m ruff --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
fi

if [ "$INSTALLED" != "$PINNED" ]; then
    bad "ruff is $INSTALLED but CI uses $PINNED"
    exit 1
fi
ok "ruff $INSTALLED matches the pin in api/pyproject.toml"

# A bare `ruff` on PATH that disagrees is not fatal here, because everything
# below goes through `python -m ruff`. It is worth knowing about, though: it is
# what makes a manual `ruff check` in a terminal lie to you.
if command -v ruff >/dev/null 2>&1; then
    BARE=$(ruff --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    if [ "$BARE" != "$PINNED" ]; then
        printf '%s  note%s  a bare `ruff` on PATH is %s — use `%s -m ruff` by hand, not `ruff`\n' \
               "$YEL" "$OFF" "$BARE" "$PY"
    fi
fi

# --- pre-commit pin ---------------------------------------------------------
step "pre-commit ruff pin"

if [ -f .pre-commit-config.yaml ]; then
    HOOK=$(grep -A1 'ruff-pre-commit' .pre-commit-config.yaml | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | tr -d v | head -1)
    if [ -n "$HOOK" ] && [ "$HOOK" != "$PINNED" ]; then
        # Worth failing on. The hook runs with --fix, so a mismatched version
        # rewrites files into a shape the gate rejects.
        bad "pre-commit pins ruff $HOOK but CI uses $PINNED — set rev: v$PINNED in .pre-commit-config.yaml"
    else
        ok "pre-commit pins ruff ${HOOK:-none} (consistent)"
    fi
fi

# --- PR Gate / lint ---------------------------------------------------------
step "PR Gate / lint"
if (cd api && $PY -m ruff check .); then
    ok "ruff check"
else
    bad "ruff check  ->  fix with:  cd api && $PY -m ruff check . --fix"
fi

# --- PR Gate / test ---------------------------------------------------------
step "PR Gate / test"
if (cd api && \
    JWT_SECRET=ci-secret-key-minimum-32-chars-long \
    RAZORPAY_MOCK=true ENABLE_DEV_ROUTES=true USE_IN_MEMORY_STORE=true \
    $PY -m pytest tests/test_risk_engine.py tests/test_authorization_engine.py \
                  tests/test_transaction_passport.py -q); then
    ok "engine tests"
else
    bad "engine tests"
fi

# --- API CI / full suite ----------------------------------------------------
# CI gives this job a Postgres service. Without one locally the DB-backed tests
# skip, which is the same outcome CI gets for the tenant-isolation module (CI
# has Postgres but never applies the migration). Set DATABASE_URL to run them
# for real.
step "API CI / full suite"
if (cd api && \
    DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://keenpay:keenpay@localhost:5432/keenpay_test}" \
    REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}" \
    JWT_SECRET=ci-secret-key-minimum-32-chars-long \
    RAZORPAY_MOCK=true ENABLE_DEV_ROUTES=true \
    $PY -m pytest -q --cov=. --cov-report= --cov-fail-under=0); then
    ok "full suite"
else
    bad "full suite"
fi

# --- verdict ----------------------------------------------------------------
printf '\n%s────────────────────────────────────────%s\n' "$DIM" "$OFF"
if [ ${#FAILED[@]} -eq 0 ]; then
    printf '%sALL GATES PASS%s — safe to push.\n' "$GREEN" "$OFF"
    exit 0
fi
printf '%s%d GATE(S) FAILED%s — do not push:\n' "$RED" "${#FAILED[@]}" "$OFF"
for f in "${FAILED[@]}"; do printf '  - %s\n' "$f"; done
exit 1
