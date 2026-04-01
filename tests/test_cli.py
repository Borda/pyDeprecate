"""Tests for the CLI."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from deprecate.audit import DeprecationWrapperInfo
from deprecate.cli import _report_issues, _report_issues_plain, _report_issues_rich, cli, main

_SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_no_issues_package(mock_find: MagicMock, tmp_path: Path) -> None:
    """Test CLI when scanning a package with no issues found."""
    pkg_dir = tmp_path / "mypkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").touch()

    mock_find.return_value = []
    assert main(path=str(pkg_dir)) == 0
    mock_find.assert_called_once_with("mypkg")


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_no_issues_file(mock_find: MagicMock) -> None:
    """Test CLI when scanning a single module with no issues found."""
    mock_find.return_value = []
    assert main(path="some_module") == 0
    mock_find.assert_called_once_with("some_module")


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_scan_plain_directory(mock_find: MagicMock, tmp_path: Path) -> None:
    """Test CLI when scanning a plain directory (no __init__.py)."""
    (tmp_path / "module_a.py").touch()
    (tmp_path / "module_b.py").touch()
    # __dunder files should be skipped by the scanner
    (tmp_path / "__helpers__.py").touch()

    mock_find.return_value = []
    assert main(path=str(tmp_path)) == 0
    assert mock_find.call_count == 2


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_scan_directory_with_scan_error(mock_find: MagicMock, tmp_path: Path) -> None:
    """Test CLI when scanning a plain directory and individual file fails."""
    (tmp_path / "bad_module.py").touch()

    mock_find.side_effect = Exception("import error")
    # Individual file errors are caught as warnings, returns 0
    assert main(path=str(tmp_path)) == 0


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_found_issues(mock_find: MagicMock) -> None:
    """Test CLI when issues are found."""
    info = DeprecationWrapperInfo(module="test_mod", function="test_func", invalid_args=["bad_arg"])
    mock_find.return_value = [info]

    assert main(path="some_module") == 1


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_found_warnings_only(mock_find: MagicMock) -> None:
    """Test CLI when only warnings are found (identity mapping)."""
    info = DeprecationWrapperInfo(module="test_mod", function="test_func", identity_mapping=["arg"], no_effect=True)
    mock_find.return_value = [info]

    assert main(path="some_module") == 0


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_no_effect_empty_mapping(mock_find: MagicMock) -> None:
    """Test CLI reports empty mapping as reason for no-effect wrapper."""
    info = DeprecationWrapperInfo(module="test_mod", function="test_func", empty_mapping=True, no_effect=True)
    mock_find.return_value = [info]

    assert main(path="some_module") == 0


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_no_effect_self_reference(mock_find: MagicMock) -> None:
    """Test CLI reports self-reference as reason for no-effect wrapper."""
    info = DeprecationWrapperInfo(module="test_mod", function="test_func", self_reference=True, no_effect=True)
    mock_find.return_value = [info]

    assert main(path="some_module") == 0


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_all_correct(mock_find: MagicMock) -> None:
    """Test CLI when deprecated wrappers exist but all are correctly configured."""
    info = DeprecationWrapperInfo(module="test_mod", function="test_func")
    mock_find.return_value = [info]

    assert main(path="some_module") == 0


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_error_scanning(mock_find: MagicMock) -> None:
    """Test CLI when scanning fails."""
    mock_find.side_effect = Exception("Boom")

    assert main(path="some_module") == 1


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_skip_errors(mock_find: MagicMock) -> None:
    """Test CLI with --skip-errors returns 0 even with invalid args."""
    info = DeprecationWrapperInfo(module="test_mod", function="test_func", invalid_args=["bad_arg"])
    mock_find.return_value = [info]

    assert main(path="some_module", skip_errors=True) == 0


# --- Plain text fallback tests ---


def test_report_plain_invalid_args() -> None:
    """Test plain text reporter with invalid args."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])]
    assert _report_issues_plain(results) is True


def test_report_plain_identity_mapping() -> None:
    """Test plain text reporter with identity mappings."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", identity_mapping=["a"])]
    assert _report_issues_plain(results) is True


def test_report_plain_no_effect_empty_mapping() -> None:
    """Test plain text reporter with no-effect empty mapping."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", empty_mapping=True, no_effect=True)]
    assert _report_issues_plain(results) is True


def test_report_plain_no_effect_self_reference() -> None:
    """Test plain text reporter with no-effect self-reference."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", self_reference=True, no_effect=True)]
    assert _report_issues_plain(results) is True


def test_report_plain_no_effect_identity_only() -> None:
    """Test plain text reporter with no-effect identity-only mapping."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", identity_mapping=["a"], no_effect=True)]
    assert _report_issues_plain(results) is True


def test_report_plain_no_issues() -> None:
    """Test plain text reporter with no issues."""
    results = [DeprecationWrapperInfo(module="mod", function="fn")]
    assert _report_issues_plain(results) is False


# --- Rich reporter tests ---


def test_report_rich_invalid_args() -> None:
    """Test rich reporter with invalid args."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])]
    assert _report_issues_rich(results) is True


def test_report_rich_identity_mapping() -> None:
    """Test rich reporter with identity mappings."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", identity_mapping=["a"])]
    assert _report_issues_rich(results) is True


def test_report_rich_no_effect_empty_mapping() -> None:
    """Test rich reporter with no-effect empty mapping."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", empty_mapping=True, no_effect=True)]
    assert _report_issues_rich(results) is True


def test_report_rich_no_effect_self_reference() -> None:
    """Test rich reporter with no-effect self-reference."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", self_reference=True, no_effect=True)]
    assert _report_issues_rich(results) is True


def test_report_rich_no_effect_identity_only() -> None:
    """Test rich reporter with no-effect identity-only mapping."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", identity_mapping=["a"], no_effect=True)]
    assert _report_issues_rich(results) is True


def test_report_rich_no_issues() -> None:
    """Test rich reporter with no issues."""
    results = [DeprecationWrapperInfo(module="mod", function="fn")]
    assert _report_issues_rich(results) is False


def test_report_issues_dispatches() -> None:
    """Test _report_issues dispatches based on _HAS_RICH."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])]
    assert _report_issues(results) is True


# --- CLI entry point tests ---


def test_cli_entry_with_jsonargparse() -> None:
    """Test cli() entry point delegates to jsonargparse CLI."""
    with patch("jsonargparse.CLI") as mock_cli:
        cli()
        mock_cli.assert_called_once()


def test_cli_entry_argparse_fallback() -> None:
    """Test cli() falls back to argparse when jsonargparse is not available."""
    with (
        patch.dict("sys.modules", {"jsonargparse": None}),
        patch("sys.argv", ["pydeprecate", "some_module", "--skip-errors"]),
        patch("deprecate.cli.main", return_value=0) as mock_main,
        patch("sys.exit") as mock_exit,
    ):
        cli()
        mock_main.assert_called_once_with(path="some_module", ignore=[], skip_errors=True)
        mock_exit.assert_called_once_with(0)


# --- Real CLI invocation tests ---


def _cli_env(**extra: str) -> dict[str, str]:
    """Build env dict with PYTHONPATH pointing at src/ so subprocess can find deprecate."""
    return {**os.environ, "PYTHONPATH": _SRC_DIR, **extra}


def test_cli_invocation_no_args() -> None:
    """Test real CLI invocation via subprocess with no arguments."""
    result = subprocess.run(
        [sys.executable, "-m", "deprecate.cli"],
        capture_output=True,
        text=True,
        env=_cli_env(),
    )
    assert "Scanning path" in result.stdout


def test_cli_invocation_help() -> None:
    """Test real CLI invocation prints help text."""
    result = subprocess.run(
        [sys.executable, "-m", "deprecate.cli", "--help"],
        capture_output=True,
        text=True,
        env=_cli_env(),
    )
    assert result.returncode == 0
    assert "path" in result.stdout.lower()


def test_cli_invocation_nonexistent_module() -> None:
    """Test real CLI invocation with a module that doesn't exist."""
    result = subprocess.run(
        [sys.executable, "-m", "deprecate.cli", "nonexistent_module_xyz"],
        capture_output=True,
        text=True,
        env=_cli_env(COLUMNS="200"),
    )
    assert result.returncode != 0
