"""Tests for the CLI."""

from unittest.mock import MagicMock, patch

from deprecate.cli import main
from deprecate.utils import DeprecatedCallableInfo


@patch("deprecate.cli.find_deprecated_callables")
def test_cli_no_issues(mock_find: MagicMock) -> None:
    """Test CLI when no issues are found."""
    # Setup mock to return a clean list of deprecations (or empty)
    mock_find.return_value = []
    
    # Run main
    assert main(["."]) == 0
    mock_find.assert_called_once_with(".")


@patch("deprecate.cli.find_deprecated_callables")
def test_cli_found_issues(mock_find: MagicMock) -> None:
    """Test CLI when issues are found."""
    # Setup mock to return a list with issues
    info = DeprecatedCallableInfo(
        module="test_mod",
        function="test_func",
        invalid_args=["bad_arg"]
    )
    
    mock_find.return_value = [info]
    
    # Run main - should exit with 1 because of invalid args
    assert main(["."]) == 1


@patch("deprecate.cli.find_deprecated_callables")
def test_cli_found_warnings_only(mock_find: MagicMock) -> None:
    """Test CLI when only warnings are found (identity mapping)."""
    # Setup mock to return a list with warning-level issues
    info = DeprecatedCallableInfo(
        module="test_mod",
        function="test_func",
        identity_mapping=["arg"],
        no_effect=True
    )
    
    mock_find.return_value = [info]
    # Run main - should exit with 0 as these are just warnings
    assert main(["."]) == 0


@patch("deprecate.cli.find_deprecated_callables")
def test_cli_error_scanning(mock_find: MagicMock) -> None:
    """Test CLI when scanning fails."""
    mock_find.side_effect = Exception("Boom")
    
    # Should catch exception and return 1
    assert main(["."]) == 1
