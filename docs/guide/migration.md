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

## Pick Your Upgrade Path

Each adjacent-release delta is documented once, in the per-version section below it belongs to — pick the tab matching the version you are upgrading *from*, then read the linked sections in order to reach the current release (v0.12). Only breaking and behaviour changes are listed; a release that was purely additive says so and needs no action. Where a change was itself superseded by a later one, the earlier section points to the final state, so a path gives the net result rather than each intermediate step.

=== "From v0.11"

    1. [Coming from v0.11](#coming-from-v011) — changes in v0.12.

=== "From v0.10"

    1. [Coming from v0.10](#coming-from-v010) — changes in v0.11.
    2. [Coming from v0.11](#coming-from-v011) — changes in v0.12.

=== "From v0.9"

    1. [Coming from v0.9](#coming-from-v09) — changes in v0.10.
    2. [Coming from v0.10](#coming-from-v010) — changes in v0.11.
    3. [Coming from v0.11](#coming-from-v011) — changes in v0.12.

=== "From v0.8"

    1. [Coming from v0.8](#coming-from-v08) — changes in v0.9.
    2. [Coming from v0.9](#coming-from-v09) — changes in v0.10.
    3. [Coming from v0.10](#coming-from-v010) — changes in v0.11.
    4. [Coming from v0.11](#coming-from-v011) — changes in v0.12.

=== "From v0.7"

    1. [Coming from v0.7](#coming-from-v07) — changes in v0.8.
    2. [Coming from v0.8](#coming-from-v08) — changes in v0.9.
    3. [Coming from v0.9](#coming-from-v09) — changes in v0.10.
    4. [Coming from v0.10](#coming-from-v010) — changes in v0.11.
    5. [Coming from v0.11](#coming-from-v011) — changes in v0.12.

=== "From v0.6"

    1. [Coming from v0.6](#coming-from-v06) — changes in v0.7.
    2. [Coming from v0.7](#coming-from-v07) — changes in v0.8.
    3. [Coming from v0.8](#coming-from-v08) — changes in v0.9.
    4. [Coming from v0.9](#coming-from-v09) — changes in v0.10.
    5. [Coming from v0.10](#coming-from-v010) — changes in v0.11.
    6. [Coming from v0.11](#coming-from-v011) — changes in v0.12.

=== "From v0.5"

    1. [Coming from v0.5](#coming-from-v05) — changes in v0.6.
    2. [Coming from v0.6](#coming-from-v06) — changes in v0.7.
    3. [Coming from v0.7](#coming-from-v07) — changes in v0.8.
    4. [Coming from v0.8](#coming-from-v08) — changes in v0.9.
    5. [Coming from v0.9](#coming-from-v09) — changes in v0.10.
    6. [Coming from v0.10](#coming-from-v010) — changes in v0.11.
    7. [Coming from v0.11](#coming-from-v011) — changes in v0.12.

=== "From v0.4"

    1. [Coming from v0.4](#coming-from-v04) — changes in v0.5.
    2. [Coming from v0.5](#coming-from-v05) — changes in v0.6.
    3. [Coming from v0.6](#coming-from-v06) — changes in v0.7.
    4. [Coming from v0.7](#coming-from-v07) — changes in v0.8.
    5. [Coming from v0.8](#coming-from-v08) — changes in v0.9.
    6. [Coming from v0.9](#coming-from-v09) — changes in v0.10.
    7. [Coming from v0.10](#coming-from-v010) — changes in v0.11.
    8. [Coming from v0.11](#coming-from-v011) — changes in v0.12.

=== "From v0.3"

    1. [Coming from v0.3](#coming-from-v03) — changes in v0.4.
    2. [Coming from v0.4](#coming-from-v04) — changes in v0.5.
    3. [Coming from v0.5](#coming-from-v05) — changes in v0.6.
    4. [Coming from v0.6](#coming-from-v06) — changes in v0.7.
    5. [Coming from v0.7](#coming-from-v07) — changes in v0.8.
    6. [Coming from v0.8](#coming-from-v08) — changes in v0.9.
    7. [Coming from v0.9](#coming-from-v09) — changes in v0.10.
    8. [Coming from v0.10](#coming-from-v010) — changes in v0.11.
    9. [Coming from v0.11](#coming-from-v011) — changes in v0.12.

=== "From v0.2"

    1. [Coming from v0.2](#coming-from-v02) — changes in v0.3.
    2. [Coming from v0.3](#coming-from-v03) — changes in v0.4.
    3. [Coming from v0.4](#coming-from-v04) — changes in v0.5.
    4. [Coming from v0.5](#coming-from-v05) — changes in v0.6.
    5. [Coming from v0.6](#coming-from-v06) — changes in v0.7.
    6. [Coming from v0.7](#coming-from-v07) — changes in v0.8.
    7. [Coming from v0.8](#coming-from-v08) — changes in v0.9.
    8. [Coming from v0.9](#coming-from-v09) — changes in v0.10.
    9. [Coming from v0.10](#coming-from-v010) — changes in v0.11.
    10. [Coming from v0.11](#coming-from-v011) — changes in v0.12.

=== "From v0.1"

    1. [Coming from v0.1](#coming-from-v01) — changes in v0.2.
    2. [Coming from v0.2](#coming-from-v02) — changes in v0.3.
    3. [Coming from v0.3](#coming-from-v03) — changes in v0.4.
    4. [Coming from v0.4](#coming-from-v04) — changes in v0.5.
    5. [Coming from v0.5](#coming-from-v05) — changes in v0.6.
    6. [Coming from v0.6](#coming-from-v06) — changes in v0.7.
    7. [Coming from v0.7](#coming-from-v07) — changes in v0.8.
    8. [Coming from v0.8](#coming-from-v08) — changes in v0.9.
    9. [Coming from v0.9](#coming-from-v09) — changes in v0.10.
    10. [Coming from v0.10](#coming-from-v010) — changes in v0.11.
    11. [Coming from v0.11](#coming-from-v011) — changes in v0.12.

______________________________________________________________________

## Coming from v0.11

Here is what changed in v0.12 that you might have missed:

### `@deprecated` on a class is now first-class supported

Before v0.12, applying `@deprecated` directly to a class emitted a warning threatening `TypeError: ... will become a TypeError in a future release`. That threat is retired — `@deprecated` on a class now dispatches to `deprecated_class()` and produces an identical `_DeprecatedProxy`. The warning is now a one-time informational notice, fired at most once per class (keyed by module + qualified name, so same-named classes in different modules each warn) per process:

```
`@deprecated` on class `MyClass` now dispatches to `@deprecated_class`.
```

The dispatch is permanent; only this notice is removed entirely, in v1.0, and suppressed by `stream=None`. Prefer `deprecated_class()` directly — same result, no notice, and required to reach class-only options such as `attrs_mapping`. See [`@deprecated` on a class](classes.md#deprecated-on-a-class) for a runnable example.

### `TargetMode.AUTO` — how an omitted `target` is resolved

> **Prefer an explicit `target`.** `AUTO` exists only to give an *omitted* `target` a sensible default. Passing an explicit mode — `target=TargetMode.NOTIFY`, `target=TargetMode.ARGS_REMAP`, or a callable — keeps intent visible at the call site and is the recommended style everywhere in these docs. You never write `target=TargetMode.AUTO` yourself; the strict factories reject it.

`TargetMode` gained a fourth member, `AUTO`, which is the default value of `target` on the `@deprecated` front door (previously `TargetMode.NOTIFY`). It is a decoration-time fallback, not a runtime mode: when `target` is omitted, `AUTO` resolves to the mode implied by the rest of the configuration before the wrapper or proxy is built, and the resolved mode — never `AUTO` itself — is stored in `DeprecationConfig`:

| Front-door call                                 | Resolves to                                          |
| ----------------------------------------------- | ---------------------------------------------------- |
| `@deprecated(args_mapping={...})` on a function | `TargetMode.ARGS_REMAP`                              |
| `@deprecated()` on a function (no mapping)      | `TargetMode.NOTIFY`                                  |
| `@deprecated(args_mapping={...})` on a class    | proxy auto-resolve → `TargetMode.ARGS_REMAP`         |
| `@deprecated()` on a class (no mapping)         | warn-only proxy (`DeprecationConfig.target is None`) |

The practical win on the callable path: `@deprecated(args_mapping={"old": "new"})` previously fell into the default `TargetMode.NOTIFY` and was flagged as a misconfiguration — now the omitted target infers `ARGS_REMAP` and the mapping is applied. Even so, spelling out `target=TargetMode.ARGS_REMAP` alongside `args_mapping` is clearer and is what the examples show.

`AUTO` is front-door-only. The strict forms keep explicit defaults — `deprecated_callable()` defaults to `TargetMode.NOTIFY`, `deprecated_class()` leaves `target` unset — and both raise `TypeError` when handed `target=TargetMode.AUTO`. Legacy proxy sentinels (`target=True` without a mapping, `target=False`) now also resolve to an unset target, so they follow the same auto-resolve as an omitted `target` (audit metadata records `None` for warn-only proxies).

### Explicit `TargetMode.NOTIFY` with a mapping is never silently overridden

Passing `target=TargetMode.NOTIFY` explicitly together with a mapping remains a misconfiguration on every path — your explicit configuration is never rewritten behind your back. A `UserWarning` fires at decoration time (`TypeError` in v1.0), the mode stays `NOTIFY`, and the mapping is inert at runtime; audit metadata keeps the mapping and flags the wrapper `misconfigured`. Omit `target` when you want the mapping applied — auto-resolve only ever fills in an *unset* target.

There is consequently no single-proxy way to get a blanket "warn on every access" notice while a mapping is also active on that same proxy. If you need both, stack two `deprecated_class()` layers — an inner mapping-only layer (`ARGS_REMAP` / `ATTRS_REMAP`) plus an outer no-mapping layer (`NOTIFY`); see [Nested proxy wrappers](classes.md#nested-proxy-wrappers).

### `deprecated()` slimmed to common arguments only

The front-door `deprecated()` dispatcher now exposes only the arguments common to both dispatch shapes: `target`, `deprecated_in`, `remove_in`, `stream`, `num_warns`, `message_template`, `args_mapping`, `args_extra`, `skip_if`, `update_docstring`, and `docstring_style`. The one shape-specific option, class-only `attrs_mapping`, raises `TypeError` (unexpected keyword argument) on the front door — use `deprecated_class(attrs_mapping=...)` directly.

As part of this alignment, `message_template` and `skip_if` passed through `@deprecated` on a class are now forwarded to the proxy (`message_template` used to be dropped silently on the class-dispatch path).

### `template_mgs` → `message_template`

The custom-notice parameter was a typo (`mgs` for `msg`). It is now `message_template` on every factory (`@deprecated`, `deprecated_callable`, `deprecated_class`, `deprecated_instance`, and `deprecated_module`). The old name keeps working as a deprecated alias until v1.0:

```python
# phmdoctest:skip — illustrative rename, not a runnable block
# Before — still works, but emits a FutureWarning
@deprecated(target=new_fn, deprecated_in="1.0", remove_in="2.0", template_mgs="v%(deprecated_in)s: gone")
def old_fn(): ...


# After — the canonical name
@deprecated(target=new_fn, deprecated_in="1.0", remove_in="2.0", message_template="v%(deprecated_in)s: gone")
def old_fn(): ...
```

Passing both `message_template` and `template_mgs` raises `TypeError`. Audit code reading `__deprecated__.template_mgs` keeps working through a read-only alias, but the stored field is now `message_template`.

See the [Changelog](../changelog.md) for the complete v0.12 release notes.

______________________________________________________________________

## Coming from v0.10

Here is what changed in v0.11 that you might have missed:

- **In-place operators on a proxy rebind the name to the unwrapped result.** After `x += 1` on a `deprecated_instance` proxy, `x` is now a plain value (e.g. an `int`), not a re-wrapped proxy — so every later use of `x` is silent even if the deprecation window is still open. Assign to a fresh name, or avoid in-place operators, when you need the warning to keep firing.

See the [Changelog](../changelog.md) for the complete v0.11 release notes.

______________________________________________________________________

## Coming from v0.9

Here is what changed in v0.10 that you might have missed. The first item is a behaviour change:

- **`@deprecated @property` (outer order) now wraps `fset` and `fdel`.** Writing to or deleting a deprecated property now fires `FutureWarning`; before v0.10 only reads warned. Under `filterwarnings=error::FutureWarning`, a write or delete that used to pass silently now raises. Keep the silent setter/deleter by using inner order (`@property @deprecated`) or by decorating only `fget`.
- **`args_mapping` precedence fixed — explicit new name always wins.** When a caller passes both the old and new argument names (`fn(val=5, new_val=6)`), the explicit new-name value now wins; previously the remapped old-name value could clobber it, regardless of call-site order.
- **Circular callable-target chains raise `RuntimeError`.** An A → B → A target cycle previously ran into `RecursionError`; a re-entrancy guard now raises a clear `RuntimeError` naming the cycle.

See the [Changelog](../changelog.md) for the complete v0.10 release notes.

______________________________________________________________________

## Coming from v0.8

Here is what changed in v0.9 that you might have missed. The CLI rename is breaking:

- **CLI flag renamed: `--skip_errors` → `--exit-zero`.** The old flag is no longer accepted on any subcommand (`check`, `expiry`, `chains`, `all`) — update existing scripts. The canonical spelling is `--exit-zero` (dash); `--exit_zero` (underscore) is accepted as an alias. The new name matches the linter convention and describes the behaviour: exit-code override only, no exception suppression.
- **Misconfigured `@deprecated` stacking now warns at decoration time.** Six previously-undefined stacking shapes (e.g. callable-over-callable) emit `UserWarning` naming the shape (→ `TypeError` in v1.0). The supported lifecycle shape is `ARGS_REMAP` (outer) + `NOTIFY` (inner): rename arguments first, deprecate the whole function later.
- **Audit reclassification:** an `ARGS_REMAP + NOTIFY` chain is now reported as `ChainType.STACKED`, not `TARGET`.

See the [Changelog](../changelog.md) for the complete v0.9 release notes.

______________________________________________________________________

## Coming from v0.7

Here is what changed in v0.8 that you might have missed:

- `TargetMode.NOTIFY` — replaces `target=None`; warn-only mode where the function body runs unchanged.
- `TargetMode.ARGS_REMAP` — replaces `target=True`; argument-rename mode where kwargs are remapped and the body runs.
- Construction-time `UserWarning` for all misconfigured `TargetMode` combinations.
- `target` parameter of `@deprecated` now defaults to `TargetMode.NOTIFY`, so `@deprecated(deprecated_in="1.0", remove_in="2.0")` is the canonical warn-only form. (Since v0.12 the front-door default is `TargetMode.AUTO`, which resolves to `NOTIFY` when no mapping is given — the canonical warn-only form is unchanged.)
- `DeprecationWrapperInfo` field renames: `empty_mapping` → `empty_args_mapping`, `identity_mapping` → `identity_args_mapping`.

See the [Changelog](../changelog.md) for the complete v0.8 release notes.

______________________________________________________________________

## Coming from v0.6

No breaking or behaviour changes in v0.7 — nothing to migrate.

See the [Changelog](../changelog.md) for the complete v0.7 release notes.

______________________________________________________________________

## Coming from v0.5

Here is what changed in v0.6 that you might have missed. This is the biggest pre-v0.8 jump — two items need action:

- **`@deprecated` on a class stopped raising and started delegating.** v0.6.0 first made `@deprecated` directly on a class raise `TypeError` at decoration time; v0.6.0.post0 softened that to a `UserWarning` plus automatic delegation to `deprecated_class()`. Existing class-decoration code keeps working with a warning — switch to `deprecated_class()` for a clean result (since v0.12 `@deprecated` dispatches there automatically).
- **Cross-class method forwarding now raises at decoration time.** Passing a class as `target` on a non-`__init__` method used to silently forward a `self` of the wrong type — always a bug. It now raises `TypeError` at decoration time; fix the target if you relied on it.

Deprecated (old names kept as shims until v1.0): the audit API was renamed for consistency — `find_deprecated_callables` → `find_deprecation_wrappers`, `validate_deprecated_callable` → `validate_deprecation_wrapper`, `DeprecatedCallableInfo` → `DeprecationWrapperInfo`, and the test helper `no_warning_call` → `assert_no_warnings`. Swap them out at your convenience.

See the [Changelog](../changelog.md) for the complete v0.6 release notes.

______________________________________________________________________

## Coming from v0.4

No breaking or behaviour changes in v0.5 — nothing to migrate.

See the [Changelog](../changelog.md) for the complete v0.5 release notes.

______________________________________________________________________

## Coming from v0.3

Here is what changed in v0.4 that you might have missed. One behaviour change needs attention:

- **Deprecation warnings switched from `DeprecationWarning` to `FutureWarning`.** `DeprecationWarning` is hidden by Python's default filters outside test runs, so callers rarely saw it; `FutureWarning` is shown by default. If you filter or assert on the warning category, update it to `FutureWarning`.
- **Minimum Python raised to 3.9** (3.8 reached end-of-life), and the **license changed from MIT to Apache-2.0**.

See the [Changelog](../changelog.md) for the complete v0.4 release notes.

______________________________________________________________________

## Coming from v0.2

No breaking or behaviour changes in v0.3 — nothing to migrate.

See the [Changelog](../changelog.md) for the complete v0.3 release notes.

______________________________________________________________________

## Coming from v0.1

No breaking or behaviour changes in v0.2 — nothing to migrate.

See the [Changelog](../changelog.md) for the complete v0.2 release notes.

______________________________________________________________________

If you hit anything not covered here, [open an issue](https://github.com/Borda/pyDeprecate/issues) — we are happy to help.
