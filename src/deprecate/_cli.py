"""CLI entry point for pyDeprecate validation.

Provides two entry points for scanning Python code for misconfigured ``@deprecated`` wrappers:

- ``pydeprecate <subcommand> <path>`` — console script installed via ``pip install 'pyDeprecate[cli]'``
- ``python -m deprecate <subcommand> <path>`` — module invocation of the same CLI

Subcommands:
    check   — Validate wrapper configuration and flag misconfigured or chain-forming wrappers.
    expiry  — Check for deprecated wrappers that have passed their scheduled ``remove_in`` deadline.
    chains  — Detect deprecated wrappers whose ``target`` is itself a deprecated callable.
    all     — Run all three checks in a single scan pass.
"""

import functools
import importlib.util
import sys
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from deprecate.audit import (
    DeprecationWrapperInfo,
    _check_expiry_for_callables,
    find_deprecation_wrappers,
    validate_deprecation_chains,
    validate_deprecation_expiry,
)


def _is_package_available(name: str) -> bool:
    """Return True if *name* is importable without actually importing it."""
    return importlib.util.find_spec(name) is not None


def _print(msg: str, *, stderr: bool = False) -> None:
    """Print a message, using Rich console when available.

    Routes output through :class:`~rich.console.Console` when the ``rich``
    package is installed, falling back to built-in :func:`print` otherwise.

    Args:
        msg: The message to print.
        stderr: If ``True``, send the message to *stderr* instead of *stdout*.
    """
    if _Reporter._HAS_RICH:
        _Reporter._console(stderr).print(msg, markup=False, highlight=False)
    else:
        std_ = sys.stderr if stderr else sys.stdout
        print(msg, file=std_)


def _is_package_dir(pth: Path) -> bool:
    return (pth / "__init__.py").exists()


@contextmanager
def _managed_sys_path(path: str) -> Generator[None, None, None]:
    """Context manager that prepends the import root to ``sys.path`` and restores it after scanning.

    For package directories (containing ``__init__.py``), inserts the parent directory so
    the package name resolves as an importable module. For plain directories, inserts the
    directory itself. Importable module name strings are passed through unchanged.
    """
    abs_path: Path = Path(path).resolve()
    original = list(sys.path)
    if abs_path.is_dir():
        import_root = str(abs_path.parent) if _is_package_dir(abs_path) else str(abs_path)
        sys.path.insert(0, import_root)
    try:
        yield
    finally:
        sys.path[:] = original


def _resolve_module_name(path: str) -> str:
    """Convert a filesystem path to an importable module name.

    Accepts a package directory (with ``__init__.py``) or an importable module
    name string. Plain directories and individual ``.py`` files are not supported
    because ``validate_deprecation_expiry`` and ``validate_deprecation_chains``
    require an importable module name, not a filesystem path.

    Args:
        path: Package directory path or importable module name string.

    Returns:
        Importable module name string.

    Raises:
        ValueError: If path is a plain directory without ``__init__.py``, a ``.py``
            file, or cannot be resolved to an importable module name.
    """
    pth = Path(path)
    if pth.is_file():
        raise ValueError(
            f"File paths are not supported: {path!r}. Pass an importable module/package name or a directory instead."
        )
    if pth.is_dir():
        if _is_package_dir(pth):
            return pth.resolve().name
        raise ValueError(
            f"Plain directories without '__init__.py' are not supported for expiry or chain checks: {path!r}. "
            "Use an importable package layout with '__init__.py', or pass an importable module name instead."
        )
    return path


def _scan_directory(path: str) -> list[DeprecationWrapperInfo]:
    """Scan a plain directory of top-level Python files.

    Nested Python files in subdirectories are skipped unless they are part of an
    importable package layout. Plain directories do not generally support dotted
    imports for nested modules, so this function only scans top-level modules and
    warns when deeper files are present.
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
                results.extend(find_deprecation_wrappers(module_name, recursive=False))
            except SystemExit:
                _print(f"Skipping {module_name}: module-level code exited (not a library module)", stderr=True)
            except Exception as e:
                _print(f"Could not scan {module_name}: {e}", stderr=True)
    finally:
        sys.argv = original_argv

    nested_python_files_found: bool = any(
        not fpy.name.startswith("__") for fpy in abs_path.rglob("*.py") if fpy.parent != abs_path
    )

    if nested_python_files_found:
        _print(
            "Skipping nested Python files in plain directory scan. Use an importable package layout with '__init__.py'"
            " files, or scan an importable module/package path instead.",
            stderr=True,
        )
    return results


def _scan_path(path: str, recursive: bool = True) -> list[DeprecationWrapperInfo]:
    """Scan a directory or importable module/package name for deprecated wrappers.

    File paths are not accepted because ``find_deprecation_wrappers()`` expects an
    importable module or package name, not a filesystem path.
    """
    pth: Path = Path(path)
    if pth.is_dir():
        if _is_package_dir(pth):
            # package dir: resolve importable name from directory stem
            return find_deprecation_wrappers(Path(path).resolve().name, recursive=recursive)
        return _scan_directory(path)
    if pth.is_file():
        raise ValueError(
            f"File paths are not supported: {path!r}. Pass an importable module/package name or a directory instead."
        )
    return find_deprecation_wrappers(path, recursive=recursive)


# ---------------------------------------------------------------------------
# Reporter — all rich/plain dispatch in one namespace
# ---------------------------------------------------------------------------


class _Reporter:
    _HAS_RICH: bool = _is_package_available("rich")
    _out: Any = None
    _err: Any = None
    _rich_box: Any = None
    _RichTable: Any = None

    try:
        from rich import box as _rich_box  # type: ignore[no-redef]
        from rich.console import Console as _RichConsole
        from rich.table import Table as _RichTable  # type: ignore[no-redef]

        _out = _RichConsole()
        _err = _RichConsole(stderr=True)
    except ImportError:  # pragma: no cover
        pass

    @staticmethod
    def _console(stderr: bool = False) -> Any:  # noqa: ANN401
        """Return the Rich Console for stdout (default) or stderr."""
        return _Reporter._err if stderr else _Reporter._out

    @staticmethod
    def _make_table(title: str, col: str, *, title_style: str, col_style: str) -> Any:  # noqa: ANN401
        """Build a three-column Module/Function/<col> Rich table."""
        table = _Reporter._RichTable(title=title, box=_Reporter._rich_box.ROUNDED, title_style=title_style)
        table.add_column("Module", style="cyan")
        table.add_column("Function", style="magenta")
        table.add_column(col, style=col_style)
        return table

    @staticmethod
    def _render_table(
        title: str,
        col: str,
        *,
        title_style: str,
        col_style: str,
        rows: list[tuple[str, ...]],
        plain_prefix: str,
    ) -> None:
        """Render a three-column Module/Function/Detail table (rich or plain fallback)."""
        if _Reporter._HAS_RICH:
            table = _Reporter._make_table(title, col, title_style=title_style, col_style=col_style)
            for row in rows:
                table.add_row(*row)
            _Reporter._console().print(table)
        else:
            _print(f"\n{plain_prefix}")
            for mod, fn, detail in rows:
                _print(f"\t- {mod}.{fn}: {detail}")

    @staticmethod
    def invalid_args(items: list[DeprecationWrapperInfo]) -> None:
        """Report wrappers with invalid ``args_mapping`` keys."""
        _Reporter._render_table(
            "Invalid Argument Mappings",
            "Invalid Args",
            title_style="bold red",
            col_style="red",
            rows=[(r.module, r.function, ", ".join(r.invalid_args)) for r in items],
            plain_prefix="[ERROR] Found functions with invalid argument mappings:",
        )

    @staticmethod
    def identity_mappings(items: list[DeprecationWrapperInfo]) -> None:
        """Report wrappers whose ``args_mapping`` maps an argument to itself."""
        _Reporter._render_table(
            "Identity Argument Mappings (arg -> arg)",
            "Identity Args",
            title_style="bold yellow",
            col_style="yellow",
            rows=[(r.module, r.function, ", ".join(r.identity_mapping)) for r in items],
            plain_prefix="[WARNING] Found functions with identity argument mappings (arg -> arg):",
        )

    @staticmethod
    def no_effect(items: list[DeprecationWrapperInfo]) -> None:
        """Report wrappers that have no observable effect on callers."""

        def _reasons(r: DeprecationWrapperInfo) -> str:
            parts = []
            if r.empty_mapping:
                parts.append("Empty mapping")
            if r.self_reference:
                parts.append("Self reference")
            if r.all_identity:
                parts.append("All identity mappings")
            return ", ".join(parts)

        _Reporter._render_table(
            "No-Effect Wrappers (zero impact)",
            "Reason",
            title_style="bold yellow",
            col_style="yellow",
            rows=[(r.module, r.function, _reasons(r)) for r in items],
            plain_prefix="[WARNING] Found deprecated wrappers with NO EFFECT (zero impact):",
        )

    @staticmethod
    def chains(items: list[DeprecationWrapperInfo], *, error: bool = False) -> None:
        """Report deprecated-to-deprecated forwarding chains; ``error=True`` renders in red."""
        style = "bold red" if error else "bold yellow"
        col_style = "red" if error else "yellow"
        prefix = "[ERROR]" if error else "[WARNING]"
        _Reporter._render_table(
            "Deprecation Chains",
            "Chain Type",
            title_style=style,
            col_style=col_style,
            rows=[(r.module, r.function, r.chain_type.value if r.chain_type is not None else "") for r in items],
            plain_prefix=f"{prefix} Found deprecated wrappers forming deprecation chains:",
        )

    @staticmethod
    def expiry(expired: list[str]) -> None:
        """Report deprecated wrappers that have passed their ``remove_in`` deadline."""
        if _Reporter._HAS_RICH:
            table = _Reporter._RichTable(
                title="Expired Deprecated Wrappers", box=_Reporter._rich_box.ROUNDED, title_style="bold red"
            )
            table.add_column("Message", style="red")
            for msg in expired:
                table.add_row(msg)
            _Reporter._console().print(table)
        else:
            _print("\n[ERROR] Found expired deprecated wrappers:")
            for msg in expired:
                _print(f"\t- {msg}")

    @staticmethod
    def issues(results: list[DeprecationWrapperInfo], *, error_on_chains: bool = False) -> bool:
        """Print categorised diagnostics and return whether any issues were found."""
        invalid_args = [r for r in results if r.invalid_args]
        identity_mappings = [r for r in results if r.identity_mapping]
        no_effect = [r for r in results if r.no_effect]
        chains = [r for r in results if r.chain_type is not None]

        if not (invalid_args or identity_mappings or no_effect or chains):
            return False

        if invalid_args:
            _Reporter.invalid_args(invalid_args)
        if identity_mappings:
            _Reporter.identity_mappings(identity_mappings)
        if no_effect:
            _Reporter.no_effect(no_effect)
        if chains:
            _Reporter.chains(chains, error=error_on_chains)

        return True


def _do_expiry(path: str, version: Optional[str], recursive: bool) -> Optional[list[str]]:
    """Run the expiry scan and return expired wrapper messages, or None when packaging is unavailable.

    Caller is responsible for setting up ``sys.path`` via :func:`_managed_sys_path` before calling.

    Args:
        path: Package directory path or importable module name string.
        version: Current package version for comparison, or None to auto-detect.
        recursive: Scan submodules recursively.

    Returns:
        List of expired wrapper message strings (may be empty), or None when the
        ``packaging`` library is unavailable (advisory — warning already printed to stderr).
    """
    module_name = _resolve_module_name(path)
    try:
        return validate_deprecation_expiry(module_name, version, recursive=recursive)
    except ImportError as exc:
        if _is_missing_packaging_import_error(exc):
            _print(
                "The 'expiry' subcommand requires the 'packaging' library.\n"
                "Install it with: `pip install 'pyDeprecate[audit]'`",
                stderr=True,
            )
        else:
            _print(
                "Could not determine the current package version automatically.\n"
                "Pass --version explicitly, or ensure the package is installed and importable.\n\n"
                f"Original error: {exc}",
                stderr=True,
            )
        return None
    # other exceptions propagate to _wrap


def _is_missing_packaging_import_error(error: ImportError) -> bool:
    """Return True when an ImportError was caused by a missing packaging dependency.

    Args:
        error: The ImportError raised while trying to inspect expiry information.

    Returns:
        True if the error indicates that the ``packaging`` library is unavailable.
    """
    for exc in (error, getattr(error, "__cause__", None)):
        if exc is None:
            continue
        name = getattr(exc, "name", None)
        if isinstance(name, str) and (name == "packaging" or name.startswith("packaging.")):
            return True
    return "No module named 'packaging'" in str(error)


# ---------------------------------------------------------------------------
# Version auto-detection helper
# ---------------------------------------------------------------------------


def _auto_detect_version(module_name: str) -> Optional[str]:
    """Return the installed version for *module_name*, or ``None`` on any failure.

    Uses :mod:`importlib.metadata` so no extra dependencies are required.

    Args:
        module_name: Importable package name whose installed version to look up.

    Returns:
        Version string (e.g. ``"1.2.3"``) or ``None`` when the package is not
        installed or the version cannot be determined.
    """
    try:
        import importlib.metadata

        return importlib.metadata.version(module_name)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Subcommand functions
# ---------------------------------------------------------------------------


def cmd_check(
    path: str = ".",
    recursive: bool = True,
    skip_errors: bool = False,
    *,
    _wrappers: Optional[list[DeprecationWrapperInfo]] = None,
) -> int:
    """Scan Python code for misconfigured ``@deprecated`` wrappers and deprecation chains.

    Reports invalid argument mappings (exit 1), identity mappings, no-effect wrappers,
    and deprecation chains (advisory warnings only, exit 0). Use the ``chains``
    subcommand for a dedicated hard-error chain check.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        skip_errors: Always exit 0 even if hard errors (invalid argument mappings) are found.
        _wrappers: Pre-scanned wrapper list. When provided, skips the scan step. Underscore
            prefix hides this parameter from the Fire CLI (internal use by ``cmd_all`` only).

    Returns:
        0 on success or advisory-only issues; 1 when hard errors are found and ``skip_errors`` is False.
    """
    if _wrappers is None:
        _print(f"Scanning path: {path} ...")
        with _managed_sys_path(path):
            _wrappers = _scan_path(path, recursive=recursive)

    if not _wrappers:
        _print("No deprecated callables found.")
        return 0

    if _Reporter.issues(_wrappers, error_on_chains=False):
        _print("\nIssues were found in deprecated wrappers.")
    else:
        _print("\nAll deprecated wrappers look correct!")

    has_invalid = any(r.invalid_args for r in _wrappers)
    return 1 if not skip_errors and has_invalid else 0


def cmd_expiry(
    path: str = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    skip_errors: bool = False,
    *,
    _wrappers: Optional[list[DeprecationWrapperInfo]] = None,
) -> int:
    """Check for deprecated wrappers that have passed their scheduled removal version.

    Requires the ``packaging`` library: ``pip install 'pyDeprecate[audit]'``.
    A missing ``packaging`` library is treated as advisory (returns 0 with a warning).

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for comparison (e.g. ``"2.0.0"``). Auto-detected
            from installed package metadata if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        skip_errors: Always exit 0 even if expired wrappers are found.
        _wrappers: Pre-scanned wrapper list. When provided, skips the scan step and derives
            expired wrappers via ``_check_expiry_for_callables``. Requires *version* to be
            non-``None`` when set. Underscore prefix hides this parameter from the Fire CLI
            (internal use by ``cmd_all`` only).

    Returns:
        0 on success or when the ``packaging`` library is unavailable; 1 when expired
        wrappers are found and ``skip_errors`` is False.
    """
    # Fire auto-converts numeric-looking strings (e.g. "1.0" → float); normalise to str.
    if version is not None:
        version = str(version)
    if _wrappers is None:
        # Standalone path: full scan + version auto-detect inside _do_expiry.
        _print(f"Scanning path: {path} ...")
        with _managed_sys_path(path):
            raw = _do_expiry(path, version, recursive)
        if raw is None:  # packaging unavailable — warning already printed to stderr
            return 0
        expired = raw
    else:
        # Pre-scanned path: derive expired list from wrappers directly.
        if version is None:
            _print("Cannot check expiry: version not resolved. Pass --version explicitly.", stderr=True)
            return 0
        try:
            expired = _check_expiry_for_callables(_wrappers, version)
        except ImportError:
            _print(
                "The 'expiry' subcommand requires the 'packaging' library.\n"
                "Install it with: `pip install 'pyDeprecate[audit]'`",
                stderr=True,
            )
            return 0
    if not expired:
        _print("No expired deprecated wrappers found.")
        return 0
    _Reporter.expiry(expired)
    _print(f"\n{len(expired)} expired wrapper(s) found.")
    return 0 if skip_errors else 1


def cmd_chains(
    path: str = ".",
    recursive: bool = True,
    skip_errors: bool = False,
    *,
    _wrappers: Optional[list[DeprecationWrapperInfo]] = None,
) -> int:
    """Detect deprecated wrappers whose ``target`` is itself a deprecated callable (chains).

    Two chain kinds are detected: ``target`` (forwarding chain to another deprecated
    callable) and ``stacked`` (composed argument mappings that should be collapsed).

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        skip_errors: Always exit 0 even if chains are found.
        _wrappers: Pre-scanned wrapper list. When provided, skips the scan step and filters
            for ``chain_type is not None`` internally. Underscore prefix hides this parameter
            from the Fire CLI (internal use by ``cmd_all`` only).

    Returns:
        0 when no chains are found or ``skip_errors`` is True; 1 when chains are found.
    """
    if _wrappers is None:
        _print(f"Scanning path: {path} ...")
        with _managed_sys_path(path):
            _wrappers = validate_deprecation_chains(_resolve_module_name(path), recursive=recursive)
    chains = [r for r in _wrappers if r.chain_type is not None]
    if not chains:
        _print("No deprecation chains found.")
        return 0
    _Reporter.chains(chains, error=True)
    _print(f"\n{len(chains)} deprecation chain(s) found.")
    return 0 if skip_errors else 1


def cmd_all(
    path: str = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    skip_errors: bool = False,
) -> int:
    """Run all three checks sequentially: wrapper configuration, expiry, and chain detection.

    Performs a single scan pass and distributes the wrappers to ``cmd_check``,
    ``cmd_expiry``, and ``cmd_chains`` so the filesystem is only traversed once.
    Version is auto-detected from installed package metadata when not provided.
    A missing ``packaging`` library skips the expiry check with a warning and does not
    count as a hard error.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for expiry comparison (e.g. ``"2.0.0"``).
            Auto-detected from installed package metadata if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        skip_errors: Always exit 0 even if issues are found.

    Returns:
        0 when all checks pass or ``skip_errors`` is True; 1 when any hard error is found.
    """
    if version is not None:
        version = str(version)
    _print(f"Scanning path: {path} ...")
    with _managed_sys_path(path):
        wrappers = _scan_path(path, recursive=recursive)

    # Resolve version for expiry check (auto-detect from installed metadata if not given).
    resolved_version = version
    if resolved_version is None:
        try:
            with _managed_sys_path(path):
                module_name = _resolve_module_name(path)
            resolved_version = _auto_detect_version(module_name)
        except ValueError:
            resolved_version = None  # plain dir — expiry will warn and skip

    check_code = cmd_check(path, recursive=recursive, skip_errors=False, _wrappers=wrappers)
    expiry_code = cmd_expiry(path, version=resolved_version, recursive=recursive, skip_errors=False, _wrappers=wrappers)
    chains_code = cmd_chains(path, recursive=recursive, skip_errors=False, _wrappers=wrappers)

    has_errors = bool(check_code or expiry_code or chains_code)
    return 0 if not has_errors or skip_errors else 1


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _wrap(fn: Callable[..., int]) -> Callable[..., None]:
    """Wrap a cmd_* function so its integer return value becomes a sys.exit() call.

    Acts as the single top-level exception handler for the CLI: unhandled exceptions
    are converted to a non-zero sys.exit with the exception message as the exit code
    string. SystemExit is re-raised unchanged so Fire's --help and normal exits pass
    through unmodified.
    """

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> None:
        try:
            sys.exit(fn(*args, **kwargs))
        except SystemExit:
            raise
        except Exception as exc:
            sys.exit(str(exc))

    return wrapper


def cli() -> None:
    """CLI entry point for pydeprecate."""
    try:
        import fire
    except ImportError:
        sys.exit("The 'pydeprecate' CLI requires the 'fire' package.\nInstall it with: pip install 'pyDeprecate[cli]'")

    subcommands = {"check", "expiry", "chains", "all"}
    argv = sys.argv[1:]
    if argv and argv[0] not in subcommands and argv[0] not in {"-h", "--help"}:
        argv = ["check", *argv]

    fire.Fire(
        {
            "check": _wrap(cmd_check),
            "expiry": _wrap(cmd_expiry),
            "chains": _wrap(cmd_chains),
            "all": _wrap(cmd_all),
        },
        command=argv,
    )
