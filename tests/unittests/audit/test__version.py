"""Unit tests for :mod:`deprecate.audit._version`: version detection and ``pyproject.toml`` reading."""

import importlib.metadata
import sys
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deprecate.audit._version import (
    _distribution_for_import,
    _load_toml,
    _resolve_package_version,
    _version_from_dynamic,
    _version_from_toml,
)

# ---------------------------------------------------------------------------
# _resolve_package_version (package helper)
# ---------------------------------------------------------------------------


class TestResolvePackageVersion:
    """Tests for _resolve_package_version() — one resolution chain for every CLI subcommand and the audit API."""

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
        assert _resolve_package_version("somepkg", path="somepkg") is None

    def test_existing_path_uses_local_pyproject(self, tmp_path: Path) -> None:
        """An existing package directory still resolves the version from the nearby ``pyproject.toml``.

        In a development checkout the local ``pyproject.toml`` version may differ from the installed
        distribution; scanning the checkout directory must keep preferring the local file.

        """
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "mypkg"\nversion = "7.7.7"\n')
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").touch()
        assert _resolve_package_version("mypkg", path=str(pkg)) == "7.7.7"

    def test_none_path_falls_back_to_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without a path, the installed distribution metadata provides the version."""
        monkeypatch.setattr(importlib.metadata, "version", MagicMock(return_value="1.2.3"))
        assert _resolve_package_version("somepkg", path=None) == "1.2.3"

    def test_direct_metadata_skips_distribution_mapping(self) -> None:
        """Resolve a known distribution without enumerating every installed package.

        A CLI invocation for an installed package with matching import and distribution names needs only one
        metadata lookup. Building the environment-wide import mapping first adds work unrelated to that result.
        """
        expected = importlib.metadata.version("pytest")
        with patch.object(importlib.metadata, "packages_distributions", create=True) as mapping:
            assert _resolve_package_version("pytest") == expected
        mapping.assert_not_called()

    def test_import_name_maps_to_distribution(self) -> None:
        """Auto-detect resolves the version even when the distribution name differs from the import name.

        ``importlib.metadata.version`` needs the *distribution* name (``pyDeprecate``) but the CLI is
        handed the *import* name (``deprecate``) — the same mismatch as Pillow/PIL and scikit-learn/sklearn.
        Without the import→distribution mapping this whole class of packages silently yields ``None`` and
        the expiry gate runs against an undefined version.
        """
        expected = importlib.metadata.version("pyDeprecate")
        assert _resolve_package_version("deprecate", path=None) == expected

    @pytest.mark.parametrize("dynamic_pyproject", [False, True])
    def test_uninstalled_package_falls_back_to_dunder_version(self, tmp_path: Path, dynamic_pyproject: bool) -> None:
        """An uninstalled checkout with only ``__version__`` resolves to that version.

        A setup.py-era checkout has no ``pyproject.toml`` version, and a dynamic-version project whose distribution
        name differs from its import name cannot be imported under ``[project].name``. Neither is installed in an
        isolated pre-commit environment, so ``__version__`` is the only source left; every gate must reach it.
        """
        if dynamic_pyproject:
            (tmp_path / "pyproject.toml").write_text(
                '[project]\nname = "uninstalled-fixture-dist"\ndynamic = ["version"]\n'
            )
        pkg = tmp_path / "uninstalled_fixture_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text('__version__ = "2.0"\n')
        assert _resolve_package_version("uninstalled_fixture_pkg", path=str(pkg)) == "2.0"

    def test_non_string_dunder_version_is_unresolved(self, tmp_path: Path) -> None:
        """A non-string ``__version__`` (e.g. a tuple) yields ``None`` so the caller reports an unresolved version."""
        pkg = tmp_path / "tuple_version_fixture_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("__version__ = (1, 2)\n")
        assert _resolve_package_version("tuple_version_fixture_pkg", path=str(pkg)) is None

    def test_plain_path_is_never_imported(self, tmp_path: Path) -> None:
        """A raw path that is no importable dotted name resolves to ``None`` without an import attempt.

        The CLI hands over the raw path when it cannot derive a module name (a plain directory); importing that
        string would at best fail and at worst import an unrelated namespace package of the same name.
        """
        plain = tmp_path / "plain dir"
        plain.mkdir()
        with patch("importlib.import_module") as mock_import:
            assert _resolve_package_version(str(plain), path=str(plain)) is None
        mock_import.assert_not_called()

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
        with patch("deprecate.audit._version.importlib.metadata.packages_distributions", None, create=True):
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

    def test_strict_raises_on_malformed_toml(self, tmp_path: Path) -> None:
        """With ``strict=True`` a parse error propagates as the parser's ``ValueError`` instead of becoming ``{}``.

        The configuration reader must tell a broken ``pyproject.toml`` apart from one without a
        ``[tool.pydeprecate]`` table; the tolerant ``{}`` conflates the two, which is how a syntax error used to
        reset every policy rule to its built-in default without a word.
        """
        toml = tmp_path / "pyproject.toml"
        toml.write_text("this is = = not valid toml [[[")
        with pytest.raises(ValueError, match="line 1"):
            _load_toml(str(toml), strict=True)

    def test_strict_raises_on_unreadable_file(self, tmp_path: Path) -> None:
        """With ``strict=True`` an I/O failure propagates as ``OSError`` — a vanished file is not an empty one."""
        with pytest.raises(OSError, match="does_not_exist"):
            _load_toml(str(tmp_path / "does_not_exist.toml"), strict=True)


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
