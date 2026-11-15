"""Unit tests for the static source discovery."""

import os
import sys
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path

import pytest

from deprecate._gaps import _GapKind
from deprecate._static.discovery import _locate_static, _StaticSource
from deprecate._static.rows import _read_static
from tests.unittests.static._helpers import (
    _DEP,
    _NAMESPACE,
    _PKG,
    _read_package,
    _refusing,
    _rows,
    _write,
    _write_namespace_portion,
)


class TestDiscovery:
    """The static walk reads the files the import walk would import, and reports the ones it cannot read."""

    def test_excluded_module_is_never_read(self, tmp_path: Path) -> None:
        """An excluded submodule is skipped before reading: its syntax error never surfaces.

        ``exclude`` keeps a module out of the audit; reading it anyway could fail the run on a file the user asked
        to leave alone.

        """
        pkg = _write(tmp_path, {"__init__.py": "", "skip.py": "def broken(:\n", "keep.py": "X = 1\n"})
        _, gaps = _read_package(pkg, exclude=(f"{_PKG}.skip",))
        assert not any(item.kind is _GapKind.MODULE_NOT_AUDITED for item in gaps)

    def test_unreadable_module_is_a_gap(self, tmp_path: Path) -> None:
        """A file that does not parse is a ``module not audited`` gap, and the rest of the package is still read.

        Import mode reports a submodule that fails to import the same way; one broken file never aborts the audit.

        """
        pkg = _write(
            tmp_path,
            {
                "__init__.py": "",
                "broken.py": "def broken(:\n",
                "good.py": "from deprecate import deprecated\n\n@deprecated(deprecated_in='1.0')\ndef old(x):\n"
                "    return x\n",
            },
        )
        wrappers, gaps = _read_package(pkg)
        assert _rows(wrappers) == [f"{_PKG}.good:old"]
        assert any(f"{_PKG}.broken: could not read its source" in item.detail for item in gaps)

    @pytest.mark.parametrize(
        ("lister", "blocked", "modules"),
        [
            pytest.param("scandir", "", f"{_PKG}.*", id="package-root"),
            pytest.param("listdir", "sub", f"{_PKG}.sub", id="subpackage"),
        ],
    )
    def test_unlistable_directory_is_a_gap(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        lister: str,
        blocked: str,
        modules: str,
    ) -> None:
        """A package directory the walk cannot list prints a ``module not audited`` gap naming its modules and itself.

        A directory without read permission once looked like a package with no submodules, so every wrapper below it
        passed the audit unseen; the gap names the directory and the error, while the rest of the tree is still read.
        The detail opens with the module names left out, as the import scan's does (``<package>.*`` when only the
        submodules are missing), so a baseline id under them is not taken for a stale one.

        """
        pkg = _write(tmp_path, {"__init__.py": "", "sub/__init__.py": "", "m.py": ""})
        monkeypatch.setattr(os, lister, _refusing(getattr(os, lister), pkg / blocked))
        _, gaps = _read_package(pkg)
        expected = f"{modules}: submodules of {pkg / blocked} not listed"
        assert any(expected in item.detail for item in gaps)

    def test_module_without_source_is_a_gap(self, tmp_path: Path) -> None:
        """A compiled extension module with no ``.py`` beside it is reported, since its wrappers cannot be read.

        Import mode would audit it; static mode has nothing to parse, so it says so.

        """
        pkg = _write(tmp_path, {"__init__.py": ""})
        (pkg / f"fast{EXTENSION_SUFFIXES[0]}").write_bytes(b"")  # the running interpreter's own extension suffix
        _, gaps = _read_package(pkg)
        assert any(f"{_PKG}.fast: no Python source to read" in item.detail for item in gaps)

    @pytest.mark.parametrize(
        ("files", "recursive", "exclude", "gap"),
        [
            pytest.param(
                {"__init__.py": "from .broken import thing\n"},
                False,
                (),
                f"{_PKG}.broken: could not read its source",
                id="no-recursive",
            ),
            pytest.param(
                {"__init__.py": "", "a.py": "from .broken import thing\n"},
                True,
                (f"{_PKG}.broken",),
                f"{_PKG}.broken: could not read its source",
                id="excluded-module-imported-by-a-walked-one",
            ),
            pytest.param(
                {"__init__.py": "from .compiled import thing\n"},
                False,
                (),
                f"{_PKG}.compiled: no Python source to read",
                id="imported-module-without-python-source",
            ),
        ],
    )
    def test_module_reached_only_through_an_import_is_a_gap(
        self,
        tmp_path: Path,
        files: dict[str, str],
        recursive: bool,
        exclude: tuple[str, ...],
        gap: str,
    ) -> None:
        """A module the walk never reaches still reports why it was not audited when another module imports it.

        Under ``--no-recursive`` or ``--exclude`` the broken module is read only to follow the importer's names; the
        reason recorded there is the only signal that the importer's view is incomplete, so it must be printed.

        """
        pkg = _write(tmp_path, {**files, "broken.py": "def (:\n"})
        _, gaps = _read_package(pkg, recursive=recursive, exclude=exclude)
        assert any(f"{gap}" in item.detail for item in gaps)

    @pytest.mark.parametrize(
        ("recursive", "exclude"),
        [
            pytest.param(False, (), id="no-recursive"),
            pytest.param(True, (f"{_PKG}.core",), id="excluded-module-re-exported-by-a-walked-one"),
        ],
    )
    def test_structural_gaps_of_a_module_reached_only_through_an_import_are_printed(
        self, tmp_path: Path, recursive: bool, exclude: tuple[str, ...]
    ) -> None:
        """A shape the reader cannot place in a module the walk skips is still a gap when another module re-exports it.

        The package re-exports ``old`` from ``core``, which builds its wrapper through ``partial(deprecated, ...)``, a
        shape static mode does not read. Under ``--no-recursive`` or ``--exclude`` ``core`` is analyzed only to follow
        the re-export, and the gap that analysis records is the sole sign that ``old`` went unaudited.

        """
        core = (
            f"from functools import partial\n{_DEP}\n"
            "old = partial(deprecated, deprecated_in='1.0', remove_in='2.0')(print)\n"
        )
        pkg = _write(tmp_path, {"__init__.py": "from .core import old\n", "core.py": core})
        _, gaps = _read_package(pkg, recursive=recursive, exclude=exclude)
        assert any("core.py:4: unrecognized use of `deprecated`" in item.detail for item in gaps)

    def test_plain_directory_reads_top_level_files(self, tmp_path: Path) -> None:
        """A plain directory reads its top-level modules by file name and reports nested files as not audited.

        This mirrors the import scan of a directory without ``__init__.py``, which only imports top-level modules.

        """
        (tmp_path / "nested").mkdir()
        (tmp_path / "nested" / "inner.py").write_text("X = 1\n")
        (tmp_path / "mod.py").write_text(
            "from deprecate import deprecated\n\n@deprecated(deprecated_in='1.0')\ndef old(x):\n    return x\n"
        )
        source = _locate_static(str(tmp_path), None)
        assert isinstance(source, _StaticSource)
        found, gaps = _read_static(source, recursive=True, include_members=True, exclude=[])
        assert _rows(found) == ["mod:old"]
        assert any(item.kind is _GapKind.NESTED_FILES for item in gaps)

    def test_namespace_package_reads_every_portion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A namespace package split over two import-path entries is read from both, with no gap for its root.

        The import scan walks every directory of ``__path__`` and has no root module to report; reading only the
        first directory would drop the second one's wrappers silently, and a root gap would cry wolf.

        """
        _write_namespace_portion(tmp_path / "first", "alpha")
        _write_namespace_portion(tmp_path / "second", "beta")
        monkeypatch.syspath_prepend(str(tmp_path / "first"))
        monkeypatch.syspath_prepend(str(tmp_path / "second"))
        source = _locate_static(_NAMESPACE, None)
        assert isinstance(source, _StaticSource)
        found, gaps = _read_static(source, recursive=True, include_members=True, exclude=[])
        assert sorted(_rows(found)) == [f"{_NAMESPACE}.alpha:old", f"{_NAMESPACE}.beta:old"]
        assert not any(item.kind is _GapKind.MODULE_NOT_AUDITED for item in gaps)

    @pytest.mark.parametrize(
        ("path", "message"),
        [
            pytest.param("os.path", "would import its parent packages", id="dotted"),
            pytest.param("no_such_module_xyz", "No such path or importable module", id="missing"),
            pytest.param("sys", "has no Python source", id="builtin"),
        ],
    )
    def test_module_names_without_readable_source(self, path: str, message: str) -> None:
        """A module name static mode cannot read without importing is a usage error, not a silent empty scan.

        Locating a dotted name imports its parents, and a built-in has no source; both would make the scan either run
        code or audit nothing.

        """
        located = _locate_static(path, None)
        assert isinstance(located, str)
        assert message in located

    def test_top_level_module_name_located_without_import(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A top-level module name is located on the import path without running the module.

        The module raises when imported; locating it for a static read must not trigger that.

        """
        (tmp_path / "standalone_static_mod.py").write_text("raise RuntimeError('imported')\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        located = _locate_static("standalone_static_mod", None)
        assert isinstance(located, _StaticSource)
        assert (located.kind, located.location.name) == ("module", "standalone_static_mod.py")
        assert "standalone_static_mod" not in sys.modules
