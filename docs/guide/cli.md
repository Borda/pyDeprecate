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

    Checks whether each deprecation was *scheduled* responsibly, using [`validate_deprecation_policy()`](audit.md#enforcing-a-deprecation-policy). Two rules exist — `min-grace` and `message-required`, both on by default — and every violation message is prefixed with the slug of the rule it broke. `min-grace` requires the `[audit]` extra (included in `[audit,cli]`); `message-required` works without it. `policy` never needs a package version, so it takes no `--version` flag.

    ```bash
    # default policy: three-minor grace window on a clean release boundary, guidance required
    pydeprecate policy path/to/your/package

    # tune the window to your own release convention — here: removals only at a major
    pydeprecate policy path/to/your/package --min-grace=1.0

    # switch individual rules off
    pydeprecate policy path/to/your/package --min-grace=None --message-required=False
    ```

    Exit 1 if any enabled rule finds a violation, exit 2 if `--min-grace` is malformed **or** if the nearest `pyproject.toml` cannot be read or parsed (prints `Cannot read <path>: <err>`), and exit 0 when clean or when `--exit-zero` is passed. If `packaging` is not installed, `min-grace` is skipped with a warning, but `message-required` still runs and can return exit 1.

    !!! warning "`all` reports policy violations but never fails on them"

        The policy defaults encode *a* project convention — a three-minor grace window on a clean release boundary — that not every repository shares, so `pydeprecate all` runs this check in **advisory** mode: violations are printed, but they never change `all`'s exit code. Inside `all` the violation table renders as `[WARNING]` (yellow), with a trailer noting "advisory here — run `pydeprecate policy` to gate on it" — never as `[ERROR]`, which is reserved for a direct `pydeprecate policy` run. To gate CI on the policy, run the dedicated `pydeprecate policy` subcommand, whose exit code is truthful. A config-read failure is the one exception to "advisory": an unreadable or malformed `pyproject.toml` still exits `2` inside `all` too (see below), before the chains check ever runs.

=== "chains"

    Detects deprecated wrappers whose `target` is itself a deprecated callable (`ChainType.TARGET`) or where stacked argument mappings should be collapsed (`ChainType.STACKED`). Backed by [`validate_deprecation_chains()`](audit.md#detecting-deprecation-chains).

    ```bash
    pydeprecate chains path/to/your/package
    ```

    Exit 1 if any chains are found.

=== "all"

    Single scan pass running all four checks ([`find_deprecation_wrappers()`](audit.md#validating-wrapper-configuration), [`validate_deprecation_expiry()`](audit.md#enforcing-removal-deadlines), [`validate_deprecation_policy()`](audit.md#enforcing-a-deprecation-policy), [`validate_deprecation_chains()`](audit.md#detecting-deprecation-chains)), then appends a compact markdown deprecation table. If `packaging` is not installed, expiry and policy's `min-grace` rule are skipped with a warning; `message-required` still runs, and the other checks continue.

    ```bash
    # explicit version
    pydeprecate all path/to/your/package --version 2.0.0

    # auto-detect version
    pydeprecate all path/to/your/package
    ```

    Exit 1 if any hard error is found: invalid argument mappings, deprecated-to-deprecated chains, or expired wrappers. **Policy violations are advisory here** — they are printed (rendered as `[WARNING]`, with a trailer noting the check is advisory and naming `pydeprecate policy` as the gate) but never contribute to the exit code, because the policy defaults encode a project convention that not every repository shares; run `pydeprecate policy` as its own CI step to gate on them. This advisory treatment covers violation *counts* only: a policy **configuration** failure — the nearest `pyproject.toml` cannot be read or parsed, or a `[tool.pydeprecate.policy]` value is malformed — still exits `2`, printing `Cannot read <path>: <err>`, and that exit is checked before the chains check ever runs. If `packaging` is not installed, expiry is skipped and policy runs its packaging-free `message-required` rule in advisory mode, so neither affects `all`'s exit `1`; a malformed explicit `--version` is also advisory. With `packaging` available, an invalid explicit `--version` exits `2`. The deprecation table is always appended regardless of pass/fail outcome.

=== "status"

    Generates and prints a markdown deprecation table to stdout. Standalone — runs no checks and exits `0` on success (invalid `--style` falls back to `compact` with a warning to stderr). Use this when you only want to render the deprecation status table without running any validation.

    ```bash
    pydeprecate status path/to/your/package
    pydeprecate status path/to/your/package --style matrix
    pydeprecate status path/to/your/package --version 2.0.0 --output DEPRECATIONS.md
    ```

    `--style compact` (default) renders one row per symbol with a status column; `--style matrix` renders one column per version with `D`/`R` lifecycle markers. `--output FILE` writes the table to a file in addition to printing it to stdout.

## Flags

| Flag                | Default          | `check` | `expiry` | `policy` | `chains` | `all` | `status` | Effect                                                                                                                                                                                | Note                                                                                                                                                                     |
| ------------------- | ---------------- | :-----: | :------: | :------: | :------: | :---: | :------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--version VERSION` | auto-detected    |         |    ✓     |          |          |   ✓   |    ✓     | Package version for deadline comparison. Auto-detected from installed metadata if omitted.                                                                                            | `policy` compares `deprecated_in` to `remove_in` only, so it takes no version.                                                                                           |
| `--norecursive`     | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |    ✓     | Scan top-level module only; skip submodules.                                                                                                                                          | Fire auto-generates this from `recursive=False` — the flag is `--norecursive`, not `--no-recursive`.                                                                     |
| `--exit-zero`       | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |          | Always exit `0` even when hard errors are found — useful for advisory CI steps that should never block.                                                                               |                                                                                                                                                                          |
| `--exclude`         | `pyproject.toml` |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |    ✓     | Glob patterns over full dotted module names to leave out of the scan; the scan itself never imports a matching package nor descends into it. One pattern, comma-separated, or a list. | Default is the `exclude` list of `[tool.pydeprecate]` (see below), else nothing. Quote the value — an unquoted `*` is expanded by the shell first. Malformed → exit `2`. |
| `--style`           | `compact`        |         |          |          |          |       |    ✓     | Table rendering style — `compact` (default) or `matrix`.                                                                                                                              |                                                                                                                                                                          |
| `--output FILE`     | stdout only      |         |          |          |          |       |    ✓     | Also save the markdown table to a file. Table is always printed to stdout regardless.                                                                                                 |                                                                                                                                                                          |

A bare `pydeprecate policy src/mypackage` therefore runs a recursive scan, exits `1` on violations, and applies both default-on rules below (`--min-grace=0.3`, `--message-required=True`) — unless the project's `pyproject.toml` says otherwise (see [Project configuration in `pyproject.toml`](#project-configuration-in-pyprojecttoml)).

### Policy rule flags

These two are specific to `policy` — one flag per rule, switched off with `--min-grace=None` and `--message-required=False`. The *Default* column is the built-in value; a `[tool.pydeprecate.policy]` table in `pyproject.toml` replaces it, and a typed flag beats both.

| Flag                        | Default | Rule slug          | Effect                                                                                                                                        |
| --------------------------- | ------- | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `--min-grace=<delta>`       | `0.3`   | `min-grace`        | Minimum distance between `deprecated_in` and `remove_in` as a version-shaped delta: `1.0` one major, `0.3` three minors, `0.0.2` two patches. |
| `--message-required=<bool>` | `True`  | `message-required` | Require every wrapper to name a replacement (a `target`, a mapping, or a `message_template`).                                                 |

The delta has two or three components with at most one non-zero; a bare `1` is rejected as ambiguous (one *what*?) and so is a mixed spelling such as `1.2`. The removal must be one clean bump of a single component (`1.2` → `1.5` or `2.0`, never `2.3`); a coarser bump always clears a finer window — `0.3` is satisfied by one major bump. An all-zero spelling such as `0.0` is a zero-count window: no minimum distance, but a clean bump of that unit or coarser is still required. Ten or more steps: quote as a string (`--min-grace='"0.10"'`), else the shell value parses as the float `0.1`. `--min-grace=1.0` (or `min-grace = "1.0"` in `pyproject.toml`) is the strict "removals only at a major release" policy. A `0.x` project needs no special setting: a bump to `1.0` is a major step and clears any window, while removals inside the `0.x` line are counted in minors as usual.

A malformed `--min-grace` value is rejected before the scan starts and exits `2` with a message naming the accepted spellings — it is never silently ignored.

### Project configuration in `pyproject.toml`

Declare the settings once, next to the code they govern, and every bare `pydeprecate` run — a developer's shell or a CI step — applies them without repeating flags:

```toml
[tool.pydeprecate]
exclude = ["my_package.tests", "*._legacy*"]   # every subcommand: the scan skips these packages (never imports them itself)

[tool.pydeprecate.policy]                      # the `policy` subcommand's rules, keyed by rule slug
min-grace = "0.3"  # quoted dotted delta; false switches the rule off (TOML has no null)
message-required = true
```

Each setting resolves independently as **flag → `pyproject.toml` → built-in default**, and the header lines of every run name the source of each value (`flag`, `pyproject.toml`, or `built-in`), so a log always shows what was applied:

```text
Exclude: my_package.tests, *._legacy* (pyproject.toml)
Policy: min-grace=0.3 (pyproject.toml)  message-required=True (built-in)
```

- The table is looked up like `--version` auto-detection below: from the scanned *path*'s directory up to two parents, nearest file that declares `[tool.pydeprecate]` wins (one table per project — a nested file that declares it replaces the parent's whole table); a bare module *name* never triggers the lookup.
- `exclude` patterns are `fnmatch` globs over the full dotted module name (`my_package.tests`, not `tests`); a pattern that matches a package excludes its whole subtree. A wrapper is reported iff the module it is reported under does not match — so even when something else in the package imports an excluded module, its wrappers stay out. A string or comma-separated string is accepted in place of the list.
- `min-grace` takes the dotted delta as a string (`"1.0"`, `"0.3"`, `"0.0.2"`); a one-key unit table (`{ minor = 3 }`) is also accepted; quote the delta (`"0.10"`, not `0.10`) — a TOML float drops the trailing zero exactly as the shell does.
- A malformed value from the file exits `2` and the message names the `pyproject.toml` it came from; a non-boolean `message-required` or a non-string `exclude` entry is rejected the same way. Unknown keys in either table (`min_grace` with an underscore is the usual typo) are reported on stderr and ignored, never silently enforced.
- Reading the file needs a TOML parser — built in on Python 3.11+, the `tomli` backport from the `[audit]` extra on 3.9–3.10. Without one, a reachable `pyproject.toml` triggers a stderr advisory instead of being silently skipped.
- `pydeprecate all` applies the same resolved settings to its single scan and its advisory policy pass. The Python API (`validate_deprecation_policy()`) takes a module (object or importable name), never a filesystem path, and does not read `pyproject.toml` — pass its keyword arguments (including `exclude`) explicitly.

`--version` auto-detect: when the scanned argument is an existing *path*, `_read_pyproject_version` searches the current directory and up to 2 parent directories for a `pyproject.toml` (current dir + 2 levels up); the nearest one found wins, falling back to installed package metadata. A bare module *name* skips the `pyproject.toml` lookup entirely, so it can never pick up an unrelated project's version from your current working directory.

## Exit codes

| Subcommand | Exit `0`                                                                 | Exit `1`                                                              | Exit `2`                                                                      |
| ---------- | ------------------------------------------------------------------------ | --------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `check`    | Clean or advisory warnings only (chains / identity / no-effect)          | Invalid argument mappings found                                       | Malformed `--exclude`                                                         |
| `expiry`   | No expired wrappers; or `packaging` not installed (skipped with warning) | Expired wrappers found (and `--exit-zero` not set)                    | Malformed `--version` when `packaging` is available, or malformed `--exclude` |
| `policy`   | No violations; or only a skipped `min-grace` rule                        | Policy violations found (and `--exit-zero` not set)                   | Malformed rule value or `--exclude` (flag or `pyproject.toml`)                |
| `chains`   | No chains                                                                | Deprecated-to-deprecated chains found                                 | Malformed `--exclude`                                                         |
| `all`      | All checks clean, or only policy violations (table always appended)      | Any hard error above (`packaging` missing → skips expiry, no failure) | Malformed `--version` when `packaging` is available, or malformed `--exclude` |
| `status`   | Always — status table is not a pass/fail gate                            | —                                                                     | Malformed `--exclude`                                                         |

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
        run: pydeprecate policy src/mypackage --min-grace=0.3 --message-required=True
```

Spell the rule flags out even where they match the defaults, as above, or declare them once in `[tool.pydeprecate.policy]` and run a bare `pydeprecate policy src/mypackage`: either way the project carries a written record of what it promises, and a later change to pyDeprecate's built-in defaults cannot quietly change what your CI enforces.

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
