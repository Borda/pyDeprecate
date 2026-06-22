---
id: advanced
description: Advanced deprecation patterns — automatic docstring updates, injecting new required arguments, suppressing warnings in tests, class and static methods, and generator functions.
---

# Advanced

This page covers patterns that go beyond the basics: injecting a deprecation notice into the docstring at import time, supplying a fixed default for a new required argument, testing helpers, deprecating `@classmethod` and `@staticmethod` descriptors, and generator functions. For core function patterns see [Functions](functions.md); for async generators see [Async](async.md).

## Automatic docstring updates

Set `update_docstring=True` to inject a deprecation notice directly into the function's docstring at import time. The rendered API reference (Sphinx or MkDocs) always shows the deprecation status alongside the signature, with no manual upkeep.

!!! tip "See it live"

    The [Sphinx demo](../demo-sphinx/index.html) and [MkDocs demo](../demo-mkdocs/index.html) show how the injected notice renders in real API docs.

```python
# NEW/FUTURE API — renamed to be more explicit about what it does
def transform(x: int) -> int:
    """New implementation of the function."""
    return x * 2


# doc-example only: forces realistic path in warning; real modules set __module__ automatically
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


# doc-example only: forces realistic path in warning; real modules set __module__ automatically
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


# NEW API — creates a client connection dict from host and port
def new_create_client(host: str, port: int = 443) -> dict:
    return {"host": host, "port": port}


# DEPRECATED API — `create_client` replaced by `new_create_client`
@deprecated(target=new_create_client, deprecated_in="1.0", remove_in="2.0")
def create_client(host: str, port: int = 443) -> dict:
    return void(host, port)


def make_test_client() -> dict:
    """Test helper that calls the deprecated API."""
    return create_client("localhost", 8080)  # warns: FutureWarning


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
    @deprecated(deprecated_in="1.2", remove_in="2.0")
    @classmethod
    def from_config(cls, config: dict) -> "ApiClient":
        return cls()

    # Same flexibility with @staticmethod
    @staticmethod
    @deprecated(deprecated_in="0.9", remove_in="1.5")
    def version() -> str:
        return "1.0"

    @deprecated(deprecated_in="1.1", remove_in="2.0")
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

### Forwarding a static method to another static method in the same class

When renaming a `@staticmethod` or `@classmethod` within the same class, pass the new method directly as `target=new_method` — no `.__func__` suffix needed. Inside the class body `new_method` is still the raw descriptor (not yet bound); pyDeprecate unwraps it automatically.

```python
from deprecate import deprecated, void


class Compute:
    @staticmethod
    def area(radius: float) -> float:
        return 3.14159 * radius**2

    # DEPRECATED — renamed from surface() to area()
    @staticmethod
    @deprecated(target=area, deprecated_in="1.0", remove_in="2.0")
    def surface(radius: float) -> float:
        """Deprecated — use area() instead."""
        return void(radius)


print(Compute.surface(3.0))  # warns: FutureWarning
```

<details>
  <summary>Output: <code>Compute.surface(3.0)</code></summary>

```
28.27431
```

</details>

Combine with `args_mapping` when a parameter was also renamed:

```python
from deprecate import deprecated, void


class Compute:
    @staticmethod
    def area(radius: float) -> float:
        return 3.14159 * radius**2

    @staticmethod
    @deprecated(
        target=area,
        args_mapping={"r": "radius"},
        deprecated_in="1.0",
        remove_in="2.0",
    )
    def surface(r: float = 0.0, radius: float = 0.0) -> float:
        """Deprecated — use area(radius=...) instead."""
        return void(r, radius)


print(Compute.surface(r=3.0))  # warns: FutureWarning
```

<details>
  <summary>Output: <code>Compute.surface(r=3.0)</code></summary>

```
28.27431
```

</details>

The same pattern works for `@classmethod`. Both the deprecated and the replacement must be classmethods — pyDeprecate enforces this symmetry at decoration time and raises `TypeError` if the source has no `cls` parameter.

```python
from deprecate import deprecated, void


class Compute:
    @classmethod
    def area(cls, radius: float) -> float:
        return 3.14159 * radius**2

    # DEPRECATED — renamed from surface() to area()
    @classmethod
    @deprecated(target=area, deprecated_in="1.0", remove_in="2.0")
    def surface(cls, radius: float) -> float:
        """Deprecated — use area() instead."""
        return void(radius)


print(Compute.surface(3.0))  # warns: FutureWarning
```

<details>
  <summary>Output: <code>Compute.surface(3.0)</code></summary>

```
28.27431
```

</details>

## Deprecating generator functions

Generator functions — any function that contains `yield` — are fully supported by `@deprecated`. The decorator wraps them using an eager factory pattern: the deprecation warning fires when you **call** the generator function, not when you first iterate the result.

This is the right behavior. It keeps generator deprecations consistent with regular function deprecations. If the warning fired on the first `next()` call instead, you could easily miss it: someone might call the generator, pass it around, and iterate it elsewhere — the warning would appear far from the actual deprecated call site.

```python
from deprecate import deprecated, void


# NEW API — yields sequential integer IDs starting from a given value
def generate_ids(start: int, count: int):
    """Yield `count` sequential IDs starting from `start`."""
    for i in range(count):
        yield start + i


# DEPRECATED API — `iter_ids` replaced by `generate_ids`
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
def run_pipeline(items):
    """This generator is going away; no replacement yet."""
    for item in items:
        yield item.strip()


print(list(run_pipeline(["a ", "b "])))
```

<details>
  <summary>Output: <code>list(run_pipeline(["a ", "b "]))</code></summary>

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


# NEW API — yields integers from start (inclusive) to stop (exclusive)
def new_range(start: int, stop: int):
    yield from range(start, stop)


# DEPRECATED API — `old_range` replaced by `new_range`
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

## See also

- [Use Cases overview](use-cases.md) — start here for a guided tour of all deprecation patterns
- [Functions](functions.md) — core function deprecation patterns
- [Classes](classes.md) — class, Enum, and dataclass deprecation
- [Properties](properties.md) — `@property` and `@cached_property` deprecation
- [Async](async.md) — async functions and async generators
- [Customization](customization.md) — custom message templates and output streams
- [void() Helper](void-helper.md) — when and why to use `void()` in the function body
- [Audit Tools](audit.md) — enforce removal deadlines and detect deprecation chains in CI
- [Troubleshooting](../troubleshooting.md) — common errors and fixes

______________________________________________________________________

Next: [Customization](customization.md) — redirect deprecation output to a logger or use a custom message template.
