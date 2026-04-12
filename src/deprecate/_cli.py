"""CLI entry point for pyDeprecate validation.

Provides two entry points for scanning Python code for misconfigured ``@deprecated`` wrappers:

- ``pydeprecate <path>`` — console script installed via ``pip install 'pyDeprecate[cli]'``
- ``python -m deprecate <path>`` — module invocation of the same optional CLI

Both entry points require the optional ``cli`` extra. This module supports both rich and
plain-text reporting, but invoking the CLI still requires the optional CLI dependencies.
"""

import os
import sys
from typing import Optional

from deprecate.audit import DeprecationWrapperInfo, find_deprecation_wrappers

_HAS_RICH = False
try:
    from rich import box as rich_box
    from rich.console import Console as RichConsole
    from rich.table import Table as RichTable

    _HAS_RICH = True
except ImportError:  # pragma: no cover
    pass


def _scan_package(path: str) -> list[DeprecationWrapperInfo]:
    """Scan a Python package (directory with ``__init__.py``)."""
    module_name = os.path.basename(os.path.abspath(path))
    return find_deprecation_wrappers(module_name)


def _scan_directory(path: str) -> list[DeprecationWrapperInfo]:
    """Scan a plain directory of top-level Python files.

    Nested Python files in subdirectories are skipped unless they are part of an
    importable package layout. Plain directories do not generally support dotted
    imports for nested modules, so this function only scans top-level modules and
    warns when deeper files are present.
    """
    abs_path = os.path.abspath(path)
    results: list[DeprecationWrapperInfo] = []

    for entry in sorted(os.listdir(abs_path)):
        full_path = os.path.join(abs_path, entry)
        if os.path.isfile(full_path) and entry.endswith(".py") and not entry.startswith("__"):
            module_name = entry[:-3]  # remove .py
            try:
                results.extend(find_deprecation_wrappers(module_name, recursive=False))
            except Exception as e:
                print(f"[WARNING] Could not scan {module_name}: {e}")

    nested_python_files_found = False
    for root, _, files in os.walk(abs_path):
        if root == abs_path:
            continue
        if any(file.endswith(".py") and not file.startswith("__") for file in files):
            nested_python_files_found = True
            break

    if nested_python_files_found:
        print(
            "[WARNING] Skipping nested Python files in plain directory scan. "
            "Use an importable package layout with '__init__.py' files, or scan "
            "an importable module/package path instead."
        )
    return results


def _scan_path(path: str) -> list[DeprecationWrapperInfo]:
    """Scan a directory or importable module/package name for deprecated wrappers.

    File paths are not accepted because ``find_deprecation_wrappers()`` expects an
    importable module or package name, not a filesystem path.
    """
    if os.path.isdir(path):
        if os.path.exists(os.path.join(path, "__init__.py")):
            return _scan_package(path)
        return _scan_directory(path)
    if os.path.isfile(path):
        raise ValueError(
            f"File paths are not supported: {path!r}. Pass an importable module/package name or a directory instead."
        )
    return find_deprecation_wrappers(path)


def _report_issues_rich(results: list[DeprecationWrapperInfo]) -> bool:
    """Print categorised diagnostics using Rich and return whether any issues were found."""
    console = RichConsole()
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
        console.print(table)
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
        console.print(table)
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
        console.print(table)
        issues_found = True

    return issues_found


def _has_all_identity_mappings(info: DeprecationWrapperInfo) -> bool:
    """Return whether all configured mappings are identity mappings."""
    args_mapping = info.deprecated_info.args_mapping
    return bool(args_mapping) and len(info.identity_mapping) == len(args_mapping or {}) and not info.invalid_args


def _report_issues_plain(results: list[DeprecationWrapperInfo]) -> bool:
    """Print categorised diagnostics in plain text and return whether any issues were found."""
    invalid_args = [r for r in results if r.invalid_args]
    identity_mappings = [r for r in results if r.identity_mapping]
    no_effect = [r for r in results if r.no_effect]

    issues_found = False

    if invalid_args:
        print("\n[ERROR] Found functions with invalid argument mappings:")
        for r in invalid_args:
            print(f"  - {r.module}.{r.function}: {r.invalid_args}")
        issues_found = True

    if identity_mappings:
        print("\n[WARNING] Found functions with identity argument mappings (arg -> arg):")
        for r in identity_mappings:
            print(f"  - {r.module}.{r.function}: {r.identity_mapping}")
        issues_found = True

    if no_effect:
        print("\n[WARNING] Found deprecated wrappers with NO EFFECT (zero impact):")
        for r in no_effect:
            print(f"  - {r.module}.{r.function}")
            if r.empty_mapping:
                print("    Reason: Empty mapping")
            if r.self_reference:
                print("    Reason: Self reference")
            if _has_all_identity_mappings(r):
                print("    Reason: All identity mappings")
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
    print(f"Scanning path: {path} ...")

    abs_path = os.path.abspath(path)
    import_root: Optional[str] = None
    original_sys_path = list(sys.path)

    if os.path.isdir(abs_path):
        import_root = os.path.dirname(abs_path) if os.path.exists(os.path.join(abs_path, "__init__.py")) else abs_path

    if import_root is not None:
        sys.path.insert(0, import_root)

    try:
        results = _scan_path(path)
    except Exception as e:
        print(f"Error scanning {path}: {e}")
        return 1
    finally:
        sys.path[:] = original_sys_path

    if not results:
        print("No deprecated callables found.")
        return 0

    has_invalid = any(r.invalid_args for r in results)

    if _report_issues(results):
        print("\nIssues were found in deprecated wrappers.")
        if not skip_errors and has_invalid:
            return 1
    else:
        print("\nAll deprecated wrappers look correct!")

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
        print(
            "The pyDeprecate CLI requires additional dependencies.\n"
            "Install them with:\n\n"
            "    pip install 'pyDeprecate[cli]'\n",
            file=sys.stderr,
        )
        sys.exit(1)
