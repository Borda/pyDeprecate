"""Subcommand implementations for the CLI: ``check``, ``expiry``, ``policy``, ``chains``, ``all`` and ``status``.

Every subcommand takes one path or several. A run checks every path before it imports anything (:func:`_plan_paths`),
then scans the paths one by one, each with its own ``pyproject.toml`` settings and version, and finally prints each
check's findings of all paths in one section and decides one exit code over all of them.

"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Callable, NamedTuple, Optional, Protocol, Union

from deprecate._cli._pkg import _safe_module_name
from deprecate._cli.config import (
    _FROM_PYPROJECT,
    _Config,
    _ConfigBool,
    _ConfigFlag,
    _ConfigList,
    _is_missing_packaging_import_error,
    _load_pydeprecate_config,
    _resolve_exclude,
    _resolve_gate,
    _resolve_policy_spec,
    _resolve_strict,
    _resolve_suppressor,
    _validate_user_version,
)
from deprecate._cli.findings import (
    _GATE_ORDER,
    _exit_code,
    _gate_reasons,
    _GateReason,
    _level,
    _merge_reasons,
    _verdict,
)
from deprecate._cli.report import (
    _ensure_run_record,
    _gap_recorded,
    _GapKind,
    _join_tables,
    _print,
    _print_gap,
    _record_findings,
    _record_options,
    _record_table,
    _Reporter,
    _RunRecord,
    _ScanRecord,
)
from deprecate._cli.scan import _preflight_targets, _scan_for_command, _ScanTarget
from deprecate._cli.static.checks import _report_unresolved, _resolved, _static_policy_findings
from deprecate._cli.static.consts import _EXPIRY_KEYWORDS
from deprecate._cli.suppress import _Suppressor
from deprecate.audit import (
    DeprecationWrapperInfo,
    PolicyRule,
    TableStyle,
)
from deprecate.audit._findings import _Check, _Finding, _Rule, _wrapper_findings
from deprecate.audit._lifecycle import _expiry_findings
from deprecate.audit._policy import _build_policy_spec, _policy_findings
from deprecate.audit._report import _render_deprecation_table, _resolve_table_version
from deprecate.audit._scan import _is_excluded
from deprecate.audit._wrappers import _format_subject

if TYPE_CHECKING:
    from deprecate.audit._policy import _OnUnparsableVersion, _PolicySpec

#: The ``path`` argument of every subcommand: one path, or several scanned in one run.
_PathArg = Union[str, Sequence[str]]


class _PolicyFindings(Protocol):
    """How a ``policy`` run evaluates scanned wrappers: :func:`_policy_findings`, or the per-rule static variant."""

    def __call__(
        self,
        wrappers: list[DeprecationWrapperInfo],
        spec: "_PolicySpec",
        /,
        *,
        on_unparsable: Optional["_OnUnparsableVersion"] = None,
    ) -> list[_Finding]:
        """Return the policy findings of *wrappers*, handing each version the grace rule skipped to *on_unparsable*."""


def _do_expiry_prescanned(wrappers: list[DeprecationWrapperInfo], version: Optional[str]) -> Optional[list[_Finding]]:
    """Derive expired wrapper messages from an already-scanned wrapper list, without rescanning.

    Used by both ``cmd_expiry`` and ``cmd_all`` after their single scan. Every way this can fail is advisory: an
    explicit ``--version`` is validated up front (exit 2), so only an auto-detected version reaches here, and
    neither an unresolvable nor an unparsable one is a usage error the user can be blamed for, so the expiry gate
    is skipped with a message instead of failing the run.

    A wrapper whose own ``remove_in`` is not PEP 440 is skipped by the check, which only warns about it. The check
    hands each such wrapper back to :func:`_record_remove_in_gap`, which records it as a
    :attr:`_GapKind.REMOVE_IN_INVALID` gap, since the warning can be silenced by the audited package; the expired list
    and the exit code are unchanged.

    Args:
        wrappers: Pre-scanned wrapper list to compare against *version*.
        version: Resolved package version, or None when it could not be resolved at all.

    Returns:
        One ``expired`` finding per overdue wrapper (may be empty), or None when the check could not run
        (advisory — the reason is already printed to stderr).

    """
    if version is None:
        _print_gap(_GapKind.VERSION_UNRESOLVED, "Cannot check expiry: version not resolved. Pass --version explicitly.")
        return None
    try:
        # The audit layer only warns about a `remove_in` it skips and hands the wrapper back, so the gap is recorded
        # here and the audit layer stays free of CLI imports. An unusable `version` fails before any wrapper is read,
        # so it is reported once (below) and never as a per-wrapper gap.
        return _expiry_findings(wrappers, version, on_unparsable=_record_remove_in_gap)
    except ImportError:
        _print_gap(
            _GapKind.PACKAGING_MISSING,
            "The 'expiry' subcommand requires the 'packaging' library.\n"
            "Install it with: `pip install 'pyDeprecate[audit]'`",
        )
        return None
    except ValueError:
        # Only an auto-detected version reaches here — an explicit one is rejected up front with exit 2.
        # A package whose own metadata version is not PEP 440 is not a usage error, so the check degrades
        # to an advisory skip rather than aborting the run with an unhandled parse failure.
        _print_gap(
            _GapKind.VERSION_INVALID,
            f"Cannot check expiry: the auto-detected version `{version}` is not a valid PEP 440 version "
            "string. Pass `--version` explicitly for a definitive check.",
        )
        return None


#: Gap kind of each wrapper version field that a check skips when it is not PEP 440.
_INVALID_VERSION_GAPS: dict[str, _GapKind] = {
    "deprecated_in": _GapKind.DEPRECATED_IN_INVALID,
    "remove_in": _GapKind.REMOVE_IN_INVALID,
}


def _invalid_version_subject(info: DeprecationWrapperInfo, field_name: str) -> str:
    """Name the wrapper and its version *field_name* that is not PEP 440: the opening of every such gap's detail."""
    value = getattr(info.deprecated_info, field_name)
    return f"{_format_subject(info)} has `{field_name}` `{value}`, which is not a valid PEP 440 version string,"


def _record_remove_in_gap(info: DeprecationWrapperInfo) -> None:
    """Record a wrapper whose ``remove_in`` is not PEP 440 as a gap: its removal deadline was never checked."""
    _print_gap(
        _GapKind.REMOVE_IN_INVALID,
        f"{_invalid_version_subject(info, 'remove_in')} so its removal deadline was not checked.",
    )


def _record_grace_version_gap(info: DeprecationWrapperInfo, field_name: str) -> None:
    """Record an invalid version or forward epoch change that left the grace window unchecked.

    ``all`` runs the expiry check first on the same scan, and it already names a wrapper whose ``remove_in`` it skipped;
    the policy pass then adds no second gap for it. When the expiry check did not run (no usable version), it named no
    wrapper, so the policy pass records the gap itself.

    """
    if field_name == "epoch":
        config = info.deprecated_info
        _print_gap(
            _GapKind.EPOCH_CHANGE,
            f"{_format_subject(info)} spans `{config.deprecated_in}` to `{config.remove_in}`; "
            "its `min-grace` grace window was not checked.",
        )
        return
    kind = _INVALID_VERSION_GAPS[field_name]
    subject = _invalid_version_subject(info, field_name)
    if _gap_recorded(kind, subject):
        return
    _print_gap(kind, f"{subject} so its `{PolicyRule.MIN_GRACE.value}` grace window was not checked.")


def _save_table(markdown: str, output: str) -> None:
    """Write the rendered deprecation table to *output* (missing parent directories are created) and say so on stderr.

    Args:
        markdown: The rendered markdown table.
        output: File path to write.

    Raises:
        OSError: When the directory cannot be created or the file cannot be written.

    """
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    _print(f"→ Saved to: {out_path}", stderr=True)


def _skipped_policy_rules(spec: "_PolicySpec") -> list[str]:
    """Return the CLI-facing slugs of the version-dependent policy rules *spec* has enabled.

    These are exactly the rules that cannot run without the ``packaging`` library — used to name them in the advisory
    printed when ``packaging`` turns out to be unavailable. Today ``min-grace`` is the only such rule, so the list holds
    at most one entry.

    """
    return [PolicyRule.MIN_GRACE.value] if spec.grace is not None else []


def _policy_violations_without_packaging(
    wrappers: list[DeprecationWrapperInfo], spec: "_PolicySpec", findings: Optional[_PolicyFindings] = None
) -> Optional[list[_Finding]]:
    """Handle a missing-``packaging`` failure from :func:`_policy_findings`.

    Prints an advisory naming the version-dependent rule *spec* had enabled (``min_grace``) — it is skipped.
    ``message_required`` needs no version parsing, so when it is enabled it is re-run standalone and its
    violations still gate the exit code; only the fully-disabled case falls through to an advisory no-op.

    Args:
        wrappers: Pre-scanned wrapper list to re-check for ``message_required`` alone.
        spec: The originally requested (unsatisfiable) policy configuration.
        findings: The policy evaluation to re-run, :func:`_static_policy_findings` for a ``--static`` scan; ``None``
            runs :func:`_policy_findings`, looked up at call time.

    Returns:
        The ``message_required``-only violations list, or ``None`` when ``message_required`` is disabled and there
        is nothing left to check.

    """
    skipped_rules = ", ".join(f"`{rule}`" for rule in _skipped_policy_rules(spec))
    _print_gap(
        _GapKind.PACKAGING_MISSING,
        f"The `packaging` library is required for the {skipped_rules} policy rule; skipping it.\n"
        "Install it with: `pip install 'pyDeprecate[audit]'`",
    )
    if not spec.message_required:
        return None
    # `message_required` needs no version parsing at all, so a spec with the grace-window rule switched off keeps
    # the version machinery — and with it the ImportError just handled — out of this second pass entirely.
    evaluate = findings if findings is not None else _policy_findings
    return evaluate(wrappers, _build_policy_spec(None, True))


#: The checks whose error findings fail each gate subcommand, as the report's ``options.gates`` lists them; ``all``
#: adds the checks ``--gate`` names.
_GATES: dict[str, list[str]] = {
    "check": ["check"],
    "expiry": ["expiry"],
    "policy": ["policy"],
    "chains": ["chains"],
    # the checks `all` always gates on; `--gate` adds to them and never removes one
    "all": ["check", "expiry", "chains"],
}


def _as_paths(path: _PathArg) -> list[str]:
    """Return the paths of a run: *path* itself, or each path it lists; an empty list scans ``.`` as no path does.

    Examples:
        >>> _as_paths("src/pkg"), _as_paths(["pkg_a", "pkg_b"]), _as_paths([])
        (['src/pkg'], ['pkg_a', 'pkg_b'], ['.'])

    """
    paths = [path] if isinstance(path, str) else list(path)
    return paths or ["."]


@dataclass(frozen=True)
class _RunOptions:
    """The options of one subcommand run that every path's plan resolves against its own ``pyproject.toml``."""

    recursive: bool = True
    exclude: _ConfigFlag = _FROM_PYPROJECT
    include_members: bool = True
    exit_zero: bool = False
    strict: _ConfigBool = _FROM_PYPROJECT
    ignore: _ConfigList = _FROM_PYPROJECT
    baseline: _ConfigFlag = _FROM_PYPROJECT
    #: The ``--gate`` value of ``all``; ``None`` for a subcommand that takes no ``--gate``.
    gate: Optional[_ConfigList] = None
    #: Rule slug -> flag value for the subcommands that run the policy check; ``None`` for the others.
    policy_flags: Optional[dict[str, _ConfigFlag]] = None
    #: Whether the subcommand gates on findings; ``status`` resolves no strict mode and no accepted findings.
    gating: bool = True
    #: Read each path's source with ``ast`` instead of importing it (``--static``); ``expiry`` and ``policy`` only.
    static: bool = False


@dataclass(frozen=True)
class _PathPlan:
    """One path of a run with the settings its own ``pyproject.toml`` resolved, ready to scan."""

    target: _ScanTarget
    config: _Config
    strict: bool = False
    suppressor: Optional[_Suppressor] = None
    #: The checks ``--gate`` adds for this path (``all`` only).
    gates: frozenset[str] = frozenset()
    #: The resolved policy rules and the ``Policy:`` header line naming their sources.
    policy: Optional[tuple["_PolicySpec", str]] = None
    #: The resolved module-exclusion patterns of this path.
    exclude: tuple[str, ...] = ()
    #: Whether the scan descends into submodules.
    recursive: bool = True

    @property
    def path(self) -> str:
        """The path as the user passed it."""
        return self.target.path

    def audits(self, module: str, *, not_audited: frozenset[str] = frozenset()) -> bool:
        """Tell whether this path's scan audited *module*: inside its scope, not excluded, and imported.

        *not_audited* holds the modules the scan reported as not audited (an import failure), with their submodules.

        """
        if any(module == name or module.startswith(f"{name}.") for name in not_audited):
            return False
        if _is_excluded(module, self.exclude):
            return False
        root = self.target.module
        if root is None:  # a plain directory scans its top-level files, each by its bare name
            location = self.target.location
            return location is not None and "." not in module and (location / f"{module}.py").is_file()
        return module == root or (self.recursive and module.startswith(f"{root}."))


def _plan_path(target: _ScanTarget, options: _RunOptions) -> Optional[_PathPlan]:
    """Resolve one path's settings as flag > its own ``pyproject.toml`` > built-in, printing any usage error.

    Returns:
        The plan, or ``None`` after printing a usage error (the caller exits 2): an unreadable ``pyproject.toml``, a
        malformed ``exclude``, ``strict``, ``ignore``, ``baseline``, ``gate`` or policy rule value, strict mode with
        ``--exit-zero``, or a baseline file that cannot be read or is not a report with finding ids.

    """
    path = target.path
    # `status` reads the file only for `exclude`, as it always did; a typed `--exclude` leaves it unread
    reads_file = options.gating or options.exclude is _FROM_PYPROJECT
    config = _load_pydeprecate_config(path) if reads_file else ({}, None)
    exclude = None if config is None else _resolve_exclude(path, options.exclude, config)
    if config is None or exclude is None:
        return None
    if not options.gating:
        return _PathPlan(target, config)
    strict = _resolve_strict(options.strict, options.exit_zero, config)
    suppressor = None if strict is None else _resolve_suppressor(options.ignore, options.baseline, config)
    if strict is None or suppressor is None:
        return None
    gates = frozenset() if options.gate is None else _resolve_gate(options.gate, config)
    policy = None
    if gates is not None and options.policy_flags is not None:
        policy = _resolve_policy_spec(path, options.policy_flags, config)
    if gates is None or (options.policy_flags is not None and policy is None):
        return None
    return _PathPlan(target, config, strict, suppressor, gates, policy, tuple(exclude[0]), options.recursive)


def _plan_paths(paths: Sequence[str], options: _RunOptions) -> Optional[list[_PathPlan]]:
    """Check every path of a run and resolve its settings before anything is imported.

    Any usage error on any path stops the whole run with exit 2 before the first scan: a path that is a file, a
    directory of several packages or neither a path nor a findable module, two paths importing as the same name from
    different places, or a malformed setting of any path's ``pyproject.toml``.

    Returns:
        One plan per path to scan (a path another path's scan already covers is dropped with a note), or ``None``.

    """
    targets = _preflight_targets(paths, recursive=options.recursive)
    if targets is None:
        return None
    plans = []
    for target in targets:
        plan = _plan_path(target, options)
        if plan is None:
            return None
        plans.append(plan)
    return plans


class _GateStart(NamedTuple):
    """Where a gate subcommand's own findings and gaps begin in the run record, and the plan of each path."""

    record: _RunRecord
    findings: int
    gaps: int
    plans: list[_PathPlan]


def _start_gate(record: _RunRecord, path: _PathArg, options: _RunOptions) -> Optional[_GateStart]:
    """Plan every path of a gate run before any import and record the run-level options.

    Returns:
        The gate's start marks and its path plans, or ``None`` after printing a usage error (the caller exits 2).

    """
    plans = _plan_paths(_as_paths(path), options)
    if plans is None:
        return None
    for plan in plans:
        record.gates_by_path[plan.path] = plan.gates
        _record_options(strict=plan.strict, exit_zero=options.exit_zero)
    return _GateStart(record, len(record.findings), len(record.gaps), plans)


def _scan_plans(
    plans: list[_PathPlan],
    options: _RunOptions,
    *,
    version: Optional[str] = None,
    resolve_version: bool = False,
    record: bool = True,
) -> Optional[list[tuple[_PathPlan, _ScanRecord]]]:
    """Scan every planned path in turn, each with its own settings and, with *resolve_version*, its own version.

    A typed *version* applies to every path, and a run over several paths says so before the first scan header.

    Returns:
        Each plan with its scan, or ``None`` after printing a usage error found only while importing (exit 2).

    """
    if version is not None and len(plans) > 1:
        _print(f"Version: {version} (user-provided) applies to every path.")
    scanned = []
    for plan in plans:
        scan = _scan_for_command(
            plan.path,
            recursive=options.recursive,
            exclude=options.exclude,
            config=plan.config,
            include_members=options.include_members,
            version=version,
            version_explicit=version is not None,
            resolve_version=resolve_version,
            record=record,
            static=options.static,
        )
        if scan is None:
            return None
        scanned.append((plan, scan))
    return scanned


def _select_path(record: _RunRecord, plan: _PathPlan) -> None:
    """Attribute the findings and gaps recorded next to *plan*'s path, and suppress them with its accepted findings."""
    record.current_path = plan.path
    record.suppressor = plan.suppressor


def _derive_each(
    record: _RunRecord,
    scanned: list[tuple[_PathPlan, _ScanRecord]],
    derive: Callable[[_PathPlan, _ScanRecord], Optional[list[_Finding]]],
) -> Optional[list[_Finding]]:
    """Run one check's derivation on every scanned path and return the active findings of all of them.

    Returns:
        The findings in path order, or ``None`` when the check could not run on any path (each reason was printed).

    """
    found: Optional[list[_Finding]] = None
    for plan, scan in scanned:
        _select_path(record, plan)
        derived = derive(plan, scan)
        if derived is not None:
            found = [*(found or []), *derived]
    return found


#: Gap kinds after which a check did not run, and the rules whose baseline ids it would have matched.
_SKIPPED_RULES: dict[_GapKind, frozenset[str]] = {
    _GapKind.VERSION_UNRESOLVED: frozenset({_Rule.EXPIRED.value}),
    _GapKind.VERSION_INVALID: frozenset({_Rule.EXPIRED.value}),
    _GapKind.PACKAGING_MISSING: frozenset({_Rule.EXPIRED.value, _Rule.MIN_GRACE.value}),
}


def _report_suppression(record: _RunRecord, plans: list[_PathPlan], subcommand: str) -> None:
    """Say how many findings the ignore lists and baselines suppressed, and count the entries that matched none.

    A baseline id counts as stale only for a rule this subcommand evaluates and whose check actually ran on the path
    whose baseline holds it, in a module that path's scan audited (inside the scanned package, not excluded, imported),
    and only when no path matched it; it is reported once on stderr and never fails the run. Unused ignore entries —
    matched on no path — are only counted for the report: an entry for a check that did not run is legitimately unused.

    """
    kinds = Counter({item.finding.id: item.kind for item in record.suppressed}.values())
    if kinds:
        total = sum(kinds.values())
        _print(f"\n{total} finding(s) suppressed (ignore: {kinds['ignore']}, baseline: {kinds['baseline']}).")
    evaluated = {rule.value for rule in _Rule if _level(rule, subcommand) is not None}
    planned = {plan.path for plan in plans}
    entries: set[str] = set()
    used_entries: set[str] = set()
    expected_ids: set[str] = set()
    used_ids: set[str] = set()
    for plan in plans:
        if plan.suppressor is None:
            continue
        own_gaps = [gap for gap in record.gaps if gap.path == plan.path or gap.path not in planned]
        skipped = {rule for gap in own_gaps for rule in _SKIPPED_RULES.get(gap.kind, ())}
        not_audited = frozenset(
            gap.detail.split(":", 1)[0] for gap in own_gaps if gap.kind is _GapKind.MODULE_NOT_AUDITED
        )
        entries |= {entry.text for entry in plan.suppressor.ignore}
        used_entries |= plan.suppressor.used_entries
        expected_ids |= plan.suppressor.evaluated_baseline(
            evaluated - skipped, partial(plan.audits, not_audited=not_audited)
        )
        used_ids |= plan.suppressor.used_ids
    record.ignore_unmatched = len(entries - used_entries)
    record.baseline_unmatched = len(expected_ids - used_ids)
    if record.baseline_unmatched:
        verb = "entry matches" if record.baseline_unmatched == 1 else "entries match"
        _print(
            f"pydeprecate: {record.baseline_unmatched} baseline {verb} no finding of this run"
            " (stale, or outside a narrowed scan); regenerate the baseline from a full-scope run.",
            stderr=True,
        )


def _gate_outcome(start: _GateStart, subcommand: str) -> tuple[list[_GateReason], int]:
    """Return why the run since *start* failed its gate, and how many gaps fail it in strict mode.

    Each path is judged with its own strict mode and ``--gate`` list on its own findings and gaps; a finding or gap
    recorded outside any planned path counts with no gate added, and as strict when any path is strict.

    """
    record = start.record
    plans = {plan.path: plan for plan in start.plans}
    any_strict = any(plan.strict for plan in start.plans)
    findings = list(zip(record.findings[start.findings :], record.finding_paths[start.findings :]))
    gaps = record.gaps[start.gaps :]
    per_path = []
    strict_gaps = 0
    for path in [*plans, None]:
        plan = plans.get(path) if path is not None else None
        own_findings = [finding for finding, where in findings if (where if where in plans else None) == path]
        own_gaps = sum(1 for gap in gaps if (gap.path if gap.path in plans else None) == path)
        strict = plan.strict if plan is not None else any_strict
        gates = plan.gates if plan is not None else frozenset()
        per_path.append(_gate_reasons(own_findings, subcommand, strict=strict, gaps=own_gaps, gates=gates))
        strict_gaps += own_gaps if strict else 0
    return _merge_reasons(per_path), strict_gaps


def _finish_gate(start: _GateStart, subcommand: str, *, exit_zero: bool) -> int:
    """Return the one exit code of a gate run over all its paths from the findings and gaps recorded since *start*.

    Suppressed findings never count. A gap of a strict path fails the run, and the run says so on stderr after its gap
    lines; a gap of a path that is not strict does not.

    """
    _report_suppression(start.record, start.plans, subcommand)
    reasons, strict_gaps = _gate_outcome(start, subcommand)
    if strict_gaps:
        _print(f"Strict mode: {strict_gaps} gap(s) fail this run.", stderr=True)
    start.record.gate_reasons = reasons
    added = frozenset().union(*(plan.gates for plan in start.plans))
    gates = [check for check in _GATE_ORDER if check in _GATES[subcommand] or (subcommand == "all" and check in added)]
    _record_options(gates=gates)
    return _verdict(reasons, exit_zero=exit_zero)


# ---------------------------------------------------------------------------
# The checks: derive findings per path, render them once for every path
# ---------------------------------------------------------------------------


def _check_derive(wrappers: list[DeprecationWrapperInfo]) -> list[_Finding]:
    """Record and return the active configuration findings of already-scanned wrappers."""
    return _record_findings([finding for info in wrappers for finding in _wrapper_findings(info)])


def _check_render(findings: list[_Finding]) -> None:
    """Print the configuration findings section by section, then the verdict line."""
    if _Reporter.issues(findings, error_on_chains=False):
        _print("\nIssues were found in deprecated wrappers.")
    else:
        _print("\nAll deprecated wrappers look correct!")


def _check_wrappers(wrappers: list[DeprecationWrapperInfo], *, exit_zero: bool = False) -> int:
    """Report the configuration findings of already-scanned wrappers and return the ``check`` exit code.

    The evaluation half of :func:`cmd_check` for one wrapper list.

    """
    if not wrappers:
        _print("No deprecated callables found.")
        return 0
    findings = _check_derive(wrappers)
    _check_render(findings)
    return _exit_code(findings, "check", exit_zero=exit_zero)


def _run_check(record: _RunRecord, scanned: list[tuple[_PathPlan, _ScanRecord]]) -> None:
    """Derive the configuration findings of every scanned path and print them as one set of sections."""
    if not any(scan.wrappers for _, scan in scanned):
        _print("No deprecated callables found.")
        return
    _check_render(_derive_each(record, scanned, lambda _plan, scan: _check_derive(scan.wrappers)) or [])


def _expiry_derive(
    wrappers: list[DeprecationWrapperInfo], version: Optional[str], *, static: bool = False
) -> Optional[list[_Finding]]:
    """Record and return the active ``expired`` findings, or ``None`` when the check could not run (gap printed).

    A ``--static`` scan (*static*) checks the wrappers whose ``remove_in`` the source settled; a ``not statically
    resolvable`` gap names each one it left out, printed only once the check has run — when it could not (no version, an
    invalid one, no ``packaging``) it has already printed why, and a gap per wrapper would imply that the others were
    checked.

    """
    checked = _resolved(wrappers, _EXPIRY_KEYWORDS) if static else wrappers
    derived = _do_expiry_prescanned(checked, version)
    if derived is None:
        return None
    if static:
        _report_unresolved(wrappers, _EXPIRY_KEYWORDS)
    return _record_findings(derived)


def _expiry_render(expired: Optional[list[_Finding]]) -> None:
    """Print the expired wrappers and their count; nothing when the check did not run."""
    if expired is None:
        return
    if not expired:
        _print("No expired deprecated wrappers found.")
        return
    _Reporter.expiry([finding.message for finding in expired])
    _print(f"\n{len(expired)} expired wrapper(s) found.")


def _expiry_wrappers(
    wrappers: list[DeprecationWrapperInfo], version: Optional[str], *, exit_zero: bool = False, static: bool = False
) -> int:
    """Report the already-scanned wrappers past their removal deadline and return the ``expiry`` exit code.

    The evaluation half of :func:`cmd_expiry` for one wrapper list and version. *version* is never validated here: a
    typed ``--version`` was validated before the scan, and an auto-detected one that is not PEP 440 is an advisory skip,
    not a usage error. *static* marks wrappers read from source (``--static``), see :func:`_expiry_derive`.

    """
    expired = _expiry_derive(wrappers, version, static=static)
    _expiry_render(expired)
    return 0 if expired is None else _exit_code(expired, "expiry", exit_zero=exit_zero)


def _run_expiry(record: _RunRecord, scanned: list[tuple[_PathPlan, _ScanRecord]]) -> None:
    """Check every scanned path against its own version and print the expired wrappers of all of them."""
    _expiry_render(
        _derive_each(
            record, scanned, lambda _plan, scan: _expiry_derive(scan.wrappers, scan.version, static=scan.static)
        )
    )


def _policy_derive(
    wrappers: list[DeprecationWrapperInfo], spec: "_PolicySpec", findings: Optional[_PolicyFindings] = None
) -> Optional[list[_Finding]]:
    """Record and return the active policy violations, or ``None`` when no rule could run (gap printed).

    A wrapper the ``min-grace`` rule skips because its ``deprecated_in`` or ``remove_in`` is not PEP 440 is recorded as
    a gap (:func:`_record_grace_version_gap`), as is a forward epoch change that cannot be measured; strict mode then
    fails the run on it. An unset version is a deliberate choice and records nothing.

    Args:
        wrappers: The scanned wrappers of one path.
        spec: The resolved policy rules.
        findings: How the rules evaluate the wrappers; a ``--static`` scan runs each rule on the wrappers whose
            settings for it resolved (:func:`~deprecate._cli.static.checks._static_policy_findings`). ``None`` runs
            :func:`_policy_findings`, looked up at call time.

    """
    try:
        evaluate = findings if findings is not None else _policy_findings
        violations = evaluate(wrappers, spec, on_unparsable=_record_grace_version_gap)
    except ImportError as exc:
        if not _is_missing_packaging_import_error(exc):
            raise
        fallback = _policy_violations_without_packaging(wrappers, spec, findings)
        if fallback is None:
            return None
        violations = fallback
    return _record_findings(violations)


def _policy_render(violations: list[_Finding], *, advisory: bool) -> None:
    """Print one policy section: ``[ERROR]`` where the violations gate the run, else ``[WARNING]`` with a trailer."""
    if not violations:
        _print("No deprecation policy violations found.")
        return
    _Reporter.policy([finding.message for finding in violations], advisory=advisory)
    _print(f"\n{len(violations)} policy violation(s) found.")
    if advisory:
        _print("(advisory here — run `pydeprecate policy` to gate on it)")


def _policy_wrappers(
    wrappers: list[DeprecationWrapperInfo],
    spec: "_PolicySpec",
    policy_header: str,
    *,
    exit_zero: bool = False,
    advisory: bool = False,
    findings: Optional[_PolicyFindings] = None,
) -> int:
    """Report the policy violations of already-scanned wrappers and return the ``policy`` exit code.

    The evaluation half of :func:`cmd_policy` for one wrapper list.

    Args:
        wrappers: The scanned wrappers.
        spec: The resolved policy rules.
        policy_header: The ``Policy:`` line naming each rule value and its source.
        exit_zero: Exit 0 even when violations are found.
        advisory: Render violations as a yellow ``[WARNING]`` with a trailer pointing at the ``policy``
            subcommand, instead of the red ``[ERROR]`` of a gating run. ``cmd_all`` reports policy violations
            without failing on them unless ``--gate`` names ``policy``, and the output has to say so. Never inferred
            from *exit_zero*, which downgrades the exit code without changing what the violations mean.
        findings: How the rules evaluate the wrappers, see :func:`_policy_derive`.

    """
    _print(policy_header)
    violations = _policy_derive(wrappers, spec, findings)
    if violations is None:
        return 0
    _policy_render(violations, advisory=advisory)
    return _exit_code(violations, "policy", exit_zero=exit_zero)


def _run_policy(record: _RunRecord, scanned: list[tuple[_PathPlan, _ScanRecord]], *, in_all: bool) -> None:
    """Check every scanned path against its own policy rules and print the violations of all of them.

    Each distinct ``Policy:`` header is printed once. Inside ``all`` the violations of a path whose ``--gate`` names
    ``policy`` are errors and print in an ``[ERROR]`` section; the others are advisory and print as ``[WARNING]``.

    """
    for header in dict.fromkeys(plan.policy[1] for plan, _ in scanned if plan.policy is not None):
        _print(header)
    gated: Optional[list[_Finding]] = None
    advisory: Optional[list[_Finding]] = None
    for plan, scan in scanned:
        if plan.policy is None:
            continue
        _select_path(record, plan)
        derived = _policy_derive(scan.wrappers, plan.policy[0], _static_policy_findings if scan.static else None)
        if derived is None:
            continue
        if not in_all or _Check.POLICY.value in plan.gates:
            gated = [*(gated or []), *derived]
        else:
            advisory = [*(advisory or []), *derived]
    if gated is None and advisory is None:
        return
    if not gated and not advisory:
        _print("No deprecation policy violations found.")
        return
    if gated:
        _policy_render(gated, advisory=False)
    if advisory:
        _policy_render(advisory, advisory=True)


def _chain_derive(wrappers: list[DeprecationWrapperInfo]) -> list[_Finding]:
    """Record and return the active ``chain`` findings of already-scanned wrappers."""
    return _record_findings(
        [finding for info in wrappers for finding in _wrapper_findings(info) if finding.rule is _Rule.CHAIN]
    )


def _chain_render(chains: list[_Finding]) -> None:
    """Print the deprecation chains as errors, with their count."""
    if not chains:
        _print("No deprecation chains found.")
        return
    _Reporter.chains(chains, error=True)
    _print(f"\n{len(chains)} deprecation chain(s) found.")


def _chain_wrappers(wrappers: list[DeprecationWrapperInfo], *, exit_zero: bool = False) -> int:
    """Report the deprecation chains among already-scanned wrappers and return the ``chains`` exit code.

    The evaluation half of :func:`cmd_chains` for one wrapper list.

    """
    chains = _chain_derive(wrappers)
    _chain_render(chains)
    return _exit_code(chains, "chains", exit_zero=exit_zero)


def _run_chains(record: _RunRecord, scanned: list[tuple[_PathPlan, _ScanRecord]]) -> None:
    """Derive the deprecation chains of every scanned path and print them as one section."""
    _chain_render(_derive_each(record, scanned, lambda _plan, scan: _chain_derive(scan.wrappers)) or [])


# ---------------------------------------------------------------------------
# Subcommand functions
# ---------------------------------------------------------------------------


def cmd_check(
    path: _PathArg = ".",
    recursive: bool = True,
    exit_zero: bool = False,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    include_members: bool = True,
    strict: _ConfigBool = _FROM_PYPROJECT,
    ignore: _ConfigList = _FROM_PYPROJECT,
    baseline: _ConfigFlag = _FROM_PYPROJECT,
) -> int:
    """Scan Python code for misconfigured ``@deprecated`` wrappers and deprecation chains.

    Reports invalid argument mappings, misconfigured targets and inner-order ``@property @deprecated`` wrappers
    (errors, exit 1), and identity mappings, no-effect wrappers, deprecation chains and ``args_mapping`` entries
    targeting POSITIONAL_ONLY constructor parameters (warnings only, exit 0). Use the ``chains`` subcommand for a
    dedicated hard-error chain check.

    Args:
        path: Path to the module, package directory, or importable module name to scan; several paths are scanned in
            one run, each with its own ``pyproject.toml`` settings (a list in a direct call).
        recursive: Scan submodules recursively (default True). Pass ``--no-recursive`` to scan top-level only.
        exit_zero: Always exit 0 even if hard errors (invalid argument mappings) are found.
            Useful for advisory CI steps that should report but never block.
        exclude: Module-name glob patterns to leave out of the scan — one pattern or a comma-separated string
            (``--exclude='my_package.tests,*._legacy*'``), or a list in a direct call; the scan itself never imports
            a matching package nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the
            nearest ``pyproject.toml``, else nothing.
        include_members: Include deprecated class members such as methods and constructors (default True). Pass
            ``--no-include-members`` to check module-level wrappers only.
        strict: Fail the run (exit 1) when any part of the audit did not run — a ``pydeprecate gap:`` line such as a
            module that could not be imported or a version that could not be resolved. Default: ``strict`` of
            ``[tool.pydeprecate]`` in the nearest ``pyproject.toml``, else off; ``--no-strict`` switches it off.
            A typed ``--strict`` cannot be combined with ``exit_zero`` (usage error); a typed ``exit_zero`` overrides a
            ``strict = true`` that only ``pyproject.toml`` sets, with a stderr note.
        ignore: Findings to accept without fixing them: entries ``module:symbol`` (every rule) or
            ``rule:module:symbol`` (one rule, or ``*``), each part an ``fnmatch`` glob, comma-separated and repeatable
            (``--ignore='pkg.legacy:old_api'``). Default: ``ignore`` of ``[tool.pydeprecate]``, else nothing;
            ``--ignore=`` switches the file's list off. Suppressed findings are not printed, counted or gated on.
        baseline: A ``--report-json`` file (pyDeprecate 0.16+) whose findings are accepted by id. Default: ``baseline``
            of ``[tool.pydeprecate]`` (relative to that ``pyproject.toml``), else none; ``--baseline=`` switches it off.

    Returns:
        0 on success or warnings only; 1 when an error is found (or, in strict mode, a gap) and ``exit_zero`` is False;
        2 for a usage error on any path, before anything is scanned (a missing submodule of an importable package: once
        its path is reached): a malformed ``--exclude`` or ``strict``, ``strict`` with ``exit_zero``, a *path* that is a
        file, a directory holding several packages, or neither an existing path nor an importable module, or two paths
        importing as the same name from different places. Raises ``_ScanError`` (exit 3) when a scanned package itself
        cannot be imported.

    """
    options = _RunOptions(recursive, exclude, include_members, exit_zero, strict, ignore, baseline)
    with _ensure_run_record() as record:
        start = _start_gate(record, path, options)
        if start is None:
            return 2
        scanned = _scan_plans(start.plans, options)
        if scanned is None:
            return 2
        _run_check(record, scanned)
        return _finish_gate(start, "check", exit_zero=exit_zero)


def cmd_expiry(
    path: _PathArg = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    exit_zero: bool = False,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    include_members: bool = True,
    strict: _ConfigBool = _FROM_PYPROJECT,
    ignore: _ConfigList = _FROM_PYPROJECT,
    baseline: _ConfigFlag = _FROM_PYPROJECT,
    static: bool = False,
) -> int:
    """Check for deprecated wrappers that have passed their scheduled removal version.

    Requires the ``packaging`` library: ``pip install 'pyDeprecate[audit]'``.
    A missing ``packaging`` library is treated as advisory (returns 0 with a warning).

    Args:
        path: Path to the module, package directory, or importable module name to scan; several paths are scanned in
            one run, each against its own version and ``pyproject.toml`` settings (a list in a direct call).
        version: Current package version for comparison (e.g. ``"2.0.0"``), applied to every path. Auto-detected per
            path from local project metadata, installed package metadata, or the top-level package's ``__version__``
            if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--no-recursive`` to scan top-level only.
        exit_zero: Always exit 0 even if expired wrappers are found.
            Useful for advisory CI steps that should report but never block.
        exclude: Module-name glob patterns to leave out of the scan — one pattern or a comma-separated string
            (``--exclude='my_package.tests,*._legacy*'``), or a list in a direct call; the scan itself never imports
            a matching package nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the
            nearest ``pyproject.toml``, else nothing.
        include_members: Include deprecated class members such as methods and constructors (default True). Pass
            ``--no-include-members`` to check module-level wrappers only.
        strict: Fail the run (exit 1) when any part of the audit did not run — a ``pydeprecate gap:`` line such as a
            module that could not be imported or a version that could not be resolved. Default: ``strict`` of
            ``[tool.pydeprecate]`` in the nearest ``pyproject.toml``, else off; ``--no-strict`` switches it off.
            A typed ``--strict`` cannot be combined with ``exit_zero`` (usage error); a typed ``exit_zero`` overrides a
            ``strict = true`` that only ``pyproject.toml`` sets, with a stderr note.
        ignore: Findings to accept without fixing them: entries ``module:symbol`` (every rule) or
            ``rule:module:symbol`` (one rule, or ``*``), each part an ``fnmatch`` glob, comma-separated and repeatable
            (``--ignore='pkg.legacy:old_api'``). Default: ``ignore`` of ``[tool.pydeprecate]``, else nothing;
            ``--ignore=`` switches the file's list off. Suppressed findings are not printed, counted or gated on.
        baseline: A ``--report-json`` file (pyDeprecate 0.16+) whose findings are accepted by id. Default: ``baseline``
            of ``[tool.pydeprecate]`` (relative to that ``pyproject.toml``), else none; ``--baseline=`` switches it off.
        static: Read the source with ``ast`` instead of importing the package, for an environment without its
            runtime dependencies (default False). Nothing from the package is imported or executed; a wrapper or
            ``remove_in`` the source alone does not settle is a ``not statically resolvable`` gap, not a finding.

    Returns:
        0 on success, when the ``packaging`` library is unavailable, when no version could be resolved (the gap is
        printed and the check is skipped), or when an auto-detected version turns out not to be valid PEP 440 (advisory
        — the check is skipped); 1 when expired wrappers are found (or, in strict mode, any gap is recorded, including
        the three skips above) and ``exit_zero`` is False; 2 for a usage error on any path, before anything is scanned
        (a missing submodule of an importable package: once its path is reached): a user-supplied ``--version`` that is
        not a valid PEP 440 version string, a malformed ``--exclude`` or ``strict``, ``strict`` with ``exit_zero``, a
        *path* that is a file, a directory holding several packages, or neither an existing path nor an importable
        module, or two paths importing as the same name from different places. Raises ``_ScanError`` (exit 3) when a
        scanned package itself cannot be imported.

    """
    err_code = _validate_user_version(version)
    if err_code is not None:
        return err_code
    options = _RunOptions(recursive, exclude, include_members, exit_zero, strict, ignore, baseline, static=static)
    with _ensure_run_record() as record:
        start = _start_gate(record, path, options)
        if start is None:
            return 2
        scanned = _scan_plans(start.plans, options, version=version, resolve_version=True)
        if scanned is None:
            return 2
        _run_expiry(record, scanned)
        return _finish_gate(start, "expiry", exit_zero=exit_zero)


def cmd_policy(
    path: _PathArg = ".",
    recursive: bool = True,
    exit_zero: bool = False,
    min_grace: _ConfigFlag = _FROM_PYPROJECT,
    message_required: _ConfigBool = _FROM_PYPROJECT,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    include_members: bool = True,
    strict: _ConfigBool = _FROM_PYPROJECT,
    ignore: _ConfigList = _FROM_PYPROJECT,
    baseline: _ConfigFlag = _FROM_PYPROJECT,
    static: bool = False,
) -> int:
    """Check deprecated wrappers against deprecation-governance policy rules.

    Where ``expiry`` asks whether a wrapper was removed on time, ``policy`` asks whether it was scheduled
    responsibly: a removal deadline that leaves callers too short a grace window or lands off a release
    boundary, or a warning that never names a replacement.

    The ``min-grace`` rule needs the ``packaging`` library (``pip install 'pyDeprecate[audit]'``) for version
    comparison; ``message-required`` does not. When ``packaging`` is unavailable, ``min-grace`` is skipped with
    an advisory warning; ``message_required`` still runs and gates the exit code normally. The gate only fully
    no-ops (return 0 with a warning) when ``min-grace`` was requested and ``message_required`` is also disabled.
    A wrapper whose ``deprecated_in`` or ``remove_in`` is set but not PEP 440 is skipped by ``min-grace`` and recorded
    as a ``deprecated_in not PEP 440`` or ``remove_in not PEP 440`` gap, in import and ``--static`` mode alike.

    Each rule is resolved as flag > ``[tool.pydeprecate.policy]`` in the nearest ``pyproject.toml`` (the scanned
    directory or up to two levels above it) > built-in default, and the header names the source of each. The
    table uses the rule slugs as keys — ``min-grace = "0.3"``, ``message-required = true`` — and ``false`` for
    ``min-grace`` where the flag would take ``None`` (TOML has no null). It is only consulted for an existing
    path, never for a bare module name.

    Args:
        path: Path to the module, package directory, or importable module name to scan; several paths are scanned in
            one run, each against its own ``pyproject.toml`` rules (a list in a direct call).
        recursive: Scan submodules recursively (default True). Pass ``--no-recursive`` to scan top-level only.
        exit_zero: Always exit 0 even if violations are found.
            Useful for advisory CI steps that should report but never block.
        min_grace: Minimum distance between ``deprecated_in`` and ``remove_in`` as a version-shaped delta with
            two or three components — ``1.0`` one major, ``0.3`` three minors, ``0.0.2`` two patches (a bare
            ``1`` is rejected as ambiguous) — or, in ``pyproject.toml`` and a direct call only, a one-key unit table
            (``{"minor": 3}``); built-in default ``0.3``, ``None`` skips the rule. The removal must be one clean
            bump of a single component (``1.2`` → ``1.5`` or ``2.0``, never ``2.3``); a coarser bump always clears
            a finer window.
        message_required: Require every wrapper to name a replacement (built-in default True).
        exclude: Module-name glob patterns to leave out of the scan — one pattern or a comma-separated string
            (``--exclude='my_package.tests,*._legacy*'``), or a list in a direct call; the scan itself never imports
            a matching package nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the
            nearest ``pyproject.toml``, else nothing.
        include_members: Include deprecated class members such as methods and constructors (default True). Pass
            ``--no-include-members`` to check module-level wrappers only.
        strict: Fail the run (exit 1) when any part of the audit did not run — a ``pydeprecate gap:`` line such as a
            module that could not be imported or a version that could not be resolved. Default: ``strict`` of
            ``[tool.pydeprecate]`` in the nearest ``pyproject.toml``, else off; ``--no-strict`` switches it off.
            A typed ``--strict`` cannot be combined with ``exit_zero`` (usage error); a typed ``exit_zero`` overrides a
            ``strict = true`` that only ``pyproject.toml`` sets, with a stderr note.
        ignore: Findings to accept without fixing them: entries ``module:symbol`` (every rule) or
            ``rule:module:symbol`` (one rule, or ``*``), each part an ``fnmatch`` glob, comma-separated and repeatable
            (``--ignore='pkg.legacy:old_api'``). Default: ``ignore`` of ``[tool.pydeprecate]``, else nothing;
            ``--ignore=`` switches the file's list off. Suppressed findings are not printed, counted or gated on.
        baseline: A ``--report-json`` file (pyDeprecate 0.16+) whose findings are accepted by id. Default: ``baseline``
            of ``[tool.pydeprecate]`` (relative to that ``pyproject.toml``), else none; ``--baseline=`` switches it off.
        static: Read the source with ``ast`` instead of importing the package, for an environment without its
            runtime dependencies (default False). Nothing from the package is imported or executed; each rule checks
            the wrappers whose settings for it the source settles, and every other one is a ``not statically
            resolvable`` gap, not a finding.

    Returns:
        0 on success, or when ``min-grace`` is skipped because ``packaging`` is unavailable and ``message_required``
        finds no violation; 1 when violations are found (including from a still-running ``message_required`` check), or
        in strict mode a gap is recorded, and ``exit_zero`` is False; 2 for a usage error on any path, before anything
        is scanned (a missing submodule of an importable package: once its path is reached): a rule's value, ``exclude``
        or ``strict`` — from a flag or from ``pyproject.toml`` — that is malformed, ``strict`` with ``exit_zero``, a
        ``policy`` key in ``[tool.pydeprecate]`` that is not a table, a ``pyproject.toml`` within reach that cannot be
        read or parsed, a *path* that is a file, a directory holding several packages, or neither an existing path nor
        an importable module, or two paths importing as the same name from different places. Raises ``_ScanError``
        (exit 3) when a scanned package itself cannot be imported.

    """
    policy_flags = {PolicyRule.MIN_GRACE.value: min_grace, PolicyRule.MESSAGE_REQUIRED.value: message_required}
    options = _RunOptions(
        recursive,
        exclude,
        include_members,
        exit_zero,
        strict,
        ignore,
        baseline,
        policy_flags=policy_flags,
        static=static,
    )
    with _ensure_run_record() as record:
        start = _start_gate(record, path, options)
        if start is None:
            return 2
        scanned = _scan_plans(start.plans, options)
        if scanned is None:
            return 2
        _run_policy(record, scanned, in_all=False)
        return _finish_gate(start, "policy", exit_zero=exit_zero)


def cmd_chains(
    path: _PathArg = ".",
    recursive: bool = True,
    exit_zero: bool = False,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    include_members: bool = True,
    strict: _ConfigBool = _FROM_PYPROJECT,
    ignore: _ConfigList = _FROM_PYPROJECT,
    baseline: _ConfigFlag = _FROM_PYPROJECT,
) -> int:
    """Detect deprecated wrappers whose ``target`` is itself a deprecated callable (chains).

    Two chain kinds are detected: ``target`` (forwarding chain to another deprecated
    callable) and ``stacked`` (composed argument mappings that should be collapsed).

    Args:
        path: Path to the module, package directory, or importable module name to scan; several paths are scanned in
            one run, each with its own ``pyproject.toml`` settings (a list in a direct call).
        recursive: Scan submodules recursively (default True). Pass ``--no-recursive`` to scan top-level only.
        exit_zero: Always exit 0 even if chains are found.
            Useful for advisory CI steps that should report but never block.
        exclude: Module-name glob patterns to leave out of the scan — one pattern or a comma-separated string
            (``--exclude='my_package.tests,*._legacy*'``), or a list in a direct call; the scan itself never imports
            a matching package nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the
            nearest ``pyproject.toml``, else nothing.
        include_members: Include deprecated class members such as methods and constructors (default True). Pass
            ``--no-include-members`` to check module-level wrappers only.
        strict: Fail the run (exit 1) when any part of the audit did not run — a ``pydeprecate gap:`` line such as a
            module that could not be imported or a version that could not be resolved. Default: ``strict`` of
            ``[tool.pydeprecate]`` in the nearest ``pyproject.toml``, else off; ``--no-strict`` switches it off.
            A typed ``--strict`` cannot be combined with ``exit_zero`` (usage error); a typed ``exit_zero`` overrides a
            ``strict = true`` that only ``pyproject.toml`` sets, with a stderr note.
        ignore: Findings to accept without fixing them: entries ``module:symbol`` (every rule) or
            ``rule:module:symbol`` (one rule, or ``*``), each part an ``fnmatch`` glob, comma-separated and repeatable
            (``--ignore='pkg.legacy:old_api'``). Default: ``ignore`` of ``[tool.pydeprecate]``, else nothing;
            ``--ignore=`` switches the file's list off. Suppressed findings are not printed, counted or gated on.
        baseline: A ``--report-json`` file (pyDeprecate 0.16+) whose findings are accepted by id. Default: ``baseline``
            of ``[tool.pydeprecate]`` (relative to that ``pyproject.toml``), else none; ``--baseline=`` switches it off.

    Returns:
        0 when no chains are found or ``exit_zero`` is True; 1 when chains are found (or, in strict mode, a gap is
        recorded); 2 for a usage error on any path, before anything is scanned (a missing submodule of an importable
        package: once its path is reached): a malformed ``--exclude`` or ``strict``, ``strict`` with ``exit_zero``, a
        *path* that is a file, a directory holding several packages, or neither an existing path nor an importable
        module, or two paths importing as the same name from different places. Raises ``_ScanError`` (exit 3) when a
        scanned package itself cannot be imported.

    """
    options = _RunOptions(recursive, exclude, include_members, exit_zero, strict, ignore, baseline)
    with _ensure_run_record() as record:
        start = _start_gate(record, path, options)
        if start is None:
            return 2
        # every wrapper, not only chains: the run record needs them all for its table; the chain filter is below
        scanned = _scan_plans(start.plans, options)
        if scanned is None:
            return 2
        _run_chains(record, scanned)
        return _finish_gate(start, "chains", exit_zero=exit_zero)


def cmd_all(
    path: _PathArg = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    exit_zero: bool = False,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
    output: Optional[str] = None,
    include_members: bool = True,
    strict: _ConfigBool = _FROM_PYPROJECT,
    ignore: _ConfigList = _FROM_PYPROJECT,
    baseline: _ConfigFlag = _FROM_PYPROJECT,
    gate: _ConfigList = _FROM_PYPROJECT,
) -> int:
    """Run all four checks then append a deprecation table.

    Performs a single scan pass per path and hands the wrappers to the checks of ``check``, ``expiry``, ``policy`` and
    ``chains`` so the filesystem is only traversed once. After all four checks complete, the compact markdown
    deprecation table of ``status`` is always appended to the output, one per path under a ``### <package>`` heading
    when several paths are scanned. The policy check runs in advisory mode here — its violations are printed but never
    change the aggregate exit code, because its defaults encode a project convention — unless ``gate`` names
    ``policy``: then they are errors, exactly as in a ``pydeprecate policy`` run, from the same single scan.
    Version is auto-detected when not provided, exactly as for standalone ``expiry`` and ``status``.
    A missing ``packaging`` library skips the expiry check with a warning and does not
    count as a hard error.

    Args:
        path: Path to the module, package directory, or importable module name to scan; several paths are scanned in
            one run, each against its own version and ``pyproject.toml`` settings (a list in a direct call).
        version: Current package version for expiry comparison (e.g. ``"2.0.0"``), applied to every path.
            Auto-detected per path from local project metadata, installed package metadata, or the top-level package's
            ``__version__`` if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--no-recursive`` to scan top-level only.
        exit_zero: Always exit 0 even if issues are found.
            Useful for advisory CI steps that should report but never block.
        exclude: Module-name glob patterns to leave out of the scan — one pattern or a comma-separated string
            (``--exclude='my_package.tests,*._legacy*'``), or a list in a direct call; the scan itself never imports
            a matching package nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the
            nearest ``pyproject.toml``, else nothing.
        output: Optional file path the appended deprecation table is also written to, as with ``status --output``,
            so a CI job gets the table of the very scan it gated on. A failure to write it is reported on stderr as
            ``Could not write the deprecation table`` — apart from a failure to render the table — and never changes
            the exit code.
        include_members: Include deprecated class members such as methods and constructors (default True) in the one
            scan every check and the table share. Pass ``--no-include-members`` to check module-level wrappers only.
        strict: Fail the run (exit 1) when any part of the audit did not run — a ``pydeprecate gap:`` line such as a
            module that could not be imported or a version that could not be resolved. Default: ``strict`` of
            ``[tool.pydeprecate]`` in the nearest ``pyproject.toml``, else off; ``--no-strict`` switches it off.
            A typed ``--strict`` cannot be combined with ``exit_zero`` (usage error); a typed ``exit_zero`` overrides a
            ``strict = true`` that only ``pyproject.toml`` sets, with a stderr note.
        ignore: Findings to accept without fixing them: entries ``module:symbol`` (every rule) or
            ``rule:module:symbol`` (one rule, or ``*``), each part an ``fnmatch`` glob, comma-separated and repeatable
            (``--ignore='pkg.legacy:old_api'``). Default: ``ignore`` of ``[tool.pydeprecate]``, else nothing;
            ``--ignore=`` switches the file's list off. Suppressed findings are not printed, counted or gated on.
        baseline: A ``--report-json`` file (pyDeprecate 0.16+) whose findings are accepted by id. Default: ``baseline``
            of ``[tool.pydeprecate]`` (relative to that ``pyproject.toml``), else none; ``--baseline=`` switches it off.
        gate: Checks to gate on beyond ``check``, ``expiry`` and ``chains``, which always gate: one of ``check``,
            ``expiry``, ``policy``, ``chains`` or a comma-separated list (``--gate=policy``), or a list in a direct
            call. A listed check fails the run as its own subcommand would; today only ``policy`` changes anything.
            Default: ``gate`` of ``[tool.pydeprecate]``, else none; ``--gate=`` switches the file's list off.

    Returns:
        0 when the check, expiry, and chain gates pass (and the policy gate, when ``gate`` names it) or ``exit_zero``
        is True; 1 when any of them finds an error on any path, or in strict mode a strict path recorded a gap (one
        exit code computed from all their findings and gaps); 2 for a usage error on any path, before anything is
        scanned, which ``exit_zero`` never downgrades: a user-supplied ``--version`` that is not a valid PEP 440 version
        string, a malformed ``exclude``, ``strict`` or ``gate`` (flag or file), ``strict`` with ``exit_zero``, a
        ``pyproject.toml`` within reach that cannot be read or parsed, a ``policy`` key in ``[tool.pydeprecate]`` that
        is not a table, a malformed rule value in ``[tool.pydeprecate.policy]``, a *path* that is a file, a directory
        holding several packages, or neither an existing path nor an importable module, or two paths importing as the
        same name from different places. Raises ``_ScanError`` (exit 3) when a scanned package itself cannot be
        imported. Policy violations are advisory here and never contribute to this code unless ``gate`` names
        ``policy``.

    """
    err_code = _validate_user_version(version)
    if err_code is not None:
        return err_code
    # a malformed `[tool.pydeprecate.policy]` value is a usage error, reported before the package is imported
    policy_flags: dict[str, _ConfigFlag] = {
        rule.value: _FROM_PYPROJECT for rule in (PolicyRule.MIN_GRACE, PolicyRule.MESSAGE_REQUIRED)
    }
    options = _RunOptions(
        recursive, exclude, include_members, exit_zero, strict, ignore, baseline, gate=gate, policy_flags=policy_flags
    )
    with _ensure_run_record() as run_record:
        start = _start_gate(run_record, path, options)
        if start is None:
            return 2
        scanned = _scan_plans(start.plans, options, version=version, resolve_version=True, record=False)
        if scanned is None:
            return 2
        # The checks record their findings and the exit code of ``all`` is computed once below from those findings and
        # the run's gaps, at the levels ``all`` gives them (policy violations are warnings here unless gated). Each
        # path's version may be auto-detected; only a typed one was validated, so a package stamped with a non-PEP 440
        # version skips the expiry check with an advisory instead of failing as an invalid ``--version``.
        _run_check(run_record, scanned)
        _run_expiry(run_record, scanned)
        # Advisory inside ``all`` by default: the policy defaults encode a project convention (a three-minor grace
        # window on a clean release boundary) that not every repo shares; ``--gate=policy`` opts in to gating on it.
        _run_policy(run_record, scanned, in_all=True)
        _run_chains(run_record, scanned)
        _append_tables(run_record, scanned, output, version_explicit=version is not None)
        return _finish_gate(start, "all", exit_zero=exit_zero)


def _append_tables(
    run_record: _RunRecord,
    scanned: list[tuple[_PathPlan, _ScanRecord]],
    output: Optional[str],
    *,
    version_explicit: bool,
) -> None:
    """Render, record and print the deprecation table of every scanned path after the gates of ``all``.

    The tables are display artifacts: a table-rendering failure must never change the exit code the gates produced.
    Each is recorded as it is rendered and the ``--output`` file is saved from that record, so a failure to write the
    file is told apart from one to render.

    """
    headed = len(scanned) > 1
    for plan, scan in scanned:
        try:
            _status_table(
                scan.wrappers,
                plan.path,
                scan.version,
                style=TableStyle("compact"),
                version_explicit=version_explicit,
                heading=headed,
            )
        except Exception as exc:
            _print(f"Could not render the deprecation table: {exc}", stderr=True)
    if output is not None and run_record.table is not None:
        try:
            _save_table(run_record.table, output)
        except OSError as exc:
            _print(f"Could not write the deprecation table to {output}: {exc}", stderr=True)


def cmd_status(
    path: _PathArg = ".",
    version: Optional[str] = None,
    recursive: bool = True,
    style: str = "compact",
    include_members: bool = True,
    output: Optional[str] = None,
    exclude: _ConfigFlag = _FROM_PYPROJECT,
) -> int:
    """Print a markdown deprecation status table to stdout.

    Scans the target package for deprecated wrappers and renders their lifecycle status as a Markdown table.
    Standalone — runs no checks and never fails on findings. When ``--output`` is given, the table is also written
    to that file. Several paths each get their own table under a ``### <package>`` heading.

    Args:
        path: Path to the module, package directory, or importable module name to scan; several paths are scanned in
            one run, each against its own version (a list in a direct call).
        version: Current package version for lifecycle status (e.g. ``"2.0.0"``), applied to every path.
            Auto-detected per path from local project metadata, installed package metadata, or the top-level package's
            ``__version__`` if not provided.
        recursive: Scan submodules recursively (default True). Pass ``--no-recursive`` to scan top-level only.
        style: Table format — ``compact`` (default) or ``matrix``.
        include_members: Include deprecated class members such as methods and constructors (default True). Pass
            ``--no-include-members`` to list module-level wrappers only.
        output: Optional file path to write the markdown table. The table is always
            printed to stdout regardless of this flag.
        exclude: Module-name glob patterns to leave out of the scan — one pattern or a comma-separated string
            (``--exclude='my_package.tests,*._legacy*'``), or a list in a direct call; the scan itself never imports
            a matching package nor descends into it. Default: the ``exclude`` list of ``[tool.pydeprecate]`` in the
            nearest ``pyproject.toml``, else nothing.

    Returns:
        0 once the table is rendered — status is not a pass/fail gate; 2 for a usage error on any path, before anything
        is scanned (a missing submodule of an importable package: once its path is reached): a user-supplied
        ``--version`` that is not a valid PEP 440 version string, a ``--style`` other than ``compact`` or ``matrix``, a
        malformed ``--exclude``, a *path* that is a file, a directory holding several packages, or neither an existing
        path nor an importable module, or two paths importing as the same name from different places. Raises
        ``_ScanError`` (exit 3) when a scanned package itself cannot be imported.

    """
    err_code = _validate_user_version(version)
    if err_code is not None:
        return err_code
    try:
        table_style = TableStyle(style)
    except ValueError:
        valid = ", ".join(s.value for s in TableStyle)
        _print(f"Invalid `--style` {style!r}; expected one of: {valid}.", stderr=True)
        return 2
    options = _RunOptions(recursive, exclude, include_members, gating=False)
    plans = _plan_paths(_as_paths(path), options)
    if plans is None:
        return 2
    scanned = _scan_plans(plans, options, version=version, resolve_version=True, record=False)
    if scanned is None:
        return 2
    tables = [
        (
            _safe_module_name(plan.path),
            _status_table(
                scan.wrappers,
                plan.path,
                scan.version,
                style=table_style,
                version_explicit=version is not None,
                heading=len(scanned) > 1,
            ),
        )
        for plan, scan in scanned
    ]
    markdown = _join_tables(tables)
    if output is not None and markdown is not None:
        _save_table(markdown, output)
    return 0


def _status_table(
    wrappers: list[DeprecationWrapperInfo],
    path: str,
    version: Optional[str],
    *,
    style: TableStyle,
    version_explicit: bool,
    heading: bool = False,
) -> str:
    """Render, record and print the deprecation table of already-scanned wrappers, and return it.

    The rendering half of :func:`cmd_status`, which ``cmd_all`` runs on the wrappers and the version of each scan.
    *version_explicit* tells a typed version from an auto-detected one, so a package stamped with a non-PEP 440 version
    still renders its table (unparsed) instead of aborting it. With *heading* (a run over several paths) the table is
    printed under a ``### <package>`` line.

    """
    # Never resolve the importable module name eagerly: a plain directory without ``__init__.py`` has none, and
    # status still renders its table. ``_safe_module_name`` falls back to the raw path, used only as table metadata.
    module_name = _safe_module_name(path)
    resolved_version, parsed_version = _resolve_table_version(
        module_name, current_version=version, version_explicit=version_explicit
    )
    markdown = _render_deprecation_table(wrappers, resolved_version, parsed_version, style)
    _record_table(markdown, module_name)
    if heading:
        _print(f"### {module_name}\n")
    _print(markdown)
    return markdown
