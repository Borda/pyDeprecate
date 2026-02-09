# Contributing Guide

Thank you for your interest in contributing to pyDeprecate! We appreciate all contributions and welcome everyone, regardless of experience level. Your help makes this project better for everyone.

> [!TIP]
> **First time contributing to open source?** Check out [First Contributions](https://github.com/firstcontributions/first-contributions) for a beginner-friendly guide that walks you through the entire process.

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
3. **Investigate and fix** — Identify the root cause and implement a fix. Keep your changes focused on the specific issue.
4. **Test your fix** — Write or update tests to verify your fix works and prevents the bug from recurring.
5. **Submit a PR** — Create a pull request with your fix, linking to the issue it addresses.

> [!TIP]
> Fixes with tests are more likely to be merged quickly!

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
2. **Add tests** — Write comprehensive tests to ensure your feature works correctly.
3. **Update documentation** — Document how to use the new feature.
4. **Submit a PR** — Create a pull request, linking to the approved issue.

## 📬 Pull Requests

### Before Opening a PR

Complete this checklist before opening a pull request to ensure quality and smooth review:

- [ ] Followed existing code style
- [ ] Added tests for new functionality
- [ ] Ran tests locally and they pass
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

# Run the full test suite (including doctests if configured in pytest)
pytest .

# Run linting and formatting
pre-commit run --all-files
```

## 💎 Quality Expectations

> [!IMPORTANT]
> **Always do your best — that's the essential spirit of OSS contributions.**

We value all levels of contribution and want to encourage everyone, regardless of skill level or time available. What matters is being reasonable and meaningful about what you can deliver:

- **Start small and iterate** — Better to do smaller tasks piece by piece than take too much and leave it unfinished. Small contributions add up over time.
- **Break work into steps** — This allows others to take over and continue if needed. It also makes your work more approachable for reviewers.
- **Avoid abandoned PRs** — Forked PRs are difficult to carry over except by maintainers, creating significant burden. If you can't complete a PR, let us know.
- **Be meaningful and reasonable** — Contribute what you can realistically complete. Even small improvements make a difference.

We don't expect perfection. We expect genuine effort. If you're unsure about something, ask! The community is here to help.

## 🚀 Quick Start

```bash
# 1. Fork the repository

# 2. Clone your fork
git clone https://github.com/YOUR-USERNAME/pyDeprecate.git
cd pyDeprecate

# 3. Create a feature branch
git checkout -b feature/amazing-feature

# 4. Install in development mode
pip install -e . "pre-commit" -r tests/requirements.txt
pre-commit install

# 5. Make your changes

# 6. Run tests
pytest tests/

# 7. Stage and commit your changes
git status        # Check which files have been changed
git add -A        # Stage all modified and new files
git commit -m "Add amazing feature"  # Commit the staged changes

# 8. Push to your fork
git push origin feature/amazing-feature

# 9. Open a Pull Request
```

## 📋 Coding Standards

### Style & Formatting

- Follow [PEP 8](https://pep8.org/) style guidelines
- Write clear, descriptive docstrings (Google-style convention)
- Keep functions focused and modular
- Add type hints to all function signatures
- Write meaningful variable and function names
- Add comments only where the code is not self-explanatory
- No bare `except:` — always catch specific exceptions

> [!TIP]
> Code style is enforced by pre-commit hooks — run `pre-commit run --all-files` before submitting. Key tools and their configs live in `pyproject.toml` and `.pre-commit-config.yaml` (`ruff` for formatting/linting, `mypy` for type checking).

### Architecture Constraints

- **Zero runtime dependencies** — pyDeprecate has no runtime dependencies. Do not add any.
- **Fast imports / low overhead** — avoid expensive computations or premature imports in module-level code or wrapper setup.
- **Circular imports** — when editing `src/deprecate/`, verify new imports don't create cycles. Use `if TYPE_CHECKING:` blocks for type-only imports.
- **Deprecation chains** — if modifying chain validation logic, handle infinite loops (A deprecates B, B deprecates A) gracefully without crashing.

### Test Organization

Tests live in `tests/` and follow a **three-layer separation**:

| File                          | Purpose                                                                                   |
| ----------------------------- | ----------------------------------------------------------------------------------------- |
| `collection_targets.py`       | Target functions and classes (the "new" implementations that deprecated code forwards to) |
| `collection_deprecate.py`     | Deprecated wrappers that use `@deprecated(...)` to forward to targets                     |
| `collection_misconfigured.py` | Intentionally invalid/ineffective deprecation configurations for validation testing       |
| `test_*.py`                   | Actual test logic — imports from the collections above and asserts behavior               |

> [!IMPORTANT]
> In almost all cases, **do not** define target functions or `@deprecated` wrappers directly inside `test_*.py` files. Prefer placing targets in `collection_targets.py` and deprecated wrappers in `collection_deprecate.py`, then importing them in tests. A small number of existing tests intentionally define `@deprecated` callables inline when the test itself is about how `@deprecated` is declared; new tests should follow the three-layer pattern unless such an inline definition is explicitly required.

**Docstrings in test collections:**

Functions in `collection_deprecate.py` and `collection_misconfigured.py` must have Google-style docstrings with a **user-first focus** — describe the real-world scenario a user would encounter, not just the technical configuration. This keeps tests grounded in actual use cases and helps contributors understand *why* each deprecation pattern exists.

Use a one-line summary of the deprecation pattern, then an `Examples:` section describing the user scenario:

```python
@deprecated(target=None, deprecated_in="0.2", remove_in="0.3")
def depr_sum_warn_only(a: int, b: int = 5) -> int:
    """Warning-only deprecation with no forwarding.

    Examples:
        The function is going away but has no replacement yet. The user gets
        warned, but the original body still executes (`target=None`).
    """
```

**Test requirements:**

- Every new function or behavior change must have accompanying tests.
- For every new utility or feature, include tests for:
  1. The happy path (expected correct behavior).
  2. The failure path (expected errors are raised).
  3. Edge cases (None types, empty inputs, circular chains, missing arguments).
- **Group related tests in classes** — use test classes when you have multiple related tests or need shared fixtures.
- **Avoid redundant naming** — don't repeat class context in test method names (e.g., in `TestDeprecatedWrapper`, use `test_shows_warning` not `test_deprecated_wrapper_shows_warning`).
- **Use fixtures for independence** — use pytest fixtures to reset state between tests. Add `autouse=True` fixtures when a class needs per-test reset.
- **One behavior per test** — each test method should verify one specific aspect.
- **Assertions on warnings:** Use `pytest.warns(FutureWarning|DeprecationWarning)` to verify deprecation warnings are emitted correctly.

Example:

```python
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


# tests/test_functions.py — the test
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
