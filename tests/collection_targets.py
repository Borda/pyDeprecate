"""Target functions for deprecation testing.

This module provides base functions that are used as targets for deprecated
functions in other test modules.
"""

import functools
import time
from typing import Any, Callable


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


def logging_wrapper(func: Callable) -> Callable:
    """Decorator to log function calls (improved version)."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Wrapper function."""
        print(f"Calling {func.__name__!r} with args={args}, kwargs={kwargs}")
        result = func(*args, **kwargs)
        print(f"Function {func.__name__!r} returned {result!r}")
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
