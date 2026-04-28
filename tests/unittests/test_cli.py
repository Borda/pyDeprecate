"""Unit tests for the CLI module (all external calls fully mocked)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deprecate._cli import (
    _check_expiry_from_results,
    _print,
    _report_issues,
    cli,
    cmd_all,
    cmd_chains,
    cmd_check,
    cmd_expiry,
)
from deprecate._types import DeprecationConfig
from deprecate.audit import ChainType, DeprecationWrapperInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TARGET_CHAIN = DeprecationWrapperInfo(module="mod", function="fn", chain_type=ChainType.TARGET)
_STACKED_CHAIN = DeprecationWrapperInfo(module="mod", function="fn2", chain_type=ChainType.STACKED)
_INVALID_ARGS = DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])
_EXPIRED_MSG = (
    "Callable `fn` was scheduled for removal in version 1.0"
    " but still exists in version 2.0. Please delete this deprecated code."
)


class TestCmdCheckScanning:
    """Tests for cmd_check() scanning and path-handling behavior."""

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_issues_package(self, mock_find: MagicMock, tmp_path: Path) -> None:
        """Scanning a package directory with no issues exits 0."""
        pkg_dir = tmp_path / "mypkg"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").touch()

        mock_find.return_value = []
        assert cmd_check(path=str(pkg_dir)) == 0
        mock_find.assert_called_once_with("mypkg", recursive=True)

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_issues_file(self, mock_find: MagicMock) -> None:
        """Scanning an importable module name with no issues exits 0."""
        mock_find.return_value = []
        assert cmd_check(path="some_module") == 0
        mock_find.assert_called_once_with("some_module", recursive=True)

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_scan_plain_directory(self, mock_find: MagicMock, tmp_path: Path) -> None:
        """Scanning a plain directory (no __init__.py) scans each .py file."""
        (tmp_path / "module_a.py").touch()
        (tmp_path / "module_b.py").touch()
        # __dunder files should be skipped by the scanner
        (tmp_path / "__helpers__.py").touch()

        mock_find.return_value = []
        assert cmd_check(path=str(tmp_path)) == 0
        assert mock_find.call_count == 2

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_scan_directory_nested_files_warning(
        self, mock_find: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A warning is printed when nested Python files are found in a plain directory."""
        (tmp_path / "module_a.py").touch()
        subdir = tmp_path / "subpkg"
        subdir.mkdir()
        (subdir / "nested.py").touch()

        mock_find.return_value = []
        assert cmd_check(path=str(tmp_path)) == 0
        captured = capsys.readouterr()
        assert "Skipping nested Python files" in captured.err

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_scan_directory_with_scan_error(self, mock_find: MagicMock, tmp_path: Path) -> None:
        """Per-file scan errors in plain directory are caught as warnings; exits 0."""
        (tmp_path / "bad_module.py").touch()

        mock_find.side_effect = Exception("import error")
        assert cmd_check(path=str(tmp_path)) == 0

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_found_issues(self, mock_find: MagicMock) -> None:
        """Invalid arg mappings cause exit 1."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", invalid_args=["bad_arg"])
        mock_find.return_value = [info]

        assert cmd_check(path="some_module") == 1

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_found_warnings_only(self, mock_find: MagicMock) -> None:
        """Identity mapping (warning only) exits 0."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", identity_mapping=["arg"], no_effect=True)
        mock_find.return_value = [info]

        assert cmd_check(path="some_module") == 0

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_effect_empty_mapping(self, mock_find: MagicMock) -> None:
        """Empty mapping reported as no-effect reason; exits 0."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", empty_mapping=True, no_effect=True)
        mock_find.return_value = [info]

        assert cmd_check(path="some_module") == 0

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_effect_self_reference(self, mock_find: MagicMock) -> None:
        """Self-reference reported as no-effect reason; exits 0."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", self_reference=True, no_effect=True)
        mock_find.return_value = [info]

        assert cmd_check(path="some_module") == 0

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_all_correct(self, mock_find: MagicMock) -> None:
        """Correctly configured wrappers exit 0."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func")
        mock_find.return_value = [info]

        assert cmd_check(path="some_module") == 0

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_error_scanning(self, mock_find: MagicMock) -> None:
        """Scan failure exits 1."""
        mock_find.side_effect = Exception("Boom")

        assert cmd_check(path="some_module") == 1

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_skip_errors(self, mock_find: MagicMock) -> None:
        """skip_errors=True returns 0 even with invalid args."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", invalid_args=["bad_arg"])
        mock_find.return_value = [info]

        assert cmd_check(path="some_module", skip_errors=True) == 0

    def test_file_path_rejected(self, tmp_path: Path) -> None:
        """File path (not directory or module) exits 1."""
        f = tmp_path / "module.py"
        f.touch()
        assert cmd_check(path=str(f)) == 1

    def test_absolute_path_package_outside_cwd(self, tmp_path: Path) -> None:
        """sys.path is fully restored after scanning an absolute package path."""
        pkg = tmp_path / "isolated_testpkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text('"""Minimal test package with no deprecations."""\n')

        original_path = list(sys.path)
        result = cmd_check(path=str(pkg))

        assert sys.path == original_path
        assert result == 0


# ---------------------------------------------------------------------------
# cmd_check
# ---------------------------------------------------------------------------


class TestCmdCheck:
    """Tests for cmd_check() subcommand — the refactored core of main()."""

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_recursive_threads_flag(self, mock_find: MagicMock) -> None:
        """recursive=False passes recursive=False to find_deprecation_wrappers."""
        mock_find.return_value = []
        assert cmd_check(path="some_module", recursive=False) == 0
        mock_find.assert_called_once_with("some_module", recursive=False)

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_chain_warning_exits_zero(self, mock_find: MagicMock) -> None:
        """Chains in check subcommand are warnings — do not cause exit 1."""
        mock_find.return_value = [_TARGET_CHAIN]
        assert cmd_check(path="some_module") == 0

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_chain_warning_reported(self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """Chain issues are included in check output."""
        mock_find.return_value = [_TARGET_CHAIN]
        with patch("deprecate._cli._HAS_RICH", False):
            cmd_check(path="some_module")
        captured = capsys.readouterr()
        assert "chain" in captured.out.lower()

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_invalid_args_exits_one(self, mock_find: MagicMock) -> None:
        """Invalid args still cause exit 1 in check subcommand."""
        mock_find.return_value = [_INVALID_ARGS]
        assert cmd_check(path="some_module") == 1


# ---------------------------------------------------------------------------
# cmd_expiry
# ---------------------------------------------------------------------------


class TestCmdExpiry:
    """Tests for cmd_expiry() subcommand."""

    @patch("deprecate._cli.validate_deprecation_expiry")
    def test_no_expired_exits_zero(self, mock_expiry: MagicMock) -> None:
        """No expired wrappers → exit 0."""
        mock_expiry.return_value = []
        assert cmd_expiry(path="some_module", version="1.0") == 0

    @patch("deprecate._cli.validate_deprecation_expiry")
    def test_expired_found_exits_one(self, mock_expiry: MagicMock) -> None:
        """Expired wrappers found → exit 1."""
        mock_expiry.return_value = [_EXPIRED_MSG]
        assert cmd_expiry(path="some_module", version="2.0") == 1

    @patch("deprecate._cli.validate_deprecation_expiry")
    def test_expired_skip_errors_exits_zero(self, mock_expiry: MagicMock) -> None:
        """skip_errors=True overrides exit code to 0 even when expired wrappers found."""
        mock_expiry.return_value = [_EXPIRED_MSG]
        assert cmd_expiry(path="some_module", version="2.0", skip_errors=True) == 0

    @patch("deprecate._cli.validate_deprecation_expiry")
    def test_packaging_missing_exits_one(self, mock_expiry: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """ImportError from missing packaging library → install hint on stderr + exit 1."""
        mock_expiry.side_effect = ImportError("No module named 'packaging'")
        result = cmd_expiry(path="some_module", version="2.0")
        assert result == 1
        captured = capsys.readouterr()
        assert "pyDeprecate[audit]" in captured.err

    @patch("deprecate._cli.validate_deprecation_expiry")
    def test_packaging_missing_skip_errors_exits_zero(self, mock_expiry: MagicMock) -> None:
        """ImportError with skip_errors=True → exit 0 (skip_errors overrides all exit-1 conditions)."""
        mock_expiry.side_effect = ImportError("No module named 'packaging'")
        assert cmd_expiry(path="some_module", version="2.0", skip_errors=True) == 0

    @patch("deprecate._cli.validate_deprecation_expiry")
    def test_version_passed_through(self, mock_expiry: MagicMock) -> None:
        """Explicit version is forwarded to validate_deprecation_expiry."""
        mock_expiry.return_value = []
        cmd_expiry(path="some_module", version="3.0")
        mock_expiry.assert_called_once_with("some_module", "3.0", recursive=True)

    @patch("deprecate._cli.validate_deprecation_expiry")
    def test_no_recursive_threads_flag(self, mock_expiry: MagicMock) -> None:
        """recursive=False passes recursive=False to validate_deprecation_expiry."""
        mock_expiry.return_value = []
        cmd_expiry(path="some_module", version="1.0", recursive=False)
        mock_expiry.assert_called_once_with("some_module", "1.0", recursive=False)

    def test_plain_directory_rejected(self, tmp_path: Path) -> None:
        """Plain directory without __init__.py → exit 1 with helpful error."""
        (tmp_path / "module_a.py").touch()
        assert cmd_expiry(path=str(tmp_path), version="1.0") == 1

    @patch("deprecate._cli.validate_deprecation_expiry")
    def test_expired_reported_plain(self, mock_expiry: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """Expired messages appear in plain-text output."""
        mock_expiry.return_value = [_EXPIRED_MSG]
        with patch("deprecate._cli._HAS_RICH", False):
            cmd_expiry(path="some_module", version="2.0")
        captured = capsys.readouterr()
        assert "expired" in captured.out.lower()


# ---------------------------------------------------------------------------
# cmd_chains
# ---------------------------------------------------------------------------


class TestCmdChains:
    """Tests for cmd_chains() subcommand."""

    @patch("deprecate._cli.validate_deprecation_chains")
    def test_no_chains_exits_zero(self, mock_chains: MagicMock) -> None:
        """No chains found → exit 0."""
        mock_chains.return_value = []
        assert cmd_chains(path="some_module") == 0

    @patch("deprecate._cli.validate_deprecation_chains")
    def test_chains_found_exits_one(self, mock_chains: MagicMock) -> None:
        """Chains found → exit 1 (user explicitly asked for chain detection)."""
        mock_chains.return_value = [_TARGET_CHAIN]
        assert cmd_chains(path="some_module") == 1

    @patch("deprecate._cli.validate_deprecation_chains")
    def test_chains_skip_errors_exits_zero(self, mock_chains: MagicMock) -> None:
        """skip_errors=True overrides exit code to 0 even when chains found."""
        mock_chains.return_value = [_TARGET_CHAIN]
        assert cmd_chains(path="some_module", skip_errors=True) == 0

    @patch("deprecate._cli.validate_deprecation_chains")
    def test_no_recursive_threads_flag(self, mock_chains: MagicMock) -> None:
        """recursive=False passes recursive=False to validate_deprecation_chains."""
        mock_chains.return_value = []
        cmd_chains(path="some_module", recursive=False)
        mock_chains.assert_called_once_with("some_module", recursive=False)

    def test_plain_directory_rejected(self, tmp_path: Path) -> None:
        """Plain directory without __init__.py → exit 1 with helpful error."""
        (tmp_path / "module_a.py").touch()
        assert cmd_chains(path=str(tmp_path)) == 1

    @patch("deprecate._cli.validate_deprecation_chains")
    def test_chains_reported_plain(self, mock_chains: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """Chain messages appear in plain-text output."""
        mock_chains.return_value = [_TARGET_CHAIN]
        with patch("deprecate._cli._HAS_RICH", False):
            cmd_chains(path="some_module")
        captured = capsys.readouterr()
        assert "chain" in captured.out.lower()

    @patch("deprecate._cli.validate_deprecation_chains")
    def test_stacked_chain_label(self, mock_chains: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """STACKED chain type label appears in plain-text output."""
        mock_chains.return_value = [_STACKED_CHAIN]
        with patch("deprecate._cli._HAS_RICH", False):
            cmd_chains(path="some_module")
        captured = capsys.readouterr()
        assert "stacked" in captured.out.lower()


# ---------------------------------------------------------------------------
# cmd_all
# ---------------------------------------------------------------------------


class TestCmdAll:
    """Tests for cmd_all() subcommand — single-scan three-check pass."""

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_all_clean_exits_zero(self, mock_find: MagicMock) -> None:
        """No issues in any check → exit 0."""
        mock_find.return_value = []
        assert cmd_all(path="some_module", version="1.0") == 0

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_invalid_args_exits_one(self, mock_find: MagicMock) -> None:
        """Invalid args in check phase → exit 1."""
        mock_find.return_value = [_INVALID_ARGS]
        assert cmd_all(path="some_module", version="1.0") == 1

    @patch("deprecate._cli._check_expiry_from_results")
    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_expired_exits_one(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """Expired wrappers found → exit 1."""
        mock_find.return_value = [DeprecationWrapperInfo(module="mod", function="fn")]
        mock_expiry.return_value = [_EXPIRED_MSG]
        assert cmd_all(path="some_module", version="2.0") == 1

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_skip_errors_exits_zero(self, mock_find: MagicMock) -> None:
        """skip_errors=True overrides the invalid-args exit 1 to 0."""
        mock_find.return_value = [_INVALID_ARGS]
        assert cmd_all(path="some_module", version="1.0", skip_errors=True) == 0

    @patch("deprecate._cli._check_expiry_from_results")
    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_packaging_missing_skips_expiry_continues(
        self, mock_find: MagicMock, mock_expiry: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Missing packaging library skips expiry with warning; other checks still run."""
        mock_find.return_value = [DeprecationWrapperInfo(module="mod", function="fn")]
        mock_expiry.side_effect = ImportError("No module named 'packaging'")
        result = cmd_all(path="some_module", version="2.0")
        # No other errors → exit 0; expiry was skipped gracefully
        assert result == 0
        captured = capsys.readouterr()
        assert "packaging" in captured.err.lower()

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_version_skips_expiry(self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """When version cannot be auto-detected, expiry is skipped with a message."""
        mock_find.return_value = [DeprecationWrapperInfo(module="mod", function="fn")]
        # Simulate auto-detect failure: _resolve_module_name raises ValueError
        with patch("deprecate._cli._resolve_module_name", side_effect=ValueError("plain dir")):
            result = cmd_all(path="some_module")
        assert result == 0
        captured = capsys.readouterr()
        assert "version" in captured.err.lower()

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_recursive_threads_flag(self, mock_find: MagicMock) -> None:
        """recursive=False passes recursive=False to find_deprecation_wrappers."""
        mock_find.return_value = []
        cmd_all(path="some_module", version="1.0", recursive=False)
        mock_find.assert_called_once_with("some_module", recursive=False)


# ---------------------------------------------------------------------------
# Chain and expiry reporters
# ---------------------------------------------------------------------------


class TestReportChains:
    """Tests for _report_chains_rich and _report_chains_plain via _report_issues."""

    @pytest.mark.parametrize("has_rich", [True, False], ids=["rich", "plain"])
    @pytest.mark.parametrize(
        ("chain_type", "expected_label"),
        [
            pytest.param(ChainType.TARGET, "target", id="target-chain"),
            pytest.param(ChainType.STACKED, "stacked", id="stacked-chain"),
        ],
    )
    def test_chain_type_label_in_output(
        self,
        capsys: pytest.CaptureFixture[str],
        chain_type: ChainType,
        expected_label: str,
        has_rich: bool,
    ) -> None:
        """Chain type label appears in both rich and plain output."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", chain_type=chain_type)]
        with patch("deprecate._cli._HAS_RICH", has_rich):
            assert _report_issues(results) is True
        captured = capsys.readouterr()
        assert expected_label in captured.out.lower()

    @pytest.mark.parametrize("has_rich", [True, False], ids=["rich", "plain"])
    def test_chains_flag_true(self, has_rich: bool) -> None:
        """_report_issues returns True when chains are present."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", chain_type=ChainType.TARGET)]
        with patch("deprecate._cli._HAS_RICH", has_rich):
            assert _report_issues(results) is True


class TestReportExpiry:
    """Tests for _report_expiry_rich and _report_expiry_plain."""

    @pytest.mark.parametrize("has_rich", [True, False], ids=["rich", "plain"])
    def test_expired_message_in_output(self, capsys: pytest.CaptureFixture[str], has_rich: bool) -> None:
        """Expired message text appears in both rich and plain output via cmd_expiry."""
        with (
            patch("deprecate._cli.validate_deprecation_expiry", return_value=[_EXPIRED_MSG]),
            patch("deprecate._cli._HAS_RICH", has_rich),
        ):
            cmd_expiry(path="some_module", version="2.0")
        captured = capsys.readouterr()
        assert "expired" in captured.out.lower()


# ---------------------------------------------------------------------------
# report_issues chain parametrize extension
# ---------------------------------------------------------------------------


class TestReportIssues:
    """Tests for _report_issues covering both the rich and plain-text output paths."""

    @pytest.mark.parametrize("has_rich", [True, False], ids=["rich", "plain"])
    @pytest.mark.parametrize(
        ("results", "expected"),
        [
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])],
                True,
                id="invalid-args",
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", identity_mapping=["a"])],
                True,
                id="identity-mapping",
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", empty_mapping=True, no_effect=True)],
                True,
                id="no-effect-empty-mapping",
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", self_reference=True, no_effect=True)],
                True,
                id="no-effect-self-reference",
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", identity_mapping=["a"], no_effect=True)],
                True,
                id="no-effect-identity-only",
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", chain_type=ChainType.TARGET)],
                True,
                id="chain-target",
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", chain_type=ChainType.STACKED)],
                True,
                id="chain-stacked",
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn")],
                False,
                id="no-issues",
            ),
        ],
    )
    def test_flag(self, results: list, expected: bool, has_rich: bool) -> None:
        """_report_issues returns the correct has-issues flag for both rich and plain paths."""
        with patch("deprecate._cli._HAS_RICH", has_rich):
            assert _report_issues(results) is expected

    @pytest.mark.parametrize(
        ("has_rich", "expected_present", "expected_absent"),
        [
            pytest.param(True, "Self reference", "All identity mappings", id="rich"),
            pytest.param(False, "Reason: Self reference", "Reason: All identity mappings", id="plain"),
        ],
    )
    def test_partial_identity_with_self_reference(
        self, capsys: pytest.CaptureFixture[str], has_rich: bool, expected_present: str, expected_absent: str
    ) -> None:
        """Partial identity mappings should not be reported as all-identity in either output path."""
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
        with patch("deprecate._cli._HAS_RICH", has_rich):
            assert _report_issues(results) is True
        captured = capsys.readouterr()
        assert expected_present in captured.out
        assert expected_absent not in captured.out


def test_report_issues_dispatches() -> None:
    """Test _report_issues dispatches based on _HAS_RICH."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])]
    assert _report_issues(results) is True


# ---------------------------------------------------------------------------
# cli() entry point and backward compat
# ---------------------------------------------------------------------------


class TestCliEntryPoint:
    """Tests for the cli() Fire-based entry point."""

    def test_no_subcommand_shows_help(self) -> None:
        """cli() with no arguments prints help and returns (Fire does not exit for dict components)."""
        with patch("sys.argv", ["pydeprecate"]):
            cli()  # no SystemExit — Fire prints help and returns

    def test_help_exits_zero(self) -> None:
        """cli() with --help exits 0."""
        with patch("sys.argv", ["pydeprecate", "--help"]), pytest.raises(SystemExit) as exc_info:
            cli()
        assert exc_info.value.code == 0

    def test_check_subcommand_dispatches(self) -> None:
        """cli() with check subcommand calls cmd_check."""
        with (
            patch("sys.argv", ["pydeprecate", "check", "some_module"]),
            patch("deprecate._cli.cmd_check", return_value=0) as mock_check,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        mock_check.assert_called_once()
        assert exc_info.value.code == 0

    def test_expiry_subcommand_dispatches(self) -> None:
        """cli() with expiry subcommand calls cmd_expiry."""
        with (
            patch("sys.argv", ["pydeprecate", "expiry", "some_module", "--version", "2.0"]),
            patch("deprecate._cli.cmd_expiry", return_value=0) as mock_expiry,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        mock_expiry.assert_called_once()
        assert exc_info.value.code == 0

    def test_chains_subcommand_dispatches(self) -> None:
        """cli() with chains subcommand calls cmd_chains."""
        with (
            patch("sys.argv", ["pydeprecate", "chains", "some_module"]),
            patch("deprecate._cli.cmd_chains", return_value=0) as mock_chains,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        mock_chains.assert_called_once()
        assert exc_info.value.code == 0

    def test_all_subcommand_dispatches(self) -> None:
        """cli() with all subcommand calls cmd_all."""
        with (
            patch("sys.argv", ["pydeprecate", "all", "some_module"]),
            patch("deprecate._cli.cmd_all", return_value=0) as mock_all,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        mock_all.assert_called_once()
        assert exc_info.value.code == 0

    def test_exit_code_propagated(self) -> None:
        """cli() propagates the non-zero return code from the subcommand."""
        with (
            patch("sys.argv", ["pydeprecate", "check", "some_module"]),
            patch("deprecate._cli.cmd_check", return_value=1),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _check_expiry_from_results
# ---------------------------------------------------------------------------


class TestCheckExpiryFromResults:
    """Tests for _check_expiry_from_results helper."""

    def test_no_remove_in_skipped(self) -> None:
        """Wrappers without remove_in are silently skipped."""
        results = [DeprecationWrapperInfo(module="mod", function="fn")]
        expired = _check_expiry_from_results(results, "2.0")
        assert expired == []

    def test_not_yet_expired(self) -> None:
        """Wrapper with future remove_in is not expired."""
        config = DeprecationConfig(deprecated_in="1.0", remove_in="3.0")
        results = [DeprecationWrapperInfo(module="mod", function="fn", deprecated_info=config)]
        expired = _check_expiry_from_results(results, "2.0")
        assert expired == []

    def test_expired(self) -> None:
        """Wrapper with remove_in <= current_version is reported."""
        config = DeprecationConfig(deprecated_in="1.0", remove_in="2.0")
        results = [DeprecationWrapperInfo(module="mod", function="fn", deprecated_info=config)]
        expired = _check_expiry_from_results(results, "2.0")
        assert len(expired) == 1
        assert "fn" in expired[0]

    def test_invalid_remove_in_skipped(self) -> None:
        """Wrappers with non-PEP-440 remove_in are silently skipped."""
        config = DeprecationConfig(deprecated_in="1.0", remove_in="not-a-version")
        results = [DeprecationWrapperInfo(module="mod", function="fn", deprecated_info=config)]
        expired = _check_expiry_from_results(results, "2.0")
        assert expired == []


# ---------------------------------------------------------------------------
# TestHasRichFalse (preserved from original suite)
# ---------------------------------------------------------------------------


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
    def test_check_output_streams(self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """``cmd_check()`` prints scanning and no-results messages to stdout when rich is unavailable."""
        mock_find.return_value = []
        with patch("deprecate._cli._HAS_RICH", False):
            assert cmd_check(path="some_module") == 0
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
            assert cmd_check(path=str(tmp_path)) == 0
        captured = capsys.readouterr()
        assert "Skipping nested Python files" in captured.err
