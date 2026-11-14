"""The rules' view of static rows: which settings the source settled, and the policy findings over those rows.

Shared by the public validators in :mod:`deprecate.audit` and the CLI's checks layer in :mod:`deprecate._cli.static`;
nothing here prints. A rule judges only the rows whose settings it reads were settled by the source.

"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Optional

from deprecate._static.consts import _GRACE_KEYWORDS, _GUIDANCE_KEYWORDS
from deprecate._static.model import _StaticWrapperInfo
from deprecate.audit._findings import _Finding, _Rule
from deprecate.audit._policy import _OnUnparsableVersion, _policy_findings, _PolicySpec
from deprecate.audit._wrappers import DeprecationWrapperInfo


def _unresolved(info: DeprecationWrapperInfo) -> Mapping[str, str]:
    """Return the settings of *info* the source did not settle (none for an imported wrapper)."""
    return info.unresolved if isinstance(info, _StaticWrapperInfo) else {}


def _resolved(wrappers: Sequence[DeprecationWrapperInfo], keywords: Sequence[str]) -> list[DeprecationWrapperInfo]:
    """Return the wrappers whose settings among *keywords* the source settled."""
    return [info for info in wrappers if not any(key in _unresolved(info) for key in keywords)]


def _static_policy_findings(
    wrappers: list[DeprecationWrapperInfo],
    spec: _PolicySpec,
    *,
    on_unparsable: Optional[_OnUnparsableVersion] = None,
) -> list[_Finding]:
    """Evaluate each policy rule on its resolved rows, preserving import-scan finding order.

    An unresolved version does not hide a provable guidance violation, and an unresolved target does not hide a provable
    grace-window violation. Parsing and optional dependency failures propagate without printing anything.

    """
    findings: list[_Finding] = []
    if spec.grace is not None:
        findings += _policy_findings(
            _resolved(wrappers, _GRACE_KEYWORDS), replace(spec, message_required=False), on_unparsable=on_unparsable
        )
    if spec.message_required:
        findings += _policy_findings(_resolved(wrappers, _GUIDANCE_KEYWORDS), replace(spec, grace=None))
    order = {(info.module, info.function): index for index, info in enumerate(wrappers)}
    return sorted(findings, key=lambda f: (order.get((f.module, f.symbol), 0), f.rule is _Rule.MESSAGE_REQUIRED))
