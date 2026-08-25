"""Shared type definitions for pyDeprecate.

This module provides typed interfaces consumed by the decorator, proxy, and audit modules so that static analysers can
catch schema mismatches at analysis time rather than silently returning ``None`` at runtime.

"""

import copy
import threading
import types
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Generic, Literal, Optional, Protocol, TypeVar, Union, runtime_checkable

if TYPE_CHECKING:
    from typing_extensions import TypeGuard


class TargetMode(Enum):
    """Selects ``@deprecated`` behaviour when no callable replacement is provided.

    Attributes:
        AUTO: Decoration-time fallback that resolves the mode when ``target`` is omitted on the
            :func:`~deprecate.deprecated` front door -- "infer the mode from the configuration". It exists only
            to give an omitted ``target`` a sensible default; **prefer passing an explicit mode**
            (:attr:`NOTIFY`, :attr:`ARGS_REMAP`, :attr:`ATTRS_REMAP`) or a callable target so the intent is
            visible at the call site. Resolves before the wrapper/proxy is built: a mapping present selects
            :attr:`ARGS_REMAP` (``args_mapping``) or :attr:`ATTRS_REMAP` (``attrs_mapping``, class path); no
            mapping selects :attr:`NOTIFY` (callable path) or the proxy's unset target (class path). Passing
            ``AUTO`` explicitly to ``@deprecated`` is identical to omitting ``target``; the strict factories
            (:func:`~deprecate.routine.deprecated_callable`, :func:`~deprecate.proxy.deprecated_class`) reject
            it with :class:`TypeError` -- they require an explicit mode. ``AUTO`` is never stored in
            :class:`~deprecate._types.DeprecationConfig` -- audit metadata always records the resolved mode.
        NOTIFY: Notify-only deprecation -- warn on every call; original body executes unchanged. Replaces
            ``target=None``. Combining an explicit ``NOTIFY`` with ``args_mapping``, ``attrs_mapping``, or
            ``args_extra`` is contradictory and emits a :class:`UserWarning` (the mapping/extras are ignored;
            :class:`TypeError` is planned in ``v1.0``) -- with ``@deprecated``, omit ``target`` instead to let
            :attr:`AUTO` pick the matching remap mode.
        ARGS_REMAP: Deprecate argument names only -- warn only when deprecated argument names are passed; remaps
            kwargs via ``args_mapping`` before calling the original body. Replaces ``target=True``. Selected
            automatically by :attr:`AUTO` when ``args_mapping`` is provided to ``@deprecated`` without an
            explicit ``target``; using this mode without ``args_mapping`` emits a :class:`UserWarning` today,
            and :class:`TypeError` is planned in ``v1.0``.
        ATTRS_REMAP: Selective per-attribute deprecation -- warn only when a deprecated attribute alias listed in
            ``attrs_mapping`` is accessed; all other attribute access is forwarded silently. Proxy-specific mode:
            only valid for :func:`~deprecate.proxy.deprecated_class`; raises :class:`TypeError` on
            :func:`~deprecate.deprecated` decorated functions/methods. Analogous to :attr:`ARGS_REMAP` but for
            attribute access instead of call arguments. Selected automatically by :attr:`AUTO` when
            ``attrs_mapping`` is non-empty and no explicit ``target`` is provided.

    Examples:
        >>> from deprecate import TargetMode
        >>> TargetMode.AUTO.value
        'auto'
        >>> TargetMode.NOTIFY.value
        'notify'
        >>> TargetMode.ARGS_REMAP.value
        'args_remap'
        >>> TargetMode.ATTRS_REMAP.value
        'attrs_remap'

    """

    AUTO = "auto"
    NOTIFY = "notify"
    ARGS_REMAP = "args_remap"
    ATTRS_REMAP = "attrs_remap"

    @classmethod
    def _from_legacy(
        cls,
        target: Union[None, bool],
        *,
        stacklevel: Optional[int] = 3,
    ) -> "TargetMode":
        """Convert ``target`` sentinel (``None`` / ``True`` / ``False``) to :class:`~deprecate._types.TargetMode`.

        Backwards-compatibility shim for the :func:`~deprecate.deprecated` decorator.  Each sentinel is mapped to its
        modern equivalent and a deprecation warning is emitted.  Pass ``stacklevel=None`` to suppress all warnings.

        Args:
            target: Legacy sentinel — ``None`` → :attr:`~deprecate._types.TargetMode.NOTIFY`,
                ``True`` → :attr:`~deprecate._types.TargetMode.ARGS_REMAP`,
                ``False`` → :attr:`~deprecate._types.TargetMode.NOTIFY` (not a valid mode; :class:`TypeError` in v1.0).
            stacklevel: Stack level forwarded to :func:`warnings.warn`.  Pass ``None`` to suppress all warnings
                entirely.  Defaults to ``3``.

        Returns:
            The corresponding :class:`~deprecate._types.TargetMode` member.

        Raises:
            TypeError: If ``target`` is anything other than ``None``, ``True``, or ``False``.

        Examples:
            >>> TargetMode._from_legacy(None, stacklevel=None)
            <TargetMode.NOTIFY: 'notify'>
            >>> TargetMode._from_legacy(True, stacklevel=None)
            <TargetMode.ARGS_REMAP: 'args_remap'>
            >>> TargetMode._from_legacy(False, stacklevel=None)
            <TargetMode.NOTIFY: 'notify'>

        """
        if target is None:
            if stacklevel is not None:
                warnings.warn(
                    "target=None is deprecated since `v0.8`; use `TargetMode.NOTIFY` instead."
                    " Will be removed in `v1.0`.",
                    FutureWarning,
                    stacklevel=stacklevel,
                )
            return cls.NOTIFY
        if target is True:
            if stacklevel is not None:
                warnings.warn(
                    "target=True is deprecated since `v0.8`; use `TargetMode.ARGS_REMAP` instead."
                    " Will be removed in `v1.0`.",
                    FutureWarning,
                    stacklevel=stacklevel,
                )
            return cls.ARGS_REMAP
        if target is False:
            if stacklevel is not None:
                warnings.warn(
                    "`target=False` is not a valid deprecation mode and will be treated as `TargetMode.NOTIFY`."
                    " This will be `TypeError` in `v1.0`.",
                    UserWarning,
                    stacklevel=stacklevel,
                )
            return cls.NOTIFY
        raise TypeError(
            f"`TargetMode._from_legacy` accepts only None, True, or False; got {type(target).__name__}: {target!r}."
        )

    @classmethod
    def _from_legacy_proxy(
        cls,
        target: Any,  # noqa: ANN401
        *,
        args_mapping: Optional[Mapping[str, Optional[str]]] = None,
        stacklevel: Optional[int] = 3,
    ) -> "Union[Callable[..., Any], TargetMode, None]":
        """Normalise a proxy-specific legacy ``target`` sentinel for :func:`~deprecate.proxy.deprecated_class`.

        ``True`` and ``False`` are invalid for the proxy context and normalise to ``None`` — the proxy's
        unset-target value (or :attr:`~deprecate._types.TargetMode.ARGS_REMAP` when ``args_mapping`` is
        non-empty) after emitting a warning, so any configured mapping still auto-resolves like an omitted
        target. All other values pass through unchanged.  Pass ``stacklevel=None`` to suppress warnings.

        Args:
            target: Raw ``target`` value from the caller.  Only ``True`` and ``False`` trigger normalisation;
                any other value is returned as-is.
            args_mapping: The ``args_mapping`` dict supplied to :func:`~deprecate.deprecated_class`, or ``None`` when
                not provided. When ``target=True`` and ``args_mapping`` is non-empty the result is
                :attr:`~deprecate._types.TargetMode.ARGS_REMAP` instead of
                :attr:`~deprecate._types.TargetMode.NOTIFY`, and a :class:`FutureWarning` is emitted.
            stacklevel: Stack level forwarded to :func:`warnings.warn`.  Pass ``None`` to suppress all warnings.
                Defaults to ``3``.

        Returns:
            :attr:`~deprecate._types.TargetMode.ARGS_REMAP` when ``target=True`` and ``args_mapping`` is non-empty;
            ``None`` (the proxy's unset-target value) when ``target`` was ``True`` (without ``args_mapping``) or
            ``False``; otherwise ``target`` unchanged.

        Examples:
            >>> TargetMode._from_legacy_proxy(True, stacklevel=None) is None
            True
            >>> TargetMode._from_legacy_proxy(True, args_mapping={"old": "new"}, stacklevel=None)
            <TargetMode.ARGS_REMAP: 'args_remap'>
            >>> TargetMode._from_legacy_proxy(False, stacklevel=None) is None
            True
            >>> TargetMode._from_legacy_proxy(None) is None
            True
            >>> TargetMode._from_legacy_proxy(TargetMode.NOTIFY)
            <TargetMode.NOTIFY: 'notify'>

        """
        if target is True:
            if args_mapping:
                if stacklevel is not None:
                    warnings.warn(
                        "`target=True` with `args_mapping` will resolve to `TargetMode.ARGS_REMAP`."
                        " Will be `TypeError` in `v1.0`.",
                        FutureWarning,
                        stacklevel=stacklevel,
                    )
                return cls.ARGS_REMAP
            if stacklevel is not None:
                warnings.warn(
                    "`target=True` without `args_mapping` resolves to `TargetMode.NOTIFY`"
                    " (warns on every access). Will be `TypeError` in `v1.0`.",
                    FutureWarning,
                    stacklevel=stacklevel,
                )
            # ``None`` (the proxy's unset value), not NOTIFY: legacy sentinels behave like an omitted
            # target, so a configured ``attrs_mapping`` still auto-resolves instead of being flagged as an
            # explicit-NOTIFY conflict.
            return None
        if target is False:
            if stacklevel is not None:
                warnings.warn(
                    "`target=False` is not valid for `deprecated_class()`. Will be `TypeError` in `v1.0`.",
                    UserWarning,
                    stacklevel=stacklevel,
                )
            return None
        return target

    @classmethod
    def _validate(
        cls,
        mode: "TargetMode",
        source_name: str,
        args_mapping: Optional[Mapping[str, Optional[str]]] = None,
        args_extra: Optional[dict[str, Any]] = None,
        *,
        stacklevel: Optional[int] = 2,
    ) -> bool:
        """Validate :class:`~deprecate._types.TargetMode` against supplied configuration and emit misconfig warnings.

        Checks three misconfiguration combinations and emits a :class:`UserWarning` for each one found.
        These warnings will become :class:`TypeError` in ``v1.0``.

        Misconfiguration warnings are always emitted via :func:`warnings.warn` with ``UserWarning`` — they are
        construction-time guards independent of the runtime ``stream`` callable used for deprecation notices.

        Args:
            mode: The resolved :class:`~deprecate._types.TargetMode` to validate.
            source_name: ``__name__`` of the decorated source callable, used in warning messages.
            args_mapping: The ``args_mapping`` dict supplied to :func:`~deprecate.deprecated`, or ``None`` when not
                provided.
            args_extra: The ``args_extra`` dict supplied to :func:`~deprecate.deprecated`, or ``None`` when not
                provided.
            stacklevel: Stack level forwarded to :func:`warnings.warn` so that reported locations point at the
                decorator application site.  Pass ``None`` to suppress all warnings.  Defaults to ``2``.

        Returns:
            ``True`` if any misconfiguration was detected, ``False`` if the configuration is valid.

        Examples:
            >>> TargetMode._validate(TargetMode.ARGS_REMAP, "my_func", args_mapping=None, stacklevel=None)
            True
            >>> TargetMode._validate(TargetMode.NOTIFY, "my_func", args_mapping={"old": "new"}, stacklevel=None)
            True
            >>> TargetMode._validate(TargetMode.NOTIFY, "my_func", args_extra={"bias": 1}, stacklevel=None)
            True
            >>> TargetMode._validate(TargetMode.NOTIFY, "my_func", stacklevel=None)
            False

        """
        messages = []
        if mode is cls.ARGS_REMAP and not args_mapping:
            messages.append(
                f"`@deprecated(target=TargetMode.ARGS_REMAP)` on `{source_name}` requires "
                "`args_mapping` to specify which arguments are being renamed. Without it the "
                "decorator has zero effect. This will be `TypeError` in `v1.0`."
            )
        if mode is cls.NOTIFY and args_mapping:
            messages.append(
                f"`@deprecated(target=TargetMode.NOTIFY)` on `{source_name}` ignores "
                "`args_mapping`. Omit `target` to auto-resolve to `TargetMode.ARGS_REMAP`, pass "
                "`TargetMode.ARGS_REMAP` explicitly, or pass a callable target to forward the call. "
                "This will be `TypeError` in `v1.0`."
            )
        if mode is cls.NOTIFY and args_extra:
            messages.append(
                f"`@deprecated(target=TargetMode.NOTIFY)` on `{source_name}` ignores "
                "`args_extra`. Use a callable target to forward with extra arguments. "
                "This will be `TypeError` in `v1.0`."
            )
        if stacklevel is not None:
            for msg in messages:
                warnings.warn(msg, UserWarning, stacklevel=stacklevel)
        return bool(messages)

    @classmethod
    def _validate_proxy(
        cls,
        mode: "Union[TargetMode, Callable[..., Any], None]",
        source_name: str,
        attrs_mapping: Optional[Mapping[str, Optional[str]]] = None,
        args_mapping: Optional[Mapping[str, Optional[str]]] = None,
        args_extra: Optional[dict[str, Any]] = None,
        *,
        stacklevel: Optional[int] = 2,
    ) -> bool:
        """Validate proxy-specific configuration: ``attrs_mapping`` combinations.

        Catches misconfiguration pairs involving ``attrs_mapping`` that the existing
        :meth:`~deprecate._types.TargetMode._validate` does not cover (it handles only ``args_mapping`` /
        ``args_extra`` and is used by the function-decorator path as well).

        Misconfiguration warnings will become :class:`TypeError` in ``v1.0``.

        Args:
            mode: Resolved target (:class:`~deprecate._types.TargetMode` member, callable, or ``None``).
            source_name: ``__name__`` of the decorated class, for warning messages.
            attrs_mapping: The ``attrs_mapping`` dict passed to :func:`~deprecate.proxy.deprecated_class`, or
                ``None``.
            args_mapping: The ``args_mapping`` dict, forwarded here so we can detect the
                ``ATTRS_REMAP + args_mapping`` combination.
            args_extra: The ``args_extra`` dict, forwarded here so we can detect the
                ``ATTRS_REMAP + args_extra`` combination — ``ATTRS_REMAP`` governs attribute access only and
                does not honour call-time kwarg injection.
            stacklevel: Forwarded to :func:`warnings.warn`. Pass ``None`` to suppress warnings. Defaults to ``2``.

        Returns:
            ``True`` if any misconfiguration was detected, ``False`` otherwise.

        Examples:
            >>> # Explicit NOTIFY + attrs_mapping is contradictory — only TargetMode.AUTO (the factory
            >>> # default, i.e. an omitted ``target``) auto-resolves a mapping to ATTRS_REMAP.
            >>> TargetMode._validate_proxy(TargetMode.NOTIFY, "Cls", attrs_mapping={"a": "b"}, stacklevel=None)
            True
            >>> TargetMode._validate_proxy(TargetMode.ATTRS_REMAP, "Cls", attrs_mapping=None, stacklevel=None)
            True
            >>> TargetMode._validate_proxy(TargetMode.ATTRS_REMAP, "Cls", attrs_mapping={}, stacklevel=None)
            True
            >>> TargetMode._validate_proxy(TargetMode.ATTRS_REMAP, "Cls", attrs_mapping={"a": "b"}, stacklevel=None)
            False
            >>> TargetMode._validate_proxy(
            ...     TargetMode.ARGS_REMAP, "Cls",
            ...     args_mapping={"old": "new"}, attrs_mapping={"a": "b"}, stacklevel=None,
            ... )
            True
            >>> TargetMode._validate_proxy(
            ...     TargetMode.ATTRS_REMAP, "Cls",
            ...     args_mapping={"old": "new"}, attrs_mapping={"a": "b"}, stacklevel=None,
            ... )
            True
            >>> TargetMode._validate_proxy(
            ...     TargetMode.ATTRS_REMAP, "Cls",
            ...     attrs_mapping={"a": "b"}, args_extra={"bias": 1}, stacklevel=None,
            ... )
            True

        """
        messages = []
        # Explicit NOTIFY + attrs_mapping is contradictory: only TargetMode.AUTO (the factory default)
        # auto-resolves a mapping — an explicitly chosen NOTIFY must not be silently overridden, and the
        # mapping is ignored at runtime instead.
        if mode is cls.NOTIFY and attrs_mapping:
            messages.append(
                f"`deprecated_class(target=TargetMode.NOTIFY)` on `{source_name}` ignores "
                "`attrs_mapping`. Omit `target` to auto-resolve to `TargetMode.ATTRS_REMAP`, or pass "
                "`TargetMode.ATTRS_REMAP` explicitly. This will be `TypeError` in `v1.0`."
            )
        if mode is cls.ARGS_REMAP and args_mapping and attrs_mapping:
            messages.append(
                f"`deprecated_class` on `{source_name}` has both `args_mapping` and `attrs_mapping` "
                "configured with `target=TargetMode.ARGS_REMAP`. `ARGS_REMAP` governs call-time argument "
                "renames only; `attrs_mapping` is inactive in this mode. "
                "`DeprecationConfig.target` no longer reflects that `attrs_mapping` is also active. "
                "Pass an explicit callable `target=<class>` to activate both mappings on the same proxy. "
                "This will be `TypeError` in `v1.0`."
            )
        if mode is cls.ATTRS_REMAP and not attrs_mapping:
            messages.append(
                f"`deprecated_class(target=TargetMode.ATTRS_REMAP)` on `{source_name}` requires "
                "`attrs_mapping` to specify which attribute names are deprecated. Without it the "
                "proxy has zero selective effect. This will be `TypeError` in `v1.0`."
            )
        if mode is cls.ATTRS_REMAP and args_mapping:
            messages.append(
                f"`deprecated_class(target=TargetMode.ATTRS_REMAP)` on `{source_name}` ignores `args_mapping`. "
                "`ATTRS_REMAP` only governs attribute access; argument renames on `__call__` are not applied. "
                "Use `target=<class>` (with both mappings) or `TargetMode.ARGS_REMAP` (with `args_mapping` only). "
                "This will be `TypeError` in `v1.0`."
            )
        if mode is cls.ATTRS_REMAP and args_extra:
            messages.append(
                f"`deprecated_class(target=TargetMode.ATTRS_REMAP)` on `{source_name}` ignores `args_extra`. "
                "`ATTRS_REMAP` only governs attribute access; call-time kwarg injection is not applied. "
                "Use `target=<class>` to forward calls with extra arguments. "
                "This will be `TypeError` in `v1.0`."
            )
        if attrs_mapping is not None and len(attrs_mapping) == 0:
            messages.append(
                f"`deprecated_class` on `{source_name}` received `attrs_mapping={{}}` (empty dict). "
                "An empty mapping has no effect — remove it or add deprecated attribute names. "
                "This will be `TypeError` in `v1.0`."
            )
        if stacklevel is not None:
            for msg in messages:
                warnings.warn(msg, UserWarning, stacklevel=stacklevel)
        return bool(messages)


@dataclass(frozen=True)
class DeprecationConfig:
    """Static deprecation metadata attached to deprecated callables as ``__deprecated__``.

    All fields are always set — both :func:`~deprecate.deprecated`-decorated functions and
    :class:`~deprecate.proxy._DeprecatedProxy` objects use this unified schema.

    Attributes:
        deprecated_in: Version string when the callable was deprecated.
        remove_in: Version string when the callable will be removed.
        name: Display the name of the deprecated source (function or class name).
        target: Normalised target — ``None`` (default), :attr:`~deprecate._types.TargetMode.NOTIFY`,
            :attr:`~deprecate._types.TargetMode.ARGS_REMAP`, :attr:`~deprecate._types.TargetMode.ATTRS_REMAP`,
            a callable, or a :class:`types.ModuleType` (stored by :func:`~deprecate.module.deprecated_module`
            when a redirect module is provided). Legacy sentinels (``True``/``False``) are normalised at
            decoration time and never stored verbatim.
        args_extra: Optional kwargs injected into forwarded calls; stored for audit visibility.
        misconfigured: ``True`` when an invalid raw target sentinel (``False``) was passed at decoration time.
            Audit tools surface this via
            :attr:`~deprecate.audit.DeprecationWrapperInfo.misconfigured_target`.
        docstring_style: Docstring notice output style when ``update_docstring=True``.
        message_template: Optional custom warning-message template (``%``-style placeholders) that overrides the
            built-in templates at warn time. ``None`` (default) keeps the built-in template selected for the active
            scenario. Audit tools may surface this for introspection. See :func:`~deprecate.deprecation.deprecated`
            for the available placeholders (e.g. ``%(source_name)s``, ``%(target_path)s``, ``%(deprecated_in)s``).
            Note: for :func:`~deprecate.module.deprecated_module` specifically this field stores the fully-rendered
            warning text (the template already substituted at decoration time), not the raw caller-supplied template
            that the callable and proxy factories store here verbatim.
        attrs_mapping: Optional mapping of deprecated attribute names to their canonical replacement names (or
            ``None`` for warn-only).  Set by :func:`~deprecate.proxy.deprecated_class` when
            selective per-attribute deprecation is enabled.  Non-``None`` redirect targets must be existing attribute
            names on the target class when ``target`` is provided, or on the wrapped source class otherwise.
            Non-cyclic chains are stored and surfaced by audit tooling.  ``None`` (default) means the proxy uses its
            blanket-warning behaviour (every attribute access emits a warning).
        args_mapping: Optional dict remapping argument names; values are a plain string (new argument name) or
            ``None`` (drop the argument).
        args_mapping_auto_expanded: Keys that were automatically copied from ``attrs_mapping`` into
            ``args_mapping`` by the dataclass dual-surface expansion in
            :class:`~deprecate.proxy._DeprecatedProxy`.  Empty tuple when no auto-expansion occurred.
            Populated at decoration time; read by audit tools to distinguish user-supplied mappings from
            auto-generated ones.
        args_mapping_positional_only: ``args_mapping`` old-key names whose remapped target name is a
            POSITIONAL_ONLY parameter in the target class constructor.  Calling the proxy with such
            keys as keyword arguments would raise ``TypeError`` without the positional split applied
            by the proxy call path (see :attr:`target_positional_only`).  Empty tuple when all
            remapped targets are kwarg-accessible.  Populated at decoration time; surfaced by
            :func:`~deprecate.audit.validate_mapping_compatibility`.
        target_positional_only: Names of POSITIONAL_ONLY parameters on the forwarding target callable
            used with :func:`~deprecate.deprecated`, including ``self`` and ``cls`` when they are
            declared positional-only.  Non-empty when a ``@deprecated(target=fn)`` call found ``fn``
            declares at least one positional-only parameter.  ``self`` and ``cls`` are excluded from
            the :class:`UserWarning` message but included here so the split-dispatch gate fires even
            when the only positional-only parameter is the instance or class receiver (e.g.
            ``def __init__(self, /): ...``).  The call dispatcher splits these out of
            ``resolved_kwargs`` and forwards them positionally so the target call does not raise
            ``TypeError``.  For :class:`~deprecate.proxy._DeprecatedProxy` objects this holds the
            POSITIONAL_ONLY parameter names of the *active* class constructor (target class when
            configured, wrapped class otherwise) so the proxy call path can apply the same
            positional split (see also :attr:`args_mapping_positional_only`).  Empty frozenset
            for non-callable targets.
        target_positional_only_order: Full parameter-name sequence of the forwarding target callable,
            in declaration order.  Pre-computed at decoration time alongside
            :attr:`target_positional_only` so the call dispatcher can iterate in declaration order
            without calling ``inspect.signature`` on every dispatch.  Not included in ``repr``.
            Empty tuple when ``target_positional_only`` is empty.
        source_positional_only: Names of POSITIONAL_ONLY parameters declared by the decorated
            *source* callable itself.  The wrapper converts positional args to kwargs internally,
            so whenever the dispatcher invokes the source body (NOTIFY, ARGS_REMAP, and the
            migrated-caller short-circuit) it must split these names back out of the resolved
            kwargs and pass them positionally — otherwise every call would raise ``TypeError``.
            Mirrors :attr:`target_positional_only` on the source side.  Empty frozenset when the
            source declares no ``/``.
        source_positional_only_order: Full parameter-name sequence of the decorated source in
            declaration order; the source-side twin of :attr:`target_positional_only_order`.
            Not included in ``repr``.  Empty tuple when :attr:`source_positional_only` is empty.
        source_var_positional_prefix: Number of named positional parameters declared before the
            source's ``*args``.  Used by the forwarding dispatcher to compute the surplus
            positional tail (``args[prefix:]``) that must be forwarded to a callable target
            rather than silently dropped.  ``0`` when the source declares no var-positional
            parameter.
        target_all_param_names: All parameter names of the forwarding target callable (including any
            ``*args`` / ``**kwargs`` names), pre-computed at decoration time so the call-time kwarg
            validation in :func:`~deprecate._dispatch._prepare_target_call` does not re-inspect the
            target on every forwarded call.  ``None`` when facts are not precomputed (manual
            ``DeprecationConfig`` construction or non-callable target); ``frozenset()`` for zero-arg
            callables.  Internal call-time cache — excluded from ``repr`` and equality so audit output
            and stored-config comparisons stay stable.
        target_accepts_var_positional: ``True`` when the forwarding target declares ``*args``.  Pre-computed
            decoration-time replacement for the per-call ``inspect.getfullargspec`` probe.  Internal cache —
            excluded from ``repr`` and equality.
        target_accepts_var_keyword: ``True`` when the forwarding target declares ``**kwargs``.  Pre-computed
            decoration-time replacement for the per-call ``inspect.getfullargspec`` probe.  Internal cache —
            excluded from ``repr`` and equality.

    Note:
        ``@dataclass(frozen=True)`` blocks attribute *rebinding* on the config object itself, but the
        stored ``args_mapping`` and ``attrs_mapping`` dicts remain mutable.  Code that reads these fields
        must not modify them in place; the decorator copies them defensively at decoration time.

    """

    deprecated_in: str = ""
    remove_in: str = ""
    name: str = ""
    target: Optional[Union[Callable[..., Any], "TargetMode", types.ModuleType]] = None
    args_mapping: Optional[dict[str, Optional[str]]] = None
    args_extra: Optional[dict[str, Any]] = None
    misconfigured: bool = False
    docstring_style: Literal["rst", "mkdocs"] = "rst"
    message_template: Optional[str] = None
    attrs_mapping: Optional[dict[str, Optional[str]]] = None
    args_mapping_auto_expanded: tuple[str, ...] = field(default_factory=tuple)
    args_mapping_positional_only: tuple[str, ...] = field(default_factory=tuple)
    target_positional_only: frozenset[str] = field(default_factory=frozenset)
    target_positional_only_order: tuple[str, ...] = field(default_factory=tuple, repr=False)
    source_positional_only: frozenset[str] = field(default_factory=frozenset)
    source_positional_only_order: tuple[str, ...] = field(default_factory=tuple, repr=False)
    source_var_positional_prefix: int = 0
    target_all_param_names: Optional[frozenset[str]] = field(default=None, repr=False, compare=False)
    target_accepts_var_positional: bool = field(default=False, repr=False, compare=False)
    target_accepts_var_keyword: bool = field(default=False, repr=False, compare=False)

    @property
    def template_mgs(self) -> Optional[str]:
        """Deprecated read alias for :attr:`message_template` (renamed in ``v0.12``; removed in ``v1.0``).

        The stored field is :attr:`message_template`; audit code that still reads ``__deprecated__.template_mgs`` keeps
        working through this alias.  The old name was a typo (``mgs`` for ``msg``).

        """
        return self.message_template


@runtime_checkable
class _HasDeprecationMeta(Protocol):
    """Structural type for any callable that carries ``__deprecated__`` metadata.

    Both ``@deprecated``-decorated functions and :class:`~deprecate.proxy._DeprecatedProxy` instances satisfy this
    protocol once the decorator has been applied.

    Module narrowing via :func:`_has_deprecation_meta` is metadata-only and does not imply a callable ``__call__``.

    Used as a TypeGuard target so that a ``hasattr`` guard narrows the type of an arbitrary callable to one whose
    ``__deprecated__`` attribute is typed — eliminating the need for a ``cast`` after the guard.

    """

    __deprecated__: DeprecationConfig
    __name__: str

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Call the deprecated object."""
        raise NotImplementedError


def _has_deprecation_meta(obj: Any) -> "TypeGuard[_HasDeprecationMeta]":  # noqa: ANN401
    """Return ``True`` if *obj* carries typed :class:`~deprecate._types.DeprecationConfig` metadata.

    Using this as a guard narrows the type of *obj* from ``Any`` / ``Callable`` to
    :class:`~deprecate._types._HasDeprecationMeta`,
    allowing direct typed access to ``obj.__deprecated__`` without a ``cast``.

    Args:
        obj: Any object to test.

    Returns:
        ``True`` if ``__deprecated__`` exists and is a :class:`~deprecate._types.DeprecationConfig`;
        ``False`` otherwise.

    """
    # ``getattr(..., default)`` only swallows ``AttributeError``; a foreign object encountered during a
    # recursive audit scan whose ``__getattr__``/``__getattribute__`` raises something else (e.g. a lazy
    # proxy raising ``RuntimeError``) would otherwise crash the whole scan. Treat any failure as "no meta".
    #
    # Module objects: a module deprecated via ``deprecated_module()`` (see ``deprecate.module``) intercepts
    # attribute access by reassigning its ``__class__`` to a ``types.ModuleType`` subclass whose
    # ``__getattribute__`` emits a ``FutureWarning`` — but only for *public* names; underscore-prefixed
    # names (including ``__deprecated__``) are explicitly exempt, so the ``getattr`` below is warning-free on
    # a ``deprecated_module`` wrapper. The ``mod.__dict__.get("__deprecated__")`` probing in
    # ``deprecate.audit`` (``_scan_module`` / ``_scan_module_meta``) is belt-and-braces for *foreign*
    # third-party modules with arbitrary hooks, not required for our own wrappers.
    try:
        meta = getattr(obj, "__deprecated__", None)
    except Exception:
        return False
    return isinstance(meta, DeprecationConfig)


_T_co = TypeVar("_T_co", covariant=True)


@runtime_checkable
class DeprecationProxy(Protocol, Generic[_T_co]):
    """Public type produced by :func:`~deprecate.proxy.deprecated_class` / :func:`~deprecate.proxy.deprecated_instance`.

    Documents the public contract that deprecated proxies satisfy:

    - ``__wrapped__`` points back to the source object/class for static-analysis tools (Sphinx, mypy, IDEs).
    - ``__deprecated__`` carries :class:`DeprecationConfig` metadata consumed by audit tools.
    - Attribute, item, and call access is transparently forwarded to the wrapped source.

    The type parameter ``T`` describes the instance type returned by calling the proxy (i.e. the target
    type for class-forwarding proxies). It is purely a static affordance: runtime instances of
    :class:`~deprecate.proxy._DeprecatedProxy` satisfy this Protocol structurally, so no explicit ``isinstance``
    check is required.

    !!! note "Protocol vs implementation"
        ``DeprecationProxy`` (public, this Protocol) describes the contract; :class:`~deprecate.proxy._DeprecatedProxy`
        (private) is the concrete runtime class that satisfies it. Annotate against the former, never import
        the latter.

    !!! note "Static-typing scope"
        This Protocol is the type to *annotate* against; it is not what the public functions declare as their
        return type. ``deprecated_class`` and ``deprecated_instance`` return the concrete
        :class:`~deprecate.proxy._DeprecatedProxy`, whose forwarded dunders (``int()``, ``with``, ``await``, ...)
        a narrow Protocol would hide from type checkers. No call shape narrows to ``DeprecationProxy[T]`` on its
        own, so to let the target type flow into call sites, annotate the one site that needs it —
        ``OldCls: DeprecationProxy[NewCls] = deprecated_class(target=NewCls, ...)(OldClsDef)``. The decorator form
        ``@deprecated_class(target=NewCls, ...)`` is typed as the raw source class because mypy does not rebind a
        class definition to an instance return type; annotating it would require returning a real class
        instead of a proxy.

    !!! warning "``isinstance`` only"
        This is a *data* Protocol (it declares attributes, not just methods), so only ``isinstance`` is
        supported at runtime. ``issubclass(SomeType, DeprecationProxy)`` raises :class:`TypeError`, as it does
        for every ``@runtime_checkable`` Protocol with non-method members.

    Example:
        >>> from deprecate import deprecated_class, DeprecationProxy
        >>> class NewColor:
        ...     def __init__(self, code: int) -> None:
        ...         self.code = code
        >>> @deprecated_class(target=NewColor, deprecated_in="1.0", remove_in="2.0", stream=None)
        ... class OldColor:
        ...     pass
        >>> isinstance(OldColor, DeprecationProxy)
        True
        >>> OldColor.__wrapped__ is OldColor.__wrapped__  # breadcrumb back to the source class
        True

        An explicit annotation on the assignment is what gives mypy the target type — spelled out as
        ``DeprecationProxy[NewColor]``, ``OldColorAlias(1)`` is typed as ``NewColor``:

        >>> _decorator = deprecated_class(target=NewColor, deprecated_in="1.0", remove_in="2.0", stream=None)
        >>> class _OldColorSource:
        ...     pass
        >>> OldColorAlias: DeprecationProxy[NewColor] = _decorator(_OldColorSource)
        >>> isinstance(OldColorAlias(1), NewColor)
        True

    """

    __wrapped__: Any
    __deprecated__: DeprecationConfig
    __signature__: Any

    def __call__(self, *args: Any, **kwargs: Any) -> _T_co:  # noqa: ANN401
        """Forward a call to the wrapped source (e.g. ``OldColor(1)`` -> ``NewColor(1)``)."""
        raise NotImplementedError

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        """Forward attribute access (e.g. ``OldColor.RED`` -> ``NewColor.RED``)."""
        raise NotImplementedError

    def __getitem__(self, key: Any) -> Any:  # noqa: ANN401
        """Forward item access (e.g. ``proxy['key']`` -> ``source['key']``)."""
        raise NotImplementedError


@dataclass
class _ProxyConfig:
    """Private mutable runtime state for :class:`~deprecate.proxy._DeprecatedProxy`.

    This is an internal type — not exported from the package and not referenced outside of :mod:`deprecate.proxy`.

    Attributes:
        obj: The wrapped (source) object.
        stream: Callable used to emit warnings, or ``None`` to suppress them.
        num_warns: Maximum number of warnings to emit; ``-1`` means unlimited.
        read_only: When ``True``, write operations through the proxy raise :class:`AttributeError`.
        args_extra: Optional dict of extra keyword arguments merged into forwarded calls after ``args_mapping`` has
            been applied. Ignored when the proxy is in :attr:`~deprecate._types.TargetMode.NOTIFY` mode.
        message_template: Optional custom warning-message template (``%``-style placeholders) that overrides the
            built-in templates at warn time. ``None`` (default) keeps the built-in templates. See
            :func:`~deprecate.proxy.deprecated_class` for the placeholder catalogue.
        attrs_mapping: Optional mapping of deprecated attribute names to their canonical replacement names (or
            ``None`` for warn-only).  When set, the proxy emits warnings only on access to listed names and forwards
            all other reads/writes/deletes silently.  See :func:`~deprecate.proxy.deprecated_class` for full semantics.
        skip_if: ``bool`` or zero-argument callable returning ``bool``.  When it evaluates ``True`` at access time,
            the deprecation machinery is inactive for that operation — no warning, no attribute redirect, no argument
            remapping, no target forwarding; the proxy transparently serves the wrapped source object.  May be
            consulted more than once per proxy operation, so keep the callable cheap and stable.
        warned: Mutable counter tracking how many global (callable-level) warnings have been emitted so far.
        warned_args: Per-argument warning counts for argument-level deprecations. Keys are deprecated argument names;
            values are emission counts.
        lock: Guards the warn-quota check-then-act sequence (read counter → decide to emit → increment) so that
            concurrent first accesses cannot all pass the ``num_warns`` gate and each emit a warning.  Excluded from
            ``repr`` and equality — it is runtime machinery, not state identity.  A fresh lock is created on copy /
            deepcopy rather than transferring the original mutex state.

    """

    obj: Any
    stream: Optional[Callable[..., None]]
    num_warns: int
    read_only: bool
    args_extra: Optional[dict[str, Any]] = None
    message_template: Optional[str] = None
    attrs_mapping: Optional[dict[str, Optional[str]]] = None
    skip_if: Union[bool, Callable[[], bool]] = False
    warned: int = 0
    warned_args: dict[str, int] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def __deepcopy__(self, memo: dict[int, Any]) -> "_ProxyConfig":
        return replace(
            self,
            obj=copy.deepcopy(self.obj, memo),
            warned_args=copy.deepcopy(self.warned_args, memo),
            lock=threading.Lock(),  # fresh lock — mutex state is not transferable
        )

    def __getstate__(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "lock"}

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self.lock = threading.Lock()  # fresh lock — mutex state is not transferable


@dataclass
class _WrapperState:
    """Private mutable runtime state for ``@deprecated``-decorated callables.

    This is an internal type — not exported from the package and not referenced outside of :mod:`deprecate.deprecation`.

    Attributes:
        called: Total invocation count, including calls where the warning was suppressed.
        warned_calls: Number of callable-level deprecation warnings emitted so far.
        warned_args: Per-argument warning counts for argument-level deprecations. Keys are deprecated argument names;
            values are emission counts.
        warned_misconfigured: ``True`` after the one-time misconfiguration UserWarning has been emitted at call time.
        lock: Guards the warn-quota check-then-act sequence (read counter → decide to emit → increment) so that
            concurrent first calls cannot all pass the ``num_warns`` gate and each emit a warning.  Only the warn
            path takes this lock; the ``called`` counter is a best-effort diagnostic and stays lock-free.  Excluded
            from ``repr`` and equality — it is runtime machinery, not state identity.

    """

    called: int = 0
    warned_calls: int = 0
    warned_args: dict[str, int] = field(default_factory=dict)
    warned_misconfigured: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


@runtime_checkable
class _DeprecatedCallable(Protocol):
    """Structural type for a ``@deprecated``-decorated callable with mutable runtime state.

    This protocol describes the shape of a function or method after the ``@deprecated`` decorator has been applied. It
    includes both static metadata (``__deprecated__``) and mutable runtime state (``_state``).

    Used to type-safely access ``_state`` on decorated callables without casting.

    """

    __deprecated__: DeprecationConfig
    _state: _WrapperState

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Call the deprecated callable."""
        raise NotImplementedError


@dataclass
class _CallPlan:
    """Resolved dispatch plan returned by :func:`deprecate._dispatch._build_call_plan`.

    Captures the outcome of all decision-making shared between the sync and async wrappers in :func:`deprecated`: state
    mutation, kwargs assembly, warning emission, target resolution.  The wrapper consumes this struct to decide whether
    to invoke ``source`` or a forwarded target callable, and with which kwargs shape.

    Attributes:
        short_circuit: ``True`` when the wrapper must call ``source`` directly (no warning, no remap, no target
            lookup) — this is the "migrated caller using the new arg name" fast path before any warning logic runs.
            The wrapper calls ``source(**resolved_kwargs)`` for sources without ``*args``, or
            ``source(*args, **original_kwargs)`` when the source declares a var-positional parameter so that
            extra positional arguments are forwarded correctly.  When ``True``, :attr:`resolved_kwargs` equals the
            kwargs after :func:`_update_kwargs_with_args`, before defaults / remap / extras are applied.
        original_kwargs: A shallow copy of the wrapper's incoming ``kwargs`` (before positional args were converted to
            keyword form).  Used by the var-positional branch when no argument-rename reason fired, so that the source
            sees its original positional/keyword shape.
        resolved_kwargs: Final kwargs after defaults injection, ``args_mapping`` remap, and ``args_extra`` merge when
            the normal planning path runs.  On the :attr:`short_circuit` path, this instead holds the kwargs produced
            immediately after :func:`_update_kwargs_with_args`, and that is what the wrapper passes to ``source``.
        reason_argument: Subset of ``args_mapping`` whose old-arg names appeared in the caller's kwargs.  Non-empty
            value means the argument-rename warning fired (or was suppressed by ``num_warns`` quota).  Empty when the
            warning reason was callable-deprecation or when no rename was triggered.
        target_func: Resolved target callable when forwarding is required, or ``None`` when the source body should be
            invoked instead.  ``None`` for :attr:`TargetMode.NOTIFY` and :attr:`TargetMode.ARGS_REMAP`; a validated
            callable when the user passed a forwarding target.

    """

    short_circuit: bool
    original_kwargs: dict[str, Any]
    resolved_kwargs: dict[str, Any]
    reason_argument: dict[str, Optional[str]] = field(default_factory=dict)
    target_func: Optional[Callable[..., Any]] = None
