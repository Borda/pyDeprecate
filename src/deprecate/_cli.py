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

import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, Optional

if TYPE_CHECKING:
    from rich.table import Table as RichTable

from deprecate.audit import (
    DeprecationWrapperInfo,
    _get_package_version,
    find_deprecation_wrappers,
    validate_deprecation_chains,
    validate_deprecation_expiry,
)

_console: Any = None
_err_console: Any = None

try:
    from rich import box as rich_box
    from rich.console import Console as RichConsole
    from rich.table import Table as RichTable

    _console = RichConsole()
    _err_console = RichConsole(stderr=True)
    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False


def _print(msg: str, *, stderr: bool = False) -> None:
    """Print a message, using Rich console when available.

    Routes output through :class:`~rich.console.Console` when the ``rich``
    package is installed, falling back to built-in :func:`print` otherwise.

    Args:
        msg: The message to print.
        stderr: If ``True``, send the message to *stderr* instead of *stdout*.
    """
    if _HAS_RICH:
        (_err_console if stderr else _console).print(msg, markup=False, highlight=False)
    else:
        print(msg, file=sys.stderr if stderr else sys.stdout)


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


def _scan_or_exit(path: str, recursive: bool = True) -> list[DeprecationWrapperInfo]:
    """Scan a path for deprecated wrappers, exiting with code 1 on failure.

    Prepends the appropriate import root to ``sys.path`` via :func:`_managed_sys_path`,
    then delegates to :func:`_scan_path`. On any exception, prints the error to
    *stderr* and calls ``sys.exit(1)``.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        recursive: Scan submodules recursively (default ``True``).

    Returns:
        List of :class:`~deprecate.audit.DeprecationWrapperInfo` instances found during the scan.
    """
    with _managed_sys_path(path):
        try:
            return _scan_path(path, recursive=recursive)
        except Exception as exc:
            sys.exit(f"Error scanning {path}: {exc}")


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
            "Skipping nested Python files in plain directory scan."
            " Use an importable package layout with '__init__.py' files, or scan"
            " an importable module/package path instead.",
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
# Reporters (rich/plain dispatch unified per reporter)
# ---------------------------------------------------------------------------


def _make_table(title: str, col: str, *, title_style: str, col_style: str) -> "RichTable":
    table = RichTable(title=title, box=rich_box.ROUNDED, title_style=title_style)
    table.add_column("Module", style="cyan")
    table.add_column("Function", style="magenta")
    table.add_column(col, style=col_style)
    return table


def _report_invalid_args(invalid_args: list[DeprecationWrapperInfo]) -> None:
    if _HAS_RICH:
        table = _make_table("Invalid Argument Mappings", "Invalid Args", title_style="bold red", col_style="red")
        for r in invalid_args:
            table.add_row(r.module, r.function, ", ".join(r.invalid_args))
        _console.print(table)  # RichTable cannot route through _print(); call _console directly
    else:
        _print("\n[ERROR] Found functions with invalid argument mappings:")
        for r in invalid_args:
            _print(f"\t- {r.module}.{r.function}: {r.invalid_args}")


def _report_identity_mappings(identity_mappings: list[DeprecationWrapperInfo]) -> None:
    if _HAS_RICH:
        table = _make_table(
            "Identity Argument Mappings (arg -> arg)", "Identity Args", title_style="bold yellow", col_style="yellow"
        )
        for r in identity_mappings:
            table.add_row(r.module, r.function, ", ".join(r.identity_mapping))
        _console.print(table)
    else:
        _print("\n[WARNING] Found functions with identity argument mappings (arg -> arg):")
        for r in identity_mappings:
            _print(f"\t- {r.module}.{r.function}: {r.identity_mapping}")


def _report_no_effect(no_effect: list[DeprecationWrapperInfo]) -> None:
    if _HAS_RICH:
        table = _make_table("No-Effect Wrappers (zero impact)", "Reason", title_style="bold yellow", col_style="yellow")
        for r in no_effect:
            reasons = []
            if r.empty_mapping:
                reasons.append("Empty mapping")
            if r.self_reference:
                reasons.append("Self reference")
            if r.all_identity:
                reasons.append("All identity mappings")
            table.add_row(r.module, r.function, ", ".join(reasons))
        _console.print(table)
    else:
        _print("\n[WARNING] Found deprecated wrappers with NO EFFECT (zero impact):")
        for r in no_effect:
            _print(f"\t- {r.module}.{r.function}")
            if r.empty_mapping:
                _print("\t\tReason: Empty mapping")
            if r.self_reference:
                _print("\t\tReason: Self reference")
            if r.all_identity:
                _print("\t\tReason: All identity mappings")


def _report_chains(chains: list[DeprecationWrapperInfo], *, error: bool = False) -> None:
    if _HAS_RICH:
        style = "bold red" if error else "bold yellow"
        col_style = "red" if error else "yellow"
        table = _make_table("Deprecation Chains", "Chain Type", title_style=style, col_style=col_style)
        for r in chains:
            chain_label = r.chain_type.value if r.chain_type is not None else ""
            table.add_row(r.module, r.function, chain_label)
        _console.print(table)
    else:
        prefix = "[ERROR]" if error else "[WARNING]"
        _print(f"\n{prefix} Found deprecated wrappers forming deprecation chains:")
        for r in chains:
            chain_label = r.chain_type.value if r.chain_type is not None else "unknown"
            _print(f"\t- {r.module}.{r.function}: {chain_label} chain")


def _report_expiry(expired: list[str]) -> None:
    if _HAS_RICH:
        table = RichTable(title="Expired Deprecated Wrappers", box=rich_box.ROUNDED, title_style="bold red")
        table.add_column("Message", style="red")
        for msg in expired:
            table.add_row(msg)
        _console.print(table)
    else:
        _print("\n[ERROR] Found expired deprecated wrappers:")
        for msg in expired:
            _print(f"\t- {msg}")


# ---------------------------------------------------------------------------
# Aggregated issue reporter
# ---------------------------------------------------------------------------


def _report_issues(results: list[DeprecationWrapperInfo], *, error_on_chains: bool = False) -> bool:
    """Print categorised diagnostics and return whether any issues were found."""
    invalid_args = [r for r in results if r.invalid_args]
    identity_mappings = [r for r in results if r.identity_mapping]
    no_effect = [r for r in results if r.no_effect]
    chains = [r for r in results if r.chain_type is not None]

    if not (invalid_args or identity_mappings or no_effect or chains):
        return False

    if invalid_args:
        _report_invalid_args(invalid_args)
    if identity_mappings:
        _report_identity_mappings(identity_mappings)
    if no_effect:
        _report_no_effect(no_effect)
    if chains:
        _report_chains(chains, error=error_on_chains)

    return True


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
# Subcommand functions
# ---------------------------------------------------------------------------


def cmd_check(
    path: str = ".",
    recursive: bool = True,
    skip_errors: bool = False,
) -> NoReturn:
    """Scan Python code for misconfigured ``@deprecated`` wrappers and deprecation chains.

    Reports invalid argument mappings (exit 1), identity mappings, no-effect wrappers,
    and deprecation chains (warnings only, exit 0).

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        skip_errors: Always exit 0 even if hard errors (invalid argument mappings) are found.
    """
    _print(f"Scanning path: {path} ...")

    results = _scan_or_exit(path, recursive=recursive)

    if not results:
        _print("No deprecated callables found.")
        sys.exit(0)

    has_invalid = any(r.invalid_args for r in results)

    if _report_issues(results):
        _print("\nIssues were found in deprecated wrappers.")
        if not skip_errors and has_invalid:
            sys.exit(1)
    else:
        _print("\nAll deprecated wrappers look correct!")

    sys.exit(0)


def cmd_expiry(
    path: str = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    skip_errors: bool = False,
) -> NoReturn:
    """Check for deprecated wrappers that have passed their scheduled removal version.

    Requires the ``packaging`` library: ``pip install 'pyDeprecate[audit]'``.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for comparison (e.g. ``"2.0.0"``). Auto-detected
            from installed package metadata if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        skip_errors: Always exit 0 even if expired wrappers are found.
    """
    # Fire auto-converts numeric-looking strings (e.g. "1.0" → float); normalise to str.
    if version is not None:
        version = str(version)
    _print(f"Scanning path: {path} ...")

    with _managed_sys_path(path):
        try:
            module_name = _resolve_module_name(path)
        except ValueError as err:
            sys.exit(str(err))

        try:
            expired = validate_deprecation_expiry(module_name, version, recursive=recursive)
        except ImportError as e:
            if _is_missing_packaging_import_error(e):
                _print(
                    "The 'expiry' subcommand requires the 'packaging' library.\n"
                    "Install it with:\n\n"
                    "    pip install 'pyDeprecate[audit]'\n",
                    stderr=True,
                )
            else:
                _print(
                    "Could not determine the current package version automatically.\n"
                    "Pass --version explicitly, or ensure the package is installed and importable.\n\n"
                    f"Original error: {e}",
                    stderr=True,
                )
            sys.exit(0 if skip_errors else 1)
        except Exception as e:
            sys.exit(f"Error checking expiry for {path}: {e}")

    if not expired:
        _print("No expired deprecated wrappers found.")
        sys.exit(0)

    _report_expiry(expired)

    _print(f"\n{len(expired)} expired wrapper(s) found.")
    sys.exit(0 if skip_errors else 1)


def cmd_chains(
    path: str = ".",
    recursive: bool = True,
    skip_errors: bool = False,
) -> NoReturn:
    """Detect deprecated wrappers whose ``target`` is itself a deprecated callable (chains).

    Two chain kinds are detected: ``target`` (forwarding chain to another deprecated
    callable) and ``stacked`` (composed argument mappings that should be collapsed).

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        skip_errors: Always exit 0 even if chains are found.
    """
    _print(f"Scanning path: {path} ...")

    with _managed_sys_path(path):
        try:
            module_name = _resolve_module_name(path)
        except ValueError as err:
            sys.exit(str(err))

        try:
            chains = validate_deprecation_chains(module_name, recursive=recursive)
        except Exception as e:
            sys.exit(f"Error checking chains for {path}: {e}")

    if not chains:
        _print("No deprecation chains found.")
        sys.exit(0)

    _report_chains(chains, error=True)

    _print(f"\n{len(chains)} deprecation chain(s) found.")
    sys.exit(0 if skip_errors else 1)


def cmd_all(
    path: str = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    skip_errors: bool = False,
) -> NoReturn:
    """Run all checks: wrapper configuration, expiry, and chain detection.

    Performs a single scan pass and applies all three analyses to the same results.
    Requires the ``packaging`` library for expiry checks; if unavailable, the expiry
    check is skipped with a warning and the other checks still run.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for expiry comparison (e.g. ``"2.0.0"``).
            Auto-detected from installed package metadata if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        skip_errors: Always exit 0 even if issues are found.
    """
    # Fire auto-converts numeric-looking strings (e.g. "1.0" → float); normalise to str.
    if version is not None:
        version = str(version)
    _print(f"Scanning path: {path} ...")

    results = _scan_or_exit(path, recursive=recursive)

    if not results:
        _print("No deprecated callables found.")
        sys.exit(0)

    has_errors = False

    # --- check: wrapper config + chains ---
    has_invalid = any(r.invalid_args for r in results)
    has_chains = any(r.chain_type is not None for r in results)
    issues_reported = _report_issues(results, error_on_chains=True)
    if issues_reported and (has_invalid or has_chains):
        has_errors = True

    # --- expiry ---
    resolved_version = version
    if resolved_version is None:
        try:
            module_name = _resolve_module_name(path)
            package_name = module_name.split(".")[0]
            resolved_version = _get_package_version(package_name)
        except Exception:
            resolved_version = None

    if resolved_version is not None:
        try:
            from deprecate.audit import _check_expiry_for_callables

            expired = _check_expiry_for_callables(results, resolved_version)
            if expired:
                _report_expiry(expired)
                _print(f"\n{len(expired)} expired wrapper(s) found.")
                has_errors = True
        except ImportError:
            _print(
                "Skipping expiry check: 'packaging' library not installed. "
                "Install with: pip install 'pyDeprecate[audit]'",
                stderr=True,
            )
        except ValueError as err:
            sys.exit(f"Invalid version {resolved_version!r}: {err}. Pass a valid PEP 440 version with --version.")
    else:
        _print(
            "Skipping expiry check: could not determine package version. Pass --version explicitly to enable.",
            stderr=True,
        )

    if has_errors:
        _print("\nIssues were found.")
        sys.exit(0 if skip_errors else 1)

    if issues_reported:
        _print("\nWarnings only — no hard errors.")
        sys.exit(0)

    _print("\nAll checks passed!")
    sys.exit(0)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


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
        {"check": cmd_check, "expiry": cmd_expiry, "chains": cmd_chains, "all": cmd_all},
        command=argv,
    )
