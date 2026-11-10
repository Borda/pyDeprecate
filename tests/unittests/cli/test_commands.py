"""Unit tests for :mod:`deprecate._cli.commands`: the subcommands (all external calls fully mocked)."""

import importlib.util
import inspect
import json
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest

from deprecate._cli.commands import (
    _chain_wrappers,
    _check_wrappers,
    _expiry_wrappers,
    _policy_wrappers,
    _status_table,
    cmd_all,
    cmd_chains,
    cmd_check,
    cmd_expiry,
    cmd_policy,
    cmd_status,
)
from deprecate._cli.config import _FROM_PYPROJECT, _POLICY_DEFAULTS, _ConfigBool, _ConfigFlag
from deprecate._cli.report import _GapKind, _record_run, _ScanRecord
from deprecate._cli.scan import _ScanError
from deprecate._types import DeprecationConfig, TargetMode
from deprecate.audit import ChainType, DeprecationWrapperInfo, PolicyRule, TableStyle, validate_deprecation_policy
from deprecate.audit._policy import _build_policy_spec
from tests._helpers import write_pkg
from tests.unittests.cli._helpers import _EXPIRED_FINDING, _MESSAGE_REQUIRED_FINDING, _POLICY_VIOLATION


def _scanned(wrappers: list[DeprecationWrapperInfo]) -> AbstractContextManager[MagicMock]:
    """Make the subcommands' scan prologue return *wrappers* without importing anything."""
    return patch("deprecate._cli.commands._scan_for_command", return_value=_ScanRecord("some_module", wrappers, None))


@pytest.fixture(autouse=True)
def _forget_scanned_tmp_packages(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Drop the packages a test imported from its temporary directory once the test ends.

    Many tests here scan a fresh ``mypkg`` in their own ``tmp_path``. Left in ``sys.modules``, the first one would
    shadow every later one, which the scan refuses as a package already imported from elsewhere.

    """
    yield
    base = str(tmp_path_factory.getbasetemp())
    for name, module in list(sys.modules.items()):
        if str(getattr(module, "__file__", None) or "").startswith(base):
            del sys.modules[name]


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
        """A package that fails to import raises ``_ScanError``, which cli() turns into exit 3."""
        mock_find.side_effect = Exception("Boom")

        with pytest.raises(_ScanError, match="could not import 'some_module': Exception: Boom"):
            cmd_check(path="some_module")

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_missing_module_exits_two(self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """A module name that cannot be found is a usage error (exit 2), not a crash of the scan.

        The import fails with ``ModuleNotFoundError`` for the named module itself, so the argument has a typo; a
        dependency missing inside an existing package is a failed import instead (exit 3).

        """
        mock_find.side_effect = ModuleNotFoundError("No module named 'some_module'", name="some_module")
        assert cmd_check(path="some_module") == 2
        assert "No such path or importable module: 'some_module'" in capsys.readouterr().err

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_missing_dependency_is_a_scan_error(self, mock_find: MagicMock) -> None:
        """A ``ModuleNotFoundError`` for another module means the package exists but failed to import."""
        mock_find.side_effect = ModuleNotFoundError("No module named 'numpy'", name="numpy")
        with pytest.raises(_ScanError, match="numpy"):
            cmd_check(path="some_module")

    @pytest.mark.parametrize(
        ("info", "header", "line"),
        [
            pytest.param(
                DeprecationWrapperInfo(
                    module="mod",
                    function="fn",
                    deprecated_info=DeprecationConfig(target=TargetMode.NOTIFY, args_mapping={"old_x": "x"}),
                    misconfigured_target=True,
                ),
                "[ERROR] Found deprecated wrappers with a misconfigured target:",
                "\t- mod.fn: `TargetMode.NOTIFY` with `args_mapping`",
                id="misconfigured-target",
            ),
            pytest.param(
                DeprecationWrapperInfo(module="mod", function="Cls.value", inner_order_property=True),
                "[ERROR] Found inner-order @property @deprecated wrappers (setter/deleter unprotected):",
                "\t- mod.Cls.value: use @deprecated(...) above @property",
                id="inner-order-property",
            ),
        ],
    )
    def test_configuration_error_fails(
        self, info: DeprecationWrapperInfo, header: str, line: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A misconfigured target or an inner-order property is reported in its own section and exits 1.

        Both used to print "All deprecated wrappers look correct!" and exit 0, although the docs promise that the
        gate fails on misconfigured wrappers: a NOTIFY wrapper silently ignores its mapping, and an inner-order
        property leaves its setter and deleter without a warning.

        """
        assert _check_wrappers([info]) == 1
        out = capsys.readouterr().out
        assert header in out
        assert line in out
        assert "All deprecated wrappers look correct!" not in out

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_exit_zero(self, mock_find: MagicMock) -> None:
        """exit_zero=True returns 0 even with invalid args."""
        info = DeprecationWrapperInfo(module="test_mod", function="test_func", invalid_args=["bad_arg"])
        mock_find.return_value = [info]

        assert cmd_check(path="some_module", exit_zero=True) == 0

    def test_file_path_rejected(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A file path is a usage error: exit 2 with the refusal on stderr, before anything is scanned."""
        fpath = tmp_path / "module.py"
        fpath.touch()
        assert cmd_check(path=str(fpath)) == 2
        assert "File paths are not supported" in capsys.readouterr().err

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
        write_pkg(tmp_path, "mypkg", "")
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
        write_pkg(tmp_path, "mypkg", "")
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
        write_pkg(tmp_path, "mypkg", "")
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
        write_pkg(tmp_path, "mypkg", "")
        broken = tmp_path / "pyproject.toml"
        broken.write_text("[tool.pydeprecate\nexclude = oops\n")
        assert cmd_check(path=str(tmp_path / "mypkg")) == 2
        assert f"Cannot read {broken}" in capsys.readouterr().err
        mock_find.assert_not_called()

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_unknown_top_level_key_warns(
        self, mock_find: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An unrecognised key directly under ``[tool.pydeprecate]`` is reported, not ignored in silence."""
        write_pkg(tmp_path, "mypkg", "")
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate]\nexcludes = ["mypkg.tests"]\n')
        assert cmd_check(path=str(tmp_path / "mypkg")) == 0
        assert "`excludes`" in capsys.readouterr().err
        assert mock_find.call_args.kwargs["exclude"] == []

    @patch("deprecate._cli.commands._status_table", return_value="")
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_all_loads_config_once(
        self, mock_find: MagicMock, mock_status: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``all`` reads ``[tool.pydeprecate]`` once and its advisory policy pass reuses it, so a warning prints once.

        ``all`` hands the loaded configuration to ``policy``; re-reading the file there would repeat every
        unknown-key advisory and make the log look like two separate problems.
        """
        write_pkg(tmp_path, "mypkg", "")
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
        cmd_check(path="some_module")
        captured = capsys.readouterr()
        assert "chain" in captured.out.lower()

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_invalid_args_exits_one(self, mock_find: MagicMock) -> None:
        """Invalid args still cause exit 1 in check subcommand."""
        mock_find.return_value = [_INVALID_ARGS]
        assert cmd_check(path="some_module") == 1


# ---------------------------------------------------------------------------
# cmd_expiry
# ---------------------------------------------------------------------------


class TestCmdExpiry:
    """Tests for cmd_expiry() subcommand: one scan, then the expired-wrapper derivation."""

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    def test_no_expired_exits_zero(self, mock_check: MagicMock, mock_scan: MagicMock) -> None:
        """No expired wrappers → exit 0."""
        assert cmd_expiry(path="some_module", version="1.0") == 0

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    @patch("deprecate._cli.commands._expiry_findings", return_value=[_EXPIRED_FINDING])
    def test_expired_found_exits_one(self, mock_check: MagicMock, mock_scan: MagicMock) -> None:
        """Expired wrappers found → exit 1."""
        assert cmd_expiry(path="some_module", version="2.0") == 1

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    @patch("deprecate._cli.commands._expiry_findings", return_value=[_EXPIRED_FINDING])
    def test_expired_exit_zero_exits_zero(self, mock_check: MagicMock, mock_scan: MagicMock) -> None:
        """exit_zero=True overrides exit code to 0 even when expired wrappers found."""
        assert cmd_expiry(path="some_module", version="2.0", exit_zero=True) == 0

    def test_invalid_version_exits_two(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A malformed explicit version is a usage error before the expiry scan starts.

        CI must distinguish an invalid gate configuration from an expired wrapper. The command therefore returns
        exit 2 and explains the rejected ``--version`` without importing or scanning the requested package.
        """
        assert cmd_expiry(path="some_module", version="not-a-version") == 2
        assert "Invalid `--version`" in capsys.readouterr().err

    @patch("deprecate._cli.commands._expiry_findings")
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
        result = _expiry_wrappers([DeprecationWrapperInfo(module="mod", function="fn")], "2024.06-nightly")
        assert result == 0
        assert "2024.06-nightly" in capsys.readouterr().err

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    @patch(
        "deprecate._cli.commands._expiry_findings",
        side_effect=ImportError("No module named 'packaging'", name="packaging"),
    )
    def test_packaging_missing_exits_zero(
        self, mock_check: MagicMock, mock_scan: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """ImportError from missing packaging library → install hint on stderr + returns 0 (advisory)."""
        assert cmd_expiry(path="some_module", version="2.0") == 0
        assert "pyDeprecate[audit]" in capsys.readouterr().err

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    @patch(
        "deprecate._cli.commands._expiry_findings",
        side_effect=ImportError("No module named 'packaging'", name="packaging"),
    )
    def test_packaging_missing_exit_zero_exits_zero(self, mock_check: MagicMock, mock_scan: MagicMock) -> None:
        """ImportError with exit_zero=True → returns 0 (missing packaging is always advisory)."""
        assert cmd_expiry(path="some_module", version="2.0", exit_zero=True) == 0

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    def test_version_passed_through(self, mock_check: MagicMock, mock_scan: MagicMock) -> None:
        """An explicit version is the one the scanned wrappers are compared against."""
        cmd_expiry(path="some_module", version="3.0")
        mock_check.assert_called_once_with([], "3.0", on_unparsable=ANY)

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    def test_no_recursive_threads_flag(self, mock_check: MagicMock, mock_scan: MagicMock) -> None:
        """``recursive=False`` passes through to the scan."""
        cmd_expiry(path="some_module", version="1.0", recursive=False)
        mock_scan.assert_called_once_with("some_module", recursive=False, include_members=True, exclude=[])

    @patch("deprecate._cli.scan._scan_directory", return_value=[])
    def test_plain_directory_scanned(self, mock_scan_dir: MagicMock, tmp_path: Path) -> None:
        """A plain directory without ``__init__.py`` is scanned module by module, as ``check`` and ``all`` do."""
        (tmp_path / "module_a.py").touch()
        assert cmd_expiry(path=str(tmp_path), version="1.0") == 0
        mock_scan_dir.assert_called_once()

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    @patch("deprecate._cli.commands._expiry_findings", return_value=[_EXPIRED_FINDING])
    def test_expired_reported_plain(
        self, mock_check: MagicMock, mock_scan: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Expired messages appear in plain-text output."""
        cmd_expiry(path="some_module", version="2.0")
        assert "expired" in capsys.readouterr().out.lower()

    def test_pre_scanned_wrappers_expired_exits_one(self) -> None:
        """_wrappers with an expired wrapper and matching version → returns 1."""
        config = DeprecationConfig(deprecated_in="1.0", remove_in="2.0")
        wrapper = DeprecationWrapperInfo(module="mod", function="fn", deprecated_info=config)
        with patch("deprecate._cli.scan.find_deprecation_wrappers") as mock_scan:
            result = _expiry_wrappers([wrapper], "2.0")
        mock_scan.assert_not_called()
        assert result == 1

    def test_unparsable_remove_in_recorded_as_gap(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A wrapper whose ``remove_in`` is not PEP 440 is recorded as a gap, and the run still exits 0.

        A typo such as ``remove_in="next-release"`` makes the wrapper permanently un-expirable: the audit layer skips it
        with a warning only, and the audited package can silence that warning. The CLI must therefore record the
        skipped deadline itself, so ``--report-json`` shows that this removal date was never checked, without turning
        the advisory gap into a failing exit code.
        """
        typo = DeprecationWrapperInfo(
            module="mod",
            function="typo_fn",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="next-release"),
        )
        with _record_run() as record, pytest.warns(UserWarning, match="unparsable `remove_in`"):
            result = _expiry_wrappers([typo], "2.0")

        ((kind, detail, _path),) = record.gaps
        assert result == 0
        assert kind == _GapKind.REMOVE_IN_INVALID
        assert "typo_fn" in detail
        assert "next-release" in detail
        assert "pydeprecate gap: remove_in not PEP 440: " in capsys.readouterr().err

    def test_unparsable_remove_in_keeps_expired_exit_code(self) -> None:
        """An unparsable ``remove_in`` on one wrapper does not hide a genuinely expired wrapper beside it.

        The gap is advisory and is recorded as the expiry check skips the wrapper, so a package that mixes a mistyped
        deadline with an overdue one still fails the gate for the overdue wrapper, exactly as it did before the gap
        existed.
        """
        typo = DeprecationWrapperInfo(
            module="mod",
            function="typo_fn",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="next-release"),
        )
        overdue = DeprecationWrapperInfo(
            module="mod",
            function="overdue_fn",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="2.0"),
        )
        with _record_run() as record, pytest.warns(UserWarning, match="unparsable `remove_in`"):
            result = _expiry_wrappers([typo, overdue], "2.0")

        assert result == 1
        assert [gap.kind for gap in record.gaps] == [_GapKind.REMOVE_IN_INVALID]

    def test_valid_remove_in_records_no_gap(self) -> None:
        """Wrappers with a parsable or absent ``remove_in`` leave the gap list empty.

        Most packages schedule every deprecation with a real version, and some wrappers carry no removal date at all
        (a forwarding-only shim); neither is a skipped deadline, so a clean expiry run must not report any gap.
        """
        scheduled = DeprecationWrapperInfo(
            module="mod",
            function="scheduled_fn",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="9.0"),
        )
        unscheduled = DeprecationWrapperInfo(
            module="mod",
            function="unscheduled_fn",
            deprecated_info=DeprecationConfig(deprecated_in="1.0"),
        )
        with _record_run() as record:
            result = _expiry_wrappers([scheduled, unscheduled], "2.0")

        assert result == 0
        assert record.gaps == []

    def test_unusable_version_reports_no_remove_in_gap(self) -> None:
        """When the version itself cannot be used, only the version gap is recorded, not one per wrapper.

        An auto-detected version that is not PEP 440 skips the whole expiry check, so no deadline was compared at all.
        Adding a ``remove_in`` gap for every mistyped wrapper on top would report one cause several times; the version
        gap already says nothing was checked.
        """
        typo = DeprecationWrapperInfo(
            module="mod",
            function="typo_fn",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="next-release"),
        )
        with _record_run() as record:
            result = _expiry_wrappers([typo], "not-a-version")

        assert result == 0
        assert [gap.kind for gap in record.gaps] == [_GapKind.VERSION_INVALID]

    def test_pre_scanned_wrappers_version_none_skips(self, capsys: pytest.CaptureFixture[str]) -> None:
        """_wrappers provided but version=None → warns on stderr and returns 0."""
        with patch("deprecate._cli.scan.find_deprecation_wrappers") as mock_scan:
            result = _expiry_wrappers([], None)
        mock_scan.assert_not_called()
        assert result == 0
        assert "version" in capsys.readouterr().err.lower()

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    @patch("deprecate._cli.scan._resolve_package_version", return_value=None)
    def test_unresolved_version_prints_one_gap(
        self, mock_detect: MagicMock, mock_scan: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Standalone expiry with no --version and no auto-detectable version reports one gap instead of passing.

        A CI job that cannot resolve the package version (import name unmapped, no metadata, no local pyproject) must
        be told the expiry check did not run, rather than silently reporting a misleading pass, and one cause must
        give one gap line, not two.
        """
        result = cmd_expiry(path="some_module")
        mock_detect.assert_called_once_with("some_module", path="some_module")
        mock_scan.assert_called_once()
        err = capsys.readouterr().err
        assert result == 0
        assert err.count("pydeprecate gap: version not resolved: ") == 1


# ---------------------------------------------------------------------------
# cmd_chains
# ---------------------------------------------------------------------------


class TestCmdChains:
    """Tests for cmd_chains() subcommand."""

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_no_chains_exits_zero(self, mock_scan: MagicMock) -> None:
        """No chains found → exit 0."""
        mock_scan.return_value = []
        assert cmd_chains(path="some_module") == 0

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_chains_found_exits_one(self, mock_scan: MagicMock) -> None:
        """Chains found → exit 1 (user explicitly asked for chain detection)."""
        mock_scan.return_value = [_TARGET_CHAIN]
        assert cmd_chains(path="some_module") == 1

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_chains_exit_zero_exits_zero(self, mock_scan: MagicMock) -> None:
        """exit_zero=True overrides exit code to 0 even when chains found."""
        mock_scan.return_value = [_TARGET_CHAIN]
        assert cmd_chains(path="some_module", exit_zero=True) == 0

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_no_recursive_threads_flag(self, mock_scan: MagicMock) -> None:
        """``recursive=False`` passes through to find_deprecation_wrappers."""
        mock_scan.return_value = []
        cmd_chains(path="some_module", recursive=False)
        mock_scan.assert_called_once_with("some_module", recursive=False, include_members=True, exclude=[])

    @patch("deprecate._cli.scan._scan_directory", return_value=[])
    def test_plain_directory_scanned(self, mock_scan_dir: MagicMock, tmp_path: Path) -> None:
        """A plain directory without ``__init__.py`` is scanned module by module, as ``check`` and ``all`` do."""
        (tmp_path / "module_a.py").touch()
        assert cmd_chains(path=str(tmp_path)) == 0
        mock_scan_dir.assert_called_once()

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_chains_reported_plain(self, mock_scan: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """Chain messages appear in plain-text output."""
        mock_scan.return_value = [_TARGET_CHAIN]
        cmd_chains(path="some_module")
        captured = capsys.readouterr()
        assert "chain" in captured.out.lower()

    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_stacked_chain_label(self, mock_scan: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """STACKED chain type label appears in plain-text output."""
        mock_scan.return_value = [_STACKED_CHAIN]
        cmd_chains(path="some_module")
        captured = capsys.readouterr()
        assert "stacked" in captured.out.lower()

    def test_pre_scanned_wrappers_skips_scan(self) -> None:
        """_wrappers provided → find_deprecation_wrappers not called; returns 0."""
        with patch("deprecate._cli.scan.find_deprecation_wrappers") as mock_scan:
            result = _chain_wrappers([_TARGET_CHAIN])
        mock_scan.assert_not_called()
        assert result == 1

    def test_pre_scanned_wrappers_no_chains_exits_zero(self) -> None:
        """_wrappers with no chain_type entries → returns 0."""
        plain = DeprecationWrapperInfo(module="mod", function="fn")
        with patch("deprecate._cli.scan.find_deprecation_wrappers") as mock_scan:
            result = _chain_wrappers([plain])
        mock_scan.assert_not_called()
        assert result == 0


# ---------------------------------------------------------------------------
# cmd_all
# ---------------------------------------------------------------------------


# Markdown table the patched renderer returns in the ``--output`` tests of ``cmd_all``.
_ALL_TABLE = "| Col |\n| :--- |\n| `fn` |"


class TestCmdAll:
    """Tests for cmd_all() subcommand — sequential three-check composition."""

    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_all_clean_exits_zero(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """No issues in any check → exit 0."""
        mock_find.return_value = []
        assert cmd_all(path="some_module", version="1.0") == 0

    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
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
        with (
            patch("deprecate._cli.scan._resolve_package_version", return_value="2024.06-nightly") as detect,
            _record_run() as run,
        ):
            result = cmd_all(path="some_module")
        detect.assert_called_once_with("some_module", path="some_module")
        mock_find.assert_called_once()
        captured = capsys.readouterr()
        assert result == 0
        assert "2024.06-nightly" in captured.out
        assert run.table is not None
        assert "forwarding_fn" in run.table
        assert "2024.06-nightly" in run.table
        assert "pydeprecate gap: version not PEP 440:" in captured.err
        assert "version not resolved" not in captured.err
        assert "Invalid `--version`" not in captured.err
        assert "Could not render the deprecation table" not in captured.err
        assert "No deprecation policy violations found." in captured.out

    @patch("deprecate._cli.commands._expiry_findings")
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_expired_exits_one(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """Expired wrappers found → exit 1."""
        mock_find.return_value = [DeprecationWrapperInfo(module="mod", function="fn")]
        mock_expiry.return_value = [_EXPIRED_FINDING]
        assert cmd_all(path="some_module", version="2.0") == 1

    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_chains_found_exits_one(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """Chains detected from wrappers by cmd_chains phase → exit 1."""
        mock_find.return_value = [_TARGET_CHAIN]
        assert cmd_all(path="some_module", version="1.0") == 1

    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_exit_zero_exits_zero(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """exit_zero=True overrides the invalid-args exit 1 to 0."""
        mock_find.return_value = [_INVALID_ARGS]
        assert cmd_all(path="some_module", version="1.0", exit_zero=True) == 0

    @patch("deprecate._cli.commands._expiry_findings")
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
        """An unresolved package version skips expiry while the remaining checks and table still run.

        An uninstalled project without version metadata must report exactly one unresolved-version gap, while
        preserving the wrapper scan and its advisory status table instead of treating the missing version as invalid.

        """
        mock_find.return_value = [DeprecationWrapperInfo(module="mod", function="fn")]
        with patch("deprecate._cli.scan._resolve_package_version", return_value=None) as detect:
            result = cmd_all(path="some_module")
        detect.assert_called_once_with("some_module", path="some_module")
        mock_find.assert_called_once()
        assert result == 0
        captured = capsys.readouterr()
        assert captured.err.count("pydeprecate gap: version not resolved:") == 1
        assert "version not PEP 440" not in captured.err
        assert "No Removal Target" in captured.out

    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_no_recursive_threads_flag(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """``recursive=False`` passes through to find_deprecation_wrappers."""
        mock_find.return_value = []
        cmd_all(path="some_module", version="1.0", recursive=False)
        mock_find.assert_any_call("some_module", recursive=False, include_members=True, exclude=[])

    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_all_scans_once(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """cmd_all scans once and hands that one scan to every sub-gate instead of re-importing per gate."""
        mock_find.return_value = []
        cmd_all(path="some_module", version="1.0")
        assert mock_find.call_count == 1

    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_policy_violations_stay_advisory(self, mock_find: MagicMock, mock_expiry: MagicMock) -> None:
        """A policy violation is reported by ``all`` but never changes its exit code.

        The policy defaults encode one project's release convention (a three-minor grace window on a clean
        release boundary); folding them into ``all``'s exit code would break the CI of every repo that upgrades and does
        not share that convention, so the dedicated ``policy`` subcommand is the only gate.
        """
        mock_find.return_value = [_POLICY_VIOLATION]
        assert cmd_all(path="some_module", version="2.0") == 0

    @patch("deprecate._cli.commands._status_table", return_value="")
    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[_POLICY_VIOLATION])
    def test_policy_violations_render_as_advisory(
        self, mock_find: MagicMock, mock_expiry: MagicMock, mock_status: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Inside ``all`` a policy violation prints as a ``[WARNING]`` with a trailer naming the gating command.

        The same table used to print as a bold-red ``[ERROR]`` and the run exited 0 — a reader saw an error the
        pipeline ignored and could not tell whether the gate was broken or the finding was informational. The
        warning register plus the trailer make the advisory status explicit, while the exit code stays 0.
        """
        result = cmd_all(path="some_module", version="2.0")
        captured = capsys.readouterr()
        assert result == 0
        assert "[WARNING] Found deprecation policy violations:" in captured.out
        assert "[ERROR] Found deprecation policy violations:" not in captured.out
        assert "(advisory here — run `pydeprecate policy` to gate on it)" in captured.out

    @pytest.mark.parametrize("exit_zero", [False, True])
    @patch("deprecate._cli.commands._status_table", return_value="")
    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
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
        pkg = write_pkg(tmp_path, "mypkg", "")
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
        pkg = write_pkg(tmp_path, "mypkg", "")
        broken = tmp_path / "pyproject.toml"
        broken.write_text("[tool.pydeprecate\nexclude = oops\n")
        assert cmd_all(path=str(pkg), version="1.0") == 2
        assert f"Cannot read {broken}" in capsys.readouterr().err
        mock_find.assert_not_called()

    @patch("deprecate._cli.commands._status_table", side_effect=RuntimeError("table rendering failed"))
    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_status_exception_does_not_affect_exit_code(
        self,
        mock_find: MagicMock,
        mock_expiry: MagicMock,
        mock_status: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The status table raising inside cmd_all must not change the aggregate exit code.

        The status table is a display artifact appended after the three gates. If ``_render_deprecation_table``
        or any rendering step throws, cmd_all must still report exit 0 (no issues) or exit 1 (issues found)
        based solely on the check, expiry, and chains outcomes — not on the table step crashing.

        Scenario: clean scan (no wrappers, no issues) with a broken status table. The aggregate must be
        exit 0 and the failure message must appear in stderr so the user knows the table failed.

        """
        mock_find.return_value = []
        result = cmd_all(path="some_module", version="1.0")
        assert result == 0
        captured = capsys.readouterr()
        assert "Could not render the deprecation table" in captured.err

    @patch("deprecate._cli.commands._render_deprecation_table", return_value=_ALL_TABLE)
    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_output_writes_table_to_file(
        self, mock_find: MagicMock, mock_expiry: MagicMock, mock_gen: MagicMock, tmp_path: Path
    ) -> None:
        """``all --output FILE`` saves the table it appends, as ``status --output`` does.

        A CI job running ``pydeprecate all --output DEPRECATIONS.md`` wants the table of the very scan it gated on
        as a file to publish. With a clean scan the gates pass, and the file must hold exactly the rendered table.
        """
        out_file = tmp_path / "DEPRECATIONS.md"
        result = cmd_all(path="some_module", version="1.0", output=str(out_file))
        assert result == 0
        assert out_file.read_text(encoding="utf-8") == _ALL_TABLE

    @patch("deprecate._cli.commands._render_deprecation_table", return_value=_ALL_TABLE)
    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[_INVALID_ARGS])
    def test_output_writes_table_when_gate_fails(
        self, mock_find: MagicMock, mock_expiry: MagicMock, mock_gen: MagicMock, tmp_path: Path
    ) -> None:
        """A failing gate (exit 1) still leaves the table file behind.

        Findings are exactly when the table is wanted: the file is written after the gates ran, whatever they
        found, and the aggregate exit code stays the gates' own.
        """
        out_file = tmp_path / "DEPRECATIONS.md"
        result = cmd_all(path="some_module", version="1.0", output=str(out_file))
        assert result == 1
        assert out_file.read_text(encoding="utf-8") == _ALL_TABLE

    @pytest.mark.parametrize(
        ("wrappers", "expected_code"),
        [
            pytest.param([], 0, id="gates-pass"),
            pytest.param([_INVALID_ARGS], 1, id="gate-fails"),
        ],
    )
    @patch("deprecate._cli.commands._render_deprecation_table", return_value=_ALL_TABLE)
    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_unwritable_output_keeps_gate_exit_code(
        self,
        mock_find: MagicMock,
        mock_expiry: MagicMock,
        mock_gen: MagicMock,
        wrappers: list[DeprecationWrapperInfo],
        expected_code: int,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An ``--output`` path that cannot be written is reported as a write error and never moves the exit code.

        The path here is an existing directory, which no OS lets a file be written over. The table rendered fine,
        so the message must name the write — not claim the table could not be rendered — and the aggregate code
        stays whatever the gates produced.
        """
        mock_find.return_value = wrappers
        result = cmd_all(path="some_module", version="1.0", output=str(tmp_path))
        err = capsys.readouterr().err
        assert result == expected_code
        assert "Could not write the deprecation table" in err
        assert "Could not render the deprecation table" not in err

    def test_usage_error_writes_no_table_file(self, tmp_path: Path) -> None:
        """A malformed ``--version`` (exit 2) stops ``all`` before any scan, so no table file appears.

        The table is appended after the gates; a run that never reached them has no table to save and must not
        leave an empty or stale file for a CI job to publish.
        """
        out_file = tmp_path / "DEPRECATIONS.md"
        result = cmd_all(path="some_module", version="not-a-version", output=str(out_file))
        assert result == 2
        assert not out_file.exists()

    @patch("deprecate._cli.commands._render_deprecation_table", return_value=_ALL_TABLE)
    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_policy_usage_error_writes_no_table_file(
        self, mock_find: MagicMock, mock_expiry: MagicMock, mock_gen: MagicMock, tmp_path: Path
    ) -> None:
        """A malformed policy value in ``pyproject.toml`` (exit 2) stops ``all`` before its table, so no file appears.

        The scan and the first gates have already run when the policy pass rejects ``min-grace = "abc"``; the
        usage error skips the appended table, so the ``--output`` file must not be created either.
        """
        pkg = write_pkg(tmp_path, "mypkg", "")
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate.policy]\nmin-grace = "abc"\n')
        out_file = tmp_path / "DEPRECATIONS.md"
        result = cmd_all(path=str(pkg), version="1.0", output=str(out_file))
        assert result == 2
        assert not out_file.exists()

    @patch("deprecate._cli.commands._render_deprecation_table", return_value=_ALL_TABLE)
    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_output_keeps_table_in_the_run_record(
        self, mock_find: MagicMock, mock_expiry: MagicMock, mock_gen: MagicMock, tmp_path: Path
    ) -> None:
        """Saving the ``--output`` file does not hide the table from the ``--report-json`` record of the same run.

        ``all`` reads the table back from the run record to write the file; a run started through ``cli()`` owns
        that record, and ``--report-json`` takes its table from it, so the record must still hold the table.
        """
        out_file = tmp_path / "DEPRECATIONS.md"
        with _record_run() as record:
            cmd_all(path="some_module", version="1.0", output=str(out_file))
        assert record.table == _ALL_TABLE


class TestCmdStatus:
    """Tests for cmd_status() — markdown table rendering."""

    @patch(
        "deprecate._cli.commands._render_deprecation_table",
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

        result = cmd_status(path=str(pkg))

        assert result == 0
        assert "| A | B |" in capsys.readouterr().out

    @patch("deprecate._cli.commands._render_deprecation_table", return_value="| A |")
    def test_table_renders_the_given_wrappers(self, mock_gen: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """The table helper renders exactly the wrappers it is handed, the ones of the caller's own scan.

        ``all`` scans once and renders the table of that scan; rendering a re-scan instead could show wrappers the
        gates never saw.
        """
        wrappers = [DeprecationWrapperInfo(module="m", function="f")]
        _status_table(wrappers, "some_module", None, style=TableStyle("compact"), version_explicit=False)
        mock_gen.assert_called_once()
        assert mock_gen.call_args.args[0] == wrappers

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_invalid_version_exits_two(self, mock_find: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """A typed ``--version`` that is not PEP 440 exits 2 before scanning, as it does for ``expiry`` and ``all``.

        ``status --version abc`` used to crash on the first comparison with a raw ``ValueError`` (exit 1), so a typo
        read like a failed gate in a subcommand that never gates.

        """
        assert cmd_status(path="some_module", version="abc") == 2
        mock_find.assert_not_called()
        assert "Invalid `--version` 'abc'" in capsys.readouterr().err

    @patch("deprecate._cli.commands._render_deprecation_table", return_value="| A |")
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_invalid_style_exits_two(
        self, mock_find: MagicMock, mock_gen: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An unknown ``--style`` is a usage error: exit 2 before anything is scanned or rendered.

        A CI job asking for ``--style matrx`` used to get the compact table and exit 0, so the typo went unnoticed and
        the published artifact had the wrong layout; every other malformed option already exited 2.
        """
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").touch()

        result = cmd_status(path=str(pkg), style="bogus")

        assert result == 2
        mock_find.assert_not_called()
        mock_gen.assert_not_called()
        assert "Invalid `--style` 'bogus'; expected one of: compact, matrix." in capsys.readouterr().err

    @patch("deprecate._cli.commands._render_deprecation_table", return_value="| Col |\n| :--- |\n| `fn` |")
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_output_writes_to_file(
        self, mock_find: MagicMock, mock_gen: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--output writes the markdown to a file in addition to stdout."""
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").touch()
        out_file = tmp_path / "DEPRECATIONS.md"

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
        with _scanned([]):
            assert cmd_policy(path="some_module") == 0

    def test_violations_exit_one(self) -> None:
        """A wrapper breaking a rule fails the gate so the PR that introduced it cannot merge.

        The message-required rule is the one a hurried deprecation trips most often — a warning shipped without
        a replacement named, which reads as complete until a caller asks what to migrate to.
        """
        with _scanned([_POLICY_VIOLATION]):
            assert cmd_policy(path="some_module") == 1

    def test_exit_zero_downgrades_violations(self) -> None:
        """``exit_zero=True`` reports the violations but never blocks the pipeline.

        Teams adopting the gate on an existing codebase run it advisory-first to see the backlog before making
        it blocking; without this the first run would fail every branch at once.
        """
        with _scanned([_POLICY_VIOLATION]):
            assert cmd_policy(path="some_module", exit_zero=True) == 0

    def test_invalid_grace_specification_exits_two(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A malformed ``--min-grace`` reports the accepted format and exits 2 without scanning.

        Exit 2 (usage error) separates "you configured the gate wrong" from exit 1 ("your code broke the
        policy") — a typo must never be reported as a clean policy run.
        """
        with _scanned([]):
            assert cmd_policy(path="some_module", min_grace="1 minor") == 2
        assert "min_grace" in capsys.readouterr().err

    def test_invalid_grace_flag_never_suggests_a_table(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A malformed ``--min-grace`` flag lists only the dotted spellings a command line can carry.

        A flag value is a plain string, so ``--min-grace='{"minor": 1}'`` cannot work; an error that recommended that
        table sent the user from one rejected spelling to another.
        """
        with _scanned([]):
            assert cmd_policy(path="some_module", min_grace="1 minor") == 2
        err = capsys.readouterr().err
        assert "`0.0.1` (one patch)." in err
        assert "one-key table" not in err

    @patch("deprecate._cli.commands._policy_findings")
    def test_packaging_missing_exits_zero(self, mock_policy: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        """A missing ``packaging`` library prints the install hint and stays advisory (exit 0).

        The version comparison needs the optional ``audit`` extra; a CI job that installed only the base package
        should be told what to add rather than failing on a check it never ran.
        """
        mock_policy.side_effect = [ImportError("No module named 'packaging'", name="packaging"), []]
        with _scanned([]):
            assert cmd_policy(path="some_module") == 0
        assert "pyDeprecate[audit]" in capsys.readouterr().err

    @patch("deprecate._cli.commands._policy_findings")
    def test_packaging_missing_keeps_message_required_blocking(
        self, mock_policy: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A missing optional dependency skips the grace-window rule but not missing migration guidance.

        A base-install CI job may lack ``packaging`` while still using the packaging-free message rule. Its
        warning must name the skipped version rule, then fail with exit 1 when a wrapper omits a replacement.
        """
        mock_policy.side_effect = [
            ImportError("No module named 'packaging'", name="packaging"),
            [_MESSAGE_REQUIRED_FINDING],
        ]

        with _scanned([_POLICY_VIOLATION]):
            assert cmd_policy(path="some_module") == 1
        captured = capsys.readouterr()
        assert "message-required" in captured.out
        assert "min-grace" in captured.err

    @patch("deprecate._cli.commands._policy_findings")
    def test_unrelated_import_error_propagates(self, mock_policy: MagicMock) -> None:
        """An ImportError from the scanned package itself is not swallowed as a missing-``packaging`` case.

        Reporting a broken user import as "install the audit extra" would send the reader to fix the wrong
        thing entirely, so only genuine ``packaging`` failures are converted to the advisory path.
        """
        mock_policy.side_effect = ImportError("No module named 'user_dep'", name="user_dep")
        with pytest.raises(ImportError, match="user_dep"), _scanned([]):
            cmd_policy(path="some_module")

    def test_violations_reported_plain(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Violation messages, including the rule slug, appear in the output.

        The bracketed slug is what a CI log grep filters on; a renderer that dropped it would leave the log
        readable but unfilterable, which is how the old Rich table renderer behaved before its escape was added.
        """
        with _scanned([_POLICY_VIOLATION]):
            cmd_policy(path="some_module")
        captured = capsys.readouterr()
        assert "[ERROR] Found deprecation policy violations:" in captured.out
        assert "[message-required]" in captured.out
        assert "advisory here" not in captured.out

    @pytest.mark.parametrize("exit_zero", [False, True])
    def test_advisory_rendering_leaves_exit_code_alone(
        self, exit_zero: bool, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``advisory`` changes only how violations are printed; the exit code still follows ``exit_zero``.

        ``all`` decides what to do with the policy result itself, so the advisory register must not quietly
        turn into a second ``exit_zero`` — otherwise a future caller passing ``advisory=True`` would lose the
        truthful exit 1 that ``all`` reads and ignores on purpose.
        """
        spec = _build_policy_spec(None, True)
        result = _policy_wrappers([_POLICY_VIOLATION], spec, "Policy:", exit_zero=exit_zero, advisory=True)
        captured = capsys.readouterr()
        assert result == (0 if exit_zero else 1)
        assert "[WARNING] Found deprecation policy violations:" in captured.out
        assert "(advisory here — run `pydeprecate policy` to gate on it)" in captured.out

    def test_disabled_rules_pass_through(self) -> None:
        """Disabling both rules leaves nothing to report, even for a wrapper that breaks both of them.

        The flags are the escape hatch for a project whose conventions differ; if a disabled rule still fired,
        the gate could not be adopted incrementally.
        """
        with _scanned([_POLICY_VIOLATION]):
            result = cmd_policy(path="some_module", min_grace=None, message_required=False)
        assert result == 0

    def test_header_names_built_in_source(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Without flags or a ``pyproject.toml`` the header reports both rules as built-in defaults.

        A reader of a CI log has to know which convention the gate applied and where it came from before deciding
        whether to change the code or the configuration.
        """
        with _scanned([]):
            cmd_policy(path="some_module")
        assert "min-grace=0.3 (built-in)" in capsys.readouterr().out

    def test_pyproject_table_supplies_defaults(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """``[tool.pydeprecate.policy]`` next to the scanned package replaces the built-in defaults.

        A project that wants its policy versioned with the code declares it once in ``pyproject.toml`` and every
        bare ``pydeprecate policy`` invocation — local or CI — applies the same rules without flags.
        """
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmessage-required = false\n")
        with _scanned([_POLICY_VIOLATION]):
            assert cmd_policy(path=str(tmp_path)) == 0
        assert "message-required=False (pyproject.toml)" in capsys.readouterr().out

    def test_flag_overrides_pyproject(self, tmp_path: Path) -> None:
        """An explicit flag wins over the ``pyproject.toml`` value for the same rule.

        A one-off stricter run (``--message-required=True`` on a project that disabled the rule) must not be
        silently overruled by the file, or the flag would become a no-op nobody can trust.
        """
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmessage-required = false\n")
        with _scanned([_POLICY_VIOLATION]):
            assert cmd_policy(path=str(tmp_path), message_required=True) == 1

    def test_pyproject_false_disables_grace_window(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """``min-grace = false`` in TOML stands in for the ``None`` a flag would pass.

        TOML has no null, so the file needs its own spelling for "rule off"; ``false`` mirrors the boolean rule
        and must reach the engine as a disabled window rather than as a malformed delta (exit 2).
        """
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmin-grace = false\n")
        with _scanned([]):
            assert cmd_policy(path=str(tmp_path)) == 0
        assert "min-grace=None (pyproject.toml)" in capsys.readouterr().out

    def test_malformed_pyproject_value_exits_two_naming_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A malformed ``min-grace`` in ``pyproject.toml`` exits 2 and names the file it came from.

        The value did not come from the command line, so an error that only quoted the bad delta would send the
        reader hunting through flags that were never typed.
        """
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate.policy]\nmin-grace = "1.2"\n')
        with _scanned([]):
            assert cmd_policy(path=str(tmp_path)) == 2
        assert "pyproject.toml" in capsys.readouterr().err

    def test_non_bool_message_required_exits_two(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A non-boolean ``message-required`` in ``pyproject.toml`` is a usage error, not a truthy toggle.

        ``message-required = "no"`` would be truthy if passed straight through, enabling the rule the author
        meant to switch off; rejecting it keeps a typo from silently inverting the configuration.
        """
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate.policy]\nmessage-required = "no"\n')
        with _scanned([]):
            assert cmd_policy(path=str(tmp_path)) == 2
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
        with _scanned([_POLICY_VIOLATION]):
            assert cmd_policy(path=str(tmp_path)) == 2
        assert f"Cannot read {broken}" in capsys.readouterr().err

    def test_non_table_policy_exits_two(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A ``policy`` key that is not a table is a usage error, the same as a malformed value inside the table.

        ``policy = "strict"`` configures nothing; treating it as advisory ran the built-in rules while a malformed
        ``min-grace`` inside a proper table exited 2 — the same class of mistake, two different outcomes. Both now
        stop the run and name the file.
        """
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate]\npolicy = "strict"\n')
        with _scanned([]):
            assert cmd_policy(path=str(tmp_path)) == 2
        captured = capsys.readouterr()
        assert "`policy`" in captured.err
        assert "pyproject.toml" in captured.err

    def test_unknown_pyproject_key_warns(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """An unrecognised key in the table is reported on stderr instead of being ignored.

        ``min_grace`` (underscore) is the likeliest typo; without the warning it would leave the built-in window in
        force while the author believes they configured a different one.
        """
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate.policy]\nmin_grace = "1"\n')
        with _scanned([]):
            assert cmd_policy(path=str(tmp_path)) == 0
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
        with _scanned([_POLICY_VIOLATION]):
            assert cmd_policy(path="some_module") == 1
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
        with _scanned([_POLICY_VIOLATION]):
            assert cmd_policy(path=str(tmp_path)) == 1
        assert "pyDeprecate[audit]" in capsys.readouterr().err


class TestIncludeMembersFlag:
    """Every scanning subcommand forwards ``include_members`` to its one scan."""

    @pytest.mark.parametrize(
        ("command", "kwargs"),
        [
            pytest.param(cmd_check, {}, id="check"),
            pytest.param(cmd_expiry, {"version": "1.0"}, id="expiry"),
            pytest.param(cmd_policy, {}, id="policy"),
            pytest.param(cmd_chains, {}, id="chains"),
            pytest.param(cmd_all, {"version": "1.0"}, id="all"),
            pytest.param(cmd_status, {"version": "1.0"}, id="status"),
        ],
    )
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_opt_out_reaches_scan(self, mock_find: MagicMock, command: Callable[..., int], kwargs: dict) -> None:
        """``include_members=False`` narrows the scan itself, for every subcommand.

        A project whose class members carry deprecations it does not want gated yet (a framework's overridable hooks,
        say) must be able to run any gate on module-level wrappers only, not just render a narrower ``status`` table
        while ``expiry`` and ``all`` still fail on the members.

        """
        command(path="some_module", include_members=False, **kwargs)
        mock_find.assert_called_once_with("some_module", recursive=True, include_members=False, exclude=[])


_LIST_LITERAL_EXCLUDE = '["pkg.tests", "pkg.legacy"]'


class TestInvalidExcludeExitsTwo:
    """A malformed ``--exclude`` is a usage error (exit 2) for every subcommand, before anything is scanned."""

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param(cmd_check, id="check"),
            pytest.param(cmd_expiry, id="expiry"),
            pytest.param(cmd_policy, id="policy"),
            pytest.param(cmd_chains, id="chains"),
            pytest.param(cmd_all, id="all"),
            pytest.param(cmd_status, id="status"),
        ],
    )
    @patch("deprecate._cli.scan.find_deprecation_wrappers")
    def test_list_literal_exclude_exits_two(
        self, mock_find: MagicMock, command: Callable[..., int], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A list literal passed as ``--exclude`` is refused with the comma-separated hint and nothing is scanned.

        ``--exclude='[a, b]'`` would become one pattern that matches nothing, leaving the scan silently unexcluded
        while the author believes two packages are skipped; every subcommand must reject it the same way.

        """
        assert command(path="some_module", exclude=_LIST_LITERAL_EXCLUDE) == 2
        assert f"Invalid `--exclude` value `{_LIST_LITERAL_EXCLUDE}`" in capsys.readouterr().err
        mock_find.assert_not_called()

    def test_policy_reads_exclude_from_malformed_pyproject_list(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``policy`` rejects an ``exclude`` in ``[tool.pydeprecate]`` that is not a list of strings with exit 2."""
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate]\nexclude = [1, 2]\n")
        pkg = write_pkg(tmp_path, "mypkg", "")

        assert cmd_policy(path=str(pkg)) == 2
        assert "Invalid `exclude` value" in capsys.readouterr().err


class TestPolicyWithoutPackaging:
    """``policy`` without the optional ``packaging`` library skips the grace rule instead of failing."""

    def test_grace_only_policy_is_advisory_exit_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With ``message_required`` off, the only rule left needs ``packaging``: it is skipped, exit 0, gap printed.

        A base-install CI job (no ``pyDeprecate[audit]``) running ``policy --no-message-required`` has nothing it can
        check; it must be told which rule did not run and how to enable it, and must not fail or claim a clean pass.
        ``packaging.version`` is made unimportable, the import the grace-window rule performs.

        """
        wrapper = DeprecationWrapperInfo(
            module="mod",
            function="fn",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="1.1", target=TargetMode.NOTIFY),
        )
        monkeypatch.setitem(sys.modules, "packaging.version", None)

        with _scanned([wrapper]):
            result = cmd_policy(path="some_module", message_required=False)

        captured = capsys.readouterr()
        assert result == 0
        assert "pydeprecate gap: " in captured.err
        assert "`min-grace`" in captured.err
        assert "pyDeprecate[audit]" in captured.err
        assert "No deprecation policy violations found." not in captured.out


# Two wrappers past their removal at version 2.0, defined in the package's `__init__`.
_TWO_EXPIRED_INIT = """\
from deprecate import deprecated


def new_fn(x: int) -> int:
    return x


@deprecated(target=new_fn, deprecated_in="1.0", remove_in="1.5")
def old_fn(x: int) -> int:
    pass


@deprecated(target=new_fn, deprecated_in="1.0", remove_in="1.8")
def mid_fn(x: int) -> int:
    pass
"""


def _pkg_with_two_expired(tmp_path: Path, pyproject: str = "") -> Path:
    """Create ``mypkg`` with two expired wrappers, with an optional ``pyproject.toml`` beside it."""
    pkg = write_pkg(tmp_path, "mypkg", _TWO_EXPIRED_INIT)
    if pyproject:
        (tmp_path / "pyproject.toml").write_text(pyproject)
    return pkg


class TestSuppression:
    """``--ignore`` and ``--baseline`` accept known findings: not printed, counted or gated on, but reported."""

    def test_ignore_flag_suppresses_one_finding(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """An ignored expired wrapper leaves the other one failing the gate and is counted as suppressed.

        A team accepts one overdue removal while it lands the migration, without excluding the whole module.

        """
        pkg = _pkg_with_two_expired(tmp_path)
        result = cmd_expiry(str(pkg), version="2.0", ignore=["expired:mypkg:mid_fn"])
        out = capsys.readouterr().out
        assert result == 1
        assert "mypkg.old_fn" in out
        assert "mypkg.mid_fn" not in out
        assert "1 expired wrapper(s) found." in out
        assert "1 finding(s) suppressed (ignore: 1, baseline: 0)." in out

    def test_all_suppressed_passes(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """With every finding accepted the gate passes and says nothing was found, plus the suppressed count.

        Suppressed findings must never fail the run, or the escape hatch would not let the gate go green.

        """
        pkg = _pkg_with_two_expired(tmp_path, '[tool.pydeprecate]\nignore = ["mypkg:*"]\n')
        result = cmd_expiry(str(pkg), version="2.0")
        out = capsys.readouterr().out
        assert result == 0
        assert "No expired deprecated wrappers found." in out
        assert "2 finding(s) suppressed (ignore: 2, baseline: 0)." in out

    def test_empty_flag_switches_pyproject_list_off(self, tmp_path: Path) -> None:
        """``--ignore=`` (an empty list) drops the ``pyproject.toml`` list for one run.

        An audit step that must see every finding, accepted or not, overrides the project's list without editing it.

        """
        pkg = _pkg_with_two_expired(tmp_path, '[tool.pydeprecate]\nignore = ["mypkg:*"]\n')
        assert cmd_expiry(str(pkg), version="2.0", ignore=[""]) == 1

    def test_pyproject_baseline_is_relative_to_its_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A ``baseline`` path in ``pyproject.toml`` resolves next to that file, whatever the working directory.

        CI may run the gate from another directory; the committed baseline must still be found.

        """
        baseline = {"schema": 2, "findings": [{"id": "expired:mypkg:old_fn"}, {"id": "expired:mypkg:mid_fn"}]}
        (tmp_path / "ci").mkdir()
        (tmp_path / "ci" / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
        pkg = _pkg_with_two_expired(tmp_path, '[tool.pydeprecate]\nbaseline = "ci/baseline.json"\n')
        result = cmd_expiry(str(pkg), version="2.0")
        assert result == 0
        assert "2 finding(s) suppressed (ignore: 0, baseline: 2)." in capsys.readouterr().out

    def test_stale_baseline_entry_is_reported(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A baseline id that no longer matches a finding gets one stderr advisory and never fails the run.

        After a wrapper is removed its baseline entry is dead weight; the advisory says to regenerate the baseline.

        """
        baseline = tmp_path / "baseline.json"
        ids = ["expired:mypkg:old_fn", "expired:mypkg:mid_fn", "expired:mypkg:removed_fn"]
        baseline.write_text(json.dumps({"schema": 2, "findings": [{"id": item} for item in ids]}), encoding="utf-8")
        pkg = _pkg_with_two_expired(tmp_path)
        result = cmd_expiry(str(pkg), version="2.0", baseline=str(baseline))
        assert result == 0
        assert "1 baseline entry no longer match a finding; regenerate the baseline." in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("pyproject", "kwargs", "message"),
        [
            pytest.param(
                '[tool.pydeprecate]\nignore = ["mypkg"]\n',
                {},
                "Invalid ignore entry `mypkg` from `[tool.pydeprecate]` in ",
                id="pyproject-entry-without-symbol",
            ),
            pytest.param(
                "[tool.pydeprecate]\nignore = [1]\n", {}, "Invalid `ignore` value `[1]`", id="pyproject-not-strings"
            ),
            pytest.param(
                "", {"ignore": ["nonsense"]}, "Invalid ignore entry `nonsense` from `--ignore`", id="flag-entry"
            ),
            pytest.param(
                "[tool.pydeprecate]\nbaseline = 3\n", {}, "Invalid `baseline` value `3`", id="baseline-not-a-path"
            ),
            pytest.param(
                "", {"baseline": "missing.json"}, "is not a pyDeprecate schema-2 report", id="baseline-missing"
            ),
        ],
    )
    def test_malformed_setting_exits_two_before_scanning(
        self,
        pyproject: str,
        kwargs: dict,
        message: str,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A malformed ignore list or baseline is a usage error that stops the run before any import.

        A suppression that silently failed to load would let the gate run with nothing accepted, or worse, with the
        author believing a finding is accepted that is not.

        """
        pkg = _pkg_with_two_expired(tmp_path, pyproject)
        result = cmd_expiry(str(pkg), version="2.0", **kwargs)
        captured = capsys.readouterr()
        assert result == 2
        assert message in captured.err
        assert "Scanning:" not in captured.out


# A package whose only submodule fails to import: one `module not audited` gap per scan, no finding.
_BROKEN_SUBMODULE = "raise RuntimeError('broken submodule')\n"
# Per gate subcommand, the keyword arguments that pin its version, so a package of the test leaves exactly one gap.
_GATE_COMMANDS = [
    pytest.param(cmd_check, {}, id="check"),
    pytest.param(cmd_expiry, {"version": "1.0"}, id="expiry"),
    pytest.param(cmd_policy, {}, id="policy"),
    pytest.param(cmd_chains, {}, id="chains"),
    pytest.param(cmd_all, {"version": "1.0"}, id="all"),
]


def _pkg_with_broken_submodule(tmp_path: Path, pyproject: str = "") -> Path:
    """Create ``mypkg`` whose ``broken`` submodule raises on import, with an optional ``pyproject.toml`` beside it."""
    pkg = write_pkg(tmp_path, "mypkg", "")
    (pkg / "broken.py").write_text(_BROKEN_SUBMODULE)
    if pyproject:
        (tmp_path / "pyproject.toml").write_text(pyproject)
    return pkg


@pytest.mark.filterwarnings("ignore:audit. skipped mypkg.broken:UserWarning")
class TestStrictMode:
    """``--strict`` fails a gate run that recorded any gap; ``pyproject.toml`` can switch it on."""

    @pytest.mark.parametrize(("command", "kwargs"), _GATE_COMMANDS)
    def test_gap_fails_strict_run(
        self, command: Callable[..., int], kwargs: dict, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A submodule that cannot be imported fails a strict run with exit 1 and one ``Strict mode`` line.

        The CI job's environment lacks a dependency of one submodule, so the scan skipped it and found nothing wrong
        in the rest. The gate must not pass a partial audit in strict mode, also when the subcommand is called
        directly rather than through ``cli()``: the skipped submodule is recorded as a gap of the scan itself.

        """
        pkg = _pkg_with_broken_submodule(tmp_path)
        result = command(str(pkg), strict=True, **kwargs)
        err = capsys.readouterr().err
        assert result == 1
        assert "pydeprecate gap: module not audited: mypkg.broken: RuntimeError(" in err
        assert err.count("Strict mode: 1 gap(s) fail this run.") == 1

    @pytest.mark.parametrize(("command", "kwargs"), _GATE_COMMANDS)
    def test_gap_passes_without_strict(
        self, command: Callable[..., int], kwargs: dict, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Without strict mode the same skipped submodule is reported as a gap and the run still passes.

        Gaps stay advisory by default, so upgrading never turns an existing green gate red on its own.

        """
        pkg = _pkg_with_broken_submodule(tmp_path)
        result = command(str(pkg), **kwargs)
        err = capsys.readouterr().err
        assert result == 0
        assert "pydeprecate gap: module not audited: mypkg.broken" in err
        assert "Strict mode" not in err

    def test_pyproject_switches_strict_on(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """``strict = true`` in ``[tool.pydeprecate]`` makes every run of the project strict without a flag.

        A project declares its gate policy once in ``pyproject.toml``, so the pre-commit hook and the CI step both
        fail on a partial audit without repeating ``--strict`` in each.

        """
        pkg = _pkg_with_broken_submodule(tmp_path, "[tool.pydeprecate]\nstrict = true\n")
        result = cmd_check(str(pkg))
        assert result == 1
        assert "Strict mode: 1 gap(s) fail this run." in capsys.readouterr().err

    def test_no_strict_overrides_pyproject(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """``--no-strict`` switches off a strict mode that ``pyproject.toml`` switched on, for one run.

        An advisory CI step in a project that declares ``strict = true`` reports its gaps without failing.

        """
        pkg = _pkg_with_broken_submodule(tmp_path, "[tool.pydeprecate]\nstrict = true\n")
        result = cmd_check(str(pkg), strict=False)
        assert result == 0
        assert "Strict mode" not in capsys.readouterr().err

    def test_clean_strict_run_passes(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A strict run that recorded no gap passes and prints no ``Strict mode`` line.

        Strict mode adds a failure reason; it never fails a complete audit that found nothing.

        """
        pkg = write_pkg(tmp_path, "mypkg", "")
        result = cmd_check(str(pkg), strict=True)
        assert result == 0
        assert "Strict mode" not in capsys.readouterr().err

    def test_malformed_pyproject_value_exits_two_before_scanning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A ``strict`` value that is not a boolean is a usage error naming the value and its file; nothing is scanned.

        ``strict = "yes"`` reads as true to a person but configures nothing to a parser that wants a boolean; the run
        must stop before importing the package rather than guess.

        """
        pkg = _pkg_with_broken_submodule(tmp_path, '[tool.pydeprecate]\nstrict = "yes"\n')
        result = cmd_check(str(pkg))
        captured = capsys.readouterr()
        assert result == 2
        assert "Invalid `strict` value `yes` from `[tool.pydeprecate]` in " in captured.err
        assert "Scanning:" not in captured.out

    @pytest.mark.parametrize(
        ("pyproject", "strict", "remedy"),
        [
            pytest.param("", True, "drop one of them", id="flag"),
            pytest.param(
                "[tool.pydeprecate]\nstrict = true\n",
                _FROM_PYPROJECT,
                "pass `--no-strict` to override `strict = true` for this run",
                id="pyproject",
            ),
        ],
    )
    def test_exit_zero_contradiction_exits_two(
        self,
        pyproject: str,
        strict: _ConfigBool,
        remedy: str,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Strict mode together with ``--exit-zero`` is a usage error that names where strict mode came from.

        ``--exit-zero`` passes every run, so with strict mode one of the two would silently do nothing. When strict
        mode comes from ``pyproject.toml`` the message points at ``--no-strict``, the per-run override.

        """
        pkg = _pkg_with_broken_submodule(tmp_path, pyproject)
        result = cmd_check(str(pkg), exit_zero=True, strict=strict)
        captured = capsys.readouterr()
        assert result == 2
        assert "and `--exit-zero` contradict each other" in captured.err
        assert remedy in captured.err
        assert "Scanning:" not in captured.out


# ---------------------------------------------------------------------------
# Several paths in one run, and `all --gate`
# ---------------------------------------------------------------------------

_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None
# One wrapper scheduled for removal in 2.0, so whether it is expired depends on the version of its own package.
_REMOVED_IN_TWO = """\
from deprecate import deprecated


def new_fn(x: int) -> int:
    return x


@deprecated(target=new_fn, deprecated_in="1.0", remove_in="2.0")
def old_fn(x: int) -> int:
    pass
"""


def _project(root: Path, name: str, *, pyproject: str = "", body: str = "", broken: bool = False) -> Path:
    """Create a project directory *root* holding package *name*, optionally a ``pyproject.toml`` and a broken module."""
    root.mkdir(parents=True)
    pkg = write_pkg(root, name, body)
    if broken:
        (pkg / "broken.py").write_text(_BROKEN_SUBMODULE)
    if pyproject:
        (root / "pyproject.toml").write_text(pyproject)
    return pkg


class TestMultiPath:
    """Several paths in one run: one preflight before any import, one scan each, one exit code over all of them."""

    @pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
    def test_each_path_expires_against_its_own_version(self, tmp_path: Path) -> None:
        """Two packages of a monorepo released at different versions are each checked against their own version.

        ``pkg_a`` is at 1.0 and ``pkg_b`` at 3.0, and both carry a wrapper scheduled for removal in 2.0. Only the one in
        ``pkg_b`` is overdue; a run that took the first package's version for both would miss it.

        """
        pkg_a = _project(tmp_path / "a", "pkg_a", pyproject='[project]\nversion = "1.0"\n', body=_REMOVED_IN_TWO)
        pkg_b = _project(tmp_path / "b", "pkg_b", pyproject='[project]\nversion = "3.0"\n', body=_REMOVED_IN_TWO)
        with _record_run() as run:
            result = cmd_expiry(path=[str(pkg_a), str(pkg_b)])
        assert result == 1
        assert [(finding.id, path) for finding, path in zip(run.findings, run.finding_paths)] == [
            ("expired:pkg_b:old_fn", str(pkg_b))
        ]
        assert [(item.package, item.version) for item in run.scanned] == [("pkg_a", "1.0"), ("pkg_b", "3.0")]

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_malformed_config_of_second_path_stops_before_first_scan(
        self, mock_find: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A broken ``pyproject.toml`` of the second path exits 2 before the first path is imported.

        Scanning the first package and then failing on the second would leave a half-audited run that a CI log could
        mistake for a pass; every path's configuration is read before the first import.

        """
        pkg_a = _project(tmp_path / "a", "pkg_a")
        pkg_b = _project(tmp_path / "b", "pkg_b", pyproject="[tool.pydeprecate\nexclude = oops\n")
        result = cmd_check(path=[str(pkg_a), str(pkg_b)])
        captured = capsys.readouterr()
        assert result == 2
        assert "Cannot read" in captured.err
        assert "Scanning:" not in captured.out
        mock_find.assert_not_called()

    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_same_import_name_from_two_places_exits_two(
        self, mock_find: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Two checkouts of ``mypkg`` cannot share one run: the second scan would get the first copy.

        Both directories import as ``mypkg``, and ``sys.modules`` keeps the first one imported, so the second scan would
        audit the wrong files while reporting them under the second path.

        """
        first = _project(tmp_path / "x", "mypkg")
        second = _project(tmp_path / "y", "mypkg")
        result = cmd_check(path=[str(first), str(second)])
        assert result == 2
        assert f"{first} and {second} both import as mypkg; scan them in separate runs" in capsys.readouterr().err
        mock_find.assert_not_called()

    @pytest.mark.parametrize(
        "second",
        [pytest.param("{pkg}", id="same-path"), pytest.param("{pkg}/sub", id="own-subpackage")],
    )
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_covered_path_is_scanned_once(
        self, mock_find: MagicMock, second: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A path given twice, or a package together with its own subpackage, is scanned once with one note.

        A pre-commit ``args`` list and the hook's own default can name the same package twice; the recursive scan of a
        package already covers its subpackages, so a second scan would only duplicate every finding.

        """
        pkg = _project(tmp_path / "a", "mypkg")
        write_pkg(pkg, "sub", "")
        result = cmd_check(path=[str(pkg), second.format(pkg=pkg)])
        captured = capsys.readouterr()
        assert result == 0
        assert mock_find.call_count == 1
        assert captured.out.count("Scanning:") == 1
        assert captured.err.count("scanning it once") == 1

    @pytest.mark.parametrize(
        ("broken", "expected"),
        [pytest.param("a", 1, id="gap-of-strict-path-fails"), pytest.param("b", 0, id="gap-of-other-path-passes")],
    )
    @pytest.mark.filterwarnings("ignore:audit. skipped pkg_.\\.broken:UserWarning")
    def test_strict_mode_applies_per_path(
        self, broken: str, expected: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``strict = true`` in one package's ``pyproject.toml`` fails the run on that package's gaps only.

        In a monorepo one team may have opted into strict audits while another has not. An unimportable module of the
        strict package fails the run; the same gap in the other package is reported and leaves the exit code alone.

        """
        pkg_a = _project(tmp_path / "a", "pkg_a", pyproject="[tool.pydeprecate]\nstrict = true\n", broken=broken == "a")
        pkg_b = _project(tmp_path / "b", "pkg_b", broken=broken == "b")
        result = cmd_check(path=[str(pkg_a), str(pkg_b)])
        err = capsys.readouterr().err
        assert result == expected
        assert "pydeprecate gap: module not audited:" in err


class TestAllGate:
    """``all --gate``: the listed checks gate ``all`` exactly as their own subcommand does."""

    @pytest.mark.parametrize(
        ("gate", "expected", "header", "trailer"),
        [
            pytest.param(_FROM_PYPROJECT, 0, "[WARNING] Found deprecation policy violations:", True, id="advisory"),
            pytest.param(["policy"], 1, "[ERROR] Found deprecation policy violations:", False, id="gated"),
            pytest.param(["check,policy"], 1, "[ERROR] Found deprecation policy violations:", False, id="with-default"),
        ],
    )
    @patch("deprecate._cli.commands._status_table", return_value="")
    @patch("deprecate._cli.commands._expiry_findings", return_value=[])
    def test_policy_gate_turns_violations_into_errors(
        self,
        mock_expiry: MagicMock,
        mock_status: MagicMock,
        gate: object,
        expected: int,
        header: str,
        trailer: bool,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """With ``policy`` gated, ``all`` fails on a policy violation and prints it as an error without the trailer.

        One ``pydeprecate all --gate=policy`` step replaces an ``all`` step plus a ``policy`` step, scanning once; the
        output must then stop calling the violation advisory.

        """
        with _scanned([_POLICY_VIOLATION]), _record_run() as run:
            result = cmd_all(path="some_module", version="1.0", gate=gate)  # type: ignore[arg-type]
        out = capsys.readouterr().out
        assert result == expected
        assert header in out
        assert ("(advisory here" in out) is trailer
        assert ("policy" in run.options["gates"]) is not trailer

    @pytest.mark.parametrize(
        ("gate", "pyproject", "message"),
        [
            pytest.param(["bogus"], "", "Invalid `gate` value `bogus` from `--gate`", id="unknown-flag-value"),
            pytest.param(
                _FROM_PYPROJECT, "[tool.pydeprecate]\ngate = true\n", "Invalid `gate` value `True`", id="type"
            ),
            pytest.param(_FROM_PYPROJECT, '[tool.pydeprecate]\ngate = ["lint"]\n', "`lint`", id="unknown-in-file"),
        ],
    )
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[])
    def test_malformed_gate_exits_two(
        self,
        mock_find: MagicMock,
        gate: object,
        pyproject: str,
        message: str,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An unknown check or a non-list ``gate`` value is a usage error before anything is scanned.

        An unknown name such as ``lint``, or a misspelt ``policy``, would otherwise gate on nothing new while the author
        believes the policy is enforced.

        """
        pkg = _project(tmp_path / "a", "mypkg", pyproject=pyproject)
        result = cmd_all(path=str(pkg), version="1.0", gate=gate)  # type: ignore[arg-type]
        assert result == 2
        assert message in capsys.readouterr().err
        mock_find.assert_not_called()

    @patch("deprecate._cli.commands._status_table", return_value="")
    @patch("deprecate._cli.scan.find_deprecation_wrappers", return_value=[_POLICY_VIOLATION])
    def test_one_scan_per_path(self, mock_find: MagicMock, mock_status: MagicMock, tmp_path: Path) -> None:
        """``all --gate=policy`` over two packages imports each once and gates on both from those scans.

        The gate replaces a separate ``policy`` run, so the policy check must reuse the scan the other checks used
        instead of importing every package a second time.

        """
        pkg_a = _project(tmp_path / "a", "pkg_a")
        pkg_b = _project(tmp_path / "b", "pkg_b")
        result = cmd_all(path=[str(pkg_a), str(pkg_b)], version="1.0", gate=["policy"])
        assert result == 1
        assert [call.args[0] for call in mock_find.call_args_list] == ["pkg_a", "pkg_b"]


def _write_baseline(path: Path, ids: list[str]) -> Path:
    """Write a schema-2 baseline report accepting the finding *ids*."""
    path.write_text(json.dumps({"schema": 2, "findings": [{"id": item} for item in ids]}), encoding="utf-8")
    return path


@pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging (pip install 'pyDeprecate[audit]')")
class TestStaleBaselineScope:
    """A baseline id reads as stale only for a rule the run evaluated in a module the run audited."""

    @pytest.mark.parametrize(
        ("extra_id", "stale"),
        [
            pytest.param("expired:pkg_a:removed_fn", 1, id="gone-from-audited-module"),
            pytest.param("expired:pkg_a.broken:gone_fn", 0, id="module-failed-to-import"),
            pytest.param("expired:other_pkg:old_fn", 0, id="package-not-scanned"),
        ],
    )
    def test_only_audited_modules_count(
        self, extra_id: str, stale: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An id of a module that failed to import, or of a package the run did not scan, is not stale.

        A monorepo shares one baseline across per-package runs, and a module missing a runtime dependency is not
        audited; telling the user to regenerate would drop accepted ids that the next full run fails on again.

        """
        pkg = _project(tmp_path / "a", "pkg_a", body=_REMOVED_IN_TWO, broken=True)
        baseline = _write_baseline(tmp_path / "baseline.json", ["expired:pkg_a:old_fn", extra_id])
        with _record_run() as run:
            result = cmd_expiry(path=str(pkg), version="3.0", baseline=str(baseline))
        assert result == 0
        assert run.baseline_unmatched == stale
        assert ("no longer match a finding" in capsys.readouterr().err) is bool(stale)

    def test_skipped_check_of_one_path_keeps_its_ids(self, tmp_path: Path) -> None:
        """Ids of a path whose expiry check was skipped stay expected-free, although another path ran the check.

        ``pkg_a`` has no resolvable version, so its expiry check is skipped; ``pkg_b`` at 3.0 runs it. The shared
        baseline's ``pkg_a`` id must not be counted as stale through ``pkg_b``'s copy of the same baseline.

        """
        pkg_a = _project(tmp_path / "a", "pkg_a", body=_REMOVED_IN_TWO)
        pkg_b = _project(tmp_path / "b", "pkg_b", pyproject='[project]\nversion = "3.0"\n', body=_REMOVED_IN_TWO)
        baseline = _write_baseline(tmp_path / "baseline.json", ["expired:pkg_a:old_fn", "expired:pkg_b:old_fn"])
        with _record_run() as run:
            result = cmd_expiry(path=[str(pkg_a), str(pkg_b)], baseline=str(baseline))
        assert result == 0
        assert run.baseline_unmatched == 0
