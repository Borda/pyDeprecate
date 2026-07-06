"""Audit tools for deprecation lifecycle management.

This module provides several complementary utilities for verifying the health of deprecated callables across a
codebase. All are designed to be called from pytest or a CI script against an imported package.

**Wrapper configuration** (:func:`~deprecate.audit.validate_deprecation_wrapper`,
:func:`~deprecate.audit.find_deprecation_wrappers`):
    Detect wrappers that have zero impact — invalid ``args_mapping`` keys, identity mappings, empty mappings, or a
    ``target`` pointing back to the same wrapper.

**Mapping compatibility** (:func:`~deprecate.audit.validate_mapping_compatibility`):
    Detect :func:`~deprecate.proxy.deprecated_class` wrappers whose ``args_mapping`` remaps to POSITIONAL_ONLY
    constructor params, silently degrading to ``setattr`` at call time.

**Expiry enforcement** (:func:`~deprecate.audit.validate_deprecation_expiry`):
    Detect wrappers whose ``remove_in`` version has been reached or passed, preventing zombie code from shipping past
    its scheduled removal deadline.

**Chain detection** (:func:`~deprecate.audit.validate_deprecation_chains`):
    Detect wrappers whose ``target`` is itself a deprecated callable, forming a chain that users traverse
    unnecessarily. Two chain kinds are reported via :class:`~deprecate.audit.ChainType`: ``TARGET`` (forwarding chain)
    and ``STACKED`` (composed argument or attribute mappings).

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
import importlib
import importlib.metadata
import inspect
import pkgutil
import re
import warnings
from contextlib import suppress
from dataclasses import dataclass, field, is_dataclass, replace
from enum import Enum
from functools import cached_property, wraps
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

if TYPE_CHECKING:
    from packaging.version import Version

from deprecate._types import DeprecationConfig, TargetMode, _has_deprecation_meta
from deprecate.deprecation import _DeprecatedProperty
from deprecate.proxy import _DeprecatedProxy, deprecated_class
from deprecate.utils import get_func_arguments_types_defaults


class TableStyle(str, enum.Enum):
    """Markdown table layout produced by :func:`~deprecate.audit.generate_deprecation_table`."""

    COMPACT = "compact"
    MATRIX = "matrix"


class DeprecationStatus(str, enum.Enum):
    """Lifecycle status labels used in the deprecation report's *Current Status* column.

    Each member's value is the full display string (emoji + text) rendered in the table.
    Using a ``str`` enum means members compare equal to their string values and can be
    returned wherever a plain string is expected.

    Members are *declared* from least to most urgent for readability, but this enum is **not orderable by
    urgency**: because each value starts with an emoji, the inherited ``str`` ordering operators (``<``, ``>``)
    compare Unicode codepoints of those emoji, not deprecation urgency. Do not rely on ``status_a > status_b``
    to mean "more urgent" — compare members explicitly (``status is DeprecationStatus.PAST_REMOVAL_DATE``).
    The ordering operators are overridden to raise ``TypeError`` so accidental comparisons fail loudly.

    Examples:
        >>> DeprecationStatus.ACTIVE_WARNING.value
        '📢 Deprecation Active'
        >>> DeprecationStatus.PAST_REMOVAL_DATE is DeprecationStatus.PAST_REMOVAL_DATE
        True

    """

    SCHEDULED_DEPRECATION = "🕒 Scheduled Deprecation"  # current < deprecated_in
    NO_REMOVAL_TARGET = "ℹ️ No Removal Target"  # remove_in not set
    STATUS_UNKNOWN = "⚪ Status Unknown"  # current_version unavailable
    INVALID_REMOVAL_TARGET = "⚪ Invalid Removal Target"  # remove_in unparsable
    ACTIVE_WARNING = "📢 Deprecation Active"  # current < remove_in (different base)
    REMOVAL_IMMINENT = "⏰ Removal Imminent"  # pre-release dev/a/b of remove_in base
    REMOVE_BEFORE_RELEASE = "🔔 Remove Before Release"  # RC of the remove_in base release
    PAST_REMOVAL_DATE = "💥 Past Removal Date"  # current >= remove_in

    def __lt__(self, other: object) -> bool:
        """Raise TypeError — urgency ordering is not meaningful for emoji-valued status labels."""
        raise TypeError("'<' not supported between instances of 'DeprecationStatus' — compare by identity")

    def __le__(self, other: object) -> bool:
        """Raise TypeError — urgency ordering is not meaningful for emoji-valued status labels."""
        raise TypeError("'<=' not supported between instances of 'DeprecationStatus' — compare by identity")

    def __gt__(self, other: object) -> bool:
        """Raise TypeError — urgency ordering is not meaningful for emoji-valued status labels."""
        raise TypeError("'>' not supported between instances of 'DeprecationStatus' — compare by identity")

    def __ge__(self, other: object) -> bool:
        """Raise TypeError — urgency ordering is not meaningful for emoji-valued status labels."""
        raise TypeError("'>=' not supported between instances of 'DeprecationStatus' — compare by identity")


def _normalize_version_string(version: str) -> str:
    """Normalize non-standard version strings before PEP 440 parsing.

    Newer ``packaging`` (>=22) is strict PEP 440 and rejects real-world strings that omit trailing digits
    on pre/post/dev release labels (e.g. ``"1.8.0.dev"``, ``"1.8.0dev"``, ``"1.8.0.post"``). This helper
    performs the minimum normalization needed to make such strings parseable, then defers everything else
    (label aliasing like ``alpha`` -> ``a``, case folding, separator handling) to ``packaging.Version``.

    The transformation is conservative:

    1. Strip a single leading ``v`` or ``V`` prefix (left-anchored, so only one leading ``v`` is removed).
    2. Append ``0`` to bare pre/post/dev labels that lack a trailing digit. Labels recognized:
       ``dev``, ``rc``, ``a``, ``b``, ``c``, ``alpha``, ``beta``, ``preview``, ``post``.

    The label normalization runs only over the *public* part of the version — any PEP 440 local segment
    (everything after ``+``) is split off first and re-attached verbatim, so a legitimate local like
    ``1.2.3+cuda`` is never mangled into ``1.2.3+cuda0`` by the trailing-``a`` label rule.

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
        >>> _normalize_version_string("1.2.3+cuda")
        '1.2.3+cuda'
        >>> _normalize_version_string("v1.8.0.dev+local.a")
        '1.8.0.dev0+local.a'

    """
    # Split off any PEP 440 local segment (after ``+``) so the label regex never touches it; labels like
    # ``post``/``dev`` never appear in a local segment, and running the rule over it mangles legit locals.
    public, plus, local = version.partition("+")
    # Strip a single left-anchored leading ``v``/``V`` (``lstrip("vV")`` would strip *all* leading v's).
    normalized = re.sub(r"^[vV]", "", public)
    # Append ``0`` to bare pre/post/dev labels with no trailing digit. The ordering of the alternatives
    # matters: longer labels (``alpha``, ``beta``, ``preview``) must come before their single-letter
    # forms (``a``, ``b``) so the regex prefers the longer match.
    # Use a negative lookahead for ``[0-9]`` to detect "no trailing digit"; ``(?=$|[^A-Za-z0-9])``
    # ensures the label is a whole token (e.g. ``dev`` but not ``develop``).
    pattern = re.compile(
        r"(?P<sep>\.?)(?P<label>alpha|beta|preview|post|dev|rc|a|b|c)(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    normalized = pattern.sub(lambda m: f"{m.group('sep')}{m.group('label')}0", normalized)
    return f"{normalized}{plus}{local}"


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
            Also used when ``attrs_mapping`` values point at another deprecated attribute alias.

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
        args_mapping_auto_expanded: ``args_mapping`` keys that were automatically copied from ``attrs_mapping``
            by the dataclass dual-surface expansion at decoration time.  Empty list when no auto-expansion
            occurred.  Read from :attr:`~deprecate._types.DeprecationConfig.args_mapping_auto_expanded`.
        args_mapping_positional_only: ``args_mapping`` old-key names whose remapped target is a POSITIONAL_ONLY
            constructor parameter.  Non-empty list signals that the proxy falls back to ``setattr`` for those
            keys.  Use :func:`~deprecate.audit.validate_mapping_compatibility` to filter wrappers by this
            field.  Read from :attr:`~deprecate._types.DeprecationConfig.args_mapping_positional_only`.
        inner_order_property: ``True`` when the wrapper is a plain :class:`property` whose ``fget`` carries
            ``@deprecated`` metadata — the *inner order* ``@property @deprecated`` (``@deprecated`` closer to
            ``def``).  In this order only ``fget`` warns; any setter or deleter added afterwards is built from the
            plain :class:`property` base and is silently unprotected.  The flag fires for every inner-order
            property, including the getter-only shape, because the canonical order is the outer
            ``@deprecated(...) @property`` (which produces a :class:`~deprecate.deprecation._DeprecatedProperty`
            that re-wraps every rebound accessor).  CI pipelines can filter on this field to reject the silent
            write/delete gap.  ``False`` for outer-order properties, non-property wrappers, and proxies.

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
    args_mapping_auto_expanded: list[str] = field(default_factory=list)
    args_mapping_positional_only: list[str] = field(default_factory=list)
    inner_order_property: bool = False

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


def _detect_chain_type(
    dep_info: DeprecationConfig,
    func: Callable,
    target: Any,  # noqa: ANN401
    _is_args_remap: bool,
) -> Optional[ChainType]:
    """Return the chain type when target or attrs_mapping forms a deprecation chain, else None."""
    chain_type: Optional[ChainType] = None
    if callable(target) and _has_deprecation_meta(target):
        wrp_depr_tgt = target.__deprecated__.target
        chain_type = ChainType.STACKED if wrp_depr_tgt is TargetMode.ARGS_REMAP else ChainType.TARGET
    elif _is_args_remap:
        wrapped = getattr(func, "__wrapped__", None)
        # ``target`` is always a ``TargetMode`` (or callable/None) after normalization — the legacy
        # ``target is True`` sentinel can no longer reach here, so only ARGS_REMAP marks a stacked chain.
        if (
            wrapped is not None
            and _has_deprecation_meta(wrapped)
            and wrapped.__deprecated__.target is TargetMode.ARGS_REMAP
        ):
            chain_type = ChainType.STACKED
    attrs_mapping = dep_info.attrs_mapping
    has_chained_attrs = attrs_mapping is not None and any(
        v is not None and v in attrs_mapping for v in attrs_mapping.values()
    )
    if chain_type is None and has_chained_attrs:
        chain_type = ChainType.STACKED
    return chain_type


def _validate_args_mapping(
    func: Callable,
    args_mapping: Optional[dict[str, Optional[str]]],
) -> tuple[list[str], list[str], bool]:
    """Return (invalid_args, identity_args_mapping, all_identity) for the given mapping."""
    if not args_mapping:
        return [], [], False
    if isinstance(func, _DeprecatedProxy):
        invalid_args: list[str] = []
    else:
        func_args = [arg[0] for arg in get_func_arguments_types_defaults(func)]
        invalid_args = [arg for arg in args_mapping if arg not in func_args]
    identity_args_mapping = [arg for arg, val in args_mapping.items() if arg == val]
    all_identity = len(identity_args_mapping) == len(args_mapping) > 0
    return invalid_args, identity_args_mapping, all_identity


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
        >>> from deprecate import TargetMode, deprecated, validate_deprecation_wrapper
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

        >>> @deprecated(target=TargetMode.ARGS_REMAP, deprecated_in="1.0", args_mapping={"arg": "arg"})
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
    if not _has_deprecation_meta(func):
        raise ValueError(
            f"Function {getattr(func, '__name__', func)} has missing or invalid `__deprecated__` metadata. "
            "Expected `DeprecationConfig`; ensure it is decorated with `@deprecated`."
        )

    dep_info = func.__deprecated__
    args_mapping = dep_info.args_mapping
    target = dep_info.target
    _is_args_remap = target is TargetMode.ARGS_REMAP
    _is_notify = target is TargetMode.NOTIFY

    # Self-reference: the wrapper forwards to itself. For a proxy the wrapper object is the proxy while its
    # deprecated target is the *wrapped* object, so ``target is func`` never matches — compare against
    # ``func.wrapped`` too. A proxy is self-referential only when there is *no* active remapping:
    # a proxy with non-empty ``args_mapping`` or ``attrs_mapping`` still performs meaningful transformation
    # and must not be flagged as a no-op.
    self_reference = target is not None and (
        target is func
        or (
            isinstance(func, _DeprecatedProxy)
            and target is func.wrapped
            and not dep_info.args_mapping
            and not dep_info.attrs_mapping
        )
    )
    empty_args_mapping = not args_mapping
    chain_type = _detect_chain_type(dep_info, func, target, _is_args_remap)
    invalid_args, identity_args_mapping, all_identity = _validate_args_mapping(func, args_mapping)

    # NOTIFY (target=None) is NOT no_effect — it still emits deprecation warnings.
    # When target is a different function, there's ALWAYS an effect (forwarding).
    is_self_deprecation = _is_args_remap or self_reference
    no_effect = self_reference or (is_self_deprecation and (empty_args_mapping or all_identity))

    # Construction-time `target=False` is captured in DeprecationConfig.misconfigured by the
    # decorator/proxy before normalisation; NOTIFY ignores args_mapping; ARGS_REMAP needs args_mapping.
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
        args_mapping_auto_expanded=list(getattr(dep_info, "args_mapping_auto_expanded", ())),
        args_mapping_positional_only=list(getattr(dep_info, "args_mapping_positional_only", ())),
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
    # Try importlib.metadata first (standard approach for installed packages)
    with suppress(Exception):
        return importlib.metadata.version(package_name)

    # Fall back to checking __version__ attribute
    with suppress(Exception):
        module = importlib.import_module(package_name)
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
            # An unparsable ``remove_in`` (e.g. a typo) would make the wrapper permanently un-expirable and the
            # batch gate would never flag it. Warn per skip so the misconfiguration is visible rather than silent
            # — the single-callable path raises; here a warning keeps the batch scan going for the rest.
            warnings.warn(
                f"Callable `{info.function}` has an unparsable `remove_in` version `{remove_in}`; "
                "its expiry cannot be checked until the version string is fixed.",
                stacklevel=2,
            )
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
    include_members: bool = True,
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
        include_members: If True (default), also scan deprecated class members (methods, constructors,
            classmethods, staticmethods, properties) — matching the discovery default of
            :func:`~deprecate.audit.find_deprecation_wrappers`, so the enforcement gate sees everything
            discovery and reporting see.

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
        >>> print(len(expired))  # Some functions and class members have remove_in <= "0.5"
        31

    !!! note
        - Skips callables without a ``remove_in`` field (warnings only, no removal deadline)
        - Skips callables that cannot be imported or accessed
        - Emits a ``UserWarning`` (rather than silently skipping) for callables with an unparsable ``remove_in``
        - Uses semantic versioning comparison (e.g., "1.2.3" vs "2.0.0")
        - Intended for automated checks in CI/CD pipelines
        - Can be integrated into test suites or pre-commit hooks

    """
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


def _scan_callable(
    obj: Any,  # noqa: ANN401
    module_name: str,
    qualified_name: str,
    *,
    member_name: Optional[str] = None,
    descriptor_kind: Optional[str] = None,
) -> Optional[DeprecationWrapperInfo]:
    """Emit a result if ``obj`` carries ``__deprecated__`` metadata."""
    if _has_deprecation_meta(obj):
        info = validate_deprecation_wrapper(obj)
        api_type = _classify_wrapper_api_type(obj, info, member_name=member_name, descriptor_kind=descriptor_kind)
        return replace(info, module=module_name, function=qualified_name, api_type=api_type)
    return None


def _descriptor_underlying_callables(obj: Any) -> tuple[Any, ...]:  # noqa: ANN401
    """Return the underlying callable(s) from a descriptor, or a 1-tuple of *obj* for plain callables.

    Centralises the descriptor-unwrapping logic shared by :func:`_member_has_deprecation_meta` and
    :func:`_scan_class` so each descriptor type is handled in exactly one place.

    Args:
        obj: A raw class member — may be a ``classmethod``, ``staticmethod``, ``property``,
            ``cached_property``, or a plain callable.

    Returns:
        A tuple of the underlying callable objects.  Empty when ``obj`` is a ``cached_property``
        with no ``func`` attribute (degenerate case).

    Examples:
        >>> import warnings
        >>> _descriptor_underlying_callables(classmethod(warnings.warn))  # doctest: +ELLIPSIS
        (<built-in function warn>,)
        >>> _descriptor_underlying_callables(staticmethod(warnings.warn))
        (<built-in function warn>,)

    """
    if isinstance(obj, (classmethod, staticmethod)):
        return (obj.__func__,)
    if isinstance(obj, property):
        return tuple(a for a in (obj.fget, obj.fset, obj.fdel) if a is not None)
    if isinstance(obj, cached_property):
        func = getattr(obj, "func", None)
        return (func,) if func is not None else ()
    return (obj,)


def _member_has_deprecation_meta(obj: Any) -> bool:  # noqa: ANN401
    """Return ``True`` if a class member — peeking through descriptors — carries ``__deprecated__`` metadata.

    Descriptors store the deprecation metadata on the underlying callable (``classmethod``/``staticmethod``
    ``__func__``, ``property`` accessors, ``cached_property`` ``func``), so a plain :func:`_has_deprecation_meta`
    on the raw member would miss deprecated descriptors.

    Args:
        obj: The raw class member (possibly a descriptor) to inspect.

    """
    return any(_has_deprecation_meta(c) for c in _descriptor_underlying_callables(obj))


def _scan_class(cls: Any, module_name: str, cls_name: str) -> list[DeprecationWrapperInfo]:  # noqa: ANN401
    """Scan class members, peeking through descriptors."""
    results: list[DeprecationWrapperInfo] = []
    try:
        members = _getmembers_static_compat(cls)
    except (AttributeError, TypeError):
        return results
    for attr_name, obj in members:
        # Skip private/dunder members that are *not* themselves deprecated. Deprecated private or dunder
        # members (e.g. a deprecated ``_legacy`` method or ``__eq__``) still carry ``__deprecated__`` and must
        # be surfaced so they can expire — only ``__init__`` is exempt.
        if attr_name.startswith("_") and attr_name != "__init__" and not _member_has_deprecation_meta(obj):
            continue
        qualified = f"{cls_name}.{attr_name}"
        result: Optional[DeprecationWrapperInfo] = None
        if isinstance(obj, (classmethod, staticmethod)):
            kind = "classmethod" if isinstance(obj, classmethod) else "staticmethod"
            result = _scan_callable(obj.__func__, module_name, qualified, member_name=attr_name, descriptor_kind=kind)
        elif isinstance(obj, property):
            _prop_accessor = next(
                (c for c in _descriptor_underlying_callables(obj) if _has_deprecation_meta(c)),
                None,
            )
            if _prop_accessor is not None:
                result = _scan_callable(_prop_accessor, module_name, qualified, member_name=attr_name)
                # Inner-order ``@property @deprecated``: a *plain* ``property`` (not ``_DeprecatedProperty``)
                # whose ``fget`` is deprecation-wrapped. Only ``fget`` warns; any setter/deleter rebound
                # afterwards is built from the plain ``property`` base and stays silent. Flag every such
                # wrapper (getter-only included) since the canonical form is outer ``@deprecated(...) @property``.
                if (
                    result is not None
                    and not isinstance(obj, _DeprecatedProperty)
                    and obj.fget is not None
                    and _has_deprecation_meta(obj.fget)
                ):
                    result = replace(result, inner_order_property=True)
        elif isinstance(obj, cached_property):
            result = _scan_callable(obj.func, module_name, qualified, member_name=attr_name)
        else:
            result = _scan_callable(obj, module_name, qualified, member_name=attr_name)
        if result is not None:
            results.append(result)
    return results


def _reexport_module(obj: Any) -> Optional[str]:  # noqa: ANN401
    """Return the defining module of a non-proxy deprecated wrapper, for re-export attribution.

    ``functools.wraps`` preserves ``__module__`` on ``@deprecated`` functions, so a wrapper re-exported through another
    package's ``__init__`` still reports the module it was defined in and can be attributed there rather than counted
    once per importing module.

    Proxies (:class:`~deprecate.proxy._DeprecatedProxy`) have no reliable defining module — normal lookup returns the
    proxy class's own ``__module__`` and the wrapped object commonly lives in a different module than where the proxy
    was created — so this returns ``None`` for them and the caller falls back to id-based dedup.

    """
    if isinstance(obj, _DeprecatedProxy):
        return None
    return getattr(obj, "__module__", None)


def _same_top_package(mod_a: str, mod_b: str) -> bool:
    """Return True when ``mod_a`` and ``mod_b`` share the same top-level package."""
    return mod_a.split(".")[0] == mod_b.split(".")[0]


def _scan_module(
    mod: Any,  # noqa: ANN401
    *,
    include_members: bool,
    seen: set[int],
    attribute_to_defining_module: bool = True,
) -> list[DeprecationWrapperInfo]:
    """Scan a single module for deprecated functions and class members.

    ``seen`` accumulates ``id()`` of every wrapper already recorded across the whole :func:`find_deprecation_wrappers`
    run so a re-exported wrapper (the same object bound in more than one module) is reported once, not once per
    importing module.

    When ``attribute_to_defining_module`` is True (the default), wrappers whose ``__module__`` differs from ``mod_name``
    but belongs to the same top-level package are skipped — they will be counted when the recursive walk reaches their
    defining submodule.  Pass False when the defining submodule will not be visited (package scanned with
    ``recursive=False``) so that re-exports visible on the public surface are not silently dropped.

    """
    results: list[DeprecationWrapperInfo] = []
    try:
        members = _getmembers_static_compat(mod)
    except (AttributeError, TypeError, ImportError):
        return results

    mod_name = mod.__name__ if hasattr(mod, "__name__") else str(mod)
    for name, obj in members:
        if name.startswith("_"):
            continue

        if _has_deprecation_meta(obj):
            # Attribute a re-exported wrapper to its defining module, not to every module that
            # merely re-exports it. Non-proxy wrappers carry a reliable ``__module__``; proxies do
            # not, so they fall through to the id-based dedup below.
            # Guard 1 — only apply when a recursive walk will visit the defining module.
            # Guard 2 — only skip when the defining module is in the same top-level package;
            #   if ``__module__`` points to an external package (e.g. a ``functools.partial``
            #   wrapper whose ``__module__`` was not propagated by ``functools.wraps``), the
            #   defining module will never be visited and the wrapper would be dropped silently.
            defining_module = _reexport_module(obj)
            if (
                attribute_to_defining_module
                and defining_module is not None
                and defining_module != mod_name
                and _same_top_package(defining_module, mod_name)
            ):
                continue
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            result = _scan_callable(obj, mod_name, name)
            if result is not None:
                results.append(result)
        elif include_members and inspect.isclass(obj) and getattr(obj, "__module__", None) == mod_name:
            results.extend(_scan_class(obj, mod_name, name))
    return results


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
        - Recursive scans import every submodule of the package, executing their module-level code; packages
          with heavy import-time work (GPU init, network access) make the scan correspondingly expensive
        - Skips submodules that fail to import — any exception raised by module-level code is reported as a
          ``UserWarning`` (``audit: skipped <module>: ...``) and the scan continues
        - Inspects the ``__deprecated__`` attribute set by the :func:`~deprecate.deprecated` decorator
        - Skips private/magic attributes and imports from other modules
        - Uses static member inspection to avoid scan-time side effects from dynamic attribute access

    """
    results: list[DeprecationWrapperInfo] = []
    # Track object identity across the whole scan so a wrapper re-exported through several modules
    # (the standard packaging pattern) is recorded once, not once per importing module.
    seen: set[int] = set()

    # Handle string module path
    if isinstance(module, str):
        module = importlib.import_module(module)

    # Disable the attribution filter only when the top module is a package AND recursive=False.
    # In that case the submodules that own the re-exported wrappers will never be visited, so
    # skipping re-exports would silently drop them.  For single-file modules or recursive package
    # scans the old behaviour (attribute re-exports to their defining submodule) is preserved.
    _is_package = hasattr(module, "__path__")
    results.extend(
        _scan_module(
            module,
            include_members=include_members,
            seen=seen,
            attribute_to_defining_module=not (_is_package and not recursive),
        )
    )

    if recursive and _is_package:
        try:
            packages = list(
                pkgutil.walk_packages(path=module.__path__, prefix=module.__name__ + ".", onerror=lambda x: None)
            )
        except (OSError, ImportError):
            packages = []

        for _importer, modname, _ispkg in packages:
            # Submodule top-level code can raise anything at import time (RuntimeError, OSError, KeyError from
            # env lookups, ...), not just ImportError. One broken submodule must not abort the whole scan — and
            # with it every audit gate built on top — so catch broad Exception, keep the signal via a warning,
            # and continue with the remaining submodules.
            try:
                submod = importlib.import_module(modname)
            except Exception as exc:
                warnings.warn(f"audit: skipped {modname}: {exc!r}", stacklevel=2)
                continue
            results.extend(_scan_module(submod, include_members=include_members, seen=seen))

    return results


def _resolve_table_version(
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


def _safe_parse_version(version: str) -> Optional["Version"]:
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
    if isinstance(target, _DeprecatedProxy):
        # A chained-proxy target (the New API is itself a deprecated alias). Bypass proxy
        # ``__getattr__`` interception to read the static metadata directly — ``__deprecated__``
        # is stored in the instance ``__dict__`` via ``object.__setattr__`` in ``_DeprecatedProxy.__init__``,
        # so ``object.__getattribute__`` retrieves it without triggering forwarding logic.
        return object.__getattribute__(target, "__deprecated__").name
    if callable(target):
        target_module = getattr(target, "__module__", "")
        target_name = getattr(target, "__qualname__", getattr(target, "__name__", str(target)))
        return f"{target_module}.{target_name}" if target_module else target_name
    return str(target)


def _classify_member_api_type(member_name: str, descriptor_kind: Optional[str], has_mapping: bool) -> str:
    """Classify the API type for a deprecated class member (method, constructor, descriptor)."""
    if member_name == "__init__":
        return "class constructor args" if has_mapping else "class constructor"
    if descriptor_kind == "classmethod":
        return "classmethod args" if has_mapping else "classmethod"
    if descriptor_kind == "staticmethod":
        return "staticmethod args" if has_mapping else "staticmethod"
    return "class method args" if has_mapping else "class method"


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
        return _classify_member_api_type(member_name, descriptor_kind, has_mapping)

    if isinstance(wrapped_obj, _DeprecatedProxy):
        while isinstance(wrapped_obj, _DeprecatedProxy):
            wrapped_obj = wrapped_obj.wrapped
        if inspect.isclass(wrapped_obj):
            if is_dataclass(wrapped_obj):
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


def _format_version(version: Optional[str], *, missing: str = "—") -> str:
    """Format version values with a stable ``v`` prefix for report output."""
    if not version:
        return missing
    # Strip a single left-anchored leading ``v``/``V`` (``lstrip`` would remove *all* leading v's).
    return f"v{re.sub(r'^[vV]', '', version)}"


def _get_deprecation_status(info: DeprecationWrapperInfo, current_version: Optional["Version"]) -> DeprecationStatus:
    """Classify one deprecated symbol into a report lifecycle status."""
    if current_version is None:
        return (
            DeprecationStatus.NO_REMOVAL_TARGET
            if not info.deprecated_info.remove_in
            else DeprecationStatus.STATUS_UNKNOWN
        )

    deprecated_in = _safe_parse_version(info.deprecated_info.deprecated_in)
    if deprecated_in is not None and current_version < deprecated_in:
        return DeprecationStatus.SCHEDULED_DEPRECATION

    remove_in = info.deprecated_info.remove_in
    if not remove_in:
        return DeprecationStatus.NO_REMOVAL_TARGET

    remove_version = _safe_parse_version(remove_in)
    if remove_version is None:
        return DeprecationStatus.INVALID_REMOVAL_TARGET

    if current_version >= remove_version:
        return DeprecationStatus.PAST_REMOVAL_DATE

    # Pre-release of the same base release as remove_in gets an elevated status:
    # dev/alpha/beta → REMOVAL_IMMINENT; rc → REMOVE_BEFORE_RELEASE.
    # base_version strips pre/post/dev/local markers so "1.8" == "1.8.0" compare equal.
    if current_version.is_prerelease:
        try:
            _VersionType = type(current_version)  # noqa: N806
            same_base = _VersionType(current_version.base_version) == _VersionType(remove_version.base_version)
        except Exception:
            same_base = False
        if same_base:
            if current_version.pre is not None and current_version.pre[0] == "rc":
                return DeprecationStatus.REMOVE_BEFORE_RELEASE
            return DeprecationStatus.REMOVAL_IMMINENT

    return DeprecationStatus.ACTIVE_WARNING


def _format_matrix_row(info: DeprecationWrapperInfo, col_idx: dict[str, int], n_versions: int) -> str:
    """Format one matrix-style table row for a deprecated wrapper."""
    markers: list[str] = [" "] * n_versions
    dep_in = info.deprecated_info.deprecated_in
    rem_in = info.deprecated_info.remove_in
    if dep_in and dep_in in col_idx:
        markers[col_idx[dep_in]] = "D"
    if rem_in and rem_in in col_idx:
        i = col_idx[rem_in]
        markers[i] = "R" if markers[i] == " " else "D/R"
    return (
        "| "
        f"`{_format_report_symbol(info)}` | "
        f"{_format_report_api_type(info)} | "
        f"`{_format_report_target(info.deprecated_info.target)}` | " + " | ".join(markers) + " |"
    )


def generate_deprecation_table(
    module: Union[Any, str],  # noqa: ANN401
    current_version: Optional[str] = None,
    recursive: bool = True,
    style: Union[TableStyle, str] = TableStyle.COMPACT,
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
        style = TableStyle(style)
    except ValueError as err:
        raise ValueError(
            f"Invalid style {style!r}. Expected one of: {', '.join(s.value for s in TableStyle)}."
        ) from err

    resolved_version, parsed_version = _resolve_table_version(module, current_version=current_version)
    if _wrappers is None:
        _wrappers = find_deprecation_wrappers(module, recursive=recursive, include_members=include_members)
    wrappers = sorted(
        _wrappers,
        key=_report_row_sort_key,
    )

    if style == TableStyle.COMPACT:
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
                f"{_format_version(info.deprecated_info.deprecated_in)} | "
                f"{_format_version(info.deprecated_info.remove_in)} | "
                f"{_get_deprecation_status(info, parsed_version).value} |"
            )
    else:
        version_map: dict[str, Optional[Version]] = {}
        for info in wrappers:
            for version in (info.deprecated_info.deprecated_in, info.deprecated_info.remove_in):
                if version and version not in version_map:
                    version_map[version] = _safe_parse_version(version)

        sorted_versions = sorted(
            version_map,
            key=lambda version: (
                version_map[version] is None,
                version_map[version] if version_map[version] is not None else version,
            ),
        )
        version_headers = [_format_version(version) for version in sorted_versions]
        header_row = "| Original API | API Type | New API | " + " | ".join(version_headers) + " |"
        divider_row = "| :--- | :--- | :--- | " + " | ".join(":---:" for _ in version_headers) + " |"
        col_idx = {v: i for i, v in enumerate(sorted_versions)}
        n_versions = len(sorted_versions)
        rows = [header_row, divider_row]

        for info in wrappers:
            rows.append(_format_matrix_row(info, col_idx, n_versions))

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
    2. **STACKED chains**: Multiple ``@deprecated(target=TargetMode.ARGS_REMAP, ...)`` decorators are stacked on the
       same function with argument mappings that should be collapsed, or a callable ``target`` is itself a
       self-deprecation (``target=TargetMode.ARGS_REMAP``) requiring mapping composition.

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


def validate_mapping_compatibility(
    module: Union[Any, str],  # noqa: ANN401
    recursive: bool = True,
) -> list[DeprecationWrapperInfo]:
    """Return wrappers whose ``args_mapping`` remaps deprecated names to POSITIONAL_ONLY constructor params.

    A non-empty ``args_mapping_positional_only`` on the returned ``DeprecationWrapperInfo`` means the
    proxy falls back to ``setattr`` at call time instead of forwarding the remapped kwarg.  Use this
    validator in CI to detect :func:`~deprecate.proxy.deprecated_class` configurations that silently
    degrade to attribute assignment and may not behave as expected on all target class types.

    Args:
        module: A Python module or package to scan.  Accepts an imported module object or a dotted
            module path string.
        recursive: When ``True`` (default) recursively scan submodules.

    Returns:
        List of ``DeprecationWrapperInfo`` instances whose ``args_mapping_positional_only`` field is
        non-empty.  Returns an empty list when no incompatibilities are found.

    Examples:
        >>> from deprecate import validate_mapping_compatibility
        >>> import tests.collection_deprecate as col
        >>> results = validate_mapping_compatibility(col, recursive=False)
        >>> len(results) > 0  # DepPositionalOnly remaps to a POSITIONAL_ONLY param
        True
        >>> results[0].function
        'DepPositionalOnly'

    """
    return [
        info for info in find_deprecation_wrappers(module, recursive=recursive) if info.args_mapping_positional_only
    ]


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
