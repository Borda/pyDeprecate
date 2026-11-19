---
id: lifecycle
description: Follow a deprecation from introduction and caller warnings through tests, CI audits, removal, cleanup, and release notes.
---

# Deprecation lifecycle

Follow a summation API from its deprecation in `0.1` to removal in `0.5`.

## 1. Add the wrapper

Keep the old entry point while forwarding calls to its replacement.

```python
from deprecate import deprecated, void


# NEW API — adds quantities using the replacement implementation
def base_sum_kwargs(a: int, b: int = 5) -> int:
    return a + b


# DEPRECATED API — `decorated_sum` replaced by `base_sum_kwargs`
@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.5")
def decorated_sum(a: int, b: int = 5) -> int:
    """Add two quantities through the compatibility entry point."""
    return void(a, b)


print(decorated_sum(2, 3))  # warns: FutureWarning
```

<details><summary>Output: <code>decorated_sum(2, 3)</code></summary>

```text
5
```

</details>

This **forwards** the compatibility function to the replacement. See [Functions](functions.md) for forwarding and argument mapping, or [Classes](classes.md) for class migrations.

## 2. Warn callers

The default `FutureWarning` names the deprecated function, replacement, introduction version, and removal version. The summation call still returns the *replacement's* result.

| `num_warns`   | Behavior                                     |
| ------------- | -------------------------------------------- |
| `1` (default) | Emit once per wrapper lifetime.              |
| `N > 1`       | Emit at most `N` times per wrapper lifetime. |
| `-1`          | Emit on every call.                          |
| `0`           | Suppress deprecation messages.               |

For argument deprecations, the warning budget applies **per deprecated argument**.

!!! note "Output routing"

    Python's warning filters can further limit visible warnings. Set `stream` to a logger or another callable to redirect messages, or `None` to silence them. Forwarding still works with `stream=None`.

See [Customization](customization.md) for message templates and stream signatures.

## 3. Test the migration

Check **both** the compatibility result and the replacement's warning-free behavior.

```python
import pytest

from deprecate import assert_no_warnings, deprecated


# NEW API — adds quantities without a deprecation notice
def base_sum_kwargs(a: int, b: int = 5) -> int:
    return a + b


# DEPRECATED API — give the compatibility function an independent warning budget
@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.5")
def decorated_sum(a: int, b: int = 5) -> int:
    return a + b


def test_summation_migration() -> None:
    """Keep legacy callers working while new callers use the replacement.

    A consumer still calls the compatibility entry point during the migration
    window. It must warn once, return the correct total, and then stay silent.
    """
    with pytest.warns(FutureWarning, match="base_sum_kwargs"):
        assert decorated_sum(2, 3) == 5
    with assert_no_warnings(FutureWarning):
        assert base_sum_kwargs(2, 3) == 5
        assert decorated_sum(2, 3) == 5


test_summation_migration()
print("Migration checks passed.")
```

<details><summary>Output: <code>test_summation_migration()</code></summary>

```text
Migration checks passed.
```

</details>

See [Testing](../integrations/testing.md) for pytest patterns, warning budgets, and `assert_no_warnings` message matching.

## 4. Audit in CI

Run the same audits *locally and in CI* before each release.

1. Install `pyDeprecate[audit]` in the audit environment.
2. Run `pydeprecate expiry --version 0.4 <package-path>` while the summation wrapper is still supported.
3. Run `pydeprecate policy <package-path>` to enforce your configured grace period and metadata requirements.
4. Configure pre-commit hooks or the GitHub Action to repeat the checks automatically.

Replace `<package-path>` with your package's source path. See [CLI Reference](../audit/cli.md) for arguments and exit codes, and [CI Integration](../audit/ci-integration.md) for pre-commit and Action recipes.

!!! warning "Confirm audit coverage"

    Review skipped imports and version-detection gaps. Pass `--version` explicitly when the package version cannot be detected. <u>A passing scan does not prove that skipped modules were checked.</u>

## 5. Remove and prune

At version `0.5`, the expiry gate reports the summation wrapper because the current version has **reached** `remove_in`.

1. Run `pydeprecate expiry --version 0.5 <package-path>` to identify wrappers due for removal.
2. Delete `decorated_sum` and remove the old name from public re-exports and import lists.
3. Migrate remaining internal callers to `base_sum_kwargs`.
4. Prune obsolete compatibility tests, documentation examples, and wrapper-only imports.
5. Keep replacement tests, regenerate the deprecation table, and rerun the audits.

!!! note "Removal is manual"

    `remove_in` records a *deadline*. It does **not** delete the wrapper or automatically stop forwarding calls. The expiry gate fails for expired wrappers unless failure is explicitly suppressed with `--exit-zero`.

## 6. Record the release

Generate the active deprecation inventory *before* removing wrappers, so release notes can preserve their migration information.

```python
from types import ModuleType
from deprecate import deprecated, generate_deprecation_table


# NEW API — the replacement adds quantities
def base_sum_kwargs(a: int, b: int = 5) -> int:
    return a + b


# DEPRECATED API — the old name remains discoverable until removal
@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.5")
def decorated_sum(a: int, b: int = 5) -> int:
    return a + b


module = ModuleType("example")
module.decorated_sum = decorated_sum
report = generate_deprecation_table(module, current_version="0.4", recursive=False)
print("Deprecation Active" in report)
```

<details><summary>Output: <code>Deprecation Active in report</code></summary>

```text
True
```

</details>

`generate_deprecation_table` returns Markdown from discovered wrapper metadata. See [Audit Tools](../audit/index.md) for the full table and `style="matrix"` release columns.

1. Announce the deprecation, replacement, and planned removal version when introducing the wrapper.
2. Record the removed public name and replacement in the removal release's changelog and release notes.
3. Publish the refreshed inventory after deletion; removed wrappers no longer appear in a live scan.
