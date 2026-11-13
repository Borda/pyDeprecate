"""Console output for the CLI: plain-text printing, the ``_Reporter`` finding sections and the run record."""

import importlib.util
import json
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePath
from typing import Any, NamedTuple, Optional

from deprecate import __about__
from deprecate._cli._pkg import _safe_module_name
from deprecate._cli.findings import _GATE_ORDER, _LEVEL_RANK, _GateReason, _Level, _level
from deprecate._cli.suppress import _Suppressor
from deprecate.audit import DeprecationWrapperInfo, TableStyle
from deprecate.audit._findings import _Finding, _Rule
from deprecate.audit._report import _render_deprecation_table, _resolve_table_version
from deprecate.audit._version import _resolve_package_version


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
    #: A wrapper's ``remove_in`` is not a valid PEP 440 version, so its removal deadline (``expiry``) or its grace
    #: window (``policy``'s ``min-grace``) was not checked; ``all`` names each wrapper once.
    REMOVE_IN_INVALID = "remove_in not PEP 440"
    #: A wrapper's ``deprecated_in`` is not a valid PEP 440 version, so its grace window (``min-grace``) was not
    #: checked.
    DEPRECATED_IN_INVALID = "deprecated_in not PEP 440"
    #: A forward epoch change makes the grace window unmeasurable.
    EPOCH_CHANGE = "epoch change"
    #: The ``packaging`` library is missing, so version-based checks were skipped.
    PACKAGING_MISSING = "packaging missing"
    #: A plain-directory scan left the Python files in its subdirectories out.
    NESTED_FILES = "nested files not audited"
    #: The scanned package's import name was already taken by a copy from another directory, so that copy was audited.
    OTHER_COPY = "other copy audited"
    #: ``--static`` could not read a wrapper, or one of the settings a rule needs, from the source alone.
    STATIC_UNRESOLVED = "not statically resolvable"


#: Gap kind -> one-sentence remedy, the same for every occurrence.
_GAP_HINTS: dict[_GapKind, str] = {
    _GapKind.MODULE_NOT_AUDITED: (
        "install the scanned package's runtime dependencies in the environment that runs the scan"
    ),
    _GapKind.VERSION_UNRESOLVED: "install the scanned package in this environment or pass `--version X.Y`",
    _GapKind.VERSION_INVALID: "pass `--version X.Y` with a valid PEP 440 version",
    _GapKind.REMOVE_IN_INVALID: "set the wrapper's `remove_in` to a valid PEP 440 version such as `2.0`",
    _GapKind.DEPRECATED_IN_INVALID: "set the wrapper's `deprecated_in` to a valid PEP 440 version such as `1.0`",
    _GapKind.EPOCH_CHANGE: "use versions in the same epoch to make the grace window measurable",
    _GapKind.PACKAGING_MISSING: "install 'pyDeprecate[audit]' to enable version-based checks",
    _GapKind.NESTED_FILES: "scan an importable package (with `__init__.py` files) instead of a plain directory",
    _GapKind.OTHER_COPY: (
        "run the scan in an environment where the package is not already imported from elsewhere, such as the"
        " checkout's own virtual environment"
    ),
    _GapKind.STATIC_UNRESOLVED: "run without `--static` for a full audit, or use literal version strings",
}


class _Gap(NamedTuple):
    """One part of the audit that did not run, and the scan path that raised it (``None`` outside any scan)."""

    kind: _GapKind
    detail: str
    path: Optional[str] = None
    #: ``(module, symbol)`` of the one wrapper the gap left unchecked, as its finding ids name it; ``None`` for a gap
    #: that names no single wrapper. Never written to the report.
    subject: Optional[tuple[str, str]] = None


class _SuppressedFinding(NamedTuple):
    """A finding an ignore entry or the baseline suppressed, with its scan path and what suppressed it."""

    finding: _Finding
    path: Optional[str]
    #: ``ignore`` or ``baseline``.
    kind: str
    #: The ignore entry, or the baseline id, that matched.
    entry: str


@dataclass
class _ScanRecord:
    """The scan a gate subcommand ran, kept so the ``--report-json`` table shows exactly what it gated on."""

    path: str
    wrappers: list[DeprecationWrapperInfo]
    version: Optional[str] = None
    #: Read from source with ``ast`` (``--static``) rather than imported; the report table must not import it either.
    static: bool = False
    #: Directory holding the top-level package of a static scan, where its version is read from source.
    import_root: Optional[Path] = None


@dataclass
class _ScannedPath:
    """One scan root of the run, with the package it names and the version it was audited at."""

    path: str
    package: str
    version: Optional[str] = None
    #: ``user-provided``, ``auto-detected``, or ``None`` when no version was resolved.
    version_source: Optional[str] = None


@dataclass
class _RunRecord:
    """What one CLI run did: its gaps and findings, and its rendered table or its scan for ``--report-json``."""

    gaps: list[_Gap] = field(default_factory=list)
    #: The run's deprecation table: the one table of a single path, else every path's table under its package heading.
    table: Optional[str] = None
    #: The last scan a gate recorded; :attr:`scans` holds every one of a run over several paths.
    scan: Optional[_ScanRecord] = None
    scans: list[_ScanRecord] = field(default_factory=list)
    #: Each table the run rendered, with the package it belongs to, in path order; :attr:`table` joins them.
    tables: list[tuple[str, str]] = field(default_factory=list)
    #: Every finding the run's checks derived; ``all`` computes its exit code from those of its sub-checks.
    findings: list[_Finding] = field(default_factory=list)
    #: The scan path each entry of :attr:`findings` came from, index for index.
    finding_paths: list[Optional[str]] = field(default_factory=list)
    #: The subcommand ``cli()`` ran (``None`` for a direct ``cmd_*`` call).
    command: Optional[str] = None
    #: Whether ``cli()`` will write a ``--report-json`` file, so the scan records the version even for checks
    #: that do not compare against one.
    wants_report: bool = False
    #: The scan root being audited right now; gaps and findings recorded meanwhile carry it.
    current_path: Optional[str] = None
    scanned: list[_ScannedPath] = field(default_factory=list)
    #: Effective options of the run, as the report lists them: ``strict``, ``exit_zero``, ``gates``, ``recursive``,
    #: ``exclude``.
    options: dict[str, Any] = field(default_factory=dict)
    #: Why the gate failed before ``--exit-zero`` (empty when it passed or the subcommand is no gate).
    gate_reasons: list[_GateReason] = field(default_factory=list)
    #: The run's ignore entries and baseline; ``None`` suppresses nothing.
    suppressor: Optional[_Suppressor] = None
    #: Findings the suppressor removed; never printed, counted or gated on.
    suppressed: list[_SuppressedFinding] = field(default_factory=list)
    #: Ignore entries that matched no finding, and baseline ids of an evaluated rule that matched none.
    ignore_unmatched: int = 0
    baseline_unmatched: int = 0
    #: The checks ``--gate`` added for each scan path (``all`` only); a finding's level in the report follows its path.
    gates_by_path: dict[Optional[str], frozenset[str]] = field(default_factory=dict)


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
    """Keep a scan a standalone gate ran; ``all`` and ``status`` record their rendered table instead."""
    record = _RUN_RECORD.get()
    if record is not None:
        record.scan = scan
        record.scans.append(scan)


def _record_findings(findings: list[_Finding]) -> list[_Finding]:
    """Keep the findings a check derived, each with the scan path it came from, and return the ones to report.

    Findings the run's ignore list or baseline names are set aside as suppressed instead; the returned findings, the
    active ones, are what the check prints, counts and gates on.

    """
    record = _RUN_RECORD.get()
    if record is None:
        return findings
    active = []
    for finding in findings:
        match = record.suppressor.match(finding) if record.suppressor is not None else None
        if match is None:
            active.append(finding)
        else:
            record.suppressed.append(_SuppressedFinding(finding, record.current_path, *match))
    record.findings.extend(active)
    record.finding_paths.extend([record.current_path] * len(active))
    return active


def _record_scanned_path(path: str, version: Optional[str], *, user_provided: bool) -> None:
    """Keep one scan root of the run and the version it was audited at, for the report's ``paths`` and ``versions``."""
    record = _RUN_RECORD.get()
    if record is None:
        return
    source = None if version is None else ("user-provided" if user_provided else "auto-detected")
    record.scanned.append(_ScannedPath(path, _safe_module_name(path), version, source))


def _merge_option(name: str, current: Any, value: Any) -> Any:  # noqa: ANN401
    """Return the run-level value of option *name* after one more path resolved it to *value*.

    ``strict`` is on when any path is strict, ``gates`` is the union in report order and ``exclude`` the union in path
    order; any other option keeps the latest value.

    Examples:
        >>> _merge_option("strict", True, False), _merge_option("exclude", ["a"], ["b", "a"])
        (True, ['a', 'b'])
        >>> _merge_option("gates", ["chains"], ["policy"])
        ['policy', 'chains']

    """
    if current is None:
        return value
    if name == "strict":
        return bool(current or value)
    if name == "exclude":
        return [*current, *(item for item in value if item not in current)]
    if name == "gates":
        return [check for check in _GATE_ORDER if check in current or check in value]
    return value


def _record_options(**options: Any) -> None:  # noqa: ANN401
    """Merge resolved options into the run's ``options``, as the report lists them, across every scanned path."""
    record = _RUN_RECORD.get()
    if record is None:
        return
    for name, value in options.items():
        record.options[name] = _merge_option(name, record.options.get(name), value)


def _join_tables(tables: list[tuple[str, str]]) -> Optional[str]:
    """Join the deprecation tables of a run: one path's table as it is, several each under a ``### <package>`` heading.

    Examples:
        >>> _join_tables([("pkg", "| A |")]), _join_tables([])
        ('| A |', None)
        >>> print(_join_tables([("pkg_a", "| A |"), ("pkg_b", "| B |")]))
        ### pkg_a
        <BLANKLINE>
        | A |
        <BLANKLINE>
        ### pkg_b
        <BLANKLINE>
        | B |

    """
    if len(tables) <= 1:
        return tables[0][1] if tables else None
    return "\n\n".join(f"### {package}\n\n{markdown}" for package, markdown in tables)


def _unrendered_table(error: Exception) -> str:
    """Return the note that takes the place of a table that could not be rendered in a run over several paths.

    The package keeps its ``### <package>`` heading with this note under it, so the tables of the other packages stay
    labelled and the missing one shows.

    Examples:
        >>> _unrendered_table(RuntimeError("boom"))
        '_The deprecation table could not be rendered: boom_'

    """
    return f"_The deprecation table could not be rendered: {error}_"


def _record_table(markdown: str, package: str = "") -> None:
    """Keep a deprecation table the run rendered (``status``, and ``all`` through it), one per scanned path."""
    record = _RUN_RECORD.get()
    if record is not None:
        record.tables.append((package, markdown))
        record.table = _join_tables(record.tables)


def _print_gap(kind: _GapKind, detail: str, *, subject: Optional[tuple[str, str]] = None) -> None:
    """Print one gap line ``pydeprecate gap: <kind>: <detail>`` to stderr, unwrapped, and record it for the run.

    Args:
        kind: What part of the audit did not run.
        detail: The human-readable message following the kind; it may continue on further lines.
        subject: ``(module, symbol)`` of the one wrapper the gap left unchecked, so a baseline id of that wrapper is not
            taken for a stale one; ``None`` when the gap names no single wrapper.

    """
    print(f"pydeprecate gap: {kind.value}: {detail}", file=sys.stderr, flush=True)
    record = _RUN_RECORD.get()
    if record is not None:
        record.gaps.append(_Gap(kind, detail, record.current_path, subject))


def _gap_recorded(kind: _GapKind, detail_prefix: str) -> bool:
    """Tell whether the run already recorded a *kind* gap for the scan path in progress whose detail starts so.

    Lets a second check that skips the same wrapper for the same reason (``all`` runs ``expiry`` and ``policy`` on one
    scan) leave the gap the first check printed as the only one. Without an active run record nothing was recorded.

    """
    record = _RUN_RECORD.get()
    if record is None:
        return False
    return any(
        gap.kind is kind and gap.path == record.current_path and gap.detail.startswith(detail_prefix)
        for gap in record.gaps
    )


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


#: Header of each ``check`` section, in the order the console prints them; the chain header is built per call.
_SECTION_HEADERS: dict[_Rule, str] = {
    _Rule.INVALID_ARGS: "[ERROR] Found functions with invalid argument mappings:",
    _Rule.MISCONFIGURED_TARGET: "[ERROR] Found deprecated wrappers with a misconfigured target:",
    _Rule.INNER_ORDER_PROPERTY: (
        "[ERROR] Found inner-order @property @deprecated wrappers (setter/deleter unprotected):"
    ),
    _Rule.IDENTITY_MAPPING: "[WARNING] Found functions with identity argument mappings (arg -> arg):",
    _Rule.NO_EFFECT: "[WARNING] Found deprecated wrappers with NO EFFECT (zero impact):",
    _Rule.CHAIN: "Found deprecated wrappers forming deprecation chains:",
    _Rule.POSITIONAL_ONLY_MAPPING: (
        "[WARNING] Found args_mapping entries targeting POSITIONAL_ONLY constructor parameters"
        " (proxy falls back to setattr for these entries):"
    ),
}


def _row_detail(finding: _Finding) -> str:
    """Return the text after ``module.function:`` in a finding's console row."""
    if finding.rule is _Rule.INNER_ORDER_PROPERTY:
        return "use @deprecated(...) above @property"
    if finding.rule in (_Rule.MISCONFIGURED_TARGET, _Rule.CHAIN):
        return finding.details[0] if finding.details else ""
    return ", ".join(finding.details)


class _Reporter:
    """Print each kind of finding as a section: a ``[ERROR]``/``[WARNING]`` header line, then one line per finding."""

    @staticmethod
    def _render_table(plain_prefix: str, findings: list[_Finding]) -> None:
        """Print a section of ``module.function: detail`` lines under its header line."""
        _print(f"\n{plain_prefix}")
        for finding in findings:
            _print(f"\t- {finding.module}.{finding.symbol}: {_row_detail(finding)}")

    @staticmethod
    def chains(findings: list[_Finding], *, error: bool = False) -> None:
        """Report deprecated-to-deprecated forwarding chains; ``error=True`` reports them as ``[ERROR]``."""
        prefix = "[ERROR]" if error else "[WARNING]"
        _Reporter._render_table(f"{prefix} {_SECTION_HEADERS[_Rule.CHAIN]}", findings)

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
    def issues(findings: list[_Finding], *, error_on_chains: bool = False) -> bool:
        """Print the configuration findings section by section, in a fixed order; return whether there were any.

        Sections follow :data:`_SECTION_HEADERS`, not the order the findings were derived in; rows keep the order of
        *findings*.

        """
        printed = False
        for rule in _SECTION_HEADERS:
            rows = [finding for finding in findings if finding.rule is rule]
            if not rows:
                continue
            printed = True
            if rule is _Rule.CHAIN:
                _Reporter.chains(rows, error=error_on_chains)
            else:
                _Reporter._render_table(_SECTION_HEADERS[rule], rows)
        return printed


# ---------------------------------------------------------------------------
# --report-json: the machine-readable record of one run
# ---------------------------------------------------------------------------

#: Version of the ``--report-json`` layout. Bumped only when a key is removed or renamed, or its meaning or type
#: changes; adding a key does not bump it, and readers ignore keys they do not know. Per-finding records, the run's gate
#: outcome and ``mode`` were added under schema 1, so every key a 0.15 report has (``schema``, ``table``,
#: ``gaps[].kind/detail/hint``) keeps its type and meaning, and a 0.15 reader still accepts the report.
_REPORT_SCHEMA = 1
#: Default of each option the report lists, for a run that never resolved it (``status``, a usage error).
_DEFAULT_OPTIONS: dict[str, Any] = {"strict": False, "exit_zero": False, "gates": [], "recursive": True, "exclude": []}
#: Environment variable naming the checkout directory of a GitHub Actions job; locates the repository without ``.git``.
_WORKSPACE_ENV = "GITHUB_WORKSPACE"


def _render_recorded_table(record: _RunRecord) -> Optional[str]:
    """Return the run's deprecation table: the one it rendered, else one built from its recorded scan.

    A gate that never resolved a version (``check``, ``policy``, ``chains``) gets the table ``status`` would print: the
    version is auto-detected, as there. A table that cannot be rendered is reported on stderr and left out; over several
    paths its package keeps its heading, with a note in place of the table.

    """
    scans = record.scans or ([record.scan] if record.scan is not None else [])
    if record.table is not None or not scans:
        return record.table
    tables = []
    for scan in scans:
        module_name = _safe_module_name(scan.path)
        if scan.version is not None:
            version: Optional[str] = scan.version
        else:
            version = _resolve_package_version(
                module_name, path=scan.path, allow_import=not scan.static, import_root=scan.import_root
            )
        # A static scan never imports the package: with no version found, no module name is passed either, or the table
        # would auto-detect the version itself and import the package to read its `__version__`.
        table_module = "" if scan.static and version is None else module_name
        try:
            resolved_version, parsed_version = _resolve_table_version(
                table_module, current_version=version, version_explicit=False
            )
            markdown = _render_deprecation_table(scan.wrappers, resolved_version, parsed_version, TableStyle.COMPACT)
        except Exception as exc:  # a display artifact: it never fails the run, as in `cmd_all`
            _print(f"Could not render the deprecation table for the JSON report: {exc}", stderr=True)
            if len(scans) == 1:
                continue
            markdown = _unrendered_table(exc)  # the heading stays, so the tables around it stay labelled
        tables.append((module_name, markdown))
    return _join_tables(tables)


def _find_location_root(cwd: Path, workspace: Optional[str]) -> Path:
    """Return the repository root that report and SARIF locations are relative to, for a run in directory *cwd*.

    That is the nearest directory at or above *cwd* holding a ``.git`` entry (a directory, or the file of a worktree or
    submodule); without one, the *workspace* directory (``GITHUB_WORKSPACE``) when it is absolute and *cwd* lies in it;
    else *cwd* itself. GitHub resolves relative code-scanning and annotation paths against the repository root, so a
    run started in a sub-directory of the checkout still names every file as the repository does.

    Args:
        cwd: The directory the CLI runs in.
        workspace: The value of ``GITHUB_WORKSPACE``, ``None`` or empty outside GitHub Actions.

    Examples:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     repo = Path(tmp).resolve()
        ...     (repo / ".git").mkdir()
        ...     (repo / "src").mkdir()
        ...     _find_location_root(repo / "src", None) == repo
        True

    """
    for directory in (cwd, *cwd.parents):
        # `os.path.exists` never raises: an unreadable ancestor counts as holding no `.git`
        if os.path.exists(directory / ".git"):
            return directory
    if workspace:
        workspace_dir = Path(workspace)
        if workspace_dir.is_absolute() and cwd.is_relative_to(workspace_dir):
            return workspace_dir
    return cwd


def _location_root() -> Path:
    """Return the repository root of the working directory: the base of every report and SARIF location of the run."""
    return _find_location_root(Path.cwd(), os.environ.get(_WORKSPACE_ENV))


def _report_location(finding: _Finding, root: Path) -> Optional[dict[str, Any]]:
    """Return where *finding*'s wrapper is defined: a POSIX path relative to the repository *root* when under it."""
    if finding.source_file is None:
        return None
    source = Path(finding.source_file)
    try:
        file_name = source.relative_to(root).as_posix()
    except ValueError:
        file_name = PurePath(source).as_posix()
    return {"file": file_name, "line": finding.source_line}


def _report_finding(finding: _Finding, level: _Level, path: Optional[str], root: Path) -> dict[str, Any]:
    """Return the report record of one finding at the level it has in this run, located relative to *root*."""
    return {
        "id": finding.id,
        "rule": finding.rule.value,
        "check": finding.check.value,
        "level": level.value,
        "message": finding.message,
        "details": list(finding.details),
        "subject": {
            "module": finding.module,
            "symbol": finding.symbol,
            "api_type": finding.api_type,
            "label": finding.label,
        },
        "deprecated_in": finding.deprecated_in,
        "remove_in": finding.remove_in,
        "location": _report_location(finding, root),
        "path": path,
    }


def _report_suppressed(record: _RunRecord, root: Path) -> list[dict[str, Any]]:
    """Return the run's suppressed findings, one per id, each with what suppressed it, sorted like the findings."""
    best: dict[str, tuple[_Level, _SuppressedFinding]] = {}
    for item in record.suppressed:
        level = _level(item.finding.rule, record.command or "", record.gates_by_path.get(item.path, frozenset()))
        if level is None:
            continue
        kept = best.get(item.finding.id)
        if kept is None or _LEVEL_RANK[level] < _LEVEL_RANK[kept[0]]:
            best[item.finding.id] = (level, item)
    ordered = sorted(best.values(), key=lambda pair: (_LEVEL_RANK[pair[0]], pair[1].finding.id))
    return [
        {
            **_report_finding(item.finding, level, item.path, root),
            "suppressed_by": {"kind": item.kind, "entry": item.entry},
        }
        for level, item in ordered
    ]


def _report_findings(record: _RunRecord, root: Path) -> list[dict[str, Any]]:
    """Return the run's findings, one per id at its highest level in the subcommand, sorted by ``(level, id)``.

    ``all`` derives a ``chain`` finding in its ``check`` pass and again in its ``chains`` pass; the two are one record,
    at the higher level. Findings the subcommand does not evaluate are left out. Locations are relative to *root*.

    """
    return [finding for finding, _ in _report_findings_with_sources(record, root)]


def _report_findings_with_sources(record: _RunRecord, root: Path) -> list[tuple[dict[str, Any], Optional[str]]]:
    """Return :func:`_report_findings` paired with each finding's absolute source file (``None`` when unlocated)."""
    best: dict[str, tuple[_Level, _Finding, Optional[str]]] = {}
    for finding, path in zip(record.findings, record.finding_paths):
        level = _level(finding.rule, record.command or "", record.gates_by_path.get(path, frozenset()))
        if level is None:
            continue
        kept = best.get(finding.id)
        if kept is None or _LEVEL_RANK[level] < _LEVEL_RANK[kept[0]]:
            best[finding.id] = (level, finding, path)
    ordered = sorted(best.values(), key=lambda item: (_LEVEL_RANK[item[0]], item[1].id))
    return [(_report_finding(finding, level, path, root), finding.source_file) for level, finding, path in ordered]


def _run_mode(record: _RunRecord) -> str:
    """Return how the run's gate read the package: ``static`` for a ``--static`` source read, else ``import``.

    Both the ``--report-json`` and the ``--report-sarif`` file name the mode of the same run with this value.

    """
    return "static" if record.scan is not None and record.scan.static else "import"


def _build_report(record: _RunRecord, exit_code: int) -> dict[str, Any]:
    """Return the ``--report-json`` document of the run (layout :data:`_REPORT_SCHEMA`)."""
    root = _location_root()
    findings = _report_findings(record, root)
    suppressed = _report_suppressed(record, root)
    levels = [finding["level"] for finding in findings]
    return {
        "schema": _REPORT_SCHEMA,
        "tool": {"name": "pyDeprecate", "version": __about__.__version__},
        "command": record.command,
        "mode": _run_mode(record),
        "paths": [scanned.path for scanned in record.scanned],
        "versions": [
            {"path": item.path, "package": item.package, "value": item.version, "source": item.version_source}
            for item in record.scanned
        ],
        "options": {**_DEFAULT_OPTIONS, **record.options},
        "gate": {"failed": bool(record.gate_reasons), "reasons": [reason.value for reason in record.gate_reasons]},
        "exit_code": exit_code,
        "table": _render_recorded_table(record),
        "gaps": [
            {"kind": gap.kind.value, "detail": gap.detail, "hint": _GAP_HINTS[gap.kind], "path": gap.path}
            for gap in record.gaps
        ],
        "findings": findings,
        "suppressed": suppressed,
        "summary": {
            "findings": len(findings),
            "errors": levels.count(_Level.ERROR.value),
            "warnings": levels.count(_Level.WARNING.value),
            "notes": levels.count(_Level.NOTE.value),
            "gaps": len(record.gaps),
            "suppressed": len(suppressed),
            "baseline_unmatched": record.baseline_unmatched,
            "ignore_unmatched": record.ignore_unmatched,
        },
    }


def _write_report(path: str, record: _RunRecord, exit_code: int = 0) -> None:
    """Write the run's ``--report-json`` file; a failure only prints an advisory and never changes the exit code.

    Args:
        path: File to write; missing parent directories are created.
        record: Gaps, findings, table and scan collected while the subcommand ran.
        exit_code: The process exit code of the run, 0 or 1 (the report is never written for 2 or 3).

    """
    _write_json_file(path, lambda: _build_report(record, exit_code), "JSON report")


def _write_json_file(path: str, build: Callable[[], dict[str, Any]], label: str) -> None:
    """Write the JSON document *build* returns to *path*; any failure only prints an advisory and never raises.

    The document is built and encoded to UTF-8 before the file is touched, so a failing build or a string UTF-8 cannot
    encode (a lone surrogate) leaves no empty or partial file behind for a CI step to publish. The bytes go to a
    temporary sibling that then replaces *path* in one step, so a write failing midway (a full disk) leaves *path*
    as it was, never truncated; the temporary file is removed on failure.

    Args:
        path: File to write; missing parent directories are created.
        build: Returns the document; called inside the guard, so its ``OSError`` or ``ValueError`` is reported too.
        label: What the file is, for the advisory: ``JSON report`` or ``SARIF report``.

    """
    out_path = Path(path)
    temp_path: Optional[Path] = None
    try:
        data = (json.dumps(build(), indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # a sibling, so the replace is a rename within one directory; the random part keeps concurrent writers apart
        temp_path = out_path.with_name(f".{out_path.name}.{os.getpid()}-{os.urandom(4).hex()}.tmp")
        with open(temp_path, "xb") as stream:
            stream.write(data)
        os.replace(temp_path, out_path)
    except (OSError, ValueError) as err:  # UnicodeEncodeError and a circular-reference error are ValueErrors
        _print(f"Could not write the {label} {out_path}: {err}", stderr=True)
        if temp_path is not None:
            with suppress(OSError):  # absent when `open` itself failed
                temp_path.unlink()
