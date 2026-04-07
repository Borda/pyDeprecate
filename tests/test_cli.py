"""Tests for the CLI."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from deprecate._cli import _report_issues, _report_issues_plain, _report_issues_rich, cli, main
from deprecate.audit import DeprecationWrapperInfo

_SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")


class TestMain:
    """Tests for the main() scanning and exit-code logic."""

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_issues_package(self, mock_find: MagicMock, tmp_path: Path) -> None:
        """Test CLI when scanning a package with no issues found."""
        pkg_dir = tmp_path / "mypkg"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").touch()

        mock_find.return_value = []
        assert main(path=str(pkg_dir)) == 0
        mock_find.assert_called_once_with("mypkg")

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_issues_file(self, mock_find: MagicMock) -> None:
        """Test CLI when scanning a single module with no issues found."""
        mock_find.return_value = []
        assert main(path="some_module") == 0
        mock_find.assert_called_once_with("some_module")

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_scan_plain_directory(self, mock_find: MagicMock, tmp_path: Path) -> None:
        """Test CLI when scanning a plain directory (no __init__.py)."""
        (tmp_path / "module_a.py").touch()
        (tmp_path / "module_b.py").touch()
        # __dunder files should be skipped by the scanner
        (tmp_path / "__helpers__.py").touch()

        mock_find.return_value = []
        assert main(path=str(tmp_path)) == 0
        assert mock_find.call_count == 2

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_scan_directory_with_scan_error(self, mock_find: MagicMock, tmp_path: Path) -> None:
        """Test CLI when scanning a plain directory and individual file fails."""
        (tmp_path / "bad_module.py").touch()

        mock_find.side_effect = Exception("import error")
        # Individual file errors are caught as warnings, returns 0
        assert main(path=str(tmp_path)) == 0

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_found_issues(self, mock_find: MagicMock) -> None:
        """Test CLI when issues are found."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", invalid_args=["bad_arg"])
        mock_find.return_value = [info]

        assert main(path="some_module") == 1

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_found_warnings_only(self, mock_find: MagicMock) -> None:
        """Test CLI when only warnings are found (identity mapping)."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", identity_mapping=["arg"], no_effect=True)
        mock_find.return_value = [info]

        assert main(path="some_module") == 0

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_effect_empty_mapping(self, mock_find: MagicMock) -> None:
        """Test CLI reports empty mapping as reason for no-effect wrapper."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", empty_mapping=True, no_effect=True)
        mock_find.return_value = [info]

        assert main(path="some_module") == 0

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_effect_self_reference(self, mock_find: MagicMock) -> None:
        """Test CLI reports self-reference as reason for no-effect wrapper."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", self_reference=True, no_effect=True)
        mock_find.return_value = [info]

        assert main(path="some_module") == 0

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_all_correct(self, mock_find: MagicMock) -> None:
        """Test CLI when deprecated wrappers exist but all are correctly configured."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func")
        mock_find.return_value = [info]

        assert main(path="some_module") == 0

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_error_scanning(self, mock_find: MagicMock) -> None:
        """Test CLI when scanning fails."""
        mock_find.side_effect = Exception("Boom")

        assert main(path="some_module") == 1

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_skip_errors(self, mock_find: MagicMock) -> None:
        """Test CLI with --skip-errors returns 0 even with invalid args."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", invalid_args=["bad_arg"])
        mock_find.return_value = [info]

        assert main(path="some_module", skip_errors=True) == 0


class TestReportIssuesPlain:
    """Tests for the plain text fallback reporter."""

    def test_invalid_args(self) -> None:
        """Test plain text reporter with invalid args."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])]
        assert _report_issues_plain(results) is True

    def test_identity_mapping(self) -> None:
        """Test plain text reporter with identity mappings."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", identity_mapping=["a"])]
        assert _report_issues_plain(results) is True

    def test_no_effect_empty_mapping(self) -> None:
        """Test plain text reporter with no-effect empty mapping."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", empty_mapping=True, no_effect=True)]
        assert _report_issues_plain(results) is True

    def test_no_effect_self_reference(self) -> None:
        """Test plain text reporter with no-effect self-reference."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", self_reference=True, no_effect=True)]
        assert _report_issues_plain(results) is True

    def test_no_effect_identity_only(self) -> None:
        """Test plain text reporter with no-effect identity-only mapping."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", identity_mapping=["a"], no_effect=True)]
        assert _report_issues_plain(results) is True

    def test_no_issues(self) -> None:
        """Test plain text reporter with no issues."""
        results = [DeprecationWrapperInfo(module="mod", function="fn")]
        assert _report_issues_plain(results) is False


class TestReportIssuesRich:
    """Tests for the rich reporter."""

    def test_invalid_args(self) -> None:
        """Test rich reporter with invalid args."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])]
        assert _report_issues_rich(results) is True

    def test_identity_mapping(self) -> None:
        """Test rich reporter with identity mappings."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", identity_mapping=["a"])]
        assert _report_issues_rich(results) is True

    def test_no_effect_empty_mapping(self) -> None:
        """Test rich reporter with no-effect empty mapping."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", empty_mapping=True, no_effect=True)]
        assert _report_issues_rich(results) is True

    def test_no_effect_self_reference(self) -> None:
        """Test rich reporter with no-effect self-reference."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", self_reference=True, no_effect=True)]
        assert _report_issues_rich(results) is True

    def test_no_effect_identity_only(self) -> None:
        """Test rich reporter with no-effect identity-only mapping."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", identity_mapping=["a"], no_effect=True)]
        assert _report_issues_rich(results) is True

    def test_no_issues(self) -> None:
        """Test rich reporter with no issues."""
        results = [DeprecationWrapperInfo(module="mod", function="fn")]
        assert _report_issues_rich(results) is False


def test_report_issues_dispatches() -> None:
    """Test _report_issues dispatches based on _HAS_RICH."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])]
    assert _report_issues(results) is True


class TestCliEntryPoint:
    """Tests for the cli() entry point."""

    def test_with_jsonargparse(self) -> None:
        """Test cli() entry point delegates to auto_cli when jsonargparse is available."""
        with patch("jsonargparse.auto_cli", return_value=None) as mock_auto_cli:
            cli()
            mock_auto_cli.assert_called_once()

    def test_missing_extras_guidance(self) -> None:
        """Test cli() prints install guidance and exits when jsonargparse is not available."""
        with (
            patch.dict("sys.modules", {"jsonargparse": None}),
            patch("sys.exit") as mock_exit,
            patch("sys.stderr") as mock_stderr,
        ):
            cli()
            output = "".join(call.args[0] for call in mock_stderr.write.call_args_list)
            assert "pip install" in output
            assert "pyDeprecate[cli]" in output
            mock_exit.assert_called_once_with(1)


def _cli_env(**extra: str) -> dict[str, str]:
    """Build env dict with PYTHONPATH pointing at src/ so subprocess can find deprecate."""
    existing_pythonpath = os.environ.get("PYTHONPATH")
    pythonpath = f"{_SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else _SRC_DIR
    return {**os.environ, "PYTHONPATH": pythonpath, **extra}


class TestCliInvocation:
    """Tests for real CLI invocations via subprocess."""

    def test_no_args(self) -> None:
        """Test real CLI invocation via subprocess with no arguments."""
        result = subprocess.run(
            [sys.executable, "-m", "deprecate"],
            capture_output=True,
            text=True,
            env=_cli_env(),
        )
        assert "Scanning path" in result.stdout

    def test_help(self) -> None:
        """Test real CLI invocation prints help text."""
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "--help"],
            capture_output=True,
            text=True,
            env=_cli_env(),
        )
        assert result.returncode == 0
        assert "path" in result.stdout.lower()

    def test_nonexistent_module(self) -> None:
        """Test real CLI invocation with a module that doesn't exist."""
        result = subprocess.run(
            [sys.executable, "-m", "deprecate", "nonexistent_module_xyz"],
            capture_output=True,
            text=True,
            env=_cli_env(COLUMNS="200"),
        )
        assert result.returncode != 0
