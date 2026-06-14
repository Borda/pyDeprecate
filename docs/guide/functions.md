---
id: functions
description: Deprecating Python functions and methods — simple forwarding, argument renaming, notice-only, self argument remapping, stacked decorators, and conditional skip.
---

# Functions

This page covers all deprecation patterns for Python functions and methods: forwarding to a replacement, renaming arguments, emitting a notice with no replacement, remapping arguments within the same function, stacking multiple decorators, and conditional suppression. For class deprecation see [Classes](classes.md); for async functions see [Async](async.md).

## Simple function forwarding

!!! danger "Body is dead code when `target=<callable>`"

    When `target` is set to a callable, pyDeprecate intercepts every call **before** the function body runs — the body is dead code under normal forwarding. **Exception**: if you also use `skip_if` and it evaluates `True` at call time, the source body executes as a fallback. In that case keep a working body; otherwise `skip_if=True` calls silently return `None`.

    Do **not** call the target from inside the body:

    ```python
    from deprecate import deprecated, void


    def score_predictions(x: int) -> int:
        return x * 2


    # WRONG — score_predictions(x) is never reached; the decorator forwards before the body runs
    @deprecated(target=score_predictions, deprecated_in="1.0", remove_in="2.0")
    def score(x: int) -> int:
        return score_predictions(x)


    # CORRECT — body is empty; pyDeprecate handles all forwarding automatically
    @deprecated(target=score_predictions, deprecated_in="1.0", remove_in="2.0")
    def score(x: int) -> int:
        return void(x)  # or: pass  or: """Original function description."""
    ```

Apply `@deprecated(target=<callable>)` to the old name and pyDeprecate forwards every call (positional and keyword arguments included) to the new function. Under normal forwarding the body is dead code, so leave it empty or put a docstring there (see also [void() helper](void-helper.md) for a null-forwarding idiom). The one exception is `skip_if=True` at call time — see the danger admonition above — where the source body executes as a fallback; keep a working body when combining `target=<callable>` with `skip_if`.

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
    args_mapping={"num_workers": None},
    deprecated_in="1.8",
    remove_in="1.9",
)
def my_func(value: int, num_workers: Optional[int] = None) -> int:
    """num_workers is no longer used; omit it (auto-detected)."""
    return value * 2


# Passing the removed argument triggers a warning and the argument is silently discarded:
#   The `my_func` uses deprecated arguments: `num_workers` -> `None`.
#   They were deprecated since v1.8 and will be removed in v1.9.
print(my_func(value=42, num_workers=4))
```

<details>
  <summary>Output: <code>my_func(value=42, num_workers=4)</code></summary>

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

## See also

- [Use Cases overview](use-cases.md) — start here for a guided tour of all deprecation patterns
- [Classes](classes.md) — class, Enum, dataclass, and instance deprecation
- [Properties](properties.md) — `@property` and `@cached_property` deprecation
- [Async](async.md) — async functions and async generators
- [Advanced](advanced.md) — docstring updates, `args_extra`, testing helpers, class/static methods, generators
- [Customization](customization.md) — custom message templates and output streams
- [void() Helper](void-helper.md) — when and why to use `void()` in the function body
- [Audit Tools](audit.md) — enforce removal deadlines and detect deprecation chains in CI
- [Troubleshooting](../troubleshooting.md) — common errors and fixes

______________________________________________________________________

Next: [Classes](classes.md) — deprecating classes, Enums, and dataclasses.
