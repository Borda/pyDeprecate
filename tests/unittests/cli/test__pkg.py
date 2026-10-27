"""Unit tests for :mod:`deprecate._cli._pkg`: reading the ``[tool.pydeprecate]`` configuration."""

import sys
from pathlib import Path
from types import ModuleType

import pytest

from deprecate._cli._pkg import (
    _ConfigReadError,
    _read_pydeprecate_config,
    _resolve_module_name,
    _safe_module_name,
    _select_child_package,
)
from tests._helpers import write_pkg


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


class TestSrcLayoutDiscovery:
    """A project root is resolved to its package, preferring ``src/<pkg>`` over packages next to ``pyproject.toml``."""

    def test_src_layout_root_resolves_to_src_package(self, tmp_path: Path) -> None:
        """``pydeprecate check .`` at a ``src/``-layout project root names the package under ``src/``.

        A maintainer runs the CLI from the repository root, where ``pyproject.toml`` lives and the importable package
        sits one level down in ``src/``; the module name must be the package, not ``src`` or the root directory.

        """
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
        src = tmp_path / "src"
        src.mkdir()
        write_pkg(src, "demo", "")

        assert _resolve_module_name(str(tmp_path)) == "demo"

    def test_src_package_wins_over_root_level_package(self, tmp_path: Path) -> None:
        """A package under ``src/`` takes precedence over a package directory next to it, such as ``tests``.

        The root of a ``src/`` project often also holds a ``tests`` package; auditing that instead of the library (or
        reporting the root as ambiguous) would be wrong, so ``src/`` is searched first.

        """
        src = tmp_path / "src"
        src.mkdir()
        write_pkg(src, "demo", "")
        write_pkg(tmp_path, "tests", "")

        assert _resolve_module_name(str(tmp_path)) == "demo"

    def test_src_without_package_falls_back_to_root_level_package(self, tmp_path: Path) -> None:
        """An empty ``src/`` directory does not hide a flat-layout package at the project root."""
        (tmp_path / "src").mkdir()
        write_pkg(tmp_path, "flatpkg", "")

        assert _resolve_module_name(str(tmp_path)) == "flatpkg"

    def test_several_src_packages_are_ambiguous(self, tmp_path: Path) -> None:
        """Two packages under ``src/`` raise instead of auditing one of them, naming both in sorted order.

        Guessing would let a run pass green while half of the project was never checked.

        """
        src = tmp_path / "src"
        src.mkdir()
        write_pkg(src, "zeta", "")
        write_pkg(src, "alpha", "")

        with pytest.raises(ValueError, match=r"contains multiple packages \(alpha, zeta\)"):
            _select_child_package(str(tmp_path))

    def test_no_package_anywhere_selects_nothing(self, tmp_path: Path) -> None:
        """A directory holding neither ``src/<pkg>`` nor a child package yields ``None``."""
        (tmp_path / "src").mkdir()
        (tmp_path / "docs").mkdir()

        assert _select_child_package(str(tmp_path)) is None


class TestResolveModuleName:
    """``_resolve_module_name`` / ``_safe_module_name`` turn the user's path argument into an importable name."""

    def test_file_path_is_rejected(self, tmp_path: Path) -> None:
        """A ``.py`` file path has no single importable name and is refused with a pointer to the alternatives."""
        module_file = tmp_path / "mod.py"
        module_file.write_text("")

        with pytest.raises(ValueError, match="File paths are not supported"):
            _resolve_module_name(str(module_file))

    def test_plain_directory_without_package_is_rejected(self, tmp_path: Path) -> None:
        """A directory without ``__init__.py`` and without a child package has no importable name."""
        with pytest.raises(ValueError, match="has no importable module name"):
            _resolve_module_name(str(tmp_path))

    def test_package_directory_resolves_to_its_own_name(self, tmp_path: Path) -> None:
        """A directory that is itself a package resolves to its directory name."""
        pkg = write_pkg(tmp_path, "selfpkg", "")

        assert _resolve_module_name(str(pkg)) == "selfpkg"

    def test_importable_name_passes_through(self) -> None:
        """A string naming no filesystem path is taken to be an importable module name and returned unchanged."""
        assert _resolve_module_name("not_a_path.sub_module") == "not_a_path.sub_module"

    def test_safe_module_name_of_file_falls_back_to_the_path(self, tmp_path: Path) -> None:
        """Where resolution raises for a file, the safe variant returns the path itself."""
        module_file = tmp_path / "mod.py"
        module_file.write_text("")

        assert _safe_module_name(str(module_file)) == str(module_file)

    def test_safe_module_name_of_plain_directory_falls_back_to_the_path(self, tmp_path: Path) -> None:
        """Where resolution raises for a plain directory, the safe variant returns the path (scanned per module)."""
        assert _safe_module_name(str(tmp_path)) == str(tmp_path)


class TestTomlParserUnavailable:
    """Without ``tomllib`` and ``tomli`` the configuration reads as absent instead of failing."""

    def test_no_toml_parser_reads_no_configuration(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """With neither parser importable, a declared ``[tool.pydeprecate]`` table is ignored: ``({}, None)``.

        Python 3.9/3.10 without the ``audit`` extra has no TOML parser; the CLI then runs on its built-in defaults
        rather than crashing on the import.

        """
        (tmp_path / "pyproject.toml").write_text('[tool.pydeprecate]\nexclude = ["pkg.tests"]\n')
        monkeypatch.setitem(sys.modules, "tomllib", None)
        monkeypatch.setitem(sys.modules, "tomli", None)

        assert _read_pydeprecate_config(str(tmp_path)) == ({}, None)

    def test_tomli_backport_is_used_when_tomllib_is_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When only the ``tomli`` backport is importable, it parses the file and the table is read from it."""
        backport = ModuleType("tomli")
        table = {"tool": {"pydeprecate": {"exclude": ["from.backport"]}}}
        backport.load = lambda fh: table  # type: ignore[attr-defined]
        toml = tmp_path / "pyproject.toml"
        toml.write_text("")
        monkeypatch.setitem(sys.modules, "tomllib", None)
        monkeypatch.setitem(sys.modules, "tomli", backport)

        assert _read_pydeprecate_config(str(tmp_path)) == ({"exclude": ["from.backport"]}, str(toml))
