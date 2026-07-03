"""Guard against stale locally-generated docs example tests.

The ``test_*.py`` files in this directory are **not** tracked in git — they are generated from the ``docs/*.md``
sources by ``make docs-tests`` (the only supported entry point, which deletes and regenerates them). A checkout
carrying older generated files produces confusing failures: ``ModuleNotFoundError`` for placeholder imports and
assertion drift against reprs that have since changed. This conftest fails collection fast, with a clear
instruction, when any generated test file is older than the Markdown source it was generated from.

It is only loaded when ``tests/docs`` is collected explicitly (``pytest tests/docs/``); the default ``pytest .``
run prunes this directory via ``norecursedirs`` in ``pyproject.toml`` and never triggers the check.

"""

from __future__ import annotations

from pathlib import Path

import pytest

_GEN_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _GEN_DIR.parents[1]
_DOCS_DIR = _REPO_ROOT / "docs"


def _expected_test_name(md: Path) -> str:
    """Map a ``docs/<path>.md`` source to its generated ``test_<slug>.py`` name (mirrors ``make docs-tests``)."""
    slug = md.relative_to(_DOCS_DIR).with_suffix("").as_posix().replace("/", "_").replace("-", "_")
    return f"test_{slug}.py"


def _stale_generated_tests() -> list[str]:
    """Return one description per generated test file older than its Markdown source."""
    stale: list[str] = []
    for md in sorted(_DOCS_DIR.rglob("*.md")):
        generated = _GEN_DIR / _expected_test_name(md)
        if generated.exists() and generated.stat().st_mtime < md.stat().st_mtime:
            stale.append(f"{generated.name} (older than {md.relative_to(_REPO_ROOT)})")
    return stale


_STALE = _stale_generated_tests()
if _STALE:
    raise pytest.UsageError(
        "Stale generated docs example tests detected — regenerate them via `make docs-tests`:\n  " + "\n  ".join(_STALE)
    )
