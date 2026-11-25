---
id: convert-existing
description: Convert hand-written deprecations to pyDeprecate with before and after examples for argument renames, class renames and module moves, plus how to check the result.
---

# Convert existing deprecations

Most projects already deprecate by hand: a `warnings.warn` call, a `kwargs.pop`, a subclass that only exists to warn, or a shim module. Each of these has a one-decorator replacement that also records `deprecated_in` and `remove_in`, so the [audit tools](../audit/index.md) can enforce the deadline.

Start from what your code looks like today.

![Four hand-written deprecation patterns and the pyDeprecate call that replaces each: a warn-then-call function becomes @deprecated with a target, an argument handled by hand becomes TargetMode.ARGS_REMAP, a subclass that only warns becomes @deprecated_class, and a shim module becomes deprecated_module](../assets/images/pydeprecate-convert-map.svg)

| Hand-written pattern                                     | Replace with                                | Where                                                                            |
| -------------------------------------------------------- | ------------------------------------------- | -------------------------------------------------------------------------------- |
| A function that warns, then calls its replacement        | `@deprecated(target=new_function, ...)`     | [Compare tools](compare-python-deprecation-tools.md#migrating-from-warningswarn) |
| `deprecation` or `Deprecated` decorators                 | `@deprecated(target=new_function, ...)`     | [Compare tools](compare-python-deprecation-tools.md#migrating-from-deprecation)  |
| An argument handled by hand (`if old is not None: warn`) | `TargetMode.ARGS_REMAP` with `args_mapping` | [Argument rename](#rename-an-argument)                                           |
| A subclass or alias that warns in `__init__`             | `@deprecated_class(target=NewClass, ...)`   | [Class rename](#rename-a-class)                                                  |
| A shim module that warns at import                       | `deprecated_module(...)`                    | [Module move](#move-a-module)                                                    |
| `@warnings.deprecated` for static checkers               | Keep it and stack `@deprecated` underneath  | [Static type checkers](../usage/functions.md#static-type-checkers-pep-702)       |

## Rename an argument

Callers pass `num_proc=4` today, and the function handles the old name itself.

```python
import warnings
from typing import Optional


# DEPRECATED API — the function warns and renames the argument by hand
def load_dataset(path: str, *, workers: int = 0, num_proc: Optional[int] = None) -> dict:
    if num_proc is not None:
        warnings.warn("`num_proc` is deprecated; use `workers`.", DeprecationWarning, stacklevel=2)
        workers = num_proc
    return {"path": path, "workers": workers}


print(load_dataset("data", num_proc=4))  # warns: DeprecationWarning
```

<details><summary>Output: <code>load_dataset("data", num_proc=4)</code></summary>

```text
{'path': 'data', 'workers': 4}
```

</details>

With `TargetMode.ARGS_REMAP` the function keeps only the new name, and the decorator renames the old one for callers who still pass it.

```python
from deprecate import TargetMode, deprecated


# NEW API — only the replacement argument remains in the signature
@deprecated(
    target=TargetMode.ARGS_REMAP,
    args_mapping={"num_proc": "workers"},
    deprecated_in="1.4",
    remove_in="2.0",
)
def load_dataset(path: str, *, workers: int = 0) -> dict:
    return {"path": path, "workers": workers}


print(load_dataset("data", num_proc=4))  # warns: FutureWarning
print(load_dataset("data", workers=2))
```

<details><summary>Output: <code>the two load_dataset calls</code></summary>

```text
{'path': 'data', 'workers': 4}
{'path': 'data', 'workers': 2}
```

</details>

The second call uses the new name and **does not warn**: `ARGS_REMAP` warns *only* for callers who still pass the old argument. Map an argument to `None` to drop it. See [Functions](../usage/functions.md) for the full set of argument options.

## Rename a class

The old name survives as a subclass that warns when it is constructed.

```python
import warnings


# NEW API — the class callers should move to
class ImageClassifier:
    def __init__(self, classes: int) -> None:
        self.classes = classes


# DEPRECATED API — a subclass kept only to warn
class Classifier(ImageClassifier):
    def __init__(self, *args, **kwargs) -> None:
        warnings.warn("`Classifier` is deprecated; use `ImageClassifier`.", DeprecationWarning, stacklevel=2)
        super().__init__(*args, **kwargs)


model = Classifier(classes=10)  # warns: DeprecationWarning
print(type(model).__name__, model.classes)
```

<details><summary>Output: <code>type(model).__name__, model.classes</code></summary>

```text
Classifier 10
```

</details>

`deprecated_class` replaces the subclass with a proxy that warns once and builds the new class.

```python
from deprecate import deprecated_class


# NEW API — the class callers should move to
class ImageClassifier:
    def __init__(self, classes: int) -> None:
        self.classes = classes


# DEPRECATED API — the old name builds the new class and warns
@deprecated_class(target=ImageClassifier, deprecated_in="1.4", remove_in="2.0")
class Classifier:
    pass


model = Classifier(classes=10)  # warns: FutureWarning
print(type(model).__name__, model.classes)
print(isinstance(model, Classifier), isinstance(model, ImageClassifier))
```

<details><summary>Output: <code>the model's type and isinstance checks</code></summary>

```text
ImageClassifier 10
True True
```

</details>

!!! warning "The instance type changes"

    The hand-written subclass returned a `Classifier`. The proxy returns the new class, so code that tests `type(model) is Classifier` stops matching. `isinstance` checks keep working. See [Limitations](../limitations.md) for what else a class proxy changes.

## Move a module

A hand-written shim warns when the old module is first imported, then re-exports the new one.

```python
# phmdoctest:skip — shim module template; old_calculator and new_calculator are not installed
# old_calculator.py — DEPRECATED API; a hand-written shim
import warnings

warnings.warn("`old_calculator` moved to `new_calculator`.", DeprecationWarning, stacklevel=2)

from new_calculator import *  # noqa: E402, F401, F403
```

`deprecated_module` keeps the shim but warns on every attribute access, including names already defined in the module, and forwards the rest to the replacement.

```python
# phmdoctest:skip — shim module template; old_calculator and new_calculator are not installed
# old_calculator.py — DEPRECATED API; redirects to new_calculator
# NEW API — the same functions now live in new_calculator.py
import new_calculator as _new_calculator
from deprecate import deprecated_module

deprecated_module(
    __name__,
    target=_new_calculator,
    deprecated_in="2.0",
    remove_in="3.0",
    message_template="Use `new_calculator` instead.",
)
```

Place the call at the bottom of the file. See [Modules](../usage/modules.md) for the three modes.

## Check the conversion

1. **Run your tests with the warning as an error** so every remaining use of the old name is visible: `python -W error::FutureWarning -m pytest`.
2. **Run the audit** with `pydeprecate check src/your_package` to catch a mapping that names an argument the function does not have.
3. **Add the deadline gate** with `pydeprecate expiry src/your_package` so the removal is enforced. See [Audit and CI](../audit/ci-integration.md).
4. **Let an agent do the scan.** The `sunset` skill converts one deprecation at a time, and an adoption scan lists every candidate without editing. See [Agent recipes](agent-recipes.md).

<u>Convert one pattern at a time and rerun the tests after each.</u> A converted wrapper warns with `FutureWarning`, not the `DeprecationWarning` it replaces, so pytest filters and logging rules written for the old category need the same update.
