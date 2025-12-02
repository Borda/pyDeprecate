"""Utility functions and helpers for deprecation management.

This module provides supporting utilities for the deprecation system, including:
    - Function introspection helpers
    - Testing utilities for deprecated code
    - Warning management tools

Key Functions:
    - get_func_arguments_types_defaults(): Extract function signature details
    - no_warning_call(): Context manager for testing code without warnings
    - void(): Helper to silence IDE warnings about unused parameters
    - validate_wrapper_args(): Validate args_mapping configuration

Copyright (C) 2020-2023 Jiri Borovec <...>
"""

import inspect
import warnings
from contextlib import contextmanager
from typing import Any, Callable, Generator, List, Optional, Tuple, Type, Union


def get_func_arguments_types_defaults(func: Callable) -> List[Tuple[str, Tuple, Any]]:
    """Parse function arguments, types and default values.

    Args:
        func: a function to be examined

    Returns:
        List of tuples, one per argument, each containing:
            - str: argument name
            - type: argument type annotation (or inspect._empty if no annotation)
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

        >>> # Example with the function itself
        >>> get_func_arguments_types_defaults(get_func_arguments_types_defaults)
        [('func', typing.Callable, <class 'inspect._empty'>)]

    """
    func_default_params = inspect.signature(func).parameters
    func_arg_type_val = []
    for arg in func_default_params:
        arg_type = func_default_params[arg].annotation
        arg_default = func_default_params[arg].default
        func_arg_type_val.append((arg, arg_type, arg_default))
    return func_arg_type_val


def _warns_repr(warns: List[warnings.WarningMessage]) -> List[Union[Warning, str]]:
    """Convert list of warning messages to their string representations.

    Args:
        warns: List of warning message objects captured during execution

    Returns:
        List of warning messages as strings or Warning objects

    """
    return [w.message for w in warns]


@contextmanager
def no_warning_call(warning_type: Optional[Type[Warning]] = None, match: Optional[str] = None) -> Generator:
    """Context manager to assert that no warnings are raised.

    This is useful for testing that new/replacement functions don't trigger
    deprecation warnings, or that code paths properly avoid deprecated functionality.

    Args:
        warning_type: The type of warning to catch (e.g., FutureWarning, DeprecationWarning).
            If None, catches all warning types.
        match: If specified, only fail if warning message contains this string.
            If None, fails on any warning of the specified type.

    Raises:
        AssertionError: If a warning of the specified type (and optionally matching
            the message pattern) was raised during the context.

    Example:
        >>> # Basic usage
        >>> import warnings
        >>> def new_func(x: int) -> int:
        ...     return x * 2
        >>> # Test passes only if no FutureWarning is raised
        >>> with no_warning_call(FutureWarning):
        ...     result = new_func(42)
        >>> result
        84

        >>> # Only fails if warning contains "deprecated"
        >>> def some_function():
        ...     warnings.warn("deprecated feature", FutureWarning)
        >>> with no_warning_call(FutureWarning, match="other"):  # doesn't match, so passes
        ...     some_function()

        >>> # Fails if ANY warning is raised
        >>> def clean_function():
        ...     pass
        >>> with no_warning_call():
        ...     clean_function()

    .. note:
        This is the inverse of ``pytest.warns()`` - it ensures warnings are NOT raised.
        Useful for testing that refactored code properly uses new APIs.

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


def void(*args: Any, **kwrgs: Any) -> Any:
    """Empty function that accepts any arguments and returns None.

    This helper function is used to silence IDE warnings about unused parameters
    in deprecated functions where the body is never executed (calls are forwarded
    to a target function).

    Args:
        *args: Any positional arguments (ignored)
        **kwrgs: Any keyword arguments (ignored)

    Returns:
        None

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
        >>>
        >>> old_func(5)  # Returns 10
        10

    .. note:
        This function has no runtime effect - it's purely for developer convenience
        to avoid IDE warnings. You can also use ``pass`` or just a docstring instead.

    """
    _, _ = args, kwrgs


def validate_wrapper_args(
    func: Callable,
    args_mapping: Optional[dict] = None,
    target: Optional[Callable] = None,
) -> dict:
    """Validate if a deprecated wrapper has any effect.

    This is a development tool to check if deprecated wrappers are configured correctly.
    It identifies issues that would make the deprecation wrapper ineffective:
    - args_mapping keys that don't exist in the function's signature
    - Empty or None args_mapping (no argument remapping)
    - Target pointing to the same function (self-reference)

    Args:
        func: The decorated function to validate
        args_mapping: Dictionary mapping old argument names to new ones.
            Keys should be argument names that exist in the function's signature.
        target: The target function that calls are forwarded to.
            Used to check for self-reference.

    Returns:
        Dictionary with validation results:
            - 'invalid_args': List of args_mapping keys not in function signature
            - 'empty_mapping': True if args_mapping is None or empty
            - 'self_reference': True if target is the same as func

    Example:
        >>> def my_func(old_arg: int = 0, new_arg: int = 0) -> int:
        ...     return new_arg
        >>> # Valid mapping
        >>> validate_wrapper_args(my_func, {'old_arg': 'new_arg'})
        {'invalid_args': [], 'empty_mapping': False, 'self_reference': False}
        >>> # Invalid mapping - 'nonexistent' is not in signature
        >>> validate_wrapper_args(my_func, {'nonexistent': 'new_arg'})
        {'invalid_args': ['nonexistent'], 'empty_mapping': False, 'self_reference': False}
        >>> # Empty mapping - wrapper has no effect
        >>> validate_wrapper_args(my_func, {})
        {'invalid_args': [], 'empty_mapping': True, 'self_reference': False}
        >>> # Self-reference - wrapper forwards to itself
        >>> validate_wrapper_args(my_func, {'old_arg': 'new_arg'}, target=my_func)
        {'invalid_args': [], 'empty_mapping': False, 'self_reference': True}

    .. note::
        Use this function during development or testing to ensure your deprecation
        decorators are configured correctly. Invalid configurations won't cause
        runtime errors but will silently have no effect.

    """
    result = {
        "invalid_args": [],
        "empty_mapping": not args_mapping,
        "self_reference": target is func if target is not None and func is not None else False,
    }

    if args_mapping:
        func_args = [arg[0] for arg in get_func_arguments_types_defaults(func)]
        result["invalid_args"] = [arg for arg in args_mapping if arg not in func_args]

    return result
