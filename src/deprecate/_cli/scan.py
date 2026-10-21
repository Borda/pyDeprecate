"""Path scanning for the CLI: resolve a file, package or plain directory into deprecation wrappers."""

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

from deprecate._cli.report import _GapKind, _print_gap
from deprecate._pkg import _is_package_dir, _select_child_package
from deprecate.audit import DeprecationWrapperInfo, find_deprecation_wrappers


def _scan_directory(
    path: str, include_members: bool = True, exclude: Optional[Sequence[str]] = None
) -> list[DeprecationWrapperInfo]:
    """Scan a plain directory of top-level Python files.

    Nested Python files in subdirectories are skipped unless they are part of an importable package layout. Plain
    directories do not generally support dotted imports for nested modules, so this function only scans top-level
    modules and warns when deeper files are present.

    """
    abs_path: Path = Path(path).resolve()
    results: list[DeprecationWrapperInfo] = []

    original_argv = sys.argv[:]
    sys.argv = sys.argv[:1]  # hide CLI args from any module-level code (e.g. setup.py)
    try:
        for entry in sorted(abs_path.iterdir(), key=lambda ent: ent.name):
            if not (entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("__")):
                continue
            module_name: str = entry.stem
            try:
                results.extend(
                    find_deprecation_wrappers(
                        module_name, recursive=False, include_members=include_members, exclude=exclude
                    )
                )
            except SystemExit:
                _print_gap(
                    _GapKind.MODULE_NOT_AUDITED, f"{module_name}: module-level code exited (not a library module)"
                )
            except Exception as e:
                _print_gap(_GapKind.MODULE_NOT_AUDITED, f"{module_name}: could not scan: {e!r}")
    finally:
        sys.argv = original_argv

    nested_python_files_found: bool = any(
        not fpy.name.startswith("__") for fpy in abs_path.rglob("*.py") if fpy.parent != abs_path
    )

    if nested_python_files_found:
        _print_gap(
            _GapKind.NESTED_FILES,
            "Skipping nested Python files in plain directory scan. Use an importable package layout with '__init__.py'"
            " files, or scan an importable module/package path instead.",
        )
    return results


def _scan_path(
    path: str, recursive: bool = True, include_members: bool = True, exclude: Optional[Sequence[str]] = None
) -> list[DeprecationWrapperInfo]:
    """Scan a directory or importable module/package name for deprecated wrappers.

    File paths are not accepted because ``find_deprecation_wrappers()`` expects an importable module or package name,
    not a filesystem path. A directory holding two or more packages is ambiguous and raises ``ValueError`` instead of
    scanning one of them or falling back to its top-level modules; a directory holding no package keeps that fallback.

    """
    pth: Path = Path(path)
    if pth.is_dir():
        if _is_package_dir(pth):
            # package dir: resolve importable name from directory stem
            return find_deprecation_wrappers(
                Path(path).resolve().name, recursive=recursive, include_members=include_members, exclude=exclude
            )
        # Flat src-layout or project root: find package in direct children or src/ subdir.
        child_pkg = _select_child_package(path)
        if child_pkg is not None:
            return _scan_path(str(child_pkg), recursive=recursive, include_members=include_members, exclude=exclude)
        return _scan_directory(path, include_members=include_members, exclude=exclude)
    if pth.is_file():
        raise ValueError(
            f"File paths are not supported: {path!r}. Pass an importable module/package name or a directory instead."
        )
    return find_deprecation_wrappers(path, recursive=recursive, include_members=include_members, exclude=exclude)
