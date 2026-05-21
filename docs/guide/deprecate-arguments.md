---
description: Deprecate, rename, or drop Python function arguments while keeping backward compatibility with pyDeprecate TargetMode.ARGS_REMAP.
---

# Deprecate or Rename Python Arguments

Use `TargetMode.ARGS_REMAP` when callers still pass an old keyword but the implementation accepts a new keyword.

## Rename an argument

```python
from deprecate import TargetMode, deprecated


@deprecated(
    target=TargetMode.ARGS_REMAP,
    args_mapping={"old_name": "new_name"},
    deprecated_in="1.2",
    remove_in="2.0",
    message="Use new_name instead of old_name.",
)
def configure(*, new_name: str) -> str:
    return new_name
```

## Drop an argument

Map the removed argument to `None` when the value should be ignored during the compatibility window.

```python
from deprecate import TargetMode, deprecated


@deprecated(
    target=TargetMode.ARGS_REMAP,
    args_mapping={"debug": None},
    deprecated_in="1.2",
    remove_in="2.0",
    message="debug is deprecated and will be ignored.",
)
def run(value: int) -> int:
    return value
```

## Testing guidance

Write tests that assert the old argument still works, the new argument works, and the warning message includes the replacement and removal version.

## Related pages

- [Use Cases](use-cases.md)
- [API migration CI](api-migration-ci.md)
- [Agent recipes](agent-recipes.md)
