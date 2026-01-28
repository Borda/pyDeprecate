"""Collection of deprecated functions that call other deprecated functions.

This module contains test examples for validate_deprecation_chains functionality.
"""

from deprecate import deprecated
from tests.collection_targets import base_pow_args, base_sum_kwargs


@deprecated(target=base_sum_kwargs, deprecated_in="1.0", remove_in="2.0")
def deprecated_callee(a: int, b: int = 5) -> int:
    """A deprecated function that has a target."""
    pass


@deprecated(target=None, deprecated_in="1.0", remove_in="2.0")
def deprecated_callee_no_target(a: int, b: int = 5) -> int:
    """A deprecated function without a target."""
    return a + b + 1


@deprecated(target=base_pow_args, deprecated_in="1.0", remove_in="2.0", args_mapping={"old_arg": "a"})
def deprecated_callee_with_args(old_arg: float, b: int = 2) -> float:
    """A deprecated function with argument mapping."""
    pass


@deprecated(target=None, deprecated_in="1.5", remove_in="2.5")
def caller_calls_deprecated(a: int, b: int = 5) -> int:
    """A deprecated function that calls another deprecated function."""
    return deprecated_callee(a, b)


@deprecated(target=None, deprecated_in="1.5", remove_in="2.5")
def caller_calls_deprecated_no_target(a: int, b: int = 5) -> int:
    """A deprecated function that calls another deprecated function without target."""
    return deprecated_callee_no_target(a, b)


@deprecated(target=None, deprecated_in="1.5", remove_in="2.5")
def caller_passes_deprecated_arg(value: float) -> float:
    """A deprecated function that passes deprecated argument."""
    return deprecated_callee_with_args(old_arg=value)


def non_deprecated_caller(a: int, b: int = 5) -> int:
    """A non-deprecated function that calls a deprecated function."""
    return deprecated_callee(a, b)


@deprecated(target=None, deprecated_in="1.5", remove_in="2.5")
def caller_no_deprecated_calls(a: int, b: int = 3) -> int:
    """A deprecated function that doesn't call other deprecated functions."""
    return base_sum_kwargs(a, b)
