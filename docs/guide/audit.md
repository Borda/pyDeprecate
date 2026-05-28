---
id: audit
description: "Use pyDeprecate's audit tools in CI/CD: validate decorator configuration, enforce removal deadlines by version, detect deprecation chains, test deprecation behaviour, and integrate with pre-commit hooks."
---

# Audit Tools

Three things go wrong with deprecations in practice: a `remove_in` deadline passes and nobody deletes the code (zombie code); a deprecated wrapper targets another deprecated function, so callers get two notices instead of one (a chain); or an `args_mapping` key has a typo and silently does nothing (a misconfiguration). `validate_deprecation_expiry()`, `validate_deprecation_chains()`, and `find_deprecation_wrappers()` catch each of these in CI before they reach users.

!!! note "Renamed in v0.6"

    `find_deprecated_callables` is now `find_deprecation_wrappers`, `validate_deprecated_callable` is now `validate_deprecation_wrapper`, and `DeprecatedCallableInfo` is now `DeprecationWrapperInfo`. The old names remain exported for backwards compatibility but will be removed in v1.0.

!!! warning "Breaking change in v0.8: `__deprecated__.target` type changed"

    In v0.8, `DeprecationConfig.target` now always stores a `TargetMode` enum member or a `Callable` — never a raw boolean sentinel. Code inspecting this attribute must be updated:

    | Before v0.8                           | v0.8+                                                 |
    | ------------------------------------- | ----------------------------------------------------- |
    | `func.__deprecated__.target is None`  | `func.__deprecated__.target is TargetMode.NOTIFY`     |
    | `func.__deprecated__.target is True`  | `func.__deprecated__.target is TargetMode.ARGS_REMAP` |
    | `func.__deprecated__.target is False` | `func.__deprecated__.misconfigured`                   |

## Validating Wrapper Configuration

Use these utilities to verify that a deprecated wrapper is correctly configured: that `args_mapping` keys exist in the function signature, that the mapping has a real effect, and that the target does not point back to the same function. `validate_deprecation_wrapper()` inspects a single function; `find_deprecation_wrappers()` scans an entire package.

`DeprecationWrapperInfo` is the dataclass returned by both. Its fields:

- `module` — module name where the function is defined (empty for direct validation)
- `function` — function name
- `deprecated_info` — the `__deprecated__` attribute as a `DeprecationConfig` dataclass from the decorator
- `invalid_args` — list of `args_mapping` keys that do not exist in the function signature
- `empty_args_mapping` — `True` if `args_mapping` is `None` or empty
- `identity_args_mapping` — list of args where key equals value (e.g. `{"arg": "arg"}` — no effect)
- `self_reference` — `True` if target points to the same function
- `no_effect` — `True` if the wrapper has zero impact (self-reference, empty mapping, or all-identity)
- `misconfigured_target` — `True` if an invalid raw target sentinel (`False`) was passed at decoration time
- `all_identity` — `True` if every entry in `args_mapping` maps a key to itself
- `chain_type` — chain classification used when reporting deprecation chains, such as `TARGET` or `STACKED`
- `empty_deprecated_in` — `True` when `deprecated_in` is absent or empty; useful in CI to surface wrappers with no version annotation
- `api_type` — inferred API kind for report generation (e.g. `callable`, `args`, `class`, `dataclass attributes`, `class method`); excluded from `repr()` to keep snapshot tests stable

### Validating a single function

`validate_deprecation_wrapper()` extracts the configuration from the function's `__deprecated__` attribute and returns a `DeprecationWrapperInfo` dataclass. Use it in development or in a targeted pytest assertion to confirm a specific wrapper is sound before shipping.

```python
from deprecate import TargetMode, validate_deprecation_wrapper, deprecated, DeprecationWrapperInfo


# Define your deprecated function
@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_arg": "new_arg"}, deprecated_in="1.0")
def my_func(old_arg: int = 0, new_arg: int = 0) -> int:
    return new_arg


# Validate the configuration - automatically extracts `args_mapping` and target from the decorator
result = validate_deprecation_wrapper(my_func)


# DeprecationWrapperInfo(
#   function='my_func',
#   invalid_args=[],
#   empty_args_mapping=False,
#   identity_args_mapping=[],
#   self_reference=False,
#   no_effect=False
# )


# Example: Function with invalid args_mapping
@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"nonexistent": "new_arg"}, deprecated_in="1.0")
def bad_func(real_arg: int = 0) -> int:
    return real_arg


result = validate_deprecation_wrapper(bad_func)
# result.invalid_args == ['nonexistent']
print(result)


# Example: Function with empty mapping (no effect)
@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={}, deprecated_in="1.0")
def empty_func(arg: int = 0) -> int:
    return arg


result = validate_deprecation_wrapper(empty_func)
# result.empty_args_mapping == True, result.no_effect == True
print(result)

# Quick check if wrapper has any effect
if result.no_effect:
    print("Warning: This wrapper configuration has zero impact!")
```

<details>
  <summary>Output: <code>"Warning: This wrapper configuration has zero impact!"</code></summary>

```
DeprecationWrapperInfo(module='', function='bad_func', deprecated_info=DeprecationConfig(deprecated_in='1.0', remove_in='', name='bad_func', target=<TargetMode.ARGS_REMAP: 'args_remap'>, args_mapping={'nonexistent': 'new_arg'}, args_extra=None, misconfigured=False, docstring_style='rst', template_mgs=None), invalid_args=['nonexistent'], empty_args_mapping=False, identity_args_mapping=[], self_reference=False, no_effect=False, misconfigured_target=False, all_identity=False, chain_type=None, empty_deprecated_in=False)
DeprecationWrapperInfo(module='', function='empty_func', deprecated_info=DeprecationConfig(deprecated_in='1.0', remove_in='', name='empty_func', target=<TargetMode.ARGS_REMAP: 'args_remap'>, args_mapping={}, args_extra=None, misconfigured=True, docstring_style='rst', template_mgs=None), invalid_args=[], empty_args_mapping=True, identity_args_mapping=[], self_reference=False, no_effect=True, misconfigured_target=True, all_identity=False, chain_type=None, empty_deprecated_in=False)
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

# Filter to only ineffective wrappers
ineffective = [r for r in results if r.no_effect]
print(f"Found {len(ineffective)} deprecated wrappers with zero impact!")

# Check some results - each item is a DeprecationWrapperInfo dataclass
for r in results[:5]:
    print(f"{r.module}.{r.function}: no_effect={r.no_effect}")
    if r.no_effect:
        print(f"  Warning: This wrapper has zero impact!")
        print(f"  invalid_args: {r.invalid_args}, identity_args_mapping: {r.identity_args_mapping}")
```

<details>
  <summary>Output: <code>f"Found {len(ineffective)} deprecated wrappers with zero impact!"</code></summary>

```
Found 0 deprecated wrappers with zero impact!
tests.collection_deprecate.ChainedProxyColorEnum: no_effect=False
tests.collection_deprecate.CrossGuardModuleLevel.old_method: no_effect=False
tests.collection_deprecate.CrossGuardOldClass.__init__: no_effect=False
tests.collection_deprecate.CrossGuardSameClass.old_method: no_effect=False
tests.collection_deprecate.DecoratedDataClass: no_effect=False
```

</details>

### Scanning a misconfigured collection

`tests.collection_misconfigured` intentionally mixes invalid args, empty mappings, identity mappings, self-references,
and target-mode misconfigurations. Use it as a regression fixture to see the audit buckets in one place.
The raw module scan also sees the typed alias `self_ref_typed`, so the example reports 15 bindings even though there are
14 unique function objects.

```python
from deprecate import find_deprecation_wrappers

# For testing purposes, we use the misconfigured test module; normally you would import your own package
from tests import collection_misconfigured as my_package

results = find_deprecation_wrappers(my_package, recursive=False)

invalid_args = [r for r in results if r.invalid_args]
empty_mappings = [r for r in results if r.empty_args_mapping]
identity_mappings = [r for r in results if r.identity_args_mapping]
self_refs = [r for r in results if r.self_reference]
misconfigured_targets = [r for r in results if r.misconfigured_target]
no_effect = [r for r in results if r.no_effect]

print("=== Misconfiguration Report ===")
print(f"Wrappers scanned: {len(results)}")
print(f"Invalid arguments: {len(invalid_args)}")
print(f"Empty mappings: {len(empty_mappings)}")
print(f"Identity mappings: {len(identity_mappings)}")
print(f"Self-references: {len(self_refs)}")
print(f"Misconfigured targets: {len(misconfigured_targets)}")
print(f"No effect: {len(no_effect)}")
```

<details>
  <summary>Output: <code>f"No effect: {len(no_effect)}"</code></summary>

```
=== Misconfiguration Report ===
Wrappers scanned: 15
Invalid arguments: 3
Empty mappings: 7
Identity mappings: 3
Self-references: 2
Misconfigured targets: 6
No effect: 7
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
identity_mappings = [r for r in results if r.identity_args_mapping]
self_refs = [r for r in results if r.self_reference]

print(f"=== Deprecation Validation Report ===")
print(f"Wrong arguments: {len(wrong_args)}")
print(f"Identity mappings: {len(identity_mappings)}")
print(f"Self-references: {len(self_refs)}")
```

<details>
  <summary>Output: <code>f"Self-references: {len(self_refs)}"</code></summary>

```
=== Deprecation Validation Report ===
Wrong arguments: 0
Identity mappings: 0
Self-references: 0
```

</details>

### CLI usage

All audit functions are also available from the command line via five subcommands (`check`, `expiry`, `chains`, `all`, `status`). See the [CLI Reference](cli.md) for the full guide including flags, exit codes, and CI recipes.

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
    identity_mappings = [r for r in results if r.identity_args_mapping]

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
  <summary>Output: <code>f"Found {len(expired)} expired"</code></summary>

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
- `ChainType.STACKED` — argument mappings chain through multiple hops and must be composed. This covers both the case where a callable target is itself `@deprecated(TargetMode.ARGS_REMAP, args_mapping=...)` (self-renaming), and the case where multiple `@deprecated(TargetMode.ARGS_REMAP, args_mapping=...)` decorators are stacked on the same function without being merged.

The example below shows both bad patterns and the correct direct form:

```python
from deprecate import TargetMode, deprecated, validate_deprecation_wrapper, void


def new_power(base: float, exponent: float = 2) -> float:
    return base**exponent


# deprecated forwarder — targets new_power directly
@deprecated(target=new_power, deprecated_in="1.0", remove_in="2.0")
def power_v2(base: float, exponent: float = 2) -> float:
    void(base, exponent)


# self-deprecation — renames old arg "exp" -> "exponent" within the same function
@deprecated(TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"exp": "exponent"})
def legacy_power(base: float, exp: float = 2, exponent: float = 2) -> float:
    return base**exponent


# BAD: targets power_v2 (another deprecated forwarder) — ChainType.TARGET
# SOLUTION: point directly to new_power
@deprecated(target=power_v2, deprecated_in="1.5", remove_in="2.5")
def caller_target_chain(base: float, exponent: float = 2) -> float:  # ❌
    return void(base, exponent)


# BAD: targets legacy_power (target=TargetMode.ARGS_REMAP with arg renaming) — ChainType.STACKED
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
  <summary>Output: <code>f"{func.__name__}: {info.chain_type}"</code></summary>

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

    Native pre-commit hook support is planned. For now, run the validator directly via `pydeprecate` in your `Makefile` or CI step.

The CLI provides five subcommands. Use `check` for wrapper config validation, `all` to run every check in a single pass (and append a deprecation table), or `status` to generate a standalone markdown deprecation table without running any checks. See the [CLI Reference](cli.md) for full flag and exit-code documentation.

```bash
# Install CLI + audit extras (audit needed for expiry checks)
pip install 'pyDeprecate[audit,cli]'

# check — exits 1 if invalid arg mappings are found
pydeprecate check src/your_package

# all — exits 1 on invalid mappings, chains, or expired wrappers; appends deprecation table
pydeprecate all src/your_package

# status — standalone deprecation status table only (no checks, always exits 0)
pydeprecate status src/your_package

# Advisory-only: always exit 0, report issues without blocking
pydeprecate check src/your_package --skip_errors true
```

**Exit codes** (see [CLI Reference — Exit codes](cli.md#exit-codes) for per-subcommand details):

| Exit code | Meaning                                                             |
| --------- | ------------------------------------------------------------------- |
| `0`       | No hard errors (or `--skip_errors true` was set)                    |
| `1`       | Hard error found: invalid arg mappings, chains, or expired wrappers |

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


print(old_func_warn_n_times(1))
```

<details>
  <summary>Output: <code>old_func_warn_n_times(1)</code></summary>

```
2
```

</details>

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
  <summary>Output: <code>new_create_session("prod.example.com")</code></summary>

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

## Generating Deprecation Tables

`generate_deprecation_table()` renders discovered wrapper metadata as a Markdown table suitable for embedding in your project documentation. It supports two `style=` options:

- `"compact"` (default) — one row per symbol with a **Current Status** column (`📢 Deprecation Active`, `💥 Past Removal Date`, `ℹ️ No Removal Target`, etc.)
- `"matrix"` — one column per version with `D` (deprecated) and `R` (remove) markers

**Compact style** — one row per symbol with a lifecycle status column:

```python
from tests import collection_deprecate as my_package
from deprecate import generate_deprecation_table

report = generate_deprecation_table(my_package, current_version="1.5", recursive=False)
```

```markdown
<!-- Current version: 1.5 -->
| Original API | API Type | New API | Deprecated | Remove | Current Status |
| :--- | :--- | :--- | :---: | :---: | :--- |
| `my_package.CrossGuardOldClass.__init__` | class constructor | `my_package.CrossGuardClassTargetNew` | v1.0 | v2.0 | 📢 Deprecation Active |
| `my_package.DecoratedDataClass` | dataclass | `my_package.NewDataClass` | v0.5 | v1.0 | 💥 Past Removal Date |
| `my_package.ServiceCls.old_class_method` | classmethod | `—` | v1.0 | v2.0 | 📢 Deprecation Active |
| `my_package.ServiceCls.old_redirect_method` | class method | `my_package.ServiceCls.compute` | v1.0 | v2.0 | 📢 Deprecation Active |
| `my_package.depr_func_no_remove_in` | callable | `—` | v1.0 | — | ℹ️ No Removal Target |
| `my_package.depr_func_same_version` | callable | `—` | v2.0 | v2.0 | 🕒 Scheduled Deprecation |
| `my_package.depr_pow_args` | callable | `my_package.base_pow_args` | v1.0 | v1.3 | 💥 Past Removal Date |
| `my_package.decorated_sum` | callable | `my_package.base_sum_kwargs` | v0.1 | v0.5 | 💥 Past Removal Date |
```

**Matrix style** — one column per version with `D` (deprecated) and `R` (remove) lifecycle markers:

```python
from tests import collection_deprecate as my_package
from deprecate import generate_deprecation_table

matrix = generate_deprecation_table(my_package, current_version="1.5", recursive=False, style="matrix")
```

```markdown
<!-- Current version: 1.5 -->
| Original API | API Type | New API | v0.1 | v0.5 | v1.0 | v1.3 | v2.0 |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| `my_package.CrossGuardOldClass.__init__` | class constructor | `my_package.CrossGuardClassTargetNew` |   |   | D |   | R |
| `my_package.DecoratedDataClass` | dataclass | `my_package.NewDataClass` |   | D |   |   |   |
| `my_package.ServiceCls.old_redirect_method` | class method | `my_package.ServiceCls.compute` |   |   | D |   | R |
| `my_package.depr_func_no_remove_in` | callable | `—` |   |   | D |   |   |
| `my_package.depr_pow_args` | callable | `my_package.base_pow_args` |   |   | D | R |   |
| `my_package.decorated_sum` | callable | `my_package.base_sum_kwargs` | D |   |   |   |   |
```

The table derives all data from `__deprecated__` decorator metadata so it stays in sync with the code automatically. Install the `audit` extra (`pip install pyDeprecate[audit]`) to enable lifecycle status evaluation.

## See also

- [Use Cases](use-cases.md) — deprecation patterns and scenarios that audit tools validate against
- [Getting Started](../getting-started.md) — decorator API reference for `deprecated_in` and `remove_in` parameters
- [Troubleshooting](../troubleshooting.md) — common decorator configuration errors and how to fix them

______________________________________________________________________

Next: [Troubleshooting](../troubleshooting.md) — common errors and how to fix them.
