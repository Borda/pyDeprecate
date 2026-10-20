"""Integration tests for the GitHub Action — the composite ``action.yml`` and its runner script.

The runner (``.github/scripts/action_runner.py``) is executed as a real subprocess, exactly as the action's
``shell: python`` step runs it: inputs arrive as environment variables, the working directory is the repository
root, and ``PYDEPRECATE_ACTION_INSTALL=false`` skips the pip install so the tests never touch the network.
"""

import importlib.util
import inspect
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from deprecate._cli import commands as cli_commands

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC_DIR = str(_REPO_ROOT / "src")
_RUNNER = _REPO_ROOT / ".github" / "scripts" / "action_runner.py"
_ACTION_YML = _REPO_ROOT / "action.yml"
_HOOKS_YAML = _REPO_ROOT / ".pre-commit-hooks.yaml"
_MISCONFIGURED_MODULE = _REPO_ROOT / "tests" / "collection_misconfigured.py"


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


def _runner_env(tmp_path: Path, **inputs: str) -> dict[str, str]:
    """Build the environment the action step gives the runner: one ``PYDEPRECATE_ACTION_*`` variable per input.

    ``GITHUB_STEP_SUMMARY`` and ``RUNNER_TEMP`` always point into ``tmp_path`` so a test run inside GitHub Actions
    never writes into the real job summary.
    """
    existing_pythonpath = os.environ.get("PYTHONPATH")
    pythonpath = f"{_SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else _SRC_DIR
    env = {
        **os.environ,
        "PYTHONPATH": pythonpath,
        "COLUMNS": "200",
        "GITHUB_ACTION_PATH": str(_REPO_ROOT),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "step_summary.md"),
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
    )


def _run_action(tmp_path: Path, **inputs: str) -> subprocess.CompletedProcess[str]:
    """Run the action runner from the repository root with the given inputs."""
    return _run_runner(_runner_env(tmp_path, **inputs))


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

    @pytest.mark.parametrize(
        ("args", "forwarded"),
        [
            pytest.param(("--exclude=a.b", "--min-grace=1.0"), ["--exclude=a.b"], id="drop-policy-flag"),
            pytest.param(("--exclude", "a.b", "--exit-zero"), ["--exclude", "a.b"], id="separate-value"),
            pytest.param(("--version", "2.0", "--message-required"), ["--version", "2.0"], id="version"),
            pytest.param(("--norecursive",), ["--norecursive"], id="norecursive"),
            pytest.param(("--recursive=false",), ["--recursive=false"], id="recursive-value"),
            pytest.param(("--recursive", "false", "--exit-zero"), ["--recursive", "false"], id="recursive-separate"),
            pytest.param(("--recursive", "src/pkg"), ["--recursive"], id="recursive-bare"),
        ],
    )
    def test_summary_forwards_only_scan_scope_flags(self, args: tuple[str, ...], forwarded: list[str]) -> None:
        """The summary's ``status`` re-scan repeats only the flags that decide what is scanned.

        A gate run with ``args: --exclude=... --min-grace=1.0`` must produce a summary table of the same modules, but
        ``status`` has no ``--min-grace`` and would exit 2 if it were forwarded, silently dropping the summary.
        """
        assert action_runner.summary_forwarded_args(args) == forwarded


class TestActionSummary:
    """The opt-in ``summary`` input that appends a deprecation status table to the job summary."""

    def test_appends_status_table(self, tmp_path: Path) -> None:
        """``summary: true`` appends a markdown status table after the gate run, without changing its exit code.

        A team wants every CI run to show which deprecations are live. The gate itself passes, and the job summary
        must gain a headed markdown table listing the scanned module's deprecated callables.
        """
        result = _run_action(tmp_path, subcommand="check", path="tests.collection_chains", summary="true")
        summary = (tmp_path / "step_summary.md").read_text(encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
        assert summary.startswith("## pyDeprecate deprecation status")
        assert "| `tests.collection_chains.caller_acc_via_depr_map` |" in summary

    def test_status_subcommand_writes_summary_from_one_scan(self, tmp_path: Path) -> None:
        """With ``subcommand: status`` the summary comes from the same run instead of a second scan.

        ``status`` already renders the table, so the runner passes ``--output`` to that run; the job summary must hold
        the same table while the step still passes.
        """
        result = _run_action(tmp_path, subcommand="status", path="tests.collection_chains", summary="true")
        summary = (tmp_path / "step_summary.md").read_text(encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "| `tests.collection_chains.caller_acc_via_depr_map` |" in summary

    def test_failing_gate_keeps_exit_code(self, tmp_path: Path) -> None:
        """A failing gate still gets its summary table, and the step keeps the gate's exit code 1.

        The summary is informational: writing it must never turn a failing gate green or a passing one red.
        """
        result = _run_action(tmp_path, subcommand="check", path="tests.collection_misconfigured", summary="true")
        summary = (tmp_path / "step_summary.md").read_text(encoding="utf-8")
        assert result.returncode == 1, result.stdout + result.stderr
        assert summary.startswith("## pyDeprecate deprecation status")

    def test_skipped_after_usage_error(self, tmp_path: Path) -> None:
        """A CLI usage error (exit 2) writes no summary, since the requested scan never ran.

        A misspelled flag in ``args`` makes the CLI exit 2; a table produced by a different, flag-less scan would
        misrepresent what the step checked, so no summary is written at all.
        """
        result = _run_action(
            tmp_path, subcommand="check", path="tests.collection_chains", args="--verison 2.0", summary="true"
        )
        assert result.returncode == 2, result.stdout + result.stderr
        assert not (tmp_path / "step_summary.md").exists()

    def test_warns_without_summary_file(self, tmp_path: Path) -> None:
        """Outside GitHub Actions (no ``GITHUB_STEP_SUMMARY``) the summary is skipped with a warning only.

        Running the action locally (e.g. with ``act``) may leave the summary variable empty; the gate result must be
        unaffected and the skip must be visible.
        """
        env = _runner_env(tmp_path, subcommand="check", path="tests.collection_chains", summary="true")
        result = _run_runner({**env, "GITHUB_STEP_SUMMARY": ""})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning title=pyDeprecate::`summary: true` needs GITHUB_STEP_SUMMARY" in result.stdout


class TestActionOutputSafety:
    """Annotations and workflow-command handling around the scanned package's output."""

    def test_cli_output_cannot_issue_workflow_commands(self, tmp_path: Path) -> None:
        """The CLI's output is fenced by a matching ``stop-commands`` token pair.

        The scan imports the audited package and prints strings taken from it; a crafted deprecation message could
        otherwise emit ``::error`` or ``::add-mask`` lines. The runner must open and close a random-token fence around
        the CLI run.
        """
        result = _run_action(tmp_path, subcommand="check", path="tests.collection_chains")
        tokens = re.findall(r"^::stop-commands::([0-9a-f]{32})$", result.stdout, flags=re.MULTILINE)
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(tokens) == 1
        assert f"\n::{tokens[0]}::\n" in result.stdout

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
        assert "::warning title=pyDeprecate::the package version could not be detected" in result.stdout

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

    def test_inputs_match_runner(self) -> None:
        """Every input and its default match what the runner reads, and each is mapped to the runner's variable.

        Adding an input to ``action.yml`` without wiring it into the step ``env`` (or the runner) would silently
        ignore it; a mismatched default would make the action behave differently from its documentation.
        """
        action = _load_action()
        defaults = {name: str(spec.get("default", "")) for name, spec in action["inputs"].items()}
        step_env = next(step for step in action["runs"]["steps"] if "run" in step)["env"]
        expected_env = {
            action_runner.ENV_PREFIX + name.upper().replace("-", "_"): f"${{{{ inputs.{name} }}}}"
            for name in action_runner.INPUT_DEFAULTS
        }
        assert defaults == action_runner.INPUT_DEFAULTS
        assert {key: value for key, value in step_env.items() if key in expected_env} == expected_env

    def test_subcommands_match_cli(self) -> None:
        """The runner accepts exactly the CLI's subcommands.

        A subcommand added to the CLI but not to the action would be rejected as invalid input; one removed from
        the CLI would fail only at run time.
        """
        cli_subcommands = {name[len("cmd_") :] for name in dir(cli_commands) if name.startswith("cmd_")}
        assert set(action_runner.SUBCOMMANDS) == cli_subcommands

    def test_exit_zero_rejected_only_where_cli_lacks_it(self) -> None:
        """``exit-zero`` is refused exactly for the subcommands whose CLI function has no ``exit_zero`` flag.

        The runner rejects ``exit-zero`` with ``status`` up front; if another subcommand ever drops the flag (or
        ``status`` gains it), the runner's rule must follow instead of forwarding a flag the CLI rejects.
        """
        without_exit_zero = {
            name
            for name in action_runner.SUBCOMMANDS
            if "exit_zero" not in inspect.signature(getattr(cli_commands, f"cmd_{name}")).parameters
        }
        assert without_exit_zero == {"status"}

    def test_summary_flags_accepted_by_status(self) -> None:
        """Every flag the summary forwards is a parameter of the ``status`` subcommand.

        A forwarded flag that ``status`` does not know makes the summary scan exit 2, losing the summary.
        """
        status_params = set(inspect.signature(cli_commands.cmd_status).parameters)
        # Fire spells a negated boolean `--noX`; the parameter itself is `X`
        forwarded = {"recursive" if flag == "norecursive" else flag for flag in action_runner.SUMMARY_FORWARDED_FLAGS}
        assert forwarded <= status_params

    def test_install_extras_match_precommit_hooks(self) -> None:
        """The action installs the same extras the pre-commit hooks request.

        Both integrations promise that one pinned ref gives one working CLI; diverging extras would leave one of
        them without ``fire``/``rich`` or without ``[tool.pydeprecate]`` support on Python 3.9-3.10.
        """
        hooks = yaml.safe_load(_HOOKS_YAML.read_text(encoding="utf-8"))
        hook_requirements = {dep for hook in hooks for dep in hook["additional_dependencies"]}
        assert hook_requirements == {f"pyDeprecate[{','.join(action_runner.INSTALL_EXTRAS)}]"}
