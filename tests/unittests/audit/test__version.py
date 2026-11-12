"""Unit tests for :mod:`deprecate.audit._version`: version detection and ``pyproject.toml`` reading."""

import ast
import importlib.metadata
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from deprecate.audit._version import (
    _NOT_A_PROJECT,
    _distribution_for_import,
    _distribution_from_top_level,
    _literal_all,
    _load_toml,
    _module_source_file,
    _read_pyproject_version,
    _resolve_package_version,
    _version_from_dynamic,
    _version_from_toml,
)
from tests._helpers import write_pkg

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
        write_pkg(tmp_path, "dynpkg_read", '__version__ = "3.4.5"\n')
        assert _version_from_dynamic("dynpkg_read", str(tmp_path), str(tmp_path)) == "3.4.5"

    def test_returns_none_when_import_fails(self, tmp_path: Path) -> None:
        """A package that raises on import yields ``None`` instead of propagating the error.

        User packages run arbitrary top-level code; an import that blows up must downgrade auto-detection to
        ``None`` (caller then falls back to metadata) rather than crash the whole ``expiry`` check.

        """
        write_pkg(tmp_path, "dynpkg_boom", 'raise RuntimeError("boom at import time")\n')
        assert _version_from_dynamic("dynpkg_boom", str(tmp_path), str(tmp_path)) is None

    def test_returns_none_when_version_not_a_string(self, tmp_path: Path) -> None:
        """A package whose ``__version__`` is not a ``str`` (or is absent) resolves to ``None``."""
        write_pkg(tmp_path, "dynpkg_badtype", "__version__ = 405\n")
        assert _version_from_dynamic("dynpkg_badtype", str(tmp_path), str(tmp_path)) is None

    def test_restores_sys_path_after_import(self, tmp_path: Path) -> None:
        """``sys.path`` is returned to its original contents even after a successful import.

        The helper prepends several candidate roots to ``sys.path``; leaving them behind would let unrelated later
        imports resolve against a tmp directory. The ``finally`` restore must leave ``sys.path`` byte-for-byte.

        """
        original = list(sys.path)
        write_pkg(tmp_path, "dynpkg_syspath", '__version__ = "9.0.0"\n')
        _version_from_dynamic("dynpkg_syspath", str(tmp_path), str(tmp_path))
        assert sys.path == original


class TestVersionFromToml:
    """Tests for _version_from_toml() — extracts ``[project].version``, falling back to dynamic import."""

    def test_static_version_returned_directly(self, tmp_path: Path) -> None:
        """A literal ``version = "x"`` under ``[project]`` is returned without importing anything."""
        toml = tmp_path / "pyproject.toml"
        toml.write_text('[project]\nname = "mypkg"\nversion = "2.1.0"\n')
        assert _version_from_toml(str(toml), str(tmp_path)) == "2.1.0"

    def test_missing_project_table_is_not_a_project(self, tmp_path: Path) -> None:
        """A pyproject without a ``[project]`` table is reported as no project at all, not as an unresolved version.

        The walk up to the owning ``pyproject.toml`` skips a file that only configures tools but stops at one whose
        ``[project]`` table exists, so the two outcomes must stay distinguishable.

        """
        toml = tmp_path / "pyproject.toml"
        toml.write_text('[build-system]\nrequires = ["setuptools"]\n')
        assert _version_from_toml(str(toml), str(tmp_path)) is _NOT_A_PROJECT

    @pytest.mark.usefixtures("_clean_sys_modules")
    def test_dynamic_version_falls_back_to_import(self, tmp_path: Path) -> None:
        """A ``dynamic = ["version"]`` project resolves the version by importing the named package.

        Build backends often declare the version dynamic and compute it at build time; the auditor reproduces that
        by importing ``[project].name`` and reading ``__version__``.

        """
        write_pkg(tmp_path, "tomldynpkg", '__version__ = "5.6.7"\n')
        toml = tmp_path / "pyproject.toml"
        toml.write_text('[project]\nname = "tomldynpkg"\ndynamic = ["version"]\n')
        assert _version_from_toml(str(toml), str(tmp_path)) == "5.6.7"

    def test_dynamic_without_name_returns_none(self, tmp_path: Path) -> None:
        """A dynamic-version project missing ``[project].name`` cannot be imported and yields ``None``.

        The project table is there, so the answer is an unresolved version (``None``), not the no-project marker.

        """
        toml = tmp_path / "pyproject.toml"
        toml.write_text('[project]\ndynamic = ["version"]\n')
        assert _version_from_toml(str(toml), str(tmp_path)) is None


class TestReadPyprojectVersion:
    """Tests for _read_pyproject_version() — the walk up from the scanned path to the ``pyproject.toml`` owning it."""

    @pytest.mark.usefixtures("_clean_sys_modules")
    @pytest.mark.parametrize("allow_import", [True, False])
    def test_nearest_project_owns_an_unresolved_version(self, tmp_path: Path, allow_import: bool) -> None:
        """A package whose own ``[project]`` declares a version nobody can resolve reads none, not its parent's.

        A package vendored into a larger repository (a monorepo, a docs example tree) carries its own
        ``pyproject.toml`` with ``dynamic = ["version"]``. When that version cannot be read, because the import yields
        no ``__version__`` or the source holds none, continuing to the repository's ``pyproject.toml`` would judge the
        package's removal deadlines against an unrelated project's release number. The same holds in the import and the
        ``--static`` chain.

        """
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "outer"\nversion = "9.9.9"\n')
        inner = tmp_path / "inner"
        inner.mkdir()
        (inner / "pyproject.toml").write_text('[project]\nname = "ownedpkg"\ndynamic = ["version"]\n')
        pkg = write_pkg(inner, "ownedpkg", "")
        version = _read_pyproject_version(
            str(pkg), allow_import=allow_import, module_name="ownedpkg", import_root=inner
        )
        assert version is None

    def test_pyproject_without_project_table_is_skipped(self, tmp_path: Path) -> None:
        """A ``pyproject.toml`` that only configures tools says nothing about the version, so the walk goes on.

        A package keeps its linter settings in its own ``pyproject.toml`` while the version lives in the repository's
        root file one level up; the tool-only file must not end the search.

        """
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "outer"\nversion = "9.9.9"\n')
        inner = tmp_path / "inner"
        inner.mkdir()
        (inner / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
        pkg = write_pkg(inner, "toolonlypkg", "")
        assert _read_pyproject_version(str(pkg)) == "9.9.9"


def _version_failing_for_sub(name: str) -> str:
    """Stand-in ``importlib.metadata.version``: fail for the ``demo-sub`` distribution, answer ``9.9`` otherwise."""
    if name == "demo-sub":
        raise RuntimeError("corrupt metadata")
    return "9.9"


class _FakeDistribution:
    """Stand-in installed distribution; ``listed == "read-error"`` makes reading ``top_level.txt`` fail (fixture)."""

    def __init__(self, listed: str, metadata: Optional[dict[str, str]]) -> None:
        self._listed = listed
        self.metadata = metadata or {}

    def read_text(self, filename: str) -> str:
        """Return the ``top_level.txt`` listing, or fail like an unreadable file."""
        if self._listed == "read-error":
            raise OSError("unreadable")
        return self._listed


class TestMetadataLookupFailures:
    """``importlib.metadata`` failures are swallowed so version resolution degrades instead of crashing the CLI."""

    def test_dotted_name_falls_back_to_top_level_distribution(self) -> None:
        """When the full dotted name has no usable metadata, the top-level package's distribution version is used.

        The lookup for ``demo-sub`` raises (any error, not only ``PackageNotFoundError``); the version of ``demo``
        then answers.

        """
        with patch("deprecate.audit._version.importlib.metadata.version", side_effect=_version_failing_for_sub):
            assert _resolve_package_version("demo.sub") == "9.9"

    def test_every_metadata_lookup_failing_leaves_version_unresolved(self) -> None:
        """If metadata lookups all raise and the module cannot be imported, no version is reported (``None``)."""
        with (
            patch("deprecate.audit._version.importlib.metadata.version", side_effect=RuntimeError("broken")),
            patch("deprecate.audit._version._distribution_for_import", return_value=None),
        ):
            assert _resolve_package_version("no_such_module_for_pydeprecate_tests") is None

    def test_distribution_mapping_failure_resolves_to_none(self) -> None:
        """A ``packages_distributions`` error is swallowed: the import name maps to no distribution."""
        with patch(
            "deprecate.audit._version.importlib.metadata.packages_distributions",
            side_effect=RuntimeError("unreadable site-packages"),
            create=True,
        ):
            assert _distribution_for_import("deprecate") is None

    def test_distribution_enumeration_failure_resolves_to_none(self) -> None:
        """If listing installed distributions raises, the ``top_level.txt`` scan reports no match."""
        with patch("deprecate.audit._version.importlib.metadata.distributions", side_effect=OSError("denied")):
            assert _distribution_from_top_level("demo") is None

    @pytest.mark.parametrize(
        ("dists", "expected"),
        [
            pytest.param([("read-error", None)], None, id="unreadable-top-level-skipped"),
            pytest.param([("demo", {})], None, id="missing-name-skipped"),
            pytest.param([("demo", {"Name": ""})], None, id="empty-name-skipped"),
            pytest.param(
                [("read-error", None), ("demo", {}), ("demo", {"Name": "demo-dist"})], "demo-dist", id="mixed"
            ),
        ],
    )
    def test_broken_distributions_are_skipped(
        self, dists: list[tuple[str, Optional[dict[str, str]]]], expected: Optional[str]
    ) -> None:
        """A distribution whose ``top_level.txt`` cannot be read, or that lacks a usable name, is skipped.

        One damaged install in ``site-packages`` must not hide a later distribution that does provide the package.

        """
        fakes = [_FakeDistribution(listed, metadata) for listed, metadata in dists]
        with patch("deprecate.audit._version.importlib.metadata.distributions", return_value=fakes):
            assert _distribution_from_top_level("demo") == expected


# ---------------------------------------------------------------------------
# _resolve_package_version(allow_import=False) — the ``--static`` chain
# ---------------------------------------------------------------------------

#: An ``__init__`` tail that records any attempt to run it before failing, so a test proves it was never imported.
_SENTINEL_TAIL = "from pathlib import Path\nPath(__file__).with_name('IMPORTED').write_text('x')\nraise RuntimeError\n"
_DYNAMIC_PROJECT = '[project]\nname = "srcpkg"\ndynamic = ["version"]\n'


class TestResolveWithoutImport:
    """``allow_import=False`` reads a version from source and metadata only; the package's code never runs."""

    @pytest.mark.parametrize(
        ("init_head", "extra", "pyproject", "expected"),
        [
            pytest.param("", {}, '[project]\nname = "srcpkg"\nversion = "4.2"\n', "4.2", id="static-pyproject"),
            pytest.param(
                "",
                {"about.py": "__version__ = '3.1'\n"},
                _DYNAMIC_PROJECT + '[tool.setuptools.dynamic]\nversion = { attr = "srcpkg.about.__version__" }\n',
                "3.1",
                id="setuptools-attr",
            ),
            pytest.param(
                "from srcpkg._meta import *\n",
                {"_meta.py": "__all__ = ['__version__']\n__version__ = '2.7'\n"},
                _DYNAMIC_PROJECT,
                "2.7",
                id="dynamic-star-reexport",
            ),
            pytest.param(
                "from ._version import version as __version__\n",
                {"_version.py": "version = '1.9'\n"},
                None,
                "1.9",
                id="aliased-reexport",
            ),
            pytest.param(
                "from ._meta import *\n", {"_meta.py": "__version__ = '0.1'\n"}, None, None, id="star-skips-private"
            ),
            pytest.param(
                "__version__ = '1.0'\nfrom ._meta import *\n",
                {"_meta.py": "__all__ = ['__version__']\n__version__ = compute()\n"},
                None,
                None,
                id="star-nonliteral-clears-earlier-literal",
            ),
            pytest.param(
                "__version__ = '1.0'\nfrom ._meta import *\n",
                {"_meta.py": "__all__ = ['__version__']\nthis is not python\n"},
                None,
                None,
                id="star-unreadable-clears-earlier-literal",
            ),
            pytest.param(
                "__version__ = '1.0'\nfrom ._meta import *\n",
                {"_meta.py": "__all__ = ['x']\n__all__ += ['__version__']\n__version__ = '2.0'\n"},
                None,
                None,
                id="star-rebound-all-clears-earlier-literal",
            ),
            pytest.param(
                "__version__ = '1.0'\nfrom ._meta import *\n",
                {"_meta.py": "__all__ = []\n__all__.append('__version__')\n__version__ = '2.0'\n"},
                None,
                None,
                id="star-mutated-all-clears-earlier-literal",
            ),
            pytest.param(
                "__version__ = '1.0'\nfrom ._m1 import *\n",
                {
                    "_m1.py": "__all__ = ['__version__']\nfrom ._m2 import *\n",
                    "_m2.py": "__all__ = ['__version__']\nfrom ._m3 import *\n",
                    "_m3.py": "__all__ = ['__version__']\nfrom ._m4 import *\n",
                    "_m4.py": "__all__ = ['__version__']\n__version__ = '2.0'\n",
                },
                None,
                None,
                id="star-chain-past-hop-budget-clears-earlier-literal",
            ),
            pytest.param(
                "__version__ = '1.0'\nfrom ._meta import *\n",
                {"_meta.py": "__version__ = '9.9'\n"},
                None,
                "1.0",
                id="star-skipping-private-keeps-earlier-literal",
            ),
            pytest.param(
                "__version__ = '1.0'\nfrom json import *\n",
                {},
                None,
                None,
                id="star-outside-roots-clears-earlier-literal",
            ),
            pytest.param("__version__ = '1.0'\nglobals()['__version__'] = '3.0'\n", {}, None, None, id="globals-write"),
            pytest.param("__version__ = '1.0'\nvars().update(__version__='3.0')\n", {}, None, None, id="vars-update"),
            pytest.param(
                "__version__ = '1.0'\ndef inspect(obj): return vars(obj)\n", {}, None, "1.0", id="unrelated-vars"
            ),
            pytest.param(
                "__version__ = '1.0'\ndef inspect(obj): return obj.modules\n", {}, None, "1.0", id="unrelated-modules"
            ),
            pytest.param(
                "__version__ = '1.0'\nimport sys\nsys.modules[__name__].__version__ = '3.0'\n",
                {},
                None,
                None,
                id="module-attribute-write",
            ),
            pytest.param("", {}, None, None, id="nothing-to-read"),
            pytest.param(
                "__version__ = '0.0.0'\ntry:\n    from ._version import __version__\nexcept ImportError:\n    pass\n",
                {"_version.py": "__version__ = '3.0'\n"},
                None,
                None,
                id="try-rebind-is-unresolved",
            ),
            pytest.param(
                "__version__ = '1.0'\nimport sys\nif sys.platform:\n    __version__ = '3.0'\n",
                {},
                None,
                None,
                id="conditional-rebind-is-unresolved",
            ),
            pytest.param(
                "__version__ = '1.0'\ndef _bump():\n    global __version__\n    __version__ = '3.0'\n",
                {},
                None,
                None,
                id="global-rebind-is-unresolved",
            ),
            pytest.param(
                "try:\n    import json\nexcept ImportError:\n    pass\n__version__ = '1.5'\n",
                {},
                None,
                "1.5",
                id="unrelated-compound-keeps-version",
            ),
        ],
    )
    def test_reads_version_from_source(
        self,
        tmp_path: Path,
        init_head: str,
        extra: dict[str, str],
        pyproject: Optional[str],
        expected: Optional[str],
    ) -> None:
        """Each layout yields its version without the package being imported, or ``None`` when none is spelled.

        A static scan runs where the package's dependencies are missing, so importing it for ``__version__`` would
        fail or run arbitrary code; the source, ``pyproject.toml`` and installed metadata are all it may read. A star
        import binds ``__version__`` only through a literal ``__all__``, as at runtime. A rebinding whose outcome
        depends on runtime (inside ``try`` or ``if``, or through a function's ``global``) leaves the version unresolved
        rather than keeping the placeholder literal before it, which would silently pass every expiry check. A star
        import that does export ``__version__`` rebinds it just the same, so a value the reader cannot read (a
        computed one, a module that is not valid Python, a chain longer than it follows) clears the earlier literal;
        one confirmed not to export it leaves it in place. An uninspectable external module may export it, so its
        star import also leaves the version unresolved.

        """
        pkg = write_pkg(tmp_path, "srcpkg", init_head + _SENTINEL_TAIL)
        for name, source in extra.items():
            (pkg / name).write_text(source)
        if pyproject is not None:
            (tmp_path / "pyproject.toml").write_text(pyproject)
        version = _resolve_package_version("srcpkg", path=str(pkg), allow_import=False, import_root=tmp_path)
        assert (version, (pkg / "IMPORTED").exists()) == (expected, False)

    def test_external_star_export_leaves_version_unresolved(self, tmp_path: Path) -> None:
        """An external star import may replace the package's literal with its own exported version.

        The external module is outside the scan root, so the static reader cannot inspect its export list even
        though it could be available at runtime. Returning the earlier literal would report a stale version.

        """
        scan_root = tmp_path / "scan"
        scan_root.mkdir()
        pkg = write_pkg(scan_root, "srcpkg", "__version__ = '1.0'\nfrom external_meta import *\n")
        (tmp_path / "external_meta.py").write_text("__all__ = ['__version__']\n__version__ = '2.0'\n")
        assert _resolve_package_version("srcpkg", path=str(pkg), allow_import=False, import_root=scan_root) is None

    @pytest.mark.parametrize(
        "attr_module",
        [
            pytest.param("{outside}/m", id="absolute-part"),
            pytest.param("srcpkg..about", id="empty-part"),
            pytest.param("srcpkg/about", id="separator-in-part"),
        ],
    )
    def test_setuptools_attr_must_be_a_dotted_name(self, tmp_path: Path, attr_module: str) -> None:
        """A ``version.attr`` that is not a dotted module path reads no file, whatever file its text would name.

        ``pyproject.toml`` is data a scanned repository controls, yet ``pathlib`` restarts a path at any absolute
        component, so an attr such as ``/home/user/m.__version__`` would read ``m.py`` far outside the package and
        report its ``__version__`` as the package's. Empty and ``/``-bearing parts collapse into a legitimate-looking
        sub-path in the same way. Real setuptools values are dotted identifiers, so anything else is unresolved.

        """
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "m.py").write_text("__version__ = '6.6'\n")
        pkg = write_pkg(tmp_path, "srcpkg", _SENTINEL_TAIL)
        (pkg / "about.py").write_text("__version__ = '4.4'\n")
        attr = attr_module.format(outside=outside.as_posix()) + ".__version__"
        pyproject = _DYNAMIC_PROJECT + f'[tool.setuptools.dynamic]\nversion = {{ attr = "{attr}" }}\n'
        (tmp_path / "pyproject.toml").write_text(pyproject)
        version = _resolve_package_version("srcpkg", path=str(pkg), allow_import=False, import_root=tmp_path)
        assert (version, (pkg / "IMPORTED").exists()) == (None, False)


#: What :func:`_literal_all` reports for an ``__all__`` that source alone does not settle.
_ALL_UNREADABLE = "has an `__all__` that is not one literal list of names"


class TestLiteralAll:
    """Tests for _literal_all() — the one reader of a module's ``__all__`` behind the version and static scans."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param("__all__ = ['a', 'b']\n", {"a", "b"}, id="list"),
            pytest.param("__all__ = ('a',)\n", {"a"}, id="tuple"),
            pytest.param("__all__: list[str] = ['a']\n", {"a"}, id="annotated"),
            pytest.param("__all__ = ['a']\n__all__: list[str]\n", {"a"}, id="annotation-only-is-no-binding"),
            pytest.param("__all__ = []\n", set(), id="empty-list"),
            pytest.param("x = 1\n", None, id="no-all"),
            pytest.param("__all__ = ['a']\n__all__ += ['b']\n", _ALL_UNREADABLE, id="augmented"),
            pytest.param("__all__ = ['a']\n__all__ = ['b']\n", _ALL_UNREADABLE, id="rebound"),
            pytest.param("if x:\n    __all__ = ['a']\n", _ALL_UNREADABLE, id="conditional"),
            pytest.param("__all__ = ['a'] + ['b']\n", _ALL_UNREADABLE, id="computed"),
            pytest.param("__all__ = [*other, 'a']\n", _ALL_UNREADABLE, id="starred"),
            pytest.param("__all__ = ['a', name]\n", _ALL_UNREADABLE, id="non-literal-name"),
            pytest.param("from ._meta import __all__\n", _ALL_UNREADABLE, id="imported"),
            pytest.param("__all__ = []\n__all__.append('a')\n", _ALL_UNREADABLE, id="appended-to"),
            pytest.param("__all__ = ['a']\n__all__.extend(['b'])\n", _ALL_UNREADABLE, id="extended"),
            pytest.param("__all__ = ['a']\n__all__[0] = 'b'\n", _ALL_UNREADABLE, id="item-assigned"),
            pytest.param("__all__ = ['a']\nfor _name in __all__:\n    pass\n", _ALL_UNREADABLE, id="read-by-code"),
            pytest.param(
                "__all__ = ['a']\ndef _fn():\n    __all__.remove('a')\n", _ALL_UNREADABLE, id="mutated-in-function"
            ),
        ],
    )
    def test_reads_all_only_when_one_literal_binds_it(self, source: str, expected: object) -> None:
        """A module's ``__all__`` is the set of names when exactly one literal list binds it, else why it is not read.

        A star import copies the names ``__all__`` lists, so a reader that follows only the first assignment would
        see an export the module's later ``+=`` or rebinding removes, or miss one it adds. Both the static scan and
        the version reader answer "does this star import bind that name" from this one rule: no ``__all__`` is ``None``
        (every public name), one literal list is a set, and anything the source does not settle is the reason. A list
        changed in place (``__all__.append(...)``) is not settled by its one assignment either, so any other use of the
        name makes it unreadable: a read-only use over-reports as a gap, never as a silently wrong export set.

        """
        assert _literal_all(ast.parse(source)) == expected


class TestModuleSourceFile:
    """Tests for _module_source_file() — locating a dotted module's source under the import roots."""

    @pytest.mark.parametrize(
        "module",
        [
            pytest.param("pkg/about", id="separator"),
            pytest.param("pkg..about", id="empty-part"),
            pytest.param("", id="empty-name"),
            pytest.param("{outside}/m", id="absolute-part"),
        ],
    )
    def test_rejects_a_name_that_is_not_dotted_identifiers(self, tmp_path: Path, module: str) -> None:
        """A name with a ``/``, an empty part or an absolute part never leaves the roots to find a file.

        Both the version reader and the static scan hand this function names taken from scanned source and
        ``pyproject.toml``. ``Path.joinpath`` restarts at an absolute part and collapses empty ones, so only a name
        made of identifiers may be looked up; the files below exist and would be returned if the name were joined.

        """
        root = tmp_path / "root"
        root.mkdir()
        (write_pkg(root, "pkg", "") / "about.py").write_text("")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "m.py").write_text("")
        assert _module_source_file(module.format(outside=outside.as_posix()), [root]) is None
