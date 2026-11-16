---
description: Compare pyDeprecate with warnings.warn, warnings.deprecated, deprecation, and Deprecated for Python API migration workflows.
---

# Compare Python Deprecation Tools

Python has several ways to signal deprecation. Choose based on whether you need warnings, static checker visibility, or runtime compatibility during an API migration.

## Quick comparison

This page holds the canonical comparison table.

| Feature                 | pyDeprecate | `warnings.warn` |     `deprecation`      |                            `Deprecated`                            | `warnings.deprecated`† |
| ----------------------- | :---------: | :-------------: | :--------------------: | :----------------------------------------------------------------: | :--------------------: |
| Simple warnings         |     ✅      |       ✅        |           ✅           |                                 ✅                                 |           ✅           |
| Auto call forwarding    |     ✅      |       ❌        |           ❌           |                                 ❌                                 |           ❌           |
| Generator / async       |     ✅      |       ❌        |           ❌           |                                 ❌                                 |           ❌           |
| Argument mapping        |     ✅      |       ❌        |           ❌           |                                 ❌                                 |           ❌           |
| Argument deprecation    |     ✅      |       ✍️        |           ❌           |                      via `deprecated.params`                       |           ❌           |
| Class / Enum proxy      |     ✅      |       ❌        |           ❌           |                                 ❌                                 |           ❌           |
| Docstring injection     |     ✅      |       ❌        |           ✅           | via `deprecated.sphinx`, `deprecated.google` or `deprecated.numpy` |           ❌           |
| Version tracking        |     ✅      |       ✍️        |           ✅           |                                 ✅                                 |           ❌           |
| Prevent log spam        |     ✅      |       ✍️        |           ❌           |                                 ✍️                                 |           ❌           |
| Zero runtime deps       |     ✅      |       ✅        |           ❌           |                                 ❌                                 |           †            |
| Custom streams          |     ✅      |       ✍️        |           ❌           |                                 ❌                                 |           ❌           |
| CI audit tools          |     ✅      |       ❌        |           ❌           |                                 ❌                                 |           ❌           |
| Testing helpers         |     ✅      |       ❌        | `@fail_if_not_removed` |                                 ❌                                 |           ❌           |
| Static checker signal   |     ✍️      |       ❌        |           ❌           |                                 ❌                                 |           ✅           |
| Stacked argument remaps |     ✅      |       ❌        |           ❌           |                                 ❌                                 |           ❌           |
| Sphinx plugin           |     ✅      |       ❌        |           ❌           |                                 ❌                                 |           ❌           |
| MkDocs plugin           |     ✅      |       ❌        |           ❌           |                                 ❌                                 |           ❌           |

- ✅ = supported, ❌ = not supported, ✍️ = possible but requires manual work. For pyDeprecate's static checker signal, stack `@warnings.deprecated` above `@deprecated`.
- Plain warning decorators stack too: each `deprecation.deprecated` layer warns, then calls the next. The "Stacked argument remaps" row covers stacked layers that each remap arguments for one release, with unsupported combinations flagged by a decoration-time `UserWarning`.
- The `Deprecated` cells were checked against the `Deprecated` 3.0.1 source. Its log-spam control is a warnings filter `action` such as `"once"`.
- † `warnings.deprecated` is in the standard library on Python 3.13+ (PEP 702) and available as the `typing_extensions.deprecated` backport on older versions.

### Roles and enforcement

| Tool                                                                                         | Documented role                                                                         | Migration and enforcement                                                                                          | Docstring notices                                                                                                                                                                                     |
| -------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`warnings.warn`](https://docs.python.org/3/library/warnings.html#warnings.warn)             | Emit one-off internal warnings.                                                         | Write forwarding yourself; warning filters can escalate warnings to errors.                                        | Write notices yourself.                                                                                                                                                                               |
| [`warnings.deprecated`](https://docs.python.org/3/library/warnings.html#warnings.deprecated) | Signal deprecation to static checkers, with optional runtime warnings, on Python 3.13+. | PEP 702 does not provide forwarding, argument mapping, or CI audits.                                               | Not assessed here.                                                                                                                                                                                    |
| [`deprecation`](https://deprecation.readthedocs.io/en/latest/)                               | Decorate functions with version-aware warnings.                                         | Emits `UnsupportedWarning` when `current_version >= removed_in`; `@fail_if_not_removed` fails tests that catch it. | Appends deprecation details.                                                                                                                                                                          |
| [`Deprecated`](https://deprecated.readthedocs.io/en/latest/tutorial.html)                    | Decorate functions, classes, or methods with warnings.                                  | `action="error"` escalates warnings; this is not version-driven enforcement.                                       | Yes, via [`deprecated.sphinx`](https://deprecated.readthedocs.io/en/latest/api.html#module-deprecated.sphinx), or Google and NumPy docstring sections via `deprecated.google` and `deprecated.numpy`. |
| **pyDeprecate** *(this library)*                                                             | Keep callers compatible during API migrations.                                          | Forward calls, remap arguments, proxy classes/objects, test warnings, and audit deadlines in CI.                   | Optional Sphinx or MkDocs notices with `update_docstring=True`.                                                                                                                                       |

!!! note "Comparison scope"

    Unverified feature verdicts are omitted. This is a positioning guide, not a complete audit of every tool's features. [Report inaccuracies](https://github.com/Borda/pyDeprecate/issues).

!!! warning "A warning is not an exception"

    `deprecation.UnsupportedWarning` is a `DeprecationWarning` subclass. It becomes an exception only when warning filters escalate it.

    The version comparison happens at decoration time. See the [implementation](https://github.com/briancurtin/deprecation/blob/master/deprecation.py).

## Use `warnings.warn` when

Use raw warnings for small internal messages where callers do not need a compatibility shim.

```python
import warnings


def internal_hook() -> None:
    warnings.warn("internal_hook is deprecated", DeprecationWarning, stacklevel=2)


internal_hook()  # warns: DeprecationWarning
print("Hook completed")
```

<details><summary>Output: <code>internal_hook()</code></summary>

```text
Hook completed
```

</details>

## Use `warnings.deprecated` when

Use PEP 702 for static checker visibility when runtime behavior does not need to change.

```python
from typing_extensions import deprecated
from tests.collections.api_current import core as current

# NEW API — compute a sum with the current implementation.
compute_sum = current.base_sum_kwargs


# DEPRECATED API — mark the previous addition entry point for static checkers.
@deprecated("Use compute_sum instead.", category=None)
def addition(a: int, b: int) -> int:
    return compute_sum(a, b)


print(addition(1, 2))
```

<details><summary>Output: <code>addition(1, 2)</code></summary>

```text
3
```

</details>

!!! note "Python versions and runtime cost"

    `warnings.deprecated` is in the standard library on Python 3.13+. Use the [`typing_extensions.deprecated` backport](https://typing-extensions.readthedocs.io/en/latest/#deprecated) on earlier versions. With `category=None`, the decorator returns the original object, adding no per-call wrapper overhead.

    Suppressing the warning through a filter does not remove that wrapper. See [PEP 702](https://peps.python.org/pep-0702/#runtime-behavior).

## Use pyDeprecate when

Use pyDeprecate when old callers must keep working while receiving a deprecation warning.

```python
from deprecate import deprecated
from tests.collections.api_current import core as current
from tests.collections.api_deprecated import core

# NEW API — compute a sum with the current implementation.
compute_sum = current.base_sum_kwargs

# DEPRECATED API — addition forwards to compute_sum.
addition = deprecated(target=compute_sum, deprecated_in="1.2", remove_in="2.0")(core.original_sum)
print(addition(1, 2))  # warns: FutureWarning
```

<details><summary>Output: <code>addition(1, 2)</code></summary>

```text
3
```

</details>

!!! note "Decorator and assignment forms"

    These examples wrap imported collection functions to keep them executable. In your library, apply the same configuration as `@deprecated(...)` above the deprecated definition. Forwarding bypasses the source body.

## Migrating from warnings.warn

Replace manual forwarding boilerplate with a single decorator.

### Before

```python
import warnings
from tests.collections.api_current import core as current

# NEW API — compute a sum with the current implementation.
compute_sum = current.base_sum_kwargs


# DEPRECATED API — addition warns and forwards manually.
def addition(a: int, b: int) -> int:
    warnings.warn("addition is deprecated; use compute_sum instead.", DeprecationWarning, stacklevel=2)
    return compute_sum(a, b)


print(addition(1, 2))  # warns: DeprecationWarning
```

<details><summary>Output: <code>addition(1, 2)</code></summary>

```text
3
```

</details>

### After

```python
from deprecate import deprecated
from tests.collections.api_current import core as current
from tests.collections.api_deprecated import core

# NEW API — compute a sum with the current implementation.
compute_sum = current.base_sum_kwargs

# DEPRECATED API — addition forwards without manual warning code.
addition = deprecated(target=compute_sum, deprecated_in="1.2", remove_in="2.0")(core.original_sum)
print(addition(1, 2))  # warns: FutureWarning
```

<details><summary>Output: <code>addition(1, 2)</code></summary>

```text
3
```

</details>

## Migrating from `deprecation`

Replace the [version-aware decorator](https://deprecation.readthedocs.io/en/latest/#using-deprecated) and manual forwarding with a pyDeprecate target.

### Before

```python
# phmdoctest:skip — shows the third-party API being migrated from
import deprecation
from tests.collections.api_current import core as current

# NEW API — compute a sum with the current implementation.
compute_sum = current.base_sum_kwargs


# DEPRECATED API — addition warns, then forwards manually.
@deprecation.deprecated(
    deprecated_in="1.2", removed_in="2.0", current_version="1.2", details="Use compute_sum instead."
)
def addition(a: int, b: int) -> int:
    """Add two values through the previous entry point."""
    return compute_sum(a, b)


print(addition(1, 2))  # warns: DeprecationWarning
```

<details><summary>Output: <code>addition(1, 2)</code></summary>

```text
3
```

</details>

### After

```python
from deprecate import deprecated
from tests.collections.api_current import core as current
from tests.collections.api_deprecated import core

# NEW API — compute a sum with the current implementation.
compute_sum = current.base_sum_kwargs

# DEPRECATED API — addition delegates forwarding to pyDeprecate.
addition = deprecated(target=compute_sum, deprecated_in="1.2", remove_in="2.0")(core.original_sum)
print(addition(1, 2))  # warns: FutureWarning
```

<details><summary>Output: <code>addition(1, 2)</code></summary>

```text
3
```

</details>

!!! warning "Migration differences"

    Rename `removed_in` to `remove_in`. pyDeprecate defaults to `FutureWarning`. It does not use `current_version` to switch warning classes.

    Use [fatal mode](#turning-the-warning-into-an-error-at-a-chosen-version) or [CI expiry checks](audit-policy.md#enforcing-removal-deadlines) for enforcement. Enable `update_docstring=True` if you need injected notices.

## Migrating from `Deprecated`

Replace the [`Deprecated` warning decorator](https://deprecated.readthedocs.io/en/latest/tutorial.html) and manual forwarding with a pyDeprecate target.

### Before

```python
# phmdoctest:skip — shows the third-party API being migrated from
from deprecated import deprecated
from tests.collections.api_current import core as current

# NEW API — compute a sum with the current implementation.
compute_sum = current.base_sum_kwargs


# DEPRECATED API — addition warns, then forwards manually.
@deprecated(version="1.2", reason="Use compute_sum instead.")
def addition(a: int, b: int) -> int:
    return compute_sum(a, b)


print(addition(1, 2))  # warns: DeprecationWarning
```

<details><summary>Output: <code>addition(1, 2)</code></summary>

```text
3
```

</details>

### After

```python
from deprecate import deprecated
from tests.collections.api_current import core as current
from tests.collections.api_deprecated import core

# NEW API — compute a sum with the current implementation.
compute_sum = current.base_sum_kwargs

# DEPRECATED API — addition delegates forwarding to pyDeprecate.
addition = deprecated(target=compute_sum, deprecated_in="1.2", remove_in="2.0")(core.original_sum)
print(addition(1, 2))  # warns: FutureWarning
```

<details><summary>Output: <code>addition(1, 2)</code></summary>

```text
3
```

</details>

!!! note "Configuration changes"

    Translate `version` to `deprecated_in` and supply a `remove_in` deadline. The target supplies the replacement guidance in the warning. pyDeprecate defaults to `FutureWarning`.

    Replace `action="error"` with `as_exception=True` when calls must fail. This raises `DeprecatedError` instead of a warning exception.

## Turning the warning into an error at a chosen version

Compute `as_exception` from your package version to choose when deprecated calls must fail.

```python
from deprecate import DeprecatedError, TargetMode, deprecated_callable
from tests.collections.api_current import core as current

PACKAGE_VERSION = (1, 3)  # In a real package, parse your own __version__.
ERROR_IN = (1, 3)  # Callers must stop using the previous name in this release.

# NEW API — increment a value with the current implementation.
increment_value = current.increment_value

# DEPRECATED API — the previous entry point becomes fatal at ERROR_IN.
increment = deprecated_callable(TargetMode.NOTIFY, "1.0", "2.0", as_exception=PACKAGE_VERSION >= ERROR_IN)(
    increment_value
)

try:
    increment(1)
except DeprecatedError as err:
    print(err)
```

<details><summary>Output: <code>increment(1)</code></summary>

```text
The `increment_value` was deprecated since v1.0. It will be removed in v2.0.
```

</details>

With plain `warnings.warn`, add an explicit `raise` or configure warning filters to escalate it. pyDeprecate's `as_exception=True` raises `DeprecatedError` before executing the source or target.

As a release-time backstop, `pydeprecate expiry` in CI exits `1` when a wrapper is past its `remove_in` deadline. See [Fatal deprecations](functions.md#fatal-deprecations) for the full behaviour.

## Helping callers migrate

Make deprecated usage fail in callers' CI so they can replace affected calls before removal.

1. Run the test suite with the relevant warning category promoted to an error.

    ```bash
    python -W error::FutureWarning -m pytest
    python -W error::DeprecationWarning -m pytest
    ```

2. Keep the policy in `pytest.ini` when it should apply to every pytest run.

    ```ini
    [pytest]
    filterwarnings =
        error::FutureWarning
    ```

3. Replace each reported call using its warning's migration message, then rerun the suite.

!!! note "Warning categories and coverage"

    pyDeprecate uses `FutureWarning` by default. Use `error::DeprecationWarning` for tools emitting that category. These filters affect Python warnings, not custom logging streams or silent wrappers.

    Only executed paths can reveal deprecated calls. See [Python warning filters](https://docs.python.org/3/library/warnings.html#the-warnings-filter) and [pytest warning configuration](https://docs.pytest.org/en/stable/how-to/capture-warnings.html#controlling-warnings).

## Other tools

- [`debtcollector`](https://docs.openstack.org/debtcollector/latest/) provides deprecation patterns, including removals, moves, and renames.
- [`deprecated-params`](https://pypi.org/project/deprecated-params/) wraps functions, classes, and methods to deprecate keyword parameters.
- `wrapt` is a dependency of [`Deprecated`](https://pypi.org/project/Deprecated/). It is not a peer deprecation tool in this comparison.
- [`pytest.deprecated_call`](https://docs.pytest.org/en/stable/how-to/capture-warnings.html#ensuring-code-triggers-a-deprecation-warning) checks for `DeprecationWarning`, `PendingDeprecationWarning`, or `FutureWarning`. It is a test helper, not a deprecation decorator.

!!! note "Unverified capabilities"

    Version-driven removal enforcement for `debtcollector` was not verified. No verdict is assigned here. Only the documented keyword-parameter purpose of `deprecated-params` is compared.

### Testing a migrated caller

Use `deprecate.assert_no_warnings` to check that a migrated call no longer warns; `pytest.deprecated_call` checks the opposite expectation.

```python
from deprecate import assert_no_warnings
from tests.collections.api_current import core as current

# NEW API — callers use the replacement directly after migration.
compute_sum = current.base_sum_kwargs
with assert_no_warnings(FutureWarning):
    result = compute_sum(1, 2)
print(result)
```

<details><summary>Output: <code>result</code></summary>

```text
3
```

</details>

## Decision guide

- Use `warnings.warn` when you only need an internal warning without forwarding or CI audits.
- Use `warnings.deprecated` on Python 3.13+ when you only need static checker visibility.
- Use pyDeprecate when public callers need runtime compatibility.
- Use pyDeprecate for `async def` functions, generator functions, or async generators.
- Use `TargetMode.ARGS_REMAP` for argument renames or dropped argument compatibility.
- Use `deprecated_class` or `deprecated_instance` for class, constant, or object alias compatibility.
- Use pyDeprecate audit tools for removal deadline checks in CI.

## Fair strengths in alternative tools worth considering

- **`warnings.deprecated`** provides native static diagnostics in mypy, pyright, and IDEs. With `category=None`, it adds no per-call wrapper overhead.
- **`deprecation`** includes `@fail_if_not_removed` for test-failure enforcement when removal deadlines are reached.
- **`Deprecated`** offers `deprecated.sphinx.versionadded` and `deprecated.sphinx.versionchanged` to inject Sphinx lifecycle directives into docstrings. Its `deprecated.params` decorator deprecates individual parameters.
- **`warnings.warn`** needs no dependency and has a small surface for internal notices.

## Related pages

- [Use Cases](use-cases.md)
- [Audit Tools](audit.md)
- [Troubleshooting](../troubleshooting.md)
