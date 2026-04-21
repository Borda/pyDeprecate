---
id: use-cases
description: Twelve real-world deprecation patterns for Python functions, methods, classes, Enums, dataclasses, and constants — each with a worked example and full code.
---

# Use Cases

The two most common reasons to deprecate something are renaming a function and renaming an argument. Both require more than a bare `warnings.warn`: you need call forwarding, argument remapping, and a way to keep the old code working until removal day. This page walks through each pattern pyDeprecate supports, from a simple rename to proxy-wrapped Enums and multi-hop argument chains. If you are new to the library, start with [Getting Started](../getting-started.md).

## Simple function forwarding

Apply `@deprecated(target=new_func)` to the old name and pyDeprecate forwards every call (positional and keyword arguments included) to the new function. The body of the deprecated function is never executed, so leave it empty or put a docstring there.

```python
# NEW/FUTURE API — renamed to be more explicit about what it computes
def compute(a: int = 0, b: int = 3) -> int:
    """New function anywhere in the codebase or even other package."""
    return a + b


# ---------------------------

from deprecate import deprecated


# What this module looked like before the rename:
# def calculate(a: int, b: int = 5) -> int:
#     return a + b


# DEPRECATED API — `calculate` was the original name before the rename
@deprecated(target=compute, deprecated_in="0.1", remove_in="0.5")
def calculate(a: int, b: int = 5) -> int:
    """
    My deprecated function which now has an empty body
     as all calls are routed to the new function.
    """
    pass  # or you can just place docstring as one above


# calling this function will raise a deprecation warning:
#   The `calculate` was deprecated since v0.1 in favor of `your_module.compute`.
#   It will be removed in v0.5.
print(calculate(1, 2))
```

<details>
  <summary>Output: <code>print(calculate(1, 2)</code></summary>

```
3
```

</details>

If the deprecated name already exists as a callable (for example, imported from another package), apply `deprecated()` directly as a wrapper call instead of using decorator syntax. This works on any callable, including ones you do not control.

```python
from deprecate import deprecated


# NEW/FUTURE API — in real usage this would be imported from another module
def compute_sum(a: int, b: int = 0) -> int:
    return a + b


# LEGACY — already-existing callable that is being deprecated
def addition(a: int, b: int = 0) -> int:
    return a + b


# DEPRECATED API — `calculate` was the original name in this package;
# wrap it without redefining a function body
calculate = deprecated(
    target=compute_sum,
    deprecated_in="0.5",
    remove_in="1.0",
)(addition)
```

## Argument renaming and mapping

Use `args_mapping` when the new function accepts the same arguments under different names. The decorator translates old parameter names to new ones at call time, so callers can keep passing the old names during the deprecation window without any manual mapping code.

```python
# phmdoctest:skip
import logging
from sklearn.metrics import accuracy_score
from deprecate import deprecated, void


@deprecated(
    # use standard sklearn accuracy implementation
    target=accuracy_score,
    # custom warning stream
    stream=logging.warning,
    # number of warnings per lifetime (with -1 for always)
    num_warns=5,
    # custom message template
    template_mgs="`%(source_name)s` was deprecated, use `%(target_path)s`",
    # as target args are different, define mapping from source to target func
    args_mapping={"preds": "y_pred", "target": "y_true", "blabla": None},
)
def depr_accuracy(preds: list, target: list, blabla: float) -> float:
    """My deprecated function which is mapping to sklearn accuracy."""
    # to stop complain your IDE about unused argument you can use void/empty function
    return void(preds, target, blabla)


# calling this function will raise a deprecation warning:
#   WARNING:root:`depr_accuracy` was deprecated, use `sklearn.metrics.accuracy_score`
print(depr_accuracy([1, 0, 1, 2], [0, 1, 1, 2], 1.23))
```

## Notice-only deprecation

!!! note "The function body still executes with `target=None`"

    Unlike `target=<callable>` (where the body is dead code), `target=None` runs the original function body after emitting the deprecation notice. You must keep a working implementation in the function body.

Use `target=None` when a function is going away but has no replacement yet. The decorator emits a deprecation notice and then runs the function body normally. This is the right choice when callers need to update their own code, not switch to a different function.

```python
from deprecate import deprecated


@deprecated(target=None, deprecated_in="0.1", remove_in="0.5")
def my_sum(a: int, b: int = 5) -> int:
    """My deprecated function which still has to have implementation."""
    return a + b


# calling this function will raise a deprecation warning:
#   The `my_sum` was deprecated since v0.1. It will be removed in v0.5.
print(my_sum(1, 2))
```

<details>
  <summary>Output: <code>print(my_sum(1, 2)</code></summary>

```
3
```

</details>

## Self argument mapping

Use `target=True` to rename or drop an argument within the same function. The decorator remaps the old argument name to the new one before the body runs, so your implementation only needs the new name. This is the right pattern when refactoring a signature without moving the function.

```python
from deprecate import deprecated


@deprecated(
    # define as deprecation some self argument - mapping
    target=True,
    args_mapping={"coef": "new_coef"},
    # common version info
    deprecated_in="0.2",
    remove_in="0.4",
)
def any_pow(base: float, coef: float = 0, new_coef: float = 0) -> float:
    """My function with deprecated argument `coef` mapped to `new_coef`."""
    return base**new_coef


# calling this function will raise a deprecation warning:
#   The `any_pow` uses deprecated arguments: `coef` -> `new_coef`.
#   They were deprecated since v0.2 and will be removed in v0.4.
print(any_pow(2, 3))
```

<details>
  <summary>Output: <code>print(any_pow(2, 3)</code></summary>

```
8
```

</details>

To drop an argument entirely, map it to `None`. The decorator emits a deprecation notice when the argument is passed and then discards it.

```python
from deprecate import deprecated
from typing import Optional


@deprecated(
    target=True,
    args_mapping={"legacy_param": None},
    deprecated_in="1.8",
    remove_in="1.9",
)
def my_func(value: int, legacy_param: Optional[str] = None) -> int:
    """legacy_param is no longer used; pass None or omit it."""
    return value * 2


# Passing the removed argument triggers a warning and the argument is silently discarded:
#   The `my_func` uses deprecated arguments: `legacy_param` -> `None`.
#   They were deprecated since v1.8 and will be removed in v1.9.
my_func(value=42, legacy_param="old")
```

## `target=None` vs `target=True` vs `target=<callable>` — key differences

These modes differ in whether the function body runs, whether a warning fires, and which parameters take effect.

### Legend

| Symbol | Meaning                                                                                 |
| ------ | --------------------------------------------------------------------------------------- |
| `✓`    | Applied                                                                                 |
| `✗`    | Not applied                                                                             |
| `⚠`    | Silently ignored — accepted but has no effect; no error raised                          |
| `⊘`    | Warning suppressed; other processing (remapping / forwarding) continues                 |
| `⊛`    | `skip_if` bypasses all logic — source runs with original args, no warning or forwarding |
| `—`    | Not applicable for this mode                                                            |

### Behaviour comparison

|                               | `target=None`                                                                                             | `target=True` (no `args_mapping`) | `target=True` (with `args_mapping`)                                        | `target=<callable>`                               |
| ----------------------------- | --------------------------------------------------------------------------------------------------------- | --------------------------------- | -------------------------------------------------------------------------- | ------------------------------------------------- |
| **Warning emitted**           | Yes — up to `num_warns` times (default: once)                                                             | **Never**                         | Per deprecated arg, up to `num_warns` times (default: once)                | Yes — up to `num_warns` times (default: once)     |
| **Warning template**          | `"… was deprecated since vX. It will be removed in vY."`                                                  | —                                 | `"… uses deprecated arguments: …"`                                         | `"… was deprecated … in favour of …"`             |
| **`template_mgs` specifiers** | `source_name`, `source_path`, `deprecated_in`, `remove_in` only — `target_name`/`target_path` unavailable | —                                 | `source_name`, `source_path`, `argument_map`, `deprecated_in`, `remove_in` | All specifiers incl. `target_name`, `target_path` |
| **Function body**             | Runs with caller's args + source defaults filled in                                                       | Runs with caller's args as-is     | Runs after argument renaming/dropping                                      | **Never runs** — all calls forwarded to target    |
| **`args_mapping` applied**    | `⚠`                                                                                                       | `⚠`                               | `✓` renames or drops listed args                                           | `✓` renames or drops args before forwarding       |
| **`args_extra` injected**     | `⚠`                                                                                                       | `⚠`                               | `✓` merged into kwargs before call                                         | `✓` merged into kwargs before forwarding          |
| **Source defaults merged**    | `✓`                                                                                                       | `✗`                               | `✗`                                                                        | `✓`                                               |
| **`skip_if` effect**          | `⊛`                                                                                                       | `⊛`                               | `⊛`                                                                        | `⊛`                                               |
| **`stream=None` effect**      | `⊘` body still runs                                                                                       | No observable change              | `⊘` remapping still runs                                                   | `⊘` forwarding still runs                         |

### When to use which

- **`target=None`** — function is going away with no replacement. Callers must remove the call. Warning fires up to `num_warns` times (default: once) so each caller is notified on first use. `args_mapping` and `args_extra` are silently ignored here.
- **`target=<callable>`** — function is replaced by another callable. The source body never runs; all calls are forwarded. Use `args_mapping` to rename arguments and `args_extra` to inject new required args.
- **`target=True` + `args_mapping`** — function stays but its signature is changing. Warning fires only when the old argument name is actually used, so callers who already migrated see no noise.
- **`target=True` without `args_mapping`** — effectively a no-op. `stream`, `num_warns`, `deprecated_in`, `remove_in`, `args_extra`, and `args_mapping` all have no effect. Avoid this combination unless you explicitly want a zero-effect passthrough.

### Example — notice the difference in warning behaviour

```python
from deprecate import deprecated
import warnings


# target=None: warns once by default (num_warns=1)
@deprecated(target=None, deprecated_in="1.0", remove_in="2.0")
def going_away(x: int) -> int:
    return x


# target=True: warns only when the old argument name is passed
@deprecated(target=True, args_mapping={"old_x": "x"}, deprecated_in="1.0", remove_in="2.0")
def renamed_arg(old_x: int = 0, x: int = 0) -> int:
    return x


with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")

    going_away(1)  # → 1 warning ("going_away was deprecated since v1.0 …")
    renamed_arg(x=1)  # → 0 warnings  (new name used — no deprecated arg present)
    renamed_arg(old_x=1)  # → 1 warning ("renamed_arg uses deprecated arguments: `old_x` -> `x` …")

    print(len(w))  # 2
```

### `target=True` without `args_mapping` — silent passthrough

```python
from deprecate import deprecated
import warnings


# No args_mapping → no warning, no remapping, body runs unchanged.
@deprecated(target=True, deprecated_in="1.0", remove_in="2.0")
def no_op_wrapper(x: int) -> int:
    return x * 2


with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    result = no_op_wrapper(3)
    print(result)  # 6
    print(len(w))  # 0 — no warning was emitted
```

!!! warning "`target=True` without `args_mapping` emits no warning"

    This combination is valid Python but has no observable deprecation effect. If your intent is to warn callers that a function is going away, use `target=None` instead.

## Chained deprecation levels

!!! warning "Stacked decorators can create unintended deprecation chains"

    Each stacked `@deprecated(True, ...)` decorator emits its own notice. If you accidentally point a decorator's `target` at another deprecated function instead of the final implementation, callers receive redundant warnings. Use [`validate_deprecation_chains()`](audit.md#detecting-deprecation-chains) in CI to catch these mistakes automatically.

When arguments are deprecated across multiple releases, stack one `@deprecated(True, ...)` per rename. Each decorator operates on its own version range and emits a separate notice, giving callers version-specific migration guidance.

```python
from deprecate import deprecated


@deprecated(
    True,
    deprecated_in="0.3",
    remove_in="0.6",
    args_mapping=dict(c1="nc1"),
    template_mgs="Depr: v%(deprecated_in)s rm v%(remove_in)s for args: %(argument_map)s.",
)
@deprecated(
    True,
    deprecated_in="0.4",
    remove_in="0.7",
    args_mapping=dict(nc1="nc2"),
    template_mgs="Depr: v%(deprecated_in)s rm v%(remove_in)s for args: %(argument_map)s.",
)
def any_pow(base, c1: float = 0, nc1: float = 0, nc2: float = 2) -> float:
    return base**nc2


# calling this function will raise deprecation warnings:
#   FutureWarning('Depr: v0.3 rm v0.6 for args: `c1` -> `nc1`.')
#   FutureWarning('Depr: v0.4 rm v0.7 for args: `nc1` -> `nc2`.')
print(any_pow(2, 3))
```

<details>
  <summary>Output: <code>print(any_pow(2, 3)</code></summary>

```
8
```

</details>

## Conditional skip

`skip_if` accepts a boolean or a zero-argument callable returning a boolean. When it evaluates to `True`, the deprecation notice is suppressed and the call proceeds normally. This is useful when behaviour depends on runtime conditions, for example suppressing the notice once the caller has migrated to a newer dependency.

```python
from deprecate import deprecated

FAKE_VERSION = 1


def version_greater_1():
    return FAKE_VERSION > 1


@deprecated(True, "0.3", "0.6", args_mapping=dict(c1="nc1"), skip_if=version_greater_1)
def skip_pow(base, c1: float = 1, nc1: float = 1) -> float:
    return base ** (c1 - nc1)


# calling this function will raise a deprecation warning
print(skip_pow(2, 3))

# change the fake versions
FAKE_VERSION = 2

# will not raise any warning
print(skip_pow(2, 3))
```

<details>
  <summary>Output: <code>print(skip_pow(2, 3)</code></summary>

```
0.25
4
```

</details>

## Class deprecation

Two common patterns here. First, renaming a method within a class: apply `@deprecated(target=execute)` on the old method name and calls forward to the new method. Second, deprecating an entire class by decorating `__init__` to emit a notice at instantiation time and optionally forward construction to a successor class.

Method rename within a class:

```python
from deprecate import deprecated, void


class MyService:
    # NEW/FUTURE API — renamed from run() for clarity
    def execute(self, x: int) -> int:
        """Current method."""
        return x * 2

    # DEPRECATED API — `run` was the original name before the rename
    @deprecated(target=execute, deprecated_in="1.0", remove_in="2.0")
    def run(self, x: int) -> int:
        """Deprecated — renamed to execute()."""
        return void(x)


svc = MyService()
# calling this method will raise a deprecation warning:
#   The `run` was deprecated since v1.0 in favor of `your_module.execute`.
#   It will be removed in v2.0.
print(svc.run(5))
```

<details>
  <summary>Output: <code>print(svc.run(5)</code></summary>

```
10
```

</details>

Forwarding `__init__` to a successor class — the deprecated class inherits from the successor so all methods and properties are available on instances:

```python
# NEW/FUTURE API — renamed to be more descriptive
class HttpClient:
    """My new class anywhere in the codebase or other package."""

    def __init__(self, c: float, d: str = "abc"):
        self.my_c = c
        self.my_d = d


# ---------------------------

from deprecate import deprecated, void


# DEPRECATED API — `Client` was the original name before it was renamed to HttpClient
class Client(HttpClient):
    """
    The deprecated class should be inherited from the successor class
     to hold all methods and properties.
    """

    @deprecated(target=HttpClient, deprecated_in="0.2", remove_in="0.4")
    def __init__(self, c: int, d: str = "efg"):
        """
        You place the decorator around __init__ as you want
         to warn user just at the time of creating object.

        Decorating __init__ warns at instantiation time and optionally
        forwards to another class. For deprecating the class itself
        (name change, Enum, dataclass), use @deprecated_class() instead.
        """
        void(c, d)


# calling this function will raise a deprecation warning:
#   The `Client` was deprecated since v0.2 in favor of `your_module.HttpClient`.
#   It will be removed in v0.4.
inst = Client(7)
print(inst.my_c)  # returns: 7
print(inst.my_d)  # returns: "efg"
```

<details>
  <summary>Output: <code>print(inst.my_d)</code></summary>

```
7
efg
```

</details>

## Constants and instances

`deprecated_instance` wraps module-level objects (dicts, lists, custom objects) in a transparent proxy that emits a deprecation notice on attribute, item, or call access. Use `read_only=True` to prevent callers from mutating shared state through the deprecated alias.

Heads up: primitive protocol methods (arithmetic on `float`, concatenation on `str`) are not intercepted by the proxy. For primitive constants, wrap them in a container or update call sites directly. See [Troubleshooting](../troubleshooting.md#why-does-deprecated_instance-not-emit-a-notice-on-arithmeticcomparison-operators) for details.

```python
from deprecate import deprecated_instance

# NEW/FUTURE API — renamed to be more explicit about its scope
TRAINING_CONFIG = {"lr": 0.001, "batch_size": 32, "epochs": 10}

# What it looked like before the rename:
# DEFAULTS = {"lr": 0.001, "batch_size": 32, "epochs": 10}

# DEPRECATED API — `DEFAULTS` was the original name; read-only so
# callers cannot mutate shared state through the deprecated alias
DEFAULTS = deprecated_instance(
    TRAINING_CONFIG,
    deprecated_in="1.2",
    remove_in="2.0",
    read_only=True,
)

# Reading still works but emits a FutureWarning once:
#   The `dict` was deprecated since v1.2. It will be removed in v2.0.
print(DEFAULTS["lr"])  # 0.001
```

<details>
  <summary>Output: <code>print(DEFAULTS["lr"])</code></summary>

```
0.001
```

</details>

## Enums and dataclasses

`deprecated_class()` wraps an Enum or dataclass in a transparent proxy that emits a deprecation notice on access and forwards attribute, item, and call operations to the replacement. Use `args_mapping` to rename or drop kwargs when the deprecated class is called. Type checks (`isinstance`, `issubclass`) pass through without emitting notices, since they are structural checks rather than usage of the deprecated API.

```python
from enum import Enum
from dataclasses import dataclass
from deprecate import deprecated_class

# mypackage/theme.py — what it looked like before the rename:
#
# class Color(Enum):
#     RED = 1
#     BLUE = 2


# NEW/FUTURE API — renamed to be more descriptive
class ThemeColor(Enum):
    RED = 1
    BLUE = 2


# DEPRECATED API — `Color` was the original name; no class body needed,
# the proxy forwards all access to ThemeColor
Color = deprecated_class(target=ThemeColor, deprecated_in="1.0", remove_in="2.0")(ThemeColor)

# All access is forwarded to ThemeColor — a FutureWarning is emitted once:
#   The `Color` was deprecated since v1.0. It will be removed in v2.0.
print(Color.RED is ThemeColor.RED)  # True
print(Color(1) is ThemeColor.RED)  # True
print(Color["RED"] is ThemeColor.RED)  # True


# Precision migration story:
# - PointV1 used integer pixel coordinates.
# - PointV2 supports float coordinates for sub-pixel precision and smoother transforms.


# NEW/FUTURE API — extended to float precision
@dataclass
class PointV2:
    x: float
    y: float


# DEPRECATED API — PointV1 was the original integer-coordinate implementation
@deprecated_class(target=PointV2, deprecated_in="1.8", remove_in="2.0")
@dataclass
class PointV1:
    x: int
    y: int


# Existing callers using integer coordinates still work and are forwarded to PointV2:
p_old = PointV1(3, 4)
print(isinstance(p_old, PointV2))
print((p_old.x, p_old.y))

# New callers can use higher precision directly:
p_new = PointV2(3.25, 4.75)
print((p_new.x, p_new.y))
```

<details>
  <summary>Output: <code>print((p_new.x, p_new.y)</code></summary>

```
True
True
True
True
(3, 4)
(3.25, 4.75)
```

</details>

## Automatic docstring updates

Set `update_docstring=True` to inject a deprecation notice directly into the function's docstring at import time. The rendered API reference (Sphinx or MkDocs) always shows the deprecation status alongside the signature, with no manual upkeep.

!!! tip "See it live"

    The [Sphinx demo](../demo-sphinx/index.html) and [MkDocs demo](../demo-mkdocs/index.html) show how the injected notice renders in real API docs.

```python
# NEW/FUTURE API — renamed to be more explicit about what it does
def transform(x: int) -> int:
    """New implementation of the function."""
    return x * 2


transform.__module__ = "your_module"


# ---------------------------

from deprecate import deprecated


# DEPRECATED API — `process` was the original name before the rename
@deprecated(
    target=transform,
    deprecated_in="1.0",
    remove_in="2.0",
    update_docstring=True,  # Enable automatic docstring updates
)
def process(x: int) -> int:
    """Transforms the input value.

    Args:
        x: Input value

    Returns:
        Result of computation
    """
    pass


# The docstring now includes deprecation information (inserted before "Args:")
print(process.__doc__)
# Output includes:
# .. deprecated:: 1.0
#    Will be removed in 2.0.
#    Use `your_module.transform` instead.
```

<details>
  <summary>Output: <code>print(process.__doc__)</code></summary>

```
Transforms the input value.

.. deprecated:: 1.0
   Will be removed in 2.0.
   Use :func:`your_module.transform` instead.

Args:
    x: Input value

Returns:
    Result of computation
```

</details>

For MkDocs projects using `mkdocstrings`, switch to the admonition output style and register the Griffe extension so the injected notice renders correctly:

```python
from deprecate import deprecated


def transform(x: int) -> int:
    return x * 2


transform.__module__ = "your_module"


@deprecated(
    target=transform,
    deprecated_in="1.0",
    remove_in="2.0",
    update_docstring=True,
    docstring_style="mkdocs",  # alias: "markdown"
)
def process(x: int) -> int:
    """Transforms the input value."""
    pass


print(process.__doc__)
# !!! warning "Deprecated in 1.0"
#     Will be removed in 2.0.
#     Use `your_module.transform` instead.
```

<details>
  <summary>Output: <code>print(process.__doc__)</code></summary>

```
Transforms the input value.

!!! warning "Deprecated in 1.0"
    Will be removed in 2.0.
    Use `your_module.transform` instead.
```

</details>

Register the extension in `mkdocs.yml` so `mkdocstrings` picks up the runtime-injected notice:

```yaml
# mkdocs.yml
plugins:
  - mkdocstrings:
      handlers:
        python:
          extensions:
            - deprecate.docstring.griffe_ext:RuntimeDocstrings
```

## Injecting new required arguments

When the replacement function gains a new required parameter that the old API never had, use `args_extra` to inject a fixed default. This forwards calls without breaking existing callers while the deprecation notice tells them to migrate.

```python
from deprecate import deprecated, void


# NEW/FUTURE API — `send_email` adds an explicit `priority` field
def send_email(to: str, subject: str, priority: str) -> str:
    return f"Sent to {to!r}: {subject!r} [{priority}]"


# DEPRECATED API — `notify` was the original name; it had no `priority` concept
@deprecated(
    target=send_email,
    deprecated_in="1.5",
    remove_in="2.0",
    # callers of `notify` never passed `priority`, so inject a sensible default
    args_extra={"priority": "normal"},
)
def notify(to: str, subject: str) -> str:
    """Deprecated — use send_email() with an explicit priority instead."""
    return void(to, subject)


# calling this function will raise a deprecation warning:
#   The `notify` was deprecated since v1.5 in favor of `your_module.send_email`.
#   It will be removed in v2.0.
print(notify("alice@example.com", "Hello"))
```

<details>
  <summary>Output: <code>print(notify("alice@example.com", "Hello")</code></summary>

```
Sent to 'alice@example.com': 'Hello' [normal]
```

</details>

`args_extra` only applies when `target` is a callable. It merges into the forwarded kwargs after `args_mapping`, so extra values can override mapped ones. It is ignored for `target=True` (self-deprecation).

## Suppressing `FutureWarning` in test fixtures with `assert_no_warnings`

In test setup code (fixtures, helpers, factory functions), you often need to call deprecated functions without flooding the output with `FutureWarning` noise. `assert_no_warnings` catches and discards warnings of the specified type inside the block, while asserting that no such warning escapes.

Here is the gotcha: this is different from `pytest.warns` (which asserts a warning IS emitted) and from `warnings.filterwarnings("ignore")` (which silences globally without assertion). `assert_no_warnings` gives you a scoped, assertion-backed silence. If the code unexpectedly starts emitting a different warning category, that still surfaces.

```python
import warnings
from deprecate import deprecated, assert_no_warnings, void


def new_create_client(host: str, port: int = 443) -> dict:
    return {"host": host, "port": port}


@deprecated(target=new_create_client, deprecated_in="1.0", remove_in="2.0")
def create_client(host: str, port: int = 443) -> dict:
    return void(host, port)


# In test fixtures you need the forwarding result but not the warning noise.
# Use warnings.catch_warnings to suppress, then assert_no_warnings for new code:
def make_test_client() -> dict:
    """Test helper that calls the deprecated API without emitting warnings."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        return create_client("localhost", 8080)


# The helper works silently:
client = make_test_client()
print(client)


# For verifying that NEW code does NOT emit warnings, use assert_no_warnings:
with assert_no_warnings(FutureWarning):
    result = new_create_client("example.com")
print(result)
```

<details>
  <summary>Output: <code>print(result)</code></summary>

```
{'host': 'localhost', 'port': 8080}
{'host': 'example.com', 'port': 443}
```

</details>

Quick reference for choosing the right testing tool:

| Tool                                                   | Purpose                       | Fails when...                 |
| ------------------------------------------------------ | ----------------------------- | ----------------------------- |
| `pytest.warns(FutureWarning)`                          | Assert warning IS emitted     | No matching warning raised    |
| `assert_no_warnings(FutureWarning)`                    | Assert warning is NOT emitted | A matching warning IS raised  |
| `warnings.catch_warnings()` + `simplefilter("ignore")` | Suppress without assertion    | Never fails (use in fixtures) |

Use `assert_no_warnings` in test assertions to verify that refactored code no longer triggers deprecation notices. Use `warnings.catch_warnings` in fixtures when you need to call deprecated code silently during setup.

______________________________________________________________________

Next: [void() Helper](void-helper.md) — understanding the no-op body helper, or [Audit Tools](audit.md) for CI enforcement utilities.
