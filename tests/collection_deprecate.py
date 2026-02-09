"""Collection of deprecated functions for testing purposes.

This module contains various examples of deprecated functions with different
configurations to test the deprecation functionality.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from sklearn.metrics import accuracy_score

from deprecate import deprecated, void
from tests.collection_targets import (
    NewDataClass,
    NewEnum,
    NewIntEnum,
    TimerDecorator,
    base_pow_args,
    base_sum_kwargs,
    timing_wrapper,
)

_SHORT_MSG_FUNC = "`%(source_name)s` >> `%(target_name)s` in v%(deprecated_in)s rm v%(remove_in)s."
_SHORT_MSG_ARGS = "Depr: v%(deprecated_in)s rm v%(remove_in)s for args: %(argument_map)s."


@deprecated(target=None, deprecated_in="0.1", remove_in="0.2", num_warns=-1)
class DeprecatedEnum(Enum):
    """Deprecated enum for regression testing."""

    ALPHA = "alpha"
    BETA = "beta"


@deprecated(target=None, deprecated_in="0.1", remove_in="0.2", num_warns=-1)
class DeprecatedIntEnum(Enum):
    """Deprecated enum with integer values for regression testing."""

    ONE = 1
    TWO = 2


@deprecated(target=NewEnum, deprecated_in="0.1", remove_in="0.2", num_warns=-1)
class RedirectedEnum(Enum):
    """Deprecated enum that forwards to a new enum."""

    ALPHA = "alpha"
    BETA = "beta"


@deprecated(
    target=NewEnum,
    deprecated_in="0.1",
    remove_in="0.2",
    num_warns=-1,
    args_mapping={"old_value": "value"},
)
class MappedEnum(Enum):
    """Deprecated enum mapping old_value to value when member names differ."""

    OLD_ALPHA = "alpha"
    OLD_BETA = "beta"


@deprecated(
    target=NewIntEnum,
    deprecated_in="0.1",
    remove_in="0.2",
    num_warns=-1,
    args_mapping={"old_value": "value"},
)
class MappedIntEnum(Enum):
    """Deprecated int enum mapping old_value to NewIntEnum values with different names."""

    ONE = 1
    TWO = 2


@deprecated(
    target=NewEnum,
    deprecated_in="0.1",
    remove_in="0.2",
    num_warns=-1,
    args_mapping={"old_value": "value"},
)
class MappedValueEnum(Enum):
    """Deprecated enum mapping old_value while values differ from the new enum."""

    ALPHA = "old-alpha"
    BETA = "old-beta"


@deprecated(
    target=True,
    deprecated_in="0.1",
    remove_in="0.2",
    num_warns=-1,
    args_mapping={"old_value": "value"},
)
class SelfMappedEnum(Enum):
    """Deprecated enum mapping old_value within the same enum for keyword compatibility."""

    ALPHA = "alpha"
    BETA = "beta"


@deprecated(target=None, deprecated_in="0.1", remove_in="0.2", num_warns=-1)
@dataclass
class DeprecatedDataClass:
    """Deprecated dataclass for regression testing."""

    name: str
    count: int = 0


@deprecated(target=NewDataClass, deprecated_in="0.1", remove_in="0.2", num_warns=-1)
@dataclass
class RedirectedDataClass:
    """Deprecated dataclass forwarding to NewDataClass."""

    name: str
    count: int = 0


@deprecated(target=None, deprecated_in="0.2", remove_in="0.3")
def depr_sum_warn_only(a: int, b: int = 5) -> int:
    """Deprecated function that only warns without forwarding."""
    return void(a, b)


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.5")
def depr_sum(a: int, b: int = 5) -> int:
    """Deprecated sum function forwarding to base_sum_kwargs."""
    return void(a, b)


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.6", stream=None)
def depr_sum_no_stream(a: int, b: int = 5) -> int:
    """Deprecated sum function with no warning stream."""
    return void(a, b)


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.7", num_warns=2)
def depr_sum_calls_2(a: int, b: int = 5) -> int:
    """Deprecated sum function with limited warnings."""
    return void(a, b)


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.7", num_warns=-1)
def depr_sum_calls_inf(a: int, b: int = 5) -> int:
    """Deprecated sum function with infinite warnings."""
    return void(a, b)


@deprecated(
    target=base_sum_kwargs,
    deprecated_in="0.1",
    remove_in="0.5",
    template_mgs="v%(deprecated_in)s: `%(source_name)s` was deprecated, use `%(target_name)s`",
)
def depr_sum_msg(a: int, b: int = 5) -> int:
    """Deprecated sum function with custom message."""
    return void(a, b)


@deprecated(target=base_pow_args, deprecated_in="1.0", remove_in="1.3", template_mgs=_SHORT_MSG_FUNC)
def depr_pow_args(a: float, b: float) -> float:
    """Deprecated power function with args mapping."""
    return void(a, b)


@deprecated(target=base_pow_args, deprecated_in="0.1", remove_in="0.5")
def depr_pow_mix(a: int, b: float = 4) -> float:
    """Deprecated power function with mixed types."""
    return void(a, b)


@deprecated(target=base_pow_args, deprecated_in="0.1", remove_in="0.5")
def depr_pow_wrong(a: int, c: float = 4) -> float:
    """Deprecated power function with wrong mapping."""
    return void(a, c)


@deprecated(target=accuracy_score, args_mapping={"preds": "y_pred", "yeah_arg": None})  # type: ignore
def depr_accuracy_skip(preds: list, y_true: tuple = (0, 1, 1, 2), yeah_arg: float = 1.23) -> float:
    """Deprecated accuracy function with skipped args."""
    return void(preds, y_true, yeah_arg)


@deprecated(target=accuracy_score, args_mapping={"preds": "y_pred", "truth": "y_true"})
def depr_accuracy_map(preds: list, truth: tuple = (0, 1, 1, 2)) -> float:
    """Deprecated accuracy function with mapped args."""
    return void(preds, truth)


@deprecated(target=accuracy_score, args_extra={"y_pred": (0, 1, 1, 1)})
def depr_accuracy_extra(y_pred: list, y_true: tuple = (0, 1, 1, 2)) -> float:
    """Deprecated accuracy function with extra args."""
    return void(y_pred, y_true)


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={"coef": "new_coef"})
def depr_pow_self(base: float, coef: float = 0, new_coef: float = 0) -> float:
    """Deprecated self-referencing power function."""
    return base**new_coef


@deprecated(
    target=True,
    template_mgs="The `%(source_name)s` uses depr. args: %(argument_map)s.",
    args_mapping={"c1": "nc1", "c2": "nc2"},
)
def depr_pow_self_double(base: float, c1: float = 0, c2: float = 0, nc1: float = 1, nc2: float = 2) -> float:
    """Deprecated self-referencing power function with double mapping."""
    return base ** (c1 + c2 + nc1 + nc2)


@deprecated(True, "0.3", "0.6", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS)
@deprecated(True, "0.4", "0.7", args_mapping={"nc1": "nc2"}, template_mgs=_SHORT_MSG_ARGS)
def depr_pow_self_twice(base: float, c1: float = 0, nc1: float = 0, nc2: float = 2) -> float:
    """Deprecated self-referencing power function with chained decorators."""
    return base ** (c1 + nc1 + nc2)


@deprecated(True, "0.3", "0.4", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=True)
@deprecated(True, "0.1", "0.2", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=False)
def depr_pow_skip_if_true_false(base: float, c1: float = 1, nc1: float = 1) -> float:
    """Deprecated power function with conditional skip."""
    return base ** (c1 - nc1)


@deprecated(True, "0.1", "0.2", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=False)
@deprecated(True, "0.3", "0.4", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=True)
def depr_pow_skip_if_false_true(base: float, c1: float = 1, nc1: float = 1) -> float:
    """Deprecated power function with conditional skip reversed."""
    return base ** (c1 - nc1)


@deprecated(True, "0.1", "0.2", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=True)
def depr_pow_skip_if_true(base: float, c1: float = 1, nc1: float = 1) -> float:
    """Deprecated power function that skips if true."""
    return base ** (c1 - nc1)


@deprecated(True, "0.1", "0.2", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=lambda: True)
def depr_pow_skip_if_func(base: float, c1: float = 1, nc1: float = 1) -> float:
    """Deprecated power function that skips based on function."""
    return base ** (c1 - nc1)


@deprecated(True, "0.1", "0.3", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=lambda: 42)
def depr_pow_skip_if_func_int(base: float, c1: float = 1, nc1: float = 1) -> float:
    """Deprecated power function that skips based on int-returning function."""
    return base ** (c1 - nc1)


@deprecated(target=accuracy_score, args_mapping={"preds": "y_pred", "truth": "y_true"})
def depr_accuracy_target(preds: list, truth: tuple = (0, 1, 1, 2)) -> float:
    """A properly configured deprecation with accuracy_score target - has effect."""
    return void(preds, truth)


# ========== Wrapper Deprecation Examples ==========


# Deprecate a function-based timing wrapper in favor of the improved timing_wrapper
@deprecated(target=timing_wrapper, deprecated_in="1.0", remove_in="2.0")
def depr_timing_wrapper(func: Callable) -> Callable:
    """Deprecated timing wrapper - use timing_wrapper instead (better precision and output)."""
    return void(func)


# Deprecate a class-based timer decorator in favor of the improved TimerDecorator
class DeprTimerDecorator(TimerDecorator):
    """Deprecated class-based timer - use TimerDecorator instead (better precision and tracking).

    This class inherits from TimerDecorator to provide the same functionality
    but shows a deprecation warning when used.
    """

    @deprecated(target=TimerDecorator, deprecated_in="1.0", remove_in="2.0")
    def __init__(self, func: Callable) -> None:
        """Initialize deprecated timer."""
        void(func)
