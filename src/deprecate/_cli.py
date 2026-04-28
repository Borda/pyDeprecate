"""CLI entry point for pyDeprecate validation.

Provides two entry points for scanning Python code for misconfigured ``@deprecated`` wrappers:

- ``pydeprecate <subcommand> <path>`` — console script installed via ``pip install 'pyDeprecate[cli]'``
- ``python -m deprecate <subcommand> <path>`` — module invocation of the same optional CLI

Subcommands:
    check   — Validate wrapper configuration and flag misconfigured or chain-forming wrappers (default).
    expiry  — Check for deprecated wrappers that have passed their scheduled ``remove_in`` deadline.
    chains  — Detect deprecated wrappers whose ``target`` is itself a deprecated callable.
    all     — Run all three checks in a single scan pass.

Both entry points require the optional ``cli`` extra. This module supports both rich and
plain-text reporting, but invoking the CLI still requires the optional CLI dependencies.
"""

import sys
from pathlib import Path
from typing import Optional

from deprecate.audit import (
    DeprecationWrapperInfo,
    find_deprecation_wrappers,
    validate_deprecation_chains,
    validate_deprecation_expiry,
)

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


_SUBCOMMANDS = frozenset({"check", "expiry", "chains", "all"})
# Top-level flags that must NOT trigger the backward-compat 'check' injection.
_TOP_LEVEL_FLAGS = frozenset({"-h", "--help"})


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


def _setup_sys_path(path: str) -> tuple[Optional[str], list[str]]:
    """Add the import root to sys.path if path is a directory.

    Args:
        path: Path to the module, package directory, or importable module name.

    Returns:
        Tuple of ``(import_root, original_sys_path)``. ``import_root`` is ``None``
        if the path is not a directory. The caller is responsible for restoring
        ``sys.path`` using the returned ``original_sys_path``.
    """
    abs_path: Path = Path(path).resolve()
    import_root: Optional[str] = None
    original_sys_path = list(sys.path)

    if abs_path.is_dir():
        import_root = str(abs_path.parent) if (abs_path / "__init__.py").exists() else str(abs_path)

    if import_root is not None:
        sys.path.insert(0, import_root)

    return import_root, original_sys_path


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
    p = Path(path)
    if p.is_file():
        raise ValueError(
            f"File paths are not supported: {path!r}. Pass an importable module/package name or a directory instead."
        )
    if p.is_dir():
        if (p / "__init__.py").exists():
            return p.resolve().name
        raise ValueError(
            f"Plain directories without '__init__.py' are not supported for expiry or chain checks: {path!r}. "
            "Use an importable package layout with '__init__.py', or pass an importable module name instead."
        )
    return path


def _scan_package(path: str, recursive: bool = True) -> list[DeprecationWrapperInfo]:
    """Scan a Python package (directory with ``__init__.py``)."""
    module_name: str = Path(path).resolve().name
    return find_deprecation_wrappers(module_name, recursive=recursive)


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


def _scan_path(path: str, recursive: bool = True) -> list[DeprecationWrapperInfo]:
    """Scan a directory or importable module/package name for deprecated wrappers.

    File paths are not accepted because ``find_deprecation_wrappers()`` expects an
    importable module or package name, not a filesystem path.
    """
    p: Path = Path(path)
    if p.is_dir():
        if (p / "__init__.py").exists():
            return _scan_package(path, recursive=recursive)
        return _scan_directory(path)
    if p.is_file():
        raise ValueError(
            f"File paths are not supported: {path!r}. Pass an importable module/package name or a directory instead."
        )
    return find_deprecation_wrappers(path, recursive=recursive)


def _has_all_identity_mappings(info: DeprecationWrapperInfo) -> bool:
    """Return whether all configured mappings are identity mappings."""
    args_mapping = info.deprecated_info.args_mapping
    equal_mapping = len(info.identity_mapping) == len(args_mapping or {})
    return bool(args_mapping) and equal_mapping and not info.invalid_args


# ---------------------------------------------------------------------------
# Rich reporters
# ---------------------------------------------------------------------------


def _report_invalid_args_rich(invalid_args: list[DeprecationWrapperInfo]) -> None:
    """Print a Rich table for wrappers with invalid argument mappings."""
    table = RichTable(title="Invalid Argument Mappings", box=rich_box.ROUNDED, title_style="bold red")
    table.add_column("Module", style="cyan")
    table.add_column("Function", style="magenta")
    table.add_column("Invalid Args", style="red")
    for r in invalid_args:
        table.add_row(r.module, r.function, ", ".join(r.invalid_args))
    _console.print(table)


def _report_identity_mappings_rich(identity_mappings: list[DeprecationWrapperInfo]) -> None:
    """Print a Rich table for wrappers with identity argument mappings."""
    table = RichTable(title="Identity Argument Mappings (arg -> arg)", box=rich_box.ROUNDED, title_style="bold yellow")
    table.add_column("Module", style="cyan")
    table.add_column("Function", style="magenta")
    table.add_column("Identity Args", style="yellow")
    for r in identity_mappings:
        table.add_row(r.module, r.function, ", ".join(r.identity_mapping))
    _console.print(table)


def _report_no_effect_rich(no_effect: list[DeprecationWrapperInfo]) -> None:
    """Print a Rich table for deprecated wrappers with no effect."""
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


def _report_chains_rich(chains: list[DeprecationWrapperInfo]) -> None:
    """Print a Rich table for deprecated wrappers that form deprecation chains."""
    table = RichTable(title="Deprecation Chains", box=rich_box.ROUNDED, title_style="bold yellow")
    table.add_column("Module", style="cyan")
    table.add_column("Function", style="magenta")
    table.add_column("Chain Type", style="yellow")
    for r in chains:
        chain_label = r.chain_type.value if r.chain_type is not None else ""
        table.add_row(r.module, r.function, chain_label)
    _console.print(table)


def _report_expiry_rich(expired: list[str]) -> None:
    """Print a Rich-styled table of expired deprecated wrappers."""
    table = RichTable(title="Expired Deprecated Wrappers", box=rich_box.ROUNDED, title_style="bold red")
    table.add_column("Message", style="red")
    for msg in expired:
        table.add_row(msg)
    _console.print(table)


# ---------------------------------------------------------------------------
# Plain reporters
# ---------------------------------------------------------------------------


def _report_invalid_args_plain(invalid_args: list[DeprecationWrapperInfo]) -> None:
    """Print plain-text diagnostics for wrappers with invalid argument mappings."""
    _print("\n[ERROR] Found functions with invalid argument mappings:")
    for r in invalid_args:
        _print(f"\t- {r.module}.{r.function}: {r.invalid_args}")


def _report_identity_mappings_plain(identity_mappings: list[DeprecationWrapperInfo]) -> None:
    """Print plain-text diagnostics for wrappers with identity argument mappings."""
    _print("\n[WARNING] Found functions with identity argument mappings (arg -> arg):")
    for r in identity_mappings:
        _print(f"\t- {r.module}.{r.function}: {r.identity_mapping}")


def _report_no_effect_plain(no_effect: list[DeprecationWrapperInfo]) -> None:
    """Print plain-text diagnostics for deprecated wrappers with no effect."""
    _print("\n[WARNING] Found deprecated wrappers with NO EFFECT (zero impact):")
    for r in no_effect:
        _print(f"\t- {r.module}.{r.function}")
        if r.empty_mapping:
            _print("\t\tReason: Empty mapping")
        if r.self_reference:
            _print("\t\tReason: Self reference")
        if _has_all_identity_mappings(r):
            _print("\t\tReason: All identity mappings")


def _report_chains_plain(chains: list[DeprecationWrapperInfo]) -> None:
    """Print plain-text diagnostics for deprecated wrappers forming deprecation chains."""
    _print("\n[WARNING] Found deprecated wrappers forming deprecation chains:")
    for r in chains:
        chain_label = r.chain_type.value if r.chain_type is not None else "unknown"
        _print(f"\t- {r.module}.{r.function}: {chain_label} chain")


def _report_expiry_plain(expired: list[str]) -> None:
    """Print plain-text diagnostics for expired deprecated wrappers."""
    _print("\n[ERROR] Found expired deprecated wrappers:")
    for msg in expired:
        _print(f"\t- {msg}")


# ---------------------------------------------------------------------------
# Aggregated issue reporter (check subcommand)
# ---------------------------------------------------------------------------


def _report_issues(results: list[DeprecationWrapperInfo]) -> bool:
    """Print categorised diagnostics and return whether any issues were found."""
    invalid_args = [r for r in results if r.invalid_args]
    identity_mappings = [r for r in results if r.identity_mapping]
    no_effect = [r for r in results if r.no_effect]
    chains = [r for r in results if r.chain_type is not None]

    if not (invalid_args or identity_mappings or no_effect or chains):
        return False

    if _HAS_RICH:
        if invalid_args:
            _report_invalid_args_rich(invalid_args)
        if identity_mappings:
            _report_identity_mappings_rich(identity_mappings)
        if no_effect:
            _report_no_effect_rich(no_effect)
        if chains:
            _report_chains_rich(chains)
    else:
        if invalid_args:
            _report_invalid_args_plain(invalid_args)
        if identity_mappings:
            _report_identity_mappings_plain(identity_mappings)
        if no_effect:
            _report_no_effect_plain(no_effect)
        if chains:
            _report_chains_plain(chains)

    return True


# ---------------------------------------------------------------------------
# Expiry helper — single-scan path used by cmd_all
# ---------------------------------------------------------------------------


def _check_expiry_from_results(results: list[DeprecationWrapperInfo], current_version: str) -> list[str]:
    """Check already-scanned wrappers for expired removal deadlines without re-scanning.

    Replicates the core loop of :func:`~deprecate.audit.validate_deprecation_expiry`
    on a pre-scanned result list so that :func:`cmd_all` can check expiry in the same
    scan pass without scanning the module a second time.

    Args:
        results: Pre-scanned wrapper info list from :func:`_scan_path`.
        current_version: Current package version string for comparison (PEP 440).

    Returns:
        List of expiry error messages for callables that have passed their removal deadline.

    Raises:
        ImportError: If the ``packaging`` library is not installed.
    """
    from deprecate.audit import _parse_version  # raises ImportError when packaging missing

    current_ver = _parse_version(current_version)
    expired = []
    for info in results:
        remove_in = info.deprecated_info.remove_in
        if not remove_in:
            continue
        try:
            remove_ver = _parse_version(remove_in)
        except ValueError:
            continue
        if current_ver >= remove_ver:
            expired.append(
                f"Callable `{info.function}` was scheduled for removal in version {remove_in}"
                f" but still exists in version {current_version}. Please delete this deprecated code."
            )
    return expired


# ---------------------------------------------------------------------------
# Subcommand functions
# ---------------------------------------------------------------------------


def cmd_check(
    path: str = ".",
    no_recursive: bool = False,
    skip_errors: bool = False,
) -> int:
    """Scan Python code for misconfigured ``@deprecated`` wrappers and deprecation chains.

    Reports invalid argument mappings (exit 1), identity mappings, no-effect wrappers,
    and deprecation chains (warnings only, exit 0).

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        no_recursive: Only scan the top-level module; skip submodules.
        skip_errors: Always exit 0 even if hard errors (invalid argument mappings) are found.
    """
    _print(f"Scanning path: {path} ...")
    _, original_sys_path = _setup_sys_path(path)

    try:
        results = _scan_path(path, recursive=not no_recursive)
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


def cmd_expiry(
    path: str = ".",
    version: Optional[str] = None,
    no_recursive: bool = False,
    skip_errors: bool = False,
) -> int:
    """Check for deprecated wrappers that have passed their scheduled removal version.

    Requires the ``packaging`` library: ``pip install 'pyDeprecate[audit]'``.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for comparison (e.g. ``"2.0.0"``). Auto-detected
            from installed package metadata if not provided.
        no_recursive: Only scan the top-level module; skip submodules.
        skip_errors: Always exit 0 even if expired wrappers are found.
    """
    _print(f"Scanning path: {path} ...")
    _, original_sys_path = _setup_sys_path(path)

    try:
        try:
            module_name = _resolve_module_name(path)
        except ValueError as e:
            _print(f"Error: {e}", stderr=True)
            return 1

        try:
            expired = validate_deprecation_expiry(module_name, version, recursive=not no_recursive)
        except ImportError:
            _print(
                "The 'expiry' subcommand requires the 'packaging' library.\n"
                "Install it with:\n\n"
                "    pip install 'pyDeprecate[audit]'\n",
                stderr=True,
            )
            return 0 if skip_errors else 1
        except Exception as e:
            _print(f"Error checking expiry for {path}: {e}", stderr=True)
            return 1
    finally:
        sys.path[:] = original_sys_path

    if not expired:
        _print("No expired deprecated wrappers found.")
        return 0

    if _HAS_RICH:
        _report_expiry_rich(expired)
    else:
        _report_expiry_plain(expired)

    _print(f"\n{len(expired)} expired wrapper(s) found.")
    return 0 if skip_errors else 1


def cmd_chains(
    path: str = ".",
    no_recursive: bool = False,
    skip_errors: bool = False,
) -> int:
    """Detect deprecated wrappers whose ``target`` is itself a deprecated callable (chains).

    Two chain kinds are detected: ``target`` (forwarding chain to another deprecated
    callable) and ``stacked`` (composed argument mappings that should be collapsed).

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        no_recursive: Only scan the top-level module; skip submodules.
        skip_errors: Always exit 0 even if chains are found.
    """
    _print(f"Scanning path: {path} ...")
    _, original_sys_path = _setup_sys_path(path)

    try:
        try:
            module_name = _resolve_module_name(path)
        except ValueError as e:
            _print(f"Error: {e}", stderr=True)
            return 1

        try:
            chains = validate_deprecation_chains(module_name, recursive=not no_recursive)
        except Exception as e:
            _print(f"Error checking chains for {path}: {e}", stderr=True)
            return 1
    finally:
        sys.path[:] = original_sys_path

    if not chains:
        _print("No deprecation chains found.")
        return 0

    if _HAS_RICH:
        _report_chains_rich(chains)
    else:
        _report_chains_plain(chains)

    _print(f"\n{len(chains)} deprecation chain(s) found.")
    return 0 if skip_errors else 1


def cmd_all(
    path: str = ".",
    version: Optional[str] = None,
    no_recursive: bool = False,
    skip_errors: bool = False,
) -> int:
    """Run all checks: wrapper configuration, expiry, and chain detection.

    Performs a single scan pass and applies all three analyses to the same results.
    Requires the ``packaging`` library for expiry checks; if unavailable, the expiry
    check is skipped with a warning and the other checks still run.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for expiry comparison (e.g. ``"2.0.0"``).
            Auto-detected from installed package metadata if not provided.
        no_recursive: Only scan the top-level module; skip submodules.
        skip_errors: Always exit 0 even if issues are found.
    """
    _print(f"Scanning path: {path} ...")
    _, original_sys_path = _setup_sys_path(path)

    try:
        results = _scan_path(path, recursive=not no_recursive)
    except Exception as e:
        _print(f"Error scanning {path}: {e}", stderr=True)
        return 1
    finally:
        sys.path[:] = original_sys_path

    if not results:
        _print("No deprecated callables found.")
        return 0

    has_errors = False

    # --- check: wrapper config + chains ---
    has_invalid = any(r.invalid_args for r in results)
    has_chains = any(r.chain_type is not None for r in results)
    issues_reported = _report_issues(results)
    if issues_reported and (has_invalid or has_chains):
        has_errors = True

    # --- expiry ---
    resolved_version = version
    if resolved_version is None:
        try:
            module_name = _resolve_module_name(path)
            package_name = module_name.split(".")[0]
            from deprecate.audit import _get_package_version

            resolved_version = _get_package_version(package_name)
        except Exception:
            resolved_version = None

    if resolved_version is not None:
        try:
            expired = _check_expiry_from_results(results, resolved_version)
            if expired:
                if _HAS_RICH:
                    _report_expiry_rich(expired)
                else:
                    _report_expiry_plain(expired)
                _print(f"\n{len(expired)} expired wrapper(s) found.")
                has_errors = True
        except ImportError:
            _print(
                "Skipping expiry check: 'packaging' library not installed. "
                "Install with: pip install 'pyDeprecate[audit]'",
                stderr=True,
            )
        except ValueError as e:
            _print(
                f"Invalid version {resolved_version!r}: {e}. Pass a valid PEP 440 version with --version.",
                stderr=True,
            )
            return 1
    else:
        _print(
            "Skipping expiry check: could not determine package version. Pass --version explicitly to enable.",
            stderr=True,
        )

    if has_errors:
        _print("\nIssues were found.")
        return 0 if skip_errors else 1

    if issues_reported:
        _print("\nWarnings only — no hard errors.")
        return 0

    _print("\nAll checks passed!")
    return 0


# ---------------------------------------------------------------------------
# Backward-compatible entry point
# ---------------------------------------------------------------------------


def main(
    path: str = ".",
    skip_errors: bool = False,
) -> int:
    """Scan Python code for misconfigured ``@deprecated`` wrappers.

    Args:
        path: Path to the module or package to scan.
        skip_errors: Do not exit with error code even if issues are found.
    """
    return cmd_check(path=path, skip_errors=skip_errors)


def cli() -> None:
    """CLI entry point using jsonargparse."""
    try:
        from jsonargparse import auto_cli, set_parsing_settings
    except ImportError:
        _print(
            "The pyDeprecate CLI requires additional dependencies.\n"
            "Install them with:\n\n"
            "    pip install 'pyDeprecate[cli]'\n",
            stderr=True,
        )
        sys.exit(1)

    set_parsing_settings(parse_optionals_as_positionals=True)
    try:
        # Backward compat: if no subcommand given, default to the 'check' subcommand.
        # Unknown flags (args[0] starts with '-') are NOT rewritten — let jsonargparse
        # emit a native parse error rather than silently routing e.g. '--version' to
        # 'check --version', which would error with a confusing "unknown argument" message.
        args = sys.argv[1:]
        if not args or (
            args[0] not in _SUBCOMMANDS and args[0] not in _TOP_LEVEL_FLAGS and not args[0].startswith("-")
        ):
            args = ["check"] + args

        result = auto_cli(
            {"check": cmd_check, "expiry": cmd_expiry, "chains": cmd_chains, "all": cmd_all},
            args=args,
        )
        if isinstance(result, int):
            sys.exit(result)
    finally:
        set_parsing_settings(parse_optionals_as_positionals=False)
