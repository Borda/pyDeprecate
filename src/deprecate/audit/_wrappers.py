"""Single-wrapper inspection: the :class:`DeprecationWrapperInfo` result and the checks that read one wrapper.

:func:`~deprecate.audit.validate_deprecation_wrapper` judges a wrapper's configuration in isolation;
:func:`~deprecate.audit.validate_deprecation_chains` and :func:`~deprecate.audit.validate_mapping_compatibility` filter
the structured results by the chain and mapping fields recorded here.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import inspect
import types
import warnings
from dataclasses import dataclass, field, is_dataclass
from enum import Enum
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

if TYPE_CHECKING:
    pass

from deprecate._types import DeprecationConfig, TargetMode, _has_deprecation_meta, get_deprecation_config
from deprecate.proxy import _DeprecatedProxy
from deprecate.utils import get_func_arguments_types_defaults


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
        deprecated_info: The ``__deprecation_config__`` attribute from the decorator,
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
            ``@deprecated(...) @property`` (which produces a :class:`~deprecate._properties._DeprecatedProperty`
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


def _detect_chain_type(
    dep_info: DeprecationConfig,
    func: Callable,
    target: Any,  # noqa: ANN401
    _is_args_remap: bool,
) -> Optional[ChainType]:
    """Return the chain type when target or attrs_mapping forms a deprecation chain, else None."""
    chain_type: Optional[ChainType] = None
    target_config = get_deprecation_config(target) if callable(target) else None
    if target_config is not None:
        wrp_depr_tgt = target_config.target
        chain_type = ChainType.STACKED if wrp_depr_tgt is TargetMode.ARGS_REMAP else ChainType.TARGET
    elif _is_args_remap:
        wrapped = getattr(func, "__wrapped__", None)
        # ``target`` is always a ``TargetMode`` (or callable/None) after normalization — the legacy
        # ``target is True`` sentinel can no longer reach here, so only ARGS_REMAP marks a stacked chain.
        wrapped_config = get_deprecation_config(wrapped) if wrapped is not None else None
        if wrapped_config is not None and wrapped_config.target is TargetMode.ARGS_REMAP:
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


def _scan_module_meta(mod: Any) -> DeprecationWrapperInfo:  # noqa: ANN401
    """Build :class:`~deprecate.audit.DeprecationWrapperInfo` for a deprecated module.

    Called only when the module itself carries ``__deprecation_config__`` metadata (set by
    :func:`~deprecate.module.deprecated_module`).  Bypasses callable introspection entirely because a module is not a
    callable and has no signature to validate.

    Args:
        mod: The module object carrying ``__deprecation_config__`` metadata.  The caller is responsible for
            verifying that :func:`~deprecate._types._has_deprecation_meta` returned ``True`` before calling this
            function.

    Returns:
        A :class:`~deprecate.audit.DeprecationWrapperInfo` with ``api_type="module"`` and all validation fields set
        to safe defaults (no invalid args, no misconfig).

    """
    dep_info = get_deprecation_config(mod)
    if dep_info is None:
        raise ValueError(f"Module {getattr(mod, '__name__', mod)!r} no longer carries valid deprecation metadata.")
    # Read via __dict__ to avoid triggering the PEP 562 __getattr__ hook.
    # str(mod) must be lazy — eager evaluation calls _module_repr which accesses __spec__ via getattr
    # and may trigger the module's own __getattr__ before __spec__ is in __dict__.
    _raw_name = mod.__dict__.get("__name__")
    mod_name: str = _raw_name if _raw_name is not None else str(mod)
    # A module has no callable name — its identity lives entirely in ``module``. Leave ``function``
    # empty (never a ``"(module)"`` sentinel): ``_format_report_symbol`` then renders just the module
    # name, and ``_subject_noun`` picks the ``Module`` noun from ``api_type``, so no ``(module)`` label
    # can leak into report rows or expiry error messages.
    return DeprecationWrapperInfo(
        function="",
        module=mod_name,
        deprecated_info=dep_info,
        invalid_args=[],
        empty_args_mapping=True,
        identity_args_mapping=[],
        self_reference=False,
        no_effect=False,
        misconfigured_target=False,
        all_identity=False,
        chain_type=None,
        api_type="module",
        args_mapping_auto_expanded=[],
        args_mapping_positional_only=[],
    )


def validate_deprecation_wrapper(func: Union[Callable, types.ModuleType]) -> DeprecationWrapperInfo:
    """Validate a deprecated callable or module wrapper and return structured metadata.

    This is a development tool to check if deprecated wrappers are configured correctly and will have the intended
    effect. It examines the ``__deprecation_config__`` attribute set by the :func:`~deprecate.deprecated` decorator
    and identifies
    configurations that would result in zero impact:

    - args_mapping keys that don't exist in the function's signature
    - Empty or None args_mapping (no argument remapping)
    - Identity mappings where key equals value (e.g., {'arg': 'arg'})
    - Target pointing to the same function (self-reference)
    - target=None with no args_mapping (just warns, no forwarding)

    Args:
        func: The deprecated wrapper to validate. Accepts either a callable decorated with ``@deprecated`` or a module
            object passed through :func:`deprecated_module`. Must have a ``__deprecation_config__`` attribute.

    Returns:
        :class:`~deprecate.audit.DeprecationWrapperInfo`: Dataclass with validation results:
            - function: Name of the wrapper being validated
            - deprecated_info: The typed :class:`~deprecate._types.DeprecationConfig` metadata from
              ``__deprecation_config__``
            - invalid_args: List of args_mapping keys not in wrapper signature
            - empty_args_mapping: True if args_mapping is None or empty
            - identity_args_mapping: List of args where key equals value (no effect)
            - self_reference: True if target is the same as the wrapper
            - no_effect: True if wrapper has zero impact (all checks combined)
            - empty_deprecated_in: True when ``deprecated_in`` is absent or empty

    Raises:
        ValueError: If the wrapper has missing or invalid ``__deprecation_config__`` metadata (expected
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
    if inspect.ismodule(func):
        if not _has_deprecation_meta(func):
            raise ValueError(
                f"Module {getattr(func, '__name__', func)!r} has missing or invalid `__deprecation_config__` "
                "(or legacy `__deprecated__`) metadata. Ensure `deprecated_module()` was called on it."
            )
        return _scan_module_meta(func)
    if not _has_deprecation_meta(func):
        raise ValueError(
            f"Function {getattr(func, '__name__', func)} has missing or invalid `__deprecation_config__` "
            "(or legacy `__deprecated__`) metadata. Expected `DeprecationConfig`; ensure it is decorated"
            " with `@deprecated`."
        )

    dep_info = get_deprecation_config(func)
    if dep_info is None:
        raise ValueError(f"{getattr(func, '__name__', func)!r} no longer carries valid deprecation metadata.")
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


def _format_report_symbol(info: DeprecationWrapperInfo) -> str:
    """Return a stable fully-qualified label for report rows and error messages.

    Modules carry their name in :attr:`~deprecate.audit.DeprecationWrapperInfo.module` and leave
    ``function`` empty, so a plain ``module.function`` join would append a trailing dot. Guard against
    an empty ``function`` (or an empty ``module``) so the rendered label is always clean — e.g.
    ``tests.collection_modules.old_math`` for a deprecated module, never ``...old_math.`` or ``...(module)``.

    """
    if info.module and info.function:
        return f"{info.module}.{info.function}"
    return info.module or info.function


#: Subject noun per ``api_type`` for wrappers that are not callables; every other kind reads as *Callable*.
_SUBJECT_NOUNS = {"module": "Module", "data": "Instance"}


def _subject_noun(info: DeprecationWrapperInfo) -> str:
    """Return the grammatical subject noun for a deprecated wrapper, driven by ``api_type``.

    Centralises noun selection so error and report text says *Module* for a deprecated module, *Instance* for a
    :func:`~deprecate.proxy.deprecated_instance` proxy (``api_type="data"`` -- the wrapped object is not called), and
    *Callable* for everything else, instead of inlining ``info.function`` (which is empty for modules).

    """
    return _SUBJECT_NOUNS.get(info.api_type, "Callable")


def _format_subject(info: DeprecationWrapperInfo) -> str:
    """Return the backticked subject phrase used in expiry error messages.

    Renders the noun from :func:`_subject_noun` (``Callable``, ``Instance`` or ``Module``) followed by the backtick-
    quoted fully-qualified label from :func:`_format_report_symbol` — for example a callable subject reads *Callable*
    then the quoted name, and a module subject reads *Module* then the quoted module path. Centralising this here means
    no expiry site inlines ``info.function`` directly, so an empty or sentinel module label can never leak into the
    text.

    """
    return f"{_subject_noun(info)} `{_format_report_symbol(info)}`"


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

    if inspect.ismodule(wrapped_obj):
        return "module"

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
