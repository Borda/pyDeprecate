"""Runner for the pyDeprecate GitHub Action defined in the repository-root ``action.yml``.

The action's single ``shell: python`` step executes this file from ``GITHUB_ACTION_PATH`` — the pyDeprecate checkout
at the ref the workflow pinned in ``uses:``. Inputs arrive as ``PYDEPRECATE_ACTION_*`` environment variables (never
interpolated into script text), and the script:

1. validates the inputs, failing with exit code ``2`` and an ``::error`` annotation on a bad value,
2. optionally installs ``pyDeprecate[cli,audit]`` from that same checkout into the job's current Python, so the
   ``uses:`` ref pins the version — the same binding the pre-commit hook manifest uses,
3. runs ``python -m deprecate <subcommand> [path...] [args...] --report-json=<file>`` and returns its exit code
   unchanged (the ``path`` input holds one path per line),
4. raises one warning annotation per kind of gap the CLI recorded in that report, then one annotation per finding
   within the slots GitHub's per-level annotation limit leaves, and optionally appends the report's deprecation table
   — the table of the very scan that gated — to the job summary,
5. publishes the step outputs (exit code, counts, the paths of a kept report copy and of the SARIF file) to
   ``GITHUB_OUTPUT``.

Everything the runner knows about a run comes from the CLI's ``--report-json`` file, never from matching its console
output. Self-contained on purpose: it runs in the caller's environment before pyDeprecate may be installed, so it uses
only the standard library and stays compatible with the package's oldest supported Python (3.9).

"""

from __future__ import annotations

import json
import os
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from enum import Enum
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
    "fail-on-gap": "false",
    "install": "true",
    "summary": "false",
    "sarif": "false",
}
#: Name of the report file inside the runner's private temporary directory.
REPORT_FILENAME = "report.json"
#: Layout version of the CLI's ``--report-json`` file that the CLI of this ref writes.
REPORT_SCHEMA = 1
#: Layout versions this runner reads. New keys never bump the layout, so a 0.15 CLI kept with ``install: false`` writes
#: the same version 1, and the gaps and table this runner reads keep their meaning in it.
REPORT_SCHEMAS = (1,)
#: Most gap details quoted in one warning annotation; the CLI log above it lists every one.
GAP_DETAILS_SHOWN = 3
#: Workflow-command levels of the annotations the runner raises.
ANNOTATION_LEVELS = ("error", "warning", "notice")
#: Most annotations of one level that GitHub shows for one step; the rest are dropped, and its documentation does not
#: say which. From actions/toolkit ``docs/problem-matchers.md``: "10 warning annotations, 10 error annotations, and 10
#: notice annotations per step". The 50 annotations per job, summed over all its steps, are not tracked here. One notice
#: counts the findings left out, which the log and the report list in full.
ANNOTATIONS_PER_LEVEL = 10
#: Warning slots the findings leave free for the two runner warnings that can follow them: the skipped job summary and
#: a failed write of the step outputs. The annotations raised before the findings are counted exactly instead.
_TRAILING_WARNING_SLOTS = 2
#: Workflow-command level of each finding level of the report.
FINDING_LEVELS = {"error": "error", "warning": "warning", "note": "notice"}
#: Step outputs, in the order ``action.yml`` declares them.
OUTPUT_NAMES = ("exit-code", "findings", "errors", "warnings", "gaps", "suppressed", "report-json", "sarif-file")
#: Count outputs, each read from the report ``summary`` key of the same name.
SUMMARY_OUTPUTS = ("findings", "errors", "warnings", "gaps", "suppressed")
#: Name of the SARIF file and of the kept report copy inside the outputs directory.
SARIF_FILENAME = "report.sarif"
#: Appended to a fallback warning when the `sarif` input was set: the run cannot write the file.
_SARIF_SKIPPED = " and the `sarif` input is skipped (the `sarif-file` output stays empty)"

_TRUE_VALUES = ("true", "1")
_FALSE_VALUES = ("false", "0")
#: ``args`` tokens that make the CLI print its help and exit 0 without scanning anything.
_HELP_FLAGS = ("-h", "--help")
#: Both spellings of the report options (the CLI also takes the underscore form) that the runner owns.
_REPORT_OPTION_PREFIXES = ("--report-json", "--report_json", "--report-sarif", "--report_sarif")
#: Program a child interpreter runs to print the installed pyDeprecate's metadata version, or nothing when absent.
_VERSION_QUERY = (
    "import importlib.metadata as m\n"
    "try:\n"
    "    print(m.version('pyDeprecate'))\n"
    "except m.PackageNotFoundError:\n"
    "    pass\n"
)
#: Exit code of :data:`_CAPABILITY_PROBE` for an importable CLI without ``--report-json`` (and so without
#: ``--report-sarif``, which came later); any other failure is not one. It is the exit code of that separate
#: ``python -c`` probe, unrelated to the CLI's own exit 3 for a crashed scan.
_PROBE_NO_REPORT = 3
#: Exit code of :data:`_CAPABILITY_PROBE` for an importable CLI that takes ``--report-json`` but not ``--report-sarif``.
_PROBE_NO_SARIF = 4
#: Program a child interpreter runs to tell which report options the installed CLI takes: exit 0 for both,
#: :data:`_PROBE_NO_SARIF` for ``--report-json`` alone, :data:`_PROBE_NO_REPORT` for neither.
_CAPABILITY_PROBE = (
    "import deprecate._cli as cli\n"
    "if not hasattr(cli, '_REPORT_OPTION'):\n"
    f"    raise SystemExit({_PROBE_NO_REPORT})\n"
    f"raise SystemExit(0 if hasattr(cli, '_SARIF_OPTION') else {_PROBE_NO_SARIF})\n"
)
#: Exit code of a CLI run whose scanned package could not be imported or that failed internally.
_CLI_CRASHED = 3


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
    #: The ``path`` input, one path per non-empty line; empty lets the CLI find the package itself.
    paths: tuple[str, ...]
    args: tuple[str, ...]
    exit_zero: bool
    fail_on_gap: bool
    install: bool
    summary: bool
    sarif: bool = False


class CliSupport(Enum):
    """The report options of the pyDeprecate CLI that the job's Python runs; each one arrived after the one before."""

    #: Predates ``--report-json``: no report, so no gap annotations, job summary or SARIF file.
    NO_REPORT = "no report option"
    #: Takes ``--report-json`` but not ``--report-sarif``.
    REPORT_ONLY = "report without SARIF"
    #: Takes both.
    REPORT_AND_SARIF = "report and SARIF"


def escape_data(text: str) -> str:
    """Escape a workflow-command message so newlines or ``%`` in it cannot start or corrupt a command."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text: str) -> str:
    """Escape a workflow-command property value (``file=``, ``title=``), which also ends at ``:`` and ``,``."""
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


@dataclass
class AnnotationBudget:
    """The annotations of each level the step may still raise before GitHub drops the rest.

    One budget serves one run. :func:`annotate` spends it on every annotation raised before the findings, so the
    findings, which come last, take only the slots that the gate error, the install and SARIF warnings and the gaps
    left.

    """

    left: dict[str, int] = field(default_factory=lambda: dict.fromkeys(ANNOTATION_LEVELS, ANNOTATIONS_PER_LEVEL))

    def spend(self, level: str) -> None:
        """Count one annotation of ``level`` as raised."""
        self.left[level] -= 1

    def remaining(self, level: str) -> int:
        """Return the slots of ``level`` still free; never negative, since a raised overflow is already lost."""
        return max(self.left[level], 0)


def annotate(level: str, message: str, budget: AnnotationBudget | None = None) -> None:
    """Print a GitHub ``error``/``warning``/``notice`` annotation titled ``pyDeprecate``, counted in ``budget``."""
    if budget is not None:
        budget.spend(level)
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
            "the action passes its own report options to annotate the run and fill the job summary and the `sarif`"
            " output; drop it from `args`"
        )
    return None


def read_paths(env: dict[str, str]) -> tuple[str, ...]:
    """Read the ``path`` input as one path per non-empty line, each stripped of surrounding whitespace.

    Raises:
        ValueError: A path starts with ``-``.

    """
    paths = tuple(line.strip() for line in _input(env, "path").splitlines() if line.strip())
    for path in paths:
        if path.startswith("-"):
            # the CLI would parse it as an option (`--help` prints help and exits 0, scanning nothing), and a `--`
            # separator does not help: the options the action appends after it would be read as further paths
            raise ValueError(
                f"input `path` must not start with `-`, got {path!r}; pass CLI options through `args`, and write a"
                " directory whose name starts with `-` as `./-name`"
            )
    return paths


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
    inputs = ActionInputs(
        subcommand=subcommand,
        paths=read_paths(env),
        args=args,
        exit_zero=_parse_bool("exit-zero", _input(env, "exit-zero")),
        fail_on_gap=_parse_bool("fail-on-gap", _input(env, "fail-on-gap")),
        install=_parse_bool("install", _input(env, "install")),
        summary=_parse_bool("summary", _input(env, "summary")),
        sarif=_parse_bool("sarif", _input(env, "sarif")),
    )
    if inputs.exit_zero and inputs.subcommand == "status":
        raise ValueError("input `exit-zero` has no effect on `status`, which never fails on findings")
    if inputs.fail_on_gap and inputs.subcommand == "status":
        raise ValueError("input `fail-on-gap` has no effect on `status`, which never fails")
    if inputs.sarif and inputs.subcommand == "status":
        raise ValueError("input `sarif` has no effect on `status`, which reports no findings")
    if inputs.fail_on_gap and inputs.exit_zero:
        raise ValueError(
            "inputs `fail-on-gap` and `exit-zero` contradict each other: `fail-on-gap` fails the step on a gap,"
            " `exit-zero` passes every run; set only one of them"
        )
    return inputs


def build_cli_argv(
    inputs: ActionInputs, python: str, report_file: Path | None = None, sarif_file: Path | None = None
) -> list[str]:
    """Build the ``python -m deprecate`` command line for the gate run, writing its report to ``report_file``.

    The runner's own options (``--strict`` for ``fail-on-gap``, ``--exit-zero``, the report) follow ``args``.
    :func:`read_inputs` has already refused the ``args`` tokens that would break that — a ``--`` separator, which makes
    the CLI read everything after it as positionals, a help flag, and any ``--report-json`` spelling — so the CLI always
    receives ``--exit-zero`` and the report option as written here.

    """
    argv = [python, "-m", "deprecate", inputs.subcommand, *inputs.paths]
    argv.extend(inputs.args)
    if inputs.fail_on_gap:
        argv.append("--strict")
    if inputs.exit_zero:
        argv.append("--exit-zero")
    if report_file is not None:
        argv.append(f"--report-json={report_file}")
    if sarif_file is not None:
        argv.append(f"--report-sarif={sarif_file}")
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


def cli_support(python: str) -> CliSupport:
    """Tell which report options the pyDeprecate CLI that ``python`` runs takes: both, only ``--report-json``, or none.

    A capability probe rather than a version comparison: with ``install: false`` the job may have any release
    installed, and comparing pre-release versions would need the ``packaging`` library this script cannot assume. One
    child interpreter answers for both options. Only an importable CLI that lacks an option counts as lacking it. An
    interpreter that cannot start, or one without pyDeprecate at all, counts as supporting both: the gate run then
    fails with the real reason, instead of a warning blaming the pyDeprecate version.

    """
    result = _child_output(python, _CAPABILITY_PROBE)
    returncode = None if result is None else result.returncode
    if returncode == _PROBE_NO_REPORT:
        return CliSupport.NO_REPORT
    if returncode == _PROBE_NO_SARIF:
        return CliSupport.REPORT_ONLY
    return CliSupport.REPORT_AND_SARIF


def install_package(action_path: Path, python: str, budget: AnnotationBudget | None = None) -> int:
    """Install ``pyDeprecate[cli,audit]`` from the action checkout into the current interpreter; return pip's status.

    A pyDeprecate of another version that the job had installed before is reported once pip has replaced it, by
    comparing the installed metadata versions before and after the install. Its annotations are counted in ``budget``.

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
            budget,
        )
        return pip.code
    after = installed_version(python)
    if after is None:
        annotate(
            "warning",
            "the version of the pyDeprecate installed from the action ref could not be read back, so a replaced"
            " install of another version cannot be ruled out; check the install log above",
            budget,
        )
    elif before is not None and before != after:
        annotate(
            "warning",
            f"replaced the installed pyDeprecate {before} with {after} from the action ref for the rest of this"
            " job; pin the action to the release your project uses, or set `install: false`",
            budget,
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
    if not isinstance(report, dict) or report.get("schema") not in REPORT_SCHEMAS:
        versions = " or ".join(str(schema) for schema in REPORT_SCHEMAS)
        annotate("warning", f"the pyDeprecate report is not of layout version {versions}; ignoring it")
        return None
    return report


def annotate_gaps(gaps: list[dict[str, Any]], budget: AnnotationBudget | None = None) -> None:
    """Raise one warning annotation per kind of gap: its count, the CLI's hint and the first few details.

    A gap is a part of the audit that did not run although the gate passed; without an annotation the green step would
    hide it. Kind, hint and detail are the CLI's own words, so this runner keeps no copy of them. Every kind is raised,
    whatever ``budget`` has left: a gap is the signal a green run must not lose, so the findings annotated after the
    gaps give way instead.

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
        annotate("warning", "\n".join(lines), budget)


def _finding_command(finding: dict[str, Any]) -> str:
    """Build the workflow command of one finding: its level, the file and line when the report has them, the rule."""
    properties = []
    location = finding.get("location")
    if isinstance(location, dict) and location.get("file"):
        properties.append(f"file={escape_property(str(location['file']))}")
        if isinstance(location.get("line"), int):
            properties.append(f"line={location['line']}")
    properties.append(f"title={escape_property('pyDeprecate ' + str(finding.get('rule', '')))}")
    level = FINDING_LEVELS[finding["level"]]
    return f"::{level} {','.join(properties)}::{escape_data(str(finding.get('message', '')))}"


def _findings_within(findings: list[dict[str, Any]], budget: AnnotationBudget) -> tuple[list[dict[str, Any]], int]:
    """Pick the findings to annotate within ``budget``, errors first, and count the ones left out.

    Each level has its own slots. The warning slots keep :data:`_TRAILING_WARNING_SLOTS` free, and when anything is left
    out the notice that counts it takes one notice slot, so a full notice level gives up its last finding for it.

    """
    by_level: dict[str, list[dict[str, Any]]] = {level: [] for level in ANNOTATION_LEVELS}
    for finding in findings:
        if finding.get("level") in FINDING_LEVELS:
            by_level[FINDING_LEVELS[finding["level"]]].append(finding)
    room = {level: budget.remaining(level) for level in ANNOTATION_LEVELS}
    room["warning"] = max(room["warning"] - _TRAILING_WARNING_SLOTS, 0)
    shown = {level: group[: room[level]] for level, group in by_level.items()}
    hidden = sum(len(group) for group in by_level.values()) - sum(len(group) for group in shown.values())
    if hidden and shown["notice"] and len(shown["notice"]) >= room["notice"]:
        shown["notice"].pop()
        hidden += 1
    return [finding for level in ANNOTATION_LEVELS for finding in shown[level]], hidden


def annotate_findings(findings: list[dict[str, Any]], budget: AnnotationBudget | None = None) -> None:
    """Raise one annotation per finding of a report, errors first, as far as the slots left in ``budget`` reach.

    Each annotation carries the finding's file and line when the report has them, so it lands on the line of the
    deprecated wrapper in the pull request. The findings that no slot is left for are counted in one notice, which names
    exactly the ones hidden; the log and the report list them all. Call it after the annotations that must not be
    crowded out (the gate error, the gaps), so that ``budget`` has already counted them.

    """
    budget = AnnotationBudget() if budget is None else budget
    shown, hidden = _findings_within(findings, budget)
    for finding in shown:
        budget.spend(FINDING_LEVELS[finding["level"]])
        print(_finding_command(finding), flush=True)
    if hidden > 0:
        annotate("notice", f"{hidden} more findings; see the log or the report", budget)


def write_outputs(env: dict[str, str], outputs: dict[str, str]) -> None:
    """Append the step outputs as ``name=value`` lines to ``GITHUB_OUTPUT``; skipped silently when it is unset.

    Every value is one line (a count or a path), so the plain ``name=value`` form is enough.

    """
    target = env.get("GITHUB_OUTPUT")
    if not target:
        return
    try:
        with open(target, "a", encoding="utf-8") as handle:
            for name in OUTPUT_NAMES:
                handle.write(f"{name}={outputs.get(name, '')}\n")
    except OSError as err:
        annotate("warning", f"could not write the step outputs: {err}")


def outputs_directory(env: dict[str, str], inputs: ActionInputs, budget: AnnotationBudget | None = None) -> Path | None:
    """Create the directory that keeps the report copy and the SARIF file for later steps, or ``None``.

    Created only when something reads it: step outputs (``GITHUB_OUTPUT`` set) or the ``sarif`` input. It lives in
    ``RUNNER_TEMP``, which the runner empties at the end of the job; without ``RUNNER_TEMP`` the path outputs stay
    empty. Its warnings are counted in ``budget``.

    """
    if not (inputs.sarif or env.get("GITHUB_OUTPUT")):
        return None
    runner_temp = env.get("RUNNER_TEMP")
    if not runner_temp:
        annotate("warning", "RUNNER_TEMP is not set, so the `report-json` and `sarif-file` outputs stay empty", budget)
        return None
    directory = Path(runner_temp) / f"pydeprecate-outputs-{secrets.token_hex(4)}"
    try:
        directory.mkdir()
    except OSError as err:
        annotate("warning", f"could not create the outputs directory ({err}); the path outputs stay empty", budget)
        return None
    return directory


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
    """Run the action end to end, publish the step outputs, and return the exit code the step should finish with."""
    _ensure_utf8_streams()
    env = dict(os.environ)
    outputs: dict[str, str] = {}
    code = _run_action(env, outputs)
    write_outputs(env, {**outputs, "exit-code": str(code)})
    return code


def _run_action(env: dict[str, str], outputs: dict[str, str]) -> int:
    """Validate the inputs, install, run the gate and annotate it; fill *outputs* along the way."""
    try:
        inputs = read_inputs(env)
    except ValueError as err:
        annotate("error", str(err))
        return 2
    python = sys.executable
    budget = AnnotationBudget()
    if inputs.install:
        action_path = Path(env.get("GITHUB_ACTION_PATH", Path(__file__).resolve().parents[2]))
        code = install_package(action_path, python, budget)
        if code != 0:
            return code
    else:
        # just installed from the action's own ref, the CLI takes both report options; only an older one kept with
        # `install: false` may lack one
        support = cli_support(python)
        if support is CliSupport.NO_REPORT:
            annotate(
                "warning",
                "the installed pyDeprecate predates `--report-json`, so gaps are not annotated, no job summary is"
                f" written{_SARIF_SKIPPED if inputs.sarif else ''}; keep `install: true` or upgrade the pyDeprecate"
                " this job installs",
            )
            return run_gate(inputs, python, None, env, outputs)
        if support is CliSupport.REPORT_ONLY and inputs.sarif:
            # passing `--report-sarif` would fail the gate with a usage error (exit 2); the report still works
            annotate(
                "warning",
                f"the installed pyDeprecate predates `--report-sarif`{_SARIF_SKIPPED}; keep `install: true` or"
                " upgrade the pyDeprecate this job installs",
                budget,
            )
            inputs = replace(inputs, sarif=False)
    try:
        # a private directory removed on exit, so no report outlives the step or lands in the checkout
        scratch = tempfile.TemporaryDirectory(prefix="pydeprecate-", dir=env.get("RUNNER_TEMP") or None)
    except OSError as err:
        skipped = _SARIF_SKIPPED if inputs.sarif else ""
        annotate(
            "warning", f"could not create a temporary directory for the report ({err}); running without it{skipped}"
        )
        return run_gate(inputs, python, None, env, outputs)
    with scratch as scratch_dir:
        # never created here: only a CLI run that completed writes it, so a missing file means no report
        return run_gate(inputs, python, Path(scratch_dir) / REPORT_FILENAME, env, outputs, budget)


def publish_report(
    report: dict[str, Any],
    report_file: Path,
    kept: Path | None,
    outputs: dict[str, str],
    budget: AnnotationBudget | None = None,
) -> None:
    """Annotate the report's gaps and findings, and fill the count and path outputs from it and the kept files.

    The gaps come first: each is a warning that a green run must not lose, while the findings, annotated last, take
    the slots of ``budget`` that the gate error and the gaps left.

    """
    budget = AnnotationBudget() if budget is None else budget
    annotate_gaps([gap for gap in report.get("gaps") or [] if isinstance(gap, dict)], budget)
    summary = report.get("summary")
    if isinstance(summary, dict):
        for name in SUMMARY_OUTPUTS:
            if isinstance(summary.get(name), int):
                outputs[name] = str(summary[name])
    if kept is not None:
        try:
            outputs["report-json"] = str(shutil.copy(report_file, kept / REPORT_FILENAME))
        except OSError as err:
            annotate("warning", f"could not keep a copy of the report for later steps: {err}", budget)
    findings = report.get("findings")
    if isinstance(findings, list):
        annotate_findings([finding for finding in findings if isinstance(finding, dict)], budget)


def run_gate(
    inputs: ActionInputs,
    python: str,
    report_file: Path | None,
    env: dict[str, str],
    outputs: dict[str, str] | None = None,
    budget: AnnotationBudget | None = None,
) -> int:
    """Run the gate, annotate its gaps and findings, with ``summary`` append its table, and fill the step *outputs*.

    ``budget`` counts the annotations the step has raised so far; the gate error is counted in it before the findings.

    """
    outputs = {} if outputs is None else outputs
    budget = AnnotationBudget() if budget is None else budget
    kept = outputs_directory(env, inputs, budget) if report_file is not None else None
    sarif_file = kept / SARIF_FILENAME if kept is not None and inputs.sarif else None
    try:
        returncode = run_cli(build_cli_argv(inputs, python, report_file, sarif_file))
    except CliStartError as err:
        annotate("error", str(err))
        return 1
    gate = exit_status(returncode)
    if gate.code == _CLI_CRASHED:
        annotate("error", f"pydeprecate {inputs.subcommand} exited 3 (scan or internal error; see the log)", budget)
    elif gate.code != 0:
        annotate("error", f"pydeprecate {inputs.subcommand} exited {gate.description}", budget)
    if report_file is None or gate.code in (2, _CLI_CRASHED):
        if report_file is None and inputs.summary:
            annotate("warning", "no pyDeprecate report is available; skipping the job summary")
        return gate.code
    if sarif_file is not None and sarif_file.is_file():
        outputs["sarif-file"] = str(sarif_file)
    report = read_report(report_file)
    if report is None:
        # a crash, or a report the CLI could not write (its reason is on stderr); a green step must not hide that
        # its gaps went unchecked
        skipped = " and the job summary is skipped" if inputs.summary else ""
        annotate("warning", f"the gate wrote no pyDeprecate report, so its gaps are not annotated{skipped}")
        return gate.code
    publish_report(report, report_file, kept, outputs, budget)
    if inputs.summary:
        table = report.get("table")
        if isinstance(table, str):
            append_summary(table, env)
        else:
            annotate("warning", "the gate's report holds no deprecation table; skipping the job summary")
    return gate.code


if __name__ == "__main__":
    sys.exit(main())
