"""Path scanning for the CLI: resolve a package, plain directory or module name into deprecation wrappers.

:func:`_scan_for_command` is the one scan prologue every subcommand runs, so a path is accepted, refused, imported and
reported the same way whichever gate scans it.

"""

import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from deprecate._cli._pkg import _safe_module_name, _select_child_package
from deprecate._cli.config import _Config, _ConfigFlag, _resolve_exclude, _source_label
from deprecate._cli.report import _GapKind, _print, _print_gap, _record_scan, _ScanRecord
from deprecate.audit import DeprecationWrapperInfo, find_deprecation_wrappers
from deprecate.audit._scan import _is_excluded
from deprecate.audit._version import _is_package_dir, _managed_sys_path, _resolve_package_version


class _ScanError(Exception):
    """The scanned package itself could not be imported, so nothing was audited; the CLI exits 3."""


def _names_missing_module(error: Exception, path: str) -> bool:
    """Tell whether *error* says that *path*, a module name and no existing path, names no importable module.

    Only a :exc:`ModuleNotFoundError` for the named module or one of its parent packages counts; one raised for a
    dependency the scanned package imports is a failed import of an existing package.

    Examples:
        >>> _names_missing_module(ModuleNotFoundError("x", name="pkg"), "pkg.sub")
        True
        >>> _names_missing_module(ModuleNotFoundError("x", name="numpy"), "pkg")
        False

    """
    if Path(path).exists() or not isinstance(error, ModuleNotFoundError) or error.name is None:
        return False
    return path == error.name or path.startswith(f"{error.name}.")


def _is_module_name(path: str) -> bool:
    """Tell whether *path* is spelled as an absolute dotted module name, the only kind a scan can import.

    Examples:
        >>> _is_module_name("pkg.sub"), _is_module_name("./pkg"), _is_module_name(".pkg")
        (True, False, False)

    """
    return all(part.isidentifier() for part in path.split("."))


@contextmanager
def _root_exit_as_scan_error(path: str) -> Iterator[None]:
    """Turn a ``sys.exit()`` from the scanned package's module-level code into a :class:`_ScanError` (exit 3).

    Both the version auto-detection and the scan import the package; neither may let the package's own exit code become
    the CLI's, which for ``sys.exit(0)`` would pass a gate that audited nothing.

    """
    try:
        yield
    except SystemExit as err:
        # as `_scan_directory` reports per module; a submodule's exit is a gap inside the scan, never this
        raise _ScanError(f"importing {path!r} ran module-level code that exited ({err.code!r})") from err


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

    for entry in sorted(abs_path.iterdir(), key=lambda ent: ent.name):
        if not (entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("__")):
            continue
        module_name: str = entry.stem
        if _is_excluded(module_name, exclude or []):
            continue
        try:
            results.extend(
                find_deprecation_wrappers(
                    module_name, recursive=False, include_members=include_members, exclude=exclude
                )
            )
            # A cached import can resolve json.py or os.py to the CLI's own standard-library module.
            loaded_from = _foreign_copy(entry)
            if loaded_from is not None:
                _print_gap(
                    _GapKind.OTHER_COPY,
                    f"`{module_name}` was imported from {loaded_from}, not from {entry}; the scan audited that copy.",
                )
        except SystemExit:
            _print_gap(_GapKind.MODULE_NOT_AUDITED, f"{module_name}: module-level code exited (not a library module)")
        except Exception as e:
            _print_gap(_GapKind.MODULE_NOT_AUDITED, f"{module_name}: could not scan: {e!r}")

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


def _target_package_dir(path: str) -> Optional[Path]:
    """Return the package directory *path* names, or ``None`` for a plain directory or an importable module name.

    Raises:
        ValueError: If *path* is a file, or a directory holding two or more packages — both usage errors.

    """
    pth = Path(path)
    if pth.is_file():
        raise ValueError(
            f"File paths are not supported: {path!r}. Pass an importable module/package name or a directory instead."
        )
    if not pth.is_dir():
        return None
    if _is_package_dir(pth):
        return pth.resolve()
    child_pkg = _select_child_package(path)
    return child_pkg.resolve() if child_pkg is not None else None


def _module_locations(module: object) -> list[Path]:
    """Return the directories a module was loaded from: its file's directory, else its ``__path__`` entries."""
    module_file = getattr(module, "__file__", None)
    if module_file:
        return [Path(module_file).resolve().parent]
    return [Path(entry).resolve() for entry in getattr(module, "__path__", [])]


def _foreign_copy(source: Path) -> Optional[str]:
    """Return where an imported package or plain module lives when it differs from the requested source.

    Importing by name hands back whatever ``sys.modules`` holds — the CLI's own ``deprecate`` or standard-library
    modules, or a copy an editable install's finder resolved ahead of ``sys.path``. Packages compare their directory;
    plain modules compare their exact file, since a different file in the same directory is also the wrong source.

    Returns:
        The loaded package directory or module file, or ``None`` when no conflicting import is found.

    """
    is_module = source.is_file()
    module = sys.modules.get(source.stem if is_module else source.name)
    if module is None:
        return None
    if is_module:
        module_file = getattr(module, "__file__", None)
        if module_file:
            loaded_file = Path(module_file).resolve()
            return str(loaded_file) if loaded_file != source.resolve() else None
        # Namespace packages expose directories rather than a source file or spec origin.
        locations = _module_locations(module)
        if locations:
            return str(locations[0])
        # Built-in and frozen modules can take a plain module's name without exposing a source file.
        return getattr(getattr(module, "__spec__", None), "origin", None)
    locations = _module_locations(module)
    if not locations or source.resolve() in locations:
        return None
    return str(locations[0])


def _imported_location(path: str, package_dir: Optional[Path]) -> Optional[str]:
    """Return the file the scanned package or module was imported from, or ``None`` for a plain-directory scan."""
    if package_dir is not None:
        module = sys.modules.get(package_dir.name)
    elif Path(path).is_dir():
        return None
    else:
        module = sys.modules.get(path)
    return getattr(module, "__file__", None) if module is not None else None


@contextmanager
def _hidden_argv() -> Iterator[None]:
    """Hide the CLI's own arguments from module-level code that the scan imports, restoring them afterwards."""
    original_argv = sys.argv[:]
    sys.argv = sys.argv[:1]
    try:
        yield
    finally:
        sys.argv = original_argv


def _print_scan_header(
    path: str,
    version: Optional[str] = None,
    *,
    user_provided: bool = False,
    exclude: Optional[tuple[list[str], str]] = None,
) -> None:
    """Print a consistent scan header: scanning location, package name, version, and any exclusion patterns."""
    module = _safe_module_name(path)
    _print(f"Scanning: {path}")
    if version is not None:
        source = "user-provided" if user_provided else "auto-detected"
        _print(f"Package: {module}  Version: {version} ({source})")
    else:
        _print(f"Package: {module}")
    if exclude is not None and exclude[0]:
        _print(f"Exclude: {', '.join(exclude[0])} ({_source_label(exclude[1])})")


def _scan_for_command(
    path: str,
    *,
    recursive: bool,
    exclude: _ConfigFlag,
    config: Optional[_Config] = None,
    include_members: bool = True,
    version: Optional[str] = None,
    version_explicit: bool = False,
    resolve_version: bool = False,
    record: bool = True,
) -> Optional[_ScanRecord]:
    """Run the scan prologue shared by every subcommand and return what it scanned.

    In order: refuse a file path or an ambiguous multi-package directory, resolve ``exclude`` (flag >
    ``pyproject.toml``), auto-detect the version when asked with CLI arguments hidden from imported code, print the
    header, scan with ``sys.path`` pointed at the import root and the CLI's arguments hidden, name the imported file
    (a gap when that is another copy than the scanned directory), and record the scan for ``--report-json`` (unless
    *record* is off).

    Args:
        path: Package directory, plain directory, or importable module name, as the user passed it.
        recursive: Scan submodules recursively.
        exclude: The ``--exclude`` flag value, or the sentinel that defers to ``pyproject.toml``.
        config: Already-loaded ``[tool.pydeprecate]`` configuration, so a caller that read it does not re-read it.
        include_members: Also scan deprecated class members.
        version: Version for the header and the record; with *resolve_version*, ``None`` is auto-detected.
        version_explicit: Whether *version* was typed by the user (header label only).
        resolve_version: Auto-detect a missing *version* — for the subcommands that compare against one.
        record: Keep the scan for ``--report-json``. ``all`` and ``status`` turn it off: they record the table they
            render instead, so a table that failed to render is not rebuilt from the scan for the report.

    Returns:
        The recorded scan, or ``None`` after printing a usage error (the caller exits 2).

    """
    try:
        package_dir = _target_package_dir(path)
    except ValueError as err:
        _print(str(err), stderr=True)
        return None
    resolved_exclude = _resolve_exclude(path, exclude, config)
    if resolved_exclude is None:
        return None
    if package_dir is None and not Path(path).exists() and not _is_module_name(path):
        # `./typo` or `.pkg` is no path and no module name; importing it would fail as a relative import
        _print(f"No such path or importable module: {path!r}", stderr=True)
        return None
    if version is None and resolve_version:
        # Dynamic project versions and the __version__ fallback import code before the wrapper scan does.
        with _hidden_argv(), _root_exit_as_scan_error(path):
            version = _resolve_package_version(_safe_module_name(path), path=path)
    _print_scan_header(path, version, user_provided=version_explicit, exclude=resolved_exclude)
    with _managed_sys_path(path), _hidden_argv(), _root_exit_as_scan_error(path):
        try:
            wrappers = _scan_path(
                path, recursive=recursive, include_members=include_members, exclude=resolved_exclude[0]
            )
        except Exception as err:
            if _names_missing_module(err, path):
                # a typo in the argument, not a package that failed to import
                _print(f"No such path or importable module: {path!r}", stderr=True)
                return None
            # submodule failures are gaps inside the scan; only the package itself failing to import reaches here
            raise _ScanError(f"could not import {path!r}: {type(err).__name__}: {err}") from err
    location = _imported_location(path, package_dir)
    if location is not None:
        _print(f"Imported from: {location}")
    # Checked after importing: an editable install's finder can resolve the import name ahead of ``sys.path``.
    loaded_from = _foreign_copy(package_dir) if package_dir is not None else None
    if package_dir is not None and loaded_from is not None:
        _print_gap(
            _GapKind.OTHER_COPY,
            f"`{package_dir.name}` was imported from {loaded_from}, not from {package_dir};"
            " the scan audited that copy.",
        )
    scan = _ScanRecord(path, wrappers, version)
    if record:
        _record_scan(scan)
    return scan
