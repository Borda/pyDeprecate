---
id: index
description: >-
  pyDeprecate — zero-dependency Python library for deprecating functions and
  classes with call forwarding, argument mapping, and CI/CD audit tools.
  Python 3.9+.
---

# pyDeprecate

Every time you rename a function or retire an argument, you end up writing the same boilerplate: a wrapper, a `warnings.warn` call with the right category and `stacklevel`, manual argument forwarding, and no way to enforce the removal deadline when it arrives. **pyDeprecate** replaces all of that with a single decorator and gives you CI tools to make sure deprecated code does not quietly outlive its deadline.

> **pyDeprecate is downloaded over 700,000 times per month** from PyPI — used across production Python projects that need reliable API deprecation without adding runtime dependencies.

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

- **Automatic call forwarding** — the decorator routes every call to the replacement, including positional and keyword arguments. No manual `*args/**kwargs` plumbing.
- **Argument mapping** — `args_mapping={"old": "new"}` handles renames across the API boundary so you never write custom forwarding code.
- **Class and Enum support** — `@deprecated_class` wraps entire classes, Enums, and dataclasses in a transparent proxy where `isinstance` and `issubclass` just work.
- **Configurable frequency** — `num_warns=1` (default) emits once per function, not on every call. Set `-1` for always or `N` for exactly N times.
- **Docstring injection** — `update_docstring=True` appends a Sphinx `.. deprecated::` notice automatically, keeping rendered API docs accurate without manual edits.
- **CI audit tools** — `validate_deprecation_expiry()` catches zombie code past its deadline, `validate_deprecation_chains()` detects double-deprecation chains, and `find_deprecation_wrappers()` surfaces misconfigured `args_mapping` keys before they silently do nothing.
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

    The base install gives you `@deprecated`, `@deprecated_class`, `deprecated_instance`, and all other audit functions. Only `validate_deprecation_expiry()` needs the extra because it pulls in `packaging` for PEP 440 version comparison.

## Comparison with other tools

The alternatives emit a deprecation notice but leave forwarding, argument mapping, and deadline enforcement to you. Here is what each tool covers:

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

- [Getting Started](getting-started.md) — install, write your first deprecation, see the full API at a glance.
- [Use Cases](guide/use-cases.md) — thirteen real-world deprecation patterns with worked examples.
- [Audit Tools](guide/audit.md) — enforce removal deadlines and catch deprecation chains in CI.
- [Troubleshooting](troubleshooting.md) — common errors and how to fix them.
- [Sphinx demo](demo-sphinx/index.html) · [MkDocs demo](demo-mkdocs/index.html) — live rendered output.
