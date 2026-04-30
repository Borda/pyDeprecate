"""Collection of deprecated functions for testing purposes.

This module contains deprecated wrappers covering real-world use cases:

- Warning-only deprecation (no forwarding, just notify users)
- Basic call forwarding to a replacement function
- Function-to-class forwarding via constructor call
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
- Deprecating individual class methods (warn-only and redirect)
- Instance deprecation via deprecated_instance()
- Class-level deprecation with deprecated_class (Enum and dataclass)

For functions, each decorator/wrapper pair is co-located as a four-element group:
  original_* — source callable
  _deprecation_* — shared deprecated() instance
  @_deprecation_* decorated_* — decorator form
  wrapped_* = _deprecation_*(original_*) — assignment (wrapper) form

Class-level form-equivalence groups follow the same conceptual pattern, but may declare
the shared deprecated_class instance (for example, _class_deprecation_*) before the
corresponding _Original* type.

Decorator-form equivalents (same deprecated_class config as Wrapped* — for parametrize comparison):
- DecoratedEnum: decorator-form enum equivalent of WrappedEnum
- DecoratedDataClass: decorator-form dataclass equivalent of WrappedDataClass
"""

import warnings
from dataclasses import dataclass
from enum import Enum
from functools import partial
from typing import Any, Callable
from warnings import warn

from sklearn.metrics import accuracy_score

from deprecate import TargetMode, deprecated, deprecated_class, deprecated_instance, void
from tests.collection_targets import (
    CrossGuardClassTargetNew,
    NewCls,
    NewDataClass,
    NewEnum,
    NewIntEnum,
    SomeTargetClass,
    TargetColorEnum,
    TimerDecorator,
    add_values,
    base_pow_args,
    base_sum_kwargs,
    cross_guard_standalone_increment,
    double_value,
    identity_value,
    increment_value,
    power_with_new_coef,
    return_b,
    return_new,
    return_none,
    return_z,
    timing_wrapper,
    tracked_identity,
)

_deprecation_warning = partial(warn, category=DeprecationWarning)

_SHORT_MSG_FUNC = "`%(source_name)s` >> `%(target_name)s` in v%(deprecated_in)s rm v%(remove_in)s."
_SHORT_MSG_ARGS = "Depr: v%(deprecated_in)s rm v%(remove_in)s for args: %(argument_map)s."


@deprecated_class(deprecated_in="0.1", remove_in="0.2", num_warns=-1)
class DeprecatedEnum(Enum):
    """Deprecated enum for regression testing.

    Example:
        A user can still resolve DeprecatedEnum("alpha") via value lookup (DeprecatedEnum.ALPHA).
    """

    ALPHA = "alpha"
    BETA = "beta"


@deprecated_class(deprecated_in="0.1", remove_in="0.2", num_warns=-1)
class DeprecatedIntEnum(Enum):
    """Deprecated enum with integer values for regression testing.

    Example:
        A user can still resolve a deprecated enum by integer value such as 1.
    """

    ONE = 1
    TWO = 2


@deprecated_class(target=NewEnum, deprecated_in="0.1", remove_in="0.2", num_warns=-1)
class RedirectedEnum(Enum):
    """Deprecated enum that forwards to a new enum.

    Example:
        A user can still call RedirectedEnum("alpha") via value lookup and receive NewEnum.ALPHA.
    """

    ALPHA = "alpha"
    BETA = "beta"


@deprecated_class(
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


@deprecated_class(
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


@deprecated_class(
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


class _SelfMappedEnum(Enum):
    """Deprecated enum with old_value->value mapping that forwards to itself.

    Example:
        A user can call SelfMappedEnum(old_value="alpha") to resolve SelfMappedEnum.ALPHA.
    """

    ALPHA = "alpha"
    BETA = "beta"


SelfMappedEnum = deprecated_class(
    target=_SelfMappedEnum,
    deprecated_in="0.1",
    remove_in="0.2",
    num_warns=-1,
    args_mapping={"old_value": "value"},
)(_SelfMappedEnum)


# Form-equivalence pairs — shared instance ensures decorator and wrapper form use identical config
class _OriginalEnum(Enum):
    """Original enum class that gets wrapped by deprecated_class in wrapper form."""

    ALPHA = "alpha"
    BETA = "beta"


_class_deprecation_enum = deprecated_class(target=NewEnum, deprecated_in="0.5", remove_in="1.0", num_warns=1)


@_class_deprecation_enum
class DecoratedEnum(Enum):
    """Decorator-form enum with same config as WrappedEnum, for form-equivalence tests."""

    ALPHA = "alpha"
    BETA = "beta"


WrappedEnum = _class_deprecation_enum(_OriginalEnum)


@dataclass
class _OriginalDataClass:
    """Original dataclass that gets wrapped by deprecated_class in wrapper form."""

    label: str
    total: int = 0


_class_deprecation_dataclass = deprecated_class(target=NewDataClass, deprecated_in="0.5", remove_in="1.0", num_warns=1)


@_class_deprecation_dataclass
@dataclass
class DecoratedDataClass:
    """Decorator-form dataclass with same config as WrappedDataClass, for form-equivalence tests."""

    label: str
    total: int = 0


WrappedDataClass = _class_deprecation_dataclass(_OriginalDataClass)


@deprecated_class(deprecated_in="0.1", remove_in="0.2", num_warns=-1)
@dataclass
class DeprecatedDataClass:
    """Deprecated dataclass for regression testing.

    Example:
        A user can still instantiate DeprecatedDataClass(label="alpha", total=2).
    """

    label: str
    total: int = 0


@deprecated_class(target=NewDataClass, deprecated_in="0.1", remove_in="0.2", num_warns=-1)
@dataclass
class RedirectedDataClass:
    """Deprecated dataclass forwarding to NewDataClass.

    Example:
        A user can still instantiate RedirectedDataClass(label="alpha", total=2).
    """

    label: str
    total: int = 0


def original_sum_warn_only(a: int, b: int = 5) -> int:
    """Source function for the assignment-form warn-only pair."""
    return void(a, b)


_deprecation_sum_warn_only = deprecated(target=None, deprecated_in="0.2", remove_in="0.3")


@_deprecation_sum_warn_only
def decorated_sum_warn_only(a: int, b: int = 5) -> int:
    """Warning-only deprecation with no forwarding.

    Examples:
        The function is going away but has no replacement yet. The user gets
        warned, but the original body still executes (`target=None`).
    """
    return void(a, b)


wrapped_sum_warn_only = _deprecation_sum_warn_only(original_sum_warn_only)


def original_sum(a: int, b: int = 5) -> int:
    """Source function for the assignment-form sum pairs."""
    return void(a, b)


_deprecation_sum = deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.5")


@_deprecation_sum
def decorated_sum(a: int, b: int = 5) -> int:
    """Basic call forwarding to a replacement function.

    Examples:
        User calls an old function that has been replaced. Warns and
        transparently forwards the call to `base_sum_kwargs`.
    """
    return void(a, b)


wrapped_sum = _deprecation_sum(original_sum)


@deprecated(target=NewCls, deprecated_in="0.2", remove_in="0.4")
def depr_make_new_cls(c: float, d: str = "abc", **kwargs: Any) -> NewCls:  # noqa: ANN401
    """Forward a deprecated factory function to a class constructor.

    Examples:
        Users call the old function but receive a ``NewCls`` instance.
    """
    return void(c, d, kwargs)


@deprecated(target=NewCls, deprecated_in="0.2", remove_in="0.4", args_mapping={"old_c": "c"})
def depr_make_new_cls_mapped(old_c: float, d: str = "abc", **kwargs: Any) -> NewCls:  # noqa: ANN401
    """Forward a deprecated factory function to a class constructor with argument renaming.

    Examples:
        Old argument ``old_c`` is renamed to ``c`` before forwarding to ``NewCls``.
    """
    return void(old_c, d, kwargs)


_deprecation_sum_no_stream = deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.6", stream=None)


@_deprecation_sum_no_stream
def decorated_sum_no_stream(a: int, b: int = 5) -> int:
    """Silent forwarding with no warning emitted.

    Examples:
        Library silently redirects calls without bothering the user.
        Forwards to `base_sum_kwargs` with `stream=None`.
    """
    return void(a, b)


wrapped_sum_no_stream = _deprecation_sum_no_stream(original_sum)


_deprecation_sum_calls_2 = deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.7", num_warns=2)


@_deprecation_sum_calls_2
def decorated_sum_calls_2(a: int, b: int = 5) -> int:
    """Limited warning frequency to avoid log spam.

    Examples:
        Warn the user only the first 2 times, then forward silently (`num_warns=2`).
    """
    return void(a, b)


wrapped_sum_calls_2 = _deprecation_sum_calls_2(original_sum)


_deprecation_sum_calls_inf = deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.7", num_warns=-1)


@_deprecation_sum_calls_inf
def decorated_sum_calls_inf(a: int, b: int = 5) -> int:
    """Warn on every single call for maximum visibility.

    Examples:
        Critical migration where users must be reminded every time (`num_warns=-1`).
    """
    return void(a, b)


wrapped_sum_calls_inf = _deprecation_sum_calls_inf(original_sum)


_deprecation_sum_msg = deprecated(
    target=base_sum_kwargs,
    deprecated_in="0.1",
    remove_in="0.5",
    template_mgs="v%(deprecated_in)s: `%(source_name)s` was deprecated, use `%(target_name)s`",
)


@_deprecation_sum_msg
def decorated_sum_msg(a: int, b: int = 5) -> int:
    """Custom warning message template.

    Examples:
        Library wants to control exactly what the user sees instead of the default
        template. Uses `template_mgs` with format specifiers.
    """
    return void(a, b)


wrapped_sum_msg = _deprecation_sum_msg(original_sum)


@deprecated(
    target=TargetMode.NOTIFY,
    deprecated_in="0.9",
    remove_in="1.0",
    num_warns=-1,
)
def depr_target_mode_whole_warns_on_every_call(x: int) -> int:
    """TargetMode.NOTIFY wrapper used by integration tests."""
    return double_value(x)


@deprecated(target=TargetMode.NOTIFY, deprecated_in="0.9", remove_in="1.0")
def depr_target_mode_whole_executes_original_body(x: int) -> int:
    """TargetMode.NOTIFY wrapper that records body execution."""
    return tracked_identity(x)


@deprecated(
    target=TargetMode.ARGS_REMAP,
    deprecated_in="0.9",
    remove_in="1.0",
    args_mapping={"old_x": "x"},
)
def depr_target_mode_args_only_warns_when_old_arg_passed(x: int = 0, old_x: int = 0) -> int:
    """TargetMode.ARGS_REMAP wrapper used when callers pass the old name."""
    return increment_value(x)


@deprecated(
    target=TargetMode.ARGS_REMAP,
    deprecated_in="0.9",
    remove_in="1.0",
    args_mapping={"old_x": "x"},
)
def depr_target_mode_args_only_silent_when_new_arg_passed(x: int = 0, old_x: int = 0) -> int:
    """TargetMode.ARGS_REMAP wrapper used when callers already use the new name."""
    return increment_value(x)


@deprecated(
    target=TargetMode.ARGS_REMAP,
    deprecated_in="0.9",
    remove_in="1.0",
    args_mapping={"coef": "new_coef"},
)
def depr_target_mode_args_only_remaps_kwargs(base: float, new_coef: float = 1.0, coef: float = 1.0) -> float:
    """TargetMode.ARGS_REMAP wrapper that remaps kwargs before executing."""
    return power_with_new_coef(base, new_coef)


@deprecated(
    target=TargetMode.ARGS_REMAP,
    deprecated_in="0.9",
    remove_in="1.0",
    args_mapping={"old_x": "x"},
    args_extra={"y": 10},
)
def depr_target_mode_args_only_with_args_extra_injects_kwargs(x: int = 0, y: int = 0, old_x: int = 0) -> int:
    """TargetMode.ARGS_REMAP wrapper that injects extra keyword arguments."""
    return add_values(x, y)


def make_target_mode_args_only_without_args_mapping_warns() -> Callable[[int], int]:
    """Build a TargetMode.ARGS_REMAP wrapper that warns about missing args_mapping."""

    @deprecated(target=TargetMode.ARGS_REMAP, deprecated_in="0.9", remove_in="1.0")
    def noop(x: int) -> int:
        return identity_value(x)

    return noop


def make_target_mode_whole_with_args_mapping_warns() -> Callable[[int], int]:
    """Build a TargetMode.NOTIFY wrapper that warns about ignored args_mapping."""

    @deprecated(
        target=TargetMode.NOTIFY,
        deprecated_in="0.9",
        remove_in="1.0",
        args_mapping={"a": "b"},
    )
    def fn(b: int) -> int:
        return return_b(b)

    return fn


def make_target_mode_whole_with_args_extra_warns() -> Callable[[int], int]:
    """Build a TargetMode.NOTIFY wrapper that warns about ignored args_extra."""

    @deprecated(
        target=TargetMode.NOTIFY,
        deprecated_in="0.9",
        remove_in="1.0",
        args_extra={"z": 1},
    )
    def fn(z: int = 0) -> int:
        return return_z(z)

    return fn


def make_target_mode_args_only_legacy_args_extra() -> Callable[[int], int]:
    """Build a legacy ``target=True`` wrapper with ``args_extra`` for equivalence tests."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)

        @deprecated(
            target=True,
            deprecated_in="0.9",
            remove_in="1.0",
            args_mapping={"old_x": "x"},
            args_extra={"y": 10},
        )
        def fn(x: int, y: int) -> int:
            return add_values(x, y)

    return fn


def make_target_mode_target_false_warns() -> Callable[[], None]:
    """Build a wrapper that warns for the unsupported ``target=False`` sentinel."""

    @deprecated(target=False, deprecated_in="0.9", remove_in="1.0")
    def fn() -> None:
        return return_none()

    return fn


def make_target_mode_target_none_sentinel_emits_future_warning() -> Callable[[], None]:
    """Build a wrapper that warns for the legacy ``target=None`` sentinel."""

    @deprecated(target=None, deprecated_in="0.9", remove_in="1.0")
    def legacy_none() -> None:
        return return_none()

    return legacy_none


def make_target_mode_target_true_sentinel_emits_future_warning() -> Callable[[int], int]:
    """Build a wrapper that warns for the legacy ``target=True`` sentinel."""

    @deprecated(
        target=True,
        deprecated_in="0.9",
        remove_in="1.0",
        args_mapping={"old": "new"},
    )
    def legacy_true(new: int = 0) -> int:
        return return_new(new)

    return legacy_true


@deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0", num_warns=-1)
def depr_class_whole_mode_warns_on_call(x: int) -> int:
    """TargetMode.NOTIFY wrapper used by class integration tests."""
    return double_value(x)


@deprecated(
    target=TargetMode.ARGS_REMAP,
    deprecated_in="1.0",
    remove_in="2.0",
    args_mapping={"old_x": "x"},
    num_warns=-1,
)
def depr_class_args_only_mode_warns_on_deprecated_arg(x: int = 0, old_x: int = 0) -> int:
    """TargetMode.ARGS_REMAP wrapper used by class integration tests."""
    return double_value(x)


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


@deprecated(target=accuracy_score, args_mapping={"preds": "y_pred", "yeah_arg": None})
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


def original_pow_self(base: float, coef: float = 0, new_coef: float = 0) -> float:
    """Source function for the assignment-form self-deprecation pair."""
    return base**new_coef


_deprecation_pow_self = deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={"coef": "new_coef"})


@_deprecation_pow_self
def decorated_pow_self(base: float, coef: float = 0, new_coef: float = 0) -> float:
    """Self-deprecation: renaming a parameter within the same function.

    Examples:
        User calls `my_func(coef=5)` but the new name is `new_coef`. The function
        body uses the new name; the decorator transparently remaps the old name (`target=True`).
    """
    return base**new_coef


wrapped_pow_self = _deprecation_pow_self(original_pow_self)


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


def original_pow_skip(base: float, c1: float = 1, nc1: float = 1) -> float:
    """Source function for the assignment-form skip_if pairs."""
    return base ** (c1 - nc1)


_deprecation_pow_skip_if_true = deprecated(
    True, "0.1", "0.2", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=True
)


@_deprecation_pow_skip_if_true
def decorated_pow_skip_if_true(base: float, c1: float = 1, nc1: float = 1) -> float:
    """Deprecation entirely disabled via static flag.

    Examples:
        `skip_if=True` means the deprecation is inactive — user calls the function
        normally with no warning and no remapping.
    """
    return base ** (c1 - nc1)


wrapped_pow_skip_if_true = _deprecation_pow_skip_if_true(original_pow_skip)


_deprecation_pow_skip_if_func = deprecated(
    True, "0.1", "0.2", args_mapping={"c1": "nc1"}, template_mgs=_SHORT_MSG_ARGS, skip_if=lambda: True
)


@_deprecation_pow_skip_if_func
def decorated_pow_skip_if_func(base: float, c1: float = 1, nc1: float = 1) -> float:
    """Deprecation controlled by a runtime callable.

    Examples:
        `skip_if` is a callable (e.g., checking an env var or feature flag) that
        returns `True`, so the deprecation is bypassed dynamically.
    """
    return base ** (c1 - nc1)


wrapped_pow_skip_if_func = _deprecation_pow_skip_if_func(original_pow_skip)


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
        them. Used by validation tests (`find_deprecation_wrappers`) to verify correct configs.
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
class DeprecatedTimerDecorator(TimerDecorator):
    """Deprecating a class-based decorator via __init__.

    Examples:
        Library replaces a class-based decorator with an improved version.
        User's code uses `DeprecatedTimerDecorator(func)` — the `__init__` forwards to `TimerDecorator`.
    """

    @deprecated(target=TimerDecorator, deprecated_in="1.0", remove_in="2.0")
    def __init__(self, func: Callable) -> None:
        """Initialize deprecated timer."""
        void(func)


# ========== Testing Expiry Enforcement Examples ==========


@deprecated(target=None, deprecated_in="1.0")
def depr_func_no_remove_in(x: int) -> int:
    """Warning-only deprecation with no removal deadline.

    Examples:
        Function is deprecated but no specific removal version is set yet.
        This tests that expiry enforcement gracefully handles callables
        without a `remove_in` field.
    """
    return x


# ========== Class deprecation with custom DeprecationWarning stream ==========


class PastCls(NewCls):
    """Deprecated class forwarding to NewCls with DeprecationWarning."""

    @deprecated(target=NewCls, deprecated_in="0.2", remove_in="0.4", stream=_deprecation_warning)
    def __init__(self, c: int, d: str = "efg", **kwargs: Any) -> None:  # noqa: ANN401
        """Initialize PastCls."""
        super().__init__(c)


class PastClsMapped(NewCls):
    """Deprecated class forwarding to NewCls with argument renaming via args_mapping."""

    @deprecated(
        target=NewCls, deprecated_in="0.2", remove_in="0.4", args_mapping={"old_c": "c"}, stream=_deprecation_warning
    )
    def __init__(self, old_c: int, d: str = "efg", **kwargs: Any) -> None:  # noqa: ANN401
        """Initialize PastClsMapped."""
        super().__init__(old_c)


class ThisCls(NewCls):
    """Class with deprecated __init__ remapping argument via self-deprecation."""

    @deprecated(
        target=True, deprecated_in="0.3", remove_in="0.5", args_mapping={"c": "nc"}, stream=_deprecation_warning
    )
    def __init__(self, c: int = 3, nc: int = 5) -> None:
        """Initialize ThisCls."""
        super().__init__(c=nc)


# ========== Class method deprecation examples ==========


class ServiceCls:
    """Class with deprecated individual methods for integration testing.

    Demonstrates @deprecated on non-__init__ methods — warn-only, redirect,
    and argument renaming via args_mapping.

    Note on target syntax for class methods:
        Use ``target=method_name`` (bare name in the class body) to capture the
        unbound function at decoration time.  The target must be a method on the
        **same** class; cross-class method forwarding is rejected at decoration time
        because ``self`` would carry the wrong type.  To forward to a different class
        entirely, use ``target=NewClass`` (constructor forwarding via ``__init__``).
        String targets (``target="compute"``) are **not** supported.
    """

    def compute(self, x: int) -> int:
        """Current implementation."""
        return x * 2

    def compute_scaled(self, value: int, scale: int = 1) -> int:
        """Current implementation with renamed and extended signature."""
        return value * 2 * scale

    @deprecated(target=None, deprecated_in="1.0", remove_in="2.0")
    def old_warn_method(self, x: int) -> int:
        """Deprecated — warns only, body still executes.

        Examples:
            User is notified the method is going away but it keeps working
            (`target=None`). Body delegates to the current implementation.
        """
        return self.compute(x)

    @deprecated(target=compute, deprecated_in="1.0", remove_in="2.0")
    def old_redirect_method(self, x: int) -> int:
        """Deprecated — forwards to compute().

        Examples:
            User calls the old name; the decorator transparently remaps the
            call to `compute()` on the same object (`target=compute`).
        """
        return void(x)

    @deprecated(
        target=compute_scaled,
        deprecated_in="1.0",
        remove_in="2.0",
        args_mapping={"x": "value"},
    )
    def old_mapped_method(self, x: int) -> int:
        """Deprecated — args_mapping renames x->value when forwarding to compute_scaled().

        Examples:
            User calls `old_mapped_method(x=5)` and receives the result of
            `compute_scaled(value=5)` after the argument is renamed transparently.
        """
        return void(x)

    @deprecated(
        target=True,
        deprecated_in="1.0",
        remove_in="2.0",
        args_mapping={"old_x": "x"},
    )
    def self_renamed_method(self, old_x: int = 0, x: int = 0) -> int:
        """Deprecated argument renamed within the same method (target=True).

        Examples:
            User calls `self_renamed_method(old_x=5)` and the decorator
            transparently remaps `old_x` -> `x` before running the body.
        """
        return self.compute(x)


class CrossGuardSameClass:
    """Class used by cross-class guard tests for same-class method forwarding."""

    def new_method(self, x: int) -> int:
        """Current implementation on the same class."""
        return x * 2

    @deprecated(target=new_method, deprecated_in="1.0", remove_in="2.0")
    def old_method(self, x: int) -> int:
        """Deprecated method that forwards to `new_method`."""
        return void(x)


class CrossGuardModuleLevel:
    """Class used by cross-class guard tests for module-level function forwarding."""

    @deprecated(target=cross_guard_standalone_increment, deprecated_in="1.0", remove_in="2.0")
    def old_method(self, x: int) -> int:
        """Deprecated method that forwards to module-level function target."""
        return void(x)


class CrossGuardOldClass(CrossGuardClassTargetNew):
    """Class used by cross-class guard tests for constructor-to-constructor forwarding."""

    @deprecated(target=CrossGuardClassTargetNew, deprecated_in="1.0", remove_in="2.0")
    def __init__(self, x: int) -> None:
        """Deprecated constructor forwarding to `CrossGuardClassTargetNew.__init__`."""
        void(x)


# ========== Instance and class-level proxy deprecation examples ==========


# shared source payload for deprecated config dict fixtures
_DEPR_CONFIG_DICT = {"threshold": 0.5, "enabled": True}

# deprecated config dict for integration tests (name auto-inferred as "dict")
depr_config_dict = deprecated_instance(
    _DEPR_CONFIG_DICT.copy(),
    deprecated_in="1.0",
    remove_in="2.0",
    num_warns=-1,
)

# read-only deprecated config dict — rejects mutations
depr_config_dict_read_only = deprecated_instance(
    _DEPR_CONFIG_DICT.copy(),
    deprecated_in="1.0",
    remove_in="2.0",
    num_warns=-1,
    read_only=True,
)


@deprecated_class(target=TargetColorEnum, deprecated_in="1.0", remove_in="2.0", num_warns=-1)
class DeprecatedColorEnum(Enum):
    """Deprecated color enum forwarding to TargetColorEnum via deprecated_class.

    Example:
        A user calling DeprecatedColorEnum.RED receives TargetColorEnum.RED.
    """

    RED = 1
    BLUE = 2


@deprecated_class(target=NewDataClass, deprecated_in="1.0", remove_in="2.0", num_warns=-1)
@dataclass
class DeprecatedColorDataClass:
    """Deprecated dataclass forwarding to NewDataClass via deprecated_class.

    Example:
        A user instantiating DeprecatedColorDataClass(label="x") receives a NewDataClass instance.
    """

    label: str
    total: int = 0


@deprecated_class(deprecated_in="1.0", remove_in="2.0", num_warns=-1)
class WarnOnlyColorEnum(Enum):
    """Deprecated enum with no forwarding target — warns on access only.

    Example:
        A user accessing WarnOnlyColorEnum.A receives the original member with a warning.
    """

    A = "a"


@deprecated_class(
    target=TargetColorEnum,
    deprecated_in="1.0",
    remove_in="2.0",
    num_warns=-1,
    args_mapping={"val": "value"},
)
class MappedColorEnum(Enum):
    """Deprecated enum with args_mapping: remaps 'val' kwarg to 'value' when called.

    Example:
        A user calling MappedColorEnum(val=1) receives TargetColorEnum.RED.
    """

    RED = 1
    BLUE = 2


@deprecated_class(
    target=NewDataClass,
    deprecated_in="1.0",
    remove_in="2.0",
    num_warns=-1,
    args_mapping={"name": "label", "count": "total"},
)
@dataclass
class MappedDataClass:
    """Deprecated dataclass with args_mapping: remaps 'name'->'label' and 'count'->'total'.

    Example:
        A user calling MappedDataClass(name="x", count=3) receives NewDataClass(label="x", total=3).
    """

    label: str
    total: int = 0


@deprecated_class(
    target=NewDataClass,
    deprecated_in="1.0",
    remove_in="2.0",
    num_warns=-1,
    args_mapping={"legacy_flag": None, "name": "label"},
)
@dataclass
class MappedDropArgDataClass:
    """Deprecated dataclass with args_mapping: drops 'legacy_flag', remaps 'name'->'label'.

    Example:
        A user calling MappedDropArgDataClass(name="x", legacy_flag=True) receives NewDataClass(label="x").
    """

    label: str
    total: int = 0


# ========== Proxy chain fixtures for chain detection tests ==========


# ========== Proxy args_mapping behaviour fixtures ==========


# Proxy: NOTIFY + args_mapping (misconfig — UserWarning at decoration time)
with warnings.catch_warnings():
    warnings.simplefilter("always")
    ProxyNotifyWithArgsMapping = deprecated_class(
        deprecated_in="0.9",
        remove_in="1.0",
        target=TargetMode.NOTIFY,
        args_mapping={"old_key": "new_key"},
    )(SomeTargetClass)

# Proxy: ARGS_REMAP + no args_mapping (misconfig — UserWarning at decoration time)
with warnings.catch_warnings():
    warnings.simplefilter("always")
    ProxyArgsRemapNoMapping = deprecated_class(
        deprecated_in="0.9",
        remove_in="1.0",
        target=TargetMode.ARGS_REMAP,
    )(SomeTargetClass)

# Proxy: auto ARGS_REMAP via args_mapping only (no explicit target)
ProxyArgsRemapAuto = deprecated_class(
    deprecated_in="0.9",
    remove_in="1.0",
    args_mapping={"old_key": "new_key"},
)(SomeTargetClass)

# Proxy: callable target + args_mapping
ProxyCallableWithArgsMapping = deprecated_class(
    deprecated_in="0.9",
    remove_in="1.0",
    target=SomeTargetClass,
    args_mapping={"old_key": "new_key"},
)(SomeTargetClass)


@deprecated_class(target=DeprecatedColorEnum, deprecated_in="1.0", remove_in="2.0", num_warns=-1)
class ChainedProxyColorEnum(Enum):
    """Deprecated color enum whose target is itself a deprecated proxy (proxy→proxy chain).

    Example:
        This creates a two-hop chain: ChainedProxyColorEnum → DeprecatedColorEnum → TargetColorEnum.
        Used to test that validate_deprecation_chains detects proxy-to-proxy chains.
    """

    RED = 1
    BLUE = 2


@deprecated(target=DeprecatedColorEnum, deprecated_in="1.0", remove_in="2.0")
def depr_func_targeting_proxy(value: int) -> Any:  # noqa: ANN401
    """Deprecated function whose target is a deprecated proxy (function→proxy chain).

    Examples:
        This creates a cross-kind chain: a @deprecated function forwards to a _DeprecatedProxy.
        Used to test that validate_deprecation_chains detects function-to-proxy chains.
    """
    void(value)
