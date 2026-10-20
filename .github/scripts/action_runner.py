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

import importlib.metadata
import os
import re
import secrets
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: CLI subcommands the action accepts; mirrors the command map in ``deprecate._cli``.
SUBCOMMANDS = ("check", "expiry", "policy", "chains", "all", "status")
#: Extras installed with the package — the same ones ``.pre-commit-hooks.yaml`` requests.
INSTALL_EXTRAS = ("cli", "audit")
#: Prefix of the environment variables ``action.yml`` maps each input to.
ENV_PREFIX = "PYDEPRECATE_ACTION_"
#: Input name (as spelled in ``action.yml``) -> default used when the variable is unset.
INPUT_DEFAULTS = {
    "subcommand": "all",
    "path": "",
    "args": "",
    "exit-zero": "false",
    "install": "true",
    "summary": "false",
}
#: Long flags of the gate's ``args`` that the summary's ``status`` re-scan forwards, so both scans see the same
#: modules and version; every other flag is gate-specific and would make ``status`` reject the call.
SUMMARY_FORWARDED_FLAGS = ("exclude", "version", "recursive", "norecursive")
#: Substrings of the CLI's stderr that mean the run passed without auditing everything -> warning annotation text.
SILENT_GAP_MARKERS = {
    "audit: skipped": (
        "{count} module(s) could not be imported and were not audited; install your package's dependencies"
        " in this job before the pyDeprecate step"
    ),
    "Cannot check expiry: version not resolved": (
        "the package version could not be detected, so removal deadlines were not checked; install your"
        " package in this job or pass `--version X.Y` through `args`"
    ),
    "Could not resolve the current package version": (
        "the package version could not be detected, so removal deadlines were not checked; install your"
        " package in this job or pass `--version X.Y` through `args`"
    ),
}

_TRUE_VALUES = ("true", "1")
_FALSE_VALUES = ("false", "0")
_VERSION_PATTERN = re.compile(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)


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
    """Return one raw input value from the environment, falling back to its ``action.yml`` default."""
    variable = ENV_PREFIX + name.upper().replace("-", "_")
    return env.get(variable, INPUT_DEFAULTS[name]).strip()


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
    inputs = ActionInputs(
        subcommand=subcommand,
        path=_input(env, "path"),
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

    ``summary_file`` is only given for the ``status`` subcommand, whose own run writes the job-summary table.

    """
    argv = [python, "-m", "deprecate", inputs.subcommand]
    if inputs.path:
        argv.append(inputs.path)
    argv.extend(inputs.args)
    if inputs.exit_zero:
        argv.append("--exit-zero")
    if summary_file is not None:
        argv.append(f"--output={summary_file}")
    return argv


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
    """Build the ``status`` command line that writes the job-summary table after a non-``status`` gate run."""
    argv = [python, "-m", "deprecate", "status"]
    if inputs.path:
        argv.append(inputs.path)
    argv.extend(summary_forwarded_args(inputs.args))
    argv.append(f"--output={summary_file}")
    return argv


def checkout_version(action_path: Path) -> str | None:
    """Read ``__version__`` of the pyDeprecate checkout the action runs from, without importing it."""
    about = action_path / "src" / "deprecate" / "__about__.py"
    try:
        match = _VERSION_PATTERN.search(about.read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(1) if match else None


def install_package(action_path: Path, python: str) -> int:
    """Install ``pyDeprecate[cli,audit]`` from the action checkout into the current interpreter; return pip's code."""
    wanted = checkout_version(action_path)
    try:
        installed: str | None = importlib.metadata.version("pyDeprecate")
    except importlib.metadata.PackageNotFoundError:
        installed = None
    if installed and wanted and installed != wanted:
        annotate(
            "warning",
            f"replacing the installed pyDeprecate {installed} with {wanted} from the action ref for the rest of"
            " this job; pin the action to the release your project uses, or set `install: false`",
        )
    # a PEP 508 direct reference sidesteps pip's parsing of "<local path>[extras]" on Windows paths
    requirement = f"pyDeprecate[{','.join(INSTALL_EXTRAS)}] @ {action_path.resolve().as_uri()}"
    print("::group::Install pyDeprecate from the action ref", flush=True)
    # list argv, no shell: the requirement is built from the action's own path, never from an input
    code = subprocess.call([python, "-m", "pip", "install", "--disable-pip-version-check", requirement])  # noqa: S603
    print("::endgroup::", flush=True)
    if code != 0:
        annotate(
            "error",
            f"pip install of pyDeprecate failed (exit {code}). If this Python has no pip (e.g. a uv venv) or is"
            " externally managed, install 'pyDeprecate[audit,cli]' in an earlier step and set `install: false`",
        )
    return code


def run_cli(argv: list[str]) -> int:
    """Run the CLI with workflow commands disabled for its output; return its exit code.

    The scan imports the audited package and prints strings taken from it, so its output must not be able to emit
    ``::error``/``::add-mask`` lines of its own. Its stderr is relayed line by line to spot runs that passed
    without auditing everything (:data:`SILENT_GAP_MARKERS`), which get one warning annotation each afterwards.

    """
    token = secrets.token_hex(16)
    print(f"::stop-commands::{token}", flush=True)
    counts = dict.fromkeys(SILENT_GAP_MARKERS, 0)
    # list argv, no shell: user-supplied `args` reach the CLI as separate tokens and are never parsed by a shell
    with subprocess.Popen(argv, stderr=subprocess.PIPE) as process:  # noqa: S603
        if process.stderr is None:  # unreachable with stderr=PIPE; keeps the type checker honest without assert
            raise RuntimeError("subprocess stderr pipe was not created")
        for raw_line in process.stderr:
            line = raw_line.decode("utf-8", errors="replace")
            sys.stderr.write(line)
            sys.stderr.flush()
            for marker in counts:
                counts[marker] += marker in line
        code = process.wait()
    print(f"::{token}::", flush=True)
    for marker, count in counts.items():
        if count:
            annotate("warning", SILENT_GAP_MARKERS[marker].format(count=count))
    return code


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
    """Re-scan with ``status`` after a non-``status`` gate run and append its table to the job summary."""
    code = subprocess.call(  # noqa: S603 — list argv, no shell; only allowlisted gate flags are forwarded
        build_summary_argv(inputs, python, summary_file), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    if code != 0:
        annotate("warning", f"the summary scan `pydeprecate status` exited {code}; skipping the job summary")
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
    summary_file = Path(env.get("RUNNER_TEMP") or Path.cwd()) / f"pydeprecate-summary-{os.getpid()}.md"
    env_status_file = summary_file if inputs.summary and inputs.subcommand == "status" else None
    code = run_cli(build_cli_argv(inputs, python, env_status_file))
    if code != 0:
        annotate("error", f"pydeprecate {inputs.subcommand} exited {code}")
    if inputs.summary and code != 2:
        if env_status_file is not None:
            append_summary(env_status_file, env)
        else:
            write_summary(inputs, python, summary_file, env)
    return code


if __name__ == "__main__":
    sys.exit(main())
