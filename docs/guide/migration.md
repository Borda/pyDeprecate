---
id: migration
description: 'Upgrade guide for pyDeprecate: breaking changes introduced in v1.0 and v0.8, with concrete before/after code snippets for each item.'
---

# Migration Guide

## Upgrade to Latest API (v1.0)

pyDeprecate v1.0 will introduce several breaking changes that were soft-deprecated with `UserWarning` or `FutureWarning` in v0.8. Each item below shows the warning you see now, explains what will break in v1.0, and gives the corrected code.

### `target=None` → `TargetMode.NOTIFY`

**Current behaviour (v0.8):** `target=None` is accepted but emits a `FutureWarning` at decoration time.

**v1.0 behaviour:** `target=None` raises `TypeError` immediately at decoration time.

```python
# BEFORE (v0.8 — emits FutureWarning; breaks in v1.0)
from deprecate import deprecated


@deprecated(target=None, deprecated_in="1.0", remove_in="2.0")
def my_func(x: int) -> int:
    return x * 2


# AFTER — use TargetMode.NOTIFY, or omit target entirely (defaults to NOTIFY)
from deprecate import TargetMode, deprecated


@deprecated(deprecated_in="1.0", remove_in="2.0")
def my_func(x: int) -> int:
    return x * 2
```

`TargetMode.NOTIFY` is the default when `target` is omitted; it emits a deprecation warning on every call and then executes the function body as normal.

### `target=True` → `TargetMode.ARGS_REMAP`

**Current behaviour (v0.8):** `target=True` is accepted but emits a `FutureWarning` at decoration time.

**v1.0 behaviour:** `target=True` raises `TypeError` immediately at decoration time.

```python
# BEFORE (v0.8 — emits FutureWarning; breaks in v1.0)
from deprecate import deprecated


@deprecated(target=True, args_mapping={"old_arg": "new_arg"}, deprecated_in="1.0", remove_in="2.0")
def my_func(old_arg: int = 0, new_arg: int = 0) -> int:
    return new_arg * 2


# AFTER — use TargetMode.ARGS_REMAP explicitly
from deprecate import TargetMode, deprecated


@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_arg": "new_arg"}, deprecated_in="1.0", remove_in="2.0")
def my_func(old_arg: int = 0, new_arg: int = 0) -> int:
    return new_arg * 2
```

### `target=False` → remove the decorator

**Current behaviour (v0.8):** `target=False` emits a `UserWarning` at decoration time and falls through to `TargetMode.NOTIFY`.

**v1.0 behaviour:** `target=False` raises `TypeError` immediately at decoration time.

`target=False` was never a valid deprecation mode. Fix by replacing it with either `TargetMode.NOTIFY` (warn-only) or a callable target (forwarding):

```python
# BEFORE (v0.8 — UserWarning now, TypeError in v1.0)
@deprecated(target=False, deprecated_in="1.0", remove_in="2.0")
def my_func(x: int) -> int:
    return x * 2


# AFTER — use TargetMode.NOTIFY (warn-only, body executes)
from deprecate import TargetMode, deprecated


@deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
def my_func(x: int) -> int:
    return x * 2
```

The same applies inside `deprecated_class()` and the proxy path — `target=False` is invalid in all contexts.

### Misconfigured `TargetMode` combinations

**Current behaviour (v0.8):** Three combinations emit `UserWarning` at decoration time.

**v1.0 behaviour:** All three raise `TypeError` at decoration time.

| Misconfiguration                               | Fix                                                                                            |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `TargetMode.ARGS_REMAP` without `args_mapping` | Add `args_mapping={"old": "new"}`, or switch to `TargetMode.NOTIFY` if you only need a warning |
| `TargetMode.NOTIFY` with `args_mapping`        | Switch to `TargetMode.ARGS_REMAP` if you want argument remapping, or remove `args_mapping`     |
| `TargetMode.NOTIFY` with `args_extra`          | Switch to a callable `target=` if you need to inject extra kwargs into a forwarded call        |

### `DeprecationWrapperInfo` field renames

**Current behaviour (v0.8):** `empty_mapping` and `identity_mapping` are deprecated property aliases that emit `DeprecationWarning` on access.

**v1.0 behaviour:** Both old names are removed.

```python
# BEFORE (emits DeprecationWarning; removed in v1.0)
info.empty_mapping
info.identity_mapping
dataclasses.replace(info, empty_mapping=True)

# AFTER
info.empty_args_mapping
info.identity_args_mapping
dataclasses.replace(info, empty_args_mapping=True)
```

______________________________________________________________________

## Upgrade to v0.8

v0.8 introduced the `TargetMode` enum as the canonical API for non-callable `target` values. The legacy boolean sentinels (`None`, `True`, `False`) still work but emit deprecation warnings as described above. The key additions in v0.8 are:

- `TargetMode.NOTIFY` — replaces `target=None`; warn-only mode where the function body runs unchanged.
- `TargetMode.ARGS_REMAP` — replaces `target=True`; argument-rename mode where kwargs are remapped and the body runs.
- Construction-time `UserWarning` for all misconfigured `TargetMode` combinations.
- `target` parameter of `@deprecated` now defaults to `TargetMode.NOTIFY`, so `@deprecated(deprecated_in="1.0", remove_in="2.0")` is the canonical warn-only form.
- `DeprecationWrapperInfo` field renames: `empty_mapping` → `empty_args_mapping`, `identity_mapping` → `identity_args_mapping`.
- New `DeprecationWrapperInfo.empty_deprecated_in` field for CI detection of wrappers with no version annotation.

See the [Changelog](../changelog.md) for the complete v0.8 release notes.
