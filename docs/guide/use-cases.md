---
id: use-cases
description: Fourteen real-world deprecation patterns for Python functions, methods, classes, Enums, dataclasses, and constants — each with a worked example and full code.
---

# Use Cases

The two most common reasons to deprecate something are renaming a function and renaming an argument. Both require more than a bare `warnings.warn`: you need call forwarding, argument remapping, and a way to keep the old code working until removal day. This page walks through each pattern pyDeprecate supports, from a simple rename to proxy-wrapped Enums and multi-hop argument chains. If you are new to the library, start with [Getting Started](../getting-started.md).

## Simple function forwarding

!!! danger "Body is dead code when `target=<callable>`"

    When `target` is set to a callable, pyDeprecate intercepts every call **before** the function body runs — the body is dead code under normal forwarding. **Exception**: if you also use `skip_if` and it evaluates `True` at call time, the source body executes as a fallback. In that case keep a working body; otherwise `skip_if=True` calls silently return `None`.

    Do **not** call the target from inside the body:

    ```python
    from deprecate import deprecated, void


    def new_func(x: int) -> int:
        return x * 2


    # WRONG — new_func(x) is never reached; the decorator forwards before the body runs
    @deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
    def old_func(x: int) -> int:
        return new_func(x)


    # CORRECT — body is empty; pyDeprecate handles all forwarding automatically
    @deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
    def old_func(x: int) -> int:
        return void(x)  # or: pass  or: """Original function description."""
    ```

Apply `@deprecated(target=new_func)` to the old name and pyDeprecate forwards every call (positional and keyword arguments included) to the new function. Under normal forwarding the body is dead code, so leave it empty or put a docstring there (see also [void() helper](void-helper.md) for a null-forwarding idiom). The one exception is `skip_if=True` at call time — see the danger admonition above — where the source body executes as a fallback; keep a working body when combining `target=<callable>` with `skip_if`.

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
  <summary>Output: <code>calculate(1, 2)</code></summary>

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
print(calculate(1, 2))
```

<details>
  <summary>Output: <code>calculate(1, 2)</code></summary>

```
3
```

</details>

## Argument renaming and mapping

Use `args_mapping` when the new function accepts the same arguments under different names. The decorator translates old parameter names to new ones at call time, so callers can keep passing the old names during the deprecation window without any manual mapping code.

```python
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

!!! warning "The function body still executes with `TargetMode.NOTIFY` — keep a working implementation"

    Unlike `target=<callable>` (where the body is **dead code** under normal forwarding, but still executes as a fallback when `skip_if=True` at call time), `TargetMode.NOTIFY` runs the original function body after emitting the deprecation notice. You **must** keep a working implementation in the function body. An empty body (`pass`) will cause the function to return `None` instead of the intended value (the deprecation warning still fires).

Use warn-only mode when a function is going away but has no replacement yet. The decorator emits a deprecation notice and then runs the function body normally. This is the right choice when callers need to update their own code, not switch to a different function.

Since `target` defaults to `TargetMode.NOTIFY`, you can omit it entirely:

```python
from deprecate import deprecated


@deprecated(deprecated_in="0.1", remove_in="0.5")
def my_sum(a: int, b: int = 5) -> int:
    """My deprecated function which still has to have implementation."""
    return a + b


# calling this function will raise a deprecation warning:
#   The `my_sum` was deprecated since v0.1. It will be removed in v0.5.
print(my_sum(1, 2))
```

<details>
  <summary>Output: <code>my_sum(1, 2)</code></summary>

```
3
```

</details>

## Self argument mapping

Use `TargetMode.ARGS_REMAP` to rename or drop an argument within the same function. The decorator remaps the old argument name to the new one before the body runs, so your implementation only needs the new name. This is the right pattern when refactoring a signature without moving the function.

```python
from deprecate import TargetMode, deprecated


@deprecated(
    # define as deprecation some self argument - mapping
    target=TargetMode.ARGS_REMAP,
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
  <summary>Output: <code>any_pow(2, 3)</code></summary>

```
8
```

</details>

To drop an argument entirely, map it to `None`. The decorator emits a deprecation notice when the argument is passed and then discards it.

```python
from deprecate import TargetMode, deprecated
from typing import Optional


@deprecated(
    target=TargetMode.ARGS_REMAP,
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
print(my_func(value=42, legacy_param="old"))
```

<details>
  <summary>Output: <code>my_func(value=42, legacy_param="old")</code></summary>

```
84
```

</details>

## `TargetMode.NOTIFY` vs `TargetMode.ARGS_REMAP` vs `target=<callable>` — key differences

These modes differ in whether the function body runs, whether a warning fires, and which parameters take effect.

!!! tip

    `TargetMode.NOTIFY` replaces the old `target=None` sentinel and `TargetMode.ARGS_REMAP` replaces the old `target=True` sentinel. The old forms still work but emit a `FutureWarning` at decoration time.

### Behaviour comparison

|                               | `TargetMode.NOTIFY`                                                                                       | `TargetMode.ARGS_REMAP` (with `args_mapping`)                              | `target=<callable>`                                                                                                                                                                            |
| ----------------------------- | --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Warning emitted**           | Yes — up to `num_warns` times (default: once)                                                             | Per deprecated arg, up to `num_warns` times (default: once)                | Yes — up to `num_warns` times (default: once)                                                                                                                                                  |
| **Warning template**          | `"… was deprecated since vX. It will be removed in vY."`                                                  | `"… uses deprecated arguments: …"`                                         | `"… was deprecated … in favour of …"`                                                                                                                                                          |
| **`template_mgs` specifiers** | `source_name`, `source_path`, `deprecated_in`, `remove_in` only — `target_name`/`target_path` unavailable | `source_name`, `source_path`, `argument_map`, `deprecated_in`, `remove_in` | All specifiers incl. `target_name`, `target_path`                                                                                                                                              |
| **Function body**             | Runs with caller's args + source defaults filled in                                                       | Runs after argument renaming/dropping                                      | **Does not run** under normal forwarding — body is dead code, calls intercepted first. **Exception**: `skip_if=True` at call time bypasses forwarding and executes the source body as fallback |
| **`args_mapping` applied**    | `⚠`                                                                                                       | `✓` renames or drops listed args                                           | `✓` renames or drops args before forwarding                                                                                                                                                    |
| **`args_extra` injected**     | `⚠`                                                                                                       | `✓` merged into kwargs before call                                         | `✓` merged into kwargs before forwarding                                                                                                                                                       |
| **Source defaults merged**    | `✓`                                                                                                       | `✗`                                                                        | `✓`                                                                                                                                                                                            |
| **`skip_if` effect**          | `⊛`                                                                                                       | `⊛`                                                                        | `⊛`                                                                                                                                                                                            |
| **`stream=None` effect**      | `⊘` body still runs                                                                                       | `⊘` remapping still runs                                                   | `⊘` forwarding still runs                                                                                                                                                                      |

**Legend:** `✓` applied · `✗` not applied · `⚠` ignored with `UserWarning` (will be `TypeError` in v1.0) · `⊘` warning suppressed, processing continues · `⊛` `skip_if` bypasses everything · `—` not applicable

### When to use which

- **`TargetMode.NOTIFY`** — function is going away with no replacement. Callers must remove the call. Warning fires up to `num_warns` times (default: once) so each caller is notified on first use.
- **`target=<callable>`** — function is replaced by another callable. The source body never runs under normal forwarding (exception: `skip_if=True` bypasses forwarding and executes the source body as fallback). Use `args_mapping` to rename arguments and `args_extra` to inject new required args.
- **`TargetMode.ARGS_REMAP` + `args_mapping`** — function stays but its signature is changing. Warning fires only when the old argument name is actually used, so callers who already migrated see no noise.

### Example — notice the difference in warning behaviour

```python
from deprecate import TargetMode, deprecated


# TargetMode.NOTIFY: warns once by default (num_warns=1)
@deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
def going_away(x: int) -> int:
    return x


# TargetMode.ARGS_REMAP: warns only when the old argument name is passed
@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_x": "x"}, deprecated_in="1.0", remove_in="2.0")
def renamed_arg(old_x: int = 0, x: int = 0) -> int:
    return x


going_away(1)  # warns: FutureWarning — "going_away was deprecated since v1.0 …"
renamed_arg(x=1)  # silent — new name used, no deprecated arg present
renamed_arg(old_x=1)  # warns: FutureWarning — "renamed_arg uses deprecated arguments: `old_x` → `x` …"
```

!!! danger "`target=True` (or `TargetMode.ARGS_REMAP`) without `args_mapping` is a misconfiguration"

    As of v0.8, `target=True` is a deprecated sentinel for `TargetMode.ARGS_REMAP`. Using either without `args_mapping` emits construction-time warnings: a `FutureWarning` for the legacy sentinel, and a `UserWarning` because `ARGS_REMAP` requires `args_mapping` to have any effect. This will become a `TypeError` in v1.0. If your intent is to warn callers with no forwarding or remapping, use `TargetMode.NOTIFY` instead.

## Stacked deprecation decorators

Stack multiple `@deprecated` decorators on a single function to handle migrations that span several releases. Each layer tracks its own version range and warning count independently.

!!! warning "Not all stacking combinations are supported"

    Only three combinations work correctly. Everything else emits `UserWarning` at **decoration time** (not at call time) so you catch the misconfiguration immediately. See the [supported combinations table](#supported-stacking-combinations) below.

### Pattern 1 — multi-step argument renames (ARGS_REMAP + ARGS_REMAP)

When an argument is renamed more than once across releases, stack one `@deprecated(TargetMode.ARGS_REMAP, ...)` per rename. Each decorator operates on its own version range and emits a separate notice, giving callers version-specific migration guidance.

```python
from deprecate import TargetMode, deprecated


@deprecated(
    TargetMode.ARGS_REMAP,
    deprecated_in="0.3",
    remove_in="0.6",
    args_mapping=dict(c1="nc1"),
    template_mgs="Depr: v%(deprecated_in)s rm v%(remove_in)s for args: %(argument_map)s.",
)
@deprecated(
    TargetMode.ARGS_REMAP,
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
  <summary>Output: <code>any_pow(2, 3)</code></summary>

```
8
```

</details>

### Pattern 2 — lifecycle migration (ARGS_REMAP + NOTIFY)

The most common real-world lifecycle: an argument is renamed in an early release (`ARGS_REMAP`), then the entire function is deprecated in a later release once a complete replacement exists (`NOTIFY`). Put `ARGS_REMAP` outermost (top decorator) and `NOTIFY` below it.

Callers still using the old argument name receive **both** warnings — the arg-rename notice and the function-deprecated notice. Callers already using the new name receive only the function-deprecated notice.

```python
from deprecate import TargetMode, deprecated


@deprecated(TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"factor": "scale"})
@deprecated(TargetMode.NOTIFY, deprecated_in="2.0", remove_in="3.0")
def compute_power(base: float, factor: float = 1, scale: float = 1) -> float:
    return base**scale


print(compute_power(2, factor=3))  # → 2 warnings (arg rename + function deprecated)
print(compute_power(2, scale=3))  # → 1 warning  (function deprecated only)
```

<details>
  <summary>Output: <code>compute_power(2, factor=3); compute_power(2, scale=3)</code></summary>

```
8
8
```

</details>

!!! danger "Wrong order raises `UserWarning` at decoration time"

    `@deprecated(NOTIFY)` on top of `@deprecated(ARGS_REMAP)` is the wrong order — pyDeprecate detects it and warns immediately at decoration time with the message *"Reverse the decorator order: put @deprecated(ARGS_REMAP, ...) outermost"*.

### Supported stacking combinations

| Outer (top)  | Inner (bottom) | Status                             | Notes                                                                                                                                                              |
| ------------ | -------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `ARGS_REMAP` | `ARGS_REMAP`   | ✓ Supported                        | Multi-step argument renames across versions                                                                                                                        |
| `ARGS_REMAP` | `NOTIFY`       | ✓ Supported                        | Lifecycle: rename args first, then deprecate the whole function                                                                                                    |
| `NOTIFY`     | `callable`     | ✓ Supported                        | Outer NOTIFY warns callers the function is going away; inner callable handles forwarding. Prefer `@deprecated(target=<callable>)` directly — same effect, simpler. |
| `callable`   | `callable`     | ✗ `UserWarning` at decoration time | Use a single `@deprecated(target=<callable>)` instead                                                                                                              |
| `callable`   | `ARGS_REMAP`   | ✗ `UserWarning` at decoration time | Collapse to `@deprecated(target=fn, args_mapping={...})`                                                                                                           |
| `callable`   | `NOTIFY`       | ✗ `UserWarning` at decoration time | Collapse to a single `@deprecated(target=<callable>)`                                                                                                              |
| `ARGS_REMAP` | `callable`     | ✗ `UserWarning` at decoration time | Update the inner decorator to include both `target=` and `args_mapping=`                                                                                           |
| `NOTIFY`     | `NOTIFY`       | ✗ `UserWarning` at decoration time | Update the existing decorator's versions instead of adding a second one                                                                                            |
| `NOTIFY`     | `ARGS_REMAP`   | ✗ `UserWarning` at decoration time | Wrong order — swap: `ARGS_REMAP` on top, `NOTIFY` below                                                                                                            |

### N-level stacking

Any sequence of supported adjacent pairs stacks transitively. The guard inspects only one hop at a time — as long as each adjacent pair is a supported combination, the full stack is accepted silently.

**Example — three-level lifecycle migration:**

```python
from deprecate import TargetMode, deprecated


@deprecated(TargetMode.ARGS_REMAP, deprecated_in="0.3", remove_in="0.6", args_mapping={"c1": "nc1"})
@deprecated(TargetMode.ARGS_REMAP, deprecated_in="0.4", remove_in="0.7", args_mapping={"nc1": "nc2"})
@deprecated(TargetMode.NOTIFY, deprecated_in="0.7", remove_in="1.0")
def any_pow(base, c1: float = 0, nc1: float = 0, nc2: float = 2) -> float:
    return base**nc2


print(any_pow(2))
```

<details>
  <summary>Output: <code>any_pow(2)</code></summary>

```
4
```

</details>

Each adjacent pair is `ARGS_REMAP + ARGS_REMAP` (supported) and `ARGS_REMAP + NOTIFY` (supported), so no decoration-time warning fires. The three layers execute in turn at call time.

!!! note "Unsupported pair breaks the whole chain"

    A single unsupported adjacent pair anywhere in the stack emits `UserWarning` at decoration time for that pair. Chains with `NOTIFY + ARGS_REMAP` adjacent remain unsupported — the wrong-order warning still fires.

Use [`validate_deprecation_chains()`](audit.md#detecting-deprecation-chains) in CI to catch accidental deprecated-to-deprecated chains automatically.

## Conditional skip

`skip_if` accepts a boolean or a zero-argument callable returning a boolean. When it evaluates to `True`, the deprecation notice is suppressed and the call proceeds normally. This is useful when behaviour depends on runtime conditions, for example suppressing the notice once the caller has migrated to a newer dependency.

```python
from deprecate import TargetMode, deprecated

FAKE_VERSION = 1


def version_greater_1():
    return FAKE_VERSION > 1


@deprecated(TargetMode.ARGS_REMAP, "0.3", "0.6", args_mapping=dict(c1="nc1"), skip_if=version_greater_1)
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
  <summary>Output: <code>skip_pow(2, 3)</code></summary>

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
  <summary>Output: <code>svc.run(5)</code></summary>

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
  <summary>Output: <code>inst.my_d</code></summary>

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
  <summary>Output: <code>DEFAULTS["lr"]</code></summary>

```
0.001
```

</details>

## Enums and dataclasses

`deprecated_class()` wraps an Enum or dataclass in a transparent proxy that emits a deprecation notice on access and forwards attribute, item, and call operations to the replacement. Use `args_mapping` to rename or drop kwargs when the deprecated class is called. When `args_mapping` is provided without an explicit `target`, the proxy auto-resolves to `TargetMode.ARGS_REMAP` and warns **only when an old argument name is actually used** — matching the per-argument behaviour of `@deprecated(target=TargetMode.ARGS_REMAP, args_mapping=...)`. Callers already using the new argument names see no warning. Type checks (`isinstance`, `issubclass`) pass through without emitting notices, since they are structural checks rather than usage of the deprecated API. Use `args_extra` to inject fixed kwargs into every forwarded call, and `template_mgs` to override the default warning message — both work identically to their `@deprecated` counterparts.

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
  <summary>Output: <code>(p_new.x, p_new.y)</code></summary>

```
True
True
True
True
(3, 4)
(3.25, 4.75)
```

</details>

## Selective attribute deprecation

Use `attrs_mapping` on `deprecated_class()` to deprecate only specific attribute names — all other attributes pass through silently. This covers attribute renames, misspelling corrections (e.g. `color` → `colour`), and warn-only notices on individual attributes.

The mapping keys are the deprecated attribute names; values are either the canonical replacement name (string) or `None` for a warn-only notice with no rename. Reads, writes, and deletes on deprecated attribute names all warn and resolve against the active class. Non-listed attribute names pass through without any warning.
Non-`None` values must exist on the `target` class when `target=` is provided, or on the wrapped source class otherwise. Redirect chains such as `{"a": "b", "b": "c"}` are allowed at decoration time and reported by audit as `ChainType.STACKED`; cycles such as `{"a": "b", "b": "a"}` raise immediately.

### Decorator syntax — attribute rename

Apply `@deprecated_class(attrs_mapping=...)` at class definition time. Only the attribute names listed as keys emit a `FutureWarning`; all others pass through silently:

```python
from deprecate import deprecated_class


@deprecated_class(
    attrs_mapping={"color": "colour"},  # "color" is the deprecated spelling
    deprecated_in="2.0",
    remove_in="3.0",
)
class Palette:
    colour: str = "red"  # canonical name
    size: int = 10  # unlisted — silent passthrough, no warning


# Deprecated alias — warns: "The `color` was deprecated since v2.0 in favor of `Palette.colour`."
print(Palette.color)  # red

# Canonical names — silent passthrough, no warning
print(Palette.colour)  # red
print(Palette.size)  # 10
```

<details>
  <summary>Output: <code>Palette.color; Palette.colour; Palette.size</code></summary>

```
red
red
10
```

</details>

Wrapper form — equivalent to decorator syntax, useful when wrapping an already-existing class or applying deprecation outside the class definition:

```python
from deprecate import deprecated_class


class Config:
    colour: str = "red"
    size: int = 42
    timeout: int = 30


# Misspelling migration: "color" → "colour"; "size" is warn-only (no rename)
DeprecatedConfig = deprecated_class(
    attrs_mapping={"color": "colour", "size": None},
    deprecated_in="1.0",
    remove_in="2.0",
)(Config)

print(DeprecatedConfig.color)  # warns → returns Config.colour ("red")
print(DeprecatedConfig.colour)  # silent passthrough ("red")
print(DeprecatedConfig.size)  # warns (warn-only, size=42 unchanged)
```

<details>
  <summary>Output: <code>DeprecatedConfig.color; DeprecatedConfig.colour; DeprecatedConfig.size</code></summary>

```
red
red
42
```

</details>

### Reads, writes, and deletes all redirect

The `attrs_mapping` interception applies to all three access modes. Writing to a deprecated attribute alias warns and sets the canonical attribute instead:

```python
from deprecate import deprecated_class


class Palette:
    colour: str = "red"


DeprecatedPalette = deprecated_class(
    attrs_mapping={"color": "colour"},  # "color" is the deprecated spelling
    deprecated_in="1.0",
    remove_in="2.0",
)(Palette)

# Write — warns: FutureWarning and redirects to Palette.colour
DeprecatedPalette.color = "blue"  # warns: FutureWarning

# Canonical attribute now holds the new value (no warning on canonical reads)
print(Palette.colour)  # blue
```

<details>
  <summary>Output: <code>Palette.colour</code></summary>

```
blue
```

</details>

### Warn-only with `None` redirect

Map a deprecated attribute to `None` to emit a warning on access without renaming anything. The attribute is fetched by its original name on the active class after the warning fires. Use this when an attribute is going away with no replacement:

```python
from deprecate import deprecated_class


class Widget:
    size: int = 42  # scheduled for removal — callers should stop reading it


DeprecatedWidget = deprecated_class(
    attrs_mapping={"size": None},  # warn-only, no rename
    deprecated_in="1.0",
    remove_in="2.0",
)(Widget)

# Warns: "The `size` was deprecated since v1.0. It will be removed in v2.0."
print(DeprecatedWidget.size)  # 42 — value still returned, just warned

# Second access is silent — num_warns=1 budget exhausted
print(DeprecatedWidget.size)  # 42 — no second warning
```

<details>
  <summary>Output: <code>DeprecatedWidget.size; DeprecatedWidget.size (second call)</code></summary>

```
42
42
```

</details>

### Per-attribute independent warning budgets

Each deprecated attribute name has its own warning counter. With `num_warns=1` (the default), accessing two different deprecated aliases each emits one warning independently — two warnings total, not one shared budget:

```python
from deprecate import deprecated_class


class Config:
    colour: str = "red"
    text: str = "hello"


proxy = deprecated_class(
    attrs_mapping={"color": "colour", "txt": "text"},
    deprecated_in="1.0",
    remove_in="2.0",
)(Config)

print(proxy.color)  # warns: FutureWarning — "color" budget consumed
print(proxy.txt)  # warns: FutureWarning — "txt" budget consumed (independent counter)

# Both budgets now exhausted — subsequent accesses are silent
print(proxy.color)  # silent
print(proxy.txt)  # silent
```

<details>
  <summary>Output: <code>proxy.color; proxy.txt; proxy.color (silent); proxy.txt (silent)</code></summary>

```
red
hello
red
hello
```

</details>

### Enum — deprecated member aliases

`attrs_mapping` works on Enum proxies too. Use it when Enum member names changed (for example, a casing convention migration) and callers may still be using the old names. Wrap the canonical Enum in a proxy that registers the deprecated names as aliases:

```python
from enum import Enum
from deprecate import deprecated_class


class Direction(Enum):
    NORTH = "N"
    SOUTH = "S"
    EAST = "E"
    WEST = "W"


# Wrap the canonical Enum and add deprecated lowercase aliases that redirect to canonical members
LegacyDirection = deprecated_class(
    attrs_mapping={"north": "NORTH", "south": "SOUTH"},
    deprecated_in="1.0",
    remove_in="2.0",
)(Direction)

# Deprecated lowercase alias — warns and returns the canonical Direction.NORTH member
print(LegacyDirection.north is Direction.NORTH)  # True

# Canonical uppercase name — silent passthrough, no warning
print(LegacyDirection.NORTH is Direction.NORTH)  # True
```

<details>
  <summary>Output: <code>LegacyDirection.north is Direction.NORTH; LegacyDirection.NORTH is Direction.NORTH</code></summary>

```
True
True
```

</details>

`attrs_mapping` can be combined with `target=NewClass`; listed attribute aliases redirect to their canonical counterparts on the target class. Unlisted attributes and calls continue to use the normal target-forwarding behaviour.

!!! note "Audit visibility"

    `find_deprecation_wrappers` discovers the proxy via its class-level `__deprecated__`. Individual `attrs_mapping` entries are data inside the single proxy config and are not emitted as separate `DeprecationWrapperInfo` records. All entries share the same `deprecated_in`/`remove_in` lifecycle.

### Explicit `TargetMode.ATTRS_REMAP` form

Passing `attrs_mapping` alone auto-resolves the mode to `TargetMode.ATTRS_REMAP`. The equivalent self-documenting form is to pass `target=TargetMode.ATTRS_REMAP` together with `attrs_mapping` — both forms are behaviourally identical, and the explicit form makes the intent visible at the call site without changing semantics:

```python
from deprecate import TargetMode, deprecated_class


class Palette:
    colour: str = "red"  # canonical name


# Explicit form — equivalent to passing `attrs_mapping` alone
DeprecatedPalette = deprecated_class(
    target=TargetMode.ATTRS_REMAP,
    attrs_mapping={"color": "colour"},
    deprecated_in="1.0",
    remove_in="2.0",
)(Palette)

print(DeprecatedPalette.color)  # warns → returns "red"
```

<details>
  <summary>Output: <code>DeprecatedPalette.color</code></summary>

```
red
```

</details>

Three misconfiguration combinations are caught at decoration time and emit a `UserWarning` (planned to become `TypeError` in `v1.0`):

| Misconfiguration                                        | Why it is wrong                                                                                                                            |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `target=TargetMode.NOTIFY` + `attrs_mapping=...`        | `NOTIFY` means "warn on every access"; `attrs_mapping` switches to selective per-attribute warning. They contradict each other — drop one. |
| `target=TargetMode.ATTRS_REMAP` without `attrs_mapping` | `ATTRS_REMAP` requires the deprecated attribute names listed via `attrs_mapping`. Without it the proxy has zero selective effect.          |
| `attrs_mapping={}` (empty dict)                         | An empty mapping has no effect. Remove it or add deprecated attribute names.                                                               |

`TargetMode.ATTRS_REMAP` is a **proxy-only** mode: applying it via `@deprecated(target=TargetMode.ATTRS_REMAP)` on a function, method, or property raises `TypeError` at decoration time, with the error message pointing to `deprecated_class(attrs_mapping=...)` as the correct API.

### Callable target with attribute redirection

When `deprecated_class` receives both `target=NewClass` and `attrs_mapping`, the two features compose cleanly: listed deprecated attribute aliases resolve against `NewClass`, while unlisted attributes and instantiation calls also forward to `NewClass`. Entries mapped to `None` keep the same attribute name on `NewClass`; for example, `attrs_mapping={"size": None}` warns and then reads, writes, or deletes `NewClass.size`. Use this pattern for a full class replacement where some attribute names changed between the old and the new class.

```python
from deprecate import deprecated_class


class Config:
    lr: float = 0.01  # canonical name in the new class
    batch_size: int = 32  # unchanged attribute


@deprecated_class(
    target=Config,
    attrs_mapping={"learning_rate": "lr"},  # "learning_rate" was renamed to "lr"
    deprecated_in="2.0",
    remove_in="3.0",
    num_warns=-1,
)
class LegacyConfig:
    learning_rate: float = 0.01  # old name — will warn
    lr: float = 0.01  # canonical alias also present


print(LegacyConfig.learning_rate)  # warns: FutureWarning — value from Config.lr

print(LegacyConfig.lr)  # silent — canonical name

print(LegacyConfig.batch_size)  # silent — unlisted attribute
```

<details>
  <summary>Output: <code>LegacyConfig.learning_rate; LegacyConfig.lr; LegacyConfig.batch_size</code></summary>

```
0.01
0.01
32
```

</details>

Instantiation calls are also forwarded to `Config` — `LegacyConfig(lr=0.05)` returns a `Config` instance. The `attrs_mapping` applies only to class-level attribute access on the proxy, not to the returned instance.

### Dataclass field renames

When the wrapped class is a `@dataclass`, `deprecated_class(attrs_mapping=...)` automatically covers **both surfaces** in a single call: attribute access on an existing instance (`obj.old_field`) and constructor kwargs (`DC(old_field=5)`) both emit `FutureWarning`. The auto-expand copies each `attrs_mapping` entry whose redirect target is a dataclass field into `args_mapping`, so you do not need to set `args_mapping` separately for a pure field rename. Entries already present in an explicit `args_mapping` are never overwritten — explicit user values always win.

```python
from dataclasses import dataclass
from deprecate import deprecated_class


@dataclass
class NewPoint:
    x: float = 0.0
    y: float = 0.0


OldPoint = deprecated_class(
    attrs_mapping={"px": "x", "py": "y"},
    deprecated_in="2.0",
    remove_in="3.0",
    num_warns=-1,
)(NewPoint)

# Constructor kwarg warns: FutureWarning — "px" remapped to "x"
pt = OldPoint(px=1.0)  # warns: FutureWarning
assert pt.x == 1.0
```

### Class-type compatibility

C-extension types, classes whose constructor accepts only positional-only parameters (e.g. `def __init__(self, val, /): ...`), and `tuple`/`frozenset` subclasses emit `UserWarning` at decoration time when `args_mapping` remaps a deprecated kwarg to a `POSITIONAL_ONLY` constructor parameter. At call time the proxy falls back to `setattr` for those entries instead of passing the remapped name as a constructor kwarg, so the instance is created and then the field is patched in — which behaves correctly for regular dataclasses but may not suit all class types. Run `validate_mapping_compatibility(module)` in CI to surface these patterns before they reach users.

### Combining attribute and argument deprecation

`attrs_mapping` and `args_mapping` operate on orthogonal surfaces: `attrs_mapping` intercepts class-level attribute access (`__getattr__` / `__setattr__` / `__delattr__` on the proxy), while `args_mapping` intercepts call arguments (`__call__`). Both can be combined on the same proxy when `target` is a callable class with renamed class attributes and a renamed constructor parameter.

```python
from deprecate import deprecated_class


class NewTrainer:
    epochs: int = 10  # class-level default, required for attrs_mapping validation
    lr: float = 0.01  # class-level default, required for attrs_mapping validation

    def __init__(self, lr: float = 0.01, epochs: int = 10) -> None:
        self.lr = lr
        self.epochs = epochs


@deprecated_class(
    target=NewTrainer,
    attrs_mapping={"n_epochs": "epochs"},  # class-level attribute rename
    args_mapping={"learning_rate": "lr"},  # constructor argument rename
    deprecated_in="2.0",
    remove_in="3.0",
    num_warns=-1,
)
class LegacyTrainer:
    pass


# Warning path 1 — args_mapping fires: old kwarg "learning_rate" remapped to "lr"
trainer = LegacyTrainer(learning_rate=0.05)  # warns: FutureWarning
print(trainer.lr)  # NewTrainer instance has lr=0.05

# Warning path 2 — attrs_mapping fires: class-level "n_epochs" redirects to "epochs"
default_epochs = LegacyTrainer.n_epochs  # warns: FutureWarning
print(default_epochs)  # value from NewTrainer.epochs
```

<details>
  <summary>Output: <code>trainer.lr; default_epochs</code></summary>

```
0.05
10
```

</details>

The two warning budgets are independent — exhausting one does not affect the other. Each deprecated name (argument or attribute) maintains its own counter, so `num_warns=1` (the default) allows each old name to warn exactly once before silencing.

**Choosing between a single call and stacking**

Use a **single `deprecated_class()` call** when all attributes and arguments share the same `deprecated_in`/`remove_in` — it is the simplest form and keeps both mappings in one place.

**Stack two `@deprecated_class()` decorators** when different attributes were deprecated at different versions and each mapping needs its own version pair:

```python
from deprecate import deprecated_class


@deprecated_class(
    attrs_mapping={"old_attr": "new_attr"},
    deprecated_in="1.0",
    remove_in="2.0",
)
@deprecated_class(
    attrs_mapping={"older_attr": "newer_attr"},
    deprecated_in="0.9",
    remove_in="1.0",
)
class MyClass:
    newer_attr: str = "a"
    new_attr: str = "b"


obj = MyClass()
assert isinstance(obj, MyClass)
```

Stacking is fully supported: `isinstance()` and `issubclass()` resolve through the proxy chain, each layer emits its own version-accurate warning, and instantiation fires at most one global warning. As an alternative to stacking, use [`DeprecationEntry`](#per-entry-version-overrides-with-deprecationentry) values inside a single `deprecated_class()` call for the same per-attribute version control without an extra proxy layer. When stacking two `ATTRS_REMAP` layers, only the innermost layer’s instantiation warning fires — the outer layer’s `deprecated_in`/`remove_in` are omitted from the instantiation notice (attribute-access warnings remain independent and fire for each layer).

### Per-entry version overrides with `DeprecationEntry`

`DeprecationEntry` lets each key in `attrs_mapping` or `args_mapping` carry its own `deprecated_in`/`remove_in` pair, independent of the proxy-level fallback. Use it instead of stacking when you prefer a single proxy layer:

```python
from deprecate import DeprecationEntry, deprecated_class


class Config:
    new_attr: str = "b"
    newer_attr: str = "a"


proxy = deprecated_class(
    attrs_mapping={
        "old_attr": DeprecationEntry("new_attr", deprecated_in="1.0", remove_in="2.0"),
        "older_attr": DeprecationEntry("newer_attr", deprecated_in="0.9", remove_in="1.0"),
    },
    deprecated_in="0.9",  # fallback for plain-string entries
    remove_in="2.0",
)(Config)

proxy.old_attr  # warns: FutureWarning — deprecated since v1.0
proxy.older_attr  # warns: FutureWarning — deprecated since v0.9
```

Plain-string values and `None` (warn-only) entries continue to work as before and fall back to the proxy-level `deprecated_in`/`remove_in`. `DeprecationEntry` values are stored verbatim in `__deprecated__.attrs_mapping` and visible to audit tools.

### Chained redirect

`attrs_mapping` supports multi-hop rename chains. `{"num_iters": "num_steps", "num_steps": "max_steps"}` is a valid chain — accessing `proxy.num_iters` warns once (for `num_iters`) and resolves directly to the value stored under `num_steps` on the active class; accessing `proxy.num_steps` warns once (for `num_steps`) and resolves to `max_steps`. Audit reports this mapping structure as `ChainType.STACKED`. Cycles such as `{"a": "b", "b": "a"}` raise `ValueError` at decoration time.

Every non-`None` redirect target in the chain must be a static class attribute. In the example below, `num_steps` must exist on the class because it is a redirect target for `num_iters`:

```python
from deprecate import deprecated_class


class TrainLoop:
    max_steps: int = 200
    num_steps: int = max_steps  # redirect target — must exist as a static class attribute


proxy = deprecated_class(
    attrs_mapping={"num_iters": "num_steps", "num_steps": "max_steps"},
    deprecated_in="2.0",
    remove_in="3.0",
    num_warns=-1,
)(TrainLoop)


val1 = proxy.num_iters  # warns: FutureWarning — deprecated v1.0 name
print(val1)

val2 = proxy.num_steps  # warns: FutureWarning — deprecated v2.0 name
print(val2)

val3 = proxy.max_steps  # silent — canonical name
print(val3)
```

<details>
  <summary>Output: <code>val1; val2; val3</code></summary>

```
200
200
200
```

</details>

Each deprecated name in the chain fires exactly one warning per access (not two). The resolution is a single lookup hop: `proxy.num_iters` warns for `num_iters` and then reads `TrainLoop.num_steps` directly, which at the class level holds the same value as `max_steps`.

### Nested proxy wrappers

A `deprecated_class` proxy can wrap another `deprecated_class` proxy. The inner proxy handles selective attribute deprecation; the outer proxy adds a blanket class-level deprecation warning on every access regardless of attribute name. The two warning budgets are independent.

```python
from deprecate import deprecated_class


class Palette:
    colour: str = "red"
    color: str = colour  # deprecated alias kept for backwards compatibility


# Inner proxy: warns only when the deprecated alias "color" is accessed
selective_proxy = deprecated_class(
    attrs_mapping={"color": "colour"},
    deprecated_in="1.0",
    remove_in="2.0",
    num_warns=-1,
)(Palette)

# Outer proxy: warns on every attribute access regardless of name
blanket_proxy = deprecated_class(
    deprecated_in="1.0",
    remove_in="2.0",
    num_warns=-1,
)(selective_proxy)


# Accessing the deprecated alias through the outer proxy: two warnings fire —
# one from the outer blanket proxy ("Palette" is deprecated) and one from the
# inner selective proxy ("color" is deprecated in favor of "colour").
_ = blanket_proxy.color  # warns: FutureWarning × 2 — outer blanket + inner selective

# Accessing the canonical name through the outer proxy: one warning fires —
# only the outer blanket proxy warns; the inner proxy forwards silently.
_ = blanket_proxy.colour  # warns: FutureWarning × 1 — outer blanket only
```

The outer proxy issues its blanket class-deprecation warning first; the inner proxy then handles the attribute redirect. Two warnings fire for `blanket_proxy.color` — one per proxy layer. Accessing `blanket_proxy.colour` fires only the outer proxy warning because `colour` is not listed in the inner proxy's `attrs_mapping`.

### Real-world migration: ML training config

The following end-to-end example shows a typical ML library migration where a `TrainingConfig` dataclass renames several fields across versions. Existing code using the old attribute names continues to work with deprecation notices guiding users toward the canonical API.

Migration summary:

- v1.0 → v2.0: `lr` renamed to `learning_rate`, `n_epochs` renamed to `max_epochs`
- v2.0: `size` attribute removed with no replacement (warn-only, `None` redirect)
- Constructor: `hidden_dim` renamed to `hidden_size`

```python
from dataclasses import dataclass
from deprecate import deprecated_class, find_deprecation_wrappers


@dataclass
class TrainingConfig:
    learning_rate: float = 0.001
    max_epochs: int = 100
    hidden_size: int = 256


@deprecated_class(
    target=TrainingConfig,
    attrs_mapping={
        "lr": "learning_rate",  # v2.0 rename
        "n_epochs": "max_epochs",  # v2.0 rename
        "size": None,  # removed in v2.0 — warn-only, no replacement
    },
    args_mapping={"hidden_dim": "hidden_size"},  # constructor rename
    deprecated_in="2.0",
    remove_in="3.0",
)
class LegacyTrainingConfig:
    lr: float = 0.001
    learning_rate: float = 0.001
    n_epochs: int = 100
    max_epochs: int = 100
    size: int = 128  # removed in v2.0
    hidden_size: int = 256


# Old attribute names still work — each emits one FutureWarning
print(LegacyTrainingConfig.lr)  # warns: FutureWarning — "lr" → "learning_rate"
print(LegacyTrainingConfig.n_epochs)  # warns: FutureWarning — "n_epochs" → "max_epochs"
print(LegacyTrainingConfig.size)  # warns: FutureWarning — "size" removed (no replacement)
```

<details>
  <summary>Output: <code>LegacyTrainingConfig.lr; LegacyTrainingConfig.n_epochs; LegacyTrainingConfig.size</code></summary>

```
0.001
100
128
```

</details>

```python
# phmdoctest:skip
# Old constructor argument still works — emits one FutureWarning
cfg = LegacyTrainingConfig(hidden_dim=512)  # warns: FutureWarning
print(cfg.hidden_size)
```

<details>
  <summary>Output: <code>cfg.hidden_size</code></summary>

```
512
```

</details>

```python
# phmdoctest:skip
# Audit tools discover the proxy — useful for CI expiry checks
import sys
import types

mod = types.ModuleType("my_ml_lib")
mod.LegacyTrainingConfig = LegacyTrainingConfig
sys.modules["my_ml_lib"] = mod

results = find_deprecation_wrappers(mod)
print(results[0].function, results[0].deprecated_info.deprecated_in)
```

<details>
  <summary>Output: <code>results[0].function; results[0].deprecated_info.deprecated_in</code></summary>

```
LegacyTrainingConfig 2.0
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
  <summary>Output: <code>process.__doc__</code></summary>

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
  <summary>Output: <code>process.__doc__</code></summary>

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
  <summary>Output: <code>notify("alice@example.com", "Hello")</code></summary>

```
Sent to 'alice@example.com': 'Hello' [normal]
```

</details>

`args_extra` merges into kwargs after `args_mapping` is applied. It is used when `target` is a Callable or `TargetMode.ARGS_REMAP` (with `args_mapping`). For `TargetMode.NOTIFY`, it is not used for forwarding; supplying it also triggers a construction-time `UserWarning` when the decorator is applied.

## Suppressing `FutureWarning` in test fixtures with `assert_no_warnings`

In test setup code (fixtures, helpers, factory functions), you often need to call deprecated functions without flooding the output with `FutureWarning` noise. [`assert_no_warnings`](audit.md#testing-deprecated-code) catches and discards warnings of the specified type inside the block, while asserting that no such warning escapes.

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
  <summary>Output: <code>new_create_client("example.com")</code></summary>

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

## Class methods and static methods

`@deprecated` works with both `@classmethod` and `@staticmethod` in either decorator order — place `@deprecated` above or below the descriptor decorator and the deprecation warning fires correctly at call time either way.

```python
from deprecate import deprecated


class ApiClient:
    # @deprecated inside @classmethod — conventional order, @deprecated closer to def
    @classmethod
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    def from_url(cls, url: str) -> "ApiClient":
        return cls()

    # @deprecated outside @classmethod — also works; both produce the same descriptor
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    @classmethod
    def from_config(cls, config: dict) -> "ApiClient":
        return cls()

    # Same flexibility with @staticmethod
    @staticmethod
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    def version() -> str:
        return "1.0"

    @deprecated(deprecated_in="1.0", remove_in="2.0")
    @staticmethod
    def build_id() -> str:
        return "legacy"


print(ApiClient.build_id())
```

<details>
  <summary>Output: <code>ApiClient.build_id()</code></summary>

```
legacy
```

</details>

Both decorator orders produce `classmethod(deprecated_wrapper)` or `staticmethod(deprecated_wrapper)` respectively. The deprecation `FutureWarning` fires at call time regardless of which order the decorators were applied.

!!! tip "Prefer `@classmethod @deprecated` (deprecated closer to `def`)"

    The inner-first order is the conventional Python style — outer decorators apply last. Follow this pattern for consistency if your team has no existing convention.

## Properties and cached properties

`@deprecated` works with `@property` and `@cached_property`. The decorator only adds a `FutureWarning` at access time — it does **not** forward reads or writes to another property. For a getter-only property, either decorator order is valid. To add a warning to all three accessors (`fget`, `fset`, `fdel`) so that read, write, **and** delete each fire `FutureWarning`, place `@deprecated` on the **outside** (`@deprecated @property` order, or explicit `deprecated(...)(property(fget, fset, fdel))`). The inner-first order (`@property @deprecated`) only adds a warning to `fget` — apply `@deprecated` to setter and deleter separately if you also need them to warn.

```python
from functools import cached_property

from deprecate import deprecated


class Config:
    @property
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    def timeout(self) -> int:
        return 30

    @cached_property
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    def base_url(self) -> str:
        return "https://example.com"


print(Config().timeout)
```

<details>
  <summary>Output: <code>Config().timeout</code></summary>

```
30
```

</details>

The `FutureWarning` fires on **attribute access** (`obj.timeout`), not on a call. For `@cached_property`, the warning fires on **first access only** — subsequent accesses return the cached value without emitting another warning.

!!! tip "Decorator order for getter-only properties"

    Either `@property @deprecated` (inner) or `@deprecated @property` (outer) order works for getter-only properties. Inner order is conventional — the deprecated decorator is closer to the `def`. For properties with a setter or deleter, use outer order; see the next section.

### Deprecating a property with a setter or deleter

When the property being deprecated has a setter or deleter, all three accessors (`fget`, `fset`, `fdel`) are wrapped automatically — each fires a `FutureWarning`. Both the chain-style decorator pattern and the explicit construction pattern work:

```python
from deprecate import deprecated


class Config:
    def __init__(self) -> None:
        self._timeout: int = 30

    # Outer order required: @deprecated @property wraps fget, fset, and fdel
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    @property
    def timeout(self) -> int:
        return self._timeout

    @timeout.setter
    def timeout(self, value: int) -> None:
        self._timeout = value

    @timeout.deleter
    def timeout(self) -> None:
        del self._timeout


cfg = Config()
cfg.timeout = 10  # FutureWarning: write
print(cfg.timeout)  # FutureWarning: read; prints 10
del cfg.timeout  # FutureWarning: delete
```

<details>
  <summary>Output: <code>cfg.timeout</code></summary>

```
10
```

</details>

`obj.timeout` fires `FutureWarning` on **read**, `obj.timeout = value` fires on **write**, and `del obj.timeout` fires on **delete**.

!!! tip "Want only the getter to warn?"

    If you want the setter or deleter to remain silent, apply `@deprecated` directly to `fget` using inner order (`@property @deprecated`) instead of wrapping the full `property` object.

!!! tip "Testing each accessor independently"

    Each accessor (`fget`, `fset`, `fdel`) has its own warning counter — assert read, write, and delete warnings in separate `pytest.warns` blocks, or use `num_warns=-1` to disable per-accessor deduplication.

The explicit `property(fget, fset[, fdel])` construction also works:

```python
from deprecate import deprecated


def _timeout_fget(self) -> int:
    return self._timeout


def _timeout_fset(self, value: int) -> None:
    self._timeout = value


def _timeout_fdel(self) -> None:
    del self._timeout


class Config:
    def __init__(self) -> None:
        self._timeout: int = 30

    timeout = deprecated(deprecated_in="1.0", remove_in="2.0")(property(_timeout_fget, _timeout_fset, _timeout_fdel))
```

!!! note "Audit discoverability with explicit construction"

    `find_deprecation_wrappers` discovers explicit-construction properties via the accessor that carries `__deprecated__` metadata. For setter-only properties (`property(None, fset)`), it discovers via `fset`; if `fget` is plain (not deprecated), it falls through to `fset` or `fdel`.

### Deprecated property alias on a dataclass

When a dataclass field is renamed, define a property with the old name that delegates to the new field in its accessor body. `@deprecated` adds a `FutureWarning` to each accessor — the delegation itself is plain Python in the method body, not something the library provides.

**Read-only alias (warns on read only):**

```python
from dataclasses import dataclass

from deprecate import deprecated


@dataclass
class Config:
    timeout_ms: int = 30_000  # renamed from `timeout`

    @property
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    def timeout(self) -> int:
        """Deprecated — use ``timeout_ms`` instead."""
        return self.timeout_ms // 1000


cfg = Config(timeout_ms=5_000)
print(cfg.timeout)  # FutureWarning fired; prints 5
```

<details>
  <summary>Output: <code>cfg.timeout</code></summary>

```
5
```

</details>

**Read-write alias (warns on read and write):** use the outer order and chain `.setter`:

```python
from dataclasses import dataclass

from deprecate import deprecated


@dataclass
class Config:
    timeout_ms: int = 30_000  # renamed from `timeout`

    @deprecated(deprecated_in="1.0", remove_in="2.0")
    @property
    def timeout(self) -> int:
        return self.timeout_ms // 1000

    @timeout.setter
    def timeout(self, value: int) -> None:
        self.timeout_ms = value * 1000


cfg = Config(timeout_ms=5_000)
print(cfg.timeout)  # FutureWarning fired; prints 5
cfg.timeout = 10  # FutureWarning fired; sets timeout_ms = 10_000
print(cfg.timeout_ms)  # prints 10_000
```

<details>
  <summary>Output: <code>cfg.timeout; cfg.timeout_ms</code></summary>

```
5
10000
```

</details>

`cfg.timeout` fires `FutureWarning` (from `@deprecated`) and the getter body returns `cfg.timeout_ms // 1000`. `cfg.timeout = 5` fires `FutureWarning` and the setter body assigns `cfg.timeout_ms = 5000`.

!!! warning "Do not shadow a dataclass field"

    Do **not** use the same name as an existing dataclass field for the deprecated property. The `@dataclass`-generated `__init__` performs `self.field = value`, which conflicts with a property descriptor of the same name. Use a different name for the deprecated alias and keep the dataclass field under its new name.

The same pattern works on regular (non-dataclass) classes — replace field access with `self._attr` lookups in the accessor body. `@deprecated` only adds the warning in either case.

!!! note "`target=<callable>` not supported on properties"

    `@deprecated` rejects `target=<callable>` on a `property` with `TypeError`. Properties have three independent accessors (`fget`, `fset`, `fdel`); there is no single callable to forward to. Delegate in each accessor body as shown above.

## Deprecating generator functions

Generator functions — any function that contains `yield` — are fully supported by `@deprecated`. The decorator wraps them using an eager factory pattern: the deprecation warning fires when you **call** the generator function, not when you first iterate the result.

This is the right behavior. It keeps generator deprecations consistent with regular function deprecations. If the warning fired on the first `next()` call instead, you could easily miss it: someone might call the generator, pass it around, and iterate it elsewhere — the warning would appear far from the actual deprecated call site.

```python
from deprecate import deprecated, void


# NEW/FUTURE API — new name, same semantics
def generate_ids(start: int, count: int):
    """Yield `count` sequential IDs starting from `start`."""
    for i in range(count):
        yield start + i


# DEPRECATED API — `iter_ids` was the old name before the rename
@deprecated(target=generate_ids, deprecated_in="0.9", remove_in="1.0")
def iter_ids(start: int, count: int):
    """Deprecated — use generate_ids() instead."""
    return void(start, count)


# The warning fires here — at call time, before any iteration
gen = iter_ids(10, 3)
# FutureWarning: The `iter_ids` was deprecated since v0.9 in favor of `generate_ids`.
#                It will be removed in v1.0.

# Iteration proceeds normally — you already got the warning
print(list(gen))  # [10, 11, 12]
```

All three TargetModes work with generator functions:

**`TargetMode.NOTIFY` — warn and keep the generator body:**

```python
from deprecate import deprecated


@deprecated(deprecated_in="0.9", remove_in="1.0")
def old_pipeline(items):
    """This generator is going away; no replacement yet."""
    for item in items:
        yield item.strip()


print(list(old_pipeline(["a ", "b "])))
```

<details>
  <summary>Output: <code>list(old_pipeline(["a ", "b "]))</code></summary>

```
['a', 'b']
```

</details>

Warning fires at call time (when the generator object is created), before any iteration. Unlike regular functions where the body runs immediately after the warning, the generator body executes lazily as the caller iterates.

**`TargetMode.ARGS_REMAP` — rename an argument within the same generator:**

```python
from deprecate import TargetMode, deprecated


@deprecated(
    target=TargetMode.ARGS_REMAP,
    args_mapping={"n": "count"},
    deprecated_in="0.9",
    remove_in="1.0",
)
def repeat_value(value: int, n: int = 0, count: int = 0):
    """Deprecated argument `n` renamed to `count`."""
    for _ in range(count):
        yield value


print(list(repeat_value(value=1, count=2)))
```

<details>
  <summary>Output: <code>list(repeat_value(value=1, count=2))</code></summary>

```
[1, 1]
```

</details>

**`target=<callable>` — forward to a replacement generator:**

```python
from deprecate import deprecated, void


def new_range(start: int, stop: int):
    yield from range(start, stop)


@deprecated(target=new_range, deprecated_in="0.9", remove_in="1.0")
def old_range(start: int, stop: int):
    return void(start, stop)


print(list(old_range(1, 4)))
```

<details>
  <summary>Output: <code>list(old_range(1, 4))</code></summary>

```
[1, 2, 3]
```

</details>

!!! note "Warning deduplication and the generator factory pattern"

    Internally, the deprecated wrapper for a generator is a regular (non-generator) function that fires the warning eagerly and then returns the actual generator object. In the current implementation, `_WrapperState.called` is incremented once per external call via the wrapper's normal dispatch path. Warning deduplication still works correctly: warnings fire at most `num_warns` times as configured.

## Async

`@deprecated` works on `async def` functions natively. The wrapper produced is itself `async def`, so `inspect.iscoroutinefunction(wrapper)` returns `True` and callers can `await` it as expected.

All three TargetModes work with async functions. The deprecation warning fires when the coroutine is awaited — not when it is created by calling the wrapper — because the warning logic runs inside the `async def` body. This differs from sync and generator wrappers where the warning fires eagerly at call time.

**`TargetMode.NOTIFY` — warn and keep the async body:**

```python
import asyncio
from deprecate import deprecated


@deprecated(deprecated_in="0.9", remove_in="1.0")
async def fetch_data(url: str) -> bytes:
    """Deprecated — no replacement yet; remove call sites."""
    return b""


print(asyncio.run(fetch_data("https://example.com")))
```

<details>
  <summary>Output: <code>asyncio.run(fetch_data("https://example.com"))</code></summary>

```
b''
```

</details>

**`TargetMode.ARGS_REMAP` — rename an argument within the same async function:**

```python
import asyncio
from deprecate import TargetMode, deprecated


@deprecated(
    target=TargetMode.ARGS_REMAP,
    args_mapping={"endpoint": "url"},
    deprecated_in="0.9",
    remove_in="1.0",
)
async def fetch_data(endpoint: str = "", url: str = "") -> bytes:
    """Deprecated argument `endpoint` renamed to `url`."""
    return url.encode()


print(asyncio.run(fetch_data(endpoint="https://example.com")))
```

<details>
  <summary>Output: <code>asyncio.run(fetch_data(endpoint="https://example.com"))</code></summary>

```
b'https://example.com'
```

</details>

**`target=<callable>` — forward to a replacement async function:**

```python
import asyncio
from deprecate import deprecated, void


async def download(url: str) -> bytes:
    """New async API."""
    return url.encode()


@deprecated(target=download, deprecated_in="0.9", remove_in="1.0")
async def fetch(url: str) -> bytes:
    """Deprecated — use download() instead."""
    return void(url)


print(asyncio.run(fetch("https://example.com")))
```

<details>
  <summary>Output: <code>asyncio.run(fetch("https://example.com"))</code></summary>

```
b'https://example.com'
```

</details>

!!! warning "Concurrent coroutines and warning counts"

    `_WrapperState` fields (`called`, `warned_calls`, `warned_args`) are plain dataclass fields — there is no asyncio lock protecting them. If multiple coroutines share one deprecated wrapper and run concurrently, they can race on the warning counter: the same wrapper may emit more or fewer warnings than `num_warns` specifies, depending on scheduling.

    This is an accepted limitation for v0.9. If exact warning counts matter (for example in tests), either run deprecated coroutines sequentially or set `num_warns=-1` to bypass the gate entirely.

## Async generators

`@deprecated` works on async generator functions (`async def` + `yield`) too. The wrapper is a **sync** callable that fires the deprecation warning eagerly at call time and returns the underlying async generator object; callers iterate the result with `async for`. All three TargetModes — `NOTIFY`, `ARGS_REMAP`, and `target=<callable>` — work the same way they do for sync generators.

**`TargetMode.NOTIFY` — warn and keep the async generator body:**

```python
import asyncio
from collections.abc import AsyncIterator

from deprecate import deprecated


@deprecated(deprecated_in="0.9", remove_in="1.0")
async def stream_lines(start: int = 0) -> AsyncIterator[int]:
    """Deprecated — no replacement yet; remove call sites."""
    for i in range(start, start + 3):
        yield i


async def main() -> list[int]:
    return [item async for item in stream_lines(start=1)]


asyncio.run(main())
```

**`TargetMode.ARGS_REMAP` — rename an argument within the same async generator:**

```python
import asyncio
from collections.abc import AsyncIterator

from deprecate import TargetMode, deprecated


@deprecated(
    target=TargetMode.ARGS_REMAP,
    args_mapping={"begin": "start"},
    deprecated_in="0.9",
    remove_in="1.0",
)
async def stream_lines(begin: int = 0, start: int = 0) -> AsyncIterator[int]:
    """Deprecated argument `begin` renamed to `start`."""
    for i in range(start, start + 3):
        yield i


async def main() -> list[int]:
    return [item async for item in stream_lines(begin=1)]


asyncio.run(main())
```

**`target=<callable>` — forward to a replacement async generator:**

```python
import asyncio
from collections.abc import AsyncIterator

from deprecate import deprecated


async def stream(start: int) -> AsyncIterator[int]:
    """New async generator API."""
    for i in range(start, start + 3):
        yield i


@deprecated(target=stream, deprecated_in="0.9", remove_in="1.0")
async def stream_legacy(start: int) -> AsyncIterator[int]:
    """Deprecated — use stream() instead."""
    if False:  # pragma: no cover — body unreachable; target forwards every call
        yield 0


async def main() -> list[int]:
    return [item async for item in stream_legacy(start=1)]


asyncio.run(main())
```

!!! note "The wrapper itself is sync, not an async generator"

    Calling `wrapper(...)` returns the async generator object directly — no `await` is required at call time, and the deprecation warning fires once at that point. Because the wrapper is implemented as a regular function (it never enters an `async def` body), `inspect.iscoroutinefunction(wrapper)` and `inspect.isasyncgenfunction(wrapper)` both return `False`. Frameworks that branch on those introspections (rare in practice — `async for` does not consult them) may need a hand-written passthrough async generator placed between `@deprecated` and the framework.

## See also

- [Customization](customization.md) — redirect deprecation output to a logger or use a custom message template
- [void() Helper](void-helper.md) — when and why the deprecated function body should call `void()`
- [Audit Tools](audit.md) — enforce removal deadlines and detect deprecation chains in CI
- [Troubleshooting](../troubleshooting.md) — common errors and fixes for `@deprecated` configuration

______________________________________________________________________

Next: [void() Helper](void-helper.md) — understanding the no-op body helper, or [Audit Tools](audit.md) for CI enforcement utilities.
