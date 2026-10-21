"""CLI entry point for pyDeprecate validation.

Provides two entry points for scanning Python code for misconfigured ``@deprecated`` wrappers:

- ``pydeprecate <subcommand> <path>`` — console script installed via ``pip install 'pyDeprecate[cli]'``
- ``python -m deprecate <subcommand> <path>`` — module invocation of the same CLI

Subcommands:
    check   — Validate wrapper configuration and flag misconfigured, chain-forming, or positional-only-arg wrappers.
    expiry  — Check for deprecated wrappers that have passed their scheduled ``remove_in`` deadline.
    policy  — Check wrappers against deprecation-governance rules (grace window, migration guidance).
    chains  — Detect deprecated wrappers whose ``target`` is itself a deprecated callable.
    all     — Run all four checks in a single scan pass.
    status  — Render a markdown deprecation table to stdout (and optionally save it to a file).

The whole ``deprecate._cli`` package is internal and may change without notice; the public Python API
for the same checks is :mod:`deprecate.audit`.

"""

import functools
import sys
from collections.abc import Callable
from typing import Optional, Union

from deprecate._cli.argv import (
    _bind_cli_arguments,
    _bool_params,
    _cli_signature,
    _ensure_utf8_streams,
    _internal_option,
    _normalize_bool_flags,
)
from deprecate._cli.commands import cmd_all, cmd_chains, cmd_check, cmd_expiry, cmd_policy, cmd_status
from deprecate._cli.report import _print, _print_skipped_modules, _record_run, _run_active, _write_report
from deprecate.audit._scan import _collect_skipped_modules

#: Option of every subcommand naming the file the run's machine-readable report goes to; ``cli`` handles it itself.
_REPORT_OPTION = "--report-json"


def _pop_report_option(argv: list[str]) -> Union[tuple[list[str], Optional[str]], str]:
    """Remove ``--report-json PATH`` / ``--report-json=PATH`` from *argv*; the last one wins, as for other flags.

    Returns:
        The remaining arguments and the report path (``None`` when not given), or an error message when the option
        has no value. Arguments after a ``--`` separator are left untouched.

    Examples:
        >>> _pop_report_option(["check", "--report-json", "r.json", "src/pkg"])
        (['check', 'src/pkg'], 'r.json')
        >>> _pop_report_option(["check", "--report-json=r.json"])
        (['check'], 'r.json')
        >>> _pop_report_option(["check", "--report-json"])
        'Option `--report-json` needs a file path.'

    """
    remaining: list[str] = []
    path: Optional[str] = None
    idx = 0
    while idx < len(argv):
        arg = argv[idx]
        idx += 1
        if arg == "--":
            return [*remaining, arg, *argv[idx:]], path
        name, has_value, value = arg.partition("=")
        if name != _REPORT_OPTION:
            remaining.append(arg)
            continue
        if not has_value:
            if idx >= len(argv):
                return "Option `--report-json` needs a file path."
            value = argv[idx]
            idx += 1
        if not value:
            return "Option `--report-json` needs a file path."
        path = value
    return remaining, path


def _prepare_argv(argv: list[str], commands: dict[str, Callable[..., int]]) -> tuple[list[str], Optional[str]]:
    """Turn the raw arguments into the Fire command line and the ``--report-json`` path; exit 2 on a usage error.

    Pops ``--report-json``, prepends the implicit ``check`` when the first token is no subcommand, rejects private
    ``--_*`` options and makes every boolean flag of the subcommand explicit (see :func:`_normalize_bool_flags`).

    """
    popped = _pop_report_option(argv)
    if isinstance(popped, str):
        _print(popped, stderr=True)
        sys.exit(2)
    argv, report_path = popped
    if argv and argv[0] not in commands and argv[0] not in {"-h", "--help"}:
        argv = ["check", *argv]
    internal = _internal_option(argv)
    if internal is not None:
        _print(f"Unknown option `{internal}`: options starting with an underscore are internal.", stderr=True)
        sys.exit(2)
    if argv and argv[0] in commands:
        argv = [argv[0], *_normalize_bool_flags(argv[1:], _bool_params(commands[argv[0]]))]
    return argv, report_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def cli() -> None:
    """CLI entry point for pydeprecate.

    The subcommand's integer return value becomes the process exit code, applied *after* ``fire.Fire``
    returns. Raising ``SystemExit`` from inside the Fire trace would abort Fire's own unconsumed-argument
    check, silently ignoring unknown or misspelled flags (exit 0 on typos); letting the trace complete
    makes Fire report ``Could not consume arg`` and exit 2 for such flags. Acts as the single top-level
    exception handler: unhandled exceptions from subcommands are converted to a non-zero exit with the
    exception type and message (the type prefix guarantees a non-blank stderr line even when the exception
    carries no message). SystemExit (Fire's ``--help`` and error exits) passes through unchanged. Every submodule the
    scan skipped because importing it raised gets one ``pydeprecate gap: module not audited: <module>: <error>``
    line on stderr at the end of the run, whatever warnings filters the audited package installs; every other
    advisory about a part of the audit that did not run (unresolved version, missing ``packaging``, unscanned files)
    is printed as an unwrapped stderr line starting with ``pydeprecate gap: <kind>:`` as well.

    A path whose name collides with a subcommand (``check``, ``expiry``, ``policy``, ``chains``, ``all``, ``status``) is
    treated as that subcommand by the implicit-``check`` shim. Scan such a path explicitly, e.g.
    ``pydeprecate check ./check``, so the leading token is the subcommand and the path is its argument.

    Boolean flags may appear before or after the path (``check --exit-zero src/pkg`` scans ``src/pkg``): bare flags
    are rewritten to their explicit form before Fire parses them, and any non-boolean value that still reaches a
    boolean parameter, such as a short flag's value (``-r src/pkg``), exits 2 instead of being
    taken as a truthy setting. Only the path and optional version accept positional values; extra paths exit 2 before
    scanning. Internal options and their negated aliases (``--_advisory``, ``--no-wrappers``) exit 2 before Fire sees
    them. Direct calls to the ``cmd_*`` functions retain their original signatures.

    Every subcommand also takes ``--report-json PATH``, handled here rather than by Fire: once the subcommand ran with
    exit code 0 or 1, the run's machine-readable record is written there — the deprecation table of the very scan
    that gated and every gap, each with its kind, detail and hint (see :func:`~deprecate._cli.report._write_report`).
    A usage error (exit 2) writes no report, and a failure to write one never changes the exit code.

    """
    _ensure_utf8_streams()
    try:
        import fire
    except ImportError:
        sys.exit("The 'pydeprecate' CLI requires the 'fire' package.\nInstall it with: pip install 'pyDeprecate[cli]'")

    commands: dict[str, Callable[..., int]] = {
        "check": cmd_check,
        "expiry": cmd_expiry,
        "policy": cmd_policy,
        "chains": cmd_chains,
        "all": cmd_all,
        "status": cmd_status,
    }
    argv, report_path = _prepare_argv(sys.argv[1:], commands)

    exit_code: Optional[int] = None

    def _capture(fn: Callable[..., int]) -> Callable[..., None]:
        """Record the cmd_* return code without returning it into the Fire trace.

        Returning the int would make Fire print it and treat it as a further component to consume arguments against;
        returning ``None`` keeps the output clean and lets Fire finish its trace. Expose only path and version as
        positional parameters, public options as keyword-only, and capture overflow to reject it before scanning. A non-
        boolean value bound to a boolean parameter also records exit 2 without running the subcommand.

        """
        cli_signature = _cli_signature(fn)

        @functools.wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> None:
            nonlocal exit_code
            bound = _bind_cli_arguments(fn, cli_signature, args, kwargs)
            if isinstance(bound, str):
                _print(bound, stderr=True)
                exit_code = 2
                return
            exit_code = fn(*bound.args, **bound.kwargs)

        wrapper.__signature__ = cli_signature  # type: ignore[attr-defined]
        return wrapper

    if _run_active():
        # an audited package whose import runs the CLI again sees the outer `sys.argv`; only the outermost run may
        # write the report, or the nested run's report would pass for the outer scan's
        report_path = None
    with _record_run() as run_record, _collect_skipped_modules() as skipped_modules:
        try:
            fire.Fire({name: _capture(fn) for name, fn in commands.items()}, command=argv)
        except Exception as exc:  # SystemExit is BaseException — Fire's own exits pass through untouched
            # Prefix the exception type so an exception with an empty message still produces a non-blank stderr
            # line (bare ``str(exc)`` on such exceptions exited 1 with nothing printed).
            sys.exit(f"{type(exc).__name__}: {exc}")
        finally:
            # Unlike the `audit: skipped` warning, these lines survive the audited package's own warnings filters.
            _print_skipped_modules(skipped_modules)
    # written after the skipped-module gaps are recorded, and never for a usage error (exit 2): nothing was scanned
    if report_path is not None and exit_code in (0, 1):
        _write_report(report_path, run_record)
    # exit_code stays None when no subcommand ran (bare `pydeprecate` help) → return normally (exit 0).
    if exit_code is not None:
        sys.exit(exit_code)
