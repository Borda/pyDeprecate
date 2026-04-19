---
description: >-
  pyDeprecate — zero-dependency Python library for deprecating functions,
  methods, and classes with automatic call forwarding, argument mapping, and
  CI/CD audit utilities. Python 3.9+.
---

# pyDeprecate

**pyDeprecate** is a zero-dependency Python library that turns API deprecation from a chore into a one-liner.
Decorate a function or method with `@deprecated(...)`, or a class/Enum/dataclass with `@deprecated_class(...)`,
and the library handles the rest: runtime `FutureWarning` emission, transparent call forwarding to the replacement,
and automatic documentation updates.

```python
from deprecate import deprecated


def compute_sum(x: int, y: int) -> int:
    return x + y


@deprecated(target=compute_sum, deprecated_in="1.0", remove_in="2.0")
def addition(x: int, y: int) -> int: ...
```

Calling `addition(1, 2)` now emits a `FutureWarning` and transparently forwards to `compute_sum`.

## Features

- **Automatic call forwarding** — no wrapper boilerplate; the decorator does the routing.
- **Argument mapping** — rename or drop arguments between old and new APIs via `args_mapping`.
- **Class and Enum support** — `@deprecated_class` wraps entire classes in a transparent proxy.
- **Configurable warning frequency** — emit once, N times, or always (`num_warns`).
- **Docstring injection** — `update_docstring=True` keeps rendered docs current automatically.
- **CI audit tools** — `find_deprecation_wrappers()`, `validate_deprecation_expiry()`, and
  `validate_deprecation_chains()` enforce removal deadlines and detect chained deprecations in CI.
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

## Comparison with other tools

| Feature              | pyDeprecate | `warnings.warn` | `deprecation` | `Deprecated` (wrapt) |
| -------------------- | ----------- | --------------- | ------------- | -------------------- |
| Auto call forwarding | ✅          | ❌              | ❌            | ❌                   |
| Argument mapping     | ✅          | ❌              | ❌            | ❌                   |
| Class / Enum proxy   | ✅          | ❌              | ❌            | ❌                   |
| Docstring injection  | ✅          | ❌              | ❌            | ❌                   |
| CI audit tools       | ✅          | ❌              | ❌            | ❌                   |
| Testing helpers      | ✅          | ❌              | ❌            | ❌                   |
| Zero runtime deps    | ✅          | ✅              | ✅            | ❌                   |

## Where to go next

- [Getting Started](getting-started.md) — installation, first deprecation, API overview.
- [Use Cases](guide/use-cases.md) — 11 real-world deprecation patterns with worked examples.
- [Audit Tools](guide/audit.md) — CI enforcement of removal deadlines and chain detection.
- [Troubleshooting](troubleshooting.md) — common errors and how to fix them.
- [Sphinx demo](demo-sphinx/index.html) · [MkDocs demo](demo-mkdocs/index.html) — live rendered output.
