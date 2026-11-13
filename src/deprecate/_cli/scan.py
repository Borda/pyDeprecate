"""Path scanning for the CLI: resolve a package, plain directory or module name into deprecation wrappers.

:func:`_preflight_targets` checks every path of a run before anything is imported, :func:`_select_plans` drops the paths
another path's scan already audits once every path is planned, and :func:`_scan_for_command` is the one scan prologue
every subcommand runs per path, so a path is accepted, refused, imported and reported the same way whichever gate scans
it.

"""

import importlib.util
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from importlib.machinery import ModuleSpec, all_suffixes
from pathlib import Path
from typing import NamedTuple, Optional, Protocol, TypeVar

from deprecate._cli._pkg import _safe_module_name, _select_child_package
from deprecate._cli.config import _Config, _ConfigFlag, _resolve_exclude, _source_label
from deprecate._cli.report import (
    _RUN_RECORD,
    _GapKind,
    _print,
    _print_gap,
    _print_skipped_modules,
    _record_options,
    _record_scan,
    _record_scanned_path,
    _ScanRecord,
)
from deprecate._cli.static.discovery import _locate_static
from deprecate._cli.static.rows import _read_static
from deprecate.audit import DeprecationWrapperInfo, find_deprecation_wrappers
from deprecate.audit._scan import _collect_skipped_modules, _is_excluded
from deprecate.audit._version import (
    _is_package_dir,
    _managed_sys_path,
    _own_distribution,
    _project_file,
    _resolve_package_version,
)


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


def _top_level_spec(name: str) -> Optional[ModuleSpec]:
    """Return the import spec of the top-level package of the dotted *name*, without importing anything.

    Only the first segment is looked up: :func:`importlib.util.find_spec` of a dotted name imports its parent packages,
    which would run the scanned package's code before the preflight of a run finished.

    """
    top = name.split(".")[0]
    if not top.isidentifier():
        return None
    try:
        return importlib.util.find_spec(top)
    except (ImportError, ValueError):  # a broken finder, or a half-initialised module in `sys.modules`
        return None


def _top_level_findable(name: str) -> bool:
    """Tell whether the top-level package of the dotted *name* can be imported, without importing it.

    Examples:
        >>> _top_level_findable("json.decoder"), _top_level_findable("no_such_module_xyz"), _top_level_findable("2.0")
        (True, False, False)

    """
    return _top_level_spec(name) is not None


def _spec_location(spec: Optional[ModuleSpec]) -> Optional[Path]:
    """Return where an import spec loads from: a package's first directory, a module's file, ``None`` for a built-in."""
    if spec is None:
        return None
    if spec.submodule_search_locations:
        return Path(next(iter(spec.submodule_search_locations))).resolve()
    if spec.origin and spec.has_location:
        return Path(spec.origin).resolve()
    return None


class _ScanTarget(NamedTuple):
    """One path of a run after the preflight: what it imports as and where that name resolves."""

    path: str
    #: The top-level name the scan binds in ``sys.modules``; ``None`` for a plain directory.
    import_name: Optional[str]
    #: The dotted module the scan starts from; ``None`` for a plain directory.
    module: Optional[str]
    #: The package directory, the module file or the plain directory; ``None`` for a built-in module.
    location: Optional[Path]


def _check_target(path: str) -> Optional[_ScanTarget]:
    """Check one path of a run before anything is imported and describe what its scan would import.

    Returns:
        The target, or ``None`` after printing a usage error (the caller exits 2): a file path, a directory holding
        several packages, or a name that is neither an existing path nor a findable module.

    """
    try:
        package_dir = _target_package_dir(path)
    except ValueError as err:
        _print(str(err), stderr=True)
        return None
    if package_dir is not None:
        return _ScanTarget(path, package_dir.name, package_dir.name, package_dir)
    if Path(path).exists():
        return _ScanTarget(path, None, None, Path(path).resolve())
    if not _is_module_name(path) or not _top_level_findable(path):
        # `./typo` or `.pkg` is no path and no module name; importing it would fail as a relative import
        _print(f"No such path or importable module: {path!r}", stderr=True)
        return None
    top = path.split(".")[0]
    return _ScanTarget(path, top, path, _spec_location(_top_level_spec(top)))


class _Planned(Protocol):
    """What the coverage and import-name checks of a run read from one path's plan (the CLI's ``_PathPlan``)."""

    @property
    def target(self) -> _ScanTarget:
        """The checked path."""

    @property
    def exclude(self) -> tuple[str, ...]:
        """The path's resolved module-exclusion patterns."""

    @property
    def config(self) -> _Config:
        """The path's ``[tool.pydeprecate]`` settings and the ``pyproject.toml`` they came from."""


def _module_source(directory: Path, name: str) -> Optional[Path]:
    """Return the regular package directory or the module file *name* names inside *directory*, if there is one.

    Judged from the filesystem alone: a directory counts only with an ``__init__.py`` (a regular package), a file with
    any suffix the import system loads (source, bytecode or extension module).

    """
    package = directory / name
    if _is_package_dir(package):
        return package
    return next((directory / f"{name}{suf}" for suf in all_suffixes() if (directory / f"{name}{suf}").is_file()), None)


def _walk_reaches(package_dir: Path, parts: Sequence[str]) -> bool:
    """Tell whether a recursive scan of the package in *package_dir* imports its submodule at the relative *parts*.

    Judged from the filesystem alone, so nothing is imported. The scan's walk (:func:`pkgutil.iter_modules`) enters
    only package directories, so every part but the last must name one (a directory with ``__init__.py``), and the last
    part a package directory or a module file. A directory without ``__init__.py`` on the way, a name that does not
    exist or is no identifier, and no parts at all are not reached.

    Examples:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     pkg = Path(tmp)
        ...     (pkg / "data" / "sub").mkdir(parents=True)
        ...     _ = (pkg / "data" / "sub" / "__init__.py").write_text("")
        ...     _walk_reaches(pkg, ["data", "sub"]), _walk_reaches(pkg / "data", ["sub"])
        (False, True)

    """
    location = package_dir
    for part in parts:
        source = _module_source(location, part) if location.is_dir() and part.isidentifier() else None
        if source is None:
            return False
        location = source
    return bool(parts)


def _walk_route(scan: _ScanTarget, other: _ScanTarget) -> Optional[tuple[Path, list[str]]]:
    """Return where the recursive scan of *scan* would have to walk to import *other*, if *other* lies inside it.

    The route is the directory of *scan*'s top-level package and the dotted name of *other* under it, split into its
    parts: by name (``pkg`` holds ``pkg.sub``) or by directory (the package directory ``src/pkg`` holds
    ``src/pkg/sub``, named ``pkg.sub``).

    """
    if scan.import_name is None or scan.module is None or other.module is None or scan.location is None:
        return None
    if scan.import_name == other.import_name and scan.location == other.location:
        inside = other.module.startswith(f"{scan.module}.")
        return (scan.location, other.module.split(".")) if inside else None
    whole_package = scan.module == scan.import_name and scan.location.is_dir()
    if not whole_package or other.location is None or scan.location not in other.location.parents:
        return None
    return scan.location, [scan.import_name, *other.location.relative_to(scan.location).parts]


def _covers(scan: _Planned, other: _Planned, *, recursive: bool) -> bool:
    """Tell whether the scan of *scan* already audits everything the scan of *other* would.

    That holds for the same target spelled twice, and with *recursive* for a subpackage that the recursive walk of
    *scan*'s package imports: every directory between them is a package (:func:`_walk_reaches`) and *scan*'s ``exclude``
    does not leave the subpackage out. A subpackage under a directory without ``__init__.py``, a submodule that does not
    exist, or one *scan* excludes is not covered, so it keeps its own scan (and its own usage error).

    """
    first, second = scan.target, other.target
    if (first.module, first.location) == (second.module, second.location):
        return True
    route = _walk_route(first, second) if recursive else None
    if route is None:
        return False
    package_dir, parts = route
    return _walk_reaches(package_dir, parts[1:]) and not _is_excluded(".".join(parts), scan.exclude)


def _preflight_targets(paths: Sequence[str]) -> Optional[list[_ScanTarget]]:
    """Check every path of a run before the first import and describe what each one's scan would import.

    Returns:
        The targets, in path order, or ``None`` after printing a usage error (the caller exits 2).

    """
    targets = []
    for path in paths:
        target = _check_target(path)
        if target is None:
            return None
        targets.append(target)
    return targets


#: A path plan, kept as its own type through :func:`_select_plans`.
_PlanT = TypeVar("_PlanT", bound=_Planned)


def _version_source(target: _ScanTarget) -> Optional[str]:
    """Return what gives *target* an auto-detected version of its own, found without importing anything, if anything.

    An existing path takes its version from the nearest ``pyproject.toml`` with a ``[project]`` table, a dotted module
    name from a distribution named after it (``pkg.component`` from ``pkg-component``). ``None`` when there is neither:
    the version then comes from the same top-level package as that of a path covering it.

    """
    if Path(target.path).exists():
        return _project_file(target.path)
    if target.module is not None and "." in target.module:
        distribution = _own_distribution(target.module)
        return None if distribution is None else f"the distribution {distribution!r}"
    return None


def _own_context(plan: _Planned, cover: _Planned, *, own_versions: bool) -> Optional[str]:
    """Return what *plan*'s own scan would apply that the scan of *cover* drops, or ``None`` when nothing differs.

    A covered path whose own lookup finds no ``[tool.pydeprecate]`` file, or no version source, inherits the covering
    path's, as the covering scan applies its own to it anyway. Only a different file of its own, or with *own_versions*
    (each path judged against its auto-detected version) a version source of its own, would be silently dropped.

    """
    settings = plan.config[1]
    if settings is not None and settings != cover.config[1]:
        return "has its own `[tool.pydeprecate]` settings"
    version_source = _version_source(plan.target) if own_versions else None
    if version_source is not None and version_source != _version_source(cover.target):
        return f"has its own version (from {version_source})"
    return None


def _select_plans(plans: Sequence[_PlanT], *, recursive: bool, own_versions: bool = False) -> Optional[list[_PlanT]]:
    """Drop the paths another path's scan already audits, and refuse two paths that cannot share one process.

    Runs once every path is planned, before the first import, so a covering path's own resolved ``exclude`` decides
    what its scan reaches. The same target given twice, or a package together with a subpackage its recursive walk
    imports (:func:`_covers`), is scanned once, with one stderr note per dropped path; a covered path with settings or,
    with *own_versions*, a version of its own (:func:`_own_context`) is refused, since they would be silently dropped.
    Two paths whose scans bind the same top-level import name from different places (a package name, or the bare name
    of a plain directory's top-level file) cannot share one process: ``sys.modules`` would hand the second scan the
    first copy.

    Returns:
        The plans to scan, in path order, or ``None`` after printing a usage error (the caller exits 2).

    """
    kept = []
    for index, plan in enumerate(plans):
        cover = _covering_plan(plans, index, recursive=recursive)
        own = None if cover is None else _own_context(plan, cover, own_versions=own_versions)
        if cover is None:
            kept.append(plan)
        elif own is not None:
            _print(
                f"{plan.target.path!r} {own}, but the scan of {cover.target.path!r} already audits it with those of"
                " its own path; scan it in a separate run.",
                stderr=True,
            )
            return None
        else:
            _print(
                f"pydeprecate: {plan.target.path!r} is already audited by the scan of {cover.target.path!r};"
                " scanning it once.",
                stderr=True,
            )
    for index, plan in enumerate(kept):
        for other in kept[:index]:
            name = _shared_name(other, plan)
            if name is not None:
                _print(
                    f"{other.target.path} and {plan.target.path} both import as {name}; scan them in separate runs.",
                    stderr=True,
                )
                return None
    return kept


def _covering_plan(plans: Sequence[_PlanT], index: int, *, recursive: bool) -> Optional[_PlanT]:
    """Return another plan whose scan covers ``plans[index]``; of two that cover each other, the first one wins."""
    plan = plans[index]
    for pos, other in enumerate(plans):
        if pos == index or not _covers(other, plan, recursive=recursive):
            continue
        if pos < index or not _covers(plan, other, recursive=recursive):
            return other
    return None


def _bound_names(target: _ScanTarget, exclude: Sequence[str] = ()) -> dict[str, Optional[Path]]:
    """Return the top-level import names a target's scan binds in ``sys.modules``, each with where it loads from.

    A package or module binds its top-level name; a plain directory binds the bare name of each top-level file it scans,
    which leaves out a file whose name matches one of the target's *exclude* patterns, as :func:`_scan_directory` does.

    """
    if target.import_name is not None:
        return {target.import_name: target.location}
    if target.location is None or not target.location.is_dir():
        return {}
    return {
        entry.stem: entry.resolve()
        for entry in target.location.iterdir()
        if entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("__")
        if not _is_excluded(entry.stem, exclude)
    }


def _shared_name(first: _Planned, second: _Planned) -> Optional[str]:
    """Return a top-level import name both paths' scans bind to different places, or ``None`` when they can share a run.

    Each path counts only what its own scan imports by name, so a file its resolved ``exclude`` leaves out takes no
    part.

    """
    first_names = _bound_names(first.target, first.exclude)
    second_names = _bound_names(second.target, second.exclude)
    return next(
        (name for name in sorted(first_names.keys() & second_names.keys()) if first_names[name] != second_names[name]),
        None,
    )


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


def _import_and_scan(
    path: str,
    *,
    recursive: bool,
    include_members: bool,
    exclude: tuple[list[str], str],
    version: Optional[str],
    version_explicit: bool,
    resolve_version: bool,
    record_version: bool = False,
) -> Optional[tuple[list[DeprecationWrapperInfo], Optional[str], Optional[str]]]:
    """Run the importing half of :func:`_scan_for_command`: version auto-detection, the header and the scan.

    With *record_version* and no *resolve_version*, the version is still auto-detected — for the report only, never
    printed in the header — so every subcommand's report names the version its scan ran at.

    Returns:
        The wrappers, the version the subcommand compares against (``None`` unless given or *resolve_version*) and the
        version to record, or ``None`` after printing that *path* names no importable module (the caller exits 2).

    Raises:
        _ScanError: When the scanned package itself cannot be imported, or its module-level code exits.

    """
    recorded = version
    if version is None and (resolve_version or record_version):
        # Dynamic project versions and the __version__ fallback import code before the wrapper scan does, so they run
        # with the same import root as the scan: in a run over several paths, each package resolves its own version.
        with _managed_sys_path(path), _hidden_argv(), _root_exit_as_scan_error(path):
            recorded = _resolve_package_version(_safe_module_name(path), path=path)
        if resolve_version:
            version = recorded
    _print_scan_header(path, version, user_provided=version_explicit, exclude=exclude)
    with _managed_sys_path(path), _hidden_argv(), _root_exit_as_scan_error(path):
        try:
            wrappers = _scan_path(path, recursive=recursive, include_members=include_members, exclude=exclude[0])
        except Exception as err:
            if _names_missing_module(err, path):
                # a typo in the argument, not a package that failed to import
                _print(f"No such path or importable module: {path!r}", stderr=True)
                return None
            # submodule failures are gaps inside the scan; only the package itself failing to import reaches here
            raise _ScanError(f"could not import {path!r}: {type(err).__name__}: {err}") from err
    return wrappers, version, recorded


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
    static: bool = False,
) -> Optional[_ScanRecord]:
    """Run the scan prologue shared by every subcommand and return what it scanned.

    In order: refuse a file path or an ambiguous multi-package directory, resolve ``exclude`` (flag >
    ``pyproject.toml``), auto-detect the version when asked with CLI arguments hidden from imported code, print the
    header, scan with ``sys.path`` pointed at the import root and the CLI's arguments hidden, print one
    ``module not audited`` gap per submodule the detection or the scan skipped, name the imported file (a gap when that
    is another copy than the scanned directory), and record the scan for ``--report-json`` (unless *record* is off).

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
        static: Read the source with ``ast`` instead of importing it (``--static``): nothing from the scanned package is
            imported or executed, the version included (:func:`_static_scan`).

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
    run_record = _RUN_RECORD.get()
    if run_record is not None:
        run_record.current_path = path
    _record_options(recursive=recursive, exclude=resolved_exclude[0])
    run = _static_scan if static else _import_scan
    return run(
        path,
        package_dir,
        recursive=recursive,
        include_members=include_members,
        exclude=resolved_exclude,
        version=version,
        version_explicit=version_explicit,
        resolve_version=resolve_version,
        record=record,
        record_version=run_record is not None and run_record.wants_report,
    )


def _import_scan(
    path: str,
    package_dir: Optional[Path],
    *,
    recursive: bool,
    include_members: bool,
    exclude: tuple[list[str], str],
    version: Optional[str],
    version_explicit: bool,
    resolve_version: bool,
    record: bool,
    record_version: bool,
) -> Optional[_ScanRecord]:
    """Run the importing half of :func:`_scan_for_command`: version, header, scan, imported location, record.

    Returns:
        The recorded scan, or ``None`` after printing a usage error (the caller exits 2).

    """
    # Every submodule the version detection or the scan skips is recorded as a gap of this scan right after it, also
    # when the scan then fails, so a strict verdict computed at the subcommand's return already counts it.
    with _collect_skipped_modules() as skipped_modules:
        try:
            scanned = _import_and_scan(
                path,
                recursive=recursive,
                include_members=include_members,
                exclude=exclude,
                version=version,
                version_explicit=version_explicit,
                resolve_version=resolve_version,
                record_version=record_version,
            )
        finally:
            # Unlike the `audit: skipped` warning, these lines survive the audited package's own warnings filters.
            _print_skipped_modules(skipped_modules)
    if scanned is None:
        return None
    wrappers, version, recorded_version = scanned
    _record_scanned_path(path, recorded_version, user_provided=version_explicit)
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
    scan = _ScanRecord(path, wrappers, recorded_version)
    if record:
        _record_scan(scan)
    return scan


def _static_scan(
    path: str,
    package_dir: Optional[Path],
    *,
    recursive: bool,
    include_members: bool,
    exclude: tuple[list[str], str],
    version: Optional[str],
    version_explicit: bool,
    resolve_version: bool,
    record: bool,
    record_version: bool,
) -> Optional[_ScanRecord]:
    """Run the ``--static`` half of :func:`_scan_for_command`: locate, version and read the source, importing nothing.

    A module name is located without running it, and only a top-level one (a dotted name would import its parents);
    the version comes from ``pyproject.toml``, installed metadata or the source's ``__version__``, never from an import.
    With *record_version* and no *resolve_version*, the version is still detected for the report, never the header.

    Returns:
        The recorded scan, or ``None`` after printing a usage error (the caller exits 2).

    """
    located = _locate_static(path, package_dir)
    if isinstance(located, str):
        _print(located, stderr=True)
        return None
    recorded = version
    if version is None and (resolve_version or record_version):
        existing = path if Path(path).exists() else None
        recorded = _resolve_package_version(
            _safe_module_name(path), path=existing, allow_import=False, import_root=located.import_root
        )
        if resolve_version:
            version = recorded
    _print_scan_header(path, version, user_provided=version_explicit, exclude=exclude)
    _print("Mode: static (source read with ast; nothing imported)")
    wrappers = _read_static(located, recursive=recursive, include_members=include_members, exclude=exclude[0])
    _record_scanned_path(path, recorded, user_provided=version_explicit)
    scan = _ScanRecord(path, wrappers, recorded, static=True, import_root=located.import_root)
    if record:
        _record_scan(scan)
    return scan
