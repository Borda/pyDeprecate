"""Tests for the CLI."""

import os
import tempfile
from unittest.mock import MagicMock, patch

from deprecate.audit import DeprecationWrapperInfo
from deprecate.cli import main


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_no_issues_package(mock_find: MagicMock) -> None:
    """Test CLI when scanning a package with no issues found."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a package directory with __init__.py
        pkg_dir = os.path.join(tmpdir, "mypkg")
        os.makedirs(pkg_dir)
        open(os.path.join(pkg_dir, "__init__.py"), "w").close()

        mock_find.return_value = []
        assert main([pkg_dir]) == 0
        mock_find.assert_called_once_with("mypkg")


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_no_issues_file(mock_find: MagicMock) -> None:
    """Test CLI when scanning a single module with no issues found."""
    mock_find.return_value = []
    assert main(["some_module"]) == 0
    mock_find.assert_called_once_with("some_module")


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_found_issues(mock_find: MagicMock) -> None:
    """Test CLI when issues are found."""
    info = DeprecationWrapperInfo(module="test_mod", function="test_func", invalid_args=["bad_arg"])
    mock_find.return_value = [info]

    # Run main - should exit with 1 because of invalid args
    assert main(["some_module"]) == 1


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_found_warnings_only(mock_find: MagicMock) -> None:
    """Test CLI when only warnings are found (identity mapping)."""
    info = DeprecationWrapperInfo(module="test_mod", function="test_func", identity_mapping=["arg"], no_effect=True)
    mock_find.return_value = [info]

    # Run main - should exit with 0 as these are just warnings
    assert main(["some_module"]) == 0


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_error_scanning(mock_find: MagicMock) -> None:
    """Test CLI when scanning fails."""
    mock_find.side_effect = Exception("Boom")

    # Should catch exception and return 1
    assert main(["some_module"]) == 1


@patch("deprecate.cli.find_deprecation_wrappers")
def test_cli_skip_errors(mock_find: MagicMock) -> None:
    """Test CLI with --skip-errors flag returns 0 even with invalid args."""
    info = DeprecationWrapperInfo(module="test_mod", function="test_func", invalid_args=["bad_arg"])
    mock_find.return_value = [info]

    assert main(["some_module", "--skip-errors"]) == 0
