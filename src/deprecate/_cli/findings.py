"""Exit codes for the CLI: the weight of each finding per subcommand, and the one function turning them into a code.

A finding is an *error* where it fails the run, a *warning* where it is only reported, and not evaluated where the
subcommand does not look at it. ``all`` gates on everything ``check``, ``expiry`` and ``chains`` gate on and reports
policy violations as warnings, unless ``--gate`` lists ``policy``: a check named there gates ``all`` exactly as its own
subcommand does. In strict mode (``--strict``) a run that recorded any gap fails as well. A run over several paths
decides each path's gate with that path's own settings and fails when any of them fails.

"""

import enum
from collections.abc import Iterable
from typing import Optional

from deprecate.audit._findings import _RULE_CHECKS, _Check, _Finding, _Rule


class _Level(str, enum.Enum):
    """How a finding counts in one subcommand; the values are the levels the report uses."""

    ERROR = "error"
    WARNING = "warning"
    #: Reported for information only; no rule has this level today, the report schema already admits it.
    NOTE = "note"


#: Report order of the levels, most severe first.
_LEVEL_RANK: dict[_Level, int] = {_Level.ERROR: 0, _Level.WARNING: 1, _Level.NOTE: 2}


class _GateReason(str, enum.Enum):
    """Why a gate failed, as the report lists it."""

    FINDINGS = "findings"
    GAPS = "gaps"


_E, _W = _Level.ERROR, _Level.WARNING
#: Rule -> subcommand -> level; a subcommand missing from a rule's row does not evaluate that rule.
_LEVELS: dict[_Rule, dict[str, _Level]] = {
    _Rule.INVALID_ARGS: {"check": _E, "all": _E},
    _Rule.MISCONFIGURED_TARGET: {"check": _E, "all": _E},
    _Rule.INNER_ORDER_PROPERTY: {"check": _E, "all": _E},
    _Rule.IDENTITY_MAPPING: {"check": _W, "all": _W},
    _Rule.NO_EFFECT: {"check": _W, "all": _W},
    _Rule.POSITIONAL_ONLY_MAPPING: {"check": _W, "all": _W},
    _Rule.CHAIN: {"check": _W, "chains": _E, "all": _E},
    _Rule.EXPIRED: {"expiry": _E, "all": _E},
    _Rule.MIN_GRACE: {"policy": _E, "all": _W},
    _Rule.MESSAGE_REQUIRED: {"policy": _E, "all": _W},
}
#: Every check ``--gate`` may name, in the order the report lists ``options.gates``.
_GATE_ORDER = tuple(check.value for check in _Check)


def _level(rule: _Rule, subcommand: str, gates: Iterable[str] = ()) -> Optional[_Level]:
    """Return how *rule* counts in *subcommand*, or ``None`` when the subcommand does not evaluate it.

    In ``all`` a rule whose check is one of *gates* (the ``--gate`` list) counts as in that check's own subcommand.

    Examples:
        >>> _level(_Rule.CHAIN, "check"), _level(_Rule.CHAIN, "chains"), _level(_Rule.EXPIRED, "check")
        (<_Level.WARNING: 'warning'>, <_Level.ERROR: 'error'>, None)
        >>> _level(_Rule.MIN_GRACE, "all"), _level(_Rule.MIN_GRACE, "all", {"policy"})
        (<_Level.WARNING: 'warning'>, <_Level.ERROR: 'error'>)

    """
    level = _LEVELS[rule].get(subcommand)
    if level is None or subcommand != "all":
        return level
    owner = _RULE_CHECKS[rule].value
    return _LEVELS[rule].get(owner, level) if owner in gates else level


def _gate_reasons(
    findings: Iterable[_Finding],
    subcommand: str,
    *,
    strict: bool = False,
    gaps: int = 0,
    gates: Iterable[str] = (),
) -> list[_GateReason]:
    """Return why a run of *subcommand* fails its gate, before ``--exit-zero``: empty when it passes.

    Examples:
        >>> _gate_reasons([], "check", strict=True, gaps=1)
        [<_GateReason.GAPS: 'gaps'>]

    """
    reasons = []
    gated = frozenset(gates)
    if any(_level(finding.rule, subcommand, gated) is _E for finding in findings):
        reasons.append(_GateReason.FINDINGS)
    if strict and gaps > 0:
        reasons.append(_GateReason.GAPS)
    return reasons


def _merge_reasons(per_path: Iterable[list[_GateReason]]) -> list[_GateReason]:
    """Return the gate reasons of a run over several paths: every reason any path failed for, in report order.

    Examples:
        >>> _merge_reasons([[_GateReason.GAPS], [_GateReason.FINDINGS]])
        [<_GateReason.FINDINGS: 'findings'>, <_GateReason.GAPS: 'gaps'>]

    """
    found = {reason for reasons in per_path for reason in reasons}
    return [reason for reason in _GateReason if reason in found]


def _verdict(reasons: list[_GateReason], *, exit_zero: bool = False) -> int:
    """Return the exit code of a gate that failed for *reasons*: 1 when there is any, 0 when none or ``exit_zero``.

    The CLI never lets ``exit_zero`` meet strict mode. Usage errors (2) and scan or internal errors (3) never come from
    here: they are decided before the scan or by the top-level handler of :func:`~deprecate._cli.cli`.

    Examples:
        >>> _verdict([_GateReason.GAPS]), _verdict([_GateReason.GAPS], exit_zero=True), _verdict([])
        (1, 0, 0)

    """
    return 1 if reasons and not exit_zero else 0
