"""Collection of deprecated functions for testing purposes.

This module retains shared configuration and the core, audit and documentation fixtures.
Additional wrappers live in the sibling forwarding and regressions collection modules.

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

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from enum import Enum
from functools import partial
from typing import Any, Callable
from warnings import catch_warnings, simplefilter, warn

from sklearn.metrics import accuracy_score

from deprecate import (
    TargetMode,
    deprecated,
    deprecated_callable,
    deprecated_class,
    deprecated_instance,
    deprecated_module,
    void,
)
from deprecate.proxy import _DeprecatedProxy, _ProxySpec
from tests.collections.api_current.core import (
    AutoExpandDC,
    CallableAdder,
    ColorEnum,
    CrossGuardClassTargetNew,
    DerivedPositionalOnlyTarget,
    GenericBox,
    ImmutablePositionalOnlyTarget,
    MixedPositionalOnlyTarget,
    MutableAttrsList,
    NewCls,
    NewDataClass,
    NewEnum,
    NewIntEnum,
    Palette,
    PositionalOnlyTarget,
    RequiredPositionalOnlyTarget,
    SideEffectBox,
    TagList,
    TimerDecorator,
    _InnerOrderPropTarget,
    add_values,
    base_pow_args,
    base_sum_kwargs,
    compute_power,
    cross_guard_standalone_increment,
    double_value,
    identity_value,
    increment_value,
    positional_only_target,
    power_with_new_coef,
    return_b,
    return_z,
    timing_wrapper,
    tracked_identity,
)
from tests.collections.api_current.forwarding import SubclassableBase

_deprecation_warning = partial(warn, category=DeprecationWarning)

_SHORT_MSG_FUNC = "`%(source_name)s` >> `%(target_name)s` in v%(deprecated_in)s rm v%(remove_in)s."
_SHORT_MSG_ARGS = "Depr: v%(deprecated_in)s rm v%(remove_in)s for args: %(argument_map)s."

# Reusable deprecation-version kwarg groups.  Each constant captures a (deprecated_in, remove_in
# [, num_warns]) combination that is shared by 3+ inline @deprecated / @deprecated_class /
# deprecated_instance / _DeprecatedProxy call sites across the sibling modules.  Splat with **NAME at the call
# site to keep the shared values in one place.
_DEPRS_CASE_PROXY_LEGACY_ARGS: dict[str, Any] = {"deprecated_in": "0.1", "remove_in": "0.2", "num_warns": -1}
_DEPRS_CASE_CLS_FWD_ARGS: dict[str, Any] = {"deprecated_in": "0.2", "remove_in": "0.4"}
_DEPRS_CASE_TGT_MODE_ARGS: dict[str, Any] = {"deprecated_in": "1.2", "remove_in": "2.0"}
_DEPRS_CASE_TGT_MODE_INF_ARGS: dict[str, Any] = {"deprecated_in": "1.2", "remove_in": "2.0", "num_warns": -1}
_DEPRS_CASE_STD_ARGS: dict[str, Any] = {"deprecated_in": "1.0", "remove_in": "2.0"}
_DEPRS_CASE_STD_INF_ARGS: dict[str, Any] = {"deprecated_in": "1.0", "remove_in": "2.0", "num_warns": -1}
_DEPRS_CASE_FATAL_ARGS: dict[str, Any] = {"deprecated_in": "1.0", "remove_in": "2.0", "as_exception": True}
_DEPRS_CASE_OLD_ARGS: dict[str, Any] = {"deprecated_in": "0.1", "remove_in": "0.5"}
_DEPRS_CASE_OUTER_ARGS: dict[str, Any] = {"deprecated_in": "2.0", "remove_in": "3.0"}

# Shared deprecated_class() instances — used as both decorator and factory form in
# form-equivalence and sibling fixture groups; defined here so all usages reference the
# same config object.
_class_deprecation_enum = deprecated_class(target=NewEnum, deprecated_in="0.5", remove_in="1.0", num_warns=1)
_class_deprecation_dataclass = deprecated_class(target=NewDataClass, deprecated_in="0.5", remove_in="1.0", num_warns=1)
# H5 fixture: callable target + warn-only attrs_mapping — shared config for both application forms.
_class_deprecation_notify_only_callable_target = deprecated_class(
    target=Palette, attrs_mapping={"size": None}, **_DEPRS_CASE_STD_INF_ARGS
)


@deprecated_class(**_DEPRS_CASE_PROXY_LEGACY_ARGS)
class DeprecatedEnum(Enum):
    """Deprecated enum for regression testing.

    Example:
        A user can still resolve DeprecatedEnum("alpha") via value lookup (DeprecatedEnum.ALPHA).

    """

    ALPHA = "alpha"
    BETA = "beta"


@deprecated_class(**_DEPRS_CASE_PROXY_LEGACY_ARGS)
class DeprecatedIntEnum(Enum):
    """Deprecated enum with integer values for regression testing.

    Example:
        A user can still resolve a deprecated enum by integer value such as 1.

    """

    ONE = 1
    TWO = 2


@deprecated_class(target=NewEnum, **_DEPRS_CASE_PROXY_LEGACY_ARGS)
class RedirectedEnum(Enum):
    """Deprecated enum that forwards to a new enum.

    Example:
        A user can still call RedirectedEnum("alpha") via value lookup and receive NewEnum.ALPHA.

    """

    ALPHA = "alpha"
    BETA = "beta"


@deprecated_class(target=NewEnum, **_DEPRS_CASE_PROXY_LEGACY_ARGS, args_mapping={"old_value": "value"})
class MappedEnum(Enum):
    """Deprecated enum with old_value->value mapping, where member names differ but values match NewEnum.

    Example:
        A user can pass old_value="alpha" and resolve NewEnum.ALPHA.

    """

    OLD_ALPHA = "alpha"
    OLD_BETA = "beta"


@deprecated_class(target=NewIntEnum, **_DEPRS_CASE_PROXY_LEGACY_ARGS, args_mapping={"old_value": "value"})
class MappedIntEnum(Enum):
    """Deprecated int enum mapping old_value->value where member names differ from NewIntEnum.

    Example:
        A user can pass old_value=1 and resolve NewIntEnum.ALPHA.

    """

    ONE = 1
    TWO = 2


@deprecated_class(target=NewEnum, **_DEPRS_CASE_PROXY_LEGACY_ARGS, args_mapping={"old_value": "value"})
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
    target=_SelfMappedEnum, **_DEPRS_CASE_PROXY_LEGACY_ARGS, args_mapping={"old_value": "value"}
)(_SelfMappedEnum)


# Form-equivalence pairs — shared instance ensures decorator and wrapper form use identical config
class _OriginalEnum(Enum):
    """Original enum class that gets wrapped by deprecated_class in wrapper form."""

    ALPHA = "alpha"
    BETA = "beta"


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


@_class_deprecation_dataclass
@dataclass
class DecoratedDataClass:
    """Decorator-form dataclass with same config as WrappedDataClass, for form-equivalence tests."""

    label: str
    total: int = 0


WrappedDataClass = _class_deprecation_dataclass(_OriginalDataClass)


@deprecated_class(**_DEPRS_CASE_PROXY_LEGACY_ARGS)
@dataclass
class DeprecatedDataClass:
    """Deprecated dataclass for regression testing.

    Example:
        A user can still instantiate DeprecatedDataClass(label="alpha", total=2).

    """

    label: str
    total: int = 0


@deprecated_class(target=NewDataClass, **_DEPRS_CASE_PROXY_LEGACY_ARGS)
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


_deprecation_sum_warn_only = deprecated(target=TargetMode.NOTIFY, deprecated_in="0.2", remove_in="0.3")


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


_deprecation_sum = deprecated(target=base_sum_kwargs, **_DEPRS_CASE_OLD_ARGS)


@_deprecation_sum
def decorated_sum(a: int, b: int = 5) -> int:
    """Basic call forwarding to a replacement function.

    Examples:
        User calls an old function that has been replaced. Warns and
        transparently forwards the call to `base_sum_kwargs`.

    """
    return void(a, b)


wrapped_sum = _deprecation_sum(original_sum)


@deprecated(target=NewCls, **_DEPRS_CASE_CLS_FWD_ARGS)
def depr_make_new_cls(c: float, d: str = "abc", **kwargs: Any) -> NewCls:  # noqa: ANN401
    """Forward a deprecated factory function to a class constructor.

    Examples:
        Users call the old function but receive a ``NewCls`` instance.

    """
    return void(c, d, kwargs)


@deprecated(target=NewCls, **_DEPRS_CASE_CLS_FWD_ARGS, args_mapping={"old_c": "c"})
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
    **_DEPRS_CASE_OLD_ARGS,
    message_template="v%(deprecated_in)s: `%(source_name)s` was deprecated, use `%(target_name)s`",
)


@_deprecation_sum_msg
def decorated_sum_msg(a: int, b: int = 5) -> int:
    """Custom warning message template.

    Examples:
        Library wants to control exactly what the user sees instead of the default
        template. Uses `message_template` with format specifiers.

    """
    return void(a, b)


wrapped_sum_msg = _deprecation_sum_msg(original_sum)


@deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_TGT_MODE_INF_ARGS)
def depr_target_mode_whole_warns_on_every_call(x: int) -> int:
    """TargetMode.NOTIFY wrapper used by integration tests."""
    return double_value(x)


@deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_TGT_MODE_ARGS)
def depr_target_mode_whole_executes_original_body(x: int) -> int:
    """TargetMode.NOTIFY wrapper that records body execution."""
    return tracked_identity(x)


@deprecated(target=TargetMode.ARGS_REMAP, **_DEPRS_CASE_TGT_MODE_ARGS, args_mapping={"old_x": "x"})
def depr_target_mode_args_only_warns_when_old_arg_passed(x: int = 0, old_x: int = 0) -> int:
    """TargetMode.ARGS_REMAP wrapper used when callers pass the old name."""
    return increment_value(x)


@deprecated(target=TargetMode.ARGS_REMAP, **_DEPRS_CASE_TGT_MODE_ARGS, args_mapping={"old_x": "x"})
def depr_target_mode_args_only_silent_when_new_arg_passed(x: int = 0, old_x: int = 0) -> int:
    """TargetMode.ARGS_REMAP wrapper used when callers already use the new name."""
    return increment_value(x)


@deprecated(target=TargetMode.ARGS_REMAP, **_DEPRS_CASE_TGT_MODE_ARGS, args_mapping={"coef": "new_coef"})
def depr_target_mode_args_only_remaps_kwargs(base: float, new_coef: float = 1.0, coef: float = 1.0) -> float:
    """TargetMode.ARGS_REMAP wrapper that remaps kwargs before executing."""
    return power_with_new_coef(base, new_coef)


@deprecated(
    target=TargetMode.ARGS_REMAP, **_DEPRS_CASE_TGT_MODE_ARGS, args_mapping={"old_x": "x"}, args_extra={"y": 10}
)
def depr_target_mode_args_only_with_args_extra_injects_kwargs(x: int = 0, y: int = 0, old_x: int = 0) -> int:
    """TargetMode.ARGS_REMAP wrapper that injects extra keyword arguments."""
    return add_values(x, y)


def make_target_mode_args_only_without_args_mapping_warns() -> Callable[[int], int]:
    """Build a TargetMode.ARGS_REMAP wrapper that warns about missing args_mapping."""

    @deprecated(target=TargetMode.ARGS_REMAP, **_DEPRS_CASE_TGT_MODE_ARGS)
    def noop(x: int) -> int:
        return identity_value(x)

    return noop


def make_target_mode_whole_with_args_mapping_warns() -> Callable[[int], int]:
    """Build a TargetMode.NOTIFY wrapper that warns about ignored args_mapping."""

    @deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_TGT_MODE_ARGS, args_mapping={"a": "b"})
    def fn(b: int) -> int:
        return return_b(b)

    return fn


def make_target_mode_whole_with_args_extra_warns() -> Callable[[int], int]:
    """Build a TargetMode.NOTIFY wrapper that warns about ignored args_extra."""

    @deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_TGT_MODE_ARGS, args_extra={"z": 1})
    def fn(z: int = 0) -> int:
        return return_z(z)

    return fn


@deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_INF_ARGS)
def depr_class_whole_mode_warns_on_call(x: int) -> int:
    """TargetMode.NOTIFY wrapper used by class integration tests."""
    return double_value(x)


@deprecated(target=TargetMode.ARGS_REMAP, **_DEPRS_CASE_STD_INF_ARGS, args_mapping={"old_x": "x"})
def depr_class_args_only_mode_warns_on_deprecated_arg(x: int = 0, old_x: int = 0) -> int:
    """TargetMode.ARGS_REMAP wrapper used by class integration tests."""
    return double_value(x)


@deprecated(target=base_pow_args, deprecated_in="1.0", remove_in="1.3", message_template=_SHORT_MSG_FUNC)
def depr_pow_args(a: float, b: float) -> float:
    """Positional argument forwarding with a compact warning template.

    Examples:
        Both old and new functions take positional args; the library wants
        a short, machine-readable warning format.

    """
    return void(a, b)


@deprecated(target=base_pow_args, **_DEPRS_CASE_OLD_ARGS)
def depr_pow_mix(a: int, b: float = 4) -> float:
    """Forwarding with mixed parameter types between source and target.

    Examples:
        Old function has `(int, float)` but target expects `(float, int)`. Tests
        that the forwarding handles type differences gracefully.

    """
    return void(a, b)


@deprecated(target=base_pow_args, **_DEPRS_CASE_OLD_ARGS)
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


_deprecation_pow_self = deprecated(
    target=TargetMode.ARGS_REMAP, **_DEPRS_CASE_OLD_ARGS, args_mapping={"coef": "new_coef"}
)


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
    target=TargetMode.ARGS_REMAP,
    message_template="The `%(source_name)s` uses depr. args: %(argument_map)s.",
    args_mapping={"c1": "nc1", "c2": "nc2"},
)
def depr_pow_self_double(base: float, c1: float = 0, c2: float = 0, nc1: float = 1, nc2: float = 2) -> float:
    """Self-deprecation: renaming multiple parameters at once.

    Examples:
        Both `c1` -> `nc1` and `c2` -> `nc2` are remapped in a single decorator
        with a custom warning template.

    """
    return base ** (c1 + c2 + nc1 + nc2)


@deprecated(
    target=TargetMode.ARGS_REMAP, **_DEPRS_CASE_STD_ARGS, num_warns=1, args_mapping={"old_x": "x", "old_y": "y"}
)
def depr_two_renames_warn_once(x: int = 0, y: int = 0, old_x: int = 0, old_y: int = 0) -> int:
    """Self-deprecation renaming two parameters, each with its own one-warning budget.

    Examples:
        A caller that already received the `old_x` notice and later passes `old_x` and `old_y` together is
        told only about `old_y`.

    """
    return add_values(x, y)


@deprecated(TargetMode.ARGS_REMAP, "0.3", "0.6", args_mapping={"c1": "nc1"}, message_template=_SHORT_MSG_ARGS)
@deprecated(TargetMode.ARGS_REMAP, "0.4", "0.7", args_mapping={"nc1": "nc2"}, message_template=_SHORT_MSG_ARGS)
def depr_pow_self_twice(base: float, c1: float = 0, nc1: float = 0, nc2: float = 2) -> float:
    """Chained deprecation: multi-step parameter migration across versions.

    Examples:
        V0.3 renamed `c1` to `nc1`, then v0.4 renamed `nc1` to `nc2`. Stacked
        decorators handle users calling with any of the historical names.

    """
    return base ** (c1 + nc1 + nc2)


def make_depr_compute_power_stacked() -> Callable:
    """Return a fresh ARGS_REMAP-outer + NOTIFY-inner stacked fixture.

    Each call produces a new wrapper pair with an independent ``_WrapperState``,
    preventing ``num_warns`` counter exhaustion across tests.

    Examples:
        Calling with the old arg name raises two warnings.

        >>> from tests._helpers import captured_warnings
        >>> fn = make_depr_compute_power_stacked()
        >>> with captured_warnings() as w:
        ...     result = fn(2.0, factor=3.0)
        >>> result
        8.0
        >>> len(w)
        2
    """
    inner = deprecated(TargetMode.NOTIFY, "2.0", "3.0")(compute_power)
    return deprecated(TargetMode.ARGS_REMAP, "1.0", "2.0", args_mapping={"factor": "scale"})(inner)


def make_depr_notify_callable_stacked() -> Callable:
    """Return a fresh NOTIFY-outer + callable-target-inner stacked fixture.

    Each call produces a new wrapper pair with an independent ``_WrapperState``,
    preventing ``num_warns`` counter exhaustion across tests.

    The outer ``@deprecated(TargetMode.NOTIFY, ...)`` warns that the whole function is going
    away; the inner ``@deprecated(target=compute_power, ...)`` warns and forwards to
    ``compute_power``.  Both ``FutureWarning`` instances fire on every call until counters
    are exhausted.

    Examples:
        Calling the stacked wrapper emits two FutureWarnings and returns the computed value.

        >>> from tests._helpers import captured_warnings
        >>> fn = make_depr_notify_callable_stacked()
        >>> with captured_warnings() as w:
        ...     result = fn(2.0, scale=3.0)
        >>> result
        8.0
        >>> len(w)
        2
    """
    inner = deprecated(target=compute_power, **_DEPRS_CASE_STD_ARGS)(compute_power)
    return deprecated(TargetMode.NOTIFY, **_DEPRS_CASE_OUTER_ARGS)(inner)


def make_depr_args_remap_notify_with_extra() -> Callable:
    """Return a fresh ARGS_REMAP-outer + NOTIFY-inner fixture with ``args_extra`` injection.

    The outer ``ARGS_REMAP`` layer maps ``factor`` → ``scale`` and injects ``base=2.0`` via
    ``args_extra``.  The inner ``NOTIFY`` layer emits its own ``FutureWarning`` and runs the
    ``compute_power`` body.  Together they verify that ``args_extra`` set on the outer ARGS_REMAP
    layer flows correctly through the inner NOTIFY layer to the final function call.

    Examples:
        Calling with the deprecated arg name fires two warnings and returns the injected-base result.

        >>> from tests._helpers import captured_warnings
        >>> fn = make_depr_args_remap_notify_with_extra()
        >>> with captured_warnings() as w:
        ...     result = fn(factor=3.0)
        >>> result
        8.0
        >>> len(w)
        2
    """
    inner = deprecated(TargetMode.NOTIFY, **_DEPRS_CASE_OUTER_ARGS)(compute_power)
    return deprecated(
        TargetMode.ARGS_REMAP,
        **_DEPRS_CASE_STD_ARGS,
        args_mapping={"factor": "scale"},
        args_extra={"base": 2.0},
    )(inner)


def make_async_stacked_notify() -> Callable:
    """Return a fresh NOTIFY-outer + NOTIFY-inner stacked async wrapper.

    Each call produces a new wrapper pair with an independent ``_WrapperState``,
    preventing ``num_warns`` counter exhaustion across tests.  The inner wrapper
    is itself a ``@deprecated`` wrapper (``_source_is_stacked=True`` on the outer),
    exercising the stacked-source code path in the async branch of ``packing()``.

    Examples:
        Building the stack flags the duplicate NOTIFY layers; awaiting the result emits two FutureWarnings.

        >>> import asyncio
        >>> from tests._helpers import captured_warnings
        >>> with captured_warnings() as build:
        ...     fn = make_async_stacked_notify()
        >>> "duplicate @deprecated(NOTIFY) layers" in str(build[0].message)
        True
        >>> with captured_warnings() as w:
        ...     _ = asyncio.run(fn(x=1))
        >>> len(w)
        2

    """
    inner = deprecated(TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)(async_source_notify)
    return deprecated(TargetMode.NOTIFY, **_DEPRS_CASE_OUTER_ARGS)(inner)


@deprecated_callable(
    TargetMode.ARGS_REMAP, "0.3", "0.4", args_mapping={"c1": "nc1"}, message_template=_SHORT_MSG_ARGS, skip_if=True
)
@deprecated_callable(
    TargetMode.ARGS_REMAP, "0.1", "0.2", args_mapping={"c1": "nc1"}, message_template=_SHORT_MSG_ARGS, skip_if=False
)
def depr_pow_skip_if_true_false(base: float, c1: float = 1, nc1: float = 1) -> float:
    """Conditional skip: outer decorator skipped, inner fires.

    Examples:
        One migration step is conditionally disabled (e.g., feature flag)
        while another is active. Tests `skip_if=True` on outer, `skip_if=False` on inner.

    """
    return base ** (c1 - nc1)


@deprecated_callable(
    TargetMode.ARGS_REMAP, "0.1", "0.2", args_mapping={"c1": "nc1"}, message_template=_SHORT_MSG_ARGS, skip_if=False
)
@deprecated_callable(
    TargetMode.ARGS_REMAP, "0.3", "0.4", args_mapping={"c1": "nc1"}, message_template=_SHORT_MSG_ARGS, skip_if=True
)
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


_deprecation_pow_skip_if_true = deprecated_callable(
    TargetMode.ARGS_REMAP, "0.1", "0.2", args_mapping={"c1": "nc1"}, message_template=_SHORT_MSG_ARGS, skip_if=True
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


_deprecation_pow_skip_if_func = deprecated_callable(
    TargetMode.ARGS_REMAP,
    "0.1",
    "0.2",
    args_mapping={"c1": "nc1"},
    message_template=_SHORT_MSG_ARGS,
    skip_if=lambda: True,
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


@deprecated_callable(
    TargetMode.ARGS_REMAP,
    "0.1",
    "0.3",
    args_mapping={"c1": "nc1"},
    message_template=_SHORT_MSG_ARGS,
    skip_if=lambda: 42,
)
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
@deprecated(target=timing_wrapper, **_DEPRS_CASE_STD_ARGS)
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

    @deprecated(target=TimerDecorator, **_DEPRS_CASE_STD_ARGS)
    def __init__(self, func: Callable) -> None:
        """Initialize deprecated timer."""
        void(func)


# ========== Testing Expiry Enforcement Examples ==========


@deprecated(target=TargetMode.NOTIFY, deprecated_in="2.0", remove_in="2.0")
def depr_func_same_version(x: int) -> int:
    """Deprecation with identical deprecated_in and remove_in — exercises D/R matrix marker.

    Examples:
        Function is deprecated and scheduled for removal in the same version.

    """
    return x


@deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0")
def depr_func_no_remove_in(x: int) -> int:
    """Warning-only deprecation with no removal deadline.

    Examples:
        Function is deprecated but no specific removal version is set yet.
        This tests that expiry enforcement gracefully handles callables
        without a `remove_in` field.

    """
    return x


# ========== Audit scan fixtures built per call ==========
# Factories rather than module-level fixtures: each test binds the result into its own ``types.ModuleType`` and may
# rewrite ``__module__`` on it, so a shared object would carry that edit into later tests, and the audit scans of this
# module must not count these wrappers.


def make_self_referential_class() -> type[Any]:
    """Build a fresh class with one deprecated method and a ``cls_ref`` class attribute pointing back at the class.

    Examples:
        A registry class stores a reference to itself for self-registration. Member scanning must visit the class
        once and report its one deprecated method instead of recursing through the self-reference without end.

    """

    class SelfRef:
        """A class that holds a reference to itself as a class attribute."""

        @deprecated(target=identity_value, **_DEPRS_CASE_STD_ARGS)
        def old_compute(self, x: int) -> None:
            """Deprecated redirect."""

    SelfRef.cls_ref = SelfRef  # type: ignore[attr-defined]
    return SelfRef


def make_installed_version_notify_wrapper() -> Callable[..., Any]:
    """Build a fresh warn-only wrapper for a deprecation report whose current version comes from the installed package.

    Examples:
        A release job renders the deprecation table with ``current_version=None`` and relies on the installed
        distribution's version to decide each row's status.

    """

    def _old_fn() -> None:
        """Old."""

    return deprecated(target=TargetMode.NOTIFY, deprecated_in="0.1", remove_in="0.2")(_old_fn)


# ========== Class deprecation with custom DeprecationWarning stream ==========


class PastCls(NewCls):
    """Deprecated class forwarding to NewCls with DeprecationWarning."""

    @deprecated(target=NewCls, **_DEPRS_CASE_CLS_FWD_ARGS, stream=_deprecation_warning)
    def __init__(self, c: int, d: str = "efg", **kwargs: Any) -> None:  # noqa: ANN401
        """Initialize PastCls."""
        super().__init__(c)


class PastClsMapped(NewCls):
    """Deprecated class forwarding to NewCls with argument renaming via args_mapping."""

    @deprecated(target=NewCls, **_DEPRS_CASE_CLS_FWD_ARGS, args_mapping={"old_c": "c"}, stream=_deprecation_warning)
    def __init__(self, old_c: int, d: str = "efg", **kwargs: Any) -> None:  # noqa: ANN401
        """Initialize PastClsMapped."""
        super().__init__(old_c)


class ThisCls(NewCls):
    """Class with deprecated __init__ remapping argument via self-deprecation."""

    @deprecated(
        target=TargetMode.ARGS_REMAP,
        deprecated_in="0.3",
        remove_in="0.5",
        args_mapping={"c": "nc"},
        stream=_deprecation_warning,
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

    @classmethod
    def class_compute(cls, x: int) -> int:
        """Current classmethod implementation."""
        return x * 2

    @staticmethod
    def static_compute(x: int) -> int:
        """Current staticmethod implementation."""
        return x * 2

    @deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)
    @classmethod
    def old_class_method(cls, x: int) -> int:
        """Deprecated classmethod — warns only, body still executes.

        Examples:
            Deprecated classmethod that forwards to class_compute after warning.

        """
        return cls.class_compute(x)

    @deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_x": "x"}, **_DEPRS_CASE_STD_ARGS)
    @classmethod
    def old_class_method_args(cls, old_x: int = 0, x: int = 0) -> int:
        """Deprecated classmethod with argument rename.

        Examples:
            Deprecated classmethod remapping old_x to x before body executes.

        """
        return cls.class_compute(x)

    @deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)
    @staticmethod
    def old_static_method(x: int) -> int:
        """Deprecated staticmethod — warns only, body still executes.

        Examples:
            Deprecated staticmethod that warns and returns result.

        """
        return x * 2

    @deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_x": "x"}, **_DEPRS_CASE_STD_ARGS)
    @staticmethod
    def old_static_method_args(old_x: int = 0, x: int = 0) -> int:
        """Deprecated staticmethod with argument rename.

        Examples:
            Deprecated staticmethod remapping old_x to x before body executes.

        """
        return x * 2

    @staticmethod
    @deprecated(target=static_compute, **_DEPRS_CASE_STD_ARGS)
    def old_static_redirect(x: int) -> int:
        """Deprecated staticmethod — forwards to static_compute via descriptor target.

        ``target=static_compute`` passes the raw ``staticmethod`` descriptor; ``_normalize_target``
        unwraps it to ``static_compute.__func__`` so call forwarding works without the caller
        needing to write ``target=static_compute.__func__`` explicitly.

        Examples:
            ``old_static_redirect(5)`` transparently calls ``static_compute(5)`` after warning.

        """
        return void(x)

    @staticmethod
    @deprecated(
        target=static_compute,
        args_mapping={"old_x": "x"},
        **_DEPRS_CASE_STD_ARGS,
    )
    def old_static_redirect_mapped(old_x: int = 0, x: int = 0) -> int:
        """Deprecated staticmethod forwarding to static_compute with argument rename.

        Combines descriptor-target unwrapping with ``args_mapping``: ``old_x`` is remapped to ``x``
        before the call is forwarded to ``static_compute.__func__``.

        Examples:
            ``old_static_redirect_mapped(old_x=5)`` remaps to ``x=5`` then calls ``static_compute(x=5)``.

        """
        return void(old_x, x)

    @classmethod
    @deprecated(target=class_compute, **_DEPRS_CASE_STD_ARGS)
    def old_class_redirect(cls, x: int) -> int:
        """Deprecated classmethod — forwards to class_compute via descriptor target.

        ``target=class_compute`` passes the raw ``classmethod`` descriptor; ``_normalize_target``
        unwraps it to ``class_compute.__func__`` so call forwarding works.  The symmetric case
        (both source and target are classmethods) is valid: ``cls`` is supplied by the caller.

        Examples:
            ``old_class_redirect(5)`` transparently calls ``class_compute(5)`` after warning.

        """
        return void(x)

    @classmethod
    @deprecated(
        target=class_compute,
        args_mapping={"old_x": "x"},
        **_DEPRS_CASE_STD_ARGS,
    )
    def old_class_redirect_mapped(cls, old_x: int = 0, x: int = 0) -> int:
        """Deprecated classmethod forwarding to class_compute with argument rename.

        Combines descriptor-target unwrapping with ``args_mapping``: ``old_x`` is remapped to ``x``
        before the call is forwarded to ``class_compute.__func__``.

        Examples:
            ``old_class_redirect_mapped(old_x=5)`` remaps to ``x=5`` then calls ``class_compute(x=5)``.

        """
        return void(old_x, x)

    @deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)
    def old_warn_method(self, x: int) -> int:
        """Deprecated — warns only, body still executes.

        Examples:
            User is notified the method is going away but it keeps working
            (`target=None`). Body delegates to the current implementation.

        """
        return self.compute(x)

    @deprecated(target=compute, **_DEPRS_CASE_STD_ARGS)
    def old_redirect_method(self, x: int) -> int:
        """Deprecated — forwards to compute().

        Examples:
            User calls the old name; the decorator transparently remaps the
            call to `compute()` on the same object (`target=compute`).

        """
        return void(x)

    @deprecated(target=compute_scaled, **_DEPRS_CASE_STD_ARGS, args_mapping={"x": "value"})
    def old_mapped_method(self, x: int) -> int:
        """Deprecated — args_mapping renames x->value when forwarding to compute_scaled().

        Examples:
            User calls `old_mapped_method(x=5)` and receives the result of
            `compute_scaled(value=5)` after the argument is renamed transparently.

        """
        return void(x)

    @deprecated(target=TargetMode.ARGS_REMAP, **_DEPRS_CASE_STD_ARGS, args_mapping={"old_x": "x"})
    def self_renamed_method(self, old_x: int = 0, x: int = 0) -> int:
        """Deprecated argument renamed within the same method (TargetMode.ARGS_REMAP).

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

    @deprecated(target=new_method, **_DEPRS_CASE_STD_ARGS)
    def old_method(self, x: int) -> int:
        """Deprecated method that forwards to `new_method`."""
        return void(x)


class CrossGuardModuleLevel:
    """Class used by cross-class guard tests for module-level function forwarding."""

    @deprecated(target=cross_guard_standalone_increment, **_DEPRS_CASE_STD_ARGS)
    def old_method(self, x: int) -> int:
        """Deprecated method that forwards to module-level function target."""
        return void(x)


class CrossGuardOldClass(CrossGuardClassTargetNew):
    """Class used by cross-class guard tests for constructor-to-constructor forwarding."""

    @deprecated(target=CrossGuardClassTargetNew, **_DEPRS_CASE_STD_ARGS)
    def __init__(self, x: int) -> None:
        """Deprecated constructor forwarding to `CrossGuardClassTargetNew.__init__`."""
        void(x)


class StaticGuardNewClass:
    """Target class for the staticmethod cross-class guard regression."""

    @staticmethod
    def compute(x: int) -> int:
        """Current staticmethod implementation on the replacement class."""
        return x * 3


class StaticGuardOldClass:
    """Source class whose deprecated staticmethod forwards to a staticmethod on a *different* class.

    Regression test: the cross-class guard used to raise ``TypeError`` at decoration time here because it
    saw two different owning classes in the qualnames.  A staticmethod receives no ``self``, so cross-class
    forwarding is safe; the guard is now skipped for staticmethod sources and this module imports cleanly.
    """

    @deprecated(target=StaticGuardNewClass.compute, **_DEPRS_CASE_STD_ARGS)
    @staticmethod
    def compute(x: int) -> int:
        """Deprecated staticmethod forwarding cross-class to ``StaticGuardNewClass.compute``."""
        return void(x)


# ========== Instance and class-level proxy deprecation examples ==========


# shared source payload for deprecated config dict fixtures
_DEPR_CONFIG_DICT = {"threshold": 0.5, "enabled": True}

# deprecated config dict for integration tests (name auto-inferred as "dict")
depr_config_dict = deprecated_instance(_DEPR_CONFIG_DICT.copy(), **_DEPRS_CASE_STD_INF_ARGS)

# read-only deprecated config dict — rejects mutations
depr_config_dict_read_only = deprecated_instance(_DEPR_CONFIG_DICT.copy(), **_DEPRS_CASE_STD_INF_ARGS, read_only=True)

depr_read_only_attrs_list = _DeprecatedProxy(
    obj=MutableAttrsList(),
    spec=_ProxySpec(
        name="legacy_mutable_attrs_list",
        **_DEPRS_CASE_STD_INF_ARGS,
        stream=None,
        read_only=True,
        attrs_mapping={"push": "append"},
    ),
)


@deprecated_class(target=ColorEnum, **_DEPRS_CASE_STD_INF_ARGS)
class DeprecatedColorEnum(Enum):
    """Deprecated color enum forwarding to ColorEnum via deprecated_class.

    Example:
        A user calling DeprecatedColorEnum.RED receives ColorEnum.RED.

    """

    RED = 1
    BLUE = 2


# Protocol-surface aliases: a class proxy carries exactly the protocols its wrapped class (and target) supports.
DeprecatedGenericBox = deprecated_class(stream=None, **_DEPRS_CASE_STD_ARGS)(GenericBox)
DeprecatedTagList = deprecated_class(stream=None, **_DEPRS_CASE_STD_ARGS)(TagList)


@deprecated_class(target=ColorEnum, stream=None, **_DEPRS_CASE_STD_ARGS)
class LegacyColorTable:
    """Plain lookup class replaced by ``ColorEnum``.

    Example:
        Code iterating ``LegacyColorTable`` or calling ``len()`` on it reaches the Enum it now serves.

    """


# source payload behind `depr_ast_breadcrumb_dict` — exported so tests can assert `__wrapped__` identity
ast_breadcrumb_source_dict = {"threshold": 0.5}

# instance proxy over a plain `dict`: a C-level type with no introspectable signature, so the proxy's
# `__signature__` breadcrumb falls back to None while `__wrapped__` still points at the source
depr_ast_breadcrumb_dict = deprecated_instance(ast_breadcrumb_source_dict, **_DEPRS_CASE_STD_INF_ARGS, stream=None)


class AstFunctionalColorEnum(Enum):
    """Source enum wrapped through the functional form of `deprecated_class` rather than by decoration."""

    RED = 1


# functional/assignment form — the same wrapping the decorator form above performs, spelled as an
# assignment. Both shapes build a `_DeprecatedProxy` at runtime. With a class `target` the alias is typed as that
# target (`type[ColorEnum]`, not the private source it wraps) because it resolves `isinstance`/construction against
# the target; `tests/unittests/test_proxy.py` pins that statically and exercises the runtime forwarding here.
# The version kwargs are spelled out rather than unpacked from `_DEPRS_CASE_STD_INF_ARGS`: a `dict[str, Any]` splat
# could carry `target`, so type checkers cannot pick an overload and fall back to `Any` (also pinned there).
DeprecatedColorEnumFunctional = deprecated_class(
    target=ColorEnum, deprecated_in="1.0", remove_in="2.0", num_warns=-1, stream=None
)(AstFunctionalColorEnum)

# same functional form with no class `target` — the proxy still forwards, and the alias is typed `type[Palette]`,
# so `isinstance`/`issubclass` checks against it type-check
DeprecatedPaletteFunctionalFallback = deprecated_class(deprecated_in="1.0", remove_in="2.0", num_warns=-1, stream=None)(
    Palette
)


@deprecated_class(target=NewDataClass, **_DEPRS_CASE_STD_INF_ARGS)
@dataclass
class DeprecatedColorDataClass:
    """Deprecated dataclass forwarding to NewDataClass via deprecated_class.

    Example:
        A user instantiating DeprecatedColorDataClass(label="x") receives a NewDataClass instance.

    """

    label: str
    total: int = 0


@deprecated_class(**_DEPRS_CASE_STD_INF_ARGS)
class WarnOnlyColorEnum(Enum):
    """Deprecated enum with no forwarding target — warns on access only.

    Example:
        A user accessing WarnOnlyColorEnum.A receives the original member with a warning.

    """

    A = "a"


@deprecated_class(target=ColorEnum, **_DEPRS_CASE_STD_INF_ARGS, args_mapping={"val": "value"})
class MappedColorEnum(Enum):
    """Deprecated enum with args_mapping: remaps 'val' kwarg to 'value' when called.

    Example:
        A user calling MappedColorEnum(val=1) receives ColorEnum.RED.

    """

    RED = 1
    BLUE = 2


@deprecated_class(target=NewDataClass, **_DEPRS_CASE_STD_INF_ARGS, args_mapping={"name": "label", "count": "total"})
@dataclass
class MappedDataClass:
    """Deprecated dataclass with args_mapping: remaps 'name'->'label' and 'count'->'total'.

    Example:
        A user calling MappedDataClass(name="x", count=3) receives NewDataClass(label="x", total=3).

    """

    label: str
    total: int = 0


@deprecated_class(target=NewDataClass, **_DEPRS_CASE_STD_INF_ARGS, args_mapping={"legacy_flag": None, "name": "label"})
@dataclass
class MappedDropArgDataClass:
    """Deprecated dataclass with args_mapping: drops 'legacy_flag', remaps 'name'->'label'.

    Example:
        A user calling MappedDropArgDataClass(name="x", legacy_flag=True) receives NewDataClass(label="x").

    """

    label: str
    total: int = 0


# ========== Proxy chain fixtures for chain detection tests ==========


@deprecated_class(target=DeprecatedColorEnum, **_DEPRS_CASE_STD_INF_ARGS)
class ChainedProxyColorEnum(Enum):
    """Deprecated color enum whose target is itself a deprecated proxy (proxy→proxy chain).

    Example:
        This creates a two-hop chain: ChainedProxyColorEnum → DeprecatedColorEnum → ColorEnum.
        Used to test that validate_deprecation_chains detects proxy-to-proxy chains.

    """

    RED = 1
    BLUE = 2


@deprecated(target=DeprecatedColorEnum, **_DEPRS_CASE_STD_ARGS)
def depr_func_targeting_proxy(value: int) -> Any:  # noqa: ANN401
    """Deprecated function whose target is a deprecated proxy (function→proxy chain).

    Examples:
        This creates a cross-kind chain: a @deprecated function forwards to a _DeprecatedProxy.
        Used to test that validate_deprecation_chains detects function-to-proxy chains.

    """
    void(value)


# Shared async source used by the stacking factory above and sibling callable-kind fixtures.


async def async_source_notify(x: int) -> int:
    """NOTIFY-mode async source: body runs unchanged after warning fires.

    Doubles its input so the integration test can assert end-to-end equivalence without referencing the target.

    """
    return x * 2


# ========== Inner-order @property @deprecated regression fixtures (H2) ==========
# Inner-order means ``@property`` outermost, ``@deprecated`` closer to ``def`` — only ``fget`` is
# wrapped.  Chain-style ``@value.setter`` / ``@value.deleter`` afterwards builds a plain ``property``
# whose ``fset`` / ``fdel`` are *not* deprecation-wrapped, so writes and deletes must be silent.


class InnerOrderDeprecatedPropCls(_InnerOrderPropTarget):
    """Inner-order ``@property @deprecated`` with chain-style setter and deleter (H2 fixture).

    Inner-order wrapping wraps ``fget`` only.  ``@value.setter`` and ``@value.deleter`` rebuild the
    ``property`` from the inner ``property`` base class — not :class:`~deprecate._properties._DeprecatedProperty` —
    so the freshly-added accessors are plain callables.  The regression tests assert that writes
    and deletes do NOT emit ``FutureWarning`` for this configuration.

    """

    @property
    @deprecated(**_DEPRS_CASE_STD_ARGS)
    def value(self) -> int:
        """Inner-order deprecated property: only the getter is wrapped."""
        return self._value

    @value.setter
    def value(self, new_value: int) -> None:
        """Plain setter — not wrapped because inner order only wraps the getter."""
        self._value = new_value

    @value.deleter
    def value(self) -> None:
        """Plain deleter — not wrapped because inner order only wraps the getter."""
        self._del_value = None


# ========== Dataclass dual-surface auto-expand fixtures ==========

# attrs_mapping-only on a @dataclass — proxy auto-expands to also populate args_mapping
# so both instance.old_field access and DC(old_field=5) constructor calls emit FutureWarning.
DepAutoExpandDC = deprecated_class(attrs_mapping={"old_field": "new_field"}, **_DEPRS_CASE_STD_INF_ARGS)(AutoExpandDC)

# ========== Positional-only constructor guard fixture ==========

# args_mapping remaps old_val → new_val, but new_val is POSITIONAL_ONLY on
# PositionalOnlyTarget — proxy emits UserWarning at decoration time and forwards
# the remapped value positionally at call time.  Suppress the decoration-time
# UserWarning here so importing this module does not pollute test output.
with catch_warnings():
    simplefilter("ignore", UserWarning)
    DepPositionalOnly = deprecated_class(args_mapping={"old_val": "new_val"}, **_DEPRS_CASE_STD_INF_ARGS)(
        PositionalOnlyTarget
    )

    class PositionalOnlyProxyHolder:
        """Namespace class exposing a positional-only-remapping proxy as a class attribute.

        Same configuration as ``DepPositionalOnly``, reached only through the class: mapping validation with
        ``include_members=False`` stops at the holder and does not report ``LegacyTarget``.

        """

        LegacyTarget = deprecated_class(args_mapping={"old_val": "new_val"}, **_DEPRS_CASE_STD_INF_ARGS)(
            PositionalOnlyTarget
        )


# ========== Proxy POSITIONAL_ONLY constructor forwarding fixtures ==========

# args_mapping remaps to POSITIONAL_ONLY constructor params; the proxy must forward the
# remapped values positionally (decorator split approach) instead of the historical
# pop-and-setattr fallback, which failed for required params, immutable instances, and
# constructor-derived state.  Suppress the decoration-time UserWarning here so importing
# this module does not pollute test output.
with catch_warnings():
    simplefilter("ignore", UserWarning)
    DepPositionalOnlyRequired = deprecated_class(args_mapping={"old_val": "new_val"}, **_DEPRS_CASE_STD_INF_ARGS)(
        RequiredPositionalOnlyTarget
    )
    DepPositionalOnlyImmutable = deprecated_class(args_mapping={"old_val": "new_val"}, **_DEPRS_CASE_STD_INF_ARGS)(
        ImmutablePositionalOnlyTarget
    )
    DepPositionalOnlyDerived = deprecated_class(args_mapping={"old_val": "new_val"}, **_DEPRS_CASE_STD_INF_ARGS)(
        DerivedPositionalOnlyTarget
    )
    DepPositionalOnlyMixed = deprecated_class(args_mapping={"old_x": "x"}, **_DEPRS_CASE_STD_INF_ARGS)(
        MixedPositionalOnlyTarget
    )


# ========== Circular target cycle fixtures ==========
# A → B → A forms a deprecation cycle; calling either wrapper must raise RuntimeError.
# Late-binding via a mutable dict avoids the forward-reference problem at module load time.

_dep_cycle: dict[str, Any] = {}


@deprecated(target=lambda *a, **kw: _dep_cycle["b"](*a, **kw), **_DEPRS_CASE_STD_ARGS)
def dep_cycle_fn_a(x: int) -> int:
    """Deprecated cycle node A — forwards to dep_cycle_fn_b; never reached directly."""
    return x


@deprecated(target=lambda *a, **kw: _dep_cycle["a"](*a, **kw), **_DEPRS_CASE_STD_ARGS)
def dep_cycle_fn_b(x: int) -> int:
    """Deprecated cycle node B — forwards to dep_cycle_fn_a; never reached directly."""
    return x


_dep_cycle["a"] = dep_cycle_fn_a
_dep_cycle["b"] = dep_cycle_fn_b


# ========== Async circular target cycle fixtures ==========
# Same A → B → A cycle shape as above, but for async def sources.
# Targets must be async def (not sync lambdas) because _invoke_async checks
# inspect.iscoroutinefunction(target) to decide whether to await the call.
# A sync lambda that returns a coroutine would have its return value returned
# unawaited, so the cycle guard inside async_wrapped_fn would never fire.

_dep_async_cycle: dict[str, Any] = {}


async def _async_cycle_a_target(x: int) -> int:
    """Async target for dep_async_cycle_fn_a — delegates to dep_async_cycle_fn_b."""
    return await _dep_async_cycle["b"](x)


async def _async_cycle_b_target(x: int) -> int:
    """Async target for dep_async_cycle_fn_b — delegates to dep_async_cycle_fn_a."""
    return await _dep_async_cycle["a"](x)


@deprecated(target=_async_cycle_a_target, **_DEPRS_CASE_STD_ARGS)
async def dep_async_cycle_fn_a(x: int) -> int:
    """Async deprecated cycle node A — forwards to dep_async_cycle_fn_b; never reached directly."""
    return x


@deprecated(target=_async_cycle_b_target, **_DEPRS_CASE_STD_ARGS)
async def dep_async_cycle_fn_b(x: int) -> int:
    """Async deprecated cycle node B — forwards to dep_async_cycle_fn_a; never reached directly."""
    return x


_dep_async_cycle["a"] = dep_async_cycle_fn_a
_dep_async_cycle["b"] = dep_async_cycle_fn_b


# ========== Generator circular target cycle fixtures ==========
# The same A → B → A shape for generator and async generator sources. A generator wrapper creates its inner generator
# and takes the first item under the cycle guard, so the loop re-enters the guard on the first step.

_dep_gen_cycle: dict[str, Any] = {}


@deprecated(target=lambda *a, **kw: _dep_gen_cycle["b"](*a, **kw), **_DEPRS_CASE_STD_ARGS)
def dep_gen_cycle_fn_a(x: int) -> Iterator[int]:
    """Deprecated generator cycle node A — forwards to dep_gen_cycle_fn_b; never reached directly."""
    yield x


@deprecated(target=lambda *a, **kw: _dep_gen_cycle["a"](*a, **kw), **_DEPRS_CASE_STD_ARGS)
def dep_gen_cycle_fn_b(x: int) -> Iterator[int]:
    """Deprecated generator cycle node B — forwards to dep_gen_cycle_fn_a; never reached directly."""
    yield x


@deprecated(target=lambda *a, **kw: _dep_gen_cycle["async_b"](*a, **kw), **_DEPRS_CASE_STD_ARGS)
async def dep_async_gen_cycle_fn_a(x: int) -> AsyncIterator[int]:
    """Deprecated async generator cycle node A — forwards to dep_async_gen_cycle_fn_b; never reached directly."""
    yield x


@deprecated(target=lambda *a, **kw: _dep_gen_cycle["async_a"](*a, **kw), **_DEPRS_CASE_STD_ARGS)
async def dep_async_gen_cycle_fn_b(x: int) -> AsyncIterator[int]:
    """Deprecated async generator cycle node B — forwards to dep_async_gen_cycle_fn_a; never reached directly."""
    yield x


_dep_gen_cycle.update(
    a=dep_gen_cycle_fn_a, b=dep_gen_cycle_fn_b, async_a=dep_async_gen_cycle_fn_a, async_b=dep_async_gen_cycle_fn_b
)


def make_deprecated_notify_args_extra_alone_on_class() -> type:
    """Apply ``@deprecated`` with bare ``args_extra`` (no ``args_mapping``) to a fresh class.

    ``NOTIFY + args_extra`` alone does not auto-resolve to a forwarding mode — the proxy still flags it
    as a misconfiguration via ``_validate_callable_mode``. Used to prove the misconfig warning's reported
    location follows the dispatcher's stacklevel adjustment to this decoration site rather than pointing
    into ``deprecation.py``/``proxy.py`` internals.

    """

    @deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS, args_extra={"z": 1})
    class _DispatchedNotifyArgsExtraAlone:
        """Plain class source with NOTIFY + bare ``args_extra`` — misconfiguration fixture."""

    return _DispatchedNotifyArgsExtraAlone


# ========== Characterization fixtures: warning locations and call shapes ==========
#
# One wrapper per pyDeprecate warn site for ``tests/integration/test_warning_locations.py`` (where does the warning
# point?) and one per call shape for ``tests/integration/test_call_transparency.py`` (what does the source receive?).
# Both files pin today's behaviour so internal refactors of the dispatch and stacklevel machinery are proven against it.
#
# Budgets: module-level ``@deprecated`` wrappers below (including the inner layers of stacks, bound to their own module
# names on purpose), module-level proxies and class-body descriptor wrappers are reset before every test by
# ``tests/conftest.py``.  The descriptor wrappers also use ``num_warns=-1``, so a location test may call one
# repeatedly.  Decoration-time warnings come from ``make_loc_*`` factories: the decorating statement of each
# factory carries a ``# warn-site`` marker so the test can compute the exact line the warning has to point at.


@deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)
def loc_notify(x: int) -> int:
    """Warn-only source — its call-time ``FutureWarning`` must point at the caller's line."""
    return x * 2


def make_loc_audit_wrapper(remove_in: str) -> Callable[..., Any]:
    """Build a warn-only wrapper deprecated in ``1.0`` with the given ``remove_in`` for the audit-gate location rows.

    Built on demand rather than defined at module level, so audit scans of this module never see the deliberately
    unusable ``remove_in`` values (a typo'd version, an epoch change) and emit their diagnostics elsewhere.  The
    deprecation-table tests reuse it for the row of an unparsable ``remove_in``.

    """

    def loc_audit_source(x: int) -> int:
        """Warn-only source scanned by the audit-gate location rows."""
        return x

    return deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in=remove_in)(loc_audit_source)


@deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old_x": "x"}, **_DEPRS_CASE_STD_ARGS)
def loc_args_remap(x: int = 0, old_x: int = 0) -> int:
    """Self-rename source (``old_x`` -> ``x``) — argument ``FutureWarning`` and collision ``UserWarning`` site."""
    return x


@deprecated(target=double_value, **_DEPRS_CASE_STD_ARGS)
def loc_forward(x: int) -> int:
    """Forwarding source — the callable-target ``FutureWarning`` must point at the caller's line."""
    return void(x)


def loc_stack_lifecycle_source(x: int = 0, old_x: int = 0) -> int:
    """Body of the supported ARGS_REMAP-over-NOTIFY lifecycle stack."""
    return x


#: Inner NOTIFY layer of the supported lifecycle stack (bound to a module name so its budget is reset per test).
loc_stack_notify_inner = deprecated(target=TargetMode.NOTIFY, **_DEPRS_CASE_STD_ARGS)(loc_stack_lifecycle_source)
#: Supported stack: the outer layer renames ``old_x``, the inner layer deprecates the whole function.
loc_stack_remap_over_notify = deprecated(
    target=TargetMode.ARGS_REMAP, args_mapping={"old_x": "x"}, **_DEPRS_CASE_STD_ARGS
)(loc_stack_notify_inner)


def loc_stack_forward_source(x: int) -> int:
    """Body of the supported NOTIFY-over-callable stack; never runs because the inner layer forwards."""
    return void(x)


#: Inner forwarding layer of the supported NOTIFY-over-callable stack.
loc_stack_callable_inner = deprecated(target=double_value, **_DEPRS_CASE_STD_ARGS)(loc_stack_forward_source)
#: Supported stack: the outer NOTIFY layer (``v1.5``) announces removal, the inner layer (``v1.0``) forwards.
loc_stack_notify_over_callable = deprecated(target=TargetMode.NOTIFY, deprecated_in="1.5", remove_in="2.0")(
    loc_stack_callable_inner
)


def loc_stack_rename_chain_source(a: int = 0, b: int = 0, c: int = 0) -> int:
    """Body of the supported two-step rename stack (``a`` -> ``b`` first, ``b`` -> ``c`` later)."""
    return c


#: Inner ARGS_REMAP layer of the two-step rename stack (``b`` -> ``c``).
loc_stack_remap_inner = deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"b": "c"}, **_DEPRS_CASE_STD_ARGS)(
    loc_stack_rename_chain_source
)
#: Supported stack: outer layer renames ``a`` -> ``b``; a caller passing ``b`` and ``c`` collides in the inner layer.
loc_stack_remap_over_remap = deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"a": "b"}, **_DEPRS_CASE_STD_ARGS)(
    loc_stack_remap_inner
)


def _loc_positional_only_source(x: int, y: int = 0) -> int:
    """Source forwarding to ``positional_only_target``, whose ``x`` parameter is positional-only."""
    return void(x, y)


def make_loc_legacy_none_sentinel() -> Callable[..., Any]:
    """Decorate through the ``@deprecated`` front door with the legacy ``target=None`` sentinel.

    The legacy-sentinel ``FutureWarning`` has to point at the ``# warn-site`` line, the user's decoration site.
    """
    decorator = deprecated(target=None, **_DEPRS_CASE_STD_ARGS)
    return decorator(identity_value)  # warn-site


def make_loc_decoration_misconfig() -> Callable[..., Any]:
    """Decorate with ``TargetMode.NOTIFY`` plus an ignored ``args_mapping`` — decoration-time misconfig warning."""
    decorator = deprecated(target=TargetMode.NOTIFY, args_mapping={"old_x": "x"}, **_DEPRS_CASE_STD_ARGS)
    return decorator(identity_value)  # warn-site


def make_loc_missing_deprecated_in() -> Callable[..., Any]:
    """Decorate without ``deprecated_in`` — decoration-time missing-version ``UserWarning``."""
    decorator = deprecated(target=TargetMode.NOTIFY, remove_in="2.0")
    return decorator(identity_value)  # warn-site


def make_loc_positional_only_target() -> Callable[..., Any]:
    """Decorate with a target that declares a positional-only parameter — decoration-time ``UserWarning``."""
    decorator = deprecated(target=positional_only_target, **_DEPRS_CASE_STD_ARGS)
    return decorator(_loc_positional_only_source)  # warn-site


def make_loc_class_dispatch_notice() -> Any:  # noqa: ANN401
    """Apply ``@deprecated`` to a fresh class — the one-time class-dispatch ``UserWarning`` at decoration time."""

    class LocLegacyWidget:
        """Plain class deprecated through the ``@deprecated`` front door."""

    decorator = deprecated(**_DEPRS_CASE_STD_ARGS)
    return decorator(LocLegacyWidget)  # warn-site


#: Mapping proxy for the ``__getattr__`` / ``__getitem__`` / ``__iter__`` sites.
loc_proxy_mapping = deprecated_instance({"threshold": 0.5}, name="loc_proxy_mapping", **_DEPRS_CASE_STD_INF_ARGS)
#: Number proxy for the binary- and unary-operator sites.
loc_proxy_number = deprecated_instance(5, name="loc_proxy_number", **_DEPRS_CASE_STD_INF_ARGS)
#: Callable-object proxy for the ``__call__`` site.
loc_proxy_callable = deprecated_instance(CallableAdder(offset=1), name="loc_proxy_callable", **_DEPRS_CASE_STD_INF_ARGS)
#: Deprecated base-class alias for the ``__mro_entries__`` site (``class Child(LocProxyBase): ...``).
LocProxyBase = deprecated_class(**_DEPRS_CASE_STD_INF_ARGS)(SubclassableBase)


def make_loc_side_effect_box_proxy() -> Any:  # noqa: ANN401
    """Wrap a fresh ``SideEffectBox`` for the in-place-operator, ``__enter__`` and iteration sites."""
    return deprecated_instance(SideEffectBox(), name="loc_proxy_box", **_DEPRS_CASE_STD_INF_ARGS)


#: Constructor-rename proxy (``ARGS_REMAP``, no ``target``) for the per-argument and collision constructor sites.
loc_proxy_ctor_remap = deprecated_class(args_mapping={"name": "label"}, **_DEPRS_CASE_STD_INF_ARGS)(NewDataClass)

# Configurable decoration-time factories: each applies one entry point to *source* on its ``# warn-site`` line, with
# *config* overriding the standard version pair (``deprecated_in=""`` drops it).  Decorator-factory warnings (the
# ``template_mgs`` alias) and decorator-application warnings therefore share the same expected line.


def make_loc_front_door(source: Any, **config: Any) -> Any:  # noqa: ANN401
    """Decorate *source* through the ``@deprecated`` front door configured by *config*."""
    return deprecated(**{**_DEPRS_CASE_STD_ARGS, **config})(source)  # warn-site


def make_loc_callable_direct(source: Any, **config: Any) -> Any:  # noqa: ANN401
    """Decorate *source* with the strict ``deprecated_callable`` configured by *config*."""
    return deprecated_callable(**{**_DEPRS_CASE_STD_ARGS, **config})(source)  # warn-site


def make_loc_class_direct(source: Any, **config: Any) -> Any:  # noqa: ANN401
    """Wrap the class *source* with ``deprecated_class`` configured by *config*."""
    return deprecated_class(**{**_DEPRS_CASE_STD_ARGS, **config})(source)  # warn-site


def make_loc_instance(source: Any, **config: Any) -> Any:  # noqa: ANN401
    """Wrap the object *source* with ``deprecated_instance`` configured by *config*."""
    return deprecated_instance(source, **{**_DEPRS_CASE_STD_ARGS, **config})  # warn-site


def make_loc_module(name: str, **config: Any) -> None:  # noqa: ANN401
    """Deprecate the registered module *name* in place with ``deprecated_module`` configured by *config*."""
    deprecated_module(name, **{**_DEPRS_CASE_STD_ARGS, **config})  # warn-site
