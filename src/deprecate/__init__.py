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
    - :func:`~deprecate.deprecation.deprecated_callable`: Strict callable-only form of ``deprecated`` that
      raises :class:`TypeError` when applied to a class
    - :func:`~deprecate.utils.void`: Silences IDE and mypy warnings about unused parameters in deprecated stubs

**Fatal deprecations** (:mod:`deprecate._fatal`):
    - :class:`~deprecate._fatal.DeprecatedError`: Raised instead of a warning when a deprecation is fatal
    - :func:`~deprecate._fatal.as_exceptions`: Context manager and decorator that makes deprecations fatal for one
      block of code in the current thread or task and restores the previous setting on exit

**Audit** (:mod:`deprecate.audit`, loaded on first access to ``deprecate.audit`` or any name below, so a plain
``import deprecate`` stays light; ``from deprecate import *`` loads it):
    - :func:`~deprecate.audit.validate_deprecation_wrapper`: Validate a single wrapper's configuration
    - :func:`~deprecate.audit.find_deprecation_wrappers`: Scan a package for all deprecated wrappers
    - :func:`~deprecate.audit.generate_deprecation_table`: Build compact or matrix markdown deprecation tables
    - :func:`~deprecate.audit.validate_deprecation_expiry`: Detect wrappers that outlived their ``remove_in`` deadline
    - :func:`~deprecate.audit.validate_deprecation_policy`: Check wrappers against deprecation-governance rules
      (grace window, removal cadence, migration guidance)
    - :func:`~deprecate.audit.validate_deprecation_chains`: Detect deprecated wrappers chaining to
      other deprecated wrappers
    - :class:`~deprecate.audit.DeprecationWrapperInfo`: Structured result returned by the audit functions
    - :class:`~deprecate.audit.ChainType`: Enum describing the kind of deprecation chain detected
    - :class:`~deprecate.audit.GraceWindow`: Strict form of a policy ``min_grace`` window — ``count`` steps of one
      version component — accepted directly by :func:`~deprecate.audit.validate_deprecation_policy`
    - :class:`~deprecate.audit.VersionBump`: Enum naming the version component (major, minor, patch) a
      :class:`~deprecate.audit.GraceWindow` is counted in
    - :func:`~deprecate._types.get_deprecation_config`: Read a wrapper's :class:`~deprecate._types.DeprecationConfig`
      metadata — the supported external read path since ``__deprecated__`` became a plain PEP 702-conformant
      message string in ``v0.13``

**Proxy** (:mod:`deprecate.proxy`):
    - :func:`~deprecate.proxy.deprecated_instance`: Wrap any object with deprecation warnings
    - :func:`~deprecate.proxy.deprecated_class`: Decorator for deprecating Enum/dataclass definitions
    - :func:`~deprecate.module.deprecated_module`: Module-level deprecation via PEP-562-inspired
      ``__getattribute__`` interception

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
    - Warning-only deprecation (``TargetMode.NOTIFY``)
    - Self-deprecation (``TargetMode.ARGS_REMAP``)
    - Multiple deprecation levels
    - Conditional skip (skip_if parameter)
    - Class deprecation
    - Automatic docstring updates
    - Auditing: wrapper configuration, expiry enforcement, chain detection
    - Testing deprecated code

"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from deprecate.__about__ import *  # noqa: F403
from deprecate._fatal import DeprecatedError, _env_as_exceptions, as_exceptions

# Opt-in strict ``property`` replacement: ``from deprecate import property`` shadows the builtin
# in the importing module only, rejecting inner-order ``@property @deprecated`` at class-body time.
from deprecate._properties import (
    _StrictProperty as property,  # noqa: F401 # intentional: explicit-import only; excluded from __all__ to prevent star-import from silently enabling strict mode
)
from deprecate._types import DeprecationProxy, TargetMode, get_deprecation_config
from deprecate.deprecation import deprecated
from deprecate.module import deprecated_module
from deprecate.proxy import deprecated_class, deprecated_instance
from deprecate.routine import deprecated_callable
from deprecate.utils import (
    assert_no_warnings,
    no_warning_call,  # noqa: F401 # deprecated since 0.6, use assert_no_warnings
    void,
)

#: Process-wide default for ``as_exception``: when ``True``, every deprecation raises
#: :class:`~deprecate._fatal.DeprecatedError` instead of warning, including ``as_exception=False``. Seeded from
#: ``DEPRECATE_AS_EXCEPTIONS`` at import and re-read on every emission, so assigning to it at runtime (or in a
#: test) takes effect immediately.  An explicit ``as_exception=True`` on a wrapper is fatal regardless of this.
AS_EXCEPTIONS: bool = _env_as_exceptions()

__all__ = [
    "AS_EXCEPTIONS",
    "ChainType",
    "DeprecatedError",
    "DeprecationProxy",
    "DeprecationStatus",
    "DeprecationWrapperInfo",
    "GraceWindow",
    "PolicyRule",
    "TableStyle",
    "TargetMode",
    "VersionBump",
    "as_exceptions",
    "assert_no_warnings",
    "deprecated",
    "deprecated_callable",
    "deprecated_class",
    "deprecated_instance",
    "deprecated_module",
    "find_deprecation_wrappers",
    "generate_deprecation_table",
    "get_deprecation_config",
    "validate_deprecation_chains",
    "validate_deprecation_expiry",
    "validate_deprecation_policy",
    "validate_deprecation_wrapper",
    "validate_mapping_compatibility",
    "void",
]

if TYPE_CHECKING:
    from deprecate import audit  # noqa: F401 # lazily served by __getattr__
    from deprecate.audit import (
        ChainType,
        DeprecatedCallableInfo,  # noqa: F401 # backward-compat alias for DeprecationWrapperInfo
        DeprecationStatus,
        DeprecationWrapperInfo,
        GraceWindow,
        PolicyRule,
        TableStyle,
        VersionBump,
        find_deprecated_callables,  # noqa: F401 # deprecated since 0.6, use find_deprecation_wrappers
        find_deprecation_wrappers,
        generate_deprecation_table,
        validate_deprecated_callable,  # noqa: F401 # deprecated since 0.6, use validate_deprecation_wrapper
        validate_deprecation_chains,
        validate_deprecation_expiry,
        validate_deprecation_policy,
        validate_deprecation_wrapper,
        validate_mapping_compatibility,
    )

#: Names served from :mod:`deprecate.audit` on first access, so ``import deprecate`` does not pay for the audit
#: tooling (and its ``importlib.metadata`` import) in code that only decorates. The three legacy aliases
#: (``DeprecatedCallableInfo``, ``find_deprecated_callables``, ``validate_deprecated_callable``) are outside
#: ``__all__`` but stay reachable as attributes.
_AUDIT_EXPORTS: frozenset[str] = frozenset(
    {
        "ChainType",
        "DeprecatedCallableInfo",
        "DeprecationStatus",
        "DeprecationWrapperInfo",
        "GraceWindow",
        "PolicyRule",
        "TableStyle",
        "VersionBump",
        "find_deprecated_callables",
        "find_deprecation_wrappers",
        "generate_deprecation_table",
        "validate_deprecated_callable",
        "validate_deprecation_chains",
        "validate_deprecation_expiry",
        "validate_deprecation_policy",
        "validate_deprecation_wrapper",
        "validate_mapping_compatibility",
    }
)


def __getattr__(name: str) -> Any:  # noqa: ANN401
    """Load the audit tooling on first access to ``deprecate.audit`` or one of its re-exported names (PEP 562).

    The resolved object is cached in the module namespace, so later lookups skip this hook. ``from deprecate import *``
    resolves every ``__all__`` name and therefore loads the audit tooling too.

    """
    if name == "audit" or name in _AUDIT_EXPORTS:
        audit_module = import_module("deprecate.audit")
        value = audit_module if name == "audit" else getattr(audit_module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'deprecate' has no attribute {name!r}")


def __dir__() -> list[str]:
    """List the module namespace together with the lazily served audit names."""
    return sorted(set(globals()) | _AUDIT_EXPORTS | {"audit"})
