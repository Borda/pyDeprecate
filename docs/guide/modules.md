---
id: modules
description: Deprecating an entire Python module — in-place warn intercepting all public attribute access, redirect to a replacement module, and parent alias patterns with their limitations.
---

# Modules

`deprecated_module()` marks an entire Python module as deprecated by replacing its `__class__` with an intercepting wrapper, so every public attribute access on the old module name emits a `FutureWarning`. Three patterns are supported: warn in place without moving any code (Mode 1), redirect all attribute access to a replacement module (Mode 2), and expose a deprecated module name as a package attribute (Mode 3). For deprecating individual functions see [Functions](functions.md); for classes see [Classes](classes.md).

## When to use `deprecated_module()`

Reach for `deprecated_module()` when a module is being replaced or renamed and you want every attribute access on the old name to warn callers automatically. Place the call at the bottom of the file being deprecated: it reassigns the module's `__class__` to an intercepting wrapper, attaches `__deprecated__` metadata so that [`find_deprecation_wrappers()`](audit.md) can discover it, and emits a `FutureWarning` on every public attribute access.

## Mode 1 — in-place warn

Use this when you want to keep the module in place and warn on every attribute access. Call `deprecated_module(__name__, ...)` at the bottom of `old_calculator.py`:

```python
# phmdoctest:skip — old_calculator.py in-place warn template
# old_calculator.py — DEPRECATED API; use new_calculator instead
# NEW API — same functions now live in new_calculator.py

from deprecate import deprecated_module


def add(a: float, b: float) -> float:
    """Add two numbers. Prefer new_calculator.add."""
    return a + b


def multiply(a: float, b: float) -> float:
    """Multiply two numbers. Prefer new_calculator.multiply."""
    return a * b


# Mark this module deprecated — call once at the bottom.
deprecated_module(
    __name__,
    deprecated_in="2.0",
    remove_in="3.0",
    message="Use `new_calculator` instead.",
)
```

`import old_calculator; old_calculator.add(1, 2)` emits a `FutureWarning` and returns the result. Every public attribute access — real functions, classes, constants — warns because `deprecated_module()` replaces the module's `__class__` with an intercepting wrapper.

## Mode 2 — redirect to a replacement module

Use this when you rename an entire module. All attribute access on the old module name is forwarded to `new_calculator`, so callers get both a warning and the correct value regardless of which name they access.

```python
# phmdoctest:skip — CI template; new_calculator is not installed
# old_calculator.py — DEPRECATED API — redirect to new_calculator
# NEW API — same functions live in new_calculator.py

from deprecate import deprecated_module

# Load the replacement module to attach as redirect target
import new_calculator as _new_calculator


deprecated_module(
    __name__,
    target=_new_calculator,  # every unknown attr forwarded here
    deprecated_in="2.0",
    remove_in="3.0",
    message="Use `new_calculator` instead.",
)
```

Now `import old_calculator; old_calculator.add(1, 2)` emits a `FutureWarning` and returns the result from `new_calculator.add`. The `attrs_mapping` parameter lets you rename specific attributes during the redirect:

```python
from deprecate import deprecated_module

# phmdoctest:skip — attrs_mapping continuation; requires new_calculator
# Map old attr name to new attr name, or to None to raise AttributeError.
# Keys are the names callers use today (the original API names).
deprecated_module(
    __name__,
    target=_new_calculator,
    attrs_mapping={"compute": "add", "beta_feature": None},
    deprecated_in="2.0",
    remove_in="3.0",
)
```

`old_calculator.compute` warns and forwards to `new_calculator.add` (the function was renamed). `old_calculator.beta_feature` warns and raises `AttributeError` (no replacement). All other names fall through to `new_calculator`.

## Mode 3 — parent alias via `deprecated_instance`

When you reorganise a package and want the old sub-module name to remain accessible as an attribute on the parent package, use [`deprecated_instance()`](classes.md#deprecating-constants-and-instances) in the parent `__init__.py`. This does not require a new API — it reuses the existing proxy mechanism.

```python
# phmdoctest:skip — CI template; my_package is not installed
# my_package/__init__.py

import my_package.new_calculator as _new_calculator
from deprecate import deprecated_instance

# DEPRECATED API — expose `old_utils` as a package attribute pointing to new_calculator
old_utils = deprecated_instance(
    _new_calculator,
    name="old_utils",
    deprecated_in="2.0",
    remove_in="3.0",
    message="Use `my_package.new_calculator` instead.",
)
```

`import my_package; my_package.old_utils.add(1, 2)` emits a `FutureWarning` on the first attribute access and forwards to `new_calculator`.

## Star-import limitation

Star imports (`from old_calculator import *`) read `__all__` (or all public names) directly from the module's `__dict__` at import time and bypass `__getattribute__` entirely, so no `FutureWarning` is emitted. If star imports are a concern, document the change in the module docstring and in your release notes.

## Audit integration

`find_deprecation_wrappers()` discovers modules marked with `deprecated_module()` the same way it discovers function and class wrappers — by reading the `__deprecated__` attribute attached to the module object:

```python
# phmdoctest:skip — CI template: replace my_package with your actual package
import my_package
from deprecate import find_deprecation_wrappers

deprecated_items = find_deprecation_wrappers(my_package)
module_items = [r for r in deprecated_items if r.api_type == "module"]
```

## See also

- [Use Cases overview](use-cases.md) — start here for a guided tour of all deprecation patterns
- [Functions](functions.md) — deprecating individual functions and methods
- [Classes](classes.md) — class, Enum, dataclass, and instance deprecation
- [Audit Tools](audit.md) — enforce removal deadlines and detect deprecation chains in CI
- [Troubleshooting](../troubleshooting.md) — common errors and fixes

______________________________________________________________________

Next: [Advanced](advanced.md) — docstring updates, `args_extra`, testing helpers, class/static methods, generators.
