"""Runner for the pyDeprecate GitHub Action defined in the repository-root ``action.yml``.

The action's single ``shell: python`` step executes this file from ``GITHUB_ACTION_PATH`` — the pyDeprecate checkout
at the ref the workflow pinned in ``uses:``. Inputs arrive as ``PYDEPRECATE_ACTION_*`` environment variables (never
interpolated into script text), and the script:

1. validates the inputs, failing with exit code ``2`` and an ``::error`` annotation on a bad value,
2. optionally installs ``pyDeprecate[cli,audit]`` from that same checkout into the job's current Python, so the
   ``uses:`` ref pins the version — the same binding the pre-commit hook manifest uses,
3. runs ``python -m deprecate <subcommand> [path] [args...] --report-json=<file>`` and returns its exit code unchanged,
4. raises one warning annotation per kind of gap the CLI recorded in that report, and optionally appends the report's
   deprecation table — the table of the very scan that gated — to the job summary.

Everything the runner knows about a run comes from the CLI's ``--report-json`` file, never from matching its console
output. Self-contained on purpose: it runs in the caller's environment before pyDeprecate may be installed, so it uses
only the standard library and stays compatible with the package's oldest supported Python (3.9).

"""

from __future__ import annotations

import json
import os
import secrets
import shlex
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

#: CLI subcommands the action accepts; mirrors the ``cmd_*`` functions of ``deprecate._cli.commands``.
SUBCOMMANDS = ("check", "expiry", "policy", "chains", "all", "status")
#: Extras installed with the package — the same ones ``.pre-commit-hooks.yaml`` requests.
INSTALL_EXTRAS = ("cli", "audit")
#: Prefix of the environment variables ``action.yml`` maps each input to.
ENV_PREFIX = "PYDEPRECATE_ACTION_"
#: Input name (as spelled in ``action.yml``) -> default used when the variable is unset, empty or whitespace-only.
INPUT_DEFAULTS = {
    "subcommand": "all",
    "path": "",
    "args": "",
    "exit-zero": "false",
    "install": "true",
    "summary": "false",
}
#: Name of the report file inside the runner's private temporary directory.
REPORT_FILENAME = "report.json"
#: Layout version of the CLI's ``--report-json`` file that this runner reads.
REPORT_SCHEMA = 1
#: Most gap details quoted in one warning annotation; the CLI log above it lists every one.
GAP_DETAILS_SHOWN = 3

_TRUE_VALUES = ("true", "1")
_FALSE_VALUES = ("false", "0")
#: ``args`` tokens that make the CLI print its help and exit 0 without scanning anything.
_HELP_FLAGS = ("-h", "--help")
#: Both spellings of the ``--report-json`` option (the CLI also takes the underscore form) that the runner owns.
_REPORT_OPTION_PREFIXES = ("--report-json", "--report_json")
#: Program a child interpreter runs to print the installed pyDeprecate's metadata version, or nothing when absent.
_VERSION_QUERY = (
    "import importlib.metadata as m\n"
    "try:\n"
    "    print(m.version('pyDeprecate'))\n"
    "except m.PackageNotFoundError:\n"
    "    pass\n"
)
#: Program a child interpreter runs to tell whether the installed CLI takes ``--report-json`` (exit 0) or not.
_REPORT_PROBE = "import deprecate._cli as cli\nraise SystemExit(0 if hasattr(cli, '_REPORT_OPTION') else 3)\n"
#: Exit code of :data:`_REPORT_PROBE` for an importable CLI without ``--report-json``; any other failure is not one.
_PROBE_UNSUPPORTED = 3


class ExitStatus(NamedTuple):
    """Exit status of a finished subprocess as a shell reports it, with its wording for an annotation."""

    code: int
    description: str


def exit_status(returncode: int) -> ExitStatus:
    """Map a subprocess return code to the exit status a shell reports, naming the signal that killed the process.

    On POSIX a process killed by signal N returns ``-N``; handed to ``sys.exit`` that would end the step with
    ``256 - N`` (``247`` for SIGKILL) and read "exited -9" in an annotation, so it maps to ``128 + N`` (``137``) as a
    shell does. The return code of a process that exited on its own is kept unchanged.

    """
    if returncode >= 0:
        return ExitStatus(returncode, str(returncode))
    code = 128 - returncode
    try:
        reason = signal.Signals(-returncode).name
    except ValueError:  # a signal number this platform does not name
        reason = f"signal {-returncode}"
    return ExitStatus(code, f"{code} (killed by {reason})")


class CliStartError(Exception):
    """The CLI process could not be started, e.g. the job's Python interpreter is missing or not executable."""


@dataclass(frozen=True)
class ActionInputs:
    """Validated action inputs, parsed once from the environment."""

    subcommand: str
    path: str
    args: tuple[str, ...]
    exit_zero: bool
    install: bool
    summary: bool


def escape_data(text: str) -> str:
    """Escape a workflow-command message so newlines or ``%`` in it cannot start or corrupt a command."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotate(level: str, message: str) -> None:
    """Print a GitHub ``error``/``warning``/``notice`` annotation titled ``pyDeprecate``."""
    print(f"::{level} title=pyDeprecate::{escape_data(message)}", flush=True)


def _input(env: dict[str, str], name: str) -> str:
    """Return one stripped input value from the environment, falling back to its ``action.yml`` default.

    An empty or whitespace-only value counts as unset: a reusable workflow that forwards one of its own optional inputs
    passes ``''`` when its caller left it out, and the step must then behave as if the input were omitted.

    """
    variable = ENV_PREFIX + name.upper().replace("-", "_")
    return (env.get(variable) or "").strip() or INPUT_DEFAULTS[name]


def _parse_bool(name: str, raw: str) -> bool:
    """Parse a boolean input with the CLI's own rule: ``true``/``false``/``1``/``0``, case-insensitive."""
    value = raw.lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError(f"input `{name}` must be true, false, 1 or 0, got {raw!r}")


def _reserved_args_reason(arg: str) -> str | None:
    """Return why the ``args`` input must not hand the argument ``arg`` to the CLI, or ``None`` when it may.

    The runner appends ``--exit-zero`` and ``--report-json`` after ``args``; an argument that stops the CLI from seeing
    them, ends its run without a scan or competes for the report file is refused instead of being silently dropped.

    """
    if arg == "--":
        return (
            "the CLI reads every token after a `--` separator as a positional argument, so the `--exit-zero` and"
            " report options the action appends would be rejected as extra paths"
        )
    if arg in _HELP_FLAGS:
        return "the CLI would print its help and exit 0 without scanning anything, turning the gate green"
    if arg.startswith(_REPORT_OPTION_PREFIXES):
        return (
            "the action passes its own `--report-json` to annotate gaps and fill the job summary; drop it from `args`"
        )
    return None


def read_inputs(env: dict[str, str]) -> ActionInputs:
    """Read and validate every action input from ``env``.

    Raises:
        ValueError: An input holds a value the action cannot run with.

    """
    subcommand = _input(env, "subcommand")
    if subcommand not in SUBCOMMANDS:
        # the CLI itself would read an unknown first token as the path of an implicit `check`, scanning the wrong thing
        raise ValueError(f"input `subcommand` must be one of {', '.join(SUBCOMMANDS)}, got {subcommand!r}")
    try:
        args = tuple(shlex.split(_input(env, "args")))
    except ValueError as err:
        raise ValueError(f"input `args` cannot be split into arguments: {err}") from err
    for arg in args:
        reason = _reserved_args_reason(arg)
        if reason is not None:
            raise ValueError(f"input `args` must not contain {arg!r}: {reason}")
    path = _input(env, "path")
    if path.startswith("-"):
        # the CLI would parse it as an option (`--help` prints help and exits 0, scanning nothing), and a `--`
        # separator does not help: the options the action appends after it would be read as further paths
        raise ValueError(
            f"input `path` must not start with `-`, got {path!r}; pass CLI options through `args`, and write a"
            " directory whose name starts with `-` as `./-name`"
        )
    inputs = ActionInputs(
        subcommand=subcommand,
        path=path,
        args=args,
        exit_zero=_parse_bool("exit-zero", _input(env, "exit-zero")),
        install=_parse_bool("install", _input(env, "install")),
        summary=_parse_bool("summary", _input(env, "summary")),
    )
    if inputs.exit_zero and inputs.subcommand == "status":
        raise ValueError("input `exit-zero` has no effect on `status`, which never fails on findings")
    return inputs


def build_cli_argv(inputs: ActionInputs, python: str, report_file: Path | None = None) -> list[str]:
    """Build the ``python -m deprecate`` command line for the gate run, writing its report to ``report_file``.

    The runner's own options follow ``args``. :func:`read_inputs` has already refused the ``args`` tokens that would
    break that — a ``--`` separator, which makes the CLI read everything after it as positionals, a help flag, and any
    ``--report-json`` spelling — so the CLI always receives ``--exit-zero`` and the report option as written here.

    """
    argv = [python, "-m", "deprecate", inputs.subcommand]
    if inputs.path:
        argv.append(inputs.path)
    argv.extend(inputs.args)
    if inputs.exit_zero:
        argv.append("--exit-zero")
    if report_file is not None:
        argv.append(f"--report-json={report_file}")
    return argv


def _child_output(python: str, program: str) -> subprocess.CompletedProcess[str] | None:
    """Run ``program`` in a fresh ``python`` and return the finished process, or ``None`` when it cannot start.

    A fresh child interpreter answers, not this process: it sees the distribution pip has just written, free of any
    discovery cache of this process, through the import path the CLI later runs with.

    """
    try:
        return subprocess.run(  # noqa: S603 — list argv, no shell; the program is a constant
            [python, "-c", program],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None


def installed_version(python: str) -> str | None:
    """Return the version of the pyDeprecate distribution ``python`` sees, or ``None`` when absent or unreadable.

    The value is the installed metadata version, which build backends record in normalized form (``0.15.0.dev`` becomes
    ``0.15.0.dev0``), so two such values compare equal exactly when they name the same release.

    """
    result = _child_output(python, _VERSION_QUERY)
    if result is None:
        return None
    version = result.stdout.strip()
    return version if result.returncode == 0 and version else None


def report_supported(python: str) -> bool:
    """Tell whether the pyDeprecate CLI that ``python`` runs writes ``--report-json`` reports.

    A capability probe rather than a version comparison: with ``install: false`` the job may have any release
    installed, and comparing pre-release versions would need the ``packaging`` library this script cannot assume.
    Only an importable CLI that lacks the option counts as unsupported. An interpreter that cannot start, or one
    without pyDeprecate at all, counts as supporting it: the gate run then fails with the real reason, instead of a
    warning blaming the pyDeprecate version.

    """
    result = _child_output(python, _REPORT_PROBE)
    return result is None or result.returncode != _PROBE_UNSUPPORTED


def install_package(action_path: Path, python: str) -> int:
    """Install ``pyDeprecate[cli,audit]`` from the action checkout into the current interpreter; return pip's status.

    A pyDeprecate of another version that the job had installed before is reported once pip has replaced it, by
    comparing the installed metadata versions before and after the install.

    """
    before = installed_version(python)
    # a PEP 508 direct reference sidesteps pip's parsing of "<local path>[extras]" on Windows paths
    requirement = f"pyDeprecate[{','.join(INSTALL_EXTRAS)}] @ {action_path.resolve().as_uri()}"
    print("::group::Install pyDeprecate from the action ref", flush=True)
    # list argv, no shell: the requirement is built from the action's own path, never from an input
    pip = exit_status(
        subprocess.call([python, "-m", "pip", "install", "--disable-pip-version-check", requirement])  # noqa: S603
    )
    print("::endgroup::", flush=True)
    if pip.code != 0:
        annotate(
            "error",
            f"pip install of pyDeprecate failed (exit {pip.description}). If this Python has no pip (e.g. a uv venv)"
            " or is externally managed, install 'pyDeprecate[audit,cli]' in an earlier step and set `install: false`",
        )
        return pip.code
    after = installed_version(python)
    if after is None:
        annotate(
            "warning",
            "the version of the pyDeprecate installed from the action ref could not be read back, so a replaced"
            " install of another version cannot be ruled out; check the install log above",
        )
    elif before is not None and before != after:
        annotate(
            "warning",
            f"replaced the installed pyDeprecate {before} with {after} from the action ref for the rest of this"
            " job; pin the action to the release your project uses, or set `install: false`",
        )
    return pip.code


def run_cli(argv: list[str]) -> int:
    """Run the CLI with workflow commands disabled and return its exit code.

    The scan imports the audited package and prints strings taken from it, so its output must not be able to emit
    ``::error``/``::add-mask`` lines of its own. Commands are resumed in a ``finally``, so a failure mid-run (Ctrl+C,
    an argument the operating system rejects) cannot leave them stopped for the rest of the step, where they would
    swallow every later annotation.

    The fence covers the CLI process itself, not what it leaves behind: ``wait()`` returns when that direct child
    exits, so a descendant process that the audited package started and left running still holds the inherited stdout.
    Anything it writes after the resume token is read as workflow commands again. Stopping commands guards against the
    scan's own output; it is not a sandbox for processes the scan spawns.

    Raises:
        CliStartError: The CLI process could not be started; raised once commands are resumed, so it can be annotated.

    """
    token = secrets.token_hex(16)
    print(f"::stop-commands::{token}", flush=True)
    try:
        # list argv, no shell: user-supplied `args` reach the CLI as separate tokens and are never parsed by a shell
        process = subprocess.Popen(argv, env={**os.environ, "PYTHONIOENCODING": "utf-8"})  # noqa: S603
    except OSError as err:
        raise CliStartError(f"could not start the pyDeprecate CLI with {argv[0]}: {err}") from err
    else:
        with process:
            return process.wait()
    finally:
        # Child stdout may end mid-line; a resume token joined to that text is not a workflow command.
        print(f"\n::{token}::", flush=True)


def read_report(report_file: Path) -> dict[str, Any] | None:
    """Return the CLI's report from ``report_file``, or ``None`` when it was not written or cannot be used.

    The file sits in the runner's fresh private directory, so its existence alone proves this run wrote it. An
    unreadable file or one of another layout version is reported with a warning.

    """
    if not report_file.is_file():
        return None
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        annotate("warning", f"could not read the pyDeprecate report: {err}")
        return None
    if not isinstance(report, dict) or report.get("schema") != REPORT_SCHEMA:
        annotate("warning", f"the pyDeprecate report is not of layout version {REPORT_SCHEMA}; ignoring it")
        return None
    return report


def annotate_gaps(gaps: list[dict[str, Any]]) -> None:
    """Raise one warning annotation per kind of gap: its count, the CLI's hint and the first few details.

    A gap is a part of the audit that did not run although the gate passed; without an annotation the green step would
    hide it. Kind, hint and detail are the CLI's own words, so this runner keeps no copy of them.

    """
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for gap in gaps:
        by_kind.setdefault(str(gap.get("kind")), []).append(gap)
    for kind, group in by_kind.items():
        details = [str(gap.get("detail", "")).splitlines()[0] for gap in group[:GAP_DETAILS_SHOWN] if gap.get("detail")]
        more = len(group) - len(details)
        lines = [f"{kind} ({len(group)}): {group[0].get('hint', '')}", *details]
        if details and more > 0:
            lines.append(f"... and {more} more in the log above")
        annotate("warning", "\n".join(lines))


def append_summary(table: str, env: dict[str, str]) -> None:
    """Append the markdown ``table`` to the job summary; failures only warn."""
    target = env.get("GITHUB_STEP_SUMMARY")
    if not target:
        annotate("warning", "`summary: true` needs GITHUB_STEP_SUMMARY, which is not set; skipping the job summary")
        return
    try:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"## pyDeprecate deprecation status\n\n{table}\n")
    except OSError as err:
        annotate("warning", f"could not write the job summary: {err}")


def _ensure_utf8_streams() -> None:
    """Write this script's own output as UTF-8 regardless of the console code page (e.g. cp1252 on Windows)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    """Run the action end to end and return the exit code the step should finish with."""
    _ensure_utf8_streams()
    env = dict(os.environ)
    try:
        inputs = read_inputs(env)
    except ValueError as err:
        annotate("error", str(err))
        return 2
    python = sys.executable
    if inputs.install:
        code = install_package(Path(env.get("GITHUB_ACTION_PATH", Path(__file__).resolve().parents[2])), python)
        if code != 0:
            return code
    # just installed from the action's own ref, the CLI takes `--report-json`; only an older one kept with
    # `install: false` may not
    if not inputs.install and not report_supported(python):
        annotate(
            "warning",
            "the installed pyDeprecate predates `--report-json`, so gaps are not annotated and no job summary is"
            " written; keep `install: true` or upgrade the pyDeprecate this job installs",
        )
        return run_gate(inputs, python, None, env)
    try:
        # a private directory removed on exit, so no report outlives the step or lands in the checkout
        scratch = tempfile.TemporaryDirectory(prefix="pydeprecate-", dir=env.get("RUNNER_TEMP") or None)
    except OSError as err:
        annotate("warning", f"could not create a temporary directory for the report ({err}); running without it")
        return run_gate(inputs, python, None, env)
    with scratch as scratch_dir:
        # never created here: only a CLI run that completed writes it, so a missing file means no report
        return run_gate(inputs, python, Path(scratch_dir) / REPORT_FILENAME, env)


def run_gate(inputs: ActionInputs, python: str, report_file: Path | None, env: dict[str, str]) -> int:
    """Run the gate, then annotate its gaps and, with ``summary``, append its table from ``report_file``."""
    try:
        returncode = run_cli(build_cli_argv(inputs, python, report_file))
    except CliStartError as err:
        annotate("error", str(err))
        return 1
    gate = exit_status(returncode)
    if gate.code != 0:
        annotate("error", f"pydeprecate {inputs.subcommand} exited {gate.description}")
    if report_file is None:
        if inputs.summary:
            annotate("warning", "no pyDeprecate report is available; skipping the job summary")
        return gate.code
    if gate.code == 2:  # a usage error scanned nothing, so there are no gaps or table to miss
        return gate.code
    report = read_report(report_file)
    if report is None:
        # a crash, or a report the CLI could not write (its reason is on stderr); a green step must not hide that
        # its gaps went unchecked
        skipped = " and the job summary is skipped" if inputs.summary else ""
        annotate("warning", f"the gate wrote no pyDeprecate report, so its gaps are not annotated{skipped}")
        return gate.code
    annotate_gaps([gap for gap in report.get("gaps") or [] if isinstance(gap, dict)])
    if inputs.summary:
        table = report.get("table")
        if isinstance(table, str):
            append_summary(table, env)
        else:
            annotate("warning", "the gate's report holds no deprecation table; skipping the job summary")
    return gate.code


if __name__ == "__main__":
    sys.exit(main())
