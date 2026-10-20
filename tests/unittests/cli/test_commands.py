"""Unit tests for :mod:`deprecate._cli.commands`: the subcommands (all external calls fully mocked)."""

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deprecate._cli.commands import cmd_all, cmd_chains, cmd_check, cmd_expiry, cmd_policy, cmd_status
from deprecate._cli.config import _FROM_PYPROJECT, _POLICY_DEFAULTS, _ConfigFlag
from deprecate._cli.report import _Reporter
from deprecate._types import DeprecationConfig
from deprecate.audit import ChainType, DeprecationWrapperInfo, PolicyRule, validate_deprecation_policy
from tests.unittests.cli._helpers import _EXPIRED_MSG, _POLICY_VIOLATION

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TARGET_CHAIN = DeprecationWrapperInfo(module="mod", function="fn", chain_type=ChainType.TARGET)


_STACKED_CHAIN = DeprecationWrapperInfo(module="mod", function="fn2", chain_type=ChainType.STACKED)


_INVALID_ARGS = DeprecationWrapperInfo(module="mod", function="fn", invalid_args=["bad"])


# Forwarding wrapper with no scheduled removal — clean under every default policy rule.
_POLICY_CLEAN = DeprecationWrapperInfo(
    module="mod",
    function="forwarding_fn",
    deprecated_info=DeprecationConfig(deprecated_in="1.0", target=str),
)


class TestCmdCheckScanning:
    """Tests for cmd_check() scanning and path-handling behavior."""

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_no_issues_package(self, mock_find: MagicMock, tmp_path: Path) -> None:
        """Scanning a package directory with no issues exits 0."""
        pkg_dir = tmp_path / "mypkg"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").touch()

        mock_find.return_value = []
        assert cmd_check(path=str(pkg_dir)) == 0
        mock_find.assert_called_once_with("mypkg", recursive=True, include_members=True, exclude=[])

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_no_issues_file(self, mock_find: MagicMock) -> None:
        """Scanning an importable module name with no issues exits 0."""
        mock_find.return_value = []
        assert cmd_check(path="some_module") == 0
        mock_find.assert_called_once_with("some_module", recursive=True, include_members=True, exclude=[])

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_scan_plain_directory(self, mock_find: MagicMock, tmp_path: Path) -> None:
        """Scanning a plain directory (no __init__.py) scans each .py file."""
        (tmp_path / "module_a.py").touch()
        (tmp_path / "module_b.py").touch()
        # __dunder files should be skipped by the scanner
        (tmp_path / "__helpers__.py").touch()

        mock_find.return_value = []
        assert cmd_check(path=str(tmp_path)) == 0
        assert mock_find.call_count == 2

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
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

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_scan_directory_with_scan_error(self, mock_find: MagicMock, tmp_path: Path) -> None:
        """Per-file scan errors in plain directory are caught as warnings; exits 0."""
        (tmp_path / "bad_module.py").touch()

        mock_find.side_effect = Exception("import error")
        assert cmd_check(path=str(tmp_path)) == 0

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_found_issues(self, mock_find: MagicMock) -> None:
        """Invalid arg mappings cause exit 1."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", invalid_args=["bad_arg"])
        mock_find.return_value = [info]

        assert cmd_check(path="some_module") == 1

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_found_warnings_only(self, mock_find: MagicMock) -> None:
        """Identity mapping (warning only) exits 0."""
        info = DeprecationWrapperInfo(
            module="test_mod", function="test_func", identity_args_mapping=["arg"], no_effect=True
        )
        mock_find.return_value = [info]

        assert cmd_check(path="some_module") == 0

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_no_effect_empty_args_mapping(self, mock_find: MagicMock) -> None:
        """Empty mapping reported as no-effect reason; exits 0."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", empty_args_mapping=True, no_effect=True)
        mock_find.return_value = [info]

        assert cmd_check(path="some_module") == 0

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_no_effect_self_reference(self, mock_find: MagicMock) -> None:
        """Self-reference reported as no-effect reason; exits 0."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", self_reference=True, no_effect=True)
        mock_find.return_value = [info]

        assert cmd_check(path="some_module") == 0

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_all_correct(self, mock_find: MagicMock) -> None:
        """Correctly configured wrappers exit 0."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func")
        mock_find.return_value = [info]

        assert cmd_check(path="some_module") == 0

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_error_scanning(self, mock_find: MagicMock) -> None:
        """Scan failure raises; cli() converts it to sys.exit at the CLI boundary."""
        mock_find.side_effect = Exception("Boom")

        with pytest.raises(Exception, match="Boom"):
            cmd_check(path="some_module")

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
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

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
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

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
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

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_unreadable_pyproject_exits_two_before_scanning(
        self, mock_find: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A ``pyproject.toml`` within reach that does not parse is a usage error for every scanning subcommand.

        The file may hold an ``exclude`` list the author relies on; reading it as "no configuration" would scan
        the excluded packages and report their fixtures as findings. The scan never starts, and the message names
        the file so the reader fixes the right thing.
        """
        _write_pkg(tmp_path, "mypkg", "")
        broken = tmp_path / "pyproject.toml"
        broken.write_text("[tool.pydeprecate\nexclude = oops\n")
        assert cmd_check(path=str(tmp_path / "mypkg")) == 2
        assert f"Cannot read {broken}" in _unwrapped(capsys.readouterr().err)
        mock_find.assert_not_called()

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_unknown_top_level_key_warns(
        self, mock_find: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An unrecognised key directly under ``[tool.pydeprecate]`` is reported, not ignored in silence."""
        _write_pkg(tmp_path, "mypkg", "")
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate]\nexcludes = ["mypkg.tests"]\n')
        assert cmd_check(path=str(tmp_path / "mypkg")) == 0
        assert "`excludes`" in capsys.readouterr().err
        assert mock_find.call_args.kwargs["exclude"] == []

    @patch("deprecate._cli.commands.cmd_status", return_value=0)
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
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

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_no_recursive_threads_flag(self, mock_find: MagicMock) -> None:
        """``recursive=False`` passes through to find_deprecation_wrappers."""
        mock_find.return_value = []
        assert cmd_check(path="some_module", recursive=False) == 0
        mock_find.assert_called_once_with("some_module", recursive=False, include_members=True, exclude=[])

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_chain_warning_exits_zero(self, mock_find: MagicMock) -> None:
        """Chains in check subcommand are warnings — do not cause exit 1."""
        mock_find.return_value = [_TARGET_CHAIN]
        assert cmd_check(path="some_module") == 0

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_chain_warning_reported(self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """Chain issues are included in check output."""
        mock_find.return_value = [_TARGET_CHAIN]
        with patch("deprecate._cli.report._Reporter._HAS_RICH", False):
            cmd_check(path="some_module")
        captured = capsys.readouterr()
        assert "chain" in captured.out.lower()

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_invalid_args_exits_one(self, mock_find: MagicMock) -> None:
        """Invalid args still cause exit 1 in check subcommand."""
        mock_find.return_value = [_INVALID_ARGS]
        assert cmd_check(path="some_module") == 1

    def test_pre_scanned_wrappers_skips_scan(self) -> None:
        """_wrappers provided → find_deprecation_wrappers not called."""
        with patch("deprecate._cli.scan.find_deprecation_wrappers") as mock_find:
            result = cmd_check(path="some_module", _wrappers=[])
        mock_find.assert_not_called()
        assert result == 0


# ---------------------------------------------------------------------------
# cmd_expiry
# ---------------------------------------------------------------------------


class TestCmdExpiry:
    """Tests for cmd_expiry() subcommand."""

    @patch("deprecate._cli.commands.validate_deprecation_expiry")
    def test_no_expired_exits_zero(self, mock_expiry: MagicMock) -> None:
        """No expired wrappers → exit 0."""
        mock_expiry.return_value = []
        assert cmd_expiry(path="some_module", version="1.0") == 0

    @patch("deprecate._cli.commands.validate_deprecation_expiry")
    def test_expired_found_exits_one(self, mock_expiry: MagicMock) -> None:
        """Expired wrappers found → exit 1."""
        mock_expiry.return_value = [_EXPIRED_MSG]
        assert cmd_expiry(path="some_module", version="2.0") == 1

    @patch("deprecate._cli.commands.validate_deprecation_expiry")
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

    @patch("deprecate._cli.commands._check_expiry_for_callables")
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

    @patch("deprecate._cli.commands.validate_deprecation_expiry")
    def test_packaging_missing_exits_zero(self, mock_expiry: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """ImportError from missing packaging library → install hint on stderr + returns 0 (advisory)."""
        mock_expiry.side_effect = ImportError("No module named 'packaging'", name="packaging")
        assert cmd_expiry(path="some_module", version="2.0") == 0
        captured = capsys.readouterr()
        assert "pyDeprecate[audit]" in captured.err

    @patch("deprecate._cli.commands.validate_deprecation_expiry")
    def test_packaging_missing_exit_zero_exits_zero(self, mock_expiry: MagicMock) -> None:
        """ImportError with exit_zero=True → returns 0 (missing packaging is always advisory)."""
        mock_expiry.side_effect = ImportError("No module named 'packaging'", name="packaging")
        assert cmd_expiry(path="some_module", version="2.0", exit_zero=True) == 0

    @patch("deprecate._cli.commands.validate_deprecation_expiry")
    def test_version_passed_through(self, mock_expiry: MagicMock) -> None:
        """Explicit version is forwarded to validate_deprecation_expiry."""
        mock_expiry.return_value = []
        cmd_expiry(path="some_module", version="3.0")
        mock_expiry.assert_called_once_with("some_module", "3.0", recursive=True, exclude=[])

    @patch("deprecate._cli.commands.validate_deprecation_expiry")
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

    @patch("deprecate._cli.commands.validate_deprecation_expiry")
    def test_expired_reported_plain(self, mock_expiry: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """Expired messages appear in plain-text output."""
        mock_expiry.return_value = [_EXPIRED_MSG]
        with patch("deprecate._cli.report._Reporter._HAS_RICH", False):
            cmd_expiry(path="some_module", version="2.0")
        captured = capsys.readouterr()
        assert "expired" in captured.out.lower()

    def test_pre_scanned_wrappers_skips_scan(self) -> None:
        """_wrappers=[] provided → validate_deprecation_expiry not called; returns 0."""
        with patch("deprecate._cli.commands.validate_deprecation_expiry") as mock_expiry:
            result = cmd_expiry(path="some_module", version="1.0", _wrappers=[])
        mock_expiry.assert_not_called()
        assert result == 0

    def test_pre_scanned_wrappers_expired_exits_one(self) -> None:
        """_wrappers with an expired wrapper and matching version → returns 1."""
        config = DeprecationConfig(deprecated_in="1.0", remove_in="2.0")
        wrapper = DeprecationWrapperInfo(module="mod", function="fn", deprecated_info=config)
        with patch("deprecate._cli.commands.validate_deprecation_expiry") as mock_expiry:
            result = cmd_expiry(path="some_module", version="2.0", _wrappers=[wrapper])
        mock_expiry.assert_not_called()
        assert result == 1

    def test_pre_scanned_wrappers_version_none_skips(self, capsys: pytest.CaptureFixture[str]) -> None:
        """_wrappers provided but version=None → warns on stderr and returns 0."""
        with patch("deprecate._cli.commands.validate_deprecation_expiry") as mock_expiry:
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
            patch("deprecate._cli.commands._auto_detect_version", return_value=None),
            patch("deprecate._cli.commands._do_expiry", return_value=[]),
        ):
            result = cmd_expiry(path="some_module")
        assert result == 0
        assert "without a resolved version" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cmd_chains
# ---------------------------------------------------------------------------


class TestCmdChains:
    """Tests for cmd_chains() subcommand."""

    @patch("deprecate._cli.commands.validate_deprecation_chains")
    def test_no_chains_exits_zero(self, mock_chains: MagicMock) -> None:
        """No chains found → exit 0."""
        mock_chains.return_value = []
        assert cmd_chains(path="some_module") == 0

    @patch("deprecate._cli.commands.validate_deprecation_chains")
    def test_chains_found_exits_one(self, mock_chains: MagicMock) -> None:
        """Chains found → exit 1 (user explicitly asked for chain detection)."""
        mock_chains.return_value = [_TARGET_CHAIN]
        assert cmd_chains(path="some_module") == 1

    @patch("deprecate._cli.commands.validate_deprecation_chains")
    def test_chains_exit_zero_exits_zero(self, mock_chains: MagicMock) -> None:
        """exit_zero=True overrides exit code to 0 even when chains found."""
        mock_chains.return_value = [_TARGET_CHAIN]
        assert cmd_chains(path="some_module", exit_zero=True) == 0

    @patch("deprecate._cli.commands.validate_deprecation_chains")
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

    @patch("deprecate._cli.commands.validate_deprecation_chains")
    def test_chains_reported_plain(self, mock_chains: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """Chain messages appear in plain-text output."""
        mock_chains.return_value = [_TARGET_CHAIN]
        with patch("deprecate._cli.report._Reporter._HAS_RICH", False):
            cmd_chains(path="some_module")
        captured = capsys.readouterr()
        assert "chain" in captured.out.lower()

    @patch("deprecate._cli.commands.validate_deprecation_chains")
    def test_stacked_chain_label(self, mock_chains: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """STACKED chain type label appears in plain-text output."""
        mock_chains.return_value = [_STACKED_CHAIN]
        with patch("deprecate._cli.report._Reporter._HAS_RICH", False):
            cmd_chains(path="some_module")
        captured = capsys.readouterr()
        assert "stacked" in captured.out.lower()

    def test_pre_scanned_wrappers_skips_scan(self) -> None:
        """_wrappers provided → validate_deprecation_chains not called; returns 0."""
        with patch("deprecate._cli.commands.validate_deprecation_chains") as mock_chains:
            result = cmd_chains(path="some_module", _wrappers=[_TARGET_CHAIN])
        mock_chains.assert_not_called()
        assert result == 1

    def test_pre_scanned_wrappers_no_chains_exits_zero(self) -> None:
        """_wrappers with no chain_type entries → returns 0."""
        plain = DeprecationWrapperInfo(module="mod", function="fn")
        with patch("deprecate._cli.commands.validate_deprecation_chains") as mock_chains:
            result = cmd_chains(path="some_module", _wrappers=[plain])
        mock_chains.assert_not_called()
        assert result == 0


# ---------------------------------------------------------------------------
# cmd_all
# ---------------------------------------------------------------------------


class TestCmdAll:
    """Tests for cmd_all() subcommand — sequential three-check composition."""

    @patch("deprecate._cli.commands._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_all_clean_exits_zero(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """No issues in any check → exit 0."""
        mock_find.return_value = []
        assert cmd_all(path="some_module", version="1.0") == 0

    @patch("deprecate._cli.commands._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
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

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
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
        with patch("deprecate._cli.commands._auto_detect_version", return_value="2024.06-nightly"):
            result = cmd_all(path="some_module")
        captured = capsys.readouterr()
        assert result == 0
        assert "Invalid `--version`" not in captured.err
        assert "Could not render the deprecation table" not in captured.err
        assert "No deprecation policy violations found." in captured.out

    @patch("deprecate._cli.commands._check_expiry_for_callables")
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_expired_exits_one(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """Expired wrappers found → exit 1."""
        mock_find.return_value = [DeprecationWrapperInfo(module="mod", function="fn")]
        mock_expiry.return_value = [_EXPIRED_MSG]
        assert cmd_all(path="some_module", version="2.0") == 1

    @patch("deprecate._cli.commands._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_chains_found_exits_one(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """Chains detected from wrappers by cmd_chains phase → exit 1."""
        mock_find.return_value = [_TARGET_CHAIN]
        assert cmd_all(path="some_module", version="1.0") == 1

    @patch("deprecate._cli.commands._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_exit_zero_exits_zero(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """exit_zero=True overrides the invalid-args exit 1 to 0."""
        mock_find.return_value = [_INVALID_ARGS]
        assert cmd_all(path="some_module", version="1.0", exit_zero=True) == 0

    @patch("deprecate._cli.commands._check_expiry_for_callables")
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_packaging_missing_skips_expiry_continues(
        self, mock_find: MagicMock, mock_expiry: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Missing packaging library skips expiry with warning; other checks still run."""
        mock_find.return_value = [DeprecationWrapperInfo(module="mod", function="fn")]
        mock_expiry.side_effect = ImportError("No module named 'packaging'", name="packaging")
        assert cmd_all(path="some_module", version="2.0") == 0
        captured = capsys.readouterr()
        assert "packaging" in captured.err.lower()

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_no_version_skips_expiry(self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """When version is None and auto-detect fails, expiry warns and returns 0; cmd_all continues."""
        mock_find.return_value = [DeprecationWrapperInfo(module="mod", function="fn")]
        with patch("deprecate._cli.commands._auto_detect_version", return_value=None):
            result = cmd_all(path="some_module")
        assert result == 0
        captured = capsys.readouterr()
        assert "version" in captured.err.lower()

    @patch("deprecate._cli.commands._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_no_recursive_threads_flag(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """``recursive=False`` passes through to find_deprecation_wrappers."""
        mock_find.return_value = []
        cmd_all(path="some_module", version="1.0", recursive=False)
        mock_find.assert_any_call("some_module", recursive=False, include_members=True, exclude=[])

    @patch("deprecate._cli.commands._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.commands.validate_deprecation_chains")
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_all_scans_once(self, mock_find: MagicMock, mock_chains: MagicMock, mock_expiry: MagicMock) -> None:
        """cmd_all calls find_deprecation_wrappers exactly once; validate_deprecation_chains not called."""
        mock_find.return_value = []
        cmd_all(path="some_module", version="1.0")
        assert mock_find.call_count == 1
        mock_chains.assert_not_called()

    @patch("deprecate._cli.commands._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_policy_violations_stay_advisory(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """A policy violation is reported by ``all`` but never changes its exit code.

        The policy defaults encode one project's release convention (a three-minor grace window on a clean
        release boundary); folding them into ``all``'s exit code would break the CI of every repo that upgrades and does
        not share that convention, so the dedicated ``policy`` subcommand is the only gate.
        """
        mock_find.return_value = [_POLICY_VIOLATION]
        assert cmd_all(path="some_module", version="2.0") == 0

    @patch("deprecate._cli.commands.cmd_status", return_value=0)
    @patch("deprecate._cli.commands._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[_POLICY_VIOLATION])
    def test_policy_violations_render_as_advisory(
        self, mock_find: MagicMock, mock_expiry: MagicMock, mock_status: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Inside ``all`` a policy violation prints as a ``[WARNING]`` with a trailer naming the gating command.

        The same table used to print as a bold-red ``[ERROR]`` and the run exited 0 — a reader saw an error the
        pipeline ignored and could not tell whether the gate was broken or the finding was informational. The
        warning register plus the trailer make the advisory status explicit, while the exit code stays 0.
        """
        with patch("deprecate._cli.report._Reporter._HAS_RICH", False):
            result = cmd_all(path="some_module", version="2.0")
        captured = capsys.readouterr()
        assert result == 0
        assert "[WARNING] Found deprecation policy violations:" in captured.out
        assert "[ERROR] Found deprecation policy violations:" not in captured.out
        assert "(advisory here — run `pydeprecate policy` to gate on it)" in captured.out

    @pytest.mark.parametrize("exit_zero", [False, True])
    @patch("deprecate._cli.commands.cmd_status", return_value=0)
    @patch("deprecate._cli.commands._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_malformed_policy_value_exits_two(
        self,
        mock_find: MagicMock,
        mock_expiry: MagicMock,
        mock_status: MagicMock,
        exit_zero: bool,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A malformed rule value in ``[tool.pydeprecate.policy]`` fails ``all`` with exit 2, ``--exit-zero`` or not.

        ``pydeprecate policy`` already exits 2 on ``min-grace = "abc"``; ``all`` discarded that code because
        policy *violations* are advisory there, so the same broken file passed CI under ``all`` while failing the
        dedicated gate. A usage error is not a violation: it stops the run before the chain check and the status
        table, and ``--exit-zero`` — which downgrades findings, not mistakes — leaves it at 2.
        """
        pkg = _write_pkg(tmp_path, "mypkg", "")
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate.policy]\nmin-grace = "abc"\n')
        assert cmd_all(path=str(pkg), version="1.0", exit_zero=exit_zero) == 2
        assert "min_grace" in capsys.readouterr().err
        mock_status.assert_not_called()

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_unreadable_pyproject_exits_two_before_scanning(
        self, mock_find: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A ``pyproject.toml`` within reach that fails to parse stops ``all`` before its shared scan.

        The file may carry both the ``exclude`` list and the policy table; scanning without either and reporting
        the outcome as clean is the silent loosening the strict reader exists to prevent.
        """
        pkg = _write_pkg(tmp_path, "mypkg", "")
        broken = tmp_path / "pyproject.toml"
        broken.write_text("[tool.pydeprecate\nexclude = oops\n")
        assert cmd_all(path=str(pkg), version="1.0") == 2
        assert f"Cannot read {broken}" in _unwrapped(capsys.readouterr().err)
        mock_find.assert_not_called()

    @patch("deprecate._cli.commands.cmd_status", side_effect=RuntimeError("table rendering failed"))
    @patch("deprecate._cli.commands._check_expiry_for_callables", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
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


def _write_pkg(root: Path, name: str, init_body: str) -> Path:
    """Create an importable ``name`` package under *root* with *init_body* as its ``__init__.py`` and return it."""
    pkg = root / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(init_body)
    return pkg


def _unwrapped(captured: str) -> str:
    """Undo Rich's soft-wrapping of captured console output so a long file path can be matched whole.

    Under ``capsys`` the Rich console assumes an 80-column terminal and breaks a ``tmp_path`` in the middle of an
    error message; joining the lines restores the printed text without touching what was actually emitted.
    """
    return captured.replace("\n", "")


class TestCmdStatus:
    """Tests for cmd_status() — markdown table rendering."""

    @patch(
        "deprecate._cli.commands.generate_deprecation_table",
        return_value="| A | B |\n| :--- | :--- |\n| `fn` | callable |",
    )
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_exits_0_and_prints_table(
        self, mock_find: MagicMock, mock_gen: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """cmd_status exits 0 and prints the generated table to stdout."""
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").touch()

        with patch("deprecate._cli.report._Reporter._HAS_RICH", False):
            result = cmd_status(path=str(pkg))

        assert result == 0
        assert "| A | B |" in capsys.readouterr().out

    @patch("deprecate._cli.commands.generate_deprecation_table", return_value="| A |")
    def test_pre_scanned_wrappers_skip_scan(
        self, mock_gen: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Providing _wrappers bypasses find_deprecation_wrappers."""
        wrappers = [DeprecationWrapperInfo(module="m", function="f")]
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").touch()

        with patch("deprecate._cli.scan.find_deprecation_wrappers") as mock_find:
            with patch("deprecate._cli.report._Reporter._HAS_RICH", False):
                cmd_status(path=str(pkg), _wrappers=wrappers)
            mock_find.assert_not_called()
        mock_gen.assert_called_once()
        _, kwargs = mock_gen.call_args
        assert kwargs.get("_wrappers") == wrappers

    @patch("deprecate._cli.commands.generate_deprecation_table", return_value="| A |")
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_invalid_style_falls_back_to_compact(
        self, mock_find: MagicMock, mock_gen: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Unknown style value falls back to compact with a stderr warning and exits 0."""
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").touch()

        with patch("deprecate._cli.report._Reporter._HAS_RICH", False):
            result = cmd_status(path=str(pkg), style="bogus")

        assert result == 0
        assert "falling back" in capsys.readouterr().err.lower()

    @patch("deprecate._cli.commands.generate_deprecation_table", return_value="| Col |\n| :--- |\n| `fn` |")
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_output_writes_to_file(
        self, mock_find: MagicMock, mock_gen: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--output writes the markdown to a file in addition to stdout."""
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").touch()
        out_file = tmp_path / "DEPRECATIONS.md"

        with patch("deprecate._cli.report._Reporter._HAS_RICH", False):
            result = cmd_status(path=str(pkg), output=str(out_file))

        assert result == 0
        assert out_file.exists()
        assert "| Col |" in out_file.read_text()


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

    @patch("deprecate._cli.commands._check_policy_for_callables")
    def test_packaging_missing_exits_zero(self, mock_policy: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """A missing ``packaging`` library prints the install hint and stays advisory (exit 0).

        The version comparison needs the optional ``audit`` extra; a CI job that installed only the base package
        should be told what to add rather than failing on a check it never ran.
        """
        mock_policy.side_effect = [ImportError("No module named 'packaging'", name="packaging"), []]
        assert cmd_policy(path="some_module", _wrappers=[]) == 0
        assert "pyDeprecate[audit]" in capsys.readouterr().err

    @patch("deprecate._cli.commands._check_policy_for_callables")
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

    @patch("deprecate._cli.commands._check_policy_for_callables")
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
        with patch("deprecate._cli.report._Reporter._HAS_RICH", False):
            cmd_policy(path="some_module", _wrappers=[_POLICY_VIOLATION])
        captured = capsys.readouterr()
        assert "[ERROR] Found deprecation policy violations:" in captured.out
        assert "[message-required]" in captured.out
        assert "advisory here" not in captured.out

    @pytest.mark.parametrize("exit_zero", [False, True])
    def test_advisory_rendering_leaves_exit_code_alone(
        self, exit_zero: bool, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``_advisory`` changes only how violations are printed; the exit code still follows ``exit_zero``.

        ``all`` decides what to do with the policy result itself, so the advisory register must not quietly
        turn into a second ``exit_zero`` — otherwise a future caller passing ``_advisory=True`` would lose the
        truthful exit 1 that ``all`` reads and ignores on purpose.
        """
        with patch("deprecate._cli.report._Reporter._HAS_RICH", False):
            result = cmd_policy(path="some_module", exit_zero=exit_zero, _wrappers=[_POLICY_VIOLATION], _advisory=True)
        captured = capsys.readouterr()
        assert result == (0 if exit_zero else 1)
        assert "[WARNING] Found deprecation policy violations:" in captured.out
        assert "(advisory here — run `pydeprecate policy` to gate on it)" in captured.out

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

    def test_unreadable_pyproject_exits_two_naming_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A ``pyproject.toml`` within reach that fails to parse is a usage error, not a silent reset to defaults.

        A syntax slip in the file used to read as "no ``[tool.pydeprecate]`` here", so the gate ran with the
        built-in rules and passed a wrapper the project's own stricter window would have rejected. Exit 2 with the
        file named keeps a broken configuration from masquerading as a clean policy run.
        """
        broken = tmp_path / "pyproject.toml"
        broken.write_text("[tool.pydeprecate.policy\nmin-grace = oops\n")
        assert cmd_policy(path=str(tmp_path), _wrappers=[_POLICY_VIOLATION]) == 2
        assert f"Cannot read {broken}" in _unwrapped(capsys.readouterr().err)

    def test_non_table_policy_exits_two(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A ``policy`` key that is not a table is a usage error, the same as a malformed value inside the table.

        ``policy = "strict"`` configures nothing; treating it as advisory ran the built-in rules while a malformed
        ``min-grace`` inside a proper table exited 2 — the same class of mistake, two different outcomes. Both now
        stop the run and name the file.
        """
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate]\npolicy = "strict"\n')
        assert cmd_policy(path=str(tmp_path), _wrappers=[]) == 2
        captured = capsys.readouterr()
        assert "`policy`" in captured.err
        assert "pyproject.toml" in captured.err

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

    @pytest.mark.parametrize(
        ("rule", "parameter"),
        [
            pytest.param(PolicyRule.MIN_GRACE, "min_grace", id="min-grace"),
            pytest.param(PolicyRule.MESSAGE_REQUIRED, "message_required", id="message-required"),
        ],
    )
    def test_built_in_default_matches_library_signature(self, rule: PolicyRule, parameter: str) -> None:
        """The CLI's built-in default for each rule is the value ``validate_deprecation_policy`` defaults to.

        The two used to be spelled separately, so a library-side change of the default window would have left
        ``pydeprecate policy`` enforcing the old one — the same project passing in a test and failing in CI. Both
        now read one module-level constant; this pins that neither side re-inlines a literal.
        """
        signature = inspect.signature(validate_deprecation_policy)
        assert _POLICY_DEFAULTS[rule.value] == signature.parameters[parameter].default

    @patch("deprecate._cli.config._is_package_available", return_value=False)
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
