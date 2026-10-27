"""Console output for the CLI: plain-text printing, the ``_Reporter`` finding sections and the run record."""

import importlib.util
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from deprecate._cli._pkg import _resolve_package_version, _safe_module_name
from deprecate.audit import DeprecationWrapperInfo, generate_deprecation_table
from deprecate.audit._findings import _Finding, _misconfiguration


def _is_package_available(name: str) -> bool:
    """Return True if *name* is importable without actually importing it."""
    return importlib.util.find_spec(name) is not None


class _GapKind(str, Enum):
    """A part of the audit that did not run although the scan itself passed.

    Each gap is printed as an unwrapped stderr line ``pydeprecate gap: <kind>: <detail>`` — plain ``print``, independent
    of the warnings filters the audited package may install — and recorded for ``--report-json`` with its hint from
    :data:`_GAP_HINTS`.

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
    #: The scanned package's import name was already taken by a copy from another directory, so that copy was audited.
    OTHER_COPY = "other copy audited"


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
    _GapKind.OTHER_COPY: (
        "run the scan in an environment where the package is not already imported from elsewhere, such as the"
        " checkout's own virtual environment"
    ),
}


@dataclass
class _ScanRecord:
    """The scan a gate subcommand ran, kept so the ``--report-json`` table shows exactly what it gated on."""

    path: str
    wrappers: list[DeprecationWrapperInfo]
    version: Optional[str] = None


@dataclass
class _RunRecord:
    """What one CLI run did: its gaps and findings, and its rendered table or its scan for ``--report-json``."""

    gaps: list[tuple[_GapKind, str]] = field(default_factory=list)
    table: Optional[str] = None
    scan: Optional[_ScanRecord] = None
    #: Every finding the run's checks derived; ``all`` computes its exit code from those of its sub-checks.
    findings: list[_Finding] = field(default_factory=list)


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


def _record_findings(findings: list[_Finding]) -> None:
    """Keep the findings a check derived, for the exit code of ``all`` that ran it."""
    record = _RUN_RECORD.get()
    if record is not None:
        record.findings.extend(findings)


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
    """Print a message to stdout, or to stderr when *stderr* is ``True``.

    Args:
        msg: The message to print.
        stderr: If ``True``, send the message to *stderr* instead of *stdout*.

    """
    print(msg, file=sys.stderr if stderr else sys.stdout)


# ---------------------------------------------------------------------------
# Reporter — the finding sections of the console output
# ---------------------------------------------------------------------------


class _Reporter:
    """Print each kind of finding as a section: a ``[ERROR]``/``[WARNING]`` header line, then one line per item."""

    @staticmethod
    def _render_table(plain_prefix: str, rows: list[tuple[str, str, str]]) -> None:
        """Print a section of ``module.function: detail`` lines under its header line."""
        _print(f"\n{plain_prefix}")
        for mod, fn, detail in rows:
            _print(f"\t- {mod}.{fn}: {detail}")

    @staticmethod
    def invalid_args(items: list[DeprecationWrapperInfo]) -> None:
        """Report wrappers with invalid ``args_mapping`` keys."""
        _Reporter._render_table(
            "[ERROR] Found functions with invalid argument mappings:",
            [(r.module, r.function, ", ".join(r.invalid_args)) for r in items],
        )

    @staticmethod
    def misconfigured_targets(items: list[DeprecationWrapperInfo]) -> None:
        """Report wrappers whose ``target`` contradicts the rest of their configuration."""
        _Reporter._render_table(
            "[ERROR] Found deprecated wrappers with a misconfigured target:",
            [(r.module, r.function, _misconfiguration(r)) for r in items],
        )

    @staticmethod
    def inner_order_properties(items: list[DeprecationWrapperInfo]) -> None:
        """Report inner-order ``@property @deprecated`` wrappers, whose setter and deleter never warn."""
        _Reporter._render_table(
            "[ERROR] Found inner-order @property @deprecated wrappers (setter/deleter unprotected):",
            [(r.module, r.function, "use @deprecated(...) above @property") for r in items],
        )

    @staticmethod
    def identity_args_mappings(items: list[DeprecationWrapperInfo]) -> None:
        """Report wrappers whose ``args_mapping`` maps an argument to itself."""
        _Reporter._render_table(
            "[WARNING] Found functions with identity argument mappings (arg -> arg):",
            [(r.module, r.function, ", ".join(r.identity_args_mapping)) for r in items],
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
            "[WARNING] Found deprecated wrappers with NO EFFECT (zero impact):",
            [(r.module, r.function, _reasons(r)) for r in items],
        )

    @staticmethod
    def chains(items: list[DeprecationWrapperInfo], *, error: bool = False) -> None:
        """Report deprecated-to-deprecated forwarding chains; ``error=True`` reports them as ``[ERROR]``."""
        prefix = "[ERROR]" if error else "[WARNING]"
        _Reporter._render_table(
            f"{prefix} Found deprecated wrappers forming deprecation chains:",
            [(r.module, r.function, r.chain_type.value if r.chain_type is not None else "") for r in items],
        )

    @staticmethod
    def _render_message_table(plain_prefix: str, messages: list[str]) -> None:
        """Print a section of whole-message lines under its header line, shared by the expiry and policy reports."""
        _print(f"\n{plain_prefix}")
        for msg in messages:
            _print(f"\t- {msg}")

    @staticmethod
    def expiry(expired: list[str]) -> None:
        """Report deprecated wrappers that have passed their ``remove_in`` deadline."""
        _Reporter._render_message_table("[ERROR] Found expired deprecated wrappers:", expired)

    @staticmethod
    def policy(violations: list[str], *, advisory: bool = False) -> None:
        """Report wrappers that break one of the deprecation-governance policy rules.

        ``advisory=True`` reports the same lines as a ``[WARNING]`` — how ``all`` reports violations it does not fail on
        — so a reader can tell it from the ``[ERROR]`` of the gating ``policy`` subcommand.

        """
        prefix = "[WARNING]" if advisory else "[ERROR]"
        _Reporter._render_message_table(f"{prefix} Found deprecation policy violations:", violations)

    @staticmethod
    def positional_only_args(items: list[DeprecationWrapperInfo]) -> None:
        """Report wrappers whose ``args_mapping`` remaps a kwarg to a POSITIONAL_ONLY constructor parameter."""
        _Reporter._render_table(
            "[WARNING] Found args_mapping entries targeting POSITIONAL_ONLY constructor parameters"
            " (proxy falls back to setattr for these entries):",
            [(r.module, r.function, ", ".join(r.args_mapping_positional_only)) for r in items],
        )

    @staticmethod
    def issues(results: list[DeprecationWrapperInfo], *, error_on_chains: bool = False) -> bool:
        """Print categorised diagnostics and return whether any issues were found."""
        invalid_args = [r for r in results if r.invalid_args]
        misconfigured = [r for r in results if r.misconfigured_target]
        inner_order = [r for r in results if r.inner_order_property]
        identity_args_mappings = [r for r in results if r.identity_args_mapping]
        no_effect = [r for r in results if r.no_effect]
        chains = [r for r in results if r.chain_type is not None]
        positional_only = [r for r in results if r.args_mapping_positional_only]

        if not (
            invalid_args
            or misconfigured
            or inner_order
            or identity_args_mappings
            or no_effect
            or chains
            or positional_only
        ):
            return False

        if invalid_args:
            _Reporter.invalid_args(invalid_args)
        if misconfigured:
            _Reporter.misconfigured_targets(misconfigured)
        if inner_order:
            _Reporter.inner_order_properties(inner_order)
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
