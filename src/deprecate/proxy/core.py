"""Run deprecation proxies with their original public import and serialization identities.

This module owns proxy construction, runtime state, attribute and call forwarding, warning rendering, and copy/pickle
reconstruction. Decoration-time mapping and target resolution live in :mod:`deprecate.proxy.resolve`; protocol
forwarders are installed by :mod:`deprecate.proxy.dunders` when the package is imported.

"""

import copy
import inspect
import sys
import types
from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Callable, Optional, SupportsIndex, Union, cast

from deprecate._dispatch import _split_positional_only_kwargs
from deprecate._fatal import DeprecatedError, _resolve_as_exception
from deprecate._types import DeprecationConfig, TargetMode, _CallFacts, _ProxyConfig
from deprecate._validate import _resolve_legacy_proxy_target, _validate_callable_mode, _validate_proxy_mode
from deprecate.docstring.inject import normalize_docstring_style
from deprecate.messaging import (
    TEMPLATE_ARGUMENT_MAPPING,
    TEMPLATE_WARNING_ARGUMENTS,
    TEMPLATE_WARNING_CALLABLE,
    TEMPLATE_WARNING_NO_TARGET,
    _callable_display_name,
    _consume_warn_budget,
    _emit,
    _eval_skip_if,
    _format_deprecation_message,
    _release_warn_budget,
    _render_static_deprecation_message,
    _validate_message_template,
    deprecation_warning,
)
from deprecate.proxy.resolve import (
    _detect_proxy_positional_only,
    _get_static_attr_owner,
    _resolve_proxy_target,
    _validate_attrs_mapping,
    _validate_attrs_redirect_targets,
    _validate_attrs_warn_only_keys,
)
from deprecate.utils import _remap_kwargs, _SigFacts, _signature_facts

#: Standard collection methods blocked by the read-only proxy guard.
_MUTATING_METHOD_NAMES = frozenset(
    {
        "append",
        "extend",
        "insert",
        "pop",
        "remove",
        "clear",
        "update",
        "setdefault",
        "add",
        "discard",
    }
)


def _is_dunder(name: str) -> bool:
    """Return whether *name* is a dunder attribute name (``__x__``) — introspection machinery, not user API."""
    return name.startswith("__") and name.endswith("__")


@dataclass(frozen=True)
class _ProxySpec:
    """Describe proxy construction without changing its runtime or serialized configuration.

    Factories build this frozen record; the proxy validates and copies its mappings before storing runtime state.
    The spec itself is not retained by the proxy or included in copy and pickle payloads.

    Attributes:
        name: Display name used in the warning message.
        deprecated_in: Version string when the object was deprecated.
        remove_in: Version string when the object will be removed.
        num_warns: Maximum number of warnings to emit. ``1`` (default) warns once; ``-1`` warns on every access.
        stream: Callable used to emit warnings. Defaults to :data:`~deprecate.deprecation.deprecation_warning`
            (:class:`FutureWarning`).  Pass ``None`` to suppress warnings.
            A custom ``stream`` is called once per warning, with ``stacklevel=`` only when its signature declares a
            ``stacklevel`` parameter or ``**kwargs``; a C-level callable without an introspectable signature gets the
            message alone and loses caller attribution.
            The ``stacklevel`` passed is counted from the frame that calls ``stream``, so it points at the caller
            for :func:`warnings.warn` and a C-level :func:`functools.partial` of it; a pure-Python stream, and the
            module-level :func:`logging.warning` before Python 3.11, land one frame short (see
            ``deprecate._frames._caller_stacklevel`` for the full rule).
        message_template: Optional custom warning message template that overrides the built-in templates.  When ``None``
            (default), the built-in template for the active scenario is used (callable-target, no-target, or
            per-argument).  See :func:`~deprecate.proxy.deprecated_class` for the available ``%``-style placeholders.
        read_only: If ``True``, raise :class:`AttributeError` on any write attempt through the proxy.
            Only the following standard collection mutator names are intercepted: ``append``, ``clear``,
            ``discard``, ``extend``, ``insert``, ``pop``, ``remove``, ``setdefault``, ``update``, ``add``.
            Custom method names (e.g. ``register()``, ``reload()``, ``set_value()``) are not blocked.
        target: Optional replacement object.  When set, all attribute, item, and call access is forwarded to *target*
            instead of *obj*.
        args_mapping: Optional dict remapping keyword argument names when the proxy is called.  Keys are old argument
            names; values are new names, or ``None`` to drop the argument entirely.

    """

    name: str
    target: Any = None
    args_mapping: Optional[dict[str, Optional[str]]] = None
    args_extra: Optional[dict[str, Any]] = None
    attrs_mapping: Optional[dict[str, Optional[str]]] = None
    deprecated_in: str = ""
    remove_in: str = ""
    num_warns: int = 1
    stream: Optional[Callable[..., None]] = deprecation_warning
    message_template: Optional[str] = None
    skip_if: Union[bool, Callable[[], bool]] = False
    as_exception: Optional[bool] = None
    read_only: bool = False
    docstring_style: str = "auto"
    _misconfigured_override: bool = False


class _DeprecatedProxy:
    """Transparent proxy that emits deprecation warnings on attribute and item access.

    Wraps any Python object and forwards all read operations (attribute lookup, subscript, iteration, calls) to the
    underlying object — or to an optional *target* replacement — while emitting a configurable :class:`FutureWarning`.

    In *read-only* mode any attempt to mutate the proxied object via ``__setitem__``, ``__delitem__``, or
    ``__setattr__`` raises :class:`AttributeError`, unless ``skip_if`` is active. Writes never warn.

    Use :func:`~deprecate.proxy.deprecated_instance` or :func:`~deprecate.proxy.deprecated_class` to create
    instances rather than instantiating this class directly.

    Args:
        obj: The deprecated object to wrap (the *source*).
        spec: Frozen construction settings, including warning policy, mappings, and an optional target.

    Protocol forwarding:
        Operator, conversion, context-manager, iteration, and subclassing protocols are forwarded to the active object
        so the proxy stays transparent (arithmetic ``+ - * / // % ** @`` and their reflected/in-place forms, ordering
        ``< <= > >=``, ``int()``/``float()``/``complex()``/``index()``, ``abs()``/``-``/``+``/``~``,
        ``round()``/``math.trunc``/``floor``/``ceil``, ``reversed()``, ``os.fspath()``, ``format()``,
        ``with``/``async with``, ``next()``/``async for``, ``await``, and ``class Child(DeprecatedAlias)`` via PEP 560
        ``__mro_entries__``). A proxy over a function stored as a class attribute binds like a method through
        ``__get__`` (see :class:`_BindingDeprecatedProxy`). Because implicit special-method lookup
        bypasses ``__getattr__``, any dunder *not* listed here is not forwarded; a protocol the wrapped object does not
        implement raises the normal :class:`TypeError` (binary operators return :data:`NotImplemented` so reflected
        operands and Python's error path are preserved).

        **Warn policy** (consistent with ``__getitem__`` vs ``__len__``): operations that use or produce the wrapped
        data warn once against the shared budget — arithmetic and its reflected/in-place forms, numeric/path
        conversions, ``round``/``trunc``/``floor``/``ceil``, ``reversed``, ``next``, context entry (``__enter__`` /
        ``__aenter__``), async iteration, and subclassing. Cheap structural probes stay silent — ordering comparisons,
        ``format``, and context exit (``__exit__`` / ``__aexit__``) — mirroring the silent ``__eq__`` / ``__len__`` /
        ``__str__``. In-place operators return the active object's own result, so a proxied value assigned via ``+=``
        rebinds to that result rather than a re-wrapped proxy.

        **Warn timing**: most data-use operations warn once the forwarded operation has succeeded, so a failed probe (a
        ``KeyError`` subscript, a raising getter, an operand the hook declines) keeps the budget for the real use.
        Calling the proxy and ``next`` warn first: a call mirrors ``@deprecated``, and ``next`` consumes an item that a
        failing warning delivery would otherwise lose, so an exhausting ``next`` spends the budget too. ``__anext__``
        and ``__await__`` warn once the awaitable is obtained, before it runs. When delivery raises after the
        operation already ran — a ``FutureWarning`` filter set to ``"error"`` or a failing ``stream`` — context entry
        is unwound through the context's own exit and a coroutine that ``__anext__`` or ``__await__`` created is
        closed unstarted (an async generator is left open), while the effect of an in-place operator (``p += [2]`` has
        already extended the list) or of a ``defaultdict`` subscript (the missing key has been inserted) stays and its
        result is lost. Those operations keep warning after success because warning first would spend the budget on
        every failed probe, and an in-place hook that declines would add a second warning to the binary fallback.

    Known limitations:
        **Structural ABC over-claim**: because dunder methods are installed unconditionally on the *type*
        (implicit special-method lookup bypasses ``__getattr__`` and cannot be conditional per-instance),
        ``isinstance(proxy, os.PathLike)`` returns ``True`` even when the wrapped object is not a path — then
        ``os.fspath(proxy)`` raises ``TypeError``.  The same applies to ``Awaitable``, ``AsyncIterable``, and
        any other structural ABC that keys on dunder presence.  Always check the *unwrapped* object's type
        when using structural ABCs as a dispatch guard, or call ``type(deprecated_instance_proxy.__class__)``
        to get the wrapped type (which is ``_DeprecatedProxy``).

        **Binding**: only a proxy over a plain function (a ``@deprecated`` wrapper included) or over another such proxy
        is a :class:`_BindingDeprecatedProxy` with ``__get__``, so ``inspect.isroutine(proxy)`` is ``True`` for it and
        ``False`` for proxies over data, classes, and other callables. A ``functools.partial``, a bound method, a
        builtin or a Cython-style binding callable stored as a class attribute is served unbound, on every Python
        version.

    """

    # Class-level annotations for the AST-friendliness breadcrumbs set in ``_set_ast_breadcrumbs``.
    # Declared here so static type-checkers know they exist; assigned in ``__init__`` via
    # ``object.__setattr__`` because the proxy overrides ``__setattr__`` to forward writes.
    __wrapped__: Any
    __signature__: Any

    if TYPE_CHECKING:
        # Installed by :mod:`deprecate.proxy.dunders` when the package is imported; declared here so type checkers
        # see the context-manager, async-iteration and await protocols as methods of the proxy type.
        def __enter__(self) -> Any: ...  # noqa: ANN401
        def __exit__(self, *exc_info: Any) -> Any: ...  # noqa: ANN401
        async def __aenter__(self) -> Any: ...  # noqa: ANN401
        async def __aexit__(self, *exc_info: Any) -> Any: ...  # noqa: ANN401
        def __aiter__(self) -> Any: ...  # noqa: ANN401
        def __anext__(self) -> Any: ...  # noqa: ANN401
        def __await__(self) -> Any: ...  # noqa: ANN401

    @staticmethod
    def _get_static_attr_owner(obj: Any) -> Any:  # noqa: ANN401
        """Delegate to :func:`~deprecate.proxy.resolve._get_static_attr_owner` with the nominal proxy type."""
        return _get_static_attr_owner(_DeprecatedProxy, obj)

    @classmethod
    def _has_static_attribute(cls, obj: Any, name: str) -> bool:  # noqa: ANN401
        """Return whether *name* exists without invoking ``__getattr__`` or descriptors."""
        try:
            inspect.getattr_static(cls._get_static_attr_owner(obj), name)
        except AttributeError:
            return False
        return True

    @staticmethod
    def _target_display_name(target: Any, fallback: str) -> str:  # noqa: ANN401
        """Return a warning-display name without triggering proxy ``__getattr__``."""
        target_owner = _DeprecatedProxy._get_static_attr_owner(target)
        # a callable owner (``functools.partial``, callable instance) may lack ``__name__``; the helper names it
        if callable(target_owner) and not isinstance(target_owner, _DeprecatedProxy):
            return _callable_display_name(target_owner)[0]
        return getattr(target_owner, "__name__", fallback)

    @staticmethod
    def _validate_attrs_mapping(
        attrs_mapping: dict[str, Optional[str]],
        obj: Any,  # noqa: ANN401
        attr_check_obj: Any,  # noqa: ANN401
    ) -> None:
        """Delegate to :func:`~deprecate.proxy.resolve._validate_attrs_mapping` with the nominal proxy type."""
        return _validate_attrs_mapping(_DeprecatedProxy, attrs_mapping, obj, attr_check_obj)

    @staticmethod
    def _validate_attrs_redirect_targets(
        attrs_mapping: dict[str, Optional[str]],
        attr_check_obj: Any,  # noqa: ANN401
    ) -> None:
        """Delegate to :func:`~deprecate.proxy.resolve._validate_attrs_redirect_targets` with the nominal proxy type."""
        return _validate_attrs_redirect_targets(_DeprecatedProxy, attrs_mapping, attr_check_obj)

    @staticmethod
    def _validate_attrs_warn_only_keys(
        attrs_mapping: dict[str, Optional[str]],
        obj: Any,  # noqa: ANN401
        attr_check_obj: Any,  # noqa: ANN401
    ) -> None:
        """Delegate to :func:`~deprecate.proxy.resolve._validate_attrs_warn_only_keys` with the nominal proxy type."""
        return _validate_attrs_warn_only_keys(_DeprecatedProxy, attrs_mapping, obj, attr_check_obj)

    def __init__(self, obj: Any, spec: _ProxySpec) -> None:  # noqa: ANN401
        """Initialise the proxy with typed runtime/config dataclasses.

        ``__config`` stores private mutable runtime state in :class:`~deprecate._types._ProxyConfig` (obj, stream,
        num_warns, read_only, args_extra, warned counter).

        ``__deprecation_config__`` is the metadata interface consumed by audit tools
        (:func:`~deprecate.audit.validate_deprecation_wrapper`,
        :func:`~deprecate.audit.find_deprecation_wrappers`, etc.)
        as a :class:`~deprecate._types.DeprecationConfig` instance aligned with the ``@deprecated`` schema.
        ``__deprecated__`` is the PEP-702-conformant rendered message string.

        ``spec._misconfigured_override`` is a private hook used by :func:`~deprecate.deprecated` when it delegates to
        :func:`~deprecate.deprecated_class` for class targets: it lets the caller pre-compute misconfig signals
        (raw ``target=False`` plus NOTIFY+args_mapping / NOTIFY+args_extra detected upstream) before the proxy
        rewrites them away, so the final frozen :class:`~deprecate._types.DeprecationConfig` records every signal
        in one place.

        """
        target = spec.target
        args_mapping = spec.args_mapping
        attrs_mapping = spec.attrs_mapping
        # Probe ``message_template`` against every documented placeholder so typos and malformed conversion specifiers
        # fail at decoration time instead of on the first proxy access.
        _validate_message_template(spec.message_template)
        # Reject true cycles in ``attrs_mapping`` at decoration time. Non-cyclic chains are allowed at runtime
        # and surfaced by audit as mapping chains.
        if attrs_mapping is not None:
            attr_check_obj = target if target is not None and not isinstance(target, (TargetMode, bool)) else obj
            self._validate_attrs_mapping(attrs_mapping, obj, attr_check_obj)
        # Track whether the raw ``target=False`` sentinel was passed so audit can flag it. The override
        # path lets upstream callers fold their own pre-validated misconfig signals into the same flag.
        misconfigured = target is False or spec._misconfigured_override
        if isinstance(target, bool):
            target = _resolve_legacy_proxy_target(target, args_mapping=args_mapping)
        _dc_check = _DeprecatedProxy._get_static_attr_owner(
            target if target is not None and not isinstance(target, (TargetMode, bool)) else obj
        )
        if args_mapping is not None:
            args_mapping = dict(args_mapping)
        # Copy ``attrs_mapping`` too (``args_mapping`` is copied just above): the frozen config stores it, so
        # aliasing the caller's dict would let post-decoration mutation introduce the exact cycle validation rejected.
        if attrs_mapping is not None:
            attrs_mapping = dict(attrs_mapping)
        # Resolve the unset/legacy target against the configured mappings; detects the explicit-NOTIFY
        # contradiction (mapping ignored at runtime, flagged by the validators below).
        target, args_mapping, _auto_expanded, _explicit_notify_attrs = _resolve_proxy_target(
            obj, target, args_mapping, attrs_mapping, _dc_check
        )
        # Constructor signature of the active class, read once (a non-class active object has no constructor to
        # inspect); both the kwargs-compatibility guard and the POSITIONAL_ONLY split data below derive from it.
        _ctor_facts = _signature_facts(_dc_check, empty_on_error=True) if isinstance(_dc_check, type) else _SigFacts()
        # Kwargs-compatibility guard: detect args_mapping keys that remap to POSITIONAL_ONLY params.
        _incompatible = _detect_proxy_positional_only(_dc_check, spec.name, args_mapping, _ctor_facts.positional_only)
        # Constructor POSITIONAL_ONLY split data: the call path forwards remapped values
        # positionally (same split machinery as the @deprecated dispatcher) instead of the
        # historical pop-and-setattr fallback, which failed for required params, immutable
        # instances, and constructor-derived state.
        _ctor_positional_only = _ctor_facts.positional_only
        _ctor_positional_only_order = _ctor_facts.order if _ctor_positional_only else ()
        # Validate misconfig (NOTIFY+mapping, ARGS_REMAP+no-args_mapping, NOTIFY+args_extra). The
        # validator returns True when any signal fired so we extend ``misconfigured`` accordingly —
        # ``DeprecationConfig.misconfigured`` becomes a single source of truth for all signals.
        if isinstance(target, TargetMode):
            misconfigured |= _validate_callable_mode(
                target, spec.name, args_mapping=args_mapping, args_extra=spec.args_extra
            )
        # Proxy-specific validation: attrs_mapping combinations not covered by _validate().
        misconfigured |= _validate_proxy_mode(
            target,
            spec.name,
            attrs_mapping=attrs_mapping,
            args_mapping=args_mapping,
            args_extra=spec.args_extra,
        )
        # Private mutable runtime state — warn counter, stream, read-only flag, wrapped object, extras to merge
        # after args_mapping at call time, optional custom warning template.
        cfg = _ProxyConfig(
            obj=obj,
            stream=spec.stream,
            num_warns=spec.num_warns,
            read_only=spec.read_only,
            args_extra=spec.args_extra,
            message_template=spec.message_template,
            # Explicit-NOTIFY conflict: the mapping is ignored at runtime (no redirects), while the frozen
            # ``DeprecationConfig`` below keeps it so audit can still show what the caller configured.
            attrs_mapping=None if _explicit_notify_attrs else attrs_mapping,
            skip_if=spec.skip_if,
            # Call-time home of the constructor POSITIONAL_ONLY split; the frozen config below mirrors the same two
            # values for audit (and for 0.14 pickles, which ``_reconstruct_proxy`` rebuilds the facts from).
            facts=_CallFacts(
                target_positional_only=_ctor_positional_only, target_positional_only_order=_ctor_positional_only_order
            ),
        )
        object.__setattr__(self, "_DeprecatedProxy__config", cfg)
        # Static deprecation metadata stored as a dunder attribute — readable by audit tools via
        # __deprecation_config__; __deprecated__ carries the PEP-702-conformant rendered message string.
        dep_meta = DeprecationConfig(
            deprecated_in=spec.deprecated_in,
            remove_in=spec.remove_in,
            name=spec.name,
            target=target,
            args_mapping=args_mapping,
            args_extra=spec.args_extra,
            misconfigured=misconfigured,
            docstring_style=normalize_docstring_style(spec.docstring_style),
            message_template=spec.message_template,
            as_exception=spec.as_exception,
            attrs_mapping=attrs_mapping,
            args_mapping_auto_expanded=tuple(_auto_expanded),
            args_mapping_positional_only=_incompatible,
            target_positional_only=_ctor_positional_only,
            target_positional_only_order=_ctor_positional_only_order,
        )
        object.__setattr__(self, "__deprecation_config__", dep_meta)
        object.__setattr__(
            self,
            "__deprecated__",
            _render_static_deprecation_message(dep_meta, spec.name, _proxy_source_path(obj, spec.name)),
        )
        # Expose the wrapped object's docstring as an instance attribute so that external tools (autodoc,
        # mkdocstrings/griffe) see the source class's documentation rather than _DeprecatedProxy's own class docstring.
        _doc = getattr(obj, "__doc__", None)
        if _doc:
            object.__setattr__(self, "__doc__", _doc)
        # AST-friendliness breadcrumbs: ``__wrapped__`` lets static tools (Sphinx, mypy with follow-wrapped,
        # IDEs) trace the proxy back to the source object; ``__signature__`` exposes the source's signature
        # to ``inspect.signature`` for tools that don't walk ``__wrapped__``.
        self._set_ast_breadcrumbs(obj)

    # ------------------------------------------------------------------
    # Internal helpers — must use object.__getattribute__ / object.__setattr__
    # to avoid triggering the proxy's own __getattr__ / __setattr__.
    # ------------------------------------------------------------------

    def _set_ast_breadcrumbs(self, obj: Any) -> None:  # noqa: ANN401
        """Set ``__wrapped__`` and ``__signature__`` so static-analysis tools can trace the proxy.

        Not every wrapped object is introspectable — builtins and instances of C types (e.g. a plain ``dict``) make
        ``inspect.signature`` raise ``ValueError``/``TypeError``. In that case ``__signature__`` falls back to ``None``
        rather than propagating: both attributes are always assigned so the proxy uniformly satisfies the
        :class:`~deprecate._types.DeprecationProxy` Protocol, and wrapping stays infallible at decoration time.

        Every ``Exception`` is caught, not just those two: ``ValueError``/``TypeError`` are merely the *normalised*
        introspection failures. A source exposing a ``__signature__`` descriptor that raises — mocks, lazily built
        extension wrappers, some model metaclasses — has that exception forwarded verbatim by ``inspect.signature``,
        and a deep ``__wrapped__``/metaclass chain can surface a ``RecursionError``. The breadcrumbs are a
        best-effort convenience for static tooling, never a precondition of the deprecation itself, so no
        introspection failure may take down decoration. ``BaseException`` (``KeyboardInterrupt``, ``SystemExit``)
        still propagates.

        """
        object.__setattr__(self, "__wrapped__", obj)
        try:
            sig: Any = inspect.signature(obj)
        except Exception:
            sig = None
        object.__setattr__(self, "__signature__", sig)

    @property
    def _cfg(self) -> _ProxyConfig:
        """Private mutable runtime state (warn counter, stream, read-only flag, wrapped object).

        Private to this class — not part of the public API and not consumed by audit tools.

        """
        return cast(_ProxyConfig, object.__getattribute__(self, "_DeprecatedProxy__config"))

    @property
    def _dep(self) -> DeprecationConfig:
        """Static deprecation metadata (versions, name, target, args_mapping).

        Stored as ``__deprecation_config__`` (dunder, not name-mangled) — audit tools and external code should read it
        via :func:`~deprecate.get_deprecation_config` rather than the raw attribute; this property simply provides a
        typed view of the same object for internal use.

        """
        return cast(DeprecationConfig, object.__getattribute__(self, "__deprecation_config__"))

    def _build_attr_warning_msg(
        self,
        arg_name: str,
        dep: DeprecationConfig,
        attrs_mapping: dict[str, Optional[str]],
        target: Any,  # noqa: ANN401
        custom_template: Optional[str],
        source_path: str,
    ) -> Optional[str]:
        """Build the warning message for a deprecated ``attrs_mapping`` attribute access.

        Strips the ``"__attr__:"`` prefix from *arg_name* to recover the attribute name visible to
        callers.  The prefix must be preserved in the *arg_name* passed by the caller so that the
        per-attribute budget key in ``cfg.state.warned_args`` stays disjoint from any same-named
        ``args_mapping`` key — this method intentionally does not reassign *arg_name*.  The
        attribute's ``%(source_path)s`` is ``f"{source_path}.{attr_name}"``.

        Returns:
            The formatted warning string, or ``None`` when *attr_name* is not registered in
            ``dep.attrs_mapping`` (caller should silently return without warning).

        """
        attr_name = arg_name[len("__attr__:") :]
        if attr_name not in attrs_mapping:
            return None
        new_attr = attrs_mapping[attr_name]
        attr_path = f"{source_path}.{attr_name}"
        if new_attr is not None:
            owner = self._target_display_name(target, dep.name) if not isinstance(target, TargetMode) else dep.name
            return _format_deprecation_message(
                custom_template or TEMPLATE_WARNING_CALLABLE,
                attr_name,
                attr_path,
                deprecated_in=dep.deprecated_in,
                remove_in=dep.remove_in,
                target_name=new_attr,
                target_path=f"{owner}.{new_attr}",
            )
        return _format_deprecation_message(
            custom_template or TEMPLATE_WARNING_NO_TARGET,
            attr_name,
            attr_path,
            deprecated_in=dep.deprecated_in,
            remove_in=dep.remove_in,
        )

    def _shall_skip(self) -> bool:
        """Evaluate the ``skip_if`` condition — ``True`` means the deprecation machinery is inactive.

        Mirrors the callable-decorator semantics: a ``bool`` is used as-is, a zero-argument callable is invoked
        and must return a strict ``bool``.

        Raises:
            TypeError: If ``skip_if`` is a callable that does not return a ``bool``.

        """
        return _eval_skip_if(self._cfg.skip_if)

    def _warn(self, *, arg_name: Optional[str] = None) -> None:
        """Gate and deliver a deprecation warning in one step, for an operation that warns before it runs.

        Equivalent to :meth:`_gate` followed by :meth:`_deliver` on its decision. Operations that touch the wrapped
        object call the two halves themselves, so the operation and its warning share one ``skip_if`` evaluation.

        Args:
            arg_name: When given, use per-argument warning tracking (``cfg.state.warned_args``) instead of the
                proxy-wide ``cfg.state.warned_calls`` counter.  The warning is suppressed when the per-argument
                count has already reached ``cfg.num_warns``.  When provided alongside an ``args_mapping`` entry, the
                emitted message uses the per-argument template (`old -> new`) rather than the generic callable
                template — matching the decorator's argument-deprecation form.

        """
        _, live = self._gate(arg_name=arg_name)
        self._deliver(live, arg_name=arg_name)

    def _activation(self) -> tuple[Any, bool]:
        """Evaluate ``skip_if`` once and return the object to operate on with whether the deprecation is live.

        Returns:
            ``(active, live)`` — the wrapped source and ``False`` while ``skip_if`` holds, otherwise the configured
            replacement (the source when there is none) and ``True``.

        """
        if self._shall_skip():
            return self._cfg.obj, False
        return self._unskipped_active(), True

    def _gate(self, *, arg_name: Optional[str] = None, read_only_op: Optional[str] = None) -> tuple[Any, bool]:
        """Decide once whether this operation's deprecation is live, refusing a fatal one before the object is used.

        Every read and data-use operation that gates here makes this decision exactly once: ``skip_if`` is evaluated
        here (through :meth:`_activation`) and nowhere else in the operation, and the returned pair carries the
        decision to the operation itself and to :meth:`_deliver`. A ``skip_if`` callable that changes its answer
        between calls therefore cannot serve one object and warn about another, and it is not paid for twice. Calls
        (``__call__``) and writes (``__setattr__``, ``__setitem__`` and their deletes) still check ``skip_if`` on their
        own before reaching the forwarding helpers, so they may consult it more than once.

        This is the single place that refuses a fatal deprecation ahead of the operation. Proxy operations with an
        effect on the wrapped object (subscript, iteration, ``round``, the unary and conversion forwards,
        ``attrs_mapping`` reads, ``__next__``, context entry, the async protocols, ``await`` and the in-place
        operators) gate before they touch it, so under fatal mode the operation never runs: a missing key raises
        ``DeprecatedError`` rather than ``KeyError``, a ``defaultdict`` gains no entry, and a mapped getter or a user
        ``__iter__`` is not executed. Plain attribute reads and binary operators resolve first: they take the
        decision from :meth:`_activation` and refuse a fatal deprecation once the lookup or operator has succeeded.
        The fatal check runs ahead of the ``stream`` check — ``stream=None`` silences the message but never opens the
        door — and it never touches the warn counters, so turning the global switch back off restores warn-once.

        Args:
            arg_name: Per-argument tracking key, as for :meth:`_warn`. A name absent from ``attrs_mapping`` builds no
                message, so the gate does not raise for it.
            read_only_op: Description of an in-place write. When given, a live read-only proxy raises
                :class:`AttributeError` here, before the fatal check and before the wrapped operator is looked up.

        Returns:
            ``(active, live)`` from :meth:`_activation`.

        Raises:
            AttributeError: If *read_only_op* is given and the live proxy is read-only.
            DeprecatedError: If ``as_exception`` (or the process-wide switch) makes this deprecation fatal.

        """
        active, live = self._activation()
        if live:
            if read_only_op is not None:
                self._check_read_only(read_only_op)
            self._refuse_fatal(arg_name)
        return active, live

    def _refuse_fatal(self, arg_name: Optional[str] = None) -> None:
        """Raise ``DeprecatedError`` when this live deprecation is fatal; otherwise do nothing.

        Raises:
            DeprecatedError: If ``as_exception`` (or the process-wide switch) makes this deprecation fatal.

        """
        dep = self._dep
        if _resolve_as_exception(dep.as_exception):
            msg = _build_proxy_warn_msg(self, arg_name, dep, self._cfg)
            if msg is not None:
                raise DeprecatedError(msg)

    def _deliver(self, live: bool, *, arg_name: Optional[str] = None) -> None:
        """Emit the deprecation warning for an operation whose gate returned *live*, if the warn budget allows.

        Re-evaluates neither ``skip_if`` nor the fatal switch: *live* is the decision the operation was gated on.

        Args:
            live: The second element of the pair returned by :meth:`_gate` or :meth:`_activation`.
            arg_name: Per-argument tracking key, as for :meth:`_warn`.

        """
        # Every delivery is a data-producing use of the deprecated object — structural probes (``__eq__``, ordering,
        # ``__len__``, ``__repr__``, ``__bool__``, ``__exit__``) never reach here.
        if not live:
            return
        cfg = self._cfg
        stream = cfg.stream
        if not stream:
            return
        # Lock-free precheck, as in ``_emit_call_warning``: once this key's budget is spent the claim below refuses
        # anyway, so a use past the budget skips building the message and walking ``_proxy_source_path``.  The
        # authoritative check-then-increment still runs under ``state.lock``.
        state = cfg.state
        used = state.warned_calls if arg_name is None else state.warned_args.get(arg_name, 0)
        if 0 <= cfg.num_warns <= used:
            return
        # Build the message before taking the lock: attrs_mapping miss returns None (no warning, no budget spent),
        # and message building is pure CPU work with no IO.
        msg = _build_proxy_warn_msg(self, arg_name, self._dep, cfg)
        if msg is None:
            return
        # One budget for every deprecation path: the proxy-wide counter is the callable-level ``warned_calls``; a
        # per-argument or ``__attr__:`` attribute key is a one-key argument budget.  The check and the increment
        # run together under ``state.lock`` so concurrent first accesses cannot all pass the ``num_warns`` gate; the
        # warning itself is emitted outside the lock, so callers never serialise on a slow ``stream``.
        reason_callable = arg_name is None
        reason_argument = None if arg_name is None else {arg_name: None}
        with state.lock:
            claimed = _consume_warn_budget(
                state, cfg.num_warns, reason_callable=reason_callable, reason_argument=reason_argument
            )
        if not claimed:
            return
        # Route the warning to the caller's frame rather than ``proxy/core.py``.  ``_emit`` decides from the stream's
        # signature whether to pass ``stacklevel``, so the stream is called exactly once and any ``TypeError`` it
        # raises propagates; the level itself is measured from ``_emit``'s frame.  A raising stream delivered
        # nothing, so the claimed slot goes back to the budget before the error propagates.
        try:
            _emit(stream, msg)
        except BaseException:
            with state.lock:
                _release_warn_budget(
                    state, cfg.num_warns, reason_callable=reason_callable, reason_argument=reason_argument
                )
            raise

    def _deliver_or_release(
        self,
        live: bool,
        release: Callable[..., object],
        *release_args: Any,  # noqa: ANN401
    ) -> None:
        """Deliver the warning for an operation that already acquired something, releasing it if delivery raises.

        Python never runs the release half of a protocol (``__exit__``, closing an unused coroutine) when the acquiring
        call itself raises, so the proxy does it: on a delivery error, ``release(*release_args, *sys.exc_info())`` runs
        and the delivery error is then re-raised unchanged, whatever *release* returns. Context entry passes
        :func:`_exit_acquired`; ``__anext__`` and ``__await__`` pass :func:`_close_abandoned`. ``__aenter__`` repeats
        the same steps inline, because its release (``__aexit__``) has to be awaited.

        Args:
            live: The decision the operation was gated on, as for :meth:`_deliver`.
            release: Called only when delivery raises.
            release_args: Leading positional arguments for *release*.

        """
        try:
            self._deliver(live)
        except BaseException:
            release(*release_args, *sys.exc_info())
            raise

    def _check_read_only(self, operation: str) -> None:
        """Raise AttributeError when the proxy is in read-only mode.

        Raises:
            AttributeError: If ``read_only=True`` was set at construction time.

        """
        if self._cfg.read_only:
            name: str = self._dep.name
            raise AttributeError(
                f"'{name}' is deprecated and read-only. {operation} is not allowed. Migrate away from this object."
            )

    @property
    def wrapped(self) -> Any:  # noqa: ANN401
        """The deprecated source object this proxy wraps (audit contract accessor)."""
        return self._cfg.obj

    def _get_active(self) -> Any:  # noqa: ANN401
        """Return the active object: *target* when set, otherwise *source*.

        When ``skip_if`` evaluates ``True`` the deprecation machinery is inactive, so the wrapped source is served
        regardless of any configured target. For the silent structural probes; an operation that warns takes the active
        object from its :meth:`_gate` result instead, so ``skip_if`` is not evaluated a second time.

        """
        if self._shall_skip():
            return self._cfg.obj
        return self._unskipped_active()

    def _unskipped_active(self) -> Any:  # noqa: ANN401
        """Return the configured replacement when there is one, otherwise the wrapped source, ignoring ``skip_if``."""
        target = self._dep.target
        if target is not None and not isinstance(target, TargetMode):
            return target
        return self._cfg.obj

    def _merge_args_extra(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Merge :attr:`_ProxyConfig.args_extra` into *kwargs*; extra values win."""
        args_extra = self._cfg.args_extra
        if not args_extra:
            return kwargs
        merged = dict(kwargs)
        merged.update(args_extra)
        return merged

    @staticmethod
    def _is_potential_mutator(name: str) -> bool:
        """Heuristic to detect common mutating methods on built-in collections.

        This is intentionally conservative and only covers the most common mutating APIs on built-in container types
        (lists, dicts, sets).

        """
        return name in _MUTATING_METHOD_NAMES

    def _guard_read_only_mutator(self, attr: Any, name: str) -> Any:  # noqa: ANN401
        """Return a guarded callable for standard mutators in read-only mode."""
        if self._cfg.read_only and callable(attr) and self._is_potential_mutator(name):

            def _guarded_mutator(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
                """Raise a read-only error when this mutating method is called."""
                self._check_read_only(f"Calling mutating method '{name}'")

            return _guarded_mutator
        return attr

    # ------------------------------------------------------------------
    # Attribute access
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        """Forward attribute lookup to the active object, emitting a deprecation warning.

        When ``attrs_mapping`` is configured, only attributes listed in the mapping emit a warning; all other accesses
        are forwarded silently without a warning.  The redirect target name (value in the mapping) is used for the
        actual attribute lookup when the value is a non-``None`` string; ``None`` means warn-only with no rename on the
        active object.

        The attribute is resolved *first* and the warning fires only on successful access — with or without
        ``attrs_mapping``, failed probes (``hasattr`` duck-typing, copy/pickle machinery, a raising getter) propagate
        their own error without warning or consuming the warn budget.  Under a fatal deprecation a listed
        ``attrs_mapping`` name raises ``DeprecatedError`` before the lookup, so its getter never runs; any other read
        still resolves first, so a missing attribute raises ``AttributeError``.  Dunder reads (``__mro__``,
        ``__wrapped__``, …) are forwarded silently: they are introspection machinery, not a use of the deprecated API —
        the same rationale as :meth:`~deprecate.proxy._DeprecatedProxy.__instancecheck__`.

        In read-only mode, common mutating methods on built-in collections (for example, ``append`` or ``update``) are
        wrapped so that calling them raises :class:`AttributeError` instead of mutating the underlying object.

        """
        # ``__deprecated__`` is proxy metadata (see ``__setattr__``); reaching here means it was deleted from the
        # proxy, so report it missing instead of falling through to the wrapped object's own marker.
        if name == "__deprecated__":
            raise AttributeError(name)
        # Half-initialised instance guard (``cls.__new__(cls)`` during copy/pickle reconstruction): the ``_cfg``
        # property raises AttributeError when ``__config`` is missing, which Python routes back into __getattr__ —
        # touching ``self._cfg`` here again would mutually recurse (property ↔ __getattr__) into RecursionError.
        try:
            cfg = cast(_ProxyConfig, object.__getattribute__(self, "_DeprecatedProxy__config"))
        except AttributeError:
            raise AttributeError(name) from None
        # One ``skip_if`` decision serves the lookup and its warning.
        active, live = self._activation()
        # skip_if active — deprecation machinery inactive: serve the wrapped source directly, with no
        # warning, no attrs_mapping redirect, and no read-only mutator guard.
        if not live:
            return getattr(active, name)
        attrs_mapping = cfg.attrs_mapping
        if attrs_mapping is not None:
            if name in attrs_mapping:
                # A fatal deprecation is refused before the lookup, so the redirected getter never runs.  Otherwise
                # resolve first, as below: a failed lookup raises without warning or burning this name's budget.
                arg_name = f"__attr__:{name}"
                self._refuse_fatal(arg_name)
                attr_name, attr = self._resolve_mapped_attr(attrs_mapping, name, active)
                self._deliver(live, arg_name=arg_name)
                return self._guard_read_only_mutator(attr, attr_name)
            # Not a deprecated attr — silent passthrough, no warning.
            attr = getattr(active, name)
            return self._guard_read_only_mutator(attr, name)
        # Resolve first so a missing attribute raises without warning or burning the warn budget.
        attr = getattr(active, name)
        # ARGS_REMAP deprecates constructor argument names only; attribute access is unrelated
        # to the deprecated call signature and must not emit a spurious global warning.
        # Dunder reads are silent machinery — see the docstring above.
        if self._dep.target is not TargetMode.ARGS_REMAP and not _is_dunder(name):
            # A plain read resolves first, so a fatal deprecation is refused only once the lookup has succeeded.
            self._refuse_fatal()
            self._deliver(live)
        return self._guard_read_only_mutator(attr, name)

    def _resolve_mapped_attr(
        self,
        attrs_mapping: dict[str, Optional[str]],
        name: str,
        active: Any,  # noqa: ANN401
    ) -> tuple[str, Any]:
        """Look up the attribute a deprecated ``attrs_mapping`` alias reads, returning its resolved name and value.

        A string redirect is read from *active* (the object the caller's gate resolved) under its canonical name. A
        ``None`` (warn-only) entry keeps the name and falls back to the wrapped source when *active* lacks it.

        Raises:
            AttributeError: If the resolved attribute exists on neither object.

        """
        redirect = attrs_mapping[name]
        if redirect is not None:
            return redirect, getattr(active, redirect)
        try:
            return name, getattr(active, name)
        except AttributeError:
            # Attribute absent from target — fall back to source. Covers the "being-removed" pattern where
            # the deprecated attribute still lives on the old class but was dropped from the new target.
            return name, getattr(self._cfg.obj, name)

    def __setattr__(self, name: str, value: Any) -> None:  # noqa: ANN401
        """Forward attribute mutation to the active object, raising in read-only mode.

        When ``attrs_mapping`` is configured and the attribute name is a deprecated alias, emits a warning and
        redirects the write to the canonical attribute name.

        ``__deprecated__`` is proxy metadata, not wrapped-object state: it is stored on the proxy itself, ahead of the
        ``skip_if`` pass-through and the read-only guard, so a PEP 702 decorator stacked on the proxy never marks the
        wrapped (replacement) object deprecated.

        Raises:
            AttributeError: If the proxy is in read-only mode.

        """
        if name == "__deprecated__":
            object.__setattr__(self, name, value)
            return
        # skip_if active — write straight to the wrapped source; no warning, redirect, or read-only guard.
        if self._shall_skip():
            setattr(self._cfg.obj, name, value)
            return
        self._check_read_only(f"Setting attribute '{name}'")
        attrs_mapping = self._cfg.attrs_mapping
        if attrs_mapping is not None and name in attrs_mapping:
            self._warn(arg_name=f"__attr__:{name}")
            redirect = attrs_mapping[name]
            active = self._get_active()
            attr_name = redirect if redirect is not None else name
            if redirect is None:
                # "Being-removed" pattern: attribute may live only on the source class.
                # Mirror the __getattr__ fallback: prefer active, fall back to source.
                owner = active if hasattr(active, attr_name) else self._cfg.obj
                setattr(owner, attr_name, value)
                return
            setattr(active, attr_name, value)
            return
        setattr(self._get_active(), name, value)

    def __delattr__(self, name: str) -> None:
        """Forward attribute deletion to the active object, raising in read-only mode.

        When ``attrs_mapping`` is configured and the attribute name is a deprecated alias, emits a warning and
        redirects the deletion to the canonical attribute name. ``__deprecated__`` is deleted from the proxy itself,
        mirroring :meth:`~deprecate.proxy._DeprecatedProxy.__setattr__`.

        Raises:
            AttributeError: If the proxy is in read-only mode.

        """
        if name == "__deprecated__":
            object.__delattr__(self, name)
            return
        # skip_if active — delete straight on the wrapped source; no warning, redirect, or read-only guard.
        if self._shall_skip():
            delattr(self._cfg.obj, name)
            return
        self._check_read_only(f"Deleting attribute '{name}'")
        attrs_mapping = self._cfg.attrs_mapping
        if attrs_mapping is not None and name in attrs_mapping:
            self._warn(arg_name=f"__attr__:{name}")
            redirect = attrs_mapping[name]
            active = self._get_active()
            attr_name = redirect if redirect is not None else name
            if redirect is None:
                # "Being-removed" pattern: attribute may live only on the source class.
                # Mirror the __getattr__ fallback so delete behaviour is symmetric.
                try:
                    delattr(active, attr_name)
                except AttributeError:
                    delattr(self._cfg.obj, attr_name)
                return
            delattr(active, attr_name)
            return
        delattr(self._get_active(), name)

    # ------------------------------------------------------------------
    # Subscript access
    # ------------------------------------------------------------------

    def __getitem__(self, key: Any) -> Any:  # noqa: ANN401
        """Forward subscript lookup to the active object, emitting a deprecation warning once the lookup succeeds.

        The lookup runs first, so a missing key raises its own ``KeyError``/``IndexError`` without warning or spending
        the warn budget — a ``try: cfg[key] except KeyError`` probe stays free, as on ``__getattr__``.  A fatal
        deprecation raises ``DeprecatedError`` before the lookup instead, so a missing key raises it too and a
        ``defaultdict`` gains no entry. Outside fatal mode the lookup's own effects stay if warning delivery then
        raises: a ``defaultdict`` keeps the entry it inserted.

        """
        active, live = self._gate()
        value = active[key]
        self._deliver(live)
        return value

    def __setitem__(self, key: Any, value: Any) -> None:  # noqa: ANN401
        """Forward subscript mutation to the active object, raising in read-only mode.

        Item writes never warn: a write stores new data rather than reading the deprecated value, the same policy as
        attribute writes outside ``attrs_mapping``. With ``skip_if`` active the write goes straight to the wrapped
        source and the read-only guard does not apply, mirroring :meth:`~deprecate.proxy._DeprecatedProxy.__setattr__`.

        Raises:
            AttributeError: If the proxy is in read-only mode and ``skip_if`` is inactive.

        """
        # skip_if active — write straight to the wrapped source; no read-only guard (parity with ``__setattr__``).
        if self._shall_skip():
            self._cfg.obj[key] = value
            return
        self._check_read_only(f"Setting item '{key}'")
        self._get_active()[key] = value

    def __delitem__(self, key: Any) -> None:  # noqa: ANN401
        """Forward subscript deletion to the active object, raising in read-only mode.

        Never warns, like :meth:`~deprecate.proxy._DeprecatedProxy.__setitem__`. With ``skip_if`` active the deletion
        goes straight to the wrapped source and the read-only guard does not apply, mirroring
        :meth:`~deprecate.proxy._DeprecatedProxy.__delattr__`.

        Raises:
            AttributeError: If the proxy is in read-only mode and ``skip_if`` is inactive.

        """
        # skip_if active — delete straight on the wrapped source; no read-only guard (parity with ``__delattr__``).
        if self._shall_skip():
            del self._cfg.obj[key]
            return
        self._check_read_only(f"Deleting item '{key}'")
        del self._get_active()[key]

    # ------------------------------------------------------------------
    # Container protocol
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        """Return length of the active object without emitting a warning."""
        return len(self._get_active())

    def __iter__(self) -> Iterator[Any]:
        """Iterate over the active object, emitting a deprecation warning once the iterator is obtained."""
        active, live = self._gate()
        iterator = iter(active)
        self._deliver(live)
        return iterator

    def __next__(self) -> Any:  # noqa: ANN401
        """Advance the active iterator, emitting the deprecation warning before the iterator moves.

        Advancing consumes an item that cannot be handed back, so the warning comes first: when its delivery raises (a
        ``FutureWarning`` turned into an error, a failing ``stream``) the iterator has not moved and no item is lost.
        The exhausting call that raises ``StopIteration`` is therefore a use too, and spends the budget when it comes
        first. An active object that is not an iterator raises the builtin ``TypeError`` before any warning.

        """
        active, live = self._gate()
        if getattr(type(active), "__next__", None) is None:
            # Not an iterator: ``next`` raises its own ``TypeError`` without touching the object or the budget.
            next(active)
        self._deliver(live)
        return next(active)

    def __contains__(self, item: Any) -> bool:  # noqa: ANN401
        """Check membership in the active object without emitting a warning."""
        return item in self._get_active()

    # ------------------------------------------------------------------
    # Callable protocol
    # ------------------------------------------------------------------

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Call the active object, emitting a deprecation warning conditionally based on target mode.

        Branching logic:
        - :attr:`~deprecate._types.TargetMode.ARGS_REMAP`: warn only when a deprecated kwarg name is present in the
          call; remap, merge ``args_extra``, and call ``obj``.
        - Callable target with ``args_mapping``: warn per deprecated kwarg present; if none of the old names were
          passed still warn at the callable level (class is deprecated); remap, merge ``args_extra``, and forward to
          target.
        - Callable target without ``args_mapping``: warn (global budget), merge ``args_extra``, and forward to target.
        - :attr:`~deprecate._types.TargetMode.ATTRS_REMAP`: governs attribute access only; ``args_extra`` is
          intentionally not applied on ``__call__`` — :func:`~deprecate._validate._validate_proxy_mode`
          already warns about this misconfiguration at decoration time.
        - :attr:`~deprecate._types.TargetMode.NOTIFY`: always warn (global budget) and forward kwargs unchanged;
          ``args_extra`` is intentionally ignored (misconfig).

        """
        dep = object.__getattribute__(self, "__deprecation_config__")
        cfg = object.__getattribute__(self, "_DeprecatedProxy__config")

        # skip_if active — deprecation machinery inactive: call the wrapped source as-is, with no warning,
        # no args_mapping/args_extra handling, and no target forwarding (parity with the callable decorator,
        # where skip_if=True executes the source body unchanged).
        if self._shall_skip():
            return cfg.obj(*args, **kwargs)

        # ATTRS_REMAP stacked over a proxy: delegate the call to the inner proxy without firing the global
        # callable warning. ATTRS_REMAP governs attribute access only; the call itself is not a deprecated
        # surface, so forwarding through the inner proxy avoids double-warning when both layers are active.
        if dep.target is TargetMode.ATTRS_REMAP and isinstance(cfg.obj, _DeprecatedProxy):
            return cfg.obj(*args, **kwargs)
        # ATTRS_REMAP (non-stacking path): only the listed attribute aliases are deprecated, so
        # instantiating/calling the class is not a deprecated surface — forward WITHOUT the global
        # warning (mirrors the stacked branch above and the ARGS_REMAP skip in __getattr__). Moving
        # this check above ``self._warn()`` keeps plain construction from branding the whole class
        # deprecated and burning the global warn budget. args_extra is intentionally not applied —
        # validated as a misconfiguration at decoration time.
        if dep.target is TargetMode.ATTRS_REMAP:
            return self._get_active()(*args, **kwargs)
        if dep.target is TargetMode.ARGS_REMAP:
            return _proxy_call_args_remap(self, dep, cfg, args, kwargs)
        if callable(dep.target) and dep.args_mapping:
            return _proxy_call_callable_with_mapping(self, dep, dep.target, args, kwargs)
        return _proxy_call_global(self, dep, args, kwargs)

    # ------------------------------------------------------------------
    # Comparison and hashing
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        """Compare the *active* object for equality — the object the proxy actually serves.

        Data access (attribute/item/call) forwards to the active object (target when set, else source), so identity #
        operations must too; comparing the source while serving the target would make a # target-forwarding proxy equal
        to an object it never returns.

        """
        obj = self._get_active()
        if isinstance(other, _DeprecatedProxy):
            other = other._get_active()
        return bool(obj == other)

    def __ne__(self, other: object) -> bool:
        """Return the inverse of equality."""
        return not self.__eq__(other)

    def __hash__(self) -> int:
        """Return the hash of the active object (consistent with :meth:`__eq__`)."""
        return hash(self._get_active())

    # ------------------------------------------------------------------
    # String representations — no warning emitted (used for debugging)
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        """Return repr of the active object (the object the proxy serves)."""
        return repr(self._get_active())

    def __str__(self) -> str:
        """Return str of the active object (the object the proxy serves)."""
        return str(self._get_active())

    def __bool__(self) -> bool:
        """Return bool of the active object without emitting a warning."""
        return bool(self._get_active())

    # ------------------------------------------------------------------
    # Copy / pickle protocol — no warning emitted (structural machinery)
    #
    # Semantics: a copy (shallow, deep, or pickle round-trip) is a *proxy again* — the
    # deprecation travels with the object so a copied deprecated config keeps warning during
    # the migration window. The wrapped object is copied per the respective protocol; the
    # frozen DeprecationConfig metadata is preserved; the warn-budget counters are snapshotted.
    # The live ``__deprecated__`` marker travels as-is — a PEP 702 message written onto the proxy
    # survives, and a deleted marker stays deleted (see ``_pep702_marker_state``).
    # Both reconstruction paths bypass ``__init__``, so they re-establish the AST breadcrumbs
    # explicitly against the object the new proxy actually serves — see ``_set_ast_breadcrumbs``.
    # ------------------------------------------------------------------

    def _pep702_marker_state(self) -> tuple[Any, ...]:
        """Return the proxy's live ``__deprecated__`` as ``(value,)``, or ``()`` when it was deleted from the proxy."""
        own = object.__getattribute__(self, "__dict__")
        return (own["__deprecated__"],) if "__deprecated__" in own else ()

    def __copy__(self) -> "_DeprecatedProxy":
        """Return a new proxy wrapping a shallow copy of the wrapped object.

        Deprecation metadata (frozen, immutable) is shared; the mutable warn-budget counters are snapshotted into an
        independent config so the copy warns independently from the original.

        """
        cfg = self._cfg
        new_cfg = replace(cfg, obj=copy.copy(cfg.obj), state=copy.copy(cfg.state))
        doc = object.__getattribute__(self, "__dict__").get("__doc__")
        return _reconstruct_proxy(new_cfg, self._dep, doc, self._pep702_marker_state())

    def __deepcopy__(self, memo: dict[int, Any]) -> "_DeprecatedProxy":
        """Return a new proxy deep-copying the wrapped object (and any instance target).

        Registers the new proxy in *memo* before descending so self-referential structures that contain the proxy deep-
        copy without infinite recursion.

        """
        cls = type(self)
        new = cls.__new__(cls)
        memo[id(self)] = new
        new_cfg = copy.deepcopy(self._cfg, memo)
        new_dep = copy.deepcopy(self._dep, memo)
        object.__setattr__(new, "_DeprecatedProxy__config", new_cfg)
        object.__setattr__(new, "__deprecation_config__", new_dep)
        _restore_pep702_marker(new, new_dep, copy.deepcopy(self._pep702_marker_state(), memo))
        doc = object.__getattribute__(self, "__dict__").get("__doc__")
        if doc is not None:
            object.__setattr__(new, "__doc__", doc)
        # Breadcrumbs point at the *deep-copied* source, not the original's: this proxy forwards to
        # ``new_cfg.obj``, so that is what ``inspect.unwrap`` and Sphinx must resolve to.
        new._set_ast_breadcrumbs(new_cfg.obj)
        return new

    def __reduce_ex__(self, protocol: SupportsIndex) -> tuple[Any, ...]:
        """Support pickling: reconstruct the proxy from its two config dataclasses.

        The default ``object.__reduce_ex__`` probes attributes through ``__getattr__`` and cannot
        rebuild an instance whose state lives behind ``object.__setattr__``; this override pickles
        the runtime config and frozen metadata directly and rebuilds via
        :func:`~deprecate.proxy._reconstruct_proxy` without re-running ``__init__`` validation.

        Note:
            The ``stream`` callable is pickled by reference; lambdas and closures are not picklable
            by the standard pickle protocol.  Use a module-level callable (e.g. ``warnings.warn``)
            or ``None`` when you intend to pickle the proxy.

            For :func:`~deprecate.proxy.deprecated_instance` proxies the wrapped object is serialised
            directly and pickle works reliably.  For :func:`~deprecate.proxy.deprecated_class` proxies
            the wrapped class is serialised by reference; if the module-level name was replaced by the
            proxy (the typical usage), pickle cannot resolve the reference and raises
            :class:`~pickle.PicklingError`.

        """
        doc = object.__getattribute__(self, "__dict__").get("__doc__")
        return (_reconstruct_proxy, (self._cfg, self._dep, doc, self._pep702_marker_state()))

    # ------------------------------------------------------------------
    # Type protocol — supports isinstance/issubclass against a proxy
    # ------------------------------------------------------------------

    @property  # type: ignore[misc]
    def __class__(self) -> type:
        """Report the active object's class so ``isinstance(proxy, WrappedType)`` holds.

        Instance-side type transparency (the ``wrapt``/``unittest.mock`` technique): ``isinstance``
        honours ``__class__``, so consumer code type-checking the deprecated object (json encoders,
        validators, defensive ``isinstance``) behaves identically before and after wrapping.
        Stacked proxy chains are resolved to the innermost concrete object. ``type(proxy)`` still
        returns :class:`_DeprecatedProxy` for code that needs the truth, and
        ``isinstance(proxy, _DeprecatedProxy)`` remains ``True`` via the ``type()`` fast path.
        No warning is emitted — type checks are structural, matching
        :meth:`~deprecate.proxy._DeprecatedProxy.__instancecheck__`.

        *Class* proxies (``deprecated_class`` aliases) deliberately keep reporting the proxy
        class: forwarding the metaclass would make ``isinstance(proxy, type)`` true, and
        class-dispatching decorators (e.g. PEP 702 ``typing_extensions.deprecated``) would then
        patch the wrapped class in place instead of wrapping the proxy callable.  Class-side
        transparency is provided by ``__instancecheck__``/``__subclasscheck__`` instead.

        """
        active = _DeprecatedProxy._get_static_attr_owner(self)
        if isinstance(active, type):
            return type(self)
        return type(active)

    def __instancecheck__(self, instance: object) -> bool:
        """Support ``isinstance(x, proxy)`` by delegating to the active class.

        Allows a proxy used as a deprecated class alias to work transparently with ``isinstance`` without emitting a
        warning — type checks are structural, not a use of the deprecated API.

        When the active object is itself a :class:`_DeprecatedProxy` (stacked proxy chain), the loop walks inward until
        a concrete type is reached. Defensive cycle guard breaks the loop on the unlikely case of a circular stack so
        this dunder never blocks.

        Raises ``TypeError`` (mirroring the builtin) when the active object is an instance rather than a type — using an
        *instance* proxy as the second argument to ``isinstance`` is a misuse. Returns ``False`` only on a circular
        proxy stack.

        """
        seen: set[int] = set()
        active = self._get_active()
        while isinstance(active, _DeprecatedProxy):
            if id(active) in seen:
                return False
            seen.add(id(active))
            active = active._get_active()
        if isinstance(active, type):
            # Delegate via isinstance to preserve metaclass-defined instance checks.
            return isinstance(instance, active)
        # Active object is an instance, not a class: using an *instance* proxy as the second argument to
        # ``isinstance`` is a misuse.  Raise the same ``TypeError`` the builtin does instead of silently
        # returning ``False`` (which hid the mistake).
        raise TypeError("isinstance() arg 2 must be a type, a tuple of types, or a union")

    def __subclasscheck__(self, subclass: type) -> bool:
        """Support ``issubclass(X, proxy)`` by delegating to the active class.

        Same rationale as :meth:`~deprecate.proxy._DeprecatedProxy.__instancecheck__` — no warning emitted.
        Stacked proxy chains are walked iteratively until a concrete type is reached; a defensive cycle guard
        breaks the loop on the unlikely case of a circular stack.

        Raises ``TypeError`` (mirroring the builtin) when the active object is an instance rather than a type.
        Returns ``False`` only on a circular proxy stack.

        """
        seen: set[int] = set()
        active = self._get_active()
        while isinstance(active, _DeprecatedProxy):
            if id(active) in seen:
                return False
            seen.add(id(active))
            active = active._get_active()
        if isinstance(active, type):
            # Delegate via issubclass so that any metaclass-defined
            # __subclasscheck__ (e.g., from abc.ABCMeta) is respected.
            return issubclass(subclass, active)
        # Active object is an instance, not a class: using an *instance* proxy as the second argument to
        # ``issubclass`` is a misuse.  Raise the same ``TypeError`` the builtin does instead of silently
        # returning ``False`` (which hid the mistake).
        raise TypeError("issubclass() arg 2 must be a class, a tuple of classes, or a union")

    # ------------------------------------------------------------------
    # Subclassing protocol (PEP 560) — supports ``class Child(DeprecatedAlias)``
    # ------------------------------------------------------------------

    def __mro_entries__(self, bases: tuple[Any, ...]) -> tuple[Any, ...]:
        """Resolve the proxy to the active class when it appears in a subclass ``bases`` list.

        ``deprecated_class`` replaces the class binding with a (non-``type``) proxy, so ``class Child(OldName)`` would
        otherwise pass the proxy to the class machinery and fail with an opaque metaclass arity error. PEP 560's
        ``__mro_entries__`` hook lets the proxy substitute the concrete active class (unwrapping any stacked-proxy
        chain) so the subclass transparently derives from the replacement.

        Subclassing *is* a use of the deprecated name, so a warning is emitted here — except for
        :attr:`~deprecate._types.TargetMode.ATTRS_REMAP` / :attr:`~deprecate._types.TargetMode.ARGS_REMAP`, whose
        deprecation scope is a specific set of attributes/arguments rather than the class itself (consistent with
        :meth:`__call__` and :meth:`__getattr__`).

        """
        if self._dep.target not in (TargetMode.ATTRS_REMAP, TargetMode.ARGS_REMAP):
            self._warn()
        return (_DeprecatedProxy._get_static_attr_owner(self),)

    # ------------------------------------------------------------------
    # Numeric/string dunders with non-uniform signatures — hand-written
    # ------------------------------------------------------------------

    def __round__(self, ndigits: Any = None) -> Any:  # noqa: ANN401
        """Round the active object, emitting a deprecation warning (numeric read) once rounding succeeds."""
        active, live = self._gate()
        rounded = round(active) if ndigits is None else round(active, ndigits)
        self._deliver(live)
        return rounded

    def __format__(self, format_spec: str) -> str:
        """Format the active object without emitting a warning (a repr-like probe, mirroring ``__str__``)."""
        return format(self._get_active(), format_spec)


def _proxy_call_with_positional_split(
    ctor: Callable[..., Any],
    facts: _CallFacts,
    args: tuple[Any, ...],
    mapped_kwargs: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Invoke *ctor*, forwarding kwargs bound to POSITIONAL_ONLY constructor params positionally.

    Adopts the ``@deprecated`` dispatcher's split approach: remapped values whose target parameter is positional-only
    are reordered into positional arguments by declaration order, after the ``consumed=len(args)`` slots the caller
    already filled positionally.  This keeps required positional-only params, immutable (frozen-dataclass-style)
    instances, and constructor-derived state working — unlike the historical pop-and-``setattr`` fallback.  The split
    data comes from :attr:`~deprecate._types._ProxyConfig.facts`.

    """
    if facts.target_positional_only:
        pos_args, kw_args = _split_positional_only_kwargs(
            facts.target_positional_only_order, mapped_kwargs, facts.target_positional_only, consumed=len(args)
        )
        return ctor(*args, *pos_args, **kw_args)
    return ctor(*args, **mapped_kwargs)


def _restore_pep702_marker(
    proxy: "_DeprecatedProxy", dep: DeprecationConfig, marker: Optional[tuple[Any, ...]]
) -> None:
    """Re-attach ``__deprecated__`` to a rebuilt proxy from the state carried by a copy or pickle.

    Args:
        proxy: The half-built proxy (state attached via ``object.__setattr__``).
        dep: Frozen deprecation metadata used to render the default message.
        marker: ``(value,)`` to carry the original's live marker, ``()`` when the original had it deleted, or
            ``None`` — a pickle payload from a release that did not record the marker — to render the default.

    """
    if marker is None:
        source_path = _proxy_source_path(object.__getattribute__(proxy, "_DeprecatedProxy__config").obj, dep.name)
        object.__setattr__(proxy, "__deprecated__", _render_static_deprecation_message(dep, dep.name, source_path))
    elif marker:
        object.__setattr__(proxy, "__deprecated__", marker[0])


def _reconstruct_proxy(
    cfg: _ProxyConfig,
    dep: DeprecationConfig,
    doc: Optional[str],
    pep702_marker: Optional[tuple[Any, ...]] = None,
) -> "_DeprecatedProxy":
    """Rebuild a :class:`_DeprecatedProxy` from its config dataclasses without re-running ``__init__``.

    Used by ``__copy__`` and as the pickle reconstructor in ``__reduce_ex__``: re-running
    ``__init__`` would repeat decoration-time validation and misconfiguration warnings, so the
    state is re-attached directly via ``object.__setattr__`` (bypassing the proxy's forwarding
    ``__setattr__``).

    The AST breadcrumbs are re-derived from ``cfg.obj`` rather than carried in *cfg*: a copy wraps its
    own (possibly copied) source, and recomputing keeps ``inspect.Signature`` objects out of the pickle
    payload entirely.  Skipping this step would leave copied and unpickled proxies without
    ``__wrapped__``/``__signature__``, so ``inspect.unwrap`` would stop at the proxy and the object would
    no longer satisfy the :class:`~deprecate._types.DeprecationProxy` Protocol.

    Args:
        cfg: Private mutable runtime state for the new proxy.
        dep: Frozen deprecation metadata (shared or copied by the caller as appropriate).
        doc: Instance-level ``__doc__`` mirrored from the wrapped object, or ``None`` when the
            original proxy carried no instance docstring.
        pep702_marker: The original's live ``__deprecated__`` state — see :func:`_restore_pep702_marker`.
            Defaults to ``None`` so pickles written without it still load with the rendered message.

    """
    # A 0.14 payload carries no ``facts`` (``_ProxyConfig.__setstate__`` starts them empty) but its metadata holds the
    # constructor POSITIONAL_ONLY split; without this rebuild the restored proxy would pass those values by keyword.
    if not cfg.facts.target_positional_only and dep.target_positional_only:
        cfg.facts = replace(
            cfg.facts,
            target_positional_only=dep.target_positional_only,
            target_positional_only_order=dep.target_positional_only_order,
        )
    proxy_type = _proxy_type_for(cfg.obj)
    proxy = proxy_type.__new__(proxy_type)
    object.__setattr__(proxy, "_DeprecatedProxy__config", cfg)
    object.__setattr__(proxy, "__deprecation_config__", dep)
    _restore_pep702_marker(proxy, dep, pep702_marker)
    if doc is not None:
        object.__setattr__(proxy, "__doc__", doc)
    proxy._set_ast_breadcrumbs(cfg.obj)
    return proxy


def _proxy_call_global(
    proxy: _DeprecatedProxy,
    dep: DeprecationConfig,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Warn globally before forwarding a call, merging extras except in NOTIFY mode."""
    # Callable target without args_mapping: warn globally; still merge args_extra so
    # injected defaults reach the target.
    proxy._warn()
    if callable(dep.target):
        return dep.target(*args, **proxy._merge_args_extra(kwargs))
    # NOTIFY: forward unchanged — args_extra is intentionally ignored (already warned at construction).
    if dep.target is TargetMode.NOTIFY:
        return proxy._get_active()(*args, **kwargs)
    # No target configured (e.g. deprecated_instance with no target): merge args_extra into the call so
    # wrappers can inject default kwargs even without a forwarding target.
    return proxy._get_active()(*args, **proxy._merge_args_extra(kwargs))


def _proxy_call_args_remap(
    proxy: _DeprecatedProxy,
    dep: DeprecationConfig,
    cfg: _ProxyConfig,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Handle ``TargetMode.ARGS_REMAP`` branch of ``__call__``: warn per deprecated kwarg, remap, call obj."""
    mapping = dep.args_mapping or {}
    for old_key in mapping:
        if old_key in kwargs:
            proxy._warn(arg_name=old_key)
    mapped_kwargs = _remap_kwargs(mapping, kwargs, name=dep.name, stream=cfg.stream)
    mapped_kwargs = proxy._merge_args_extra(mapped_kwargs)
    return _proxy_call_with_positional_split(cfg.obj, cfg.facts, args, mapped_kwargs)


def _proxy_call_callable_with_mapping(
    proxy: _DeprecatedProxy,
    dep: DeprecationConfig,
    target_fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Handle callable-target + args_mapping branch of ``__call__``: warn per kwarg, remap, forward."""
    mapping = dep.args_mapping or {}
    for old_key in mapping:
        if old_key in kwargs:
            proxy._warn(arg_name=old_key)
    if not any(old_key in kwargs for old_key in mapping):
        proxy._warn()
    mapped_kwargs = _remap_kwargs(mapping, kwargs, name=dep.name, stream=proxy._cfg.stream)
    mapped_kwargs = proxy._merge_args_extra(mapped_kwargs)
    return _proxy_call_with_positional_split(target_fn, proxy._cfg.facts, args, mapped_kwargs)


def _proxy_source_path(obj: Any, name: str) -> str:  # noqa: ANN401
    """Return the ``%(source_path)s`` of a proxy over *obj* displayed as *name*.

    A wrapped class or plain function renders as ``f"{obj.__module__}.{name}"``; any other object (an instance, a
    builtin) has no module of its own and renders as *name*.  A stacked proxy is looked through to the object the
    innermost proxy wraps, read via ``object.__getattribute__`` so no deprecated attribute access fires; the type
    checks use ``type(obj)`` for the same reason.  Defensive cycle guard as in :meth:`_get_static_attr_owner`.

    Args:
        obj: The object the proxy wraps (its ``_ProxyConfig.obj``).
        name: Display name of the proxy (``DeprecationConfig.name``).

    Returns:
        The module-qualified path, or *name* when *obj* has no module of its own.

    Example:
        >>> from collections import OrderedDict
        >>> _proxy_source_path(OrderedDict, "OrderedDict"), _proxy_source_path(OrderedDict(), "cfg")
        ('collections.OrderedDict', 'cfg')

    """
    seen: set[int] = set()
    while issubclass(type(obj), _DeprecatedProxy):
        if id(obj) in seen:
            break
        seen.add(id(obj))
        obj = object.__getattribute__(obj, "_DeprecatedProxy__config").obj
    if not issubclass(type(obj), (type, types.FunctionType)):
        return name
    module = getattr(obj, "__module__", None)
    return f"{module}.{name}" if isinstance(module, str) and module else name


def _build_proxy_warn_msg(
    proxy: _DeprecatedProxy,
    arg_name: Optional[str],
    dep: DeprecationConfig,
    cfg: _ProxyConfig,
) -> Optional[str]:
    """Build the warning message string for a proxy warning emission.

    Every branch renders through :func:`~deprecate.messaging._format_deprecation_message`, so placeholders the branch
    does not fill render as empty strings, as on the ``@deprecated`` path.  ``%(source_path)s`` comes from
    :func:`_proxy_source_path`.

    Returns ``None`` when the warning should be suppressed (``attrs_mapping`` lookup miss).

    """
    target: Any = dep.target
    args_mapping = dep.args_mapping
    custom_template = cfg.message_template
    source_path = _proxy_source_path(cfg.obj, dep.name)
    versions = {"deprecated_in": dep.deprecated_in, "remove_in": dep.remove_in}

    if arg_name is not None and args_mapping and arg_name in args_mapping:
        new_arg = args_mapping[arg_name]
        return _format_deprecation_message(
            custom_template or TEMPLATE_WARNING_ARGUMENTS,
            dep.name,
            source_path,
            target_name=str(new_arg) if new_arg is not None else "",
            argument_map=TEMPLATE_ARGUMENT_MAPPING % {"old_arg": arg_name, "new_arg": str(new_arg)},
            **versions,
        )
    if arg_name is not None and arg_name.startswith("__attr__:") and dep.attrs_mapping is not None:
        return proxy._build_attr_warning_msg(arg_name, dep, dep.attrs_mapping, target, custom_template, source_path)
    if callable(target):
        # ``functools.partial`` and callable-instance targets carry no ``__name__``; the helper names them.
        target_name, target_path = _callable_display_name(target)
        return _format_deprecation_message(
            custom_template or TEMPLATE_WARNING_CALLABLE,
            dep.name,
            source_path,
            target_name=target_name,
            target_path=target_path,
            **versions,
        )
    return _format_deprecation_message(custom_template or TEMPLATE_WARNING_NO_TARGET, dep.name, source_path, **versions)


class _BindingDeprecatedProxy(_DeprecatedProxy):
    """A deprecation proxy over a callable that binds as a method when read through an instance.

    Covers a plain function, a ``@deprecated`` wrapper, and a nested proxy over either. Defining ``__get__`` makes an
    object a descriptor, and :func:`inspect.isroutine` / :func:`inspect.ismethoddescriptor` report every descriptor type
    as a routine — so ``__get__`` lives only on this subclass, which :func:`_proxy_type_for` selects only when
    :func:`_binds_as_method` holds. Proxies over data, classes and other callables (``functools.partial``, bound
    methods, builtins) stay plain :class:`_DeprecatedProxy` instances and are not reported as routines.

    """

    def __get__(self, instance: Any, owner: Optional[type] = None) -> Any:  # noqa: ANN401
        """Bind a wrapped function stored as a class attribute, so a call through an instance receives the instance.

        Python turns a plain function read through an instance into a bound method; a proxy stored in a class body
        (``class Model: scale = deprecated_instance(legacy_scale, ...)``) must do the same, or ``Model().scale(2)``
        would call ``legacy_scale(2)`` without the instance. For a wrapped plain function, ``@deprecated`` wrapper, or a
        nested binding proxy over either, instance access returns a :class:`types.MethodType` whose ``__func__`` is
        this proxy, so the call warns against this proxy's budget and honours ``skip_if`` and fatal mode exactly as a
        direct call does, with no extra Python frame. The wrapped object's own ``__get__`` is never called.

        Class access (``Model.scale``, where a plain function does not bind either) returns this proxy unchanged. Every
        other wrapped object gets a plain :class:`_DeprecatedProxy` without ``__get__`` (see :func:`_binds_as_method`),
        so it is served as stored too.

        Binding is silent: attribute access is how ``hasattr``, ``inspect.getmembers`` and autodoc read members, while
        the call is the use of the deprecated API and warns.

        """
        # The wrapped object is not asked to bind itself: only a plain function or a nested binding proxy gets here
        # (see ``_binds_as_method``), and both bind any instance by prepending it, so no foreign ``__get__`` runs.
        if instance is None or not _binds_as_method(self._cfg.obj):
            return self
        return types.MethodType(self, instance)


def _binds_as_method(obj: Any) -> bool:  # noqa: ANN401
    """Return whether a proxy over *obj* binds like a method when read through an instance.

    Only a plain Python function — which covers ``@deprecated`` wrappers — and a nested binding proxy over one bind. The
    check is by exact type, not by the presence of ``__get__``: ``functools.partial`` gains a ``__get__`` that warns on
    3.13 and binds on 3.14, method descriptors reject foreign instances, and a callable whose binding the proxy cannot
    predict is served unbound on every Python version. Cython-style binding callables are not bound either.

    """
    # ``type()`` rather than ``isinstance`` for the function: a proxy reports its wrapped object's class through
    # ``__class__``, so a plain proxy over a function would pass ``isinstance(obj, types.FunctionType)``.
    return type(obj) is types.FunctionType or isinstance(obj, _BindingDeprecatedProxy)


def _proxy_type_for(obj: Any) -> type[_DeprecatedProxy]:  # noqa: ANN401
    """Return the proxy class for *obj*: :class:`_BindingDeprecatedProxy` when it binds as a method, else the base.

    Uses :func:`_binds_as_method`, the same test :meth:`_BindingDeprecatedProxy.__get__` applies, so the proxy type and
    its binding behaviour cannot disagree.

    """
    if _binds_as_method(obj):
        return _BindingDeprecatedProxy
    return _DeprecatedProxy


# Pickles and reprs name the proxy classes and the reconstruction helpers by their home before the package split,
# deprecate.proxy (which re-exports them); keep that identity so existing payloads and reprs are unchanged.
_DeprecatedProxy.__module__ = "deprecate.proxy"
_BindingDeprecatedProxy.__module__ = "deprecate.proxy"
_reconstruct_proxy.__module__ = "deprecate.proxy"
_restore_pep702_marker.__module__ = "deprecate.proxy"

# The proxy classes' methods keep the same module identity as their classes.
for _class in (_DeprecatedProxy, _BindingDeprecatedProxy):
    for _member in vars(_class).values():
        if isinstance(_member, (staticmethod, classmethod)):
            _member = _member.__func__
        if isinstance(_member, types.FunctionType):
            _member.__module__ = "deprecate.proxy"
        elif isinstance(_member, property):
            for _accessor in (_member.fget, _member.fset, _member.fdel):
                if _accessor is not None:
                    _accessor.__module__ = "deprecate.proxy"
