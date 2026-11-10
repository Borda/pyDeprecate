"""Exit codes for the CLI: the weight of each finding per subcommand, and the one function turning them into a code.

A finding is an *error* where it fails the run, a *warning* where it is only reported, and not evaluated where the
subcommand does not look at it. ``all`` gates on everything ``check``, ``expiry`` and ``chains`` gate on and reports
policy violations as warnings. In strict mode (``--strict``) a run that recorded any gap fails as well.

"""

import enum
from collections.abc import Iterable
from typing import Optional

from deprecate.audit._findings import _Finding, _Rule


class _Level(str, enum.Enum):
    """How a finding counts in one subcommand; the values are the levels the report and SARIF output use."""

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


def _level(rule: _Rule, subcommand: str) -> Optional[_Level]:
    """Return how *rule* counts in *subcommand*, or ``None`` when the subcommand does not evaluate it.

    Examples:
        >>> _level(_Rule.CHAIN, "check"), _level(_Rule.CHAIN, "chains"), _level(_Rule.EXPIRED, "check")
        (<_Level.WARNING: 'warning'>, <_Level.ERROR: 'error'>, None)

    """
    return _LEVELS[rule].get(subcommand)


def _gate_levels(findings: Iterable[_Finding], subcommand: str) -> dict[str, _Level]:
    """Return the level of every evaluated finding by its id, one entry per id at its highest level.

    ``all`` derives a ``chain`` finding in both its ``check`` and its ``chains`` pass; the two collapse into one, at the
    level ``chains`` gives it.

    """
    levels: dict[str, _Level] = {}
    for finding in findings:
        level = _level(finding.rule, subcommand)
        if level is None:
            continue
        if levels.get(finding.id) is not _E:
            levels[finding.id] = level
    return levels


def _gate_reasons(
    findings: Iterable[_Finding], subcommand: str, *, strict: bool = False, gaps: int = 0
) -> list[_GateReason]:
    """Return why a run of *subcommand* fails its gate, before ``--exit-zero``: empty when it passes.

    Examples:
        >>> _gate_reasons([], "check", strict=True, gaps=1)
        [<_GateReason.GAPS: 'gaps'>]

    """
    reasons = []
    if _E in _gate_levels(findings, subcommand).values():
        reasons.append(_GateReason.FINDINGS)
    if strict and gaps > 0:
        reasons.append(_GateReason.GAPS)
    return reasons


def _exit_code(
    findings: Iterable[_Finding], subcommand: str, *, exit_zero: bool = False, strict: bool = False, gaps: int = 0
) -> int:
    """Return the exit code a run of *subcommand* gives for *findings*: 1 when the gate failed, else 0.

    The gate fails when any finding is an error in *subcommand*, or, in strict mode, when the run recorded at least one
    gap (a part of the audit that did not run). ``exit_zero`` downgrades 1 to 0; the CLI never lets it meet ``strict``.
    Usage errors (2) and scan or internal errors (3) never come from here: they are decided before the scan or by the
    top-level handler of :func:`~deprecate._cli.cli`.

    Examples:
        >>> _exit_code([], "check"), _exit_code([], "check", strict=True, gaps=2), _exit_code([], "check", gaps=2)
        (0, 1, 0)

    """
    failed = bool(_gate_reasons(findings, subcommand, strict=strict, gaps=gaps))
    return 1 if failed and not exit_zero else 0
