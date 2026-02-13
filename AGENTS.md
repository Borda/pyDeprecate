# Agent Configuration for pyDeprecate

> [!IMPORTANT]
> **For detailed coding standards, testing requirements, and contribution workflows**, see the [Contributing Guide](.github/CONTRIBUTING.md). This file provides agent-specific behavioral instructions and quick reference only.

## Overview

pyDeprecate is a lightweight Python library providing decorator-based deprecation for functions, methods, and classes with automatic call forwarding. Python 3.9+, zero runtime dependencies.

## ⚓ Before Any Work: Anchor with Repository Context

> [!IMPORTANT]
> **Before starting any work, agents MUST anchor themselves with the repository context.** Read these files in order:

1. **Configuration files** (source of truth):

   - `pyproject.toml` — project config, tool settings (ruff, mypy, pytest)
   - `.pre-commit-config.yaml` — pre-commit hooks configuration
   - `setup.py` — package metadata and dependencies
   - `.github/workflows/*.yml` — CI/CD pipeline configuration

2. **Documentation and guidelines**:

   - [CONTRIBUTING.md](.github/CONTRIBUTING.md) — contribution workflow, coding standards, testing requirements
   - `README.md` — project usage and API
   - `src/deprecate/` — core library code to understand existing patterns

3. **Project structure**: For codebase layout and file organization, explore the repository structure directly or see [Test Organization](.github/CONTRIBUTING.md#test-organization) for test file structure

### Configuration Files Are Source of Truth

> [!WARNING]
> **If documentation contradicts actual configuration**, the configuration files have **higher authority**. When you detect a mismatch:
>
> 1. **Trust the config file** (e.g., `pyproject.toml`, `.pre-commit-config.yaml`)
> 2. **Suggest updating the documentation** to match reality
> 3. **Report the mismatch** clearly to maintainers

**When you find a mismatch:**

Write a clear explanation linking to both sources, then let maintainers decide on the update action:

*"I notice [CONTRIBUTING.md:45](.github/CONTRIBUTING.md#L45) mentions using black for formatting, but [pyproject.toml:23](pyproject.toml#L23) actually configures ruff. The config file should be considered correct. Should the documentation be updated to reflect that ruff handles formatting?"*

## 🧠 Agent Roles

### Engineer

**Scope**: Core logic, CI/CD, and Python compatibility

**Responsibilities**:

- Review PRs modifying `src/` code
- Validate deprecation warnings and function/class deprecations
- Ensure Python patterns for backwards compatibility
- Monitor CI/CD pipeline health

**Guidelines**:

- Follow [Coding Standards](/.github/CONTRIBUTING.md#-coding-standards)
- Follow [Test Requirements](/.github/CONTRIBUTING.md#-tests-and-quality-assurance)
- Use [Branch Naming Convention](/.github/CONTRIBUTING.md#-branch-naming-convention)

### Community-Scribe

**Scope**: Documentation and communication

**Responsibilities**:

- Maintain README and documentation
- Draft follow-ups after releases or PR merges
- Help onboard new contributors
- Ensure deprecation examples are documented

**Guidelines**:

- Follow [Documentation Guidelines](/.github/CONTRIBUTING.md#-coding-standards)
- See [Ways to Contribute](/.github/CONTRIBUTING.md#-ways-to-contribute)

### Security-Watcher

**Scope**: Security monitoring and vulnerability assessment

**Responsibilities**:

- Monitor dependencies for vulnerabilities
- Review PRs for security issues
- Follow [Security Policy](.github/SECURITY.md)

## 🧭 Agent Behavioral Rules

### Branch Management

- Use [branch naming convention](.github/CONTRIBUTING.md#-branch-naming-convention): `{type}/{issue-nb}-description`
- Types: `fix/`, `feat/`, `docs/`, `refactor/`, `test/`, `chore/`

### Bug Fixes

- **Use Test-Driven Development (TDD)**: Write a failing test that reproduces the bug first, then implement the fix
- See [Fixing Bugs](.github/CONTRIBUTING.md#-fixing-bugs) for complete workflow

### New Features

- **Require approval first**: See [Building Features](.github/CONTRIBUTING.md#-building-features-with-consensus)
- **Mandatory test coverage**: Happy path, failure path, and edge cases
- See [Test Requirements](.github/CONTRIBUTING.md#-tests-and-quality-assurance)

### Code Quality

- **Zero runtime dependencies** — never add to `install_requires`
- **Type hints required** — all function signatures must have type hints
- **No bare `except:`** — always catch specific exceptions
- **Fast imports** — no expensive module-level code
- See [Coding Standards](.github/CONTRIBUTING.md#-coding-standards) for complete rules

### Test Organization

- **Three-layer separation**: targets in `collection_targets.py`, deprecated wrappers in `collection_deprecate.py`, test logic in `test_*.py`
- **Do not** define targets or `@deprecated` wrappers directly in test files
- See [Test Organization](.github/CONTRIBUTING.md#test-organization) for details

## 🚫 Critical Constraints

### Never:

- Add runtime dependencies
- Commit sensitive information (`.env`, API keys)
- Use bare `except:` clauses
- Define deprecated wrappers inside test files
- Skip test coverage for new features or bug fixes
- Implement features without maintainer approval
- Start work without first reading config files and guidelines

### Always:

- **Anchor with repository context first** — read config files (`pyproject.toml`, `.pre-commit-config.yaml`) and guidelines before any work
- **Trust config files over documentation** — when mismatches occur, config files are the source of truth
- **Suggest documentation updates** when you find mismatches between docs and actual configuration
- Ensure pre-commit hooks are installed (they run automatically on commit)
- Provide `deprecated_in` and `remove_in` versions
- Include migration messages in deprecation warnings
- Write tests first when fixing bugs (TDD)
- Cover happy path, failure path, and edge cases in tests

## 🔗 Complete Guidelines

This file provides quick reference for agents. For complete, authoritative guidelines:

- **Contribution workflow** → [Contributing Guide](.github/CONTRIBUTING.md)
- **Coding standards** → [Contributing: Coding Standards](.github/CONTRIBUTING.md#-coding-standards)
- **Testing requirements** → [Contributing: Tests and Quality](.github/CONTRIBUTING.md#-tests-and-quality-assurance)
- **PR process** → [Contributing: Pull Requests](.github/CONTRIBUTING.md#-pull-requests)
- **PR review format** → [PR Review Template](.github/PR_REVIEW_TEMPLATE.md)
- **Security reporting** → [Security Policy](.github/SECURITY.md)
- **Community guidelines** → [Code of Conduct](.github/CODE_OF_CONDUCT.md)
