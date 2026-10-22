"""Unit tests for :mod:`deprecate._cli.scan`: resolving a path into deprecation wrappers."""

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from deprecate._cli.scan import _foreign_copy, _scan_path


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
