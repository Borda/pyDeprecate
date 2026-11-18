---
id: troubleshooting
description: 'Fix common pyDeprecate errors involving imports, warnings, argument mapping, classes, proxies, async code, modules, typing, pickling, audits, CI, and fatal mode.'
---

# Troubleshooting

Find direct answers and corrected code for common pyDeprecate problems. If your issue is not listed, see [Still stuck?](#still-stuck).

- [Installation and imports](#installation-and-imports)
- [Warnings and output](#warnings-and-output)
- [Functions and argument mapping](#functions-and-argument-mapping)
- [Classes, enums and dataclasses](#classes-enums-and-dataclasses)
- [Instances and proxies](#instances-and-proxies)
- [Properties](#properties)
- [Async and generators](#async-and-generators)
- [Modules](#modules)
- [Typing, IDEs and Sphinx](#typing-ides-and-sphinx)
- [Pickling and copying](#pickling-and-copying)
- [Audit, CLI and GitHub Action](#audit-cli-and-github-action)
- [Fatal mode](#fatal-mode)

## Installation and imports

### ModuleNotFoundError: No module named 'pydeprecate'

The install name and the import name are different. The package installs as `pyDeprecate` but imports as `deprecate`.

**Q:** I installed the package with `pip install pyDeprecate` but get `ModuleNotFoundError: No module named 'pydeprecate'` when I try to import it.

Use the import name `deprecate`:

```python
from deprecate import deprecated

print(deprecated.__name__)
```

<details>
  <summary>Output: <code>deprecated.__name__</code></summary>

```
deprecated
```

</details>

Do not use the install name as the import name:

```python
# phmdoctest:skip — intentional wrong import; would raise ModuleNotFoundError
from pydeprecate import deprecated  # wrong — no such module
```

## Warnings and output

### Deprecation notice not appearing

By default, pyDeprecate emits the deprecation message once per function (`num_warns=1`) to avoid log spam. Subsequent calls are silent. Set `num_warns=-1` for unlimited emissions or `num_warns=N` for exactly `N` emissions.

**Q:** I call my deprecated function but no deprecation notice is printed. Where did it go?

With `args_mapping` and `TargetMode.ARGS_REMAP`, each deprecated argument has an independent message counter. Each argument therefore warns once by default.

A call using several deprecated arguments names only those with unspent counters. Previously reported arguments are omitted from later messages.

```python
# Minimal replacement function for examples
# NEW API — score_predictions is the replacement API.
def score_predictions(val: int) -> int:
    return val * 2


# ---------------------------

from deprecate import deprecated


# Show warning every time
# DEPRECATED API — predict forwards to score_predictions.
@deprecated(target=score_predictions, deprecated_in="1.0", remove_in="2.0", num_warns=-1)
def predict():
    pass


# Show warning N times total
# DEPRECATED API — predict_batch forwards to score_predictions.
@deprecated(target=score_predictions, deprecated_in="1.0", remove_in="2.0", num_warns=5)
def predict_batch():
    pass


print(callable(predict), callable(predict_batch))
```

<details>
  <summary>Output: <code>callable(predict), callable(predict_batch)</code></summary>

```
True True
```

</details>

If you are writing tests and need to verify that a warning fires, use `pytest.warns(FutureWarning)` on the first call and `assert_no_warnings(FutureWarning)` on subsequent calls. See [Testing Deprecated Code](guide/testing.md#testing-deprecated-code) for full examples.

### Why did the deprecation warning stop appearing in my long-running service?

No. The `num_warns` budget lasts for the process's lifetime. A long-running worker or web server can exhaust the default `num_warns=1` at startup.

**Q:** My worker logged the deprecation warning once after a deploy and has been silent for weeks, although the deprecated path is surely still called. Did the warning break?

The path then stays silent for the rest of that process's life, even after weeks of traffic. Missing production logs do not prove that the deprecated path is unused. The warning may already have scrolled out of the retained log window.

To check whether the path is still used, choose one option:

- Restart the process to reset the counter, then watch for the warning.
- Temporarily redeploy with `num_warns=-1` to invoke the configured warning path on every call.

!!! note "Delivery and display differ"

    Standard warning filters can still suppress repeated display. A custom stream can record each delivery.

### How do I redirect deprecation output to a logger instead of `warnings.warn`?

Pass a logging method as `stream`. It receives the formatted deprecation message as a single string argument, matching the signature of methods such as `logging.warning`.

**Q:** I want deprecation messages to go through Python's `logging` module instead of the default `warnings.warn` mechanism. How?

See [Deprecation Output Sink](guide/customization.md#deprecation-output-sink-stream) for silencing, custom callables, `print`, and other `stream` options.

```python
import logging
from deprecate import deprecated

# Configure logging (typically done once at application startup)
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# NEW API — new_endpoint is the replacement API.
def new_endpoint(url: str) -> str:
    return f"GET {url}"


# DEPRECATED API — old_endpoint forwards to new_endpoint.
@deprecated(
    target=new_endpoint,
    deprecated_in="2.0",
    remove_in="3.0",
    stream=logging.warning,
)
def old_endpoint(url: str) -> str:
    pass


# Instead of a FutureWarning, emits a log line:
#   2026-04-20 12:00:00 [WARNING] The `old_endpoint` was deprecated since v2.0
#   in favor of `your_module.new_endpoint`. It will be removed in v3.0.
print(old_endpoint("/api/users"))
```

<details>
  <summary>Output: <code>old_endpoint("/api/users")</code></summary>

```
GET /api/users
```

</details>

**Choosing the log level:**

| Level             | When to use                                   |
| ----------------- | --------------------------------------------- |
| `logging.info`    | Early deprecation window; low urgency         |
| `logging.warning` | Standard choice; default log configs show it  |
| `logging.error`   | Critical deprecation nearing removal deadline |

**Benefits over `warnings.warn`:**

- Logging integrates with existing log aggregation, such as ELK, Datadog, or CloudWatch.
- Logging respects your log format, handlers, and filters.
- Messages are always emitted regardless of Python's warning filter state, with no `-W` flag interaction.
- Timestamps appear automatically if your formatter adds them.

!!! note "Logging still uses the warning budget"

    With `stream=logging.warning`, `num_warns` still controls how many times the message is emitted. Combine `num_warns=-1` with `stream=logging.warning` to log every deprecated call site. Use log analytics to measure migration progress.

### Why does my deprecation warning point inside pyDeprecate or `functools.py`?

pyDeprecate measures the location when the warning fires and reports the first frame outside the `deprecate` package. Custom streams and `cached_property` can still point to another frame.

**Q:** A deprecation `FutureWarning` or pyDeprecate `UserWarning` reports a location inside `deprecate` or `functools.py` instead of my line. My module-scoped warning filter or `-W error` traceback therefore does not match my code.

This location rule covers call-time notices, decoration-time configuration warnings, and audit diagnostics, including:

- Stacked `@deprecated` layers use this rule.
- The `@deprecated` class front door uses this rule.
- Legacy `target=None/True/False` sentinels use this rule.

Two cases can report a different frame:

- **A custom `stream` written in Python.** pyDeprecate counts `stacklevel` from the frame calling the stream. A Python function forwarding to `warnings.warn` adds a frame, placing the warning one frame short, inside pyDeprecate. Use `warnings.warn`, its `functools.partial`, or a bound logger method such as `logging.getLogger().warning`. Alternatively, add `1` to the received `stacklevel` before forwarding it.

!!! warning "Module-level logging differs by Python version"

    `logging.warning` gives the correct location only on Python 3.11 and newer. On 3.9 and 3.10, it adds a logging frame, so the record points one frame short.

- **A `@cached_property` source.** The getter runs inside `functools.cached_property.__get__`, which is the first frame outside pyDeprecate, so the warning reports `functools.py`. Filter on the warning category or message instead of the module.

## Functions and argument mapping

### TypeError: Failed mapping

Your deprecated function passes an argument that the target function does not accept. You need to either drop the argument, rename it to match the target's signature, or use `TargetMode.ARGS_REMAP` for in-place remapping.

**Q:** I get `` TypeError: Failed mapping of `my_func`, arguments not accepted by target: ['old_arg'] ``. What does this mean?

pyDeprecate prepares the forwarded call from the deprecated source and validates its arguments against the target's signature. Rejected mapped names raise `` TypeError: Failed mapping of `{source}`, arguments not accepted by target: [...] ``.

!!! note "Error timing and message variants"

    Plain functions raise at call time. Generators and async generators raise on the first `next()` or `async for` step because calling their wrapper only creates the generator.

    A target accepting `*args` produces a slightly different message. It still means the target cannot accept the mapped arguments.

Choose the fix that matches your situation:

**Option 1:** Drop the argument if the target no longer needs it.

```python
# define a target that ignores the extra arg
# NEW API — new_func is the replacement API.
def new_func(new_arg: int) -> int:
    return new_arg * 2


# ---------------------------

from deprecate import deprecated


# None means skip this argument
# DEPRECATED API — old_func forwards to new_func.
@deprecated(target=new_func, args_mapping={"old_arg": None}, deprecated_in="1.0", remove_in="2.0")
def old_func(old_arg: int, new_arg: int) -> int:
    pass


print(isinstance(old_func(old_arg=1, new_arg=2), int))
```

<details>
  <summary>Output: <code>isinstance(old_func(old_arg=1, new_arg=2), int)</code></summary>

```
True
```

</details>

**Option 2:** Rename the argument if the target uses a different parameter name.

```python
# NEW API — new_func is the replacement API.
def new_func(new_name: int) -> int:
    return new_name * 2


# ---------------------------

from deprecate import deprecated


# Map old to new
# DEPRECATED API — old_func forwards to new_func.
@deprecated(target=new_func, args_mapping={"old_name": "new_name"}, deprecated_in="1.0", remove_in="2.0")
def old_func(old_name: int) -> int:
    pass


print(isinstance(old_func(old_name=3), int))
```

<details>
  <summary>Output: <code>isinstance(old_func(old_name=3), int)</code></summary>

```
True
```

</details>

**Option 3:** Use `TargetMode.ARGS_REMAP` to deprecate an argument within the same function, without forwarding to another function.

```python
from deprecate import TargetMode, deprecated


# Deprecate within same function
@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_arg": "new_arg"}, deprecated_in="1.0", remove_in="2.0")
def my_func(old_arg: int = 0, new_arg: int = 0) -> int:
    return new_arg * 2


print(isinstance(my_func(old_arg=1, new_arg=2), int))
```

<details>
  <summary>Output: <code>isinstance(my_func(old_arg=1, new_arg=2), int)</code></summary>

```
True
```

</details>

### TypeError: skip_if function must return bool

The callable passed to `skip_if` must return a `bool`. Any other type, including a truthy int or string, raises `TypeError("User function 'skip_if' shall return bool, but got: ...")`.

**Q:** I see `TypeError: User function 'skip_if' shall return bool, but got: <type>`. What is wrong with my `skip_if` callable?

pyDeprecate enforces this type strictly to keep conditional skipping unambiguous. The error message refers to `skip_if` itself, rather than your callback's name.

- Wrap a non-bool expression in `bool()`.
- Alternatively, use a `lambda` returning literal `True` or `False`.

The same contract applies to `@deprecated`, `deprecated_callable()`, `deprecated_class()`, and `deprecated_instance()`.

```python
# Minimal replacement function for examples
# NEW API — get_status is the replacement API.
def get_status() -> str:
    return "Hi!"


# ---------------------------

from deprecate import deprecated_callable


# Correct: function returns bool
def should_skip() -> bool:
    return False  # replace with your condition


# DEPRECATED API — infer forwards to get_status.
@deprecated_callable(target=get_status, skip_if=should_skip, deprecated_in="1.0", remove_in="2.0")
def infer():
    pass


# Also correct: use a lambda
# DEPRECATED API — infer_v2 forwards to get_status.
@deprecated_callable(target=get_status, skip_if=lambda: False, deprecated_in="1.0", remove_in="2.0")
def infer_v2():
    pass


print(infer())
print(infer_v2())
```

<details>
  <summary>Output: <code>infer(), infer_v2()</code></summary>

```
Hi!
Hi!
```

</details>

### I passed both the old and new argument names at the same time — which value wins?

The explicit new-name value always wins, regardless of argument order. pyDeprecate snapshots that value before remapping and restores it afterwards.

**Q:** My function is decorated with `@deprecated(args_mapping={"val": "new_val"}, ...)`. A caller accidentally passes both `val=5` and `new_val=6` in the same call. Which value does the target receive for `new_val`?

In this example, the target or `TargetMode.ARGS_REMAP` source body receives `new_val=6`. The remapped `val→new_val=5` is discarded.

In addition to the normal `FutureWarning` about the deprecated argument, pyDeprecate emits a `UserWarning` naming the ignored old argument so the caller can clean up the call site:

```
UserWarning: Both `val` (deprecated) and `new_val` were supplied to `old_api()`; `val` is ignored.
```

```python
from deprecate import TargetMode, deprecated


# NEW API — new_api is the replacement API.
def new_api(new_val: int = 0) -> int:
    return new_val


# DEPRECATED API — old_api forwards to new_api.
@deprecated(target=new_api, args_mapping={"val": "new_val"}, deprecated_in="1.0", remove_in="2.0")
def old_api(val: int = 0, new_val: int = 0) -> int:
    return new_val


print(old_api(val=5, new_val=6))  # warns: FutureWarning (UserWarning also fires: val ignored)
print(old_api(new_val=6, val=5))  # warns: FutureWarning (UserWarning also fires: same result)
```

<details>
  <summary>Output: <code>old_api(...)</code></summary>

```
6
6
```

</details>

**Precedence hierarchy (highest to lowest):**

| Priority | Value                   | Effect                                                                                            |
| -------- | ----------------------- | ------------------------------------------------------------------------------------------------- |
| Highest  | `args_extra`            | Merged last; unconditionally overwrites all caller values, including the explicit new-name kwarg. |
| Middle   | Explicit new-name kwarg | Wins over the remapped old-name value.                                                            |
| Lowest   | Remapped old-name value | Wins over source defaults.                                                                        |

```python
from deprecate import deprecated


# NEW API — new_api is the replacement API.
def new_api(new_val: int = 0) -> int:
    return new_val


# DEPRECATED API — old_api_with_extra forwards to new_api.
@deprecated(
    target=new_api,
    args_mapping={"val": "new_val"},
    args_extra={"new_val": 99},
    deprecated_in="1.0",
    remove_in="2.0",
)
def old_api_with_extra(val: int = 0, new_val: int = 0) -> int:
    return new_val


print(old_api_with_extra(val=5, new_val=6))  # args_extra wins — result is 99, not 6
```

<details>
  <summary>Output: <code>old_api_with_extra(val=5, new_val=6)</code></summary>

```
99
```

</details>

This applies equally to `@deprecated` with `target=TargetMode.ARGS_REMAP` or a callable `target`, and to `deprecated_class()` / `deprecated_instance()` with `args_mapping`.

### UserWarning when using `TargetMode.ARGS_REMAP` without `args_mapping`

`TargetMode.ARGS_REMAP` is designed exclusively for renaming or dropping arguments within the same function. Without `args_mapping` there is nothing to remap — the decorator has zero call-time effect. This is a misconfiguration that emits a `UserWarning` today and will become a `TypeError` in v1.0.

**Q:** I applied `@deprecated(target=TargetMode.ARGS_REMAP, ...)` and got the warning `UserWarning: @deprecated(target=TargetMode.ARGS_REMAP) on my_func requires args_mapping ...`. Why, and how do I fix it?

Choose the mode that matches your intent:

- **Rename or drop a parameter** — provide `args_mapping` as required:

```python
from deprecate import TargetMode, deprecated


@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_name": "new_name"}, deprecated_in="1.0", remove_in="2.0")
def my_func(old_name: int = 0, new_name: int = 0) -> int:
    return new_name * 2


print(isinstance(my_func(old_name=3), int))
```

<details>
  <summary>Output: <code>isinstance(my_func(old_name=3), int)</code></summary>

```
True
```

</details>

- **Warn callers with no forwarding or remapping** — use `TargetMode.NOTIFY` instead:

```python
from deprecate import TargetMode, deprecated


@deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
def my_func(val: int) -> int:
    """Going away — remove all call sites."""
    return val * 2


print(my_func(3))
```

<details>
  <summary>Output: <code>print(my_func(3))</code></summary>

```
6
```

</details>

!!! danger "This misconfiguration will become a TypeError in v1.0"

    Migrate now to avoid a hard break on upgrade. Use the [audit tools](guide/audit.md) to detect this combination across your codebase automatically — `find_deprecation_wrappers()` reports it via the `misconfigured_target` flag on `DeprecationWrapperInfo`.

### TypeError at decoration time: cross-class method target

The cross-class guard raises `TypeError` at decoration time, when the class body executes. It rejects a method in `Foo` whose `@deprecated` target is a method defined on another class, `Bar`.

**Q:** I got the following error at decoration time — what does it mean and how do I fix it?

```text
TypeError: Cannot use @deprecated on 'Foo.old_method' with target 'Bar.new_method':
cross-class method forwarding is not supported because `self` would carry the wrong type.
The target must be a method on the same class ('Foo') or a full class (use target=Bar for class migration).
```

Forwarding would pass `self` of the wrong type, risking `AttributeError` or incorrect runtime behavior. The guard catches that misconfiguration before production.

**Common fix — forward to the correct target within the same class or to a standalone function:**

```python
from deprecate import deprecated


class MyService:
    # NEW API — execute is the replacement API.
    def execute(self, val: int) -> int:
        return val * 2

    # Correct: target is on the same class
    # DEPRECATED API — run forwards to execute.
    @deprecated(target=execute, deprecated_in="1.0", remove_in="2.0")
    def run(self, val: int) -> int:
        pass


print(MyService().run(3))
```

<details>
  <summary>Output: <code>MyService().run(3)</code></summary>

```
6
```

</details>

If you are intentionally delegating to another class, convert the target to a standalone function or use `@deprecated_class` to deprecate the whole class instead.

!!! note "Static methods can forward across classes"

    The guard skips sources that are `@staticmethod` descriptors because they receive no `self`. Use `@deprecated(...) @staticmethod` or the equivalent `@staticmethod @deprecated(...)` order:

```python
from deprecate import deprecated


# NEW API — NewMath is the replacement API.
class NewMath:
    @staticmethod
    def compute(x: int) -> int:
        return x * 3


# DEPRECATED API — OldMath forwards to NewMath.
class OldMath:
    @deprecated(target=NewMath.compute, deprecated_in="1.0", remove_in="2.0")
    @staticmethod
    def compute(x: int) -> int:
        pass


print(OldMath.compute(5))  # warns: FutureWarning — forwards to NewMath.compute
```

<details>
  <summary>Output: <code>OldMath.compute(5)</code></summary>

```
15
```

</details>

**False-positive triggers fixed in v0.8:**

Before v0.8, two patterns produced spurious `TypeError` raises from the guard:

- **Decorators rewriting `__qualname__`.** A decorator setting `fn.__qualname__ = "OtherClass.method"` before `@deprecated` made the guard infer the wrong owner. Since v0.8, the guard reads `__qualname__` from the enclosing class-body frame, which Python sets before decorators run.
- **Metaclass-generated classes.** `type("Name", bases, ns)` and similar patterns can produce `"FakeOwner.method"` qualnames for methods outside `FakeOwner`. Since v0.8, the guard checks that the qualname's class name exists in module globals. If it does not, the guard skips the check.

If you are on v0.8+ and still seeing an unexpected `TypeError` from the cross-class guard, open an issue with a minimal reproducer.

### UserWarning at decoration time: unsupported stacking combination

pyDeprecate validates stacking combinations at decoration time and emits `UserWarning` for every unsupported case.

**Q:** I got a `UserWarning` when applying multiple `@deprecated` decorators to the same function. What does it mean and how do I fix it?

The message looks like one of:

```text
UserWarning: 'my_func' has @deprecated(NOTIFY) stacked over @deprecated(ARGS_REMAP).
Reverse the decorator order: put @deprecated(ARGS_REMAP, ...) outermost (on top)
and @deprecated(NOTIFY, ...) below it. Will be `TypeError` in `v1.0`.
```

Common supported combinations include:

- `ARGS_REMAP` above `ARGS_REMAP` supports multi-step argument renames across versions. The top layer is outermost.
- `ARGS_REMAP` above `NOTIFY` supports a lifecycle pattern: rename arguments first, then deprecate the whole function. The top layer is outermost.
- `NOTIFY` above a callable deprecates that target directly without an inner `@deprecated` wrapper. The top layer is outermost.

See [Supported stacking combinations](guide/functions.md#supported-stacking-combinations) for the full table. The warning message identifies which combination fired and includes a corrective hint.

**Most common case — wrong order (NOTIFY over ARGS_REMAP):**

```python
from deprecate import TargetMode, deprecated


# Wrong — NOTIFY outer emits UserWarning at decoration time
@deprecated(deprecated_in="2.0", remove_in="3.0")  # outer NOTIFY
@deprecated(TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"old": "new"})
def my_func(old: int = 0, new: int = 0) -> int:
    return new


# Correct — ARGS_REMAP on top, NOTIFY below
@deprecated(TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"old": "new"})
@deprecated(deprecated_in="2.0", remove_in="3.0")  # inner NOTIFY
def my_func(old: int = 0, new: int = 0) -> int:
    return new


print(my_func())
```

<details>
  <summary>Output: <code>my_func()</code></summary>

```
0
```

</details>

### My deprecated wrapper's warning fires when I define a function, not when I call it

No. Applying `@my_deprecated_wrapper` calls the deprecated wrapper immediately when the `def` statement executes. The warning fires at that call, before the decorated function is called later.

**Q:** My deprecated function produces wrappers and is used as a decorator (`@deprecated(target=new_wrapper, ...)`). Its warning fires immediately at the `def` statement when I apply it with `@` syntax. Is this a decorator-ordering bug?

The expression `@my_deprecated_wrapper` on `def my_func(): ...` is equivalent to `my_func = my_deprecated_wrapper(my_func)`. At module scope, this occurs during import.

```python
from deprecate import deprecated


# NEW API — new_wrapper is the replacement API.
def new_wrapper(func):
    return func


# DEPRECATED API — old_wrapper forwards to new_wrapper.
@deprecated(target=new_wrapper, deprecated_in="0.9", remove_in="1.0")
def old_wrapper(func):
    return func


# Warning fires HERE, at the @old_wrapper line — decorating IS calling old_wrapper(sample)
@old_wrapper
def sample(x: int) -> int:
    return x * 2


# FutureWarning: The `old_wrapper` was deprecated since v0.9 in favor of `new_wrapper`.

# Calling sample(...) later does not warn again — the deprecated call already happened above
print(sample(5))
```

<details>
  <summary>Output: <code>sample(5)</code></summary>

```
10
```

</details>

This matches other pyDeprecate warnings: they fire when the deprecated callable is invoked. They are never deferred to later use of its return value.

!!! note "Warning timing cannot be deferred"

    There is no option to delay this warning until `sample(...)` runs. That would require intercepting `old_wrapper`'s return value with another wrapper, adding `num_warns` and introspection complications without a clear benefit.

To warn at the wrapped function's call site, choose one approach:

- Decorate the target explicitly at the call site instead of using `@` syntax on its definition.
- Apply `@deprecated` directly to `sample`, rather than to its wrapper-producing function.

### Warning fires or UserWarning appears when using `@deprecated @classmethod`

Yes. pyDeprecate silently rescues the misordered stack at decoration time.

**Q:** I applied `@deprecated` on top of `@classmethod` (decorator order: `@deprecated` outermost, `@classmethod` innermost). Does this work?

```python
from deprecate import deprecated


class Foo:
    # Works — @deprecated outside @classmethod is transparently rescued
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    @classmethod
    def old_method(cls, val): ...


print(Foo.old_method(1))
```

<details>
  <summary>Output: <code>Foo.old_method(1)</code></summary>

```
None
```

</details>

`@deprecated` detects that it received a `classmethod` descriptor, unwraps it, applies the deprecation wrapper to the underlying function, and re-wraps the result in `classmethod`. The outcome is a fully working deprecated classmethod: no `UserWarning` at decoration time, and a `FutureWarning` fires when the method is called.

The preferred order is still `@classmethod` outermost with `@deprecated` applied closer to `def` — it is explicit and avoids the silent rescue:

```python
from deprecate import deprecated


# NEW API — new_impl is the replacement API.
def new_impl(cls, val):
    return val * 2


# DEPRECATED API — Foo.old_method forwards to new_impl.
class Foo:
    # Preferred — @deprecated applied to the raw function, @classmethod wraps the result
    @classmethod
    @deprecated(target=new_impl, deprecated_in="1.0", remove_in="2.0")
    def old_method(cls, val):
        pass


print(Foo.old_method(1))
```

<details>
  <summary>Output: <code>Foo.old_method(1)</code></summary>

```
2
```

</details>

The same rule applies to `@staticmethod`.

### Why can't I decorate a `functools.partial` or callable wrapper?

`@deprecated` accepts Python functions, supported descriptors, and classes as sources. Callable wrappers such as `functools.partial`, `functools.partialmethod`, and `singledispatchmethod` raise `TypeError`, even when their wrapped callable is async.

**Q:** I applied `@deprecated` to a `functools.partial`, `functools.partialmethod`, or `singledispatchmethod`. Which callable forms can be decorated?

Define a plain `def` or `async def` with the desired public signature and decorate that named function.

!!! note "Async detection and object aliases"

    For async targets, pyDeprecate follows `inspect.iscoroutinefunction` on the supported Python runtime. An async callable instance returning an awaitable is a distinct kind and might not be recognized as a coroutine function.

    `deprecated_instance` deprecates an object alias. It does not substitute for a decorated function source.

```python
import asyncio
import functools
from deprecate import deprecated


# NEW API — new_fetch is the replacement API.
async def new_fetch(url: str, timeout: int = 30) -> bytes:
    return url.encode()


# Correct: apply @deprecated to the async def directly
# DEPRECATED API — old_fetch forwards to new_fetch.
@deprecated(target=new_fetch, deprecated_in="0.9", remove_in="1.0")
async def old_fetch(url: str, timeout: int = 30) -> bytes:
    pass


# Then use partial on the already-deprecated async wrapper if needed
fetch_with_timeout = functools.partial(old_fetch, timeout=10)
url = asyncio.run(old_fetch("https://example.com"))
print(url.decode())
```

<details>
  <summary>Output: <code>asyncio.run(old_fetch("https://example.com")).decode()</code></summary>

```
https://example.com
```

</details>

### `@deprecated` and targets with POSITIONAL_ONLY parameters

Yes, this is fully supported since `v0.10`. The decorator detects POSITIONAL_ONLY parameters at decoration time and advises removing `/` from the target signature through a `UserWarning`.

**Q:** I applied `@deprecated(target=new_fn)` where `new_fn` has a positional-only parameter — does it work?

It automatically splits dispatch so positional-only values are forwarded positionally instead of as kwargs.

```python
from deprecate import deprecated


# NEW API — new_fn is the replacement API.
def new_fn(val: int, /, y: int = 0) -> int:
    return val + y


# DEPRECATED API — old_fn forwards to new_fn.
@deprecated(target=new_fn, deprecated_in="1.0", remove_in="2.0")  # warns: UserWarning (POSITIONAL_ONLY detected)
def old_fn(val: int, y: int = 0) -> int: ...


print(old_fn(5))  # warns: FutureWarning
print(old_fn(val=5))  # warns: FutureWarning
print(old_fn(3, y=4))  # warns: FutureWarning
```

<details><summary>Output: <code>5 · 5 · 7</code></summary>

```
5
5
7
```

</details>

The thin-adapter pattern shown below still works and avoids the decoration-time `UserWarning` — use it when you want to silence the warning or keep the target signature unambiguous:

```python
from deprecate import deprecated


def new_fn(val: int, /, y: int = 0) -> int:
    return val + y


# NEW API — _new_fn_compat is the replacement API.
def _new_fn_compat(val: int, y: int = 0) -> int:
    return new_fn(val, y)


# DEPRECATED API — old_fn forwards to _new_fn_compat.
@deprecated(target=_new_fn_compat, deprecated_in="1.0", remove_in="2.0")
def old_fn(val: int, y: int = 0) -> int: ...
```

`deprecated_class` handles constructor POSITIONAL_ONLY parameters the same way. It forwards remapped values positionally and emits a decoration-time `UserWarning` for visibility. Required positional-only parameters, frozen dataclasses, and `__post_init__` logic all work.

Decorated sources declaring `/` are also fully supported in all modes. They produce no decoration-time warning.

### `RuntimeError: Circular deprecation cycle detected`

A circular `target=` chain re-enters a `@deprecated` wrapper and raises `RuntimeError`. This occurs when a wrapper targets another deprecated callable whose chain eventually loops back.

Plain calls raise at the first re-entry point. Generator wrappers raise on the first `next()` or `async for` step because calling them runs no code.

The same error occurs if a replacement recurses through the deprecated name while the wrapper remains on the call path. For a generator, that means before its first `yield`. Recurse through the replacement's own name instead.

```python
# phmdoctest:skip
from deprecate import deprecated


def new_fn(x: int) -> int:
    return old_fn(x)


@deprecated(target=new_fn, deprecated_in="1.0", remove_in="2.0")
def old_fn(x: int) -> int: ...
```

Calling `old_fn(1)` raises:

```
RuntimeError: Circular deprecation cycle detected: `old_fn` re-entered via its own target chain.
Point to a non-deprecated final implementation.
```

**Why?**

The `target=` expression is evaluated when the definition executes. Here the replacement calls the deprecated wrapper again, which would recurse infinitely. The detector intercepts re-entry and raises `RuntimeError` instead of letting Python reach `RecursionError`.

**Fix**

Point `target=` at the plain (non-deprecated) final implementation, not at another `@deprecated` callable:

```python
from deprecate import deprecated


# NEW API — _impl is the replacement API.
def _impl(x: int) -> int:
    return x * 2


# DEPRECATED API — old_fn forwards to _impl.
@deprecated(target=_impl, deprecated_in="1.0", remove_in="2.0")
def old_fn(x: int) -> int: ...


# DEPRECATED API — also_old_fn forwards to _impl.
@deprecated(target=_impl, deprecated_in="1.0", remove_in="2.0")
def also_old_fn(x: int) -> int: ...
```

Both deprecated names forward to the same non-deprecated implementation with no cycle.

### My `singledispatch` or `__signature__`-patched source receives keyword arguments

`TargetMode.NOTIFY` forwards caller arguments unchanged. `TargetMode.ARGS_REMAP` also does so when no deprecated argument name is used. Positional dispatch therefore keeps working in these cases.

**Q:** My deprecated `functools.singledispatch` source fails with `TypeError: ... requires at least 1 positional argument` or receives unexpected keywords. A source with `__signature__` differing from its real call shape can also reject calls the plain function accepts.

Two cases still differ from calling the source directly:

- **Deprecated names with `TargetMode.ARGS_REMAP`, or any `args_extra`.** Remapping rewrites the whole call into kwargs, whether the old name was passed by keyword or filled positionally. Setting `args_extra` rewrites every call this way. Positional dispatchers then receive keywords. Keep the dispatcher outside the rename: decorate a plain `def` accepting both names, then call the dispatcher positionally.
- **A patched `__signature__` omits accepted positional parameters.** The wrapper checks positional argument count against `inspect.signature(source)`. Calls filling more slots than that signature lists raise `TypeError: ... takes N positional argument(s) but got M positional argument(s)`. This can reject calls the real source accepts. List every positional slot in `__signature__`, or give it a `*args` parameter.

### Why does a classmethod receiver keyword raise even when mapped to an empty string?

A caller keyword passed through `**kwargs` can match a classmethod target's receiver name and collide with the forwarded class. An `args_mapping` value of `""` keeps the original name. The call therefore raises `TypeError` before emitting a warning or spending its budget.

Rename that keyword to a different target parameter or map it to `None` to drop it.

## Classes, enums and dataclasses

### UserWarning when decorating a class

Since v0.12, `@deprecated` on a class is first-class supported and permanent. It dispatches to `@deprecated_class()` and produces an identical `_DeprecatedProxy`. Nothing is broken and nothing needs migrating.

**Q:** I applied `@deprecated` directly to a class and got `` UserWarning: `@deprecated` on class `MyClass` now dispatches to `@deprecated_class`. `` Is this a problem, and how do I stop it?

!!! note "The dispatch notice is temporary"

    The `UserWarning` is a one-time informational notice that the dispatch happened. Only this notice is removed entirely in v1.0; class dispatch remains supported.

    - The notice fires at most once per class, rather than on every decoration.

    - The cache key combines the module and qualified name. Same-named classes in different modules each warn.

    - The cache is thread-safe and retains at most 4096 class keys in insertion order. An evicted class may receive the notice again.

You have two ways to quiet it, and one alternative pattern for a different use case:

- **Apply `@deprecated_class()` directly** — same result, and it skips the notice entirely. Prefer this for new code, and it is required to reach proxy-only knobs such as `attrs_mapping`.
- **Pass `stream=None`** — suppresses the notice (and every other deprecation notice from that decoration) while keeping `@deprecated` on the class.
- **Decorate `__init__` instead of the class** — use this when you want a notice only at instantiation time while keeping the class name and identity untouched (no proxy involved).

```python
from deprecate import TargetMode, deprecated_class
from enum import Enum


# Preferred: use @deprecated_class directly for classes — identical result, no notice
@deprecated_class(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
class MyClass:
    pass


@deprecated_class(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
class MyEnum(Enum):
    A = 1
    B = 2


# Alternative: decorate __init__ to warn at instantiation while keeping the class name
from deprecate import TargetMode, deprecated


class MyOtherClass:
    @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
    def __init__(self, val: int) -> None:
        self.val = val  # body still executes; warning fires on every new MyOtherClass(...)


print(MyOtherClass(5).val)
```

<details>
  <summary>Output: <code>MyOtherClass(5).val</code></summary>

```
5
```

</details>

### Upgrading from `@deprecated` on a class to `@deprecated_class`

Before v0.6.0, applying `@deprecated` directly to a class replaced the class object with a plain wrapper function. Python's `isinstance`, `issubclass`, and attribute lookup operate on the class type. Replacing the class with a function broke downstream uses that depended on a type.

**Q:** My codebase used an older version of pyDeprecate that applied `@deprecated` directly to a class. It behaved strangely — `isinstance()` checks failed, subclassing broke, and class attributes were inaccessible. What went wrong, and how do I migrate?

!!! failure "The old pattern silently broke isinstance, issubclass, and attribute access"

    Before v0.6.0, `@deprecated` on a class replaced the class with a plain function. All type checks, subclassing, and class attribute access failed silently or raised `TypeError`. If you see this pattern in your codebase, migrate to `@deprecated_class()` immediately.

Symptoms of the old behaviour:

```python
# phmdoctest:skip — shows pre-v0.6 behaviour; current code uses a proxy and no longer produces these errors
# --- Old broken pattern (pre-v0.6) ---
from deprecate import deprecated


# NEW API — Renderer is the replacement API.
class Renderer:
    pass


# DEPRECATED API — Formatter forwards to Renderer.
@deprecated(target=Renderer, deprecated_in="1.0", remove_in="2.0")
class Formatter:
    class_attr = 42


obj = Formatter()
isinstance(obj, Formatter)  # TypeError or False — Formatter is now a function
issubclass(Formatter, object)  # TypeError — same reason
Formatter.class_attr  # AttributeError — wrapper function has no class attributes
```

The replacement is `@deprecated_class()`, which wraps the class in a `_DeprecatedProxy`. The proxy forwards attribute access, item access, calls, and type checks to the target class.

- `isinstance` and `issubclass` work correctly.
- Class attributes remain accessible.
- Existing subclasses continue to work.

```python
from deprecate import deprecated_class


# NEW API — MathCalculator is the replacement API.
class MathCalculator:
    def add(self, a: int, b: int) -> int:
        return a + b


# DEPRECATED API — Calculator forwards to MathCalculator.
@deprecated_class(target=MathCalculator, deprecated_in="1.0", remove_in="2.0")
class Calculator:
    pass


obj = Calculator()
print(isinstance(obj, MathCalculator))  # True — proxy forwards isinstance checks
print(issubclass(Calculator, object))  # True — type checks pass through
print(obj.add(1, 2))  # 3 — forwarded to MathCalculator
```

<details>
  <summary>Output: <code>obj.add(1, 2)</code></summary>

```
True
True
3
```

</details>

The same rule applies to Enums and dataclasses: use `@deprecated_class` rather than `@deprecated` for this pattern.

```python
from enum import Enum
from deprecate import deprecated_class


# NEW API — ColorPalette is the replacement API.
class ColorPalette(Enum):
    RED = 1
    BLUE = 2


# DEPRECATED API — Color forwards to ColorPalette.
Color = deprecated_class(target=ColorPalette, deprecated_in="1.5", remove_in="2.0")(ColorPalette)

print(Color.RED is ColorPalette.RED)  # True
print(Color(1) is ColorPalette.RED)  # True
print(Color["RED"] is ColorPalette.RED)  # True
```

<details>
  <summary>Output: <code>Color["RED"] is ColorPalette.RED</code></summary>

```
True
True
True
```

</details>

For a notice only at instantiation time, decorate `__init__`. This keeps the class object intact without deprecating its name, so `isinstance` and `issubclass` remain unaffected:

```python
from deprecate import TargetMode, deprecated


class MyService:
    @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
    def __init__(self, host: str) -> None:
        self.host = host  # body executes; warning fires at every MyService(...)


svc = MyService("localhost")
print(isinstance(svc, MyService))  # True
```

<details>
  <summary>Output: <code>isinstance(svc, MyService)</code></summary>

```
True
```

</details>

### Can I subclass a class created by `deprecated_class`?

Yes. The proxy implements PEP 560 `__mro_entries__` so Python transparently subclasses the replacement class instead of the proxy. Subclassing is treated as usage of the deprecated name and emits one `FutureWarning` — the class hierarchy is correct at the Python level.

**Q:** I tried `class Child(OldName): pass` where `OldName` was created by `deprecated_class(target=NewBase, ...)`. In older pyDeprecate versions this raised `TypeError`. Does subclassing work now?

```python
from deprecate import deprecated_class


# NEW API — Base is the replacement API.
class Base:
    def greet(self) -> str:
        return "hello"


# DEPRECATED API — OldBase forwards to Base.
OldBase = deprecated_class(target=Base, deprecated_in="1.0", remove_in="2.0")(Base)


class Child(OldBase):  # warns: FutureWarning
    pass


print(issubclass(Child, Base))
print(Child().greet())
```

<details>
  <summary>Output: <code>issubclass(Child, Base); Child().greet()</code></summary>

```
True
hello
```

</details>

!!! note "Subclassing budgets and mapping-only proxies"

    The warning fires at class-definition time, when `class Child(OldBase)` is evaluated. It uses `OldBase`'s shared `num_warns` budget.

    `TargetMode.ATTRS_REMAP` and `TargetMode.ARGS_REMAP` proxies do not warn on subclassing. They scope deprecation to attribute names or argument names, rather than general class-alias usage.

### Why doesn't `deprecated_class` warn when I call it with the new argument name?

No, this is intended. With `args_mapping` provided and `target` omitted, the proxy auto-resolves to `TargetMode.ARGS_REMAP`. It warns only when the call contains the old argument name.

**Q:** I set up `deprecated_class(args_mapping={"old_arg": "new_arg"}, ...)` on my class but no warning fires when I call it with `new_arg=...`. Did I configure it incorrectly?

Callers using the new name see no warning. This matches `@deprecated(target=TargetMode.ARGS_REMAP, args_mapping=...)`.

```python
from deprecate import deprecated_class


# NEW API — Config is the replacement API.
class Config:
    def __init__(self, timeout: int = 0) -> None:
        self.timeout = timeout


# args_mapping without a callable target → auto ARGS_REMAP
# DEPRECATED API — LegacyConfig forwards to Config.
LegacyConfig = deprecated_class(
    args_mapping={"time_limit": "timeout"},
    deprecated_in="1.5",
    remove_in="2.0",
)(Config)

print(LegacyConfig(timeout=30).timeout)  # new name — no warning (caller already migrated)
print(LegacyConfig(time_limit=30).timeout)  # old name — FutureWarning emitted + remapped
```

<details>
  <summary>Output: <code>LegacyConfig(...).timeout</code></summary>

```
30
30
```

</details>

!!! warning "Explicit targets disable auto-resolution"

    Auto-resolution applies only when `target` is omitted. Explicit `target=TargetMode.NOTIFY` with `args_mapping` is a misconfiguration.

    - A `UserWarning` fires at decoration time; this becomes `TypeError` in v1.0.

    - The mode stays `NOTIFY`, and the mapping is inert at runtime.

    - Audit metadata preserves the mapping and flags it as `misconfigured`.

Explicit configuration is never silently rewritten. When `target` is omitted, a present `args_mapping` resolves to `TargetMode.ARGS_REMAP`. On the `@deprecated` front door, the `TargetMode.AUTO` default performs this inference.

No single proxy both warns on every instantiation and actively remaps arguments. To get both behaviors, stack two `deprecated_class()` layers:

1. Use an inner layer with `args_mapping` only (`ARGS_REMAP`) for the rename.
2. Use an outer layer without mapping (`NOTIFY`) for the blanket notice.

See [Nested proxy wrappers](guide/attributes.md#nested-proxy-wrappers) for the pattern.

### `attrs_mapping` auto-expand skips some fields on a dataclass with a custom `__init__`

For a `@dataclass` target, `deprecated_class()` automatically copies eligible `attrs_mapping` entries into `args_mapping`. Class-proxy access (`DC.old_field`) and constructor calls (`DC(old_field=5)`) can then emit `FutureWarning`. Returned instances are not proxied.

**Q:** I added `attrs_mapping` to a `deprecated_class()` wrapping a dataclass that overrides `__init__`. Some deprecated aliases aren't being auto-expanded into `args_mapping` — constructor calls with the old name raise `TypeError` instead of remapping. Why?

Auto-expansion uses `inspect.signature` to identify valid `__init__` kwargs.

There are two cases where a field is intentionally excluded:

1. **`field(init=False)`** — the field is an instance attribute set inside `__init__` (or `__post_init__`) but is not a constructor parameter. Passing it as a kwarg would raise `TypeError`.
2. **A custom `__init__` omits a dataclass field.** A field absent from that signature is not a valid kwarg. Auto-expansion excludes it.

Both surfaces still work independently: `attrs_mapping` redirects class-proxy access for listed class attributes, while `args_mapping` only covers fields present in the actual constructor signature.

```python
from dataclasses import dataclass, field
from deprecate import deprecated_class, get_deprecation_config


# NEW API — Config is the replacement API.
@dataclass
class Config:
    timeout: int = 30
    _cache: dict = field(default_factory=dict, init=False)  # not a constructor param


# DEPRECATED API — DepConfig forwards to Config.
DepConfig = deprecated_class(
    attrs_mapping={"time_limit": "timeout", "store": "_cache"},
    deprecated_in="1.0",
    remove_in="2.0",
    stream=None,
)(Config)

meta = get_deprecation_config(DepConfig)
print("timeout auto-expanded:", "time_limit" in meta.args_mapping_auto_expanded)
print("_cache auto-expanded:", "store" in meta.args_mapping_auto_expanded)
```

<details>
  <summary>Output: <code>meta.args_mapping_auto_expanded</code></summary>

```
timeout auto-expanded: True
_cache auto-expanded: False
```

</details>

`store` is excluded from `args_mapping` — calling `DepConfig(store={})` would raise `TypeError`. Leave the `init=False` field out of constructor calls. `attrs_mapping` applies to class-proxy access, not attributes on returned instances.

## Instances and proxies

### Do arithmetic and operator protocol methods emit deprecation notices on `deprecated_instance` proxies?

Yes. The proxy forwards operator and protocol dunders to the wrapped object and emits warnings according to the policy below.

**Q:** I wrapped an `int` or `float` with `deprecated_instance` and operations like `old_value + 1` or `abs(old_value)` don't seem to emit a notice. How does operator interception work?

Python invokes special methods on the object's type, bypassing `__getattr__`. The proxy class therefore defines those methods itself.

Each proxy uses a cached `_DeprecatedProxy` subclass carrying only the protocols defined by the wrapped object's type. A proxy over a `dict` is neither `callable()` nor an `Iterator`, just like the dict.

!!! note "Text conversion support"

    Text is the exception to that protocol matching. `int()`, `float()`, `complex()` and `os.fspath()` keep working on a proxy over `str` or `bytes`.

**What warns (data-use operations):**

- Arithmetic operators (`+`, `-`, `*`, `/`, `//`, `**`, `%`, `@`) and `divmod()` warn. Their reflected and in-place forms also warn.
- Bitwise and shift operators (`&`, `|`, `^`, `<<`, `>>`) warn. Their reflected and in-place forms also warn.
- Numeric and path conversions warn: `int()`, `float()`, `complex()`, `operator.index()`, and `os.fspath()`.
- Unary numeric operators warn: `abs()`, `-obj`, `+obj`, and `~obj`.
- Rounding operations warn: `round()`, `math.trunc()`, `math.floor()`, and `math.ceil()`.
- `reversed()` and `next()` warn.
- Context-manager entry warns for `with proxy:` and `async with proxy:`.
- Entering async iteration with `async for` warns.
- Awaiting a proxy with `await proxy` warns.
- Subclassing through `class Child(proxy):` warns.

**What stays silent (structural probes):**

- Ordering comparisons stay silent: `<`, `<=`, `>`, and `>=`.
- `bool()` and `hash()` stay silent.
- The `in` operator stays silent when the proxy's own `__contains__` answers it.
- Without a wrapped `__contains__`, Python falls back to the proxy's warning `__iter__` or `__getitem__`. In that case, `in` warns like iteration.
- `format()` and f-strings using the wrapped object stay silent.
- Context-manager exit through `__exit__` or `__aexit__` stays silent.

This mirrors the existing silent/warn split for `__eq__` / `__len__` / `__str__` versus `__getitem__` / `__iter__`.

In-place operators (`+=`, `-=`, etc.) return the active object's own result without wrapping it again. Assigning that result rebinds the variable to the underlying type:

```python
from deprecate import deprecated_instance

n = deprecated_instance(3, deprecated_in="1.0", remove_in="2.0", stream=None)
n += 1  # silent (stream=None); n is now plain int(4), not a proxy
print(type(n).__name__)
```

<details>
  <summary>Output: <code>type(n).__name__</code></summary>

```
int
```

</details>

!!! warning "Strict-type reflected operators"

    A proxy on the right cannot intercept an operation if the left operand raises `TypeError` instead of returning `NotImplemented`. Python cannot fall back to the proxy's reflected method.

    For example, `"prefix" + proxy_str` fails because `str.__add__` raises `TypeError` for non-string operands. The proxy-on-left form works normally and warns:

```python
from deprecate import deprecated_instance

s = deprecated_instance("hello", deprecated_in="1.0", remove_in="2.0", stream=None)
print(s + " world")  # silent (stream=None) — proxy on left side works
# " world" + s → TypeError (str.__add__ raises instead of returning NotImplemented)
```

<details>
  <summary>Output: <code>s + " world"</code></summary>

```
hello world
```

</details>

For primitive constants where reflected operations on the right are required, wrap the value in a container (dict or dataclass) so access goes through `__getitem__` or `__getattr__`:

```python
from deprecate import deprecated_instance

# Container approach — subscript access warns regardless of operand position
_THRESHOLDS = {"value": 0.5}
OLD_THRESHOLD = deprecated_instance(
    _THRESHOLDS,
    name="OLD_THRESHOLD",
    deprecated_in="1.0",
    remove_in="2.0",
)

# Access via subscript triggers the warning:
print(OLD_THRESHOLD["value"])
```

<details>
  <summary>Output: <code>OLD_THRESHOLD["value"]</code></summary>

```
0.5
```

</details>

### My object mutated despite `read_only=True`

`read_only=True` intercepts only the following standard collection mutator names: `append`, `clear`, `discard`, `extend`, `insert`, `pop`, `remove`, `setdefault`, `update`, `add`. These cover the mutating methods on Python's built-in `list`, `dict`, and `set` types.

**Q:** I passed `read_only=True` to `deprecated_instance()` but a method on my object still mutated its state. Why?

Custom method names — for example `register()`, `reload()`, or `set_value()` — are not in this list and call through to the underlying object without any guard.

!!! warning "An active skip disables mutation guards"

    While `skip_if` evaluates `True`, the whole deprecation machinery is off. Attribute writes, item writes and deletes (`proxy[key] = value`, `del proxy[key]`), and mutator calls reach the wrapped object directly.

**Workaround:** Subclass the wrapped object's type and override the custom mutator method to raise explicitly:

```python
class ReadOnlyRegistry(dict):
    def register(self, item):
        raise AttributeError("'LEGACY_REGISTRY' is deprecated and read-only. Migrate away from this object.")


print(issubclass(ReadOnlyRegistry, dict))
```

<details>
  <summary>Output: <code>issubclass(ReadOnlyRegistry, dict)</code></summary>

```
True
```

</details>

Then wrap an instance of `ReadOnlyRegistry` instead of a plain `dict`. This keeps `read_only=True` in place for standard collection mutators while adding explicit guards for your custom methods.

### Why did a failed `proxy[key]` lookup not warn?

Synchronous data-use operations normally warn only after success. A `KeyError` from `proxy[key]` or an `AttributeError` during `hasattr` emits nothing. The `num_warns` budget remains unspent.

**Q:** My code probes a deprecated mapping with `try: settings[key] except KeyError: ...`. The miss emitted no `FutureWarning`, but the next successful `settings["threshold"]` read did.

!!! note "Some operations warn before completion"

    - `next(proxy)` warns before advancing the wrapped iterator, so failed warning delivery loses no item. An exhausting `next()` raising `StopIteration` still spends the budget.

    - `__anext__` and `__await__` warn when they obtain the underlying awaitable, before it runs. Later async failure or exhaustion still consumes the budget.

    - If warning delivery raises after context entry acquires a resource, the proxy invokes that context's exit method before propagating the error.

    - Calling a proxy warns before the call, as `@deprecated` does. A deprecated constructor that raises therefore still reports the deprecation.

!!! warning "Fatal proxies reject use before touching the object"

    With `as_exception=True`, the proxy raises `DeprecatedError` before invoking any of these operations:

    - Subscripts and iteration are rejected before use.

    - Unary and conversion operators are rejected before use.

    - `next()` is rejected before use.

    - `with` and `async with` entry are rejected before use.

    - Async iteration and `await` are rejected before use.

    - In-place operators such as `+=` are rejected before use.

    - `attrs_mapping` attribute reads are rejected before use.

    A missing key therefore raises `DeprecatedError` instead of `KeyError`. A `defaultdict` gains no entry.

### Can a `deprecated_instance` of a function be used as a class attribute (method)?

Yes. A proxy over a plain function or `@deprecated` wrapper binds like a method when read through an instance. Its bound method's `__func__` is the proxy.

**Q:** I replaced a method with `class Model: scale = deprecated_instance(legacy_scale, ...)`. Does `Model().scale(2)` still pass the instance to `legacy_scale`?

`Model().scale(2)` calls `legacy_scale(model, 2)` and warns at the calling line.

- The proxy owns the `num_warns` budget, so all instances share the default single warning.
- `skip_if` and `as_exception` behave as on a direct call.
- Class access through `Model.scale` returns the proxy itself, as with a plain function. `Model.scale(model, 2)` therefore also works.

!!! note "Other wrapped objects do not bind"

    Proxies over data, classes, and non-function descriptors are returned as stored. This includes `property`, `classmethod`, and `staticmethod` objects. Deprecate these by applying `@deprecated` to the underlying function instead.

Releases without this binding called `legacy_scale(2)` and raised `TypeError: legacy_scale() missing 1 required positional argument`.

## Properties

### Setter or deleter on a deprecated property doesn't warn

The two property decorator orders behave differently. **Inner order** (`@property` outermost, `@deprecated` closer to `def`) wraps the getter (`fget`) only. Any setter or deleter added later via `@value.setter` / `@value.deleter` is a plain accessor with no deprecation closure, so writes and deletes are silent.

**Q:** I decorated my property with `@deprecated` and added a setter via `@value.setter`, but writing to the property is silent — no `FutureWarning` is emitted. Deleting via `del obj.value` is also silent. What is happening?

```python
# phmdoctest:skip — silent setter/deleter is intended for inner-order wrapping
from deprecate import deprecated


class MyClass:
    @property
    @deprecated(deprecated_in="1.0", remove_in="2.0")  # inner order — wraps fget only
    def value(self) -> int:
        return self._value

    @value.setter
    def value(self, new_value: int) -> None:
        self._value = new_value  # write is SILENT — no FutureWarning

    @value.deleter
    def value(self) -> None:
        self._value = 0  # delete is SILENT — no FutureWarning
```

Use **outer order** (`@deprecated` outermost, `@property` closer to `def`) so all three accessors are wrapped — the `property` subclass re-wraps any setter or deleter added later via chain-style decorators:

```python
from deprecate import deprecated


class MyClass:
    def __init__(self) -> None:
        self._value = 0

    @deprecated(deprecated_in="1.0", remove_in="2.0")  # outer order — wraps fget, fset, fdel
    @property
    def value(self) -> int:
        return self._value

    @value.setter
    def value(self, new_value: int) -> None:
        self._value = new_value  # write now FIRES FutureWarning

    @value.deleter
    def value(self) -> None:
        self._value = 0  # delete now FIRES FutureWarning


obj = MyClass()
obj.value = 5  # FutureWarning fires here
del obj.value  # FutureWarning fires here
print(obj._value)
```

<details>
  <summary>Output: <code>obj._value</code></summary>

```
0
```

</details>

When you need full control over which accessors warn — for example a setter-only or deleter-only property — construct the `property` explicitly and wrap it once with `@deprecated`:

```python
from deprecate import deprecated


def _fget(self):
    return self._value


def _fset(self, v):
    self._value = v


def _fdel(self):
    self._value = None


class MyClass:
    def __init__(self) -> None:
        self._value = 0

    # All three accessors deprecation-wrapped in one shot.
    value = deprecated(deprecated_in="1.0", remove_in="2.0")(property(_fget, _fset, _fdel))


obj = MyClass()
obj.value = 7  # FutureWarning on write
print(obj.value)  # FutureWarning on read
del obj.value  # FutureWarning on delete
print(obj._value)
```

<details>
  <summary>Output: <code>obj._value</code></summary>

```
7
None
```

</details>

**CI / audit detection:** `find_deprecation_wrappers` flags inner-order properties with `inner_order_property=True` on the returned `DeprecationWrapperInfo`. Add this filter to your CI pipeline to catch the pattern before it ships:

```python
# phmdoctest:skip — CI template: replace my_package with your actual package
from deprecate import find_deprecation_wrappers
import my_package  # replace with your package, e.g. `import mylib`


def test_no_inner_order_properties():
    inner_order = [r for r in find_deprecation_wrappers(my_package) if r.inner_order_property]
    assert not inner_order, f"Inner-order @property @deprecated detected: {inner_order}"
```

**Strict-mode import:** Add `from deprecate import property` at module scope. This replaces builtin `property` with a guard against an already-`@deprecated` getter.

The guard raises `TypeError` while evaluating the class body, before any instance exists:

```python
# phmdoctest:skip — TypeError raised at class-body time
from deprecate import deprecated, property  # strict property replaces builtin


class MyClass:
    @property  # strict guard — raises TypeError immediately
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    def value(self) -> int:
        return self._value

    # TypeError: Inner-order `@property @deprecated` detected on `MyClass.value`. ...
```

Modules that do not import the strict `property` keep builtin behaviour; the guard is purely opt-in.

### TypeError: `@deprecated` cannot be applied twice to the already-deprecated property

Double-wrapping would emit two `FutureWarning` notices per access and run the stacking guard three times. The decorator therefore raises `TypeError` at decoration time. The message names the offending property and points to supported alternatives.

**Q:** I tried to stack two `@deprecated` decorators on a property and got `TypeError: ... cannot be applied twice to the already-deprecated property`. Why is this disallowed, and how do I add a setter or deleter to an already-deprecated property?

```python
# phmdoctest:skip — intentional misconfiguration; raises TypeError at decoration time
from deprecate import deprecated


def _fget(self):
    return 0


# First @deprecated — fine.
once_wrapped = deprecated(deprecated_in="1.0", remove_in="2.0")(property(_fget))

# Second @deprecated on the already-wrapped property — TypeError at decoration time.
twice_wrapped = deprecated(deprecated_in="2.0", remove_in="3.0")(once_wrapped)
# TypeError: @deprecated cannot be applied twice to the already-deprecated property ...
```

Apply `@deprecated` once, then add accessors through chain-style `@value.setter` and `@value.deleter` decorators. The outer-order `property` subclass re-wraps each new accessor with the same packing config. Warnings therefore continue to fire on every access kind:

```python
from deprecate import deprecated


class MyClass:
    def __init__(self) -> None:
        self._value = 0

    @deprecated(deprecated_in="1.0", remove_in="2.0")  # outer order — single application
    @property
    def value(self) -> int:
        return self._value

    @value.setter
    def value(self, new_value: int) -> None:
        self._value = new_value  # warning fires here too

    @value.deleter
    def value(self) -> None:
        self._value = 0  # warning fires here too


obj = MyClass()
obj.value = 9
print(obj.value)
```

<details>
  <summary>Output: <code>obj.value</code></summary>

```
9
```

</details>

**Alternative — build the `property` explicitly with all three accessors and wrap once:**

```python
from deprecate import deprecated


def _fget(self):
    return self._value


def _fset(self, v):
    self._value = v


def _fdel(self):
    self._value = None


class MyClass:
    def __init__(self) -> None:
        self._value = 0

    # Single @deprecated wraps all three accessors at once — no chain stacking needed.
    value = deprecated(deprecated_in="1.0", remove_in="2.0")(property(_fget, _fset, _fdel))


obj = MyClass()
obj.value = 42
print(obj.value)
```

<details>
  <summary>Output: <code>obj.value</code></summary>

```
42
```

</details>

If you need to chain multiple migration steps across versions, the supported pattern is one `@deprecated` on the property plus chain-style accessors — not a second `@deprecated` on top.

## Async and generators

### My deprecated generator does not warn until I iterate it

No, this is intended. Calling the wrapper only creates a generator object and runs no code. The warning, `num_warns` budget, and any `as_exceptions()` error occur on the first iteration step.

**Q:** My generator function is decorated with `@deprecated`. Calling the function does not warn — the deprecation warning only appears when I call `next()` or start iterating. Is this a bug?

A generator that is never iterated never warns.

The wrapper remains a generator function, so `inspect.isgeneratorfunction(wrapper)` stays `True`. Frameworks depend on that flag: pytest uses it to run a deprecated `yield` fixture's setup and teardown.

The warning points at the line taking the first item. In tests, put `pytest.warns()` around iteration rather than the call.

```python
from deprecate import deprecated, void


# NEW API — generate_ids is the replacement API.
def generate_ids(start: int, count: int):
    for i in range(count):
        yield start + i


# DEPRECATED API — iter_ids forwards to generate_ids.
@deprecated(target=generate_ids, deprecated_in="0.9", remove_in="1.0")
def iter_ids(start: int, count: int):
    void(start, count)
    yield from ()  # `yield` keeps the stub a generator function


# No warning yet — creating the generator runs no code
gen = iter_ids(10, 3)

# Warning fires here — on the first item
print(list(gen))  # warns: FutureWarning
```

<details>
  <summary>Output: <code>list(gen)</code></summary>

```
[10, 11, 12]
```

</details>

### My deprecated async generator does not warn until I iterate it

No, this is intended. Calling the wrapper only creates an async generator object. The warning fires on the first `async for` step, at the first `__anext__()`.

**Q:** My async generator function is decorated with `@deprecated`. Calling the function does not warn — the deprecation warning only appears once my `async for` loop starts. Is this a bug?

An async generator that is never iterated never warns.

```python
import asyncio
from deprecate import deprecated


# NEW API — stream is the replacement API.
async def stream(n: int):
    for i in range(n):
        yield i


# DEPRECATED API — old_stream forwards to stream.
@deprecated(target=stream, deprecated_in="0.9", remove_in="1.0")
async def old_stream(n: int):
    if False:  # pragma: no cover
        yield 0


# No warning yet — creating the async generator runs no code
agen = old_stream(3)


async def consume(gen):
    async for _ in gen:
        pass


# Warning fires here — on the first `async for` step
asyncio.run(consume(agen))  # warns: FutureWarning
```

!!! note "Framework detection is preserved"

    The wrapper remains an async generator function, so `inspect.isasyncgenfunction(old_stream)` returns `True`. Frameworks checking that flag, including pytest-asyncio fixtures, treat it like the undecorated function.

### Concurrent async calls and warning counts

Concurrent async calls sharing one wrapper cannot race while reserving warning budgets. The async wrapper reaches warning dispatch before its first `await`. Budget checks and reservations, whether callable-wide or per-argument, use the wrapper's lock.

**Q:** I have multiple coroutines all calling the same deprecated `async def` wrapper concurrently. The deprecation notice only appeared once, but I expected it to fire `num_warns` times. What happened?

Each deprecated argument has its own `num_warns` budget. This value is therefore not one overall cap across calls using several deprecated arguments.

The budget controls warning attempts, rather than an exact number of visible records. Standard warning filters may suppress records.

!!! note "Delivery failures differ for callable wrappers and proxies"

    A custom stream may raise after a callable wrapper reserves its budget. Callable delivery does not restore that reservation. Proxy delivery has separate rollback behavior.

Use `num_warns=-1` only for unlimited warning attempts, rather than to avoid a concurrency race.

### My deprecated `async def` warning does not appear in CI

For `async def` functions, the deprecation warning fires when the coroutine is **awaited**, not when the wrapper is called. Creating the coroutine object — `coro = old_fn(val=1)` — produces no warning; `await coro` is what triggers it.

**Q:** I decorated an `async def` function with `@deprecated` but the deprecation notice never shows up in my CI logs or test output. Why, and how do I fix it?

This means warnings can be silently lost in these common scenarios:

- **Tasks scheduled with `asyncio.create_task(old_fn(val=1))`, fixtures, or library hooks.** The warning fires when the event loop executes the coroutine. That can be outside an active `catch_warnings` context, such as during startup, background work, or fixture scheduling.
- **Third-party schedulers**, such as Celery, arq, and anyio task groups, execute the handed-off coroutine in their own context. Its warning may use a different filter from the caller's.
- **`pytest.warns(FutureWarning)` wrapping only the call** — `with pytest.warns(FutureWarning): coro = old_fn()` captures nothing because no warning fires at call time. The block must wrap the `await`.

```python
import asyncio
import warnings
from deprecate import deprecated


# NEW API — new_fetch is the replacement API.
async def new_fetch(url: str) -> bytes:
    return url.encode()


# DEPRECATED API — old_fetch forwards to new_fetch.
@deprecated(target=new_fetch, deprecated_in="0.9", remove_in="1.0")
async def old_fetch(url: str) -> bytes:
    pass


async def captured_correctly():
    result = await old_fetch("https://example.com")  # warns: FutureWarning
    return result


asyncio.run(captured_correctly())
```

**Fix 1 — use `stream=logging.warning`** (recommended for CI):

Logging bypasses Python's warning filter state entirely. The deprecation message appears in your log output regardless of when or where the coroutine is awaited.

```python
import logging
from deprecate import deprecated


# NEW API — new_fetch is the replacement API.
async def new_fetch(url: str) -> bytes:
    return url.encode()


# DEPRECATED API — old_fetch forwards to new_fetch.
@deprecated(target=new_fetch, deprecated_in="0.9", remove_in="1.0", stream=logging.warning)
async def old_fetch(url: str) -> bytes:
    pass
```

**Fix 2 — wrap the `await` in tests**, not just the call:

```python
import pytest
from deprecate import deprecated


# NEW API — fetch returns the encoded URL
async def new_fetch(url: str) -> bytes:
    return url.encode()


# DEPRECATED API — this wrapper uses the default warnings stream
@deprecated(target=new_fetch, deprecated_in="0.9", remove_in="1.0")
async def old_fetch(url: str) -> bytes:
    pass


@pytest.mark.asyncio
async def test_old_fetch_warns():
    with pytest.warns(FutureWarning):
        await old_fetch("https://example.com")  # correct: await inside the warns block
```

!!! note "Async warning timing is by design"

    The `async def` wrapper is a true coroutine: `inspect.iscoroutinefunction(wrapper)` returns `True`. Its warning fires inside the coroutine body when execution begins on `await`.

    Generators and async generators similarly wait for the first `next()` or `async for` step. Only plain functions warn at call time.

### A non-async decorator applied over my `@deprecated` async wrapper no longer looks like a coroutine

A sync decorator over a `@deprecated async def` produces a sync callable, even when it uses `functools.wraps`. `inspect.iscoroutinefunction` checks the outermost callable's `CO_COROUTINE` flag. A sync wrapper therefore hides the coroutine nature.

Returning a sync `wrapper(*args, **kwargs)` hides that coroutine flag.

**Q:** I have `@my_decorator @deprecated(...) async def fn(...)`. After decoration, `inspect.iscoroutinefunction(fn)` returns `False` and asyncio frameworks (FastAPI route handlers, `asyncio.create_task`) reject it. Why?

**Solution:** Make any outer decorator that may be applied over an async wrapper coroutine-aware. Inspect the wrapped callable and emit either an `async def` or a sync wrapper accordingly.

```python
import functools
import inspect


def my_decorator(fn):
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            return await fn(*args, **kwargs)

        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return sync_wrapper


print(my_decorator(lambda x: x + 1)(41))
```

<details>
  <summary>Output: <code>my_decorator(lambda x: x + 1)(41)</code></summary>

```
42
```

</details>

With this pattern, stacking `@my_decorator` above `@deprecated` on an `async def` produces an `async def` wrapper whose `inspect.iscoroutinefunction(...)` returns `True`.

## Modules

### Deprecation target path incorrect across modules

The deprecation message reports the fully-qualified path of the target callable as Python resolves it at decoration time. Ensure the target is imported from its canonical location before the `@deprecated` decorator is applied.

**Q:** I moved a function to a different module and the deprecation message shows an unexpected path. How do I fix the displayed module path?

When moving functions across modules, import the target from its new home explicitly rather than relying on a re-export alias. The path shown in the deprecation message will then reflect the module where the function actually lives, giving callers accurate migration information. The message will correctly show the full path for real imports when used in your package.

### How do I deprecate an entire Python module?

Call `deprecated_module(__name__, deprecated_in=..., remove_in=...)` at the bottom of the module you are retiring.

**Q:** I am retiring a whole module, not a single function or class. Which API covers that, and how do callers get pointed at the replacement?

Three modes are available.

- **Mode 1, without `target=`:** The module's `__class__` becomes an intercepting wrapper. It delivers a `FutureWarning` on every public attribute access, including functions, classes, and constants already in `__dict__`.
- **Mode 2** (`target=new_module`) also forwards every missing-attribute lookup to the replacement module, optionally with `attrs_mapping` for per-name redirects. Use it when callers must be redirected to the new name transparently.
- **Mode 3** uses `deprecated_instance()` in the parent package `__init__.py` to expose the old module name as a deprecated attribute; no extra API is needed.

`num_warns=-1` is the default and delivers on every access; `0` delivers none and a positive `N` delivers exactly `N`. See the [module deprecation guide](guide/modules.md) for each mode with runnable examples.

### Does `deprecated_module()` warn when accessing real attributes (functions, classes, constants) on the deprecated module?

Yes, real attributes warn too. `deprecated_module()` changes the module's `__class__` to a wrapper overriding `__getattribute__`. Every public attribute access invokes the `FutureWarning` path, including attributes already in `__dict__`.

**Q:** My deprecated module still defines its functions and constants. Do callers that use those real attributes get the warning, or only callers of names that no longer exist?

Standard warning filters can suppress repeated display. A PEP 562 module-level `__getattr__` hook would intercept only missing names; `__getattribute__` intercepts all lookups. Star imports are also covered, as described below.

### Module deprecation: accessing a missing attribute raises AttributeError

Yes. Mode 2 (`target=new_module`) forwards missing attributes to the replacement module. Mode 1 has no forwarding target, so a missing name raises `AttributeError` after invoking the warning path.

**Q:** I called `deprecated_module()` in Mode 1 (no `target=`). Real attributes invoke the warning path correctly, but accessing a name that is not in the module raises `AttributeError` instead of just invoking that path. Is there a way to forward missing-attr lookups to a replacement module?

Mode 1 intercepts every public lookup through `__getattribute__`, including real and missing attributes. Existing attributes are returned after invoking the warning path. Standard warning filters can suppress repeated display.

If you want unknown names to invoke the warning path **and** resolve, switch to Mode 2:

```python
# phmdoctest:skip — CI template; new_calculator is not installed
# old_calculator.py — redirect missing-attr lookups to new_calculator
import new_calculator as _new_calculator
from deprecate import deprecated_module

deprecated_module(
    __name__,
    target=_new_calculator,
    deprecated_in="2.0",
    remove_in="3.0",
    message_template="Use `new_calculator` instead.",
)
# old_calculator.add(1, 2)  # invokes FutureWarning path + returns new_calculator.add(1, 2)
```

### Can a context copied during a module redirect resolve the same name later?

Yes. Redirect guards expire when the original lookup finishes, whether it returns a value or raises. Later work in a copied context can resolve the same name normally.

**Q:** A redirect target captures `contextvars.copy_context()` while resolving a missing attribute. Can later work in that context access the same deprecated attribute?

!!! warning "Active redirect cycles still fail"

    A cyclic redirect re-entering the same name during an active lookup on the same thread still raises `AttributeError`.

### Does `from old_calculator import *` still trigger the deprecation warning?

Yes, `from module import *` still invokes the warning path. CPython's `IMPORT_STAR` calls `getattr(module, name)` once for each imported public name. It uses `__all__` if present, otherwise all non-underscore names.

**Q:** I used `from old_calculator import *` after calling `deprecated_module()` on it. Does that star import emit a `FutureWarning`, or does it slip through silently?

Each call passes through `_DeprecatedModuleWrapper.__getattribute__`, like any other attribute access. Standard warning filters can suppress repeated display.

This holds for Mode 1 and Mode 2 of `deprecated_module()`. The `__class__` reassignment and `__getattribute__` interception close the star-import gap.

A PEP 562 module-level `__getattr__` hook would fire only for missing names. It would miss `IMPORT_STAR` lookups of existing names and leave them unwarned.

### Why does `num_warns=-1` still appear in profiling?

`-1` disables only budget accounting. Each public access still looks up module deprecation metadata and delivers the configured warning path; the standard warnings filter may suppress repeated display, but it still evaluates the warning. Use a finite `num_warns` when the warning delivery work itself must stop after a known number of accesses.

**Q:** I left `deprecated_module(..., num_warns=-1)` at its default. Why do repeated attribute accesses still show warning-related work, even though no warning-budget lock is taken?

### Why does `stream=None` not silence `deprecated_module`?

For `deprecated_module()`, explicit `stream=None` historically meant `warnings.warn`, whereas other entry points treat it as silence. It still delivers warnings today, with a `FutureWarning` announcing the change at the `deprecated_module(...)` call.

**Q:** I passed `stream=None` to `deprecated_module()` expecting silence, as with `@deprecated`, but the module warning still appears, and a `FutureWarning` now fires at the `deprecated_module(...)` call itself.

The default is now a separate sentinel, displayed as `<warnings.warn>` by `help()`. Omitting `stream` preserves current behavior without that notice.

!!! warning "Module defaults change in v1.0"

    In v1.0, `stream=None` silences module warnings and the `num_warns` default changes from `-1` to `1`.

- Omit `stream` to keep warning on access.
- Pass `num_warns=-1` explicitly if every access must keep warning after v1.0.
- To silence module deprecations today, use a warnings filter or a custom stream that discards the message.

### Why do pytest, Sphinx, or my IDE trigger warnings on a deprecated module?

That is expected in Mode 1 and Mode 2. `deprecated_module()` overrides the module's `__getattribute__`, so any public attribute probe invokes the warning path even when the access comes from tooling rather than your own code. Standard warning filters can suppress repeated display; custom streams can still observe every permitted delivery.

Test and documentation tools often call `getattr()`, inspect plugin metadata, or walk module attributes repeatedly during discovery.

**Q:** I only imported a deprecated module once, but pytest collection, Sphinx autodoc, or my IDE/linter triggers many `FutureWarning`s. Why?

If that noise is expected in tests, scope it in test config with `warnings.filterwarnings` instead of suppressing warnings globally:

```python
# tests/conftest.py
import warnings

warnings.filterwarnings("ignore", category=FutureWarning, message=r"The `old_module` module was deprecated")
```

Replace `old_module` with the deprecated module's import path. Escape dots in dotted paths, such as `pkg\.old_module`.

Filter by `message=`, rather than `module=`. The warning is attributed to the module accessing the deprecated one, so `module=r"^old_module$"` never matches.

The example matches the start of the built-in notice: "The `old_module` module was deprecated since v...". With a custom `message_template`, match the start of that text instead. This silences noisy test-collection introspection while keeping the warning visible elsewhere.

## Typing, IDEs and Sphinx

### `AttributeError: 'str' object has no attribute 'target'` after upgrading to v0.13

Since v0.13, `__deprecated__` holds a plain PEP 702-conformant message string instead of the internal `DeprecationConfig` object. This matches `warnings.deprecated` and `typing_extensions.deprecated`.

**Q:** Code that used to read `wrapper.__deprecated__.target` (or `.deprecated_in`, or checked `isinstance(wrapper.__deprecated__, DeprecationConfig)`) now raises `AttributeError: 'str' object has no attribute ...`. What changed?

Before v0.13, PEP-702-aware tooling received a `DeprecationConfig` repr where a message string belonged. The change fixes that collision for IDE hover, `pyright`, and `mypy`.

The config object moved to `__deprecation_config__`. Read it through the public `get_deprecation_config(obj)` helper rather than accessing either attribute directly.

The helper also supports legacy objects whose `__deprecated__` still holds a config. Migrated code therefore works across mixed-version installs, including objects built before v0.13.

```python
from deprecate import deprecated, get_deprecation_config


# NEW API — new_func is the replacement API.
def new_func(x: int) -> int:
    return x * 2


# DEPRECATED API — old_func forwards to new_func.
@deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
def old_func(x: int) -> int:  # warns: nothing — decoration only, no call yet
    pass


print(isinstance(old_func.__deprecated__, str))
print(get_deprecation_config(old_func).target is new_func)
```

<details>
  <summary>Output: <code>isinstance(...) and get_deprecation_config(...).target</code></summary>

```text
True
True
```

</details>

Affects every wrapper kind — functions, methods, class proxies (`deprecated_class`), instance proxies (`deprecated_instance`), and modules (`deprecated_module`). See the [v0.12-to-v0.13 migration guide](guide/migration.md) for the full list of affected reads.

### How do I type-annotate the object returned by `deprecated_class` / `deprecated_instance`?

A class alias is typed as the class it forwards to; an instance proxy is annotated with the public `DeprecationProxy`.

**Q:** What type do type checkers see for the result of `deprecated_class(...)` and `deprecated_instance(...)`, and what should I annotate against?

`deprecated_class` is typed to return its `target`. For example, `RetryConfig = deprecated_class(target=RetryPolicy, ...)(RetryPolicy)` has type `type[RetryPolicy]`. It needs no annotation, and `isinstance(x, RetryConfig)` narrows to `RetryPolicy`.

Without a class `target`, an alias using only `args_mapping` or `attrs_mapping` keeps the wrapped class's type.

Annotate `deprecated_instance` results with public `DeprecationProxy`, never private `_DeprecatedProxy`. Use `DeprecationProxy[T]` when calling the proxy produces a `T`:

```python
from dataclasses import dataclass
from deprecate import DeprecationProxy, deprecated_class, deprecated_instance


# NEW API — RetryPolicy is the replacement API.
@dataclass
class RetryPolicy:
    attempts: int


# class alias — typed `type[RetryPolicy]`, no annotation needed
# DEPRECATED API — RetryConfig forwards to RetryPolicy.
RetryConfig = deprecated_class(target=RetryPolicy, deprecated_in="1.4", remove_in="2.0")(RetryPolicy)


def default_policy() -> RetryPolicy:
    return RetryPolicy(3)


# instance proxy — annotated with the public protocol, so `legacy_default()` is typed `RetryPolicy`
legacy_default: DeprecationProxy[RetryPolicy] = deprecated_instance(
    default_policy, name="legacy_default", deprecated_in="1.4", remove_in="2.0"
)

print(isinstance(RetryConfig(3), RetryPolicy))  # warns: FutureWarning
print(legacy_default().attempts)  # warns: FutureWarning
```

<details>
  <summary>Output: <code>isinstance(RetryConfig(3), RetryPolicy); legacy_default().attempts</code></summary>

```
True
3
```

</details>

A class alias remains a proxy at runtime: `isinstance(RetryConfig, DeprecationProxy)` is `True`. Its proxy-only attributes, `__wrapped__` and `__deprecation_config__`, are invisible on `type[RetryPolicy]`.

Read them using `get_deprecation_config(RetryConfig)` or `inspect.unwrap(RetryConfig)`. Alternatively, use `cast(DeprecationProxy[Any], RetryConfig)` at the site needing them. See [Classes → Type annotations and static analysis](guide/classes.md#type-annotations-and-static-analysis) for other checks against aliases.

!!! warning "`issubclass` raises `TypeError`"

    `DeprecationProxy` declares attributes (`__wrapped__`, `__deprecated__`), which makes it a *data* protocol. Python supports only `isinstance` for those: `isinstance(obj, DeprecationProxy)` works. `issubclass(SomeType, DeprecationProxy)` raises `TypeError: Protocols with non-method members don't support issubclass()`.

    This is a CPython rule for every runtime-checkable protocol with non-method members, not a pyDeprecate restriction.

### mypy reports errors on deprecated class aliases after upgrading

Before `v0.14`, pyDeprecate shipped no type information, so type checkers treated its objects as `Any`. Since `v0.14`, the package ships `py.typed`. Deprecated class aliases now have the forwarded class's type.

**Q:** After upgrading pyDeprecate, mypy reports new errors around `deprecated_class` aliases that used to pass. What changed?

| Form                                                | Inferred type                            |
| --------------------------------------------------- | ---------------------------------------- |
| `deprecated_class(target=New, ...)(Cls)`            | `type[New]`                              |
| `deprecated_class(...)(Cls)` without a class target | `type[Cls]`                              |
| `@deprecated_class(...)` class statement in mypy    | The class statement's type               |
| `@deprecated_class(target=New, ...)` in pyright     | The decorator's return type, `type[New]` |

Common errors and fixes follow.

- **`[assignment]` on `Old: DeprecationProxy[New] = deprecated_class(target=New, ...)(Cls)`.** The alias is `type[New]`, not a proxy type. Drop the annotation; the alias already has the replacement's type.
- **`[attr-defined]` on `Old.__wrapped__` or `Old.__deprecation_config__`** — proxy-only attributes are invisible on the class type. Use `get_deprecation_config(Old)`, `inspect.unwrap(Old)`, or `cast(DeprecationProxy[Any], Old)`.
- **`[call-arg]` or `[attr-defined]` on an old keyword or attribute name.** `args_mapping` and `attrs_mapping` can accept names absent from the alias's class type. This matches the decorator form's existing diagnostics. Keep old names in a legacy class body, or cast the alias as `cast(DeprecationProxy[New], deprecated_class(...)(New))`.

!!! warning "Casting trades precision for compatibility"

    That cast makes calls and attributes permissive. However, `isinstance` against the alias no longer type-checks.

- **`[valid-type]` on `def f(x: Old)`, or `class Child(Old)` in mypy.** A call-form alias is a variable, which type checkers cannot accept as a type. Annotate or subclass the replacement class. In mypy, you can also use the decorator form, whose name is a class statement.

See [Classes → Type annotations and static analysis](guide/classes.md#type-annotations-and-static-analysis).

### Sphinx / my IDE shows `(*args, **kwargs)` instead of my deprecated class's real signature

Every proxy exposes `__wrapped__` and `__signature__` to reach the wrapped object and its signature. Reading either emits no deprecation warning, keeping documentation builds and IDE hovers quiet.

**Q:** Autodoc renders the proxy rather than the wrapped class, or `inspect.signature()` on a deprecated alias returns something useless. How do I reach the original?

| Attribute       | Points to                  | Consumed by                                                   |
| --------------- | -------------------------- | ------------------------------------------------------------- |
| `__wrapped__`   | the source class or object | `inspect.unwrap`, Sphinx autodoc, griffe/mkdocstrings, IDEs   |
| `__signature__` | the source's signature     | `inspect.signature`, and tools that do not walk `__wrapped__` |

```python
import inspect
from dataclasses import dataclass
from deprecate import deprecated_class


# NEW API — RetryPolicy is the replacement API.
@dataclass
class RetryPolicy:
    attempts: int
    backoff: float = 1.0


# DEPRECATED API — RetryConfig forwards to RetryPolicy.
@deprecated_class(target=RetryPolicy, deprecated_in="1.4", remove_in="2.0")
@dataclass
class RetryConfig:
    attempts: int
    backoff: float = 1.0


print(inspect.unwrap(RetryConfig).__name__)
print(str(inspect.signature(RetryConfig)))
```

<details>
  <summary>Output: <code>inspect.unwrap(RetryConfig).__name__; str(inspect.signature(RetryConfig))</code></summary>

```
RetryConfig
(attempts: int, backoff: float = 1.0) -> None
```

</details>

If a tool still renders the proxy, point it at `__wrapped__` explicitly.

!!! note "Some objects have no introspectable signature"

    Examples include a plain `dict` passed to `deprecated_instance()` and C-level types. In these cases, `__signature__` is `None`, rather than absent. Wrapping never fails because the source cannot be introspected.

### Why don't mypy, pyright, or my IDE flag functions deprecated with pyDeprecate?

Type checkers recognize deprecation only from a literal `warnings.deprecated` (Python 3.13+) or `typing_extensions.deprecated` decorator. They match its name, so runtime decorators, including pyDeprecate's, cannot supply this signal alone.

**Q:** My deprecated function warns at runtime, but the editor shows no strikethrough and `mypy` / `pyright` report nothing when I call it.

1. Stack the PEP 702 decorator directly above pyDeprecate's: `@warnings.deprecated("Use `new_name` instead.", category=None)` over `@deprecated(...)`.
2. Keep `category=None` to avoid two warnings per call.
3. Write the message as a string literal. Mypy silently ignores constants and f-strings.
4. Import the module instead of the name to avoid shadowing pyDeprecate's `deprecated`.
5. Enable `enable_error_code = ["deprecated"]` in mypy or `reportDeprecated = "error"` for pyright in CI.

Over `@deprecated_class(...)`, both mypy and pyright flag the old class name. See [Functions → Static type checkers](guide/functions.md#static-type-checkers-pep-702).

## Pickling and copying

### Does the deprecation survive `copy`, `deepcopy`, and `pickle`?

Yes for `copy.copy` and `copy.deepcopy`: the result remains a proxy with deprecation warnings. Warning counters are snapshotted at copy time, and each copy keeps an independent counter.

**Q:** I copied (or pickled and restored) an object wrapped by `deprecated_instance` / `deprecated_class` — does the deprecation still apply to the copy?

Copy operations are silent because the protocol methods rebuild the proxy directly without attribute access. Warnings begin when the copy is used.

!!! note "Pickling depends on the wrapped object"

    - `deprecated_instance` proxies over plain objects, including dicts, lists, and custom objects, are fully picklable.

    - A `deprecated_class` alias itself commonly raises `PicklingError` when its proxy replaces the original class's module-level name. Pickle cannot find the original class by reference.

    - Instances of a class deprecated in place pickle and load as instances of the original class. Neither step warns.

    - This includes exception and `OrderedDict` subclasses. An exception raised in a process-pool worker therefore still reaches the parent.

    - A class with its own `__reduce_ex__` keeps its own pickling. Members of an Enum deprecated in place therefore still raise `PicklingError`.

```python
import copy
import pickle

from deprecate import deprecated_instance

# NEW API — the canonical settings object going forward
NEW_LIMITS = {"max_retries": 3}

# DEPRECATED API — `OLD_LIMITS` kept as a deprecated alias during the migration window
OLD_LIMITS = deprecated_instance(NEW_LIMITS, name="OLD_LIMITS", deprecated_in="1.0", remove_in="2.0")

restored = pickle.loads(pickle.dumps(OLD_LIMITS))  # noqa: S301  # silent — pickling rebuilds the proxy, not a usage
duplicate = copy.deepcopy(OLD_LIMITS)  # silent

print(restored["max_retries"])  # warns: FutureWarning
print(duplicate["max_retries"])  # warns: FutureWarning — each copy has its own counter snapshotted from the original
```

<details><summary>Output: <code>restored["max_retries"]; duplicate["max_retries"]</code></summary>

```
3
3
```

</details>

### What do pickled instances of a class deprecated in place need to load?

The loading process needs pyDeprecate installed, and the class must remain deprecated in place when the payload loads.

**Q:** I pickle instances of a class deprecated in place (`@deprecated_class` with no replacement class) into a cache or a worker queue. What does the process that loads them need, and do the payloads survive upgrading pyDeprecate or removing the deprecation?

The class's module-level name holds the proxy, so the payload cannot name the class directly. Instead, it names a pyDeprecate rebuild helper together with the class's module and qualified name.

The helper is `deprecate.proxy._rebuild_class_instance`, `_rebuild_class_instance_ex`, or `_rebuild_class_call`.

- **Upgrading pyDeprecate is safe.** The helper names belong to the pickle compatibility contract and stay unchanged across releases, allowing payloads to load in later releases.

!!! warning "Older releases cannot load these payloads"

    pyDeprecate v0.14 and earlier lack these helpers and cannot load such payloads.

- **Removing the deprecation breaks loading.** The helpers rebuild only a class whose name a deprecation proxy holds. Once the deprecation is gone and the name points at the plain class again, loading raises `AttributeError: Cannot rebuild the pickled instance: ... is not a class deprecated in place`.

To migrate stored instances:

1. Load them while the deprecation remains in place.
2. In the migrating process, set the module attribute back to the real class using `inspect.unwrap(OldClass)`.
3. Pickle the loaded instances again.

!!! warning "Preserve the class's import identity"

    As with any Python pickle, do not rename or move the original class's module or qualified name without a compatibility alias.

`deprecated_instance` proxies over plain objects have no in-place-class requirement. They pickle the wrapped object itself.

### TypeError: `<obj>` is wrapped in a deprecation proxy and cannot be pickled directly

Versions before [PR #212](https://github.com/Borda/pyDeprecate/pull/212) raised this error. Since PR #212, `pickle.dumps(proxy)` reconstructs `deprecated_instance` proxies on unpickle with deprecation semantics preserved. Upgrade pyDeprecate if this error persists on such a proxy.

**Q:** I get `TypeError: ... is wrapped in a deprecation proxy and cannot be pickled directly` when passing a `deprecated_instance` proxy to `pickle.dumps`, `multiprocessing`, or `joblib`.

!!! warning "Pickling still has two caveats"

    - The `stream` callable must be picklable by reference. Replace lambdas or closures with `stream=None` or `stream=warnings.warn`.

    - A `deprecated_class` alias itself commonly raises `PicklingError` when its proxy replaces the decorated class name. Use `copy.deepcopy` for class aliases. Instances of classes deprecated in place pickle successfully.

## Audit, CLI and GitHub Action

### My expiry gate reports more expired wrappers after upgrading

`validate_deprecation_expiry` now includes deprecated class members by default (`include_members=True`), matching `find_deprecation_wrappers` and `generate_deprecation_table`. Expired member wrappers — for example an `__init__` carrying deprecation metadata — are now counted, so totals can rise after upgrading even though your code is unchanged. The newly reported entries are real expired deprecations that were previously invisible to the gate.

**Q:** After upgrading pyDeprecate, `validate_deprecation_expiry` (and `pydeprecate expiry`) suddenly reports more expired wrappers than before — nothing in my code changed. Why?

```python
from deprecate import validate_deprecation_expiry

# For testing purposes, we use the test module; normally you would import your own package
from tests.collections.api_deprecated import core as my_package

# Default scope now includes deprecated class members
expired = validate_deprecation_expiry(my_package, "0.5")
print(f"Found {len(expired)} expired")

# Restore the previous module-level-only scope
expired = validate_deprecation_expiry(my_package, "0.5", include_members=False)
print(f"Found {len(expired)} expired")
```

<details><summary>Output: <code>Found ... expired</code></summary>

```
Found 31 expired
Found 28 expired
```

</details>

Prefer fixing the newly surfaced entries over passing `include_members=False` — the narrower scope hides genuinely expired member deprecations. Every audit function takes the same `include_members` switch, and every CLI subcommand takes `--no-include-members` (for example `pydeprecate expiry src/mypackage --no-include-members`).

### Why does `pydeprecate all` report policy violations but still exit 0?

No. `all` treats policy violations as advisory by default because its policy defaults encode one release convention rather than a universal rule.

**Q:** `pydeprecate all src/mypackage` prints a *Deprecation Policy Violations* table, but the command exits `0` and my CI step passes. Is the exit code wrong?

The defaults are `min_grace="0.3"` and `message_required=True`. Violations are printed for visibility but do not contribute to `all`'s exit code unless you opt in.

| Version     | Make policy violations fail `all`                                                                 |
| ----------- | ------------------------------------------------------------------------------------------------- |
| Since v0.16 | Use `pydeprecate all --gate=policy src/mypackage` or `gate = ["policy"]` in `[tool.pydeprecate]`. |
| On v0.15    | Add a separate `policy` step, as shown below.                                                     |

The opt-in makes `all` exit `1` on policy violations from that same scan. Without it, these findings make `all` exit `1`:

- Invalid argument mappings make `all` exit `1`.
- Misconfigured targets make `all` exit `1`.
- Inner-order properties make `all` exit `1`.
- Deprecated-to-deprecated chains make `all` exit `1`.
- Expired wrappers make `all` exit `1`.

!!! warning "Policy configuration errors are never advisory"

    An unreadable or unparsable nearest `pyproject.toml`, or malformed `[tool.pydeprecate.policy]` value, still exits `2` inside `all`. The message is `Cannot read <path>: <err>`. This failure is checked before the chains check runs.

On v0.15, or to keep policy gating separate from `all`, give `pydeprecate policy` its own CI step.

| Exit code | Meaning                    |
| --------- | -------------------------- |
| `0`       | No violations.             |
| `1`       | Policy violations.         |
| `2`       | A malformed rule argument. |

```bash
# reports policy violations, but the exit code ignores them
pydeprecate all src/mypackage

# this is the step that fails the build
pydeprecate policy src/mypackage --min-grace=0.3 --message-required=True
```

Spell out the rule flags even when they match defaults. The command then records your project's promises. Future pyDeprecate default changes cannot silently change what CI enforces.

### How do I switch off a policy rule my project does not follow?

Both rules are independently controlled — pass `None` for `min_grace` or `False` for `message_required`. Nothing is all-or-nothing.

**Q:** My project removes deprecated code one minor after announcing it, so `pydeprecate policy` flags every wrapper with `[min-grace]`. I do not want to abandon the guidance rule to silence it.

Before disabling the window, consider retargeting it to your release cadence. `min_grace` is a version-shaped delta:

| Value             | Required warning window |
| ----------------- | ----------------------- |
| `"0.1"`           | One minor.              |
| `"0.3"` (default) | Three minors.           |
| `"1.0"`           | One major.              |

Choose your project's actual cadence to retain the gate. Disabling it leaves removal schedules unchecked. A `0.x` project needs no special setting: a bump to `1.0` is a major step and clears any window.

Here is the difference between the two:

```python
from deprecate import validate_deprecation_policy

# For testing purposes, we use the test module; normally you would import your own package
from tests.collections import policy as my_package

# Relax the window: one minor of warning is enough.
# The fixtures still trip it — one removes in the same release, one at a patch off any boundary.
violations = validate_deprecation_policy(my_package, recursive=False, min_grace="0.1")
print(f"Found {len(violations)} violations")

# Or drop the rule entirely and keep the guidance rule
violations = validate_deprecation_policy(my_package, recursive=False, min_grace=None)
print(f"Found {len(violations)} violations")
```

<details><summary>Output: <code>f"Found {len(violations)} violations"</code></summary>

```
Found 6 violations
Found 4 violations
```

</details>

The CLI exposes one flag per rule: `--min-grace=0.1`, `--min-grace=None`, and `--message-required=False`.

An unrecognized value is rejected before scanning with exit `2`. The message names accepted spellings, so a typo cannot silently disable a rule.

### Where does `pydeprecate policy` take its rules from when I pass no flags?

Each rule resolves independently: command-line flag, then `[tool.pydeprecate.policy]` in `pyproject.toml`, then built-in default. The defaults are `min-grace = "0.3"` and `message-required = true`.

**Q:** I run a bare `pydeprecate policy src/mypackage` in CI and locally. Which rules apply, and can the project declare them once instead of every invocation repeating `--min-grace` and `--message-required`?

Declare your project's convention once:

```toml
[tool.pydeprecate.policy]
min-grace = "0.3"  # quoted — a TOML float 0.10 reads as 0.1; false switches the rule off
message-required = true
```

Configuration discovery follows the same search as `--version` auto-detection:

1. Start at the scanned path's directory.
2. Search up to two parents.
3. Use the nearest table.

Bare module names never trigger this search. Running from an unrelated checkout therefore cannot adopt its policy.

Every run prints a `Policy:` header identifying each value's source: `flag`, `pyproject.toml`, or `built-in`.

!!! warning "Configuration failures and unknown keys"

    - Malformed values, unreadable files, and unparsable `pyproject.toml` files exit `2` with `Cannot read <path>: <err>`.

    - Unknown keys, such as `min_grace` with an underscore, are reported on stderr and ignored. They are never silently enforced.

    - On Python 3.9–3.10, the parser is `tomli` from the `[audit]` extra. Without it, a reachable `pyproject.toml` triggers an advisory instead of being skipped silently.

`pydeprecate all` uses these resolved settings for its advisory policy pass. Configuration-read failures still propagate as exit `2`, checked before `chains`, even though violation counts are advisory.

!!! note "The Python API does not read configuration files"

    `validate_deprecation_policy()` accepts a module object or importable name, never a filesystem path. Pass `min_grace` and `message_required` explicitly.

### How do I keep test fixtures or a legacy tree out of the audit?

Yes. Every scanning function and CLI subcommand accepts an `exclude` list of glob patterns matched against full dotted module names.

**Q:** My package ships `my_package.tests` (or a frozen `my_package._legacy` tree) with deliberately odd `@deprecated` wrappers. `pydeprecate all` reports them, and importing the fixtures is slow. Can the scan skip them?

For project configuration, use:

```toml
[tool.pydeprecate]
exclude = ["my_package.tests", "*._legacy*"]
```

You can also pass exclusions directly:

- For the CLI, run `pydeprecate all src/my_package --exclude='my_package.tests,*._legacy*'`. Quote the value so the shell does not expand `*`.
- In Python, call `find_deprecation_wrappers(my_package, exclude=["my_package.tests"])`.

A pattern matching a package excludes its whole subtree. The scan itself never imports excluded packages. A fixture raising at import time therefore neither slows the scan nor produces an `audit: skipped` warning.

!!! warning "Imports elsewhere still run"

    If other code in your package imports an excluded package, that import happens normally. Only the wrappers are filtered out.

The header `Exclude: my_package.tests (pyproject.toml)` identifies the exclusions and their source.

- Use full names such as `my_package.tests`. Bare `tests` matches only a top-level module with that name.
- A value other than a string or list of strings exits `2`.

### UserWarning: `audit: skipped <module>` during a recursive scan

Recursive scans import every submodule and run its module-level code. If any submodule raises during import, the scanner warns and continues with the remaining modules. This includes exceptions beyond `ImportError`.

**Q:** A recursive audit scan (`find_deprecation_wrappers`, `validate_deprecation_expiry`, `pydeprecate check` / `all`) emits `UserWarning: audit: skipped <module>: <exception>`. What does it mean?

```
UserWarning: audit: skipped my_package.legacy_ext: RuntimeError('optional native extension not built')
```

Results cover every successfully imported module; only the named submodule is missing. Investigate and fix that submodule, or accept the gap if expected, such as for an optional dependency.

!!! warning "Skipped modules remain unaudited"

    A broken submodule no longer aborts the gate, but its deprecations remain unaudited until it imports cleanly.

The CLI also prints `pydeprecate gap: module not audited: <module>: <exception>` on stderr for each skipped submodule. This line appears even if your package's warnings filter hides the `UserWarning`.

### My pydeprecate pre-commit hook passes but misses deprecations

The remote hook (`repo: https://github.com/Borda/pyDeprecate`) runs in an isolated pre-commit environment. It contains pyDeprecate and its CLI dependencies, but lacks your package's runtime dependencies.

**Q:** I added the `pydeprecate` pre-commit hook and it always passes, but `pydeprecate check` in my virtual environment reports problems. Why?

The scan imports your package. Submodules with missing dependencies are skipped with `UserWarning: audit: skipped <module>`. Pre-commit hides output from passing hooks.

Choose a setup matching your audit needs:

- Run a local hook (`repo: local`, `language: system`, `entry: pydeprecate check`) so the scan uses your development environment — the recommended setup for any package with runtime dependencies.
- **Use the remote hook with dependencies.** List your package's dependencies in `additional_dependencies`. This replaces the hook's own list, so start with `"pyDeprecate[cli,audit]"` and append yours.

!!! warning "Retain the hook's extras"

    Pip resolves the `cli` and `audit` extras to the hook's own checkout. Without `audit`, missing `packaging` causes `expiry` and `min-grace` checks to warn and skip instead of failing. Without `tomli` on Python 3.9–3.10, `[tool.pydeprecate]` settings are ignored.

- For removal deadlines and policy rules only, use the import-free `pydeprecate-expiry-static` and `pydeprecate-policy-static` hooks, which need none of your dependencies (from `v0.16.0`).

The [audit guide](guide/ci-integration.md#pre-commit-integration) contains the hook recipes.

### Can pydeprecate audit a package whose dependencies are not installed?

Yes, for removal deadlines and policy rules. From `v0.16.0`, `pydeprecate expiry --static` and `pydeprecate policy --static` read source with `ast` without importing your package.

**Q:** Can pydeprecate audit a package whose dependencies are not installed?

The `pydeprecate-expiry-static` and `pydeprecate-policy-static` pre-commit hooks use the same approach. Neither runtime dependencies nor your package's import-time code run.

Recognized wrappers can be checked when the required settings are statically resolvable. Supported settings include literals, module-level names bound once to literals, and values splatted from a module-level dict literal.

!!! warning "Static coverage has limits"

    A recognized wrapper with unresolved settings prints `pydeprecate gap: not statically resolvable: <file>:<line>: <reason>`. This does not change the exit code unless strict mode is enabled. Read the gaps and spell settings literally where gating matters.

    These forms are invisible and produce no gap:

    - Wrappers built in loops are invisible.

    - Wrappers built through `getattr`, `importlib`, or `exec` are invisible.

    - Wrappers imported from outside the scanned package are invisible.

`check`, `chains`, `all`, and `status` need imported wrappers and have no static mode. See [Static mode](guide/cli.md#static-mode). For reusable Python rows, see the [Python static API](#how-do-i-audit-deprecations-without-importing-my-package).

### How do I audit deprecations without importing my package?

Call `deprecate.audit.find_deprecation_wrappers_static(path)` (since v0.16) to read source with `ast` without importing or executing the scanned package.

Pass its frozen `StaticScanResult` directly to `validate_deprecation_expiry`, `validate_deprecation_policy`, or `generate_deprecation_table`.

!!! warning "Inspect gaps and avoid stand-in targets"

    Always inspect `.gaps` for incomplete coverage, even when `.wrappers` is empty. Never call a row's stand-in target: it raises `TypeError`.

This suits missing dependencies, docs builds, and isolated audit scripts. Supply `current_version` to expiry when `.version` is `None`; otherwise expiry raises `ImportError`. The result shares the [CLI `--static` blind spots](guide/cli.md#static-mode): dynamic forms can remain invisible without a gap.

See the [runnable Python example](guide/audit.md#scanning-from-python-without-importing) and [limitations](limitations.md#audit-tooling).

### My pydeprecate pre-commit hook fails: contains multiple packages

Without a path in `args`, the hook searches for a single package under `src/`, then at the repository root. A root containing multiple top-level packages, such as `mypkg/` and `tests/`, is ambiguous. pyDeprecate stops with an error instead of scanning nothing and passing.

**Q:** The `pydeprecate` pre-commit hook fails with `contains multiple packages (mypkg, tests)` and a non-zero exit. What should I change?

Src-layout repositories and single-package roots resolve automatically. For multi-package repositories, name the package in the hook's `args`:

```yaml
- id: pydeprecate-check
  args: [mypkg]
```

The same error, a usage error with exit `2`, applies to every subcommand; see the [audit guide](guide/ci-integration.md#pre-commit-integration).

### `pydeprecate gap: other copy audited`

The scan checked the copy named in the message, which may differ from the directory you passed. The `Imported from:` header identifies the file actually audited.

**Q:** The CLI prints `pydeprecate gap: other copy audited: <pkg> was imported from <dir>, not from <scanned dir>`. Were my deprecations checked?

Packages are imported by name. When the CLI process already has that name loaded, the scan uses the loaded copy.

This can happen when:

- You scan a pyDeprecate fork or checkout while the CLI already uses its own `deprecate` package.
- An editable install from another checkout resolves ahead of the requested directory on `sys.path`.

If the copies contain identical code, the result remains valid. For example, CI can install a wheel built from the checkout, then scan `src/`.

Otherwise, run the scan from the checkout's own virtual environment. Ensure the package name resolves to the directory you pass.

### Why does my CI step exit 3?

The audit did not run. Exit `3` represents a scan or internal error, rather than findings (exit `1`) or a usage error (exit `2`).

**Q:** `pydeprecate check src/mypackage` (or the GitHub Action) fails with exit code `3` and `pydeprecate: scan error: …` or `pydeprecate: internal error: …`. What does it mean?

| Error          | Cause and action                                                                                                                                                    |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Scan error     | The scanned package's `__init__` or named module raised during import, or its module-level code called `sys.exit()`. Install its runtime dependencies as for tests. |
| Internal error | A pyDeprecate bug occurred. The message names the exception and issue tracker for reporting it.                                                                     |

!!! note "A failed submodule is a gap"

    A submodule import failure produces `pydeprecate gap: module not audited`. It preserves the exit code unless strict mode is enabled.

`--exit-zero` does not mask exit `3`, and no `--report-json` file is written. Before v0.15, both error types exited `1`, like findings.

### Why does `pydeprecate check` now fail on a wrapper it used to pass?

Since v0.15, misconfigured targets and inner-order properties are errors, as the CLI guide always promised. The scan exposes them through `misconfigured_target` and `inner_order_property` in `find_deprecation_wrappers`.

**Q:** After upgrading, `pydeprecate check` (and `all`) exits `1` with *Found deprecated wrappers with a misconfigured target* or *Found inner-order @property @deprecated wrappers*, on code that passed before.

| Misconfiguration                                                          | Effect                                                                         | Fix                                                                                       |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| `TargetMode.NOTIFY` with `args_mapping`, `attrs_mapping`, or `args_extra` | The mode ignores the mapping or extras. Old argument names are never migrated. | Use `TargetMode.ARGS_REMAP` for argument renames, or drop the mapping for a plain notice. |
| `TargetMode.ARGS_REMAP` without `args_mapping`                            | There is nothing to remap.                                                     | Supply a mapping or use a plain notice.                                                   |
| `target=False`                                                            | The target is invalid.                                                         | Choose the appropriate supported target.                                                  |
| `@property` above `@deprecated`                                           | Only the getter warns; setters and deleters added later stay silent.           | Put `@deprecated(...)` above `@property`.                                                 |

### How do I accept one known expired wrapper without excluding its module?

Accept just that finding with `--ignore='expired:mypackage.module:old_fn'`. Alternatively, list the entry under `ignore = [...]` in `[tool.pydeprecate]` (since v0.16).

**Q:** `pydeprecate expiry` (or `all`) fails on a wrapper we deliberately keep one more release, and `--exclude` would hide every other wrapper of that module too.

| Entry form           | Effect                                                        |
| -------------------- | ------------------------------------------------------------- |
| `rule:module:symbol` | Accepts matching findings, with `fnmatch` globs in each part. |
| `module:symbol`      | Accepts every rule for that symbol.                           |

To accept a whole backlog:

1. Write a report with `--report-json`.
2. Pass that report to later runs with `--baseline`. Only newly added findings fail.

Suppressed findings do not fail the gate. Every run prints their count, and reports list them. They remain visible until fixed.

### How do I make the CI gate fail when part of the audit did not run?

Pass `--strict` (since v0.16), set `strict = true` under `[tool.pydeprecate]`, or use `fail-on-gap: true` in the GitHub Action. Any recorded gap then makes the run exit `1`, even without findings.

**Q:** `pydeprecate all src/mypackage` prints `pydeprecate gap: module not audited: …` or `pydeprecate gap: version not resolved: …` and still exits `0`. How do I make such a run fail?

A strict run prints `Strict mode: N gap(s) fail this run.` after its gap lines. Without strict mode, gaps are advisory: they identify unaudited parts, while the exit code reflects completed checks.

Fix the cause named by the gap's hint. Usually this means installing a missing runtime dependency or passing a version with `--version`. Use `--exclude` for modules you never want audited.

| Setting                                         | Effect                                                 |
| ----------------------------------------------- | ------------------------------------------------------ |
| Explicit `--strict` with `--exit-zero`          | Rejected with exit `2`.                                |
| `--exit-zero` alone or Action `exit-zero: true` | Disables configured `strict = true` for that run.      |
| `--no-strict`                                   | Disables configured strict mode without `--exit-zero`. |

!!! warning "Configured strict mode needs a TOML parser"

    Python 3.11+ includes one. On Python 3.9 and 3.10, install `[audit]` for `tomli`, or pass `--strict` explicitly.

    Without a parser, configured `strict = true` is off. Only a stderr advisory is printed, and gaps do not fail the run.

### Why does the GitHub Action say the report is not of layout version 1?

The action runner only reads the report layout its own CLI writes: layout 1 since v0.15. A different layout usually means `install: false` selected another pyDeprecate version.

**Q:** The pyDeprecate GitHub Action warns `the pyDeprecate report is not of layout version 1; ignoring it` and adds no gap annotations or job summary, although the CLI ran.

The step still uses the CLI's exit code, so the gate result remains correct. Only annotations and the summary are skipped.

- Pin the action to the installed release, such as `uses: Borda/pyDeprecate@v0.16.0`.
- Alternatively, keep `install: true` so the action installs the CLI from its own ref.

### Why does a pyDeprecate annotation show no file or line?

The CLI cannot name a source location for every finding. Objects without a defining line include `deprecated_instance` data values, runtime-built classes, and code compiled from strings.

**Q:** The GitHub Action's annotation for a finding appears on the run summary instead of on a line of the pull request.

Static scans also cannot locate wrappers for objects defined in another module or bound more than once.

Since v0.16, a finding with a location uses the file and line of its wrapper's first decorator. A deprecated module is annotated on its file without a line.

!!! note "Only checkout paths match pull requests"

    The repository root is the nearest directory holding `.git` at or above the job's working directory. Files outside it use absolute paths, which GitHub cannot match to the pull request. Scan the package in the checkout instead of an installed copy.

### Why did the GitHub Action skip my job summary?

The summary is optional and never changes the gate's exit code. It uses the table from `--report-json`, which the CLI writes only after a completed scan with exit `0` or `1`.

**Q:** The action ran with `summary: true`, but the job summary is absent. It may warn that the gate wrote no pyDeprecate report, that no report is available, or show another summary warning.

A usage error (exit `2`) or crashed scan (exit `3`, such as a package import failure) produces neither a summary nor a summary warning. No scan finished; the step's error annotation names the exit code.

| Runner warning                                                           | Meaning and action                                                                                                                             |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| "The gate wrote no pyDeprecate report"                                   | Report writing failed. Read the CLI's stderr immediately above the warning.                                                                    |
| "No pyDeprecate report is available"                                     | With `install: false`, an older pyDeprecate may lack `--report-json`. Keep `install: true` or upgrade the installed version.                   |
| "No pyDeprecate report is available" after a temporary-directory warning | The runner could not create its report directory. The preceding warning gives the OS error.                                                    |
| "`summary: true` needs GITHUB_STEP_SUMMARY"                              | The job is not on a GitHub runner.                                                                                                             |
| "could not write the job summary"                                        | The warning includes the OS error.                                                                                                             |
| "The gate's report holds no deprecation table"                           | CLI table rendering failed. Its stderr explains why.                                                                                           |
| "Could not read the pyDeprecate report" or "not of layout version"       | The report is corrupt or from another pyDeprecate version, for example through `install: false`. The preceding runner warning names the cause. |

Report-reading and layout warnings do not change the gate result.

## Fatal mode

### My deprecated function raises `DeprecatedError` instead of warning

Something switched the deprecation to fatal. Check the wrapper, active exception scope, process-wide setting, and environment variable in that order.

**Q:** A call that used to emit a `FutureWarning` now raises `deprecate.DeprecatedError`. Nothing in my code changed.

1. Read the wrapper's `as_exception=True` setting on its `@deprecated`, `deprecated_class`, `deprecated_instance`, or `deprecated_module` call.
2. Look for an enclosing `with deprecate.as_exceptions():` in the same thread or task.
3. Check `deprecate.AS_EXCEPTIONS`. Application bootstrap code or a test fixture may set this process-wide attribute.
4. Check `DEPRECATE_AS_EXCEPTIONS`. At import, values `1`, `true`, `yes`, or `on` seed that attribute.

!!! note "Fatal precedence is monotonic"

    A wrapper's `as_exception=False` cannot escape a `True` global. This prevents a wrapper flag from opting out of strict CI. Only the consumer's `as_exceptions(False)` scope or `AS_EXCEPTIONS` setting can override that global.

To find the source at the failing call site:

1. Print `deprecate.AS_EXCEPTIONS` and `os.environ.get("DEPRECATE_AS_EXCEPTIONS")`.
2. Read `get_deprecation_config(the_wrapper).as_exception` for the wrapper's value.
3. If all three read `False`, inspect the traceback's call stack for `with as_exceptions():` or `@as_exceptions()`.

An open `as_exceptions()` scope never appears in `deprecate.AS_EXCEPTIONS`.

### Why does `as_exception` ignore `num_warns`, and raise even with `stream=None`?

Both behaviors are intended. Fatal deprecations bypass `num_warns` and raise on every call, leaving warning counters untouched. `stream=None` silences messages but does not disable the fatal lifecycle state.

**Q:** I set `num_warns=1` expecting one exception, or `stream=None` expecting silence, but every call to my fatal deprecation still raises.

The budget controls messages. If a fatal gate stopped after one call, a caller could retry inside `except DeprecatedError` and pass straight through.

Changing the stream affects presentation, so it cannot turn off a CI gate. `skip_if` does suppress fatal deprecations: while the wrapper is disabled, it is not a deprecation.

!!! warning "Fatal modules stop attribute-walking tools"

    A fatal `deprecated_module` raises on every public attribute access. This includes probes by pytest collection, Sphinx autodoc, `pickle`, and `copy`. These tools stop at the module instead of skipping it.

pyDeprecate's own audit remains unaffected. `find_deprecation_wrappers()` and the CLI read the module's `__dict__` directly. Fatal modules are therefore still discovered and reported normally.

## Still stuck?

!!! question "Open a GitHub issue"

    If none of the above covers your situation, open an issue on [GitHub Issues](https://github.com/Borda/pyDeprecate/issues). Include the full traceback, the decorator call you used, and the Python and pyDeprecate versions (`pip show pyDeprecate`).
