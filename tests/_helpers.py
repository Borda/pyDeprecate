"""Shared helpers for test modules in more than one test package (``unittests/*`` and ``integration``)."""

import os
import subprocess
import sys
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


def run_isolated_python(code: str) -> str:
    """Run *code* in a fresh isolated interpreter (``python -I``) and return its stripped stdout.

    An import-cost probe needs a child whose ``sys.modules`` holds only what the code under test loads. The child gets
    no ``COVERAGE_*`` variables, so under ``--cov`` the ``run.patch = ["subprocess"]`` setting does not start coverage
    in it. That is not enough on coverage 7.10.x, the newest release that installs on Python 3.9: its site ``.pth``
    file imports coverage, and with it ``json``, in every child whatever the environment says. Probes that inspect
    ``sys.modules`` therefore also need a pytest run without ``--cov``, which CI gives ``test_import_cost.py``.

    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("COVERAGE_")}
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True, timeout=60, env=env
    )
    return result.stdout.strip()


def write_pkg(root: Path, name: str, init_body: str) -> Path:
    """Create an importable ``name`` package under *root* with *init_body* as its ``__init__.py`` and return it."""
    pkg = root / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(init_body)
    return pkg
