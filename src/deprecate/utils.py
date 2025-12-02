"""Utility functions and helpers for deprecation management.

This module provides supporting utilities for the deprecation system, including:
    - Function introspection helpers
    - Testing utilities for deprecated code
    - Warning management tools
    - Package scanning for deprecated wrappers

Key Functions:
    - get_func_arguments_types_defaults(): Extract function signature details
    - no_warning_call(): Context manager for testing code without warnings
    - void(): Helper to silence IDE warnings about unused parameters
    - validate_deprecated_callable(): Validate wrapper configuration
    - find_deprecated_callables(): Scan a package for deprecated wrappers

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


def validate_deprecated_callable(
    func: Callable,
    args_mapping: Optional[dict] = None,
    target: Optional[Callable] = None,
) -> dict:
    """Validate if a deprecated wrapper has any effect.

    This is a development tool to check if deprecated wrappers are configured correctly.
    It identifies configurations that would make the deprecation wrapper have zero impact:
    - args_mapping keys that don't exist in the function's signature
    - Empty or None args_mapping (no argument remapping)
    - Identity mappings where key equals value (e.g., {'arg': 'arg'})
    - Target pointing to the same function (self-reference)
    - All identity: True if ALL mappings are identity (complete no-op)

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
            - 'identity_mapping': List of args where key equals value (no effect)
            - 'self_reference': True if target is the same as func
            - 'no_effect': True if wrapper has zero impact (all checks combined)

    Example:
        >>> def my_func(old_arg: int = 0, new_arg: int = 0) -> int:
        ...     return new_arg
        >>> # Valid mapping - has effect
        >>> result = validate_deprecated_callable(my_func, {'old_arg': 'new_arg'})
        >>> result['no_effect']
        False
        >>> # Identity mapping - no effect
        >>> result = validate_deprecated_callable(my_func, {'old_arg': 'old_arg'})
        >>> result['identity_mapping'], result['no_effect']
        (['old_arg'], True)
        >>> # Self-reference - no effect
        >>> result = validate_deprecated_callable(my_func, {'old_arg': 'new_arg'}, target=my_func)
        >>> result['self_reference'], result['no_effect']
        (True, True)

    .. note::
        Use this function during development or testing to ensure your deprecation
        decorators are configured correctly. Invalid configurations won't cause
        runtime errors but will silently have no effect.

    """
    result = {
        "invalid_args": [],
        "empty_mapping": not args_mapping,
        "identity_mapping": [],
        "self_reference": target is func if target is not None else False,
        "no_effect": False,
    }

    all_identity = False
    if args_mapping:
        func_args = [arg[0] for arg in get_func_arguments_types_defaults(func)]
        result["invalid_args"] = [arg for arg in args_mapping if arg not in func_args]
        result["identity_mapping"] = [arg for arg, val in args_mapping.items() if arg == val]
        # Check if ALL mappings are identity (complete no-op)
        all_identity = len(result["identity_mapping"]) == len(args_mapping) and len(args_mapping) > 0

    # Wrapper has no effect if:
    # - Self-reference (forwards to itself)
    # - Empty mapping (no argument remapping at all)
    # - All mappings are identity (all args map to themselves)
    result["no_effect"] = result["self_reference"] or result["empty_mapping"] or all_identity

    return result


def find_deprecated_callables(
    module: Any,
    recursive: bool = True,
) -> List[dict]:
    """Scan a module or package for deprecated wrapper usages and validate them.

    This is a development tool to scan a codebase for all functions decorated with
    `@deprecated` and validate that each wrapper configuration is meaningful
    (has an effect).

    Args:
        module: A Python module or package to scan for deprecated decorators.
            Can be imported module object or a string module path.
        recursive: If True, recursively scan submodules. Default is True.

    Returns:
        List of dictionaries, one per deprecated function found, each containing:
            - 'module': Module name where the function is defined
            - 'function': Function name
            - 'deprecated_info': The __deprecated__ attribute dict if present
            - 'validation': Result from validate_deprecated_callable() if applicable
            - 'has_effect': True if the wrapper has a meaningful effect

    Example:
        >>> import my_package
        >>> results = find_deprecated_callables(my_package)
        >>> for r in results:
        ...     if not r['has_effect']:
        ...         print(f"Warning: {r['module']}.{r['function']} has no effect!")

    .. note::
        This function requires that the module be importable. It inspects
        the `__deprecated__` attribute set by the @deprecated decorator
        at decoration time.

    """
    import importlib
    import pkgutil

    results = []

    # Handle string module path
    if isinstance(module, str):
        module = importlib.import_module(module)

    def _scan_module(mod: Any) -> None:
        """Scan a single module for deprecated functions."""
        try:
            for name, obj in inspect.getmembers(mod):
                # Skip private/magic attributes and imports from other modules
                if name.startswith("_"):
                    continue

                # Check if it's a function or method with __deprecated__ attribute
                if callable(obj) and hasattr(obj, "__deprecated__"):
                    dep_info = getattr(obj, "__deprecated__", {})
                    target = dep_info.get("target")
                    args_mapping = dep_info.get("args_mapping")

                    # Validate the wrapper
                    validation = validate_deprecated_callable(obj, args_mapping, target)

                    results.append(
                        {
                            "module": mod.__name__ if hasattr(mod, "__name__") else str(mod),
                            "function": name,
                            "deprecated_info": dep_info,
                            "validation": validation,
                            "has_effect": not validation["no_effect"],
                        }
                    )
        except (AttributeError, TypeError, ImportError):
            # Skip modules that can't be inspected
            pass

    # Scan the main module
    _scan_module(module)

    # Recursively scan submodules if requested
    if recursive and hasattr(module, "__path__"):
        try:
            for _importer, modname, _ispkg in pkgutil.walk_packages(
                path=module.__path__, prefix=module.__name__ + ".", onerror=lambda x: None
            ):
                try:
                    submod = importlib.import_module(modname)
                    _scan_module(submod)
                except (ImportError, ModuleNotFoundError):
                    # Skip modules that can't be imported
                    pass
        except (OSError, ImportError):
            pass

    return results
