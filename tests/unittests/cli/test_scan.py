"""Unit tests for :mod:`deprecate._cli.scan`: resolving a path into deprecation wrappers."""

import sys
import types
from importlib.machinery import PathFinder
from importlib.util import module_from_spec
from pathlib import Path
from unittest.mock import patch

import pytest

from deprecate._cli.config import _FROM_PYPROJECT
from deprecate._cli.scan import (
    _foreign_copy,
    _ImportIsolation,
    _scan_for_command,
    _scan_path,
    _ScanTarget,
    _walk_reaches,
)
from tests._helpers import write_pkg

#: The top-level name the import-isolation tests put in ``sys.modules``; no real module has it.
_ISOLATED_NAME = "isolation_fixture_common"


def _loaded_from(root: Path) -> types.ModuleType:
    """Return a stand-in for the package ``_ISOLATED_NAME`` as if it had been imported from *root*."""
    module = types.ModuleType(_ISOLATED_NAME)
    module.__file__ = str(root / _ISOLATED_NAME / "__init__.py")
    module.__path__ = [str(root / _ISOLATED_NAME)]
    return module


def _package_target(root: Path, name: str) -> _ScanTarget:
    """Create the package *name* under *root* and return the target a run would plan for its directory."""
    root.mkdir(parents=True, exist_ok=True)
    pkg = write_pkg(root, name, "").resolve()
    return _ScanTarget(str(pkg), name, name, pkg)


class TestImportIsolation:
    """Tests for ``_ImportIsolation`` — dropping a module another path's scan imported before the next scan."""

    @pytest.mark.parametrize(
        ("loaded_root", "released"),
        [pytest.param("a", True, id="other-checkout"), pytest.param("b", False, id="own-checkout")],
    )
    def test_bound_name_from_elsewhere_is_released(
        self, loaded_root: str, released: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A path's own package name loaded from another checkout during the run is dropped before the path's scan.

        An earlier path's code imported ``common`` from its checkout ``a``; the next path is the checkout ``b`` of
        ``common`` itself. Its scan must import ``b``'s copy, and a copy already loaded from ``b`` is kept as it is.

        """
        target = _package_target(tmp_path / "b", _ISOLATED_NAME)
        isolation = _ImportIsolation()
        monkeypatch.setitem(sys.modules, _ISOLATED_NAME, _loaded_from(tmp_path / loaded_root))
        isolation.release_stale(target)
        assert (_ISOLATED_NAME not in sys.modules) is released

    @pytest.mark.parametrize(
        ("loaded_root", "released"),
        [pytest.param("a", True, id="earlier-path-root"), pytest.param("site", False, id="environment")],
    )
    def test_borrowed_name_is_released_only_from_an_earlier_path(
        self, loaded_root: str, released: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A name the next path does not hold is dropped only when an earlier path's checkout provided it.

        Scanned alone, the path ``g`` could not import a module that only the earlier path ``a`` provides, so that copy
        is dropped; a copy from the installed environment is what ``g`` would import anyway and stays.

        """
        first = _package_target(tmp_path / "a", "alpha")
        second = _package_target(tmp_path / "g", "gamma")
        isolation = _ImportIsolation()
        isolation.release_stale(first)
        monkeypatch.setitem(sys.modules, _ISOLATED_NAME, _loaded_from(tmp_path / loaded_root))
        isolation.release_stale(second)
        assert (_ISOLATED_NAME not in sys.modules) is released

    def test_module_imported_before_the_run_is_kept(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A module loaded before the run started is never dropped, even when the path holds its own copy.

        The CLI's own ``deprecate`` or a standard-library module is loaded before any scan; dropping it would make the
        CLI re-import itself mid-run. The scan reports such a copy as ``other copy audited`` instead.

        """
        target = _package_target(tmp_path / "b", _ISOLATED_NAME)
        loaded = _loaded_from(tmp_path / "a")
        monkeypatch.setitem(sys.modules, _ISOLATED_NAME, loaded)
        isolation = _ImportIsolation()
        isolation.release_stale(target)
        assert sys.modules[_ISOLATED_NAME] is loaded


class TestWalkReaches:
    """Tests for ``_walk_reaches`` — whether a package's recursive scan imports a submodule, read from disk."""

    @pytest.mark.parametrize(
        ("parts", "expected"),
        [
            pytest.param(["sub", "deep"], True, id="package-chain"),
            pytest.param(["sub", "leaf"], True, id="module-file"),
            pytest.param(["data", "inner"], False, id="plain-directory-between"),
            pytest.param(["sub", "nosuch"], False, id="missing-leaf"),
            pytest.param(["sub", "leaf", "deeper"], False, id="below-module-file"),
            pytest.param([], False, id="no-parts"),
        ],
    )
    def test_reaches_only_through_packages(self, parts: list[str], expected: bool, tmp_path: Path) -> None:
        """Only a chain of package directories ending in a package or module file is imported by the walk.

        A run drops a path as already audited by its parent package's scan only when that scan's walk imports it; the
        walk enters directories with ``__init__.py`` only, so ``data/inner`` under a plain ``data`` directory, a name
        that does not exist and anything below a module file are never imported.

        """
        write_pkg(tmp_path, "sub", "")
        write_pkg(tmp_path / "sub", "deep", "")
        (tmp_path / "sub" / "leaf.py").write_text("")
        (tmp_path / "data").mkdir()
        write_pkg(tmp_path / "data", "inner", "")
        assert _walk_reaches(tmp_path, parts) is expected


class TestScanPath:
    """Tests for ``_scan_path`` resolving a package from a project-root directory."""

    def test_multiple_child_packages_raise(self, tmp_path: Path) -> None:
        """A directory holding two packages raises ``ValueError`` naming both instead of scanning neither.

        A flat-layout root with ``mypkg/`` and ``tests/`` used to fall through to the top-level-module scan, which
        imports only ``setup.py``-style files, so ``pydeprecate check`` reported a clean run on an unscanned project.

        """
        for name in ("mypkg", "tests"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "__init__.py").write_text("")
        with pytest.raises(ValueError, match=r"contains multiple packages \(mypkg, tests\)"):
            _scan_path(str(tmp_path))

    def test_directory_without_packages_scans_top_level_modules(self, tmp_path: Path) -> None:
        """A plain directory with no package keeps the top-level-module scan and finds no wrappers in a clean one.

        Only an ambiguous choice between several packages is an error; a scripts directory has nothing to choose.

        """
        (tmp_path / "plain_script.py").write_text("VALUE = 1\n")
        with patch("deprecate._cli.scan._scan_directory", return_value=[]) as mock_scan:
            assert _scan_path(str(tmp_path)) == []
        mock_scan.assert_called_once()


class TestForeignCopy:
    """Tests for ``_foreign_copy`` — spotting an imported package that does not come from the scanned directory."""

    _NAME = "foreign_copy_fixture_pkg"

    def _pkg_dir(self, root: Path) -> Path:
        pkg = root / self._NAME
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        return pkg

    def test_not_imported_is_no_foreign_copy(self, tmp_path: Path) -> None:
        """A package nobody imported yet has no copy that could shadow the scanned directory."""
        assert _foreign_copy(self._pkg_dir(tmp_path)) is None

    def test_copy_from_scanned_directory_is_accepted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The module imported from the scanned directory itself is the copy the scan is meant to audit."""
        pkg = self._pkg_dir(tmp_path)
        module = types.ModuleType(self._NAME)
        module.__file__ = str(pkg / "__init__.py")
        monkeypatch.setitem(sys.modules, self._NAME, module)
        assert _foreign_copy(pkg) is None

    @pytest.mark.parametrize("same_file", [False, True])
    def test_plain_module_compares_file_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_file: bool
    ) -> None:
        """Modules sharing a directory still need the requested source file to be audited.

        Comparing only parent directories would accept an imported file with a different name as the requested
        module, hiding a redirected import even though the two sources contain different wrappers.

        """
        source = tmp_path / f"{self._NAME}.py"
        source.write_text("VALUE = 1\n")
        loaded = source if same_file else tmp_path / "different.py"
        module = types.ModuleType(self._NAME)
        module.__file__ = str(loaded)
        monkeypatch.setitem(sys.modules, self._NAME, module)
        assert _foreign_copy(source) == (None if same_file else str(loaded.resolve()))

    def test_plain_module_shadowed_by_namespace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A cached namespace package must be reported when it shadows a requested plain module.

        An earlier import can cache a namespace package with no source file or spec origin. A later directory scan
        then audits that package instead of its same-named Python file, so the gap must name the namespace location.

        """
        installed = tmp_path / "installed"
        namespace = installed / self._NAME
        namespace.mkdir(parents=True)
        spec = PathFinder.find_spec(self._NAME, [str(installed)])
        assert spec is not None
        module = module_from_spec(spec)
        assert module.__spec__ is not None
        assert module.__spec__.origin is None
        assert getattr(module, "__file__", None) is None
        monkeypatch.setitem(sys.modules, self._NAME, module)
        source = tmp_path / f"{self._NAME}.py"
        source.write_text("VALUE = 1\n")
        assert _foreign_copy(source) == str(namespace.resolve())
        assert _scan_path(str(tmp_path)) == []
        assert (
            f"pydeprecate gap: other copy audited: `{self._NAME}` was imported from {namespace.resolve()},"
            f" not from {source.resolve()}; the scan audited that copy."
        ) in capsys.readouterr().err

    @pytest.mark.parametrize("namespace", [False, True])
    def test_copy_from_elsewhere_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, namespace: bool
    ) -> None:
        """A module of the same name imported from another directory is reported with that directory.

        An editable install of a second checkout resolves the import name through its own finder ahead of
        ``sys.path``, so even an import made during the scan can come from somewhere else; a namespace package
        carries only ``__path__``, no ``__file__``.

        """
        pkg = self._pkg_dir(tmp_path / "scanned")
        elsewhere = tmp_path / "installed" / self._NAME
        elsewhere.mkdir(parents=True)
        module = types.ModuleType(self._NAME)
        if namespace:
            module.__path__ = [str(elsewhere)]
        else:
            module.__file__ = str(elsewhere / "__init__.py")
        monkeypatch.setitem(sys.modules, self._NAME, module)
        assert _foreign_copy(pkg) == str(elsewhere.resolve())


class TestVersionImportArguments:
    """Version resolution and scanning hide arguments and restore the invoking process afterwards."""

    @pytest.mark.parametrize("raises", [False, True])
    def test_resolver_sees_empty_arguments(self, monkeypatch: pytest.MonkeyPatch, raises: bool) -> None:
        """A resolver that imports module-level parsers cannot consume the CLI's own flags.

        Even a failed version import must restore the caller's argv; a successful resolver must not cause a
        second scan while handing its version to the shared scan record.

        """
        original = ["pydeprecate", "expiry", "example", "--report-json=report.json"]
        monkeypatch.setattr(sys, "argv", original[:])
        with (
            patch("deprecate._cli.scan._resolve_package_version", return_value="3.0") as resolver,
            patch("deprecate._cli.scan._scan_path", return_value=[]) as scan,
        ):

            def _resolve(module: str, *, path: str) -> str:
                """Observe the arguments visible to the version import."""
                assert module == path == "example"
                assert sys.argv == original[:1]
                if raises:
                    raise RuntimeError("version import failed")
                return "3.0"

            resolver.side_effect = _resolve
            if raises:
                with pytest.raises(RuntimeError, match="version import failed"):
                    _scan_for_command("example", recursive=True, exclude=_FROM_PYPROJECT, resolve_version=True)
                scan.assert_not_called()
            else:
                result = _scan_for_command("example", recursive=True, exclude=_FROM_PYPROJECT, resolve_version=True)
                assert result is not None
                assert result.version == "3.0"
                scan.assert_called_once()
            resolver.assert_called_once_with("example", path="example")
        assert sys.argv == original
