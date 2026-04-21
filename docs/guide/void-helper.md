---
id: void-helper
description: "Learn when and how to use pyDeprecate's void(...) helper: silence IDE warnings in deprecated stubs, compare alternatives like pass and docstring-only bodies, and understand how void(...) interacts with type checking."
---

# The void(...) Helper

When `@deprecated` forwards to a `target` callable, the deprecated function's body never runs — every call is intercepted before the body executes. This creates a practical annoyance: IDEs and linters see the parameters in the signature but nothing using them in the body, and they flag them as unused.

`void(...)` is a no-op that accepts any arguments and returns `None`. Its only job is to silence those IDE warnings by giving the parameters a nominal "use" that says: the body is intentionally empty, not accidentally incomplete. It has no runtime effect.

## When to use void(...)

Use `void(...)` when all three of these are true:

- The deprecated function forwards to a `target` callable (not `target=None` or `target=True`).
- Your IDE or linter flags the function parameters as unused.
- You want the code to clearly signal that the empty body is deliberate.

If you prefer, a bare `pass` or a standalone docstring are equally valid. pyDeprecate does not require `void(...)`. Use whichever form is clearest for your team.

## Basic usage

`void(...)` appears as the return expression of a deprecated wrapper. The call to `old_add` is forwarded to `new_add` immediately — the `return void(a, b)` line is never reached, but it tells the IDE that `a` and `b` are intentionally accepted and ignored.

```python
def new_add(a: int, b: int) -> int:
    return a + b


# ---------------------------

from deprecate import deprecated, void


@deprecated(target=new_add, deprecated_in="1.0", remove_in="2.0")
def old_add(a: int, b: int) -> int:
    return void(a, b)  # Tells IDE: "Yes, I know these parameters aren't used"
    # This line is never reached - call is forwarded to new_add


# Alternative: You can also use pass or just a docstring
@deprecated(target=new_add, deprecated_in="1.0", remove_in="2.0")
def old_add_v2(a: int, b: int) -> int:
    """Just a docstring works too."""
    pass  # This also works
```

## Alternatives to void(...)

Three common patterns for the body of a deprecated function that forwards to a target. All are equivalent at runtime because the body is never reached — the choice is about communicating intent to readers and static analysis.

### Option 1: `pass` statement

Simplest approach. Clear, idiomatic Python for "do nothing". IDEs may still flag the parameters as unused since nothing references them.

```python
from deprecate import deprecated


def new_compute(x: int, y: int) -> int:
    return x + y


@deprecated(target=new_compute, deprecated_in="1.0", remove_in="2.0")
def old_compute(x: int, y: int) -> int:
    pass
```

### Option 2: Docstring-only body

A docstring alone is a valid function body. Useful when you want to document what the deprecated function did historically or provide migration notes inline.

```python
from deprecate import deprecated


def new_compute(x: int, y: int) -> int:
    return x + y


@deprecated(target=new_compute, deprecated_in="1.0", remove_in="2.0")
def old_compute(x: int, y: int) -> int:
    """Previously computed x + y. Use new_compute() directly."""
```

### Option 3: `void(...)` call

References all parameters explicitly, silencing "unused parameter" warnings in PyCharm, VS Code (Pylance), and ruff. Also serves as a visual marker that the empty body is intentional.

```python
from deprecate import deprecated, void


def new_compute(x: int, y: int) -> int:
    return x + y


@deprecated(target=new_compute, deprecated_in="1.0", remove_in="2.0")
def old_compute(x: int, y: int) -> int:
    return void(x, y)
```

### Comparison summary

| Approach       | Silences unused-param warnings | Self-documenting intent | Extra import                       |
| -------------- | ------------------------------ | ----------------------- | ---------------------------------- |
| `pass`         | No                             | Moderate                | No                                 |
| Docstring only | No                             | High (can explain why)  | No                                 |
| `void(...)`    | Yes                            | High (explicit no-op)   | Yes (`from deprecate import void`) |

If you use a strict linter that flags unused parameters (e.g. ruff's `ARG001`), `void(...)` is the cleanest solution that does not require `# noqa` comments.

## void(...) with methods

`void(...)` works identically in class methods. Reference `self` and all parameters when forwarding a deprecated method:

```python
from deprecate import deprecated, void


class ImageProcessor:
    def resize(self, width: int, height: int, interpolation: str = "bilinear") -> str:
        return f"Resized to {width}x{height} ({interpolation})"

    @deprecated(target=resize, deprecated_in="2.0", remove_in="3.0")
    def scale(self, width: int, height: int, interpolation: str = "bilinear") -> str:
        """Deprecated — renamed to resize()."""
        return void(width, height, interpolation)


proc = ImageProcessor()
print(proc.scale(800, 600))
```

<details>
  <summary>Output: <code>print(proc.scale(800, 600)</code></summary>

```
Resized to 800x600 (bilinear)
```

</details>

You do not need to pass `self` to `void(...)` — only the parameters your linter flags. `self` is implicitly used by method dispatch and is never flagged.

## Type annotation note

!!! info "No action needed in most cases"

    Modern versions of mypy and pyright handle decorated functions correctly and do not flag the return type mismatch in dead code paths. The workarounds below are only needed if your type checker explicitly complains.

`void(...)` returns `None`, which looks incompatible with a function annotated as `-> int`. In practice this is fine because:

1. **The body never runs.** `@deprecated` intercepts the call and forwards to the target before the body executes. The `return void(...)` line is dead code.

2. **Type checkers handle dead code leniently.** Since mypy and pyright can see the decorator, they generally do not flag return type mismatches in unreachable paths.

3. **If your type checker does complain**, two workarounds:

```python
from deprecate import deprecated, void
from typing import Any, cast


def new_func(x: int) -> int:
    return x * 2


# Option A: Use `pass` instead (no return statement at all)
@deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
def old_func_a(x: int) -> int:
    pass  # type: ignore[return-value]


# Option B: Cast the void return (technically dead code)
@deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
def old_func_b(x: int) -> int:
    return cast(int, void(x))
```

In practice, neither workaround is usually needed — modern versions of mypy handle decorated functions correctly and do not flag unreachable return statements.

## void(...) vs assert_no_warnings

These two utilities solve completely different problems. Their names might suggest a connection, but they operate in different contexts. `assert_no_warnings` is a test-time tool covered in [Testing Deprecated Code](audit.md#testing-deprecated-code).

|                    | `void(...)`                                                | `assert_no_warnings()`                                                  |
| ------------------ | ---------------------------------------------------------- | ----------------------------------------------------------------------- |
| **Purpose**        | Silence IDE "unused parameter" warnings in function bodies | Assert that no warnings of a given type are emitted during a code block |
| **Where used**     | Inside the body of a deprecated function stub              | In test code, as a context manager                                      |
| **Runtime effect** | None (accepts args, returns `None`)                        | Captures warnings and raises `AssertionError` if any match              |
| **Import**         | `from deprecate import void`                               | `from deprecate import assert_no_warnings`                              |

```python
from deprecate import deprecated, void, assert_no_warnings


def new_func(x: int) -> int:
    return x * 2


# void() — used in the deprecated function body (decoration-time concern)
@deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
def old_func(x: int) -> int:
    return void(x)


# assert_no_warnings — used in tests (runtime assertion concern)
with assert_no_warnings(FutureWarning):
    # Verify that calling the NEW function does not trigger any deprecation warning
    result = new_func(42)
print(result)
```

<details>
  <summary>Output: <code>print(result)</code></summary>

```
84
```

</details>

______________________________________________________________________

Next: [Audit Tools](audit.md) — validate decorator configuration, enforce removal deadlines, and detect deprecation chains in CI.
