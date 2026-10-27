"""Unit tests for :mod:`deprecate._cli._pkg`: reading the ``[tool.pydeprecate]`` configuration."""

from pathlib import Path

import pytest

from deprecate._cli._pkg import _ConfigReadError, _read_pydeprecate_config


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
