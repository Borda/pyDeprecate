---
description: "Use pyDeprecate's audit tools in CI/CD: validate decorator configuration, enforce removal deadlines by version, detect deprecation chains, and test warning behaviour."
---

# Audit Tools

Deprecations are only as good as the hygiene around them. The `deprecate.audit` module provides utilities for verifying that deprecated wrappers are correctly configured, that removal deadlines are actually enforced, and that chains of deprecated-to-deprecated calls do not silently accumulate. These tools are designed to run inside CI pipelines and test suites, catching problems before they reach users.

> **Renamed in v0.6**: `find_deprecated_callables` is now `find_deprecation_wrappers`, `validate_deprecated_callable` is now `validate_deprecation_wrapper`, and `DeprecatedCallableInfo` is now `DeprecationWrapperInfo`. The old names remain exported for backwards compatibility but will be removed in v1.0.

## Validating Wrapper Configuration

During development you may want to verify that a deprecated wrapper is correctly configured — that `args_mapping` keys actually exist in the function signature, that the mapping has a real effect, and that the target does not point back to the same function. Two utilities cover this: `validate_deprecation_wrapper()` inspects a single function, and `find_deprecation_wrappers()` scans an entire package.

`DeprecationWrapperInfo` is the dataclass returned by both utilities. Its fields are:

- `module` — module name where the function is defined (empty for direct validation)
- `function` — function name
- `deprecated_info` — the `__deprecated__` attribute dict from the decorator
- `invalid_args` — list of `args_mapping` keys that do not exist in the function signature
- `empty_mapping` — `True` if `args_mapping` is `None` or empty
- `identity_mapping` — list of args where key equals value (e.g. `{"arg": "arg"}` — no effect)
- `self_reference` — `True` if target points to the same function
- `no_effect` — `True` if the wrapper has zero impact (self-reference, empty mapping, or all-identity)

### Validating a single function

`validate_deprecation_wrapper()` extracts the configuration from the function's `__deprecated__` attribute and returns a `DeprecationWrapperInfo` dataclass. Use it in development or in a targeted pytest assertion to confirm that a specific wrapper is sound before shipping.

```python
from deprecate import validate_deprecation_wrapper, deprecated, DeprecationWrapperInfo


# Define your deprecated function
@deprecated(target=True, args_mapping={"old_arg": "new_arg"}, deprecated_in="1.0")
def my_func(old_arg: int = 0, new_arg: int = 0) -> int:
    return new_arg


# Validate the configuration - automatically extracts `args_mapping` and target from the decorator
result = validate_deprecation_wrapper(my_func)


# DeprecationWrapperInfo(
#   function='my_func',
#   invalid_args=[],
#   empty_mapping=False,
#   identity_mapping=[],
#   self_reference=False,
#   no_effect=False
# )


# Example: Function with invalid args_mapping
@deprecated(target=True, args_mapping={"nonexistent": "new_arg"}, deprecated_in="1.0")
def bad_func(real_arg: int = 0) -> int:
    return real_arg


result = validate_deprecation_wrapper(bad_func)
# result.invalid_args == ['nonexistent']
print(result)


# Example: Function with empty mapping (no effect)
@deprecated(target=True, args_mapping={}, deprecated_in="1.0")
def empty_func(arg: int = 0) -> int:
    return arg


result = validate_deprecation_wrapper(empty_func)
# result.empty_mapping == True, result.no_effect == True
print(result)

# Quick check if wrapper has any effect
if result.no_effect:
    print("Warning: This wrapper configuration has zero impact!")
```

### Scanning a package for deprecated wrappers

`find_deprecation_wrappers()` walks an entire package or module and returns a list of `DeprecationWrapperInfo` entries — one per deprecated callable discovered. Pass either a module object or a dotted module path string. This is the foundation for all package-wide CI checks.

```python
from deprecate import find_deprecation_wrappers

# For testing purposes, we use the test module; normally you would import your own package
from tests import collection_deprecate as my_package

# Scan an entire package for deprecated wrappers
results = find_deprecation_wrappers(my_package)

# Or scan using a string module path
results = find_deprecation_wrappers("tests.collection_deprecate")

# Check results - each item is a DeprecationWrapperInfo dataclass
for r in results:
    print(f"{r.module}.{r.function}: no_effect={r.no_effect}")
    if r.no_effect:
        print(f"  Warning: This wrapper has zero impact!")
        print(f"  invalid_args: {r.invalid_args}, identity_mapping: {r.identity_mapping}")

# Filter to only ineffective wrappers
ineffective = [r for r in results if r.no_effect]
if ineffective:
    print(f"Found {len(ineffective)} deprecated wrappers with zero impact!")
```

Group results by issue type to produce structured reports — for example, separating hard errors (invalid argument names) from advisory warnings (identity mappings):

```python
from deprecate import find_deprecation_wrappers

# For testing purposes, we use the test module; normally you would import your own package
from tests import collection_deprecate as my_package

results = find_deprecation_wrappers(my_package)

# Group by issue type (using dataclass attribute access)
wrong_args = [r for r in results if r.invalid_args]
identity_mappings = [r for r in results if r.identity_mapping]
self_refs = [r for r in results if r.self_reference]

print(f"=== Deprecation Validation Report ===")
print(f"Wrong arguments: {len(wrong_args)}")
print(f"Identity mappings: {len(identity_mappings)}")
print(f"Self-references: {len(self_refs)}")
```

### CLI usage

Install the optional CLI extra and scan any package directly from the command line without writing a Python script:

```bash
pip install 'pyDeprecate[cli]'
pydeprecate path/to/your/package
```

The CLI reports invalid argument mappings and wrappers with no effect, making it easy to add a validation step to a `Makefile` or pre-commit hook.

### pytest integration

Add a test that fails the suite when any deprecated wrapper in your package has an invalid configuration. Wrong argument names are hard errors; identity mappings are worth a warning.

```python
import warnings

import pytest
from deprecate import find_deprecation_wrappers

# For testing purposes, we use the test module; normally you would import your own package
from tests import collection_deprecate as my_package


def test_deprecated_wrappers_are_valid():
    """Validate all deprecated wrappers have proper configuration."""
    results = find_deprecation_wrappers(my_package)

    # Collect issues — wrong arg names are errors, identity mappings are worth a warning
    wrong_args = [r for r in results if r.invalid_args]
    identity_mappings = [r for r in results if r.identity_mapping]

    # Raise errors for wrong arguments (critical issues)
    if wrong_args:
        for r in wrong_args:
            print(f"ERROR: {r.module}.{r.function} has invalid args: {r.invalid_args}")
        pytest.fail(f"Found {len(wrong_args)} deprecated wrappers with invalid arguments")

    # Warn for identity mappings (less severe)
    for r in identity_mappings:
        pytest.warns(UserWarning, match=f"{r.function} has identity mapping")
```

## Enforcing Removal Deadlines

When you deprecate code with a `remove_in` version, you commit to deleting that code when that version ships. Without automation it is easy to forget, leaving "zombie code" that lingers past its deadline. `validate_deprecation_expiry()` scans a module or package and returns a list of error messages for every deprecated callable whose `remove_in` version is less than or equal to the supplied current version. An empty list means no expired deprecations were found.

The `audit` install extra is required for this utility because it depends on `packaging` for PEP 440 version comparison:

```bash
pip install 'pyDeprecate[audit]'
```

Scan a package for expired deprecations and control recursion depth:

```python
from deprecate import validate_deprecation_expiry

# For testing purposes, we use the test module; normally you would import your own package
from tests import collection_deprecate as my_package

# Scan your package for expired deprecations - using early-version that won't have expirations
expired = validate_deprecation_expiry(my_package, "0.2")
print(f"Found {len(expired)} expired")  # Returns a list of error messages (empty list = no expired)

# Example with expired deprecations found (using later-version)
expired = validate_deprecation_expiry(my_package, "0.5")
print(f"Found {len(expired)} expired")

# Auto-detect version from package metadata (mocked for demo)
from unittest.mock import patch

with patch("importlib.metadata.version", return_value="0.3"):
    expired = validate_deprecation_expiry(my_package)  # Automatically detects version
    print(f"Found {len(expired)} expired")

# Control recursion
expired = validate_deprecation_expiry(my_package, "0.1", recursive=False)  # Only scan top-level module
print(f"Found {len(expired)} expired")
```

Useful behaviours to know:

- Callables without `remove_in` are skipped — warnings-only deprecations are allowed.
- Invalid version formats in `remove_in` are silently skipped.
- PEP 440 versioning is used for comparison (e.g. `"2.0.0" > "1.9.5"`).
- Pre-release versions are handled correctly (e.g. `"1.5.0a1" < "1.5.0"`).

### pytest integration for expiry enforcement

Integrate expiry checks into the test suite so that zombie code is caught before any tests run. The session-scoped autouse fixture pattern below prevents the suite from starting at all if expired deprecations are present.

```python
import pytest
from deprecate import validate_deprecation_expiry

# For testing purposes, we use the test module; normally you would import your own package
from tests import collection_deprecate as my_package


def test_no_zombie_deprecations():
    """Ensure all deprecated code is removed when it reaches its deadline."""
    # Use your package's actual version - for this example we use a test version
    current_version = "0.5"  # Replace with: from mypackage import __version__

    expired = validate_deprecation_expiry(my_package, current_version)

    if expired:
        error_msg = "Found deprecated code past its removal deadline:\n"
        for msg in expired:
            error_msg += f"  - {msg}\n"
        pytest.fail(error_msg)


# Alternative: Use a fixture to run on every test session
# For testing purposes, we use the test module; normally you would import your own package
@pytest.fixture(scope="session", autouse=True)
def enforce_deprecation_deadlines():
    """Automatically check for zombie code before running any tests."""
    from tests import collection_deprecate as my_package

    current_version = "0.5"  # Replace with: from mypackage import __version__
    expired = validate_deprecation_expiry(my_package, current_version)
    if expired:
        raise AssertionError(
            f"Cannot run tests: {len(expired)} deprecated callables past removal deadline. "
            f"Remove these functions first: {expired}"
        )
```

## Detecting Deprecation Chains

When refactoring, it is easy to accidentally create a deprecated wrapper whose `target` is itself another deprecated function. These chains defeat the purpose of deprecation: callers get two warnings instead of one, and the intermediate hop adds no value. `validate_deprecation_chains()` scans a module or package for exactly this pattern using purely metadata-based detection — no source-code inspection is required.

Two chain types are reported:

- `ChainType.TARGET` — the target is a deprecated callable that forwards to another function. Fix by pointing directly to the final (non-deprecated) implementation.
- `ChainType.STACKED` — argument mappings chain through multiple hops and must be composed. This covers both the case where a callable target is itself `@deprecated(True, args_mapping=...)` (self-renaming), and the case where multiple `@deprecated(True, args_mapping=...)` decorators are stacked on the same function without being merged.

The example below demonstrates both bad patterns and the correct direct form:

```python
from deprecate import deprecated, validate_deprecation_wrapper, void


def new_power(base: float, exponent: float = 2) -> float:
    return base**exponent


# deprecated forwarder — targets new_power directly
@deprecated(target=new_power, deprecated_in="1.0", remove_in="2.0")
def power_v2(base: float, exponent: float = 2) -> float:
    void(base, exponent)


# self-deprecation — renames old arg "exp" -> "exponent" within the same function
@deprecated(True, deprecated_in="1.0", remove_in="2.0", args_mapping={"exp": "exponent"})
def legacy_power(base: float, exp: float = 2, exponent: float = 2) -> float:
    return base**exponent


# BAD: targets power_v2 (another deprecated forwarder) — ChainType.TARGET
# SOLUTION: point directly to new_power
@deprecated(target=power_v2, deprecated_in="1.5", remove_in="2.5")
def caller_target_chain(base: float, exponent: float = 2) -> float:  # ❌
    return void(base, exponent)


# BAD: targets legacy_power (target=True with arg renaming) — ChainType.STACKED
# Mappings chain: "power" -> "exp" -> "exponent" — must be composed.
# SOLUTION: target=new_power, args_mapping={"power": "exponent"}
@deprecated(target=legacy_power, deprecated_in="1.5", remove_in="2.5", args_mapping={"power": "exp"})
def caller_stacked_chain(base: float, power: float = 2) -> float:  # ❌
    return void(base, power)


# GOOD: targets final implementation directly with composed mapping
@deprecated(target=new_power, deprecated_in="1.5", remove_in="2.5", args_mapping={"power": "exponent"})
def caller_direct(base: float, power: float = 2) -> float:  # ✅
    return void(base, power)


for func in (caller_target_chain, caller_stacked_chain, caller_direct):
    info = validate_deprecation_wrapper(func)
    print(f"{func.__name__}: {info.chain_type}")
```

### pytest integration for chain detection

Add a test that fails whenever any deprecated function in your package targets another deprecated function. The session-scoped autouse fixture variant prevents the entire suite from running until chains are resolved.

```python
import pytest
from deprecate import validate_deprecation_chains

# normally you would import your own package
from tests import collection_chains as my_package


def test_no_deprecation_chains():
    """Ensure no deprecated function targets another deprecated function."""
    issues = validate_deprecation_chains(my_package)

    if issues:
        lines = [
            f"  - {i.function}: target '{getattr(i.deprecated_info['target'], '__name__', repr(i.deprecated_info['target']))}' is deprecated"
            for i in issues
        ]
        pytest.fail("Found deprecation chains:\n" + "\n".join(lines))


# Alternative: session-scoped auto-use fixture
@pytest.fixture(scope="session", autouse=True)
def enforce_no_deprecation_chains():
    from tests import collection_chains as my_package

    issues = validate_deprecation_chains(my_package)
    if issues:
        raise AssertionError(f"Found {len(issues)} deprecation chain(s). Fix before running tests.")
```

Use `recursive=False` to restrict scanning to the top-level module only, which can speed up large codebases when you know submodules are clean.

## Testing Deprecated Code

pyDeprecate ships `assert_no_warnings`, a context manager that fails if a specified warning category is raised inside the block. Use it alongside `pytest.warns` to write precise tests that verify both the presence and absence of deprecation warnings.

The `num_warns` parameter (default `1`) controls how many times a warning fires per function lifetime. The first test below verifies the warning appears; the third test verifies it does not appear on subsequent calls, which is the default behaviour.

```python
from deprecate import deprecated, assert_no_warnings, void
import pytest


def new_func(x: int) -> int:
    return x * 2


@deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
def old_func(x: int) -> int:
    pass


@deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
def old_func2(x: int) -> int:
    return void(x)


def test_deprecated_function_shows_warning():
    """Verify the deprecation warning is shown."""
    with pytest.warns(FutureWarning, match="old_func.*deprecated"):
        result = old_func(42)
    assert result == 84


def test_new_function_no_warning():
    """Verify new function doesn't trigger warnings."""
    with assert_no_warnings(FutureWarning):
        result = new_func(42)
    assert result == 84


def test_no_warning_after_first_call():
    """By default, warnings are shown only once per function."""
    # First call shows warning
    with pytest.warns(FutureWarning):
        old_func2(1)

    # Subsequent calls don't show warning (by default num_warns=1)
    with assert_no_warnings(FutureWarning):
        old_func2(2)


# call the tests for CI demonstration/validation
test_deprecated_function_shows_warning()
test_new_function_no_warning()
test_no_warning_after_first_call()
```

When a deprecation must be impossible to miss, set `num_warns=-1` to emit the warning on every call. Use `num_warns=N` to fire exactly `N` times — useful for integration tests that need to verify the warning count precisely.

```python
# Minimal replacement implementation used in examples
def new_func(x: int) -> int:
    return x * 2


# ---------------------------

from deprecate import deprecated


# Show warning every time (useful for critical deprecations)
@deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0", num_warns=-1)
def old_func_always_warn(x: int) -> int:
    pass


# Show warning N times total
@deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0", num_warns=5)
def old_func_warn_n_times(x: int) -> int:
    pass
```

______________________________________________________________________

Next: [Troubleshooting](../troubleshooting.md) — common errors and how to fix them.
