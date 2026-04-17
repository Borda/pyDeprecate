"""CLI entry point for pyDeprecate validation.

Provides two entry points for scanning Python code for misconfigured ``@deprecated`` wrappers:

- ``pydeprecate <path>`` — console script installed via ``pip install 'pyDeprecate[cli]'``
- ``python -m deprecate <path>`` — module invocation of the same optional CLI

Both entry points require the optional ``cli`` extra. This module supports both rich and
plain-text reporting, but invoking the CLI still requires the optional CLI dependencies.
"""

import sys
from pathlib import Path
from typing import Optional

from deprecate.audit import DeprecationWrapperInfo, find_deprecation_wrappers

try:
    from rich import box as rich_box
    from rich.console import Console as RichConsole
    from rich.table import Table as RichTable

    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False

if _HAS_RICH:
    _console: RichConsole = RichConsole()
    _err_console: RichConsole = RichConsole(stderr=True)


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


def _scan_package(path: str) -> list[DeprecationWrapperInfo]:
    """Scan a Python package (directory with ``__init__.py``)."""
    module_name: str = Path(path).resolve().name
    return find_deprecation_wrappers(module_name)


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
        for entry in sorted(abs_path.iterdir(), key=lambda p: p.name):
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
        not p.name.startswith("__") for p in abs_path.rglob("*.py") if p.parent != abs_path
    )

    if nested_python_files_found:
        _print(
            "Skipping nested Python files in plain directory scan."
            " Use an importable package layout with '__init__.py' files, or scan"
            " an importable module/package path instead.",
            stderr=True,
        )
    return results


def _scan_path(path: str) -> list[DeprecationWrapperInfo]:
    """Scan a directory or importable module/package name for deprecated wrappers.

    File paths are not accepted because ``find_deprecation_wrappers()`` expects an
    importable module or package name, not a filesystem path.
    """
    p: Path = Path(path)
    if p.is_dir():
        if (p / "__init__.py").exists():
            return _scan_package(path)
        return _scan_directory(path)
    if p.is_file():
        raise ValueError(
            f"File paths are not supported: {path!r}. Pass an importable module/package name or a directory instead."
        )
    return find_deprecation_wrappers(path)


def _report_issues_rich(results: list[DeprecationWrapperInfo]) -> bool:
    """Print categorised diagnostics using Rich and return whether any issues were found."""
    invalid_args = [r for r in results if r.invalid_args]
    identity_mappings = [r for r in results if r.identity_mapping]
    no_effect = [r for r in results if r.no_effect]

    issues_found = False

    if invalid_args:
        table = RichTable(title="Invalid Argument Mappings", box=rich_box.ROUNDED, title_style="bold red")
        table.add_column("Module", style="cyan")
        table.add_column("Function", style="magenta")
        table.add_column("Invalid Args", style="red")
        for r in invalid_args:
            table.add_row(r.module, r.function, ", ".join(r.invalid_args))
        _console.print(table)
        issues_found = True

    if identity_mappings:
        table = RichTable(
            title="Identity Argument Mappings (arg -> arg)", box=rich_box.ROUNDED, title_style="bold yellow"
        )
        table.add_column("Module", style="cyan")
        table.add_column("Function", style="magenta")
        table.add_column("Identity Args", style="yellow")
        for r in identity_mappings:
            table.add_row(r.module, r.function, ", ".join(r.identity_mapping))
        _console.print(table)
        issues_found = True

    if no_effect:
        table = RichTable(title="No-Effect Wrappers (zero impact)", box=rich_box.ROUNDED, title_style="bold yellow")
        table.add_column("Module", style="cyan")
        table.add_column("Function", style="magenta")
        table.add_column("Reason", style="yellow")
        for r in no_effect:
            reasons = []
            if r.empty_mapping:
                reasons.append("Empty mapping")
            if r.self_reference:
                reasons.append("Self reference")
            if _has_all_identity_mappings(r):
                reasons.append("All identity mappings")
            table.add_row(r.module, r.function, ", ".join(reasons))
        _console.print(table)
        issues_found = True

    return issues_found


def _has_all_identity_mappings(info: DeprecationWrapperInfo) -> bool:
    """Return whether all configured mappings are identity mappings."""
    args_mapping = info.deprecated_info.args_mapping
    equal_mapping = len(info.identity_mapping) == len(args_mapping or {})
    return bool(args_mapping) and equal_mapping and not info.invalid_args


def _report_issues_plain(results: list[DeprecationWrapperInfo]) -> bool:
    """Print categorised diagnostics in plain text and return whether any issues were found."""
    invalid_args = [r for r in results if r.invalid_args]
    identity_mappings = [r for r in results if r.identity_mapping]
    no_effect = [r for r in results if r.no_effect]

    issues_found = False

    if invalid_args:
        _print("\n[ERROR] Found functions with invalid argument mappings:")
        for r in invalid_args:
            _print(f"\t- {r.module}.{r.function}: {r.invalid_args}")
        issues_found = True

    if identity_mappings:
        _print("\n[WARNING] Found functions with identity argument mappings (arg -> arg):")
        for r in identity_mappings:
            _print(f"\t- {r.module}.{r.function}: {r.identity_mapping}")
        issues_found = True

    if no_effect:
        _print("\n[WARNING] Found deprecated wrappers with NO EFFECT (zero impact):")
        for r in no_effect:
            _print(f"\t- {r.module}.{r.function}")
            if r.empty_mapping:
                _print("\t\tReason: Empty mapping")
            if r.self_reference:
                _print("\t\tReason: Self reference")
            if _has_all_identity_mappings(r):
                _print("\t\tReason: All identity mappings")
        issues_found = True

    return issues_found


def _report_issues(results: list[DeprecationWrapperInfo]) -> bool:
    """Print categorised diagnostics and return whether any issues were found."""
    if _HAS_RICH:
        return _report_issues_rich(results)
    return _report_issues_plain(results)


def main(
    path: str = ".",
    skip_errors: bool = False,
) -> int:
    """Scan Python code for misconfigured ``@deprecated`` wrappers.

    Args:
        path: Path to the module or package to scan.
        skip_errors: Do not exit with error code even if issues are found.
    """
    _print(f"Scanning path: {path} ...")

    abs_path: Path = Path(path).resolve()
    import_root: Optional[str] = None
    original_sys_path = list(sys.path)

    if abs_path.is_dir():
        import_root = str(abs_path.parent) if (abs_path / "__init__.py").exists() else str(abs_path)

    if import_root is not None:
        sys.path.insert(0, import_root)

    try:
        results = _scan_path(path)
    except Exception as e:
        _print(f"Error scanning {path}: {e}", stderr=True)
        return 1
    finally:
        sys.path[:] = original_sys_path

    if not results:
        _print("No deprecated callables found.")
        return 0

    has_invalid = any(r.invalid_args for r in results)

    if _report_issues(results):
        _print("\nIssues were found in deprecated wrappers.")
        if not skip_errors and has_invalid:
            return 1
    else:
        _print("\nAll deprecated wrappers look correct!")

    return 0


def cli() -> None:
    """CLI entry point using jsonargparse."""
    try:
        from jsonargparse import auto_cli, set_parsing_settings

        set_parsing_settings(parse_optionals_as_positionals=True)
        result = auto_cli(main)
        if isinstance(result, int):
            sys.exit(result)
    except ImportError:
        _print(
            "The pyDeprecate CLI requires additional dependencies.\n"
            "Install them with:\n\n"
            "    pip install 'pyDeprecate[cli]'\n",
            stderr=True,
        )
        sys.exit(1)
