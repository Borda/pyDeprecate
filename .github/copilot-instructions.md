# Copilot Instructions for pyDeprecate

> [!TIP]
> **For coding standards and contribution workflow**, see [CONTRIBUTING.md](CONTRIBUTING.md). **For agent behavioral rules**, see [AGENTS.md](../AGENTS.md). This file provides Copilot-specific guidance for understanding and working with the codebase.

## Project Overview

pyDeprecate is a lightweight Python library (Python 3.9+) for decorator-based deprecation of functions, methods, and classes with automatic call forwarding. **Zero runtime dependencies** by design.

**Tech stack**: Python, pytest, setuptools, pre-commit, GitHub Actions

## ⚠️ Important: Configuration Files Are Source of Truth

> [!WARNING]
> **If this documentation contradicts actual configuration files**, the config files have **higher authority**. Trust `pyproject.toml`, `.pre-commit-config.yaml`, and other config files over documentation. When you detect a mismatch, suggest updating this documentation to match the actual configuration.

**Configuration files** (source of truth):

- `pyproject.toml` — project config, tool settings (ruff, mypy, pytest)
- `.pre-commit-config.yaml` — pre-commit hooks
- `setup.py` — package metadata and dependencies
- `.github/workflows/*.yml` — CI/CD pipeline

## Quick Start Commands

```bash
# Development setup
pip install -e . "pre-commit" -r tests/requirements.txt
pre-commit install

# Run all linters and formatters manually (optional - runs automatically on commit)
pre-commit run --all-files

# Run tests (includes doctests from src/)
pytest src/ tests/

# Run specific test file
pytest tests/test_functions.py

# Run tests with coverage
pytest --cov=src/deprecate tests/
```

## Project Structure

```
pyDeprecate/
├── src/deprecate/              # Core library code
│   ├── __about__.py            # Version and metadata
│   ├── __init__.py             # Public API exports
│   ├── deprecation.py          # @deprecated decorator and warning logic
│   └── utils.py                # Helpers: void(), validate_*, no_warning_call()
├── tests/                      # Test suite
│   ├── collection_targets.py       # Target functions (new implementations)
│   ├── collection_deprecate.py     # Deprecated wrappers (@deprecated)
│   ├── collection_misconfigured.py # Invalid configs for validation
│   ├── test_functions.py           # Function deprecation tests
│   ├── test_classes.py             # Class deprecation tests
│   ├── test_docs.py                # Docstring tests
│   └── test_utils.py               # Utility function tests
├── .github/
│   ├── workflows/              # CI/CD pipelines
│   ├── CONTRIBUTING.md         # Contribution guidelines (canonical)
│   └── SECURITY.md             # Security policy
├── AGENTS.md                   # Agent behavioral instructions
├── pyproject.toml              # Project config (ruff, mypy, pytest)
└── setup.py                    # Package setup
```

## Build & Test Workflow

1. **Install development dependencies**:

   ```bash
   pip install -e . "pre-commit" -r tests/requirements.txt
   ```

2. **Install pre-commit hooks** (runs linting/formatting on commit):

   ```bash
   pre-commit install
   ```

3. **Make code changes** following [coding standards](CONTRIBUTING.md#-coding-standards)

4. **Run linters manually** (optional - pre-commit hooks run automatically on commit):

   ```bash
   pre-commit run --all-files
   ```

5. **Run tests**:

   ```bash
   pytest src/ tests/     # Run all tests
   pytest -v              # Verbose output
   pytest -k "test_name"  # Run specific test
   ```

6. **Commit changes** (pre-commit hooks automatically run all linting/formatting):

   ```bash
   git add .
   git commit -m "fix: description"  # Hooks enforce ruff + mypy automatically
   ```

## Architecture & Constraints

### Critical Constraints

- **Zero runtime dependencies** — `install_requires` is empty and must stay that way
- **Fast imports** — no expensive module-level code or premature imports
- **Type hints required** — all function signatures must have type hints
- **No bare `except:`** — always catch specific exceptions

### Test File Organization

Tests use a **three-layer separation**:

1. **Targets** (`collection_targets.py`) — new implementations
2. **Deprecated wrappers** (`collection_deprecate.py`) — `@deprecated` wrappers
3. **Test logic** (`test_*.py`) — imports from collections and asserts behavior

**Important**: Do not define target functions or `@deprecated` wrappers directly in `test_*.py` files.

### Circular Import Prevention

When editing `src/deprecate/`, use `if TYPE_CHECKING:` blocks for type-only imports to avoid circular dependencies:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deprecate.deprecation import SomeType
```

## Development Guidelines

### Branch Naming

Follow the pattern: `{type}/{issue-nb}-description`

- Types: `fix/`, `feat/`, `docs/`, `refactor/`, `test/`, `chore/`
- Examples: `fix/123-circular-import`, `feat/45-new-validator`

See [Branch Naming Convention](CONTRIBUTING.md#-branch-naming-convention)

### Test-Driven Development (TDD) for Bug Fixes

1. Write a failing test that reproduces the bug
2. Implement the fix to make the test pass
3. Verify all tests pass

See [Fixing Bugs](CONTRIBUTING.md#-fixing-bugs)

### Test Coverage Requirements

All new features and bug fixes **must** include tests for:

- **Happy path** — correct behavior with valid inputs
- **Failure path** — appropriate errors raised
- **Edge cases** — None, empty inputs, circular chains, boundary conditions

See [Test Requirements](CONTRIBUTING.md#-tests-and-quality-assurance)

## Common Patterns

### Adding a deprecation wrapper (in test collections)

```python
# tests/collection_targets.py
def new_func(x: int) -> int:
    """New implementation."""
    return x * 2


# tests/collection_deprecate.py
from deprecate import deprecated
from tests.collection_targets import new_func


@deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
def old_func(x: int) -> int:
    """Deprecated: use new_func instead."""


# tests/test_functions.py
import pytest
from tests.collection_deprecate import old_func


def test_deprecation_warning() -> None:
    with pytest.warns(FutureWarning, match="was deprecated"):
        assert old_func(5) == 10
```

### Testing without warnings

```python
from deprecate import no_warning_call


def test_without_warning() -> None:
    with no_warning_call(FutureWarning):
        # Code that should not emit warnings
        pass
```

## PR Review Guidelines

When reviewing PRs, use the structured format in this file for consistent, actionable feedback.

### 1. Overall Recommendation

Provide a clear recommendation with justification:

- 🟢 **Approve** — ready to merge
- 🟡 **Minor Suggestions** — improvements recommended but not blocking
- 🟠 **Request Changes** — significant issues must be addressed
- 🔴 **Block** — critical issues require major rework

### 2. PR Completeness Check

Verify (✅ complete, ⚠️ incomplete, ❌ missing, 🔵 N/A):

- [ ] Clear description of what changed and why
- [ ] Link to related issue (`Fixes #N` or `Relates to #N`)
- [ ] Tests added/updated for new functionality
- [ ] Docstrings for new public functions/classes (Google-style)
- [ ] All CI checks pass

### 3. Quality Assessment

Score each dimension (n/5) with specific inline comments:

- **Code quality** — correctness, edge cases, idiomatic Python, type hints
- **Testing quality** — happy path, failure path, edge cases; correct file placement
- **Documentation quality** — complete docstrings, updated docs

### 4. Risk Assessment

Flag risks with severity:

- **Breaking changes** — API changes, removed features (need migration instructions)
- **Performance impact** — inefficient algorithms, memory issues
- **Compatibility** — Python version changes, platform-specific code
- **Architecture** — runtime dependencies (not allowed), circular imports, expensive imports

### 5. Specific Suggestions

Provide actionable improvements using GitHub suggestion format:

````markdown
```suggestion
if data is None:
    return None
return process(data)
```
````

### Review Best Practices

- Explain **why** something is a problem, not just **what**
- Distinguish blocking issues from nice-to-haves
- Acknowledge good work
- Be pragmatic — don't let perfect be the enemy of good
- Use inline comments for specific code feedback

### Review Summary Template

```markdown
## Review Summary

### Recommendation
[emoji] [Status] — [One-sentence justification]

### PR Completeness
- ✅ Complete: [list]
- ❌ Missing: [list with links to inline comments]

### Quality Scores
- Code Quality: n/5 — [reason]
- Testing: n/5 — [reason]
- Documentation: n/5 — [reason]

### Risk Level: n/5
[Brief risk description]

### Critical Issues (Must Fix)
1. [Issue with link to inline comment]

### Suggestions (Nice to Have)
1. [Suggestion with link to inline comment]
```

## Known Issues & Workarounds

- **Circular imports**: Use `if TYPE_CHECKING:` blocks in `src/deprecate/`
- **Deprecation chains**: Handle infinite loops (A deprecates B, B deprecates A) gracefully

## CI/CD Pipeline

GitHub Actions workflows (`.github/workflows/`):

- **Linting**: Runs `ruff` and `mypy` on all Python files
- **Testing**: Runs `pytest` across multiple Python versions (3.9, 3.10, 3.11, 3.12, 3.13)
- **Pre-commit**: Validates formatting and style

All checks must pass before merge.

## Reference Documentation

- **Contribution workflow** → [CONTRIBUTING.md](CONTRIBUTING.md)
- **Agent behavioral rules** → [AGENTS.md](../AGENTS.md)
- **Security reporting** → [SECURITY.md](SECURITY.md)
- **Code of Conduct** → [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
