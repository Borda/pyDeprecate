---
id: audit
description: "Use pyDeprecate's audit tools in CI/CD: validate decorator configuration, enforce removal deadlines by version, detect deprecation chains, test deprecation behaviour, and integrate with pre-commit hooks."
---

# Audit Tools

Three things go wrong with deprecations in practice: a `remove_in` deadline passes and nobody deletes the code (zombie code); a deprecated wrapper targets another deprecated function, so callers get two notices instead of one (a chain); or an `args_mapping` key has a typo and silently does nothing (a misconfiguration). `validate_deprecation_expiry()`, `validate_deprecation_chains()`, and `find_deprecation_wrappers()` catch each of these in CI before they reach users.

!!! note "Renamed in v0.6"

    `find_deprecated_callables` is now `find_deprecation_wrappers`, `validate_deprecated_callable` is now `validate_deprecation_wrapper`, and `DeprecatedCallableInfo` is now `DeprecationWrapperInfo`. The old names remain exported for backwards compatibility but will be removed in v1.0.

## Validating Wrapper Configuration

Use these utilities to verify that a deprecated wrapper is correctly configured: that `args_mapping` keys exist in the function signature, that the mapping has a real effect, and that the target does not point back to the same function. `validate_deprecation_wrapper()` inspects a single function; `find_deprecation_wrappers()` scans an entire package.

`DeprecationWrapperInfo` is the dataclass returned by both. Its fields:

- `module` — module name where the function is defined (empty for direct validation)
- `function` — function name
- `deprecated_info` — the `__deprecated__` attribute as a `DeprecationConfig` dataclass from the decorator
- `invalid_args` — list of `args_mapping` keys that do not exist in the function signature
- `empty_mapping` — `True` if `args_mapping` is `None` or empty
- `identity_mapping` — list of args where key equals value (e.g. `{"arg": "arg"}` — no effect)
- `self_reference` — `True` if target points to the same function
- `no_effect` — `True` if the wrapper has zero impact (self-reference, empty mapping, or all-identity)
- `chain_type` — chain classification used when reporting deprecation chains, such as `TARGET` or `STACKED`

### Validating a single function

`validate_deprecation_wrapper()` extracts the configuration from the function's `__deprecated__` attribute and returns a `DeprecationWrapperInfo` dataclass. Use it in development or in a targeted pytest assertion to confirm a specific wrapper is sound before shipping.

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

<details>
  <summary>Output: <code>print("Warning: This wrapper configuration has zero impact!")</code></summary>

```
DeprecationWrapperInfo(module='', function='bad_func', deprecated_info=DeprecationConfig(deprecated_in='1.0', remove_in='', name='bad_func', target=True, args_mapping={'nonexistent': 'new_arg'}, docstring_style='rst'), invalid_args=['nonexistent'], empty_mapping=False, identity_mapping=[], self_reference=False, no_effect=False, chain_type=None)
DeprecationWrapperInfo(module='', function='empty_func', deprecated_info=DeprecationConfig(deprecated_in='1.0', remove_in='', name='empty_func', target=True, args_mapping={}, docstring_style='rst'), invalid_args=[], empty_mapping=True, identity_mapping=[], self_reference=False, no_effect=True, chain_type=None)
Warning: This wrapper configuration has zero impact!
```

</details>

### Scanning a package for deprecated wrappers

`find_deprecation_wrappers()` walks an entire package or module and returns a list of `DeprecationWrapperInfo` entries, one per deprecated callable discovered. Pass either a module object or a dotted module path string. This is the foundation for all package-wide CI checks.

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

<details>
  <summary>Output: <code>print(f"Found {len(ineffective)</code></summary>

```
tests.collection_deprecate.ChainedProxyColorEnum: no_effect=False
tests.collection_deprecate.DecoratedDataClass: no_effect=False
tests.collection_deprecate.DecoratedEnum: no_effect=False
tests.collection_deprecate.DeprecatedColorDataClass: no_effect=False
tests.collection_deprecate.DeprecatedColorEnum: no_effect=False
tests.collection_deprecate.DeprecatedDataClass: no_effect=False
tests.collection_deprecate.DeprecatedEnum: no_effect=False
tests.collection_deprecate.DeprecatedIntEnum: no_effect=False
tests.collection_deprecate.MappedColorEnum: no_effect=False
tests.collection_deprecate.MappedDataClass: no_effect=False
tests.collection_deprecate.MappedDropArgDataClass: no_effect=False
tests.collection_deprecate.MappedEnum: no_effect=False
tests.collection_deprecate.MappedIntEnum: no_effect=False
tests.collection_deprecate.MappedValueEnum: no_effect=False
tests.collection_deprecate.RedirectedDataClass: no_effect=False
tests.collection_deprecate.RedirectedEnum: no_effect=False
tests.collection_deprecate.SelfMappedEnum: no_effect=False
tests.collection_deprecate.WarnOnlyColorEnum: no_effect=False
tests.collection_deprecate.WrappedDataClass: no_effect=False
tests.collection_deprecate.WrappedEnum: no_effect=False
tests.collection_deprecate.decorated_pow_self: no_effect=False
tests.collection_deprecate.decorated_pow_skip_if_func: no_effect=False
tests.collection_deprecate.decorated_pow_skip_if_true: no_effect=False
tests.collection_deprecate.decorated_sum: no_effect=False
tests.collection_deprecate.decorated_sum_calls_2: no_effect=False
tests.collection_deprecate.decorated_sum_calls_inf: no_effect=False
tests.collection_deprecate.decorated_sum_msg: no_effect=False
tests.collection_deprecate.decorated_sum_no_stream: no_effect=False
tests.collection_deprecate.decorated_sum_warn_only: no_effect=False
tests.collection_deprecate.depr_accuracy_extra: no_effect=False
tests.collection_deprecate.depr_accuracy_map: no_effect=False
tests.collection_deprecate.depr_accuracy_skip: no_effect=False
tests.collection_deprecate.depr_accuracy_target: no_effect=False
tests.collection_deprecate.depr_config_dict: no_effect=False
tests.collection_deprecate.depr_config_dict_read_only: no_effect=False
tests.collection_deprecate.depr_func_no_remove_in: no_effect=False
tests.collection_deprecate.depr_func_targeting_proxy: no_effect=False
tests.collection_deprecate.depr_make_new_cls: no_effect=False
tests.collection_deprecate.depr_make_new_cls_mapped: no_effect=False
tests.collection_deprecate.depr_pow_args: no_effect=False
tests.collection_deprecate.depr_pow_mix: no_effect=False
tests.collection_deprecate.depr_pow_self_double: no_effect=False
tests.collection_deprecate.depr_pow_self_twice: no_effect=False
tests.collection_deprecate.depr_pow_skip_if_false_true: no_effect=False
tests.collection_deprecate.depr_pow_skip_if_func_int: no_effect=False
tests.collection_deprecate.depr_pow_skip_if_true_false: no_effect=False
tests.collection_deprecate.depr_pow_wrong: no_effect=False
tests.collection_deprecate.depr_timing_wrapper: no_effect=False
tests.collection_deprecate.wrapped_pow_self: no_effect=False
tests.collection_deprecate.wrapped_pow_skip_if_func: no_effect=False
tests.collection_deprecate.wrapped_pow_skip_if_true: no_effect=False
tests.collection_deprecate.wrapped_sum: no_effect=False
tests.collection_deprecate.wrapped_sum_calls_2: no_effect=False
tests.collection_deprecate.wrapped_sum_calls_inf: no_effect=False
tests.collection_deprecate.wrapped_sum_msg: no_effect=False
tests.collection_deprecate.wrapped_sum_no_stream: no_effect=False
tests.collection_deprecate.wrapped_sum_warn_only: no_effect=False
```

</details>

Group results by issue type for structured reports — separate hard errors (invalid argument names) from advisory notes (identity mappings):

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

<details>
  <summary>Output: <code>print(f"Self-references: {len(self_refs)</code></summary>

```
=== Deprecation Validation Report ===
Wrong arguments: 0
Identity mappings: 0
Self-references: 0
```

</details>

### CLI usage

Install the optional CLI extra and scan any package from the command line without writing a script:

```bash
pip install 'pyDeprecate[cli]'
pydeprecate path/to/your/package
```

The CLI reports invalid argument mappings and wrappers with no effect, making it straightforward to add a validation step to a `Makefile` or pre-commit hook.

**Exit codes:**

| Exit code | Meaning                                                                     |
| --------- | --------------------------------------------------------------------------- |
| `0`       | No issues found (or only advisory notes like identity mappings)             |
| `1`       | Invalid argument mappings detected (hard errors that break call forwarding) |

Use `--skip-errors` to always exit `0` even when issues are found — useful for advisory-only CI steps where you want visibility without blocking the pipeline.

### pytest integration

Add a test that fails the suite when any deprecated wrapper has an invalid configuration. Wrong argument names are hard errors; identity mappings are worth a warning.

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
        warnings.warn(f"{r.function} has identity mapping", UserWarning)
```

## Enforcing Removal Deadlines

When you set `remove_in`, you are committing to delete that code when the version ships. Without automation, it is easy to forget — leaving zombie code that lingers past its deadline. `validate_deprecation_expiry()` scans a module or package and returns a list of error messages for every deprecated callable whose `remove_in` version has passed. An empty list means everything is clean.

The `audit` install extra is required because this utility depends on `packaging` for PEP 440 version comparison:

```bash
pip install 'pyDeprecate[audit]'
```

Scan a package and control recursion depth:

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

<details>
  <summary>Output: <code>print(f"Found {len(expired)</code></summary>

```
Found 14 expired
Found 28 expired
Found 17 expired
Found 0 expired
```

</details>

Good to know:

- Callables without `remove_in` are skipped — notice-only deprecations are allowed.
- Invalid version formats in `remove_in` are silently skipped.
- PEP 440 versioning is used for comparison (e.g. `"2.0.0" > "1.9.5"`).
- Pre-release versions are handled correctly (e.g. `"1.5.0a1" < "1.5.0"`).

### pytest integration for expiry enforcement

Wire expiry checks into your test suite so zombie code is caught before any tests run. The session-scoped autouse fixture pattern below prevents the suite from starting at all if expired deprecations are present.

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

A deprecated wrapper whose `target` is itself another deprecated function creates a chain: callers get two deprecation notices instead of one, and the intermediate hop adds no value. `validate_deprecation_chains()` scans a module or package for exactly this pattern using purely metadata-based detection — no source-code inspection required.

Two chain types are reported:

- `ChainType.TARGET` — the target is a deprecated callable that forwards to another function. Fix by pointing directly to the final (non-deprecated) implementation.
- `ChainType.STACKED` — argument mappings chain through multiple hops and must be composed. This covers both the case where a callable target is itself `@deprecated(True, args_mapping=...)` (self-renaming), and the case where multiple `@deprecated(True, args_mapping=...)` decorators are stacked on the same function without being merged.

The example below shows both bad patterns and the correct direct form:

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

<details>
  <summary>Output: <code>print(f"{func.__name__}: {info.chain_type}")</code></summary>

```
caller_target_chain: ChainType.TARGET
caller_stacked_chain: ChainType.STACKED
caller_direct: None
```

</details>

### pytest integration for chain detection

Add a test that fails whenever a deprecated function in your package targets another deprecated function. The session-scoped autouse fixture variant prevents the entire suite from running until chains are resolved.

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
            f"  - {i.function}: target '{getattr(i.deprecated_info.target, '__name__', repr(i.deprecated_info.target))}' is deprecated"
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

## Pre-commit Integration

!!! info "Coming soon"

    Native pre-commit hook support is planned. For now, run the validator directly via `python -m deprecate` in your `Makefile` or CI step.

The CLI checks for misconfigured wrappers only — invalid `args_mapping` keys, identity mappings, self-references:

```bash
# Install the CLI extra
pip install 'pyDeprecate[cli]'

# Scan your package — exits 1 if invalid arg mappings are found
python -m deprecate src/your_package

# Advisory-only: always exit 0, report issues without blocking
python -m deprecate src/your_package --skip_errors true
```

**Exit codes:**

| Exit code | Meaning                                                          |
| --------- | ---------------------------------------------------------------- |
| `0`       | No issues found (or `--skip_errors true` was set)                |
| `1`       | Invalid argument mappings detected — these break call forwarding |

## Testing Deprecated Code

pyDeprecate ships `assert_no_warnings`, a context manager that fails if a specified warning category is raised inside the block. Use it alongside `pytest.warns` to write precise tests that verify both the presence and absence of deprecation notices.

`num_warns` (default `1`) controls how many times the deprecation message fires per function lifetime. The first test below verifies the message appears; the third verifies it stops on subsequent calls, which is the default behaviour.

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

When a deprecation must be impossible to miss, set `num_warns=-1` to fire on every call. Use `num_warns=N` for exactly N times — useful for integration tests that need to verify the emit count precisely.

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

### Suppressing warnings in test fixtures

When you call deprecated functions in test setup code (fixtures, factory helpers, shared utilities), use `warnings.catch_warnings()` with `simplefilter("ignore")` to suppress the noise while still exercising the call-forwarding path.

```python
import warnings
from deprecate import deprecated, assert_no_warnings, void


def new_create_session(host: str, timeout: int = 30) -> dict:
    return {"host": host, "timeout": timeout}


@deprecated(target=new_create_session, deprecated_in="1.0", remove_in="2.0")
def create_session(host: str, timeout: int = 30) -> dict:
    return void(host, timeout)


def make_test_session(host: str = "localhost") -> dict:
    """Test fixture helper — calls deprecated API silently."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        return create_session(host, timeout=5)


# The helper works without emitting warnings:
session = make_test_session()
print(session)

# Meanwhile, assert_no_warnings verifies NEW code is clean:
with assert_no_warnings(FutureWarning):
    clean_session = new_create_session("prod.example.com")
print(clean_session)
```

<details>
  <summary>Output: <code>print(clean_session)</code></summary>

```
{'host': 'localhost', 'timeout': 5}
{'host': 'prod.example.com', 'timeout': 30}
```

</details>

### Choosing the right testing tool

| Tool                                                   | Use when...                                                 | Behaviour                                              |
| ------------------------------------------------------ | ----------------------------------------------------------- | ------------------------------------------------------ |
| `pytest.warns(FutureWarning)`                          | Testing that a deprecated function DOES warn on first call  | Fails if no matching warning is raised                 |
| `assert_no_warnings(FutureWarning)`                    | Testing that new code or subsequent calls do NOT warn       | Fails if a matching warning IS raised                  |
| `assert_no_warnings(FutureWarning, match="pattern")`   | Testing that a specific warning message is absent           | Only fails if a warning matching the pattern is raised |
| `warnings.catch_warnings()` + `simplefilter("ignore")` | Calling deprecated code in fixtures/setup without assertion | Silently suppresses; never fails                       |

The `match` parameter on `assert_no_warnings` accepts a substring — it filters captured warnings by message content, so you can assert absence of a specific deprecation while allowing unrelated warnings through.

______________________________________________________________________

Next: [Troubleshooting](../troubleshooting.md) — common errors and how to fix them.
