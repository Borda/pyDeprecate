---
id: functions
description: Deprecating Python functions and methods — simple forwarding, argument renaming, notice-only, same-function argument renaming, stacked decorators, and conditional skip.
---

# Functions

This page covers all deprecation patterns for Python functions and methods: forwarding to a replacement, renaming arguments, emitting a notice with no replacement, remapping arguments within the same function, stacking multiple decorators, and conditional suppression. For class deprecation see [Classes](classes.md); for async functions see [Async](async.md).

!!! note "`@deprecated` vs. `deprecated_callable`"

    `@deprecated` is the friendly front door: applied to a function, method, or other callable/descriptor it behaves exactly as documented on this page. Applied to a class instead, it dispatches to [`deprecated_class`](classes.md) (with a one-time informational notice) rather than raising an error. If you want a strict, callable-only form that rejects a class up front at decoration time — for example at a call site meant to enforce "only functions here" — use `deprecated_callable()`: it shares every parameter with `@deprecated` and raises `TypeError` immediately when applied to a class.

## Simple function forwarding

!!! danger "Body is dead code when `target=<callable>`"

    When `target` is set to a callable, pyDeprecate intercepts every call **before** the function body runs — the body is dead code under normal forwarding. **Exception**: if you also use `skip_if` and it evaluates `True` at call time, the source body executes as a fallback. In that case keep a working body; otherwise `skip_if=True` calls silently return `None`.

    Do **not** call the target from inside the body:

    ```python
    from deprecate import deprecated, void


    def score_predictions(val: int) -> int:
        return val * 2


    # WRONG — score_predictions(val) is never reached; the decorator forwards before the body runs
    @deprecated(target=score_predictions, deprecated_in="1.0", remove_in="2.0")
    def score(val: int) -> int:
        return score_predictions(val)


    # CORRECT — body is empty; pyDeprecate handles all forwarding automatically
    @deprecated(target=score_predictions, deprecated_in="1.0", remove_in="2.0")
    def score(val: int) -> int:
        return void(val)  # or: pass  or: """Original function description."""
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
    # lifecycle metadata shown in notices and audit output
    deprecated_in="0.6",
    remove_in="1.0",
    # custom message template
    message_template="`%(source_name)s` was deprecated, use `%(target_path)s`",
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

!!! warning "Passing both old and new argument names at once"

    If a caller supplies both the deprecated name and its replacement in the same call (e.g. `fn(val=5, new_val=6)`), the explicit new-name value always wins. The remapped old-name value is silently discarded. To make this visible, pyDeprecate also emits a `UserWarning` alongside the regular `FutureWarning`:

    ```
    UserWarning: Both `val` (deprecated) and `new_val` were supplied to `fn()`; `val` is ignored.
    ```

    Clean up the call site by removing the deprecated argument name.

## Notice-only deprecation

!!! warning "The function body still executes with `TargetMode.NOTIFY` — keep a working implementation"

    Unlike `target=<callable>` (where the body is **dead code** under normal forwarding, but still executes as a fallback when `skip_if=True` at call time), `TargetMode.NOTIFY` runs the original function body after emitting the deprecation notice. You **must** keep a working implementation in the function body. An empty body (`pass`) will cause the function to return `None` instead of the intended value (the deprecation warning still fires).

Use warn-only mode when a function is going away but has no replacement yet. The decorator emits a deprecation notice and then runs the function body normally. This is the right choice when callers need to update their own code, not switch to a different function.

Prefer spelling out `target=TargetMode.NOTIFY` — it makes the warn-only intent explicit at the call site. If you omit `target`, the `TargetMode.AUTO` default resolves to `TargetMode.NOTIFY` when no `args_mapping` is given, so this shorter form is equivalent:

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

## Rename arguments within one function

Use `TargetMode.ARGS_REMAP` to rename or drop an argument within the same function. The decorator remaps the old argument name to the new one before the body runs, so your implementation only needs the new name. This is the right pattern when refactoring a signature without moving the function. Pass `target=TargetMode.ARGS_REMAP` explicitly alongside `args_mapping` — the recommended, self-documenting form. (On `@deprecated`, omitting `target` also works: a bare `args_mapping` auto-resolves via the `TargetMode.AUTO` default; the strict `deprecated_callable()` form always requires the explicit `target`.)

```python
from deprecate import TargetMode, deprecated


@deprecated(
    # rename an argument within the same function
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

    On `@deprecated`, `target` defaults to `TargetMode.AUTO` — a fallback that resolves an *omitted* `target` at decoration time (a non-empty `args_mapping` → `ARGS_REMAP`, otherwise `NOTIFY`; an empty `{}` counts as no mapping). The resolved mode — never `AUTO` itself — is stored in `DeprecationConfig`. Prefer passing the explicit mode so intent is visible at the call site. The front door accepts `target=TargetMode.AUTO` written out — it is identical to omitting `target` — but it conveys nothing extra; the strict `deprecated_callable()` (default `TargetMode.NOTIFY`) raises `TypeError` if you try.

### Behaviour comparison

|                                   | `TargetMode.NOTIFY`                                                                                       | `TargetMode.ARGS_REMAP` (with `args_mapping`)                              | `target=<callable>`                                                                                                                                                                            |
| --------------------------------- | --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Warning emitted**               | Yes — up to `num_warns` times (default: once)                                                             | Per deprecated arg, up to `num_warns` times (default: once)                | Yes — up to `num_warns` times (default: once)                                                                                                                                                  |
| **Warning template**              | `"… was deprecated since vX. It will be removed in vY."`                                                  | `"… uses deprecated arguments: …"`                                         | `"… was deprecated … in favour of …"`                                                                                                                                                          |
| **`message_template` specifiers** | `source_name`, `source_path`, `deprecated_in`, `remove_in` only — `target_name`/`target_path` unavailable | `source_name`, `source_path`, `argument_map`, `deprecated_in`, `remove_in` | All specifiers incl. `target_name`, `target_path`                                                                                                                                              |
| **Function body**                 | Runs with caller's args + source defaults filled in                                                       | Runs after argument renaming/dropping                                      | **Does not run** under normal forwarding — body is dead code, calls intercepted first. **Exception**: `skip_if=True` at call time bypasses forwarding and executes the source body as fallback |
| **`args_mapping` applied**        | `⚠`                                                                                                       | `✓` renames or drops listed args                                           | `✓` renames or drops args before forwarding                                                                                                                                                    |
| **`args_extra` injected**         | `⚠`                                                                                                       | `✓` merged into kwargs before call                                         | `✓` merged into kwargs before forwarding                                                                                                                                                       |
| **Source defaults merged**        | `✓`                                                                                                       | `✗`                                                                        | `✓`                                                                                                                                                                                            |
| **`skip_if` effect**              | `⊛`                                                                                                       | `⊛`                                                                        | `⊛`                                                                                                                                                                                            |
| **`stream=None` effect**          | `⊘` body still runs                                                                                       | `⊘` remapping still runs                                                   | `⊘` forwarding still runs                                                                                                                                                                      |

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
def going_away(val: int) -> int:
    return val


# TargetMode.ARGS_REMAP: warns only when the old argument name is passed
@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"val": "new_val"}, deprecated_in="1.0", remove_in="2.0")
def renamed_arg(val: int = 0, new_val: int = 0) -> int:
    return new_val


going_away(1)  # warns: FutureWarning — "going_away was deprecated since v1.0 …"
renamed_arg(new_val=1)  # silent — new name used, no deprecated arg present
renamed_arg(val=1)  # warns: FutureWarning — "renamed_arg uses deprecated arguments: `val` → `new_val` …"
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
    message_template="Depr: v%(deprecated_in)s rm v%(remove_in)s for args: %(argument_map)s.",
)
@deprecated(
    TargetMode.ARGS_REMAP,
    deprecated_in="0.4",
    remove_in="0.7",
    args_mapping=dict(nc1="nc2"),
    message_template="Depr: v%(deprecated_in)s rm v%(remove_in)s for args: %(argument_map)s.",
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

`skip_if` accepts a boolean or a zero-argument callable returning a boolean. When it evaluates to `True`, the deprecation notice is suppressed and the call proceeds normally. This is useful when behaviour depends on runtime conditions, for example suppressing the notice once the caller has migrated to a newer dependency. `skip_if` is available on `@deprecated` and `deprecated_callable()` alike (and on the class/instance proxies — see [Classes](classes.md)):

```python
from deprecate import TargetMode, deprecated_callable

FAKE_VERSION = 1


def version_greater_1():
    return FAKE_VERSION > 1


@deprecated_callable(TargetMode.ARGS_REMAP, "0.3", "0.6", args_mapping=dict(c1="nc1"), skip_if=version_greater_1)
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

## Fatal deprecations

`as_exception=True` raises `DeprecatedError` instead of emitting the warning, carrying the same rendered message. The call is refused rather than flagged: the forwarding target is never invoked and the source body never runs, so `TargetMode.NOTIFY` and `TargetMode.ARGS_REMAP` stop instead of falling through to the body. This is the middle step of the warn → raise → delete lifecycle, and it is available on every entry point (`@deprecated`, `deprecated_callable()`, `deprecated_class()`, `deprecated_instance()`, `deprecated_module()`).

Two rules differ from the warning path on purpose:

- **`num_warns` does not apply.** A fatal deprecation raises on *every* call. A budget would leave a gate that stops refusing after the first attempt — a caller retrying inside `except` would sail through.
- **`stream=None` silences the message, not the raise.** Fatal mode is a lifecycle state, not an output channel.

`skip_if` still suppresses everything, fatal deprecations included — a wrapper switched off by configuration is not a deprecation at that moment.

```python
from deprecate import DeprecatedError, TargetMode, deprecated_callable


@deprecated_callable(TargetMode.NOTIFY, "1.0", "2.0", as_exception=True)
def legacy_checksum(payload: str) -> int:
    return len(payload)


try:
    legacy_checksum("abc")
except DeprecatedError as err:
    print(err)
```

<details>
  <summary>Output: <code>legacy_checksum("abc")</code></summary>

```
The `legacy_checksum` was deprecated since v1.0. It will be removed in v2.0.
```

</details>

### The global switch

`deprecate.AS_EXCEPTIONS` is the process-wide default, seeded at import from the `DEPRECATE_AS_EXCEPTIONS` environment variable (`1`, `true`, `yes`, or `on`; anything else is off). It is re-read on every emission, so an application can flip it during bootstrap and a test suite can toggle it per case:

```bash
DEPRECATE_AS_EXCEPTIONS=1 pytest
```

Precedence is **monotonic**: an explicit `as_exception=True` is always fatal, and `as_exception=False` means "not fatal by default" but still yields to a `True` global. A consumer's strict run therefore cannot be opted out of by the library that declared the deprecation, while an author can still promote one symbol ahead of the rest.

### When to prefer `-W error` instead

Python already turns warnings into exceptions: `-W error::FutureWarning` or `PYTHONWARNINGS=error::FutureWarning`. Use those when you want *every* library's `FutureWarning` to raise. `as_exception` exists for the three cases they cannot cover:

1. **Scope** — fail only on pyDeprecate's own deprecations, not on NumPy's or pandas'.
2. **Non-warnings streams** — a wrapper with `stream=logging.warning` never reaches the warnings machinery, so no filter can make it fail.
3. **Per-symbol promotion** — ship one deprecation as fatal while the rest keep warning.

## Static type checkers (PEP 702)

mypy, pyright, and IDEs flag a deprecated symbol only when its decorator is literally `warnings.deprecated` (Python 3.13+) or its backport `typing_extensions.deprecated` — they recognise the decorator by name, not by what it does at runtime. `@deprecated` from pyDeprecate is invisible to them. To get the strikethrough in the editor *and* pyDeprecate's forwarding and warning, stack the PEP 702 decorator directly above pyDeprecate's:

```python
import typing_extensions  # on Python 3.13+: `import warnings` and `@warnings.deprecated(...)`

from deprecate import deprecated


# NEW API — the renamed helper
def fetch_orders(customer_id: int) -> list:
    return [customer_id]


# DEPRECATED API — struck through by type checkers, forwarded with a warning at runtime
@typing_extensions.deprecated("Use `fetch_orders` instead.", category=None)
@deprecated(target=fetch_orders, deprecated_in="1.4", remove_in="2.0")
def get_orders(customer_id: int) -> list: ...


print(get_orders(7))  # warns: FutureWarning
```

<details>
  <summary>Output: <code>get_orders(7)</code></summary>

```
[7]
```

</details>

Four rules make the pair work:

- **`category=None`.** The PEP 702 decorator then only marks the symbol for type checkers; without it, every call emits a second `DeprecationWarning` next to pyDeprecate's `FutureWarning`.
- **A string-literal message.** mypy silently ignores a message held in a constant or built with an f-string; pyright still flags the symbol but drops the text.
- **Directly above `@deprecated(...)`.** Inside `@staticmethod` / `@classmethod`, and above the outer-order `@deprecated(...) @property` (mypy asks for `# type: ignore[prop-decorator]` on that line, as it does for any decorator above `@property`). Placed *under* `@property`, pyright misses it.
- **Import the module, not the name.** `from typing_extensions import deprecated` shadows pyDeprecate's `deprecated`; write `@typing_extensions.deprecated(...)` or `@warnings.deprecated(...)`.

IDEs strike the symbol through as soon as the decorator is there. To fail CI on new uses, turn the diagnostic into an error — mypy leaves it off by default:

```toml
[tool.mypy]
enable_error_code = ["deprecated"]

[tool.pyright]
reportDeprecated = "error"
```

Since `v0.14`, `@deprecated` and `deprecated_callable()` keep the decorated callable's own type and the package ships a `py.typed` marker, so type checkers see the real signature and name the symbol correctly in the diagnostic. For class aliases see [Classes → Type annotations and static analysis](classes.md#type-annotations-and-static-analysis); to list symbols deprecated with the PEP 702 decorator alone, see [`include_pep702`](audit.md#pep-702-only-deprecations).

That static type is the *source's* type, and a callable object is not preserved at runtime: decorating a `functools.lru_cache` / `functools.cache` object returns a plain function, so `cache_clear()` and `cache_info()` are gone even though type checkers still offer them. Deprecate the underlying function and put the cache on top (`@functools.lru_cache` above `@deprecated(...)`) — the cache attributes then exist, but a cache hit never reaches the wrapper, so the warning fires only on a miss. Callable objects without a `__name__`, such as `functools.partial`, are rejected at decoration time; wrap those with `deprecated_instance()`.

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
