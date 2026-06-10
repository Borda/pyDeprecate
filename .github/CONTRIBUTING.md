# Contributing Guide

Thank you for your interest in contributing to pyDeprecate! We appreciate all contributions and welcome everyone, regardless of experience level. Your help makes this project better for everyone.

> [!TIP]
> **First time contributing to open source?** Check out [First Contributions](https://github.com/firstcontributions/first-contributions) for a beginner-friendly guide that walks you through the entire process.

> [!NOTE]
> **Configuration files are the source of truth.** If you notice this documentation contradicts actual configuration files (`pyproject.toml`, `.pre-commit-config.yaml`, etc.), please open an issue! The config files are always correct, and the documentation should be updated to match.

## 📖 Code of Conduct

Please read and follow our [Code of Conduct](CODE_OF_CONDUCT.md). We expect all contributors to be respectful, considerate, and help create a welcoming environment for everyone. This ensures our community remains inclusive and supportive for people from all backgrounds.

## 🎯 Ways to Contribute

There are many ways to contribute beyond writing code. Every contribution, no matter how small, makes a difference:

| Contribution            | Description                                                                                                                     |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| 🐛 Report bugs          | Found an issue? Let us know! Detailed bug reports help us fix problems faster.                                                  |
| 🔧 Fix bugs             | Implement fixes for reported issues. Great way to start contributing!                                                           |
| 💡 Suggest improvements | Propose enhancements, optimizations, or better approaches to existing functionality                                             |
| ✨ Build features       | Implement new features after getting maintainer approval                                                                        |
| 📚 Improve docs         | Fix typos, clarify explanations, or add examples. Good documentation makes the project accessible to more people.               |
| 👀 Review PRs           | Provide feedback on pull requests. Code reviews help maintain quality and catch potential issues early.                         |
| 💬 Answer questions     | Help others in discussions and issues. Your knowledge can help someone overcome a problem.                                      |
| ⭐ Spread the word      | Star the repo, share it with others, or write about your experience. This helps the project grow and attract more contributors. |

## 💭 Before You Start

### Read First

Taking time to understand the project first helps you contribute more effectively:

- **Documentation** — Learn how the project works and its key concepts. This prevents misunderstandings and ensures your contributions align with project goals.
- **Existing issues** — Your idea might already be discussed or even implemented. Searching first saves you time and effort.
- **Codebase** — Familiarize yourself with the code structure and style. This makes it easier to make changes that fit well with the existing code.

### Ask Questions

Don't hesitate to ask! Open an issue or use discussions to:

- Clarify project goals and scope — Make sure your contributions are aligned with the project's direction
- Understand implementation details — Get help with specific technical questions
- Get guidance on where to contribute — Find areas where your skills and interests can make the biggest impact

> [!TIP]
> Asking questions shows you're thoughtful and helps everyone learn together.

## 🐛 Reporting Bugs

When reporting a bug, providing clear and detailed information helps us fix it faster. Here's how:

1. **Search existing issues** — The bug might already be reported and being worked on.
2. **Create a minimal reproduction** — Provide a simple example that demonstrates the bug. This makes it easier for us to reproduce and fix the issue.
3. **Include environment details** — Tell us your operating system, Python version, and pyDeprecate version.
4. **Describe expected vs actual behavior** — Be specific about what you expected to happen and what actually occurred.

## 🔧 Fixing Bugs

Bug fixing is a great way to contribute! Here's how to get started:

1. **Find a bug to fix** — Look for issues labeled `bug` or `help wanted` in the issue tracker.
   - Check if the issue is already assigned to someone
   - If it's assigned but has no activity for a while, comment to ask if you can take it over
2. **Understand the problem** — Read the issue carefully and try to reproduce the bug.
3. **Write a failing test first (TDD)** — Before fixing the bug, write a test that reproduces the issue. This ensures:
   - You understand the problem correctly
   - The fix actually resolves the issue
   - The bug won't silently reappear in the future
4. **Implement the fix** — Now that you have a failing test, implement the fix to make it pass. Keep your changes focused on the specific issue.
5. **Verify the test passes** — Run the test suite to ensure your fix resolves the issue without breaking anything else.
6. **Submit a PR** — Create a pull request with your fix, linking to the issue it addresses.

> [!IMPORTANT]
> **Test-Driven Development (TDD) for bugs:** Always reproduce the bug in a test *before* implementing the fix. This is the most reliable way to ensure the bug is actually fixed and won't regress.

## 💡 Suggesting Improvements

Improvements are enhancements to existing functionality that make the project better. They could be:

- Performance optimizations
- Code refactoring for better maintainability
- User experience enhancements
- Documentation improvements
- Process or workflow improvements

Here's how to suggest and implement improvements:

1. **Open an issue** — Describe the improvement you're suggesting. Explain the current situation, why it needs improvement, and your proposed solution.
2. **Provide context** — Include examples, use cases, or data to support your suggestion.
3. **Discuss and refine** — Engage in the issue discussion to clarify requirements and explore alternatives.
4. **Get feedback** — Wait for input from maintainers and the community. For small improvements, you may get rapid approval.
5. **Implement if approved** — If your suggestion is accepted, follow the standard development process.

## ✨ Building Features with Consensus

**Before implementing any new feature:**

1. **Open an issue first** — Clearly describe your idea, use case, and how it benefits the project.
   - Check if the issue is already assigned to someone
   - If it's assigned but has no activity for a while, comment to ask if you can take it over
2. **Wait for maintainer approval** — Look for a **👍 "go-ahead" reaction** or explicit approval from a maintainer.
3. **Discuss and refine** — Engage in the issue discussion to clarify requirements and explore alternatives.
4. **Get consensus** — Ensure there's general agreement from the community before starting implementation.

> [!CAUTION]
> Always get maintainer approval before implementing new features! This ensures your work aligns with project direction and won't be wasted effort. Features implemented without prior approval may be rejected.

**When you have approval:**

1. **Implement the feature** — Build the feature following the project's coding style and guidelines.
2. **Add comprehensive tests** — Every new feature **must** have tests covering:
   - **Happy path** — The feature works correctly with valid inputs
   - **Failure path** — The feature handles errors gracefully and raises appropriate exceptions
   - **Edge cases** — None values, empty inputs, boundary conditions, circular chains, missing arguments
3. **Update documentation** — Document how to use the new feature.
4. **Submit a PR** — Create a pull request, linking to the approved issue.

> [!IMPORTANT]
> **Test coverage is mandatory for new features.** Untested features will not be merged. Tests ensure reliability and prevent regressions.

## 📬 Pull Requests

### Before Opening a PR

Complete this checklist before opening a pull request to ensure quality and smooth review:

- [ ] Followed existing code style (pre-commit hooks enforce this automatically)
- [ ] Added tests for new functionality (happy path, failure path, edge cases)
- [ ] Ran tests locally and they pass (`pytest .`)
- [ ] Updated documentation if needed (README, docs/guide pages, docstrings, inline comments)
- [ ] Updated `docs/llms.txt` if public API behavior, patterns, or anti-patterns changed
- [ ] Self-reviewed my code
- [ ] Linked to related issue(s)

### Linking Issues

**Every PR should reference an issue.** This provides context and helps track progress. Use keywords like:

- `Fixes #123` — Closes the issue when PR is merged
- `Relates to #456` — Links without auto-closing

If no issue exists, open one first to discuss the change before implementing it.

### Keep PRs Focused

- **One PR = one logical change** — This makes your PR easier to review and understand
- **Smaller PRs are easier to review** — Large PRs can be overwhelming and take longer to merge
- **Split large changes into multiple PRs** — Break complex features into smaller, manageable pieces

### Reviewing PRs

When reviewing pull requests, provide structured, actionable feedback:

**Overall Assessment:**

- 🟢 **Approve** — Ready to merge
- 🟡 **Minor Suggestions** — Improvements recommended but not blocking
- 🟠 **Request Changes** — Significant issues must be addressed
- 🔴 **Block** — Critical issues require major rework

**Review Checklist:**

- [ ] Clear description and linked issue
- [ ] Tests cover happy path, failure cases, and edge cases
- [ ] Code quality (correctness, idioms, type hints)
- [ ] Documentation (docstrings for public APIs)
- [ ] No breaking changes or runtime dependencies
- [ ] CI checks pass

**Provide Actionable Feedback:**

- Explain **why** something is a problem, not just **what**
- Distinguish blocking issues from nice-to-haves
- Use GitHub's suggestion format for specific code improvements
- Acknowledge good work and be pragmatic

## ✅ Tests and Quality Assurance

Tests and quality improvements are **always welcome**! These contributions are highly valuable because they:

- Improve project stability — Well-tested code is more reliable for everyone
- Help catch future regressions — Tests prevent issues from reoccurring
- Reduce maintainer burden — Comprehensive tests require less ongoing debugging

### Running Tests

```bash
# Install in development mode
pip install -e . "pre-commit" -r tests/requirements.txt
pre-commit install

# Run linting and formatting first (optional - runs automatically on commit)
pre-commit run --all-files

# Generate/extract README examples as tests (when updating README examples)
phmdoctest README.md --outfile tests/integration/test_readme.py

# Generate tests from README and all docs pages
make docs-tests

# Run the full test suite (including doctests if configured in pytest)
pytest .
```

> [!TIP]
> Pre-commit hooks run **automatically** on every commit, handling all linting and formatting (ruff, mypy). You only need to run `pre-commit run --all-files` manually if you want to check before committing.

> [!NOTE]
> When updating code examples in README.md, use `phmdoctest` to extract them as runnable tests. This ensures examples stay accurate and working as the codebase evolves. Code blocks paired with an output block must produce exactly that output when executed, which sometimes requires mocking external state (e.g. `unittest.mock.patch`); illustrative patterns that can't run standalone (like CI fixtures) are wrapped in nested functions marked `# Caution- no assertions.` so phmdoctest skips their execution.

## 💎 Quality Expectations

> [!IMPORTANT]
> **Always do your best — that's the essential spirit of OSS contributions.**

We value all levels of contribution and want to encourage everyone, regardless of skill level or time available. What matters is being reasonable and meaningful about what you can deliver:

- **Start small and iterate** — Better to do smaller tasks piece by piece than take too much and leave it unfinished. Small contributions add up over time.
- **Break work into steps** — This allows others to take over and continue if needed. It also makes your work more approachable for reviewers.
- **Avoid abandoned PRs** — Forked PRs are difficult to carry over except by maintainers, creating significant burden. If you can't complete a PR, let us know.
- **Be meaningful and reasonable** — Contribute what you can realistically complete. Even small improvements make a difference.

We don't expect perfection. We expect genuine effort. If you're unsure about something, ask! The community is here to help.

## 🌿 Branch Naming Convention

Follow this pattern for branch names to keep the repository organized:

```
{type}/{issue-number}-description
```

**Types:**

- `fix/` — Bug fixes (e.g., `fix/123-deprecation-warning-crash`)
- `feat/` — New features (e.g., `feat/45-add-class-deprecation`)
- `docs/` — Documentation changes (e.g., `docs/update-readme-examples`)
- `refactor/` — Code refactoring (e.g., `refactor/simplify-validation`)
- `test/` — Test additions or improvements (e.g., `test/edge-cases-for-chains`)
- `chore/` — Maintenance tasks (e.g., `chore/update-dependencies`)

> [!TIP]
> Always include the issue number when one exists. If there's no issue, use a descriptive name: `fix/typo-in-readme`

## 🚀 Quick Start

```bash
# 1. Fork the repository

# 2. Clone your fork
git clone https://github.com/YOUR-USERNAME/pyDeprecate.git
cd pyDeprecate

# 3. Create a feature branch (following naming convention)
git checkout -b fix/123-your-bug-description
# or: git checkout -b feat/456-your-feature-description

# 4. Install in development mode
pip install -e . "pre-commit" -r tests/requirements.txt
pre-commit install

# 5. Make your changes

# 6. Run linting & tests
pre-commit run --all-files  # Run linter first (optional - runs on commit anyway)
pytest tests/  # Run test suite

# 7. Commit your changes (pre-commit hooks run automatically)
git add -A        # Stage all modified and new files
git commit -m "Add amazing feature"  # Pre-commit runs linting/formatting automatically

# 8. Push to your fork
git push origin fix/123-your-bug-description

# 9. Open a Pull Request
```

## 📋 Coding Standards

### Style & Formatting

- Follow [PEP 8](https://pep8.org/) style guidelines — enforced automatically by `ruff` via pre-commit hooks
- Write clear, descriptive docstrings (Google-style convention) for all public **and** private functions, methods, and classes:
  - **Private functions** (name starts with `_`): minimum one-line summary — callers and reviewers must be able to understand the function contract without reading the body
  - **Public functions**: full Google-style sections — `Args:` for every parameter (name, type if not already in the signature, what it means), `Returns:` describing the return value and its type when non-obvious, `Raises:` listing every exception the function intentionally raises with the condition that triggers it; omit a section only when it genuinely does not apply (e.g. no parameters, returns `None`, raises nothing)
  - Include an `Examples:` section for non-trivial behavior — omit only when an example literally cannot run (e.g., requires an external service or produces non-deterministic output); showing the call without output (`>>> result = fn(x)` with no output line) is fine and still counts as a runnable doctest; `# doctest: +SKIP` and `# phmdoctest:skip` are highly discouraged — each instance degrades testability; if you must use one, explain why in a comment on the same line
  - **If a function has no dedicated test**, it must have at least one runnable `Examples:` doctest — the doctest is the minimum proof that the function works as documented
- In docstrings, always reference project symbols with their full import path using Sphinx cross-reference syntax (e.g., `` :func:`~deprecate.deprecation.deprecated` `` rather than just `` :func:`deprecated` ``); standard library symbols (e.g., `FutureWarning`) do not need a module prefix
- Use **MkDocs admonition syntax** for docstring notices in `src/deprecate/` by default — `!!! note`, `!!! warning "Deprecated in X.Y"`, etc. Do not use RST directives (`.. note::`, `.. deprecated::`) in package source docstrings unless the module specifically exists to integrate with Sphinx/RST docstrings (for example, `src/deprecate/docstring/sphinx_ext.py` and `src/deprecate/docstring/griffe_ext.py`). For demos, `demo-docs/sphinx/` follows RST conventions and `demo-docs/mkdocs/` follows MkDocs conventions — each demo matches its own renderer.
- Keep functions focused and modular — a function should do one thing; if it needs a long comment to explain what it does, it probably needs to be split
- Add type hints to all function signatures, including return types
- Align type hint syntax with the **minimum supported Python version** (check `python_requires` in `setup.py`)
- If unsure about syntax compatibility, consult the official Python documentation for that version or search for the relevant PEP
- Write meaningful variable and function names — prefer `expired_callables` over `lst`, `source_func` over `f`
- Write Python code that is readable on its own — good names and structure are the primary documentation. Add an inline comment only when it carries context the code cannot: **why** a non-obvious choice was made, a hidden constraint, a subtle invariant, or a workaround for a specific external behaviour. One short line; never explain what the code does
- When changing existing behavior, scan changed files for stale inline comments — update or remove them (stale comments mislead more than none)
- No bare `except:` — always catch specific exceptions (e.g., `except ValueError:`, `except ImportError:`)

> [!TIP]
> **All linting and formatting is automatically handled by pre-commit hooks** on every commit. Tools include `ruff` (formatting/linting) and `mypy` (type checking). Configs live in `pyproject.toml` and `.pre-commit-config.yaml`. The hooks will prevent commits with style violations.

### Architecture Constraints

- **Zero runtime dependencies** — pyDeprecate has no runtime dependencies. Do not add any.
- **Fast imports / low overhead** — avoid expensive computations or premature imports in module-level code or wrapper setup.
- **Circular imports** — when editing `src/deprecate/`, verify new imports don't create cycles. Use `if TYPE_CHECKING:` blocks for type-only imports.
- **Deprecation chains** — if modifying chain validation logic, handle infinite loops (A deprecates B, B deprecates A) gracefully without crashing.

### Project Structure

Understanding the codebase layout helps you navigate and contribute effectively:

```
pyDeprecate/
├── src/deprecate/              # Core library code
│   ├── __about__.py            # Version and metadata
│   ├── __init__.py             # Public API exports
│   ├── docstring/              # Docstring utilities subpackage
│   │   ├── inject.py           # Runtime injection helpers: TEMPLATE_DOC_*, _update_docstring_*()
│   │   ├── griffe_ext.py       # Griffe extension for mkdocstrings / MkDocs (beta)
│   │   └── sphinx_ext.py       # Sphinx autodoc extension (beta)
│   ├── _types.py               # Shared type definitions: DeprecationConfig, _ProxyConfig
│   ├── deprecation.py          # @deprecated decorator and warning logic
│   ├── audit.py                # Audit tools: validate_*, find_deprecation_wrappers()
│   ├── proxy.py                # Instance/class proxy: deprecated_class(), deprecated_instance()
│   └── utils.py                # Low-level helpers: void(), no_warning_call()
├── tests/                      # Test suite
│   ├── collection_targets.py       # Target functions (new implementations)
│   ├── collection_deprecate.py     # Deprecated wrappers (@deprecated)
│   ├── collection_misconfigured.py # Invalid configs for validation
│   ├── collection_chains.py        # Chained deprecation patterns
│   ├── collection_docstrings.py    # Fixtures for update_docstring=True behaviour
│   ├── integration/                # End-to-end tests via the public API
│   └── unittests/                  # Focused tests for private/internal helpers
├── .github/
│   ├── workflows/              # CI/CD pipelines
│   └── *.md                    # Documentation and guidelines
├── pyproject.toml              # Project config (ruff, mypy, pytest)
└── setup.py                    # Package setup
```

**Circular import prevention example:**

When editing `src/deprecate/`, use `if TYPE_CHECKING:` blocks to avoid circular dependencies:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deprecate.deprecation import SomeType


def my_function(arg: SomeType) -> None:
    # Implementation here
    pass
```

### Test Organization

Tests live in `tests/` and follow a **three-layer separation**:

| File/Folder                   | Purpose                                                                                                                        |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `collection_targets.py`       | Target functions and classes (the "new" implementations that deprecated code forwards to)                                      |
| `collection_deprecate.py`     | Deprecated wrappers that use `@deprecated(...)` to forward to targets                                                          |
| `collection_misconfigured.py` | Intentionally invalid/ineffective deprecation configurations for validation testing                                            |
| `collection_chains.py`        | Multi-hop deprecation chains (deprecated → deprecated → target) for chain-detection tests                                      |
| `collection_docstrings.py`    | Fixtures for `update_docstring=True` behaviour — new and deprecated callables whose generated docstrings are compared in tests |
| `integration/`                | End-to-end tests exercising the **public API** via the collection modules                                                      |
| `unittests/`                  | Focused tests for **private/internal helpers**, each file mirroring one source module                                          |

**`integration/`** — Each file covers one area of the public surface (functions, classes, audit, utils, docstrings, README examples). Tests call `@deprecated`-decorated code as a user would and assert on warnings, return values, and forwarded types. `test_readme.py` is generated by `phmdoctest` from README code blocks.

> [!IMPORTANT]
> **README examples must be runnable.** Every Python code block in `README.md` is extracted by `phmdoctest` and executed as a test. Follow these rules when writing README examples:
>
> - Use `print()` for values you want to verify, paired with a `<details><summary>Output: <code>expression</code></summary>` block immediately after the code block. The `<summary>` label shows the **expression** being evaluated (e.g. `cfg.timeout`), not the `print()` wrapper.
> - Only import and use `pytest.raises` when an example intentionally raises an exception — this prevents the extracted test from crashing. Do **not** use `pytest.warns`; deprecation warnings are emitted to stderr and do not cause test failures.
> - **Never use `with warnings.catch_warnings(record=True) as w: warnings.simplefilter("always")`** in any `.md` code block (README, docs, docstrings). Use direct calls annotated with `# warns: FutureWarning` or `# silent` instead. Output blocks show only return values — not warning counts or `w[0].category.__name__`.
> - Do **not** use bare `assert` statements — they crash the test with an unhelpful `AssertionError` if the value changes.
> - Regenerate `test_readme.py` after any README change: `phmdoctest README.md --outfile tests/integration/test_readme.py`

> **Docs examples must use `print()` + output blocks — no `assert`.** For all `docs/**/*.md` blocks that execute code:
>
> - Use `print()` to display values; follow immediately with a `<details><summary>Output: <code>expression</code></summary>` block showing expected output.
> - Do **not** use bare `assert` statements (e.g. `assert pt.x == 1.0`, `assert isinstance(obj, MyClass)`) — use `print()` instead so the value is visible rather than crashing with `AssertionError`.
> - Avoid placeholders that do not validate behavior.

> [!NOTE]
> **Some docs examples use collection modules as fixtures and report hardcoded counts.** `docs/guide/audit.md` embeds expected output from scanning `tests.collection_misconfigured` with hardcoded numbers (wrappers scanned, empty mappings, etc.). When you add or remove entries from any `collection_*.py` module:
>
> - Update the expected counts in the relevant `docs/guide/*.md` code block output.
> - Regenerate the corresponding test file: `phmdoctest docs/guide/audit.md -s "phmdoctest:skip" --outfile tests/docs/test_guide_audit.py`
>
> Failing to do this causes `tests/docs/test_guide_audit.py` to fail in CI.

**`unittests/`** — Tests import private symbols directly (e.g. `_raise_warn`, `_parse_version`) and use mocking/monkeypatching to stay isolated from external state. Each file mirrors a source module (`deprecation.py`, `docstring/inject.py`, `audit.py`, `utils.py`).

> [!IMPORTANT]
> **Three-layer rule**: do not define target objects or deprecated wrappers directly inside `test_*.py` files. Place targets in `collection_targets.py`, deprecated wrappers in `collection_deprecate.py`, then import them in tests. This includes class definitions — do not define classes inside test functions; define them in the appropriate collection module instead.

> [!NOTE]
> **Exception — one-off inline fixtures:** inline fixtures are allowed inside a test function when all of the following hold:
>
> - **Single use** — used by exactly one test and not reused elsewhere
> - **Non-representational** — does not model real API migration behavior or a named deprecation pattern
> - **Purely mechanical** — exists only to drive a protocol or edge case (for example a malformed `remove_in` string, or a tiny local class used solely for `isinstance`/`issubclass` behavior)
>
> If in doubt, extract. Move to a collection module when the fixture is shared across tests, models real migration behavior, or represents a reusable deprecated wrapper or target.

**Fixture naming convention in `collection_deprecate.py`:**

The prefix encodes whether the fixture exists in one form or two:

- **Single form** (only one way the deprecation is applied — no decorator/wrapper comparison needed): use `depr_<name>` for functions and `Deprecated<Name>` for classes.
- **Both forms** (decorator and wrapper, used together in a parametrized test): reflect the form in the name:

| Form                         | Functions/methods  | Classes (PascalCase) |
| ---------------------------- | ------------------ | -------------------- |
| Decorator (`@deprecated...`) | `decorated_<name>` | `Decorated<Name>`    |
| Wrapper (assignment form)    | `wrapped_<name>`   | `Wrapped<Name>`      |

Examples:

- `DeprecatedEnum` — single form, no wrapper counterpart
- `decorated_sum_warn_only` / `wrapped_sum_warn_only` — paired for parametrize
- `DecoratedEnum` / `WrappedEnum` — paired class fixtures for the same parametrized comparison

When adding a parametrized test that covers both forms, always add both fixtures and share the same `deprecated(...)` instance to guarantee identical configuration:

```python
from deprecate import deprecated, void


# original_* is declared first — _deprecation_* refers to it immediately after.
def original_sum_warn_only(a: int, b: int = 5) -> int:
    """Source function for the wrapper form."""
    return void(a, b)


# The _deprecation_* variable is the deprecation tool (the decorator instance),
# NOT a deprecated callable — that distinction is why it's named _deprecation_*
# rather than _depr_* (which would imply the thing being deprecated).
_deprecation_warn_only = deprecated(target=None, deprecated_in="0.2", remove_in="0.3")


@_deprecation_warn_only
def decorated_sum_warn_only(a: int, b: int = 5) -> int:
    """..."""
    return void(a, b)


wrapped_sum_warn_only = _deprecation_warn_only(original_sum_warn_only)
```

The same pattern applies to `deprecated_class()` pairs — define `_class_deprecation_<name> = deprecated_class(...)` once and reuse it for both `Wrapped<Name>` and `@Decorated<Name>`:

```python
from deprecate import deprecated_class


class NewWidget:
    """Canonical replacement class."""

    size: int = 42


_class_deprecation_widget = deprecated_class(target=NewWidget, deprecated_in="1.0", remove_in="2.0")


@_class_deprecation_widget
class DecoratedWidget:
    """Decorator-form: same config as WrappedWidget."""

    size: int = 42


class _OriginalWidget:
    """Source class for the wrapper form."""

    size: int = 42


WrappedWidget = _class_deprecation_widget(_OriginalWidget)
```

> **Rule**: when a `Decorated<Name>` / `Wrapped<Name>` pair exists, both **must** share a single `_class_deprecation_<name>` instance. Duplicating the `deprecated_class(...)` kwargs is a bug — a silent config drift will cause the parametrized test to compare two different deprecations instead of the same one in two application forms.

**Unification pattern — shared version kwargs and hoisted instances:**

When three or more `@deprecated(...)` or `@deprecated_class(...)` call sites share the same `(deprecated_in, remove_in[, num_warns])` combination, extract the repeated kwargs into a named `dict` constant and splat it at each call site. This eliminates silent version drift and makes bulk version-bump changes a one-line edit.

Naming convention: `_DEPRS_CASE_<SLUG>_ARGS` where `<SLUG>` is an ALL_CAPS descriptor of the usage context (e.g. `PROXY_LEGACY`, `TGT_MODE`, `STD_INF`). Type-annotate as `dict[str, Any]` to avoid mypy narrowing complaints on heterogeneous values.

```python
from typing import Any

# Reusable deprecation-version kwarg groups.
_DEPRS_CASE_PROXY_LEGACY_ARGS: dict[str, Any] = {"deprecated_in": "0.1", "remove_in": "0.2", "num_warns": -1}
_DEPRS_CASE_TGT_MODE_ARGS: dict[str, Any] = {"deprecated_in": "1.2", "remove_in": "2.0"}


@deprecated_class(**_DEPRS_CASE_PROXY_LEGACY_ARGS)
class DeprecatedEnum(Enum): ...


@deprecated_class(target=NewEnum, **_DEPRS_CASE_PROXY_LEGACY_ARGS)
class RedirectedEnum(Enum): ...


@deprecated(**_DEPRS_CASE_TGT_MODE_ARGS, target=TargetMode.NOTIFY, num_warns=-1)
def depr_class_whole_mode_warns_on_call(x: int) -> int: ...
```

**Rules:**

- Minimum **3 call sites** before extracting — two occurrences stay inline.
- Only group call sites where **all three** of `deprecated_in`, `remove_in`, and `num_warns` are identical (or all three omit `num_warns`). Sites that differ on any key stay inline.
- `_class_deprecation_*` shared instances (see above) and `_DEPRS_CASE_*` constants both go in the **constants block at the top of `collection_deprecate.py`**, right after `_SHORT_MSG_FUNC` / `_SHORT_MSG_ARGS`. This makes version metadata scannable in one place.
- When adding a new fixture group that would form a third call site for an existing tuple, extract rather than inline.


**Docstrings in test collections:**

Functions in `collection_deprecate.py`, `collection_misconfigured.py`, `collection_chains.py`, and `collection_docstrings.py` must have Google-style docstrings with a **user-first focus** — describe the real-world scenario a user would encounter, not just the technical configuration. This keeps tests grounded in actual use cases and helps contributors understand *why* each deprecation pattern exists.

Use a one-line summary of the deprecation pattern, then an `Examples:` section describing the user scenario:

```python
from deprecate import deprecated


@deprecated(target=None, deprecated_in="0.2", remove_in="0.3")
def decorated_sum_warn_only(a: int, b: int = 5) -> int:
    """Warning-only deprecation with no forwarding.

    Examples:
        The function is going away but has no replacement yet. The user gets
        warned, but the original body still executes (`target=None`).
    """
```

**Test requirements:**

> [!IMPORTANT]
> **All new features and bug fixes must include tests.** This is non-negotiable.

- Every new function or behavior change must have accompanying tests.
- For every new utility or feature, include tests for:
  1. Happy path — expected correct behavior with valid inputs
  2. Failure path — expected errors are raised with appropriate messages
  3. Edge cases — None types, empty inputs, circular chains, missing arguments, boundary conditions
- **Group related tests in classes** — use test classes when you have multiple related tests or need shared fixtures.
- **Avoid redundant naming** — don't repeat class context in test method names (e.g., in `TestDeprecatedWrapper`, use `test_shows_warning` not `test_deprecated_wrapper_shows_warning`).
- **Use fixtures for independence** — use pytest fixtures to reset state between tests. Add `autouse=True` fixtures when a class needs per-test reset.
- **One behavior per test** — each test method should verify one specific aspect.
- **Prefer parametrization for repetitive shapes** — when the setup/assertion flow is the same and only inputs/expected outputs differ, use `pytest.mark.parametrize(...)` to reduce duplication while keeping one behavioral intent per case.
- **Assertions on warnings:** Use `pytest.warns(FutureWarning|DeprecationWarning)` to verify deprecation warnings are emitted correctly.
- **Scenario description in docstrings** — every non-trivial test method must include a prose paragraph after the one-line summary that describes the real-world situation being tested. A one-line summary alone is not sufficient for complex tests.

```python
def test_warns_on_read(self) -> None:
    """FutureWarning fires on property read access.

    A user accesses a deprecated property on an existing object and
    expects a FutureWarning with the original value still returned.
    """
```

**For bug fixes:**

- Use **Test-Driven Development (TDD)**: Write a failing test that reproduces the bug first, then implement the fix to make it pass. This ensures the bug is truly fixed and won't regress.

Example:

```python
import pytest


class TestMyFeature:
    """Test suite for my feature."""

    @pytest.fixture(autouse=True)
    def reset_state(self) -> None:
        """Reset state before each test."""
        # Reset any shared state

    def test_basic_functionality(self) -> None:
        """Test that feature works correctly."""
        # Test one specific behavior
```

> [!NOTE]
> Test classes are most beneficial when you have multiple tests or need fixtures. For single standalone tests, a simple test function is sufficient.

### Common Patterns

<details>
<summary>Adding a new deprecation wrapper (in test collections)</summary>

```python
# tests/collection_targets.py — the new implementation
def new_implementation(x: int) -> int:
    """New implementation."""
    return x * 2


# tests/collection_deprecate.py — the deprecated wrapper
from deprecate import deprecated
from tests.collection_targets import new_implementation


@deprecated(target=new_implementation, deprecated_in="1.0", remove_in="2.0")
def old_implementation(x: int) -> int:
    """Deprecated: use new_implementation instead."""


# tests/integration/test_functions.py — the test
import pytest
from tests.collection_deprecate import old_implementation


def test_deprecation_warning() -> None:
    with pytest.warns(FutureWarning, match="was deprecated"):
        assert old_implementation(5) == 10
```

</details>

<details>
<summary>Argument renaming</summary>

```python
from deprecate import deprecated


@deprecated(target=True, deprecated_in="1.0", remove_in="2.0", args_mapping={"old_param": "new_param"})
def my_func(old_param: int = 0, new_param: int = 0) -> int:
    """Function with renamed parameter."""
    return new_param
```

</details>

<details>
<summary>Testing without warnings</summary>

```python
from deprecate import no_warning_call


def test_without_warning() -> None:
    with no_warning_call(FutureWarning):
        # ... test code that should not emit warnings
        pass
```

</details>

## 📝 Documentation Site

The project ships two separate documentation surfaces:

| Surface            | File                        | Purpose                                                             |
| ------------------ | --------------------------- | ------------------------------------------------------------------- |
| PyPI cover page    | `README.md`                 | Install instructions, full API reference — do **not** prune         |
| Docs site home     | `docs/index.md`             | Curated overview — links to topic pages; **not** a README copy      |
| Getting started    | `docs/getting-started.md`   | Install + quick-start                                               |
| Use cases          | `docs/guide/use-cases.md`   | Patterns extracted from real usage                                  |
| void() helper      | `docs/guide/void-helper.md` | void() stub helper reference                                        |
| Audit tools        | `docs/guide/audit.md`       | validate\_\* and find_deprecation_wrappers()                        |
| Troubleshooting    | `docs/troubleshooting.md`   | Q&A; also drives FAQPage JSON-LD                                    |
| Theme override     | `docs/overrides/main.html`  | Jinja2 template — OG tags + JSON-LD per page; **prettier-excluded** |
| AI discoverability | `docs/llms.txt`             | Spec-compliant link directory for AI crawlers                       |
| AI crawler policy  | `docs/robots.txt`           | Crawler allow/block rules; comment links to `docs/llms.txt`         |

### Local Build

```bash
# Install docs dependencies (separate from test requirements)
pip install -r docs/requirements.txt

make docs-build   # one-shot build with strict mode (fails on warnings)
make docs-serve   # live-reload preview at http://127.0.0.1:8000
make docs-tests   # regenerate tests/integration/test_readme.py and tests/docs/test_*.py
```

> [!NOTE]
> Every Python code block in a docs page is extracted by `phmdoctest` and executed as a test. After updating any `docs/**/*.md` code example, regenerate the corresponding `tests/docs/test_<name>.py` file using the commands above. The generated files are gitignored — CI regenerates them automatically before running pytest.

> [!NOTE]
> The `git-revision-date-localized` plugin requires a full git history. Run `git fetch --unshallow` (or use `fetch-depth: 0` in CI) if revision dates show as today for old files.

### Consistency Rules

When making changes, keep all surfaces in sync:

1. **New or renamed public API symbol** — update `README.md` (API reference) **and** the relevant `docs/guide/` page.
2. **New use-case pattern** — add an entry to `docs/guide/use-cases.md`.
3. **New troubleshooting item** — add a Q&A block to `docs/troubleshooting.md` **and** a matching `Question`/`Answer` pair to the `FAQPage` JSON-LD in `docs/overrides/main.html`.
4. **README stays authoritative for install and full API** — `docs/index.md` is a curated overview that links out, never a verbatim copy.
5. **Never copy README → docs in CI** — the build workflow (`build-docs.yml`) does not copy `README.md`; tracked `docs/index.md` is used directly.
6. **AI crawler policy** — when a new mainstream AI crawler is released, add a `User-agent: <bot> / Allow: /` pair to `docs/robots.txt`. The comment line referencing `docs/llms.txt` must stay current if the llms.txt URL changes.

### Keeping AI-agent documentation in sync

`docs/llms.txt` is a machine-readable contract. AI coding assistants and agent frameworks fetch it before generating any pyDeprecate code. An inaccuracy there propagates into every AI-generated snippet at scale.

**What `docs/llms.txt` contains:** package facts, links to human-facing docs, and Agent Notes (critical mental model, anti-patterns with WRONG/CORRECT pairs, decision flowchart for choosing the right API).

**The comprehensive sync rule:** these five surfaces must always agree: public-API module docstrings (`deprecation.py`, `audit.py`, `proxy.py`, `utils.py`) ↔ inline comments in changed `src/` files ↔ `README.md` ↔ `docs/guide/use-cases.md` ↔ `docs/llms.txt`. Additionally update `docs/robots.txt` when AI crawler policy changes.

| When you change...                                                          | Also update...                                                                                                                                                                                                    |
| --------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Public API behavior (parameter meaning, forwarding semantics, default mode) | affected module docstrings (`deprecation.py`, `audit.py`, `proxy.py`, `utils.py`) · inline comments in changed `src/` files · `README.md` Quick Start · `docs/guide/use-cases.md` · `docs/llms.txt` § Agent Notes |
| A new supported deprecation pattern                                         | `docs/guide/use-cases.md` (new section) · `docs/llms.txt` Decision Flowchart                                                                                                                                      |
| A newly discovered anti-pattern                                             | `docs/llms.txt` § Anti-Patterns · `docs/guide/use-cases.md` (danger admonition)                                                                                                                                   |
| A `TargetMode` value (added, renamed, removed)                              | `docs/llms.txt` Critical Mental Model and Decision Flowchart · `docs/guide/use-cases.md` · `README.md`                                                                                                            |
| A new mainstream AI crawler is released                                     | `docs/robots.txt` (new `User-agent: <bot> / Allow: /` block)                                                                                                                                                      |

> [!IMPORTANT]
> `docs/llms.txt` is the highest-leverage surface for AI agents. Update it in the same commit as the code change — never as a follow-up.

### Template Override

`docs/overrides/main.html` is a Jinja2 template (MkDocs Material `custom_dir`). It is excluded from prettier (`^docs/overrides/.*\.html$` in `.pre-commit-config.yaml`) because prettier corrupts Jinja2 syntax. Do not put Markdown content files in `docs/overrides/` — that directory is excluded from MkDocs page output via `exclude_docs` in `mkdocs.yml`.

## 📄 License

By contributing, you agree that your contributions will be licensed under the same license as the project (see [LICENSE](../LICENSE) file).

______________________________________________________________________

<div align="center">

**Questions about security?** See our [Security Policy](SECURITY.md) for reporting vulnerabilities.

Made with 💙 by pyDeprecate contributors.

</div>
