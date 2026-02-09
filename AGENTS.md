# Agent HQ Configuration for pyDeprecate

> [!IMPORTANT]
> For contribution workflows (PRs, issues, community process), see the [Contributing Guide](.github/CONTRIBUTING.md).

## Overview

pyDeprecate is a lightweight Python library providing decorator-based deprecation for functions, methods, and classes with automatic call forwarding. Python 3.9+, zero runtime dependencies.

## 🧠 Agents

### Engineer

**Role**: Core logic, CI/CD, and Python compatibility specialist
**Tools**: Python, pytest, setuptools, pre-commit, tox, GitHub Actions
**Behavior**:

- Review PRs modifying code in the `src/` folder
- Validate deprecation warnings and function/class deprecations
- Ensure correct use of Python patterns for backwards compatibility
- Monitor CI/CD pipeline health and validate test results
- Check reproducibility: fixed seeds (if applicable), versioned dependencies, consistent configs
- Follow coding standards defined in [Contributing Guide](.github/CONTRIBUTING.md#-coding-standards)

### Community-Scribe

**Role**: Documentation, communication, and onboarding assistant
**Tools**: Markdown, Sphinx (temp_docs/), GitHub Issues, Discussions
**Behavior**:

- Maintain README and documentation with setup, usage, and migration instructions
- Draft follow-ups after releases or PR merges
- Summarize feedback from reviewers and suggest next steps
- Help onboard new contributors with deprecation-specific guides
- Ensure deprecation examples and API changes are documented
- Follow documentation guidelines in [Contributing Guide](.github/CONTRIBUTING.md#-coding-standards)

### Security-Watcher

**Role**: Security monitoring and vulnerability assessment
**Tools**: GitHub Security Advisories, Dependabot
**Behavior**:

- Monitor dependencies for known vulnerabilities
- Review PRs for potential security issues
- Follow the [Security Policy](.github/SECURITY.md) for vulnerability handling
- Ensure secure coding practices are followed

## 🔐 Permissions

| Agent            | Branch Access  | PR Review | Issue Commenting | Security Advisory |
| ---------------- | -------------- | --------- | ---------------- | ----------------- |
| engineer         | `main`, `dev`  | ✅        | ✅               | ❌                |
| community-scribe | `docs`, `main` | ✅        | ✅               | ❌                |
| security-watcher | `main`         | ✅        | ✅               | ✅                |

## 📚 Project Structure

```
src/deprecate/
├── __about__.py              # Version and metadata
├── __init__.py               # Public API exports
├── deprecation.py            # Core @deprecated decorator and warning logic
└── utils.py                  # Helpers: void, validation, no_warning_call
tests/
├── collection_targets.py     # Target functions/classes (the "new" implementations)
├── collection_deprecate.py   # Deprecated wrappers using @deprecated(...)
├── collection_misconfigured.py # Invalid deprecation configs for validation tests
├── test_functions.py         # Tests for function deprecation
├── test_classes.py           # Tests for class deprecation
├── test_docs.py              # Tests for docstring updates
└── test_utils.py             # Tests for utility functions
```

## 📚 Context

> [!IMPORTANT]
> Before starting any work, agents **must** read and understand these resources to ensure all actions are aligned with the project.

- `README.md`, `setup.py` and `pyproject.toml` — project setup, usage, and configuration
- `src/` — core deprecation logic
- `tests/` — unit tests and test collections for deprecation logic
- `.github/CONTRIBUTING.md` — contribution guidelines and coding standards
- `.github/SECURITY.md` — security policies and vulnerability reporting
- `.github/CODE_OF_CONDUCT.md` — community guidelines

## 🧭 Mission Rules

- Never commit `.env`, API keys, or sensitive information
- PRs touching deprecation code or API changes must be reviewed by `engineer`
- All deprecations must include proper warnings and migration paths
- Dependency updates must be documented with version information
- Follow Python best practices for library development
- Refer to [Contributing Guide](.github/CONTRIBUTING.md) for detailed coding standards
- Security vulnerabilities must be handled per [Security Policy](.github/SECURITY.md)

## 🧪 Protocols

### Deprecation Validation

- Confirm deprecation decorators and warnings are correctly applied
- Validate backwards compatibility and test coverage
- Ensure proper error handling and logging for deprecated features
- Check for proper migration examples in documentation

### Documentation & Community Update

- Update README if API, config, or deprecation logic changes
- Include example usage:
  ```python
  from deprecate import deprecated


  @deprecated(target=None, deprecated_in="X.Y.Z", remove_in="A.B.C")
  def old_function():
      pass
  ```

## 📦 Commands

```bash
pip install -e . "pre-commit" -r tests/requirements.txt && pre-commit install  # dev setup
pre-commit run --all-files     # run all linters and formatters
pytest src/ tests/             # run tests (includes doctests)
```

## 📋 Coding Rules & Architecture

Follow the [Coding Standards](.github/CONTRIBUTING.md#-coding-standards) in the Contributing Guide. Key constraints agents must not violate:

- **Zero runtime dependencies** — `install_requires` is empty by design
- **All function signatures must have type hints** — enforced by `mypy`
- **No bare `except:`** — always catch specific exceptions
- **Fast imports** — no expensive module-level code or premature imports
- **Circular imports** — use `if TYPE_CHECKING:` blocks in `src/deprecate/`

## 🧪 Test File Placement

Tests follow a **three-layer separation** — do not mix these concerns:

| Layer               | File(s)                       | What goes here                                                   |
| ------------------- | ----------------------------- | ---------------------------------------------------------------- |
| Targets             | `collection_targets.py`       | The "new" functions and classes that deprecated code forwards to |
| Deprecated wrappers | `collection_deprecate.py`     | Functions/classes decorated with `@deprecated(...)`              |
| Misconfigured       | `collection_misconfigured.py` | Intentionally invalid deprecation configs for validation testing |
| Test logic          | `test_*.py`                   | Imports from collections above, asserts behavior                 |

> [!IMPORTANT]
> Do **not** define target functions or `@deprecated` wrappers directly inside `test_*.py` files.

**Test requirements:**

- Every new function or behavior change must have accompanying tests.
- Include tests for: happy path, failure path, and edge cases (None, empty inputs, circular chains).
- Group related tests in classes. Avoid redundant naming — in `TestDeprecatedWrapper`, use `test_shows_warning` not `test_deprecated_wrapper_shows_warning`.
- Use `pytest.warns(FutureWarning)` (or `pytest.warns(DeprecationWarning)` when testing custom warning streams/categories) to verify deprecation warnings.
- Use `pytest.fixture(autouse=True)` for per-test state reset when needed.
- One behavior per test method.

## 🚧 Boundaries

### ✅ Always

- Run `pre-commit run` before committing
- Provide `deprecated_in` and `remove_in` version strings on every deprecation
- Include migration messages in deprecation warnings pointing to replacements
- Place targets in `collection_targets.py`, wrappers in `collection_deprecate.py`, tests in `test_*.py`

### ⚠️ Ask First

- Adding new public API surface to `src/deprecate/`
- Modifying deprecation chain validation logic
- Changes that affect backwards compatibility

### 🚫 Never

- Add runtime dependencies
- Commit `.env`, API keys, or sensitive information
- Use bare `except:` clauses

## 🔗 Cross-Reference Guidelines

This file avoids duplicating content from other documentation. Agents should follow these references for the complete, authoritative guidelines:

- **Coding standards** → [Contributing Guide: Coding Standards](.github/CONTRIBUTING.md#-coding-standards)
- **Testing guidelines** → [Contributing Guide: Tests and Quality Assurance](.github/CONTRIBUTING.md#-tests-and-quality-assurance)
- **PR process** → [Contributing Guide: Pull Requests](.github/CONTRIBUTING.md#-pull-requests)
- **PR review guidelines** → [Copilot Instructions: PR Review Guidelines](.github/copilot-instructions.md#pr-review-guidelines)
- **Security reporting** → [Security Policy](.github/SECURITY.md)
- **Community guidelines** → [Code of Conduct](.github/CODE_OF_CONDUCT.md)
