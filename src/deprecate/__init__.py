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
    - :func:`~deprecate.deprecation.deprecated`: Decorator for marking functions/methods as deprecated
    - :func:`~deprecate.utils.void`: Silences IDE and mypy warnings about unused parameters in deprecated stubs

**Audit** (:mod:`deprecate.audit`):
    - :func:`~deprecate.audit.validate_deprecation_wrapper`: Validate a single wrapper's configuration
    - :func:`~deprecate.audit.find_deprecation_wrappers`: Scan a package for all deprecated wrappers
    - :func:`~deprecate.audit.generate_deprecation_markdown`: Build a markdown deprecation matrix
    - :func:`~deprecate.audit.generate_deprecation_timeline`: Build a Mermaid timeline from wrapper metadata
    - :func:`~deprecate.audit.validate_deprecation_expiry`: Detect wrappers that outlived their ``remove_in`` deadline
    - :func:`~deprecate.audit.validate_deprecation_chains`: Detect deprecated wrappers chaining to
      other deprecated wrappers
    - :class:`~deprecate.audit.DeprecationWrapperInfo`: Structured result returned by the audit functions
    - :class:`~deprecate.audit.ChainType`: Enum describing the kind of deprecation chain detected

**Proxy** (:mod:`deprecate.proxy`):
    - :func:`~deprecate.proxy.deprecated_instance`: Wrap any object with deprecation warnings
    - :func:`~deprecate.proxy.deprecated_class`: Decorator for deprecating Enum/dataclass definitions

**Testing** (:mod:`deprecate.utils`):
    - :func:`~deprecate.utils.assert_no_warnings`: Context manager asserting that no warnings are raised

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
    DeprecatedCallableInfo,  # noqa: F401 # backward-compat alias for DeprecatedWrapperInfo
    DeprecationWrapperInfo,
    find_deprecated_callables,  # noqa: F401 # deprecated since 0.6, use find_deprecation_wrappers
    find_deprecation_wrappers,
    generate_deprecation_markdown,
    generate_deprecation_timeline,
    validate_deprecated_callable,  # noqa: F401 # deprecated since 0.6, use validate_deprecation_wrapper
    validate_deprecation_chains,
    validate_deprecation_expiry,
    validate_deprecation_wrapper,
)
from deprecate.deprecation import deprecated
from deprecate.proxy import deprecated_class, deprecated_instance
from deprecate.utils import (
    assert_no_warnings,
    no_warning_call,  # noqa: F401 # deprecated since 0.6, use assert_no_warnings
    void,
)

__all__ = [
    "deprecated",
    "DeprecationWrapperInfo",
    "deprecated_class",
    "deprecated_instance",
    "find_deprecation_wrappers",
    "generate_deprecation_markdown",
    "generate_deprecation_timeline",
    "validate_deprecation_wrapper",
    "validate_deprecation_chains",
    "validate_deprecation_expiry",
    "assert_no_warnings",
    "void",
]
