"""Deprecation tooling for Python functions and classes.

This package provides a simple decorator-based approach to deprecate functions,
methods, and classes while automatically forwarding calls to their replacements.

Main Features:
    - Automatic call forwarding to new implementations
    - Argument mapping and renaming between old and new APIs
    - Customizable warning messages and output streams
    - Per-function and per-argument warning tracking to prevent log spam
    - Support for multiple deprecation levels via decorator stacking
    - Audit tools for CI pipelines: wrapper validation, expiry enforcement, chain detection
    - Testing utilities for writing deterministic tests

Core Components:

**Main Decorator** (:mod:`deprecate.deprecation`):
    - :func:`~deprecate.deprecation.deprecated`: Decorator for marking functions/classes as deprecated
    - :func:`~deprecate.utils.void`: Silences IDE and mypy warnings about unused parameters in deprecated stubs

**Audit** (:mod:`deprecate.audit`):
    - :func:`~deprecate.audit.validate_deprecated_callable`: Validate a single wrapper's configuration
    - :func:`~deprecate.audit.find_deprecated_callables`: Scan a package for all deprecated wrappers
    - :func:`~deprecate.audit.validate_deprecation_expiry`: Detect wrappers that outlived their ``remove_in`` deadline
    - :func:`~deprecate.audit.validate_deprecation_chains`: Detect deprecated functions chaining to
      other deprecated functions
    - :class:`~deprecate.audit.DeprecatedCallableInfo`: Structured result returned by the audit functions
    - :class:`~deprecate.audit.ChainType`: Enum describing the kind of deprecation chain detected

**Testing** (:mod:`deprecate.utils`):
    - :func:`~deprecate.utils.no_warning_call`: Context manager asserting that no warnings are raised

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
    - Auditing: wrapper configuration, expiry enforcement, chain detection
    - Testing deprecated code

"""

from deprecate.__about__ import *  # noqa: F403
from deprecate.audit import (
    DeprecatedCallableInfo,
    find_deprecated_callables,
    validate_deprecated_callable,
    validate_deprecation_chains,
    validate_deprecation_expiry,
)
from deprecate.deprecation import deprecated
from deprecate.structs import DeprecatedStruct, deprecated_instance
from deprecate.utils import no_warning_call, void

__all__ = [
    "deprecated",
    "DeprecatedCallableInfo",
    "find_deprecated_callables",
    "validate_deprecated_callable",
    "validate_deprecation_chains",
    "validate_deprecation_expiry",
    "no_warning_call",
    "void",
    "DeprecatedStruct",
    "deprecated_instance",
]
