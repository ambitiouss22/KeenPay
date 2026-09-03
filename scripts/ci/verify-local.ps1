# Run every CI gate locally, exactly as GitHub runs it.
#
# Run this before every push. If it is green, CI is green - the commands and
# the tool versions are the same ones the workflows use.
#
#   .\scripts\ci\verify-local.ps1
#
# Two rules this script exists to enforce:
#
#   1. Never invoke a bare `ruff`. A `ruff` binary earlier on PATH shadows the
#      pip-installed one, so `ruff --version` can disagree with what pip
#      reports. Formatting with one version and gating on another is how the
#      lint job goes red with no code change. `python -m ruff` always resolves
#      to the installed package.
#
#   2. The pinned version lives in api\pyproject.toml and nowhere else. This
#      script reads it from there and refuses to run against anything else, so
#      the pin cannot silently drift out of sync with CI.
#
# Windows PowerShell 5.1 compatible.

$ErrorActionPreference = 'Continue'
Set-Location (Join-Path $PSScriptRoot '..\..')

$script:Failed = @()
if ($env:PYTHON) { $Py = $env:PYTHON } else { $Py = 'python' }

function Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Yellow }
function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red; $script:Failed += $m }

function Get-Ver($text) {
    if ($text -match '(\d+\.\d+\.\d+)') { return $Matches[1] }
    return $null
}

# --- ruff version gate ------------------------------------------------------
Step 'ruff version'

$pinned = Get-Ver ((Select-String -Path 'api\pyproject.toml' -Pattern '"ruff==\d+\.\d+\.\d+"' |
                    Select-Object -First 1).Line)
if (-not $pinned) {
    Bad 'could not read the pinned ruff version from api\pyproject.toml'
    Write-Host '       Expected a line like:  "ruff==0.16.5",' -ForegroundColor DarkGray
    exit 1
}

$installed = Get-Ver (& $Py -m ruff --version 2>$null | Out-String)

if ($installed -ne $pinned) {
    $shown = $installed; if (-not $shown) { $shown = 'none' }
    Write-Host "pinned=$pinned installed=$shown - installing the pinned version"
    & $Py -m pip install -q "ruff==$pinned"
    if ($LASTEXITCODE -ne 0) { Bad "could not install ruff==$pinned"; exit 1 }
    $installed = Get-Ver (& $Py -m ruff --version 2>$null | Out-String)
}

if ($installed -ne $pinned) { Bad "ruff is $installed but CI uses $pinned"; exit 1 }
Ok "ruff $installed matches the pin in api\pyproject.toml"

# A bare `ruff` on PATH that disagrees is not fatal here, because everything
# below goes through `python -m ruff`. It is worth knowing about, though: it is
# what makes a manual `ruff check` in a terminal lie to you.
$bareCmd = Get-Command ruff -ErrorAction SilentlyContinue
if ($bareCmd) {
    $bare = Get-Ver ((& ruff --version 2>$null) | Out-String)
    if ($bare -and $bare -ne $pinned) {
        Write-Host "  note  a bare 'ruff' on PATH is $bare - use '$Py -m ruff' by hand, not 'ruff'" -ForegroundColor Yellow
    }
}

# --- pre-commit pin ---------------------------------------------------------
Step 'pre-commit ruff pin'

if (Test-Path '.pre-commit-config.yaml') {
    $lines = Get-Content '.pre-commit-config.yaml'
    $hook = $null
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match 'ruff-pre-commit') {
            for ($j = $i; $j -lt [Math]::Min($i + 3, $lines.Count); $j++) {
                if ($lines[$j] -match 'rev:\s*v?(\d+\.\d+\.\d+)') { $hook = $Matches[1]; break }
            }
            break
        }
    }
    if ($hook -and $hook -ne $pinned) {
        # Worth failing on. The hook runs with --fix, so a mismatched version
        # rewrites files into a shape the gate rejects.
        Bad "pre-commit pins ruff $hook but CI uses $pinned - set rev: v$pinned in .pre-commit-config.yaml"
    } else {
        $shown = $hook; if (-not $shown) { $shown = 'none' }
        Ok "pre-commit pins ruff $shown (consistent)"
    }
}

# --- PR Gate / lint ---------------------------------------------------------
Step 'PR Gate / lint'
Push-Location api
& $Py -m ruff check .
$lintOk = ($LASTEXITCODE -eq 0)
Pop-Location
if ($lintOk) { Ok 'ruff check' }
else { Bad "ruff check  ->  fix with:  cd api; $Py -m ruff check . --fix" }

# --- PR Gate / test ---------------------------------------------------------
Step 'PR Gate / test'
$env:JWT_SECRET = 'ci-secret-key-minimum-32-chars-long'
$env:RAZORPAY_MOCK = 'true'
$env:ENABLE_DEV_ROUTES = 'true'
$env:USE_IN_MEMORY_STORE = 'true'
Push-Location api
& $Py -m pytest tests/test_risk_engine.py tests/test_authorization_engine.py tests/test_transaction_passport.py -q
$testOk = ($LASTEXITCODE -eq 0)
Pop-Location
if ($testOk) { Ok 'engine tests' } else { Bad 'engine tests' }

# --- API CI / full suite ----------------------------------------------------
# CI gives this job a Postgres service. Without one locally the DB-backed tests
# skip, which is the same outcome CI gets for the tenant-isolation module (CI
# has Postgres but never applies the migration). Set DATABASE_URL to run them
# for real.
Step 'API CI / full suite'
Remove-Item Env:USE_IN_MEMORY_STORE -ErrorAction SilentlyContinue
if (-not $env:DATABASE_URL) {
    $env:DATABASE_URL = 'postgresql+asyncpg://keenpay:keenpay@localhost:5432/keenpay_test'
}
if (-not $env:REDIS_URL) { $env:REDIS_URL = 'redis://localhost:6379/0' }
Push-Location api
& $Py -m pytest -q --cov=. --cov-report= --cov-fail-under=0
$suiteOk = ($LASTEXITCODE -eq 0)
Pop-Location
if ($suiteOk) { Ok 'full suite' } else { Bad 'full suite' }

# --- verdict ----------------------------------------------------------------
Write-Host "`n----------------------------------------" -ForegroundColor DarkGray
if ($script:Failed.Count -eq 0) {
    Write-Host 'ALL GATES PASS - safe to push.' -ForegroundColor Green
    exit 0
}
Write-Host "$($script:Failed.Count) GATE(S) FAILED - do not push:" -ForegroundColor Red
foreach ($f in $script:Failed) { Write-Host "  - $f" }
exit 1
