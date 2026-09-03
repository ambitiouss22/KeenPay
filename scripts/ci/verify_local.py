#!/usr/bin/env python3
"""Run every CI gate locally, exactly as GitHub runs it.

Run this before every push. If it is green, CI is green - the commands and the
tool versions are the same ones the workflows use.

    python scripts/ci/verify_local.py

Python rather than a shell script on purpose. A .ps1 will not run under
Windows' default execution policy, and a .sh needs a POSIX shell; both would
have to be kept in step with each other. One Python file runs everywhere the
project already runs, with nothing to enable and nothing to duplicate.

Two rules this script exists to enforce:

  1. Never invoke a bare `ruff`. A `ruff` binary earlier on PATH shadows the
     pip-installed one, so `ruff --version` can disagree with what pip reports.
     Formatting with one version and gating on another is how the lint job goes
     red with no code change. Everything here runs through sys.executable, the
     interpreter actually running this script, so there is nothing to shadow.

  2. The pinned version lives in api/pyproject.toml and nowhere else. This
     script reads it from there and refuses to run against anything else, so
     the pin cannot silently drift out of sync with CI.

Exit code is 0 only when every gate passes.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "api"
PY = sys.executable

# CI's environment, from .github/workflows/. Kept here so a drift between this
# script and the workflows is a one-place fix.
CI_ENV = {
    "JWT_SECRET": "ci-secret-key-minimum-32-chars-long",
    "RAZORPAY_MOCK": "true",
    "ENABLE_DEV_ROUTES": "true",
}
PR_GATE_TESTS = [
    "tests/test_risk_engine.py",
    "tests/test_authorization_engine.py",
    "tests/test_transaction_passport.py",
]


# --- output -----------------------------------------------------------------

def _colour_ok() -> bool:
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        # Win10+ consoles support ANSI once virtual-terminal processing is on;
        # this call is what turns it on. Older consoles just fail harmlessly.
        try:
            import ctypes

            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except (OSError, AttributeError):
            return False
    return True


C = _colour_ok()
RED = "\033[31m" if C else ""
GREEN = "\033[32m" if C else ""
YELLOW = "\033[33m" if C else ""
DIM = "\033[2m" if C else ""
OFF = "\033[0m" if C else ""

failures: list[str] = []


def step(msg: str) -> None:
    print(f"\n{YELLOW}=== {msg} ==={OFF}", flush=True)


def ok(msg: str) -> None:
    print(f"{GREEN}  PASS{OFF}  {msg}", flush=True)


def bad(msg: str) -> None:
    print(f"{RED}  FAIL{OFF}  {msg}", flush=True)
    failures.append(msg)


def note(msg: str) -> None:
    print(f"{YELLOW}  note{OFF}  {msg}", flush=True)


def run(cmd: list[str], cwd: Path, extra_env: dict[str, str] | None = None) -> int:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    print(f"{DIM}$ {' '.join(cmd)}{OFF}", flush=True)
    return subprocess.run(cmd, cwd=str(cwd), env=env, check=False).returncode


def version_of(text: str) -> str | None:
    m = re.search(r"(\d+\.\d+\.\d+)", text or "")
    return m.group(1) if m else None


# --- gates ------------------------------------------------------------------

def gate_worktree_clean(allow_dirty: bool) -> None:
    """Refuse to bless a working tree that differs from the commit.

    This gate exists because of a real failure: a file was fixed locally,
    verified green here, and then never included in the commit. CI ran the
    commit, hit the unfixed file, and went red - after this script had said
    "safe to push".

    The lesson is that verifying a *directory* proves nothing about what CI
    will run. CI runs what you pushed. So the check is now: is the thing I am
    about to verify the same thing that will land?

    Commit first, then verify. Use --allow-dirty for a mid-edit run, knowing
    the result only describes your disk.
    """
    step("worktree vs commit")

    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(ROOT), capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        note("not a git repo - skipping")
        return

    modified, untracked = [], []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        code, path = line[:2], line[3:].strip()
        (untracked if code == "??" else modified).append(path)

    # Untracked files are usually scratch, but a *new source file* that never
    # gets added is the same bug wearing a different hat, so they are listed.
    relevant_untracked = [
        p for p in untracked
        if p.endswith((".py", ".toml", ".yaml", ".yml", ".sql"))
    ]

    if not modified and not relevant_untracked:
        ok("worktree matches HEAD - what is verified is what CI will run")
        return

    for p in modified:
        print(f"{DIM}       modified:  {p}{OFF}")
    for p in relevant_untracked:
        print(f"{DIM}       untracked: {p}{OFF}")

    to_add = " ".join(modified + relevant_untracked)
    if allow_dirty:
        note("worktree is dirty; results describe your disk, NOT what CI will run")
        print(f"{DIM}       git add {to_add}{OFF}")
        return

    bad(
        f"{len(modified) + len(relevant_untracked)} uncommitted file(s): CI runs the "
        "commit, not your worktree. Commit them, then re-run "
        "(or pass --allow-dirty to check your disk anyway)"
    )
    print(f"{DIM}       git add {to_add}{OFF}")


def gate_ruff_version() -> str | None:
    """Reconcile the installed ruff with the pin. Returns the pinned version."""
    step("ruff version")

    pyproject = API / "pyproject.toml"
    if not pyproject.exists():
        bad(f"{pyproject} not found - run this from the repo, not a copy")
        return None

    m = re.search(r'"ruff==(\d+\.\d+\.\d+)"', pyproject.read_text(encoding="utf-8"))
    if not m:
        bad('could not read the pinned ruff version from api/pyproject.toml')
        print(f'{DIM}       Expected a line like:  "ruff==0.16.5",{OFF}')
        return None
    pinned = m.group(1)

    def installed() -> str | None:
        p = subprocess.run(
            [PY, "-m", "ruff", "--version"], capture_output=True, text=True, check=False
        )
        return version_of(p.stdout) if p.returncode == 0 else None

    have = installed()
    if have != pinned:
        print(f"pinned={pinned} installed={have or 'none'} - installing the pinned version")
        if subprocess.run(
            [PY, "-m", "pip", "install", "-q", f"ruff=={pinned}"], check=False
        ).returncode != 0:
            bad(f"could not install ruff=={pinned}")
            return None
        have = installed()

    if have != pinned:
        bad(f"ruff is {have} but CI uses {pinned}")
        return None

    ok(f"ruff {have} matches the pin in api/pyproject.toml")

    # A bare `ruff` on PATH that disagrees is not fatal - everything here goes
    # through `python -m ruff`. It is worth knowing about, because it is what
    # makes a hand-run `ruff check` in a terminal lie to you.
    try:
        p = subprocess.run(
            ["ruff", "--version"], capture_output=True, text=True, check=False
        )
        bare = version_of(p.stdout)
        if bare and bare != pinned:
            note(f"a bare 'ruff' on PATH is {bare} - use '{Path(PY).name} -m ruff' by hand")
    except (OSError, FileNotFoundError):
        pass

    return pinned


def gate_precommit_pin(pinned: str) -> None:
    """The hook runs with --fix; a mismatched version rewrites files CI rejects."""
    step("pre-commit ruff pin")
    cfg = ROOT / ".pre-commit-config.yaml"
    if not cfg.exists():
        ok("no .pre-commit-config.yaml")
        return

    lines = cfg.read_text(encoding="utf-8").splitlines()
    hook = None
    for i, line in enumerate(lines):
        if "ruff-pre-commit" in line:
            for nxt in lines[i : i + 3]:
                m = re.search(r"rev:\s*v?(\d+\.\d+\.\d+)", nxt)
                if m:
                    hook = m.group(1)
                    break
            break

    if hook and hook != pinned:
        bad(
            f"pre-commit pins ruff {hook} but CI uses {pinned} - "
            f"set 'rev: v{pinned}' in .pre-commit-config.yaml"
        )
    else:
        ok(f"pre-commit pins ruff {hook or 'none'} (consistent)")


def gate_lint() -> None:
    step("PR Gate / lint")
    if run([PY, "-m", "ruff", "check", "."], cwd=API) == 0:
        ok("ruff check")
    else:
        bad(f"ruff check  ->  fix with:  cd api && {Path(PY).name} -m ruff check . --fix")


def gate_engine_tests() -> None:
    step("PR Gate / test")
    env = dict(CI_ENV, USE_IN_MEMORY_STORE="true")
    if run([PY, "-m", "pytest", *PR_GATE_TESTS, "-q"], cwd=API, extra_env=env) == 0:
        ok("engine tests")
    else:
        bad("engine tests")


def gate_full_suite() -> None:
    # CI gives this job a Postgres service. Without one locally the DB-backed
    # tests skip - the same outcome CI gets for the tenant-isolation module,
    # since CI has Postgres but never applies the migration. Set DATABASE_URL
    # to run them for real.
    step("API CI / full suite")
    env = dict(CI_ENV)
    env["DATABASE_URL"] = os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://keenpay:keenpay@localhost:5432/keenpay_test"
    )
    env["REDIS_URL"] = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    env.pop("USE_IN_MEMORY_STORE", None)
    cmd = [PY, "-m", "pytest", "-q", "--cov=.", "--cov-report=", "--cov-fail-under=0"]
    if run(cmd, cwd=API, extra_env=env) == 0:
        ok("full suite")
    else:
        bad("full suite")


# --- main -------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--lint-only", action="store_true", help="run only the version and lint gates"
    )
    ap.add_argument(
        "--allow-dirty",
        action="store_true",
        help="verify the working tree even when it differs from HEAD",
    )
    args = ap.parse_args()

    print(f"{DIM}repo:   {ROOT}{OFF}")
    print(f"{DIM}python: {PY}{OFF}")

    gate_worktree_clean(args.allow_dirty)

    pinned = gate_ruff_version()
    if pinned is None:
        print(f"\n{RED}version gate failed - nothing else was run.{OFF}")
        return 1

    gate_precommit_pin(pinned)
    gate_lint()
    if not args.lint_only:
        gate_engine_tests()
        gate_full_suite()

    print(f"\n{DIM}{'-' * 40}{OFF}")
    if not failures:
        print(f"{GREEN}ALL GATES PASS{OFF} - safe to push.")
        return 0
    print(f"{RED}{len(failures)} GATE(S) FAILED{OFF} - do not push:")
    for f in failures:
        print(f"  - {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
