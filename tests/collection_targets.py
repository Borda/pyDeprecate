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


def base_add(a: int, b: int = 0) -> int:
    """Base addition function used as target for wrapper-form deprecation tests."""
    return a + b


def raise_pow(base: float, coef: float) -> float:
    """Compute base**coef while emitting a UserWarning — used to test no_warning_call."""
    warnings.warn("warning you!", UserWarning)
    return base**coef


def base_sum_kwargs(a: int = 0, b: int = 3) -> int:
    """Base sum function with keyword arguments."""
    return a + b


def base_pow_args(a: float, b: int) -> float:
    """Base power function with positional arguments."""
    return a**b


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


@dataclass
class NewDataClass:
    """Target dataclass for deprecation forwarding tests."""

    label: str
    total: int = 0


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


def standalone_sum(a: int, b: int = 0) -> int:
    """Standalone sum function used as source body for target=None wrapper-form tests."""
    return a + b


def self_rename_pow(base: float, old_exp: float = 0, new_exp: float = 0) -> float:
    """Self-deprecation target: body uses new_exp, old_exp is the deprecated name."""
    return base**new_exp
