"""CLI entry point for pyDeprecate validation.

Provides two entry points for scanning Python code for misconfigured ``@deprecated`` wrappers:

- ``pydeprecate <subcommand> <path>`` — console script installed with the package
- ``python -m deprecate <subcommand> <path>`` — module invocation of the same CLI

Subcommands:
    check   — Validate wrapper configuration and flag misconfigured, chain-forming, or positional-only-arg wrappers.
    expiry  — Check for deprecated wrappers that have passed their scheduled ``remove_in`` deadline.
    policy  — Check wrappers against deprecation-governance rules (grace window, migration guidance).
    chains  — Detect deprecated wrappers whose ``target`` is itself a deprecated callable.
    all     — Run all four checks in a single scan pass.
    status  — Render a markdown deprecation table to stdout (and optionally save it to a file).

The whole ``deprecate._cli`` package is internal and may change without notice; the public Python API
for the same checks is :mod:`deprecate.audit`. This module stays import-light — the parser, the subcommands and the
audit layer load only when :func:`cli` runs — so internal helpers such as :mod:`deprecate._cli._pkg` can be imported
from :mod:`deprecate.audit` without loading the CLI.

"""

import sys

#: Option of every subcommand naming the file the run's machine-readable report goes to; ``cli`` handles it itself.
#: The GitHub Action probes for this name to tell whether the installed CLI writes reports.
_REPORT_OPTION = "--report-json"


def cli() -> None:
    """CLI entry point for pydeprecate.

    Parses the command line with :func:`~deprecate._cli.argv._parse_cli_args` and runs the subcommand; its integer
    return value becomes the process exit code. A usage error (unknown option, malformed value, a second path) exits 2
    before anything is scanned, and ``--help`` exits 0. Acts as the single top-level exception handler: unhandled
    exceptions from subcommands are converted to a non-zero exit with the exception type and message (the type prefix
    guarantees a non-blank stderr line even when the exception carries no message). Every submodule the scan skipped
    because importing it raised gets one ``pydeprecate gap: module not audited: <module>: <error>`` line on stderr at
    the end of the run, whatever warnings filters the audited package installs; every other advisory about a part of the
    audit that did not run (unresolved version, missing ``packaging``, unscanned files) is printed as an unwrapped
    stderr line starting with ``pydeprecate gap: <kind>:`` as well.

    A path whose name collides with a subcommand (``check``, ``expiry``, ``policy``, ``chains``, ``all``, ``status``) is
    treated as that subcommand by the implicit-``check`` shim. Scan such a path explicitly, e.g. ``pydeprecate check
    ./check``, so the leading token is the subcommand and the path is its argument.

    Flags may appear before, between or after the positionals (``check --exit-zero src/pkg`` scans ``src/pkg``), in
    either spelling (``--exit-zero``, ``--exit_zero``). A boolean flag takes ``true``/``false``/``1``/``0`` attached
    (``--exit-zero=false``) or as the next argument, and is negated with ``--no-<flag>`` or ``--no<flag>``. Only the
    path and, for ``expiry``, ``all`` and ``status``, the version are positional; a further positional exits 2. Direct
    calls to the ``cmd_*`` functions keep their signatures, including the internal ``_*`` parameters, which are never
    command-line options.

    Every subcommand also takes ``--report-json PATH``: once the subcommand ran with exit code 0 or 1, the run's
    machine-readable record is written there — the deprecation table of the very scan that gated and every gap, each
    with its kind, detail and hint (see :func:`~deprecate._cli.report._write_report`). A usage error (exit 2) writes no
    report, and a failure to write one never changes the exit code.

    """
    from deprecate._cli.argv import _ensure_utf8_streams, _parse_cli_args
    from deprecate._cli.report import _print_skipped_modules, _record_run, _run_active, _write_report
    from deprecate.audit._scan import _collect_skipped_modules

    _ensure_utf8_streams()
    parsed = _parse_cli_args(sys.argv[1:])
    if parsed is None:  # bare `pydeprecate`: the help was printed
        return
    command, kwargs, report_path = parsed
    if _run_active():
        # an audited package whose import runs the CLI again sees the outer `sys.argv`; only the outermost run may
        # write the report, or the nested run's report would pass for the outer scan's
        report_path = None
    with _record_run() as run_record, _collect_skipped_modules() as skipped_modules:
        try:
            exit_code = command.function(**kwargs)
        except Exception as exc:  # SystemExit is BaseException — argparse's own exits pass through untouched
            # Prefix the exception type so an exception with an empty message still produces a non-blank stderr
            # line (bare ``str(exc)`` on such exceptions exited 1 with nothing printed).
            sys.exit(f"{type(exc).__name__}: {exc}")
        finally:
            # Unlike the `audit: skipped` warning, these lines survive the audited package's own warnings filters.
            _print_skipped_modules(skipped_modules)
    # written after the skipped-module gaps are recorded, and never for a usage error (exit 2): nothing was scanned
    if report_path is not None and exit_code in (0, 1):
        _write_report(report_path, run_record)
    sys.exit(exit_code)
