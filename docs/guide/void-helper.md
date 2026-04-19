---
description: Learn when and how to use pyDeprecate's void() helper function as a no-op body for deprecated functions with automatic call forwarding.
---

# The void() Helper

When `@deprecated` is used with a `target` callable, the deprecated function's body is never executed — every call is intercepted and forwarded to the replacement before the body runs. This creates a practical problem: IDEs and linters see the parameters listed in the signature but never used in the body, and they flag them as unused variables.

`void()` is a no-op helper that accepts any number of arguments and returns `None`. Its sole purpose is to silence those IDE warnings by giving the parameters a nominal "use" that communicates intent — the body will never execute, and that is deliberate. It has no runtime effect whatsoever.

## When to use void()

Use `void()` when all of the following are true:

- The deprecated function forwards to a `target` callable (not `target=None` or `target=True`).
- Your IDE or linter is flagging the function parameters as unused.
- You want the code to clearly communicate that the body is intentionally empty rather than accidentally incomplete.

If you prefer, a bare `pass` statement or a standalone docstring are equally valid — pyDeprecate does not require `void()`. Use whichever form makes the intent clearest to your team.

## Basic usage

The example below shows `void()` used as the return expression of a deprecated wrapper. The call to `old_add` is forwarded to `new_add` immediately — the `return void(a, b)` line is never reached, but its presence tells the IDE that `a` and `b` are intentionally accepted and ignored.

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

`void()` is purely for IDE convenience and has no runtime effect. It simply returns `None` after accepting any arguments.

______________________________________________________________________

Next: [Audit Tools](audit.md) — validate decorator configuration, enforce removal deadlines, and detect deprecation chains in CI.
