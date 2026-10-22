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

`tests/` contains pyDeprecate's own deprecation fixtures. Every subcommand scans a path the same way — a package directory, a plain directory of top-level modules, or an importable module name (see [Path formats](#path-formats)).

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

    # auto-detect version (pyproject.toml, installed metadata, then package __version__)
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

    # also save the appended table
    pydeprecate all path/to/your/package --output DEPRECATIONS.md
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

| Flag                  | Default          | `check` | `expiry` | `policy` | `chains` | `all` | `status` | Effect                                                                                                                                                                                | Note                                                                                                                                                                     |
| --------------------- | ---------------- | :-----: | :------: | :------: | :------: | :---: | :------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--version VERSION`   | auto-detected    |         |    ✓     |          |          |   ✓   |    ✓     | Package version for deadline comparison. Auto-detected if omitted (see below).                                                                                                        | `policy` compares `deprecated_in` to `remove_in` only, so it takes no version.                                                                                           |
| `--norecursive`       | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |    ✓     | Scan top-level module only; skip submodules.                                                                                                                                          | Fire auto-generates this from `recursive=False` — the flag is `--norecursive`, not `--no-recursive`.                                                                     |
| `--noinclude-members` | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |    ✓     | Leave deprecated class members (methods, constructors, descriptors) out of the scan; only module-level wrappers are checked.                                                          | Members are scanned by default, as by every audit function (`include_members=True`).                                                                                     |
| `--exit-zero`         | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |          | Always exit `0` even when hard errors are found — useful for advisory CI steps that should never block.                                                                               |                                                                                                                                                                          |
| `--exclude`           | `pyproject.toml` |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |    ✓     | Glob patterns over full dotted module names to leave out of the scan; the scan itself never imports a matching package nor descends into it. One pattern, comma-separated, or a list. | Default is the `exclude` list of `[tool.pydeprecate]` (see below), else nothing. Quote the value — an unquoted `*` is expanded by the shell first. Malformed → exit `2`. |
| `--style`             | `compact`        |         |          |          |          |       |    ✓     | Table rendering style — `compact` (default) or `matrix`.                                                                                                                              |                                                                                                                                                                          |
| `--output FILE`       | stdout only      |         |          |          |          |   ✓   |    ✓     | Also save the markdown table to a file. Table is always printed to stdout regardless.                                                                                                 | For `all`, a failure to write the file is reported but never changes the exit code.                                                                                      |
| `--report-json FILE`  | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |    ✓     | Write the run's machine-readable record for CI tooling: the deprecation table of this run's scan and every gap (see below).                                                           | Written on exit `0` or `1` only; a write failure is reported on stderr and never changes the exit code.                                                                  |

A bare `pydeprecate policy src/mypackage` therefore runs a recursive scan, exits `1` on violations, and applies both default-on rules below (`--min-grace=0.3`, `--message-required=True`) — unless the project's `pyproject.toml` says otherwise (see [Project configuration in `pyproject.toml`](#project-configuration-in-pyprojecttoml)).

Flags and the path may come in any order: `pydeprecate check --exit-zero src/mypackage` scans `src/mypackage`, which is the order a [pre-commit hook](audit.md#pre-commit-integration) produces from its `args`. Boolean flags accept `true`, `false`, `1` or `0` (case-insensitive) as `--flag value` or `--flag=value`, and the negated form attaches directly: `--norecursive`, not `--no-recursive`. Each run takes one path. For `check`, `policy` and `chains`, a second positional path exits `2` with "Pass one path per run". A short flag such as `-r src/mypackage`, or a boolean value other than `true`/`false`/`1`/`0`, exits `2` with a message that the flag expects True or False (use long flags and an explicit `--flag=True|False`) rather than scanning something you did not name. For `expiry`, `all` and `status`, a second positional argument binds `--version` instead. Private `--_*` options (for example `--_advisory`) are not part of the CLI and exit `2`.

A run can pass without auditing everything. Each such gap is reported as one unwrapped stderr line starting with `pydeprecate gap: <kind>:` — `module not audited` (one line per submodule that failed to import, printed whatever warnings filters the audited package installs, plus one `<package>.*` line when a recursive walk stopped early and left the remaining submodules unaudited), `version not resolved`, `version not PEP 440`, `remove_in not PEP 440` (a wrapper's own `remove_in` is not a valid PEP 440 version, so that one removal deadline was skipped; one line per wrapper, exit code unchanged), `packaging missing`, `nested files not audited` or `other copy audited` (the scanned package's import name was already taken by a copy from another directory — the CLI's own `deprecate`, or an editable install of another checkout — so that copy was audited; run the scan in the checkout's own environment) — so they stay visible in a CI log.

For tooling, `--report-json FILE` records the same run as JSON — every subcommand takes it:

```json
{
  "schema": 1,
  "table": "| API | ... |",
  "gaps": [
    {
      "kind": "module not audited",
      "detail": "mypackage.extra: ImportError(...)",
      "hint": "install the scanned package's runtime dependencies in the environment that runs the scan"
    }
  ]
}
```

`table` is the markdown deprecation table of the scan that ran (`null` when it could not be rendered), and each gap carries one of the kinds above, its detail and a remedy. The file is written only when the run exits `0` or `1`: a usage error (exit `2`) scanned nothing and a crash writes no file, so a consumer never publishes stale results. The [GitHub Action](audit.md#github-action) builds its gap annotations and job summary from this file.

There is intentionally no `--include-pep702` flag: every subcommand evaluates pyDeprecate schedule metadata (`deprecated_in`, `remove_in`, a replacement) that symbols deprecated only with `warnings.deprecated` lack. List those with the Python API — `find_deprecation_wrappers(..., include_pep702=True)`, see [PEP 702-only deprecations](audit.md#pep-702-only-deprecations).

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

`--version` auto-detect: every subcommand (and the JSON report table) uses the same resolver. When the scanned argument is an existing *path*, the scanned path's directory and up to 2 parent directories are searched for a `pyproject.toml`; the nearest file that yields a version wins. If none does, or the argument is a bare module name, installed metadata is tried under the full import name, then the top-level import name, then the distribution that ships that top-level package (`PIL` → `Pillow`). The full name takes priority because a dotted component can have a separately versioned distribution. Only after these lookups fail does the resolver read the top-level import package's string `__version__`. A bare module *name* skips the `pyproject.toml` lookup, so it cannot pick up an unrelated project's version from your current working directory. The Python API (`validate_deprecation_expiry()`, `generate_deprecation_table()`) resolves the module's top-level package through this chain without the `pyproject.toml` step, as it receives a module, never a path.

Only a genuinely unresolved version skips the expiry check with a `version not resolved` gap and exit `0`; pass `--version` to supply it explicitly.

## Exit codes

| Subcommand | Exit `0`                                                                 | Exit `1`                                                              | Exit `2`                                                                      |
| ---------- | ------------------------------------------------------------------------ | --------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `check`    | Clean or advisory warnings only (chains / identity / no-effect)          | Invalid argument mappings found                                       | Malformed `--exclude`                                                         |
| `expiry`   | No expired wrappers; or `packaging` not installed (skipped with warning) | Expired wrappers found (and `--exit-zero` not set)                    | Malformed `--version` when `packaging` is available, or malformed `--exclude` |
| `policy`   | No violations; or only a skipped `min-grace` rule                        | Policy violations found (and `--exit-zero` not set)                   | Malformed rule value or `--exclude` (flag or `pyproject.toml`)                |
| `chains`   | No chains                                                                | Deprecated-to-deprecated chains found                                 | Malformed `--exclude`                                                         |
| `all`      | All checks clean, or only policy violations (table always appended)      | Any hard error above (`packaging` missing → skips expiry, no failure) | Malformed `--version` when `packaging` is available, or malformed `--exclude` |
| `status`   | Table rendered — status is not a pass/fail gate                          | —                                                                     | Malformed `--exclude`                                                         |

Every subcommand exits `2` with `contains multiple packages (…)` when the scanned directory holds two or more top-level packages, and with `File paths are not supported` for a single `.py` file — both are usage errors, so `--exit-zero` never masks them; pass the package sub-directory instead. Every subcommand exits `2` for an internal `--_*` option. `check`, `policy` and `chains` also exit `2` for a boolean flag given a value other than `true`/`false`/`1`/`0` and for a second positional path; on `expiry`, `all` and `status` such a stray token binds `--version` instead.

Policy violations are the one finding `all` reports without acting on: they never move `all` from `0` to `1`. Give `policy` its own CI step when you want the build to fail on them.

Unknown or misspelled flags are never silently ignored: any unconsumed argument (e.g. `pydeprecate expiry mypackage --verison 2.0`) exits `2` with a `Could not consume arg: --verison` diagnostic from the underlying argument parser.

## CI recipes

Two gates, two steps: `pydeprecate all` fails the build on misconfigured wrappers, chains and expired deprecations, and a separate `pydeprecate policy` step fails it when a *new* deprecation is scheduled in a way the project does not allow — keeping them separate means a failure tells you which problem you have. The full workflow, with the rules declared once in `[tool.pydeprecate.policy]`, is in the [audit guide](audit.md#enforcing-the-policy-in-ci).

While you are bringing an existing codebase into line, run the gate advisory-first so it reports without blocking, then drop the flag once the backlog is clear:

```bash
pydeprecate policy src/mypackage --exit-zero
```

On GitHub Actions the CLI also ships as a ready-made step, `uses: Borda/pyDeprecate@<tag>` with `subcommand`, `path`, `args`, `exit-zero`, `install` and `summary` inputs; the step exits with the CLI's own code. See the [GitHub Action](audit.md#github-action) section of the audit guide.

## Path formats

All subcommands accept:

- **Package directory** — path to a directory with `__init__.py` (e.g. `src/mypackage`)
- **Importable module name** — dotted module path (e.g. `mypackage.utils`)
- **Plain directory** — scans top-level `.py` files only (nested files are reported as a `nested files not audited` gap)

A single `.py` file, or a directory holding two or more top-level packages, is a usage error (exit `2`) for every subcommand. While scanning, the imported code sees no pydeprecate arguments in `sys.argv`, and the header line `Imported from:` names the file the package was actually imported from.

## Python module invocation

Both `pydeprecate` and `python -m deprecate` are equivalent entry points:

```bash
python -m deprecate check src/mypackage
python -m deprecate expiry src/mypackage --version 2.0.0
```

Useful in environments where the `pydeprecate` script is not on `PATH` (e.g. inside a Docker image where only `python` is available).
