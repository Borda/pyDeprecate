---
description: 'Fix common pyDeprecate errors: missing warnings, TypeError mapping failures, class deprecation warnings, bool return errors, and cross-module warning issues.'
---

# Troubleshooting

This page covers the five most common problems encountered when using pyDeprecate, with direct answers and corrected code for each. If your issue is not listed here, see the "Still stuck?" section at the bottom.

## UserWarning when decorating a class

**Q:** I applied `@deprecated` directly to a class and got `UserWarning: Direct use of `@deprecated` on class `MyClass` is deprecated since `v0.6.0`. Use `@deprecated_class(...)` instead. This will become a `TypeError` in a future release.` Why, and how do I fix it?

**A:** Use `@deprecated_class()` for classes. The `@deprecated` decorator is designed for functions and methods only.

That warning is triggered specifically when `@deprecated` is applied directly to a class. This still works today because pyDeprecate delegates to `@deprecated_class()` under the hood, but that delegation path is itself deprecated and will become a `TypeError` in a future release. The warning is telling you to switch to the explicit class API now.

There are two supported alternatives depending on what you need. Use `@deprecated_class()` when you want to deprecate the class name itself (including Enums and dataclasses). Use `@deprecated` on `__init__` when you want to warn only at instantiation time while keeping the class name in place.

```python
from deprecate import deprecated_class
from enum import Enum


# Correct: use @deprecated_class for classes
@deprecated_class(target=None, deprecated_in="1.0", remove_in="2.0")
class MyClass:
    pass


@deprecated_class(target=None, deprecated_in="1.0", remove_in="2.0")
class MyEnum(Enum):
    A = 1
    B = 2


# Alternative: decorate __init__ to warn at instantiation while keeping the class name
from deprecate import deprecated


class MyClass:
    @deprecated(target=None, deprecated_in="1.0", remove_in="2.0")
    def __init__(self, x: int) -> None:
        self.x = x  # body still executes; warning fires on every new MyClass(...)
```

## TypeError: Failed mapping

**Q:** I get `TypeError: Failed mapping of `my_func`, arguments not accepted by target: ['old_arg']`. What does this mean?

**A:** Your deprecated function passes an argument that the target function does not accept. You need to either drop the argument, rename it to match the target's signature, or use `target=True` for in-place remapping.

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

**Option 3 — Use `target=True`** (deprecating an argument of the same function, not forwarding to a different one):

```python
from deprecate import deprecated


# Deprecate within same function
@deprecated(target=True, args_mapping={"old_arg": "new_arg"})
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

## Deprecation warning not appearing

**Q:** I call my deprecated function but no warning is printed. Where did the warning go?

**A:** By default, pyDeprecate emits the warning only once per function (`num_warns=1`) to avoid log spam. After the first call, subsequent calls are silent. Set `num_warns=-1` for unlimited warnings or `num_warns=N` for exactly `N` emissions.

For per-argument deprecation (when using `args_mapping` with `target=True`), each deprecated argument has its own independent warning counter — so warnings for different arguments are tracked separately and each fires once by default.

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

## Warning path incorrect across modules

**Q:** I moved a function to a different module and the deprecation warning shows an unexpected path. How do I fix the displayed module path?

**A:** The warning message reports the fully-qualified path of the target callable as Python resolves it at decoration time. Ensure the target is imported from its canonical location before the `@deprecated` decorator is applied.

When moving functions across modules, import the target from its new home explicitly rather than relying on a re-export alias. The path shown in the warning will then reflect the module where the function actually lives, giving callers accurate migration information. The warning will correctly show the full path for real imports when used in your package.

______________________________________________________________________

## Still stuck?

If none of the above covers your situation, open an issue on [GitHub Issues](https://github.com/Borda/pyDeprecate/issues). Include the full traceback, the decorator call you used, and the Python and pyDeprecate versions (`pip show pyDeprecate`).
