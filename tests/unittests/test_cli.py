"""Unit tests for the CLI module (all external calls fully mocked)."""

import importlib.metadata
import sys
import types
from collections.abc import Generator
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

import deprecate
from deprecate._cli import (
    _FROM_PYPROJECT,
    _ConfigFlag,
    _ensure_utf8_streams,
    _print,
    _Reporter,
    cli,
    cmd_all,
    cmd_chains,
    cmd_check,
    cmd_expiry,
    cmd_policy,
    cmd_status,
)
from deprecate._pkg import (
    _auto_detect_version,
    _distribution_for_import,
    _load_toml,
    _read_pydeprecate_config,
    _version_from_dynamic,
    _version_from_toml,
)
from deprecate._types import DeprecationConfig, TargetMode
from deprecate.audit import ChainType, DeprecationWrapperInfo
from deprecate.audit._lifecycle import _check_expiry_for_callables

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TARGET_CHAIN = DeprecationWrapperInfo(module="mod", function="fn", chain_type=ChainType.TARGET)
_STACKED_CHAIN = DeprecationWrapperInfo(module="mod", function="fn2", chain_type=ChainType.STACKED)
_INVALID_ARGS = DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])
# Warn-only wrapper with no replacement named — trips the ``message-required`` policy rule.
_POLICY_VIOLATION = DeprecationWrapperInfo(
    module="mod",
    function="warn_only_fn",
    deprecated_info=DeprecationConfig(deprecated_in="1.0", target=TargetMode.NOTIFY),
)
# Forwarding wrapper with no scheduled removal — clean under every default policy rule.
_POLICY_CLEAN = DeprecationWrapperInfo(
    module="mod",
    function="forwarding_fn",
    deprecated_info=DeprecationConfig(deprecated_in="1.0", target=str),
)
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
        mock_find.assert_called_once_with("mypkg", recursive=True, include_members=True, exclude=[])

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_issues_file(self, mock_find: MagicMock) -> None:
        """Scanning an importable module name with no issues exits 0."""
        mock_find.return_value = []
        assert cmd_check(path="some_module") == 0
        mock_find.assert_called_once_with("some_module", recursive=True, include_members=True, exclude=[])

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
        info = DeprecationWrapperInfo(
            module="test_mod", function="test_func", identity_args_mapping=["arg"], no_effect=True
        )
        mock_find.return_value = [info]

        assert cmd_check(path="some_module") == 0

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_effect_empty_args_mapping(self, mock_find: MagicMock) -> None:
        """Empty mapping reported as no-effect reason; exits 0."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", empty_args_mapping=True, no_effect=True)
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
        """Scan failure raises; cli() converts it to sys.exit at the CLI boundary."""
        mock_find.side_effect = Exception("Boom")

        with pytest.raises(Exception, match="Boom"):
            cmd_check(path="some_module")

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_exit_zero(self, mock_find: MagicMock) -> None:
        """exit_zero=True returns 0 even with invalid args."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", invalid_args=["bad_arg"])
        mock_find.return_value = [info]

        assert cmd_check(path="some_module", exit_zero=True) == 0

    def test_file_path_rejected(self, tmp_path: Path) -> None:
        """File path raises ValueError; cli() converts it to sys.exit at the CLI boundary."""
        fpath = tmp_path / "module.py"
        fpath.touch()
        with pytest.raises(ValueError, match="File paths are not supported"):
            cmd_check(path=str(fpath))

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


class TestCmdExclude:
    """Tests for the ``--exclude`` flag shared by every subcommand and its ``[tool.pydeprecate]`` counterpart."""

    @patch("deprecate._cli.find_deprecation_wrappers", return_value=[])
    def test_pyproject_exclude_reaches_scan(
        self, mock_find: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``exclude`` in ``[tool.pydeprecate]`` is forwarded to the scan and announced in the header.

        A project keeps its fixture packages out of every audit by listing them once; each subcommand must apply
        that list without a flag and say so, or a reader cannot tell a clean scan from a skipped one.
        """
        _write_pkg(tmp_path, "mypkg", "")
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate]\nexclude = ["mypkg.tests"]\n')
        assert cmd_check(path=str(tmp_path / "mypkg")) == 0
        assert mock_find.call_args.kwargs["exclude"] == ["mypkg.tests"]
        assert "Exclude: mypkg.tests (pyproject.toml)" in capsys.readouterr().out

    @patch("deprecate._cli.find_deprecation_wrappers", return_value=[])
    def test_flag_overrides_pyproject_and_splits_commas(self, mock_find: MagicMock, tmp_path: Path) -> None:
        """A typed ``--exclude`` replaces the file's list and a comma-separated value becomes several patterns.

        Fire hands a single string over; splitting on commas lets a shell one-liner name several packages
        without Python list syntax, and the typed value must win over the file like every other setting.
        """
        _write_pkg(tmp_path, "mypkg", "")
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate]\nexclude = ["mypkg.tests"]\n')
        assert cmd_check(path=str(tmp_path / "mypkg"), exclude="mypkg.a, *.b") == 0
        assert mock_find.call_args.kwargs["exclude"] == ["mypkg.a", "*.b"]

    @pytest.mark.parametrize(
        ("body", "flag"),
        [
            pytest.param("[tool.pydeprecate]\nexclude = 7\n", _FROM_PYPROJECT, id="file-not-strings"),
            pytest.param("[tool.pydeprecate]\nexclude = [1, 2]\n", _FROM_PYPROJECT, id="file-list-of-ints"),
            pytest.param("", 7, id="flag-not-string"),
        ],
    )
    def test_malformed_exclude_exits_two(
        self, body: str, flag: _ConfigFlag, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An ``exclude`` that is not a string or a list of strings, in the file or as a flag, is a usage error.

        Silently coercing a number would exclude nothing while the author believes a package is skipped; the
        value is rejected before any scan starts and the message names where it came from.
        """
        _write_pkg(tmp_path, "mypkg", "")
        (tmp_path / "pyproject.toml").write_text(body)
        assert cmd_check(path=str(tmp_path / "mypkg"), exclude=flag) == 2
        assert "`exclude`" in capsys.readouterr().err

    @patch("deprecate._cli.find_deprecation_wrappers", return_value=[])
    def test_unknown_top_level_key_warns(
        self, mock_find: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An unrecognised key directly under ``[tool.pydeprecate]`` is reported, not ignored in silence."""
        _write_pkg(tmp_path, "mypkg", "")
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate]\nexcludes = ["mypkg.tests"]\n')
        assert cmd_check(path=str(tmp_path / "mypkg")) == 0
        assert "`excludes`" in capsys.readouterr().err
        assert mock_find.call_args.kwargs["exclude"] == []

    @patch("deprecate._cli.cmd_status", return_value=0)
    @patch("deprecate._cli.find_deprecation_wrappers", return_value=[])
    def test_all_loads_config_once(
        self, mock_find: MagicMock, mock_status: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``all`` reads ``[tool.pydeprecate]`` once and its advisory policy pass reuses it, so a warning prints once.

        ``all`` hands the loaded configuration to ``policy``; re-reading the file there would repeat every
        unknown-key advisory and make the log look like two separate problems.
        """
        _write_pkg(tmp_path, "mypkg", "")
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate]\nexclude = ["mypkg.tests"]\ntypo = 1\n')
        assert cmd_all(path=str(tmp_path / "mypkg")) == 0
        captured = capsys.readouterr()
        assert captured.err.count("`typo`") == 1
        assert "Exclude: mypkg.tests (pyproject.toml)" in captured.out
        assert mock_find.call_args.kwargs["exclude"] == ["mypkg.tests"]


class TestCmdCheck:
    """Tests for cmd_check() subcommand — the refactored core of main()."""

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_recursive_threads_flag(self, mock_find: MagicMock) -> None:
        """``recursive=False`` passes through to find_deprecation_wrappers."""
        mock_find.return_value = []
        assert cmd_check(path="some_module", recursive=False) == 0
        mock_find.assert_called_once_with("some_module", recursive=False, include_members=True, exclude=[])

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_chain_warning_exits_zero(self, mock_find: MagicMock) -> None:
        """Chains in check subcommand are warnings — do not cause exit 1."""
        mock_find.return_value = [_TARGET_CHAIN]
        assert cmd_check(path="some_module") == 0

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_chain_warning_reported(self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """Chain issues are included in check output."""
        mock_find.return_value = [_TARGET_CHAIN]
        with patch("deprecate._cli._Reporter._HAS_RICH", False):
            cmd_check(path="some_module")
        captured = capsys.readouterr()
        assert "chain" in captured.out.lower()

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_invalid_args_exits_one(self, mock_find: MagicMock) -> None:
        """Invalid args still cause exit 1 in check subcommand."""
        mock_find.return_value = [_INVALID_ARGS]
        assert cmd_check(path="some_module") == 1

    def test_pre_scanned_wrappers_skips_scan(self) -> None:
        """_wrappers provided → find_deprecation_wrappers not called."""
        with patch("deprecate._cli.find_deprecation_wrappers") as mock_find:
            result = cmd_check(path="some_module", _wrappers=[])
        mock_find.assert_not_called()
        assert result == 0


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
    def test_expired_exit_zero_exits_zero(self, mock_expiry: MagicMock) -> None:
        """exit_zero=True overrides exit code to 0 even when expired wrappers found."""
        mock_expiry.return_value = [_EXPIRED_MSG]
        assert cmd_expiry(path="some_module", version="2.0", exit_zero=True) == 0

    def test_invalid_version_exits_two(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A malformed explicit version is a usage error before the expiry scan starts.

        CI must distinguish an invalid gate configuration from an expired wrapper. The command therefore returns
        exit 2 and explains the rejected ``--version`` without importing or scanning the requested package.
        """
        assert cmd_expiry(path="some_module", version="not-a-version") == 2
        assert "Invalid `--version`" in capsys.readouterr().err

    @patch("deprecate._cli._check_expiry_for_callables")
    def test_auto_detected_unparsable_version_skips_check(
        self, mock_expiry: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An auto-detected version that is not PEP 440 skips the check rather than aborting the run.

        ``all`` resolves one version for its whole run and forwards it here, so a project stamped with
        something like a ``2024.06-nightly`` build number fails the parse deep inside the check. That is a
        fact about the scanned package, not a flag the user typed: blaming ``--version`` would mislead and
        letting the parse error escape would take the other checks' results down with it, so the gate
        degrades to an advisory skip naming the version it could not read.
        """
        mock_expiry.side_effect = ValueError("Invalid version: '2024.06-nightly'")
        result = cmd_expiry(
            path="some_module",
            version="2024.06-nightly",
            _wrappers=[DeprecationWrapperInfo(module="mod", function="fn")],
            _version_explicit=False,
        )
        assert result == 0
        assert "2024.06-nightly" in capsys.readouterr().err

    @patch("deprecate._cli.validate_deprecation_expiry")
    def test_packaging_missing_exits_zero(self, mock_expiry: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """ImportError from missing packaging library → install hint on stderr + returns 0 (advisory)."""
        mock_expiry.side_effect = ImportError("No module named 'packaging'", name="packaging")
        assert cmd_expiry(path="some_module", version="2.0") == 0
        captured = capsys.readouterr()
        assert "pyDeprecate[audit]" in captured.err

    @patch("deprecate._cli.validate_deprecation_expiry")
    def test_packaging_missing_exit_zero_exits_zero(self, mock_expiry: MagicMock) -> None:
        """ImportError with exit_zero=True → returns 0 (missing packaging is always advisory)."""
        mock_expiry.side_effect = ImportError("No module named 'packaging'", name="packaging")
        assert cmd_expiry(path="some_module", version="2.0", exit_zero=True) == 0

    @patch("deprecate._cli.validate_deprecation_expiry")
    def test_version_passed_through(self, mock_expiry: MagicMock) -> None:
        """Explicit version is forwarded to validate_deprecation_expiry."""
        mock_expiry.return_value = []
        cmd_expiry(path="some_module", version="3.0")
        mock_expiry.assert_called_once_with("some_module", "3.0", recursive=True, exclude=[])

    @patch("deprecate._cli.validate_deprecation_expiry")
    def test_no_recursive_threads_flag(self, mock_expiry: MagicMock) -> None:
        """``recursive=False`` passes through to validate_deprecation_expiry."""
        mock_expiry.return_value = []
        cmd_expiry(path="some_module", version="1.0", recursive=False)
        mock_expiry.assert_called_once_with("some_module", "1.0", recursive=False, exclude=[])

    def test_plain_directory_rejected(self, tmp_path: Path) -> None:
        """Plain directory without __init__.py raises ValueError; cli() converts it at the CLI boundary."""
        (tmp_path / "module_a.py").touch()
        with pytest.raises(ValueError, match="not supported"):
            cmd_expiry(path=str(tmp_path), version="1.0")

    @patch("deprecate._cli.validate_deprecation_expiry")
    def test_expired_reported_plain(self, mock_expiry: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """Expired messages appear in plain-text output."""
        mock_expiry.return_value = [_EXPIRED_MSG]
        with patch("deprecate._cli._Reporter._HAS_RICH", False):
            cmd_expiry(path="some_module", version="2.0")
        captured = capsys.readouterr()
        assert "expired" in captured.out.lower()

    def test_pre_scanned_wrappers_skips_scan(self) -> None:
        """_wrappers=[] provided → validate_deprecation_expiry not called; returns 0."""
        with patch("deprecate._cli.validate_deprecation_expiry") as mock_expiry:
            result = cmd_expiry(path="some_module", version="1.0", _wrappers=[])
        mock_expiry.assert_not_called()
        assert result == 0

    def test_pre_scanned_wrappers_expired_exits_one(self) -> None:
        """_wrappers with an expired wrapper and matching version → returns 1."""
        config = DeprecationConfig(deprecated_in="1.0", remove_in="2.0")
        wrapper = DeprecationWrapperInfo(module="mod", function="fn", deprecated_info=config)
        with patch("deprecate._cli.validate_deprecation_expiry") as mock_expiry:
            result = cmd_expiry(path="some_module", version="2.0", _wrappers=[wrapper])
        mock_expiry.assert_not_called()
        assert result == 1

    def test_pre_scanned_wrappers_version_none_skips(self, capsys: pytest.CaptureFixture[str]) -> None:
        """_wrappers provided but version=None → warns on stderr and returns 0."""
        with patch("deprecate._cli.validate_deprecation_expiry") as mock_expiry:
            result = cmd_expiry(path="some_module", version=None, _wrappers=[])
        mock_expiry.assert_not_called()
        assert result == 0
        captured = capsys.readouterr()
        assert "version" in captured.err.lower()

    def test_unresolved_version_prints_advisory_note(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Standalone expiry with no --version and no auto-detectable version warns instead of staying silent.

        A CI job that cannot resolve the package version (import name unmapped, no metadata, no local
        pyproject) must be told the expiry check ran without a resolved version, rather than silently
        comparing removal deadlines against an undefined version and reporting a misleading pass.
        """
        with (
            patch("deprecate._cli._auto_detect_version", return_value=None),
            patch("deprecate._cli._do_expiry", return_value=[]),
        ):
            result = cmd_expiry(path="some_module")
        assert result == 0
        assert "without a resolved version" in capsys.readouterr().err


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
    def test_chains_exit_zero_exits_zero(self, mock_chains: MagicMock) -> None:
        """exit_zero=True overrides exit code to 0 even when chains found."""
        mock_chains.return_value = [_TARGET_CHAIN]
        assert cmd_chains(path="some_module", exit_zero=True) == 0

    @patch("deprecate._cli.validate_deprecation_chains")
    def test_no_recursive_threads_flag(self, mock_chains: MagicMock) -> None:
        """``recursive=False`` passes through to validate_deprecation_chains."""
        mock_chains.return_value = []
        cmd_chains(path="some_module", recursive=False)
        mock_chains.assert_called_once_with("some_module", recursive=False, exclude=[])

    def test_plain_directory_rejected(self, tmp_path: Path) -> None:
        """Plain directory without __init__.py raises ValueError; cli() converts it at the CLI boundary."""
        (tmp_path / "module_a.py").touch()
        with pytest.raises(ValueError, match="not supported"):
            cmd_chains(path=str(tmp_path))

    @patch("deprecate._cli.validate_deprecation_chains")
    def test_chains_reported_plain(self, mock_chains: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """Chain messages appear in plain-text output."""
        mock_chains.return_value = [_TARGET_CHAIN]
        with patch("deprecate._cli._Reporter._HAS_RICH", False):
            cmd_chains(path="some_module")
        captured = capsys.readouterr()
        assert "chain" in captured.out.lower()

    @patch("deprecate._cli.validate_deprecation_chains")
    def test_stacked_chain_label(self, mock_chains: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """STACKED chain type label appears in plain-text output."""
        mock_chains.return_value = [_STACKED_CHAIN]
        with patch("deprecate._cli._Reporter._HAS_RICH", False):
            cmd_chains(path="some_module")
        captured = capsys.readouterr()
        assert "stacked" in captured.out.lower()

    def test_pre_scanned_wrappers_skips_scan(self) -> None:
        """_wrappers provided → validate_deprecation_chains not called; returns 0."""
        with patch("deprecate._cli.validate_deprecation_chains") as mock_chains:
            result = cmd_chains(path="some_module", _wrappers=[_TARGET_CHAIN])
        mock_chains.assert_not_called()
        assert result == 1

    def test_pre_scanned_wrappers_no_chains_exits_zero(self) -> None:
        """_wrappers with no chain_type entries → returns 0."""
        plain = DeprecationWrapperInfo(module="mod", function="fn")
        with patch("deprecate._cli.validate_deprecation_chains") as mock_chains:
            result = cmd_chains(path="some_module", _wrappers=[plain])
        mock_chains.assert_not_called()
        assert result == 0


# ---------------------------------------------------------------------------
# cmd_all
# ---------------------------------------------------------------------------


class TestCmdAll:
    """Tests for cmd_all() subcommand — sequential three-check composition."""

    @patch("deprecate._cli._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_all_clean_exits_zero(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """No issues in any check → exit 0."""
        mock_find.return_value = []
        assert cmd_all(path="some_module", version="1.0") == 0

    @patch("deprecate._cli._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_invalid_args_exits_one(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """Invalid args in check phase → exit 1."""
        mock_find.return_value = [_INVALID_ARGS]
        assert cmd_all(path="some_module", version="1.0") == 1

    def test_invalid_version_exits_two(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A malformed explicit version stops the aggregate command before its shared scan.

        ``all`` shares one wrapper scan across its checks, so it must reject an unusable version before touching
        the target. Exit 2 keeps that CLI usage error distinct from the checks' exit-1 findings.
        """
        assert cmd_all(path="some_module", version="not-a-version") == 2
        assert "Invalid `--version`" in capsys.readouterr().err

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_unparsable_auto_detected_version_is_not_a_flag_error(
        self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A malformed *auto-detected* version degrades the checks instead of failing as a usage error.

        A project whose version metadata is not PEP 440 — a nightly stamped ``2024.06-nightly`` — is scanned
        with no ``--version`` at all. ``all`` resolves that version once and hands it to its subcommands, so
        without the provenance travelling with it they each re-validate it as a flag the user never typed:
        the expiry check exits early reporting an invalid ``--version`` and the status table never renders.
        """
        mock_find.return_value = [_POLICY_CLEAN]
        with patch("deprecate._cli._auto_detect_version", return_value="2024.06-nightly"):
            result = cmd_all(path="some_module")
        captured = capsys.readouterr()
        assert result == 0
        assert "Invalid `--version`" not in captured.err
        assert "Could not render the deprecation table" not in captured.err
        assert "No deprecation policy violations found." in captured.out

    @patch("deprecate._cli._check_expiry_for_callables")
    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_expired_exits_one(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """Expired wrappers found → exit 1."""
        mock_find.return_value = [DeprecationWrapperInfo(module="mod", function="fn")]
        mock_expiry.return_value = [_EXPIRED_MSG]
        assert cmd_all(path="some_module", version="2.0") == 1

    @patch("deprecate._cli._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_chains_found_exits_one(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """Chains detected from wrappers by cmd_chains phase → exit 1."""
        mock_find.return_value = [_TARGET_CHAIN]
        assert cmd_all(path="some_module", version="1.0") == 1

    @patch("deprecate._cli._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_exit_zero_exits_zero(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """exit_zero=True overrides the invalid-args exit 1 to 0."""
        mock_find.return_value = [_INVALID_ARGS]
        assert cmd_all(path="some_module", version="1.0", exit_zero=True) == 0

    @patch("deprecate._cli._check_expiry_for_callables")
    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_packaging_missing_skips_expiry_continues(
        self, mock_find: MagicMock, mock_expiry: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Missing packaging library skips expiry with warning; other checks still run."""
        mock_find.return_value = [DeprecationWrapperInfo(module="mod", function="fn")]
        mock_expiry.side_effect = ImportError("No module named 'packaging'", name="packaging")
        assert cmd_all(path="some_module", version="2.0") == 0
        captured = capsys.readouterr()
        assert "packaging" in captured.err.lower()

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_version_skips_expiry(self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """When version is None and auto-detect fails, expiry warns and returns 0; cmd_all continues."""
        mock_find.return_value = [DeprecationWrapperInfo(module="mod", function="fn")]
        with patch("deprecate._cli._auto_detect_version", return_value=None):
            result = cmd_all(path="some_module")
        assert result == 0
        captured = capsys.readouterr()
        assert "version" in captured.err.lower()

    @patch("deprecate._cli._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_no_recursive_threads_flag(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """``recursive=False`` passes through to find_deprecation_wrappers."""
        mock_find.return_value = []
        cmd_all(path="some_module", version="1.0", recursive=False)
        mock_find.assert_any_call("some_module", recursive=False, include_members=True, exclude=[])

    @patch("deprecate._cli._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.validate_deprecation_chains")
    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_all_scans_once(self, mock_find: MagicMock, mock_chains: MagicMock, mock_expiry: MagicMock) -> None:
        """cmd_all calls find_deprecation_wrappers exactly once; validate_deprecation_chains not called."""
        mock_find.return_value = []
        cmd_all(path="some_module", version="1.0")
        assert mock_find.call_count == 1
        mock_chains.assert_not_called()

    @patch("deprecate._cli._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_policy_violations_stay_advisory(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """A policy violation is reported by ``all`` but never changes its exit code.

        The policy defaults encode one project's release convention (a three-minor grace window on a clean
        release boundary); folding them into ``all``'s exit code would break the CI of every repo that upgrades and does
        not share that convention, so the dedicated ``policy`` subcommand is the only gate.
        """
        mock_find.return_value = [_POLICY_VIOLATION]
        assert cmd_all(path="some_module", version="2.0") == 0

    @patch("deprecate._cli.cmd_status", side_effect=RuntimeError("table rendering failed"))
    @patch("deprecate._cli._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_status_exception_does_not_affect_exit_code(
        self,
        mock_find: MagicMock,
        mock_expiry: MagicMock,
        mock_status: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cmd_status raising inside cmd_all must not change the aggregate exit code.

        The status table is a display artifact appended after the three gates. If ``generate_deprecation_table``
        or any rendering step throws, cmd_all must still report exit 0 (no issues) or exit 1 (issues found)
        based solely on the check, expiry, and chains outcomes — not on the table step crashing.

        Scenario: clean scan (no wrappers, no issues) with a broken cmd_status. The aggregate must be
        exit 0 and the failure message must appear in stderr so the user knows the table failed.

        """
        mock_find.return_value = []
        result = cmd_all(path="some_module", version="1.0")
        assert result == 0
        captured = capsys.readouterr()
        assert "Could not render the deprecation table" in captured.err


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
        self, capsys: pytest.CaptureFixture[str], chain_type: ChainType, expected_label: str, has_rich: bool
    ) -> None:
        """Chain type label appears in both rich and plain output."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", chain_type=chain_type)]
        with patch("deprecate._cli._Reporter._HAS_RICH", has_rich):
            assert _Reporter.issues(results) is True
        captured = capsys.readouterr()
        assert expected_label in captured.out.lower()

    @pytest.mark.parametrize("has_rich", [True, False], ids=["rich", "plain"])
    def test_chains_flag_true(self, has_rich: bool) -> None:
        """_report_issues returns True when chains are present."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", chain_type=ChainType.TARGET)]
        with patch("deprecate._cli._Reporter._HAS_RICH", has_rich):
            assert _Reporter.issues(results) is True


class TestReportExpiry:
    """Tests for _report_expiry_rich and _report_expiry_plain."""

    @pytest.mark.parametrize("has_rich", [True, False], ids=["rich", "plain"])
    def test_expired_message_in_output(self, capsys: pytest.CaptureFixture[str], has_rich: bool) -> None:
        """Expired message text appears in both rich and plain output via cmd_expiry."""
        with (
            patch("deprecate._cli.validate_deprecation_expiry", return_value=[_EXPIRED_MSG]),
            patch("deprecate._cli._Reporter._HAS_RICH", has_rich),
        ):
            cmd_expiry(path="some_module", version="2.0")
        captured = capsys.readouterr()
        assert "expired" in captured.out.lower()


# ---------------------------------------------------------------------------
# report_issues chain parametrize extension
# ---------------------------------------------------------------------------


class TestReportPolicy:
    """Tests for _Reporter.policy() directly, isolated from cmd_policy's scan logic."""

    @pytest.mark.parametrize(
        "has_rich",
        [
            pytest.param(
                True,
                id="rich",
                marks=pytest.mark.skipif(
                    _Reporter._RichTable is None, reason="rich not installed — forced has_rich=True is unsupported"
                ),
            ),
            pytest.param(False, id="plain"),
        ],
    )
    def test_rule_slug_prefix_survives_output(self, capsys: pytest.CaptureFixture[str], has_rich: bool) -> None:
        """The `[rule-slug]`-style prefix renders literally in both the rich and plain-text reporters.

        Rich parses square brackets in a cell as style markup, which would swallow a `[min-grace]` prefix
        silently. Calling `_Reporter.policy()` directly (rather than through `cmd_policy()`) isolates the
        reporter's own escaping behavior from the surrounding scan, so a regression here cannot hide behind
        mocked scan data used elsewhere in the test file. The forced ``has_rich=True`` case needs the real
        ``rich`` package on the interpreter — forcing ``_HAS_RICH`` alone leaves ``_RichTable``/``_rich_box``
        unset when ``rich`` was never importable, so that case is skipped in a local checkout without the
        ``cli`` extra.
        """
        message = "[min-grace] Callable `pkg.old_fn` is deprecated in `1.0` and already scheduled for removal in `1.0`."
        with patch("deprecate._cli._Reporter._HAS_RICH", has_rich):
            _Reporter.policy([message])
        captured = capsys.readouterr()
        assert "[min-grace]" in captured.out


class TestReportIssues:
    """Tests for _report_issues covering both the rich and plain-text output paths."""

    @pytest.mark.parametrize("has_rich", [True, False], ids=["rich", "plain"])
    @pytest.mark.parametrize(
        ("results", "expected"),
        [
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])], True, id="invalid-args"
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", identity_args_mapping=["a"])],
                True,
                id="identity-mapping",
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", empty_args_mapping=True, no_effect=True)],
                True,
                id="no-effect-empty-mapping",
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", self_reference=True, no_effect=True)],
                True,
                id="no-effect-self-reference",
            ),
            pytest.param(
                [DeprecationWrapperInfo(module="mod", function="fn", identity_args_mapping=["a"], no_effect=True)],
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
            pytest.param([DeprecationWrapperInfo(module="mod", function="fn")], False, id="no-issues"),
        ],
    )
    def test_flag(self, results: list, expected: bool, has_rich: bool) -> None:
        """_report_issues returns the correct has-issues flag for both rich and plain paths."""
        with patch("deprecate._cli._Reporter._HAS_RICH", has_rich):
            assert _Reporter.issues(results) is expected

    @pytest.mark.parametrize(
        ("has_rich", "expected_present", "expected_absent"),
        [
            pytest.param(True, "Self reference", "All identity mappings", id="rich"),
            pytest.param(False, "Self reference", "All identity mappings", id="plain"),
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
                identity_args_mapping=["a"],
                self_reference=True,
                no_effect=True,
            )
        ]
        with patch("deprecate._cli._Reporter._HAS_RICH", has_rich):
            assert _Reporter.issues(results) is True
        captured = capsys.readouterr()
        assert expected_present in captured.out
        assert expected_absent not in captured.out


def test_report_issues_dispatches() -> None:
    """Test _report_issues dispatches based on _HAS_RICH."""
    results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])]
    assert _Reporter.issues(results) is True


# ---------------------------------------------------------------------------
# cli() entry point and backward compat
# ---------------------------------------------------------------------------


class TestCliEntryPoint:
    """Tests for the cli() Fire-based entry point."""

    def test_no_subcommand_shows_help(self) -> None:
        """``cli()`` with no arguments prints help and returns (Fire does not exit for dict components)."""
        with patch("sys.argv", ["pydeprecate"]):
            cli()  # no SystemExit — Fire prints help and returns

    def test_help_exits_zero(self) -> None:
        """``cli()`` with --help exits 0."""
        with patch("sys.argv", ["pydeprecate", "--help"]), pytest.raises(SystemExit) as exc_info:
            cli()
        assert exc_info.value.code == 0

    def test_check_subcommand_dispatches(self) -> None:
        """``cli()`` with check subcommand calls cmd_check and exits with the captured return code."""
        with (
            patch("sys.argv", ["pydeprecate", "check", "some_module"]),
            patch("deprecate._cli.cmd_check", return_value=0) as mock_check,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        mock_check.assert_called_once()
        assert exc_info.value.code == 0

    def test_expiry_subcommand_dispatches(self) -> None:
        """``cli()`` with expiry subcommand calls cmd_expiry and exits with the captured return code."""
        with (
            patch("sys.argv", ["pydeprecate", "expiry", "some_module", "--version", "2.0"]),
            patch("deprecate._cli.cmd_expiry", return_value=0) as mock_expiry,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        mock_expiry.assert_called_once()
        assert exc_info.value.code == 0

    def test_chains_subcommand_dispatches(self) -> None:
        """``cli()`` with chains subcommand calls cmd_chains and exits with the captured return code."""
        with (
            patch("sys.argv", ["pydeprecate", "chains", "some_module"]),
            patch("deprecate._cli.cmd_chains", return_value=0) as mock_chains,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        mock_chains.assert_called_once()
        assert exc_info.value.code == 0

    def test_all_subcommand_dispatches(self) -> None:
        """``cli()`` with all subcommand calls cmd_all and exits with the captured return code."""
        with (
            patch("sys.argv", ["pydeprecate", "all", "some_module"]),
            patch("deprecate._cli.cmd_all", return_value=0) as mock_all,
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        mock_all.assert_called_once()
        assert exc_info.value.code == 0

    def test_exit_code_propagated(self) -> None:
        """``cli()`` propagates the non-zero exit code from the subcommand."""
        with (
            patch("sys.argv", ["pydeprecate", "check", "some_module"]),
            patch("deprecate._cli.cmd_check", return_value=1),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        assert exc_info.value.code == 1

    @patch("deprecate._cli.find_deprecation_wrappers", return_value=[])
    def test_unknown_flag_exits_nonzero(self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """``cli()`` with an unknown flag exits non-zero via Fire's unconsumed-argument check.

        A CI job invoking ``pydeprecate check pkg --bogusflag`` must fail loudly: if the exit code were
        produced inside the Fire trace, Fire would never reach its "Could not consume arg" check and the
        typo'd flag would be silently ignored with exit 0, letting the gate pass on unvalidated input.

        """
        with (
            patch("sys.argv", ["pydeprecate", "check", "some_module", "--bogusflag"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        assert exc_info.value.code == 2
        assert "Could not consume arg" in capsys.readouterr().err

    @patch("deprecate._cli.validate_deprecation_expiry", return_value=[])
    def test_misspelled_value_flag_exits_nonzero(self, mock_expiry: MagicMock) -> None:
        """``cli()`` with a misspelled ``--verison`` flag exits non-zero instead of dropping the value.

        A user running ``pydeprecate expiry pkg --verison 2.0`` intends to pin the comparison version;
        silently dropping the typo'd flag would check expiry against an auto-detected (wrong) version and
        report a false pass or false fail with no diagnostic.

        """
        with (
            patch("sys.argv", ["pydeprecate", "expiry", "some_module", "--verison", "2.0"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli()
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# _auto_detect_version (package helper)
# ---------------------------------------------------------------------------


class TestAutoDetectVersion:
    """Tests for _auto_detect_version() — the *path* argument must only be honored for real filesystem paths."""

    def test_module_name_ignores_cwd_pyproject(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A module *name* that is not an existing path must not trigger the cwd pyproject.toml walk-up.

        A CI job runs ``pydeprecate expiry somepkg`` from a checkout of a completely unrelated project.
        Walking up from the cwd would find that project's ``pyproject.toml`` and compare deprecation
        deadlines against the wrong version, producing false CI failures or false passes.

        """
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "fakeproj"\nversion = "9.9.9"\n')
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            importlib.metadata, "version", MagicMock(side_effect=importlib.metadata.PackageNotFoundError)
        )
        assert _auto_detect_version("somepkg", path="somepkg") is None

    def test_existing_path_uses_local_pyproject(self, tmp_path: Path) -> None:
        """An existing package directory still resolves the version from the nearby ``pyproject.toml``.

        In a development checkout the local ``pyproject.toml`` version may differ from the installed
        distribution; scanning the checkout directory must keep preferring the local file.

        """
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "mypkg"\nversion = "7.7.7"\n')
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").touch()
        assert _auto_detect_version("mypkg", path=str(pkg)) == "7.7.7"

    def test_none_path_falls_back_to_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without a path, the installed distribution metadata provides the version."""
        monkeypatch.setattr(importlib.metadata, "version", MagicMock(return_value="1.2.3"))
        assert _auto_detect_version("somepkg", path=None) == "1.2.3"

    def test_import_name_maps_to_distribution(self) -> None:
        """Auto-detect resolves the version even when the distribution name differs from the import name.

        ``importlib.metadata.version`` needs the *distribution* name (``pyDeprecate``) but the CLI is
        handed the *import* name (``deprecate``) — the same mismatch as Pillow/PIL and scikit-learn/sklearn.
        Without the import→distribution mapping this whole class of packages silently yields ``None`` and
        the expiry gate runs against an undefined version.
        """
        expected = importlib.metadata.version("pyDeprecate")
        assert _auto_detect_version("deprecate", path=None) == expected

    def test_distribution_for_import_resolves_known_package(self) -> None:
        """_distribution_for_import maps the ``deprecate`` import name to its ``pyDeprecate`` distribution."""
        assert _distribution_for_import("deprecate") == "pyDeprecate"

    def test_distribution_for_import_unknown_returns_none(self) -> None:
        """An import name provided by no installed distribution resolves to ``None``."""
        assert _distribution_for_import("nonexistent_import_xyz") is None

    def test_distribution_for_import_uses_top_level_txt_fallback(self) -> None:
        """_distribution_for_import falls back to top_level.txt scanning on Python <3.11.

        ``packages_distributions`` was added in Python 3.11. When it is absent (simulated by
        patching the attribute to ``None``), ``_distribution_for_import`` must fall back to
        ``_distribution_from_top_level``, which reads each distribution's ``top_level.txt``.
        pyDeprecate ships ``top_level.txt`` listing ``deprecate``, so the mapping must still
        resolve correctly even without the faster stdlib helper.

        ``create=True`` is required because on Python <3.11 the attribute does not exist at all
        and ``patch`` would raise ``AttributeError`` with ``create=False`` (the default).

        """
        with patch("deprecate._pkg.importlib.metadata.packages_distributions", None, create=True):
            result = _distribution_for_import("deprecate")
        assert result == "pyDeprecate"


@pytest.fixture
def _clean_sys_modules() -> Generator[None, None, None]:
    """Remove any modules imported during the test so tmp_path packages don't leak into ``sys.modules``.

    ``_version_from_dynamic`` imports the scanned package by name; without cleanup a later test importing a
    same-named package would get the cached (deleted-tmp-dir) module. Records the module set before the test and
    drops every newly-added entry afterwards.

    """
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        del sys.modules[name]


def _write_pkg(root: Path, name: str, init_body: str) -> Path:
    """Create an importable ``name`` package under *root* with *init_body* as its ``__init__.py`` and return it."""
    pkg = root / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(init_body)
    return pkg


class TestLoadToml:
    """Tests for _load_toml() — the tolerant ``pyproject.toml`` reader used by version auto-detection."""

    def test_reads_static_project_table(self, tmp_path: Path) -> None:
        """A well-formed pyproject with a ``[project]`` table is parsed into a nested dict."""
        toml = tmp_path / "pyproject.toml"
        toml.write_text('[project]\nname = "mypkg"\nversion = "1.2.3"\n')
        data = _load_toml(str(toml))
        assert data["project"]["version"] == "1.2.3"

    def test_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        """A non-existent path is swallowed and yields an empty dict rather than raising.

        The CLI probes for a ``pyproject.toml`` that may not exist; the reader must degrade to ``{}`` so callers
        fall back to installed distribution metadata instead of crashing.

        """
        assert _load_toml(str(tmp_path / "does_not_exist.toml")) == {}

    def test_malformed_toml_returns_empty_dict(self, tmp_path: Path) -> None:
        """Invalid TOML syntax is caught and yields an empty dict."""
        toml = tmp_path / "pyproject.toml"
        toml.write_text("this is = = not valid toml [[[")
        assert _load_toml(str(toml)) == {}


class TestReadPydeprecateConfig:
    """Tests for _read_pydeprecate_config() — locates ``[tool.pydeprecate]`` in the nearest ``pyproject.toml``."""

    def test_reads_nearest_table_with_raw_values(self, tmp_path: Path) -> None:
        """The table is returned with its TOML values untouched, nested ``policy`` included, and its file path.

        A project keeps its settings next to its other tool tables; the CLI needs the raw values to validate them
        itself, and the path so its usage errors can point at the file a reader has to fix.
        """
        toml = tmp_path / "pyproject.toml"
        toml.write_text(
            '[tool.pydeprecate]\nexclude = ["pkg.tests"]\npolicy.min-grace = 0.3\npolicy.message-required = false\n'
        )
        table, found = _read_pydeprecate_config(str(tmp_path))
        assert table == {"exclude": ["pkg.tests"], "policy": {"min-grace": 0.3, "message-required": False}}
        assert found == str(toml)

    def test_walks_up_two_levels(self, tmp_path: Path) -> None:
        """A ``src/pkg`` scan path finds the project root's table two directories above it.

        The scanned path is usually the package directory, not the repo root where ``pyproject.toml`` lives;
        the walk-up mirrors the one version auto-detection already performs.
        """
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate.policy]\nmin-grace = "1.0"\n')
        pkg = tmp_path / "src" / "pkg"
        pkg.mkdir(parents=True)
        table, _ = _read_pydeprecate_config(str(pkg))
        assert table == {"policy": {"min-grace": "1.0"}}

    def test_skips_pyproject_without_table(self, tmp_path: Path) -> None:
        """A nearer ``pyproject.toml`` without the table is skipped in favour of a parent that declares it.

        A sub-package with its own build metadata but no pydeprecate section inherits the repository settings
        rather than silently resetting them to the built-in defaults.
        """
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmessage-required = false\n")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "pyproject.toml").write_text('[project]\nname = "sub"\n')
        table, found = _read_pydeprecate_config(str(sub))
        assert table == {"policy": {"message-required": False}}
        assert found == str(tmp_path / "pyproject.toml")

    def test_no_table_anywhere_returns_empty(self, tmp_path: Path) -> None:
        """A tree with no ``[tool.pydeprecate]`` yields ``({}, None)`` so every setting falls back to its default."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "plain"\n')
        assert _read_pydeprecate_config(str(tmp_path)) == ({}, None)


@pytest.mark.usefixtures("_clean_sys_modules")
class TestVersionFromDynamic:
    """Tests for _version_from_dynamic() — imports a package to read its ``__version__`` for dynamic versions."""

    def test_imports_package_and_reads_version(self, tmp_path: Path) -> None:
        """A ``dynamic = ["version"]`` package exposing ``__version__`` resolves to that string.

        A src-less project whose version lives only in ``pkg.__version__`` (e.g. set from a build backend) must be
        importable via the temporarily-extended ``sys.path`` and have its runtime attribute read back.

        """
        _write_pkg(tmp_path, "dynpkg_read", '__version__ = "3.4.5"\n')
        assert _version_from_dynamic("dynpkg_read", str(tmp_path), str(tmp_path)) == "3.4.5"

    def test_returns_none_when_import_fails(self, tmp_path: Path) -> None:
        """A package that raises on import yields ``None`` instead of propagating the error.

        User packages run arbitrary top-level code; an import that blows up must downgrade auto-detection to
        ``None`` (caller then falls back to metadata) rather than crash the whole ``expiry`` check.

        """
        _write_pkg(tmp_path, "dynpkg_boom", 'raise RuntimeError("boom at import time")\n')
        assert _version_from_dynamic("dynpkg_boom", str(tmp_path), str(tmp_path)) is None

    def test_returns_none_when_version_not_a_string(self, tmp_path: Path) -> None:
        """A package whose ``__version__`` is not a ``str`` (or is absent) resolves to ``None``."""
        _write_pkg(tmp_path, "dynpkg_badtype", "__version__ = 405\n")
        assert _version_from_dynamic("dynpkg_badtype", str(tmp_path), str(tmp_path)) is None

    def test_restores_sys_path_after_import(self, tmp_path: Path) -> None:
        """``sys.path`` is returned to its original contents even after a successful import.

        The helper prepends several candidate roots to ``sys.path``; leaving them behind would let unrelated later
        imports resolve against a tmp directory. The ``finally`` restore must leave ``sys.path`` byte-for-byte.

        """
        original = list(sys.path)
        _write_pkg(tmp_path, "dynpkg_syspath", '__version__ = "9.0.0"\n')
        _version_from_dynamic("dynpkg_syspath", str(tmp_path), str(tmp_path))
        assert sys.path == original


class TestVersionFromToml:
    """Tests for _version_from_toml() — extracts ``[project].version``, falling back to dynamic import."""

    def test_static_version_returned_directly(self, tmp_path: Path) -> None:
        """A literal ``version = "x"`` under ``[project]`` is returned without importing anything."""
        toml = tmp_path / "pyproject.toml"
        toml.write_text('[project]\nname = "mypkg"\nversion = "2.1.0"\n')
        assert _version_from_toml(str(toml), str(tmp_path)) == "2.1.0"

    def test_missing_project_table_returns_none(self, tmp_path: Path) -> None:
        """A pyproject without a ``[project]`` table yields ``None``."""
        toml = tmp_path / "pyproject.toml"
        toml.write_text('[build-system]\nrequires = ["setuptools"]\n')
        assert _version_from_toml(str(toml), str(tmp_path)) is None

    @pytest.mark.usefixtures("_clean_sys_modules")
    def test_dynamic_version_falls_back_to_import(self, tmp_path: Path) -> None:
        """A ``dynamic = ["version"]`` project resolves the version by importing the named package.

        Build backends often declare the version dynamic and compute it at build time; the auditor reproduces that
        by importing ``[project].name`` and reading ``__version__``.

        """
        _write_pkg(tmp_path, "tomldynpkg", '__version__ = "5.6.7"\n')
        toml = tmp_path / "pyproject.toml"
        toml.write_text('[project]\nname = "tomldynpkg"\ndynamic = ["version"]\n')
        assert _version_from_toml(str(toml), str(tmp_path)) == "5.6.7"

    def test_dynamic_without_name_returns_none(self, tmp_path: Path) -> None:
        """A dynamic-version project missing ``[project].name`` cannot be imported and yields ``None``."""
        toml = tmp_path / "pyproject.toml"
        toml.write_text('[project]\ndynamic = ["version"]\n')
        assert _version_from_toml(str(toml), str(tmp_path)) is None


# ---------------------------------------------------------------------------
# _check_expiry_for_callables (audit helper)
# ---------------------------------------------------------------------------


class TestCheckExpiryForCallables:
    """Tests for _check_expiry_for_callables helper (from deprecate.audit)."""

    def test_no_remove_in_skipped(self) -> None:
        """Wrappers without remove_in are silently skipped."""
        results = [DeprecationWrapperInfo(module="mod", function="fn")]
        expired = _check_expiry_for_callables(results, "2.0")
        assert expired == []

    def test_not_yet_expired(self) -> None:
        """Wrapper with future remove_in is not expired."""
        config = DeprecationConfig(deprecated_in="1.0", remove_in="3.0")
        results = [DeprecationWrapperInfo(module="mod", function="fn", deprecated_info=config)]
        expired = _check_expiry_for_callables(results, "2.0")
        assert expired == []

    def test_expired(self) -> None:
        """Wrapper with remove_in <= current_version is reported."""
        config = DeprecationConfig(deprecated_in="1.0", remove_in="2.0")
        results = [DeprecationWrapperInfo(module="mod", function="fn", deprecated_info=config)]
        expired = _check_expiry_for_callables(results, "2.0")
        assert len(expired) == 1
        assert "fn" in expired[0]

    def test_invalid_remove_in_skipped(self) -> None:
        """Wrappers with non-PEP-440 remove_in are silently skipped."""
        config = DeprecationConfig(deprecated_in="1.0", remove_in="not-a-version")
        results = [DeprecationWrapperInfo(module="mod", function="fn", deprecated_info=config)]
        expired = _check_expiry_for_callables(results, "2.0")
        assert expired == []


# ---------------------------------------------------------------------------
# TestHasRichFalse (preserved from original suite)
# ---------------------------------------------------------------------------


class TestHasRichFalse:
    """Tests for the plain-text fallback path when ``_HAS_RICH`` is ``False``."""

    @pytest.mark.parametrize(("stderr", "stream"), [(False, "out"), (True, "err")])
    def test_print_routes_to_builtin_print(self, capsys: pytest.CaptureFixture[str], stderr: bool, stream: str) -> None:
        """``_print()`` falls back to built-in ``print()`` when rich is unavailable."""
        with patch("deprecate._cli._Reporter._HAS_RICH", False):
            _print("hello", stderr=stderr)
        captured = capsys.readouterr()
        assert "hello" in getattr(captured, stream)

    def test_report_issues_dispatches_to_plain(self) -> None:
        """``_Reporter.issues()`` delegates to the plain reporter when rich is unavailable."""
        results = [DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])]
        with patch("deprecate._cli._Reporter._HAS_RICH", False):
            assert _Reporter.issues(results) is True

    @patch("deprecate._cli.find_deprecation_wrappers")
    def test_check_output_streams(self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """``cmd_check()`` prints scanning and no-results messages to stdout when rich is unavailable."""
        mock_find.return_value = []
        with patch("deprecate._cli._Reporter._HAS_RICH", False):
            assert cmd_check(path="some_module") == 0
        captured = capsys.readouterr()
        assert "Scanning:" in captured.out
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
        with patch("deprecate._cli._Reporter._HAS_RICH", False):
            assert cmd_check(path=str(tmp_path)) == 0
        captured = capsys.readouterr()
        assert "Skipping nested Python files" in captured.err


class TestCmdStatus:
    """Tests for cmd_status() — markdown table rendering."""

    @patch("deprecate._cli.generate_deprecation_table", return_value="| A | B |\n| :--- | :--- |\n| `fn` | callable |")
    @patch("deprecate._cli.find_deprecation_wrappers", return_value=[])
    def test_exits_0_and_prints_table(
        self, mock_find: MagicMock, mock_gen: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cmd_status exits 0 and prints the generated table to stdout."""
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").touch()

        with patch("deprecate._cli._Reporter._HAS_RICH", False):
            result = cmd_status(path=str(pkg))

        assert result == 0
        assert "| A | B |" in capsys.readouterr().out

    @patch("deprecate._cli.generate_deprecation_table", return_value="| A |")
    def test_pre_scanned_wrappers_skip_scan(
        self, mock_gen: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Providing _wrappers bypasses find_deprecation_wrappers."""
        wrappers = [DeprecationWrapperInfo(module="m", function="f")]
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").touch()

        with patch("deprecate._cli.find_deprecation_wrappers") as mock_find:
            with patch("deprecate._cli._Reporter._HAS_RICH", False):
                cmd_status(path=str(pkg), _wrappers=wrappers)
            mock_find.assert_not_called()
        mock_gen.assert_called_once()
        _, kwargs = mock_gen.call_args
        assert kwargs.get("_wrappers") == wrappers

    @patch("deprecate._cli.generate_deprecation_table", return_value="| A |")
    @patch("deprecate._cli.find_deprecation_wrappers", return_value=[])
    def test_invalid_style_falls_back_to_compact(
        self, mock_find: MagicMock, mock_gen: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Unknown style value falls back to compact with a stderr warning and exits 0."""
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").touch()

        with patch("deprecate._cli._Reporter._HAS_RICH", False):
            result = cmd_status(path=str(pkg), style="bogus")

        assert result == 0
        assert "falling back" in capsys.readouterr().err.lower()

    @patch("deprecate._cli.generate_deprecation_table", return_value="| Col |\n| :--- |\n| `fn` |")
    @patch("deprecate._cli.find_deprecation_wrappers", return_value=[])
    def test_output_writes_to_file(
        self, mock_find: MagicMock, mock_gen: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--output writes the markdown to a file in addition to stdout."""
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").touch()
        out_file = tmp_path / "DEPRECATIONS.md"

        with patch("deprecate._cli._Reporter._HAS_RICH", False):
            result = cmd_status(path=str(pkg), output=str(out_file))

        assert result == 0
        assert out_file.exists()
        assert "| Col |" in out_file.read_text()


class _NoneEncodingStream:
    """Stream stub whose ``encoding`` attribute is present but ``None`` (fixture)."""

    encoding = None

    def __init__(self) -> None:
        self.reconfigured_to: Optional[str] = None

    def reconfigure(self, *, encoding: str) -> None:
        """Record the encoding a caller reconfigured the stream to."""
        self.reconfigured_to = encoding


class TestEnsureUtf8StreamsNoneEncoding:
    """A stream whose ``encoding`` is ``None`` must not crash the UTF-8 reconfigure pass."""

    def test_none_encoding_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A ``None`` encoding is tolerated (treated as UTF-8, so reconfigure is skipped) rather than crashing.

        ``getattr(stream, "encoding", "utf-8")`` returned ``None`` (the attribute exists but is ``None``) and the
        subsequent ``.lower()`` raised ``AttributeError``; the ``or "utf-8"`` guard folds ``None`` to ``"utf-8"``
        so the stream is considered already-UTF-8 and left untouched instead of blowing up.
        """
        stream = _NoneEncodingStream()
        monkeypatch.setattr(sys, "stdout", stream)
        monkeypatch.setattr(sys, "stderr", stream)
        _ensure_utf8_streams()
        assert stream.reconfigured_to is None


class TestCliEmptyExceptionMessage:
    """An exception with an empty message must still produce a non-blank stderr exit line."""

    def test_empty_message_exception_exits_with_type_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A subcommand raising a message-less exception exits with the exception type name, not a blank line.

        ``sys.exit(str(exc))`` on such an exception exited 1 with nothing printed, giving CI no clue what failed;
        prefixing the exception type guarantees a meaningful stderr line.
        """

        class _SilentError(Exception):
            pass

        def _raise(*_args: object, **_kwargs: object) -> None:
            raise _SilentError

        fake_fire = types.SimpleNamespace(Fire=_raise)
        monkeypatch.setitem(sys.modules, "fire", fake_fire)
        monkeypatch.setattr(sys, "argv", ["pydeprecate", "check", "."])
        with pytest.raises(SystemExit) as exc_info:
            cli()
        assert "_SilentError" in str(exc_info.value.code)


class TestCmdPolicy:
    """Tests for cmd_policy() subcommand."""

    def test_no_violations_exits_zero(self) -> None:
        """A package whose wrappers all satisfy the policy exits 0.

        This is the steady state a team lives in after adopting the gate: the run has to stay quiet and green,
        or the check gets removed from CI within a release.
        """
        assert cmd_policy(path="some_module", _wrappers=[]) == 0

    def test_violations_exit_one(self) -> None:
        """A wrapper breaking a rule fails the gate so the PR that introduced it cannot merge.

        The message-required rule is the one a hurried deprecation trips most often — a warning shipped without
        a replacement named, which reads as complete until a caller asks what to migrate to.
        """
        assert cmd_policy(path="some_module", _wrappers=[_POLICY_VIOLATION]) == 1

    def test_exit_zero_downgrades_violations(self) -> None:
        """``exit_zero=True`` reports the violations but never blocks the pipeline.

        Teams adopting the gate on an existing codebase run it advisory-first to see the backlog before making
        it blocking; without this the first run would fail every branch at once.
        """
        assert cmd_policy(path="some_module", exit_zero=True, _wrappers=[_POLICY_VIOLATION]) == 0

    def test_invalid_grace_specification_exits_two(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A malformed ``--min-grace`` reports the accepted format and exits 2 without scanning.

        Exit 2 (usage error) separates "you configured the gate wrong" from exit 1 ("your code broke the
        policy") — a typo must never be reported as a clean policy run.
        """
        assert cmd_policy(path="some_module", min_grace="1 minor", _wrappers=[]) == 2
        assert "min_grace" in capsys.readouterr().err

    @patch("deprecate._cli._check_policy_for_callables")
    def test_packaging_missing_exits_zero(self, mock_policy: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """A missing ``packaging`` library prints the install hint and stays advisory (exit 0).

        The version comparison needs the optional ``audit`` extra; a CI job that installed only the base package
        should be told what to add rather than failing on a check it never ran.
        """
        mock_policy.side_effect = ImportError("No module named 'packaging'", name="packaging")
        assert cmd_policy(path="some_module", _wrappers=[]) == 0
        assert "pyDeprecate[audit]" in capsys.readouterr().err

    @patch("deprecate._cli._check_policy_for_callables")
    def test_packaging_missing_keeps_message_required_blocking(
        self, mock_policy: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A missing optional dependency skips the grace-window rule but not missing migration guidance.

        A base-install CI job may lack ``packaging`` while still using the packaging-free message rule. Its
        warning must name the skipped version rule, then fail with exit 1 when a wrapper omits a replacement.
        """
        mock_policy.side_effect = [
            ImportError("No module named 'packaging'", name="packaging"),
            ["[message-required] Callable `mod.warn_only_fn` warns without naming a replacement"],
        ]

        assert cmd_policy(path="some_module", _wrappers=[_POLICY_VIOLATION]) == 1
        captured = capsys.readouterr()
        assert "message-required" in captured.out
        assert "min-grace" in captured.err

    @patch("deprecate._cli._check_policy_for_callables")
    def test_unrelated_import_error_propagates(self, mock_policy: MagicMock) -> None:
        """An ImportError from the scanned package itself is not swallowed as a missing-``packaging`` case.

        Reporting a broken user import as "install the audit extra" would send the reader to fix the wrong
        thing entirely, so only genuine ``packaging`` failures are converted to the advisory path.
        """
        mock_policy.side_effect = ImportError("No module named 'user_dep'", name="user_dep")
        with pytest.raises(ImportError, match="user_dep"):
            cmd_policy(path="some_module", _wrappers=[])

    def test_violations_reported_plain(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Violation messages, including the rule slug, appear in the plain-text (no-rich) output.

        The bracketed slug is what a CI log grep filters on; a renderer that dropped it would leave the log
        readable but unfilterable, which is how the rich renderer behaved before the escape was added.
        """
        with patch("deprecate._cli._Reporter._HAS_RICH", False):
            cmd_policy(path="some_module", _wrappers=[_POLICY_VIOLATION])
        captured = capsys.readouterr()
        assert "policy violations" in captured.out.lower()
        assert "[message-required]" in captured.out

    def test_rule_slug_survives_rich_rendering(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The ``[rule-slug]`` prefix survives the rich table renderer instead of being read as style markup.

        Rich parses square brackets in a cell as a style tag, so an unescaped message silently lost its rule
        name — the violation still printed, but no reader or grep could tell which policy it broke.
        """
        if not _Reporter._HAS_RICH:
            pytest.skip("rich is not installed")
        cmd_policy(path="some_module", _wrappers=[_POLICY_VIOLATION])
        assert "message-required" in capsys.readouterr().out

    def test_disabled_rules_pass_through(self) -> None:
        """Disabling both rules leaves nothing to report, even for a wrapper that breaks both of them.

        The flags are the escape hatch for a project whose conventions differ; if a disabled rule still fired,
        the gate could not be adopted incrementally.
        """
        result = cmd_policy(path="some_module", min_grace=None, message_required=False, _wrappers=[_POLICY_VIOLATION])
        assert result == 0

    def test_header_names_built_in_source(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Without flags or a ``pyproject.toml`` the header reports both rules as built-in defaults.

        A reader of a CI log has to know which convention the gate applied and where it came from before deciding
        whether to change the code or the configuration.
        """
        cmd_policy(path="some_module", _wrappers=[])
        assert "min-grace=0.3 (built-in)" in capsys.readouterr().out

    def test_pyproject_table_supplies_defaults(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """``[tool.pydeprecate.policy]`` next to the scanned package replaces the built-in defaults.

        A project that wants its policy versioned with the code declares it once in ``pyproject.toml`` and every
        bare ``pydeprecate policy`` invocation — local or CI — applies the same rules without flags.
        """
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmessage-required = false\n")
        assert cmd_policy(path=str(tmp_path), _wrappers=[_POLICY_VIOLATION]) == 0
        assert "message-required=False (pyproject.toml)" in capsys.readouterr().out

    def test_flag_overrides_pyproject(self, tmp_path: Path) -> None:
        """An explicit flag wins over the ``pyproject.toml`` value for the same rule.

        A one-off stricter run (``--message-required=True`` on a project that disabled the rule) must not be
        silently overruled by the file, or the flag would become a no-op nobody can trust.
        """
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmessage-required = false\n")
        assert cmd_policy(path=str(tmp_path), message_required=True, _wrappers=[_POLICY_VIOLATION]) == 1

    def test_pyproject_false_disables_grace_window(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """``min-grace = false`` in TOML stands in for the ``None`` a flag would pass.

        TOML has no null, so the file needs its own spelling for "rule off"; ``false`` mirrors the boolean rule
        and must reach the engine as a disabled window rather than as a malformed delta (exit 2).
        """
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmin-grace = false\n")
        assert cmd_policy(path=str(tmp_path), _wrappers=[]) == 0
        assert "min-grace=None (pyproject.toml)" in capsys.readouterr().out

    def test_malformed_pyproject_value_exits_two_naming_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A malformed ``min-grace`` in ``pyproject.toml`` exits 2 and names the file it came from.

        The value did not come from the command line, so an error that only quoted the bad delta would send the
        reader hunting through flags that were never typed.
        """
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate.policy]\nmin-grace = "1.2"\n')
        assert cmd_policy(path=str(tmp_path), _wrappers=[]) == 2
        assert "pyproject.toml" in capsys.readouterr().err

    def test_non_bool_message_required_exits_two(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A non-boolean ``message-required`` in ``pyproject.toml`` is a usage error, not a truthy toggle.

        ``message-required = "no"`` would be truthy if passed straight through, enabling the rule the author
        meant to switch off; rejecting it keeps a typo from silently inverting the configuration.
        """
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate.policy]\nmessage-required = "no"\n')
        assert cmd_policy(path=str(tmp_path), _wrappers=[]) == 2
        assert "message_required" in capsys.readouterr().err

    def test_unknown_pyproject_key_warns(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """An unrecognised key in the table is reported on stderr instead of being ignored.

        ``min_grace`` (underscore) is the likeliest typo; without the warning it would leave the built-in window in
        force while the author believes they configured a different one.
        """
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate.policy]\nmin_grace = "1"\n')
        assert cmd_policy(path=str(tmp_path), _wrappers=[]) == 0
        captured = capsys.readouterr()
        assert "`min_grace`" in captured.err
        assert "min-grace=0.3 (built-in)" in captured.out

    def test_bare_module_name_ignores_cwd_pyproject(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A module *name* never triggers the ``pyproject.toml`` walk-up from the current directory.

        Running ``pydeprecate policy somepkg`` from inside an unrelated checkout must not adopt that checkout's
        policy — the same guard version auto-detection applies to a bare name.
        """
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmessage-required = false\n")
        monkeypatch.chdir(tmp_path)
        assert cmd_policy(path="some_module", _wrappers=[_POLICY_VIOLATION]) == 1
        assert "(built-in)" in capsys.readouterr().out

    @patch("deprecate._cli._is_package_available", return_value=False)
    def test_missing_toml_parser_warns(
        self, mock_available: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With no TOML parser installed, a reachable ``pyproject.toml`` is reported as ignored, not read silently.

        On Python 3.9-3.10 without the ``audit`` extra there is no ``tomllib``; a configured-but-unread policy is
        the silent no-op the advisory exists to expose, and it names the extra that fixes it.
        """
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmessage-required = false\n")
        assert cmd_policy(path=str(tmp_path), _wrappers=[_POLICY_VIOLATION]) == 1
        assert "pyDeprecate[audit]" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Top-level package surface
# ---------------------------------------------------------------------------


class TestTopLevelExports:
    """The audit classes a policy caller configures with are mirrored at the package top level.

    Lives here for want of a dedicated public-surface test module: the CLI is the other consumer of these names,
    and ``tests/unittests/test_proxy.py`` pins ``DeprecationProxy`` the same way next to the code it belongs to.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "ChainType",
            "DeprecationStatus",
            "DeprecationWrapperInfo",
            "GraceWindow",
            "PolicyRule",
            "TableStyle",
            "VersionBump",
        ],
    )
    def test_audit_class_mirrored(self, name: str) -> None:
        """Each audit class is the very same object at ``deprecate.<name>`` and is listed in ``__all__``.

        ``from deprecate import VersionBump`` failed while ``ChainType`` and ``TableStyle`` imported fine, so a
        caller spelling ``min_grace=GraceWindow(3, VersionBump.MINOR)`` had to know which policy names were
        mirrored and which lived only under ``deprecate.audit``. Identity rather than equality pins that the top
        level re-exports the audit object instead of redefining it.
        """
        assert getattr(deprecate, name) is getattr(deprecate.audit, name)
        assert name in deprecate.__all__

    def test_grace_window_spec_stays_audit_only(self) -> None:
        """``GraceWindowSpec`` is deliberately not mirrored: a typing alias, not a class callers instantiate.

        Keeping it under ``deprecate.audit`` only is the documented split; a stray top-level re-export would widen
        the public surface nobody asked for and no other test would notice.
        """
        assert not hasattr(deprecate, "GraceWindowSpec")
        assert "GraceWindowSpec" not in deprecate.__all__
