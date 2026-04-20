---
description: Install pyDeprecate and write your first deprecation in minutes. Covers pip installation, the audit extra, a Quick Start example, and the full API at a Glance reference table.
---

# Getting Started

pyDeprecate is a lightweight Python library for adding deprecation warnings to functions, methods, and classes with automatic call forwarding. This page walks through installation, a minimal working example, and the full decorator API so you can choose the right tool for each scenario.

## Installation

pyDeprecate requires **Python 3.9 or later** and has zero runtime dependencies in its base form.

Install the latest stable release from PyPI:

```bash
pip install pyDeprecate
```

To install directly from source (for pre-release or development versions):

```bash
pip install https://github.com/Borda/pyDeprecate/archive/main.zip
```

The `audit` extra pulls in `packaging` for version-comparison logic used by `validate_deprecation_expiry`. Install it when you want to enforce removal deadlines in CI:

```bash
pip install 'pyDeprecate[audit]'
```

## Quick Start

The most common use case is renaming a function while keeping the old name working under a deprecation warning. The decorator handles call forwarding automatically — you do not need to copy any implementation into the deprecated wrapper.

```python
from deprecate import deprecated


# NEW/FUTURE API — renamed to be more explicit about what it computes
def compute_sum(a: int = 0, b: int = 3) -> int:
    return a + b


# DEPRECATED API — `addition` was the original name before the rename
@deprecated(target=compute_sum, deprecated_in="1.0", remove_in="2.0")
def addition(a: int, b: int = 5) -> int:
    pass  # body is not needed — calls are forwarded to compute_sum


# Using the original name still works but shows a warning
result = addition(1, 2)  # Returns 3
# Warning: The `addition` was deprecated since v1.0 in favor of `__main__.compute_sum`.
#          It will be removed in v2.0.
```

All calls to `addition()` are automatically forwarded to `compute_sum()` with a `FutureWarning`. The old function's body is never executed.

## API at a Glance

Not sure which decorator to reach for? The table below maps common scenarios to the correct API. For full worked examples of each, see [Use Cases](guide/use-cases.md).

**Pick the right decorator:**

| Scenario                                      | API to use                                              |
| --------------------------------------------- | ------------------------------------------------------- |
| Renaming a function or method                 | `@deprecated(target=new_func)`                          |
| Renaming an argument within the same function | `@deprecated(target=True, args_mapping={"old": "new"})` |
| Warn only — original body still runs          | `@deprecated(target=None)`                              |
| Deprecating a class, Enum, or dataclass name  | `@deprecated_class(target=NewClass)`                    |
| Deprecating a module-level constant or object | `deprecated_instance(obj, ...)`                         |

**All `@deprecated` parameters:**

| Param              | Default               | Purpose                                                                     |
| ------------------ | --------------------- | --------------------------------------------------------------------------- |
| `target`           | —                     | `Callable` to forward to · `True` to remap args in-place · `None` warn-only |
| `deprecated_in`    | `""`                  | Version when deprecated (e.g. `"1.0"`)                                      |
| `remove_in`        | `""`                  | Version when removed (e.g. `"2.0"`)                                         |
| `stream`           | `deprecation_warning` | Warning sink callable (set `None` to silence warnings)                      |
| `num_warns`        | `1`                   | `1` once · `-1` always · `N` exactly N times                                |
| `args_mapping`     | `None`                | `{"old": "new"}` rename · `{"old": None}` drop                              |
| `template_mgs`     | `None`                | Custom warning message template (`%`-style placeholders)                    |
| `args_extra`       | `None`                | Fixed kwargs injected into the target call                                  |
| `skip_if`          | `False`               | `bool` or `Callable → bool`; skip deprecation when true                     |
| `update_docstring` | `False`               | Append Sphinx `.. deprecated::` notice to docstring                         |

`@deprecated_class()` shares `target`, `deprecated_in`, `remove_in`, `num_warns`, `stream`, and `args_mapping`. `deprecated_instance()` shares `deprecated_in`, `remove_in`, `num_warns`, and `stream`; it requires `obj` and adds `name` (display name) and `read_only`.

______________________________________________________________________

Next: [Use Cases](guide/use-cases.md) — eleven real-world deprecation patterns with full code examples.
