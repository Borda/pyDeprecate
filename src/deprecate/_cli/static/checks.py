"""The checks' view of static rows: the settings a rule needs, the gaps they leave, and the policy findings."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Optional

from deprecate._cli.report import _GapKind, _print_gap
from deprecate._cli.static.consts import _GRACE_KEYWORDS, _GUIDANCE_KEYWORDS
from deprecate._cli.static.model import _StaticWrapperInfo
from deprecate.audit import DeprecationWrapperInfo
from deprecate.audit._findings import _Finding, _Rule
from deprecate.audit._policy import _OnUnparsableVersion, _policy_findings, _PolicySpec
from deprecate.audit._wrappers import _format_subject


def _unresolved(info: DeprecationWrapperInfo) -> Mapping[str, str]:
    """Return the settings of *info* the source did not settle (none for an imported wrapper)."""
    return info.unresolved if isinstance(info, _StaticWrapperInfo) else {}


def _report_unresolved(wrappers: Sequence[DeprecationWrapperInfo], keywords: Sequence[str]) -> None:
    """Print one ``not statically resolvable`` gap per wrapper and unsettled reason among *keywords*."""
    for info in wrappers:
        details = dict.fromkeys(_unresolved(info)[key] for key in keywords if key in _unresolved(info))
        for detail in details:
            _print_gap(
                _GapKind.STATIC_UNRESOLVED,
                f"{detail} ({_format_subject(info)} not checked)",
                subject=(info.module, info.function),
            )


def _resolved(wrappers: Sequence[DeprecationWrapperInfo], keywords: Sequence[str]) -> list[DeprecationWrapperInfo]:
    """Return the wrappers whose settings among *keywords* the source settled."""
    return [info for info in wrappers if not any(key in _unresolved(info) for key in keywords)]


def _policy_keywords(spec: _PolicySpec) -> tuple[str, ...]:
    """Return the settings the enabled policy rules read."""
    return (_GRACE_KEYWORDS if spec.grace is not None else ()) + (_GUIDANCE_KEYWORDS if spec.message_required else ())


def _static_policy_findings(
    wrappers: list[DeprecationWrapperInfo],
    spec: _PolicySpec,
    *,
    on_unparsable: Optional[_OnUnparsableVersion] = None,
) -> list[_Finding]:
    """Run each policy rule on the wrappers whose settings for that rule resolved, in import-scan finding order.

    A version held in a variable leaves ``min-grace`` unable to judge a wrapper, but not ``message-required``, which
    still reads its target and message — so one unsettled setting never hides a violation another rule can prove.

    Each wrapper a rule that ran had to leave out is named by a ``not statically resolvable`` gap, printed once the
    rules have run: a rule that raised below never ran, so it names no wrapper "not checked". A version the source
    spells literally but that is not PEP 440 is settled, so ``min-grace`` reads it and hands the wrapper to
    *on_unparsable*, exactly as on an import scan.

    Raises:
        ImportError: As :func:`~deprecate.audit._policy._policy_findings` does, when ``min-grace`` runs without the
            ``packaging`` library. Nothing is printed then; the caller re-runs the rules that need no version parsing,
            and their own call prints the gaps of those.

    """
    findings: list[_Finding] = []
    if spec.grace is not None:
        findings += _policy_findings(
            _resolved(wrappers, _GRACE_KEYWORDS), replace(spec, message_required=False), on_unparsable=on_unparsable
        )
    if spec.message_required:
        findings += _policy_findings(_resolved(wrappers, _GUIDANCE_KEYWORDS), replace(spec, grace=None))
    _report_unresolved(wrappers, _policy_keywords(spec))
    order = {(info.module, info.function): index for index, info in enumerate(wrappers)}
    return sorted(findings, key=lambda f: (order.get((f.module, f.symbol), 0), f.rule is _Rule.MESSAGE_REQUIRED))
