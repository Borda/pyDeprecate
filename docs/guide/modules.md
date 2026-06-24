---
id: modules
description: Deprecating an entire Python module — in-place warn via PEP 562, redirect to a replacement module, and parent alias patterns with their limitations.
---

# Modules

This page covers how to deprecate an entire module using `deprecated_module()`. Three patterns are supported: emitting a `FutureWarning` on missing-attribute access in the same module (Mode 1), redirecting all attribute access to a replacement module (Mode 2), and exposing a deprecated module name as a package attribute (Mode 3). For deprecating individual functions see [Functions](functions.md); for classes see [Classes](classes.md).

## When to use `deprecated_module()`

Call `deprecated_module()` at the bottom of a module you want to mark deprecated. The function installs a PEP 562 `__getattr__` hook on the module, attaches `__deprecated__` metadata so that [`find_deprecation_wrappers()`](audit.md) can discover it, and emits a `FutureWarning` on attribute access.

!!! warning "PEP 562 real-attribute gap — `__getattr__` fires only for missing names"

    Python's PEP 562 `__getattr__` hook fires only when an attribute name is **not** already present in the module's `__dict__`. Functions, classes, and constants defined directly in the module are in `__dict__` at import time, so accessing them is **silent** — no warning is emitted. If you need a warning on every access to every name (including real attributes), use Mode 2 (redirect) instead and move the implementation to the new module.

## Mode 1 — in-place warn

Use this when you want to keep the module in place and only warn callers who access names that are not defined in the module (e.g., wildcard re-exports, legacy symbol names). Call `deprecated_module(__name__, ...)` at the bottom of `old_calculator.py`:

```python
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
# __getattr__ fires only for names NOT already in this module's __dict__.
# The `add` and `multiply` functions above are real attributes — accessing
# them is SILENT. Only truly absent names trigger the FutureWarning.
deprecated_module(
    __name__,
    deprecated_in="2.0",
    remove_in="3.0",
    message="Use `new_calculator` instead.",
)
```

Calling `import old_calculator; old_calculator.add(1, 2)` is **silent** because `add` is a real attribute. Accessing `old_calculator.missing_symbol` emits a `FutureWarning`. For full coverage, use Mode 2.

## Mode 2 — redirect to a replacement module

Use this when you rename an entire module. All attribute access on the old module name is forwarded to `new_calculator`, so callers get both a warning and the correct value regardless of which name they access.

```python
# old_calculator.py — DEPRECATED API — redirect to new_calculator
# NEW API — same functions live in new_calculator.py

import importlib
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
# Map old attr name to new attr name, or to None to raise AttributeError
deprecated_module(
    __name__,
    target=_new_calculator,
    attrs_mapping={"legacy_add": "add", "removed_fn": None},
    deprecated_in="2.0",
    remove_in="3.0",
)
```

`old_calculator.legacy_add` warns and forwards to `new_calculator.add`. `old_calculator.removed_fn` warns and raises `AttributeError`. All other names fall through to `new_calculator`.

## Mode 3 — parent alias via `deprecated_instance`

When you reorganise a package and want the old sub-module name to remain accessible as an attribute on the parent package, use [`deprecated_instance()`](classes.md#deprecating-constants-and-instances) in the parent `__init__.py`. This does not require a new API — it reuses the existing proxy mechanism.

```python
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

## PEP 562 real-attribute gap and star-import limitation

Two limitations apply to all module-level `__getattr__` hooks (PEP 562):

**Real attributes are silent.** Names already present in the module's `__dict__` at import time (functions, classes, constants defined with `def`, `class`, or `=`) bypass `__getattr__` entirely. To warn on those names, move the implementation to the new module and use Mode 2 (redirect) so that the old module contains no real attributes to shadow the hook.

**Star imports do not warn.** `from old_calculator import *` reads `__all__` (or all public names) directly from the module's `__dict__` at import time without calling `__getattr__`, so no `FutureWarning` is emitted. If star imports are a concern, document the change in the module docstring and in your release notes.

## Audit integration

`find_deprecation_wrappers()` discovers modules marked with `deprecated_module()` the same way it discovers function and class wrappers — by reading the `__deprecated__` attribute attached to the module object:

```python
# phmdoctest:skip — CI template: replace my_package with your actual package
import my_package
from deprecate import find_deprecation_wrappers

deprecated_items = find_deprecation_wrappers(my_package)
module_items = [r for r in deprecated_items if r.function.endswith("(module)")]
```

## See also

- [Use Cases overview](use-cases.md) — start here for a guided tour of all deprecation patterns
- [Functions](functions.md) — deprecating individual functions and methods
- [Classes](classes.md) — class, Enum, dataclass, and instance deprecation
- [Audit Tools](audit.md) — enforce removal deadlines and detect deprecation chains in CI
- [Troubleshooting](../troubleshooting.md) — common errors and fixes

______________________________________________________________________

Next: [Advanced](advanced.md) — docstring updates, `args_extra`, testing helpers, class/static methods, generators.
