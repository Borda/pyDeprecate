"""Shared wrapper fixtures and expected messages for the :mod:`deprecate._cli` unit tests."""

from deprecate._types import DeprecationConfig, TargetMode
from deprecate.audit import DeprecationWrapperInfo

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
