"""Runner for the pyDeprecate GitHub Action defined in the repository-root ``action.yml``.

The action's single ``shell: python`` step executes this file from ``GITHUB_ACTION_PATH`` — the pyDeprecate checkout
at the ref the workflow pinned in ``uses:``. Inputs arrive as ``PYDEPRECATE_ACTION_*`` environment variables (never
interpolated into script text), and the script:

1. validates the inputs, failing with exit code ``2`` and an ``::error`` annotation on a bad value,
2. optionally installs ``pyDeprecate[cli,audit]`` from that same checkout into the job's current Python, so the
   ``uses:`` ref pins the version — the same binding the pre-commit hook manifest uses,
3. runs ``python -m deprecate <subcommand> [path] [args...]`` and returns its exit code unchanged,
4. optionally appends a markdown deprecation status table to the job summary.

Self-contained on purpose: it runs in the caller's environment before pyDeprecate may be installed, so it uses only
the standard library and stays compatible with the package's oldest supported Python (3.9).

"""

from __future__ import annotations

import os
import secrets
import shlex
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

#: CLI subcommands the action accepts; mirrors the command map in ``deprecate._cli``.
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
#: Subcommands whose own run renders the status table and accepts ``--output``: ``summary`` reuses a user ``--output``
#: given to such a gate run instead of scanning again; the other subcommands need a separate ``status`` run.
IN_RUN_SUMMARY_SUBCOMMANDS = ("status", "all")
#: Subcommands of :data:`IN_RUN_SUMMARY_SUBCOMMANDS` whose failure to write ``--output`` only prints an advisory, so the
#: runner can hand them its own table file. ``status`` exits 1 on a failed write — right for a report the user asked
#: for, wrong for the job summary — so it gets the table from a second ``status`` run (:func:`write_summary`) instead.
ADVISORY_OUTPUT_SUBCOMMANDS = ("all",)
#: Long flags of the gate's ``args`` that the summary's ``status`` re-scan forwards, so both scans see the same
#: modules and version; every other flag is gate-specific and would make ``status`` reject the call. Only these
#: long forms are repeated: a positional path or version in ``args``, or a short flag, is not, so the re-scan of a
#: single-gate subcommand can cover a different scope — give the path through the ``path`` input instead.
SUMMARY_FORWARDED_FLAGS = ("exclude", "version", "recursive", "norecursive")
_GAP_NOT_IMPORTED = (
    "{count} module(s) could not be imported and were not audited; install your package's dependencies"
    " in this job before the pyDeprecate step"
)
_GAP_VERSION_UNDETECTED = (
    "the package version could not be detected, so removal deadlines were not checked; install your"
    " package in this job or pass `--version X.Y` through `args`"
)
_GAP_VERSION_INVALID = (
    "the auto-detected package version is not a valid PEP 440 version, so removal deadlines were not checked;"
    " pass `--version X.Y` through `args`"
)
_GAP_NO_PACKAGING = (
    "the `packaging` library is not installed, so version-based checks (removal deadlines, the `min-grace`"
    " policy rule) were skipped; install 'pyDeprecate[audit]' or keep `install: true`"
)
_GAP_NESTED_FILES = (
    "nested Python files of a plain directory (no `__init__.py`) were not audited; point `path` at an importable"
    " package instead"
)
#: Kinds of the CLI's "gap lines" -> warning annotation text. The CLI prints one stderr line ``<kind>: <detail>`` each
#: time a run passes without auditing everything; it writes them with plain ``print``, so terminal-width wrapping never
#: splits a kind, and outside the warnings channel, so the audited package's own `warnings.filterwarnings("ignore")`
#: cannot hide them. Only lines that start with a kind count, so output of the audited package that merely quotes one
#: is ignored. Several lines of one kind raise a single annotation (``{count}`` sums them); every kind must stay
#: a literal of the CLI source (``deprecate/_cli/report.py``), which ``tests/integration/test_action.py`` checks.
SILENT_GAP_MARKERS = {
    "pydeprecate gap: module not audited": _GAP_NOT_IMPORTED,
    "pydeprecate gap: version not resolved": _GAP_VERSION_UNDETECTED,
    "pydeprecate gap: version not PEP 440": _GAP_VERSION_INVALID,
    "pydeprecate gap: packaging missing": _GAP_NO_PACKAGING,
    "pydeprecate gap: nested files not audited": _GAP_NESTED_FILES,
}

#: Trailing stderr lines of a failed summary re-scan quoted in its warning annotation.
SUMMARY_ERROR_TAIL_LINES = 3

_TRUE_VALUES = ("true", "1")
_FALSE_VALUES = ("false", "0")
#: Program a child interpreter runs to print the installed pyDeprecate's metadata version, or nothing when absent.
_VERSION_QUERY = (
    "import importlib.metadata as m\n"
    "try:\n"
    "    print(m.version('pyDeprecate'))\n"
    "except m.PackageNotFoundError:\n"
    "    pass\n"
)


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


def read_inputs(env: dict[str, str]) -> ActionInputs:
    """Read and validate every action input from ``env``.

    Raises:
        ValueError: An input holds a value the action cannot run with.

    """
    subcommand = _input(env, "subcommand")
    if subcommand not in SUBCOMMANDS:
        raise ValueError(f"input `subcommand` must be one of {', '.join(SUBCOMMANDS)}, got {subcommand!r}")
    try:
        args = tuple(shlex.split(_input(env, "args")))
    except ValueError as err:
        raise ValueError(f"input `args` cannot be split into arguments: {err}") from err
    path = _input(env, "path")
    if path.startswith("-"):
        # the CLI would parse it as an option (`--help` prints help and exits 0, scanning nothing), and a `--`
        # separator does not help: Fire reads every token after `--` as one of its own flags
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


def build_cli_argv(inputs: ActionInputs, python: str, summary_file: Path | None = None) -> list[str]:
    """Build the ``python -m deprecate`` command line for the gate run.

    ``summary_file`` is the table file the gate run itself writes for the job summary (:func:`in_run_table_file`).

    """
    argv = [python, "-m", "deprecate", inputs.subcommand]
    if inputs.path:
        argv.append(inputs.path)
    argv.extend(inputs.args)
    if inputs.exit_zero:
        argv.append("--exit-zero")
    if summary_file is not None and user_output_path(inputs.args) is None:
        # a second `--output` would win over the user's own one, whose report would then never be written
        argv.append(f"--output={summary_file}")
    return argv


def user_output_path(args: tuple[str, ...]) -> str | None:
    """Return the ``--output`` path given in the gate's ``args`` — the last one, as the CLI keeps — else ``None``.

    Recognises the long forms ``--output=PATH`` and ``--output PATH``; a trailing ``--output`` without a value is left
    to the CLI, which rejects it as a usage error.

    """
    found: str | None = None
    for index, arg in enumerate(args):
        if arg.startswith("--output="):
            found = arg[len("--output=") :]
        elif arg == "--output" and index + 1 < len(args):
            found = args[index + 1]
    return found or None


def in_run_table_file(inputs: ActionInputs, scratch_file: Path) -> Path | None:
    """Return the file the gate run writes the summary table to, or ``None`` when the summary needs a ``status`` run.

    A user ``--output`` in ``args`` is reused, so the requested report and the job summary both get the table; a failure
    to write it is the user's own request failing. The runner's ``scratch_file`` is handed only to
    :data:`ADVISORY_OUTPUT_SUBCOMMANDS`, so a job-summary file that cannot be written never fails a passing gate.

    """
    if not inputs.summary or inputs.subcommand not in IN_RUN_SUMMARY_SUBCOMMANDS:
        return None
    user_output = user_output_path(inputs.args)
    if user_output:
        return Path(user_output)
    return scratch_file if inputs.subcommand in ADVISORY_OUTPUT_SUBCOMMANDS else None


def summary_forwarded_args(args: tuple[str, ...]) -> list[str]:
    """Pick the gate arguments the summary's ``status`` re-scan must repeat to scan the same modules."""
    forwarded: list[str] = []
    take_next = False
    for index, token in enumerate(args):
        if take_next:
            forwarded.append(token)
            take_next = False
            continue
        name, has_value, _ = token.partition("=")
        flag = name[2:]
        if not name.startswith("--") or flag.replace("-", "_") not in SUMMARY_FORWARDED_FLAGS:
            continue
        forwarded.append(token)
        if has_value:
            continue
        if flag == "recursive":
            # the CLI also accepts `--recursive false`; take the next token only when it is a boolean literal
            following = args[index + 1].lower() if index + 1 < len(args) else ""
            take_next = following in _TRUE_VALUES + _FALSE_VALUES
        else:
            take_next = flag in ("exclude", "version")
    return forwarded


def build_summary_argv(inputs: ActionInputs, python: str, summary_file: Path) -> list[str]:
    """Build the ``status`` command line that writes the job-summary table when the gate run did not write it.

    After a ``status`` gate it repeats the gate's own command, so the table covers exactly what the gate scanned; after
    any other gate it scans ``path`` with the long-form :data:`SUMMARY_FORWARDED_FLAGS` of ``args``.

    """
    if inputs.subcommand == "status":
        argv = build_cli_argv(inputs, python)
    else:
        argv = [python, "-m", "deprecate", "status"]
        if inputs.path:
            argv.append(inputs.path)
        argv.extend(summary_forwarded_args(inputs.args))
    argv.append(f"--output={summary_file}")
    return argv


def installed_version(python: str) -> str | None:
    """Return the version of the pyDeprecate distribution ``python`` sees, or ``None`` when absent or unreadable.

    A fresh child interpreter answers, not this process: it reads the metadata pip has just written, free of any
    discovery cache of this process, through the import path the CLI later runs with. The value is the installed
    metadata version, which build backends record in normalized form (``0.15.0.dev`` becomes ``0.15.0.dev0``), so two
    such values compare equal exactly when they name the same release.

    """
    try:
        result = subprocess.run(  # noqa: S603 — list argv, no shell; the query is a constant
            [python, "-c", _VERSION_QUERY],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    version = result.stdout.strip()
    return version if result.returncode == 0 and version else None


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
    """Run the CLI with workflow commands disabled for its output; return its exit code.

    The scan imports the audited package and prints strings taken from it, so its output must not be able to emit
    ``::error``/``::add-mask`` lines of its own. Its stderr is relayed line by line to count the CLI's gap lines —
    lines that start with a :data:`SILENT_GAP_MARKERS` kind and mark a run that passed without auditing everything —
    which get one warning annotation per gap afterwards.
    Commands are resumed in a ``finally``, so a failure mid-run (a broken pipe, Ctrl+C) cannot leave them stopped
    for the rest of the step, where they would swallow every later annotation.

    Raises:
        CliStartError: The CLI process could not be started; raised once commands are resumed, so it can be annotated.

    """
    token = secrets.token_hex(16)
    counts = dict.fromkeys(SILENT_GAP_MARKERS, 0)
    print(f"::stop-commands::{token}", flush=True)
    try:
        code = _relay_cli(argv, counts)
    finally:
        print(f"::{token}::", flush=True)
    annotate_gaps(counts)
    return code


def _relay_cli(argv: list[str], counts: dict[str, int]) -> int:
    """Start the CLI, relay its stderr while counting its gap lines per kind in ``counts``, and return its exit code."""
    try:
        # list argv, no shell: user-supplied `args` reach the CLI as separate tokens and are never parsed by a shell
        process = subprocess.Popen(argv, stderr=subprocess.PIPE)  # noqa: S603
    except OSError as err:
        raise CliStartError(f"could not start the pyDeprecate CLI with {argv[0]}: {err}") from err
    with process:
        if process.stderr is None:  # unreachable with stderr=PIPE; keeps the type checker honest without assert
            raise RuntimeError("subprocess stderr pipe was not created")
        for raw_line in process.stderr:
            line = raw_line.decode("utf-8", errors="replace")
            sys.stderr.write(line)
            sys.stderr.flush()
            for marker in counts:
                # anchored at the line start: the audited package's own stderr output that merely mentions a gap
                # message is not a gap line (a package that forges a whole gap line is not guarded against)
                counts[marker] += line.startswith(f"{marker}:")
        return process.wait()


def annotate_gaps(counts: dict[str, int]) -> None:
    """Raise one warning annotation per distinct gap text for the markers in ``counts`` that matched a line."""
    totals: dict[str, int] = {}
    for marker, count in counts.items():
        if count:
            text = SILENT_GAP_MARKERS[marker]
            totals[text] = totals.get(text, 0) + count
    for text, count in totals.items():
        annotate("warning", text.format(count=count))


def append_summary(summary_file: Path, env: dict[str, str]) -> None:
    """Append the markdown table in ``summary_file`` to the job summary; failures only warn."""
    target = env.get("GITHUB_STEP_SUMMARY")
    if not target:
        annotate("warning", "`summary: true` needs GITHUB_STEP_SUMMARY, which is not set; skipping the job summary")
        return
    try:
        table = summary_file.read_text(encoding="utf-8")
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"## pyDeprecate deprecation status\n\n{table}\n")
    except OSError as err:
        annotate("warning", f"could not write the job summary: {err}")


def write_summary(inputs: ActionInputs, python: str, summary_file: Path, env: dict[str, str]) -> None:
    """Run ``status`` to write the table the gate run did not write, and append it to the job summary.

    Any failure of this run only warns, so the job summary can never change the gate's result. After a ``status`` gate
    the run repeats the gate's command; after another gate it repeats ``path`` and the long-form
    :data:`SUMMARY_FORWARDED_FLAGS` only, so it scans what the gate scanned unless ``args`` carries a positional path or
    version.

    """
    # stderr is captured, never relayed: the re-scan imports the audited package, and its output would sit outside
    # the stop-commands fence; quoted in the annotation it is escaped and cannot issue a workflow command
    result = subprocess.run(  # noqa: S603 — list argv, no shell; only arguments the gate run already took are passed
        build_summary_argv(inputs, python, summary_file),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    scan = exit_status(result.returncode)
    if scan.code != 0:
        message = f"the summary scan `pydeprecate status` exited {scan.description}; skipping the job summary"
        tail = result.stderr.strip().splitlines()[-SUMMARY_ERROR_TAIL_LINES:]
        annotate("warning", "\n".join([f"{message}:", *tail]) if tail else message)
        return
    append_summary(summary_file, env)


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
    if not inputs.summary:
        return run_gate(inputs, python, None, env)
    try:
        # a private directory removed on exit, so no summary file outlives the step or lands in the checkout
        scratch = tempfile.TemporaryDirectory(prefix="pydeprecate-", dir=env.get("RUNNER_TEMP") or None)
    except OSError as err:
        annotate("warning", f"could not create a temporary directory for the job summary ({err}); skipping it")
        return run_gate(inputs, python, None, env)
    with scratch as scratch_dir:
        # never created here: only a CLI run that renders the table writes it, so a run that did not is reported
        return run_gate(inputs, python, Path(scratch_dir) / "summary.md", env)


def run_gate(inputs: ActionInputs, python: str, summary_file: Path | None, env: dict[str, str]) -> int:
    """Run the gate and, when given a ``summary_file`` path for the table, append the job summary after it."""
    gate_table_file = in_run_table_file(inputs, summary_file) if summary_file is not None else None
    try:
        gate = exit_status(run_cli(build_cli_argv(inputs, python, gate_table_file)))
    except CliStartError as err:
        annotate("error", str(err))
        return 1
    if gate.code != 0:
        annotate("error", f"pydeprecate {inputs.subcommand} exited {gate.description}")
    if summary_file is not None and gate.code != 2:
        if gate_table_file is not None:
            append_summary(gate_table_file, env)
        else:
            write_summary(inputs, python, summary_file, env)
    return gate.code


if __name__ == "__main__":
    sys.exit(main())
