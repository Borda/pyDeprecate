---
id: cli
description: 'Command-line interface reference for pyDeprecate: scan deprecated wrappers, enforce removal deadlines, lint deprecations against a governance policy, detect chains, and run all checks in one pass.'
---

# CLI Reference

The `pydeprecate` CLI runs all four [audit checks](audit.md) directly from the command line, without writing a Python script. These checks cover wrapper configuration, expiry enforcement, policy linting, and chain detection.

## Installation

```bash
pip install 'pyDeprecate[audit,cli]'
```

Start with `[audit,cli]` unless you are certain you will never use `expiry`, `policy`, or `all`. The `[audit]` extra pulls in `packaging`.

| Check                                         | When it needs `packaging`                                                                                                                                                 |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `expiry`                                      | It always requires `packaging`.                                                                                                                                           |
| `policy`, including the policy phase of `all` | It needs `packaging` only when a wrapper's `deprecated_in`/`remove_in` is actually compared against a version. A policy scan with no version fields set never imports it. |

## Quick start

```bash
pydeprecate all src/mypackage   # run all checks + deprecation table in one pass
pydeprecate check path/to/your/package   # validate wrapper config
pydeprecate check mypackage.submodule    # importable module name also accepted
pydeprecate policy src/mypackage         # gate on your deprecation-governance rules
pydeprecate all --gate=policy src/pkg_a src/pkg_b   # several packages, policy gated too, one scan each (since v0.16)
```

**Quick demo** using pyDeprecate's own test fixtures (no package setup needed):

```bash
# Run all checks + deprecation table on the bundled test fixtures
pydeprecate all tests --version 1.2
# Standalone deprecation status table only
pydeprecate status tests --version 1.2
```

`tests/` contains pyDeprecate's own deprecation fixtures. Every subcommand scans a path the same way. It accepts a package directory, a plain directory of top-level modules, or an importable module name; see [Path formats](#path-formats).

## Subcommands

=== "check"

    This subcommand validates wrapper configuration: invalid `args_mapping` keys, identity mappings, no-effect wrappers, and deprecated-to-deprecated chains. It is backed by [`find_deprecation_wrappers()`](audit.md#validating-wrapper-configuration).

    ```bash
    pydeprecate check path/to/your/package
    pydeprecate check mypackage.submodule
    ```

    | Finding                                                                    | Exit code                                                                                                                                                                          |
    | -------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
    | Invalid argument mappings                                                  | The command exits 1.                                                                                                                                                               |
    | Misconfigured targets                                                      | The command exits 1 for `target=False`, `TargetMode.NOTIFY` with an ignored `args_mapping`, `attrs_mapping` or `args_extra`, or `TargetMode.ARGS_REMAP` without an `args_mapping`. |
    | Inner-order `@property @deprecated` wrappers                               | The command exits 1; only the getter warns, leaving the setter and deleter unprotected.                                                                                            |
    | Chains, identity mappings, no-effect wrappers and positional-only mappings | These are reported as warnings; the command exits 0.                                                                                                                               |

    !!! note "Chain handling differs in `all`"

        `check` treats chains as advisory warnings (exit 0). `all` treats chains as hard errors (exit 1), because `all` ≡ `check + expiry + policy + chains`. The `chains` check exits 1 on any chain found.

        `policy` remains advisory inside `all` unless you pass `--gate=policy`.

=== "expiry"

    This subcommand checks whether any deprecated wrappers have passed their `remove_in` deadline using [`validate_deprecation_expiry()`](audit-policy.md#enforcing-removal-deadlines). It requires the `[audit]` extra, included in `[audit,cli]`.

    ```bash
    # explicit version
    pydeprecate expiry path/to/your/package --version 2.0.0

    # auto-detect version (pyproject.toml, installed metadata, then package __version__)
    pydeprecate expiry path/to/your/package
    ```

    The command exits 1 if any wrapper is past its removal deadline. Use `--exit-zero` to suppress exit 1 when expired wrappers are found.

    !!! warning "Missing `packaging` skips expiry"

        If `packaging` is not installed, the check is skipped with a warning. It exits 0, or exit 1 under strict mode.

=== "policy"

    This subcommand checks whether each deprecation was *scheduled* responsibly, using [`validate_deprecation_policy()`](audit-policy.md#enforcing-a-deprecation-policy). Every violation message is prefixed with the slug of the rule it broke. `policy` never needs a package version, so it takes no `--version` flag.

    | Rule               | Default         | Dependency                                                  |
    | ------------------ | --------------- | ----------------------------------------------------------- |
    | `min-grace`        | The rule is on. | It requires the `[audit]` extra, included in `[audit,cli]`. |
    | `message-required` | The rule is on. | It works without the extra.                                 |

    ```bash
    # default policy: three-minor grace window on a clean release boundary, guidance required
    pydeprecate policy path/to/your/package

    # tune the window to your own release convention — here: removals only at a major
    pydeprecate policy path/to/your/package --min-grace=1.0

    # switch individual rules off
    pydeprecate policy path/to/your/package --min-grace=None --message-required=False
    ```

    | Outcome                                               | Exit code                                                   |
    | ----------------------------------------------------- | ----------------------------------------------------------- |
    | Any enabled rule finds a violation                    | The command exits 1.                                        |
    | `--min-grace` is malformed                            | The command exits 2.                                        |
    | The nearest `pyproject.toml` cannot be read or parsed | The command exits 2 and prints `Cannot read <path>: <err>`. |
    | The scan is clean, or `--exit-zero` is passed         | The command exits 0.                                        |

    !!! warning "Missing `packaging` skips only `min-grace`"

        If `packaging` is not installed, `min-grace` is skipped with a warning. `message-required` still runs and can return exit 1.

    !!! warning "`all` reports policy violations without failing on them, unless `--gate=policy`"

        The policy defaults encode *a* project convention: a three-minor grace window on a clean release boundary. Not every repository shares that convention, so `pydeprecate all` runs this check in **advisory** mode by default. Violations are printed but never change `all`'s exit code.

        Inside `all`, the violation table renders as `[WARNING]` (yellow). Its trailer notes "advisory here — run `pydeprecate policy` to gate on it".

        To gate CI on the policy, pass `--gate=policy` to `all` (since v0.16), or set `gate = ["policy"]` in `[tool.pydeprecate]`. Violations then render as `[ERROR]` without the trailer and fail the run, from the same single scan. The dedicated `pydeprecate policy` subcommand gates on them too.

        A config-read failure is the one exception to "advisory". An unreadable or malformed `pyproject.toml` still exits `2` inside `all` too, before the chains check ever runs; see below.

=== "chains"

    This subcommand detects two kinds of chains using [`validate_deprecation_chains()`](audit.md#detecting-deprecation-chains):

    - `ChainType.TARGET` identifies wrappers whose `target` is itself a deprecated callable.
    - `ChainType.STACKED` identifies stacked argument mappings that should be collapsed.

    ```bash
    pydeprecate chains path/to/your/package
    ```

    The command exits 1 if any chains are found.

=== "all"

    This subcommand runs all four checks in a single scan pass, then appends a compact markdown deprecation table:

    - [`find_deprecation_wrappers()`](audit.md#validating-wrapper-configuration) validates wrapper configuration.
    - [`validate_deprecation_expiry()`](audit-policy.md#enforcing-removal-deadlines) enforces removal deadlines.
    - [`validate_deprecation_policy()`](audit-policy.md#enforcing-a-deprecation-policy) checks policy.
    - [`validate_deprecation_chains()`](audit.md#detecting-deprecation-chains) detects chains.

    !!! warning "Missing `packaging` leaves some checks running"

        If `packaging` is not installed, expiry and policy's `min-grace` rule are skipped with a warning. `message-required` still runs, and the other checks continue.

    ```bash
    # explicit version
    pydeprecate all path/to/your/package --version 2.0.0

    # auto-detect version
    pydeprecate all path/to/your/package

    # also save the appended table
    pydeprecate all path/to/your/package --output DEPRECATIONS.md

    # also gate on the policy, from the same scan (since v0.16)
    pydeprecate all path/to/your/package --gate=policy
    ```

    The command exits 1 if any hard error is found. Hard errors include invalid argument mappings, misconfigured targets, inner-order properties, deprecated-to-deprecated chains, or expired wrappers. The deprecation table is always appended regardless of pass/fail outcome.

    !!! note "Policy gating"

        **Policy violations are advisory here by default**, because the defaults encode a project convention that not every repository shares. They render as `[WARNING]` with a trailer noting the check is advisory and naming `pydeprecate policy` as the gate. They never contribute to the exit code.

        `--gate=policy` (since v0.16) makes them errors that fail `all` exactly as `pydeprecate policy` would. `--gate` takes a comma-separated list of `check`, `expiry`, `policy` and `chains`, and only adds checks. `check`, `expiry` and `chains` always gate, so today only `policy` changes anything.

    !!! warning "Configuration failures and missing dependencies"

        Advisory treatment covers violation *counts* only. A policy **configuration** failure still exits `2`, printing `Cannot read <path>: <err>`, before the chains check ever runs. This includes an unreadable or unparsable nearest `pyproject.toml`, or a malformed `[tool.pydeprecate.policy]` value.

        | Dependency state                                             | Behaviour                                                                                                                                                                                                                                                                                          |
        | ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
        | `packaging` is missing                                       | Expiry is skipped and policy skips `min-grace`, but its packaging-free `message-required` rule still runs. Without `--gate=policy`, that rule is advisory, so neither affects `all`'s exit `1`. With `--gate=policy`, a `message-required` violation still exits `1` unless `--exit-zero` applies. |
        | `packaging` is missing and explicit `--version` is malformed | The malformed version is also advisory.                                                                                                                                                                                                                                                            |
        | `packaging` is available                                     | An invalid explicit `--version` exits `2`.                                                                                                                                                                                                                                                         |

=== "status"

    This subcommand generates and prints a markdown deprecation table to stdout. It runs no checks and exits `0` on success; an invalid `--style` or `--version` exits `2`. Use this when you only want to render the deprecation status table without running any validation.

    ```bash
    pydeprecate status path/to/your/package
    pydeprecate status path/to/your/package --style matrix
    pydeprecate status path/to/your/package --version 2.0.0 --output DEPRECATIONS.md
    ```

    | Style                       | Layout                                                                                                                               |
    | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
    | `--style compact` (default) | It renders one row per symbol with a status column.                                                                                  |
    | `--style matrix`            | It renders one column per release with `D`, `R` and `D/R` lifecycle markers. `D/R` means deprecated and removed in the same release. |

    !!! note "Equivalent release spellings"

        With the `audit` extra installed, spellings of one release such as `1.0`, `v1.0` and `1.0.0` share a column. Without `packaging`, only spellings that differ by the `v` prefix merge.

    `--output FILE` writes the table to a file in addition to printing it to stdout.

## Flags

| Flag                   | Default          | `check` | `expiry` | `policy` | `chains` | `all` | `status` | Effect                                                                                                                                                                                                                                             | Note                                                                                                                                                                                                                                                                                                           |
| ---------------------- | ---------------- | :-----: | :------: | :------: | :------: | :---: | :------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--version VERSION`    | auto-detected    |         |    ✓     |          |          |   ✓   |    ✓     | Package version for deadline comparison, applied to every path. Auto-detected per path if omitted (see below).                                                                                                                                     | `policy` compares `deprecated_in` to `remove_in` only, so it takes no version.                                                                                                                                                                                                                                 |
| `--gate CHECKS`        | `pyproject.toml` |         |          |          |          |   ✓   |          | Checks `all` gates on beyond `check`, `expiry` and `chains`, comma-separated: `--gate=policy` makes policy violations errors that fail the run.                                                                                                    | Since v0.16. Default is `gate` of `[tool.pydeprecate]`, else none; `--gate=` switches the file's list off. Unknown check → exit `2`.                                                                                                                                                                           |
| `--no-recursive`       | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |    ✓     | Scan top-level module only; skip submodules.                                                                                                                                                                                                       | Also spelled `--recursive=false`.                                                                                                                                                                                                                                                                              |
| `--no-include-members` | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |    ✓     | Leave deprecated class members (methods, constructors, descriptors) out of the scan; only module-level wrappers are checked.                                                                                                                       | Members are scanned by default, as by every audit function (`include_members=True`). Also spelled `--include-members=false`.                                                                                                                                                                                   |
| `--exit-zero`          | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |          | Exit `0` instead of `1` when hard errors are found — useful for advisory CI steps; usage errors (`2`) and scan or internal errors (`3`) still fail.                                                                                                |                                                                                                                                                                                                                                                                                                                |
| `--strict`             | `pyproject.toml` |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |          | Exit `1` when any part of the audit did not run, even with no finding. This means any `pydeprecate gap:` line, such as an unimportable module or an unresolved version.                                                                            | Since v0.16. The default is `strict` of `[tool.pydeprecate]`, else off; `--no-strict` switches it off for one run. A typed `--strict` together with `--exit-zero` → exit `2`. A typed `--exit-zero` overrides a `strict = true` that only `pyproject.toml` sets; one stderr note states the run is not strict. |
| `--ignore PATTERNS`    | `pyproject.toml` |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |          | Accept known findings without fixing them: entries `module:symbol` (every rule) or `rule:module:symbol` (one rule, or `*`), each part an `fnmatch` glob; comma-separated and repeatable. Suppressed findings are not printed, counted or gated on. | Since v0.16. Default is `ignore` of `[tool.pydeprecate]`, else nothing; `--ignore=` switches the file's list off. Malformed entry → exit `2`.                                                                                                                                                                  |
| `--baseline FILE`      | `pyproject.toml` |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |          | Accept every finding listed in an earlier `--report-json` file (pyDeprecate v0.16 or later), matched by finding id.                                                                                                                                | Since v0.16. Default is `baseline` of `[tool.pydeprecate]`, relative to that file; `--baseline=` switches it off. A file that is missing or unreadable, or is not a report with a `findings` list → exit `2`.                                                                                                  |
| `--exclude`            | `pyproject.toml` |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |    ✓     | Glob patterns over full dotted module names to leave out of the scan; the scan itself never imports a matching package nor descends into it. One pattern, or comma-separated.                                                                      | Default is the `exclude` list of `[tool.pydeprecate]` (see below), else nothing. Quote the value — an unquoted `*` is expanded by the shell first. Malformed → exit `2`.                                                                                                                                       |
| `--style`              | `compact`        |         |          |          |          |       |    ✓     | Table rendering style — `compact` (default) or `matrix`.                                                                                                                                                                                           |                                                                                                                                                                                                                                                                                                                |
| `--output FILE`        | stdout only      |         |          |          |          |   ✓   |    ✓     | Also save the markdown table to a file. Table is always printed to stdout regardless.                                                                                                                                                              | For `all`, a failure to write the file is reported but never changes the exit code.                                                                                                                                                                                                                            |
| `--report-json FILE`   | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |    ✓     | Write the run's machine-readable record for CI tooling: the deprecation table of this run's scan, every gap and every finding (see below).                                                                                                         | Written on exit `0` or `1` only; a write failure is reported on stderr and never changes the exit code.                                                                                                                                                                                                        |
| `--report-sarif FILE`  | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |          | Write the run's active findings as a SARIF 2.1.0 file for code-scanning tools (see below).                                                                                                                                                         | Since v0.16. Written on exit `0` or `1` only; a write failure is reported on stderr and never changes the exit code.                                                                                                                                                                                           |
| `--static`             | off              |         |    ✓     |    ✓     |          |       |          | Read the package's source with `ast` instead of importing it, for an environment without its runtime dependencies (see [Static mode](#static-mode)).                                                                                               | Nothing from the package is imported or run. A wrapper the source alone does not settle is a `not statically resolvable` gap, not a finding.                                                                                                                                                                   |

A bare `pydeprecate policy src/mypackage` runs a recursive scan and exits `1` on violations. It applies both default-on rules below: `--min-grace=0.3` and `--message-required=True`. The project's `pyproject.toml` can override these defaults; see [Project configuration in `pyproject.toml`](#project-configuration-in-pyprojecttoml).

Flags and the path may come in any order. `pydeprecate check --exit-zero src/mypackage` scans `src/mypackage`, using the order a [pre-commit hook](ci-integration.md#pre-commit-integration) produces from its `args`.

| Syntax               | Behaviour                                                                                                                                                       |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Boolean flags        | They accept `true`, `false`, `1` or `0`, case-insensitively, as `--flag value` or `--flag=value`.                                                               |
| Negated booleans     | Use `--no-flag`, as in `--no-recursive`. The older `--noflag` spelling (`--norecursive`) still works but is not listed in `--help`.                             |
| Option names         | Every option may use dashes or underscores (`--exit-zero`, `--exit_zero`), but never abbreviations.                                                             |
| Option values        | Values stay strings, so `--version 0.10` and `--min-grace=0.10` keep their trailing zero.                                                                       |
| Positional arguments | Every positional argument is a path; see [Several paths in one run](#several-paths-in-one-run).                                                                 |
| Invalid syntax       | A short flag such as `-r src/mypackage`, or a boolean value other than `true`/`false`/`1`/`0`, exits `2`. The command does not scan something you did not name. |
| Subcommand help      | `pydeprecate <subcommand> --help` lists the options of each subcommand.                                                                                         |

### Several paths in one run

Since v0.16 every subcommand takes several paths — `pydeprecate all src/pkg_a src/pkg_b` — and audits them in one run with one exit code:

1. Every path is checked before anything is imported.

2. Duplicate and recursively covered paths are combined when their settings and versions allow it.

3. The retained paths are scanned one after another, each with its own import root, settings, and auto-detected version.

4. The console prints one scan header per path, then each check's findings of all paths in one section. For `all` and `status`, it then prints one table per path under a `### <package>` heading.

5. One exit code covers all paths. Each path is judged with its own strict mode and `--gate` list; the run fails when any path fails.

#### Path validation

The following conditions exit `2` with nothing scanned:

- A path is a file or a directory of several packages.
- A name is neither an existing path nor a findable module.
- Any path's `pyproject.toml` contains a malformed setting.
- Two paths import as the same top-level name from different places. The diagnostic is `<a> and <b> both import as <name>; scan them in separate runs`.

A plain directory takes part in import-name checks with the bare name of each top-level file it scans. A file its `exclude` leaves out takes no part.

!!! note "Missing submodules are found later"

    Validation looks up only the top-level package without importing it. A missing submodule of an importable package (`pkg.nosuch`) therefore exits `2` when its path is scanned, after the paths before it. No report is written.

#### Duplicate and covered paths

| Paths                                                      | Scan behaviour                                                                                                                                                       |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The same path is given twice                               | It is scanned once, with one stderr note.                                                                                                                            |
| A package and a subpackage its recursive scan imports      | They are scanned once, with one stderr note. Every directory between them must hold an `__init__.py`, and the package's `exclude` must not leave the subpackage out. |
| Any other subpackage                                       | It keeps its own scan.                                                                                                                                               |
| A covered path finds a different `[tool.pydeprecate]` file | It exits `2`, since those settings would be dropped. Scan it in a separate run.                                                                                      |
| A covered path finds its own version during auto-detection | It exits `2`, since that version would be dropped. Scan it in a separate run.                                                                                        |
| A covered path finds neither its own settings nor version  | It takes the covering path's settings and version. For example, `src/pkg/sub` has only a repository-root `pyproject.toml`.                                           |

Version auto-detection applies to `expiry`, `all` and `status` without `--version`. A covered path's own version can come from a nearer `pyproject.toml` with a `[project]` table. For a dotted module name such as `pkg.component`, it can come from an installed `pkg-component` distribution.

#### Scan settings and workers

Each path takes its `[tool.pydeprecate]` settings from the `pyproject.toml` nearest to that path. These settings are `exclude`, `strict`, `ignore`, `baseline`, `gate` and the policy rules.

| Retained paths and mode         | Execution                                                                                                                                                                                                              |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| More than one path, import mode | Each scan runs in a fresh Python worker process. A shared dependency can therefore initialize once in each worker. Live parent-process monkeypatches, custom import finders and `sys.modules` state are not inherited. |
| One path, import mode           | The scan runs in the calling process.                                                                                                                                                                                  |
| `--static`                      | The scan reads source without importing it or starting these workers.                                                                                                                                                  |

A typed `--version` applies to every path, and the run says so before the first scan header. Flags apply to every path.

!!! note "Run-wide exit handling"

    `--exit-zero` applies to the whole run. It conflicts with a typed `--strict`, but overrides strict mode configured in any path’s `pyproject.toml`.

The report holds every path in `paths`, `versions`, and each finding's and gap's `path`. The SARIF file lists them in `run.properties.paths` and gives each result its `path`.

!!! warning "Legacy positional versions"

    With exactly two positionals, `expiry`, `all` and `status` can still read the second one as the version. It must name no existing path or findable module and parse as a version, as in `pydeprecate expiry src/mypackage 2.0`.

    This form is deprecated and stops working in v1.0. The run prints a notice on stderr asking for `--version` instead.

    A second positional that is not a version is a second path. A mistyped one is therefore reported as `No such path or importable module`.

    Operands after a `--` separator are always paths. `pydeprecate expiry -- src/mypackage 2.0` scans `2.0` as a second path instead of reading it as the version. Pass `--version 2.0` there.

#### Audit gaps

!!! warning "A passing run can have gaps"

    A run can pass without auditing everything. A gap leaves the exit code alone unless strict mode is on; see [Exit codes](#exit-codes).

Each gap is reported as one unwrapped stderr line starting with `pydeprecate gap: <kind>:`. These lines stay visible in a CI log.

| Kind                        | Meaning or reporting detail                                                                                                                                                                                                                      |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `module not audited`        | One line is printed per submodule that failed to import, whatever warnings filters the audited package installs. One additional `<package>.*` line appears when a recursive walk stopped early and left remaining submodules unaudited.          |
| `version not resolved`      | The version was not resolved.                                                                                                                                                                                                                    |
| `version not PEP 440`       | The version is not PEP 440.                                                                                                                                                                                                                      |
| `remove_in not PEP 440`     | A wrapper's own `remove_in` is not a valid PEP 440 version. Its removal deadline (`expiry`) or grace window (`policy`'s `min-grace`) was skipped. One line is printed per wrapper; `all` names each wrapper once.                                |
| `deprecated_in not PEP 440` | A wrapper's own `deprecated_in` is not a valid PEP 440 version, so `policy`'s `min-grace` skipped its grace window. One line is printed per wrapper.                                                                                             |
| `epoch change`              | A forward change of PEP 440 epoch makes the `min-grace` window unmeasurable. One line is printed per wrapper.                                                                                                                                    |
| `packaging missing`         | `packaging` is missing.                                                                                                                                                                                                                          |
| `nested files not audited`  | Nested files were not audited.                                                                                                                                                                                                                   |
| `other copy audited`        | The scanned package's import name was already taken by a copy from another directory, so that copy was audited. It may be the CLI's own `deprecate`, or an editable install of another checkout. Run the scan in the checkout's own environment. |
| `not statically resolvable` | A `--static` run could not read a wrapper, or a setting one of its rules needs, from the source alone. See [Static mode](#static-mode).                                                                                                          |

#### JSON report

For tooling, `--report-json FILE` records the same run as JSON — every subcommand takes it. The file has layout version (`schema`) 1; since v0.16 it also holds one record per finding:

```json
{
  "schema": 1,
  "tool": { "name": "pyDeprecate", "version": "0.16.0" },
  "command": "expiry",
  "mode": "import",
  "paths": ["src/mypackage"],
  "versions": [{ "path": "src/mypackage", "package": "mypackage", "value": "2.0", "source": "user-provided" }],
  "options": { "strict": false, "exit_zero": false, "gates": ["expiry"], "recursive": true, "exclude": [] },
  "gate": { "failed": true, "reasons": ["findings"] },
  "exit_code": 1,
  "table": "| API | ... |",
  "gaps": [
    {
      "kind": "module not audited",
      "detail": "mypackage.extra: ImportError(...)",
      "hint": "install the scanned package's runtime dependencies in the environment that runs the scan",
      "path": "src/mypackage"
    }
  ],
  "findings": [
    {
      "id": "expired:mypackage:old_fn",
      "rule": "expired",
      "check": "expiry",
      "level": "error",
      "message": "Callable `mypackage.old_fn` was scheduled for removal in version 1.5 but still exists in version 2.0. ...",
      "details": [],
      "subject": { "module": "mypackage", "symbol": "old_fn", "api_type": "callable", "label": "Callable `mypackage.old_fn`" },
      "deprecated_in": "1.0",
      "remove_in": "1.5",
      "location": { "file": "src/mypackage/__init__.py", "line": 8 },
      "path": "src/mypackage"
    }
  ],
  "suppressed": [],
  "summary": { "findings": 1, "errors": 1, "warnings": 0, "notes": 0, "gaps": 1, "suppressed": 0, "baseline_unmatched": 0, "ignore_unmatched": 0 }
}
```

| Field                  | Meaning                                                                                                                                                                                                                                |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `mode`                 | It is `static` for a `--static` run and `import` otherwise.                                                                                                                                                                            |
| `table`                | It is the markdown deprecation table of the scan that ran, or `null` when it could not be rendered. Several paths join one table per path, each under a `### <package>` heading. A note replaces any table that could not be rendered. |
| Each gap               | It carries one of the kinds above, its detail, a remedy and the scan path it came from.                                                                                                                                                |
| `paths` and `versions` | They list every scanned path.                                                                                                                                                                                                          |
| `options`              | It summarises the run. `strict` is true when any path is strict; `gates` and `exclude` are the union over the paths.                                                                                                                   |
| `findings`             | It lists every finding the subcommand evaluates, once per id at its level in this run, sorted by level and then id. `error` fails the gate; `warning` is reported only.                                                                |
| `message`              | It is the finding's full description.                                                                                                                                                                                                  |
| `location`             | It identifies where the wrapper is defined. The POSIX path is relative to the repository root when the file is under it, else absolute. This is the same root as the SARIF locations below.                                            |
| Location line          | The line is that of the wrapper's first decorator.                                                                                                                                                                                     |
| Null locations         | `line` is `null` for a deprecated module. `location` is `null` for a `deprecated_instance` data object or a class built at runtime.                                                                                                    |
| Static locations       | A `--static` run places a wrapper as an import run does when the source shows its definition. A wrapper of an object defined in another module or bound more than once has no location there.                                          |
| `gate`                 | It says whether the gate failed before `--exit-zero` and why: `findings`, and `gaps` under `--strict`.                                                                                                                                 |
| `exit_code`            | It is the process exit code.                                                                                                                                                                                                           |

The `id` is `<rule>:<module>:<symbol>`. A deprecated module has an empty symbol: `expired:mypackage.oldmod:`. The id holds no version, line, message or path.

It survives moved code, a version bump and reworded messages. It changes only when the symbol, its module or the rule is renamed.

| Naming case                                                                                                              | Effect on the id                                                                                                  |
| ------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| A wrapper has several public names                                                                                       | It is reported under the one named like the wrapper (`__name__`), so adding an alias does not rename its id.      |
| Its `__name__` is private (`old = deprecated(...)(_impl)`), or it is a proxy (`deprecated_class`, `deprecated_instance`) | It is reported under the first public name in alphabetical order. A new alias that sorts earlier would rename it. |
| A subpackage is scanned instead of its package with `--no-recursive`                                                     | A re-export can be reported under another module.                                                                 |

The rule slugs are `invalid-args`, `misconfigured-target`, `inner-order-property`, `identity-mapping`, `no-effect`, `positional-only-mapping`, `chain`, `expired`, `min-grace` and `message-required`.

The layout is described by the JSON Schema [`docs/schemas/report-1.schema.json`](../schemas/report-1.schema.json). `schema` is bumped only when a key is removed or renamed, or its meaning or type changes. Adding a key does not bump it, so readers must ignore keys they do not know.

The keys added in v0.16 (per-finding records, `gate`, `mode`, …) did not bump it. Every key a v0.15 report has (`schema`, `table`, `gaps[].kind`/`detail`/`hint`) keeps its type and meaning. A v0.15 reader therefore still reads a v0.16 report.

!!! note "Report publication"

    The file is written only when the run exits `0` or `1`. A usage error (exit `2`) or crash (exit `3`) leaves any existing report unchanged. Consumers must check the exit code before reading or publishing a report.

The [GitHub Action](ci-integration.md#github-action) builds its gap annotations and job summary from this file.

### SARIF for code scanning

[SARIF](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html) (Static Analysis Results Interchange Format) is the OASIS standard JSON format in which static-analysis tools report findings. Each finding includes a rule, a level, a message, a file and line, and an identifying fingerprint.

GitHub code scanning reads it to list each finding as an alert. It uses the fingerprint to recognise a finding it has already seen, avoiding duplicate alerts when code moves. See [GitHub's SARIF support](https://docs.github.com/en/code-security/reference/code-scanning/sarif-files/sarif-support).

For pyDeprecate this turns an `expired`, `min-grace` or `chain` finding into an alert on the wrapper's decorator line, with no custom CI glue. The same file opens in any other SARIF viewer. When all you need is a pass or fail gate, skip it: the exit code and `--report-json` cover that.

For code scanning, `--report-sarif FILE` (since v0.16; every subcommand but `status`) writes the same run as one SARIF 2.1.0 run. The file is written only on exit `0` or `1`.

| SARIF content                  | Detail                                                                                                                                                                                                                           |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Results                        | There is one result per active finding with its level in the run, its message and its stable id. The id is the `pydeprecateFindingId/v1` partial fingerprint.                                                                    |
| Rules                          | There is one rule per check rule the subcommand evaluates.                                                                                                                                                                       |
| Locations                      | A result uses a path relative to the repository root under the `%SRCROOT%` base. It uses the line of the wrapper's first decorator, or line 1 for a deprecated module.                                                           |
| Files outside the root         | They use an absolute `file:` URI and are counted in a stderr advisory. Code scanning cannot match them to a repository file.                                                                                                     |
| Findings without a source file | A finding such as a `deprecated_instance` data object is kept without a location and counted in `run.properties.unlocated`. GitHub code scanning does not display it. A run whose results all lack a location says so on stderr. |
| Suppressed findings            | They are left out and counted in `run.properties.suppressed`.                                                                                                                                                                    |
| Gaps                           | They are tool-execution notifications.                                                                                                                                                                                           |
| `run.properties.mode`          | This is the report's `mode`: `static` for a `--static` run, else `import`.                                                                                                                                                       |

The repository root is resolved in this order:

1. Use the nearest directory at or above the working directory that holds `.git`.
2. Otherwise use `GITHUB_WORKSPACE` when the working directory lies inside it.
3. Otherwise use the working directory.

The CLI can therefore run from any directory of the checkout. Scan the package in the checkout rather than an installed copy.

!!! note "PEP 702-only symbols"

    There is intentionally no `--include-pep702` flag. Every subcommand evaluates pyDeprecate schedule metadata (`deprecated_in`, `remove_in`, a replacement) that symbols deprecated only with `warnings.deprecated` lack. List those with the Python API: `find_deprecation_wrappers(..., include_pep702=True)`; see [PEP 702-only deprecations](audit.md#pep-702-only-deprecations).

### Policy rule flags

These two flags are specific to `policy`, one per rule. Switch them off with `--min-grace=None` and `--message-required=False`.

The *Default* column is the built-in value. A `[tool.pydeprecate.policy]` table in `pyproject.toml` replaces it, and a typed flag beats both.

| Flag                        | Default | Rule slug          | Effect                                                                                                                                        |
| --------------------------- | ------- | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `--min-grace=<delta>`       | `0.3`   | `min-grace`        | Minimum distance between `deprecated_in` and `remove_in` as a version-shaped delta: `1.0` one major, `0.3` three minors, `0.0.2` two patches. |
| `--message-required=<bool>` | `True`  | `message-required` | Require every wrapper to name a replacement (a `target`, a mapping, or a `message_template`).                                                 |

The delta has two or three components with at most one non-zero. A bare `1` is rejected as ambiguous (one *what*?), as is a mixed spelling such as `1.2`.

The removal must be one clean bump of a single component: `1.2` → `1.5` or `2.0`, never `2.3`. A coarser bump always clears a finer window: `0.3` is satisfied by one major bump.

| Delta or project                                              | Interpretation                                                                                                                                      |
| ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| An all-zero spelling such as `0.0`                            | This is a zero-count window with no minimum distance. A clean bump of that unit or coarser is still required.                                       |
| Ten or more steps                                             | No quoting is needed on the command line: `--min-grace=0.10` is ten minors.                                                                         |
| `--min-grace=1.0`, or `min-grace = "1.0"` in `pyproject.toml` | This is the strict "removals only at a major release" policy.                                                                                       |
| A `0.x` project                                               | No special setting is needed. A bump to `1.0` is a major step and clears any window. Removals inside the `0.x` line are counted in minors as usual. |

!!! warning "Malformed grace windows"

    A malformed `--min-grace` value is rejected before the scan starts. It exits `2` with a message naming the accepted spellings; it is never silently ignored.

### Project configuration in `pyproject.toml`

Declare the settings once, next to the code they govern. Every bare `pydeprecate` run, in a developer's shell or a CI step, applies them without repeating flags:

```toml
[tool.pydeprecate]
exclude = ["my_package.tests", "*._legacy*"]   # every subcommand: the scan skips these packages (never imports them itself)
strict = true                                  # every gate subcommand: a gap fails the run (`--no-strict` overrides)
ignore = ["my_package.legacy:old_api"]         # every gate subcommand: findings accepted without a fix
baseline = "ci/pydeprecate-baseline.json"      # every gate subcommand: findings of an earlier report, by id
gate = ["policy"]                              # `all` only (since v0.16): also fail on policy violations

[tool.pydeprecate.policy]                      # the `policy` subcommand's rules, keyed by rule slug
min-grace = "0.3"  # quoted dotted delta; false switches the rule off (TOML has no null)
message-required = true
```

Each setting resolves independently as **flag → `pyproject.toml` → built-in default**. Every run's header lines name the source of each value: `flag`, `pyproject.toml`, or `built-in`. A log therefore always shows what was applied:

```text
Exclude: my_package.tests, *._legacy* (pyproject.toml)
Policy: min-grace=0.3 (pyproject.toml)  message-required=True (built-in)
```

#### Configuration lookup

The table is looked up like `--version` auto-detection below. Search from the scanned *path*'s directory up to two parents. The nearest file declaring `[tool.pydeprecate]` wins.

There is one table per project: a nested file declaring it replaces the parent's whole table. A bare module *name* never triggers the lookup.

| Setting     | Accepted values and effect                                                                                                                                                                                                     |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `exclude`   | Patterns are `fnmatch` globs over the full dotted module name (`my_package.tests`, not `tests`). A pattern matching a package excludes its whole subtree. A string or comma-separated string is accepted in place of the list. |
| `min-grace` | It takes the dotted delta as a string (`"1.0"`, `"0.3"`, `"0.0.2"`), or a one-key unit table (`{ minor = 3 }`). Quote the delta (`"0.10"`, not `0.10`): a TOML float drops the trailing zero exactly as the shell does.        |

A wrapper is reported if and only if the module it is reported under does not match `exclude`. Even when something else in the package imports an excluded module, its wrappers stay out.

!!! warning "Configuration validation"

    A malformed value from the file exits `2`. The message names the `pyproject.toml` it came from.

    The following are rejected the same way:

    - `message-required` or `strict` is not a boolean.
    - An `exclude` or `ignore` entry is not a string.
    - An ignore entry is malformed.
    - A `baseline` file cannot be read or is not a report with a `findings` list.

    Unknown keys in either table are reported on stderr and ignored, never silently enforced. `min_grace` with an underscore is the usual typo.

!!! warning "A TOML parser is required"

    Reading the file needs a TOML parser. It is built in on Python 3.11+; on 3.9–3.10, use the `tomli` backport from the `[audit]` extra.

    Without a parser, a reachable `pyproject.toml` triggers a stderr advisory instead of being silently skipped. None of its `[tool.pydeprecate]` settings apply, including `strict = true`, so a gap no longer fails the run.

    On Python 3.9 or 3.10, install the `[audit]` extra to read `strict` from `pyproject.toml`. Otherwise pass `--strict` on the command line.

`pydeprecate all` applies the same resolved settings to its single scan and its advisory policy pass.

!!! note "Python policy API"

    The Python API (`validate_deprecation_policy()`) takes a module object or importable name, never a filesystem path. It does not read `pyproject.toml`; pass its keyword arguments, including `exclude`, explicitly.

#### Version auto-detection

Every subcommand and the JSON report table use the same `--version` auto-detection resolver:

1. For an existing *path*, search its directory and up to 2 parent directories for a `pyproject.toml`. The nearest file with a `[project]` table owns the version, even if its version cannot be resolved. Files without that table are skipped.
2. If the local version is unresolved or the argument is a bare module name, try installed metadata in the order below.
3. Only after these lookups fail, read the top-level import package's string `__version__`.

Installed metadata is tried under these names in order:

1. Try the full import name first.
2. Try the top-level import name next.
3. Try the distribution that ships that top-level package, such as `PIL` → `Pillow`.

The full name takes priority because a dotted component can have a separately versioned distribution.

!!! note "Lookup boundaries"

    An unresolved version does not fall through to an ancestor project. A bare module *name* skips the `pyproject.toml` lookup, so it cannot pick up an unrelated project's version from your current working directory.

    The Python API (`validate_deprecation_expiry()`, `generate_deprecation_table()`) resolves the module's top-level package through this chain without the `pyproject.toml` step. It receives a module, never a path.

Only a genuinely unresolved version skips the expiry check with a `version not resolved` gap and exit `0`, or exit `1` under `--strict`. Pass `--version` to supply it explicitly.

### Accepting known findings

Fix a finding when you can. When one must stay for now, accept it instead of excluding its whole module. For example, a removal might be waiting on a downstream release:

```bash
pydeprecate expiry src/mypackage --ignore='expired:mypackage.legacy:old_api'
pydeprecate all src/mypackage --report-json=ci/pydeprecate-baseline.json   # record today's backlog once
pydeprecate all src/mypackage --baseline=ci/pydeprecate-baseline.json      # later runs fail only on new findings
```

#### Ignore patterns

| Entry                       | Scope                                                                                |
| --------------------------- | ------------------------------------------------------------------------------------ |
| `module:symbol`             | It accepts every rule of that symbol.                                                |
| `rule:module:symbol`        | It accepts one rule; use `*` for any rule.                                           |
| `*:mypackage.compat.*:*`    | Each part is an `fnmatch` glob, so this accepts everything under `mypackage.compat`. |
| `expired:mypackage.oldmod:` | The empty symbol names a deprecated module.                                          |

Commas always separate entries. A character class holding a comma such as `[a,o]` is therefore never one glob; write the class without commas (`[ao]`). The rule slugs are those of the report (`expired`, `min-grace`, `chain`, …).

#### Baselines and suppression

A baseline is any `--report-json` file with a `findings` list, from pyDeprecate v0.16 or later. Its finding ids are accepted exactly. Ids hold no line or version, so moved code and version bumps stay accepted, and a renamed symbol is a new finding.

Ids under the file's `suppressed` key count only when the baseline itself suppressed them (`suppressed_by.kind` is `baseline`). A baseline regenerated from a run that used it therefore keeps them.

A finding an ignore entry suppressed is not carried over: delete the ignore entry and it is active again. There is no update flag; regenerate the file with `--report-json`.

| Suppression detail                                   | Behaviour                                                                                                                                                 |
| ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Both an ignore entry and the baseline name a finding | It counts as ignored.                                                                                                                                     |
| Console and exit code                                | Suppressed findings disappear from the console sections, the counts and the exit code. The run prints `N finding(s) suppressed (ignore: a, baseline: b).` |
| Report                                               | It lists suppressed findings under `suppressed` with what matched them.                                                                                   |
| Gaps and other outputs                               | Suppressed findings never affect gaps, and SARIF and the GitHub Action never show them.                                                                   |

An unmatched baseline entry is reported once on stderr when all these conditions hold:

- It matches no finding of a check that ran on its wrapper.
- No gap such as `remove_in not PEP 440` or `not statically resolvable` names the wrapper.
- Its module was audited: it was inside a scanned package, not excluded, and imported without error.

The diagnostic is `… baseline entries match no finding of this run (stale, or outside a narrowed scan); regenerate the baseline from a full-scope run.`

The entry is counted as `summary.baseline_unmatched`; it never fails the run. One baseline may therefore be shared by several packages scanned in separate runs. Unused ignore entries are only counted (`summary.ignore_unmatched`).

## Static mode

`pydeprecate expiry --static` and `pydeprecate policy --static` read the package's source with Python's `ast` module instead of importing it. Nothing from the scanned package is imported or executed, including for the version and the `--report-json` table.

Use it where the package's runtime dependencies are not installed, such as the isolated environment of a remote [pre-commit hook](ci-integration.md#pre-commit-integration):

```bash
# phmdoctest:skip — CI template: replace src/mypackage with your package directory
pydeprecate expiry --static src/mypackage --version 2.0
pydeprecate policy --static src/mypackage
```

The output carries a `Mode: static (source read with ast; nothing imported)` header line. Recognized wrappers with resolvable settings are checked, subject to the blind spots below. A clean run does not establish complete coverage.

The source has to spell the settings a rule reads:

| Rule               | Reads                                                         |
| ------------------ | ------------------------------------------------------------- |
| `expired`          | `remove_in`                                                   |
| `min-grace`        | `deprecated_in`, `remove_in`                                  |
| `message-required` | `target`, `args_mapping`, `attrs_mapping`, `message_template` |

### Readable settings and forms

| Setting form                                                                | Example                                |
| --------------------------------------------------------------------------- | -------------------------------------- |
| A literal can be read.                                                      | Use `remove_in="2.0"`.                 |
| A module-level name bound once to a literal can be read.                    | Use `VERSION = "2.0"`.                 |
| An entry of a module-level dict literal splatted into the call can be read. | Use `**_DEPRECATION_ARGS`, bound once. |

| Target form                                                                           | Interpretation                                                                                                                                                                                                                                    |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A function, class or lambda, or a bare name imported from outside the scanned package | It is read as a replacement.                                                                                                                                                                                                                      |
| `None`, `True`, `False` and `TargetMode` members                                      | They are read as such, also through a module-level name bound once to one. That name can be bound in the same module or imported from another module of the scanned package.                                                                      |
| A `deprecated_instance` `target` that builds plain data                               | It is read as data. As at runtime, `message-required` therefore needs an `attrs_mapping` or a `message_template` for it. Plain data includes a literal, a list, dict or set display, a module-level name bound once to one, or a call of a class. |
| A call of a class the same file defines with `__call__` or with bases                 | It is a gap.                                                                                                                                                                                                                                      |

Static mode recognizes these forms:

| Form                                               | Where or how it is recognized                                                                                     |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `@deprecated(...)` and `@deprecated_callable(...)` | They are recognized on functions and class members.                                                               |
| `@deprecated_class(...)` and `@deprecated(...)`    | They are recognized on classes.                                                                                   |
| `name = deprecated(...)(obj)`                      | A wrapper call is recognized.                                                                                     |
| `name = deprecated_class(...)(obj)`                | A class wrapper call is recognized.                                                                               |
| `name = deprecated_instance(obj, ...)`             | An instance wrapper call is recognized.                                                                           |
| A decorator configured once and applied many times | `legacy = deprecated(...)`, then `@legacy`, is recognized, also when imported from another module of the package. |
| Aliases                                            | `public = _old` is recognized.                                                                                    |
| Inherited members                                  | Members inherited from a base class in the scanned package are recognized.                                        |
| `deprecated_module(...)`                           | It is recognized when called in the module it deprecates.                                                         |
| A module-level `with` block                        | Code inside it is recognized.                                                                                     |

Factory names are followed however they are imported. Examples include `import deprecate as d`, `from deprecate.proxy import deprecated_class`, `from deprecate import *`, or a project module that re-exports them. Rows are named and attributed as an import-mode scan does.

### Unsettled settings

!!! warning "Recognized but unresolved deprecations are reported"

    A form the scan recognizes as a deprecation but cannot settle from the source is reported rather than skipped. Each unsettled wrapper or setting produces one stderr line: `pydeprecate gap: not statically resolvable: <file>:<line>: <reason>`.

| Unsettled form                  | Detail                                                                                                                                                                                                                                                               |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Computed values                 | A version computed at runtime, an f-string, or a name bound twice cannot be settled.                                                                                                                                                                                 |
| Mutable dicts                   | A dict changed after construction cannot be settled. Neither can a dict read other than by a `**` splat or a factory keyword: an alias or helper call may change it.                                                                                                 |
| Rebound names                   | Names the module rebinds through `globals()`, `vars()` or `setattr(sys.modules[...], ...)` cannot be settled.                                                                                                                                                        |
| Expression splats               | Settings splatted from an expression cannot be settled.                                                                                                                                                                                                              |
| Factory placement or binding    | A factory used inside a function, inside `if` or `try`, or under a name also bound to something else cannot be settled.                                                                                                                                              |
| Outer decorators                | A decorator stacked over the factory that may replace the wrapper cannot be settled. The exceptions are `property`, `classmethod`, `staticmethod`, `functools.lru_cache`, `functools.cache`, `abc.abstractmethod`, `dataclasses.dataclass` and the `typing` markers. |
| Attribute targets               | An attribute `target` other than a recognized `TargetMode` member cannot be settled.                                                                                                                                                                                 |
| Star-import targets             | A `target` a star import may bind cannot be settled.                                                                                                                                                                                                                 |
| Targets from within the package | A `target` imported from another package module cannot be settled unless that module binds it once to a literal or a definition.                                                                                                                                     |
| Unread bases                    | Members inherited from a base class the scan does not read cannot be settled.                                                                                                                                                                                        |
| Star-import exports             | A star import cannot be settled if its `__all__` is computed or the module also changes it in place or reads it (`__all__.append(...)`).                                                                                                                             |
| Replaced class members          | A deprecated class member that an `if`, `try` or loop in the class body may replace cannot be settled.                                                                                                                                                               |
| Import cycles                   | An import cycle cannot be settled.                                                                                                                                                                                                                                   |

A module whose source cannot be parsed, or is nested too deeply to analyze, is a `module not audited` gap. The rest of the package is still read.

The wrapper is left out only of rules that read the unsettled setting. A version held in a variable therefore still lets `message-required` judge its replacement.

A module kept out of the walk by `--no-recursive` or `--exclude` is read on demand when a walked module re-exports from it. Its gap lines are printed too.

!!! warning "Use strict mode for complete CI gates"

    A gap does not change the exit code unless strict mode is on, as with every other gap. A static run therefore passes when everything it could read is clean.

    Read the gap lines, or the `gaps` of `--report-json`, to see what was not audited. Spell those settings literally to bring them into the audit.

    With `--strict` or `strict = true`, a static run that recorded any gap exits `1`. A CI gate that must not pass on a partly read source should enable it.

### Static version resolution

Without `--version`, the resolver tries these sources in order:

1. Read the static `[project].version` of `pyproject.toml`.
2. For a `dynamic` version, read the source the package build uses: `[tool.setuptools.dynamic].version.attr`, else the package's `__version__`.
3. Try installed metadata.
4. Read the `__version__` literal in the package's `__init__.py`. Follow `from .module import __version__`, `... import version as __version__` and `from module import *` re-exports.

!!! warning "Rebound versions can fall back to stale metadata"

    A `__version__` rebound inside `if` or `try`, or through a function's `global`, makes the source read yield nothing. It does not yield the literal before the rebinding. Resolution therefore falls back to the package's installed metadata, which may be stale for a development checkout.

    Pass `--version` explicitly in that case.

### Locations and blind spots

A static run's findings carry the same `location` as an import run's: the file and the line of the wrapper's first decorator. Results from `--report-sarif` therefore show in GitHub code scanning. A wrapper of an object defined in another module or bound more than once has `location: null`.

!!! warning "No gap line does not prove full coverage"

    Gap lines cover only what the scan recognizes, so they do not prove that nothing was missed. The blind spots below are invisible to the scan and print no gap line. A clean static run does not show they hold no deprecation.

| Blind spot                                           | Detail                                                                                                                                                                                                                                  |
| ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dynamically created wrappers                         | Static mode cannot see wrappers created in loops or through `getattr`, `importlib` or `exec`.                                                                                                                                           |
| Private proxy construction                           | It cannot see a proxy built from the private `_DeprecatedProxy` directly.                                                                                                                                                               |
| Imported deprecated objects or configured decorators | It cannot see these when imported from outside the scanned package. An import scan lists such an object under the module that re-exports it.                                                                                            |
| Bare external target names                           | `from compat import NO_REPLACEMENT` is read as a replacement even when it holds `None` or a `TargetMode` member, so `message-required` passes it.                                                                                       |
| Calls to external target names                       | A `deprecated_instance` `target` calling an external name (`target=Settings()`) is read as an instance of a class. `message-required` therefore flags it even when the call returns a callable.                                         |
| External factories                                   | A factory imported from outside the scanned package (`from compat import deprecated`, or a star import of one) is invisible. The exception is a call passing `remove_in` by keyword, which produces a `not statically resolvable` gap.  |
| Report target names                                  | Static mode cannot reproduce these exactly as import mode prints them: `New API` shows the source text. A dataclass whose `attrs_mapping` import mode expands into `args_mapping` reads `dataclass` rather than `dataclass attributes`. |

See [Limitations](../limitations.md). Run without `--static` in an environment with the package's dependencies for a full audit.

## Exit codes

| Subcommand | Exit `0`                                                                                                            | Exit `1`                                                                         | Exit `2`                                                           | Exit `3`               |
| ---------- | ------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- | ------------------------------------------------------------------ | ---------------------- |
| `check`    | Clean or warnings only (chains / identity / no-effect / positional-only)                                            | Invalid argument mappings, misconfigured targets or inner-order properties found | A usage error (below)                                              | Scan or internal error |
| `expiry`   | No expired wrappers; or `packaging` not installed or the version unresolved (gap printed; exit 1 under strict mode) | Expired wrappers found                                                           | Malformed `--version` when `packaging` is available; a usage error | Scan or internal error |
| `policy`   | No violations; or only a skipped `min-grace` rule                                                                   | Policy violations found                                                          | Malformed rule value (flag or `pyproject.toml`); a usage error     | Scan or internal error |
| `chains`   | No chains                                                                                                           | Deprecated-to-deprecated chains found                                            | A usage error                                                      | Scan or internal error |
| `all`      | All checks clean, or only warnings and ungated policy violations (table always appended)                            | Any error of `check`, `expiry` or `chains`, and of `policy` with `--gate=policy` | Malformed `--version`, rule value or `--gate`; a usage error       | Scan or internal error |
| `status`   | Table rendered — status is not a pass/fail gate                                                                     | —                                                                                | Malformed `--version` or `--style`; a usage error                  | Scan or internal error |

`--exit-zero` turns exit `1` into `0` and never masks `2` or `3`. A `strict = true` from `pyproject.toml` is switched off by a typed `--exit-zero` for that run; stderr says so once.

`--strict` (since v0.16), or `strict = true` in `[tool.pydeprecate]`, adds one exit-`1` reason to every subcommand but `status`: any recorded gap. A gap is a part of the audit that did not run, printed as a `pydeprecate gap: <kind>:` line. Without strict mode, gaps never change the exit code.

A strict run that fails on its gaps says so on stderr after them: `Strict mode: N gap(s) fail this run.`

### Usage errors

A usage error (exit `2`) on any path stops the run before anything is scanned, on every subcommand. The following are usage errors:

| Error                                        | Detail or diagnostic                                                                                                                                                                                                     |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Malformed exclusions                         | `--exclude` is malformed, whether supplied as a flag or in `pyproject.toml`.                                                                                                                                             |
| Unknown options                              | An option is unknown.                                                                                                                                                                                                    |
| Invalid booleans                             | A boolean flag has a value other than `true`/`false`/`1`/`0`.                                                                                                                                                            |
| Duplicate version inputs                     | The version is given both as the legacy second positional argument and as `--version`.                                                                                                                                   |
| Invalid gates                                | `--gate` or `gate` names an unknown check or is not a list.                                                                                                                                                              |
| Conflicting import names                     | Two paths import as the same name from different places.                                                                                                                                                                 |
| Covered paths with independent configuration | Another path's recursive scan already covers a path with its own `[tool.pydeprecate]` settings or, with auto-detection, its own version.                                                                                 |
| Empty report paths                           | `--report-json=` is empty.                                                                                                                                                                                               |
| Conflicting exit flags                       | `--strict` is typed together with `--exit-zero`.                                                                                                                                                                         |
| Invalid strict configuration                 | A `strict` value in `pyproject.toml` is not a boolean.                                                                                                                                                                   |
| Invalid ignore inputs                        | An `--ignore` entry is malformed, or `--ignore` receives a list literal.                                                                                                                                                 |
| Invalid baselines                            | A `--baseline` file is missing, unreadable, malformed or has no `findings` list, as in a report written before v0.16. Malformed means a `findings` or `suppressed` value is not a list, or an entry lacks a string `id`. |
| Single files                                 | A single `.py` file produces `File paths are not supported`.                                                                                                                                                             |
| Several top-level packages                   | A directory holding two or more top-level packages produces `contains multiple packages (…)`; pass the package sub-directory instead.                                                                                    |
| Missing paths or modules                     | A name is neither an existing path nor an importable module. This produces `No such path or importable module`, also for relative spellings such as `./typo` or `.pkg`.                                                  |

!!! note "Missing submodules are checked during the scan"

    Only a missing submodule of an importable package (`pkg.nosuch`) is found when its own path is scanned. With several paths, the ones before it are scanned first. It still exits `2` with no report.

With `--static`, these are also usage errors:

| Error                         | Detail or diagnostic                                                                                                   |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Unsupported subcommand        | The flag is used on a subcommand other than `expiry` and `policy`: `` --static supports `expiry` and `policy` only ``. |
| Dotted module names           | Locating one would import its parent packages; pass its directory instead.                                             |
| Modules without Python source | A built-in or compiled module has no Python source.                                                                    |

### Scan and internal errors

Exit `3` means the audit itself did not run. Neither error writes a `--report-json` file.

| Diagnostic                       | Meaning                                                                                                                                         |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `pydeprecate: scan error: …`     | The scanned package cannot be imported because its module-level code raises or exits. This never occurs with `--static`, which imports nothing. |
| `pydeprecate: internal error: …` | There is a bug in the CLI. The diagnostic includes the issue tracker to report it to.                                                           |

!!! note "Submodule failures"

    A *submodule* that fails is a `module not audited` gap unless explicitly requested. When explicitly requested, the run exits `3` even when a parent scan covers it.

Policy violations are the one finding `all` reports without acting on by default: they move `all` from `0` to `1` only with `--gate=policy`.

Unknown or misspelled flags are never silently ignored. For example, `pydeprecate expiry mypackage --verison 2.0` exits `2` with an `unrecognized arguments: --verison` diagnostic before anything is scanned.

## CI recipes

Use `pydeprecate all --gate=policy` (since v0.16) for one step and one scan. It fails the build on misconfigured wrappers, chains, expired deprecations, and deprecations scheduled in a way the project does not allow. The console sections and the report's `rule` field tell you which problem you have.

A monorepo passes every package to the same step. The full workflow, with the rules declared once in `[tool.pydeprecate.policy]`, is in the [audit guide](ci-integration.md#enforcing-the-policy-in-ci).

While bringing an existing codebase into line, follow these steps:

1. Run the gate advisory-first so it reports without blocking.
2. Drop the flag once the backlog is clear.

```bash
pydeprecate policy src/mypackage --exit-zero
```

On GitHub Actions the CLI also ships as a ready-made step, `uses: Borda/pyDeprecate@<tag>`; the step exits with the CLI's own code. See the [GitHub Action](ci-integration.md#github-action) section of the audit guide for every input and output.

## Path formats

All subcommands accept these path formats:

| Format                     | Behaviour                                                                                                               |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **Package directory**      | Provide a path to a directory with `__init__.py`, such as `src/mypackage`.                                              |
| **Importable module name** | Provide a dotted module path such as `mypackage.utils` in import mode. Static mode accepts only top-level module names. |
| **Plain directory**        | The scan reads top-level `.py` files only. Nested files are reported as a `nested files not audited` gap.               |

!!! warning "Unsupported paths"

    A single `.py` file, or a directory holding two or more top-level packages, is a usage error (exit `2`) for every subcommand.

While scanning, the imported code sees no pydeprecate arguments in `sys.argv`. The header line `Imported from:` names the file the package was actually imported from.

## Python module invocation

Both `pydeprecate` and `python -m deprecate` are equivalent entry points:

```bash
python -m deprecate check src/mypackage
python -m deprecate expiry src/mypackage --version 2.0.0
```

Module invocation is useful where the `pydeprecate` script is not on `PATH`, such as a Docker image where only `python` is available.
