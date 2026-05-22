---
description: Compare pyDeprecate with warnings.warn, typing.deprecated, deprecation, and Deprecated for Python API migration workflows.
---

# Compare Python Deprecation Tools

Python has several ways to signal deprecation. The right choice depends on whether you need only a warning, static type checker visibility, or full runtime compatibility during an API migration.

## Quick comparison

| Tool                             | Best for                               | Runtime forwarding | Argument rename | Class/object alias | CI audit |
| -------------------------------- | -------------------------------------- | -----------------: | --------------: | -----------------: | -------: |
| `warnings.warn`                  | One-off internal warnings              |                 No |              No |                 No |       No |
| `typing.deprecated`              | Python 3.13+ static-checker visibility |                 No |              No |                 No |       No |
| `deprecation`                    | Simple decorator warnings              |                 No |              No |                 No |       No |
| `Deprecated`                     | Simple decorator warnings              |                 No |              No |                 No |       No |
| **pyDeprecate** *(this library)* | Public API migration compatibility     |                Yes |             Yes |                Yes |      Yes |

## Use `warnings.warn` when

Use raw warnings for small internal messages where callers do not need a compatibility shim.

```python
import warnings


def internal_hook() -> None:
    warnings.warn("internal_hook is deprecated", DeprecationWarning, stacklevel=2)
```

## Use `typing.deprecated` when

Use `typing.deprecated`/`typing_extensions.deprecated` for static-checker-only visibility when runtime behavior does not need to change.

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
```

## Decision guide

- Need only an internal warning: use `warnings.warn`.
- Need static checker visibility only: use `typing.deprecated` on Python 3.13+.
- Need public runtime compatibility: use pyDeprecate.
- Need argument rename or dropped argument compatibility: use pyDeprecate with `TargetMode.ARGS_REMAP`.
- Need class, constant, or object alias compatibility: use `deprecated_class` or `deprecated_instance`.
- Need removal deadline checks in CI: use pyDeprecate audit tools.

## Related pages

- [Replace warnings.warn](replace-warnings-warn.md)
- [Deprecate Arguments](deprecate-arguments.md)
- [Deprecate Classes and Object Aliases](class-object-deprecation.md)
- [API Migration CI](api-migration-ci.md)
