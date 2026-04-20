---
id: use-cases
description: Twelve real-world deprecation patterns for Python functions, methods, classes, Enums, dataclasses, and constants — each with a worked example and full code.
---

# Use Cases

The two most common triggers for a deprecation are renaming a function and renaming an argument — and both require more than a bare `warnings.warn` call: you need to forward the call, remap any changed argument names, and keep the old code working correctly until removal. This page shows how pyDeprecate handles each scenario, from a simple rename to proxy-wrapped Enums and multi-hop argument chains. If you are new to the library, read [Getting Started](../getting-started.md) first.

## Simple function forwarding

When you rename a function, the old name must keep working during the deprecation window. Applying `@deprecated(target=new_func)` on the old name tells pyDeprecate to forward every call — including all positional and keyword arguments — to the new function automatically. The body of the deprecated function is never executed, so you can leave it empty or place a docstring there.

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
#   The `calculate` was deprecated since v0.1 in favor of `__main__.compute`.
#   It will be removed in v0.5.
print(calculate(1, 2))
```

When the deprecated name already exists as a callable (for example, imported from another package), you can apply `deprecated()` directly without redefining the function body. This wrapper form is equivalent to the decorator syntax but works on any already-existing callable — including one you do not control.

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

When the new function accepts the same arguments under different names, use `args_mapping` to translate the old parameter names to the new ones at call time. This lets callers continue to pass the old names during the deprecation window without any manual mapping code in the wrapper body.

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

## Warning-only deprecation

Sometimes you want to signal that a function is deprecated without replacing it — the original implementation must keep running unchanged. Setting `target=None` emits the deprecation warning and then executes the function body normally. Use this pattern when the function will be removed in a future version but has no direct replacement yet, or when callers need to update their code themselves.

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

## Self argument mapping

Use `target=True` when you want to rename or drop an argument within the same function — no external target is needed. The decorator remaps the old argument name to the new one before the function body runs, so the function implementation only needs to use the new name. This is the right pattern when refactoring a signature without moving the function.

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

To drop an argument entirely — warn when it is passed and then discard it — map it to `None`. The function body must be prepared to receive no value for that parameter.

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

## Chained deprecation levels

When a function's arguments are deprecated across multiple releases — each argument retired at a different version — stack multiple `@deprecated(True, ...)` decorators, one per argument rename. Each decorator in the chain operates on its own version range and emits a separate warning, giving callers clear, version-specific migration guidance.

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

## Conditional skip

The `skip_if` parameter accepts a boolean or a zero-argument callable that returns a boolean. When the callable returns `True`, the deprecation warning is suppressed entirely and the call proceeds normally. This is useful when behaviour differs by runtime version — for example, you may want to suppress the warning once the caller has already migrated to a newer dependency that no longer triggers the deprecated path.

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

## Class deprecation

There are two common class deprecation patterns. The first is renaming a method within the same class — apply `@deprecated(target=execute)` on the old method name and calls are forwarded to the new method at runtime. The second is deprecating an entire class by decorating `__init__` to warn at instantiation time and optionally forward construction to a successor class.

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
#   The `run` was deprecated since v1.0 in favor of `__main__.execute`.
#   It will be removed in v2.0.
print(svc.run(5))
```

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
#   The `Client` was deprecated since v0.2 in favor of `__main__.HttpClient`.
#   It will be removed in v0.4.
inst = Client(7)
print(inst.my_c)  # returns: 7
print(inst.my_d)  # returns: "efg"
```

## Constants and instances

Use `deprecated_instance` to wrap module-level objects — such as dicts, lists, or custom objects — in a transparent proxy that emits a warning on every attribute, item, or call access. The `read_only=True` flag prevents callers from mutating shared state through the deprecated alias. Note that primitive protocol methods such as numeric arithmetic on `float` or concatenation on `str` are not intercepted; for primitive constants, prefer wrapping them in a container or updating call sites directly.

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

## Enums and dataclasses

`deprecated_class()` wraps an entire Enum or dataclass in a transparent proxy that warns on access and forwards attribute, item, and call operations to the replacement class. Use `args_mapping` to rename or drop kwargs when the deprecated class is called. Type checks with `isinstance()` and `issubclass()` work transparently and do not emit warnings, since these are structural checks rather than actual usage of the deprecated API.

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

## Automatic docstring updates

When you generate API documentation with Sphinx or MkDocs, you can inject a deprecation notice directly into the function's docstring at import time using `update_docstring=True`. This ensures the rendered API reference always shows the deprecation status alongside the function signature, without you having to maintain the notice manually.

```python
# NEW/FUTURE API — renamed to be more explicit about what it does
def transform(x: int) -> int:
    """New implementation of the function."""
    return x * 2


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
#    Use `__main__.transform` instead.
```

For MkDocs projects using `mkdocstrings`, switch to the admonition output style and register the Griffe extension so the injected notice appears in generated docs:

```python
from deprecate import deprecated


def transform(x: int) -> int:
    return x * 2


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
#     Use `__main__.transform` instead.
```

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

When the replacement function gains a new required parameter that callers of the old API never passed, use `args_extra` to inject a fixed default value at the wrapper level. This lets you forward calls without breaking existing call sites while signalling through the deprecation warning that callers should migrate to the new API and pass the argument explicitly.

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
#   The `notify` was deprecated since v1.5 in favor of `__main__.send_email`.
#   It will be removed in v2.0.
print(notify("alice@example.com", "Hello"))
```

`args_extra` is only applied when `target` is a `Callable`. It is merged into the forwarded kwargs after `args_mapping` is applied, so extra values can also override mapped ones. It is ignored for `target=True` self-deprecation, where no forwarding occurs.

## Suppressing warnings in test fixtures with `assert_no_warnings`

When writing tests, you often need to call deprecated functions in setup code — fixtures, helpers, or factory functions — without polluting the test output with deprecation warnings. The `assert_no_warnings` context manager handles this by catching and discarding warnings of the specified type inside the block, while also asserting that no such warning escapes.

This is different from `pytest.warns` (which asserts a warning IS emitted) and from `warnings.filterwarnings("ignore")` (which silences globally without assertion). `assert_no_warnings` gives you a scoped, assertion-backed silence: if the function under the block unexpectedly starts emitting a different warning category, that would still surface.

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

The key distinction between the testing tools:

| Tool                                                   | Purpose                       | Fails when...                 |
| ------------------------------------------------------ | ----------------------------- | ----------------------------- |
| `pytest.warns(FutureWarning)`                          | Assert warning IS emitted     | No matching warning raised    |
| `assert_no_warnings(FutureWarning)`                    | Assert warning is NOT emitted | A matching warning IS raised  |
| `warnings.catch_warnings()` + `simplefilter("ignore")` | Suppress without assertion    | Never fails (use in fixtures) |

Use `assert_no_warnings` in your test assertions to verify that refactored code no longer triggers deprecation warnings. Use `warnings.catch_warnings` in fixtures when you need to call deprecated code silently during setup.

______________________________________________________________________

Next: [void() Helper](void-helper.md) — understanding the no-op body helper, or [Audit Tools](audit.md) for CI enforcement utilities.
