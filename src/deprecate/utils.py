"""Utility helpers for the deprecation system.

This module provides supporting helpers used internally by the deprecation decorator
and exposed for use in tests:
    - Function signature introspection
    - Testing utilities for deprecated code
    - Warning management tools

Key Functions:
    - :func:`get_func_arguments_types_defaults`: Extract function signature details
    - :func:`no_warning_call`: Context manager for testing code without warnings
    - :func:`void`: Helper to silence IDE warnings about unused parameters

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>
"""

import inspect
import warnings
from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Callable, Optional, Union


def get_func_arguments_types_defaults(func: Callable) -> list[tuple[str, Any, Any]]:
    """Parse function arguments, types and default values.

    This introspection helper extracts the complete signature information from
    a function, including parameter names, type annotations, and default values.
    Useful for dynamic argument handling and validation in wrapper functions.

    Args:
        func: A function to be examined.

    Returns:
        List of tuples, one per argument, each containing:
            - str: argument name
            - Any: argument type annotation (or inspect._empty if no annotation)
            - Any: default value (or inspect._empty if no default)

    Example:
        >>> def example_func(x: int, y: str = "hello", z=42) -> None:
        ...     pass
        >>> result = get_func_arguments_types_defaults(example_func)
        >>> for name, type_hint, default in result:
        ...     print(f"{name}: type={type_hint}, default={default}")
        x: type=<class 'int'>, default=<class 'inspect._empty'>
        y: type=<class 'str'>, default=hello
        z: type=<class 'inspect._empty'>, default=42

    Note:
        - Parameters without type annotations have annotation = inspect._empty
        - Parameters without defaults have default = inspect._empty
        - Excludes *args and **kwargs (use inspect.getfullargspec for those)

    """
    func_default_params = _get_signature(func).parameters
    func_arg_type_val = []
    for arg in func_default_params:
        arg_type = func_default_params[arg].annotation
        arg_default = func_default_params[arg].default
        func_arg_type_val.append((arg, arg_type, arg_default))
    return func_arg_type_val


@lru_cache(maxsize=256)
def _get_signature_cached(func: Callable) -> inspect.Signature:
    """Cache inspect.signature lookups for repeated calls.

    Uses an LRU cache (maxsize=256) since function signatures are stable at runtime.
    The size balances reuse for common callables without unbounded memory growth.
    """
    return inspect.signature(func)


def _get_signature(func: Callable) -> inspect.Signature:
    """Get function signature with caching when possible.

    Falls back to uncached lookup for unhashable callables.
    """
    try:
        return _get_signature_cached(func)
    except TypeError:
        return inspect.signature(func)


def _warns_repr(warns: list[warnings.WarningMessage]) -> list[Union[Warning, str]]:
    """Convert list of warning messages to their string representations.

    Args:
        warns: List of warning message objects captured during execution.

    Returns:
        List of warning messages as strings or Warning objects.

    """
    return [w.message for w in warns]


@contextmanager
def no_warning_call(warning_type: Optional[type[Warning]] = None, match: Optional[str] = None) -> Generator:
    """Context manager to assert that no warnings are raised during execution.

    This is the inverse of ``pytest.warns()`` - it ensures that specified warnings
    are NOT raised. Useful for testing that refactored code properly avoids
    deprecated functionality or that new implementations don't trigger warnings.

    Args:
        warning_type: The type of warning to catch (e.g., :class:`FutureWarning`,
            :class:`DeprecationWarning`). If None, checks that NO warnings of any
            type are raised.
        match: If specified, only fail if a warning message contains this string.
            If None, fails on any warning of the specified type.

    Raises:
        AssertionError: If a warning of the specified type (and optionally matching
            the message pattern) was raised during the context.

    Example:
        >>> # Test that new function doesn't trigger FutureWarning
        >>> import warnings
        >>> def new_func(x: int) -> int:
        ...     return x * 2
        >>> with no_warning_call(FutureWarning):
        ...     result = new_func(42)
        >>> result
        84

        >>> # Test that NO warnings at all are raised
        >>> def clean_function():
        ...     pass
        >>> with no_warning_call():
        ...     clean_function()

        >>> # Only fail if warning message matches pattern
        >>> def some_function():
        ...     warnings.warn("deprecated feature", FutureWarning)
        >>> # Passes because warning contains "feature", not "other"
        >>> with no_warning_call(FutureWarning, match="other"):
        ...     some_function()

    Note:
        This context manager is particularly useful in pytest for testing that
        refactored code properly uses new APIs without triggering deprecation warnings.

    """
    with warnings.catch_warnings(record=True) as called:
        # Cause all warnings to always be triggered.
        warnings.simplefilter("always")
        # Trigger a warning.
        yield
        # no warning raised
        if not called:
            return
        if not warning_type:
            raise AssertionError(f"While catching all warnings, these were found: {_warns_repr(called)}")
        # filter warnings by type
        warns = [w for w in called if issubclass(w.category, warning_type)]
        # Verify some things
        if not warns:
            return
        if not match:
            raise AssertionError(
                f"While catching `{warning_type.__name__}` warnings, these were found: {_warns_repr(warns)}"
            )
        found = [w for w in warns if match in w.message.__str__()]
        if found:
            raise AssertionError(
                f'While catching `{warning_type.__name__}` warnings with "{match}",'
                f" these were found: {_warns_repr(found)}"
            )


def void(*args: Any, **kwrgs: Any) -> Any:  # noqa: ANN401
    """Empty function that accepts any arguments and returns None.

    This helper function is used to silence IDE warnings about unused parameters
    in deprecated functions where the body is never executed (calls are forwarded
    to a target function). It's purely a convenience for developers.

    Args:
        *args: Any positional arguments (ignored).
        **kwrgs: Any keyword arguments (ignored).

    Returns:
        None always.

    Example:
        >>> from deprecate import deprecated, void
        >>>
        >>> def new_func(x: int) -> int:
        ...     return x * 2
        >>>
        >>> @deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
        ... def old_func(x: int) -> int:
        ...     void(x)  # Silences IDE warning about unused 'x'
        ...     # This line is never reached - call forwarded to new_func

    Note:
        This function has no runtime effect - it's purely for developer convenience.
        You can also use ``pass`` or just a docstring instead of calling ``void()``.

    """
    _, _ = args, kwrgs
