"""Shared wrapper fixtures and expected messages for the :mod:`deprecate._cli` unit tests."""

from deprecate._types import DeprecationConfig, TargetMode
from deprecate.audit import DeprecationWrapperInfo
from deprecate.audit._findings import _Check, _make_finding, _Rule

# Warn-only wrapper with no replacement named — trips the ``message-required`` policy rule.
_POLICY_VIOLATION = DeprecationWrapperInfo(
    module="mod",
    function="warn_only_fn",
    deprecated_info=DeprecationConfig(deprecated_in="1.0", target=TargetMode.NOTIFY),
)


_EXPIRED_MSG = (
    "Callable `fn` was scheduled for removal in version 1.0"
    " but still exists in version 2.0. Please delete this deprecated code."
)
# The ``expired`` finding the expiry check derives, carrying :data:`_EXPIRED_MSG`.
_EXPIRED_FINDING = _make_finding(DeprecationWrapperInfo(function="fn"), _Rule.EXPIRED, _Check.EXPIRY, _EXPIRED_MSG)
# The ``message-required`` finding of :data:`_POLICY_VIOLATION`.
_MESSAGE_REQUIRED_FINDING = _make_finding(
    _POLICY_VIOLATION,
    _Rule.MESSAGE_REQUIRED,
    _Check.POLICY,
    "[message-required] Callable `mod.warn_only_fn` warns without naming a replacement",
)
