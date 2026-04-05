"""CLI entry point for pyDeprecate validation."""

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
    """Scan a plain directory of Python files."""
    abs_path = os.path.abspath(path)
    results: list[DeprecationWrapperInfo] = []
    for root, _, files in os.walk(abs_path):
        for file in files:
            if file.endswith(".py") and not file.startswith("__"):
                rel_path = os.path.relpath(os.path.join(root, file), abs_path)
                module_name = rel_path.replace(os.path.sep, ".")[:-3]  # remove .py
                try:
                    results.extend(find_deprecation_wrappers(module_name, recursive=False))
                except Exception as e:
                    print(f"[WARNING] Could not scan {module_name}: {e}")
    return results


def _scan_path(path: str) -> list[DeprecationWrapperInfo]:
    """Scan a path for deprecated wrappers."""
    if os.path.isdir(path):
        if os.path.exists(os.path.join(path, "__init__.py")):
            return _scan_package(path)
        return _scan_directory(path)
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
            if r.identity_mapping and not r.invalid_args:
                reasons.append("All identity mappings")
            table.add_row(r.module, r.function, ", ".join(reasons))
        console.print(table)
        issues_found = True

    return issues_found


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
            if r.identity_mapping and not r.invalid_args:
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
    ignore: Optional[list[str]] = None,
    skip_errors: bool = False,
) -> int:
    """Scan Python code for misconfigured ``@deprecated`` wrappers.

    Args:
        path: Path to the module or package to scan.
        ignore: List of files or directories to ignore.
        skip_errors: Do not exit with error code even if issues are found.
    """
    print(f"Scanning path: {path} ...")

    # Add current directory to sys.path to allow importing local modules
    sys.path.append(os.getcwd())

    try:
        results = _scan_path(path)
    except Exception as e:
        print(f"Error scanning {path}: {e}")
        return 1

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
        from jsonargparse import CLI

        CLI(main)
    except ImportError:
        import argparse

        parser = argparse.ArgumentParser(description="pyDeprecate CLI - Validate deprecated wrappers.")
        parser.add_argument("path", nargs="?", default=".", help="Path to module or package to scan")
        parser.add_argument("--ignore", nargs="+", default=[], help="Files or directories to ignore")
        parser.add_argument("--skip-errors", action="store_true", help="Exit 0 even if issues found")
        args = parser.parse_args()
        sys.exit(main(path=args.path, ignore=args.ignore, skip_errors=args.skip_errors))


if __name__ == "__main__":
    cli()
