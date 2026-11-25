---
id: how-it-works
description: How pyDeprecate works inside — what a decorator builds at decoration time, what a call does at run time, how class, instance and module wrappers differ, and how the audit discovers wrappers by metadata or by reading source.
---

# How it works

pyDeprecate does its work at three moments: when the decorator runs, when a caller uses the old API, and when an audit looks for deprecations. Knowing what happens at each makes the rest of the guide predictable.

![How pyDeprecate works: the decorator wraps the old API and stores the original, a message and a frozen configuration; a forwarding call can warn within its budget, remap arguments and forward to the target; the audit reads that metadata by importing the package or by reading its source](assets/images/pydeprecate-how-it-works.svg)

## 1. Decoration time

The decorator runs once, when the module defining the old API is imported. It does the expensive thinking up front so a call stays cheap.

- It reads the signature of the old API and of the `target`, so a later call can be matched against them without re-inspecting anything.
- It resolves the *target mode* from what you passed. For `@deprecated`, a callable target forwards the call, `TargetMode.NOTIFY` warns and keeps the original behavior, and `TargetMode.ARGS_REMAP` renames arguments. Omission and explicit `TargetMode.AUTO` select the mode from the mapping. Class and instance proxy factories support `TargetMode.ATTRS_REMAP` for deprecated attributes.
- It flags a contradictory setup, such as `TargetMode.NOTIFY` together with a mapping, with a `UserWarning` at decoration time.
- It returns a **wrapper** that replaces the old API under the same name.

The wrapper carries three things you can inspect:

| Attribute                | Holds                                                                                                                 |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| `__wrapped__`            | The original function, so `inspect.unwrap` and `inspect.signature` still see it.                                      |
| `__deprecated__`         | The notice as a plain message string, the form [PEP 702](https://peps.python.org/pep-0702/) tools read (since v0.13). |
| `__deprecation_config__` | The frozen `DeprecationConfig`: versions, target, mappings and options.                                               |

Read the configuration with `get_deprecation_config()` (since v0.13), never through the attribute names:

```python
from deprecate import deprecated, get_deprecation_config


# NEW API — the maintained implementation
def compute_total(items: list, tax: float = 0.2) -> float:
    return sum(items) * (1 + tax)


# DEPRECATED API — `calc_total` forwards to `compute_total`
@deprecated(target=compute_total, deprecated_in="1.0", remove_in="2.0")
def calc_total(items: list, tax: float = 0.2) -> float:
    pass  # never runs: calls are forwarded


config = get_deprecation_config(calc_total)
print(config.deprecated_in, config.remove_in, config.target.__name__)
print(calc_total([1, 2], tax=0.5))  # warns: FutureWarning
```

<details>
  <summary>Output: <code>config fields and the forwarded result</code></summary>

```
1.0 2.0 compute_total
4.5
```

</details>

## 2. Call time

A synchronous function that forwards to a callable target follows this path:

1. **Warn.** The wrapper emits the notice, subject to its warning budget. For whole-call deprecations, the default `num_warns=1` warns once per wrapper, and `num_warns=-1` removes the budget. An argument-only remap has a separate budget for each deprecated argument and calls using only the new names stay silent. The warning points at the *caller's* line, so the migration target is easy to find. See [Customization](usage/customization.md) for templates and streams.
2. **Remap.** `args_mapping` renames or drops arguments, and `args_extra` injects new ones.
3. **Forward.** The wrapper calls the target and returns its result. <u>The body of the deprecated function is never executed</u> when it forwards.

Three options change that path without changing the model:

- `skip_if` makes the wrapper run the original body silently while the condition holds.
- `as_exception=True` raises `DeprecatedError` instead of warning, the usual step one release before removal.
- A `TargetMode.NOTIFY` wrapper warns and then runs the original body, because there is nothing to forward to.

## Other wrapper kinds

The decorator is the same idea applied to different objects.

| You deprecate | The result                                                                            | How access is caught                                                                                                                           |
| ------------- | ------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| A function    | A wrapper function with the metadata above.                                           | The call itself.                                                                                                                               |
| A class       | A proxy object standing in for the class, built by `deprecated_class`.                | The proxy forwards calls and supported protocols. Assignments to mapped attributes warn within their budget. Unmapped writes forward silently. |
| An object     | A proxy over the instance, built by `deprecated_instance`.                            | Attribute, item and call access, with an optional `read_only` guard.                                                                           |
| A module      | The same module object, now with an intercepting class, built by `deprecated_module`. | Every public attribute access, including names the module really defines.                                                                      |

A proxy exposes `__wrapped__` and `__signature__` without warning, so documentation tools and `inspect` resolve through it to the original. A class alias is a proxy, not a type; the [limitations](limitations.md) page lists what that changes for `except`, `match` and `singledispatch`.

## 3. Audit time

Audit discovery reads wrapper metadata or source declarations. Import mode also executes package and module initializers, which can themselves call deprecated APIs.

- **Import mode** imports the package, walks its modules and classes, and reads each wrapper's configuration through its metadata. It sees what the import creates, including wrappers the source does not show literally, at the cost of running module-level code.
- **Static mode** reads the source with `ast` and imports nothing, for environments without the package's runtime dependencies. Forms it cannot settle become *gaps*, reported without failing the run unless `--strict` is set; some dynamic forms stay invisible without a gap.

Import mode supports all four checks below. Use static mode for `expiry` and `policy`.

| Check    | Asks                                                                   | Page                                                       |
| -------- | ---------------------------------------------------------------------- | ---------------------------------------------------------- |
| `check`  | Is a wrapper misconfigured?                                            | [Audit tools](audit/index.md)                              |
| `expiry` | Is a wrapper still present at or past its `remove_in`?                 | [Audit policy](audit/policy.md)                            |
| `policy` | Was the schedule responsible: grace window, release boundary, message? | [Audit policy](audit/policy.md)                            |
| `chains` | Does a deprecated wrapper forward to another deprecated wrapper?       | [Audit tools](audit/index.md#detecting-deprecation-chains) |

The result is findings, gaps and an exit code, optionally written as a JSON or SARIF report. The [CLI reference](audit/cli.md) lists every flag and exit code, and [CI integration](audit/ci-integration.md) turns the checks into a gate.

!!! info "A green exit is not a complete audit"

    A failed import of an explicitly requested target exits `3`. Skipped recursive imports and unresolved versions can produce gaps. Strict gates fail on those gaps, and other hard findings can fail a run independently. Read the gaps even when the exit code is `0`.

## What it does not do

pyDeprecate is a runtime library with zero dependencies. It does not rewrite your callers, edit source or decide your release schedule. For that work, the [coding-agent plugin](adoption/agent-recipes.md) adds skills that scan, audit and retire deprecations around the library, and [Lifecycle](usage/lifecycle.md) walks one deprecation from first warning to removal.
