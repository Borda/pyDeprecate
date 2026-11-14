"""The CLI checks' view of static rows: the settings a rule needs, the gaps they leave, and the policy findings.

The import-free source reader itself lives in :mod:`deprecate._static`.

"""

from collections.abc import Mapping, Sequence
from typing import Optional

from deprecate._cli.report import _print_gap
from deprecate._gaps import _GapKind
from deprecate._static.checks import _static_policy_findings as _compute_policy_findings
from deprecate._static.checks import _unresolved
from deprecate._static.consts import _GRACE_KEYWORDS, _GUIDANCE_KEYWORDS
from deprecate.audit import DeprecationWrapperInfo
from deprecate.audit._findings import _Finding, _Rule
from deprecate.audit._policy import _OnUnparsableVersion, _PolicySpec
from deprecate.audit._wrappers import _format_subject


def _report_unresolved(
    wrappers: Sequence[DeprecationWrapperInfo],
    rule_keywords: Mapping[str, Sequence[str]],
) -> None:
    """Print one gap per wrapper and unsettled reason among the settings the enabled rules read."""
    keywords = dict.fromkeys(key for keys in rule_keywords.values() for key in keys)
    for info in wrappers:
        details = dict.fromkeys(_unresolved(info)[key] for key in keywords if key in _unresolved(info))
        for detail in details:
            rules = frozenset(
                rule
                for rule, keys in rule_keywords.items()
                if any(_unresolved(info).get(key) == detail for key in keys)
            )
            _print_gap(
                _GapKind.STATIC_UNRESOLVED,
                f"{detail} ({_format_subject(info)} not checked)",
                subject=(info.module, info.function),
                rules=rules,
            )


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
    findings = _compute_policy_findings(wrappers, spec, on_unparsable=on_unparsable)
    rule_keywords: dict[str, Sequence[str]] = {}
    if spec.grace is not None:
        rule_keywords[_Rule.MIN_GRACE.value] = _GRACE_KEYWORDS
    if spec.message_required:
        rule_keywords[_Rule.MESSAGE_REQUIRED.value] = _GUIDANCE_KEYWORDS
    _report_unresolved(wrappers, rule_keywords)
    return findings
