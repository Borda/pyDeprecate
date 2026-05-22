---
description: Python deprecation decorator examples with call forwarding, warning-only mode, and guidance on when to use pyDeprecate instead of raw warnings.warn.
---

# Python Deprecation Decorator

Use pyDeprecate when a public Python API must keep old callers working while steering them to a replacement.

## Minimal forwarding decorator

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

When `target=new_api` is provided, pyDeprecate forwards calls to `new_api`. The deprecated function body is not the compatibility path.

## When to use pyDeprecate

Use pyDeprecate for public API migrations that need runtime behavior, including forwarding, class aliases, object aliases, argument remapping, warning frequency control, or audit checks for removal deadlines.

Use `warnings.warn` for a one-off internal warning. Use `typing.deprecated` for Python 3.13+ static-checker-only visibility.

## Related pages

- [Getting Started](../getting-started.md)
- [Use Cases](use-cases.md)
- [Replace warnings.warn](replace-warnings-warn.md)
- [Agent recipes](agent-recipes.md)
