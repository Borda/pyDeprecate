"""Audit tools for deprecation lifecycle management.

This module provides three complementary utilities for verifying the health of deprecated callables across a codebase.
All three are designed to be called from pytest or a CI script against an imported package.

**Wrapper configuration** (:func:`~deprecate.audit.validate_deprecation_wrapper`,
:func:`~deprecate.audit.find_deprecation_wrappers`):
    Detect wrappers that have zero impact — invalid ``args_mapping`` keys, identity mappings, empty mappings, or a
    ``target`` pointing back to the same wrapper.

**Expiry enforcement** (:func:`~deprecate.audit.validate_deprecation_expiry`):
    Detect wrappers whose ``remove_in`` version has been reached or passed, preventing zombie code from shipping past
    its scheduled removal deadline.

**Chain detection** (:func:`~deprecate.audit.validate_deprecation_chains`):
    Detect wrappers whose ``target`` is itself a deprecated callable, forming a chain that users traverse
    unnecessarily. Two chain kinds are reported via :class:`~deprecate.audit.ChainType`: ``TARGET`` (forwarding chain)
    and ``STACKED`` (composed argument mappings).

**Report generation** (:func:`~deprecate.audit.generate_deprecation_table`):
    Generate a docs-friendly markdown summary from wrapper metadata.

Results are returned as :class:`~deprecate.audit.DeprecationWrapperInfo` dataclasses, which carry both
identification info and structured validation results for programmatic processing.

!!! note
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

import enum
import inspect
import warnings
from contextlib import suppress
from dataclasses import dataclass, field, is_dataclass, replace
from enum import Enum
from functools import cached_property, wraps
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

if TYPE_CHECKING:
    from packaging.version import Version

from deprecate._types import DeprecationConfig, TargetMode, _has_deprecation_meta
from deprecate.proxy import _DeprecatedProxy, deprecated_class
from deprecate.utils import get_func_arguments_types_defaults


class ReportStyle(str, enum.Enum):
    """Markdown table layout produced by :func:`~deprecate.audit.generate_deprecation_table`."""

    COMPACT = "compact"
    MATRIX = "matrix"


class ReportStatus(str, enum.Enum):
    """Lifecycle status labels used in the deprecation report's *Current Status* column.

    Each member's value is the full display string (emoji + text) rendered in the table.
    Using a ``str`` enum means members compare equal to their string values and can be
    returned wherever a plain string is expected.

    Members are ordered from least to most urgent for easy visual scanning:

    Examples:
        >>> ReportStatus.ACTIVE_WARNING.value
        '📢 Deprecation Active'
        >>> ReportStatus.PAST_REMOVAL_DATE > ReportStatus.ACTIVE_WARNING
        False

    """

    SCHEDULED_DEPRECATION = "🕒 Scheduled Deprecation"  # current < deprecated_in
    NO_REMOVAL_TARGET = "ℹ️ No Removal Target"  # remove_in not set
    STATUS_UNKNOWN = "⚪ Status Unknown"  # current_version unavailable
    INVALID_REMOVAL_TARGET = "⚪ Invalid Removal Target"  # remove_in unparsable
    ACTIVE_WARNING = "📢 Deprecation Active"  # current < remove_in (different base)
    REMOVAL_IMMINENT = "⏰ Removal Imminent"  # pre-release dev/a/b of remove_in base
    REMOVE_BEFORE_RELEASE = "🔔 Remove Before Release"  # RC of the remove_in base release
    PAST_REMOVAL_DATE = "💥 Past Removal Date"  # current >= remove_in


def _normalize_version_string(version: str) -> str:
    """Normalize non-standard version strings before PEP 440 parsing.

    Newer ``packaging`` (>=22) is strict PEP 440 and rejects real-world strings that omit trailing digits
    on pre/post/dev release labels (e.g. ``"1.8.0.dev"``, ``"1.8.0dev"``, ``"1.8.0.post"``). This helper
    performs the minimum normalization needed to make such strings parseable, then defers everything else
    (label aliasing like ``alpha`` -> ``a``, case folding, separator handling) to ``packaging.Version``.

    The transformation is conservative:

    1. Strip a single leading ``v`` or ``V`` prefix (``packaging`` accepts this, but stripping defensively
       keeps the normalized output stable for downstream callers).
    2. Append ``0`` to bare pre/post/dev labels that lack a trailing digit. Labels recognized:
       ``dev``, ``rc``, ``a``, ``b``, ``c``, ``alpha``, ``beta``, ``preview``, ``post``.

    No other transformations are applied — case, separators, and label aliases pass through unchanged
    so ``packaging.Version`` can apply its own canonicalization.

    Args:
        version: Raw version string, possibly missing trailing digits on labels.

    Returns:
        Normalized version string ready to be passed to ``packaging.version.Version``.

    Examples:
        >>> _normalize_version_string("1.8.0.dev")
        '1.8.0.dev0'
        >>> _normalize_version_string("1.8.0dev")
        '1.8.0dev0'
        >>> _normalize_version_string("1.8.0.post")
        '1.8.0.post0'
        >>> _normalize_version_string("v1.2.3")
        '1.2.3'
        >>> _normalize_version_string("1.8.0.RC1")
        '1.8.0.RC1'
        >>> _normalize_version_string("1.2.3")
        '1.2.3'

    """
    import re

    normalized = version.lstrip("vV")
    # Append ``0`` to bare pre/post/dev labels with no trailing digit. The ordering of the alternatives
    # matters: longer labels (``alpha``, ``beta``, ``preview``) must come before their single-letter
    # forms (``a``, ``b``) so the regex prefers the longer match.
    # Use a negative lookahead for ``[0-9]`` to detect "no trailing digit"; ``(?=$|[^A-Za-z0-9])``
    # ensures the label is a whole token (e.g. ``dev`` but not ``develop``).
    pattern = re.compile(
        r"(?P<sep>\.?)(?P<label>alpha|beta|preview|post|dev|rc|a|b|c)(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    return pattern.sub(lambda m: f"{m.group('sep')}{m.group('label')}0", normalized)


def _parse_version(version_string: str) -> "Version":
    """Parse a version string using the packaging library (PEP 440 compliant).

    This function requires the 'packaging' library, which is available as an optional dependency via the 'audit'
    extra: ``pip install pyDeprecate[audit]``

    The packaging library provides robust PEP 440 version parsing and comparison, supporting pre-releases
    (alpha/beta/rc), stable releases, post-releases, and development releases with proper ordering.

    Inputs are first passed through :func:`_normalize_version_string`, which appends ``0`` to bare
    pre/post/dev labels (e.g. ``"1.8.0.dev"`` becomes ``"1.8.0.dev0"``) so non-canonical-but-common
    strings parse successfully under strict ``packaging`` (>=22).

    Args:
        version_string: Version string (e.g., "1.2.3", "2.0", "1.5.0a1", "1.5.0rc1", "1.5.0.post1").

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
        >>> _parse_version("1.8.0.dev") < _parse_version("1.8.0")
        True
        >>> _parse_version("1.8.0.post") > _parse_version("1.8.0")
        True

    !!! note
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
        return Version(_normalize_version_string(version_string))
    except InvalidVersion as err:
        raise ValueError(
            f"Failed to parse version '{version_string}'. Expected PEP 440 format "
            f"(e.g., '1.2.3', '2.0', '1.5.0a1'). Error: {err}"
        ) from err


class ChainType(Enum):
    """Type of deprecation chain detected by :func:`~deprecate.audit.validate_deprecation_chains`.

    Attributes:
        TARGET: The ``target`` argument is itself a callable decorated with :func:`~deprecate.deprecated`
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

    This dataclass represents a deprecated wrapper (a :func:`~deprecate.deprecated`-decorated function or a
    :func:`~deprecate.proxy.deprecated_class`/:func:`~deprecate.proxy.deprecated_instance` proxy), containing both
    identification info and validation results from :func:`~deprecate.audit.validate_deprecation_wrapper` or
    :func:`~deprecate.audit.find_deprecation_wrappers`.

    Attributes:
        module: Module name where the wrapper is defined (empty for direct validation).
        function: Wrapper name.
        deprecated_info: The ``__deprecated__`` attribute from the decorator,
            as a :class:`~deprecate._types.DeprecationConfig`.
        invalid_args: List of ``args_mapping`` keys that don't exist in the wrapper's signature.
        empty_args_mapping: True if ``args_mapping`` is None or empty (no argument remapping).
        identity_args_mapping: List of args where key equals value (e.g., ``{'arg': 'arg'}``).
        self_reference: True if target points to the same wrapper.
        no_effect: True if wrapper has zero impact (combines all checks).
        all_identity: True when every configured mapping is an identity mapping (key == value, non-empty).
        chain_type: The kind of deprecation chain detected, or ``None`` if no chain.
            See :class:`~deprecate.audit.ChainType` for values
            (:attr:`~deprecate.audit.ChainType.TARGET` or :attr:`~deprecate.audit.ChainType.STACKED`).
        misconfigured_target: True when the wrapper has an invalid target configuration:
            ``target=False``, :attr:`~deprecate._types.TargetMode.NOTIFY` with ``args_mapping``, or
            :attr:`~deprecate._types.TargetMode.ARGS_REMAP` with empty ``args_mapping``.
        empty_deprecated_in: True when ``deprecated_in`` is empty. Missing ``remove_in`` alone is a valid use case
            (many libraries deprecate without a scheduled removal date), so only the absence of ``deprecated_in``
            is treated as a misconfiguration signal. CI pipelines can filter on this field to surface wrappers
            that lack the introductory version metadata without crashing callers.
        api_type: Inferred deprecated API type for report generation.
            Possible values: ``callable``, ``args``, ``class``, ``dataclass``, ``dataclass attributes``,
            ``data``, ``class constructor``, ``class constructor args``, ``class method``, ``class method args``,
            ``classmethod``, ``classmethod args``, ``staticmethod``, ``staticmethod args``.

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
    empty_args_mapping: bool = False
    identity_args_mapping: list[str] = field(default_factory=list)
    self_reference: bool = False
    no_effect: bool = False
    misconfigured_target: bool = False
    all_identity: bool = False
    chain_type: Optional[ChainType] = None
    empty_deprecated_in: bool = field(init=False, default=False)
    api_type: str = field(repr=False, default="")

    def __post_init__(self) -> None:
        """Derive ``empty_deprecated_in`` from ``deprecated_info`` to keep them in sync."""
        object.__setattr__(self, "empty_deprecated_in", not self.deprecated_info.deprecated_in)

    @property
    def empty_mapping(self) -> bool:
        """Deprecated alias for :attr:`~deprecate.audit.DeprecationWrapperInfo.empty_args_mapping`.

        !!! warning "Deprecated in 0.8"
            Renamed to :attr:`~deprecate.audit.DeprecationWrapperInfo.empty_args_mapping`.
            Will be removed in v1.0.

        Note:
            Python's default warning filter deduplicates per ``(message, category, module, lineno)``,
            so accessing this property in a loop from the same call site emits at most one warning.

        """
        warnings.warn(
            "'empty_mapping' was renamed to 'empty_args_mapping' in 0.8 and will be removed in 1.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.empty_args_mapping

    @property
    def identity_mapping(self) -> list[str]:
        """Deprecated alias for :attr:`~deprecate.audit.DeprecationWrapperInfo.identity_args_mapping`.

        !!! warning "Deprecated in 0.8"
            Renamed to :attr:`~deprecate.audit.DeprecationWrapperInfo.identity_args_mapping`.
            Will be removed in v1.0.

        Note:
            Python's default warning filter deduplicates per ``(message, category, module, lineno)``,
            so accessing this property in a loop from the same call site emits at most one warning.

        """
        warnings.warn(
            "'identity_mapping' was renamed to 'identity_args_mapping' in 0.8 and will be removed in 1.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.identity_args_mapping


# ---------------------------------------------------------------------------
# Backward-compatible constructor shim for DeprecationWrapperInfo
#
# ``empty_mapping`` and ``identity_mapping`` were renamed to
# ``empty_args_mapping`` / ``identity_args_mapping`` in 0.8.  The
# ``@property`` aliases above cover attribute *reads*; this shim patches
# ``__init__`` so old keyword arguments emit ``DeprecationWarning`` and are
# redirected to the new names rather than raising ``TypeError``.
#
# dataclasses.replace() merges all current field values with the caller's
# changes before calling ``cls(**merged)``.  Passing an old name (e.g.
# ``replace(info, empty_mapping=True)``) injects the old kwarg alongside
# the auto-copied ``empty_args_mapping`` value.  The shim detects that
# conflict and honours the old-name value (discards the auto-injected one).
#
# ADD field → add @property alias above + (old, new) pair in _dwi_compat_init.
# RENAME/REMOVE field → update _dwi_compat_init accordingly.
# ---------------------------------------------------------------------------
_dwi_orig_init = DeprecationWrapperInfo.__init__


@wraps(_dwi_orig_init)
def _dwi_compat_init(self: DeprecationWrapperInfo, *args: object, **kwargs: object) -> None:
    """Wrap the auto-generated ``__init__`` to accept legacy constructor kwargs."""
    for old, new in (
        ("empty_mapping", "empty_args_mapping"),
        ("identity_mapping", "identity_args_mapping"),
    ):
        if old in kwargs:
            warnings.warn(
                f"'{old}' was renamed to '{new}' in 0.8 and will be removed in 1.0."
                " Update your code to use the new name.",
                DeprecationWarning,
                stacklevel=2,
            )
            old_value = kwargs.pop(old)  # always remove old kwarg so it isn't forwarded
            if new in kwargs:
                # Both names present — common via dataclasses.replace() which auto-injects
                # the current field value under the new name.  Honour the old-name value
                # (the caller's explicit intent) and discard the auto-injected value.
                kwargs.pop(new)
            kwargs[new] = old_value
    _dwi_orig_init(self, *args, **kwargs)  # type: ignore[arg-type]


DeprecationWrapperInfo.__init__ = _dwi_compat_init  # type: ignore[method-assign]


def _member_name_key(item: tuple[str, Any]) -> str:
    """Extract the member name for sorting."""
    return item[0]


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
    return sorted(members, key=_member_name_key)


def validate_deprecation_wrapper(func: Callable) -> DeprecationWrapperInfo:
    """Validate if a deprecated wrapper configuration is effective.

    This is a development tool to check if deprecated wrappers are configured correctly and will have the intended
    effect. It examines the ``__deprecated__`` attribute set by the :func:`~deprecate.deprecated` decorator and
    identifies
    configurations that would result in zero impact:

    - args_mapping keys that don't exist in the function's signature
    - Empty or None args_mapping (no argument remapping)
    - Identity mappings where key equals value (e.g., {'arg': 'arg'})
    - Target pointing to the same function (self-reference)
    - target=None with no args_mapping (just warns, no forwarding)

    Args:
        func: The decorated function to validate. Must have a ``__deprecated__`` attribute set by the ``@deprecated``
            decorator.

    Returns:
        :class:`~deprecate.audit.DeprecationWrapperInfo`: Dataclass with validation results:
            - function: Name of the wrapper being validated
            - deprecated_info: The typed :class:`~deprecate._types.DeprecationConfig` metadata from ``__deprecated__``
            - invalid_args: List of args_mapping keys not in wrapper signature
            - empty_args_mapping: True if args_mapping is None or empty
            - identity_args_mapping: List of args where key equals value (no effect)
            - self_reference: True if target is the same as the wrapper
            - no_effect: True if wrapper has zero impact (all checks combined)
            - empty_deprecated_in: True when ``deprecated_in`` is absent or empty

    Raises:
        ValueError: If the wrapper has missing or invalid ``__deprecated__`` metadata (expected
            :class:`~deprecate._types.DeprecationConfig`).

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
        >>> result.identity_args_mapping
        ['arg']
        >>> result.no_effect
        True

    Note:
        Use this function during development or in CI to ensure deprecation decorators are configured meaningfully.
        Invalid configurations won't cause runtime errors but will silently have no effect.

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
    empty_args_mapping = not args_mapping
    identity_args_mapping: list[str] = []
    self_reference = target is func if target is not None else False
    # chain_type distinguishes two chain problems:
    # - ChainType.TARGET: target is a deprecated callable that itself forwards to another function
    #   (i.e. target.__deprecated__.target is not a supported stacking mode). Fix: point directly
    #   to the final target.
    # - ChainType.STACKED: supported decorator stacking. Two sub-cases:
    #   (a) target is a deprecated callable whose own target=ARGS_REMAP (self-deprecation with renaming).
    #   (b) target=True but __wrapped__ also has target=True (stacked @deprecated(True) decorators).
    _is_args_remap = target is TargetMode.ARGS_REMAP
    _is_notify = target is TargetMode.NOTIFY

    chain_type: Optional[ChainType] = None
    if callable(target) and _has_deprecation_meta(target):
        wrp_depr_tgt = target.__deprecated__.target
        # STACKED: inner is ARGS_REMAP (mappings compose)
        # TARGET: inner is NOTIFY or another callable (actual forwarding chain — should point to final target directly)
        is_stacked = wrp_depr_tgt is TargetMode.ARGS_REMAP
        chain_type = ChainType.STACKED if is_stacked else ChainType.TARGET
    elif _is_args_remap:
        wrapped = getattr(func, "__wrapped__", None)
        if wrapped is not None and _has_deprecation_meta(wrapped):
            erp_depr_tgt = wrapped.__deprecated__.target
            if erp_depr_tgt is True or erp_depr_tgt is TargetMode.ARGS_REMAP:
                chain_type = ChainType.STACKED  # stacked self-deprecation decorators

    all_identity = False
    if args_mapping:
        if isinstance(func, _DeprecatedProxy):
            invalid_args = []  # proxy __call__ is (*args, **kwargs); skip signature check
        else:
            func_args = [arg[0] for arg in get_func_arguments_types_defaults(func)]
            invalid_args = [arg for arg in args_mapping if arg not in func_args]
        identity_args_mapping = [arg for arg, val in args_mapping.items() if arg == val]
        # Check if ALL mappings are identity (complete no-op)
        all_identity = len(identity_args_mapping) == len(args_mapping) and len(args_mapping) > 0

    # Wrapper has no effect if it provides no call forwarding, arg mapping, or warning:
    # - Self-reference (forwards to itself — no meaningful forwarding)
    # - ARGS_REMAP (target=True) AND (empty mapping OR all identity mappings)
    #   → no forwarding, no meaningful arg remapping
    # Note: NOTIFY (target=None) is NOT no_effect — it still emits deprecation warnings.
    # Note: When target is a different function, there's ALWAYS an effect (forwarding).
    is_self_deprecation = _is_args_remap or self_reference
    no_effect = self_reference or (is_self_deprecation and (empty_args_mapping or all_identity))

    # Misconfigured: target+args combination is invalid regardless of whether it has effect.
    # Construction-time `target=False` is captured in DeprecationConfig.misconfigured by the
    # decorator/proxy before normalisation; combine that with the runtime checks below.
    # NOTIFY ignores args_mapping; ARGS_REMAP needs args_mapping.
    misconfigured_target = (
        bool(getattr(dep_info, "misconfigured", False))
        or (_is_notify and bool(args_mapping))
        or (_is_args_remap and empty_args_mapping)
    )

    function = dep_info.name or getattr(func, "__name__", str(func))

    return DeprecationWrapperInfo(
        function=function,
        deprecated_info=dep_info,
        invalid_args=invalid_args,
        empty_args_mapping=empty_args_mapping,
        identity_args_mapping=identity_args_mapping,
        self_reference=self_reference,
        no_effect=no_effect,
        misconfigured_target=misconfigured_target,
        all_identity=all_identity,
        chain_type=chain_type,
    )


def _check_deprecated_wrapper_expiry(func: Callable, current_version: str) -> None:
    """Check if a deprecated wrapper has passed its scheduled removal version.

    This is an internal helper function used by :func:`~deprecate.audit.validate_deprecation_expiry`.
    It verifies that deprecated code is actually removed when it reaches its scheduled removal deadline.

    The function validates that the wrapper is properly decorated, extracts the removal version from its metadata,
    and compares it against the current version using semantic versioning. If the current version is greater than or
    equal to the scheduled removal version, it raises an AssertionError indicating the code must be deleted.

    Args:
        func: The deprecated callable to check. Must have a ``__deprecated__`` attribute set by the ``@deprecated``
            decorator.
        current_version: The current version of the package (e.g., "2.0.0"). Should follow PEP 440 versioning
            conventions.

    Raises:
        ValueError: If the wrapper has missing or invalid ``__deprecated__`` metadata (expected
            :class:`~deprecate._types.DeprecationConfig`).
        ValueError: If the ``remove_in`` field is missing from the deprecation metadata.
        AssertionError: If the current version is greater than or equal to the scheduled removal version, indicating
            the code should have been removed.

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

    This private helper function attempts to retrieve the version of an installed package using importlib.metadata,
    with a fallback to checking the package's ``__version__`` attribute. This is useful for automatically detecting
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


def _check_expiry_for_callables(results: list[DeprecationWrapperInfo], current_version: str) -> list[str]:
    """Apply expiry comparison to pre-scanned wrapper results.

    Shared implementation used by :func:`validate_deprecation_expiry` and the CLI's single-scan path. Keeps the
    error message format in one place.

    Args:
        results: Pre-scanned wrapper info list.
        current_version: Current package version string for comparison (PEP 440).

    Returns:
        List of expiry error messages for callables that have passed their removal deadline.

    Raises:
        ImportError: If the ``packaging`` library is not installed.

    """
    current_ver = _parse_version(current_version)
    expired = []
    for info in results:
        remove_in = info.deprecated_info.remove_in
        if not remove_in:
            continue
        try:
            remove_ver = _parse_version(remove_in)
        except ValueError:
            continue
        if current_ver >= remove_ver:
            expired.append(
                f"Callable `{info.function}` was scheduled for removal in version {remove_in}"
                f" but still exists in version {current_version}. Please delete this deprecated code."
            )
    return expired


def validate_deprecation_expiry(
    module: Union[Any, str],  # noqa: ANN401
    current_version: Optional[str] = None,
    recursive: bool = True,
    include_members: bool = False,
) -> list[str]:
    """Check all deprecated callables in a module/package for expired removal deadlines.

    This enforcement tool scans an entire module or package for deprecated functions and checks if any have passed
    their scheduled removal version. It's designed for CI/CD pipelines to automatically detect and report zombie code
    across a codebase.

    The function uses :func:`~deprecate.audit.find_deprecation_wrappers` to discover all deprecated wrappers,
    then checks each one against
    the current version. Any wrappers that have reached or passed their removal deadline are collected and reported.

    Args:
        module: A Python module or package to scan. Can be:
            - Imported module object (e.g., ``import my_package; validate_deprecation_expiry(my_package, "2.0")``)
            - String module path (e.g., ``validate_deprecation_expiry("my_package.submodule", "2.0")``)
        current_version: The current version of your package to compare against removal deadlines (e.g., ``"2.0.0"``).
            If None, attempts to auto-detect the version using the package name from the module path (e.g.,
            ``"mypackage"`` extracts ``mypackage`` as package name).
        recursive: If True (default), recursively scan submodules. If False, only scan the top-level module.
        include_members: If True, also scan deprecated class members (methods, constructors).

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

    !!! note
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

    # Validate current_version upfront for fail-fast feedback before the module scan.
    try:
        _parse_version(current_version)
    except ValueError as err:
        raise ValueError(f"Invalid current_version '{current_version}': {err}") from err

    # Handle string module path
    if isinstance(module, str):
        module = importlib.import_module(module)

    return _check_expiry_for_callables(
        find_deprecation_wrappers(module, recursive=recursive, include_members=include_members), current_version
    )


def find_deprecation_wrappers(
    module: Union[Any, str],  # noqa: ANN401
    recursive: bool = True,
    include_members: bool = True,
) -> list[DeprecationWrapperInfo]:
    """Scan a module or package for deprecated wrappers and validate them.

    This is a development/CI tool to scan a codebase for all wrappers created with :func:`~deprecate.deprecated`,
    :func:`~deprecate.deprecated_class`, or :func:`~deprecate.deprecated_instance` and validate that each wrapper
    configuration is meaningful.
    Returns comprehensive information about each deprecated wrapper including validation results that help identify
    misconfigured wrappers.

    Args:
        module: A Python module or package to scan for deprecated wrappers. Can be:
            - Imported module object (e.g., ``import my_package; find_deprecation_wrappers(my_package)``)
            - String module path (e.g., ``find_deprecation_wrappers("my_package.submodule")``)
        recursive: If True (default), recursively scan submodules. If False, only scan the top-level module.
        include_members: If True, also scan deprecated methods and constructors defined on classes.

    Returns:
        List of :class:`~deprecate.audit.DeprecationWrapperInfo` dataclasses, one per deprecated wrapper found.
        Each contains:
            - module: Module name where the wrapper is defined
            - function: Wrapper name
            - deprecated_info: DeprecationConfig metadata from the decorator (``__deprecated__`` attribute)
            - invalid_args: List of args_mapping keys not in wrapper signature
            - empty_args_mapping: True if args_mapping is None or empty
            - identity_args_mapping: List of identity mappings (key == value)
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
        - Inspects the ``__deprecated__`` attribute set by the :func:`~deprecate.deprecated` decorator
        - Skips private/magic attributes and imports from other modules
        - Uses static member inspection to avoid scan-time side effects from dynamic attribute access

    """
    import importlib
    import pkgutil

    results: list[DeprecationWrapperInfo] = []

    # Handle string module path
    if isinstance(module, str):
        module = importlib.import_module(module)

    def _scan_callable(
        obj: Any,  # noqa: ANN401
        module_name: str,
        qualified_name: str,
        *,
        member_name: Optional[str] = None,
        descriptor_kind: Optional[str] = None,
    ) -> None:
        """Emit a result if ``obj`` carries ``__deprecated__`` metadata."""
        if _has_deprecation_meta(obj):
            info = validate_deprecation_wrapper(obj)
            info = replace(
                info,
                module=module_name,
                function=qualified_name,
                api_type=_classify_wrapper_api_type(
                    obj, info, member_name=member_name, descriptor_kind=descriptor_kind
                ),
            )
            results.append(info)

    def _scan_class(cls: Any, module_name: str, cls_name: str) -> None:  # noqa: ANN401
        """Scan class members, peeking through descriptors."""
        try:
            members = _getmembers_static_compat(cls)
        except (AttributeError, TypeError):
            return
        for attr_name, obj in members:
            if attr_name.startswith("_") and attr_name != "__init__":
                continue
            qualified = f"{cls_name}.{attr_name}"
            # Peek through descriptors to find the underlying function.
            if isinstance(obj, (classmethod, staticmethod)):
                kind = "classmethod" if isinstance(obj, classmethod) else "staticmethod"
                _scan_callable(obj.__func__, module_name, qualified, member_name=attr_name, descriptor_kind=kind)
            elif isinstance(obj, property):
                if obj.fget is not None:
                    _scan_callable(obj.fget, module_name, qualified, member_name=attr_name)
            elif isinstance(obj, cached_property):
                _scan_callable(obj.func, module_name, qualified, member_name=attr_name)
            else:
                _scan_callable(obj, module_name, qualified, member_name=attr_name)

    def _scan_module(mod: Any) -> None:  # noqa: ANN401
        """Scan a single module for deprecated functions and class members."""
        try:
            # Static inspection avoids dynamic getattr/descriptor evaluation while scanning.
            members = _getmembers_static_compat(mod)
        except (AttributeError, TypeError, ImportError):
            return

        mod_name = mod.__name__ if hasattr(mod, "__name__") else str(mod)
        for name, obj in members:
            # Skip private/magic attributes and imports from other modules
            if name.startswith("_"):
                continue

            if _has_deprecation_meta(obj):
                info = validate_deprecation_wrapper(obj)
                info = replace(
                    info,
                    module=mod_name,
                    function=name,
                    api_type=_classify_wrapper_api_type(obj, info),
                )
                results.append(info)
            elif include_members and inspect.isclass(obj) and getattr(obj, "__module__", None) == mod_name:
                _scan_class(obj, mod_name, name)

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


def _resolve_report_version(
    module: Union[Any, str],  # noqa: ANN401
    *,
    current_version: Optional[str],
) -> tuple[Optional[str], Optional["Version"]]:
    """Resolve report version string and optional parsed version object."""
    module_name = module if isinstance(module, str) else getattr(module, "__name__", None)
    resolved_version = current_version

    if resolved_version is None and module_name:
        with suppress(ImportError):
            resolved_version = _get_package_version(module_name.split(".")[0])

    if resolved_version is None:
        return None, None

    try:
        return resolved_version, _parse_version(resolved_version)
    except ImportError:
        return resolved_version, None
    except ValueError as err:
        if current_version is not None:
            raise ValueError(f"Invalid current_version '{current_version}': {err}") from err
        return resolved_version, None


def _safe_parse_report_version(version: str) -> Optional["Version"]:
    """Best-effort version parser for report status evaluation."""
    if not version:
        return None
    try:
        return _parse_version(version)
    except (ImportError, ValueError):
        return None


def _format_report_symbol(info: DeprecationWrapperInfo) -> str:
    """Return a stable fully-qualified label for report rows."""
    return f"{info.module}.{info.function}" if info.module else info.function


def _format_report_target(target: Any) -> str:  # noqa: ANN401
    """Format replacement target name for report rows."""
    if target is None or target is TargetMode.NOTIFY:
        return "—"
    if isinstance(target, TargetMode):
        return target.value
    if callable(target):
        target_module = getattr(target, "__module__", "")
        target_name = getattr(target, "__qualname__", getattr(target, "__name__", str(target)))
        return f"{target_module}.{target_name}" if target_module else target_name
    return str(target)


def _classify_wrapper_api_type(
    wrapped_obj: Any,  # noqa: ANN401
    info: DeprecationWrapperInfo,
    *,
    member_name: Optional[str] = None,
    descriptor_kind: Optional[str] = None,
) -> str:
    """Classify wrapper kind for markdown report rows."""
    has_mapping = bool(info.deprecated_info.args_mapping)

    if member_name is not None:
        if member_name == "__init__":
            return "class constructor args" if has_mapping else "class constructor"
        if descriptor_kind == "classmethod":
            return "classmethod args" if has_mapping else "classmethod"
        if descriptor_kind == "staticmethod":
            return "staticmethod args" if has_mapping else "staticmethod"
        return "class method args" if has_mapping else "class method"

    if isinstance(wrapped_obj, _DeprecatedProxy):
        source_obj = wrapped_obj.wrapped
        if inspect.isclass(source_obj):
            if is_dataclass(source_obj):
                return "dataclass attributes" if has_mapping else "dataclass"
            return "class"
        return "data"

    if inspect.isclass(wrapped_obj):
        if is_dataclass(wrapped_obj):
            return "dataclass attributes" if has_mapping else "dataclass"
        return "class"

    if has_mapping:
        return "args"

    return "callable"


def _format_report_api_type(info: DeprecationWrapperInfo) -> str:
    """Return api_type with backward-compatible fallback."""
    if info.api_type:
        return info.api_type
    return "args" if info.deprecated_info.args_mapping else "callable"


def _report_row_sort_key(info: DeprecationWrapperInfo) -> tuple[str, str, str, bool, str]:
    """Sort report rows by module and symbol family, keeping args-variants adjacent."""
    function = info.function or ""
    top_level = function.split(".", maxsplit=1)[0] if function else ""
    api_type = _format_report_api_type(info)
    return (info.module or "", top_level, function, api_type.endswith(" args"), api_type)


def _format_report_version(version: Optional[str], *, missing: str = "—") -> str:
    """Format version values with a stable ``v`` prefix for report output."""
    if not version:
        return missing
    return f"v{version.lstrip('vV')}"


def _get_report_status(info: DeprecationWrapperInfo, current_version: Optional["Version"]) -> ReportStatus:
    """Classify one deprecated symbol into a report lifecycle status."""
    if current_version is None:
        return ReportStatus.NO_REMOVAL_TARGET if not info.deprecated_info.remove_in else ReportStatus.STATUS_UNKNOWN

    deprecated_in = _safe_parse_report_version(info.deprecated_info.deprecated_in)
    if deprecated_in is not None and current_version < deprecated_in:
        return ReportStatus.SCHEDULED_DEPRECATION

    remove_in = info.deprecated_info.remove_in
    if not remove_in:
        return ReportStatus.NO_REMOVAL_TARGET

    remove_version = _safe_parse_report_version(remove_in)
    if remove_version is None:
        return ReportStatus.INVALID_REMOVAL_TARGET

    if current_version >= remove_version:
        return ReportStatus.PAST_REMOVAL_DATE

    # Pre-release of the same base release as remove_in gets an elevated status:
    # dev/alpha/beta → REMOVAL_IMMINENT; rc → REMOVE_BEFORE_RELEASE.
    # base_version strips pre/post/dev/local markers so "1.8" == "1.8.0" compare equal.
    if current_version.is_prerelease:
        try:
            _VersionType = type(current_version)
            same_base = _VersionType(current_version.base_version) == _VersionType(remove_version.base_version)
        except Exception:
            same_base = False
        if same_base:
            if current_version.pre is not None and current_version.pre[0] == "rc":
                return ReportStatus.REMOVE_BEFORE_RELEASE
            return ReportStatus.REMOVAL_IMMINENT

    return ReportStatus.ACTIVE_WARNING


def generate_deprecation_table(
    module: Union[Any, str],  # noqa: ANN401
    current_version: Optional[str] = None,
    recursive: bool = True,
    style: Union[ReportStyle, str] = ReportStyle.COMPACT,
    include_members: bool = True,
    *,
    _wrappers: Optional[list["DeprecationWrapperInfo"]] = None,
) -> str:
    """Generate a markdown table summarizing deprecated wrappers.

    The table is derived from ``__deprecated__`` metadata and includes both
    top-level wrappers and deprecated class members (methods/constructors).

    Args:
        module: Imported module/package object or string module path to scan.
        current_version: Optional current package version for lifecycle status
            evaluation in compact style. If ``None``, auto-detection is attempted
            via the package name; status falls back to ``"⚪ Status Unknown"`` when
            ``packaging`` is not installed.
        recursive: If True (default), include submodules in the scan.
        style: Table format — ``"compact"`` or ``"matrix"``.
            - ``"compact"``: ``Original API | API Type | New API | Deprecated | Remove | Current Status``
            - ``"matrix"``: ``Original API | API Type | New API | <all versions...>``, with markers
              ``D`` (deprecated) and ``R`` (remove) in version columns.
        include_members: If True (default), include deprecated class members (methods, constructors).

    Returns:
        Markdown string containing a formatted table. When a version is
        resolvable (either from current_version or auto-detected), the
        first line is an HTML comment <!-- Current version: X.Y -->
        followed by the header row and alignment row. When no version can be
        resolved, the first line is the header row directly.

    Raises:
        ValueError: If ``style`` is not ``"compact"`` or ``"matrix"``, or if
            ``current_version`` is supplied but is not a valid PEP 440 version
            string and ``packaging`` is installed.

    Example:
        >>> from tests import collection_deprecate as pkg
        >>> report = generate_deprecation_table(pkg, recursive=False)
        >>> report.splitlines()[0]
        '| Original API | API Type | New API | Deprecated | Remove | Current Status |'

    """
    try:
        style = ReportStyle(style)
    except ValueError as err:
        raise ValueError(
            f"Invalid style {style!r}. Expected one of: {', '.join(s.value for s in ReportStyle)}."
        ) from err

    resolved_version, parsed_version = _resolve_report_version(module, current_version=current_version)
    if _wrappers is None:
        _wrappers = find_deprecation_wrappers(module, recursive=recursive, include_members=include_members)
    wrappers = sorted(
        _wrappers,
        key=_report_row_sort_key,
    )

    if style == ReportStyle.COMPACT:
        rows = [
            "| Original API | API Type | New API | Deprecated | Remove | Current Status |",
            "| :--- | :--- | :--- | :---: | :---: | :--- |",
        ]

        for info in wrappers:
            rows.append(
                "| "
                f"`{_format_report_symbol(info)}` | "
                f"{_format_report_api_type(info)} | "
                f"`{_format_report_target(info.deprecated_info.target)}` | "
                f"{_format_report_version(info.deprecated_info.deprecated_in)} | "
                f"{_format_report_version(info.deprecated_info.remove_in)} | "
                f"{_get_report_status(info, parsed_version).value} |"
            )
    else:
        version_map: dict[str, Optional[Version]] = {}
        for info in wrappers:
            for version in (info.deprecated_info.deprecated_in, info.deprecated_info.remove_in):
                if version and version not in version_map:
                    version_map[version] = _safe_parse_report_version(version)

        sorted_versions = sorted(
            version_map,
            key=lambda version: (
                version_map[version] is None,
                version_map[version] if version_map[version] is not None else version,
            ),
        )
        version_headers = [_format_report_version(version) for version in sorted_versions]
        header_row = "| Original API | API Type | New API | " + " | ".join(version_headers) + " |"
        divider_row = "| :--- | :--- | :--- | " + " | ".join(":---:" for _ in version_headers) + " |"
        col_idx = {v: i for i, v in enumerate(sorted_versions)}
        n_versions = len(sorted_versions)
        rows = [header_row, divider_row]

        for info in wrappers:
            markers: list[str] = [" "] * n_versions
            dep_in = info.deprecated_info.deprecated_in
            rem_in = info.deprecated_info.remove_in
            if dep_in and dep_in in col_idx:
                markers[col_idx[dep_in]] = "D"
            if rem_in and rem_in in col_idx:
                i = col_idx[rem_in]
                markers[i] = "R" if markers[i] == " " else "D/R"
            rows.append(
                "| "
                f"`{_format_report_symbol(info)}` | "
                f"{_format_report_api_type(info)} | "
                f"`{_format_report_target(info.deprecated_info.target)}` | " + " | ".join(markers) + " |"
            )

    if resolved_version is not None:
        rows.insert(0, f"<!-- Current version: {resolved_version} -->")

    return "\n".join(rows)


def validate_deprecation_chains(
    module: Union[Any, str],  # noqa: ANN401
    recursive: bool = True,
) -> list[DeprecationWrapperInfo]:
    """Validate that deprecated functions don't form chains with other deprecated code.

    This is a developer utility that scans a module or package for deprecated functions that form chains in two ways:

    1. **TARGET chains**: The ``target`` argument points to another deprecated callable instead of the final
       non-deprecated implementation.
    2. **STACKED chains**: Multiple ``@deprecated(True, ...)`` decorators are stacked on the same function with
       argument mappings that should be collapsed, or a callable ``target`` is itself a self-deprecation
       (``target=True``) requiring mapping composition.

    Both types are wasteful: wrappers should point directly to the final (non-deprecated) implementation with
    composed argument mappings.

    Detection is based purely on decorator metadata (``__deprecated__`` attributes) — no source-code or AST
    inspection is performed.

    Args:
        module: A Python module or package to scan for deprecation chains. Can be:
            - Imported module object (e.g., ``import my_package; validate_deprecation_chains(my_package)``)
            - String module path (e.g., ``validate_deprecation_chains("my_package.submodule")``)
        recursive: If True (default), recursively scan submodules. If False, only scan the top-level module.

    Returns:
        List of :class:`~deprecate.audit.DeprecationWrapperInfo` where ``chain_type`` is not ``None``, i.e. every
        deprecated wrapper that forms a chain (``ChainType.TARGET`` or ``ChainType.STACKED``).

    Example:
        >>> from deprecate import validate_deprecation_chains
        >>> import tests.collection_chains as test_module
        >>>
        >>> issues = validate_deprecation_chains(test_module, recursive=False)
        >>> len(issues) > 0  # Should find chains
        True

    Note:
        - Only flags callees using the :func:`~deprecate.deprecated` decorator
        - Uses :func:`~deprecate.audit.find_deprecation_wrappers` and inspects ``chain_type`` to detect chains

    """
    return [info for info in find_deprecation_wrappers(module, recursive=recursive) if info.chain_type is not None]


# ---------------------------------------------------------------------------
# Backward-compatibility shims — deprecated since 0.6, removed in 1.0
# ---------------------------------------------------------------------------

# Import delayed to avoid a module-level circular import cycle:
# audit.py is imported by __init__.py before deprecation.py is available.
from deprecate.deprecation import deprecated  # noqa: E402


@deprecated(target=validate_deprecation_wrapper, deprecated_in="0.6", remove_in="1.0")
def validate_deprecated_callable(func: Callable) -> DeprecationWrapperInfo:
    """Use :func:`~deprecate.audit.validate_deprecation_wrapper` instead."""
    return validate_deprecation_wrapper(func)


@deprecated(target=find_deprecation_wrappers, deprecated_in="0.6", remove_in="1.0")
def find_deprecated_callables(
    module: Union[Any, str],  # noqa: ANN401
    recursive: bool = True,
) -> list[DeprecationWrapperInfo]:
    """Use :func:`~deprecate.audit.find_deprecation_wrappers` instead."""
    return find_deprecation_wrappers(module, recursive)


@deprecated_class(target=DeprecationWrapperInfo, deprecated_in="0.6", remove_in="1.0")
class DeprecatedCallableInfo:
    """Deprecated name for :class:`~deprecate.audit.DeprecationWrapperInfo`, use that instead."""
