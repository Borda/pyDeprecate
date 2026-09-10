---
id: cli
description: 'Command-line interface reference for pyDeprecate: scan deprecated wrappers, enforce removal deadlines, lint deprecations against a governance policy, detect chains, and run all checks in one pass.'
---

# CLI Reference

The `pydeprecate` CLI lets you run all four [audit checks](audit.md) — wrapper configuration, expiry enforcement, policy linting, and chain detection — directly from the command line without writing a Python script.

## Installation

```bash
pip install 'pyDeprecate[audit,cli]'
```

Start with `[audit,cli]` unless you are certain you will never use `expiry`, `policy`, or `all`. The `[audit]` extra pulls in `packaging`, which `expiry` always requires, and which `policy` (and the policy phase of `all`) needs only when a wrapper's `deprecated_in`/`remove_in` is actually compared against a version — a policy scan with no version fields set never imports it.

## Quick start

```bash
pydeprecate all src/mypackage   # run all checks + deprecation table in one pass
pydeprecate check path/to/your/package   # validate wrapper config
pydeprecate check mypackage.submodule    # importable module name also accepted
pydeprecate policy src/mypackage         # gate on your deprecation-governance rules
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

        `check` treats chains as advisory warnings (exit 0). `all` treats chains as hard errors (exit 1), because `all` ≡ `check + expiry + policy + chains` and `chains` exits 1 on any chain found — `policy` remains advisory even inside `all`.

=== "expiry"

    Checks whether any deprecated wrappers have passed their `remove_in` deadline using [`validate_deprecation_expiry()`](audit.md#enforcing-removal-deadlines). Requires the `[audit]` extra (included in `[audit,cli]`).

    ```bash
    # explicit version
    pydeprecate expiry path/to/your/package --version 2.0.0

    # auto-detect version (searches up to 2 parent directories for pyproject.toml, else installed metadata)
    pydeprecate expiry path/to/your/package
    ```

    Exit 1 if any wrapper is past its removal deadline. If `packaging` is not installed, the check is skipped with a warning and exits 0 (use `--exit-zero` to suppress exit 1 when expired wrappers are found).

=== "policy"

    Checks whether each deprecation was *scheduled* responsibly, using [`validate_deprecation_policy()`](audit.md#enforcing-a-deprecation-policy). Four rules run by default — `min-grace`, `remove-only-at`, `message-required`, and `deprecated-in-not-future` — and every violation message is prefixed with the slug of the rule it broke. Requires the `[audit]` extra (included in `[audit,cli]`).

    ```bash
    # default policy: one-minor grace window, major-only removals, guidance required
    pydeprecate policy path/to/your/package --version 2.0.0

    # tune the rules to your own release convention
    pydeprecate policy path/to/your/package --min-grace="2 minors" --remove-only-at=minor

    # switch individual rules off
    pydeprecate policy path/to/your/package --min-grace=None --message-required=False
    ```

    Exit 1 if any violation is found, exit 2 if `--min-grace` or `--remove-only-at` is malformed, exit 0 when clean or when `packaging` is not installed (skipped with a warning). Only the `deprecated-in-not-future` rule needs `--version`; the others run without a resolved version.

    !!! warning "`all` reports policy violations but never fails on them"

        The policy defaults encode *a* project convention — major-only removals, a one-minor grace window — that not every repository shares, so `pydeprecate all` runs this check in **advisory** mode: violations are printed, but they never change `all`'s exit code. To gate CI on the policy, run the dedicated `pydeprecate policy` subcommand, whose exit code is truthful.

=== "chains"

    Detects deprecated wrappers whose `target` is itself a deprecated callable (`ChainType.TARGET`) or where stacked argument mappings should be collapsed (`ChainType.STACKED`). Backed by [`validate_deprecation_chains()`](audit.md#detecting-deprecation-chains).

    ```bash
    pydeprecate chains path/to/your/package
    ```

    Exit 1 if any chains are found.

=== "all"

    Single scan pass running all four checks ([`find_deprecation_wrappers()`](audit.md#validating-wrapper-configuration), [`validate_deprecation_expiry()`](audit.md#enforcing-removal-deadlines), [`validate_deprecation_policy()`](audit.md#enforcing-a-deprecation-policy), [`validate_deprecation_chains()`](audit.md#detecting-deprecation-chains)), then appends a compact markdown deprecation table. If `packaging` is not installed, the expiry and policy checks are skipped with a warning and the other checks still run.

    ```bash
    # explicit version
    pydeprecate all path/to/your/package --version 2.0.0

    # auto-detect version
    pydeprecate all path/to/your/package
    ```

    Exit 1 if any hard error is found: invalid argument mappings, deprecated-to-deprecated chains, or expired wrappers. **Policy violations are advisory here** — they are printed but never contribute to the exit code, because the policy defaults encode a project convention that not every repository shares; run `pydeprecate policy` as its own CI step to gate on them. If `packaging` is not installed, both `expiry` and `policy` are skipped with a warning and do not cause exit `1`. The deprecation table is always appended regardless of pass/fail outcome.

=== "status"

    Generates and prints a markdown deprecation table to stdout. Standalone — runs no checks and exits `0` on success (invalid `--style` falls back to `compact` with a warning to stderr). Use this when you only want to render the deprecation status table without running any validation.

    ```bash
    pydeprecate status path/to/your/package
    pydeprecate status path/to/your/package --style matrix
    pydeprecate status path/to/your/package --version 2.0.0 --output DEPRECATIONS.md
    ```

    `--style compact` (default) renders one row per symbol with a status column; `--style matrix` renders one column per version with `D`/`R` lifecycle markers. `--output FILE` writes the table to a file in addition to printing it to stdout.

## Flags

| Flag                | `check` | `expiry` | `policy` | `chains` | `all` | `status` | Effect                                                                                                  | Note                                                                                                 |
| ------------------- | :-----: | :------: | :------: | :------: | :---: | :------: | ------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `--version VERSION` |         |    ✓     |    ✓     |          |   ✓   |    ✓     | Package version for deadline comparison. Auto-detected from installed metadata if omitted.              | `policy` needs it only for the `deprecated-in-not-future` rule.                                      |
| `--norecursive`     |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |    ✓     | Scan top-level module only; skip submodules.                                                            | Fire auto-generates this from `recursive=False` — the flag is `--norecursive`, not `--no-recursive`. |
| `--exit-zero`       |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |          | Always exit `0` even when hard errors are found — useful for advisory CI steps that should never block. |                                                                                                      |
| `--style`           |         |          |          |          |       |    ✓     | Table rendering style — `compact` (default) or `matrix`.                                                |                                                                                                      |
| `--output FILE`     |         |          |          |          |       |    ✓     | Also save the markdown table to a file. Table is always printed to stdout regardless.                   |                                                                                                      |

### Policy rule flags

These four are specific to `policy` — one flag per rule, each switched off with `None` (grace window, removal cadence) or `False` (the two boolean rules).

| Flag                                | Default     | Rule slug                  | Effect                                                                                          |
| ----------------------------------- | ----------- | -------------------------- | ----------------------------------------------------------------------------------------------- |
| `--min-grace="<count> <unit>"`      | `"1 minor"` | `min-grace`                | Minimum distance between `deprecated_in` and `remove_in`; unit is `major`, `minor`, or `patch`. |
| `--remove-only-at=<level>`          | `major`     | `remove-only-at`           | Release level a `remove_in` version is allowed to land on — `major`, `minor`, or `patch`.       |
| `--message-required=<bool>`         | `True`      | `message-required`         | Require every wrapper to name a replacement (a `target`, a mapping, or a `message_template`).   |
| `--deprecated-in-not-future=<bool>` | `True`      | `deprecated-in-not-future` | Require `deprecated_in` to be at or behind `--version`.                                         |

The `--remove-only-at=major` default suits a project past `1.0`. On a `0.x` line the minor **is** the breaking cadence, so pass `--remove-only-at=minor` there rather than switching the rule off — you keep the gate, you just point it at the release level your project actually breaks on.

A malformed `--min-grace` or `--remove-only-at` value is rejected before the scan starts and exits `2` with a message naming the accepted spellings — it is never silently ignored.

`--version` auto-detect: when the scanned argument is an existing *path*, `_read_pyproject_version` searches the current directory and up to 2 parent directories for a `pyproject.toml` (current dir + 2 levels up); the nearest one found wins, falling back to installed package metadata. A bare module *name* skips the `pyproject.toml` lookup entirely, so it can never pick up an unrelated project's version from your current working directory.

## Exit codes

| Subcommand | Exit `0`                                                                 | Exit `1`                                                              | Exit `2`                                            |
| ---------- | ------------------------------------------------------------------------ | --------------------------------------------------------------------- | --------------------------------------------------- |
| `check`    | Clean or advisory warnings only (chains / identity / no-effect)          | Invalid argument mappings found                                       | —                                                   |
| `expiry`   | No expired wrappers; or `packaging` not installed (skipped with warning) | Expired wrappers found (and `--exit-zero` not set)                    | —                                                   |
| `policy`   | No violations; or `packaging` not installed (skipped with warning)       | Policy violations found (and `--exit-zero` not set)                   | Malformed `--min-grace` or `--remove-only-at` value |
| `chains`   | No chains                                                                | Deprecated-to-deprecated chains found                                 | —                                                   |
| `all`      | All checks clean, or only policy violations (table always appended)      | Any hard error above (`packaging` missing → skips expiry, no failure) | —                                                   |
| `status`   | Always — status table is not a pass/fail gate                            | —                                                                     | —                                                   |

Policy violations are the one finding `all` reports without acting on: they never move `all` from `0` to `1`. Give `policy` its own CI step when you want the build to fail on them.

Unknown or misspelled flags are never silently ignored: any unconsumed argument (e.g. `pydeprecate expiry mypackage --verison 2.0`) exits `2` with a `Could not consume arg: --verison` diagnostic from the underlying argument parser.

## CI recipes

Two gates, two steps. The expiry gate fails the build when deprecated code outlives its deadline; the policy gate fails it when a *new* deprecation is scheduled in a way the project does not allow. Keeping them separate means a failure tells you which of the two problems you have.

```yaml
# .github/workflows/deprecations.yml
name: Deprecations
on: [push, pull_request]

jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e . "pyDeprecate[audit,cli]"

      # 1. wrapper config + chains + expiry (policy runs here too, advisory only)
      - name: Audit deprecations
        run: pydeprecate all src/mypackage

      # 2. governance gate — this is the step that fails on a policy violation
      - name: Enforce the deprecation policy
        run: pydeprecate policy src/mypackage --min-grace="1 minor" --remove-only-at=major
```

Spell the rule flags out even where they match the defaults, as above: the step then doubles as the written record of what your project promises, and a later change to pyDeprecate's defaults cannot quietly change what your CI enforces.

While you are bringing an existing codebase into line, run the gate advisory-first so it reports without blocking, then drop the flag once the backlog is clear:

```bash
pydeprecate policy src/mypackage --exit-zero
```

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
