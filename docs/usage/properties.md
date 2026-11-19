---
id: properties
description: Deprecating Python properties and cached properties — getter-only, setter/deleter, dataclass field aliases, and decorator order rules.
---

# Properties

This page covers deprecation of `@property` and `@cached_property` descriptors: decorator order rules, all-accessor wrapping, setter/deleter chains, and the dataclass field alias pattern. For function deprecation see [Functions](functions.md); for class deprecation see [Classes](classes.md).

## Properties and cached properties

`@deprecated` works with `@property` and `@cached_property`. The decorator *only* adds a `FutureWarning` at access time; it does **not** forward reads or writes to another property.

| Order                                                                                     | Accessors that warn                                                                             |
| ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Outer: `@deprecated @property`, or explicit `deprecated(...)(property(fget, fset, fdel))` | All three accessors (`fget`, `fset`, `fdel`) warn on read, write, **and** delete.               |
| Inner-first: `@property @deprecated`                                                      | Only `fget` warns; apply `@deprecated` to setter and deleter separately if they must also warn. |

For a getter-only property, either decorator order is valid.

```python
from functools import cached_property

from deprecate import deprecated


class Config:
    @property
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    def timeout(self) -> int:
        return 30

    @cached_property
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    def base_url(self) -> str:
        return "https://example.com"


print(Config().timeout)
```

<details>
  <summary>Output: <code>Config().timeout</code></summary>

```
30
```

</details>

The `FutureWarning` fires on **attribute access** (`obj.timeout`), not on a call.

!!! note "Cached properties warn on first access only"

    For `@cached_property`, subsequent accesses return the cached value without emitting another warning. The cache is filled after the first read. While that value remains cached, later reads of the same object do not reach pyDeprecate. Deleting the cached attribute makes the next read invoke the descriptor again, which can warn when the budget allows.

    - With `num_warns=-1`, **each new object** warns once, on its first access.
    - The default `num_warns=1` budget is **shared by all objects**, so only the first object to read the property warns.

**Decorator order for getter-only properties.** Either `@property @deprecated` (inner) or `@deprecated @property` (outer) order works for getter-only properties. Inner order is conventional — the deprecated decorator is closer to the `def`. For properties with a setter or deleter, use outer order; see the next section.

### Deprecating a property with a setter or deleter

When the property being deprecated has a setter or deleter, all three accessors (`fget`, `fset`, `fdel`) are **wrapped automatically** — each fires a `FutureWarning`. Both the chain-style decorator pattern and the explicit construction pattern work:

```python
from deprecate import deprecated


class Config:
    def __init__(self) -> None:
        self._timeout: int = 30

    # Outer order required: @deprecated @property wraps fget, fset, and fdel
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    @property
    def timeout(self) -> int:
        return self._timeout

    @timeout.setter
    def timeout(self, value: int) -> None:
        self._timeout = value

    @timeout.deleter
    def timeout(self) -> None:
        del self._timeout


cfg = Config()
cfg.timeout = 10  # FutureWarning: write
print(cfg.timeout)  # FutureWarning: read; prints 10
del cfg.timeout  # FutureWarning: delete
```

<details>
  <summary>Output: <code>cfg.timeout</code></summary>

```
10
```

</details>

`obj.timeout` fires `FutureWarning` on **read**, `obj.timeout = value` fires on **write**, and `del obj.timeout` fires on **delete**.

!!! tip "Per-accessor warnings"

    To warn on the getter only, apply `@deprecated` directly to `fget` using inner order (`@property @deprecated`) instead of wrapping the full `property` object; the setter and deleter then remain silent.

    Each accessor (`fget`, `fset`, `fdel`) has its own warning counter — assert read, write, and delete warnings in separate `pytest.warns` blocks, or use `num_warns=-1` to disable per-accessor deduplication.

The explicit `property(fget, fset[, fdel])` construction also works:

```python
from deprecate import deprecated


def _timeout_fget(self) -> int:
    return self._timeout


def _timeout_fset(self, value: int) -> None:
    self._timeout = value


def _timeout_fdel(self) -> None:
    del self._timeout


class Config:
    def __init__(self) -> None:
        self._timeout: int = 30

    timeout = deprecated(deprecated_in="1.0", remove_in="2.0")(property(_timeout_fget, _timeout_fset, _timeout_fdel))
```

`find_deprecation_wrappers` discovers explicit-construction properties via the accessor that carries `__deprecation_config__` metadata. For setter-only properties (`property(None, fset)`), it discovers via `fset`; if `fget` is plain (not deprecated), it falls through to `fset` or `fdel`.

### Strict mode: `from deprecate import property`

The inner order `@property @deprecated` wraps only `fget`. If you later add a setter or deleter with `@value.setter` / `@value.deleter`, those accessors are rebuilt from the plain `property` base. They are **<u>silently unprotected</u>**: writes and deletes never warn.

This is easy to introduce by habit, because the standard library puts `@property` outermost.

To catch this at definition time, import the strict `property` replacement. It raises `TypeError` the moment a getter already carrying `@deprecated` metadata is handed to it — *before* any instance is created:

```python
# phmdoctest:skip — TypeError raised at class-body time
from deprecate import deprecated, property  # `property` shadows the builtin in this module only


class Config:
    def __init__(self) -> None:
        self._timeout: int = 30

    # ! Raises TypeError at class-body evaluation time — inner order detected
    @property
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    def timeout(self) -> int:
        return self._timeout
```

The fix is to switch to the **canonical outer order** `@deprecated(...) @property`, which wraps every accessor and survives later setter/deleter additions. The outer order works unchanged with the strict `property`:

```python
from deprecate import deprecated, property


class Config:
    def __init__(self) -> None:
        self._timeout: int = 30

    # NEW: outer order — strict `property` passes through, all accessors warn
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    @property
    def timeout(self) -> int:
        return self._timeout
```

The strict `property` is a subclass of the builtin, so `isinstance(obj, property)` checks and the audit scanner treat it transparently. Importing it shadows the builtin **only in the importing module** — modules that do not import it keep the builtin behaviour, so the strictness is purely opt-in.

!!! tip "Auditing existing code: the `inner_order_property` flag"

    Even without the strict import, `find_deprecation_wrappers` flags every inner-order `@property`. Each `DeprecationWrapperInfo` carries `inner_order_property=True` when the wrapper is a plain `property` whose `fget` is deprecation-wrapped. The flag fires for the getter-only shape too, because the canonical form is the outer order.

    CI pipelines can reject any result with this flag set to eliminate the silent write/delete gap across a whole package. See the [Audit guide](../audit/index.md) for the full CI integration pattern.

### Deprecated property alias on a dataclass

When a dataclass field is renamed, define a property with the old name that delegates to the new field in its accessor body. `@deprecated` adds a `FutureWarning` to each accessor — the delegation itself is *plain Python* in the method body, not something the library provides.

**Read-only alias (warns on read only):**

```python
from dataclasses import dataclass

from deprecate import deprecated


@dataclass
class Config:
    timeout_ms: int = 30_000  # renamed from `timeout`

    @property
    @deprecated(deprecated_in="1.0", remove_in="2.0")
    def timeout(self) -> int:
        """Deprecated — use ``timeout_ms`` instead."""
        return self.timeout_ms // 1000


cfg = Config(timeout_ms=5_000)
print(cfg.timeout)  # FutureWarning fired; prints 5
```

<details>
  <summary>Output: <code>cfg.timeout</code></summary>

```
5
```

</details>

**Read-write alias (warns on read and write):** use the outer order and chain `.setter`:

```python
from dataclasses import dataclass

from deprecate import deprecated


@dataclass
class Config:
    timeout_ms: int = 30_000  # renamed from `timeout`

    @deprecated(deprecated_in="1.0", remove_in="2.0")
    @property
    def timeout(self) -> int:
        return self.timeout_ms // 1000

    @timeout.setter
    def timeout(self, value: int) -> None:
        self.timeout_ms = value * 1000


cfg = Config(timeout_ms=5_000)
print(cfg.timeout)  # FutureWarning fired; prints 5
cfg.timeout = 10  # FutureWarning fired; sets timeout_ms = 10_000
print(cfg.timeout_ms)  # prints 10_000
```

<details>
  <summary>Output: <code>cfg.timeout; cfg.timeout_ms</code></summary>

```
5
10000
```

</details>

`cfg.timeout` fires `FutureWarning` (from `@deprecated`) and the getter body returns `cfg.timeout_ms // 1000`. `cfg.timeout = 5` fires `FutureWarning` and the setter body assigns `cfg.timeout_ms = 5000`.

!!! warning "Do not shadow a dataclass field"

    Do **not** use the same name as an existing dataclass field for the deprecated property. The `@dataclass`-generated `__init__` performs `self.field = value`, which conflicts with a property descriptor of the same name. Use a different name for the deprecated alias and keep the dataclass field under its new name.

The same pattern works on regular (non-dataclass) classes — replace field access with `self._attr` lookups in the accessor body. `@deprecated` *only* adds the warning in either case.

`@deprecated` rejects `target=<callable>` on a `property` with `TypeError`. Properties have three independent accessors (`fget`, `fset`, `fdel`); there is no single callable to forward to. Delegate in each accessor body as shown above.

## See also

- Start with [Use Cases overview](index.md) for a guided tour of all deprecation patterns.
- [Functions](functions.md) covers function and method deprecation patterns.
- [Classes](classes.md) covers class, Enum, dataclass, and instance deprecation.
- [Async](async.md) covers async functions and async generators.
- [Advanced](advanced.md) covers class/static methods and generator functions.
- Use [Audit Tools](../audit/index.md) to enforce removal deadlines in CI.
- [Troubleshooting](../troubleshooting.md) covers common errors and fixes.

______________________________________________________________________

Next: [Async](async.md) — deprecating async functions and async generators.
