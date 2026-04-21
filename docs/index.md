---
id: index
description: >-
  pyDeprecate — zero-dependency Python library for deprecating functions,
  methods, and classes with automatic call forwarding, argument mapping, and
  CI/CD audit utilities. Python 3.9+.
---

# pyDeprecate

Renaming a function or retiring an argument by hand means writing a wrapper, getting `warnings.warn` right (correct category, correct `stacklevel`), manually forwarding every argument, and repeating that for every deprecated symbol — with no mechanism to enforce removal when the deadline arrives. **pyDeprecate** collapses that entire process into a single decorator call and gives you CI tools to make sure deprecated code doesn't quietly outlive its deadline.

```python
from deprecate import deprecated


# New function which replaces `addition`
def compute_sum(x: int, y: int) -> int:
    return x + y


@deprecated(target=compute_sum, deprecated_in="1.0", remove_in="2.0")
def addition(x: int, y: int) -> int: ...
```

Calling `addition(1, 2)` now emits a `FutureWarning` and transparently forwards to `compute_sum`.

## Features

- **Automatic call forwarding** — no manual `*args/**kwargs` delegation; the decorator routes every call to the replacement, including positional and keyword arguments.
- **Argument mapping** — `args_mapping={"old": "new"}` handles argument renames across the API boundary; no custom forwarding code needed.
- **Class and Enum support** — `@deprecated_class` wraps entire classes, Enums, and dataclasses in a transparent proxy; `isinstance` and `issubclass` checks continue to work correctly.
- **Configurable deprecation frequency** — `num_warns=1` (default) emits the deprecation message once per function, not on every call, so users aren't flooded; set to `-1` to always emit or `N` for exactly N messages.
- **Docstring injection** — `update_docstring=True` appends a Sphinx `.. deprecated::` notice automatically, so rendered API docs stay accurate without manual edits.
- **CI audit tools** — `validate_deprecation_expiry()` catches functions whose `remove_in` version has passed, `validate_deprecation_chains()` detects double-deprecation chains, and `find_deprecation_wrappers()` surfaces misconfigured `args_mapping` keys before they silently do nothing.
- **Zero runtime dependencies** — nothing added to `install_requires`.
- **Python 3.9+** supported.

## Installation

```bash
pip install pyDeprecate
```

For CI audit features that compare version strings:

```bash
pip install "pyDeprecate[audit]"
```

!!! tip "The `[audit]` extra is only needed for `validate_deprecation_expiry`"

    The base install (`pip install pyDeprecate`) includes `@deprecated`, `@deprecated_class`, `deprecated_instance`, and all other audit functions. Only `validate_deprecation_expiry()` requires the `[audit]` extra because it depends on `packaging` for PEP 440 version comparison.

## Comparison with other tools

The alternatives — `warnings.warn` directly, the `deprecation` package, or `wrapt.deprecated` — emit a deprecation notice but leave all the forwarding, argument mapping, and enforcement work to you. pyDeprecate handles those parts so you don't have to.

| Feature              | pyDeprecate | `warnings.warn` | `deprecation` | `Deprecated` (wrapt) |
| -------------------- | ----------- | --------------- | ------------- | -------------------- |
| Zero runtime deps    | ✅          | ✅              | ✅            | ❌                   |
| Auto call forwarding | ✅          | ❌              | ❌            | ❌                   |
| Argument mapping     | ✅          | ❌              | ❌            | ❌                   |
| Class / Enum proxy   | ✅          | ❌              | ❌            | ❌                   |
| Docstring injection  | ✅          | ❌              | ❌            | ❌                   |
| CI audit tools       | ✅          | ❌              | ❌            | ❌                   |
| Testing helpers      | ✅          | ❌              | ❌            | ❌                   |

## Where to go next

- [Getting Started](getting-started.md) — installation, first deprecation, API overview.
- [Use Cases](guide/use-cases.md) — 11 real-world deprecation patterns with worked examples.
- [Audit Tools](guide/audit.md) — CI enforcement of removal deadlines and chain detection.
- [Troubleshooting](troubleshooting.md) — common errors and how to fix them.
- [Sphinx demo](demo-sphinx/index.html) · [MkDocs demo](demo-mkdocs/index.html) — live rendered output.
