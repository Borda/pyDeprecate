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

Key Classes:
    - DeprecatedCallableInfo: Dataclass for deprecated callable information and validation

Copyright (C) 2020-2023 Jiri Borovec <...>
"""

import inspect
import warnings
from collections.abc import Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union


@dataclass
class DeprecatedCallableInfo:
    """Information about a deprecated callable and its validation results.

    This dataclass represents a deprecated function or method, containing both
    identification info and validation results from validate_deprecated_callable()
    or find_deprecated_callables().

    Attributes:
        module: Module name where the function is defined (empty for direct validation).
        function: Function name.
        deprecated_info: The __deprecated__ attribute dict from the decorator.
        invalid_args: List of args_mapping keys that don't exist in the function signature.
        empty_mapping: True if args_mapping is None or empty (no argument remapping).
        identity_mapping: List of args where key equals value (e.g., {'arg': 'arg'}).
        self_reference: True if target points to the same function.
        no_effect: True if wrapper has zero impact (combines all checks).
        has_effect: True if the wrapper has a meaningful effect (inverse of no_effect).

    Example:
        >>> info = DeprecatedCallableInfo(
        ...     module="my_package.module",
        ...     function="old_function",
        ...     deprecated_info={"deprecated_in": "1.0", "remove_in": "2.0"},
        ...     invalid_args=["nonexistent"],
        ...     has_effect=False,
        ... )
        >>> info.function
        'old_function'
        >>> info.invalid_args
        ['nonexistent']

    """

    module: str = ""
    function: str = ""
    deprecated_info: Dict[str, Any] = field(default_factory=dict)
    invalid_args: List[str] = field(default_factory=list)
    empty_mapping: bool = False
    identity_mapping: List[str] = field(default_factory=list)
    self_reference: bool = False
    no_effect: bool = False
    has_effect: bool = True


def get_func_arguments_types_defaults(func: Callable) -> list[tuple[str, tuple, Any]]:
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


def _warns_repr(warns: list[warnings.WarningMessage]) -> list[Union[Warning, str]]:
    """Convert list of warning messages to their string representations.

    Args:
        warns: List of warning message objects captured during execution

    Returns:
        List of warning messages as strings or Warning objects

    """
    return [w.message for w in warns]


@contextmanager
def no_warning_call(warning_type: Optional[type[Warning]] = None, match: Optional[str] = None) -> Generator:
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


def validate_deprecated_callable(func: Callable) -> DeprecatedCallableInfo:
    """Validate if a deprecated wrapper has any effect.

    This is a development tool to check if deprecated wrappers are configured correctly.
    It extracts the configuration from the function's ``__deprecated__`` attribute
    (set by the @deprecated decorator) and identifies configurations that would make
    the deprecation wrapper have zero impact:
    - args_mapping keys that don't exist in the function's signature
    - Empty or None args_mapping (no argument remapping)
    - Identity mappings where key equals value (e.g., {'arg': 'arg'})
    - Target pointing to the same function (self-reference)
    - All identity: True if ALL mappings are identity (complete no-op)

    Args:
        func: The decorated function to validate. Must have a ``__deprecated__``
            attribute set by the @deprecated decorator.

    Returns:
        DeprecatedCallableInfo: Dataclass with validation results:
            - function: Name of the function being validated
            - deprecated_info: The __deprecated__ attribute dict from the decorator
            - invalid_args: List of args_mapping keys not in function signature
            - empty_mapping: True if args_mapping is None or empty
            - identity_mapping: List of args where key equals value (no effect)
            - self_reference: True if target is the same as func
            - no_effect: True if wrapper has zero impact (all checks combined)
            - has_effect: True if wrapper has a meaningful effect

    Example:
        >>> from deprecate import deprecated, validate_deprecated_callable
        >>> def new_implementation(value: int) -> int:
        ...     return value * 2
        >>> @deprecated(target=new_implementation, deprecated_in="1.0", args_mapping={"old_val": "value"})
        ... def old_func(old_val: int) -> int:
        ...     pass
        >>> # Valid mapping to different function - has effect
        >>> result = validate_deprecated_callable(old_func)
        >>> result.no_effect
        False
        >>> @deprecated(target=True, deprecated_in="1.0", args_mapping={"arg": "arg"})
        ... def identity_func(arg: int) -> int:
        ...     return arg
        >>> # Identity mapping with self-deprecation - no effect
        >>> result = validate_deprecated_callable(identity_func)
        >>> result.identity_mapping, result.no_effect
        (['arg'], True)

    .. note::
        Use this function during development or testing to ensure your deprecation
        decorators are configured correctly. Invalid configurations won't cause
        runtime errors but will silently have no effect.

    Raises:
        ValueError: If the function does not have a __deprecated__ attribute
            (i.e., was not decorated with @deprecated).

    """
    # Extract configuration from __deprecated__ attribute
    if not hasattr(func, "__deprecated__"):
        raise ValueError(
            f"Function {getattr(func, '__name__', func)} does not have a __deprecated__ attribute. "
            "It must be decorated with @deprecated."
        )

    dep_info = getattr(func, "__deprecated__", {})
    args_mapping = dep_info.get("args_mapping")
    target = dep_info.get("target")

    invalid_args: List[str] = []
    empty_mapping = not args_mapping
    identity_mapping: List[str] = []
    self_reference = target is func if target is not None else False

    all_identity = False
    if args_mapping:
        func_args = [arg[0] for arg in get_func_arguments_types_defaults(func)]
        invalid_args = [arg for arg in args_mapping if arg not in func_args]
        identity_mapping = [arg for arg, val in args_mapping.items() if arg == val]
        # Check if ALL mappings are identity (complete no-op)
        all_identity = len(identity_mapping) == len(args_mapping) and len(args_mapping) > 0

    # Wrapper has no effect if:
    # - Self-reference (forwards to itself)
    # - target is None AND no args_mapping (just warns, no forwarding or remapping)
    # - target is True (self-deprecation) AND (empty mapping OR all identity mappings)
    # Note: When target is a different function, there's ALWAYS an effect (forwarding)
    is_self_deprecation = target is True or self_reference
    no_effect = (
        self_reference
        or (target is None and empty_mapping)
        or (is_self_deprecation and (empty_mapping or all_identity))
    )

    return DeprecatedCallableInfo(
        function=getattr(func, "__name__", str(func)),
        deprecated_info=dep_info,
        invalid_args=invalid_args,
        empty_mapping=empty_mapping,
        identity_mapping=identity_mapping,
        self_reference=self_reference,
        no_effect=no_effect,
        has_effect=not no_effect,
    )


def find_deprecated_callables(
    module: Any,
    recursive: bool = True,
) -> List[DeprecatedCallableInfo]:
    """Scan a module or package for deprecated wrapper usages and validate them.

    This is a development tool to scan a codebase for all functions decorated with
    `@deprecated` and validate that each wrapper configuration is meaningful
    (has an effect).

    Args:
        module: A Python module or package to scan for deprecated decorators.
            Can be imported module object or a string module path.
        recursive: If True, recursively scan submodules. Default is True.

    Returns:
        List of DeprecatedCallableInfo dataclasses, one per deprecated function found,
        each containing:
            - module: Module name where the function is defined
            - function: Function name
            - deprecated_info: The __deprecated__ attribute dict if present
            - invalid_args, empty_mapping, identity_mapping, self_reference, no_effect
            - has_effect: True if the wrapper has a meaningful effect

    Example:
        >>> import my_package
        >>> results = find_deprecated_callables(my_package)
        >>> for r in results:
        ...     if not r.has_effect:
        ...         print(f"Warning: {r.module}.{r.function} has no effect!")

    .. note::
        This function requires that the module be importable. It inspects
        the `__deprecated__` attribute set by the @deprecated decorator
        at decoration time.

    """
    import importlib
    import pkgutil

    results: List[DeprecatedCallableInfo] = []

    # Handle string module path
    if isinstance(module, str):
        module = importlib.import_module(module)

    def _scan_module(mod: Any) -> None:
        """Scan a single module for deprecated functions."""
        try:
            members = inspect.getmembers(mod)
        except (AttributeError, TypeError, ImportError):
            return

        for name, obj in members:
            # Skip private/magic attributes and imports from other modules
            if name.startswith("_"):
                continue

            # Check if it's a function or method with __deprecated__ attribute
            if callable(obj) and hasattr(obj, "__deprecated__"):
                # Validate the wrapper - extracts config from __deprecated__
                info = validate_deprecated_callable(obj)
                # Update with module-level info
                info.module = mod.__name__ if hasattr(mod, "__name__") else str(mod)
                info.function = name

                results.append(info)

    # Scan the main module
    _scan_module(module)

    # Recursively scan submodules if requested
    if recursive and hasattr(module, "__path__"):
        try:
            packages = list(pkgutil.walk_packages(
                path=module.__path__, prefix=module.__name__ + ".", onerror=lambda x: None
            ))
        except (OSError, ImportError):
            packages = []

        for _importer, modname, _ispkg in packages:
            with suppress(ImportError, ModuleNotFoundError):
                submod = importlib.import_module(modname)
                _scan_module(submod)

    return results
