---
description: Compare pyDeprecate with warnings.warn, warnings.deprecated, deprecation, and Deprecated for Python API migration workflows.
---

# Compare Python Deprecation Tools

Python has several ways to signal deprecation. The right choice depends on whether you need only a warning, static type checker visibility, or full runtime compatibility during an API migration.

## Quick comparison

| Tool                             | Best for                               | Runtime forwarding | Argument rename | Class/object alias | CI audit | Static checker signal |
| -------------------------------- | -------------------------------------- | -----------------: | --------------: | -----------------: | -------: | --------------------: |
| `warnings.warn`                  | One-off internal warnings              |                 No |              No |                 No |       No |                    No |
| `warnings.deprecated`            | Python 3.13+ static-checker visibility |                 No |              No |                 No |       No |                   Yes |
| `deprecation`                    | Simple decorator warnings              |                 No |              No |                 No |       No |                    No |
| `Deprecated`                     | Simple decorator warnings              |                 No |              No |                 No |       No |                    No |
| **pyDeprecate** *(this library)* | Public API migration compatibility     |                Yes |             Yes |                Yes |      Yes |          Via stacking |

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


@deprecated("Use new_api instead.")
def old_api() -> None:
    pass
```

## Use pyDeprecate when

Use pyDeprecate when old callers must keep working while receiving a deprecation warning.

```python
from deprecate import deprecated


def new_api(value: int) -> int:
    return value + 1


@deprecated(
    target=new_api,
    deprecated_in="1.2",
    remove_in="2.0",
    # FutureWarning: "The `old_api` was deprecated since v1.2 in favor of `new_api`. It will be removed in v2.0."
)
def old_api(value: int) -> int:
    raise RuntimeError("Forwarded by pyDeprecate.")


print(old_api(1))
```

<details>
  <summary>Output: <code>old_api(1)</code></summary>

```
2
```

</details>

## Migrating from warnings.warn

Replace manual forwarding boilerplate with a single decorator.

**Before: manual warning and forwarding**

```python
import warnings


def new_api(value: int) -> int:
    return value + 1


def old_api(value: int) -> int:
    warnings.warn(
        "old_api is deprecated; use new_api instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return new_api(value)


print(old_api(1))
```

<details>
  <summary>Output: <code>old_api(1)</code></summary>

```
2
```

</details>

**After: pyDeprecate owns the migration contract**

```python
from deprecate import deprecated


def new_api(value: int) -> int:
    return value + 1


@deprecated(
    target=new_api,
    deprecated_in="1.2",
    remove_in="2.0",
    # FutureWarning: "The `old_api` was deprecated since v1.2 in favor of `new_api`. It will be removed in v2.0."
)
def old_api(value: int) -> int:
    raise RuntimeError("Forwarded by pyDeprecate; this body is not used.")


print(old_api(1))
```

<details>
  <summary>Output: <code>old_api(1)</code></summary>

```
2
```

</details>

## Decision guide

- Need only an internal warning: use `warnings.warn`.
- Need static checker visibility only: use `warnings.deprecated` on Python 3.13+.
- Need public runtime compatibility: use pyDeprecate.
- Need argument rename or dropped argument compatibility: use pyDeprecate with `TargetMode.ARGS_REMAP`.
- Need class, constant, or object alias compatibility: use `deprecated_class` or `deprecated_instance`.
- Need removal deadline checks in CI: use pyDeprecate audit tools.
- One-off internal warning with no forwarding or CI audit: `warnings.warn` is sufficient.

## Meaningful strengths in alternatives

To keep this comparison fair, here are capabilities where alternatives can be the better fit:

- **`warnings.deprecated`**: native static diagnostics in mypy/pyright/IDEs without adding runtime wrappers.
- **`deprecation`**: includes the `@fail_if_not_removed` test decorator for direct test-failure enforcement when removal deadlines are reached.
- **`Deprecated`**: `deprecated.sphinx` includes `@versionadded` and `@versionchanged` decorators that inject Sphinx directives into docstrings for lifecycle annotation in Sphinx-built API docs.
- **`warnings.warn`**: no dependency and minimal surface area for quick internal notices where migration behavior is not needed.

## Related pages

- [Use Cases](use-cases.md)
- [Audit Tools](audit.md)
- [Troubleshooting](../troubleshooting.md)
