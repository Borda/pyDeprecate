"""Collection of deprecated functions for testing purposes.

This module contains deprecated wrappers covering real-world use cases:

- Warning-only deprecation (no forwarding, just notify users)
- Basic call forwarding to a replacement function
- Silent deprecation (no warning stream)
- Controlled warning frequency (warn N times, warn every call)
- Custom warning messages
- Positional argument forwarding with type differences
- Mismatched argument names (expected failure case)
- Argument renaming when forwarding to a third-party API
- Skipping/dropping deprecated arguments
- Injecting extra arguments into the forwarded call
- Self-deprecation (renaming args within the same function)
- Multiple argument renames in a single decorator
- Chained deprecation (stacked decorators for multi-step migration)
- Conditional skip (skip_if with bool and callable)
- Deprecating decorator/wrapper functions
- Deprecating class-based decorators via __init__
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
    """Deprecated enum for regression testing.

    Example:
        A user can still resolve DeprecatedEnum("alpha") via value lookup (DeprecatedEnum.ALPHA).
    """

    ALPHA = "alpha"
    BETA = "beta"


@deprecated(target=None, deprecated_in="0.1", remove_in="0.2", num_warns=-1)
class DeprecatedIntEnum(Enum):
    """Deprecated enum with integer values for regression testing.

    Example:
        A user can still resolve a deprecated enum by integer value such as 1.
    """

    ONE = 1
    TWO = 2


@deprecated(target=NewEnum, deprecated_in="0.1", remove_in="0.2", num_warns=-1)
class RedirectedEnum(Enum):
    """Deprecated enum that forwards to a new enum.

    Example:
        A user can still call RedirectedEnum("alpha") via value lookup and receive NewEnum.ALPHA.
    """

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
    """Deprecated enum with old_value->value mapping, where member names differ but values match NewEnum.

    Example:
        A user can pass old_value="alpha" and resolve NewEnum.ALPHA.
    """

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
    """Deprecated int enum mapping old_value->value where member names differ from NewIntEnum.

    Example:
        A user can pass old_value=1 and resolve NewIntEnum.ALPHA.
    """

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
    """Deprecated enum with old_value->value mapping, where member values differ from NewEnum's values.

    Example:
        A user can pass old_value="alpha" even though ALPHA stores "old-alpha".
    """

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
    """Deprecated enum with old_value->value mapping that forwards to itself via target=True.

    Example:
        A user can call SelfMappedEnum(old_value="alpha") to resolve SelfMappedEnum.ALPHA.
    """

    ALPHA = "alpha"
    BETA = "beta"


@deprecated(target=None, deprecated_in="0.1", remove_in="0.2", num_warns=-1)
@dataclass
class DeprecatedDataClass:
    """Deprecated dataclass for regression testing.

    Example:
        A user can still instantiate DeprecatedDataClass(name="alpha", count=2).
    """

    name: str
    count: int = 0


@deprecated(target=NewDataClass, deprecated_in="0.1", remove_in="0.2", num_warns=-1)
@dataclass
class RedirectedDataClass:
    """Deprecated dataclass forwarding to NewDataClass.

    Example:
        A user can still instantiate RedirectedDataClass(name="alpha", count=2).
    """

    name: str
    count: int = 0


@deprecated(target=None, deprecated_in="0.2", remove_in="0.3")
def depr_sum_warn_only(a: int, b: int = 5) -> int:
    """Warning-only deprecation with no forwarding.

    Examples:
        The function is going away but has no replacement yet. The user gets
        warned, but the original body still executes (`target=None`).
    """
    return void(a, b)


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.5")
def depr_sum(a: int, b: int = 5) -> int:
    """Basic call forwarding to a replacement function.

    Examples:
        User calls an old function that has been replaced. Warns and
        transparently forwards the call to `base_sum_kwargs`.
    """
    return void(a, b)


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.6", stream=None)
def depr_sum_no_stream(a: int, b: int = 5) -> int:
    """Silent forwarding with no warning emitted.

    Examples:
        Library silently redirects calls without bothering the user.
        Forwards to `base_sum_kwargs` with `stream=None`.
    """
    return void(a, b)


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.7", num_warns=2)
def depr_sum_calls_2(a: int, b: int = 5) -> int:
    """Limited warning frequency to avoid log spam.

    Examples:
        Warn the user only the first 2 times, then forward silently (`num_warns=2`).
    """
    return void(a, b)


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.7", num_warns=-1)
def depr_sum_calls_inf(a: int, b: int = 5) -> int:
    """Warn on every single call for maximum visibility.

    Examples:
        Critical migration where users must be reminded every time (`num_warns=-1`).
    """
    return void(a, b)


@deprecated(
    target=base_sum_kwargs,
    deprecated_in="0.1",
    remove_in="0.5",
    template_mgs="v%(deprecated_in)s: `%(source_name)s` was deprecated, use `%(target_name)s`",
)
def depr_sum_msg(a: int, b: int = 5) -> int:
    """Custom warning message template.

    Examples:
        Library wants to control exactly what the user sees instead of the default
        template. Uses `template_mgs` with format specifiers.
    """
    return void(a, b)


@deprecated(target=base_pow_args, deprecated_in="1.0", remove_in="1.3", template_mgs=_SHORT_MSG_FUNC)
def depr_pow_args(a: float, b: float) -> float:
    """Positional argument forwarding with a compact warning template.

    Examples:
        Both old and new functions take positional args; the library wants
        a short, machine-readable warning format.
    """
    return void(a, b)


@deprecated(target=base_pow_args, deprecated_in="0.1", remove_in="0.5")
def depr_pow_mix(a: int, b: float = 4) -> float:
    """Forwarding with mixed parameter types between source and target.

    Examples:
        Old function has `(int, float)` but target expects `(float, int)`. Tests
        that the forwarding handles type differences gracefully.
    """
    return void(a, b)


@deprecated(target=base_pow_args, deprecated_in="0.1", remove_in="0.5")
def depr_pow_wrong(a: int, c: float = 4) -> float:
    """Mismatched argument name between source and target (expected failure).

    Examples:
        Developer misconfigures deprecation — param `c` in source doesn't exist
        in target (expects `b`), which triggers a `TypeError` at call time.
    """
    return void(a, c)


@deprecated(target=accuracy_score, args_mapping={"preds": "y_pred", "yeah_arg": None})  # type: ignore
def depr_accuracy_skip(preds: list, y_true: tuple = (0, 1, 1, 2), yeah_arg: float = 1.23) -> float:
    """Argument renaming with one arg dropped entirely.

    Examples:
        Old API had extra args the new API doesn't need. `preds` is renamed
        to `y_pred`, `yeah_arg` is dropped (mapped to `None`).
    """
    return void(preds, y_true, yeah_arg)


@deprecated(target=accuracy_score, args_mapping={"preds": "y_pred", "truth": "y_true"})
def depr_accuracy_map(preds: list, truth: tuple = (0, 1, 1, 2)) -> float:
    """Multiple argument renames when forwarding to a third-party API.

    Examples:
        Old API used different param names. `preds` -> `y_pred` and
        `truth` -> `y_true`; user calls with old names, target receives new ones.
    """
    return void(preds, truth)


@deprecated(target=accuracy_score, args_extra={"y_pred": (0, 1, 1, 1)})
def depr_accuracy_extra(y_pred: list, y_true: tuple = (0, 1, 1, 2)) -> float:
    """Injecting and overriding arguments in the forwarded call.

    Examples:
        The wrapper forces a fixed `y_pred` to be sent to `accuracy_score`,
        overriding any user-provided `y_pred` via `args_extra={"y_pred": ...}`.
    """
    return void(y_pred, y_true)


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={"coef": "new_coef"})
def depr_pow_self(base: float, coef: float = 0, new_coef: float = 0) -> float:
    """Self-deprecation: renaming a parameter within the same function.

    Examples:
        User calls `my_func(coef=5)` but the new name is `new_coef`. The function
        body uses the new name; the decorator transparently remaps the old name (`target=True`).
    """
    return base**new_coef


@deprecated(
    target=True,
    template_mgs="The `%(source_name)s` uses depr. args: %(argument_map)s.",
    args_mapping={"c1": "nc1", "c2": "nc2"},
)
def depr_pow_self_double(base: float, c1: float = 0, c2: float = 0, nc1: float = 1, nc2: float = 2) -> float:
    """Self-deprecation: renaming multiple parameters at once.

    Examples:
        Both `c1` -> `nc1` and `c2` -> `nc2` are remapped in a single decorator
        with a custom warning template.
    """
    return base ** (c1 + c2 + nc1 + nc2)


@deprecated(True, "0.3", "0.6", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS)
@deprecated(True, "0.4", "0.7", args_mapping={"nc1": "nc2"}, template_mgs=_SHORT_MSG_ARGS)
def depr_pow_self_twice(base: float, c1: float = 0, nc1: float = 0, nc2: float = 2) -> float:
    """Chained deprecation: multi-step parameter migration across versions.

    Examples:
        V0.3 renamed `c1` to `nc1`, then v0.4 renamed `nc1` to `nc2`. Stacked
        decorators handle users calling with any of the historical names.
    """
    return base ** (c1 + nc1 + nc2)


@deprecated(True, "0.3", "0.4", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=True)
@deprecated(True, "0.1", "0.2", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=False)
def depr_pow_skip_if_true_false(base: float, c1: float = 1, nc1: float = 1) -> float:
    """Conditional skip: outer decorator skipped, inner fires.

    Examples:
        One migration step is conditionally disabled (e.g., feature flag)
        while another is active. Tests `skip_if=True` on outer, `skip_if=False` on inner.
    """
    return base ** (c1 - nc1)


@deprecated(True, "0.1", "0.2", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=False)
@deprecated(True, "0.3", "0.4", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=True)
def depr_pow_skip_if_false_true(base: float, c1: float = 1, nc1: float = 1) -> float:
    """Conditional skip: inner decorator skipped, outer fires.

    Examples:
        Reversed order of above — tests that `skip_if` works correctly
        regardless of decorator stacking order.
    """
    return base ** (c1 - nc1)


@deprecated(True, "0.1", "0.2", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=True)
def depr_pow_skip_if_true(base: float, c1: float = 1, nc1: float = 1) -> float:
    """Deprecation entirely disabled via static flag.

    Examples:
        `skip_if=True` means the deprecation is inactive — user calls the function
        normally with no warning and no remapping.
    """
    return base ** (c1 - nc1)


@deprecated(True, "0.1", "0.2", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=lambda: True)
def depr_pow_skip_if_func(base: float, c1: float = 1, nc1: float = 1) -> float:
    """Deprecation controlled by a runtime callable.

    Examples:
        `skip_if` is a callable (e.g., checking an env var or feature flag) that
        returns `True`, so the deprecation is bypassed dynamically.
    """
    return base ** (c1 - nc1)


@deprecated(True, "0.1", "0.3", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=lambda: 42)
def depr_pow_skip_if_func_int(base: float, c1: float = 1, nc1: float = 1) -> float:
    """Invalid skip_if callback returning non-bool (expected failure).

    Examples:
        Developer accidentally returns `42` instead of `bool` from `skip_if` —
        tests that `TypeError` is raised at call time.
    """
    return base ** (c1 - nc1)


@deprecated(target=accuracy_score, args_mapping={"preds": "y_pred", "truth": "y_true"})
def depr_accuracy_target(preds: list, truth: tuple = (0, 1, 1, 2)) -> float:
    """Well-configured wrapper forwarding to a third-party function.

    Examples:
        Positive control — both arg renames are valid and the target accepts
        them. Used by validation tests (`find_deprecated_callables`) to verify correct configs.
    """
    return void(preds, truth)


# ========== Wrapper Deprecation Examples ==========


# Deprecate a function-based timing wrapper in favor of the improved timing_wrapper
@deprecated(target=timing_wrapper, deprecated_in="1.0", remove_in="2.0")
def depr_timing_wrapper(func: Callable) -> Callable:
    """Deprecating a decorator/wrapper function.

    Examples:
        Library replaces a decorator function with an improved version.
        User's code uses `@depr_timing_wrapper` — the call forwards to `timing_wrapper`.
    """
    return void(func)


# Deprecate a class-based timer decorator in favor of the improved TimerDecorator
class DeprTimerDecorator(TimerDecorator):
    """Deprecating a class-based decorator via __init__.

    Examples:
        Library replaces a class-based decorator with an improved version.
        User's code uses `DeprTimerDecorator(func)` — the `__init__` forwards to `TimerDecorator`.
    """

    @deprecated(target=TimerDecorator, deprecated_in="1.0", remove_in="2.0")
    def __init__(self, func: Callable) -> None:
        """Initialize deprecated timer."""
        void(func)
