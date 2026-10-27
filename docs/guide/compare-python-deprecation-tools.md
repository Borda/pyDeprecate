---
description: Compare pyDeprecate with warnings.warn, warnings.deprecated, deprecation, and Deprecated for Python API migration workflows.
---

# Compare Python Deprecation Tools

Python has several ways to signal deprecation. The right choice depends on whether you need only a warning, static type checker visibility, or full runtime compatibility during an API migration.

## Quick comparison

| Tool                             | Best for                               | Runtime forwarding | Generator / async | Argument rename | Argument deprecation | Class/object alias |        Testing helpers | CI audit | Static checker signal |
| -------------------------------- | -------------------------------------- | -----------------: | ----------------: | --------------: | -------------------: | -----------------: | ---------------------: | -------: | --------------------: |
| `warnings.warn`                  | One-off internal warnings              |                 No |                No |              No |                   No |                 No |                     No |       No |                    No |
| `warnings.deprecated`            | Python 3.13+ static-checker visibility |                 No |                No |              No |                   No |                 No |                     No |       No |                   Yes |
| `deprecation`                    | Simple decorator warnings              |                 No |                No |              No |                   No |                 No | `@fail_if_not_removed` |       No |                    No |
| `Deprecated`                     | Simple decorator warnings              |                 No |                No |              No |                   No |                 No |                     No |       No |                    No |
| **pyDeprecate** *(this library)* | Full API migration compatibility       |                Yes |               Yes |             Yes |                  Yes |                Yes |                    Yes |      Yes |          Via stacking |

## Use `warnings.warn` when

Use raw warnings for small internal messages where callers do not need a compatibility shim.

```python
import warnings


def internal_hook() -> None:
    warnings.warn("internal_hook is deprecated", DeprecationWarning, stacklevel=2)
```

## Use `warnings.deprecated` when

Use `warnings.deprecated`/`typing_extensions.deprecated` for static-checker-only visibility when runtime behavior does not need to change.

```python
from typing_extensions import deprecated


@deprecated("Use detect_objects instead.")
def detect() -> None:
    pass
```

## Use pyDeprecate when

Use pyDeprecate when old callers must keep working while receiving a deprecation warning.

```python
from deprecate import deprecated


def detect_objects(value: int) -> int:
    return value + 1


@deprecated(
    target=detect_objects,
    deprecated_in="1.2",
    remove_in="2.0",
    # FutureWarning: "The `detect` was deprecated since v1.2 in favor of `detect_objects`. It will be removed in v2.0."
)
def detect(value: int) -> int:
    pass  # body never runs — pyDeprecate intercepts all calls before reaching here


print(detect(1))
```

<details>
  <summary>Output: <code>detect(1)</code></summary>

```
2
```

</details>

## Migrating from warnings.warn

Replace manual forwarding boilerplate with a single decorator.

**Before: manual warning and forwarding**

```python
import warnings


def detect_objects(value: int) -> int:
    return value + 1


def detect(value: int) -> int:
    warnings.warn(
        "detect is deprecated; use detect_objects instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return detect_objects(value)


print(detect(1))
```

<details>
  <summary>Output: <code>detect(1)</code></summary>

```
2
```

</details>

**After: pyDeprecate owns the migration contract**

```python
from deprecate import deprecated


def detect_objects(value: int) -> int:
    return value + 1


@deprecated(
    target=detect_objects,
    deprecated_in="1.2",
    remove_in="2.0",
    # FutureWarning: "The `detect` was deprecated since v1.2 in favor of `detect_objects`. It will be removed in v2.0."
)
def detect(value: int) -> int:
    pass  # body never runs — pyDeprecate intercepts all calls before reaching here


print(detect(1))
```

<details>
  <summary>Output: <code>detect(1)</code></summary>

```
2
```

</details>

## Turning the warning into an error at a chosen version

With plain `warnings.warn` the escalation is a manual `raise` you must remember to add. With pyDeprecate, `as_exception=True` raises `DeprecatedError` instead of warning, and a version comparison decides when it flips. No new API is involved: pass `as_exception` a boolean computed from your package version and a release of your choosing (`ERROR_IN` below).

```python
from deprecate import DeprecatedError, TargetMode, deprecated_callable

PACKAGE_VERSION = (1, 3)  # in a real package: parse your own `__version__`
ERROR_IN = (1, 3)  # the release where callers must stop using the old name


@deprecated_callable(TargetMode.NOTIFY, "1.0", "2.0", as_exception=PACKAGE_VERSION >= ERROR_IN)
def detect_legacy(value: int) -> int:
    return value


try:
    detect_legacy(1)
except DeprecatedError as err:
    print(err)
```

<details>
  <summary>Output: <code>detect_legacy(1)</code></summary>

```
The `detect_legacy` was deprecated since v1.0. It will be removed in v2.0.
```

</details>

As the release-time backstop, `pydeprecate expiry` in CI exits `1` when a wrapper is past its `remove_in` deadline. See [Fatal deprecations](functions.md#fatal-deprecations) for the full behaviour.

## Decision guide

- Need only an internal warning: use `warnings.warn`.
- Need static checker visibility only: use `warnings.deprecated` on Python 3.13+.
- Need public runtime compatibility: use pyDeprecate.
- Need to deprecate `async def` functions, generator functions, or async generators: use pyDeprecate.
- Need argument rename or dropped argument compatibility: use pyDeprecate with `TargetMode.ARGS_REMAP`.
- Need class, constant, or object alias compatibility: use `deprecated_class` or `deprecated_instance`.
- Need removal deadline checks in CI: use pyDeprecate audit tools.
- One-off internal warning with no forwarding or CI audit: `warnings.warn` is sufficient.

## Fair strengths in alternative tools worth considering

To keep this comparison fair, here are capabilities where alternatives can be the better fit:

- **`warnings.deprecated`**: native static diagnostics in mypy/pyright/IDEs without adding runtime wrappers; adds no per-call overhead when the warning category is suppressed.
- **`deprecation`**: includes the `@fail_if_not_removed` test decorator for direct test-failure enforcement when removal deadlines are reached.
- **`Deprecated`**: `deprecated.sphinx` includes `@versionadded` and `@versionchanged` decorators that inject Sphinx directives into docstrings for lifecycle annotation in Sphinx-built API docs.
- **`warnings.warn`**: no dependency and minimal surface area for quick internal notices where migration behavior is not needed.

## Related pages

- [Use Cases](use-cases.md)
- [Audit Tools](audit.md)
- [Troubleshooting](../troubleshooting.md)
