"""Deprecation-policy lint behind :func:`~deprecate.audit.validate_deprecation_policy`.

Two independently switchable rules: the grace window (``min_grace``, spelled by users as a dotted delta or unit table
and held internally as the strict :class:`GraceWindow`) and migration guidance (``message_required``).

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import enum
import importlib
import importlib.metadata
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:
    from packaging.version import Version

from deprecate._types import TargetMode
from deprecate.audit._lifecycle import _parse_version
from deprecate.audit._scan import find_deprecation_wrappers
from deprecate.audit._wrappers import DeprecationWrapperInfo, _format_subject


class PolicyRule(str, enum.Enum):
    """Governance rule checked by :func:`~deprecate.audit.validate_deprecation_policy`.

    Each member's value is the slug that prefixes the rule's violation messages, so a CI log can be grouped or
    filtered by rule. Every rule is switched by its own keyword argument, mirrored by a ``pydeprecate policy``
    flag of the same name.

    Attributes:
        MIN_GRACE: ``remove_in`` must be one clean bump of a single version component beyond ``deprecated_in``,
            at least ``min_grace`` steps of it -- a version-shaped delta: ``"0.3"`` three minors (default), ``"1"``
            one major, ``"0.0.2"`` two patches; ``None`` disables. A coarser bump clears a finer window (``1.2``
            -> ``2.0`` satisfies ``"0.3"``), a mixed bump never passes (``1.2`` -> ``2.3`` is neither a major nor
            a minor step), and a ``remove_in`` at or before ``deprecated_in`` always fails. Versions spanning a
            PEP 440 epoch, or that do not parse, are skipped with a :class:`UserWarning`.
        MESSAGE_REQUIRED: the wrapper must name what to migrate *to* -- a ``target``, a non-empty
            ``args_mapping``/``attrs_mapping``, or a non-empty custom ``message_template``; ``message_required=False``
            disables. An explicit :attr:`~deprecate.TargetMode.NOTIFY` counts only the template; a deprecated module
            only target and mappings. The one rule that runs without ``packaging``.

    Examples:
        >>> PolicyRule.MIN_GRACE.value
        'min-grace'

    """

    MIN_GRACE = "min-grace"
    MESSAGE_REQUIRED = "message-required"


class VersionBump(str, enum.Enum):
    """Version component a ``min_grace`` window is counted in -- ``"0.1"`` is one :attr:`MINOR`.

    Examples:
        >>> VersionBump("minor") is VersionBump.MINOR
        True

    """

    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"


#: Version component addressed by each position of a ``min_grace`` delta — ``"1"`` is majors, ``"0.1"`` minors,
#: ``"0.0.1"`` patches — in the same order :class:`~packaging.version.Version` exposes them.
_GRACE_WINDOW_UNITS = (VersionBump.MAJOR, VersionBump.MINOR, VersionBump.PATCH)


_GRACE_WINDOW_UNITS_BY_NAME = {unit.value: unit for unit in _GRACE_WINDOW_UNITS}
_GRACE_WINDOW_SPELLINGS = (
    "`1.0` (one major), `0.1` (one minor), `0.0.1` (one patch), or a one-key table such as `{'minor': 1}`"
)


def _grace_window_error(min_grace: object, detail: str) -> ValueError:
    """Build the uniform ``ValueError`` for a ``min_grace`` value the parser does not accept."""
    return ValueError(
        f"Invalid `min_grace` specification `{min_grace}`; {detail} — expected {_GRACE_WINDOW_SPELLINGS}."
    )


@dataclass(frozen=True)
class GraceWindow:
    """Strict form of a ``min_grace`` window: ``count`` steps of exactly one version component ``unit``.

    This is what every user-facing spelling is converted into before a policy scan starts, so the single-unit
    rule is structural rather than something each consumer re-checks: an instance cannot name two units, and
    construction rejects a negative or non-integer count. Build one directly, or from any accepted spelling with
    :meth:`parse` — a dotted delta (``"0.3"``), the float Fire or TOML may hand over, or a one-key table.

    Attributes:
        count: Minimum number of bumps required; ``0`` disables the distance check while still requiring a
            clean bump of ``unit`` or coarser.
        unit: Version component the bumps are counted in.

    Examples:
        >>> GraceWindow(3, VersionBump.MINOR)
        GraceWindow(count=3, unit=<VersionBump.MINOR: 'minor'>)
        >>> GraceWindow(-1, VersionBump.MINOR)
        Traceback (most recent call last):
            ...
        ValueError: Invalid `min_grace` specification `GraceWindow(count=-1, ...)`; ...

    """

    count: int
    unit: VersionBump

    def __post_init__(self) -> None:
        """Reject a count or unit no accepted spelling could have produced."""
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 0:
            raise _grace_window_error(self, "the count must be a non-negative integer")
        if not isinstance(self.unit, VersionBump):
            raise _grace_window_error(self, f"unknown unit `{self.unit}`")

    @classmethod
    def parse(cls, min_grace: "GraceWindowSpec") -> "GraceWindow":
        """Convert any accepted ``min_grace`` spelling into a :class:`GraceWindow`.

        Two spellings, plus an instance passed through unchanged. The **dotted delta** is shaped like a version
        with two or three components and a single non-zero one — its position is the unit, the number the count:
        ``"1.0"`` one major, ``"0.3"`` three minors, ``"0.0.2"`` two patches. A bare ``"1"`` is rejected as
        ambiguous (write ``"1.0"``), and so is ``"1.2"`` — two units have no meaning under the coarser-bump
        rule. A ``float`` is read as its text (``0.3``, ``1.0``), which drops a trailing zero (``0.10`` is
        ``0.1``): quote ten or more steps. The **table** names the unit outright — ``{"major": 1}``,
        ``{"minor": 3}``, ``{"patch": 2}`` — and can only ever carry one unit.

        Args:
            min_grace: The dotted delta as a string or float, a one-key mapping from unit name to count, or an
                already-built window.

        Returns:
            The strict window.

        Raises:
            ValueError: If the specification is not of any accepted shape.

        Examples:
            >>> GraceWindow.parse("0.1")
            GraceWindow(count=1, unit=<VersionBump.MINOR: 'minor'>)
            >>> GraceWindow.parse("2.0")
            GraceWindow(count=2, unit=<VersionBump.MAJOR: 'major'>)
            >>> GraceWindow.parse({"patch": 3})
            GraceWindow(count=3, unit=<VersionBump.PATCH: 'patch'>)
            >>> GraceWindow.parse(0.3)
            GraceWindow(count=3, unit=<VersionBump.MINOR: 'minor'>)

        """
        if isinstance(min_grace, GraceWindow):
            return min_grace
        if isinstance(min_grace, Mapping):
            return cls._parse_table(min_grace)
        if isinstance(min_grace, bool) or not isinstance(min_grace, (str, float)):
            raise _grace_window_error(min_grace, "a bare number does not say which release level it counts")
        spec = str(min_grace).strip()
        parts = spec.split(".")
        valid = 2 <= len(parts) <= 3 and all(part.isdigit() for part in parts)
        nonzero = [i for i, part in enumerate(parts) if valid and int(part)]
        if not valid or len(nonzero) > 1:
            raise _grace_window_error(spec, "a dotted delta needs two or three components with at most one non-zero")
        # All zeros (`"0.0"`) is a zero-count window in the unit of its last position.
        position = nonzero[0] if nonzero else len(parts) - 1
        return cls(int(parts[position]), _GRACE_WINDOW_UNITS[position])

    @classmethod
    def _parse_table(cls, min_grace: Mapping[str, int]) -> "GraceWindow":
        """Parse the table form ``{"minor": 3}`` — exactly one unit key; the count is validated on construction."""
        if len(min_grace) != 1:
            raise _grace_window_error(dict(min_grace), "a table must name exactly one unit")
        ((unit_name, count),) = min_grace.items()
        unit_key = unit_name.value if isinstance(unit_name, VersionBump) else unit_name
        if unit_key not in _GRACE_WINDOW_UNITS_BY_NAME:
            raise _grace_window_error(dict(min_grace), f"unknown unit `{unit_key}`")
        return cls(count, _GRACE_WINDOW_UNITS_BY_NAME[unit_key])

    def describe(self) -> str:
        """Render the window as prose, e.g. ``"1 minor release"`` or ``"2 major releases"``.

        Examples:
            >>> GraceWindow(1, VersionBump.MINOR).describe()
            '1 minor release'
            >>> GraceWindow(0, VersionBump.PATCH).describe()
            '0 patch releases'

        """
        return f"{self.count} {self.unit.value} release{'' if self.count == 1 else 's'}"


#: Accepted ``min_grace`` value: a dotted delta string (``"0.3"``), a float Fire/TOML may hand over, a one-key
#: table, or an already-strict :class:`GraceWindow`.
GraceWindowSpec = Union[str, float, Mapping[str, int], GraceWindow]


@dataclass(frozen=True)
class _PolicySpec:
    """Parsed, validated policy configuration shared by every wrapper in one policy scan.

    Attributes:
        grace: Minimum distance required between ``deprecated_in`` and ``remove_in``, or ``None`` when the rule
            is disabled.
        message_required: Whether every wrapper must offer migration guidance.

    """

    grace: Optional[GraceWindow]
    message_required: bool


def _format_release_boundaries(unit: VersionBump) -> str:
    """Name the release levels a removal may land on for a window counted in ``unit`` -- ``unit`` and coarser.

    Examples:
        >>> _format_release_boundaries(VersionBump.MAJOR)
        'major'
        >>> _format_release_boundaries(VersionBump.PATCH)
        'major, minor or patch'

    """
    levels = [level.value for level in _GRACE_WINDOW_UNITS[: _GRACE_WINDOW_UNITS.index(unit) + 1]]
    return " or ".join(filter(None, [", ".join(levels[:-1]), levels[-1]]))


def _build_policy_spec(min_grace: Optional[GraceWindowSpec], message_required: bool) -> _PolicySpec:
    """Validate the raw policy arguments once, before any wrapper is scanned.

    Args:
        min_grace: Grace-window spelling (e.g. ``"0.3"``) or a :class:`GraceWindow`, or ``None`` to disable the rule.
        message_required: Whether every wrapper must offer migration guidance.

    Returns:
        The parsed :class:`_PolicySpec`.

    Raises:
        ValueError: If ``min_grace`` is not a recognised specification.

    """
    grace = GraceWindow.parse(min_grace) if min_grace is not None else None
    return _PolicySpec(grace=grace, message_required=message_required)


def _parse_policy_version(raw: Optional[str], info: DeprecationWrapperInfo, field_name: str) -> Optional["Version"]:
    """Parse one of a wrapper's version fields, warning instead of raising on a typo.

    A single unparsable version string must not abort a batch policy scan (mirroring the expiry gate's
    behaviour), but silently skipping it would make the wrapper permanently un-lintable — so each skip warns.

    Args:
        raw: Raw version string from the wrapper metadata; ``None`` or empty means the field is unset.
        info: Wrapper whose version is being parsed, used for the warning text.
        field_name: Metadata field name (``deprecated_in`` or ``remove_in``) named in the warning.

    Returns:
        The parsed version, or ``None`` when the field is unset or unparsable.

    """
    if not raw:
        return None
    try:
        return _parse_version(raw)
    except ValueError:
        warnings.warn(
            f"{_format_subject(info)} has an unparsable `{field_name}` version `{raw}`; "
            "the policy rules depending on it are skipped until the version string is fixed.",
            stacklevel=2,
        )
        return None


def _satisfies_grace_window(deprecated_ver: "Version", remove_ver: "Version", window: GraceWindow) -> bool:
    """Return whether ``remove_ver`` is one clean bump beyond ``deprecated_ver`` that clears ``window``.

    A removal version has to be reachable from the deprecation version by bumping a *single* release component
    and resetting everything below it — ``1.2.3`` → ``1.3.0`` or ``2.0.0``, never ``2.3`` or ``1.3.1``. That
    is the shape every release cadence promises removals on; a mixed bump lands on no boundary at all, so it
    fails whatever the window asks for. The bumped component then has to be at least as coarse as the window's
    unit: a finer bump (a patch against a minor-counted window) fails, a coarser one (a major against the same
    window) clears the window whatever its count is — a wrapper deprecated in ``1.2`` and removed in ``2.0``
    satisfies ``"0.3"`` even though its minor number went *down*, because the major release is the bigger step.
    Converting across components has no defensible answer (how many minors one major is worth depends on a
    cadence this library cannot see), so the count constrains distance only *within* its own component.

    Only the release numbers are read, so a pre- or post-release of a clean version (``2.0rc1``, ``2.0.post1``)
    is the same release line and passes. Every release component below the bumped one has to be zero, not just
    the two :attr:`~packaging.version.Version.minor` and :attr:`~packaging.version.Version.micro` expose: PEP 440
    allows arbitrarily many, and a four-component ``2.0.0.1`` is as much a follow-up release as ``2.0.1`` is.

    A PEP 440 epoch change is the coarsest bump of all — ``1!1.0`` sorts above every epoch-``0`` version regardless
    of its release numbers, so the epoch is compared first and settles the answer whenever the two differ. That
    ordering keeps this predicate total for any pair of versions; the policy path does not rely on it, because
    :func:`_grace_window_violation` warns and skips the wrapper before an epoch change ever reaches here.

    Args:
        deprecated_ver: Version the wrapper was deprecated in.
        remove_ver: Version the wrapper is scheduled for removal in.
        window: Minimum number of bumps required and the version component they are counted in.

    Returns:
        True when the scheduled removal is a clean bump that respects the grace window.

    """
    if deprecated_ver.epoch != remove_ver.epoch:
        # Comparing release numbers across epochs is meaningless (``2.0`` is *older* than ``1!1.0``); the epoch
        # bump itself is a bigger step than any window expressed in majors, so it clears every unit.
        return remove_ver.epoch > deprecated_ver.epoch
    # Pad both release tuples to a common length (at least major/minor/patch) so ``2`` reads as ``2.0.0`` and a
    # fourth component is compared rather than silently dropped.
    width = max(len(deprecated_ver.release), len(remove_ver.release), len(_GRACE_WINDOW_UNITS))
    old = deprecated_ver.release + (0,) * (width - len(deprecated_ver.release))
    new = remove_ver.release + (0,) * (width - len(remove_ver.release))
    bumped = next((i for i, (o, n) in enumerate(zip(old, new)) if o != n), None)
    if bumped is None or new[bumped] < old[bumped] or any(new[bumped + 1 :]):
        return False  # Same release, a step backwards, or a mixed bump — not a clean release boundary.
    unit_position = _GRACE_WINDOW_UNITS.index(window.unit)
    if bumped != unit_position:
        return bumped < unit_position  # A coarser bump clears the window outright; a finer one never does.
    return new[bumped] - old[bumped] >= window.count


def _grace_window_violation(
    info: DeprecationWrapperInfo, deprecated_ver: "Version", remove_ver: "Version", window: GraceWindow
) -> Optional[str]:
    """Return the ``min-grace`` violation message for one wrapper, or ``None`` when it passes or is skipped.

    Kept separate from :func:`_satisfies_grace_window` so the arithmetic stays a pure predicate while the one
    case that cannot be measured — a *forward* PEP 440 epoch change between the two versions — is surfaced to
    the user instead of being settled by the predicate's ordering rule.

    Release numbers are only comparable inside one epoch: ``2.0`` is *older* than ``1!1.0``, and the distance
    between them is not expressible in majors, minors, or patches at all. A forward epoch bump would otherwise
    clear every grace window silently, so a wrapper that in fact gave callers no warning cycle at all would read
    as policy-clean; that case warns and reports the rule as skipped, the same treatment an unparsable version
    string gets in :func:`_parse_policy_version`. A removal version that sorts at or before
    ``deprecated_in`` is not a measurement gap: it is reported directly as a ``min-grace`` violation, since no
    version-distance calculation is needed to see that no grace window was given at all.

    Args:
        info: Wrapper being checked; named in the violation message and in the skip warning.
        deprecated_ver: Parsed version the wrapper was deprecated in.
        remove_ver: Parsed version the wrapper is scheduled for removal in.
        window: Minimum number of bumps the policy requires and the version component they are counted in.

    Returns:
        The violation message, or ``None`` when the window is satisfied or the check was skipped.

    """
    config = info.deprecated_info
    violation = (
        f"[{PolicyRule.MIN_GRACE.value}] {_format_subject(info)} is deprecated in `{config.deprecated_in}`"
        f" and scheduled for removal in `{config.remove_in}`; the policy requires a grace window of at least"
        f" {window.describe()}, landing on a clean {_format_release_boundaries(window.unit)} boundary."
    )
    if remove_ver <= deprecated_ver:
        return violation
    if deprecated_ver.epoch != remove_ver.epoch:
        warnings.warn(
            f"{_format_subject(info)} spans a PEP 440 epoch change between `deprecated_in`"
            f" `{config.deprecated_in}` and `remove_in` `{config.remove_in}`; version distance is not"
            " comparable across epochs, so the `min-grace` check is skipped for it.",
            stacklevel=2,
        )
        return None
    return None if _satisfies_grace_window(deprecated_ver, remove_ver, window) else violation


def _has_migration_guidance(info: DeprecationWrapperInfo) -> bool:
    """Return whether a wrapper tells callers what to migrate *to*.

    Guidance is any of: a forwarding target (callable or replacement module), an ``args_mapping`` or
    ``attrs_mapping`` naming the replacement names, or a custom ``message_template`` spelling the migration out.

    The template has to be non-empty to count. ``message_template=""`` is not a message: every emitter renders
    ``config.message_template or TEMPLATE_WARNING_*`` (see :mod:`~deprecate.messaging`), so an empty string is
    the runtime opt-in for the *built-in* warning text and names no replacement whatsoever. Reading it as
    guidance would let a wrapper opt out of this rule with a value that changes nothing a caller sees. This is
    an audit-side judgement only — the runtime contract that ``""`` selects the built-in message is unchanged.

    A remap mode counts only when its mapping actually names something: ``TargetMode.ARGS_REMAP`` with an empty
    ``args_mapping`` (or ``ATTRS_REMAP`` with no mapping at all) renames nothing, so it tells callers no more than
    a bare warning does — the wrapper-configuration audit flags it as a no-op, and this rule must not read it as
    guidance either.

    The same reading excludes a mapping configured alongside an explicit ``TargetMode.NOTIFY``: that combination is
    contradictory, so the decorator warns about it and drops the mapping (see
    :meth:`~deprecate._types.TargetMode._validate`) — nothing is renamed at call time and the emitted warning names
    no replacement, which leaves a custom ``message_template`` as ``NOTIFY``'s only way to guide a caller. An
    *unset* target keeps its mapping: only an explicitly chosen ``NOTIFY`` is barred from auto-resolving to a remap
    mode, so a mapping stored against ``target=None`` is still applied.

    Deprecated modules are judged on their target and mappings alone: :func:`~deprecate.module.deprecated_module`
    stores the already-rendered warning text in ``message_template``, so that field is always set for a module and
    would make the rule inert there.

    Args:
        info: Wrapper to inspect.

    Returns:
        True when the wrapper offers migration guidance.

    """
    config = info.deprecated_info
    target = config.target
    has_mapping = bool(config.args_mapping or config.attrs_mapping)
    # A deprecated module stores its already-rendered warning here, so that text is not a custom message.
    has_message = bool(config.message_template) and info.api_type != "module"
    if target is TargetMode.NOTIFY:
        return has_message
    if target in (TargetMode.ARGS_REMAP, TargetMode.ATTRS_REMAP):
        return has_mapping
    if target is not None:
        return True
    return has_mapping or has_message


def _policy_violations_for_wrapper(info: DeprecationWrapperInfo, spec: _PolicySpec) -> list[str]:
    """Collect every policy violation of a single wrapper.

    Args:
        info: Wrapper to check.
        spec: Parsed policy configuration.

    Returns:
        List of violation messages, each prefixed with its :class:`~deprecate.audit.PolicyRule` slug.

    """
    config = info.deprecated_info
    violations = []

    # Parse the version fields only when the grace-window rule is switched on. With it disabled the parse is
    # pure cost — and, on an install without ``packaging``, an ImportError raised for a rule the caller never
    # asked to run. Keeping the parse lazy is what lets a ``message_required``-only policy work with no
    # version machinery at all, while the grace-window rule still surfaces the install hint.
    if spec.grace is not None:
        deprecated_ver = _parse_policy_version(config.deprecated_in, info, "deprecated_in")
        remove_ver = _parse_policy_version(config.remove_in, info, "remove_in")
        if deprecated_ver is not None and remove_ver is not None:
            grace_violation = _grace_window_violation(info, deprecated_ver, remove_ver, spec.grace)
            if grace_violation is not None:
                violations.append(grace_violation)

    if spec.message_required and not _has_migration_guidance(info):
        violations.append(
            f"[{PolicyRule.MESSAGE_REQUIRED.value}] {_format_subject(info)} warns without naming a replacement;"
            " configure a `target`, an `args_mapping`/`attrs_mapping`, or a custom `message_template`"
            " so callers learn what to migrate to."
        )

    return violations


def _check_policy_for_callables(results: list[DeprecationWrapperInfo], spec: _PolicySpec) -> list[str]:
    """Apply the policy rules to pre-scanned wrapper results.

    Shared implementation used by :func:`validate_deprecation_policy` and the CLI's single-scan path, keeping
    the violation-message format in one place.

    Version parsing is lazy: a version string is only turned into a :class:`~packaging.version.Version` when
    the grace-window rule reads it. A policy that runs ``message_required`` alone therefore needs no
    ``packaging`` install at all, while the grace-window rule still raises the usual install hint.

    Args:
        results: Pre-scanned wrapper info list.
        spec: Parsed policy configuration.

    Returns:
        List of violation messages across all wrappers.

    Raises:
        ImportError: If the grace-window rule is enabled, a wrapper carries both version fields, and the
            ``packaging`` library is not installed.

    """
    violations = []
    for info in results:
        violations.extend(_policy_violations_for_wrapper(info, spec))
    return violations


def validate_deprecation_policy(
    module: Union[Any, str],  # noqa: ANN401
    recursive: bool = True,
    include_members: bool = True,
    *,
    min_grace: Optional[GraceWindowSpec] = "0.3",
    message_required: bool = True,
    exclude: Optional[Sequence[str]] = None,
) -> list[str]:
    """Check every deprecated wrapper in a module/package against deprecation-governance rules.

    Where :func:`~deprecate.audit.validate_deprecation_expiry` answers *"was this removed on time?"*, this gate
    answers *"was this scheduled responsibly in the first place?"* — catching a removal deadline that leaves
    callers too short a grace window or lands off a release boundary, and a warning that never names a
    replacement, all at review time instead of in a downstream issue.

    Two rules are checked, each independently switchable (``None``/``False`` disables):

    - ``min_grace`` — ``remove_in`` must be one clean version bump beyond ``deprecated_in``, at least this far
      (default ``"0.3"``, three minors).
    - ``message_required`` — every wrapper must name a replacement (target, mapping, or custom template).

    Args:
        module: A Python module or package to scan — an imported module object or a string module path.
        recursive: If True (default), recursively scan submodules.
        include_members: If True (default), also scan deprecated class members, matching the discovery default
            of :func:`~deprecate.audit.find_deprecation_wrappers`.
        min_grace: Minimum grace window, either a version-shaped delta with two or three components — ``"1.0"``
            (one major), ``"0.3"`` (three minors), ``"0.0.2"`` (two patches); a float such as ``0.3`` works too —
            or a one-key table naming the unit: ``{"major": 1}``, ``{"minor": 3}``, ``{"patch": 2}``; every
            spelling is converted to a :class:`~deprecate.audit.GraceWindow` up front, which can also be passed
            directly. ``None`` skips the rule; a bare ``"1"`` is rejected as ambiguous. The removal has to be a
            *clean* bump of one
            component with everything below it reset (``1.2`` → ``1.5`` or ``2.0``, never ``2.3``); a coarser bump
            always satisfies the window regardless of the count (``1.2`` → ``2.0`` clears ``"0.3"``), a finer one
            never does. The count restricts distance only within its own component.
        message_required: Require migration guidance on every wrapper.
        exclude: Glob patterns over full dotted module names to leave out of the scan, as in
            :func:`~deprecate.audit.find_deprecation_wrappers` (e.g. ``["my_package.tests"]``); ``None`` excludes
            nothing.

    Returns:
        List of violation messages, each prefixed with its :class:`~deprecate.audit.PolicyRule` slug.
        Empty list when every wrapper satisfies the enabled rules.

    Raises:
        ImportError: If ``min_grace`` is enabled, a wrapper carries both version fields, and the ``packaging``
            library is not installed (``pip install pyDeprecate[audit]``). A policy reduced to
            ``message_required`` alone parses no version and runs without it.
        ValueError: If ``min_grace`` is not a valid specification.

    Examples:
        >>> from deprecate import validate_deprecation_policy
        >>> violations = validate_deprecation_policy("tests.collection_policy", recursive=False)
        >>> [v for v in violations if "no_grace_window" in v]  # doctest: +ELLIPSIS
        ['[min-grace] Callable `tests.collection_policy.no_grace_window` is deprecated in `2.0` and ...']

        >>> # Rules are opt-out: keep the guidance rule, drop the grace window
        >>> violations = validate_deprecation_policy("tests.collection_policy", recursive=False, min_grace=None)
        >>> any("min-grace" in v for v in violations)
        False

    !!! note
        - A ``0.x`` project needs no special setting: a bump to ``1.0`` is a major step and clears any window,
          while removals within the ``0.x`` line are measured in minors as usual.
        - Wrappers missing ``deprecated_in`` or ``remove_in`` are not violations here — the grace-window rule
          simply skips them (a deprecation without a scheduled removal is a valid, common choice).
        - An unparsable version string emits a ``UserWarning`` per skip rather than aborting the scan; a grace
          window whose two versions sit in different PEP 440 epochs is skipped and warned about the same way,
          since release numbers are not comparable across an epoch change.
        - Intended for the same CI slot as the expiry gate; the CLI exposes it as ``pydeprecate policy``.

    """
    spec = _build_policy_spec(min_grace, message_required)
    if isinstance(module, str):
        module = importlib.import_module(module)
    return _check_policy_for_callables(
        find_deprecation_wrappers(module, recursive=recursive, include_members=include_members, exclude=exclude), spec
    )
