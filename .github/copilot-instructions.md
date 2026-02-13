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

## Quick Reference

**For detailed workflows, commands, and guidelines**, see:

- **Setup & commands** → [CONTRIBUTING.md: Quick Start](CONTRIBUTING.md#-quick-start)
- **Running tests** → [CONTRIBUTING.md: Tests](CONTRIBUTING.md#-tests-and-quality-assurance)
- **Project structure** → [CONTRIBUTING.md: Project Structure](CONTRIBUTING.md#project-structure)

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

See [CONTRIBUTING.md: Test Organization](CONTRIBUTING.md#test-organization) for details.

### Circular Import Prevention

When editing `src/deprecate/`, use `if TYPE_CHECKING:` blocks for type-only imports. See [CONTRIBUTING.md: Project Structure](CONTRIBUTING.md#project-structure) for the code example.

## Development Guidelines

### Branch Naming

Follow the pattern: `{type}/{issue-nb}-description`

- Types: `fix/`, `feat/`, `docs/`, `refactor/`, `test/`, `chore/`
- Examples: `fix/123-circular-import`, `feat/45-new-validator`

See [CONTRIBUTING.md: Branch Naming](CONTRIBUTING.md#-branch-naming-convention)

### Test-Driven Development (TDD) for Bug Fixes

1. Write a failing test that reproduces the bug
2. Implement the fix to make the test pass
3. Verify all tests pass

See [CONTRIBUTING.md: Fixing Bugs](CONTRIBUTING.md#-fixing-bugs)

### Test Coverage Requirements

All new features and bug fixes **must** include tests for:

- **Happy path** — correct behavior with valid inputs
- **Failure path** — appropriate errors raised
- **Edge cases** — None, empty inputs, circular chains, boundary conditions

See [CONTRIBUTING.md: Test Requirements](CONTRIBUTING.md#-tests-and-quality-assurance)

## Common Patterns

For code examples and patterns (deprecation wrappers, argument renaming, testing without warnings), see [CONTRIBUTING.md: Common Patterns](CONTRIBUTING.md#common-patterns).

## PR Review Guidelines

When reviewing PRs, use the structured format to ensure consistent, actionable feedback.

**See [PR Review Template](PR_REVIEW_TEMPLATE.md) for the complete review format and guidelines.**

Quick checklist:

- Overall recommendation (🟢 Approve / 🟡 Minor Suggestions / 🟠 Request Changes / 🔴 Block)
- Completeness check (description, issue link, tests, docs, CI)
- Quality scores (code/testing/documentation: n/5)
- Risk assessment (breaking changes, performance, compatibility, architecture)
- Specific suggestions (inline with GitHub suggestion format)

## Known Issues & Workarounds

- **Circular imports**: Use `if TYPE_CHECKING:` blocks in `src/deprecate/`
- **Deprecation chains**: Handle infinite loops (A deprecates B, B deprecates A) gracefully

## CI/CD Pipeline

GitHub Actions workflows (`.github/workflows/`):

- **Linting**: Runs `ruff` and `mypy` on all Python files
- **Testing**: Runs `pytest` across multiple Python versions (3.9, 3.11, 3.13)
- **Pre-commit**: Validates formatting and style

All checks must pass before merge.

## Reference Documentation

- **Contribution workflow** → [CONTRIBUTING.md](CONTRIBUTING.md)
- **Agent behavioral rules** → [AGENTS.md](../AGENTS.md)
- **PR review format** → [PR_REVIEW_TEMPLATE.md](PR_REVIEW_TEMPLATE.md)
- **Security reporting** → [SECURITY.md](SECURITY.md)
- **Code of Conduct** → [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
