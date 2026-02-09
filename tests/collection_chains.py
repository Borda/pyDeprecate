"""Collection of deprecated functions that call other deprecated functions.

This module contains test examples for validate_deprecation_chains functionality.
"""

from deprecate import deprecated
from tests.collection_targets import base_pow_args, base_sum_kwargs


@deprecated(target=base_sum_kwargs, deprecated_in="1.0", remove_in="2.0")
def deprecated_callee(a: int, b: int = 5) -> int:
    """Deprecated wrapper forwarding to base_sum_kwargs.

    Examples:
        A function was deprecated in favor of base_sum_kwargs. This wrapper
        forwards all calls to the new implementation automatically.
    """
    pass


@deprecated(target=None, deprecated_in="1.0", remove_in="2.0")
def deprecated_callee_no_target(a: int, b: int = 5) -> int:
    """Warning-only deprecation with no target replacement.

    Examples:
        A function is marked as deprecated but has no replacement yet. Users
        get a warning, but the original implementation still executes.
    """
    return a + b + 1


@deprecated(target=base_pow_args, deprecated_in="1.0", remove_in="2.0", args_mapping={"old_arg": "a"})
def deprecated_callee_with_args(old_arg: float, b: int = 2) -> float:
    """Deprecated wrapper with argument name changes.

    Examples:
        A function's argument was renamed from `old_arg` to `a`. The wrapper
        automatically maps the old name to the new parameter when forwarding.
    """
    pass


@deprecated(target=None, deprecated_in="1.5", remove_in="2.5")
def caller_calls_deprecated(a: int, b: int = 5) -> int:
    """Deprecated function that calls another deprecated function.

    Examples:
        A "lazy" deprecated wrapper that still calls an older deprecated function
        instead of directly calling the target. This creates a deprecation chain
        that should be detected and flagged.
    """
    return deprecated_callee(a, b)


@deprecated(target=None, deprecated_in="1.5", remove_in="2.5")
def caller_calls_deprecated_no_target(a: int, b: int = 5) -> int:
    """Deprecated function calling another deprecated function without target.

    Examples:
        A deprecated function calls another deprecated function that has no
        replacement target. While not ideal, this doesn't suggest a direct fix.
    """
    return deprecated_callee_no_target(a, b)


@deprecated(target=None, deprecated_in="1.5", remove_in="2.5")
def caller_passes_deprecated_arg(value: float) -> float:
    """Deprecated function passing deprecated arguments to another.

    Examples:
        A function passes arguments using old parameter names to another
        deprecated function. This creates both a deprecation chain and uses
        deprecated argument names that should be updated.
    """
    return deprecated_callee_with_args(old_arg=value)


def non_deprecated_caller(a: int, b: int = 5) -> int:
    """Non-deprecated function calling a deprecated function.

    Examples:
        A regular function that hasn't been deprecated yet but still calls
        a deprecated function. This should be flagged to update the call to
        use the new target directly.
    """
    return deprecated_callee(a, b)


@deprecated(target=None, deprecated_in="1.5", remove_in="2.5")
def caller_no_deprecated_calls(a: int, b: int = 3) -> int:
    """Deprecated function with clean implementation calling target directly.

    Examples:
        A properly implemented deprecated wrapper that directly calls the new
        target function instead of going through other deprecated functions.
        This is the correct pattern and should not trigger warnings.
    """
    return base_sum_kwargs(a, b)
