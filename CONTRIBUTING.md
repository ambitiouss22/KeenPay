# Contributing to KeenPay

Thank you for contributing! This guide ensures consistent code quality and smooth collaboration.

---

## Code Style

### Python (API + Workers)

**Linting & formatting:**
\\\ash
# Check issues
ruff check .

# Fix + format (in-place)
ruff check . --fix
ruff format .
\\\

**Style rules:**
- Line length: **100 characters** (enforced by ruff)
- Imports: Sorted alphabetically (ruff I-rule)
- Type hints: Required for public functions (\-> None\, \-> str\, etc.)
- Docstrings: Google-style on public functions/classes
- Security: Bandit flags (\S101\ = assertions, \S608\ = SQL injection) — justified with \# noqa: S*\ comments

**Example:**
\\\python
def process_payment(
    amount: int, currency: str, merchant_id: str
) -> PaymentResult:
    \"\"\"Process a payment via Razorpay.

    Args:
        amount: Amount in paise (e.g., 100 = ?1.00)
        currency: ISO 4217 code, typically "INR"
        merchant_id: Authenticated merchant

    Returns:
        PaymentResult with status and link

    Raises:
        PaymentError: If Razorpay rejects the payment
    \"\"\"
    ...
\\\

### TypeScript (Frontend)

**Linting:**
\\\ash
npm run lint
\\\

**Style rules:**
- ESLint + Next.js core-web-vitals config
- Strict TypeScript checks (no \ny\, use \unknown\ + type guards)
- Functional components only (no class components)

---

## Commit Messages

Follow **Conventional Commits** format:

\\\
<type>(<scope>): <subject>

<body>

<footer>
\\\

**Types:**
- \eat\ — New feature
- \ix\ — Bug fix
- \perf\ — Performance improvement
- \efactor\ — Code restructure (no functional change)
- \	est\ — Add/fix tests
- \docs\ — Documentation only
- \chore\ — Dependencies, tooling, config
- \style\ — Code formatting (ruff changes, etc.)
- \ci\ — GitHub Actions, CI config

**Scope:** Module affected (e.g., \policy\, \azorpay\, \uth\)

**Examples:**
\\\
feat(policy): add max-discount-percent rule

- Reads MERCHANT_POLICY_JSON at startup
- Validates discount <= policy.max_discount
- Returns POLICY_VIOLATION in guardrail_check response

Fixes #45
\\\

---

## Pull Request Workflow

### 1. Create a branch
\\\ash
git checkout -b feat/policy-discount-percent
\\\

### 2. Make changes locally
\\\ash
make test
make lint-fix
\\\

### 3. Commit with conventional messages
\\\ash
git add api/policy/rules/max_discount.py
git commit -m "feat(policy): add max-discount-percent rule"
\\\

### 4. Push and open PR
\\\ash
git push -u origin feat/policy-discount-percent
gh pr create --fill
\\\

### 5. PR checklist
- [ ] Tests pass locally (\make test\ ? all green)
- [ ] Linting passes (\make lint\ ? no issues)
- [ ] Commit messages follow Conventional Commits
- [ ] No \.env\ or secrets in code
- [ ] Documentation updated (if needed)
- [ ] Linked to relevant issues in PR body

### 6. Review & merge
- Wait for CI (PR Gate: lint + pytest)
- Address review feedback
- Rebase + merge

---

## Testing Expectations

### Coverage Targets
- **API:** 60%+ overall
- **Policy engine:** 100% (safety-critical)
- **Services:** 75%+

### Running tests
\\\ash
# All
make test

# Specific file
pytest api/tests/unit/test_policy.py -v
\\\

---

## Before Submitting

\\\ash
make lint-fix
ruff format .
make test
\\\

If any fail, fix before pushing.

---

**Thank you for contributing to KeenPay!** ??
