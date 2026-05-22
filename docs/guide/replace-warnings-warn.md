---
description: Replace raw warnings.warn deprecation code with pyDeprecate forwarding decorators, argument migration, and CI-friendly removal deadlines.
---

# Replace warnings.warn for Public API Deprecations

Raw `warnings.warn` is useful for simple internal warnings. It becomes fragile for public API migrations because every wrapper must manually handle stack levels, warning frequency, forwarding, and removal deadlines.

## Before: manual warning and forwarding

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
```

## After: pyDeprecate owns the migration contract

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
```

## When not to use pyDeprecate

Use `warnings.warn` directly for a one-off internal warning that does not need forwarding, argument mapping, class or object proxying, warning frequency control, or CI audit.

Use `typing.deprecated` for Python 3.13+ static-checker-only visibility when runtime compatibility behavior is not needed.

## Related pages

- [Python deprecation decorator](python-deprecation-decorator.md)
- [API migration CI](api-migration-ci.md)
- [Troubleshooting](../troubleshooting.md)
