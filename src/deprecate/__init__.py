"""Deprecation tooling for Python functions and classes.

This package provides a simple decorator-based approach to deprecate functions,
methods, and classes while automatically forwarding calls to their replacements.

Main Features:
    - Automatic call forwarding to new implementations
    - Argument mapping and renaming between old and new APIs
    - Customizable warning messages and output streams
    - Per-function and per-argument warning tracking to prevent log spam
    - Support for multiple deprecation levels via decorator stacking
    - Testing utilities for writing deterministic tests
    - Package scanning for deprecated wrapper validation

Core Components:

**Main Decorator:**
    - :func:`deprecated`: Decorator for marking functions/classes as deprecated

**Utilities:**
    - :func:`void`: Helper to silence IDE warnings about unused parameters
    - :func:`validate_deprecated_callable`: Validate single wrapper configuration
    - :func:`check_deprecation_expiry`: Check if deprecated code has passed removal deadline
    - :func:`check_module_deprecation_expiry`: Check all deprecated code in a module for expired deadlines
    - :func:`find_deprecated_callables`: Scan package for deprecated wrappers
    - :func:`no_warning_call`: Context manager for testing without warnings

**Data Classes:**
    - :class:`DeprecatedCallableInfo`: Validation results for deprecated callables

Quick Example:
    >>> from deprecate import deprecated
    >>>
    >>> def new_function(x: int) -> int:
    ...     '''New implementation.'''
    ...     return x * 2
    >>>
    >>> @deprecated(target=new_function, deprecated_in="1.0", remove_in="2.0")
    ... def old_function(x: int) -> int:
    ...     '''Old implementation - calls forwarded automatically.'''
    ...     pass
    >>>
    >>> result = old_function(5)  # Shows warning, returns 10
    >>> print(result)
    10

Complete Documentation:
    For detailed examples and use cases, see:
    https://borda.github.io/pyDeprecate

    Topics covered:
    - Simple function forwarding
    - Advanced argument mapping
    - Warning-only deprecation (target=None)
    - Self-deprecation (target=True)
    - Multiple deprecation levels
    - Conditional skip (skip_if parameter)
    - Class deprecation
    - Automatic docstring updates
    - Testing deprecated code
    - Validating wrapper configuration

"""

from deprecate.__about__ import *  # noqa: F403
from deprecate.deprecation import deprecated  # noqa: E402, F401
from deprecate.utils import (  # noqa: E402, F401
    DeprecatedCallableInfo,
    check_deprecation_expiry,
    check_module_deprecation_expiry,
    find_deprecated_callables,
    no_warning_call,
    validate_deprecated_callable,
    void,
)
