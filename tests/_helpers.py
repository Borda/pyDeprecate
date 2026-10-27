"""Shared helpers for test modules in more than one test package (``unittests/*`` and ``integration``)."""

import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def captured_warnings() -> Iterator[list[warnings.WarningMessage]]:
    """Record every warning raised inside the block, whatever the active filters and the once-per-location registry.

    Equivalent to ``warnings.catch_warnings(record=True)`` followed by ``warnings.simplefilter("always")``: use it when
    a test asserts on the exact count, order or absence of warnings; when any matching warning suffices, use
    ``pytest.warns`` instead.

    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        yield caught


def write_pkg(root: Path, name: str, init_body: str) -> Path:
    """Create an importable ``name`` package under *root* with *init_body* as its ``__init__.py`` and return it."""
    pkg = root / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(init_body)
    return pkg
