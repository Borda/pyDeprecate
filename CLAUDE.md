# CLAUDE.md - AI Assistant Guide for pyDeprecate

This document provides comprehensive guidance for AI assistants working with the pyDeprecate codebase.

## Project Overview

**pyDeprecate** is a Python library that provides simple tooling for marking deprecated functions or classes and re-routing to their successors.

- **Repository**: https://github.com/Borda/pyDeprecate
- **License**: Apache 2.0
- **Current Version**: 0.4.0dev
- **Python Support**: 3.8 - 3.14
- **Homepage**: https://borda.github.io/pyDeprecate

## Project Structure

```
pyDeprecate/
├── src/deprecate/           # Main package source (src-layout)
│   ├── __init__.py         # Package initialization, exports deprecated and void
│   ├── __about__.py        # Package metadata (version, author, urls)
│   ├── deprecation.py      # Core @deprecated decorator implementation
│   └── utils.py            # Helper utilities (get_func_arguments_types_defaults, void, no_warning_call)
├── tests/                   # Test suite
│   ├── test_functions.py   # Tests for function deprecation
│   ├── test_classes.py     # Tests for class deprecation
│   ├── test_utils.py       # Tests for utility functions
│   ├── test_docs.py        # Tests for documentation
│   ├── collection_deprecate.py  # Test fixtures for deprecated functions
│   └── collection_targets.py    # Test fixtures for target functions
├── temp_docs/               # Documentation source files
├── .github/workflows/       # CI/CD workflows
├── setup.py                 # Package setup script
├── pyproject.toml          # Build system and tool configuration
└── .pre-commit-config.yaml # Pre-commit hooks configuration
```

## Core Functionality

### Main Components

1. **`@deprecated` decorator** (`src/deprecate/deprecation.py:190-321`)

   - Primary functionality for deprecating functions, methods, and classes
   - Supports multiple deprecation patterns:
     - `target=None`: Deprecation warning only
     - `target=True`: Self argument mapping (deprecating arguments)
     - `target=Callable`: Forward call to new function/class
   - Key parameters:
     - `deprecated_in`, `remove_in`: Version information
     - `args_mapping`: Map old arguments to new ones
     - `args_extra`: Add extra arguments when calling target
     - `num_warns`: Control warning frequency (default: 1, -1 for unlimited)
     - `stream`: Custom warning stream (default: FutureWarning)
     - `skip_if`: Conditional deprecation based on callable/bool
     - `template_mgs`: Custom warning message template

2. **Utility functions** (`src/deprecate/utils.py`)

   - `void(*args, **kwargs)`: Empty function to suppress IDE unused argument warnings
   - `get_func_arguments_types_defaults(func)`: Parse function signature
   - `no_warning_call(warning_type, match)`: Context manager for testing

3. **Warning templates** (`src/deprecate/deprecation.py:14-35`)

   - `TEMPLATE_WARNING_CALLABLE`: For function/class deprecation
   - `TEMPLATE_WARNING_ARGUMENTS`: For argument deprecation
   - `TEMPLATE_WARNING_NO_TARGET`: For warning-only deprecation
   - `TEMPLATE_DOC_DEPRECATED`: For docstring updates

## Development Workflow

### Setting Up Development Environment

```bash
# Clone repository
git clone https://github.com/Borda/pyDeprecate.git
cd pyDeprecate

# Install development dependencies
pip install -r tests/requirements.txt

# Install pre-commit hooks
pip install pre-commit
pre-commit install
```

### Running Tests

```bash
# Run all tests with coverage
python -m pytest . -vv --cov=deprecate

# Run specific test file
python -m pytest tests/test_functions.py -v

# Test README examples (phmdoctest)
phmdoctest README.md --outfile tests/test_readme.py
python -m pytest tests/test_readme.py
```

### Code Quality Tools

The project uses several automated code quality tools:

1. **Ruff** (linting and formatting)

   - Configuration: `pyproject.toml:12-63`
   - Line length: 120 characters
   - Target: Python 3.8+
   - Run: `ruff check src/ tests/` and `ruff format src/ tests/`

2. **MyPy** (type checking)

   - Configuration: `pyproject.toml:115-127`
   - Requires typed definitions
   - Run: `mypy src/`

3. **Pre-commit hooks** (`.pre-commit-config.yaml`)

   - end-of-file-fixer, trailing-whitespace
   - check-yaml, check-json, check-toml
   - prettier (for yaml/toml/html)
   - codespell (spell checking)
   - ruff (auto-fixing and formatting)
   - pyproject-fmt (pyproject.toml formatting)

4. **Codespell** (spell checking)

   - Configuration: `pyproject.toml:65-72`
   - Quiet level: 3

### Git Workflow

1. **Branching**: Create feature branches from main
2. **Commits**: Use clear, descriptive commit messages
3. **Pre-commit**: All commits run automated checks via pre-commit hooks
4. **CI/CD**: GitHub Actions run on push and PR:
   - `ci_testing.yml`: Tests on Ubuntu, macOS, Windows with Python 3.9, 3.12
   - `code-format.yml`: MyPy type checking and pre-commit validation
   - `ci_install-pkg.yml`: Package installation tests
   - `sample-docs.yml`: Documentation building
   - `codeql.yml`: Security analysis

## Coding Conventions

### Python Style

- **Docstring Format**: Google-style docstrings (configured in `pyproject.toml:62`)
- **Line Length**: 120 characters (black, ruff)
- **Type Hints**: Required for all function signatures (enforced by mypy)
- **Imports**: Sorted and formatted by ruff (isort integration)

### Documentation Standards

1. **Function/Method Documentation**:

   ```python
   def function_name(param: type) -> return_type:
       """Brief one-line summary.

       Longer description if needed.

       Args:
           param: Description of parameter

       Returns:
           Description of return value

       Raises:
           ExceptionType: When this exception is raised
       """
   ```

2. **Type Annotations**: All public functions must have complete type annotations

3. **Examples in Docstrings**: Use doctest format when providing examples

### Code Organization

- **src-layout**: All package code under `src/deprecate/`
- **Single responsibility**: Each module has a clear purpose
- **Minimal dependencies**: No runtime dependencies (pure Python)
- **Test dependencies**: Listed in `tests/requirements.txt`

## Testing Guidelines

### Test Structure

- **Test files**: Match source file names (`test_*.py`)
- **Test fixtures**: Shared test data in `collection_*.py` files
- **Coverage target**: Aim for high coverage (tracked via codecov.io)
- **README tests**: All code examples in README.md are tested via phmdoctest

### Writing Tests

```python
import pytest
from deprecate import deprecated


def test_simple_deprecation():
    """Test basic deprecation warning."""

    @deprecated(target=None, deprecated_in="0.1", remove_in="0.5")
    def old_func():
        return 42

    with pytest.warns(FutureWarning, match="deprecated since v0.1"):
        result = old_func()
    assert result == 42
```

### Test Configuration

- **pytest config**: `pyproject.toml:88-104`
- **Coverage config**: `pyproject.toml:106-113`
- Key pytest options:
  - `--strict-markers`: Enforce marker registration
  - `--doctest-modules`: Test docstring examples
  - `--disable-pytest-warnings`: Clean test output

## Common Development Tasks

### Adding a New Feature

01. Read relevant source files in `src/deprecate/`
02. Understand how existing similar features work
03. Write tests first (TDD approach recommended)
04. Implement the feature
05. Update docstrings with type hints
06. Run tests: `pytest tests/ -v`
07. Run type checking: `mypy src/`
08. Run pre-commit: `pre-commit run --all-files`
09. Update README.md if adding user-facing feature
10. Commit changes with descriptive message

### Fixing a Bug

1. Write a failing test that reproduces the bug
2. Fix the bug in the source code
3. Verify test passes
4. Run full test suite to ensure no regressions
5. Update relevant documentation if needed

### Updating Documentation

1. **README.md**: User-facing documentation with examples
2. **Docstrings**: In-code documentation (Google style)
3. **temp_docs/**: Sphinx documentation source
4. All README examples are automatically tested via phmdoctest

### Release Process

1. Update version in `src/deprecate/__about__.py`
2. Ensure all tests pass on CI
3. Create git tag: `git tag v0.x.x`
4. Push tag: `git push origin v0.x.x`
5. GitHub Actions automatically publishes to PyPI via `release-pypi.yml`

## Important Notes for AI Assistants

### Code Modification Guidelines

1. **Always read before modifying**: Never propose changes to code you haven't read
2. **Preserve existing style**: Match the existing code style and conventions
3. **Type hints required**: Add type annotations to all function signatures
4. **Update tests**: When changing functionality, update corresponding tests
5. **Run checks**: Ensure pre-commit hooks pass before committing
6. **No external dependencies**: Avoid adding runtime dependencies (keep library lightweight)

### Testing Requirements

1. **Test coverage**: Write tests for all new functionality
2. **Test existing behavior**: Don't break existing tests
3. **README examples**: If adding examples to README, they will be auto-tested
4. **Multiple Python versions**: Code must work on Python 3.8-3.14

### Documentation Requirements

1. **Google-style docstrings**: Required for all public functions/classes
2. **Type hints**: Required in both code and docstrings
3. **Examples**: Provide usage examples for new features
4. **README updates**: Document user-facing changes

### Common Pitfalls to Avoid

1. **Don't skip tests**: Always run the test suite
2. **Don't ignore type errors**: Fix mypy errors, don't suppress them
3. **Don't break backwards compatibility**: This library is used by many projects
4. **Don't add heavy dependencies**: Keep the library lightweight
5. **Don't forget pre-commit**: Run `pre-commit run --all-files` before committing

### Understanding the Deprecation Flow

The decorator works in this sequence:

1. **Wrapper creation**: `@deprecated` wraps the source function
2. **Call interception**: When called, wrapper intercepts arguments
3. **Argument mapping**: Maps old arguments to new ones via `args_mapping`
4. **Warning emission**: Raises warning (controlled by `num_warns`)
5. **Target invocation**: Calls target function with mapped arguments
6. **Warning tracking**: Tracks warnings per function or per argument

### Key Implementation Details

1. **Warning tracking**: Uses function attributes (`_warned`, `_warned_{arg}`)
2. **Argument parsing**: Uses `inspect.signature()` to understand function signatures
3. **Flexible targets**:
   - `None` = warning only
   - `True` = self mapping
   - `Callable` = forward to target
4. **Default handling**: Fills in default values when forwarding calls
5. **Class deprecation**: Wraps `__init__` method for class deprecation

## Version Information

- **Python**: 3.8 - 3.14
- **Build system**: setuptools + wheel
- **Test framework**: pytest >= 6.0
- **Type checker**: mypy >= 1.0
- **Linter/Formatter**: ruff (replaces black, isort, flake8)

## Additional Resources

- **Homepage**: https://borda.github.io/pyDeprecate
- **GitHub**: https://github.com/Borda/pyDeprecate
- **PyPI**: https://pypi.org/project/pyDeprecate/
- **Conda**: https://anaconda.org/conda-forge/pyDeprecate
- **Issues**: https://github.com/Borda/pyDeprecate/issues
- **Pull Requests**: https://github.com/Borda/pyDeprecate/pulls

## Contact

- **Author**: Jiri Borovec
- **Email**: j.borovec+github[at]gmail.com

______________________________________________________________________

*This document was generated to help AI assistants understand and work effectively with the pyDeprecate codebase. Keep it updated as the project evolves.*
