# pyDeprecate

**pyDeprecate** is a zero-dependency Python library that turns deprecation from a chore into a one-liner. Decorate a function or method with `@deprecated(...)`, or a class/Enum/dataclass with `@deprecated_class(...)`, and the library handles everything else: runtime `FutureWarning` emission, transparent call forwarding to the replacement, and — crucially — automatic documentation.

## The problem it solves

When you deprecate an API, you typically need to:

1. Emit a runtime warning so callers know at call time.
2. Update the docstring so the rendered docs show a deprecation notice.
3. Keep the parameter tables intact so the docs remain readable.

Steps 2 and 3 are easy to forget and tedious to maintain. pyDeprecate automates both.

## Automatic docstring injection

Pass `update_docstring=True` and pyDeprecate rewrites the function's `__doc__` at decoration time — before the documentation tool ever sees it.

```python
from deprecate import deprecated


def new_add(x: int, y: int) -> int: ...


@deprecated(target=new_add, deprecated_in="1.0", remove_in="2.0", update_docstring=True)
def old_add(x: int, y: int) -> int:
    """Add two integers.

    Args:
        x: First operand.
        y: Second operand.

    Returns:
        The sum of *x* and *y*.
    """
    return x + y
```

The injected `__doc__` becomes:

```
Add two integers.

!!! warning "Deprecated in 1.0"
    Will be removed in 2.0.
    Use `...new_add` instead.

Args:
    x: First operand.
    ...
```

The notice is inserted **before** the first `Args:` / `Parameters` section so parameter tables render correctly. Developers writing or reviewing the deprecation do not need to touch the docstring at all — the notice appears automatically in every rendered output.

## Docstring styles

| `docstring_style`    | Output                        | Use when                   |
| -------------------- | ----------------------------- | -------------------------- |
| `"auto"` *(default)* | Detects engine at import time | Most projects — just works |
| `"rst"`              | `.. deprecated::` directive   | Sphinx / autodoc           |
| `"mkdocs"`           | `!!! warning` admonition      | MkDocs Material            |

`"auto"` checks `sys.modules` and `sys.argv` to detect whether MkDocs or Sphinx is driving the build, so the same decorator works in both stacks without changes.

## Live examples

The [API reference](api.md) shows two real cases rendered by this very MkDocs build:

- **Deprecated function with a removed argument** — inline per-argument note *and* a general deprecation block, both injected automatically.
- **Deprecated class** — notice injected into the class docstring via `deprecated_class`.
