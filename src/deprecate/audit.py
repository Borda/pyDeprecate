"""Audit tools for deprecation lifecycle management.

This module provides three complementary utilities for verifying the health of
deprecated callables across a codebase. All three are designed to be called from
pytest or a CI script against an imported package.

**Wrapper configuration** (:func:`~deprecate.audit.validate_deprecation_wrapper`,
:func:`~deprecate.audit.find_deprecation_wrappers`):
    Detect wrappers that have zero impact — invalid ``args_mapping`` keys, identity
    mappings, empty mappings, or a ``target`` pointing back to the same wrapper.

**Expiry enforcement** (:func:`~deprecate.audit.validate_deprecation_expiry`):
    Detect wrappers whose ``remove_in`` version has been reached or passed, preventing
    zombie code from shipping past its scheduled removal deadline.

**Chain detection** (:func:`~deprecate.audit.validate_deprecation_chains`):
    Detect wrappers whose ``target`` is itself a deprecated callable, forming a chain
    that users traverse unnecessarily. Two chain kinds are reported via :class:`~deprecate.audit.ChainType`:
    ``TARGET`` (forwarding chain) and ``STACKED`` (composed argument mappings).

**Timeline/report generation** (:func:`~deprecate.audit.generate_deprecation_markdown`,
:func:`~deprecate.audit.generate_deprecation_timeline`):
    Generate markdown and Mermaid summaries directly from deprecated wrapper metadata,
    including nested deprecated methods and constructors defined on classes.

Results are returned as :class:`~deprecate.audit.DeprecationWrapperInfo` dataclasses, which carry both
identification info and structured validation results for programmatic processing.

.. note::
   :func:`~deprecate.audit.validate_deprecation_expiry` requires the ``packaging`` library for PEP 440
   version comparison. Install with: ``pip install pyDeprecate[audit]``

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>
"""

# Note: Proxy objects are discoverable via the generic ``callable(obj)`` +
# ``hasattr(obj, "__deprecated__")`` scan in :func:`find_deprecation_wrappers` and
# :func:`validate_deprecation_expiry`. The ``__deprecated__`` schema is now unified
# across ``@deprecated`` and :class:`~deprecate.proxy._DeprecatedProxy` via
# :class:`~deprecate._types.DeprecationConfig` — both always populate the ``name`` field,
# so ``validate_deprecation_wrapper`` can read it correctly for proxy objects too.

import inspect
from contextlib import suppress
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

if TYPE_CHECKING:
    from packaging.version import Version

from deprecate._types import DeprecationConfig, _has_deprecation_meta
from deprecate.proxy import _DeprecatedProxy, deprecated_class
from deprecate.utils import get_func_arguments_types_defaults


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
        >>> import importlib; importlib.import_module("packaging")  # doctest: +ELLIPSIS
        <module 'packaging' ...>
        >>> v1 = _parse_version("1.2.3")
        >>> v2 = _parse_version("2.0")
        >>> v1 < v2
        True
        >>> _parse_version("1.5.0a1") < _parse_version("1.5.0")
        True

    .. note::
       Install the audit extra to use version comparison features:
       ``pip install pyDeprecate[audit]``

    """
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError as err:
        raise ImportError(
            "Version comparison requires the 'packaging' library. Install with: pip install pyDeprecate[audit]"
        ) from err

    try:
        return Version(version_string)
    except InvalidVersion as err:
        raise ValueError(
            f"Failed to parse version '{version_string}'. Expected PEP 440 format "
            f"(e.g., '1.2.3', '2.0', '1.5.0a1'). Error: {err}"
        ) from err


class ChainType(Enum):
    """Type of deprecation chain detected by ``validate_deprecation_chains()``.

    Attributes:
        TARGET: The ``target`` argument is itself a callable decorated with ``@deprecated``
            (a forwarding chain). Fix by pointing directly to the final non-deprecated target.
        STACKED: Arg mappings chain and must be composed/collapsed. Two sub-cases:
            (a) Callable ``target`` is itself ``@deprecated(True, args_mapping=...)`` — the
            caller's mapping feeds into the target's self-renaming, so both hops must be
            collapsed into one. (b) Multiple ``@deprecated(True, args_mapping=...)`` decorators
            are stacked on the same function and should be merged into a single decorator.
    """

    TARGET = "target"
    STACKED = "stacked"


@dataclass(frozen=True)
class DeprecationWrapperInfo:
    """Information about a deprecated wrapper and its validation results.

    This dataclass represents a deprecated wrapper (a ``@deprecated``-decorated function
    or a ``deprecated_class()``/``deprecated_instance()`` proxy), containing both
    identification info and validation results from ``validate_deprecation_wrapper()``
    or ``find_deprecation_wrappers()``.

    Attributes:
        module: Module name where the wrapper is defined (empty for direct validation).
        function: Wrapper name.
        deprecated_info: The ``__deprecated__`` attribute from the decorator,
            as a :class:`~deprecate._types.DeprecationConfig`.
        invalid_args: List of ``args_mapping`` keys that don't exist in the wrapper's signature.
        empty_mapping: True if ``args_mapping`` is None or empty (no argument remapping).
        identity_mapping: List of args where key equals value (e.g., ``{'arg': 'arg'}``).
        self_reference: True if target points to the same wrapper.
        no_effect: True if wrapper has zero impact (combines all checks).
        chain_type: The kind of deprecation chain detected, or ``None`` if no chain.
            See :class:`~deprecate.audit.ChainType` for values (``TARGET`` or ``STACKED``).

    Example:
        >>> info = DeprecationWrapperInfo(
        ...     module="my_package.module",
        ...     function="old_function",
        ...     deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="2.0"),
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
    deprecated_info: DeprecationConfig = field(default_factory=DeprecationConfig)
    invalid_args: list[str] = field(default_factory=list)
    empty_mapping: bool = False
    identity_mapping: list[str] = field(default_factory=list)
    self_reference: bool = False
    no_effect: bool = False
    chain_type: Optional[ChainType] = None


def _getmembers_static_compat(obj: Any) -> list[tuple[str, Any]]:  # noqa: ANN401
    """Return members without triggering dynamic ``getattr`` side effects.

    Uses ``inspect.getmembers_static`` when available (Python 3.11+). For Python
    3.9/3.10 compatibility, falls back to ``dir()`` + ``inspect.getattr_static``.
    """
    getmembers_static = getattr(inspect, "getmembers_static", None)
    if callable(getmembers_static):
        return getmembers_static(obj)

    names = dir(obj)
    members: list[tuple[str, Any]] = []
    for name in names:
        with suppress(AttributeError):
            members.append((name, inspect.getattr_static(obj, name)))
    return sorted(members, key=lambda item: item[0])


def validate_deprecation_wrapper(func: Callable) -> DeprecationWrapperInfo:
    """Validate if a deprecated wrapper configuration is effective.

    This is a development tool to check if deprecated wrappers are configured correctly
    and will have the intended effect. It examines the ``__deprecated__`` attribute
    set by the ``@deprecated`` decorator and identifies configurations that would result
    in zero impact:

    - args_mapping keys that don't exist in the function's signature
    - Empty or None args_mapping (no argument remapping)
    - Identity mappings where key equals value (e.g., {'arg': 'arg'})
    - Target pointing to the same function (self-reference)
    - target=None with no args_mapping (just warns, no forwarding)

    Args:
        func: The decorated function to validate. Must have a ``__deprecated__``
            attribute set by the ``@deprecated`` decorator.

    Returns:
        DeprecationWrapperInfo: Dataclass with validation results:
            - function: Name of the wrapper being validated
            - deprecated_info: The typed :class:`~deprecate._types.DeprecationConfig`
              metadata from ``__deprecated__``
            - invalid_args: List of args_mapping keys not in wrapper signature
            - empty_mapping: True if args_mapping is None or empty
            - identity_mapping: List of args where key equals value (no effect)
            - self_reference: True if target is the same as the wrapper
            - no_effect: True if wrapper has zero impact (all checks combined)

    Raises:
        ValueError: If the wrapper has missing or invalid ``__deprecated__``
            metadata (expected :class:`~deprecate._types.DeprecationConfig`).

    Example:
        >>> from deprecate import deprecated, validate_deprecation_wrapper
        >>> def new_implementation(value: int) -> int:
        ...     return value * 2
        >>>
        >>> @deprecated(target=new_implementation, deprecated_in="1.0", args_mapping={"old_val": "value"})
        ... def old_func(old_val: int) -> int:
        ...     pass
        >>>
        >>> # Valid mapping to different function - has effect
        >>> result = validate_deprecation_wrapper(old_func)
        >>> result.no_effect
        False
        >>> result.invalid_args
        []

        >>> @deprecated(target=True, deprecated_in="1.0", args_mapping={"arg": "arg"})
        ... def identity_func(arg: int) -> int:
        ...     return arg
        >>>
        >>> # Identity mapping with self-deprecation - no effect
        >>> result = validate_deprecation_wrapper(identity_func)
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
    if not _has_deprecation_meta(func):
        raise ValueError(
            f"Function {getattr(func, '__name__', func)} has missing or invalid `__deprecated__` metadata. "
            "Expected `DeprecationConfig`; ensure it is decorated with `@deprecated`."
        )

    dep_info = func.__deprecated__
    args_mapping = dep_info.args_mapping
    target = dep_info.target

    invalid_args: list[str] = []
    empty_mapping = not args_mapping
    identity_mapping: list[str] = []
    self_reference = target is func if target is not None else False
    # chain_type distinguishes two chain problems:
    # - ChainType.TARGET: target is a deprecated callable that itself forwards to another function
    #   (i.e. target.__deprecated__.target is not True). Fix: point directly to the final target.
    # - ChainType.STACKED: arg mappings chain/compose and need collapsing. Two sub-cases:
    #   (a) target is a deprecated callable whose own target=True (self-deprecation with renaming).
    #   (b) target=True but __wrapped__ also has target=True (stacked @deprecated(True) decorators).
    chain_type: Optional[ChainType] = None
    if callable(target) and _has_deprecation_meta(target):
        chain_type = (
            ChainType.STACKED if target.__deprecated__.target is True else ChainType.TARGET
        )  # target is self-deprecation (mappings compose) or forwarding
    elif target is True:
        wrapped = getattr(func, "__wrapped__", None)
        if wrapped is not None and _has_deprecation_meta(wrapped) and wrapped.__deprecated__.target is True:
            chain_type = ChainType.STACKED  # stacked @deprecated(True) decorators

    all_identity = False
    if args_mapping:
        if isinstance(func, _DeprecatedProxy):
            invalid_args = []  # proxy __call__ is (*args, **kwargs); skip signature check
        else:
            func_args = [arg[0] for arg in get_func_arguments_types_defaults(func)]
            invalid_args = [arg for arg in args_mapping if arg not in func_args]
        identity_mapping = [arg for arg, val in args_mapping.items() if arg == val]
        # Check if ALL mappings are identity (complete no-op)
        all_identity = len(identity_mapping) == len(args_mapping) and len(args_mapping) > 0

    # Wrapper has no effect if it provides no call forwarding, arg mapping, or warning:
    # - Self-reference (forwards to itself — no meaningful forwarding)
    # - target is True (self-deprecation) AND (empty mapping OR all identity mappings)
    #   → no forwarding, no meaningful arg remapping
    # Note: target=None is NOT no_effect — it still emits deprecation warnings.
    # Note: When target is a different function, there's ALWAYS an effect (forwarding).
    is_self_deprecation = target is True or self_reference
    no_effect = self_reference or (is_self_deprecation and (empty_mapping or all_identity))

    function = dep_info.name or getattr(func, "__name__", str(func))

    return DeprecationWrapperInfo(
        function=function,
        deprecated_info=dep_info,
        invalid_args=invalid_args,
        empty_mapping=empty_mapping,
        identity_mapping=identity_mapping,
        self_reference=self_reference,
        no_effect=no_effect,
        chain_type=chain_type,
    )


def _check_deprecated_wrapper_expiry(func: Callable, current_version: str) -> None:
    """Check if a deprecated wrapper has passed its scheduled removal version.

    This is an internal helper function used by ``validate_deprecation_expiry()``.
    It verifies that deprecated code is actually removed when it reaches its
    scheduled removal deadline.

    The function validates that the wrapper is properly decorated, extracts the
    removal version from its metadata, and compares it against the current version
    using semantic versioning. If the current version is greater than or equal to
    the scheduled removal version, it raises an AssertionError indicating the code
    must be deleted.

    Args:
        func: The deprecated callable to check. Must have a ``__deprecated__``
            attribute set by the ``@deprecated`` decorator.
        current_version: The current version of the package (e.g., "2.0.0").
            Should follow PEP 440 versioning conventions.

    Raises:
        ValueError: If the wrapper has missing or invalid ``__deprecated__``
            metadata (expected :class:`~deprecate._types.DeprecationConfig`).
        ValueError: If the ``remove_in`` field is missing from the deprecation metadata.
        AssertionError: If the current version is greater than or equal to the
            scheduled removal version, indicating the code should have been removed.

    """
    # First validate that the wrapper has proper deprecation metadata
    info = validate_deprecation_wrapper(func)

    # Extract the remove_in version from the metadata
    remove_in = info.deprecated_info.remove_in
    if not remove_in:
        raise ValueError(
            f"Callable `{info.function}` does not have a 'remove_in' version specified in its deprecation metadata."
        )

    # Parse both versions for proper semantic version comparison
    # Let ImportError propagate with its helpful install message
    try:
        current_ver = _parse_version(current_version)
    except ValueError as err:
        raise ValueError(f"Invalid current_version '{current_version}': {err}") from err

    try:
        remove_ver = _parse_version(remove_in)
    except ValueError as err:
        raise ValueError(f"Invalid remove_in '{remove_in}' for callable `{info.function}`: {err}") from err

    # Check if the current version has reached or passed the removal deadline
    if current_ver >= remove_ver:
        raise AssertionError(
            f"Callable `{info.function}` was scheduled for removal in version {remove_in} "
            f"but still exists in version {current_version}. Please delete this deprecated code."
        )


def _get_package_version(package_name: str) -> str:
    """Auto-detect the installed version of a package.

    This private helper function attempts to retrieve the version of an installed
    package using importlib.metadata, with a fallback to checking the package's
    ``__version__`` attribute. This is useful for automatically detecting
    the current version of a package when checking deprecation expiry.

    Args:
        package_name: Name of the package to get the version for (e.g., "numpy", "mypackage").

    Returns:
        The version string of the installed package.

    Raises:
        ImportError: If the package is not installed or version cannot be determined.

    """
    import importlib.metadata

    # Try importlib.metadata first (standard approach for installed packages)
    with suppress(Exception):
        return importlib.metadata.version(package_name)

    # Fall back to checking __version__ attribute
    with suppress(Exception):
        import importlib as _importlib

        module = _importlib.import_module(package_name)
        if hasattr(module, "__version__"):
            return module.__version__

    # If both methods fail, raise an informative error
    raise ImportError(
        f"Could not determine version for package '{package_name}'. "
        f"Ensure the package is installed and has version metadata."
    )


def validate_deprecation_expiry(
    module: Union[Any, str],  # noqa: ANN401
    current_version: Optional[str] = None,
    recursive: bool = True,
) -> list[str]:
    """Check all deprecated callables in a module/package for expired removal deadlines.

    This enforcement tool scans an entire module or package for deprecated functions
    and checks if any have passed their scheduled removal version. It's designed for
    CI/CD pipelines to automatically detect and report zombie code across a codebase.

    The function uses ``find_deprecation_wrappers`` to discover all deprecated wrappers,
    then checks each one against the current version. Any wrappers that have
    reached or passed their removal deadline are collected and reported.

    Args:
        module: A Python module or package to scan. Can be:
            - Imported module object (e.g., ``import my_package; validate_deprecation_expiry(my_package, "2.0")``)
            - String module path (e.g., ``validate_deprecation_expiry("my_package.submodule", "2.0")``)
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
        >>> from deprecate import validate_deprecation_expiry
        >>> expired = validate_deprecation_expiry("tests.collection_deprecate", "0.1", recursive=False)
        >>> len(expired)
        0

        >>> # Check with version past some removal deadlines
        >>> expired = validate_deprecation_expiry("tests.collection_deprecate", "0.5", recursive=False)
        >>> print(len(expired))  # Some functions have remove_in="0.5"
        28

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

    # Validate and parse current_version once upfront to provide fail-fast feedback
    # and avoid repeated parsing. Let ImportError propagate with install hint.
    try:
        current_ver = _parse_version(current_version)
    except ValueError as err:
        raise ValueError(f"Invalid current_version '{current_version}': {err}") from err

    # Handle string module path
    if isinstance(module, str):
        module = importlib.import_module(module)

    # Find all deprecated wrappers in the module
    deprecated_callables = find_deprecation_wrappers(module, recursive=recursive)

    expired_callables = []

    # Check each deprecated callable for expiry
    for info in deprecated_callables:
        # Skip if no remove_in specified (warning-only deprecation)
        remove_in = info.deprecated_info.remove_in
        if not remove_in:
            continue

        # Parse remove_in version and compare with pre-parsed current_version
        try:
            remove_ver = _parse_version(remove_in)
        except ValueError:
            # Version parsing failed for remove_in
            # Silently skip this callable - it has invalid version format
            continue

        # Check if the current version has reached or passed the removal deadline
        if current_ver >= remove_ver:
            expired_callables.append(
                f"Callable `{info.function}` was scheduled for removal in version {remove_in}"
                f" but still exists in version {current_version}. Please delete this deprecated code."
            )

    return expired_callables


def find_deprecation_wrappers(
    module: Union[Any, str],  # noqa: ANN401
    recursive: bool = True,
    include_members: bool = False,
) -> list[DeprecationWrapperInfo]:
    """Scan a module or package for deprecated wrappers and validate them.

    This is a development/CI tool to scan a codebase for all wrappers created with
    ``@deprecated``, ``deprecated_class()``, or ``deprecated_instance()`` and validate
    that each wrapper configuration is meaningful.
    Returns comprehensive information about each deprecated wrapper including
    validation results that help identify misconfigured wrappers.

    Args:
        module: A Python module or package to scan for deprecated wrappers.
            Can be:
            - Imported module object (e.g., ``import my_package; find_deprecation_wrappers(my_package)``)
            - String module path (e.g., ``find_deprecation_wrappers("my_package.submodule")``)
        recursive: If True (default), recursively scan submodules. If False, only
            scan the top-level module.
        include_members: If True, also scan deprecated methods and constructors
            defined on classes declared in the scanned module(s).

    Returns:
        List of DeprecationWrapperInfo dataclasses, one per deprecated wrapper found.
        Each contains:
            - module: Module name where the wrapper is defined
            - function: Wrapper name
            - deprecated_info: DeprecationConfig metadata from the decorator (``__deprecated__`` attribute)
            - invalid_args: List of args_mapping keys not in wrapper signature
            - empty_mapping: True if args_mapping is None or empty
            - identity_mapping: List of identity mappings (key == value)
            - self_reference: True if target points to same wrapper
            - no_effect: True if wrapper has zero impact

    Example:
        >>> from deprecate import find_deprecation_wrappers
        >>> from tests import collection_deprecate as my_package
        >>>
        >>> results = find_deprecation_wrappers(my_package)
        >>> print(len(results) > 0)  # Should find deprecated wrappers
        True
        >>> # Also works with string module paths
        >>> results = find_deprecation_wrappers("tests.collection_deprecate")
        >>> print(len(results) > 0)
        True

        >>> # Filter to find only problematic wrappers
        >>> problematic = [r for r in results if r.invalid_args or r.no_effect]
        >>> print(len(results) > 0)  # May or may not have problematic ones
        True

    Note:
        - Requires that the module be importable
        - Inspects the ``__deprecated__`` attribute set by the @deprecated decorator
        - Skips private/magic attributes and imports from other modules
        - Uses static member inspection to avoid scan-time side effects from dynamic attribute access
        - Set ``include_members=True`` to include deprecated class members in addition
          to top-level wrappers

    """
    import importlib
    import pkgutil

    results: list[DeprecationWrapperInfo] = []
    seen: set[tuple[str, str]] = set()

    # Handle string module path
    if isinstance(module, str):
        module = importlib.import_module(module)

    def _record_wrapper(mod: Any, name: str, obj: Any, *, prefix: str = "") -> None:  # noqa: ANN401
        """Validate and append a discovered wrapper while deduplicating report keys."""
        if not callable(obj) or not hasattr(obj, "__deprecated__"):
            return

        module_name = mod.__name__ if hasattr(mod, "__name__") else str(mod)
        function_name = f"{prefix}{name}" if prefix else name
        key = (module_name, function_name)
        if key in seen:
            return

        info = validate_deprecation_wrapper(obj)
        results.append(replace(info, module=module_name, function=function_name))
        seen.add(key)

    def _scan_class(mod: Any, cls: type, *, prefix: str) -> None:  # noqa: ANN401
        """Scan deprecated methods/constructors declared on a class and nested classes."""
        for name, obj in vars(cls).items():
            # Include ``__init__`` because constructor deprecations are a primary
            # migration path, but skip other dunder/private members to keep the
            # public scan focused on user-facing deprecated APIs.
            if name.startswith("_") and name != "__init__":
                continue

            _record_wrapper(mod, name, obj, prefix=prefix)

            if inspect.isclass(obj) and getattr(obj, "__module__", None) == getattr(mod, "__name__", None):
                _scan_class(mod, obj, prefix=f"{prefix}{obj.__name__}.")

    def _scan_module(mod: Any) -> None:  # noqa: ANN401
        """Scan a single module for deprecated functions."""
        try:
            # Static inspection avoids dynamic getattr/descriptor evaluation while scanning.
            members = _getmembers_static_compat(mod)
        except (AttributeError, TypeError, ImportError):
            return

        for name, obj in members:
            # Skip private/magic attributes and imports from other modules
            if name.startswith("_"):
                continue

            _record_wrapper(mod, name, obj)

            if (
                include_members
                and inspect.isclass(obj)
                and getattr(obj, "__module__", None) == getattr(mod, "__name__", None)
            ):
                _scan_class(mod, obj, prefix=f"{name}.")

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


def validate_deprecation_chains(
    module: Union[Any, str],  # noqa: ANN401
    recursive: bool = True,
) -> list[DeprecationWrapperInfo]:
    """Validate that deprecated functions don't form chains with other deprecated code.

    This is a developer utility that scans a module or package for deprecated
    functions that form chains in two ways:

    1. **TARGET chains**: The ``target`` argument points to another deprecated
       callable instead of the final non-deprecated implementation.
    2. **STACKED chains**: Multiple ``@deprecated(True, ...)`` decorators are
       stacked on the same function with argument mappings that should be
       collapsed, or a callable ``target`` is itself a self-deprecation
       (``target=True``) requiring mapping composition.

    Both types are wasteful: wrappers should point directly to the final
    (non-deprecated) implementation with composed argument mappings.

    Detection is based purely on decorator metadata (``__deprecated__``
    attributes) — no source-code or AST inspection is performed.

    Args:
        module: A Python module or package to scan for deprecation chains.
            Can be:
            - Imported module object (e.g., ``import my_package; validate_deprecation_chains(my_package)``)
            - String module path (e.g., ``validate_deprecation_chains("my_package.submodule")``)
        recursive: If True (default), recursively scan submodules. If False, only
            scan the top-level module.

    Returns:
        List of :class:`~deprecate.audit.DeprecationWrapperInfo` where ``chain_type`` is not ``None``,
        i.e. every deprecated wrapper that forms a chain (``ChainType.TARGET`` or
        ``ChainType.STACKED``).

    Example:
        >>> from deprecate import validate_deprecation_chains
        >>> import tests.collection_chains as test_module
        >>>
        >>> issues = validate_deprecation_chains(test_module, recursive=False)
        >>> len(issues) > 0  # Should find chains
        True

    Note:
        - Only flags callees using the pyDeprecate ``@deprecated`` decorator
        - Uses :func:`~deprecate.audit.find_deprecation_wrappers` and inspects ``chain_type`` to detect chains

    """
    return [info for info in find_deprecation_wrappers(module, recursive=recursive) if info.chain_type is not None]


def _resolve_report_version(
    module: Union[Any, str],  # noqa: ANN401
    current_version: Optional[str],
) -> tuple[Optional[str], Optional["Version"]]:
    """Resolve the current version for reporting, falling back to unknown status.

    Args:
        module: Imported module/package object or string module path to scan.
        current_version: Optional explicit current version supplied by the caller.

    Returns:
        A ``(version_string, parsed_version)`` tuple. When version resolution or
        parsing is unavailable for reporting, the parsed value is ``None`` so the
        caller can label status as unknown without failing the report.
    """
    module_name = module if isinstance(module, str) else getattr(module, "__name__", None)

    if current_version is None and module_name:
        with suppress(ImportError):
            package_name = module_name.split(".")[0]
            current_version = _get_package_version(package_name)

    if current_version is None:
        return None, None

    try:
        return current_version, _parse_version(current_version)
    except ImportError:
        return current_version, None
    except ValueError as err:
        raise ValueError(f"Invalid current_version '{current_version}': {err}") from err


def _safe_parse_report_version(version: Optional[str]) -> Optional["Version"]:
    """Parse a version string for reporting without failing the entire report.

    Args:
        version: Optional version string from deprecation metadata.

    Returns:
        Parsed ``Version`` when available and valid, otherwise ``None``.
    """
    if not version:
        return None

    with suppress(ImportError, ValueError):
        return _parse_version(version)
    return None


def _format_report_symbol(info: DeprecationWrapperInfo) -> str:
    """Return a stable, fully-qualified symbol label for reports.

    Args:
        info: Deprecated wrapper metadata for a discovered symbol.

    Returns:
        ``module.function`` when module context is available, otherwise just the
        function/symbol name.
    """
    return f"{info.module}.{info.function}" if info.module else info.function


def _format_report_version(version: Optional[str], *, missing: str = "—") -> str:
    """Format a version value for report output.

    Args:
        version: Optional raw version string to display.
        missing: Placeholder used when the version is not set.

    Returns:
        The version prefixed with ``v`` or the provided placeholder.
    """
    if not version:
        return missing
    return f"v{version}"


def _get_report_status(info: DeprecationWrapperInfo, current_version: Optional["Version"]) -> str:
    """Return a human-readable lifecycle status for a deprecated wrapper.

    Args:
        info: Deprecated wrapper metadata for one symbol.
        current_version: Parsed current package version, if available.

    Returns:
        One of the report status labels describing whether the wrapper is
        scheduled, active, past its removal date, open-ended, or unknown.
    """
    if current_version is None:
        return "ℹ️ No Removal Target" if not info.deprecated_info.remove_in else "⚪ Status Unknown"

    deprecated_in = _safe_parse_report_version(info.deprecated_info.deprecated_in)
    if deprecated_in is not None and current_version < deprecated_in:
        return "🕒 Scheduled Deprecation"

    remove_in = info.deprecated_info.remove_in
    if not remove_in:
        return "ℹ️ No Removal Target"

    remove_version = _safe_parse_report_version(remove_in)
    if remove_version is None:
        return "⚪ Invalid Removal Target"

    if current_version >= remove_version:
        return "❌ Past Removal Date"

    return "⚠️ Active Warning"


def _get_report_section(info: DeprecationWrapperInfo) -> str:
    """Derive a readable Mermaid section label for a deprecated symbol.

    Args:
        info: Deprecated wrapper metadata for one symbol.

    Returns:
        The owning class name for deprecated members or the module basename for
        top-level symbols.
    """
    if "." in info.function:
        return info.function.split(".", maxsplit=1)[0]
    return info.module.rsplit(".", maxsplit=1)[-1] if info.module else "root"


def generate_deprecation_markdown(
    module: Union[Any, str],  # noqa: ANN401
    current_version: Optional[str] = None,
    recursive: bool = True,
) -> str:
    """Generate a markdown table summarizing deprecated wrappers in a module/package.

    The report is derived from decorator metadata and includes both top-level
    deprecated wrappers and nested deprecated class members such as methods and
    constructors.

    Args:
        module: Imported module/package object or string module path to scan.
        current_version: Optional current package version. When None, the report
            tries to auto-detect the top-level package version and falls back to
            ``⚪ Status Unknown`` if that is not available.
        recursive: If True (default), include submodules in the scan.

    Returns:
        A Markdown table with ``Symbol``, ``Deprecated In``, ``Removal Target``,
        and ``Current Status`` columns.

    Example:
        >>> report = generate_deprecation_markdown("tests.collection_deprecate", current_version="1.5", recursive=False)
        >>> "| Symbol | Deprecated In | Removal Target | Current Status |" in report
        True
    """
    resolved_version, parsed_version = _resolve_report_version(module, current_version=current_version)
    rows = [
        "| Symbol | Deprecated In | Removal Target | Current Status |",
        "| :--- | :---: | :---: | :--- |",
    ]

    for info in find_deprecation_wrappers(module, recursive=recursive, include_members=True):
        rows.append(
            "| "
            f"`{_format_report_symbol(info)}` | "
            f"{_format_report_version(info.deprecated_info.deprecated_in)} | "
            f"{_format_report_version(info.deprecated_info.remove_in)} | "
            f"{_get_report_status(info, parsed_version)} |"
        )

    if resolved_version is not None:
        rows.insert(0, f"<!-- Current version: {resolved_version} -->")

    return "\n".join(rows)


def generate_deprecation_timeline(
    module: Union[Any, str],  # noqa: ANN401
    current_version: Optional[str] = None,
    recursive: bool = True,
) -> str:
    """Generate a Mermaid timeline summarizing deprecated wrappers in a module/package.

    Args:
        module: Imported module/package object or string module path to scan.
        current_version: Optional current package version used to label each
            symbol as active, past-due, scheduled, or open-ended.
        recursive: If True (default), include submodules in the scan.

    Returns:
        Mermaid ``timeline`` syntax grouped by class name for deprecated members
        and by module basename for top-level symbols.

    Example:
        >>> report = generate_deprecation_timeline("tests.collection_deprecate", current_version="1.5", recursive=False)
        >>> report.startswith("timeline")
        True
    """
    _resolved_version, parsed_version = _resolve_report_version(module, current_version=current_version)
    timeline = [
        "timeline",
        "    title Deprecation Lifecycle",
    ]

    section_entries: dict[str, list[str]] = {}
    for info in find_deprecation_wrappers(module, recursive=recursive, include_members=True):
        section = _get_report_section(info)
        range_label = (
            f"{_format_report_version(info.deprecated_info.deprecated_in, missing='v?')} → "
            f"{_format_report_version(info.deprecated_info.remove_in, missing='open-ended')}"
        )
        entry = (
            f"        {range_label} : {_format_report_symbol(info)} "
            f"({_get_report_status(info, parsed_version)})"
        )
        section_entries.setdefault(section, []).append(entry)

    for section in sorted(section_entries):
        timeline.append(f"    section {section}")
        timeline.extend(section_entries[section])

    return "\n".join(timeline)


# ---------------------------------------------------------------------------
# Backward-compatibility shims — deprecated since 0.6, removed in 1.0
# ---------------------------------------------------------------------------

# Import delayed to avoid a module-level circular import cycle:
# audit.py is imported by __init__.py before deprecation.py is available.
from deprecate.deprecation import deprecated  # noqa: E402


@deprecated(target=validate_deprecation_wrapper, deprecated_in="0.6", remove_in="1.0")
def validate_deprecated_callable(func: Callable) -> DeprecationWrapperInfo:
    """Use :func:`validate_deprecation_wrapper` instead."""
    return validate_deprecation_wrapper(func)


@deprecated(target=find_deprecation_wrappers, deprecated_in="0.6", remove_in="1.0")
def find_deprecated_callables(
    module: Union[Any, str],  # noqa: ANN401
    recursive: bool = True,
) -> list[DeprecationWrapperInfo]:
    """Use :func:`find_deprecation_wrappers` instead."""
    return find_deprecation_wrappers(module, recursive)


@deprecated_class(target=DeprecationWrapperInfo, deprecated_in="0.6", remove_in="1.0")
class DeprecatedCallableInfo:
    """Deprecated name for :class:`DeprecationWrapperInfo`. Use that instead."""
