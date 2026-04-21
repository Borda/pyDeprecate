---
id: getting-started
description: Install pyDeprecate and write your first deprecation in minutes. Covers pip installation, the audit extra, a Quick Start example, and the full API at a Glance reference table.
---

# Getting Started

When you rename a function or retire an argument, you need the old name to keep working, callers to receive a clear deprecation notice pointing at the new API, and a firm removal date you can actually enforce. Doing that by hand — writing a wrapper, calling `warnings.warn` with the right arguments, forwarding every parameter — is repetitive and leaves no enforcement path. pyDeprecate does all of it with a single decorator so you can focus on the new API instead of the plumbing.

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

You renamed a function and need the old name to keep working under a deprecation notice during the transition window. The decorator handles call forwarding automatically — you do not need to copy any implementation into the deprecated wrapper.

```python
from deprecate import deprecated


# NEW/FUTURE API — renamed to be more explicit about what it computes
def compute_sum(a: int = 0, b: int = 3) -> int:
    return a + b


# DEPRECATED API — `addition` was the original name before the rename
@deprecated(target=compute_sum, deprecated_in="1.0", remove_in="2.0")
def addition(a: int, b: int = 5) -> int:
    pass  # body is not needed — calls are forwarded to compute_sum


# Using the original name still works but emits a deprecation notice
print(addition(1, 2))
```

<details>
  <summary>Output: <code>print(addition(1, 2))</code></summary>

```
3
```

</details>

All calls to `addition()` are automatically forwarded to `compute_sum()` with a `FutureWarning`. The old function's body is never executed.

> **Customizing deprecation messages:** To customize the message template or redirect deprecation output to a logger, see [Customization](guide/customization.md).

## API at a Glance

Not sure which decorator to reach for? The table below maps common scenarios to the correct API. For full worked examples of each, see [Use Cases](guide/use-cases.md).

**Pick the right decorator:**

| Scenario                                      | API to use                                              |
| --------------------------------------------- | ------------------------------------------------------- |
| Renaming a function or method                 | `@deprecated(target=new_func)`                          |
| Renaming an argument within the same function | `@deprecated(target=True, args_mapping={"old": "new"})` |
| Notice only — original body still runs        | `@deprecated(target=None)`                              |
| Deprecating a class, Enum, or dataclass name  | `@deprecated_class(target=NewClass)`                    |
| Deprecating a module-level constant or object | `deprecated_instance(obj, ...)`                         |

**All `@deprecated` parameters:**

| Param              | Default               | Purpose                                                                       |
| ------------------ | --------------------- | ----------------------------------------------------------------------------- |
| `target`           | —                     | `Callable` to forward to · `True` to remap args in-place · `None` notice-only |
| `deprecated_in`    | `""`                  | Version when deprecated (e.g. `"1.0"`)                                        |
| `remove_in`        | `""`                  | Version when removed (e.g. `"2.0"`)                                           |
| `stream`           | `deprecation_warning` | Output sink callable (set `None` to silence deprecation messages)             |
| `num_warns`        | `1`                   | `1` once · `-1` always · `N` exactly N times                                  |
| `args_mapping`     | `None`                | `{"old": "new"}` rename · `{"old": None}` drop                                |
| `template_mgs`     | `None`                | Custom deprecation message template (`%`-style placeholders)                  |
| `args_extra`       | `None`                | Fixed kwargs injected into the target call                                    |
| `skip_if`          | `False`               | `bool` or `Callable → bool`; skip deprecation when true                       |
| `update_docstring` | `False`               | Append Sphinx `.. deprecated::` notice to docstring                           |
| `docstring_style`  | `"auto"`              | Style of the injected notice: `"auto"`, `"rst"`, `"mkdocs"`, `"markdown"`     |

`@deprecated_class()` shares `target`, `deprecated_in`, `remove_in`, `num_warns`, `stream`, `args_mapping`, `update_docstring`, and `docstring_style`. `deprecated_instance()` shares `deprecated_in`, `remove_in`, `num_warns`, and `stream`; it requires `obj` and adds `name` (display name) and `read_only`.

______________________________________________________________________

Next: [Use Cases](guide/use-cases.md) — twelve real-world deprecation patterns with full code examples.
