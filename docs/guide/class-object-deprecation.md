---
description: Deprecate Python classes, constants, and object aliases with pyDeprecate while preserving runtime compatibility during API migrations.
---

# Deprecate Classes and Object Aliases

Use pyDeprecate when a public class, constant, enum-like object, or module-level alias must remain importable during a migration window.

## Rename a class

Use `deprecated_class` when old callers should instantiate the replacement class and receive a deprecation warning.

```python
from deprecate import deprecated_class


class NewClient:
    def request(self) -> str:
        return "ok"


@deprecated_class(
    target=NewClient,
    deprecated_in="1.2",
    remove_in="2.0",
    message="Use NewClient instead.",
)
class OldClient:
    pass
```

## Keep a deprecated constant or object alias

Use `deprecated_instance` when the value itself is still valid but the old public name should disappear in a future release.

```python
from deprecate import deprecated_instance


DEFAULT_TIMEOUT_SECONDS = 30
TIMEOUT = deprecated_instance(
    DEFAULT_TIMEOUT_SECONDS,
    deprecated_in="1.2",
    remove_in="2.0",
    message="Use DEFAULT_TIMEOUT_SECONDS instead.",
)
```

## When this is better than raw warnings

Raw `warnings.warn` does not preserve class identity, proxy object access, or central removal metadata. pyDeprecate keeps the migration policy next to the compatibility alias and makes the deadline auditable.

## Agent rules

- Use `deprecated_class(target=NewClass)` for class renames.
- Use `deprecated_instance(obj, ...)` for constants, sentinels, and object aliases.
- Include `deprecated_in`, `remove_in`, and a migration message.
- Do not replace a class alias with a plain wrapper function unless the public API intentionally changes from class to function.

## Related pages

- [Use Cases](use-cases.md)
- [Python deprecation decorator](python-deprecation-decorator.md)
- [API Migration CI](api-migration-ci.md)
- [Agent Recipes](agent-recipes.md)
