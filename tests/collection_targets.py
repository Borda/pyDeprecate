"""Target functions for deprecation testing.

This module provides base functions that are used as targets for deprecated
functions in other test modules.
"""

import functools
import time
import warnings
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


def raise_pow(base: float, coef: float) -> float:
    """Compute base**coef while emitting a UserWarning — used to test assert_no_warnings."""
    warnings.warn("warning you!", UserWarning)
    return base**coef


def raise_pow_future(base: float, coef: float) -> float:
    """Compute base**coef while emitting a FutureWarning — used to test assert_no_warnings."""
    warnings.warn("future warning!", FutureWarning)
    return base**coef


def base_sum_kwargs(a: int = 0, b: int = 3) -> int:
    """Base sum function with keyword arguments."""
    return a + b


def base_pow_args(a: float, b: int) -> float:
    """Base power function with positional arguments."""
    return a**b


tracked_identity_calls: list[int] = []


def double_value(x: int) -> int:
    """Return double the input value for TargetMode smoke tests."""
    return x * 2


def tracked_identity(x: int) -> int:
    """Record calls and return the original value for body-execution tests."""
    tracked_identity_calls.append(x)
    return x


def increment_value(x: int) -> int:
    """Return the input value plus one for args-only deprecation tests."""
    return x + 1


def power_with_new_coef(base: float, new_coef: float = 1.0) -> float:
    """Raise a base to a remapped coefficient for args-only tests."""
    return base**new_coef


def add_values(x: int, y: int) -> int:
    """Add two integers for args-extra injection tests."""
    return x + y


def identity_value(x: int) -> int:
    """Return the original input value."""
    return x


def stacked_chain_identity(base: int) -> int:
    """Return the base input unchanged for stacked TargetMode chain fixtures."""
    return base


def return_b(b: int) -> int:
    """Return the mapped positional argument."""
    return b


def return_z(z: int = 0) -> int:
    """Return the optional keyword argument used in warning tests."""
    return z


def return_none() -> None:
    """Return ``None`` for warning-only sentinel tests."""


def return_new(new: int = 0) -> int:
    """Return the remapped value for the ``target=True`` sentinel test."""
    return new


class NewCls:
    """New class for testing deprecation."""

    def __init__(self, c: float, d: str = "abc", **kwargs: Any) -> None:  # noqa: ANN401
        """Initialize NewCls."""
        self.my_c = c
        self.my_d = d
        self.my_e = kwargs.get("e", 0.2)


class NewEnum(Enum):
    """New enum for forwarding tests."""

    ALPHA = "alpha"
    BETA = "beta"


class NewIntEnum(Enum):
    """New enum with integer values for mapping tests."""

    ALPHA = 1
    BETA = 2


class TargetColorEnum(Enum):
    """Target enum for deprecated_class forwarding tests."""

    RED = 1
    BLUE = 2


def plain_function_target(x: int) -> int:
    """Plain function without deprecation decorator for testing error handling."""
    return x


def cross_guard_standalone_increment(x: int) -> int:
    """Module-level target used by cross-class guard tests."""
    return x + 1


def call_signature_source(value: str) -> object:
    """Source signature helper for _prepare_target_call tests."""
    raise NotImplementedError


class KeywordCallMeta(type):
    """Metaclass exposing a keyword-only value in __call__ for signature-validation tests."""

    def __call__(cls, *, value: str) -> object:
        """Create a target instance using keyword-only `value`."""
        return super().__call__(raw=value)


class KeywordCallTarget(metaclass=KeywordCallMeta):
    """Target class whose metaclass __call__ differs from __init__."""

    def __init__(self, raw: str) -> None:
        """Store the raw payload passed through metaclass __call__."""
        self.raw = raw


class CrossGuardClassTargetNew:
    """Constructor-forwarding target class used by cross-class guard tests."""

    def __init__(self, x: int) -> None:
        """Store constructor argument for assertions."""
        self.x = x


def sample_function(x: int) -> int:
    """Simple callable used as input to deprecated function-wrapper tests."""
    return x * 2


@dataclass
class NewDataClass:
    """Target dataclass for deprecation forwarding tests."""

    label: str
    total: int = 0


class SomeTargetClass:
    """Simple target class for proxy args_mapping behaviour tests.

    Accepts ``new_key`` as the canonical parameter name; used to verify that
    ``deprecated_class`` fixtures correctly remap ``old_key`` to ``new_key``.
    """

    def __init__(self, new_key: int = 0) -> None:
        """Store the canonical keyword argument."""
        self.new_key = new_key


class TargetWithInjected:
    """Target class accepting an ``injected`` kwarg for ``args_extra`` tests.

    Used by proxy ``args_extra`` fixtures to verify that deprecated_class()
    merges configured extra kwargs into forwarded calls.
    """

    def __init__(self, new_key: int = 0, injected: str = "") -> None:
        """Store both the canonical keyword and the injected extra value."""
        self.new_key = new_key
        self.injected = injected


def both_old_new_target(new: int = 0) -> int:
    """Target callable used by collision-bug fixtures (only ``new`` accepted)."""
    return new


def fn_with_default(new_arg: int = 99) -> int:
    """Target callable carrying its own default for the renamed argument.

    Used by Fix 1 regression tests to verify that the source's stale default for
    the deprecated argument name does not silently override the target's default
    when the caller supplies neither name.
    """
    return new_arg


def fn_remap_with_extra_body(new_arg: int = 0, injected: int = 0) -> int:
    """Source body for Fix 2 regression tests using TargetMode.ARGS_REMAP.

    The body intentionally combines the remapped argument and the injected extra
    so the test can assert that ``args_extra`` is merged into kwargs even when the
    caller already uses the new argument name (no remap warning fires).
    """
    return new_arg + injected


def pep702_target(x: int) -> int:
    """Target for PEP 702 stacking regression tests.

    Doubles the input value so the wrapping test can confirm the inner pyDeprecate
    @deprecated forwarded the call after PEP 702 ``typing_extensions.deprecated``
    overwrote ``__deprecated__`` on the wrapper.
    """
    return x * 2


class _Pep702ProxyTarget:
    """Target class for PEP 702 stacking on ``deprecated_class`` proxy (B1b).

    Provides a stable ``value()`` method so the stacking test can confirm that
    instantiation and method dispatch survive after PEP 702
    ``typing_extensions.deprecated`` was applied on top of the ``deprecated_class``
    proxy wrapper.

    Underscore-prefixed so :func:`deprecate.find_deprecation_wrappers` skips it: the
    outer PEP 702 wrapper forwards its ``__deprecated__ = msg`` assignment through the
    proxy's ``__setattr__`` onto this wrapped class, leaving a plain string on the
    class attribute that would otherwise crash the audit walker.
    """

    def value(self) -> int:
        """Return a stable sentinel value used by the B1b regression test."""
        return 42


def timing_wrapper(func: Callable) -> Callable:
    """Decorator to measure the execution time of a function."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Wrapper function."""
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        end_time = time.perf_counter()
        print(f"Function {func.__name__!r} took {(end_time - start_time):.4f} seconds to execute.")
        return result

    return wrapper


def stacked_inner_target(x: int) -> int:
    """Inner target for stacked-callable-target guard tests."""
    return x * 3


def stacked_outer_target(x: int) -> int:
    """Outer target for stacked-callable-target guard tests."""
    return x * 5


class TimerDecorator:
    """A class-based decorator to time functions and methods."""

    def __init__(self, func: Callable) -> None:
        """Initialize the timer decorator."""
        functools.update_wrapper(self, func)
        self.func = func
        self.total_time = 0.0
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Call the wrapped function and track timing."""
        start_time = time.perf_counter()
        result = self.func(*args, **kwargs)
        end_time = time.perf_counter()
        execution_time = end_time - start_time
        self.total_time += execution_time
        self.calls += 1
        print(f"'{self.func.__name__}' executed in {execution_time:.4f}s")
        return result


def compute_power(base: float, factor: float = 1, scale: float = 1) -> float:
    """Compute base raised to scale; factor is the legacy parameter name for scale."""
    return base**scale
