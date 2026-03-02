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
- [ ] Updated documentation if needed
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
- Write clear, descriptive docstrings (Google-style convention) for all public functions, methods, and classes; include an `Example:` section for non-trivial behavior
- In docstrings, always reference project symbols with their full import path using Sphinx cross-reference syntax (e.g., `:func:`~deprecate.deprecation.deprecated``  rather than just `:func:`deprecated ``); standard library symbols (e.g., `FutureWarning`) do not need a module prefix
- Keep functions focused and modular — a function should do one thing; if it needs a long comment to explain what it does, it probably needs to be split
- Add type hints to all function signatures, including return types
- Align type hint syntax with the **minimum supported Python version** (check `python_requires` in `setup.py`)
- If unsure about syntax compatibility, consult the official Python documentation for that version or search for the relevant PEP
- Write meaningful variable and function names — prefer `expired_callables` over `lst`, `source_func` over `f`
- Add comments only where the logic is not self-evident — explain **why**, not __what__
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
│   ├── deprecation.py          # @deprecated decorator and warning logic
│   ├── audit.py                # Audit tools: validate_*, find_deprecated_callables()
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
> - Use `print()` for values you want to verify, paired with a `<details><summary>Output: ...</summary>` block immediately after the code block.
> - Only import and use `pytest.raises` when an example intentionally raises an exception — this prevents the extracted test from crashing. Do **not** use `pytest.warns`; deprecation warnings are emitted to stderr and do not cause test failures.
> - Do **not** use bare `assert` statements — they crash the test with an unhelpful `AssertionError` if the value changes.
> - Regenerate `test_readme.py` after any README change: `phmdoctest README.md --outfile tests/integration/test_readme.py`

**`unittests/`** — Tests import private symbols directly (e.g. `_raise_warn`, `_parse_version`) and use mocking/monkeypatching to stay isolated from external state. Each file mirrors a source module (`deprecation.py`, `audit.py`, `utils.py`).

> [!IMPORTANT]
> **Three-layer rule**: do not define target objects or deprecated wrappers directly inside `test_*.py` files. Place targets in `collection_targets.py`, deprecated wrappers in `collection_deprecate.py`, then import them in tests. This includes class definitions — do not define classes inside test functions; define them in the appropriate collection module instead.

**Docstrings in test collections:**

Functions in `collection_deprecate.py` and `collection_misconfigured.py` must have Google-style docstrings with a **user-first focus** — describe the real-world scenario a user would encounter, not just the technical configuration. This keeps tests grounded in actual use cases and helps contributors understand *why* each deprecation pattern exists.

Use a one-line summary of the deprecation pattern, then an `Examples:` section describing the user scenario:

```python
from deprecate import deprecated


@deprecated(target=None, deprecated_in="0.2", remove_in="0.3")
def depr_sum_warn_only(a: int, b: int = 5) -> int:
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
- **Assertions on warnings:** Use `pytest.warns(FutureWarning|DeprecationWarning)` to verify deprecation warnings are emitted correctly.

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

## 📄 License

By contributing, you agree that your contributions will be licensed under the same license as the project (see [LICENSE](../LICENSE) file).

______________________________________________________________________

<div align="center">

**Questions about security?** See our [Security Policy](SECURITY.md) for reporting vulnerabilities.

Made with 💙 by pyDeprecate contributors.

</div>
