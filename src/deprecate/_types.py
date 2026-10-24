"""Shared type definitions for pyDeprecate.

This module provides typed interfaces consumed by the decorator, proxy, and audit modules so that static analysers can
catch schema mismatches at analysis time rather than silently returning ``None`` at runtime.

"""

import copy
import threading
import types
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Generic, Literal, Optional, Protocol, TypeVar, Union, runtime_checkable


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


@dataclass(frozen=True)
class DeprecationConfig:
    """Static deprecation metadata attached to deprecated callables as ``__deprecation_config__``.

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
        as_exception: Whether this deprecation raises :class:`~deprecate._fatal.DeprecatedError` instead of
            emitting a warning.  ``None`` (default) defers to the process-wide ``deprecate.AS_EXCEPTIONS``
            switch; ``True`` is always fatal; ``False`` means "not fatal by default" and still yields to a
            ``True`` global, so a consumer's strict run cannot be opted out of by an upstream wrapper.  See
            :func:`~deprecate._fatal._resolve_as_exception` for the resolution rule.  A fatal deprecation
            bypasses ``num_warns`` entirely — it raises on every call rather than on the first one.
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
    as_exception: Optional[bool] = None
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

        The stored field is :attr:`message_template`; audit code that still reads
        ``__deprecation_config__.template_mgs`` keeps working through this alias.  The old name was a typo (``mgs`` for
        ``msg``).

        """
        return self.message_template


#: Attribute name holding the frozen :class:`DeprecationConfig` metadata. Introduced in ``v0.13`` to free
#: ``__deprecated__`` for its PEP 702-conformant role (a plain message string) — see :func:`get_deprecation_config`.
_DEPRECATION_CONFIG_ATTR = "__deprecation_config__"
#: Legacy attribute name: pre-``v0.13`` releases stored the :class:`DeprecationConfig` directly on
#: ``__deprecated__``. Only :func:`get_deprecation_config` still reads it, as a fallback, removed at ``v1.0``.
_LEGACY_DEPRECATED_ATTR = "__deprecated__"


def get_deprecation_config(obj: Any) -> Optional[DeprecationConfig]:  # noqa: ANN401
    """Return the :class:`~deprecate._types.DeprecationConfig` metadata attached to *obj*, if any.

    This is the supported external read path for a wrapper's deprecation metadata, replacing direct
    ``obj.__deprecated__`` access: since ``v0.13``, ``__deprecated__`` holds a plain PEP-702-conformant message
    string, not the config object — see the ``v0.12-to-v0.13`` migration guide.

    Args:
        obj: Any object to inspect — a ``@deprecated``-wrapped function/method, a class or instance proxy from
            :func:`~deprecate.proxy.deprecated_class` / :func:`~deprecate.proxy.deprecated_instance`, or a module
            deprecated via :func:`~deprecate.module.deprecated_module`.

    Returns:
        The attached :class:`~deprecate._types.DeprecationConfig`, or ``None`` when *obj* carries no pyDeprecate
        metadata.

    Examples:
        >>> from deprecate import deprecated, get_deprecation_config
        >>> def new_func(): pass
        >>> @deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
        ... def old_func(): pass
        >>> get_deprecation_config(old_func).target is new_func
        True
        >>> get_deprecation_config(new_func) is None
        True

    """
    # ``getattr(..., default)`` only swallows ``AttributeError``; a foreign object encountered during a
    # recursive audit scan whose ``__getattr__``/``__getattribute__`` raises something else (e.g. a lazy
    # proxy raising ``RuntimeError``) would otherwise crash the whole scan. Treat any failure as "no meta".
    #
    # Module objects: a module deprecated via ``deprecated_module()`` (see ``deprecate.module``) intercepts
    # attribute access by reassigning its ``__class__`` to a ``types.ModuleType`` subclass whose
    # ``__getattribute__`` emits a ``FutureWarning`` — but only for *public* names; underscore-prefixed
    # names (including both attribute names here) are explicitly exempt, so the lookups below are
    # warning-free on a ``deprecated_module`` wrapper.
    try:
        meta = getattr(obj, _DEPRECATION_CONFIG_ATTR, None)
    except Exception:
        meta = None
    if isinstance(meta, DeprecationConfig):
        return meta
    # Legacy fallback: an object built by a pre-v0.13 pyDeprecate release (or a mixed-version install) still
    # carries the config on ``__deprecated__`` directly. Remove at v1.0.
    try:
        legacy_meta = getattr(obj, _LEGACY_DEPRECATED_ATTR, None)
    except Exception:
        return None
    return legacy_meta if isinstance(legacy_meta, DeprecationConfig) else None


def _has_deprecation_meta(obj: Any) -> bool:  # noqa: ANN401
    """Return ``True`` if *obj* carries current or legacy deprecation metadata.

    This predicate only checks for metadata; it does not narrow the object's type. Legacy objects store their
    configuration in ``__deprecated__`` and lack ``__deprecation_config__``, so consumers must resolve the value
    with :func:`get_deprecation_config`.

    Args:
        obj: Any object to test.

    Returns:
        ``True`` if :func:`get_deprecation_config` resolves a :class:`~deprecate._types.DeprecationConfig` for
        *obj*; ``False`` otherwise.

    """
    return get_deprecation_config(obj) is not None


_T_co = TypeVar("_T_co", covariant=True)


@runtime_checkable
class DeprecationProxy(Protocol, Generic[_T_co]):
    """Public type produced by :func:`~deprecate.proxy.deprecated_class` / :func:`~deprecate.proxy.deprecated_instance`.

    Documents the public contract that deprecated proxies satisfy:

    - ``__wrapped__`` points back to the source object/class for static-analysis tools (Sphinx, mypy, IDEs).
    - ``__deprecation_config__`` carries :class:`DeprecationConfig` metadata consumed by audit tools;
      ``__deprecated__`` is a PEP-702-conformant message string.
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
        return type. ``deprecated_instance`` returns the concrete :class:`~deprecate.proxy._DeprecatedProxy`, whose
        forwarded dunders (``int()``, ``with``, ``await``, ...) a narrow Protocol would hide from type checkers;
        annotate an instance proxy as ``DeprecationProxy[T]`` to name the type calling it produces.
        ``deprecated_class`` types a class alias as a class instead: ``deprecated_class(target=NewCls, ...)(OldCls)``
        is ``type[NewCls]`` whichever class it wraps, and without a class ``target`` the alias keeps the wrapped
        class's own type. The ``@deprecated_class(...)`` decorator form is read as the class statement by mypy, while
        pyright applies the same return type as the call form. A class alias is therefore not assignable to
        ``DeprecationProxy``. To reach the proxy-only attributes of a class alias statically, use
        :func:`get_deprecation_config`, or ``cast(DeprecationProxy[Any], OldCls)`` at the one site that needs them.

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

        An instance proxy is where the type parameter pays off — spelled out as ``DeprecationProxy[NewColor]``,
        calling the proxy is typed as producing a ``NewColor``:

        >>> from deprecate import deprecated_instance
        >>> def make_color(code: int) -> NewColor:
        ...     return NewColor(code)
        >>> legacy_factory: DeprecationProxy[NewColor] = deprecated_instance(
        ...     make_color, name="legacy_factory", deprecated_in="1.0", remove_in="2.0", stream=None
        ... )
        >>> isinstance(legacy_factory(1), NewColor)
        True

    """

    __wrapped__: Any
    __deprecated__: str
    __deprecation_config__: DeprecationConfig
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
class _WrapperState:
    """Private mutable runtime state for the deprecation warning budgets of callables, proxies and modules.

    This is an internal type — not exported from the package. Callable wrappers use all counters; a
    :class:`~deprecate.proxy._DeprecatedProxy` keeps one in :attr:`_ProxyConfig.state` and uses ``warned_calls`` for
    its proxy-wide budget and ``warned_args`` for per-argument and ``__attr__:``-prefixed attribute budgets; module
    deprecation uses ``warned_calls`` and ``lock`` for its module-wide ``num_warns`` budget.  A copy, a deep copy or an
    unpickled state snapshots every counter into its own ``warned_args`` dict with a fresh lock.

    Attributes:
        warned_calls: Number of callable-level deprecation warnings emitted so far.
        warned_args: Per-argument warning counts for argument-level deprecations. Keys are deprecated argument names;
            values are emission counts.
        warned_misconfigured: ``True`` once the one-time misconfiguration UserWarning is claimed at call time; the
            call that sets it emits the warning.
        lock: Guards the check-then-act sequences of the warn quota (read counter → decide to emit → increment) and
            of the ``warned_misconfigured`` flag, so that concurrent first calls cannot all pass a gate and each emit
            a warning.  Only the warn paths take this lock.  Excluded from ``repr`` and equality — it is runtime
            machinery, not state identity.

    """

    warned_calls: int = 0
    warned_args: dict[str, int] = field(default_factory=dict)
    warned_misconfigured: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def __copy__(self) -> "_WrapperState":
        """Snapshot the counters into an independent state: its own ``warned_args`` dict and a fresh lock."""
        return replace(self, warned_args=dict(self.warned_args), lock=threading.Lock())

    def __deepcopy__(self, memo: dict[int, Any]) -> "_WrapperState":
        """Snapshot like :meth:`__copy__`; the counters are ints keyed by str, so a dict copy is already deep."""
        return self.__copy__()

    def __getstate__(self) -> dict[str, Any]:
        """Return the pickle state without the lock — a mutex cannot be pickled."""
        return {k: v for k, v in self.__dict__.items() if k != "lock"}

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Restore the counters and create a fresh lock — mutex state is not transferable.

        A state pickled while the write-only ``called`` counter still existed carries that key; it is dropped.

        """
        self.__dict__.update({k: v for k, v in state.items() if k != "called"})
        self.lock = threading.Lock()


@runtime_checkable
class _DeprecatedCallable(Protocol):
    """Structural type for a ``@deprecated``-decorated callable with mutable runtime state.

    This protocol describes the shape of a function or method after the ``@deprecated`` decorator has been applied. It
    includes static metadata (``__deprecated__`` message string, ``__deprecation_config__`` config object) and mutable
    runtime state (``_state``).

    Used to type-safely access ``_state`` on decorated callables without casting.

    """

    __deprecated__: str
    __deprecation_config__: DeprecationConfig
    _state: _WrapperState

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Call the deprecated callable."""
        raise NotImplementedError


@dataclass(frozen=True)
class _CallFacts:
    """Signature facts of a deprecated callable and its forwarding target, fixed once the callable is decorated.

    A ``@deprecated`` wrapper reads these on every call instead of inspecting the source or the target signature
    again.  :func:`deprecate._dispatch._build_call_facts` derives them; the default values describe a source with no
    positional-only parameters and no ``*args`` forwarding to nothing.  The eight fields mirrored on
    :class:`DeprecationConfig` keep the names they carry there.

    Attributes:
        source_positional_names: Names of the source's positional parameters before ``*args``, in order.
        source_has_var_positional: ``True`` when the source declares ``*args``.
        source_var_positional_prefix: Number of named positional parameters preceding the source's ``*args``.
        source_positional_only: Names of the source's POSITIONAL_ONLY parameters.
        source_positional_only_order: Every source parameter name in declaration order when the source has
            POSITIONAL_ONLY parameters, otherwise empty.
        target_all_param_names: Every parameter name of a callable target; ``None`` derives the names from the
            target on each forwarded call.
        target_accepts_var_positional: ``True`` when the target declares ``*args``.
        target_accepts_var_keyword: ``True`` when the target declares ``**kwargs``.
        target_positional_only: Names of the target's POSITIONAL_ONLY parameters.
        target_positional_only_order: Every target parameter name in declaration order when the target has
            POSITIONAL_ONLY parameters, otherwise empty.
        target_positional_names: The target's leading positional parameter names, up to its ``*args`` or its first
            keyword-only parameter, which a surplus ``*args`` tail is forwarded after; ``None`` when the target
            signature could not be read at decoration (the forwarded call then raises
            :attr:`target_signature_error`), or when the facts were built by hand (the call reads the target).
        source_defaults: Each source parameter that has a default, mapped to that default, in declaration order.  Left
            out of equality and the hash, so the record stays hashable whatever the default values are.
        target_signature_error: The ``TypeError`` or ``ValueError`` that reading a callable target's signature raised
            at decoration, without its traceback; the forwarded call that needs the signature raises a copy of it.
            ``None`` when the read succeeded or the target is a :class:`TargetMode`.  Left out of equality.
        source_positional_limit: Derived, never passed in: the most positional arguments the source accepts, or
            ``None`` when it declares ``*args`` and accepts any number.  The one place the arity rule lives; the
            arity check and the call-plan fast path compare ``len(args)`` against it.  Left out of equality.

    """

    source_positional_names: tuple[str, ...] = ()
    source_has_var_positional: bool = False
    source_var_positional_prefix: int = 0
    source_positional_only: frozenset[str] = frozenset()
    source_positional_only_order: tuple[str, ...] = ()
    target_all_param_names: Optional[frozenset[str]] = None
    target_accepts_var_positional: bool = False
    target_accepts_var_keyword: bool = False
    target_positional_only: frozenset[str] = frozenset()
    target_positional_only_order: tuple[str, ...] = ()
    target_positional_names: Optional[tuple[str, ...]] = None
    source_defaults: Mapping[str, Any] = field(default_factory=dict, compare=False)
    target_signature_error: Optional[Exception] = field(default=None, compare=False)
    source_positional_limit: Optional[int] = field(init=False, default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        """Derive :attr:`source_positional_limit` from the source's positional parameters."""
        limit = None if self.source_has_var_positional else len(self.source_positional_names)
        object.__setattr__(self, "source_positional_limit", limit)


@dataclass
class _ProxyConfig:
    """Private mutable runtime state for :class:`~deprecate.proxy._DeprecatedProxy`.

    This is an internal type — not exported from the package and not referenced outside of :mod:`deprecate.proxy`.
    Its import path and pickle layout are part of the proxy pickle contract: :meth:`__getstate__` writes the 0.14 layout
    and :meth:`__setstate__` loads it, so proxy pickles stay loadable across 0.14 and this release in both directions.

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
        state: The warn budget — counters and the lock guarding their check-then-increment — shared machinery with
            the ``@deprecated`` and module paths (:func:`~deprecate.messaging._consume_warn_budget`).  The
            proxy-wide counter is ``state.warned_calls``; per-argument and ``__attr__:`` attribute keys live in
            ``state.warned_args``.  A copy of the config snapshots the counters (see :class:`_WrapperState`).
        facts: Call-time facts of the active constructor; only ``target_positional_only`` and
            ``target_positional_only_order`` are used, by the positional-only split of a remapped call.

    """

    obj: Any
    stream: Optional[Callable[..., None]]
    num_warns: int
    read_only: bool
    args_extra: Optional[dict[str, Any]] = None
    message_template: Optional[str] = None
    attrs_mapping: Optional[dict[str, Optional[str]]] = None
    skip_if: Union[bool, Callable[[], bool]] = False
    state: _WrapperState = field(default_factory=_WrapperState)
    facts: _CallFacts = field(default_factory=_CallFacts)

    def __deepcopy__(self, memo: dict[int, Any]) -> "_ProxyConfig":
        """Deep-copy the wrapped object and snapshot the warn budget; the frozen ``facts`` are shared."""
        return replace(self, obj=copy.deepcopy(self.obj, memo), state=copy.deepcopy(self.state, memo))

    def __getstate__(self) -> dict[str, Any]:
        """Return the pickle state in the 0.14 layout, so a proxy pickled by this release also loads on 0.14.

        The budget is written as flat ``warned`` / ``warned_args`` counters; neither ``state`` nor ``facts`` is
        written, because 0.14 restores the config by updating its ``__dict__`` and cannot import either private class.
        Nothing is lost: a proxy reads only ``warned_calls`` and ``warned_args`` of its state (never
        ``warned_misconfigured``), :meth:`__setstate__` moves the flat counters back into a fresh
        :class:`_WrapperState`, and ``_reconstruct_proxy`` refills ``facts`` from the frozen metadata, which mirrors its
        positional-only split.

        """
        payload = {key: value for key, value in self.__dict__.items() if key not in ("state", "facts")}
        payload["warned"] = self.state.warned_calls
        payload["warned_args"] = dict(self.state.warned_args)
        return payload

    def __setstate__(self, payload: dict[str, Any]) -> None:
        """Restore a pickled config, migrating the 0.14 layout.

        A 0.14 payload carries the budget as flat ``warned`` / ``warned_args`` fields and no ``state`` or ``facts``; the
        counters move into a :class:`_WrapperState` and ``facts`` starts empty (``_reconstruct_proxy`` refills it from
        the metadata).  A ``lock`` key, which no payload should carry, is dropped.

        """
        payload = dict(payload)
        if "state" not in payload:
            payload["state"] = _WrapperState(
                warned_calls=payload.pop("warned", 0), warned_args=payload.pop("warned_args", {})
            )
        payload.pop("lock", None)
        payload.setdefault("facts", _CallFacts())
        self.__dict__.update(payload)


@dataclass
class _CallPlan:
    """Resolved dispatch plan returned by :func:`deprecate._dispatch._build_call_plan`.

    Captures the outcome of all decision-making shared between the sync and async wrappers in :func:`deprecated`: state
    mutation, kwargs assembly, warning emission, target resolution.  The wrapper consumes this struct to decide whether
    to invoke ``source`` or a forwarded target callable, and with which kwargs shape.

    Attributes:
        short_circuit: ``True`` when the wrapper must call ``source`` directly (no warning, no remap, no target
            lookup) — this is the "migrated caller using the new arg name" fast path before any warning logic runs.
            Such a call needs no rename and no injected extras, so it is always :attr:`verbatim` too and the wrapper
            calls ``source(*args, **original_kwargs)``.
        original_kwargs: A shallow copy of the wrapper's incoming ``kwargs`` (before positional args were converted to
            keyword form).  Used by the var-positional branch when no argument-rename reason fired, so that the source
            sees its original positional/keyword shape.
        resolved_kwargs: Final kwargs after positional-to-keyword conversion, defaults injection, ``args_mapping``
            remap, and ``args_extra`` merge when the wrapper rewrites the call.  On a :attr:`verbatim` plan nothing is
            converted, so this holds the caller's keyword arguments unchanged.
        reason_argument: Subset of ``args_mapping`` whose old-arg names appeared in the caller's kwargs.  Non-empty
            value means the argument-rename warning fired (or was suppressed by ``num_warns`` quota).  Empty when the
            warning reason was callable-deprecation or when no rename was triggered.
        target_func: Resolved target callable when forwarding is required, or ``None`` when the source body should be
            invoked instead.  ``None`` for :attr:`TargetMode.NOTIFY` and :attr:`TargetMode.ARGS_REMAP`; a validated
            callable when the user passed a forwarding target.
        verbatim: ``True`` when the call needs no forwarding target, no argument rename and no ``args_extra``
            injection, so the wrapper invokes ``source(*args, **original_kwargs)`` exactly as the caller made the
            call.  A ``functools.singledispatch`` source or one with a patched ``__signature__`` then keeps working.
            Decided by :func:`deprecate._dispatch._is_verbatim_call`; the warning and the budget are unaffected.

    """

    short_circuit: bool
    original_kwargs: dict[str, Any]
    resolved_kwargs: dict[str, Any]
    reason_argument: dict[str, Optional[str]] = field(default_factory=dict)
    target_func: Optional[Callable[..., Any]] = None
    verbatim: bool = False
