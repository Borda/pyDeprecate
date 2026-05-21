"""Collection of deprecated wrappers using legacy ``target=bool|None`` sentinels.

This module mirrors a subset of :mod:`tests.collection_deprecate` fixtures but
uses the **legacy** target sentinels (``target=None`` for warn-only and
``target=True`` for self-deprecation/ARGS_REMAP) directly, instead of the
modern :class:`deprecate.TargetMode` enum values.

These fixtures intentionally trigger the ``FutureWarning`` emitted by
``TargetMode._from_legacy()`` at decoration time.  Those warnings are suppressed
during test discovery via the targeted ``filterwarnings`` entries in
``pyproject.toml`` (scoped by message regex AND originating module
``deprecate._types``).

The file scope makes the purpose self-evident, so fixture names here match
their modern counterparts in :mod:`tests.collection_deprecate` without any
``legacy_`` prefix.

"""

from functools import partial
from warnings import warn

from deprecate import deprecated, void
from tests.collection_targets import (
    NewCls,
    add_values,
    double_value,
    fn_remap_with_extra_body,
    increment_value,
    power_with_new_coef,
    tracked_identity,
)

_deprecation_warning = partial(warn, category=DeprecationWarning)


# Legacy sentinel coverage: parallels ``decorated_sum_warn_only`` using ``target=None`` directly.
# The FutureWarning emitted by ``TargetMode._from_legacy(None)`` is suppressed via the targeted
# ``filterwarnings`` entry in pyproject.toml (scoped by message + ``deprecate._types`` module).
@deprecated(target=None, deprecated_in="0.2", remove_in="0.3")
def decorated_sum_warn_only(a: int, b: int = 5) -> int:
    """Legacy sentinel coverage: warn-only deprecation using ``target=None`` directly."""
    return void(a, b)


# Legacy sentinel coverage: parallels ``decorated_pow_self`` using ``target=True`` directly.
# The FutureWarning emitted by ``TargetMode._from_legacy(True)`` is suppressed via the targeted
# ``filterwarnings`` entry in pyproject.toml (scoped by message + ``deprecate._types`` module).
@deprecated(
    target=True,
    deprecated_in="0.1",
    remove_in="0.5",
    args_mapping={"coef": "new_coef"},
)
def decorated_pow_self(base: float, coef: float = 0, new_coef: float = 0) -> float:
    """Legacy sentinel coverage: self-deprecation using ``target=True`` directly."""
    return base**new_coef


class ServiceCls:
    """Class fixture mirroring :class:`tests.collection_deprecate.ServiceCls` legacy method variants."""

    def compute(self, x: int) -> int:
        """Current implementation (same as modern ServiceCls)."""
        return x * 2

    # Legacy sentinel coverage: parallels ``old_warn_method`` using ``target=None`` directly.
    @deprecated(target=None, deprecated_in="1.0", remove_in="2.0")
    def old_warn_method(self, x: int) -> int:
        """Legacy sentinel coverage: warn-only method using ``target=None`` directly."""
        return self.compute(x)

    # Legacy sentinel coverage: parallels ``self_renamed_method`` using ``target=True`` directly.
    @deprecated(
        target=True,
        deprecated_in="1.0",
        remove_in="2.0",
        args_mapping={"old_x": "x"},
    )
    def self_renamed_method(self, old_x: int = 0, x: int = 0) -> int:
        """Legacy sentinel coverage: self-deprecation method using ``target=True`` directly."""
        return self.compute(x)


# Legacy sentinel coverage: parallels ``depr_target_mode_args_only_with_args_extra_injects_kwargs``
# using ``target=True`` directly (instead of ``TargetMode.ARGS_REMAP``).
@deprecated(
    target=True,
    deprecated_in="1.2",
    remove_in="2.0",
    args_mapping={"old_x": "x"},
    args_extra={"y": 10},
)
def depr_target_mode_args_only_with_args_extra_injects_kwargs(x: int = 0, y: int = 0, old_x: int = 0) -> int:
    """Legacy sentinel coverage: ARGS_REMAP wrapper that injects extra kwargs using ``target=True``."""
    return add_values(x, y)


# Legacy sentinel coverage: parallels ``depr_target_mode_whole_warns_on_every_call`` using ``target=None``.
@deprecated(target=None, deprecated_in="1.2", remove_in="2.0", num_warns=-1)
def depr_target_mode_whole_warns_on_every_call(x: int) -> int:
    """Legacy sentinel coverage: NOTIFY wrapper that warns on every call using ``target=None``."""
    return double_value(x)


# Legacy sentinel coverage: parallels ``depr_target_mode_whole_executes_original_body`` using ``target=None``.
@deprecated(target=None, deprecated_in="1.2", remove_in="2.0")
def depr_target_mode_whole_executes_original_body(x: int) -> int:
    """Legacy sentinel coverage: NOTIFY wrapper that records body execution using ``target=None``."""
    return tracked_identity(x)


# Legacy sentinel coverage: parallels ``depr_target_mode_args_only_warns_when_old_arg_passed`` using ``target=True``.
@deprecated(
    target=True,
    deprecated_in="1.2",
    remove_in="2.0",
    args_mapping={"old_x": "x"},
)
def depr_target_mode_args_only_warns_when_old_arg_passed(x: int = 0, old_x: int = 0) -> int:
    """Legacy sentinel coverage: ARGS_REMAP wrapper used when callers pass the old name."""
    return increment_value(x)


# Legacy sentinel coverage: parallels ``depr_target_mode_args_only_silent_when_new_arg_passed`` using ``target=True``.
@deprecated(
    target=True,
    deprecated_in="1.2",
    remove_in="2.0",
    args_mapping={"old_x": "x"},
)
def depr_target_mode_args_only_silent_when_new_arg_passed(x: int = 0, old_x: int = 0) -> int:
    """Legacy sentinel coverage: ARGS_REMAP wrapper used when callers already use the new name."""
    return increment_value(x)


# Legacy sentinel coverage: parallels ``depr_target_mode_args_only_remaps_kwargs`` using ``target=True``.
@deprecated(
    target=True,
    deprecated_in="1.2",
    remove_in="2.0",
    args_mapping={"coef": "new_coef"},
)
def depr_target_mode_args_only_remaps_kwargs(base: float, new_coef: float = 1.0, coef: float = 1.0) -> float:
    """Legacy sentinel coverage: ARGS_REMAP wrapper that remaps kwargs before executing."""
    return power_with_new_coef(base, new_coef)


# Legacy sentinel coverage: parallels ``depr_pow_self_double`` using ``target=True`` directly.
@deprecated(
    target=True,
    template_mgs="The `%(source_name)s` uses depr. args: %(argument_map)s.",
    args_mapping={"c1": "nc1", "c2": "nc2"},
)
def depr_pow_self_double(base: float, c1: float = 0, c2: float = 0, nc1: float = 1, nc2: float = 2) -> float:
    """Legacy sentinel coverage: self-deprecation renaming multiple parameters using ``target=True``."""
    return base ** (c1 + c2 + nc1 + nc2)


# Legacy sentinel coverage: parallels ``fn_remap_with_extra`` using ``target=True`` directly.
@deprecated(
    target=True,
    args_mapping={"old_arg": "new_arg"},
    args_extra={"injected": 100},
    deprecated_in="1.0",
    remove_in="2.0",
)
def fn_remap_with_extra(old_arg: int = 0, new_arg: int = 0, injected: int = 0) -> int:
    """Legacy sentinel coverage: ARGS_REMAP source body merging remap with extra kwargs using ``target=True``."""
    return fn_remap_with_extra_body(new_arg=new_arg, injected=injected)


class ThisCls(NewCls):
    """Legacy sentinel coverage: class with deprecated ``__init__`` remapping argument using ``target=True``."""

    @deprecated(
        target=True,
        deprecated_in="0.3",
        remove_in="0.5",
        args_mapping={"c": "nc"},
        stream=_deprecation_warning,
    )
    def __init__(self, c: int = 3, nc: int = 5) -> None:
        """Initialize ThisCls (legacy sentinel variant)."""
        super().__init__(c=nc)
