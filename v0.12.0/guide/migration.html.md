---
id: migration
description: Upgrade pyDeprecate safely with source-version checklists, compatibility notes, and executable before-and-after examples.
---

# Migration Guide

The current development target is **v0.12**. This guide contains only rewrites, deprecations, and observable changes that existing users need to handle when upgrading. New APIs and capabilities belong in the [Changelog](../changelog.md) and topic guides.

## Migrate Legacy Target Values

v0.8 introduced `TargetMode` so deprecation intent no longer depends on the legacy `None`, `True`, and `False` sentinels. These examples show the current form and the caller behavior to preserve during migration.

### `target=None` → `TargetMode.NOTIFY`

`target=None` means "warn, then run the decorated body". It still works, but emits `FutureWarning` at decoration time. Omit `target` for the shortest current form; the `TargetMode.AUTO` default resolves to `TargetMode.NOTIFY` when no mapping is present. Use `target=TargetMode.NOTIFY` when being explicit helps the reader.

```diff
- # DEPRECATED API — legacy sentinel
- @deprecated(target=None, deprecated_in="1.0", remove_in="2.0")
+ # DEPRECATED API — current warn-only form
+ @deprecated(deprecated_in="1.0", remove_in="2.0")
```

```python
from deprecate import deprecated


@deprecated(deprecated_in="1.0", remove_in="2.0")
def refresh_cache(cache_key: str) -> str:
    """Refresh one cached value."""
    return f"refreshed:{cache_key}"


print(refresh_cache("products"))
```

<details>
  <summary>Output: <code>refresh_cache("products")</code></summary>

```
refreshed:products
```

</details>

The notice respects `num_warns` (once by default); the function body still executes normally.

### `target=True` → `TargetMode.ARGS_REMAP`

`target=True` remaps deprecated keyword names within the same function. Replace it with `TargetMode.ARGS_REMAP`; existing callers can keep the old keyword during the migration window while new callers use the replacement.

```diff
- target=True,
+ target=TargetMode.ARGS_REMAP,
```

```python
from deprecate import TargetMode, deprecated


@deprecated(
    target=TargetMode.ARGS_REMAP,
    args_mapping={"ttl": "cache_ttl"},
    deprecated_in="1.0",
    remove_in="2.0",
)
def configure_cache(cache_ttl: int = 60) -> int:
    """Return the configured cache lifetime."""
    return cache_ttl


# DEPRECATED API — old callers are remapped and warned.
print(configure_cache(ttl=30))
# NEW API — callers use the replacement keyword directly.
print(configure_cache(cache_ttl=45))
```

<details>
  <summary>Output: <code>configure_cache(...)</code></summary>

```
30
45
```

</details>

### `target=False` → `TargetMode.NOTIFY` or a callable target

`target=False` was never a valid target. On a callable it now emits `UserWarning` at decoration time and behaves like warn-only mode. Proxy factories treat it like an omitted target while marking the configuration as misconfigured, so a supplied mapping may still be inferred; do not rely on either compatibility path. Choose the behavior you actually need:

```diff
- # DEPRECATED API — invalid legacy sentinel
- @deprecated(target=False, deprecated_in="1.0", remove_in="2.0")
+ # DEPRECATED API — keep running this body after warning
+ @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
```

```python
from deprecate import TargetMode, deprecated


@deprecated(
    target=TargetMode.NOTIFY,
    deprecated_in="1.0",
    remove_in="2.0",
)
def legacy_checksum(payload: str) -> int:
    """Compute the retained legacy checksum."""
    return len(payload)


print(legacy_checksum("abc"))
```

<details>
  <summary>Output: <code>legacy_checksum("abc")</code></summary>

```
3
```

</details>

If the old API should forward to a replacement, pass that callable instead. The deprecated body is then not executed:

```python
from deprecate import deprecated


def checksum_v2(payload: bytes) -> int:
    """Compute the replacement checksum."""
    return sum(payload)


@deprecated(
    target=checksum_v2,
    args_mapping={"text": "payload"},
    deprecated_in="1.0",
    remove_in="2.0",
)
def checksum(text: bytes) -> int:
    """Retain the deprecated signature during migration."""
    ...


# DEPRECATED API — forwards to checksum_v2 and warns.
print(checksum(text=b"abc"))
# NEW API — callers move directly to the replacement.
print(checksum_v2(payload=b"abc"))
```

<details>
  <summary>Output: <code>checksum(...)</code> and <code>checksum_v2(...)</code></summary>

```
294
294
```

</details>

The same rule applies to `deprecated_class()` and `deprecated_instance()`: `target=False` is not a valid mode.

### Misconfigured `TargetMode` combinations

Contradictory combinations emit `UserWarning` at decoration time. The warning is a migration task: the ignored configuration is scheduled to become an error in v1.0.

| Combination                                                         | Migration                                                                                                    |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `TargetMode.ARGS_REMAP` without `args_mapping`                      | Add a real mapping, or use `TargetMode.NOTIFY` for warn-only behavior.                                       |
| Explicit `TargetMode.NOTIFY` with `args_mapping`                    | Omit `target` so `AUTO` infers `ARGS_REMAP`, select `ARGS_REMAP` explicitly, or remove the mapping.          |
| Explicit `TargetMode.NOTIFY` with `args_extra`                      | Use a callable `target` if extra kwargs must reach a replacement, or remove `args_extra`.                    |
| `deprecated_class(target=TargetMode.NOTIFY, attrs_mapping=...)`     | Omit `target` so the proxy infers `ATTRS_REMAP`, select `ATTRS_REMAP` explicitly, or remove the mapping.     |
| `deprecated_class(target=TargetMode.ATTRS_REMAP)` without a mapping | Add `attrs_mapping={"old": "new"}`, or use `NOTIFY` for class-wide warnings.                                 |
| `@deprecated(target=TargetMode.ATTRS_REMAP)`                        | Use `deprecated_class(attrs_mapping=...)`; the common front door does not expose class-only `attrs_mapping`. |

An explicitly supplied `NOTIFY` is never rewritten: its mapping stays inert. Inference happens only when `target` is omitted.

### `DeprecationWrapperInfo` field renames

Two audit fields were renamed in v0.8. The old properties still work but emit `DeprecationWarning` and will be removed in v1.0:

```diff
- # DEPRECATED API — compatibility properties
- info.empty_mapping
- info.identity_mapping
- dataclasses.replace(info, empty_mapping=True)
+ # NEW API — stored field names
+ info.empty_args_mapping
+ info.identity_args_mapping
+ dataclasses.replace(info, empty_args_mapping=True)
```

The replacement fields are regular dataclass fields, so introspection and `dataclasses.replace()` use them directly:

```python
from dataclasses import replace

from deprecate.audit import DeprecationWrapperInfo


info = DeprecationWrapperInfo(
    empty_args_mapping=True,
    identity_args_mapping=["timeout"],
)
updated = replace(info, empty_args_mapping=False)

print(info.empty_args_mapping)
print(info.identity_args_mapping)
print(updated.empty_args_mapping)
```

<details>
  <summary>Output: <code>info fields and updated.empty_args_mapping</code></summary>

```
True
['timeout']
False
```

</details>

______________________________________________________________________

## Pick Your Upgrade Path

Find the section matching the version you are upgrading **from**. Each section aggregates every later migration-relevant compatibility change into two tabs:

1. Apply every item under **Breaking changes**.
2. Review changed warnings, errors, audit scope, and proxy behavior under **Behavior changes**.
3. Run your suite with `FutureWarning` promoted to an error, then run your pyDeprecate audit/expiry CI checks.

This guide works at **minor-line granularity**. `v0.N.x` means the latest available bugfix release in that feature line. The paths assume your dependency constraint allows bugfix updates and that you update within `v0.N.x` before crossing to the next feature line. Patch and post releases are folded into their minor interval rather than receiving separate sections.

Releases with no migration work are skipped. Where a later release superseded an intermediate behavior, the section describes the final v0.12.x development behavior.

### Coming from v0.11.x

=== "Breaking changes"

    --8<-- "guide/_deltas/breaking/v0.11-to-v0.12.md"

=== "Behavior changes"

    --8<-- "guide/_deltas/behavior/v0.11-to-v0.12.md"

### Coming from v0.10.x

=== "Breaking changes"

    --8<-- "guide/_deltas/breaking/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/breaking/v0.11-to-v0.12.md"

=== "Behavior changes"

    --8<-- "guide/_deltas/behavior/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/behavior/v0.11-to-v0.12.md"

### Coming from v0.9.x

=== "Breaking changes"

    --8<-- "guide/_deltas/breaking/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/breaking/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/breaking/v0.11-to-v0.12.md"

=== "Behavior changes"

    --8<-- "guide/_deltas/behavior/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/behavior/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/behavior/v0.11-to-v0.12.md"

### Coming from v0.8.x

=== "Breaking changes"

    --8<-- "guide/_deltas/breaking/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/breaking/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/breaking/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/breaking/v0.11-to-v0.12.md"

=== "Behavior changes"

    --8<-- "guide/_deltas/behavior/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/behavior/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/behavior/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/behavior/v0.11-to-v0.12.md"

### Coming from v0.7.x

=== "Breaking changes"

    --8<-- "guide/_deltas/breaking/v0.7-to-v0.8.md"

    --8<-- "guide/_deltas/breaking/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/breaking/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/breaking/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/breaking/v0.11-to-v0.12.md"

=== "Behavior changes"

    --8<-- "guide/_deltas/behavior/v0.7-to-v0.8.md"

    --8<-- "guide/_deltas/behavior/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/behavior/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/behavior/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/behavior/v0.11-to-v0.12.md"

### Coming from v0.6.x

=== "Breaking changes"

    --8<-- "guide/_deltas/breaking/v0.7-to-v0.8.md"

    --8<-- "guide/_deltas/breaking/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/breaking/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/breaking/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/breaking/v0.11-to-v0.12.md"

=== "Behavior changes"

    --8<-- "guide/_deltas/behavior/v0.7-to-v0.8.md"

    --8<-- "guide/_deltas/behavior/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/behavior/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/behavior/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/behavior/v0.11-to-v0.12.md"

### Coming from v0.5.x

=== "Breaking changes"

    --8<-- "guide/_deltas/breaking/v0.5-to-v0.6.md"

    --8<-- "guide/_deltas/breaking/v0.7-to-v0.8.md"

    --8<-- "guide/_deltas/breaking/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/breaking/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/breaking/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/breaking/v0.11-to-v0.12.md"

=== "Behavior changes"

    --8<-- "guide/_deltas/behavior/v0.5-to-v0.6.md"

    --8<-- "guide/_deltas/behavior/v0.7-to-v0.8.md"

    --8<-- "guide/_deltas/behavior/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/behavior/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/behavior/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/behavior/v0.11-to-v0.12.md"

### Coming from v0.4.x

=== "Breaking changes"

    --8<-- "guide/_deltas/breaking/v0.5-to-v0.6.md"

    --8<-- "guide/_deltas/breaking/v0.7-to-v0.8.md"

    --8<-- "guide/_deltas/breaking/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/breaking/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/breaking/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/breaking/v0.11-to-v0.12.md"

=== "Behavior changes"

    --8<-- "guide/_deltas/behavior/v0.5-to-v0.6.md"

    --8<-- "guide/_deltas/behavior/v0.7-to-v0.8.md"

    --8<-- "guide/_deltas/behavior/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/behavior/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/behavior/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/behavior/v0.11-to-v0.12.md"

### Coming from v0.3.x

=== "Breaking changes"

    --8<-- "guide/_deltas/breaking/v0.3-to-v0.4.md"

    --8<-- "guide/_deltas/breaking/v0.5-to-v0.6.md"

    --8<-- "guide/_deltas/breaking/v0.7-to-v0.8.md"

    --8<-- "guide/_deltas/breaking/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/breaking/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/breaking/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/breaking/v0.11-to-v0.12.md"

=== "Behavior changes"

    --8<-- "guide/_deltas/behavior/v0.3-to-v0.4.md"

    --8<-- "guide/_deltas/behavior/v0.5-to-v0.6.md"

    --8<-- "guide/_deltas/behavior/v0.7-to-v0.8.md"

    --8<-- "guide/_deltas/behavior/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/behavior/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/behavior/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/behavior/v0.11-to-v0.12.md"

### Coming from v0.2.x

=== "Breaking changes"

    --8<-- "guide/_deltas/breaking/v0.3-to-v0.4.md"

    --8<-- "guide/_deltas/breaking/v0.5-to-v0.6.md"

    --8<-- "guide/_deltas/breaking/v0.7-to-v0.8.md"

    --8<-- "guide/_deltas/breaking/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/breaking/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/breaking/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/breaking/v0.11-to-v0.12.md"

=== "Behavior changes"

    --8<-- "guide/_deltas/behavior/v0.3-to-v0.4.md"

    --8<-- "guide/_deltas/behavior/v0.5-to-v0.6.md"

    --8<-- "guide/_deltas/behavior/v0.7-to-v0.8.md"

    --8<-- "guide/_deltas/behavior/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/behavior/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/behavior/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/behavior/v0.11-to-v0.12.md"

### Coming from v0.1.x

=== "Breaking changes"

    --8<-- "guide/_deltas/breaking/v0.3-to-v0.4.md"

    --8<-- "guide/_deltas/breaking/v0.5-to-v0.6.md"

    --8<-- "guide/_deltas/breaking/v0.7-to-v0.8.md"

    --8<-- "guide/_deltas/breaking/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/breaking/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/breaking/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/breaking/v0.11-to-v0.12.md"

=== "Behavior changes"

    --8<-- "guide/_deltas/behavior/v0.3-to-v0.4.md"

    --8<-- "guide/_deltas/behavior/v0.5-to-v0.6.md"

    --8<-- "guide/_deltas/behavior/v0.7-to-v0.8.md"

    --8<-- "guide/_deltas/behavior/v0.8-to-v0.9.md"

    --8<-- "guide/_deltas/behavior/v0.9-to-v0.10.md"

    --8<-- "guide/_deltas/behavior/v0.10-to-v0.11.md"

    --8<-- "guide/_deltas/behavior/v0.11-to-v0.12.md"

______________________________________________________________________

If you hit anything not covered here, [open an issue](https://github.com/Borda/pyDeprecate/issues) — we are happy to help.
