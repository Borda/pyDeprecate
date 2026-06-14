---
id: classes
description: Deprecating Python classes, Enums, dataclasses, module-level constants, and object instances — class forwarding, proxy wrappers, selective attribute deprecation, and multi-version stacking.
---

# Classes

This page covers deprecation patterns for classes, Enums, dataclasses, and module-level constants: forwarding an old class name to a replacement, wrapping with a transparent proxy, deprecating only selected attributes, and stacking multiple deprecation layers for multi-version migrations. For function deprecation see [Functions](functions.md).

## Class deprecation

Two common patterns here. First, renaming a method within a class: apply `@deprecated(target=execute)` on the old method name and calls forward to the new method. Second, deprecating an entire class by decorating `__init__` to emit a notice at instantiation time and optionally forward construction to a successor class.

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
#   The `run` was deprecated since v1.0 in favor of `your_module.execute`.
#   It will be removed in v2.0.
print(svc.run(5))
```

<details>
  <summary>Output: <code>svc.run(5)</code></summary>

```
10
```

</details>

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
#   The `Client` was deprecated since v0.2 in favor of `your_module.HttpClient`.
#   It will be removed in v0.4.
inst = Client(7)
print(inst.my_c)  # returns: 7
print(inst.my_d)  # returns: "efg"
```

<details>
  <summary>Output: <code>inst.my_d</code></summary>

```
7
efg
```

</details>

## Constants and instances

`deprecated_instance` wraps module-level objects (dicts, lists, custom objects) in a transparent proxy that emits a deprecation notice on attribute, item, or call access. Use `read_only=True` to prevent callers from mutating shared state through the deprecated alias.

Heads up: primitive protocol methods (arithmetic on `float`, concatenation on `str`) are not intercepted by the proxy. For primitive constants, wrap them in a container or update call sites directly. See [Troubleshooting](../troubleshooting.md#why-does-deprecated_instance-not-emit-a-notice-on-arithmeticcomparison-operators) for details.

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

<details>
  <summary>Output: <code>DEFAULTS["lr"]</code></summary>

```
0.001
```

</details>

## Enums and dataclasses

`deprecated_class()` wraps an Enum or dataclass in a transparent proxy that emits a deprecation notice on access and forwards attribute, item, and call operations to the replacement. Use `args_mapping` to rename or drop kwargs when the deprecated class is called. When `args_mapping` is provided without an explicit `target`, the proxy auto-resolves to `TargetMode.ARGS_REMAP` and warns **only when an old argument name is actually used** — matching the per-argument behaviour of `@deprecated(target=TargetMode.ARGS_REMAP, args_mapping=...)`. Callers already using the new argument names see no warning. Type checks (`isinstance`, `issubclass`) pass through without emitting notices, since they are structural checks rather than usage of the deprecated API. Use `args_extra` to inject fixed kwargs into every forwarded call, and `template_mgs` to override the default warning message — both work identically to their `@deprecated` counterparts.

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

<details>
  <summary>Output: <code>(p_new.x, p_new.y)</code></summary>

```
True
True
True
True
(3, 4)
(3.25, 4.75)
```

</details>

## Selective attribute deprecation

Use `attrs_mapping` on `deprecated_class()` to deprecate only specific attribute names — all other attributes pass through silently. This covers attribute renames, misspelling corrections (e.g. `color` → `colour`), and warn-only notices on individual attributes.

The mapping keys are the deprecated attribute names; values are either the canonical replacement name (string) or `None` for a warn-only notice with no rename. Reads, writes, and deletes on deprecated attribute names all warn and resolve against the active class. Non-listed attribute names pass through without any warning.
Non-`None` values must exist on the `target` class when `target=` is provided, or on the wrapped source class otherwise. Redirect chains such as `{"a": "b", "b": "c"}` are allowed at decoration time and reported by audit as `ChainType.STACKED`; cycles such as `{"a": "b", "b": "a"}` raise immediately.

### Decorator syntax — attribute rename

Apply `@deprecated_class(attrs_mapping=...)` at class definition time. Only the attribute names listed as keys emit a `FutureWarning`; all others pass through silently:

```python
from deprecate import deprecated_class


@deprecated_class(
    attrs_mapping={"color": "colour"},  # "color" is the deprecated spelling
    deprecated_in="2.0",
    remove_in="3.0",
)
class Palette:
    colour: str = "red"  # canonical name
    size: int = 10  # unlisted — silent passthrough, no warning


# Deprecated alias — warns: "The `color` was deprecated since v2.0 in favor of `Palette.colour`."
print(Palette.color)  # red

# Canonical names — silent passthrough, no warning
print(Palette.colour)  # red
print(Palette.size)  # 10
```

<details>
  <summary>Output: <code>Palette.color; Palette.colour; Palette.size</code></summary>

```
red
red
10
```

</details>

Wrapper form — equivalent to decorator syntax, useful when wrapping an already-existing class or applying deprecation outside the class definition:

```python
from deprecate import deprecated_class


class Config:
    colour: str = "red"
    size: int = 42
    timeout: int = 30


# Misspelling migration: "color" → "colour"; "size" is warn-only (no rename)
DeprecatedConfig = deprecated_class(
    attrs_mapping={"color": "colour", "size": None},
    deprecated_in="1.0",
    remove_in="2.0",
)(Config)

print(DeprecatedConfig.color)  # warns → returns Config.colour ("red")
print(DeprecatedConfig.colour)  # silent passthrough ("red")
print(DeprecatedConfig.size)  # warns (warn-only, size=42 unchanged)
```

<details>
  <summary>Output: <code>DeprecatedConfig.color; DeprecatedConfig.colour; DeprecatedConfig.size</code></summary>

```
red
red
42
```

</details>

### Reads, writes, and deletes all redirect

The `attrs_mapping` interception applies to all three access modes. Writing to a deprecated attribute alias warns and sets the canonical attribute instead:

```python
from deprecate import deprecated_class


class Palette:
    colour: str = "red"


DeprecatedPalette = deprecated_class(
    attrs_mapping={"color": "colour"},  # "color" is the deprecated spelling
    deprecated_in="1.0",
    remove_in="2.0",
)(Palette)

# Write — warns: FutureWarning and redirects to Palette.colour
DeprecatedPalette.color = "blue"  # warns: FutureWarning

# Canonical attribute now holds the new value (no warning on canonical reads)
print(Palette.colour)  # blue
```

<details>
  <summary>Output: <code>Palette.colour</code></summary>

```
blue
```

</details>

### Warn-only with `None` redirect

Map a deprecated attribute to `None` to emit a warning on access without renaming anything. The attribute is fetched by its original name on the active class after the warning fires. Use this when an attribute is going away with no replacement:

```python
from deprecate import deprecated_class


class Widget:
    size: int = 42  # scheduled for removal — callers should stop reading it


DeprecatedWidget = deprecated_class(
    attrs_mapping={"size": None},  # warn-only, no rename
    deprecated_in="1.0",
    remove_in="2.0",
)(Widget)

# Warns: "The `size` was deprecated since v1.0. It will be removed in v2.0."
print(DeprecatedWidget.size)  # 42 — value still returned, just warned

# Second access is silent — num_warns=1 budget exhausted
print(DeprecatedWidget.size)  # 42 — no second warning
```

<details>
  <summary>Output: <code>DeprecatedWidget.size; DeprecatedWidget.size (second call)</code></summary>

```
42
42
```

</details>

### Per-attribute independent warning budgets

Each deprecated attribute name has its own warning counter. With `num_warns=1` (the default), accessing two different deprecated aliases each emits one warning independently — two warnings total, not one shared budget:

```python
from deprecate import deprecated_class


class Config:
    colour: str = "red"
    text: str = "hello"


proxy = deprecated_class(
    attrs_mapping={"color": "colour", "txt": "text"},
    deprecated_in="1.0",
    remove_in="2.0",
)(Config)

print(proxy.color)  # warns: FutureWarning — "color" budget consumed
print(proxy.txt)  # warns: FutureWarning — "txt" budget consumed (independent counter)

# Both budgets now exhausted — subsequent accesses are silent
print(proxy.color)  # silent
print(proxy.txt)  # silent
```

<details>
  <summary>Output: <code>proxy.color; proxy.txt; proxy.color (silent); proxy.txt (silent)</code></summary>

```
red
hello
red
hello
```

</details>

### Enum — deprecated member aliases

`attrs_mapping` works on Enum proxies too. Use it when Enum member names changed (for example, a casing convention migration) and callers may still be using the old names. Wrap the canonical Enum in a proxy that registers the deprecated names as aliases:

```python
from enum import Enum
from deprecate import deprecated_class


class Direction(Enum):
    NORTH = "N"
    SOUTH = "S"
    EAST = "E"
    WEST = "W"


# Wrap the canonical Enum and add deprecated lowercase aliases that redirect to canonical members
LegacyDirection = deprecated_class(
    attrs_mapping={"north": "NORTH", "south": "SOUTH"},
    deprecated_in="1.0",
    remove_in="2.0",
)(Direction)

# Deprecated lowercase alias — warns and returns the canonical Direction.NORTH member
print(LegacyDirection.north is Direction.NORTH)  # True

# Canonical uppercase name — silent passthrough, no warning
print(LegacyDirection.NORTH is Direction.NORTH)  # True
```

<details>
  <summary>Output: <code>LegacyDirection.north is Direction.NORTH; LegacyDirection.NORTH is Direction.NORTH</code></summary>

```
True
True
```

</details>

`attrs_mapping` can be combined with `target=NewClass`; listed attribute aliases redirect to their canonical counterparts on the target class. Unlisted attributes and calls continue to use the normal target-forwarding behaviour.

!!! note "Audit visibility"

    `find_deprecation_wrappers` discovers the proxy via its class-level `__deprecated__`. Individual `attrs_mapping` entries are data inside the single proxy config and are not emitted as separate `DeprecationWrapperInfo` records. All entries share the same `deprecated_in`/`remove_in` lifecycle.

### Explicit `TargetMode.ATTRS_REMAP` form

Passing `attrs_mapping` alone auto-resolves the mode to `TargetMode.ATTRS_REMAP`. The equivalent self-documenting form is to pass `target=TargetMode.ATTRS_REMAP` together with `attrs_mapping` — both forms are behaviourally identical, and the explicit form makes the intent visible at the call site without changing semantics:

```python
from deprecate import TargetMode, deprecated_class


class Palette:
    colour: str = "red"  # canonical name


# Explicit form — equivalent to passing `attrs_mapping` alone
DeprecatedPalette = deprecated_class(
    target=TargetMode.ATTRS_REMAP,
    attrs_mapping={"color": "colour"},
    deprecated_in="1.0",
    remove_in="2.0",
)(Palette)

print(DeprecatedPalette.color)  # warns → returns "red"
```

<details>
  <summary>Output: <code>DeprecatedPalette.color</code></summary>

```
red
```

</details>

Three misconfiguration combinations are caught at decoration time and emit a `UserWarning` (planned to become `TypeError` in `v1.0`):

| Misconfiguration                                        | Why it is wrong                                                                                                                            |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `target=TargetMode.NOTIFY` + `attrs_mapping=...`        | `NOTIFY` means "warn on every access"; `attrs_mapping` switches to selective per-attribute warning. They contradict each other — drop one. |
| `target=TargetMode.ATTRS_REMAP` without `attrs_mapping` | `ATTRS_REMAP` requires the deprecated attribute names listed via `attrs_mapping`. Without it the proxy has zero selective effect.          |
| `attrs_mapping={}` (empty dict)                         | An empty mapping has no effect. Remove it or add deprecated attribute names.                                                               |

`TargetMode.ATTRS_REMAP` is a **proxy-only** mode: applying it via `@deprecated(target=TargetMode.ATTRS_REMAP)` on a function, method, or property raises `TypeError` at decoration time, with the error message pointing to `deprecated_class(attrs_mapping=...)` as the correct API.

### Callable target with attribute redirection

When `deprecated_class` receives both `target=NewClass` and `attrs_mapping`, the two features compose cleanly: listed deprecated attribute aliases resolve against `NewClass`, while unlisted attributes and instantiation calls also forward to `NewClass`. Entries mapped to `None` keep the same attribute name on `NewClass`; for example, `attrs_mapping={"size": None}` warns and then reads, writes, or deletes `NewClass.size`. Use this pattern for a full class replacement where some attribute names changed between the old and the new class.

```python
from deprecate import deprecated_class


class Config:
    lr: float = 0.01  # canonical name in the new class
    batch_size: int = 32  # unchanged attribute


@deprecated_class(
    target=Config,
    attrs_mapping={"learning_rate": "lr"},  # "learning_rate" was renamed to "lr"
    deprecated_in="2.0",
    remove_in="3.0",
    num_warns=-1,
)
class LegacyConfig:
    learning_rate: float = 0.01  # old name — will warn
    lr: float = 0.01  # canonical alias also present


print(LegacyConfig.learning_rate)  # warns: FutureWarning — value from Config.lr

print(LegacyConfig.lr)  # silent — canonical name

print(LegacyConfig.batch_size)  # silent — unlisted attribute
```

<details>
  <summary>Output: <code>LegacyConfig.learning_rate; LegacyConfig.lr; LegacyConfig.batch_size</code></summary>

```
0.01
0.01
32
```

</details>

Instantiation calls are also forwarded to `Config` — `LegacyConfig(lr=0.05)` returns a `Config` instance. The `attrs_mapping` applies only to class-level attribute access on the proxy, not to the returned instance.

### Dataclass field renames

When the wrapped class is a `@dataclass`, `deprecated_class(attrs_mapping=...)` automatically covers **both surfaces** in a single call: attribute access on an existing instance (`obj.old_field`) and constructor kwargs (`DC(old_field=5)`) both emit `FutureWarning`. The auto-expand copies each `attrs_mapping` entry whose redirect target is a dataclass field into `args_mapping`, so you do not need to set `args_mapping` separately for a pure field rename. Entries already present in an explicit `args_mapping` are never overwritten — explicit user values always win.

```python
from dataclasses import dataclass
from deprecate import deprecated_class


@dataclass
class NewPoint:
    x: float = 0.0
    y: float = 0.0


OldPoint = deprecated_class(
    attrs_mapping={"px": "x", "py": "y"},
    deprecated_in="2.0",
    remove_in="3.0",
    num_warns=-1,
)(NewPoint)

# Constructor kwarg warns: FutureWarning — "px" remapped to "x"
pt = OldPoint(px=1.0)  # warns: FutureWarning
print(pt.x)
```

<details>
  <summary>Output: <code>pt.x</code></summary>

```
1.0
```

</details>

### Class-type compatibility

C-extension types, classes whose constructor accepts only positional-only parameters (e.g. `def __init__(self, val, /): ...`), and `tuple`/`frozenset` subclasses emit `UserWarning` at decoration time when `args_mapping` remaps a deprecated kwarg to a `POSITIONAL_ONLY` constructor parameter. At call time the proxy falls back to `setattr` for those entries instead of passing the remapped name as a constructor kwarg, so the instance is created and then the field is patched in — which behaves correctly for regular dataclasses but may not suit all class types. Run `validate_mapping_compatibility(module)` in CI to surface these patterns before they reach users.

### Combining attribute and argument deprecation

`attrs_mapping` and `args_mapping` operate on orthogonal surfaces: `attrs_mapping` intercepts class-level attribute access (`__getattr__` / `__setattr__` / `__delattr__` on the proxy), while `args_mapping` intercepts call arguments (`__call__`). Both can be combined on the same proxy when `target` is a callable class with renamed class attributes and a renamed constructor parameter.

```python
from deprecate import deprecated_class


class NewTrainer:
    epochs: int = 10  # class-level default, required for attrs_mapping validation
    lr: float = 0.01  # class-level default, required for attrs_mapping validation

    def __init__(self, lr: float = 0.01, epochs: int = 10) -> None:
        self.lr = lr
        self.epochs = epochs


@deprecated_class(
    target=NewTrainer,
    attrs_mapping={"n_epochs": "epochs"},  # class-level attribute rename
    args_mapping={"learning_rate": "lr"},  # constructor argument rename
    deprecated_in="2.0",
    remove_in="3.0",
    num_warns=-1,
)
class LegacyTrainer:
    pass


# Warning path 1 — args_mapping fires: old kwarg "learning_rate" remapped to "lr"
trainer = LegacyTrainer(learning_rate=0.05)  # warns: FutureWarning
print(trainer.lr)  # NewTrainer instance has lr=0.05

# Warning path 2 — attrs_mapping fires: class-level "n_epochs" redirects to "epochs"
default_epochs = LegacyTrainer.n_epochs  # warns: FutureWarning
print(default_epochs)  # value from NewTrainer.epochs
```

<details>
  <summary>Output: <code>trainer.lr; default_epochs</code></summary>

```
0.05
10
```

</details>

The two warning budgets are independent — exhausting one does not affect the other. Each deprecated name (argument or attribute) maintains its own counter, so `num_warns=1` (the default) allows each old name to warn exactly once before silencing.

#### Mixed redirect and warn-only entries

`attrs_mapping` values can be a string (redirect to a new name) or `None` (warn but keep the same attribute name, no rename).
Both forms can appear in the same mapping alongside `args_mapping`, making a single proxy the authoritative record for every deprecated surface on the class.

The example below deprecates a `Model` class that renamed its `gpu` attribute to `device` and retired the `cuda` flag entirely.
The constructor kwarg `n_layers` was also renamed to `num_layers`:

```python
from deprecate import deprecated_class


class Model:
    device: str = "cpu"
    num_layers: int = 4

    def __init__(self, num_layers: int = 4, device: str = "cpu") -> None:
        self.num_layers = num_layers
        self.device = device


@deprecated_class(
    target=Model,
    attrs_mapping={
        "cuda": None,  # warn-only — flag is being removed, still served from LegacyModel
        "gpu": "device",  # redirect — old name "gpu" resolves to Model.device
    },
    args_mapping={"n_layers": "num_layers"},  # constructor kwarg rename
    deprecated_in="3.0",
    remove_in="4.0",
    num_warns=-1,
)
class LegacyModel:
    cuda: bool = False  # being-removed attribute; must live on LegacyModel for warn-only validation


# 1. Constructor — args_mapping fires: "n_layers" remapped to "num_layers"
m = LegacyModel(n_layers=8)  # warns: FutureWarning
print(m.num_layers)

# 2. Attribute redirect — attrs_mapping "gpu" -> "device" fires
print(LegacyModel.gpu)  # warns: FutureWarning

# 3. Warn-only — "cuda" warns but is still served from LegacyModel.cuda
print(LegacyModel.cuda)  # warns: FutureWarning
```

<details>
  <summary>Output: <code>m.num_layers; LegacyModel.gpu; LegacyModel.cuda</code></summary>

```
8
cpu
False
```

</details>

`"cuda": None` emits a `FutureWarning` on every access but serves the value from `LegacyModel.cuda` (the source class) because `Model` does not define `cuda`.
`"gpu": "device"` warns and redirects the lookup to `Model.device`.
Validation at decoration time requires that every `None`-value key exists on at least one of the two classes, so `cuda` must be defined on `LegacyModel` (or on `Model` if keeping it in the new API).

!!! note "Audit tip — mapping compatibility"

    After combining `attrs_mapping` and `args_mapping`, run `validate_mapping_compatibility(module)` from the audit module in CI to surface any `args_mapping` entries that remap a deprecated kwarg to a `POSITIONAL_ONLY` constructor parameter — those fall back to `setattr` at call time instead of forwarding the kwarg.
    The function returns a list of `DeprecationWrapperInfo` objects whose `args_mapping_positional_only` field is non-empty.
    See the [Audit guide](audit.md) for the full CI integration pattern.

#### Stacking `deprecated_class()` for multi-version deprecations

Use a **single `deprecated_class()` call** when all attributes and arguments share the same `deprecated_in`/`remove_in` — it is the simplest form and keeps both mappings in one place.

**Stack two `@deprecated_class()` decorators** when different attributes were deprecated at different releases and each rename needs its own version pair.
A common scenario: a library renamed `steps` in v0.8 and `lr` in v1.0 — each rename carries its own removal deadline.

```python
from deprecate import deprecated_class


# outer layer: v1.0 rename (lr → learning_rate, remove in v2.0)
@deprecated_class(
    attrs_mapping={"lr": "learning_rate"},
    deprecated_in="1.0",
    remove_in="2.0",
)
# inner layer: v0.8 rename (steps → max_steps, remove in v1.0)
@deprecated_class(
    attrs_mapping={"steps": "max_steps"},
    deprecated_in="0.8",
    remove_in="1.0",
)
class LegacyConfig:
    lr: float = 1e-3  # deprecated since 1.0
    learning_rate: float = 1e-3  # canonical
    steps: int = 1000  # deprecated since 0.8
    max_steps: int = 1000  # canonical


cfg = LegacyConfig()
print(cfg.lr)  # warns: FutureWarning (deprecated in 1.0, remove in 2.0)
print(cfg.steps)  # warns: FutureWarning (deprecated in 0.8, remove in 1.0)
print(isinstance(cfg, LegacyConfig))
```

<details>
  <summary>Output: <code>cfg.lr; cfg.steps; isinstance(cfg, LegacyConfig)</code></summary>

```
0.001
1000
True
```

</details>

Each proxy layer carries its own `deprecated_in`/`remove_in`, so attribute-access warnings are version-accurate — `cfg.lr` reports the v1.0 deadline while `cfg.steps` reports the earlier v0.8 deadline.
Stacking is fully supported: `isinstance()` and `issubclass()` resolve through the proxy chain, and instantiation fires at most one global warning. When stacking two `ATTRS_REMAP` layers, only the innermost layer's instantiation warning fires — the outer layer's version pair appears only in attribute-access warnings for that layer's keys.

### Chained redirect

`attrs_mapping` supports multi-hop rename chains. `{"num_iters": "num_steps", "num_steps": "max_steps"}` is a valid chain — accessing `proxy.num_iters` warns once (for `num_iters`) and resolves directly to the value stored under `num_steps` on the active class; accessing `proxy.num_steps` warns once (for `num_steps`) and resolves to `max_steps`. Audit reports this mapping structure as `ChainType.STACKED`. Cycles such as `{"a": "b", "b": "a"}` raise `ValueError` at decoration time.

Every non-`None` redirect target in the chain must be a static class attribute. In the example below, `num_steps` must exist on the class because it is a redirect target for `num_iters`:

```python
from deprecate import deprecated_class


class TrainLoop:
    max_steps: int = 200
    num_steps: int = max_steps  # redirect target — must exist as a static class attribute


proxy = deprecated_class(
    attrs_mapping={"num_iters": "num_steps", "num_steps": "max_steps"},
    deprecated_in="2.0",
    remove_in="3.0",
    num_warns=-1,
)(TrainLoop)


val1 = proxy.num_iters  # warns: FutureWarning — deprecated v1.0 name
print(val1)

val2 = proxy.num_steps  # warns: FutureWarning — deprecated v2.0 name
print(val2)

val3 = proxy.max_steps  # silent — canonical name
print(val3)
```

<details>
  <summary>Output: <code>val1; val2; val3</code></summary>

```
200
200
200
```

</details>

Each deprecated name in the chain fires exactly one warning per access (not two). The resolution is a single lookup hop: `proxy.num_iters` warns for `num_iters` and then reads `TrainLoop.num_steps` directly, which at the class level holds the same value as `max_steps`.

### Nested proxy wrappers

A `deprecated_class` proxy can wrap another `deprecated_class` proxy. The inner proxy handles selective attribute deprecation; the outer proxy adds a blanket class-level deprecation warning on every access regardless of attribute name. The two warning budgets are independent.

```python
from deprecate import deprecated_class


class Palette:
    colour: str = "red"
    color: str = colour  # deprecated alias kept for backwards compatibility


# Inner proxy: warns only when the deprecated alias "color" is accessed
selective_proxy = deprecated_class(
    attrs_mapping={"color": "colour"},
    deprecated_in="1.0",
    remove_in="2.0",
    num_warns=-1,
)(Palette)

# Outer proxy: warns on every attribute access regardless of name
blanket_proxy = deprecated_class(
    deprecated_in="1.0",
    remove_in="2.0",
    num_warns=-1,
)(selective_proxy)


# Accessing the deprecated alias through the outer proxy: two warnings fire —
# one from the outer blanket proxy ("Palette" is deprecated) and one from the
# inner selective proxy ("color" is deprecated in favor of "colour").
_ = blanket_proxy.color  # warns: FutureWarning × 2 — outer blanket + inner selective

# Accessing the canonical name through the outer proxy: one warning fires —
# only the outer blanket proxy warns; the inner proxy forwards silently.
_ = blanket_proxy.colour  # warns: FutureWarning × 1 — outer blanket only
```

The outer proxy issues its blanket class-deprecation warning first; the inner proxy then handles the attribute redirect. Two warnings fire for `blanket_proxy.color` — one per proxy layer. Accessing `blanket_proxy.colour` fires only the outer proxy warning because `colour` is not listed in the inner proxy's `attrs_mapping`.

### Real-world migration: ML training config

The following end-to-end example shows a typical ML library migration where a `TrainingConfig` dataclass renames several fields across versions. Existing code using the old attribute names continues to work with deprecation notices guiding users toward the canonical API.

Migration summary:

- v1.0 → v2.0: `lr` renamed to `learning_rate`, `n_epochs` renamed to `max_epochs`
- v2.0: `size` attribute removed with no replacement (warn-only, `None` redirect)
- Constructor: `hidden_dim` renamed to `hidden_size`

```python
from dataclasses import dataclass
from deprecate import deprecated_class, find_deprecation_wrappers


@dataclass
class TrainingConfig:
    learning_rate: float = 0.001
    max_epochs: int = 100
    hidden_size: int = 256


@deprecated_class(
    target=TrainingConfig,
    attrs_mapping={
        "lr": "learning_rate",  # v2.0 rename
        "n_epochs": "max_epochs",  # v2.0 rename
        "size": None,  # removed in v2.0 — warn-only, no replacement
    },
    args_mapping={"hidden_dim": "hidden_size"},  # constructor rename
    deprecated_in="2.0",
    remove_in="3.0",
)
class LegacyTrainingConfig:
    lr: float = 0.001
    learning_rate: float = 0.001
    n_epochs: int = 100
    max_epochs: int = 100
    size: int = 128  # removed in v2.0
    hidden_size: int = 256


# Old attribute names still work — each emits one FutureWarning
print(LegacyTrainingConfig.lr)  # warns: FutureWarning — "lr" → "learning_rate"
print(LegacyTrainingConfig.n_epochs)  # warns: FutureWarning — "n_epochs" → "max_epochs"
print(LegacyTrainingConfig.size)  # warns: FutureWarning — "size" removed (no replacement)
```

<details>
  <summary>Output: <code>LegacyTrainingConfig.lr; LegacyTrainingConfig.n_epochs; LegacyTrainingConfig.size</code></summary>

```
0.001
100
128
```

</details>

```python
# phmdoctest:skip
# Old constructor argument still works — emits one FutureWarning
cfg = LegacyTrainingConfig(hidden_dim=512)  # warns: FutureWarning
print(cfg.hidden_size)
```

<details>
  <summary>Output: <code>cfg.hidden_size</code></summary>

```
512
```

</details>

```python
# phmdoctest:skip
# Audit tools discover the proxy — useful for CI expiry checks
import sys
import types

mod = types.ModuleType("my_ml_lib")
mod.LegacyTrainingConfig = LegacyTrainingConfig
sys.modules["my_ml_lib"] = mod

results = find_deprecation_wrappers(mod)
print(results[0].function, results[0].deprecated_info.deprecated_in)
```

<details>
  <summary>Output: <code>results[0].function; results[0].deprecated_info.deprecated_in</code></summary>

```
LegacyTrainingConfig 2.0
```

</details>

## See also

- [Use Cases overview](use-cases.md) — start here for a guided tour of all deprecation patterns
- [Functions](functions.md) — function and method deprecation patterns
- [Properties](properties.md) — `@property` and `@cached_property` deprecation
- [Async](async.md) — async functions and async generators
- [Advanced](advanced.md) — docstring updates, `args_extra`, testing helpers, class/static methods
- [Audit Tools](audit.md) — enforce removal deadlines and detect deprecation chains in CI
- [Troubleshooting](../troubleshooting.md) — common errors and fixes for `deprecated_class` configuration

______________________________________________________________________

Next: [Properties](properties.md) — deprecating `@property` and `@cached_property` accessors.
