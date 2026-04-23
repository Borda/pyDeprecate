---
id: cli
description: 'Command-line interface reference for pyDeprecate: scan deprecated wrappers, enforce removal deadlines, detect chains, and run all checks in one pass.'
---

# CLI Reference

The `pydeprecate` CLI lets you run all three [audit checks](audit.md) — wrapper configuration, expiry enforcement, and chain detection — directly from the command line without writing a Python script.

## Installation

```bash
pip install 'pyDeprecate[cli]'
```

The `expiry` subcommand and the expiry phase of `all` additionally require the `packaging` library:

```bash
pip install 'pyDeprecate[audit]'
```

## Quick start

```bash
pydeprecate check path/to/your/package   # validate wrapper config (default)
pydeprecate path/to/your/package         # same — backward-compatible shorthand
```

## Subcommands

### `check` (default)

Validates wrapper configuration: invalid `args_mapping` keys, identity mappings, no-effect wrappers, and deprecated-to-deprecated chains. Backed by [`find_deprecation_wrappers()`](audit.md#validating-wrapper-configuration).

```bash
pydeprecate check path/to/your/package
pydeprecate check mypackage.submodule
```

Running `pydeprecate <path>` without a subcommand routes to `check` for backward compatibility.

Exit 1 only for hard errors (invalid argument mappings). Chains, identity mappings, and no-effect wrappers are warnings — exit 0.

______________________________________________________________________

### `expiry`

Checks whether any deprecated wrappers have passed their `remove_in` deadline using [`validate_deprecation_expiry()`](audit.md#enforcing-removal-deadlines). Requires `pip install 'pyDeprecate[audit]'`.

```bash
# explicit version
pydeprecate expiry path/to/your/package --version 2.0.0

# auto-detect version from installed package metadata
pydeprecate expiry path/to/your/package
```

Exit 1 if any wrapper is past its removal deadline, or if `packaging` is not installed (use `--skip_errors` to suppress).

______________________________________________________________________

### `chains`

Detects deprecated wrappers whose `target` is itself a deprecated callable (`ChainType.TARGET`) or where stacked argument mappings should be collapsed (`ChainType.STACKED`). Backed by [`validate_deprecation_chains()`](audit.md#detecting-deprecation-chains).

```bash
pydeprecate chains path/to/your/package
```

Exit 1 if any chains are found.

______________________________________________________________________

### `all`

Single scan pass running all three checks ([`find_deprecation_wrappers()`](audit.md#validating-wrapper-configuration), [`validate_deprecation_expiry()`](audit.md#enforcing-removal-deadlines), [`validate_deprecation_chains()`](audit.md#detecting-deprecation-chains)). If `packaging` is not installed, the expiry check is skipped with a warning and the other checks still run.

```bash
# explicit version
pydeprecate all path/to/your/package --version 2.0.0

# auto-detect version
pydeprecate all path/to/your/package
```

Exit 1 if any check finds a hard error. If `packaging` is not installed, expiry is skipped with a warning and does not cause exit `1`.

______________________________________________________________________

## Flags

| Flag                  | Applies to                         | Effect                                                                                                  |
| --------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `--version VERSION`   | `expiry`, `all`                    | Package version for deadline comparison. Auto-detected from installed metadata if omitted.              |
| `--no_recursive true` | `check`, `expiry`, `chains`, `all` | Scan top-level module only; skip submodules.                                                            |
| `--skip_errors true`  | `check`, `expiry`, `chains`, `all` | Always exit `0` even when hard errors are found — useful for advisory CI steps that should never block. |

## Exit codes

| Subcommand | Exit `0`                                                        | Exit `1`                                                              |
| ---------- | --------------------------------------------------------------- | --------------------------------------------------------------------- |
| `check`    | Clean or advisory warnings only (chains / identity / no-effect) | Invalid argument mappings found                                       |
| `expiry`   | No expired wrappers                                             | Expired wrappers found; or `packaging` not installed                  |
| `chains`   | No chains                                                       | Deprecated-to-deprecated chains found                                 |
| `all`      | All checks clean                                                | Any hard error above (`packaging` missing → skips expiry, no failure) |

## Path formats

All subcommands accept:

- **Package directory** — path to a directory with `__init__.py` (e.g. `src/mypackage`)
- **Importable module name** — dotted module path (e.g. `mypackage.utils`)
- **Plain directory** — scans top-level `.py` files only; not supported for `expiry` and `chains` which require an importable module name

## CI integration

**Makefile:**

```makefile
lint-deprecations:
    pydeprecate check src/mypackage

check-zombie-code:
    pydeprecate expiry src/mypackage --version $(VERSION)
```

**pre-commit hook:**

```yaml
- repo: local
  hooks:
    - id: pydeprecate
      name: validate deprecation wrappers
      entry: pydeprecate check
      language: system
      pass_filenames: false
      args: [src/mypackage]
```

**GitHub Actions:**

```yaml
- name: Validate deprecations
  run: pydeprecate all src/mypackage --version ${{ env.PACKAGE_VERSION }}
```

## Python module invocation

Both `pydeprecate` and `python -m deprecate` are equivalent entry points:

```bash
python -m deprecate check src/mypackage
python -m deprecate expiry src/mypackage --version 2.0.0
```
