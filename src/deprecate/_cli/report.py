"""Console output for the CLI: plain-text printing, the rich/plain ``_Reporter`` dispatch and the run record."""

import importlib.util
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from deprecate._pkg import _resolve_package_version, _safe_module_name
from deprecate.audit import DeprecationWrapperInfo, generate_deprecation_table


def _is_package_available(name: str) -> bool:
    """Return True if *name* is importable without actually importing it."""
    return importlib.util.find_spec(name) is not None


class _GapKind(str, Enum):
    """A part of the audit that did not run although the scan itself passed.

    Each gap is printed as an unwrapped stderr line ``pydeprecate gap: <kind>: <detail>`` — plain ``print``, never Rich,
    which wraps long lines at the terminal width, and independent of the warnings filters the audited package may
    install — and recorded for ``--report-json`` with its hint from :data:`_GAP_HINTS`.

    """

    #: A module the scan could not import (or whose module-level code exited), so none of its wrappers were audited;
    #: also ``<package>.*`` when a recursive walk of a package stopped early, leaving its submodules unaudited.
    MODULE_NOT_AUDITED = "module not audited"
    #: The package version could not be resolved, so removal deadlines were not checked.
    VERSION_UNRESOLVED = "version not resolved"
    #: The auto-detected package version is not a valid PEP 440 version, so removal deadlines were not checked.
    VERSION_INVALID = "version not PEP 440"
    #: A wrapper's ``remove_in`` is not a valid PEP 440 version, so its removal deadline was not checked.
    REMOVE_IN_INVALID = "remove_in not PEP 440"
    #: The ``packaging`` library is missing, so version-based checks were skipped.
    PACKAGING_MISSING = "packaging missing"
    #: A plain-directory scan left the Python files in its subdirectories out.
    NESTED_FILES = "nested files not audited"


#: Gap kind -> one-sentence remedy, the same for every occurrence.
_GAP_HINTS: dict[_GapKind, str] = {
    _GapKind.MODULE_NOT_AUDITED: (
        "install the scanned package's runtime dependencies in the environment that runs the scan"
    ),
    _GapKind.VERSION_UNRESOLVED: "install the scanned package in this environment or pass `--version X.Y`",
    _GapKind.VERSION_INVALID: "pass `--version X.Y` with a valid PEP 440 version",
    _GapKind.REMOVE_IN_INVALID: "set the wrapper's `remove_in` to a valid PEP 440 version such as `2.0`",
    _GapKind.PACKAGING_MISSING: "install 'pyDeprecate[audit]' to enable version-based checks",
    _GapKind.NESTED_FILES: "scan an importable package (with `__init__.py` files) instead of a plain directory",
}


@dataclass
class _ScanRecord:
    """The scan a gate subcommand ran, kept so the ``--report-json`` table shows exactly what it gated on."""

    path: str
    wrappers: list[DeprecationWrapperInfo]
    version: Optional[str] = None


@dataclass
class _RunRecord:
    """What one CLI run did, collected for ``--report-json``: its gaps, and its rendered table or its scan."""

    gaps: list[tuple[_GapKind, str]] = field(default_factory=list)
    table: Optional[str] = None
    scan: Optional[_ScanRecord] = None


#: The record of the current CLI run while :func:`_record_run` is active.
_RUN_RECORD: ContextVar[Optional[_RunRecord]] = ContextVar("_RUN_RECORD", default=None)


@contextmanager
def _record_run() -> Iterator[_RunRecord]:
    """Collect every gap, scan and rendered table of the CLI run inside the block.

    Yields:
        The record, filled while the block runs.

    """
    record = _RunRecord()
    token = _RUN_RECORD.set(record)
    try:
        yield record
    finally:
        _RUN_RECORD.reset(token)


@contextmanager
def _ensure_run_record() -> Iterator[_RunRecord]:
    """Yield the record of the current CLI run, or a private one when no run is being recorded.

    Lets a caller read back what a nested ``cmd_*`` call recorded (the rendered table) whether it was started through
    ``cli()`` or called directly, without hiding the outer run's record from ``--report-json``.

    Yields:
        The active run record, else a fresh one that is discarded when the block ends.

    """
    active = _RUN_RECORD.get()
    if active is not None:
        yield active
        return
    with _record_run() as record:
        yield record


def _run_active() -> bool:
    """Tell whether a CLI run is already being recorded, i.e. this ``cli()`` call is nested inside another one."""
    return _RUN_RECORD.get() is not None


def _record_scan(scan: _ScanRecord) -> None:
    """Keep the scan a standalone gate ran; ``all`` and ``status`` record their rendered table instead."""
    record = _RUN_RECORD.get()
    if record is not None:
        record.scan = scan


def _record_table(markdown: str) -> None:
    """Keep the deprecation table the run rendered (``status``, and ``all`` through it)."""
    record = _RUN_RECORD.get()
    if record is not None:
        record.table = markdown


def _print_gap(kind: _GapKind, detail: str) -> None:
    """Print one gap line ``pydeprecate gap: <kind>: <detail>`` to stderr, unwrapped, and record it for the run.

    Args:
        kind: What part of the audit did not run.
        detail: The human-readable message following the kind; it may continue on further lines.

    """
    print(f"pydeprecate gap: {kind.value}: {detail}", file=sys.stderr, flush=True)
    record = _RUN_RECORD.get()
    if record is not None:
        record.gaps.append((kind, detail))


def _print_skipped_modules(skipped: dict[str, str]) -> None:
    """Print one :attr:`_GapKind.MODULE_NOT_AUDITED` line per skipped module (``name: error repr``) to stderr."""
    for module_name, reason in skipped.items():
        _print_gap(_GapKind.MODULE_NOT_AUDITED, f"{module_name}: {reason}")


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


# ---------------------------------------------------------------------------
# Reporter — all rich/plain dispatch in one namespace
# ---------------------------------------------------------------------------


class _Reporter:
    _HAS_RICH: bool = _is_package_available("rich")
    _out: Any = None
    _err: Any = None
    _rich_box: Any = None
    _RichTable: Any = None
    _RichText: Any = None

    try:
        from rich import box as _rich_box_import
        from rich.console import Console as _RichConsole
        from rich.table import Table as _RichTable_import
        from rich.text import Text as _RichText_import

        _rich_box = _rich_box_import
        _RichTable = _RichTable_import
        # Rich parses square brackets in a cell as style markup, which would swallow the ``[rule-slug]``
        # prefix of a policy violation; wrapping the message in ``Text`` renders it literally.
        _RichText = _RichText_import
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
        """Render a three-column Module/Function/Detail table (rich or plain fallback).

        Every cell is wrapped in :class:`rich.text.Text`, as in :meth:`_render_message_table`, so a square-bracketed
        detail is rendered literally instead of being parsed as Rich style markup.

        """
        if _Reporter._HAS_RICH:
            table = _Reporter._make_table(title, col, title_style=title_style, col_style=col_style)
            for row in rows:
                table.add_row(*(_Reporter._RichText(cell) for cell in row))
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
    def _render_message_table(title: str, plain_prefix: str, messages: list[str], *, error: bool = True) -> None:
        """Render a single-column Message table shared by the expiry and policy reports.

        Every message is wrapped in :class:`rich.text.Text` so a ``[rule-slug]``-style prefix (used by
        policy violation messages) is rendered literally instead of being parsed as Rich markup — this
        also closes that gap for plain expiry messages, which previously passed raw strings.

        Args:
            title: Rich table title.
            plain_prefix: Header line printed above the list in the no-Rich fallback.
            messages: Message strings to render, one per row/line.
            error: Render in red (a finding that fails the run); ``False`` renders in yellow, the colour the
                other reporters use for advisory findings.

        """
        if _Reporter._HAS_RICH:
            title_style, col_style = ("bold red", "red") if error else ("bold yellow", "yellow")
            table = _Reporter._RichTable(title=title, box=_Reporter._rich_box.ROUNDED, title_style=title_style)
            table.add_column("Message", style=col_style)
            for msg in messages:
                table.add_row(_Reporter._RichText(msg))
            _Reporter._console().print(table)
        else:
            _print(f"\n{plain_prefix}")
            for msg in messages:
                _print(f"\t- {msg}")

    @staticmethod
    def expiry(expired: list[str]) -> None:
        """Report deprecated wrappers that have passed their ``remove_in`` deadline."""
        _Reporter._render_message_table(
            "Expired Deprecated Wrappers", "[ERROR] Found expired deprecated wrappers:", expired
        )

    @staticmethod
    def policy(violations: list[str], *, advisory: bool = False) -> None:
        """Report wrappers that break one of the deprecation-governance policy rules.

        ``advisory=True`` renders the same table as a ``[WARNING]`` in yellow — how ``all`` reports violations it does
        not fail on — so a reader can tell it from the red ``[ERROR]`` of the gating ``policy`` subcommand.

        """
        prefix = "[WARNING]" if advisory else "[ERROR]"
        _Reporter._render_message_table(
            "Deprecation Policy Violations",
            f"{prefix} Found deprecation policy violations:",
            violations,
            error=not advisory,
        )

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


# ---------------------------------------------------------------------------
# --report-json: the machine-readable record of one run
# ---------------------------------------------------------------------------

#: Version of the ``--report-json`` layout; bumped whenever a field changes meaning or goes away.
_REPORT_SCHEMA = 1


def _render_recorded_table(record: _RunRecord) -> Optional[str]:
    """Return the run's deprecation table: the one it rendered, else one built from its recorded scan.

    A gate that never resolved a version (``check``, ``policy``, ``chains``) gets the table ``status`` would print: the
    version is auto-detected, as there. A table that cannot be rendered is reported on stderr and left out.

    """
    if record.table is not None or record.scan is None:
        return record.table
    scan = record.scan
    module_name = _safe_module_name(scan.path)
    version = scan.version if scan.version is not None else _resolve_package_version(module_name, path=scan.path)
    try:
        return generate_deprecation_table(
            module_name, current_version=version, _wrappers=scan.wrappers, _version_explicit=False
        )
    except Exception as exc:  # a display artifact: it never fails the run, as in `cmd_all`
        _print(f"Could not render the deprecation table for the JSON report: {exc}", stderr=True)
        return None


def _write_report(path: str, record: _RunRecord) -> None:
    """Write the run's ``--report-json`` file; a failure only prints an advisory and never changes the exit code.

    Args:
        path: File to write; missing parent directories are created.
        record: Gaps, table and scan collected while the subcommand ran.

    """
    report = {
        "schema": _REPORT_SCHEMA,
        "table": _render_recorded_table(record),
        "gaps": [{"kind": kind.value, "detail": detail, "hint": _GAP_HINTS[kind]} for kind, detail in record.gaps],
    }
    out_path = Path(path)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as err:
        _print(f"Could not write the JSON report {out_path}: {err}", stderr=True)
