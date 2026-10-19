"""Unit tests for private helpers in :mod:`deprecate._pkg`: version detection and pyproject reading."""

import importlib.metadata
import sys
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deprecate._pkg import (
    _auto_detect_version,
    _ConfigReadError,
    _distribution_for_import,
    _load_toml,
    _read_pydeprecate_config,
    _version_from_dynamic,
    _version_from_toml,
)

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

    def test_broken_nearer_pyproject_is_fatal_not_skipped(self, tmp_path: Path) -> None:
        """A ``pyproject.toml`` that fails to parse raises, naming the file, instead of being walked past.

        A sub-package's ``pyproject.toml`` with a syntax error used to read as "no table here" and the walk
        continued to the parent, so the parent's stricter policy — or the built-in defaults — applied while the
        author believed their own file was in force. The error must name the broken file, since the parser's
        own message does not.
        """
        (tmp_path / "pyproject.toml").write_text("[tool.pydeprecate.policy]\nmessage-required = false\n")
        sub = tmp_path / "sub"
        sub.mkdir()
        broken = sub / "pyproject.toml"
        broken.write_text("[tool.pydeprecate\npolicy = oops\n")
        with pytest.raises(_ConfigReadError, match="Cannot read") as exc_info:
            _read_pydeprecate_config(str(sub))
        assert exc_info.value.path == str(broken)
        assert str(broken) in str(exc_info.value)


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
