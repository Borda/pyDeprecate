"""Shared source fixtures and row helpers for static-reader and CLI tests."""

import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from deprecate._gaps import _FoundGap
from deprecate._static.discovery import _StaticSource
from deprecate._static.rows import _read_static
from deprecate.audit import DeprecationWrapperInfo

_ROOT = Path(__file__).resolve().parents[3]


_PKG = "staticprobe"


_NAMESPACE = "staticnamespaceprobe"


def _write(root: Path, files: dict[str, str]) -> Path:
    """Write *files* (``relative path -> source``) under *root* and return the package directory it fills."""
    for relative, source in files.items():
        path = root / _PKG / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source))
    return root / _PKG


def _read_package(
    pkg: Path, *, recursive: bool = True, exclude: tuple[str, ...] = ()
) -> tuple[list[DeprecationWrapperInfo], list[_FoundGap]]:
    """Read wrappers and ordered gaps of the package directory *pkg*."""
    source = _StaticSource("package", pkg.name, pkg, pkg.parent)
    return _read_static(source, recursive=recursive, include_members=True, exclude=list(exclude))


def _read_module(name: str) -> tuple[list[DeprecationWrapperInfo], list[_FoundGap]]:
    """Read wrappers and ordered gaps of one module of this repository's test tree."""
    path = _ROOT.joinpath(*name.split(".")).with_suffix(".py")
    return _read_static(_StaticSource("module", name, path, _ROOT), recursive=False, include_members=True, exclude=[])


def _line_of(module: str, text: str) -> int:
    """Return the 1-based number of the first line of *module*'s source that contains *text*."""
    lines = _ROOT.joinpath(*module.split(".")).with_suffix(".py").read_text().splitlines()
    return next(number for number, line in enumerate(lines, start=1) if text in line)


def _write_namespace_portion(root: Path, module: str) -> None:
    """Write one directory of the :data:`_NAMESPACE` package under *root*, holding *module* with one expired wrapper."""
    pkg_dir = root / _NAMESPACE
    pkg_dir.mkdir(parents=True)
    (pkg_dir / f"{module}.py").write_text(
        "from deprecate import deprecated\n\n@deprecated(deprecated_in='1.0', remove_in='2.0')\ndef old():\n    pass\n"
    )


def _refusing(lister: Callable[..., Any], blocked: Path, error: Optional[PermissionError] = None) -> Callable[..., Any]:
    """Refuse listing *blocked*, optionally using a fixed error without host-specific filename formatting."""

    def _list(path: Any, *args: Any) -> Any:  # noqa: ANN401
        if Path(path) == blocked:
            if error is not None:
                raise error
            raise PermissionError(13, "Permission denied", str(path))
        return lister(path, *args)

    return _list


def _rows(wrappers: list[DeprecationWrapperInfo]) -> list[str]:
    """Return ``module:function`` for each wrapper, in order."""
    return [f"{info.module}:{info.function}" for info in wrappers]


@pytest.fixture
def probe_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Path]]:
    """Write a throwaway package on an import path, forgetting every module it imported at teardown."""
    monkeypatch.syspath_prepend(str(tmp_path))
    yield lambda files: _write(tmp_path, files)
    for name in [name for name in sys.modules if name == _PKG or name.startswith(f"{_PKG}.")]:
        del sys.modules[name]


_DEP = "from deprecate import deprecated\n"


_EXPIRED = "deprecated(deprecated_in='1.0', remove_in='2.0')"
