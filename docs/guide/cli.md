---
id: cli
description: 'Command-line interface reference for pyDeprecate: scan deprecated wrappers, enforce removal deadlines, detect chains, and run all checks in one pass.'
---

# CLI Reference

The `pydeprecate` CLI lets you run all three [audit checks](audit.md) — wrapper configuration, expiry enforcement, and chain detection — directly from the command line without writing a Python script.

## Installation

```bash
pip install 'pyDeprecate[audit,cli]'
```

Start with `[audit,cli]` unless you are certain you will never use `expiry` or `all`. The `[audit]` extra pulls in `packaging`, which `expiry` and the expiry phase of `all` require.

## Quick start

```bash
pydeprecate all src/mypackage   # run all checks + deprecation table in one pass
pydeprecate check path/to/your/package   # validate wrapper config
pydeprecate check mypackage.submodule    # importable module name also accepted
```

**Quick demo** using pyDeprecate's own test fixtures (no package setup needed):

```bash
# Run all checks + deprecation table on the bundled test fixtures
pydeprecate all tests --version 1.2
# Standalone deprecation status table only
pydeprecate status tests --version 1.2
```

`tests/` contains pyDeprecate's own deprecation fixtures. Note: `expiry` and `chains` (run as part of `all`) require an importable package name rather than a plain directory path.

## Subcommands

=== "check"

    Validates wrapper configuration: invalid `args_mapping` keys, identity mappings, no-effect wrappers, and deprecated-to-deprecated chains. Backed by [`find_deprecation_wrappers()`](audit.md#validating-wrapper-configuration).

    ```bash
    pydeprecate check path/to/your/package
    pydeprecate check mypackage.submodule
    ```

    Exit 1 only for invalid argument mappings. Chains, identity mappings, and no-effect wrappers are reported as warnings — exit 0.

    !!! note "Chain handling differs in `all`"

        `check` treats chains as advisory warnings (exit 0). `all` treats chains as hard errors (exit 1), because `all` ≡ `check + expiry + chains` and `chains` exits 1 on any chain found.

=== "expiry"

    Checks whether any deprecated wrappers have passed their `remove_in` deadline using [`validate_deprecation_expiry()`](audit.md#enforcing-removal-deadlines). Requires the `[audit]` extra (included in `[audit,cli]`).

    ```bash
    # explicit version
    pydeprecate expiry path/to/your/package --version 2.0.0

    # auto-detect version from installed package metadata
    pydeprecate expiry path/to/your/package
    ```

Exit 1 if any wrapper is past its removal deadline. If `packaging` is not installed, the check is skipped with a warning and exits 0 (use `--exit-zero` to suppress exit 1 when expired wrappers are found).

=== "chains"

    Detects deprecated wrappers whose `target` is itself a deprecated callable (`ChainType.TARGET`) or where stacked argument mappings should be collapsed (`ChainType.STACKED`). Backed by [`validate_deprecation_chains()`](audit.md#detecting-deprecation-chains).

    ```bash
    pydeprecate chains path/to/your/package
    ```

    Exit 1 if any chains are found.

=== "all"

    Single scan pass running all three checks ([`find_deprecation_wrappers()`](audit.md#validating-wrapper-configuration), [`validate_deprecation_expiry()`](audit.md#enforcing-removal-deadlines), [`validate_deprecation_chains()`](audit.md#detecting-deprecation-chains)), then appends a compact markdown deprecation table. If `packaging` is not installed, the expiry check is skipped with a warning and the other checks still run.

    ```bash
    # explicit version
    pydeprecate all path/to/your/package --version 2.0.0

    # auto-detect version
    pydeprecate all path/to/your/package
    ```

    Exit 1 if any hard error is found: invalid argument mappings, deprecated-to-deprecated chains, or expired wrappers. If `packaging` is not installed, expiry is skipped with a warning and does not cause exit `1`. The deprecation table is always appended regardless of pass/fail outcome.

=== "status"

    Generates and prints a markdown deprecation table to stdout. Standalone — runs no checks and exits `0` on success (invalid `--style` falls back to `compact` with a warning to stderr). Use this when you only want to render the deprecation status table without running any validation.

    ```bash
    pydeprecate status path/to/your/package
    pydeprecate status path/to/your/package --style matrix
    pydeprecate status path/to/your/package --version 2.0.0 --output DEPRECATIONS.md
    ```

    `--style compact` (default) renders one row per symbol with a status column; `--style matrix` renders one column per version with `D`/`R` lifecycle markers. `--output FILE` writes the table to a file in addition to printing it to stdout.

## Flags

| Flag                | `check` | `expiry` | `chains` | `all` | `status` | Effect                                                                                                  | Note                                                                                                 |
| ------------------- | :-----: | :------: | :------: | :---: | :------: | ------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `--version VERSION` |         |    ✓     |          |   ✓   |    ✓     | Package version for deadline comparison. Auto-detected from installed metadata if omitted.              |                                                                                                      |
| `--norecursive`     |    ✓    |    ✓     |    ✓     |   ✓   |    ✓     | Scan top-level module only; skip submodules.                                                            | Fire auto-generates this from `recursive=False` — the flag is `--norecursive`, not `--no-recursive`. |
| `--exit-zero`       |    ✓    |    ✓     |    ✓     |   ✓   |          | Always exit `0` even when hard errors are found — useful for advisory CI steps that should never block. |                                                                                                      |
| `--style`           |         |          |          |       |    ✓     | Table rendering style — `compact` (default) or `matrix`.                                                |                                                                                                      |
| `--output FILE`     |         |          |          |       |    ✓     | Also save the markdown table to a file. Table is always printed to stdout regardless.                   |                                                                                                      |

## Exit codes

| Subcommand | Exit `0`                                                        | Exit `1`                                                              |
| ---------- | --------------------------------------------------------------- | --------------------------------------------------------------------- |
| `check`    | Clean or advisory warnings only (chains / identity / no-effect) | Invalid argument mappings found                                       |
| `expiry`   | No expired wrappers                                             | Expired wrappers found; or `packaging` not installed                  |
| `chains`   | No chains                                                       | Deprecated-to-deprecated chains found                                 |
| `all`      | All checks clean (deprecation table always appended)            | Any hard error above (`packaging` missing → skips expiry, no failure) |
| `status`   | Always — status table is not a pass/fail gate                   | —                                                                     |

## Path formats

All subcommands accept:

- **Package directory** — path to a directory with `__init__.py` (e.g. `src/mypackage`)
- **Importable module name** — dotted module path (e.g. `mypackage.utils`)
- **Plain directory** — scans top-level `.py` files only; not supported for `expiry` and `chains` which require an importable module name

## Python module invocation

Both `pydeprecate` and `python -m deprecate` are equivalent entry points:

```bash
python -m deprecate check src/mypackage
python -m deprecate expiry src/mypackage --version 2.0.0
```

Useful in environments where the `pydeprecate` script is not on `PATH` (e.g. inside a Docker image where only `python` is available).
