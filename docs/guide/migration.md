---
id: migration
description: Align your pyDeprecate usage with the current idiomatic API — each section shows the legacy shorthand and the cleaner modern form, with a note on why the new pattern is clearer.
---

# Migration Guide

## Align with the Current API

v0.8 introduced `TargetMode` as the explicit, readable way to express deprecation intent. If you are still using the legacy boolean shorthands (`None`, `True`, `False` as `target` values), the snippets below show the modern equivalent — they are clearer, pass linting cleanly, and are what we will require going forward.

### `target=None` → `TargetMode.NOTIFY`

`target=None` was a magic sentinel meaning "emit a deprecation notice, then run the function body". Using it today emits a `FutureWarning` at decoration time because the intent was ambiguous — `None` could plausibly mean "no target" rather than "notify-only mode". `TargetMode.NOTIFY` says that intent explicitly. Better still, an omitted `target` resolves to `TargetMode.NOTIFY` when no mapping is given (via the `TargetMode.AUTO` default), so you can often drop `target` entirely:

```python
# Legacy form — still works, but emits FutureWarning
from deprecate import deprecated


@deprecated(target=None, deprecated_in="1.0", remove_in="2.0")
def my_func(x: int) -> int:
    return x * 2


# Idiomatic pyDeprecate — target omitted; resolves to NOTIFY (no mapping given)
from deprecate import deprecated


@deprecated(deprecated_in="1.0", remove_in="2.0")
def my_func(x: int) -> int:
    return x * 2


print(my_func(2))
```

<details>
  <summary>Output: <code>my_func(2)</code></summary>

```
4
```

</details>

`TargetMode.NOTIFY` emits a deprecation notice (subject to `num_warns` — once by default, not on every call) and then executes the function body as normal.

### `target=True` → `TargetMode.ARGS_REMAP`

`target=True` was the shorthand for argument-rename mode — it told pyDeprecate to remap kwargs and run the function body. Using a boolean for this was always a bit of a guess for readers; `TargetMode.ARGS_REMAP` makes the intent self-documenting:

```python
# Legacy form — still works, but emits FutureWarning
from deprecate import deprecated


@deprecated(target=True, args_mapping={"lr": "learning_rate"}, deprecated_in="1.0", remove_in="2.0")
def my_func(lr: float = 0.0, learning_rate: float = 0.0) -> float:
    return learning_rate * 2


# Modern form
from deprecate import TargetMode, deprecated


@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"lr": "learning_rate"}, deprecated_in="1.0", remove_in="2.0")
def my_func(lr: float = 0.0, learning_rate: float = 0.0) -> float:
    return learning_rate * 2


print(my_func(lr=1, learning_rate=2))
```

<details>
  <summary>Output: <code>my_func(lr=1, learning_rate=2)</code></summary>

```
4
```

</details>

### `target=False` → `TargetMode.NOTIFY` or a callable target

`target=False` was never a well-defined mode — passing `False` as a target callable made no semantic sense, so pyDeprecate fell through to `TargetMode.NOTIFY` while emitting a `UserWarning`. The modern form picks the mode you actually want:

```python
# Legacy form — UserWarning now; invalid going forward
from deprecate import deprecated


@deprecated(target=False, deprecated_in="1.0", remove_in="2.0")
def my_func(x: int) -> int:
    return x * 2


# Modern form — warn only, body executes unchanged
from deprecate import TargetMode, deprecated


@deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
def my_func(x: int) -> int:
    return x * 2


print(my_func(3))
```

<details>
  <summary>Output: <code>my_func(3)</code></summary>

```
6
```

</details>

The same applies inside `deprecated_class()` and the proxy path — `target=False` is not a valid mode in any context.

### Misconfigured `TargetMode` combinations

Some `TargetMode` + argument combinations are contradictory; pyDeprecate emits a `UserWarning` at decoration time when it detects them. Resolving these makes the intent unambiguous and silences the notice:

| Combination                                    | Cleaner alternative                                                                                                            |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `TargetMode.ARGS_REMAP` without `args_mapping` | Add `args_mapping={"old": "new"}`, or switch to `TargetMode.NOTIFY` if you only need a deprecation notice                      |
| `TargetMode.NOTIFY` with `args_mapping`        | Omit `target` (`@deprecated` auto-resolves to `ARGS_REMAP`), pass `TargetMode.ARGS_REMAP` explicitly, or remove `args_mapping` |
| `TargetMode.NOTIFY` with `args_extra`          | Use a callable `target=` if you need to inject extra kwargs into a forwarded call                                              |

> The same applies on `deprecated_class()` (and `@deprecated` on a class): explicit `TargetMode.NOTIFY` with `args_mapping` or `attrs_mapping` warns and the mapping stays inert. In every case the flag fires only for an *explicitly passed* `NOTIFY` — omitting `target` auto-resolves a present mapping instead; see the [v0.12 changes](#pick-your-upgrade-path) below.

### `DeprecationWrapperInfo` field renames

Two fields on `DeprecationWrapperInfo` were renamed in v0.8 to be consistent with the rest of the API. The old names still work but emit a `DeprecationWarning` on access — swapping them out is a one-line change:

```python
# phmdoctest:skip — info object requires audit context; snippet shows attribute names only
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

## Pick Your Upgrade Path

Pick the tab matching the version you are upgrading *from* — each tab lists, in order, every breaking and behaviour change between that version and the current release (v0.12), inline. Only breaking and behaviour changes are shown; purely additive releases carry nothing to migrate and are skipped. Where a change was later superseded, the tab shows the net final state rather than each intermediate step. See the [Changelog](../changelog.md) for the complete per-release notes.

=== "v0.11"

    --8<-- "guide/_deltas/v0.12.md"

=== "v0.10"

    --8<-- "guide/_deltas/v0.11.md"

    --8<-- "guide/_deltas/v0.12.md"

=== "v0.9"

    --8<-- "guide/_deltas/v0.10.md"

    --8<-- "guide/_deltas/v0.11.md"

    --8<-- "guide/_deltas/v0.12.md"

=== "v0.8"

    --8<-- "guide/_deltas/v0.9.md"

    --8<-- "guide/_deltas/v0.10.md"

    --8<-- "guide/_deltas/v0.11.md"

    --8<-- "guide/_deltas/v0.12.md"

=== "v0.7"

    --8<-- "guide/_deltas/v0.8.md"

    --8<-- "guide/_deltas/v0.9.md"

    --8<-- "guide/_deltas/v0.10.md"

    --8<-- "guide/_deltas/v0.11.md"

    --8<-- "guide/_deltas/v0.12.md"

=== "v0.6"

    --8<-- "guide/_deltas/v0.8.md"

    --8<-- "guide/_deltas/v0.9.md"

    --8<-- "guide/_deltas/v0.10.md"

    --8<-- "guide/_deltas/v0.11.md"

    --8<-- "guide/_deltas/v0.12.md"

=== "v0.5"

    --8<-- "guide/_deltas/v0.6.md"

    --8<-- "guide/_deltas/v0.8.md"

    --8<-- "guide/_deltas/v0.9.md"

    --8<-- "guide/_deltas/v0.10.md"

    --8<-- "guide/_deltas/v0.11.md"

    --8<-- "guide/_deltas/v0.12.md"

=== "v0.4"

    --8<-- "guide/_deltas/v0.6.md"

    --8<-- "guide/_deltas/v0.8.md"

    --8<-- "guide/_deltas/v0.9.md"

    --8<-- "guide/_deltas/v0.10.md"

    --8<-- "guide/_deltas/v0.11.md"

    --8<-- "guide/_deltas/v0.12.md"

=== "v0.3"

    --8<-- "guide/_deltas/v0.4.md"

    --8<-- "guide/_deltas/v0.6.md"

    --8<-- "guide/_deltas/v0.8.md"

    --8<-- "guide/_deltas/v0.9.md"

    --8<-- "guide/_deltas/v0.10.md"

    --8<-- "guide/_deltas/v0.11.md"

    --8<-- "guide/_deltas/v0.12.md"

=== "v0.2"

    --8<-- "guide/_deltas/v0.4.md"

    --8<-- "guide/_deltas/v0.6.md"

    --8<-- "guide/_deltas/v0.8.md"

    --8<-- "guide/_deltas/v0.9.md"

    --8<-- "guide/_deltas/v0.10.md"

    --8<-- "guide/_deltas/v0.11.md"

    --8<-- "guide/_deltas/v0.12.md"

=== "v0.1"

    --8<-- "guide/_deltas/v0.4.md"

    --8<-- "guide/_deltas/v0.6.md"

    --8<-- "guide/_deltas/v0.8.md"

    --8<-- "guide/_deltas/v0.9.md"

    --8<-- "guide/_deltas/v0.10.md"

    --8<-- "guide/_deltas/v0.11.md"

    --8<-- "guide/_deltas/v0.12.md"

______________________________________________________________________

If you hit anything not covered here, [open an issue](https://github.com/Borda/pyDeprecate/issues) — we are happy to help.
