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

> The same applies on `deprecated_class()` (and `@deprecated` on a class): explicit `TargetMode.NOTIFY` with `args_mapping` or `attrs_mapping` warns and the mapping stays inert. In every case the flag fires only for an *explicitly passed* `NOTIFY` — omitting `target` auto-resolves a present mapping instead; see [Coming from v0.11](#coming-from-v011) below.

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

Before v0.12, applying `@deprecated` directly to a class emitted a warning threatening `TypeError: ... will become a TypeError in a future release`. That threat is retired — `@deprecated` on a class now dispatches to `deprecated_class()` and produces an identical `_DeprecatedProxy`. The warning is now a one-time informational notice, fired at most once per class (keyed by module + qualified name, so same-named classes in different modules each warn) per process:

```
`@deprecated` on class `MyClass` now dispatches to `@deprecated_class`.
```

The dispatch is permanent; only this notice is removed entirely, in v1.0, and suppressed by `stream=None`. Prefer `deprecated_class()` directly — same result, no notice, and required to reach class-only options such as `attrs_mapping`. See [`@deprecated` on a class](classes.md#deprecated-on-a-class) for a runnable example.

### `TargetMode.AUTO` — the `@deprecated` front door now infers the mode

`TargetMode` gained a fourth member, `AUTO`, and it is the new default for `target` on the `@deprecated` front door (previously `TargetMode.NOTIFY`). `AUTO` is a decoration-time instruction, not a runtime mode: before the wrapper or proxy is built, it resolves to the mode implied by the rest of the configuration, and the resolved mode — never `AUTO` itself — is stored in `DeprecationConfig`:

| Front-door call                                 | Resolves to                                          |
| ----------------------------------------------- | ---------------------------------------------------- |
| `@deprecated(args_mapping={...})` on a function | `TargetMode.ARGS_REMAP`                              |
| `@deprecated()` on a function (no mapping)      | `TargetMode.NOTIFY`                                  |
| `@deprecated(args_mapping={...})` on a class    | proxy auto-resolve → `TargetMode.ARGS_REMAP`         |
| `@deprecated()` on a class (no mapping)         | warn-only proxy (`DeprecationConfig.target is None`) |

The practical win on the callable path: `@deprecated(args_mapping={"old": "new"})` previously fell into the default `TargetMode.NOTIFY` and was flagged as a misconfiguration — now the omitted target infers `ARGS_REMAP` and the mapping is applied.

`AUTO` is front-door-only. The strict forms keep explicit defaults — `deprecated_callable()` defaults to `TargetMode.NOTIFY`, `deprecated_class()` leaves `target` unset — and both raise `TypeError` when handed `target=TargetMode.AUTO`. Legacy proxy sentinels (`target=True` without a mapping, `target=False`) now also resolve to an unset target, so they follow the same auto-resolve as an omitted `target` (audit metadata records `None` for warn-only proxies).

### Explicit `TargetMode.NOTIFY` with a mapping is never silently overridden

Passing `target=TargetMode.NOTIFY` explicitly together with a mapping remains a misconfiguration on every path — your explicit configuration is never rewritten behind your back. A `UserWarning` fires at decoration time (`TypeError` in v1.0), the mode stays `NOTIFY`, and the mapping is inert at runtime; audit metadata keeps the mapping and flags the wrapper `misconfigured`. Omit `target` when you want the mapping applied — auto-resolve only ever fills in an *unset* target.

There is consequently no single-proxy way to get a blanket "warn on every access" notice while a mapping is also active on that same proxy. If you need both, stack two `deprecated_class()` layers — an inner mapping-only layer (`ARGS_REMAP` / `ATTRS_REMAP`) plus an outer no-mapping layer (`NOTIFY`); see [Nested proxy wrappers](classes.md#nested-proxy-wrappers).

### `deprecated()` slimmed to common arguments only

The front-door `deprecated()` dispatcher now exposes only the arguments common to both dispatch shapes: `target`, `deprecated_in`, `remove_in`, `stream`, `num_warns`, `template_mgs`, `args_mapping`, `args_extra`, `skip_if`, `update_docstring`, and `docstring_style`. The one shape-specific option, class-only `attrs_mapping`, raises `TypeError` (unexpected keyword argument) on the front door — use `deprecated_class(attrs_mapping=...)` directly.

As part of this alignment, `template_mgs` and `skip_if` passed through `@deprecated` on a class are now forwarded to the proxy (`template_mgs` used to be dropped silently on the class-dispatch path).

### `skip_if` now available on proxies

`deprecated_class()` and `deprecated_instance()` gained the `skip_if` option (previously callable-only). When the condition evaluates `True` at access time, the proxy transparently serves the wrapped source — no warning, no `attrs_mapping` redirect, no `args_mapping`/`args_extra` handling, no target forwarding, and no `read_only` enforcement — mirroring the callable form, where a skipped call executes the source body unchanged. The condition may be consulted more than once per proxy operation, so keep the callable cheap and stable.

See the [Changelog](../changelog.md) for the complete v0.12 release notes.

______________________________________________________________________

## Coming from v0.7

Here is what changed in v0.8 that you might have missed:

- `TargetMode.NOTIFY` — replaces `target=None`; warn-only mode where the function body runs unchanged.
- `TargetMode.ARGS_REMAP` — replaces `target=True`; argument-rename mode where kwargs are remapped and the body runs.
- Construction-time `UserWarning` for all misconfigured `TargetMode` combinations.
- `target` parameter of `@deprecated` now defaults to `TargetMode.NOTIFY`, so `@deprecated(deprecated_in="1.0", remove_in="2.0")` is the canonical warn-only form. (Since v0.12 the front-door default is `TargetMode.AUTO`, which resolves to `NOTIFY` when no mapping is given — the canonical warn-only form is unchanged.)
- `DeprecationWrapperInfo` field renames: `empty_mapping` → `empty_args_mapping`, `identity_mapping` → `identity_args_mapping`.
- New `DeprecationWrapperInfo.empty_deprecated_in` field for CI detection of wrappers with no version annotation.

See the [Changelog](../changelog.md) for the complete v0.8 release notes.

______________________________________________________________________

If you hit anything not covered here, [open an issue](https://github.com/Borda/pyDeprecate/issues) — we are happy to help.
