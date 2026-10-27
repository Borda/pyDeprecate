"""Shared helpers for test modules in more than one test package (``unittests/*`` and ``integration``)."""

from pathlib import Path


def write_pkg(root: Path, name: str, init_body: str) -> Path:
    """Create an importable ``name`` package under *root* with *init_body* as its ``__init__.py`` and return it."""
    pkg = root / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(init_body)
    return pkg
