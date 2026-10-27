"""Findings: one record per problem an audit check finds in a deprecated wrapper.

The check functions of :mod:`deprecate.audit` return message strings; the CLI gates on these records instead, so the
exit code of every subcommand is computed in one place from the rule each finding belongs to. One finding covers one
``(rule, module, symbol)``: a wrapper with three invalid argument names is one ``invalid-args`` finding with three
``details``.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import enum
from dataclasses import dataclass
from typing import Optional

from deprecate._types import TargetMode
from deprecate.audit._wrappers import DeprecationWrapperInfo, _format_subject


class _Rule(str, enum.Enum):
    """What a finding is about; the value is the slug a report shows."""

    INVALID_ARGS = "invalid-args"
    MISCONFIGURED_TARGET = "misconfigured-target"
    INNER_ORDER_PROPERTY = "inner-order-property"
    IDENTITY_MAPPING = "identity-mapping"
    NO_EFFECT = "no-effect"
    POSITIONAL_ONLY_MAPPING = "positional-only-mapping"
    CHAIN = "chain"
    EXPIRED = "expired"
    #: The two policy rules keep the slugs of :class:`~deprecate.audit.PolicyRule`.
    MIN_GRACE = "min-grace"
    MESSAGE_REQUIRED = "message-required"


class _Check(str, enum.Enum):
    """The check family that derives a finding."""

    CHECK = "check"
    EXPIRY = "expiry"
    POLICY = "policy"
    CHAINS = "chains"


@dataclass(frozen=True)
class _Finding:
    """One problem found in one deprecated wrapper.

    Attributes:
        rule: What the finding is about.
        check: The check family that derived it.
        module: Module the wrapper is reported under.
        symbol: Wrapper name (empty for a deprecated module).
        api_type: The wrapper's :attr:`~deprecate.audit.DeprecationWrapperInfo.api_type`.
        label: Backticked subject phrase, such as a callable's qualified name.
        message: Human-readable text; for ``expired`` and the policy rules, the message the Python API returns.
        details: Rule-specific items, such as the invalid argument names or the chain kind.
        deprecated_in: The wrapper's ``deprecated_in``, ``None`` when empty.
        remove_in: The wrapper's ``remove_in``, ``None`` when empty.

    """

    rule: _Rule
    check: _Check
    module: str
    symbol: str
    api_type: str
    label: str
    message: str
    details: tuple[str, ...] = ()
    deprecated_in: Optional[str] = None
    remove_in: Optional[str] = None

    @property
    def id(self) -> str:
        """Return the stable identity ``rule:module:symbol`` of the finding."""
        return f"{self.rule.value}:{self.module}:{self.symbol}"


def _make_finding(
    info: DeprecationWrapperInfo, rule: _Rule, check: _Check, message: str, details: tuple[str, ...] = ()
) -> _Finding:
    """Build a finding for *info*, copying the wrapper's identity and schedule.

    Examples:
        >>> from deprecate._types import DeprecationConfig
        >>> config = DeprecationConfig(deprecated_in="1.0")
        >>> info = DeprecationWrapperInfo(module="pkg", function="old", deprecated_info=config)
        >>> finding = _make_finding(info, _Rule.EXPIRED, _Check.EXPIRY, "gone")
        >>> finding.id, finding.deprecated_in, finding.remove_in
        ('expired:pkg:old', '1.0', None)

    """
    config = info.deprecated_info
    return _Finding(
        rule=rule,
        check=check,
        module=info.module,
        symbol=info.function,
        api_type=info.api_type,
        label=_format_subject(info),
        message=message,
        details=details,
        deprecated_in=config.deprecated_in or None,
        remove_in=config.remove_in or None,
    )


def _misconfiguration(info: DeprecationWrapperInfo) -> str:
    """Name the contradictory target configuration that makes *info* a ``misconfigured-target`` finding."""
    config = info.deprecated_info
    if config.target is TargetMode.ARGS_REMAP:
        return "`TargetMode.ARGS_REMAP` with an empty `args_mapping`"
    for field_name in ("args_mapping", "attrs_mapping", "args_extra"):
        if getattr(config, field_name):
            return f"`TargetMode.NOTIFY` with `{field_name}`"
    return "`target=False`"


def _no_effect_reasons(info: DeprecationWrapperInfo) -> tuple[str, ...]:
    """Return why *info* has no effect on its callers, in report order."""
    reasons = (
        (info.empty_args_mapping, "Empty mapping"),
        (info.self_reference, "Self reference"),
        (info.all_identity, "All identity mappings"),
    )
    return tuple(reason for flagged, reason in reasons if flagged)


def _wrapper_findings(info: DeprecationWrapperInfo) -> list[_Finding]:
    """Derive the configuration findings of one wrapper, the ``check`` and ``chains`` families.

    The order is fixed: ``invalid-args``, ``misconfigured-target``, ``inner-order-property``, ``identity-mapping``,
    ``no-effect``, ``positional-only-mapping``, ``chain``.

    Examples:
        >>> info = DeprecationWrapperInfo(module="pkg", function="old", invalid_args=["a", "b"], no_effect=True)
        >>> [(finding.rule.value, finding.details) for finding in _wrapper_findings(info)]
        [('invalid-args', ('a', 'b')), ('no-effect', ())]

    """
    label = _format_subject(info)
    findings: list[_Finding] = []
    if info.invalid_args:
        names = tuple(info.invalid_args)
        message = f"{label} maps argument(s) its signature does not have: {', '.join(names)}"
        findings.append(_make_finding(info, _Rule.INVALID_ARGS, _Check.CHECK, message, names))
    if info.misconfigured_target:
        case = _misconfiguration(info)
        message = f"{label} has a contradictory target configuration: {case}"
        findings.append(_make_finding(info, _Rule.MISCONFIGURED_TARGET, _Check.CHECK, message, (case,)))
    if info.inner_order_property:
        message = f"{label} is an inner-order `@property @deprecated`; its setter and deleter are unprotected"
        findings.append(_make_finding(info, _Rule.INNER_ORDER_PROPERTY, _Check.CHECK, message))
    if info.identity_args_mapping:
        names = tuple(info.identity_args_mapping)
        message = f"{label} maps argument(s) to themselves: {', '.join(names)}"
        findings.append(_make_finding(info, _Rule.IDENTITY_MAPPING, _Check.CHECK, message, names))
    if info.no_effect:
        reasons = _no_effect_reasons(info)
        message = f"{label} has no effect on its callers" + (f": {', '.join(reasons)}" if reasons else "")
        findings.append(_make_finding(info, _Rule.NO_EFFECT, _Check.CHECK, message, reasons))
    if info.args_mapping_positional_only:
        names = tuple(info.args_mapping_positional_only)
        message = f"{label} maps argument(s) to positional-only constructor parameters: {', '.join(names)}"
        findings.append(_make_finding(info, _Rule.POSITIONAL_ONLY_MAPPING, _Check.CHECK, message, names))
    if info.chain_type is not None:
        kind = info.chain_type.value
        message = f"{label} forms a `{kind}` deprecation chain"
        findings.append(_make_finding(info, _Rule.CHAIN, _Check.CHAINS, message, (kind,)))
    return findings
