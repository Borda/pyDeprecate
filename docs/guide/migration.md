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

| Combination                                    | Cleaner alternative                                                                                       |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `TargetMode.ARGS_REMAP` without `args_mapping` | Add `args_mapping={"old": "new"}`, or switch to `TargetMode.NOTIFY` if you only need a deprecation notice |
| `TargetMode.NOTIFY` with `args_mapping`        | Switch to `TargetMode.ARGS_REMAP` if you want argument remapping, or remove `args_mapping`                |
| `TargetMode.NOTIFY` with `args_extra`          | Use a callable `target=` if you need to inject extra kwargs into a forwarded call                         |

> This table describes `@deprecated` / `deprecated_callable()` on functions and methods, which is unchanged. On `deprecated_class()` (and `@deprecated` on a class), `TargetMode.NOTIFY` with `args_mapping` or `attrs_mapping` is **not** a misconfiguration since v0.12 — see [Coming from v0.11](#coming-from-v011) below.

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

## Coming from v0.11

Here is what changed in v0.12 that you might have missed:

### `@deprecated` on a class is now first-class supported

Before v0.12, applying `@deprecated` directly to a class emitted a warning threatening `TypeError: ... will become a TypeError in a future release`. That threat is retired — `@deprecated` on a class now dispatches to `deprecated_class()` and produces an identical `_DeprecatedProxy`. The warning is now a one-time informational notice, fired at most once per class name per process:

```
`@deprecated` on class `MyClass` now dispatches to `@deprecated_class`.
```

The dispatch is permanent; only this notice is removed entirely, in v1.0, and suppressed by `stream=None`. Prefer `deprecated_class()` directly — same result, no notice, and required to reach class-only options such as `attrs_mapping`. See [`@deprecated` on a class](classes.md#deprecated-on-a-class) for a runnable example.

### `deprecated_class` default `target` changed from `None` to `TargetMode.NOTIFY`

`deprecated_class()`'s factory default flipped from the legacy `None` sentinel to `TargetMode.NOTIFY`, matching `@deprecated`'s longstanding default. On its own this is not an observable behaviour change — `None` and `TargetMode.NOTIFY` already resolved to the same warn-only mode.

### `TargetMode.NOTIFY` with a mapping now auto-resolves instead of warning

This is the change to actually watch for. Before v0.12, passing `target=TargetMode.NOTIFY` explicitly together with `args_mapping` or `attrs_mapping` on `deprecated_class()` was flagged as a misconfiguration: a `UserWarning` fired and the mapping was silently ignored. Since `TargetMode.NOTIFY` is now the default, that guardrail would have silently broken every documented `deprecated_class(attrs_mapping=...)` / `deprecated_class(args_mapping=...)` call the moment the default flipped — so the semantics were redefined instead of just the default: presence of a mapping now always wins, exactly like the historical `target=None` sentinel already did.

| Combination                                                            | Before v0.12                          | Since v0.12                                         |
| ---------------------------------------------------------------------- | ------------------------------------- | --------------------------------------------------- |
| `deprecated_class(target=TargetMode.NOTIFY, attrs_mapping={...}, ...)` | `UserWarning`, mapping ignored        | Auto-resolves to `TargetMode.ATTRS_REMAP` — applied |
| `deprecated_class(target=TargetMode.NOTIFY, args_mapping={...}, ...)`  | `UserWarning`, mapping ignored        | Auto-resolves to `TargetMode.ARGS_REMAP` — applied  |
| `deprecated_class(attrs_mapping={...})` (no `target=`)                 | Auto-resolved (`None` sentinel), same | Unchanged                                           |

There is consequently no single-proxy way left to get a blanket "warn on every access" notice while a mapping is also configured on that same proxy. If you need both, stack two `deprecated_class()` layers — an inner mapping-only layer (`ARGS_REMAP` / `ATTRS_REMAP`) plus an outer no-mapping layer (`NOTIFY`); see [Nested proxy wrappers](classes.md#nested-proxy-wrappers).

**Scope**: this auto-resolve applies to `deprecated_class()` and the `@deprecated`-on-class dispatch path only. The callable path (`@deprecated` / `deprecated_callable()` on functions and methods) is unchanged — `TargetMode.NOTIFY` with `args_mapping` on a function or method is still a misconfiguration and still emits a `UserWarning` (see the table above).

### `deprecated()` gained `attrs_mapping`

The front-door `deprecated()` dispatcher now accepts `attrs_mapping` directly, routed to `deprecated_class()` when the source is a class. Passing `attrs_mapping` with a callable source raises `TypeError` at decoration time, naming `deprecated_class` as the right tool.

See the [Changelog](../changelog.md) for the complete v0.12 release notes.

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
