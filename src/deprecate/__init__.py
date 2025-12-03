"""Deprecation tooling for Python functions and classes.

This package provides a simple decorator-based approach to deprecate functions,
methods, and classes while automatically forwarding calls to their replacements.

Main Features:
    - Automatic call forwarding to new implementations
    - Argument mapping and renaming
    - Customizable warning messages and streams
    - Per-function and per-argument warning tracking
    - Support for multiple deprecation levels
    - Testing utilities for deprecated code

Quick Example:
    >>> from deprecate import deprecated
    >>>
    >>> def new_function(x: int) -> int:
    ...     return x * 2
    >>>
    >>> @deprecated(target=new_function, deprecated_in="1.0", remove_in="2.0")
    ... def old_function(x: int) -> int:
    ...     pass  # Calls forwarded to new_function
    >>>
    >>> result = old_function(5)  # Shows warning, returns 10

Exported Functions:
    deprecated: Main decorator for marking functions/classes as deprecated
    void: Helper function to silence IDE warnings about unused parameters
    validate_deprecated_callable: Development tool to validate wrapper configuration
    find_deprecated_callables: Scan a package for deprecated wrappers and validate them

Exported Classes:
    ValidationResult: Dataclass for validation results from validate_deprecated_callable
    DeprecatedCallableInfo: Dataclass for deprecated callable info from find_deprecated_callables

For detailed examples and use cases, see: https://borda.github.io/pyDeprecate
"""

from deprecate.__about__ import *  # noqa: F403
from deprecate.deprecation import deprecated  # noqa: E402, F401
from deprecate.utils import (  # noqa: E402, F401
    DeprecatedCallableInfo,
    ValidationResult,
    find_deprecated_callables,
    validate_deprecated_callable,
    void,
)
