"""Unit tests for the presentation-free audit gaps in :mod:`deprecate._gaps`."""

from pathlib import Path

from deprecate._gaps import _FoundGap, _GapKind, _nested_files_gap


def test_flat_directory_has_no_gap(tmp_path: Path) -> None:
    """A flat directory has no nested-file gap.

    A project keeps its scripts directly in one directory. Every Python file is included by a plain-directory scan,
    so discovery has no omission to report.
    """
    (tmp_path / "script.py").touch()
    assert _nested_files_gap(tmp_path) is None


def test_nested_file_has_gap(tmp_path: Path) -> None:
    """A nested script produces the shared plain-directory omission message.

    A project adds a helper below its scripts directory. A plain-directory scan will omit it, so discovery returns
    a gap that either an API caller or the CLI can consume.
    """
    (tmp_path / "helpers").mkdir()
    (tmp_path / "helpers" / "script.py").touch()
    assert _nested_files_gap(tmp_path) == _FoundGap(
        _GapKind.NESTED_FILES,
        "Skipping nested Python files in plain directory scan. Use an importable package layout with '__init__.py'"
        " files, or scan an importable module/package path instead.",
    )
