"""Integration tests for the GitHub Action — the composite ``action.yml`` and its runner script.

The runner (``.github/scripts/action_runner.py``) is executed as a real subprocess, exactly as the action's
``shell: python`` step runs it: inputs arrive as environment variables, the working directory is the repository
root, and ``PYDEPRECATE_ACTION_INSTALL=false`` skips the pip install so the tests never touch the network.
"""

import importlib.util
import inspect
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

import pytest
import yaml

from deprecate._cli import commands as cli_commands
from deprecate._cli import report as cli_report

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC_DIR = str(_REPO_ROOT / "src")
_RUNNER = _REPO_ROOT / ".github" / "scripts" / "action_runner.py"
_ACTION_YML = _REPO_ROOT / "action.yml"
_HOOKS_YAML = _REPO_ROOT / ".pre-commit-hooks.yaml"
_MISCONFIGURED_MODULE = _REPO_ROOT / "tests" / "collection_misconfigured.py"
#: Generous cap (seconds) on one runner subprocess, so a hung scan fails a test instead of the whole CI job.
_RUNNER_TIMEOUT = 300
#: Inherited variables the runner tests must not see: terminal colour switches change the CLI's text output, and every
#: ``PYDEPRECATE_ACTION_*`` variable is an action input the runner would read.
_AMBIENT_ENV = ("FORCE_COLOR", "NO_COLOR")


def _load_runner() -> ModuleType:
    """Import the runner script as a module so its constants can be compared with ``action.yml`` and the CLI."""
    spec = importlib.util.spec_from_file_location("action_runner", _RUNNER)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the action runner from {_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass field resolution looks the module up by name
    spec.loader.exec_module(module)
    return module


def _load_action() -> dict[str, Any]:
    """Parse the repository-root ``action.yml``."""
    return yaml.safe_load(_ACTION_YML.read_text(encoding="utf-8"))


action_runner = _load_runner()


def _summary_path(tmp_path: Path) -> Path:
    """Return the job-summary file the runner appends to, as ``_runner_env`` points ``GITHUB_STEP_SUMMARY`` at it."""
    return tmp_path / "step_summary.md"


def _runner_env(tmp_path: Path, **inputs: str) -> dict[str, str]:
    """Build the environment the action step gives the runner: one ``PYDEPRECATE_ACTION_*`` variable per input.

    ``GITHUB_STEP_SUMMARY`` and ``RUNNER_TEMP`` always point into ``tmp_path`` so a test run inside GitHub Actions
    never writes into the real job summary. Inherited ``PYDEPRECATE_ACTION_*`` inputs and terminal-colour switches are
    dropped, so the developer's or CI job's own environment cannot change what a test asserts.
    """
    existing_pythonpath = os.environ.get("PYTHONPATH")
    pythonpath = f"{_SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else _SRC_DIR
    env = {
        **{
            key: value
            for key, value in os.environ.items()
            if key not in _AMBIENT_ENV and not key.startswith(action_runner.ENV_PREFIX)
        },
        "PYTHONPATH": pythonpath,
        "COLUMNS": "200",
        "GITHUB_ACTION_PATH": str(_REPO_ROOT),
        "GITHUB_STEP_SUMMARY": str(_summary_path(tmp_path)),
        "RUNNER_TEMP": str(tmp_path),
        "PYDEPRECATE_ACTION_INSTALL": "false",
    }
    env.update({f"PYDEPRECATE_ACTION_{name.upper()}": value for name, value in inputs.items()})
    return env


def _run_runner(env: dict[str, str], cwd: Path = _REPO_ROOT) -> subprocess.CompletedProcess[str]:
    """Run the action runner as a subprocess in ``cwd`` with ``env``, decoding output as UTF-8."""
    return subprocess.run(
        [sys.executable, str(_RUNNER)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=cwd,
        timeout=_RUNNER_TIMEOUT,
    )


def _is_rejected(env: dict[str, str]) -> bool:
    """Tell whether the runner refuses the inputs in ``env`` as a usage error."""
    try:
        action_runner.read_inputs(env)
    except ValueError:
        return True
    return False


def _fence_token(output: str) -> str:
    """Return the token of the single ``::stop-commands::`` line in ``output``."""
    tokens = re.findall(r"^::stop-commands::([0-9a-f]{32})$", output, flags=re.MULTILINE)
    assert len(tokens) == 1, output
    return tokens[0]


def _run_action(tmp_path: Path, **inputs: str) -> subprocess.CompletedProcess[str]:
    """Run the action runner from the repository root with the given inputs."""
    return _run_runner(_runner_env(tmp_path, **inputs))


def _set_action_env(monkeypatch: pytest.MonkeyPatch, **inputs: str) -> None:
    """Give this process the environment of one action step: every input unset except those passed."""
    for name in action_runner.INPUT_DEFAULTS:
        monkeypatch.delenv(action_runner.ENV_PREFIX + name.upper().replace("-", "_"), raising=False)
    for name, value in inputs.items():
        monkeypatch.setenv(action_runner.ENV_PREFIX + name.upper(), value)


def _record_pip_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Replace the runner's ``pip install`` call with one that succeeds; return the command lines it was given."""
    calls: list[list[str]] = []

    def succeed(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    monkeypatch.setattr(action_runner.subprocess, "call", succeed)
    return calls


def _use_repo_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let a real CLI child spawned by this process import ``deprecate`` and the ``tests`` packages."""
    monkeypatch.setenv("PYTHONPATH", _runner_env(Path("."))["PYTHONPATH"])
    monkeypatch.chdir(_REPO_ROOT)


def _input_named_by(expression: str) -> str:
    """Return the action input a ``${{ inputs.<name> }}`` expression reads, failing the test when it names none."""
    match = re.search(r"inputs\.([\w-]+)", expression)
    assert match is not None, f"no `inputs.<name>` reference in {expression!r}"
    return match.group(1)


class TestActionDemo:
    """End-to-end use of the action as a CI step: gate on findings, or report without blocking."""

    def test_clean_package_passes(self, tmp_path: Path) -> None:
        """The default ``all`` subcommand passes on a package whose wrappers are all valid.

        A maintainer adds ``uses: Borda/pyDeprecate@<tag>`` with ``path: src/deprecate`` to their CI. The package
        has no misconfigured wrappers, no chains and nothing past its removal version, so the step must succeed.
        """
        result = _run_action(tmp_path, path="src/deprecate")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Scanning: src/deprecate" in result.stdout

    def test_misconfigured_package_fails_with_annotation(self, tmp_path: Path) -> None:
        """A hard error fails the step with the CLI's exit code and a GitHub error annotation.

        A contributor adds a wrapper whose ``args_mapping`` names a parameter that does not exist. The ``check``
        step must fail the build with exit code 1 and print a ``::error`` annotation so the failure shows up in
        the pull request's checks view, not only in the raw log.
        """
        result = _run_action(tmp_path, subcommand="check", path="tests.collection_misconfigured")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "nonexistent_arg" in result.stdout
        assert "::error title=pyDeprecate::pydeprecate check exited 1" in result.stdout

    @pytest.mark.parametrize(
        ("path", "exit_code"),
        [
            pytest.param("tests.collection_chains", 0, id="passing-gate"),
            pytest.param("tests.collection_misconfigured", 1, id="failing-gate"),
        ],
    )
    def test_step_script_of_action_yml_runs_the_runner_and_propagates_its_exit_code(
        self, tmp_path: Path, path: str, exit_code: int
    ) -> None:
        """The action step's ``run`` text, executed as ``shell: python`` does, runs the runner and keeps its exit code.

        Callers only ever reach the runner through this three-line glue in ``action.yml``. A renamed runner file, a
        wrong path join or a swallowed ``SystemExit`` would break every workflow using the action, so the step text is
        run verbatim: a clean scan must leave the step green and a failing gate must fail it.
        """
        step = next(step for step in _load_action()["runs"]["steps"] if "run" in step)
        script = tmp_path / "step.py"
        script.write_text(step["run"], encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_runner_env(tmp_path, subcommand="check", path=path),
            cwd=_REPO_ROOT,
            timeout=_RUNNER_TIMEOUT,
        )
        assert result.returncode == exit_code, result.stdout + result.stderr
        assert "Scanning: " + path in result.stdout

    def test_ambient_environment_does_not_leak_into_the_runner(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Inputs and colour switches set in the developer's shell do not change a test run.

        A contributor exports ``PYDEPRECATE_ACTION_SUBCOMMAND=status`` or ``FORCE_COLOR=1`` while debugging; the runner
        tests must still see only the inputs they pass and plain, colourless CLI text.
        """
        monkeypatch.setenv("PYDEPRECATE_ACTION_SUBCOMMAND", "not-a-subcommand")
        monkeypatch.setenv("PYDEPRECATE_ACTION_EXIT_ZERO", "maybe")
        monkeypatch.setenv("FORCE_COLOR", "1")
        result = _run_action(tmp_path, subcommand="check", path="tests.collection_chains")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Scanning: tests.collection_chains" in result.stdout
        assert "\x1b[" not in result.stdout

    def test_exit_zero_reports_without_blocking(self, tmp_path: Path) -> None:
        """``exit-zero: true`` keeps the findings in the log but lets the step pass.

        While a team clears an existing backlog of misconfigured wrappers, they run the action advisory-first:
        the same findings are printed, but the step must exit 0 so the build is not blocked.
        """
        result = _run_action(tmp_path, subcommand="check", path="tests.collection_misconfigured", exit_zero="true")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "nonexistent_arg" in result.stdout


class TestActionInputs:
    """Input validation and transport from ``action.yml`` inputs to the CLI command line."""

    @pytest.mark.parametrize(
        ("inputs", "message"),
        [
            pytest.param({"subcommand": "src/deprecate"}, "input `subcommand` must be one of", id="unknown-subcommand"),
            pytest.param({"exit_zero": "yes"}, "input `exit-zero` must be true, false, 1 or 0", id="bad-boolean"),
            pytest.param(
                {"subcommand": "status", "exit_zero": "true"},
                "input `exit-zero` has no effect on `status`",
                id="exit-zero-status",
            ),
            pytest.param({"args": "--exclude='unbalanced"}, "input `args` cannot be split", id="unbalanced-quote"),
            pytest.param({"args": "-- --exit-zero"}, "input `args` must not contain '--': ", id="args-separator"),
            pytest.param({"args": "--help"}, "input `args` must not contain '--help': ", id="args-help"),
            pytest.param(
                {"args": "--report-json=elsewhere.json"},
                "input `args` must not contain '--report-json=elsewhere.json': ",
                id="args-report-json",
            ),
        ],
    )
    def test_rejects_invalid_input(self, tmp_path: Path, inputs: dict[str, str], message: str) -> None:
        """A bad input value fails the step with exit code 2 before anything is scanned.

        A workflow author mistypes an input — for instance writes the package path into ``subcommand``. Without
        validation the CLI's implicit-``check`` shim would scan the typo as a path and could pass; the action must
        instead stop with a usage error that names the input.
        """
        result = _run_action(tmp_path, path="src/deprecate", **inputs)
        assert result.returncode == 2, result.stdout + result.stderr
        assert f"::error title=pyDeprecate::{message}" in result.stdout
        assert "Scanning:" not in result.stdout

    @pytest.mark.parametrize("blank", ["", " \t "])
    @pytest.mark.parametrize("name", list(action_runner.INPUT_DEFAULTS))
    def test_blank_input_uses_default(self, name: str, blank: str) -> None:
        """An input set to an empty or whitespace-only value falls back to its ``action.yml`` default.

        A reusable workflow forwards its own optional inputs to the action, so an input its caller left out arrives as
        an empty string rather than a missing variable. The step must run as if the input were omitted instead of
        failing validation with ``got ''``.
        """
        variable = action_runner.ENV_PREFIX + name.upper().replace("-", "_")
        assert action_runner.read_inputs({variable: blank}) == action_runner.read_inputs({})

    @pytest.mark.parametrize(
        ("name", "raw", "field", "expected"),
        [
            pytest.param("subcommand", " check ", "subcommand", "check", id="subcommand-stripped"),
            pytest.param("path", " src/pkg ", "path", "src/pkg", id="path-stripped"),
            pytest.param("args", "--version 2.0", "args", ("--version", "2.0"), id="args-split"),
            pytest.param("args", '--exclude="a b,c"', "args", ("--exclude=a b,c",), id="args-double-quotes"),
            pytest.param("args", "--exclude=a\\ b", "args", ("--exclude=a b",), id="args-backslash-escape"),
            pytest.param("args", "--exclude=--help,-h", "args", ("--exclude=--help,-h",), id="args-reserved-in-value"),
            pytest.param(
                "args",
                "--norecursive\n--version 2.0",
                "args",
                ("--norecursive", "--version", "2.0"),
                id="args-multiline",
            ),
            pytest.param("exit-zero", "TRUE", "exit_zero", True, id="exit-zero-upper-true"),
            pytest.param("exit-zero", "1", "exit_zero", True, id="exit-zero-one"),
            pytest.param("exit-zero", " true ", "exit_zero", True, id="exit-zero-padded"),
            pytest.param("exit-zero", "False", "exit_zero", False, id="exit-zero-mixed-false"),
            pytest.param("exit-zero", "0", "exit_zero", False, id="exit-zero-zero"),
            pytest.param("install", "FALSE", "install", False, id="install-upper-false"),
            pytest.param("install", "1", "install", True, id="install-one"),
            pytest.param("summary", "True", "summary", True, id="summary-mixed-true"),
            pytest.param("summary", "0", "summary", False, id="summary-zero"),
        ],
    )
    def test_reads_documented_value_spellings(self, name: str, raw: str, field: str, expected: object) -> None:
        """Inputs are stripped, booleans are case-insensitive, and ``args`` splits like a shell.

        Workflow authors write ``exit-zero: TRUE`` or pass a block scalar with one flag per line; the action documents
        shell-style splitting for ``args`` and the CLI's own boolean spellings, so each spelling must read the same way
        the CLI would.
        """
        inputs = action_runner.read_inputs({action_runner.ENV_PREFIX + name.upper().replace("-", "_"): raw})
        assert getattr(inputs, field) == expected

    @pytest.mark.parametrize("subcommand", ["expiry", "policy", "chains", "all", "check"])
    def test_exit_zero_is_accepted_where_the_cli_has_it(self, subcommand: str) -> None:
        """``exit-zero: true`` is valid for every subcommand that gates on findings.

        A team runs the advisory-first rollout on whichever gate it adopts; only ``status`` never fails on findings
        and therefore refuses the flag.
        """
        inputs = action_runner.read_inputs(
            {"PYDEPRECATE_ACTION_SUBCOMMAND": subcommand, "PYDEPRECATE_ACTION_EXIT_ZERO": "true"}
        )
        assert inputs.exit_zero is True

    @pytest.mark.parametrize(
        ("name", "raw"),
        [
            pytest.param("install", "yes", id="install-yes"),
            pytest.param("install", "2", id="install-two"),
            pytest.param("summary", "on", id="summary-on"),
            pytest.param("summary", "truee", id="summary-typo"),
            pytest.param("exit-zero", "-1", id="exit-zero-negative"),
        ],
    )
    def test_rejects_unknown_boolean_spelling(self, name: str, raw: str) -> None:
        """A boolean input that is not ``true``/``false``/``1``/``0`` is a usage error that quotes the bad value.

        ``install: yes`` would otherwise be read as false (or true) by guesswork and silently change what the step
        does; the error must name the input and echo the value so the workflow author can fix the typo.
        """
        variable = action_runner.ENV_PREFIX + name.upper().replace("-", "_")
        message = f"input `{name}` must be true, false, 1 or 0, got {raw!r}"
        with pytest.raises(ValueError, match=re.escape(message)):
            action_runner.read_inputs({variable: raw})

    @pytest.mark.parametrize(
        "token",
        ["--", "-h", "--help", "--report-json", "--report-json=out.json", "--report_json", "--report_json=out.json"],
    )
    def test_rejects_reserved_args_token(self, token: str) -> None:
        """An ``args`` token that would swallow the runner's options or skip the scan is a usage error naming it.

        A workflow author appends a ``--`` separator, ``--help`` or a ``--report-json`` copied from a local command
        line. Fire would drop the options the action appends after a separator, a help flag would end the run green
        without a scan, and a second report option would silently divert the report the job summary is read from;
        each is refused up front, wherever it sits among the other flags, and the error quotes the token.
        """
        message = f"input `args` must not contain {token!r}: "
        with pytest.raises(ValueError, match=re.escape(message)):
            action_runner.read_inputs({"PYDEPRECATE_ACTION_ARGS": f"--version 2.0 {token}"})

    @pytest.mark.parametrize("path", ["--help", "-h", "--exit-zero", "--version", "  -h  "])
    def test_rejects_option_like_path(self, tmp_path: Path, path: str) -> None:
        """A ``path`` starting with ``-`` fails the step with exit code 2 instead of reaching the CLI as an option.

        The ``path`` input is often built from event data such as a branch name or a dispatch input. A value like
        ``--help`` would make the CLI print its help and exit 0 without scanning anything, silently turning the
        gate green; the action must reject it as a usage error that names the input.
        """
        result = _run_action(tmp_path, subcommand="check", path=path)
        assert result.returncode == 2, result.stdout + result.stderr
        assert "::error title=pyDeprecate::input `path` must not start with `-`" in result.stdout
        assert "Scanning:" not in result.stdout

    def test_args_keep_quoted_values_together(self, tmp_path: Path) -> None:
        """Shell-style quoting in ``args`` keeps a comma-separated value as one CLI argument.

        A maintainer excludes several module patterns with ``args: --exclude='a.b,c.*'``. The quotes must be honoured
        so the CLI receives a single ``--exclude`` value, exactly as it would from a shell.
        """
        result = _run_action(tmp_path, subcommand="check", path="tests.collection_chains", args="--exclude='a.b,c.*'")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Exclude: a.b, c.* (flag)" in result.stdout

    def test_empty_path_scans_src_package(self, tmp_path: Path) -> None:
        """With no ``path`` input the CLI's own discovery picks the single package under ``src/``.

        Most src-layout projects need no ``path`` at all. The runner must omit the positional path instead of passing
        an empty string, so the CLI finds ``src/mypkg`` by itself and reports its misconfigured wrapper.
        """
        package = tmp_path / "project" / "src" / "mypkg"
        package.mkdir(parents=True)
        shutil.copy(_MISCONFIGURED_MODULE, package / "__init__.py")
        result = _run_runner(_runner_env(tmp_path, subcommand="check"), cwd=tmp_path / "project")
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Package: mypkg" in result.stdout


class TestActionInstall:
    """The ``install`` step and its report of a pyDeprecate of another version that it replaced."""

    def test_reads_version_from_child_metadata(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The installed version is read from the distribution metadata a fresh child interpreter finds.

        Right after pip ran, the runner's own process may still hold a stale view of the installed distributions; a
        child interpreter started with the job's Python reads the metadata pip just wrote. Here the only
        ``pyDeprecate`` metadata on the child's import path declares ``9.9.0``.
        """
        dist_info = tmp_path / "pyDeprecate-9.9.0.dist-info"
        dist_info.mkdir()
        (dist_info / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: pyDeprecate\nVersion: 9.9.0\n", encoding="utf-8"
        )
        monkeypatch.setenv("PYTHONPATH", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        assert action_runner.installed_version(sys.executable) == "9.9.0"

    def test_unstartable_interpreter_has_no_version(self, tmp_path: Path) -> None:
        """A Python that cannot be started reports no installed version instead of raising.

        The job's interpreter is missing (a broken virtualenv); the version query must degrade to "unknown" so the
        install step reaches pip and reports the real failure there.
        """
        assert action_runner.installed_version(str(tmp_path / "missing-python")) is None

    @pytest.mark.parametrize(
        ("before", "after", "warning"),
        [
            pytest.param(
                "0.14.0",
                "0.15.0.dev0",
                "::warning title=pyDeprecate::replaced the installed pyDeprecate 0.14.0 with 0.15.0.dev0",
                id="replaced",
            ),
            pytest.param(
                "0.14.0",
                None,
                "::warning title=pyDeprecate::the version of the pyDeprecate installed from the action ref could not",
                id="unreadable",
            ),
        ],
    )
    def test_warns_on_replaced_or_unreadable_version(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        before: str,
        after: Optional[str],
        warning: str,
    ) -> None:
        """A successful install warns when it replaced another version, or when the new version cannot be read.

        The job had installed pyDeprecate 0.14.0 for its own tests, and the action ref pins a development release.
        The step replaces the job's install for every later step, so it must say so; when the installed version
        cannot be read back at all, the step must say that instead of skipping the check silently.
        """
        versions = iter([before, after])
        monkeypatch.setattr(action_runner, "installed_version", lambda python: next(versions))
        monkeypatch.setattr(action_runner.subprocess, "call", lambda argv: 0)
        code = action_runner.install_package(_REPO_ROOT, sys.executable)
        assert code == 0
        assert warning in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("before", "after"),
        [
            pytest.param("0.15.0.dev0", "0.15.0.dev0", id="same-normalized-version"),
            pytest.param(None, "0.15.0", id="fresh-install"),
        ],
    )
    def test_silent_when_no_other_version_was_replaced(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        before: Optional[str],
        after: str,
    ) -> None:
        """A fresh install, or a reinstall of the same release, raises no warning.

        The checkout's ``__about__.py`` spells a development release ``0.15.0.dev`` while the installed metadata
        records ``0.15.0.dev0``; comparing the source spelling with the metadata used to warn about replacing a
        version with itself. Both sides now come from installed metadata, so the same release compares equal.
        """
        versions = iter([before, after])
        monkeypatch.setattr(action_runner, "installed_version", lambda python: next(versions))
        monkeypatch.setattr(action_runner.subprocess, "call", lambda argv: 0)
        code = action_runner.install_package(_REPO_ROOT, sys.executable)
        assert code == 0
        assert "::warning" not in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("pip_status", "step_code", "exit_text"),
        [
            pytest.param(3, 3, "exit 3", id="pip-error"),
            pytest.param(-15, 143, "exit 143 (killed by SIGTERM)", id="pip-killed"),
        ],
    )
    def test_pip_failure_fails_the_step_before_the_cli_runs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capfd: pytest.CaptureFixture[str],
        pip_status: int,
        step_code: int,
        exit_text: str,
    ) -> None:
        """A failed pip install ends the step with pip's status and an ``::error``, and nothing is scanned.

        The job's Python is externally managed (or a uv venv without pip), so ``pip install`` refuses. Scanning with
        whatever pyDeprecate happens to be importable would audit with an unpinned version; the step must stop and say
        how to opt out with ``install: false``.
        """
        _set_action_env(monkeypatch, subcommand="check", path="tests.collection_chains", install="true")
        _use_repo_checkout(monkeypatch)
        monkeypatch.setattr(action_runner.subprocess, "call", lambda argv: pip_status)
        code = action_runner.main()
        out = capfd.readouterr().out
        assert code == step_code
        assert f"::error title=pyDeprecate::pip install of pyDeprecate failed ({exit_text})." in out
        assert "set `install: false`" in out
        assert "::stop-commands::" not in out
        assert "Scanning:" not in out

    def test_requirement_is_a_file_uri_of_the_action_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """The install requirement names the action checkout as a ``file://`` URI, spaces percent-encoded.

        A self-hosted runner checks the action out below a directory whose name contains a space. A bare path in the
        requirement would be split or misparsed by pip; a PEP 508 direct reference with an encoded URI is not.
        """
        pip_calls = _record_pip_calls(monkeypatch)
        _set_action_env(monkeypatch, subcommand="check", path="tests.collection_chains", install="true")
        _use_repo_checkout(monkeypatch)
        monkeypatch.setenv("GITHUB_ACTION_PATH", str(tmp_path / "my action"))
        code = action_runner.main()
        requirement = pip_calls[0][-1]
        assert code == 0, capfd.readouterr().out
        assert pip_calls[0][:4] == [sys.executable, "-m", "pip", "install"]
        assert requirement.startswith("pyDeprecate[cli,audit] @ file://")
        assert requirement.endswith("/my%20action")
        assert " " not in requirement.split("@ ", 1)[1]

    def test_action_path_defaults_to_the_runner_checkout(
        self, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Without ``GITHUB_ACTION_PATH`` the package is installed from the checkout the runner script lives in.

        Running the script locally (or under ``act``) does not set the variable; the install must still pin the very
        checkout being executed rather than fail or install from an unrelated location.
        """
        pip_calls = _record_pip_calls(monkeypatch)
        _set_action_env(monkeypatch, subcommand="check", path="tests.collection_chains", install="true")
        _use_repo_checkout(monkeypatch)
        monkeypatch.delenv("GITHUB_ACTION_PATH", raising=False)
        code = action_runner.main()
        assert code == 0, capfd.readouterr().out
        assert pip_calls[0][-1] == f"pyDeprecate[cli,audit] @ {_REPO_ROOT.resolve().as_uri()}"


class TestActionSummary:
    """The opt-in ``summary`` input that appends the gate's own deprecation table to the job summary."""

    def test_appends_status_table(self, tmp_path: Path) -> None:
        """``summary: true`` appends a markdown status table after the gate run, without changing its exit code.

        A team wants every CI run to show which deprecations are live. The gate itself passes, and the job summary
        must gain a headed markdown table listing the scanned module's deprecated callables.
        """
        result = _run_action(tmp_path, subcommand="check", path="tests.collection_chains", summary="true")
        summary = _summary_path(tmp_path).read_text(encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
        assert summary.startswith("## pyDeprecate deprecation status")
        assert "| `tests.collection_chains.caller_acc_via_depr_map` |" in summary

    @pytest.mark.parametrize(
        ("subcommand", "args"),
        [
            pytest.param("check", "tests.collection_chains", id="check"),
            pytest.param("chains", "tests.collection_chains --exit-zero", id="chains"),
            pytest.param("status", "tests.collection_chains", id="status"),
            pytest.param("all", "tests.collection_chains --version 1.0 --exit-zero", id="all"),
        ],
    )
    def test_summary_comes_from_the_gate_scan(self, tmp_path: Path, subcommand: str, args: str) -> None:
        """The table comes from the gate's own scan, even when ``args`` names the module positionally.

        A workflow leaves ``path`` empty and passes the module in ``args``. The CLI writes the table of the scan it
        gated on into its report, so no second scan guesses the scope from the arguments: the job summary lists the
        named module, not the repository root.
        """
        result = _run_action(tmp_path, subcommand=subcommand, args=args, summary="true")
        summary = _summary_path(tmp_path).read_text(encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "| `tests.collection_chains.caller_acc_via_depr_map` |" in summary

    @pytest.mark.parametrize("summary", [True, False])
    def test_crashed_scan_warns_of_missing_report(self, tmp_path: Path, summary: bool) -> None:
        """A scan that crashes writes no report, so the gate fails and a warning says its gaps went unannotated.

        The ``path`` input points at a Python file, which the CLI refuses with exit 1 before scanning. Gap
        annotations are the action's guard against a green step that audited less than it shows, so their loss is
        flagged even with ``summary`` off; with it on, the summary is skipped, as no table of this run exists.
        """
        result = _run_action(tmp_path, subcommand="check", path="tests/collection_chains.py", summary=str(summary))
        assert result.returncode == 1, result.stdout + result.stderr
        assert "::error title=pyDeprecate::pydeprecate check exited 1" in result.stdout
        assert "::warning title=pyDeprecate::the gate wrote no pyDeprecate report, so its gaps are not annotated" in (
            result.stdout
        )
        assert ("the job summary is skipped" in result.stdout) is summary
        assert not _summary_path(tmp_path).exists()

    @pytest.mark.parametrize("separator", ["=", " "])
    def test_user_output_is_kept(self, tmp_path: Path, separator: str) -> None:
        """A user ``--output`` in ``args`` is still written, next to the job summary the runner writes.

        A team publishes the status table as a build artifact with ``args: --output report.md`` and also turns on
        ``summary``. The runner's report goes through ``--report-json``, so the user's ``--output`` is left alone and
        both the artifact and the job summary hold the table.
        """
        report = tmp_path / "artifacts" / "report.md"
        args = f"--output{separator}{shlex.quote(str(report))}"
        result = _run_action(tmp_path, subcommand="status", path="tests.collection_chains", args=args, summary="true")
        summary = _summary_path(tmp_path).read_text(encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "| `tests.collection_chains.caller_acc_via_depr_map` |" in report.read_text(encoding="utf-8")
        assert "| `tests.collection_chains.caller_acc_via_depr_map` |" in summary

    def test_user_report_option_is_rejected(self, tmp_path: Path) -> None:
        """A ``--report-json`` in ``args`` fails the step with exit code 2 instead of being dropped without a word.

        A workflow copies a local command line that already writes a JSON report. The runner owns the report option
        (the CLI keeps only one), so honouring the user's path would starve the job summary and ignoring it would hide
        that the file never appears; the step stops with a usage error that names the input before anything runs.
        """
        elsewhere = tmp_path / "elsewhere.json"
        result = _run_action(
            tmp_path,
            subcommand="check",
            path="tests.collection_chains",
            args=f"--report-json={elsewhere}",
            summary="true",
        )
        assert result.returncode == 2, result.stdout + result.stderr
        assert "::error title=pyDeprecate::input `args` must not contain '--report-json=" in result.stdout
        assert "Scanning:" not in result.stdout
        assert not elsewhere.exists()
        assert not _summary_path(tmp_path).exists()

    def test_failing_gate_keeps_exit_code(self, tmp_path: Path) -> None:
        """A failing gate still gets its summary table, and the step keeps the gate's exit code 1.

        The summary is informational: writing it must never turn a failing gate green or a passing one red.
        """
        result = _run_action(tmp_path, subcommand="check", path="tests.collection_misconfigured", summary="true")
        summary = _summary_path(tmp_path).read_text(encoding="utf-8")
        assert result.returncode == 1, result.stdout + result.stderr
        assert summary.startswith("## pyDeprecate deprecation status")

    def test_table_is_appended_to_existing_summary_content(self, tmp_path: Path) -> None:
        """The status table is appended after what earlier steps of the job already wrote to the job summary.

        The job summary file is shared by every step of the job; a step that truncated it would erase the test report
        written before it, so the earlier content must survive in front of the new ``## pyDeprecate`` section.
        """
        summary_file = _summary_path(tmp_path)
        summary_file.write_text("## Earlier step\n\nunit tests: 42 passed\n", encoding="utf-8")
        result = _run_action(tmp_path, subcommand="check", path="tests.collection_chains", summary="true")
        summary = summary_file.read_text(encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
        assert summary.startswith("## Earlier step\n\nunit tests: 42 passed\n")
        assert "## pyDeprecate deprecation status" in summary

    @pytest.mark.parametrize(
        ("path", "exit_code"),
        [
            pytest.param("tests.collection_chains", 0, id="passing-gate"),
            pytest.param("tests.collection_misconfigured", 1, id="failing-gate"),
        ],
    )
    def test_unwritable_job_summary_only_warns(self, tmp_path: Path, path: str, exit_code: int) -> None:
        """A job-summary target that cannot be written leaves the gate's exit code unchanged and warns.

        ``GITHUB_STEP_SUMMARY`` names a directory (a misconfigured self-hosted runner), so appending fails with
        ``OSError``. The summary is informational: the passing gate stays green, the failing gate keeps exit 1.
        """
        env = _runner_env(tmp_path, subcommand="check", path=path, summary="true")
        result = _run_runner({**env, "GITHUB_STEP_SUMMARY": str(tmp_path)})
        assert result.returncode == exit_code, result.stdout + result.stderr
        assert "::warning title=pyDeprecate::could not write the job summary: " in result.stdout

    @pytest.mark.parametrize("subcommand", ["check", "status"])
    def test_unwritable_report_keeps_gate_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], subcommand: str
    ) -> None:
        """A report file that cannot be written leaves a passing gate green and only skips the summary.

        The runner's report location is unusable (here its directory is a regular file; on a runner, a full disk). The
        CLI reports a failed report write as an advisory, so neither ``check`` nor ``status`` fails on it; the
        summary is skipped with a warning.
        """
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("", encoding="utf-8")
        _use_repo_checkout(monkeypatch)
        inputs = action_runner.read_inputs(
            {
                "PYDEPRECATE_ACTION_SUBCOMMAND": subcommand,
                "PYDEPRECATE_ACTION_PATH": "tests.collection_chains",
                "PYDEPRECATE_ACTION_SUMMARY": "true",
            }
        )
        env = {"GITHUB_STEP_SUMMARY": str(_summary_path(tmp_path))}
        code = action_runner.run_gate(inputs, sys.executable, blocker / "report.json", env)
        out = capsys.readouterr().out
        assert code == 0
        assert "::warning title=pyDeprecate::the gate wrote no pyDeprecate report" in out
        assert "::error" not in out

    @pytest.mark.parametrize(
        ("content", "warning"),
        [
            pytest.param(b"{not json", "could not read the pyDeprecate report: ", id="malformed-json"),
            pytest.param(b"\xff\xfe\x00", "could not read the pyDeprecate report: ", id="not-utf8"),
            pytest.param(
                json.dumps({"schema": action_runner.REPORT_SCHEMA + 1, "gaps": [], "table": "t"}).encode(),
                f"the pyDeprecate report is not of layout version {action_runner.REPORT_SCHEMA}; ignoring it",
                id="other-layout-version",
            ),
            pytest.param(
                json.dumps({"gaps": [], "table": "t"}).encode(),
                f"the pyDeprecate report is not of layout version {action_runner.REPORT_SCHEMA}; ignoring it",
                id="no-layout-version",
            ),
            pytest.param(
                b"[]",
                f"the pyDeprecate report is not of layout version {action_runner.REPORT_SCHEMA}; ignoring it",
                id="not-an-object",
            ),
        ],
    )
    def test_unusable_report_is_ignored_with_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], content: bytes, warning: str
    ) -> None:
        """A report the runner cannot trust is dropped with a warning instead of crashing the step.

        A job pins an older or newer pyDeprecate than the action expects, or the report was cut short: the file exists
        but is not JSON, not UTF-8 text, not an object, or of another layout version. Reading gap annotations or the
        table out of it could mislead, so the runner ignores it and says why.
        """
        report_file = tmp_path / "report.json"
        report_file.write_bytes(content)
        report = action_runner.read_report(report_file)
        out = capsys.readouterr().out
        assert report is None
        assert f"::warning title=pyDeprecate::{warning}" in out

    def test_missing_report_is_ignored_silently(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A report file that was never written yields no report and no warning of its own.

        The caller, not the reader, explains a missing report, because only it knows whether the gate crashed.
        """
        report = action_runner.read_report(tmp_path / "report.json")
        assert report is None
        assert capsys.readouterr().out == ""

    @pytest.mark.parametrize(
        "report",
        [
            pytest.param({"schema": action_runner.REPORT_SCHEMA, "gaps": []}, id="table-absent"),
            pytest.param({"schema": action_runner.REPORT_SCHEMA, "gaps": [], "table": None}, id="table-null"),
            pytest.param({"schema": action_runner.REPORT_SCHEMA, "gaps": [], "table": ["| a |"]}, id="table-list"),
        ],
    )
    def test_report_without_table_skips_summary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        report: dict[str, Any],
    ) -> None:
        """A valid report that holds no table text skips the job summary with a warning and keeps the gate result.

        A future CLI may drop or retype the ``table`` field while keeping the layout version. The step must stay
        green for a passing gate and say that the summary could not be written.
        """
        report_file = tmp_path / "report.json"
        report_file.write_text(json.dumps(report), encoding="utf-8")
        monkeypatch.setattr(action_runner, "run_cli", lambda argv: 0)
        inputs = action_runner.read_inputs(
            {"PYDEPRECATE_ACTION_SUBCOMMAND": "check", "PYDEPRECATE_ACTION_SUMMARY": "true"}
        )
        env = {"GITHUB_STEP_SUMMARY": str(_summary_path(tmp_path))}
        code = action_runner.run_gate(inputs, sys.executable, report_file, env)
        out = capsys.readouterr().out
        assert code == 0
        assert "::warning title=pyDeprecate::the gate's report holds no deprecation table" in out
        assert "skipping the job summary" in out
        assert not _summary_path(tmp_path).exists()

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param(b"{not json", id="malformed-json"),
            pytest.param(json.dumps({"schema": action_runner.REPORT_SCHEMA + 1}).encode(), id="other-layout-version"),
        ],
    )
    def test_unusable_report_keeps_gate_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], content: bytes
    ) -> None:
        """An unusable report leaves a passing gate green, skips annotations and the summary, and says so.

        The gate passed and wrote a file the runner cannot use. The step keeps the gate's exit code 0, warns that the
        report is unusable, and warns that its gaps and the job summary went unchecked.
        """
        report_file = tmp_path / "report.json"
        report_file.write_bytes(content)
        monkeypatch.setattr(action_runner, "run_cli", lambda argv: 0)
        inputs = action_runner.read_inputs(
            {"PYDEPRECATE_ACTION_SUBCOMMAND": "check", "PYDEPRECATE_ACTION_SUMMARY": "true"}
        )
        env = {"GITHUB_STEP_SUMMARY": str(_summary_path(tmp_path))}
        code = action_runner.run_gate(inputs, sys.executable, report_file, env)
        out = capsys.readouterr().out
        assert code == 0
        assert "::warning title=pyDeprecate::the gate wrote no pyDeprecate report, so its gaps are not annotated" in out
        assert "the job summary is skipped" in out
        assert "::error" not in out
        assert not _summary_path(tmp_path).exists()

    @pytest.mark.parametrize(
        "gaps",
        [
            pytest.param(None, id="gaps-null"),
            pytest.param(["not a gap", 3, None], id="no-gap-objects"),
        ],
    )
    def test_report_with_unusable_gaps_annotates_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], gaps: object
    ) -> None:
        """Report ``gaps`` that are missing or not objects raise no annotation and leave the gate result alone.

        Only the CLI's own gap objects are worth quoting; a retyped field must not crash the step or invent warnings.
        """
        report_file = tmp_path / "report.json"
        report_file.write_text(json.dumps({"schema": action_runner.REPORT_SCHEMA, "gaps": gaps}), encoding="utf-8")
        monkeypatch.setattr(action_runner, "run_cli", lambda argv: 0)
        inputs = action_runner.read_inputs({"PYDEPRECATE_ACTION_SUBCOMMAND": "check"})
        code = action_runner.run_gate(inputs, sys.executable, report_file, {})
        assert code == 0
        assert "::warning" not in capsys.readouterr().out

    @pytest.mark.parametrize("summary", [True, False])
    def test_unusable_runner_temp_runs_gate_without_report(self, tmp_path: Path, summary: bool) -> None:
        """A scratch directory that cannot be created leaves the gate result alone and warns about the lost report.

        ``RUNNER_TEMP`` names a directory that does not exist (a misconfigured self-hosted runner), so the private
        report directory cannot be made. The gate still runs and passes; gaps are not annotated, and with ``summary``
        on, a second warning says the job summary is skipped.
        """
        env = _runner_env(tmp_path, subcommand="check", path="tests.collection_chains", summary=str(summary))
        result = _run_runner({**env, "RUNNER_TEMP": str(tmp_path / "missing")})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning title=pyDeprecate::could not create a temporary directory for the report (" in result.stdout
        assert ("::warning title=pyDeprecate::no pyDeprecate report is available" in result.stdout) is summary
        assert not _summary_path(tmp_path).exists()

    def test_leaves_no_file_in_checkout_without_runner_temp(self, tmp_path: Path) -> None:
        """Without ``RUNNER_TEMP`` the report goes to the system temp dir and is removed afterwards.

        Running the action locally (e.g. with ``act``) may leave ``RUNNER_TEMP`` unset. The report must not be
        written into the working directory — the user's checkout — and the job summary must still be produced.
        """
        checkout = tmp_path / "checkout"
        system_temp = tmp_path / "system-temp"
        checkout.mkdir()
        system_temp.mkdir()
        env = _runner_env(tmp_path, subcommand="check", path="tests.collection_chains", summary="true")
        env.pop("RUNNER_TEMP")
        env.update(TMPDIR=str(system_temp), TEMP=str(system_temp), TMP=str(system_temp))
        env["PYTHONPATH"] = f"{_REPO_ROOT}{os.pathsep}{env['PYTHONPATH']}"
        result = _run_runner(env, cwd=checkout)
        summary = _summary_path(tmp_path).read_text(encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "| `tests.collection_chains.caller_acc_via_depr_map` |" in summary
        assert list(checkout.iterdir()) == []
        assert list(system_temp.iterdir()) == []

    @pytest.mark.parametrize("summary", ["true", "false"])
    def test_removes_its_temporary_directory(self, tmp_path: Path, summary: str) -> None:
        """The scratch directory holding the report is removed from ``RUNNER_TEMP`` once the step ends.

        Self-hosted runners reuse ``RUNNER_TEMP`` across jobs; every step writes a report (gaps are annotated from it
        even without ``summary``), so every step must clean up after itself.
        """
        result = _run_action(tmp_path, subcommand="check", path="tests.collection_chains", summary=summary)
        assert result.returncode == 0, result.stdout + result.stderr
        assert list(tmp_path.glob("pydeprecate-*")) == []

    def test_skipped_after_usage_error(self, tmp_path: Path) -> None:
        """A CLI usage error (exit 2) writes no summary and no warning about it, since the requested scan never ran.

        A misspelled flag in ``args`` makes the CLI exit 2; the error annotation already explains the step, and a
        table of any other scan would misrepresent what the step checked.
        """
        result = _run_action(
            tmp_path, subcommand="check", path="tests.collection_chains", args="--verison 2.0", summary="true"
        )
        assert result.returncode == 2, result.stdout + result.stderr
        assert not _summary_path(tmp_path).exists()
        assert "::warning" not in result.stdout

    def test_warns_without_summary_file(self, tmp_path: Path) -> None:
        """Outside GitHub Actions (no ``GITHUB_STEP_SUMMARY``) the summary is skipped with a warning only.

        Running the action locally (e.g. with ``act``) may leave the summary variable empty; the gate result must be
        unaffected and the skip must be visible.
        """
        env = _runner_env(tmp_path, subcommand="check", path="tests.collection_chains", summary="true")
        result = _run_runner({**env, "GITHUB_STEP_SUMMARY": ""})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning title=pyDeprecate::`summary: true` needs GITHUB_STEP_SUMMARY" in result.stdout

    @pytest.mark.parametrize("summary", [True, False])
    def test_cli_without_report_option_still_gates(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], summary: bool
    ) -> None:
        """An installed pyDeprecate older than ``--report-json`` still runs the gate, with a warning about what is lost.

        A job sets ``install: false`` and keeps an older pinned release. Passing it ``--report-json`` would make the
        gate exit 2; instead the gate runs unchanged, one warning says gaps and the summary are unavailable, and with
        ``summary`` on a second one says the summary was skipped.
        """
        _use_repo_checkout(monkeypatch)
        _set_action_env(
            monkeypatch, subcommand="check", path="tests.collection_chains", install="false", summary=str(summary)
        )
        monkeypatch.setattr(action_runner, "report_supported", lambda python: False)
        code = action_runner.main()
        out = capsys.readouterr().out
        assert code == 0
        assert "::warning title=pyDeprecate::the installed pyDeprecate predates `--report-json`" in out
        assert ("::warning title=pyDeprecate::no pyDeprecate report is available" in out) is summary

    @pytest.mark.parametrize(
        ("program", "supported"),
        [
            pytest.param("raise SystemExit(3)", False, id="option-absent"),
            pytest.param("import deprecate_not_installed_xyz", True, id="package-missing"),
        ],
    )
    def test_probe_blames_the_version_only_when_option_is_absent(
        self, monkeypatch: pytest.MonkeyPatch, program: str, supported: bool
    ) -> None:
        """Only an importable CLI without the option counts as too old; a missing package is left to the gate.

        A job sets ``install: false`` but never installed pyDeprecate. Warning that it "predates ``--report-json``"
        would point at the wrong fix; the gate run fails with the real ``ModuleNotFoundError`` instead.
        """
        monkeypatch.setattr(action_runner, "_REPORT_PROBE", program)
        assert action_runner.report_supported(sys.executable) is supported

    def test_probe_finds_report_option_in_checkout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The capability probe answers yes for the pyDeprecate of this checkout.

        A probe that looked for the wrong name would silently turn off gap annotations and the summary for every
        user of the action.
        """
        _use_repo_checkout(monkeypatch)
        assert action_runner.report_supported(sys.executable) is True


class TestActionOutputSafety:
    """Annotations and workflow-command handling around the scanned package's output."""

    def test_unterminated_stdout_keeps_resume_command_separate(self, capfd: pytest.CaptureFixture[str]) -> None:
        """The resume token starts on its own line even after unterminated child output.

        An imported package prints a progress fragment and exits before finishing its line. A token appended to that
        text is not a workflow command, so later annotations would remain disabled.
        """
        action_runner.run_cli([sys.executable, "-c", "import sys;sys.stdout.write('partial');sys.stdout.flush()"])
        action_runner.annotate("error", "later annotation")
        output = capfd.readouterr().out
        token = _fence_token(output)
        assert output.splitlines()[-3:] == ["partial", f"::{token}::", "::error title=pyDeprecate::later annotation"]

    def test_startup_stderr_uses_utf8(self, tmp_path: Path) -> None:
        """Child startup diagnostics retain non-ASCII text before the CLI normalizes its streams.

        A job inherits a legacy standard-stream codec, and site initialization emits a diagnostic before cli() can
        reconfigure stderr. The runner starts the CLI with UTF-8 streams, so that text reaches the log exactly.
        """
        (tmp_path / "sitecustomize.py").write_text(
            "import os,sys\n"
            "if sys.argv[0] == '-m':\n"
            "    sys.stderr.write('startup café ' + os.environ['TEST_PRESERVED'] + '\\n')\n",
            encoding="utf-8",
        )
        env = _runner_env(tmp_path, subcommand="check", path="tests.collection_chains")
        env.update(
            PYTHONIOENCODING="cp1252",
            PYTHONPATH=f"{tmp_path}{os.pathsep}{env['PYTHONPATH']}",
            TEST_PRESERVED="sentinel",
        )
        result = _run_runner(env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "startup café sentinel" in result.stderr
        assert "\ufffd" not in result.stderr

    def test_cli_output_stays_between_the_fence_tokens(self, tmp_path: Path) -> None:
        """Everything the audited package prints at import time lies between the stop and the resume token.

        The scan imports the audited package, and a hostile or careless one prints workflow commands of its own
        (``::error``, ``::add-mask``) on stdout and stderr. GitHub honours a command only while commands are enabled,
        so every such line must appear after ``::stop-commands::<token>`` and before the matching ``::<token>::``.
        """
        package = tmp_path / "hostilepkg"
        package.mkdir()
        (package / "__init__.py").write_text(
            "import sys\n"
            "print('::error title=pwned::injected from stdout')\n"
            "print('::add-mask::hunter2')\n"
            "print('::error title=pwned::injected from stderr', file=sys.stderr)\n",
            encoding="utf-8",
        )
        env = _runner_env(tmp_path, subcommand="check", path="hostilepkg")
        result = subprocess.run(  # one merged stream, as the job log interleaves stdout and stderr
            [sys.executable, str(_RUNNER)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            env=env,
            cwd=tmp_path,
            timeout=_RUNNER_TIMEOUT,
        )
        lines = result.stdout.splitlines()
        token = _fence_token(result.stdout)
        resume = lines.index(f"::{token}::")
        injected = [i for i, line in enumerate(lines) if line.startswith(("::error title=pwned", "::add-mask::"))]
        assert result.returncode == 0, result.stdout
        assert lines[0] == f"::stop-commands::{token}"
        assert len(injected) == 3
        assert all(0 < index < resume for index in injected)

    def test_fence_token_is_new_in_every_run(self, tmp_path: Path) -> None:
        """Two runs of the step open their fences with different tokens.

        A constant token would be readable from an earlier log (or the repository) by a hostile package, which could
        then print the resume line itself and re-enable commands for the rest of its output.
        """
        first = _run_action(tmp_path, subcommand="check", path="tests.collection_chains")
        second = _run_action(tmp_path, subcommand="check", path="tests.collection_chains")
        assert first.returncode == second.returncode == 0, first.stdout + second.stdout
        assert _fence_token(first.stdout) != _fence_token(second.stdout)

    def test_annotation_of_a_failing_scan_follows_the_resume_token(self, tmp_path: Path) -> None:
        """The error annotation of a failing gate is printed after commands are resumed, so it takes effect.

        A contributor's misconfigured wrapper fails the gate with exit 1. The ``::error`` the runner adds for it is a
        workflow command; printed between the tokens it would be swallowed and the failure would show no annotation.
        """
        result = _run_action(tmp_path, subcommand="check", path="tests.collection_misconfigured")
        lines = result.stdout.splitlines()
        resume = lines.index(f"::{_fence_token(result.stdout)}::")
        annotation = lines.index("::error title=pyDeprecate::pydeprecate check exited 1")
        assert result.returncode == 1, result.stdout + result.stderr
        assert annotation > resume

    @pytest.mark.parametrize(
        ("argv_tail", "error"),
        [
            pytest.param("missing-executable", action_runner.CliStartError, id="unstartable-executable"),
            pytest.param("nul\0byte", ValueError, id="nul-in-argument"),
        ],
    )
    def test_commands_resume_when_the_cli_process_cannot_be_created(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], argv_tail: str, error: type[Exception]
    ) -> None:
        """Workflow commands are resumed even when starting the CLI raises, whatever the exception type.

        The job's interpreter path is wrong, or ``args`` smuggles a NUL byte that the operating system cannot pass on;
        either way the process is never created. The stop token is already out, so the resume token must follow it
        directly or every later annotation of the step would be swallowed.
        """
        with pytest.raises(error):
            action_runner.run_cli([str(tmp_path / argv_tail)])
        lines = capsys.readouterr().out.splitlines()
        token = _fence_token("\n".join(lines))
        assert lines == [f"::stop-commands::{token}", "", f"::{token}::"]

    def test_unstartable_cli_is_annotated_after_commands_resume(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A CLI that cannot be started fails the step with an ``::error`` printed after the fence is closed.

        The job's interpreter disappears or is not executable (a broken virtualenv), so starting the CLI raises
        ``OSError``. The runner must still resume workflow commands and then print an error annotation; printed while
        commands are stopped, the annotation would be swallowed and the failure would show no reason.
        """
        monkeypatch.setattr(sys, "executable", str(tmp_path / "missing-python"))
        monkeypatch.setenv("PYDEPRECATE_ACTION_SUBCOMMAND", "check")
        monkeypatch.setenv("PYDEPRECATE_ACTION_INSTALL", "false")
        monkeypatch.setenv("PYDEPRECATE_ACTION_SUMMARY", "false")
        code = action_runner.main()
        lines = capsys.readouterr().out.splitlines()
        token = re.fullmatch(r"::stop-commands::([0-9a-f]{32})", lines[0])
        assert code == 1
        assert token is not None
        assert lines[1] == ""
        assert lines[2] == f"::{token.group(1)}::"
        assert lines[3].startswith("::error title=pyDeprecate::could not start the pyDeprecate CLI with ")

    def test_warns_when_version_undetected(self, tmp_path: Path) -> None:
        """A package whose version cannot be detected passes ``expiry`` with a warning annotation.

        The package is neither installed nor declares a static version, so removal deadlines cannot be checked and
        the CLI exits 0. That green result must not be silent: the runner raises a warning annotation explaining
        how to provide the version.
        """
        package = tmp_path / "mypkg"
        package.mkdir()
        shutil.copy(_MISCONFIGURED_MODULE, package / "__init__.py")
        result = _run_runner(_runner_env(tmp_path, subcommand="expiry", path="mypkg"), cwd=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning title=pyDeprecate::version not resolved (1): " in result.stdout
        assert result.stdout.count("::warning") == 1

    @pytest.mark.parametrize("broken_modules", [1, 2, 3])
    def test_warns_when_plain_directory_module_fails(self, tmp_path: Path, broken_modules: int) -> None:
        """A plain-directory scan that cannot import some modules passes with one warning counting them all.

        A project without ``__init__.py`` points ``path`` at a directory of scripts, some of which raise at import
        time. The CLI records one ``module not audited`` gap per script and exits 0, so the runner must raise a
        single warning stating how many modules were not audited instead of leaving a silent green step.
        """
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        for index in range(broken_modules):
            (scripts / f"broken_script_{index}.py").write_text(
                'raise RuntimeError("needs a database")\n', encoding="utf-8"
            )
        result = _run_runner(_runner_env(tmp_path, subcommand="check", path="scripts"), cwd=tmp_path)
        warning = f"::warning title=pyDeprecate::module not audited ({broken_modules}): "
        assert result.returncode == 0, result.stdout + result.stderr
        assert warning in result.stdout
        assert result.stdout.count("::warning") == 1

    def test_warns_when_plain_directory_has_nested_files(self, tmp_path: Path) -> None:
        """A plain directory whose nested ``.py`` files are skipped passes with a warning annotation.

        A project keeps scripts in ``scripts/`` and helpers in ``scripts/lib/``, with no ``__init__.py`` anywhere. The
        scan only reads the top level, so the step is green while ``lib/`` was never audited; the runner must say so.
        """
        scripts = tmp_path / "scripts"
        (scripts / "lib").mkdir(parents=True)
        (scripts / "top.py").write_text("VALUE = 1\n", encoding="utf-8")
        (scripts / "lib" / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
        result = _run_runner(_runner_env(tmp_path, subcommand="check", path="scripts"), cwd=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning title=pyDeprecate::nested files not audited (1): " in result.stdout

    def test_clean_scan_raises_no_gap_warning(self, tmp_path: Path) -> None:
        """A scan that audited everything prints no gap line and raises no warning annotation.

        The negative control of the gap warnings: a maintainer's clean run must not be decorated with warnings, or
        the real ones would be lost among them.
        """
        result = _run_action(tmp_path, subcommand="check", path="tests.collection_chains")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "pydeprecate gap:" not in result.stderr
        assert "::warning" not in result.stdout

    def test_gap_line_survives_a_narrow_terminal(self, tmp_path: Path) -> None:
        """A gap message stays on one line however narrow the terminal, so its warning annotation is still raised.

        Rich wraps console output at the terminal width (``COLUMNS``). A plain-directory script exits at import time,
        and the job's ``COLUMNS`` is narrower than the message; the gap line in the log must stay whole and greppable,
        and the annotation comes from the report regardless of the console width.
        """
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "exiting_script.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
        env = {**_runner_env(tmp_path, subcommand="check", path="scripts"), "COLUMNS": "20"}
        result = _run_runner(env, cwd=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            "pydeprecate gap: module not audited: exiting_script: module-level code exited (not a library module)"
        ) in result.stderr.splitlines()
        assert "::warning title=pyDeprecate::module not audited (1): " in result.stdout

    @pytest.mark.parametrize(
        ("subcommand", "args"),
        [
            pytest.param("check", "", id="check"),
            pytest.param("expiry", "--version 1.0", id="expiry"),
            pytest.param("chains", "", id="chains"),
            pytest.param("all", "--version 1.0", id="all"),
        ],
    )
    def test_warns_on_skipped_submodule_despite_ignore_filter(self, tmp_path: Path, subcommand: str, args: str) -> None:
        """A submodule that fails to import raises a warning annotation even when the package ignores warnings.

        The audited package calls ``warnings.filterwarnings("ignore")`` in its ``__init__`` (common in libraries
        that hide third-party noise), which also silences the scan's ``audit: skipped`` warning. One submodule needs
        an optional dependency missing from the CI job, so it is never audited; the step passes, and the runner must
        still raise the not-audited warning from the gap the CLI recorded, for every gate's scan path.
        """
        package = tmp_path / "quietpkg"
        package.mkdir()
        (package / "__init__.py").write_text('import warnings\nwarnings.filterwarnings("ignore")\n', encoding="utf-8")
        (package / "broken.py").write_text('raise RuntimeError("optional dependency missing")\n', encoding="utf-8")
        result = _run_runner(_runner_env(tmp_path, subcommand=subcommand, path="quietpkg", args=args), cwd=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "pydeprecate gap: module not audited: quietpkg.broken: RuntimeError(" in result.stderr
        assert "::warning title=pyDeprecate::module not audited (1): " in result.stdout
        assert "%0Aquietpkg.broken: RuntimeError(" in result.stdout

    @pytest.mark.parametrize(
        ("returncode", "code", "description"),
        [
            pytest.param(0, 0, "0", id="passed"),
            pytest.param(1, 1, "1", id="findings"),
            pytest.param(-15, 143, "143 (killed by SIGTERM)", id="sigterm"),
            pytest.param(-99, 227, "227 (killed by signal 99)", id="unnamed-signal"),
        ],
    )
    def test_exit_status_maps_signal_death_like_a_shell(self, returncode: int, code: int, description: str) -> None:
        """A return code of ``-N`` (killed by signal N) becomes the shell's ``128 + N`` and names the signal.

        ``sys.exit(-15)`` would end the step with 241 and the annotation would read "exited -15"; the step must end
        with 143, the status a shell reports for SIGTERM, while ordinary exit codes pass through unchanged.
        """
        assert action_runner.exit_status(returncode) == (code, description)

    def test_signal_killed_gate_exits_like_a_shell(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A gate run killed by a signal ends the step with ``128 + N`` and an annotation naming the signal.

        The runner's CLI process is terminated mid-scan (a runner shutting down, an OOM kill); the step result and its
        error annotation must use the shell convention instead of a negative return code.
        """
        monkeypatch.setattr(action_runner, "run_cli", lambda argv: -15)
        inputs = action_runner.read_inputs({"PYDEPRECATE_ACTION_SUBCOMMAND": "check"})
        code = action_runner.run_gate(inputs, sys.executable, None, {})
        assert code == 143
        assert "::error title=pyDeprecate::pydeprecate check exited 143 (killed by SIGTERM)" in capsys.readouterr().out

    @pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason="needs SIGKILL; only POSIX kills processes by signal")
    def test_real_signal_death_maps_to_shell_status(self) -> None:
        """A CLI process really killed by SIGKILL is reported with the shell's status 137.

        The child kills itself with SIGKILL, so the runner sees the negative return code a real OOM kill produces.
        """
        returncode = action_runner.run_cli(
            [
                sys.executable,
                "-c",
                "import os, signal; os.kill(os.getpid(), signal.SIGKILL)",
            ]
        )
        assert action_runner.exit_status(returncode) == (137, "137 (killed by SIGKILL)")

    def test_package_output_is_not_counted_as_a_gap(self, tmp_path: Path) -> None:
        """Text the audited package prints itself raises no gap warning, even when it quotes a gap message.

        A package logs its own diagnostics to stderr at import time: one line reads like an older pyDeprecate advisory,
        another embeds a gap line inside a log record. Gaps come from the CLI's report, never from console text, so
        the clean scan must pass without a warning annotation.
        """
        package = tmp_path / "chattypkg"
        package.mkdir()
        (package / "__init__.py").write_text(
            "import sys\n"
            "print('Could not scan chattypkg.extra: optional dependency missing', file=sys.stderr)\n"
            "print('log: pydeprecate gap: module not audited: chattypkg.extra', file=sys.stderr)\n",
            encoding="utf-8",
        )
        result = _run_runner(_runner_env(tmp_path, subcommand="check", path="chattypkg"), cwd=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "log: pydeprecate gap: module not audited: chattypkg.extra" in result.stderr
        assert "::warning" not in result.stdout

    def test_gaps_of_one_kind_annotate_once(self, capsys: pytest.CaptureFixture[str]) -> None:
        """All gaps of one kind raise a single annotation with their count, the hint and the first few details.

        A run that skips five submodules must not flood the job's annotation list with five near-identical warnings;
        one names the kind and count, quotes the CLI's hint and the first details, and points to the log for the rest.
        """
        gaps = [
            {"kind": "module not audited", "detail": f"pkg.mod{index}: ImportError()", "hint": "install deps"}
            for index in range(action_runner.GAP_DETAILS_SHOWN + 2)
        ]
        action_runner.annotate_gaps([*gaps, {"kind": "packaging missing", "detail": "line one\nline two", "hint": "h"}])
        assert capsys.readouterr().out.splitlines() == [
            (
                "::warning title=pyDeprecate::module not audited (5): install deps%0Apkg.mod0: ImportError()"
                "%0Apkg.mod1: ImportError()%0Apkg.mod2: ImportError()%0A... and 2 more in the log above"
            ),
            "::warning title=pyDeprecate::packaging missing (1): h%0Aline one",
        ]

    @pytest.mark.parametrize(
        ("message", "escaped"),
        [
            pytest.param("50% done", "50%25 done", id="percent"),
            pytest.param("line one\nline two", "line one%0Aline two", id="newline"),
            pytest.param("carriage\rreturn", "carriage%0Dreturn", id="carriage-return"),
        ],
    )
    def test_annotation_message_is_escaped(self, message: str, escaped: str) -> None:
        """Annotation text is escaped so input-derived messages cannot inject a second workflow command.

        Input values can span lines (YAML block scalars); an unescaped newline in an ``::error`` message would start
        a new, attacker-chosen workflow command.
        """
        assert action_runner.escape_data(message) == escaped


class TestActionMetadata:
    """``action.yml`` stays in lockstep with the runner, the CLI and the pre-commit hook manifest."""

    def test_is_composite_with_shell_on_every_run_step(self) -> None:
        """The action is a composite action, and every ``run`` step declares its ``shell`` as GitHub requires.

        A composite ``run`` step without ``shell`` is rejected when the workflow loads, breaking every caller.
        """
        runs = _load_action()["runs"]
        assert runs["using"] == "composite"
        assert all("shell" in step for step in runs["steps"] if "run" in step)

    def test_inputs_never_interpolated_into_scripts(self) -> None:
        """No ``${{ }}`` expression appears inside a ``run`` script; inputs reach the runner only through ``env``.

        Interpolating an input into script text lets a value such as a pull-request title inject code (GitHub's
        script-injection guidance); environment variables are passed as data.
        """
        steps = _load_action()["runs"]["steps"]
        assert not [step["run"] for step in steps if "${{" in step.get("run", "")]

    def test_input_defaults_match_runner(self) -> None:
        """The inputs declared in ``action.yml`` and their defaults are exactly the runner's inputs and defaults.

        An input added to ``action.yml`` but unknown to the runner would be silently ignored; a mismatched default
        would make the action behave differently from its documentation.
        """
        defaults = {name: str(spec.get("default", "")) for name, spec in _load_action()["inputs"].items()}
        assert defaults == action_runner.INPUT_DEFAULTS

    def test_step_env_wires_every_input_to_the_runner(self) -> None:
        """Each ``env`` entry of the action step delivers its input's value to the matching runner input.

        The step env is built from ``action.yml`` itself: every variable receives the distinct, non-default value of
        the input its expression names. If the runner read ``summary`` from the variable that carries ``install`` (or
        any variable were missing or misnamed), the parsed inputs would differ from what the workflow author set.
        """
        distinct = {
            "subcommand": "check",
            "path": "pkg/path",
            "args": "--flag value",
            "exit-zero": "true",
            "install": "false",
            "summary": "true",
        }
        step_env = next(step for step in _load_action()["runs"]["steps"] if "run" in step)["env"]
        delivered = {variable: distinct[_input_named_by(expression)] for variable, expression in step_env.items()}
        assert set(distinct) == set(action_runner.INPUT_DEFAULTS)
        assert action_runner.read_inputs(delivered) == action_runner.ActionInputs(
            subcommand="check",
            path="pkg/path",
            args=("--flag", "value"),
            exit_zero=True,
            install=False,
            summary=True,
        )

    def test_subcommands_match_cli(self) -> None:
        """The runner accepts exactly the CLI's subcommands.

        A subcommand added to the CLI but not to the action would be rejected as invalid input; one removed from
        the CLI would fail only at run time.
        """
        cli_subcommands = {name[len("cmd_") :] for name in dir(cli_commands) if name.startswith("cmd_")}
        assert set(action_runner.SUBCOMMANDS) == cli_subcommands

    @pytest.mark.parametrize("subcommand", action_runner.SUBCOMMANDS)
    def test_exit_zero_rejected_only_where_cli_lacks_it(self, subcommand: str) -> None:
        """``exit-zero: true`` is refused for a subcommand exactly when its CLI function has no ``exit_zero`` flag.

        The runner would otherwise forward ``--exit-zero`` to a subcommand that exits 2 on it, or refuse a flag the
        CLI accepts; if a subcommand gains or drops the flag, the runner's rule must follow.
        """
        cli_has_flag = "exit_zero" in inspect.signature(getattr(cli_commands, f"cmd_{subcommand}")).parameters
        env = {"PYDEPRECATE_ACTION_SUBCOMMAND": subcommand, "PYDEPRECATE_ACTION_EXIT_ZERO": "true"}
        assert _is_rejected(env) is (not cli_has_flag)

    def test_report_schema_matches_cli(self) -> None:
        """The runner reads the report layout version the CLI writes.

        A CLI that changed its report layout without the runner following would have every report ignored, silently
        dropping gap annotations and the job summary.
        """
        assert action_runner.REPORT_SCHEMA == cli_report._REPORT_SCHEMA

    def test_install_extras_match_precommit_hooks(self) -> None:
        """The action installs the same extras the pre-commit hooks request.

        Both integrations promise that one pinned ref gives one working CLI; diverging extras would leave one of
        them without ``fire``/``rich`` or without ``[tool.pydeprecate]`` support on Python 3.9-3.10.
        """
        hooks = yaml.safe_load(_HOOKS_YAML.read_text(encoding="utf-8"))
        hook_requirements = {dep for hook in hooks for dep in hook["additional_dependencies"]}
        assert hook_requirements == {f"pyDeprecate[{','.join(action_runner.INSTALL_EXTRAS)}]"}
