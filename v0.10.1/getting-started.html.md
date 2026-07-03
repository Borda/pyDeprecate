---
id: getting-started
description: Install pyDeprecate and write your first deprecation in minutes. Covers pip installation, the audit extra, a Quick Start example, and the full API at a Glance reference table.
---

# Getting Started

You renamed a function (or retired an argument) and now you need the old name to keep working, callers to see a clear deprecation notice, and a firm removal date you can enforce. pyDeprecate does all of that with a single decorator so you can focus on the new API instead of the plumbing.

## Why pyDeprecate instead of `warnings.warn`?

`warnings.warn` tells callers that something is deprecated — but they still have to update their code manually and the old code path keeps running. pyDeprecate adds three things on top:

- **Automatic call forwarding** — every call to the old function is transparently redirected to the replacement; no stale code runs.
- **Argument mapping** — rename or drop arguments across the API boundary with `args_mapping={"old": "new"}` to help callers migrate to the new signature.
- **CI deadline enforcement** — `validate_deprecation_expiry()` (via `pip install pyDeprecate[audit]`) raises in CI when a removal date has passed, so deprecated code cannot quietly outlive its deadline.

If you only need a one-line notice with no forwarding and no deadline tracking, `warnings.warn` is sufficient. Choose pyDeprecate when you need the old name to keep working while callers migrate.

## Installation

pyDeprecate requires **Python 3.9 or later** and has zero runtime dependencies.

Install from PyPI:

```bash
pip install pyDeprecate
```

To install directly from source (for pre-release or development versions):

```bash
pip install https://github.com/Borda/pyDeprecate/archive/main.zip
```

The `audit` extra adds `packaging` for version comparison, needed only by [`validate_deprecation_expiry`](guide/audit.md#enforcing-removal-deadlines). Install it when you want to enforce removal deadlines in CI:

```bash
pip install 'pyDeprecate[audit]'
```

## Quick Start

Here is the most common scenario: you renamed a function and need the old name to keep working during the transition. The decorator handles call forwarding automatically, so the deprecated wrapper needs no implementation.

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
  <summary>Output: <code>addition(1, 2)</code></summary>

```
3
```

</details>

All calls to `addition()` are automatically forwarded to `compute_sum()` with a `FutureWarning`. The old function's body is never executed.

!!! tip "Customizing deprecation messages"

    To customize the message template or redirect deprecation output to a logger, see [Customization](guide/customization.md).

## API at a Glance

Not sure which API to reach for? This table maps common scenarios to the right tool. For worked examples of each, see [Use Cases](guide/use-cases.md).

**Pick the right decorator:**

| Scenario                                      | API to use                                                               |
| --------------------------------------------- | ------------------------------------------------------------------------ |
| Renaming a function or method                 | `@deprecated(target=new_func)`                                           |
| Renaming an argument within the same function | `@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old": "new"})` |
| Notice only — original body still runs        | `@deprecated(target=TargetMode.NOTIFY)`                                  |
| Deprecating a class, Enum, or dataclass name  | `@deprecated_class(target=NewClass)`                                     |
| Deprecating a module-level constant or object | `deprecated_instance(obj, ...)`                                          |

**All `@deprecated` parameters:**

| Param              | Default               | Purpose                                                                                                    |
| ------------------ | --------------------- | ---------------------------------------------------------------------------------------------------------- |
| `target`           | `TargetMode.NOTIFY`   | `Callable` to forward to · `TargetMode.ARGS_REMAP` to remap args · `TargetMode.NOTIFY` (default) warn-only |
| `deprecated_in`    | `""`                  | Version when deprecated (e.g. `"1.0"`)                                                                     |
| `remove_in`        | `""`                  | Version when removed (e.g. `"2.0"`)                                                                        |
| `stream`           | `deprecation_warning` | Output sink callable (set `None` to silence deprecation messages)                                          |
| `num_warns`        | `1`                   | `1` once · `-1` always · `N` exactly N times                                                               |
| `args_mapping`     | `None`                | `{"old": "new"}` rename · `{"old": None}` drop                                                             |
| `template_mgs`     | `None`                | Custom deprecation message template (`%`-style placeholders)                                               |
| `args_extra`       | `None`                | Fixed kwargs injected into the target call                                                                 |
| `skip_if`          | `False`               | `bool` or `Callable → bool`; skip deprecation when true                                                    |
| `update_docstring` | `False`               | Append Sphinx `.. deprecated::` notice to docstring                                                        |
| `docstring_style`  | `"auto"`              | Style of the injected notice: `"auto"`, `"rst"`, `"mkdocs"`, `"markdown"`                                  |

`@deprecated_class()` shares `target`, `deprecated_in`, `remove_in`, `num_warns`, `stream`, `args_mapping`, `args_extra`, `template_mgs`, `update_docstring`, and `docstring_style`. `deprecated_instance()` shares `deprecated_in`, `remove_in`, `num_warns`, `stream`, `args_extra`, and `template_mgs`; it requires `obj` and adds `name` (display name) and `read_only`.

______________________________________________________________________

Next: [Use Cases](guide/use-cases.md) — overview of all deprecation patterns with links to each topic. See also: [Customization](guide/customization.md) · [Audit Tools](guide/audit.md) · [Troubleshooting](troubleshooting.md)
