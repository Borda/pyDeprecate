---
id: troubleshooting
description: 'Fix common pyDeprecate errors: missing deprecation notices, async warning not appearing in CI, TypeError mapping failures, class deprecation notices, bool return errors, cross-module path issues, proxy limitations on primitives, and redirecting deprecation output to a logger.'
---

# Troubleshooting

This page covers the most common problems encountered when using pyDeprecate, with direct answers and corrected code for each. If your issue is not listed here, see the "Still stuck?" section at the bottom.

## ModuleNotFoundError: No module named 'pydeprecate'

**Q:** I installed the package with `pip install pyDeprecate` but get `ModuleNotFoundError: No module named 'pydeprecate'` when I try to import it.

**A:** The install name and the import name are different. The package installs as `pyDeprecate` but imports as `deprecate`.

Use:

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

Not:

```python
# phmdoctest:skip — intentional wrong import; would raise ModuleNotFoundError
from pydeprecate import deprecated  # wrong — no such module
```

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


print(MyClass(5).x)
```

<details>
  <summary>Output: <code>MyClass(5).x</code></summary>

```
5
```

</details>

## Upgrading from `@deprecated` on a class to `@deprecated_class`

**Q:** My codebase used an older version of pyDeprecate that applied `@deprecated` directly to a class. It behaved strangely — `isinstance()` checks failed, subclassing broke, and class attributes were inaccessible. What went wrong, and how do I migrate?

**A:** Before v0.6.0, applying `@deprecated` directly to a class replaced the class object with a plain wrapper function. Python's `isinstance`, `issubclass`, and attribute lookup all operate on the class type — so replacing the class with a function silently broke every downstream use that depended on the class being a type.

!!! failure "The old pattern silently broke isinstance, issubclass, and attribute access"

    Before v0.6.0, `@deprecated` on a class replaced the class with a plain function. All type checks, subclassing, and class attribute access failed silently or raised `TypeError`. If you see this pattern in your codebase, migrate to `@deprecated_class()` immediately.

Symptoms of the old behaviour:

```python
# phmdoctest:skip — shows pre-v0.6 behaviour; current code uses a proxy and no longer produces these errors
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
  <summary>Output: <code>obj.add(1, 2)</code></summary>

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
  <summary>Output: <code>OldColor["RED"] is Color.RED</code></summary>

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
  <summary>Output: <code>isinstance(svc, MyService)</code></summary>

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
def new_func(new_arg: int) -> int:
    return new_arg * 2


# ---------------------------

from deprecate import deprecated


# None means skip this argument
@deprecated(target=new_func, args_mapping={"old_arg": None})
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


print(isinstance(old_func(old_name=3), int))
```

<details>
  <summary>Output: <code>isinstance(old_func(old_name=3), int)</code></summary>

```
True
```

</details>

**Option 3 — Use `TargetMode.ARGS_REMAP`** (deprecating an argument of the same function, not forwarding to a different one):

```python
from deprecate import TargetMode, deprecated


# Deprecate within same function
@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_arg": "new_arg"})
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


print(old_func1())
print(old_func2())
```

<details>
  <summary>Output: <code>old_func1(), old_func2()</code></summary>

```
Hi!
Hi!
```

</details>

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


print(callable(old_func_always_warn), callable(old_func_warn_n_times))
```

<details>
  <summary>Output: <code>callable(old_func_always_warn), callable(old_func_warn_n_times)</code></summary>

```
True True
```

</details>

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
  <summary>Output: <code>OLD_THRESHOLD["value"]</code></summary>

```
0.5
```

</details>

1. **Update call sites directly** — for simple numeric or string constants that are used in expressions, it is often simpler to rename the constant and update references rather than wrapping in a proxy. This does not emit deprecation warnings; use it when a mechanical migration is enough.

```python
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
  <summary>Output: <code>get_old_threshold()</code></summary>

```
0.5
```

</details>

## How do I redirect deprecation output to a logger instead of `warnings.warn`?

**Q:** I want deprecation messages to go through Python's `logging` module instead of the default `warnings.warn` mechanism. How?

**A:** Pass any logging method as the `stream` parameter. The `stream` callable receives the formatted deprecation message as a single string argument — logging methods like `logging.warning` have exactly this signature. For the full range of `stream` options (silencing, custom callables, `print`), see [Deprecation Output Sink](guide/customization.md#deprecation-output-sink-stream).

```python
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

- Integrates with your existing log aggregation (ELK, Datadog, CloudWatch, etc.)
- Respects your log format, handlers, and filters
- Always emitted regardless of Python's warning filter state (no `-W` flag interaction)
- Timestamps included automatically if your formatter adds them

**Note:** When using `stream=logging.warning`, the `num_warns` parameter still controls how many times the message is emitted. The combination of `num_warns=-1` with `stream=logging.warning` ensures every deprecated call site is logged — useful for measuring migration progress via log analytics.

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

To emit a deprecation notice for every instantiation regardless of which argument name is used, configure `target=TargetMode.NOTIFY` explicitly. Combining `TargetMode.NOTIFY` with `args_mapping` is a misconfiguration — `args_mapping` is not applied under `NOTIFY` and supplying it emits a construction-time `UserWarning` today that becomes a `TypeError` in v1.0.

______________________________________________________________________

## UserWarning when using `TargetMode.ARGS_REMAP` without `args_mapping`

**Q:** I applied `@deprecated(target=TargetMode.ARGS_REMAP, ...)` and got the warning `UserWarning: @deprecated(target=TargetMode.ARGS_REMAP) on my_func requires args_mapping ...`. Why, and how do I fix it?

**A:** `TargetMode.ARGS_REMAP` is designed exclusively for renaming or dropping arguments within the same function. Without `args_mapping` there is nothing to remap — the decorator has zero call-time effect. This is a misconfiguration that emits a `UserWarning` today and will become a `TypeError` in v1.0.

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
def my_func(x: int) -> int:
    """Going away — remove all call sites."""
    return x * 2


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

______________________________________________________________________

## `attrs_mapping` auto-expand skips some fields on a dataclass with a custom `__init__`

**Q:** I added `attrs_mapping` to a `deprecated_class()` wrapping a dataclass that overrides `__init__`. Some deprecated aliases aren't being auto-expanded into `args_mapping` — constructor calls with the old name raise `TypeError` instead of remapping. Why?

**A:** When `deprecated_class()` detects a `@dataclass` target, it automatically copies `attrs_mapping` entries into `args_mapping` so that both attribute access (`proxy.old_field`) and constructor calls (`DC(old_field=5)`) emit `FutureWarning`. The auto-expand consults `inspect.signature` to determine which names are valid `__init__` kwargs.

There are two cases where a field is intentionally excluded:

1. **`field(init=False)`** — the field is an instance attribute set inside `__init__` (or `__post_init__`) but is not a constructor parameter. Passing it as a kwarg would raise `TypeError`.

2. **Custom `__init__` that omits a dataclass field** — if the overridden `__init__` intentionally leaves a field out of its signature, that field is not a valid kwarg and is excluded from auto-expand.

Both surfaces still work independently: `attrs_mapping` redirects attribute access for all listed aliases (including `init=False` fields), while `args_mapping` only covers fields present in the actual constructor signature.

```python
from dataclasses import dataclass, field
from deprecate import deprecated_class


@dataclass
class Config:
    timeout: int = 30
    _cache: dict = field(default_factory=dict, init=False)  # not a constructor param


DepConfig = deprecated_class(
    attrs_mapping={"time_limit": "timeout", "store": "_cache"},
    deprecated_in="1.0",
    remove_in="2.0",
    stream=None,
)(Config)

meta = DepConfig.__deprecated__
print("timeout auto-expanded:", "time_limit" in meta.args_mapping_auto_expanded)  # warns: FutureWarning
print("_cache auto-expanded:", "store" in meta.args_mapping_auto_expanded)  # warns: FutureWarning
```

<details>
  <summary>Output: <code>meta.args_mapping_auto_expanded</code></summary>

```
timeout auto-expanded: True
_cache auto-expanded: False
```

</details>

`store` is excluded from `args_mapping` — calling `DepConfig(store={})` would raise `TypeError`. Use `attrs_mapping` for attribute access and leave the `init=False` field out of constructor calls.

______________________________________________________________________

## My object mutated despite `read_only=True`

**Q:** I passed `read_only=True` to `deprecated_instance()` but a method on my object still mutated its state. Why?

**A:** `read_only=True` intercepts only the following standard collection mutator names: `append`, `clear`, `discard`, `extend`, `insert`, `pop`, `remove`, `setdefault`, `update`, `add`. These cover the mutating methods on Python's built-in `list`, `dict`, and `set` types.

Custom method names — for example `register()`, `reload()`, or `set_value()` — are not in this list and call through to the underlying object without any guard.

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

______________________________________________________________________

## TypeError at decoration time: cross-class method target

**Q:** I got the following error at decoration time — what does it mean and how do I fix it?

```text
TypeError: Cannot use @deprecated on 'Foo.old_method' with target 'Bar.new_method':
cross-class method forwarding is not supported because `self` would carry the wrong type.
The target must be a method on the same class ('Foo') or a full class (use target=Bar for class migration).
```

**A:** The cross-class guard in pyDeprecate raises `TypeError` at **decoration time** (when the class body is executed), not at call time. It fires when `@deprecated` on a method in class `Foo` points to a method defined on a different class `Bar`. Forwarding to a method on a different class silently passes `self` of the wrong type, causing `AttributeError` or incorrect behaviour at runtime — the guard prevents this misconfiguration from reaching production.

**Common fix — forward to the correct target within the same class or to a standalone function:**

```python
from deprecate import deprecated


class MyService:
    def execute(self, x: int) -> int:
        return x * 2

    # Correct: target is on the same class
    @deprecated(target=execute, deprecated_in="1.0", remove_in="2.0")
    def run(self, x: int) -> int:
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

**False-positive triggers fixed in v0.8:**

Before v0.8, two patterns produced spurious `TypeError` raises from the guard:

- **Decorators that rewrite `__qualname__`** — a decorator applied before `@deprecated` that sets `fn.__qualname__ = "OtherClass.method"` caused the guard to see the wrong owner class. Fixed in v0.8 by reading `__qualname__` from the enclosing class-body frame, which Python sets before any decorator runs.
- **Metaclass-generated classes** — `type("Name", bases, ns)` and similar patterns produce qualnames like `"FakeOwner.method"` for methods that are not actually on `FakeOwner`. Fixed in v0.8 by verifying that the class name in the qualname prefix actually exists in the module globals; when it does not, the guard skips the check.

If you are on v0.8+ and still seeing an unexpected `TypeError` from the cross-class guard, open an issue with a minimal reproducer.

______________________________________________________________________

## UserWarning at decoration time: unsupported stacking combination

**Q:** I got a `UserWarning` when applying multiple `@deprecated` decorators to the same function. What does it mean and how do I fix it?

The message looks like one of:

```text
UserWarning: 'my_func' has @deprecated(NOTIFY) stacked over @deprecated(ARGS_REMAP).
Reverse the decorator order: put @deprecated(ARGS_REMAP, ...) outermost (on top)
and @deprecated(NOTIFY, ...) below it. Will be `TypeError` in `v1.0`.
```

**A:** pyDeprecate validates stacking combinations at decoration time and emits `UserWarning` for every unsupported case. Common supported combinations include:

- `ARGS_REMAP` (outer, on top) + `ARGS_REMAP` (inner): multi-step argument renames across versions.
- `ARGS_REMAP` (outer, on top) + `NOTIFY` (inner): lifecycle pattern — rename args first, then deprecate the whole function.
- `NOTIFY` (outer, on top) + `callable` (inner): deprecate a callable target directly without an inner `@deprecated` wrapper.

See [Supported stacking combinations](guide/use-cases.md#supported-stacking-combinations) for the full table. The warning message identifies which combination fired and includes a corrective hint.

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

______________________________________________________________________

## My deprecated generator fires the warning before I iterate it

**Q:** My generator function is decorated with `@deprecated`. The deprecation warning fires as soon as I call the function — before I even call `next()` or iterate over it. Is this a bug?

**A:** No — this is the intended behavior. pyDeprecate uses an eager factory pattern for generator wrappers: the warning fires at call time, consistent with how deprecated regular functions behave.

When you call a deprecated generator function, you get the deprecation notice immediately at the call site — the same place you would see it for a deprecated regular function. You can then pass the generator around, iterate it later, or hand it to another function, and the warning is already recorded.

```python
from deprecate import deprecated, void


def generate_ids(start: int, count: int):
    for i in range(count):
        yield start + i


@deprecated(target=generate_ids, deprecated_in="0.9", remove_in="1.0")
def iter_ids(start: int, count: int):
    return void(start, count)


# Warning fires here — at call time
gen = iter_ids(10, 3)
# FutureWarning: The `iter_ids` was deprecated since v0.9 in favor of `generate_ids`.

# Iteration is normal — warning already fired above
print(list(gen))  # [10, 11, 12]
```

<details>
  <summary>Output: <code>list(gen)</code></summary>

```
[10, 11, 12]
```

</details>

______________________________________________________________________

## My deprecated async generator fires the warning before I iterate it

**Q:** My async generator function is decorated with `@deprecated`. The deprecation warning fires as soon as I call the function — before my `async for` loop starts. Is this a bug?

**A:** No — this is the intended behavior. pyDeprecate wraps async generator sources with a sync callable. Calling the wrapper immediately fires the deprecation warning and returns the underlying async generator object; no `await` or `__anext__` call is needed for the warning to appear.

```python
import asyncio
from deprecate import deprecated


async def stream(n: int):
    for i in range(n):
        yield i


@deprecated(target=stream, deprecated_in="0.9", remove_in="1.0")
async def old_stream(n: int):
    if False:  # pragma: no cover
        yield 0


# Warning fires here — at sync call time, before any iteration
agen = old_stream(3)  # FutureWarning: The `old_stream` was deprecated since v0.9 ...


async def consume(gen):
    async for _ in gen:
        pass


# Iteration proceeds normally
asyncio.run(consume(agen))
```

**Note:** Because the wrapper is a sync function, `inspect.isasyncgenfunction(old_stream)` returns `False`. Frameworks that check this flag may misclassify the wrapper — wrap it in a thin `async def` passthrough if introspection matters.

______________________________________________________________________

## Warning fires or UserWarning appears when using `@deprecated @classmethod`

**Q:** I applied `@deprecated` on top of `@classmethod` (decorator order: `@deprecated` outermost, `@classmethod` innermost). Does this work?

**A:** Yes — pyDeprecate silently rescues the misordered stack at decoration time. When you write:

```python
from deprecate import deprecated


class Foo:
    # Works — @deprecated outside @classmethod is transparently rescued
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    @classmethod
    def old_method(cls, x): ...


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


def new_impl(cls, x):
    return x * 2


class Foo:
    # Preferred — @deprecated applied to the raw function, @classmethod wraps the result
    @classmethod
    @deprecated(target=new_impl, deprecated_in="1.0", remove_in="2.0")
    def old_method(cls, x):
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

______________________________________________________________________

## Concurrent async calls and warning counts

**Q:** I have multiple coroutines all calling the same deprecated `async def` wrapper concurrently. The deprecation notice only appeared once, but I expected it to fire `num_warns` times. What happened?

**A:** `_WrapperState` fields — `called`, `warned_calls`, and `warned_args` — are plain Python dataclass fields with no asyncio lock. When multiple coroutines share a single deprecated wrapper and run concurrently in the same event loop, they race on the warning counter. One coroutine may read the counter, another may increment it, and the first may then emit or skip based on the stale value. The result is that fewer warnings than `num_warns` specifies may be emitted.

This is an accepted limitation for v0.9 — adding an asyncio lock would change the public behaviour of synchronous wrappers and is deferred to a future release.

**Workaround:** Set `num_warns=-1` to bypass the count gate entirely. With `num_warns=-1` the warning fires unconditionally on every call, so no race can suppress it.

```python
import asyncio
from deprecate import deprecated


async def new_fetch(url: str) -> bytes:
    return url.encode()


# num_warns=-1 emits the deprecation notice on every call regardless of concurrency
@deprecated(target=new_fetch, deprecated_in="0.9", remove_in="1.0", num_warns=-1)
async def old_fetch(url: str) -> bytes:
    pass


async def main():
    urls = ["https://a.example.com", "https://b.example.com", "https://c.example.com"]
    # All three tasks fire the deprecation notice — no race suppression possible
    await asyncio.gather(*[old_fetch(u) for u in urls])


print(asyncio.run(main()) is None)
```

<details>
  <summary>Output: <code>asyncio.run(main()) is None</code></summary>

```
True
```

</details>

If you need to assert exactly one warning fires in a test, run the deprecated coroutines sequentially rather than with `asyncio.gather`.

______________________________________________________________________

## My deprecated `async def` warning does not appear in CI

**Q:** I decorated an `async def` function with `@deprecated` but the deprecation notice never shows up in my CI logs or test output. Why, and how do I fix it?

**A:** For `async def` functions, the deprecation warning fires when the coroutine is **awaited**, not when the wrapper is called. Creating the coroutine object — `coro = old_fn(x=1)` — produces no warning; `await coro` is what triggers it.

This means warnings can be silently lost in these common scenarios:

- **`asyncio.create_task(old_fn(x=1))`** or **tasks scheduled via fixtures or library hooks** — the warning fires when the event loop executes the coroutine, which may happen outside any active `catch_warnings` context (e.g., in application startup code, background workers, or test fixtures that schedule work on the event loop).
- **Third-party schedulers** (Celery, arq, anyio task groups) — the coroutine is handed off and the warning fires in the scheduler's execution context, not the caller's, and may be routed to a different warning filter.
- **`pytest.warns(FutureWarning)` wrapping only the call** — `with pytest.warns(FutureWarning): coro = old_fn()` captures nothing because no warning fires at call time. The block must wrap the `await`.

```python
import asyncio
import warnings
from deprecate import deprecated


async def new_fetch(url: str) -> bytes:
    return url.encode()


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


async def new_fetch(url: str) -> bytes:
    return url.encode()


@deprecated(target=new_fetch, deprecated_in="0.9", remove_in="1.0", stream=logging.warning)
async def old_fetch(url: str) -> bytes:
    pass
```

**Fix 2 — wrap the `await` in tests**, not just the call:

```python
import pytest


@pytest.mark.asyncio
async def test_old_fetch_warns():
    with pytest.warns(FutureWarning):
        await old_fetch("https://example.com")  # correct: await inside the warns block
```

!!! note "Async warning timing is by design"

    The `async def` wrapper is a true coroutine (`inspect.iscoroutinefunction(wrapper)` returns `True`), so the deprecation warning fires inside the coroutine body when execution begins — on `await`. Warnings for generators and async generators fire eagerly at call time; async functions are the exception because the wrapper itself is a coroutine.

______________________________________________________________________

## I wrapped a `functools.partial` of an `async def` and the wrapper is not async

**Q:** I applied `@deprecated` to a `functools.partial` of an `async def` function, but the resulting wrapper is synchronous — `inspect.iscoroutinefunction(wrapper)` returns `False` and `await wrapper(...)` raises a `TypeError`. What is happening?

**A:** On Python 3.9–3.11, `inspect.iscoroutinefunction(functools.partial(async_fn))` returns `False` — the `partial` object does not propagate the coroutine flag of its wrapped callable. pyDeprecate's async branch in `packing()` checks `inspect.iscoroutinefunction(source)` to decide whether to produce an `async def` wrapper, so wrapping a `partial` of an `async def` falls through to the sync wrapper path and yields a regular function instead of a coroutine function.

**Workaround:** Apply `@deprecated` to the `async def` directly, then use `functools.partial` on the already-deprecated async wrapper if you need preset arguments.

```python
import asyncio
import functools
from deprecate import deprecated


async def new_fetch(url: str, timeout: int = 30) -> bytes:
    return url.encode()


# Correct: apply @deprecated to the async def directly
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

This limitation is resolved in Python 3.12+ where `inspect.iscoroutinefunction` correctly handles `functools.partial` objects whose underlying callable is an `async def`.

______________________________________________________________________

## A non-async decorator applied over my `@deprecated` async wrapper no longer looks like a coroutine

**Q:** I have `@my_decorator @deprecated(...) async def fn(...)`. After decoration, `inspect.iscoroutinefunction(fn)` returns `False` and asyncio frameworks (FastAPI route handlers, `asyncio.create_task`) reject it. Why?

**A:** When you wrap a `@deprecated async def` with a sync decorator — one that returns a sync `wrapper(*args, **kwargs)` via `functools.wraps` — the result is a sync callable. `inspect.iscoroutinefunction` inspects the outermost callable's `CO_COROUTINE` flag, not the wrapped function, so the coroutine nature is lost the moment a sync wrapper is placed on top.

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

______________________________________________________________________

## Setter or deleter on a deprecated property doesn't warn

**Q:** I decorated my property with `@deprecated` and added a setter via `@value.setter`, but writing to the property is silent — no `FutureWarning` is emitted. Deleting via `del obj.value` is also silent. What is happening?

**A:** The two property decorator orders behave differently. **Inner order** (`@property` outermost, `@deprecated` closer to `def`) wraps the getter (`fget`) only. Any setter or deleter added later via `@value.setter` / `@value.deleter` is a plain accessor with no deprecation closure, so writes and deletes are silent.

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

______________________________________________________________________

## TypeError: `@deprecated` cannot be applied twice to the already-deprecated property

**Q:** I tried to stack two `@deprecated` decorators on a property and got `TypeError: ... cannot be applied twice to the already-deprecated property`. Why is this disallowed, and how do I add a setter or deleter to an already-deprecated property?

**A:** Double-wrapping a deprecated property would emit two `FutureWarning` notices per access and run the stacking guard three times. The decorator raises `TypeError` at decoration time so the misconfiguration cannot reach production. The error message names the offending property and points to the supported alternatives.

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

**Fix — apply `@deprecated` once, then use chain-style `@value.setter` / `@value.deleter`** to add accessors. The outer-order subclass of `property` re-wraps the freshly-supplied accessor with the same packing config so warnings continue to fire on every access kind:

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

______________________________________________________________________

## `@deprecated` crashes when the replacement target has POSITIONAL_ONLY parameters

**Q:** I applied `@deprecated(target=new_fn)` and get `TypeError: new_fn() got some positional-only arguments passed as keyword argument` when calling the deprecated function. Why?

**A:** `@deprecated` converts all intercepted arguments to kwargs before forwarding them to the target. If the target declares any parameter as positional-only (using `/` in the signature), that kwarg call raises `TypeError` because Python forbids passing positional-only params by name.

```python
def new_fn(x: int, /, y: int = 0) -> int:
    return x + y


# @deprecated(target=new_fn) then calling old_fn(5) internally does:
#   new_fn(**{"x": 5})  →  TypeError: got positional-only argument as keyword argument
```

**Workaround**: wrap the target in a thin adapter that accepts the same params as ordinary keyword arguments:

```python
def _new_fn_compat(x: int, y: int = 0) -> int:
    return new_fn(x, y)  # call new_fn positionally internally


@deprecated(target=_new_fn_compat, deprecated_in="1.0", remove_in="2.0")
def old_fn(x: int, y: int = 0) -> int: ...
```

This limitation affects `@deprecated` on functions and methods only. `deprecated_class` is unaffected — the proxy has a `setattr` fallback for POSITIONAL_ONLY constructor parameters and emits a `UserWarning` at decoration time rather than crashing at call time.

______________________________________________________________________

## Still stuck?

!!! question "Open a GitHub issue"

    If none of the above covers your situation, open an issue on [GitHub Issues](https://github.com/Borda/pyDeprecate/issues). Include the full traceback, the decorator call you used, and the Python and pyDeprecate versions (`pip show pyDeprecate`).
