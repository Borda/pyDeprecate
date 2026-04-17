"""Unit tests for the CLI module (all external calls fully mocked)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deprecate._cli import _print, _report_issues, cli, main
from deprecate._types import DeprecationConfig
from deprecate.audit import DeprecationWrapperInfo


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
    def test_scan_directory_nested_files_warning(
        self, mock_find: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that a warning is printed when nested Python files are found in a plain directory."""
        (tmp_path / "module_a.py").touch()
        subdir = tmp_path / "subpkg"
        subdir.mkdir()
        (subdir / "nested.py").touch()

        mock_find.return_value = []
        assert main(path=str(tmp_path)) == 0
        captured = capsys.readouterr()
        assert "Skipping nested Python files" in captured.err

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

    def test_file_path_rejected(self, tmp_path: Path) -> None:
        """Test that passing a file path raises ValueError with a helpful message."""
        f = tmp_path / "module.py"
        f.touch()
        assert main(path=str(f)) == 1

    def test_absolute_path_package_outside_cwd(self, tmp_path: Path) -> None:
        """Test that main() correctly adds the package parent to sys.path for absolute paths."""
        pkg = tmp_path / "isolated_testpkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text('"""Minimal test package with no deprecations."""\n')

        original_path = list(sys.path)
        result = main(path=str(pkg))

        # sys.path must be fully restored after scanning
        assert sys.path == original_path
        # Empty package has no deprecated wrappers → scan succeeds with exit 0
        assert result == 0


class TestReportIssuesPlain:
    """Tests for _report_issues with the plain-text path (_HAS_RICH = False)."""

    def test_invalid_args(self) -> None:
        """Test plain text reporter with invalid args."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])]
        with patch("deprecate._cli._HAS_RICH", False):
            assert _report_issues(results) is True

    def test_identity_mapping(self) -> None:
        """Test plain text reporter with identity mappings."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", identity_mapping=["a"])]
        with patch("deprecate._cli._HAS_RICH", False):
            assert _report_issues(results) is True

    def test_no_effect_empty_mapping(self) -> None:
        """Test plain text reporter with no-effect empty mapping."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", empty_mapping=True, no_effect=True)]
        with patch("deprecate._cli._HAS_RICH", False):
            assert _report_issues(results) is True

    def test_no_effect_self_reference(self) -> None:
        """Test plain text reporter with no-effect self-reference."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", self_reference=True, no_effect=True)]
        with patch("deprecate._cli._HAS_RICH", False):
            assert _report_issues(results) is True

    def test_no_effect_identity_only(self) -> None:
        """Test plain text reporter with no-effect identity-only mapping."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", identity_mapping=["a"], no_effect=True)]
        with patch("deprecate._cli._HAS_RICH", False):
            assert _report_issues(results) is True

    def test_no_effect_partial_identity_with_self_reference(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Partial identity mappings should not be reported as all-identity."""
        results = [
            DeprecationWrapperInfo(
                module="mod",
                function="fn",
                deprecated_info=DeprecationConfig(args_mapping={"a": "a", "b": "c"}),
                identity_mapping=["a"],
                self_reference=True,
                no_effect=True,
            )
        ]

        with patch("deprecate._cli._HAS_RICH", False):
            assert _report_issues(results) is True
        captured = capsys.readouterr()
        assert "Reason: Self reference" in captured.out
        assert "Reason: All identity mappings" not in captured.out

    def test_no_issues(self) -> None:
        """Test plain text reporter with no issues."""
        results = [DeprecationWrapperInfo(module="mod", function="fn")]
        with patch("deprecate._cli._HAS_RICH", False):
            assert _report_issues(results) is False


class TestReportIssuesRich:
    """Tests for _report_issues with the Rich path (_HAS_RICH = True)."""

    def test_invalid_args(self) -> None:
        """Test rich reporter with invalid args."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])]
        assert _report_issues(results) is True

    def test_identity_mapping(self) -> None:
        """Test rich reporter with identity mappings."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", identity_mapping=["a"])]
        assert _report_issues(results) is True

    def test_no_effect_empty_mapping(self) -> None:
        """Test rich reporter with no-effect empty mapping."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", empty_mapping=True, no_effect=True)]
        assert _report_issues(results) is True

    def test_no_effect_self_reference(self) -> None:
        """Test rich reporter with no-effect self-reference."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", self_reference=True, no_effect=True)]
        assert _report_issues(results) is True

    def test_no_effect_identity_only(self) -> None:
        """Test rich reporter with no-effect identity-only mapping."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", identity_mapping=["a"], no_effect=True)]
        assert _report_issues(results) is True

    def test_no_effect_partial_identity_with_self_reference(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Rich reporter should not list all-identity for partial identity mappings."""
        results = [
            DeprecationWrapperInfo(
                module="mod",
                function="fn",
                deprecated_info=DeprecationConfig(args_mapping={"a": "a", "b": "c"}),
                identity_mapping=["a"],
                self_reference=True,
                no_effect=True,
            )
        ]

        assert _report_issues(results) is True
        captured = capsys.readouterr()
        assert "Self reference" in captured.out
        assert "All identity mappings" not in captured.out

    def test_no_issues(self) -> None:
        """Test rich reporter with no issues."""
        results = [DeprecationWrapperInfo(module="mod", function="fn")]
        assert _report_issues(results) is False


def test_report_issues_dispatches() -> None:
    """Test _report_issues dispatches based on _HAS_RICH."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])]
    assert _report_issues(results) is True


class TestCliEntryPoint:
    """Tests for the cli() entry point."""

    def test_with_jsonargparse(self) -> None:
        """Test cli() entry point delegates to auto_cli when jsonargparse is available."""
        mock_jsonargparse = MagicMock()
        mock_jsonargparse.auto_cli.return_value = None
        with patch.dict("sys.modules", {"jsonargparse": mock_jsonargparse}):
            cli()
            mock_jsonargparse.auto_cli.assert_called_once()

    def test_missing_extras_guidance(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test cli() prints install guidance and exits when jsonargparse is not available."""
        with (
            patch.dict("sys.modules", {"jsonargparse": None}),
            patch("sys.exit") as mock_exit,
        ):
            cli()
        captured = capsys.readouterr()
        assert "pip install" in captured.err
        assert "pyDeprecate[cli]" in captured.err
        mock_exit.assert_called_once_with(1)


class TestHasRichFalse:
    """Tests for the plain-text fallback path when ``_HAS_RICH`` is ``False``."""

    @pytest.mark.parametrize(
        ("stderr", "stream"),
        [(False, "out"), (True, "err")],
    )
    def test_print_routes_to_builtin_print(self, capsys: pytest.CaptureFixture[str], stderr: bool, stream: str) -> None:
        """``_print()`` falls back to built-in ``print()`` when rich is unavailable."""
        with patch("deprecate._cli._HAS_RICH", False):
            _print("hello", stderr=stderr)
        captured = capsys.readouterr()
        assert "hello" in getattr(captured, stream)

    def test_report_issues_dispatches_to_plain(self) -> None:
        """``_report_issues()`` delegates to the plain reporter when rich is unavailable."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])]
        with patch("deprecate._cli._HAS_RICH", False):
            assert _report_issues(results) is True

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_main_output_streams(self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """``main()`` prints scanning and no-results messages to stdout when rich is unavailable."""
        mock_find.return_value = []
        with patch("deprecate._cli._HAS_RICH", False):
            assert main(path="some_module") == 0
        captured = capsys.readouterr()
        assert "Scanning path" in captured.out
        assert "No deprecated callables found" in captured.out

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_nested_files_warning_stderr(
        self, mock_find: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Nested-files warning lands in stderr when rich is unavailable."""
        (tmp_path / "module_a.py").touch()
        subdir = tmp_path / "subpkg"
        subdir.mkdir()
        (subdir / "nested.py").touch()

        mock_find.return_value = []
        with patch("deprecate._cli._HAS_RICH", False):
            assert main(path=str(tmp_path)) == 0
        captured = capsys.readouterr()
        assert "Skipping nested Python files" in captured.err
