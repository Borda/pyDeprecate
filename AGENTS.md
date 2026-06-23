# Agent Configuration for pyDeprecate

> [!IMPORTANT]
> **For detailed coding standards, testing requirements, and contribution workflows**, see the [Contributing Guide](.github/CONTRIBUTING.md). This file provides agent-specific behavioral instructions and quick reference only.

## 🎯 Design Principles

- **Simplicity**: the API surface must be learnable in minutes; a single decorator covers the common case with no config files required.
- **Robustness**: deprecated code is on the removal path; correctness must hold regardless of call order or framework integration; audit tools must surface all live deprecations.
- **Flexibility**: work with any Python callable — functions, class methods, async, properties, dataclasses, enums — without special-casing the caller.

When a proposed feature conflicts with simplicity, complexity wins only when robustness or flexibility requires it.

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
- **No local imports inside test functions** — all `import` / `from … import` statements at module level; never inside a test method or helper function
- **No redundant naming** — test method names must not repeat the class name; in `TestFooBar` write `test_returns_value` not `test_foo_bar_returns_value` (see [CONTRIBUTING.md: Tests and quality assurance](.github/CONTRIBUTING.md#-tests-and-quality-assurance))
- **Scenario description required** — every non-trivial test method docstring must include a prose paragraph describing the real-world situation being tested; a one-line summary alone is not sufficient (see [Test Requirements](.github/CONTRIBUTING.md#-tests-and-quality-assurance))
- **Unification pattern**: when 3+ call sites share the same `(deprecated_in, remove_in[, num_warns])` combo, extract to `_DEPRS_CASE_<SLUG>_ARGS: dict[str, Any]` and splat with `**`. Hoist `_class_deprecation_*` shared instances to the top constants block. No trailing commas before `)` in test files. See [Unification pattern](.github/CONTRIBUTING.md#unification-pattern--shared-version-kwargs-and-hoisted-instances) for full rules.
- See [Test Organization](.github/CONTRIBUTING.md#test-organization) for details

### Documentation Site

- **README ≠ docs/index.md** — README is the PyPI page (do not copy it to docs); `docs/index.md` is a curated overview
- **Never add `cp README.md docs/index.md` to CI** — `docs/index.md` is tracked in git directly
- **After any API change**: update `README.md` AND the relevant `docs/guide/` page
- **New troubleshooting item**: add to `docs/troubleshooting.md` AND the FAQPage JSON-LD in `docs/overrides/main.html`
- **`docs/overrides/main.html`** is Jinja2 (prettier-excluded); do not put content files there
- **`docs/robots.txt`** — AI crawler access policy; add a `User-agent: <bot> / Allow: /` block when a new mainstream AI crawler is released. The comment referencing the `docs/llms.txt` URL must stay current.
- See [Documentation Site](.github/CONTRIBUTING.md#documentation-site) for the full consistency rules

## 🚫 Critical Constraints

### Never:

- Add runtime dependencies
- Commit sensitive information (`.env`, API keys)
- Use bare `except:` clauses
- Define deprecated wrappers inside test files
- Use `with warnings.catch_warnings(...)` in any `.md` documentation example in any form — neither `simplefilter("always")` for capturing nor `simplefilter("ignore", ...)` for suppressing; annotate the call with `# warns: FutureWarning` or `# warns: UserWarning` instead; output blocks show only return values
- Use bare `assert` statements in `.md` documentation examples (e.g. `assert pt.x == 1.0`, `assert isinstance(obj, MyClass)`) — use `print()` instead and follow with a `<details><summary>Output: <code>expression</code></summary>` block showing expected output
- Import a fictional package name in runnable `.md` examples — executable examples must import from actual test collection modules (`from tests import collection_deprecate`, `collection_misconfigured`, or `collection_chains`). For CI-template snippets that intentionally show a placeholder import, add `# phmdoctest:skip — CI template: replace my_package with your actual package` as the first line so phmdoctest skips execution
- Skip test coverage for new features or bug fixes
- Implement features without maintainer approval
- Start work without first reading config files and guidelines

### Always:

- **Anchor with repository context first** — read config files (`pyproject.toml`, `.pre-commit-config.yaml`) and guidelines before any work

- **Trust config files over documentation** — when mismatches occur, config files are the source of truth

- **Suggest documentation updates** when you find mismatches between docs and actual configuration

- **Update docs immediately after any structural change** — adding, moving, renaming, or deleting files/folders/modules must be followed by updating `.github/CONTRIBUTING.md` (project structure tree, Test Organisation table) and any other `*.md` that references affected paths or names. Do not wait to be asked.

- **Keep README and docs site in sync** — any API addition, rename, or behavior change must be reflected in `README.md` **and** the relevant `docs/guide/` page; new troubleshooting items go in `docs/troubleshooting.md` **and** the FAQPage JSON-LD in `docs/overrides/main.html`

- **Update `CHANGELOG.md` at PR-creation time** — once the PR number is known and the diff is stable, add or re-assess the entry under `[UnReleased]` (new features → `Added`, fixes → `Fixed`, behavior changes → `Changed`); always include `(#N)` PR link; skip docs-only, test-only, CI, and pure internal refactoring changes; do not write entries during iterative development commits — content may pivot before the PR is opened

- **Update inline comments when changing behavior** — after any logic change, scan changed files for comments describing that behavior; update stale ones (stale comments mislead more than none)

- **Keep AI-agent documentation in sync** — `docs/llms.txt` is a machine-readable contract read by AI agents before generating code; it must reflect actual behavior at all times. Apply this sync table on every relevant change:

  - **Public API behavior change** — affected module docstrings (`deprecation.py`, `audit.py`, `proxy.py`, `utils.py`) · inline comments in changed `src/` files · `README.md` Quick Start · relevant `docs/guide/*.md` topic page · `docs/llms.txt` § Agent Notes · `docs/troubleshooting.md` (add or update Q&A) · FAQPage JSON-LD in `docs/overrides/main.html`
  - **New deprecation pattern** — `docs/llms.txt` Decision Flowchart · relevant `docs/guide/*.md` topic page (functions, classes, properties, async, or advanced)
  - **New anti-pattern discovered** — `docs/llms.txt` § Anti-Patterns · relevant `docs/guide/*.md` topic page
  - **`TargetMode` enum value added or removed** — `docs/llms.txt` Critical Mental Model · Decision Flowchart · relevant `docs/guide/*.md` topic page
  - **New mainstream AI crawler released** — `docs/robots.txt` (new `User-agent: <bot> / Allow: /` block)

  `docs/llms.txt` is the highest-leverage surface for AI agents — a stale entry there produces wrong code at scale. `docs/robots.txt` is the access gateway — an unlisted bot gets the wildcard `Allow: /` but explicit entries signal intent to AI platforms.

- **Follow code example naming conventions** — human-facing docs (`docs/guide/*.md`, `README.md`) use domain-realistic story-telling names; AI-facing docs (`docs/llms.txt`, `docs/llms-full.txt`) use generic names (`old_func`/`new_func`, `old_arg`/`new_arg`); every paired example carries `# NEW API —` / `# DEPRECATED API —` orientation comments. See [Code Example Conventions](.github/CONTRIBUTING.md#code-example-conventions).

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
- **PR review guidelines** → [Contributing: Reviewing PRs](.github/CONTRIBUTING.md#reviewing-prs)
- **Security reporting** → [Security Policy](.github/SECURITY.md)
- **Community guidelines** → [Code of Conduct](.github/CODE_OF_CONDUCT.md)
- **Documentation site** → [Contributing: Documentation Site](.github/CONTRIBUTING.md#documentation-site)
