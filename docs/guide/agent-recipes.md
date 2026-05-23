---
description: Coding-agent guide for pyDeprecate imports, migration recipes, anti-patterns, and links to llms.txt and llms-full.txt.
---

# Coding Agent Recipes

This page is for coding assistants that need to add a correct deprecation cycle to Python code.

## Rules for agents

- Install package: `pyDeprecate`.
- Import package: `deprecate`.
- Prefer `@deprecated(target=new_callable)` for callable renames.
- Prefer `TargetMode.ARGS_REMAP` for argument renames or removals.
- Prefer `deprecated_class` for class renames.
- Prefer `deprecated_instance` for object aliases.
- Always include `deprecated_in`, `remove_in`, and a migration message when available.

## Function rename

```python
from deprecate import deprecated


def new_api(value: int) -> int:
    return value + 1


@deprecated(target=new_api, deprecated_in="1.2", remove_in="2.0")
def old_api(value: int) -> int:
    raise RuntimeError("Forwarded by pyDeprecate.")
```

## Argument rename

```python
from deprecate import TargetMode, deprecated


@deprecated(
    target=TargetMode.ARGS_REMAP,
    args_mapping={"old": "new"},
    deprecated_in="1.2",
    remove_in="2.0",
)
def api(*, new: str) -> str:
    return new
```

## Anti-patterns

- Do not write `import pydeprecate` in Python code.
- Do not use `target=True` for argument remapping.
- Do not use `target=None` for warning-only behavior.
- Do not call the replacement function manually inside the deprecated function body when pyDeprecate is already forwarding.

## Agent context files

- [llms.txt](https://borda.github.io/pyDeprecate/llms.txt)
- [llms-full.txt](https://borda.github.io/pyDeprecate/llms-full.txt)
