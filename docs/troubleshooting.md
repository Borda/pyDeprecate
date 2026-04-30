---
id: troubleshooting
description: 'Fix common pyDeprecate errors: missing deprecation notices, TypeError mapping failures, class deprecation notices, bool return errors, cross-module path issues, proxy limitations on primitives, and redirecting deprecation output to a logger.'
---

# Troubleshooting

This page covers the most common problems encountered when using pyDeprecate, with direct answers and corrected code for each. If your issue is not listed here, see the "Still stuck?" section at the bottom.

## UserWarning when decorating a class

**Q:** I applied `@deprecated` directly to a class and got `UserWarning: Direct use of @deprecated on class MyClass is deprecated since v0.6.0. Use @deprecated_class(...) instead. This will become a TypeError in a future release.` Why, and how do I fix it?

**A:** Use `@deprecated_class()` for classes. The `@deprecated` decorator is designed for functions and methods only.

That warning is triggered specifically when `@deprecated` is applied directly to a class. This still works today because pyDeprecate delegates to `@deprecated_class()` under the hood, but that delegation path is itself deprecated and will become a `TypeError` in a future release. The warning is telling you to switch to the explicit class API now.

!!! danger "This delegation will become a TypeError in a future release"

    The implicit fallback from `@deprecated` to `@deprecated_class()` is a temporary compatibility shim. Once it is removed, applying `@deprecated` to a class will raise `TypeError` immediately at decoration time. Migrate now to avoid a hard break on upgrade.

There are two supported alternatives depending on what you need. Use `@deprecated_class()` when you want to deprecate the class name itself (including Enums and dataclasses). Use `@deprecated` on `__init__` when you want to emit a deprecation notice only at instantiation time while keeping the class name in place.

```python
from deprecate import TargetMode, deprecated_class
from enum import Enum


# Correct: use @deprecated_class for classes
@deprecated_class(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
class MyClass:
    pass


@deprecated_class(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
class MyEnum(Enum):
    A = 1
    B = 2


# Alternative: decorate __init__ to warn at instantiation while keeping the class name
from deprecate import TargetMode, deprecated


class MyClass:
    @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
    def __init__(self, x: int) -> None:
        self.x = x  # body still executes; warning fires on every new MyClass(...)
```

## Upgrading from `@deprecated` on a class to `@deprecated_class`

**Q:** My codebase used an older version of pyDeprecate that applied `@deprecated` directly to a class. It behaved strangely — `isinstance()` checks failed, subclassing broke, and class attributes were inaccessible. What went wrong, and how do I migrate?

**A:** Before v0.6.0, applying `@deprecated` directly to a class replaced the class object with a plain wrapper function. Python's `isinstance`, `issubclass`, and attribute lookup all operate on the class type — so replacing the class with a function silently broke every downstream use that depended on the class being a type.

!!! failure "The old pattern silently broke isinstance, issubclass, and attribute access"

    Before v0.6.0, `@deprecated` on a class replaced the class with a plain function. All type checks, subclassing, and class attribute access failed silently or raised `TypeError`. If you see this pattern in your codebase, migrate to `@deprecated_class()` immediately.

Symptoms of the old behaviour:

```python
# phmdoctest:skip
# --- Old broken pattern (pre-v0.6) ---
from deprecate import deprecated


class NewClass:
    pass


@deprecated(target=NewClass, deprecated_in="1.0", remove_in="2.0")
class OldClass:
    class_attr = 42


obj = OldClass()
isinstance(obj, OldClass)  # TypeError or False — OldClass is now a function
issubclass(OldClass, object)  # TypeError — same reason
OldClass.class_attr  # AttributeError — wrapper function has no class attributes
```

The replacement is `@deprecated_class()`, which wraps the class in a `_DeprecatedProxy`. The proxy forwards all attribute access, item access, calls, and type checks to the target class — so `isinstance` and `issubclass` work correctly, class attributes remain accessible, and existing subclasses continue to work.

```python
from deprecate import deprecated_class


class NewCalculator:
    def add(self, a: int, b: int) -> int:
        return a + b


@deprecated_class(target=NewCalculator, deprecated_in="1.0", remove_in="2.0")
class OldCalculator:
    pass


obj = OldCalculator()
print(isinstance(obj, NewCalculator))  # True — proxy forwards isinstance checks
print(issubclass(OldCalculator, object))  # True — type checks pass through
print(obj.add(1, 2))  # 3 — forwarded to NewCalculator
```

<details>
  <summary>Output: <code>print(obj.add(1, 2)</code></summary>

```
True
True
3
```

</details>

The same rule applies to Enums and dataclasses — `@deprecated_class` is the correct API, not `@deprecated`:

```python
from enum import Enum
from deprecate import deprecated_class


class Color(Enum):
    RED = 1
    BLUE = 2


OldColor = deprecated_class(target=Color, deprecated_in="1.5", remove_in="2.0")(Color)

print(OldColor.RED is Color.RED)  # True
print(OldColor(1) is Color.RED)  # True
print(OldColor["RED"] is Color.RED)  # True
```

<details>
  <summary>Output: <code>print(OldColor["RED"] is Color.RED)</code></summary>

```
True
True
True
```

</details>

If you need to emit a deprecation notice only at instantiation time without deprecating the class name itself, decorate `__init__` instead — this keeps the class object intact and `isinstance`/`issubclass` unaffected:

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
  <summary>Output: <code>print(isinstance(svc, MyService)</code></summary>

```
True
```

</details>

## TypeError: Failed mapping

**Q:** I get `TypeError: Failed mapping of 'my_func', arguments not accepted by target: ['old_arg']`. What does this mean?

**A:** Your deprecated function passes an argument that the target function does not accept. You need to either drop the argument, rename it to match the target's signature, or use `TargetMode.ARGS_REMAP` for in-place remapping.

The error fires at call time because pyDeprecate prepares the forwarded call from the deprecated source and validates those arguments against the target's signature. If one or more mapped names are still not accepted by the target, it raises `TypeError: Failed mapping of '{source}', arguments not accepted by target: [...]`. When the target accepts `*args`, the message uses a slightly different variant, but it still indicates that the mapped arguments could not be accepted by the target.

Choose the fix that matches your situation:

**Option 1 — Drop the argument** (it is no longer needed by the target):

```python
# define a target that ignores the extra arg
def new_func(required_arg: int, **kwargs) -> int:
    return required_arg * 2


# ---------------------------

from deprecate import deprecated


# None means skip this argument
@deprecated(target=new_func, args_mapping={"old_arg": None})
def old_func(old_arg: int, new_arg: int) -> int:
    pass
```

**Option 2 — Rename the argument** (the target uses a different parameter name):

```python
def new_func(new_name: int) -> int:
    return new_name * 2


# ---------------------------

from deprecate import deprecated


# Map old to new
@deprecated(target=new_func, args_mapping={"old_name": "new_name"})
def old_func(old_name: int) -> int:
    pass
```

**Option 3 — Use `TargetMode.ARGS_REMAP`** (deprecating an argument of the same function, not forwarding to a different one):

```python
from deprecate import TargetMode, deprecated


# Deprecate within same function
@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_arg": "new_arg"})
def my_func(old_arg: int = 0, new_arg: int = 0) -> int:
    return new_arg * 2
```

## TypeError: skip_if function must return bool

**Q:** I see `TypeError: User function 'skip_if' shall return bool, but got: <type>`. What is wrong with my `skip_if` callable?

**A:** The callable passed to `skip_if` must return a `bool`. If it returns any other type — including a truthy int or a string — pyDeprecate raises `TypeError("User function 'skip_if' shall return bool, but got: ...")`.

pyDeprecate enforces the return type strictly so that the conditional skip behaviour is unambiguous. The error message refers to `skip_if` itself, not the name of your callback. Wrap any non-bool expression in an explicit `bool()` call, or use a `lambda` that returns a literal `True` or `False`.

```python
# Minimal replacement function for examples
def new_func() -> str:
    return "Hi!"


# ---------------------------

from deprecate import deprecated


# Correct: function returns bool
def should_skip() -> bool:
    return False  # replace with your condition


@deprecated(target=new_func, skip_if=should_skip)
def old_func1():
    pass


# Also correct: use a lambda
@deprecated(target=new_func, skip_if=lambda: False)
def old_func2():
    pass
```

## Deprecation notice not appearing

**Q:** I call my deprecated function but no deprecation notice is printed. Where did it go?

**A:** By default, pyDeprecate emits the deprecation message only once per function (`num_warns=1`) to avoid log spam. After the first call, subsequent calls are silent. Set `num_warns=-1` for unlimited emissions or `num_warns=N` for exactly `N` emissions.

For per-argument deprecation (when using `args_mapping` with `TargetMode.ARGS_REMAP`), each deprecated argument has its own independent message counter — so deprecation messages for different arguments are tracked separately and each fires once by default.

```python
# Minimal replacement function for examples
def new_func(x: int) -> int:
    return x * 2


# ---------------------------

from deprecate import deprecated


# Show warning every time
@deprecated(target=new_func, num_warns=-1)  # -1 means unlimited
def old_func_always_warn():
    pass


# Show warning N times total
@deprecated(target=new_func, num_warns=5)  # Show 5 times
def old_func_warn_n_times():
    pass
```

If you are writing tests and need to verify that a warning fires, use `pytest.warns(FutureWarning)` on the first call and `assert_no_warnings(FutureWarning)` on subsequent calls. See [Testing Deprecated Code](guide/audit.md#testing-deprecated-code) for full examples.

## Deprecation target path incorrect across modules

**Q:** I moved a function to a different module and the deprecation message shows an unexpected path. How do I fix the displayed module path?

**A:** The deprecation message reports the fully-qualified path of the target callable as Python resolves it at decoration time. Ensure the target is imported from its canonical location before the `@deprecated` decorator is applied.

When moving functions across modules, import the target from its new home explicitly rather than relying on a re-export alias. The path shown in the deprecation message will then reflect the module where the function actually lives, giving callers accurate migration information. The message will correctly show the full path for real imports when used in your package.

## Why does `deprecated_instance` not emit a notice on arithmetic/comparison operators?

**Q:** I wrapped a `float` constant with `deprecated_instance` but operations like `old_value + 1` or `old_value > 0` do not emit any deprecation notice. Why?

**A:** Python's data model invokes special ("dunder") methods like `__add__`, `__lt__`, `__mul__`, etc. directly on the object's type, bypassing `__getattr__`. The `_DeprecatedProxy` class implements `__getattr__` to intercept attribute access, but CPython does not call `__getattr__` for implicit protocol method lookups (it goes through the class's MRO directly). Since `_DeprecatedProxy` does not define every possible arithmetic/comparison dunder, these operations fall through to the default behaviour or raise `TypeError` — without emitting a deprecation notice.

The proxy does intercept:

- Attribute access (`obj.name`) via `__getattr__`
- Subscript access (`obj[key]`) via `__getitem__`
- Iteration (`for x in obj`) via `__iter__`
- Calling (`obj(...)`) via `__call__`
- Equality (`obj == other`) via `__eq__`
- Boolean truth (`if obj`) via `__bool__`
- String representation (`str(obj)`, `repr(obj)`) via `__str__`/`__repr__`

It does **not** intercept:

- Arithmetic operators (`+`, `-`, `*`, `/`, `//`, `**`, `%`)
- Comparison operators (`<`, `>`, `<=`, `>=`) other than equality
- Bitwise operators (`&`, `|`, `^`, `~`, `<<`, `>>`)
- Unary operators (`-obj`, `+obj`, `abs(obj)`)

!!! bug "Known limitation: proxy cannot intercept dunder protocol methods"

    This is a fundamental CPython constraint, not a pyDeprecate bug. Wrapping primitives (`int`, `float`, `str`) in `deprecated_instance` will not emit notices for arithmetic, comparison, or bitwise operations. See the workarounds below.

**Workarounds for primitive constants:**

1. **Wrap in a container** — put the value in a dict or dataclass so access goes through `__getitem__` or `__getattr__`:

```python
from deprecate import deprecated_instance

# Instead of: OLD_THRESHOLD = deprecated_instance(0.5, ...)
# Use a container:
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
  <summary>Output: <code>print(OLD_THRESHOLD["value"])</code></summary>

```
0.5
```

</details>

1. **Update call sites directly** — for simple numeric or string constants that are used in expressions, it is often simpler to rename the constant and update references rather than wrapping in a proxy:

```python
# Just rename and grep-replace call sites:
NEW_THRESHOLD = 0.5  # new name
# OLD_THRESHOLD = 0.5  # remove after migration
```

1. **Use a deprecated function wrapper** — if you need deprecation notices on read access to a bare value, expose it through a function that you can decorate:

```python
from deprecate import TargetMode, deprecated


@deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
def get_old_threshold() -> float:
    """Use NEW_THRESHOLD constant directly instead."""
    return 0.5


# Callers get a warning when they call get_old_threshold()
print(get_old_threshold())
```

<details>
  <summary>Output: <code>print(get_old_threshold()</code></summary>

```
0.5
```

</details>

## How do I redirect deprecation output to a logger instead of `warnings.warn`?

**Q:** I want deprecation messages to go through Python's `logging` module instead of the default `warnings.warn` mechanism. How?

**A:** Pass any logging method as the `stream` parameter. The `stream` callable receives the formatted deprecation message as a single string argument — logging methods like `logging.warning` have exactly this signature. For the full range of `stream` options (silencing, custom callables, `print`), see [Deprecation Output Sink](guide/customization.md#deprecation-output-sink-stream).

```python
# phmdoctest:skip
import logging
from deprecate import deprecated

# Configure logging (typically done once at application startup)
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def new_endpoint(url: str) -> str:
    return f"GET {url}"


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
old_endpoint("/api/users")
```

**Choosing the log level:**

| Level             | When to use                                   |
| ----------------- | --------------------------------------------- |
| `logging.info`    | Early deprecation window; low urgency         |
| `logging.warning` | Standard choice; default log configs show it  |
| `logging.error`   | Critical deprecation nearing removal deadline |

**Benefits over `warnings.warn`:**

- Integrates with your existing log aggregation (ELK, Datadog, CloudWatch, etc.)
- Respects your log format, handlers, and filters
- Always emitted regardless of Python's warning filter state (no `-W` flag interaction)
- Timestamps included automatically if your formatter adds them

**Note:** When using `stream=logging.warning`, the `num_warns` parameter still controls how many times the message is emitted. The combination of `num_warns=-1` with `stream=logging.warning` ensures every deprecated call site is logged — useful for measuring migration progress via log analytics.

______________________________________________________________________

## Why doesn't `deprecated_class` warn when I call it with the new argument name?

**Q:** I set up `deprecated_class(args_mapping={"old_arg": "new_arg"}, ...)` on my class but no warning fires when I call it with `new_arg=...`. Did I configure it incorrectly?

**A:** No — this is the intended behaviour. When `args_mapping` is provided without an explicit callable `target`, the proxy auto-resolves to `TargetMode.ARGS_REMAP` and warns **only when the old argument name is actually present in the call**. Callers who have already migrated to the new argument name see no warning. This matches the per-argument warning behaviour of `@deprecated(target=TargetMode.ARGS_REMAP, args_mapping=...)`.



If you want to warn on every call regardless of which argument name is used, set `target=TargetMode.NOTIFY` explicitly (and omit `args_mapping`, which is ignored with `NOTIFY` and emits a construction-time `UserWarning`).

______________________________________________________________________

## Why doesn't `deprecated_class` warn when I call it with the new argument name?

**Q:** I set up `deprecated_class(args_mapping={"old_arg": "new_arg"}, ...)` on my class but no warning fires when I call it with `new_arg=...`. Did I configure it incorrectly?

**A:** No — this is the intended behaviour. When `args_mapping` is provided without an explicit callable `target`, the proxy auto-resolves to `TargetMode.ARGS_REMAP` and warns **only when the old argument name is actually present in the call**. Callers who have already migrated to the new argument name see no warning. This matches the per-argument warning behaviour of `@deprecated(target=TargetMode.ARGS_REMAP, args_mapping=...)`.

```python
from deprecate import deprecated_class

class Config:
    def __init__(self, timeout: int = 0) -> None:
        self.timeout = timeout

# args_mapping without a callable target → auto ARGS_REMAP
LegacyConfig = deprecated_class(
    args_mapping={"time_limit": "timeout"},
    deprecated_in="1.5", remove_in="2.0",
)(Config)

LegacyConfig(timeout=30)     # new name — no warning (caller already migrated)
LegacyConfig(time_limit=30)  # old name — FutureWarning emitted + remapped
```

If you want to warn on every call regardless of which argument name is used, set `target=TargetMode.NOTIFY` explicitly (and omit `args_mapping`, which is ignored with `NOTIFY` and emits a construction-time `UserWarning`).

______________________________________________________________________

## Still stuck?

!!! question "Open a GitHub issue"

    If none of the above covers your situation, open an issue on [GitHub Issues](https://github.com/Borda/pyDeprecate/issues). Include the full traceback, the decorator call you used, and the Python and pyDeprecate versions (`pip show pyDeprecate`).
