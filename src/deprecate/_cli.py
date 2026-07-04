"""CLI entry point for pyDeprecate validation.

Provides two entry points for scanning Python code for misconfigured ``@deprecated`` wrappers:

- ``pydeprecate <subcommand> <path>`` — console script installed via ``pip install 'pyDeprecate[cli]'``
- ``python -m deprecate <subcommand> <path>`` — module invocation of the same CLI

Subcommands:
    check   — Validate wrapper configuration and flag misconfigured, chain-forming, or positional-only-arg wrappers.
    expiry  — Check for deprecated wrappers that have passed their scheduled ``remove_in`` deadline.
    chains  — Detect deprecated wrappers whose ``target`` is itself a deprecated callable.
    all     — Run all three checks in a single scan pass.
    status  — Render a markdown deprecation table to stdout (and optionally save it to a file).

"""

import contextlib
import functools
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from deprecate._pkg import (
    _auto_detect_version,
    _find_child_packages,
    _is_package_dir,
    _managed_sys_path,
    _resolve_module_name,
    _safe_module_name,
)
from deprecate.audit import (
    DeprecationWrapperInfo,
    TableStyle,
    _check_expiry_for_callables,
    find_deprecation_wrappers,
    generate_deprecation_table,
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


def _scan_directory(path: str, include_members: bool = True) -> list[DeprecationWrapperInfo]:
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
                results.extend(find_deprecation_wrappers(module_name, recursive=False, include_members=include_members))
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


def _scan_path(path: str, recursive: bool = True, include_members: bool = True) -> list[DeprecationWrapperInfo]:
    """Scan a directory or importable module/package name for deprecated wrappers.

    File paths are not accepted because ``find_deprecation_wrappers()`` expects an importable module or package name,
    not a filesystem path.

    """
    pth: Path = Path(path)
    if pth.is_dir():
        if _is_package_dir(pth):
            # package dir: resolve importable name from directory stem
            return find_deprecation_wrappers(
                Path(path).resolve().name, recursive=recursive, include_members=include_members
            )
        # Flat src-layout or project root: find package in direct children or src/ subdir.
        child_pkgs = _find_child_packages(pth)
        if len(child_pkgs) == 1:
            return _scan_path(str(child_pkgs[0]), recursive=recursive, include_members=include_members)
        return _scan_directory(path, include_members=include_members)
    if pth.is_file():
        raise ValueError(
            f"File paths are not supported: {path!r}. Pass an importable module/package name or a directory instead."
        )
    return find_deprecation_wrappers(path, recursive=recursive, include_members=include_members)


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
        from rich import box as _rich_box_import
        from rich.console import Console as _RichConsole
        from rich.table import Table as _RichTable_import

        _rich_box = _rich_box_import
        _RichTable = _RichTable_import
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
    def identity_args_mappings(items: list[DeprecationWrapperInfo]) -> None:
        """Report wrappers whose ``args_mapping`` maps an argument to itself."""
        _Reporter._render_table(
            "Identity Argument Mappings (arg -> arg)",
            "Identity Args",
            title_style="bold yellow",
            col_style="yellow",
            rows=[(r.module, r.function, ", ".join(r.identity_args_mapping)) for r in items],
            plain_prefix="[WARNING] Found functions with identity argument mappings (arg -> arg):",
        )

    @staticmethod
    def no_effect(items: list[DeprecationWrapperInfo]) -> None:
        """Report wrappers that have no observable effect on callers."""

        def _reasons(r: DeprecationWrapperInfo) -> str:
            """Format the no-effect reasons for a single wrapper as a comma-separated string."""
            parts = []
            if r.empty_args_mapping:
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
    def positional_only_args(items: list[DeprecationWrapperInfo]) -> None:
        """Report wrappers whose ``args_mapping`` remaps a kwarg to a POSITIONAL_ONLY constructor parameter."""
        _Reporter._render_table(
            "Positional-Only Constructor Args in args_mapping",
            "Positional-Only Args",
            title_style="bold yellow",
            col_style="yellow",
            rows=[(r.module, r.function, ", ".join(r.args_mapping_positional_only)) for r in items],
            plain_prefix=(
                "[WARNING] Found args_mapping entries targeting POSITIONAL_ONLY constructor parameters"
                " (proxy falls back to setattr for these entries):"
            ),
        )

    @staticmethod
    def issues(results: list[DeprecationWrapperInfo], *, error_on_chains: bool = False) -> bool:
        """Print categorised diagnostics and return whether any issues were found."""
        invalid_args = [r for r in results if r.invalid_args]
        identity_args_mappings = [r for r in results if r.identity_args_mapping]
        no_effect = [r for r in results if r.no_effect]
        chains = [r for r in results if r.chain_type is not None]
        positional_only = [r for r in results if r.args_mapping_positional_only]

        if not (invalid_args or identity_args_mappings or no_effect or chains or positional_only):
            return False

        if invalid_args:
            _Reporter.invalid_args(invalid_args)
        if identity_args_mappings:
            _Reporter.identity_args_mappings(identity_args_mappings)
        if no_effect:
            _Reporter.no_effect(no_effect)
        if chains:
            _Reporter.chains(chains, error=error_on_chains)
        if positional_only:
            _Reporter.positional_only_args(positional_only)

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
            return None
        if getattr(exc, "name", None) is not None:
            # Python's import system sets ``exc.name`` when a module-level ``import`` statement
            # fails — this is a broken import inside the user's package, not a version-detection
            # failure.  Propagate so the caller gets the real error instead of the misleading
            # "Could not determine version" message.
            raise
        _print(
            "Could not determine the current package version automatically.\n"
            "Pass --version explicitly, or ensure the package is installed and importable.\n\n"
            f"Original error: {exc}",
            stderr=True,
        )
        return None
    # other exceptions propagate to cli()'s top-level handler around fire.Fire


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


def _print_scan_header(path: str, version: Optional[str] = None, *, user_provided: bool = False) -> None:
    """Print a consistent scan header: scanning location, package name, and version."""
    module = _safe_module_name(path)
    _print(f"Scanning: {path}")
    if version is not None:
        source = "user-provided" if user_provided else "auto-detected"
        _print(f"Package: {module}  Version: {version} ({source})")
    else:
        _print(f"Package: {module}")


def cmd_check(
    path: str = ".",
    recursive: bool = True,
    exit_zero: bool = False,
    *,
    _wrappers: Optional[list[DeprecationWrapperInfo]] = None,
) -> int:
    """Scan Python code for misconfigured ``@deprecated`` wrappers and deprecation chains.

    Reports invalid argument mappings (exit 1), identity mappings, no-effect wrappers,
    deprecation chains, and ``args_mapping`` entries targeting POSITIONAL_ONLY constructor
    parameters (all advisory warnings only, exit 0). Use the ``chains`` subcommand for a
    dedicated hard-error chain check.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        exit_zero: Always exit 0 even if hard errors (invalid argument mappings) are found.
            Useful for advisory CI steps that should report but never block.
        _wrappers: Pre-scanned wrapper list. When provided, skips the scan step. Underscore
            prefix hides this parameter from the Fire CLI (internal use by ``cmd_all`` only).

    Returns:
        0 on success or advisory-only issues; 1 when hard errors are found and ``exit_zero`` is False.

    """
    if _wrappers is None:
        _print_scan_header(path)
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
    return 1 if not exit_zero and has_invalid else 0


def cmd_expiry(
    path: str = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    exit_zero: bool = False,
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
        exit_zero: Always exit 0 even if expired wrappers are found.
            Useful for advisory CI steps that should report but never block.
        _wrappers: Pre-scanned wrapper list. When provided, skips the scan step and derives
            expired wrappers via ``_check_expiry_for_callables``. Requires *version* to be
            non-``None`` when set. Underscore prefix hides this parameter from the Fire CLI
            (internal use by ``cmd_all`` only).

    Returns:
        0 on success or when the ``packaging`` library is unavailable; 1 when expired
        wrappers are found and ``exit_zero`` is False.

    """
    # Fire auto-converts numeric-looking strings (e.g. "1.0" → float); normalise to str.
    if version is not None:
        version = str(version)
    if _wrappers is None:
        # Standalone path: full scan + version auto-detect inside _do_expiry.
        resolved_version = version if version is not None else _auto_detect_version(_safe_module_name(path), path=path)
        if resolved_version is None and version is None:
            _print(
                "Could not resolve the current package version automatically and no --version was given; "
                "the expiry check runs without a resolved version and its result may be unreliable. "
                "Pass --version explicitly for a definitive check.",
                stderr=True,
            )
        _print_scan_header(path, resolved_version, user_provided=version is not None)
        with _managed_sys_path(path):
            raw = _do_expiry(path, resolved_version, recursive)
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
    return 0 if exit_zero else 1


def cmd_chains(
    path: str = ".",
    recursive: bool = True,
    exit_zero: bool = False,
    *,
    _wrappers: Optional[list[DeprecationWrapperInfo]] = None,
) -> int:
    """Detect deprecated wrappers whose ``target`` is itself a deprecated callable (chains).

    Two chain kinds are detected: ``target`` (forwarding chain to another deprecated
    callable) and ``stacked`` (composed argument mappings that should be collapsed).

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        exit_zero: Always exit 0 even if chains are found.
            Useful for advisory CI steps that should report but never block.
        _wrappers: Pre-scanned wrapper list. When provided, skips the scan step and filters
            for ``chain_type is not None`` internally. Underscore prefix hides this parameter
            from the Fire CLI (internal use by ``cmd_all`` only).

    Returns:
        0 when no chains are found or ``exit_zero`` is True; 1 when chains are found.

    """
    if _wrappers is None:
        _print_scan_header(path)
        with _managed_sys_path(path):
            _wrappers = validate_deprecation_chains(_resolve_module_name(path), recursive=recursive)
    chains = [r for r in _wrappers if r.chain_type is not None]
    if not chains:
        _print("No deprecation chains found.")
        return 0
    _Reporter.chains(chains, error=True)
    _print(f"\n{len(chains)} deprecation chain(s) found.")
    return 0 if exit_zero else 1


def cmd_all(
    path: str = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    exit_zero: bool = False,
) -> int:
    """Run all three checks then append a deprecation table.

    Performs a single scan pass and distributes the wrappers to ``cmd_check``,
    ``cmd_expiry``, and ``cmd_chains`` so the filesystem is only traversed once.
    After all three checks complete, ``cmd_status`` is always called to append a
    compact markdown deprecation table to the output.
    Version is auto-detected from installed package metadata when not provided.
    A missing ``packaging`` library skips the expiry check with a warning and does not
    count as a hard error.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for expiry comparison (e.g. ``"2.0.0"``).
            Auto-detected from installed package metadata if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        exit_zero: Always exit 0 even if issues are found.
            Useful for advisory CI steps that should report but never block.

    Returns:
        0 when all checks pass or ``exit_zero`` is True; 1 when any hard error is found.
        The deprecation table is always appended regardless of pass/fail outcome.

    """
    if version is not None:
        version = str(version)
    version_path = path if Path(path).exists() else None
    resolved_version = (
        version if version is not None else _auto_detect_version(_safe_module_name(path), path=version_path)
    )
    _print_scan_header(path, resolved_version, user_provided=version is not None)
    with _managed_sys_path(path):
        wrappers = _scan_path(path, recursive=recursive)

    # Sub-commands run with exit_zero=False so cmd_all sees their truthful exit codes;
    # the user-facing --exit-zero is applied to the aggregate below.
    check_code = cmd_check(path, recursive=recursive, exit_zero=False, _wrappers=wrappers)
    expiry_code = cmd_expiry(path, version=resolved_version, recursive=recursive, exit_zero=False, _wrappers=wrappers)
    chains_code = cmd_chains(path, recursive=recursive, exit_zero=False, _wrappers=wrappers)

    # The status table is a display artifact appended after the three gates. Render it defensively:
    # a table-rendering failure must never change the aggregate exit code the three checks produced.
    try:
        cmd_status(path, version=resolved_version, recursive=recursive, _wrappers=wrappers)
    except Exception as exc:
        _print(f"Could not render the deprecation table: {exc}", stderr=True)

    has_errors = bool(check_code or expiry_code or chains_code)
    return 0 if not has_errors or exit_zero else 1


def cmd_status(
    path: str = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    style: str = "compact",
    include_members: bool = True,
    output: Optional[str] = None,
    *,
    _wrappers: Optional[list[DeprecationWrapperInfo]] = None,
) -> int:
    """Print a markdown deprecation status table to stdout.

    Scans the target package for deprecated wrappers and renders their lifecycle
    status as a Markdown table. Standalone — runs no checks, always exits 0.
    When ``--output`` is given, the table is also written to that file.

    Args:
        path: Path to the module, package directory, or importable module name to scan.
        version: Current package version for lifecycle status (e.g. ``"2.0.0"``).
            Auto-detected from installed package metadata if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--norecursive`` to scan top-level only.
        style: Table format — ``compact`` (default) or ``matrix``.
        include_members: Include deprecated class members such as methods and constructors (default True).
        output: Optional file path to write the markdown table. The table is always
            printed to stdout regardless of this flag.

    Returns:
        Always 0 — status table generation is not a pass/fail gate.

    """
    if version is not None:
        version = str(version)
    try:
        table_style = TableStyle(style)
    except ValueError:
        valid = ", ".join(s.value for s in TableStyle)
        _print(f"Invalid style {style!r}; falling back to 'compact'. Expected one of: {valid}.", stderr=True)
        table_style = TableStyle.COMPACT

    # Never resolve the importable module name eagerly: status "always exits 0" (it is not a
    # pass/fail gate), so a plain directory without ``__init__.py`` — which ``check`` scans fine and
    # for which ``_wrappers`` is already supplied by ``cmd_all`` — must not raise here. ``_safe_module_name``
    # falls back to the raw path, used only as table metadata for version resolution.
    module_name = _safe_module_name(path)
    resolved_version = version if version is not None else _auto_detect_version(module_name, path=path)
    if _wrappers is None:
        _print_scan_header(path, resolved_version, user_provided=version is not None)
        with _managed_sys_path(path):
            _wrappers = _scan_path(path, recursive=recursive, include_members=include_members)
    markdown = generate_deprecation_table(
        module_name,
        current_version=resolved_version,
        style=table_style,
        _wrappers=_wrappers,
    )

    if _Reporter._HAS_RICH:
        from rich.markdown import Markdown

        _Reporter._console().print(Markdown(markdown))
    else:
        _print(markdown)

    if output is not None:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
        _print(f"→ Saved to: {out_path}", stderr=True)

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _ensure_utf8_streams() -> None:
    """Reconfigure stdout/stderr to UTF-8 on platforms where the default encoding may reject non-ASCII characters.

    On Windows the default console codec (``charmap``) cannot encode Unicode emoji used by
    :class:`~deprecate.audit.DeprecationStatus`.  Calling ``reconfigure`` before any output is written ensures emoji
    reach the terminal (or CI log) without a :exc:`UnicodeEncodeError`.

    The call is a no-op when the streams are already UTF-8 or when they lack a ``reconfigure`` method (binary streams,
    pytest capture wrappers).

    """
    for stream in (sys.stdout, sys.stderr):
        # ``encoding`` may be *present but None* (e.g. some redirected/binary-ish wrappers); ``getattr`` with a
        # default only covers a missing attribute, so guard the ``None`` case with ``or "utf-8"`` before ``.lower()``.
        encoding = (getattr(stream, "encoding", None) or "utf-8").lower()
        if hasattr(stream, "reconfigure") and encoding != "utf-8":
            with contextlib.suppress(Exception):
                stream.reconfigure(encoding="utf-8")


def cli() -> None:
    """CLI entry point for pydeprecate.

    The subcommand's integer return value becomes the process exit code, applied *after* ``fire.Fire``
    returns. Raising ``SystemExit`` from inside the Fire trace would abort Fire's own unconsumed-argument
    check, silently ignoring unknown or misspelled flags (exit 0 on typos); letting the trace complete
    makes Fire report ``Could not consume arg`` and exit 2 for such flags. Acts as the single top-level
    exception handler: unhandled exceptions from subcommands are converted to a non-zero exit with the
    exception type and message (the type prefix guarantees a non-blank stderr line even when the exception
    carries no message). SystemExit (Fire's ``--help`` and error exits) passes through unchanged.

    A path whose name collides with a subcommand (``check``, ``expiry``, ``chains``, ``all``, ``status``) is
    treated as that subcommand by the implicit-``check`` shim. Scan such a path explicitly, e.g.
    ``pydeprecate check ./check``, so the leading token is the subcommand and the path is its argument.

    """
    _ensure_utf8_streams()
    try:
        import fire
    except ImportError:
        sys.exit("The 'pydeprecate' CLI requires the 'fire' package.\nInstall it with: pip install 'pyDeprecate[cli]'")

    subcommands = {"check", "expiry", "chains", "all", "status"}
    argv = sys.argv[1:]
    if argv and argv[0] not in subcommands and argv[0] not in {"-h", "--help"}:
        argv = ["check", *argv]

    exit_code: Optional[int] = None

    def _capture(fn: Callable[..., int]) -> Callable[..., None]:
        """Record the cmd_* return code without returning it into the Fire trace.

        Returning the int would make Fire print it and treat it as a further component to consume arguments against;
        returning ``None`` keeps the output clean and lets Fire finish its trace.

        """

        @functools.wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> None:
            nonlocal exit_code
            exit_code = fn(*args, **kwargs)

        return wrapper

    try:
        fire.Fire(
            {
                "check": _capture(cmd_check),
                "expiry": _capture(cmd_expiry),
                "chains": _capture(cmd_chains),
                "all": _capture(cmd_all),
                "status": _capture(cmd_status),
            },
            command=argv,
        )
    except Exception as exc:  # SystemExit is BaseException — Fire's own exits pass through untouched
        # Prefix the exception type so an exception with an empty message still produces a non-blank stderr
        # line (bare ``str(exc)`` on such exceptions exited 1 with nothing printed).
        sys.exit(f"{type(exc).__name__}: {exc}")
    # exit_code stays None when no subcommand ran (bare `pydeprecate` help) → return normally (exit 0).
    if exit_code is not None:
        sys.exit(exit_code)
