"""Locate the source a static run reads: a package directory, a namespace package or a single module file."""

import importlib
import importlib.util
import inspect
import os
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import NamedTuple, Optional, Union

from deprecate._cli.report import _GapKind, _print_gap
from deprecate._cli.static.model import _display
from deprecate.audit._scan import _is_excluded


class _StaticSource(NamedTuple):
    """What a ``--static`` path names: a package, a plain directory or a single module file."""

    kind: str  # "package", "plain" or "module"
    name: str
    location: Path
    import_root: Optional[Path]
    #: Every directory of a namespace package, in import-path order; empty for any other source.
    portions: tuple[Path, ...] = ()


def _locate_static(path: str, package_dir: Optional[Path]) -> Union[_StaticSource, str]:
    """Return what *path* names for a ``--static`` read, or the usage error to print (the CLI exits 2).

    A module name is located with :func:`importlib.util.find_spec`, which runs the import system's finders but never the
    package itself; a dotted name is refused, since locating it would import its parent packages.

    """
    if package_dir is not None:
        return _StaticSource("package", package_dir.name, package_dir, package_dir.parent)
    pth = Path(path)
    if pth.is_dir():
        return _StaticSource("plain", path, pth.resolve(), None)
    if "." in path:
        return (
            f"`--static` takes a package directory, a plain directory or a top-level module name; locating {path!r}"
            " would import its parent packages. Pass its directory instead."
        )
    try:
        spec = importlib.util.find_spec(path)
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        return f"No such path or importable module: {path!r}"
    if spec.submodule_search_locations:
        portions = tuple(Path(location) for location in spec.submodule_search_locations)
        # a namespace package has no ``__init__`` (no origin) and may span several directories, each one walked
        namespace = portions if spec.origin is None else ()
        return _StaticSource("package", path, portions[0], portions[0].parent, namespace)
    if spec.origin and spec.origin.endswith(".py"):
        return _StaticSource("module", path, Path(spec.origin), Path(spec.origin).parent)
    return f"{path!r} has no Python source for `--static` to read."


def _package_entries(pkg_dir: Path, package: str) -> Iterator[tuple[str, bool]]:
    """Yield ``(name, is_package)`` for the submodules of a package directory, as :func:`pkgutil.iter_modules` does.

    A directory that cannot be listed is a ``module not audited`` gap rather than a package with no submodules. Its
    detail opens with the module names left out, as the import scan's does: ``<package>.*`` for the submodules of
    *package*, ``<package>.<dir>`` for a subdirectory that may hold a package.

    """
    try:
        filenames = sorted(entry.name for entry in os.scandir(pkg_dir))
    except OSError as err:
        _print_gap(_GapKind.MODULE_NOT_AUDITED, f"{package}.*: submodules of {_display(pkg_dir)} not listed: {err}")
        return
    yielded: set[str] = set()
    for filename in filenames:
        name = inspect.getmodulename(filename)
        if name == "__init__" or name in yielded:
            continue
        is_package = False
        if not name and (pkg_dir / filename).is_dir() and "." not in filename:
            try:
                is_package = any(inspect.getmodulename(sub) == "__init__" for sub in os.listdir(pkg_dir / filename))
            except OSError as err:
                sub_dir = _display(pkg_dir / filename)
                _print_gap(
                    _GapKind.MODULE_NOT_AUDITED, f"{package}.{filename}: submodules of {sub_dir} not listed: {err}"
                )
                continue
            if not is_package:
                continue
            name = filename
        if name and "." not in name:
            yielded.add(name)
            yield name, is_package


def _walk_package(
    pkg_dir: Path, name: str, *, recursive: bool, exclude: Sequence[str], top: bool = True
) -> Iterator[tuple[str, Optional[Path]]]:
    """Yield ``(module name, source file or None)`` for a package and, when *recursive*, its submodules depth-first."""
    if top:
        yield name, pkg_dir / "__init__.py" if (pkg_dir / "__init__.py").is_file() else None
    if not recursive:
        return
    for sub, is_package in _package_entries(pkg_dir, name):
        modname = f"{name}.{sub}"
        if _is_excluded(modname, exclude):
            continue
        if is_package:
            init = pkg_dir / sub / "__init__.py"
            yield modname, init if init.is_file() else None
            yield from _walk_package(pkg_dir / sub, modname, recursive=True, exclude=exclude, top=False)
        else:
            source = pkg_dir / f"{sub}.py"
            yield modname, source if source.is_file() else None


def _walk_namespace(
    portions: Sequence[Path], name: str, *, recursive: bool, exclude: Sequence[str]
) -> Iterator[tuple[str, Optional[Path]]]:
    """Yield the submodules of a namespace package across its directories, as the import scan walks ``__path__``.

    The package has no ``__init__`` of its own, so its root is neither read nor a gap; a submodule found in more than
    one directory is taken from the first, as importing it would.

    """
    claimed: set[str] = set()
    for portion in portions:
        found = list(_walk_package(portion, name, recursive=recursive, exclude=exclude, top=False))
        children = {module[len(name) + 1 :].split(".")[0] for module, _ in found}
        yield from (entry for entry in found if entry[0][len(name) + 1 :].split(".")[0] not in claimed)
        claimed |= children


def _plain_modules(directory: Path, exclude: Sequence[str]) -> Iterator[tuple[str, Optional[Path]]]:
    """Yield the top-level ``*.py`` modules of a plain directory, as the import scan of one does."""
    for entry in sorted(directory.iterdir(), key=lambda ent: ent.name):
        is_module = entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("__")
        if is_module and not _is_excluded(entry.stem, exclude):
            yield entry.stem, entry
    if any(not fpy.name.startswith("__") for fpy in directory.rglob("*.py") if fpy.parent != directory):
        _print_gap(
            _GapKind.NESTED_FILES,
            "Skipping nested Python files in plain directory scan. Use an importable package layout with '__init__.py'"
            " files, or scan an importable module/package path instead.",
        )
