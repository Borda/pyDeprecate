"""Utility functions and helpers for deprecation management.

This module provides supporting utilities for the deprecation system, including:
    - Function introspection helpers
    - Testing utilities for deprecated code
    - Warning management tools
    - Package scanning for deprecated wrappers

Key Functions:
    - :func:`get_func_arguments_types_defaults`: Extract function signature details
    - :func:`no_warning_call`: Context manager for testing code without warnings
    - :func:`void`: Helper to silence IDE warnings about unused parameters
    - :func:`validate_deprecated_callable`: Validate wrapper configuration
    - :func:`check_deprecation_expiry`: Check if deprecated code has passed removal deadline
    - :func:`check_module_deprecation_expiry`: Check all deprecated code in a module for expired deadlines
    - :func:`find_deprecated_callables`: Scan a package for deprecated wrappers

Key Classes:
    - :class:`DeprecatedCallableInfo`: Dataclass for deprecated callable information

.. note::
   Version comparison features (check_deprecation_expiry, check_module_deprecation_expiry)
   require the 'packaging' library. Install with: ``pip install pyDeprecate[audit]``

Copyright (C) 2020-2026 Jiri Borovec <...>
"""

import inspect
import warnings
from collections.abc import Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

if TYPE_CHECKING:
    from packaging.version import Version


def _parse_version(version_string: str) -> "Version":
    """Parse a version string using the packaging library (PEP 440 compliant).

    This function requires the 'packaging' library, which is available as an
    optional dependency via the 'audit' extra: ``pip install pyDeprecate[audit]``

    The packaging library provides robust PEP 440 version parsing and comparison,
    supporting pre-releases (alpha/beta/rc), stable releases, post-releases, and
    development releases with proper ordering.

    Args:
        version_string: Version string (e.g., "1.2.3", "2.0", "1.5.0a1",
            "1.5.0rc1", "1.5.0.post1").

    Returns:
        packaging.version.Version object that supports comparison operations.

    Raises:
        ImportError: If the packaging library is not installed.
        ValueError: If the version string is not valid per PEP 440
            (wraps ``packaging.version.InvalidVersion`` with additional context).

    Example:
        >>> v1 = _parse_version("1.2.3")  # doctest: +SKIP
        >>> v2 = _parse_version("2.0")  # doctest: +SKIP
        >>> v1 < v2  # doctest: +SKIP
        True
        >>> _parse_version("1.5.0a1") < _parse_version("1.5.0")  # doctest: +SKIP
        True

    .. note::
       Install the audit extra to use version comparison features:
       ``pip install pyDeprecate[audit]``

    """
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError as e:
        raise ImportError(
            "Version comparison requires the 'packaging' library. Install with: pip install pyDeprecate[audit]"
        ) from e

    try:
        return Version(version_string)
    except InvalidVersion as e:
        raise ValueError(
            f"Failed to parse version '{version_string}'. Expected PEP 440 format "
            f"(e.g., '1.2.3', '2.0', '1.5.0a1'). Error: {e}"
        ) from e


@dataclass(frozen=True)
class DeprecatedCallableInfo:
    """Information about a deprecated callable and its validation results.

    This dataclass represents a deprecated function or method, containing both
    identification info and validation results from validate_deprecated_callable()
    or find_deprecated_callables().

    Attributes:
        module: Module name where the function is defined (empty for direct validation).
        function: Function name.
        deprecated_info: The ``__deprecated__`` attribute dict from the decorator.
        invalid_args: List of ``args_mapping`` keys that don't exist in the function signature.
        empty_mapping: True if ``args_mapping`` is None or empty (no argument remapping).
        identity_mapping: List of args where key equals value (e.g., ``{'arg': 'arg'}``).
        self_reference: True if target points to the same function.
        no_effect: True if wrapper has zero impact (combines all checks).

    Example:
        >>> info = DeprecatedCallableInfo(
        ...     module="my_package.module",
        ...     function="old_function",
        ...     deprecated_info={"deprecated_in": "1.0", "remove_in": "2.0"},
        ...     invalid_args=["nonexistent"],
        ...     no_effect=True,
        ... )
        >>> info.function
        'old_function'
        >>> info.invalid_args
        ['nonexistent']

    """

    module: str = ""
    function: str = ""
    deprecated_info: dict[str, Any] = field(default_factory=dict)
    invalid_args: list[str] = field(default_factory=list)
    empty_mapping: bool = False
    identity_mapping: list[str] = field(default_factory=list)
    self_reference: bool = False
    no_effect: bool = False


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


def validate_deprecated_callable(func: Callable) -> DeprecatedCallableInfo:
    """Validate if a deprecated wrapper configuration is effective.

    This is a development tool to check if deprecated wrappers are configured correctly
    and will have the intended effect. It examines the ``__deprecated__`` attribute
    set by the @deprecated decorator and identifies configurations that would result
    in zero impact:

    - args_mapping keys that don't exist in the function's signature
    - Empty or None args_mapping (no argument remapping)
    - Identity mappings where key equals value (e.g., {'arg': 'arg'})
    - Target pointing to the same function (self-reference)
    - target=None with no args_mapping (just warns, no forwarding)

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

    Raises:
        ValueError: If the function does not have a __deprecated__ attribute
            (i.e., was not decorated with @deprecated).

    Example:
        >>> from deprecate import deprecated, validate_deprecated_callable
        >>> def new_implementation(value: int) -> int:
        ...     return value * 2
        >>>
        >>> @deprecated(target=new_implementation, deprecated_in="1.0", args_mapping={"old_val": "value"})
        ... def old_func(old_val: int) -> int:
        ...     pass
        >>>
        >>> # Valid mapping to different function - has effect
        >>> result = validate_deprecated_callable(old_func)
        >>> result.no_effect
        False
        >>> result.invalid_args
        []

        >>> @deprecated(target=True, deprecated_in="1.0", args_mapping={"arg": "arg"})
        ... def identity_func(arg: int) -> int:
        ...     return arg
        >>>
        >>> # Identity mapping with self-deprecation - no effect
        >>> result = validate_deprecated_callable(identity_func)
        >>> result.identity_mapping
        ['arg']
        >>> result.no_effect
        True

    Note:
        Use this function during development or in CI to ensure deprecation
        decorators are configured meaningfully. Invalid configurations won't
        cause runtime errors but will silently have no effect.

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

    invalid_args: list[str] = []
    empty_mapping = not args_mapping
    identity_mapping: list[str] = []
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
    )


def check_deprecation_expiry(func: Callable, current_version: str) -> None:
    """Check if a deprecated callable has passed its scheduled removal version.

    This enforcement tool verifies that deprecated code is actually removed when
    it reaches its scheduled removal deadline. It prevents "zombie code" - deprecated
    functions that remain in the codebase past their intended removal date.

    The function validates that the callable is properly decorated, extracts the
    removal version from its metadata, and compares it against the current version
    using semantic versioning. If the current version is greater than or equal to
    the scheduled removal version, it raises an AssertionError indicating the code
    must be deleted.

    Args:
        func: The deprecated callable to check. Must have a ``__deprecated__``
            attribute set by the @deprecated decorator.
        current_version: The current version of the package (e.g., "2.0.0").
            Should follow semantic versioning conventions.

    Raises:
        ValueError: If the function does not have a ``__deprecated__`` attribute
            (i.e., was not decorated with @deprecated).
        ValueError: If the ``remove_in`` field is missing from the deprecation metadata.
        AssertionError: If the current version is greater than or equal to the
            scheduled removal version, indicating the code should have been removed.

    Example:
        >>> from deprecate import deprecated, check_deprecation_expiry
        >>> def new_func(x: int) -> int:
        ...     return x * 2
        >>>
        >>> @deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
        ... def old_func(x: int) -> int:
        ...     pass
        >>>
        >>> # This passes - current version is before removal deadline
        >>> check_deprecation_expiry(old_func, "1.5.0")
        >>>
        >>> # This raises AssertionError - past removal deadline
        >>> try:
        ...     check_deprecation_expiry(old_func, "2.0.0")
        ... except AssertionError as e:
        ...     print(str(e))
        Callable `old_func` was scheduled for removal in version 2.0 but still exists in version \
2.0.0. Please delete this deprecated code.

    .. note::
       - Uses semantic versioning comparison (e.g., "1.2.3" vs "2.0.0")
       - Intended for use in CI/CD pipelines or development checks
       - Helps maintain code hygiene by enforcing deprecation deadlines
       - Can be integrated into test suites or pre-commit hooks

    """
    # First validate that the function has proper deprecation metadata
    info = validate_deprecated_callable(func)

    # Extract the remove_in version from the metadata
    remove_in = info.deprecated_info.get("remove_in")
    if not remove_in:
        raise ValueError(
            f"Callable `{info.function}` does not have a 'remove_in' version specified in its deprecation metadata."
        )

    # Parse both versions for proper semantic version comparison
    try:
        current_ver = _parse_version(current_version)
        remove_ver = _parse_version(remove_in)
    except (ValueError, ImportError) as e:
        raise ValueError(
            f"Failed to parse versions for comparison. current_version='{current_version}', "
            f"remove_in='{remove_in}'. Error: {e}"
        ) from e

    # Check if the current version has reached or passed the removal deadline
    if current_ver >= remove_ver:
        raise AssertionError(
            f"Callable `{info.function}` was scheduled for removal in version {remove_in} "
            f"but still exists in version {current_version}. Please delete this deprecated code."
        )


def _get_package_version(package_name: str) -> str:
    """Auto-detect the installed version of a package.

    This private helper function attempts to retrieve the version of an installed
    package using importlib.metadata. This is useful for automatically detecting
    the current version of a package when checking deprecation expiry.

    Args:
        package_name: Name of the package to get the version for (e.g., "numpy", "mypackage").

    Returns:
        The version string of the installed package.

    Raises:
        ImportError: If the package is not installed or version cannot be determined.

    Example:
        >>> _get_package_version("deprecate")  # doctest: +SKIP
        '0.3.2'

    """
    try:
        import importlib.metadata

        return importlib.metadata.version(package_name)
    except Exception as e:
        raise ImportError(
            f"Could not determine version for package '{package_name}'. Ensure the package is installed. Error: {e}"
        ) from e


def check_module_deprecation_expiry(
    module: Union[Any, str],  # noqa: ANN401
    current_version: str | None = None,
    recursive: bool = True,
) -> list[str]:
    """Check all deprecated callables in a module/package for expired removal deadlines.

    This enforcement tool scans an entire module or package for deprecated functions
    and checks if any have passed their scheduled removal version. It's designed for
    CI/CD pipelines to automatically detect and report zombie code across a codebase.

    The function uses ``find_deprecated_callables`` to discover all deprecated functions,
    then applies ``check_deprecation_expiry`` to each one. Any callables that have
    reached or passed their removal deadline are collected and reported.

    Args:
        module: A Python module or package to scan. Can be:
            - Imported module object (e.g., ``import my_package; check_module_deprecation_expiry(my_package, "2.0")``)
            - String module path (e.g., ``check_module_deprecation_expiry("my_package.submodule", "2.0")``)
        current_version: The current version of your package to compare against removal deadlines
            (e.g., ``"2.0.0"``). If None, attempts to auto-detect the version using the package name
            from the module path (e.g., ``"mypackage"`` extracts ``mypackage`` as package name).
        recursive: If True (default), recursively scan submodules. If False, only
            scan the top-level module.

    Returns:
        List of error messages for callables that have expired (past their removal deadline).
        Empty list if all deprecated callables are still within their deprecation period.

    Example:
        >>> # Check a specific module with version before any deadlines
        >>> from deprecate import check_module_deprecation_expiry
        >>> expired = check_module_deprecation_expiry("tests.collection_deprecate", "0.1", recursive=False)
        >>> len(expired)
        0

        >>> # Check with version past some removal deadlines
        >>> expired = check_module_deprecation_expiry("tests.collection_deprecate", "0.5", recursive=False)
        >>> len(expired) > 0  # Some functions have remove_in="0.5"
        True

        >>> # Use in CI/CD pipeline (example pattern)
        >>> # from my_package import __version__
        >>> # expired = check_module_deprecation_expiry("my_package", __version__)
        >>> # assert not expired, f"Remove expired deprecated code: {expired}"

        >>> # Auto-detect version (extracts package name from module path)
        >>> # expired = check_module_deprecation_expiry("mypackage")  # auto-detects version
        >>> # assert not expired, f"Remove expired deprecated code: {expired}"

    .. note::
       - Skips callables without a ``remove_in`` field (warnings only, no removal deadline)
       - Skips callables that cannot be imported or accessed
       - Silently skips callables with invalid ``remove_in`` version formats
       - Uses semantic versioning comparison (e.g., "1.2.3" vs "2.0.0")
       - Intended for automated checks in CI/CD pipelines
       - Can be integrated into test suites or pre-commit hooks

    """
    import importlib

    # Determine module name for auto-version detection
    module_name = module if isinstance(module, str) else getattr(module, "__name__", None)

    # Auto-detect version if not provided
    if current_version is None:
        if not module_name:
            raise ValueError(
                "Cannot auto-detect version: module object has no __name__ attribute. "
                "Please provide current_version explicitly."
            )
        # Extract package name (first component of module path)
        package_name = module_name.split(".")[0]
        current_version = _get_package_version(package_name)

    # Validate current_version format upfront to provide fail-fast feedback
    try:
        _parse_version(current_version)
    except (ValueError, ImportError) as e:
        raise ValueError(f"Invalid current_version '{current_version}'. Error: {e}") from e

    # Handle string module path
    if isinstance(module, str):
        module = importlib.import_module(module)

    # Find all deprecated callables in the module
    deprecated_callables = find_deprecated_callables(module, recursive=recursive)

    expired_callables = []

    # Check each deprecated callable for expiry
    for info in deprecated_callables:
        # Skip if no remove_in specified (warning-only deprecation)
        remove_in = info.deprecated_info.get("remove_in")
        if not remove_in:
            continue

        # Try to get the actual callable object and check its expiry
        try:
            # Need to get the actual callable object from the module
            # The info has module and function name, need to retrieve the callable
            mod = importlib.import_module(info.module)
            func = getattr(mod, info.function)
        except (ImportError, AttributeError):
            # If we can't import/access the function, skip it
            continue

        # Now check if this callable has expired
        try:
            check_deprecation_expiry(func, current_version)
        except AssertionError as e:
            # This callable has expired - add to list
            expired_callables.append(str(e))
        except ValueError:
            # Version parsing failed for remove_in (not current_version, which was validated upfront)
            # This can happen if the callable's remove_in has an invalid version format
            # Silently skip this callable
            continue

    return expired_callables


def find_deprecated_callables(
    module: Union[Any, str],  # noqa: ANN401
    recursive: bool = True,
) -> list[DeprecatedCallableInfo]:
    """Scan a module or package for deprecated wrappers and validate them.

    This is a development/CI tool to scan a codebase for all functions decorated
    with @deprecated and validate that each wrapper configuration is meaningful.
    Returns comprehensive information about each deprecated function including
    validation results that help identify misconfigured wrappers.

    Args:
        module: A Python module or package to scan for deprecated decorators.
            Can be:
            - Imported module object (e.g., ``import my_package; find_deprecated_callables(my_package)``)
            - String module path (e.g., ``find_deprecated_callables("my_package.submodule")``)
        recursive: If True (default), recursively scan submodules. If False, only
            scan the top-level module.

    Returns:
        List of DeprecatedCallableInfo dataclasses, one per deprecated function found.
        Each contains:
            - module: Module name where the function is defined
            - function: Function name
            - deprecated_info: The __deprecated__ attribute dict from the decorator
            - invalid_args: List of args_mapping keys not in function signature
            - empty_mapping: True if args_mapping is None or empty
            - identity_mapping: List of identity mappings (key == value)
            - self_reference: True if target points to same function
            - no_effect: True if wrapper has zero impact

    Example:
        >>> from deprecate import find_deprecated_callables
        >>> from tests import collection_deprecate as my_package
        >>>
        >>> results = find_deprecated_callables(my_package)
        >>> len(results) > 0  # Should find deprecated functions
        True
        >>> # Also works with string module paths
        >>> results = find_deprecated_callables("tests.collection_deprecate")
        >>> len(results) > 0
        True

        >>> # Filter to find only problematic wrappers
        >>> problematic = [r for r in results if r.invalid_args or r.no_effect]
        >>> len(problematic) >= 0  # May or may not have problematic ones
        True

    Note:
        - Requires that the module be importable
        - Inspects the ``__deprecated__`` attribute set by the @deprecated decorator
        - Skips private/magic attributes and imports from other modules
        - Handles import errors gracefully (warnings are suppressed)

    """
    import importlib
    import pkgutil

    results: list[DeprecatedCallableInfo] = []

    # Handle string module path
    if isinstance(module, str):
        module = importlib.import_module(module)

    def _scan_module(mod: Any) -> None:  # noqa: ANN401
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
                info = replace(info, module=mod.__name__ if hasattr(mod, "__name__") else str(mod), function=name)

                results.append(info)

    # Scan the main module
    _scan_module(module)

    # Recursively scan submodules if requested
    if recursive and hasattr(module, "__path__"):
        try:
            packages = list(
                pkgutil.walk_packages(path=module.__path__, prefix=module.__name__ + ".", onerror=lambda x: None)
            )
        except (OSError, ImportError):
            packages = []

        for _importer, modname, _ispkg in packages:
            with suppress(ImportError, ModuleNotFoundError):
                submod = importlib.import_module(modname)
                _scan_module(submod)

    return results
