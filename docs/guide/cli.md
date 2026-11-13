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
pydeprecate all --gate=policy src/pkg_a src/pkg_b   # several packages, policy gated too, one scan each (since 0.16)
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

    Exit 1 for invalid argument mappings, misconfigured targets (`target=False`, `TargetMode.NOTIFY` with an `args_mapping`, `attrs_mapping` or `args_extra` it ignores, `TargetMode.ARGS_REMAP` without an `args_mapping`), inner-order `@property @deprecated` wrappers (only the getter warns; the setter and deleter are unprotected). Chains, identity mappings, no-effect wrappers and positional-only mappings are reported as warnings — exit 0.

    !!! note "Chain handling differs in `all`"

        `check` treats chains as advisory warnings (exit 0). `all` treats chains as hard errors (exit 1), because `all` ≡ `check + expiry + policy + chains` and `chains` exits 1 on any chain found — `policy` remains advisory inside `all` unless you pass `--gate=policy`.

=== "expiry"

    Checks whether any deprecated wrappers have passed their `remove_in` deadline using [`validate_deprecation_expiry()`](audit.md#enforcing-removal-deadlines). Requires the `[audit]` extra (included in `[audit,cli]`).

    ```bash
    # explicit version
    pydeprecate expiry path/to/your/package --version 2.0.0

    # auto-detect version (pyproject.toml, installed metadata, then package __version__)
    pydeprecate expiry path/to/your/package
    ```

    Exit 1 if any wrapper is past its removal deadline. If `packaging` is not installed, the check is skipped with a warning and exits 0 (exit 1 under strict mode; use `--exit-zero` to suppress exit 1 when expired wrappers are found).

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

    !!! warning "`all` reports policy violations without failing on them, unless `--gate=policy`"

        The policy defaults encode *a* project convention — a three-minor grace window on a clean release boundary — that not every repository shares, so `pydeprecate all` runs this check in **advisory** mode by default: violations are printed, but they never change `all`'s exit code. Inside `all` the violation table then renders as `[WARNING]` (yellow), with a trailer noting "advisory here — run `pydeprecate policy` to gate on it". To gate CI on the policy, pass `--gate=policy` to `all` (since 0.16; or `gate = ["policy"]` in `[tool.pydeprecate]`): the violations then render as `[ERROR]` without the trailer and fail the run, from the same single scan. The dedicated `pydeprecate policy` subcommand gates on them too. A config-read failure is the one exception to "advisory": an unreadable or malformed `pyproject.toml` still exits `2` inside `all` too (see below), before the chains check ever runs.

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

    # also gate on the policy, from the same scan (since 0.16)
    pydeprecate all path/to/your/package --gate=policy
    ```

    Exit 1 if any hard error is found: invalid argument mappings, misconfigured targets, inner-order properties, deprecated-to-deprecated chains, or expired wrappers. **Policy violations are advisory here by default** — they are printed (rendered as `[WARNING]`, with a trailer noting the check is advisory and naming `pydeprecate policy` as the gate) but never contribute to the exit code, because the policy defaults encode a project convention that not every repository shares. `--gate=policy` (since 0.16) makes them errors that fail `all` exactly as `pydeprecate policy` would; `--gate` takes a comma-separated list of `check`, `expiry`, `policy` and `chains`, and only adds checks — `check`, `expiry` and `chains` always gate, so today only `policy` changes anything. This advisory treatment covers violation *counts* only: a policy **configuration** failure — the nearest `pyproject.toml` cannot be read or parsed, or a `[tool.pydeprecate.policy]` value is malformed — still exits `2`, printing `Cannot read <path>: <err>`, and that exit is checked before the chains check ever runs. If `packaging` is not installed, expiry is skipped and policy skips `min-grace` but still runs its packaging-free `message-required` rule. Without `--gate=policy` that rule is advisory, so neither affects `all`'s exit `1`; with `--gate=policy` a `message-required` violation still exits `1` (unless `--exit-zero` applies); a malformed explicit `--version` is also advisory. With `packaging` available, an invalid explicit `--version` exits `2`. The deprecation table is always appended regardless of pass/fail outcome.

=== "status"

    Generates and prints a markdown deprecation table to stdout. Standalone — runs no checks and exits `0` on success (an invalid `--style` or `--version` exits `2`). Use this when you only want to render the deprecation status table without running any validation.

    ```bash
    pydeprecate status path/to/your/package
    pydeprecate status path/to/your/package --style matrix
    pydeprecate status path/to/your/package --version 2.0.0 --output DEPRECATIONS.md
    ```

    `--style compact` (default) renders one row per symbol with a status column; `--style matrix` renders one column per release with `D`, `R` and `D/R` (deprecated and removed in the same release) lifecycle markers (spellings of one release such as `1.0`, `v1.0` and `1.0.0` share a column with the `audit` extra installed; without `packaging` only spellings that differ by the `v` prefix merge). `--output FILE` writes the table to a file in addition to printing it to stdout.

## Flags

| Flag                   | Default          | `check` | `expiry` | `policy` | `chains` | `all` | `status` | Effect                                                                                                                                                                                                                                             | Note                                                                                                                                                                                                                                                                                                 |
| ---------------------- | ---------------- | :-----: | :------: | :------: | :------: | :---: | :------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--version VERSION`    | auto-detected    |         |    ✓     |          |          |   ✓   |    ✓     | Package version for deadline comparison, applied to every path. Auto-detected per path if omitted (see below).                                                                                                                                     | `policy` compares `deprecated_in` to `remove_in` only, so it takes no version.                                                                                                                                                                                                                       |
| `--gate CHECKS`        | `pyproject.toml` |         |          |          |          |   ✓   |          | Checks `all` gates on beyond `check`, `expiry` and `chains`, comma-separated: `--gate=policy` makes policy violations errors that fail the run.                                                                                                    | Since 0.16. Default is `gate` of `[tool.pydeprecate]`, else none; `--gate=` switches the file's list off. Unknown check → exit `2`.                                                                                                                                                                  |
| `--no-recursive`       | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |    ✓     | Scan top-level module only; skip submodules.                                                                                                                                                                                                       | Also spelled `--recursive=false`.                                                                                                                                                                                                                                                                    |
| `--no-include-members` | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |    ✓     | Leave deprecated class members (methods, constructors, descriptors) out of the scan; only module-level wrappers are checked.                                                                                                                       | Members are scanned by default, as by every audit function (`include_members=True`). Also spelled `--include-members=false`.                                                                                                                                                                         |
| `--exit-zero`          | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |          | Exit `0` instead of `1` when hard errors are found — useful for advisory CI steps; usage errors (`2`) and scan or internal errors (`3`) still fail.                                                                                                |                                                                                                                                                                                                                                                                                                      |
| `--strict`             | `pyproject.toml` |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |          | Exit `1` when any part of the audit did not run — any `pydeprecate gap:` line, such as a module that could not be imported or an unresolved version — even with no finding.                                                                        | Since 0.16. Default is `strict` of `[tool.pydeprecate]`, else off; `--no-strict` switches it off for one run. A typed `--strict` together with `--exit-zero` → exit `2`; a typed `--exit-zero` overrides a `strict = true` that only `pyproject.toml` sets (one stderr note, the run is not strict). |
| `--ignore PATTERNS`    | `pyproject.toml` |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |          | Accept known findings without fixing them: entries `module:symbol` (every rule) or `rule:module:symbol` (one rule, or `*`), each part an `fnmatch` glob; comma-separated and repeatable. Suppressed findings are not printed, counted or gated on. | Since 0.16. Default is `ignore` of `[tool.pydeprecate]`, else nothing; `--ignore=` switches the file's list off. Malformed entry → exit `2`.                                                                                                                                                         |
| `--baseline FILE`      | `pyproject.toml` |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |          | Accept every finding listed in an earlier `--report-json` file (pyDeprecate 0.16 or later), matched by finding id.                                                                                                                                 | Since 0.16. Default is `baseline` of `[tool.pydeprecate]`, relative to that file; `--baseline=` switches it off. A file that is missing or unreadable, or is not a report with a `findings` list → exit `2`.                                                                                         |
| `--exclude`            | `pyproject.toml` |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |    ✓     | Glob patterns over full dotted module names to leave out of the scan; the scan itself never imports a matching package nor descends into it. One pattern, or comma-separated.                                                                      | Default is the `exclude` list of `[tool.pydeprecate]` (see below), else nothing. Quote the value — an unquoted `*` is expanded by the shell first. Malformed → exit `2`.                                                                                                                             |
| `--style`              | `compact`        |         |          |          |          |       |    ✓     | Table rendering style — `compact` (default) or `matrix`.                                                                                                                                                                                           |                                                                                                                                                                                                                                                                                                      |
| `--output FILE`        | stdout only      |         |          |          |          |   ✓   |    ✓     | Also save the markdown table to a file. Table is always printed to stdout regardless.                                                                                                                                                              | For `all`, a failure to write the file is reported but never changes the exit code.                                                                                                                                                                                                                  |
| `--report-json FILE`   | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |    ✓     | Write the run's machine-readable record for CI tooling: the deprecation table of this run's scan, every gap and every finding (see below).                                                                                                         | Written on exit `0` or `1` only; a write failure is reported on stderr and never changes the exit code.                                                                                                                                                                                              |
| `--report-sarif FILE`  | off              |    ✓    |    ✓     |    ✓     |    ✓     |   ✓   |          | Write the run's active findings as a SARIF 2.1.0 file for code-scanning tools (see below).                                                                                                                                                         | Since 0.16. Written on exit `0` or `1` only; a write failure is reported on stderr and never changes the exit code.                                                                                                                                                                                  |
| `--static`             | off              |         |    ✓     |    ✓     |          |       |          | Read the package's source with `ast` instead of importing it, for an environment without its runtime dependencies (see [Static mode](#static-mode)).                                                                                               | Nothing from the package is imported or run. A wrapper the source alone does not settle is a `not statically resolvable` gap, not a finding.                                                                                                                                                         |

A bare `pydeprecate policy src/mypackage` therefore runs a recursive scan, exits `1` on violations, and applies both default-on rules below (`--min-grace=0.3`, `--message-required=True`) — unless the project's `pyproject.toml` says otherwise (see [Project configuration in `pyproject.toml`](#project-configuration-in-pyprojecttoml)).

Flags and the path may come in any order: `pydeprecate check --exit-zero src/mypackage` scans `src/mypackage`, which is the order a [pre-commit hook](audit.md#pre-commit-integration) produces from its `args`. Boolean flags accept `true`, `false`, `1` or `0` (case-insensitive) as `--flag value` or `--flag=value`, and are negated with `--no-flag`, as in `--no-recursive`; the older `--noflag` spelling (`--norecursive`) still works but is not listed in `--help`. Every option may be written with dashes or underscores (`--exit-zero`, `--exit_zero`), but never abbreviated. Option values stay strings, so `--version 0.10` and `--min-grace=0.10` keep their trailing zero. Every positional argument is a path (see [Several paths in one run](#several-paths-in-one-run)). A short flag such as `-r src/mypackage`, or a boolean value other than `true`/`false`/`1`/`0`, exits `2` rather than scanning something you did not name. `pydeprecate <subcommand> --help` lists the options of each subcommand.

### Several paths in one run

Since 0.16 every subcommand takes several paths — `pydeprecate all src/pkg_a src/pkg_b` — and audits them in one run with one exit code:

1. Before anything is imported, every path is checked: a file, a directory of several packages, a name that is neither an existing path nor a findable module, a malformed setting in any path's `pyproject.toml`, or two paths that import as the same top-level name from different places (`<a> and <b> both import as <name>; scan them in separate runs`; a plain directory takes part with the bare name of each top-level file it scans, so a file its `exclude` leaves out takes no part) exit `2` with nothing scanned. The same path given twice, or a package together with a subpackage its recursive scan imports (every directory between them holds an `__init__.py`, and the package's `exclude` does not leave the subpackage out), is scanned once, with one stderr note; any other subpackage keeps its own scan. A covered path whose own lookup finds a different `[tool.pydeprecate]` file, or — when each path's version is auto-detected (`expiry`, `all` and `status` without `--version`) — a version of its own (a nearer `pyproject.toml` with a `[project]` table, or for a dotted module name such as `pkg.component` an installed `pkg-component` distribution), exits `2` instead, since those settings or that version would be dropped — scan it in a separate run. A covered path whose own lookup finds neither, such as `src/pkg/sub` whose only `pyproject.toml` is at the repository root, takes the covering path's. The one usage error found later is a missing submodule of an importable package (`pkg.nosuch`): the check looks up only the top-level package without importing it, so the missing submodule exits `2` when its path is scanned, after the paths before it (no report is written).
2. The paths are then scanned one after another, each with its own import root, its own `[tool.pydeprecate]` settings (`exclude`, `strict`, `ignore`, `baseline`, `gate` and the policy rules, from the `pyproject.toml` nearest to that path) and its own auto-detected version. Before each path is scanned, a module an earlier path's scan imported is dropped when this path would import that name from somewhere else — its own package or import root, or the installed environment instead of an earlier path's checkout — so each path audits the copy it would audit on its own, whatever the order of the paths. A typed `--version` applies to every path, and the run says so before the first scan header. Flags apply to every path; `--exit-zero` is for the whole run and conflicts with strict mode on any path.
3. The console prints one scan header per path, then each check's findings of all paths in one section, then for `all` and `status` one table per path under a `### <package>` heading.
4. One exit code covers all paths: each path is judged with its own strict mode and `--gate` list, and the run fails when any path fails. The report holds every path (`paths`, `versions`, and each finding's and gap's `path`); the SARIF file lists them in `run.properties.paths` and gives each result its `path`.

With exactly two positionals, `expiry`, `all` and `status` still read the second one as the version when it names no existing path or findable module and parses as a version (`pydeprecate expiry src/mypackage 2.0`). This form is deprecated and stops working in 1.0; the run prints a notice on stderr asking for `--version` instead. A second positional that is not a version is a second path, so a mistyped one is reported as `No such path or importable module`. Operands after a `--` separator are always paths: `pydeprecate expiry -- src/mypackage 2.0` scans `2.0` as a second path instead of reading it as the version, so pass `--version 2.0` there.

A run can pass without auditing everything. Each such gap is reported as one unwrapped stderr line starting with `pydeprecate gap: <kind>:` — `module not audited` (one line per submodule that failed to import, printed whatever warnings filters the audited package installs, plus one `<package>.*` line when a recursive walk stopped early and left the remaining submodules unaudited), `version not resolved`, `version not PEP 440`, `remove_in not PEP 440` (a wrapper's own `remove_in` is not a valid PEP 440 version, so its removal deadline (`expiry`) or its grace window (`policy`'s `min-grace`) was skipped; one line per wrapper, `all` names each wrapper once), `deprecated_in not PEP 440` (a wrapper's own `deprecated_in` is not a valid PEP 440 version, so `policy`'s `min-grace` skipped its grace window; one line per wrapper), `epoch change` (a forward change of PEP 440 epoch makes the `min-grace` window unmeasurable; one line per wrapper), `packaging missing`, `nested files not audited`, `other copy audited` (the scanned package's import name was already taken by a copy from another directory — the CLI's own `deprecate`, or an editable install of another checkout — so that copy was audited; run the scan in the checkout's own environment) or `not statically resolvable` (a `--static` run could not read a wrapper, or a setting one of its rules needs, from the source alone; see [Static mode](#static-mode)) — so they stay visible in a CI log. A gap leaves the exit code alone unless strict mode is on (see [Exit codes](#exit-codes)).

For tooling, `--report-json FILE` records the same run as JSON — every subcommand takes it. The file has layout version (`schema`) 1; since 0.16 it also holds one record per finding:

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

- `mode` is `static` for a `--static` run and `import` otherwise. `table` is the markdown deprecation table of the scan that ran (`null` when it could not be rendered); a run over several paths joins one table per path, each under a `### <package>` heading, with a note in place of a table that could not be rendered. Each gap carries one of the kinds above, its detail, a remedy and the scan path it came from. `paths` and `versions` list every scanned path, and `options` summarises the run: `strict` is true when any path is strict, `gates` and `exclude` are the union over the paths.
- `findings` lists every finding the subcommand evaluates, once per id at its level in this run (`error` fails the gate, `warning` is reported only), sorted by level and then id. `message` is the finding's full description. `location` is where the wrapper is defined: a POSIX path relative to the working directory when the file is under it, else absolute, with the line of its first decorator (`line` is `null` for a deprecated module, and `location` is `null` for a `deprecated_instance` data object, for a class built at runtime and for every finding of a `--static` run, which builds no source location).
- `id` is `<rule>:<module>:<symbol>` (a deprecated module has an empty symbol: `expired:mypackage.oldmod:`). It holds no version, line, message or path, so it survives moved code, a version bump and reworded messages, and changes only when the symbol, its module or the rule is renamed. A wrapper kept under several public names is reported under the one named like the wrapper (its `__name__`), so adding an alias does not rename its id. A wrapper whose `__name__` is private (`old = deprecated(...)(_impl)`) or that is a proxy (`deprecated_class`, `deprecated_instance`) is reported under the first public name in alphabetical order, so a new alias that sorts earlier would rename it. Scanning a subpackage instead of its package with `--no-recursive` can report a re-export under another module.
- `gate` says whether the gate failed before `--exit-zero` and why (`findings`, and `gaps` under `--strict`); `exit_code` is the process exit code.
- The rule slugs are `invalid-args`, `misconfigured-target`, `inner-order-property`, `identity-mapping`, `no-effect`, `positional-only-mapping`, `chain`, `expired`, `min-grace` and `message-required`.
- The layout is described by the JSON Schema [`docs/schemas/report-1.schema.json`](../schemas/report-1.schema.json). `schema` is bumped only when a key is removed or renamed, or its meaning or type changes; adding a key does not bump it, so readers must ignore keys they do not know. The keys added in 0.16 (per-finding records, `gate`, `mode`, …) did not bump it: every key a 0.15 report has (`schema`, `table`, `gaps[].kind`/`detail`/`hint`) keeps its type and meaning, so a 0.15 reader still reads a 0.16 report.

The file is written only when the run exits `0` or `1`: a usage error (exit `2`) scanned nothing and a crash writes no file, so a consumer never publishes stale results. The [GitHub Action](audit.md#github-action) builds its gap annotations and job summary from this file.

### SARIF for code scanning

[SARIF](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html) (Static Analysis Results Interchange Format) is the OASIS standard JSON format in which static-analysis tools report findings: a rule, a level, a message, a file and line, and a fingerprint that identifies the finding. GitHub code scanning reads it to list each finding as an alert, and uses the fingerprint to recognise a finding it has already seen, so the same finding is not raised twice when the code moves ([GitHub's SARIF support](https://docs.github.com/en/code-security/reference/code-scanning/sarif-files/sarif-support)).

For pyDeprecate this turns an `expired`, `min-grace` or `chain` finding into an alert on the wrapper's decorator line, with no custom CI glue, and the same file opens in any other SARIF viewer. When all you need is a pass or fail gate, skip it: the exit code and `--report-json` cover that.

For code scanning, `--report-sarif FILE` (since 0.16; every subcommand but `status`) writes the same run as one SARIF 2.1.0 run: one result per active finding with its level in the run, its message and its stable id as the `pydeprecateFindingId/v1` partial fingerprint, and one rule per check rule the subcommand evaluates. A result is located by a path relative to the working directory under the `%SRCROOT%` base (an absolute `file:` URI outside it) and the line of the wrapper's first decorator (line 1 for a deprecated module). A finding without a source file, such as a `deprecated_instance` data object, is kept without a location and counted in `run.properties.unlocated`; GitHub code scanning does not display it. A `--static` run records no source location, so code scanning displays none of its results; a run whose results all lack a location says so on stderr. Suppressed findings are left out and counted in `run.properties.suppressed`; gaps are tool-execution notifications. `run.properties.mode` is the report's `mode`: `static` for a `--static` run, else `import`. Run the CLI from the repository root so the relative paths match the repository. The file is written only on exit `0` or `1`.

There is intentionally no `--include-pep702` flag: every subcommand evaluates pyDeprecate schedule metadata (`deprecated_in`, `remove_in`, a replacement) that symbols deprecated only with `warnings.deprecated` lack. List those with the Python API — `find_deprecation_wrappers(..., include_pep702=True)`, see [PEP 702-only deprecations](audit.md#pep-702-only-deprecations).

### Policy rule flags

These two are specific to `policy` — one flag per rule, switched off with `--min-grace=None` and `--message-required=False`. The *Default* column is the built-in value; a `[tool.pydeprecate.policy]` table in `pyproject.toml` replaces it, and a typed flag beats both.

| Flag                        | Default | Rule slug          | Effect                                                                                                                                        |
| --------------------------- | ------- | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `--min-grace=<delta>`       | `0.3`   | `min-grace`        | Minimum distance between `deprecated_in` and `remove_in` as a version-shaped delta: `1.0` one major, `0.3` three minors, `0.0.2` two patches. |
| `--message-required=<bool>` | `True`  | `message-required` | Require every wrapper to name a replacement (a `target`, a mapping, or a `message_template`).                                                 |

The delta has two or three components with at most one non-zero; a bare `1` is rejected as ambiguous (one *what*?) and so is a mixed spelling such as `1.2`. The removal must be one clean bump of a single component (`1.2` → `1.5` or `2.0`, never `2.3`); a coarser bump always clears a finer window — `0.3` is satisfied by one major bump. An all-zero spelling such as `0.0` is a zero-count window: no minimum distance, but a clean bump of that unit or coarser is still required. Ten or more steps need no quoting on the command line: `--min-grace=0.10` is ten minors. `--min-grace=1.0` (or `min-grace = "1.0"` in `pyproject.toml`) is the strict "removals only at a major release" policy. A `0.x` project needs no special setting: a bump to `1.0` is a major step and clears any window, while removals inside the `0.x` line are counted in minors as usual.

A malformed `--min-grace` value is rejected before the scan starts and exits `2` with a message naming the accepted spellings — it is never silently ignored.

### Project configuration in `pyproject.toml`

Declare the settings once, next to the code they govern, and every bare `pydeprecate` run — a developer's shell or a CI step — applies them without repeating flags:

```toml
[tool.pydeprecate]
exclude = ["my_package.tests", "*._legacy*"]   # every subcommand: the scan skips these packages (never imports them itself)
strict = true                                  # every gate subcommand: a gap fails the run (`--no-strict` overrides)
ignore = ["my_package.legacy:old_api"]         # every gate subcommand: findings accepted without a fix
baseline = "ci/pydeprecate-baseline.json"      # every gate subcommand: findings of an earlier report, by id
gate = ["policy"]                              # `all` only (since 0.16): also fail on policy violations

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
- A malformed value from the file exits `2` and the message names the `pyproject.toml` it came from; a non-boolean `message-required` or `strict`, a non-string `exclude` or `ignore` entry, a malformed ignore entry, or a `baseline` file that cannot be read or is not a report with a `findings` list, is rejected the same way. Unknown keys in either table (`min_grace` with an underscore is the usual typo) are reported on stderr and ignored, never silently enforced.
- Reading the file needs a TOML parser — built in on Python 3.11+, the `tomli` backport from the `[audit]` extra on 3.9–3.10. Without one, a reachable `pyproject.toml` triggers a stderr advisory instead of being silently skipped, and none of its `[tool.pydeprecate]` settings apply — including `strict = true`, so a gap no longer fails the run. On Python 3.9 or 3.10, install the `[audit]` extra to read `strict` from `pyproject.toml`; otherwise pass `--strict` on the command line.
- `pydeprecate all` applies the same resolved settings to its single scan and its advisory policy pass. The Python API (`validate_deprecation_policy()`) takes a module (object or importable name), never a filesystem path, and does not read `pyproject.toml` — pass its keyword arguments (including `exclude`) explicitly.

`--version` auto-detect: every subcommand (and the JSON report table) uses the same resolver. When the scanned argument is an existing *path*, the scanned path's directory and up to 2 parent directories are searched for a `pyproject.toml`; the nearest file with a `[project]` table owns the version, even if its version cannot be resolved; files without that table are skipped. An unresolved version does not fall through to an ancestor project. If the local version is unresolved or the argument is a bare module name, installed metadata is tried under the full import name, then the top-level import name, then the distribution that ships that top-level package (`PIL` → `Pillow`). The full name takes priority because a dotted component can have a separately versioned distribution. Only after these lookups fail does the resolver read the top-level import package's string `__version__`. A bare module *name* skips the `pyproject.toml` lookup, so it cannot pick up an unrelated project's version from your current working directory. The Python API (`validate_deprecation_expiry()`, `generate_deprecation_table()`) resolves the module's top-level package through this chain without the `pyproject.toml` step, as it receives a module, never a path.

Only a genuinely unresolved version skips the expiry check with a `version not resolved` gap and exit `0` (exit `1` under `--strict`); pass `--version` to supply it explicitly.

### Accepting known findings

Fix a finding when you can. When one must stay for now — a removal waiting on a downstream release — accept it instead of excluding its whole module:

```bash
pydeprecate expiry src/mypackage --ignore='expired:mypackage.legacy:old_api'
pydeprecate all src/mypackage --report-json=ci/pydeprecate-baseline.json   # record today's backlog once
pydeprecate all src/mypackage --baseline=ci/pydeprecate-baseline.json      # later runs fail only on new findings
```

- An `ignore` entry is `module:symbol` (every rule of that symbol) or `rule:module:symbol` (one rule, `*` for any); each part is an `fnmatch` glob, so `*:mypackage.compat.*:*` accepts everything under `mypackage.compat`, and `expired:mypackage.oldmod:` (empty symbol) names a deprecated module. Commas always separate entries, so a character class holding a comma such as `[a,o]` is never one glob; write the class without commas (`[ao]`). The rule slugs are those of the report (`expired`, `min-grace`, `chain`, …).
- A baseline is any `--report-json` file with a `findings` list (pyDeprecate 0.16 or later); its finding ids are accepted exactly. Ids hold no line or version, so moved code and version bumps stay accepted, and a renamed symbol is a new finding. Ids under the file's `suppressed` key count only when the baseline itself suppressed them (`suppressed_by.kind` is `baseline`), so a baseline regenerated from a run that used it keeps them, while a finding an ignore entry suppressed is not carried over: delete the ignore entry and it is active again. There is no update flag: regenerate the file with `--report-json`.
- A finding both name counts as ignored. Suppressed findings disappear from the console sections, the counts and the exit code; the run prints `N finding(s) suppressed (ignore: a, baseline: b).` and the report lists them under `suppressed` with what matched them. They never affect gaps, and SARIF and the GitHub Action never show them.
- A baseline entry that matches no finding of a check that ran on its wrapper (no gap such as `remove_in not PEP 440` or `not statically resolvable` names the wrapper), in a module the run audited (inside a scanned package, not excluded, imported without error), is reported once on stderr (`… baseline entries match no finding of this run (stale, or outside a narrowed scan); regenerate the baseline from a full-scope run.`) and counted as `summary.baseline_unmatched`; it never fails the run. One baseline may therefore be shared by several packages scanned in separate runs. Unused ignore entries are only counted (`summary.ignore_unmatched`).

## Static mode

`pydeprecate expiry --static` and `pydeprecate policy --static` read the package's source with Python's `ast` module instead of importing it. Nothing from the scanned package is imported or executed — not for the scan, not for the version, not for the `--report-json` table. Use it where the package's runtime dependencies are not installed, such as the isolated environment of a remote [pre-commit hook](audit.md#pre-commit-integration):

```bash
# phmdoctest:skip — CI template: replace src/mypackage with your package directory
pydeprecate expiry --static src/mypackage --version 2.0
pydeprecate policy --static src/mypackage
```

The output carries a `Mode: static (source read with ast; nothing imported)` header line, and recognized wrappers with resolvable settings are checked, subject to the blind spots below; a clean run does not establish complete coverage. The source has to spell the settings a rule reads:

| Rule               | Reads                                                         |
| ------------------ | ------------------------------------------------------------- |
| `expired`          | `remove_in`                                                   |
| `min-grace`        | `deprecated_in`, `remove_in`                                  |
| `message-required` | `target`, `args_mapping`, `attrs_mapping`, `message_template` |

A setting is read when it is a literal (`remove_in="2.0"`), a module-level name bound once to a literal (`VERSION = "2.0"`), or an entry of a module-level dict literal splatted into the call (`**_DEPRECATION_ARGS`, bound once). A `target` is read as a replacement when it names a function, class or lambda, or a bare name imported from outside the scanned package; `None`, `True`, `False` and `TargetMode` members are read as such, also through a module-level name bound once to one, whether that name is bound in the same module or imported from another module of the scanned package. A `deprecated_instance` `target` that builds plain data (a literal, a list, dict or set display, a module-level name bound once to one, or a call of a class) is read as data, so `message-required` needs an `attrs_mapping` or a `message_template` for it, as at runtime; a call of a class the same file defines with `__call__` or with bases is a gap. Static mode recognizes `@deprecated(...)` and `@deprecated_callable(...)` on functions and class members, `@deprecated_class(...)` and `@deprecated(...)` on classes, `name = deprecated(...)(obj)`, `name = deprecated_class(...)(obj)`, `name = deprecated_instance(obj, ...)`, a decorator configured once and applied many times (`legacy = deprecated(...)`, then `@legacy`, also when imported from another module of the package), aliases (`public = _old`), members inherited from a base class in the scanned package, `deprecated_module(...)` called in the module it deprecates, and code inside a module-level `with` block. It follows the factory names however they are imported (`import deprecate as d`, `from deprecate.proxy import deprecated_class`, `from deprecate import *`, or a project module that re-exports them), and names and attributes rows as an import-mode scan does.

A form the scan recognizes as a deprecation but cannot settle from the source is reported rather than skipped: each wrapper or setting the source alone does not settle — a version computed at runtime, an f-string, a name bound twice, a dict changed after it is built or read other than by a `**` splat or a factory keyword (an alias or a helper call may change it), a name the module rebinds through `globals()`, `vars()` or `setattr(sys.modules[...], ...)`, settings splatted from an expression, a factory used inside a function, inside `if` or `try`, or under a name the module also binds to something else, a decorator stacked over the factory that may replace the wrapper (any but `property`, `classmethod`, `staticmethod`, `functools.lru_cache`, `functools.cache`, `abc.abstractmethod`, `dataclasses.dataclass` and the `typing` markers), an attribute `target` other than a recognized `TargetMode` member, a `target` a star import may bind, a `target` imported from another module of the package that does not bind it once to a literal or a definition, members inherited from a base class the scan does not read, a star import of a computed `__all__` or one the module also changes in place or reads (`__all__.append(...)`), a deprecated class member that an `if`, `try` or loop in the class body may replace, an import cycle — is one `pydeprecate gap: not statically resolvable: <file>:<line>: <reason>` line on stderr. A module whose source cannot be parsed, or is nested too deeply to analyze, is a `module not audited` gap; the rest of the package is still read. The wrapper is left out only of the rules that read the unsettled setting, so a version held in a variable still lets `message-required` judge its replacement. A module that `--no-recursive` or `--exclude` keeps out of the walk but that a walked module re-exports from is read on demand, and its gap lines are printed too.

A gap does not change the exit code unless strict mode is on, as with every other gap. A static run therefore passes when everything it could read is clean; read the gap lines, or the `gaps` of `--report-json`, to see what was not audited, and spell those settings literally to bring them into the audit. With `--strict` (or `strict = true`) a static run that recorded any gap exits `1`, so a CI gate that must not pass on a partly read source should enable it.

Without `--version`, the version comes from the static `[project].version` of `pyproject.toml`, then — for a `dynamic` version — from the source the package build reads it from (`[tool.setuptools.dynamic].version.attr`, else the package's `__version__`), then from installed metadata, and finally from the `__version__` literal in the package's `__init__.py`, following `from .module import __version__`, `... import version as __version__` and `from module import *` re-exports. A `__version__` rebound inside `if` or `try`, or through a function's `global`, makes the source read yield nothing rather than the literal before it, so resolution falls back to the installed metadata of the package, which may be stale for a development checkout; pass `--version` explicitly in that case.

A static run's findings all carry `location: null` in `--report-json`, since the source reader builds no source location; in `--report-sarif` they are results without a location, which GitHub code scanning does not display. The gap lines cover only what the scan recognizes, so they do not prove that nothing was missed: the blind spots below are invisible to the scan and print no gap line, and a clean static run does not show they hold no deprecation. Static mode cannot see wrappers created in loops or through `getattr`, `importlib` or `exec`, a proxy built from the private `_DeprecatedProxy` directly, a deprecated object or configured decorator imported from outside the scanned package (an import scan lists such an object under the module that re-exports it), a bare `target` name imported from outside the scanned package (`from compat import NO_REPLACEMENT`), which is read as a replacement even when it holds `None` or a `TargetMode` member, so `message-required` passes it, a `deprecated_instance` `target` that calls a name imported from outside the scanned package (`target=Settings()`), which is read as an instance of a class, so `message-required` flags it even when the call returns a callable, a factory imported from a module outside the scanned package (`from compat import deprecated`, or a star import of one) unless its call passes `remove_in` by keyword, which is then a `not statically resolvable` gap, or the target names of report rows exactly as import mode prints them (`New API` shows the source text, and a dataclass whose `attrs_mapping` import mode expands into `args_mapping` reads `dataclass` rather than `dataclass attributes`); see [Limitations](../limitations.md). Run without `--static` in an environment with the package's dependencies for a full audit.

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

`--strict` (since 0.16), or `strict = true` in `[tool.pydeprecate]`, adds one exit-`1` reason to every subcommand but `status`: a run that recorded any gap — a part of the audit that did not run, printed as a `pydeprecate gap: <kind>:` line. Without it gaps never change the exit code. A strict run that fails on its gaps says so on stderr after them: `Strict mode: N gap(s) fail this run.`

A usage error (exit `2`) on any path stops the run before anything is scanned, on every subcommand: a malformed `--exclude` (flag or `pyproject.toml`), an unknown option, a boolean flag given a value other than `true`/`false`/`1`/`0`, the version given both as the legacy second positional argument and as `--version`, a `--gate` (or `gate`) that names an unknown check or is not a list, two paths that import as the same name from different places, a path another path's recursive scan already covers but with its own `[tool.pydeprecate]` settings or, with an auto-detected version, a version of its own, an empty `--report-json=`, `--strict` typed together with `--exit-zero`, a `strict` value in `pyproject.toml` that is not a boolean, a malformed `--ignore` entry or a list literal given to `--ignore`, a `--baseline` file that is missing, unreadable, malformed (a `findings` or `suppressed` value that is not a list, or an entry without a string `id`) or has no `findings` list (a report written before 0.16), a single `.py` file (`File paths are not supported`), a directory holding two or more top-level packages (`contains multiple packages (…)`; pass the package sub-directory instead), or a name that is neither an existing path nor an importable module (`No such path or importable module`, also for a relative spelling such as `./typo` or `.pkg`). Only a missing submodule of an importable package (`pkg.nosuch`) is found when its own path is scanned, so with several paths the ones before it are scanned first; it still exits `2` with no report. With `--static`, so is the flag on any subcommand other than `expiry` and `policy` (`` --static supports `expiry` and `policy` only ``), a dotted module name (locating it would import its parent packages; pass its directory instead), and a module name with no Python source (a built-in or compiled module).

Exit `3` means the audit itself did not run: `pydeprecate: scan error: …` when the scanned package cannot be imported (its module-level code raises or exits; never with `--static`, which imports nothing; a *submodule* that fails is only a `module not audited` gap), and `pydeprecate: internal error: …` for a bug in the CLI, with the issue tracker to report it to. Neither writes a `--report-json` file.

Policy violations are the one finding `all` reports without acting on by default: they move `all` from `0` to `1` only with `--gate=policy`.

Unknown or misspelled flags are never silently ignored: any unrecognised argument (e.g. `pydeprecate expiry mypackage --verison 2.0`) exits `2` with an `unrecognized arguments: --verison` diagnostic before anything is scanned.

## CI recipes

One step, one scan: `pydeprecate all --gate=policy` (since 0.16) fails the build on misconfigured wrappers, chains and expired deprecations, and on a deprecation scheduled in a way the project does not allow; the console sections and the report's `rule` field tell you which problem you have. A monorepo passes every package to the same step. The full workflow, with the rules declared once in `[tool.pydeprecate.policy]`, is in the [audit guide](audit.md#enforcing-the-policy-in-ci).

While you are bringing an existing codebase into line, run the gate advisory-first so it reports without blocking, then drop the flag once the backlog is clear:

```bash
pydeprecate policy src/mypackage --exit-zero
```

On GitHub Actions the CLI also ships as a ready-made step, `uses: Borda/pyDeprecate@<tag>`; the step exits with the CLI's own code. See the [GitHub Action](audit.md#github-action) section of the audit guide for every input and output.

## Path formats

All subcommands accept:

- **Package directory** — path to a directory with `__init__.py` (e.g. `src/mypackage`)
- **Importable module name** — a dotted module path (e.g. `mypackage.utils`) in import mode; static mode accepts only top-level module names
- **Plain directory** — scans top-level `.py` files only (nested files are reported as a `nested files not audited` gap)

A single `.py` file, or a directory holding two or more top-level packages, is a usage error (exit `2`) for every subcommand. While scanning, the imported code sees no pydeprecate arguments in `sys.argv`, and the header line `Imported from:` names the file the package was actually imported from.

## Python module invocation

Both `pydeprecate` and `python -m deprecate` are equivalent entry points:

```bash
python -m deprecate check src/mypackage
python -m deprecate expiry src/mypackage --version 2.0.0
```

Useful in environments where the `pydeprecate` script is not on `PATH` (e.g. inside a Docker image where only `python` is available).
