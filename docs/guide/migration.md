---
id: migration
description: Align your pyDeprecate usage with the current idiomatic API — each section shows the legacy shorthand and the cleaner modern form, with a note on why the new pattern is clearer.
---

# Migration Guide

## Align with the Current API

v0.8 introduced `TargetMode` as the explicit, readable way to express deprecation intent. If you are still using the legacy boolean shorthands (`None`, `True`, `False` as `target` values), the snippets below show the modern equivalent — they are clearer, pass linting cleanly, and are what we will require going forward.

### `target=None` → `TargetMode.NOTIFY`

`target=None` was a magic sentinel meaning "emit a deprecation notice, then run the function body". Using it today emits a `FutureWarning` at decoration time because the intent was ambiguous — `None` could plausibly mean "no target" rather than "notify-only mode". `TargetMode.NOTIFY` says that intent explicitly. Better still, `TargetMode.NOTIFY` is the default, so you can often drop `target` entirely:

```python
# Legacy form — still works, but emits FutureWarning
from deprecate import deprecated


@deprecated(target=None, deprecated_in="1.0", remove_in="2.0")
def my_func(x: int) -> int:
    return x * 2


# Idiomatic pyDeprecate — target omitted; NOTIFY is the default
from deprecate import deprecated


@deprecated(deprecated_in="1.0", remove_in="2.0")
def my_func(x: int) -> int:
    return x * 2
```

`TargetMode.NOTIFY` emits a deprecation notice on every call and then executes the function body as normal.

### `target=True` → `TargetMode.ARGS_REMAP`

`target=True` was the shorthand for argument-rename mode — it told pyDeprecate to remap kwargs and run the function body. Using a boolean for this was always a bit of a guess for readers; `TargetMode.ARGS_REMAP` makes the intent self-documenting:

```python
# Legacy form — still works, but emits FutureWarning
from deprecate import deprecated


@deprecated(target=True, args_mapping={"old_arg": "new_arg"}, deprecated_in="1.0", remove_in="2.0")
def my_func(old_arg: int = 0, new_arg: int = 0) -> int:
    return new_arg * 2


# Modern form
from deprecate import TargetMode, deprecated


@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_arg": "new_arg"}, deprecated_in="1.0", remove_in="2.0")
def my_func(old_arg: int = 0, new_arg: int = 0) -> int:
    return new_arg * 2
```

### `target=False` → `TargetMode.NOTIFY` or a callable target

`target=False` was never a well-defined mode — passing `False` as a target callable made no semantic sense, so pyDeprecate fell through to `TargetMode.NOTIFY` while emitting a `UserWarning`. The modern form picks the mode you actually want:

```python
# Legacy form — UserWarning now; invalid going forward
@deprecated(target=False, deprecated_in="1.0", remove_in="2.0")
def my_func(x: int) -> int:
    return x * 2


# Modern form — warn only, body executes unchanged
from deprecate import TargetMode, deprecated


@deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
def my_func(x: int) -> int:
    return x * 2
```

The same applies inside `deprecated_class()` and the proxy path — `target=False` is not a valid mode in any context.

### Misconfigured `TargetMode` combinations

Some `TargetMode` + argument combinations are contradictory; pyDeprecate emits a `UserWarning` at decoration time when it detects them. Resolving these makes the intent unambiguous and silences the notice:

| Combination                                    | Cleaner alternative                                                                                       |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `TargetMode.ARGS_REMAP` without `args_mapping` | Add `args_mapping={"old": "new"}`, or switch to `TargetMode.NOTIFY` if you only need a deprecation notice |
| `TargetMode.NOTIFY` with `args_mapping`        | Switch to `TargetMode.ARGS_REMAP` if you want argument remapping, or remove `args_mapping`                |
| `TargetMode.NOTIFY` with `args_extra`          | Use a callable `target=` if you need to inject extra kwargs into a forwarded call                         |

### `DeprecationWrapperInfo` field renames

Two fields on `DeprecationWrapperInfo` were renamed in v0.8 to be consistent with the rest of the API. The old names still work but emit a `DeprecationWarning` on access — swapping them out is a one-line change:

```python
# Legacy names — emit DeprecationWarning on access
info.empty_mapping
info.identity_mapping
dataclasses.replace(info, empty_mapping=True)

# Modern names
info.empty_args_mapping
info.identity_args_mapping
dataclasses.replace(info, empty_args_mapping=True)
```

______________________________________________________________________

## Coming from v0.7

Here is what changed in v0.8 that you might have missed:

- `TargetMode.NOTIFY` — replaces `target=None`; warn-only mode where the function body runs unchanged.
- `TargetMode.ARGS_REMAP` — replaces `target=True`; argument-rename mode where kwargs are remapped and the body runs.
- Construction-time `UserWarning` for all misconfigured `TargetMode` combinations.
- `target` parameter of `@deprecated` now defaults to `TargetMode.NOTIFY`, so `@deprecated(deprecated_in="1.0", remove_in="2.0")` is the canonical warn-only form.
- `DeprecationWrapperInfo` field renames: `empty_mapping` → `empty_args_mapping`, `identity_mapping` → `identity_args_mapping`.
- New `DeprecationWrapperInfo.empty_deprecated_in` field for CI detection of wrappers with no version annotation.

See the [Changelog](../changelog.md) for the complete v0.8 release notes.

______________________________________________________________________

If you hit anything not covered here, [open an issue](https://github.com/Borda/pyDeprecate/issues) — we are happy to help.
