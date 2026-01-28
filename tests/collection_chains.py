"""Collection of deprecated functions that call other deprecated functions.

This module contains test examples for validate_deprecation_chains functionality.
"""

from deprecate import deprecated


def target_func(value: int) -> int:
    """Target function for deprecated functions."""
    return value * 2


def target_with_args(new_arg: int) -> int:
    """Target function with renamed arguments."""
    return new_arg * 3


@deprecated(target=target_func, deprecated_in="1.0", remove_in="2.0")
def deprecated_callee(value: int) -> int:
    """A deprecated function that has a target."""
    pass


@deprecated(target=None, deprecated_in="1.0", remove_in="2.0")
def deprecated_callee_no_target(value: int) -> int:
    """A deprecated function without a target."""
    return value + 1


@deprecated(target=target_with_args, deprecated_in="1.0", remove_in="2.0", args_mapping={"old_arg": "new_arg"})
def deprecated_callee_with_args(old_arg: int) -> int:
    """A deprecated function with argument mapping."""
    pass


@deprecated(target=None, deprecated_in="1.5", remove_in="2.5")
def caller_calls_deprecated(value: int) -> int:
    """A deprecated function that calls another deprecated function."""
    return deprecated_callee(value)


@deprecated(target=None, deprecated_in="1.5", remove_in="2.5")
def caller_calls_deprecated_no_target(value: int) -> int:
    """A deprecated function that calls another deprecated function without target."""
    return deprecated_callee_no_target(value)


@deprecated(target=None, deprecated_in="1.5", remove_in="2.5")
def caller_passes_deprecated_arg(value: int) -> int:
    """A deprecated function that passes deprecated argument."""
    return deprecated_callee_with_args(old_arg=value)


def non_deprecated_caller(value: int) -> int:
    """A non-deprecated function that calls a deprecated function."""
    return deprecated_callee(value)


@deprecated(target=None, deprecated_in="1.5", remove_in="2.5")
def caller_no_deprecated_calls(value: int) -> int:
    """A deprecated function that doesn't call other deprecated functions."""
    return target_func(value)
