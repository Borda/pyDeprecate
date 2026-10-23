"""Proxy utilities for deprecating Python object instances and classes.

Provides :func:`~deprecate.proxy.deprecated_instance` for wrapping any Python object with
transparent deprecation warnings and optional read-only enforcement, and
:func:`~deprecate.proxy.deprecated_class` as a class-level decorator with optional target
redirection.

Typical use cases:

- Deprecating module-level config dicts, constants, or legacy singletons
  while still allowing reads during a migration window.
- Deprecating an Enum or dataclass in favor of a replacement type, with
  automatic forwarding of all attribute, item, and call access.

Both entry points return an object satisfying :class:`~deprecate._types.DeprecationProxy`, the public
protocol for proxies. Every proxy carries ``__wrapped__`` (the source object) and ``__signature__``
(the source's signature) so ``inspect.unwrap``, ``inspect.signature``, Sphinx autodoc, and IDEs
resolve through the proxy to the original — reading either attribute emits no deprecation warning.

Context entry releases an acquired resource if warning delivery raises. ``next`` warns before it advances the
iterator, so a failing delivery loses no item. Async next and await warnings fire when the underlying awaitable or
iterator is obtained, before deferred execution; if that delivery raises, a coroutine the acquisition created is
closed unstarted instead of being left unawaited.

Example:
    >>> import warnings
    >>> cfg = {"threshold": 0.5}
    >>> proxy = deprecated_instance(cfg, deprecated_in="1.0", remove_in="2.0", stream=None)
    >>> proxy["threshold"]
    0.5

    >>> proxy.get("threshold")
    0.5

"""

import copy
import gc
import inspect
import math
import operator
import os
import sys
import threading
import types
import warnings
from collections.abc import Iterator
from dataclasses import replace
from typing import Any, Callable, Literal, Optional, Protocol, SupportsIndex, TypeVar, Union, cast, overload

from deprecate._dispatch import _split_positional_only_kwargs
from deprecate._fatal import DeprecatedError, _resolve_as_exception
from deprecate._frames import _caller_stacklevel
from deprecate._types import (
    DeprecationConfig,
    DeprecationProxy,
    TargetMode,
    _ProxyConfig,
    get_deprecation_config,
)
from deprecate.docstring.inject import _update_docstring_with_deprecation, normalize_docstring_style
from deprecate.messaging import (
    TEMPLATE_ARGUMENT_MAPPING,
    TEMPLATE_WARNING_ARGUMENTS,
    TEMPLATE_WARNING_CALLABLE,
    TEMPLATE_WARNING_NO_TARGET,
    _callable_display_name,
    _emit,
    _render_static_deprecation_message,
    _resolve_message_template_alias,
    _validate_message_template,
    deprecation_warning,
)
from deprecate.utils import _apply_args_mapping_collisions, _get_args_mapping_positional_only_keys, _is_dataclass_target


def _is_dunder(name: str) -> bool:
    """Return whether *name* is a dunder attribute name (``__x__``) — introspection machinery, not user API."""
    return name.startswith("__") and name.endswith("__")


def _consume_proxy_warn_budget(cfg: _ProxyConfig, arg_name: Optional[str]) -> bool:
    """Claim one slot of a proxy's warn budget, returning whether the caller may emit the warning.

    The quota check and the counter increment happen together under ``cfg.lock`` so concurrent first accesses
    cannot all read the counter as ``0``, all pass the gate, and each emit a warning (check-then-act race).
    Mirrors the decorator path's decide-and-increment-under-the-lock pattern; the warning itself is emitted by
    the caller, outside the lock, to avoid serialising callers on a potentially slow ``stream`` callable.

    Args:
        cfg: The proxy's mutable runtime config holding the counters and the lock.
        arg_name: A renamed argument's name for a per-argument budget, or ``None`` for the proxy-wide counter.

    Returns:
        ``True`` when a slot was claimed and the warning must be emitted, ``False`` when the budget is spent.

    Examples:
        >>> from deprecate._types import _ProxyConfig
        >>> cfg = _ProxyConfig(obj=object(), stream=None, num_warns=1, read_only=False)
        >>> _consume_proxy_warn_budget(cfg, None), _consume_proxy_warn_budget(cfg, None)
        (True, False)

    """
    with cfg.lock:
        if arg_name is not None:
            arg_count = cfg.warned_args.get(arg_name, 0)
            if cfg.num_warns < 0 or arg_count < cfg.num_warns:
                cfg.warned_args[arg_name] = arg_count + 1
                return True
            return False
        if cfg.num_warns < 0 or cfg.warned < cfg.num_warns:
            cfg.warned += 1
            return True
        return False


def _release_proxy_warn_budget(cfg: _ProxyConfig, arg_name: Optional[str]) -> None:
    """Give back a slot claimed by :func:`_consume_proxy_warn_budget` whose warning was never delivered.

    When the ``stream`` raises (a ``FutureWarning`` filter set to ``"error"``, a failing logging sink) the caller did
    not receive the notice, so the slot returns to the budget and the next use warns again. An unlimited budget
    (``num_warns < 0``) has no slot to return, so nothing changes.

    Args:
        cfg: The proxy's mutable runtime config holding the counters and the lock.
        arg_name: The key the slot was claimed under, as for :func:`_consume_proxy_warn_budget`.

    Examples:
        >>> from deprecate._types import _ProxyConfig
        >>> cfg = _ProxyConfig(obj=object(), stream=None, num_warns=1, read_only=False)
        >>> _consume_proxy_warn_budget(cfg, None)
        True
        >>> _release_proxy_warn_budget(cfg, None)
        >>> _consume_proxy_warn_budget(cfg, None)
        True

    """
    if cfg.num_warns < 0:
        return
    with cfg.lock:
        if arg_name is not None:
            cfg.warned_args[arg_name] -= 1
        else:
            cfg.warned -= 1


async def _sample_coroutine() -> None:
    """Do nothing; one instance is created and closed at import to look up the coroutine await-iterator type."""


def _coroutine_wrapper_type() -> type:
    """Return the type of ``coroutine.__await__()`` (CPython's ``coroutine_wrapper``), which :mod:`types` does not name.

    The sample coroutine is closed before it starts, so creating it runs no code and leaves no unawaited coroutine.

    """
    sample = _sample_coroutine()
    try:
        return type(sample.__await__())
    finally:
        sample.close()


#: Type of the iterator returned by ``coroutine.__await__()``.
_COROUTINE_WRAPPER_TYPE = _coroutine_wrapper_type()


def _exit_acquired(context: Any, *exc_info: Any) -> None:  # noqa: ANN401
    """Unwind a context the proxy entered, handing its ``__exit__`` the error that aborted the entry.

    Whatever ``__exit__`` returns is ignored: the caller re-raises that error, so a suppressing context cannot hide it.

    """
    context.__exit__(*exc_info)


def _held_by(owner: Any) -> list[Any]:  # noqa: ANN401
    """List *owner* and the objects it references directly, looking one level into an instance ``__dict__``.

    Depending on the Python version, :func:`gc.get_referents` reports an instance's attribute values directly or through
    its ``__dict__``, so both shapes are expanded. A deprecation proxy is followed down its ``__wrapped__`` chain, so
    the object at the bottom of any stack of proxies counts as held by the outermost one.

    """
    held: list[Any] = []
    seen: set[int] = set()
    while id(owner) not in seen:
        seen.add(id(owner))
        held.append(owner)
        for ref in gc.get_referents(owner):
            held.append(ref)
            if type(ref) is dict:
                held.extend(ref.values())
        if not isinstance(owner, _DeprecatedProxy):
            break
        # Read the slot directly: attribute access through the proxy would warn.
        owner = object.__getattribute__(owner, "__wrapped__")
    return held


def _close_abandoned(acquired: Any, owner: Any, *exc_info: Any) -> None:  # noqa: ANN401
    """Close a coroutine that an acquiring call created, so it is not left behind unawaited.

    When warning delivery fails after ``__anext__`` or ``__await__`` returned, the caller never awaits what it got, and
    the garbage collector would report ``coroutine ... was never awaited`` on top of the real error. An unstarted
    coroutine closes without running its body, so closing is safe for what the call created fresh: a coroutine (from
    ``__anext__``) or the await iterator of one (``__await__`` returning ``helper().__await__()``).

    The result type alone does not prove the call created it: ``__await__`` can return the await iterator of a
    coroutine the object stores, a synchronous ``__anext__`` can return a cached coroutine, and a stacked proxy acquires
    from the inner proxy, which holds the wrapped coroutine. Closing a borrowed coroutine breaks the retry with
    ``cannot reuse already awaited coroutine``, so the coroutine is left open whenever *owner* is it or references it,
    directly or through its instance ``__dict__``, looking through any stack of deprecation proxies. A coroutine stored
    deeper inside the owner (in a list attribute or on a helper object it holds) is not detected and is closed.

    Everything else is left untouched, because it can be a view of the wrapped object itself: an async generator's
    ``asend`` awaitable (on CPython 3.13 and later closing it finishes the generator, so later iteration would end
    with no items). On CPython 3.13 and later an abandoned ``asend`` still reports that it was never awaited; keeping
    the stream usable wins. Other awaitables (futures, plain generators) need no cleanup.

    Args:
        acquired: The awaitable or await iterator the wrapped object returned.
        owner: The object it was acquired from.
        exc_info: The delivery error's ``sys.exc_info()``, accepted for the release-callback signature and unused.

    """
    kind = type(acquired)
    if kind is types.CoroutineType:
        coroutine = acquired
    elif kind is _COROUTINE_WRAPPER_TYPE:
        coroutine = next((ref for ref in gc.get_referents(acquired) if type(ref) is types.CoroutineType), None)
    else:
        return
    if coroutine is None or any(ref is coroutine for ref in _held_by(owner)):
        return
    acquired.close()


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

    @staticmethod
    def _get_static_attr_owner(obj: Any) -> Any:  # noqa: ANN401
        """Return the object whose attributes should be validated without dynamic lookup.

        For proxy targets, validate against the same "active" object attribute access will ultimately reach (callable
        target when configured, otherwise the wrapped source).

        Defensive cycle guard: stacked-proxy chains are linear in normal use (the frozen
        :class:`~deprecate._types.DeprecationConfig` prevents reassignment of ``target`` after construction),
        but the guard breaks the loop on the unlikely case of a circular chain so this helper never blocks.

        """
        seen: set[int] = set()
        while isinstance(obj, _DeprecatedProxy):
            if id(obj) in seen:
                break
            seen.add(id(obj))
            dep = get_deprecation_config(obj)
            if dep is None:
                break
            cfg = object.__getattribute__(obj, "_DeprecatedProxy__config")
            target = dep.target
            obj = target if target is not None and not isinstance(target, TargetMode) else cfg.obj
        return obj

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
        """Validate ``attrs_mapping`` at decoration time.

        Runs three checks against the configured mapping:

        1. Reject true cycles in the redirect chain.
        2. Confirm every non-``None`` redirect target attribute exists on *attr_check_obj* so
           accessing a deprecated alias never raises :class:`AttributeError` on the first warning.
        3. Confirm every warn-only key (``None`` value) exists on at least one of *attr_check_obj*
           or *obj* — a key on neither would always raise :class:`AttributeError` on access.

        Args:
            attrs_mapping: The redirect map from deprecated attribute names to canonical names
                (or ``None`` for warn-only).
            obj: The wrapped source object — used as a fallback for warn-only key existence.
            attr_check_obj: The class against which redirect targets are validated; the caller
                pre-computes this from a concrete callable target, falling back to *obj* for
                ``TargetMode`` values and legacy boolean sentinels.

        Raises:
            ValueError: If *attrs_mapping* contains a cycle, a missing redirect target, or a
                warn-only key absent from both classes.

        """
        # Reject true cycles in ``attrs_mapping`` at decoration time. Non-cyclic chains are allowed at runtime
        # and surfaced by audit as mapping chains.
        cycle_starters: list[str] = []
        for start in attrs_mapping:
            visited: set[str] = {start}
            current = attrs_mapping[start]
            while current is not None and current in attrs_mapping:
                if current in visited:
                    cycle_starters.append(start)
                    break
                visited.add(current)
                current = attrs_mapping[current]
        if cycle_starters:
            raise ValueError(
                f"`attrs_mapping` has circular redirects — the redirect chain starting from"
                f" {sorted(set(cycle_starters))} loops back to a previously visited key."
                " Redirect chains must terminate at an attribute name that does not loop."
            )
        _DeprecatedProxy._validate_attrs_redirect_targets(attrs_mapping, attr_check_obj)
        _DeprecatedProxy._validate_attrs_warn_only_keys(attrs_mapping, obj, attr_check_obj)

    @staticmethod
    def _validate_attrs_redirect_targets(
        attrs_mapping: dict[str, Optional[str]],
        attr_check_obj: Any,  # noqa: ANN401
    ) -> None:
        """Validate that every non-``None`` redirect target attribute exists on the class.

        Ensures that accessing a deprecated alias never raises :class:`AttributeError` on the
        first warning — every redirect target must resolve to an existing attribute on
        *attr_check_obj* at decoration time.

        Args:
            attrs_mapping: The redirect map from deprecated attribute names to canonical names
                (or ``None`` for warn-only).
            attr_check_obj: The class against which non-``None`` redirect targets are validated.

        Raises:
            ValueError: If any non-``None`` redirect target is absent from *attr_check_obj*.

        """
        # For @dataclass targets, required fields (no default) are not visible via getattr_static
        # because they have no class-level value — but they DO exist as valid constructor params
        # and instance attributes.  Check __dataclass_fields__ as a fallback.
        _owner = _DeprecatedProxy._get_static_attr_owner(attr_check_obj)
        _dc_field_names: set[str] = set(getattr(_owner, "__dataclass_fields__", {}))
        _resolved_targets = list(attrs_mapping.items())
        missing_targets = [
            f"{k!r} -> {target!r}"
            for k, target in _resolved_targets
            if target is not None
            and not _DeprecatedProxy._has_static_attribute(attr_check_obj, target)
            and target not in _dc_field_names
        ]
        if missing_targets:
            raise ValueError(
                f"`attrs_mapping` redirect targets not found on the active class: {missing_targets}."
                " Each non-None value must be an existing attribute name on the target class when `target` is"
                " provided, or on the wrapped class otherwise."
            )

    @staticmethod
    def _validate_attrs_warn_only_keys(
        attrs_mapping: dict[str, Optional[str]],
        obj: Any,  # noqa: ANN401
        attr_check_obj: Any,  # noqa: ANN401
    ) -> None:
        """Validate that every warn-only key (``None``-value entry) exists on at least one class.

        A ``None`` redirect means "warn but still serve the attribute value" — the attribute can
        live on the target (same-name warning: kept in new API) or only on the source
        (being-removed attribute that falls back to the source value in ``__getattr__``). Raise
        only when absent from both classes; a key on neither would always raise
        :class:`AttributeError` on access.

        Args:
            attrs_mapping: The redirect map from deprecated attribute names to canonical names
                (or ``None`` for warn-only).
            obj: The wrapped source object — used as a fallback for warn-only key existence.
            attr_check_obj: The class against which warn-only keys are first checked before
                falling back to *obj*.

        Raises:
            ValueError: If any warn-only key is absent from both *attr_check_obj* and *obj*.

        """
        missing_keys = [
            k
            for k, v in attrs_mapping.items()
            if v is None
            and not _DeprecatedProxy._has_static_attribute(attr_check_obj, k)
            and not _DeprecatedProxy._has_static_attribute(obj, k)
        ]
        if missing_keys:
            raise ValueError(
                f"`attrs_mapping` warn-only keys not found on either class: {missing_keys}."
                " Each key with a `None` value must be an existing attribute name on the target class"
                " when `target` is provided, or on the wrapped class."
            )

    def __init__(
        self,
        obj: Any,  # noqa: ANN401
        name: str,
        *,
        target: Any = None,  # noqa: ANN401
        args_mapping: Optional[dict[str, Optional[str]]] = None,
        args_extra: Optional[dict[str, Any]] = None,
        attrs_mapping: Optional[dict[str, Optional[str]]] = None,
        deprecated_in: str = "",
        remove_in: str = "",
        num_warns: int = 1,
        stream: Optional[Callable[..., None]] = deprecation_warning,
        message_template: Optional[str] = None,
        skip_if: Union[bool, Callable[[], bool]] = False,
        as_exception: Optional[bool] = None,
        read_only: bool = False,
        docstring_style: str = "auto",
        _misconfigured_override: bool = False,
    ) -> None:
        """Initialise the proxy with typed runtime/config dataclasses.

        ``__config`` stores private mutable runtime state in :class:`~deprecate._types._ProxyConfig` (obj, stream,
        num_warns, read_only, args_extra, warned counter).

        ``__deprecation_config__`` is the metadata interface consumed by audit tools
        (:func:`~deprecate.audit.validate_deprecation_wrapper`,
        :func:`~deprecate.audit.find_deprecation_wrappers`, etc.)
        as a :class:`~deprecate._types.DeprecationConfig` instance aligned with the ``@deprecated`` schema.
        ``__deprecated__`` is the PEP-702-conformant rendered message string.

        ``_misconfigured_override`` is a private hook used by :func:`~deprecate.deprecated` when it delegates to
        :func:`~deprecate.deprecated_class` for class targets: it lets the caller pre-compute misconfig signals
        (raw ``target=False`` plus NOTIFY+args_mapping / NOTIFY+args_extra detected upstream) before the proxy
        rewrites them away, so the final frozen :class:`~deprecate._types.DeprecationConfig` records every signal
        in one place.

        """
        # Probe ``message_template`` against every documented placeholder so typos and malformed conversion specifiers
        # fail at decoration time instead of on the first proxy access.
        _validate_message_template(message_template)
        # Reject true cycles in ``attrs_mapping`` at decoration time. Non-cyclic chains are allowed at runtime
        # and surfaced by audit as mapping chains.
        if attrs_mapping is not None:
            attr_check_obj = target if target is not None and not isinstance(target, (TargetMode, bool)) else obj
            self._validate_attrs_mapping(attrs_mapping, obj, attr_check_obj)
        # Track whether the raw ``target=False`` sentinel was passed so audit can flag it. The override
        # path lets upstream callers fold their own pre-validated misconfig signals into the same flag.
        misconfigured = target is False or _misconfigured_override
        if isinstance(target, bool):
            target = TargetMode._from_legacy_proxy(target, args_mapping=args_mapping)
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
        # Kwargs-compatibility guard: detect args_mapping keys that remap to POSITIONAL_ONLY params.
        _incompatible = _detect_proxy_positional_only(_dc_check, name, args_mapping)
        # Constructor POSITIONAL_ONLY split data: the call path forwards remapped values
        # positionally (same split machinery as the @deprecated dispatcher) instead of the
        # historical pop-and-setattr fallback, which failed for required params, immutable
        # instances, and constructor-derived state.
        _ctor_positional_only, _ctor_positional_only_order = _detect_ctor_positional_only(_dc_check)
        # Validate misconfig (NOTIFY+mapping, ARGS_REMAP+no-args_mapping, NOTIFY+args_extra). The
        # validator returns True when any signal fired so we extend ``misconfigured`` accordingly —
        # ``DeprecationConfig.misconfigured`` becomes a single source of truth for all signals.
        if isinstance(target, TargetMode):
            misconfigured |= TargetMode._validate(target, name, args_mapping=args_mapping, args_extra=args_extra)
        # Proxy-specific validation: attrs_mapping combinations not covered by _validate().
        misconfigured |= TargetMode._validate_proxy(
            target,
            name,
            attrs_mapping=attrs_mapping,
            args_mapping=args_mapping,
            args_extra=args_extra,
        )
        # Private mutable runtime state — warn counter, stream, read-only flag, wrapped object, extras to merge
        # after args_mapping at call time, optional custom warning template.
        cfg = _ProxyConfig(
            obj=obj,
            stream=stream,
            num_warns=num_warns,
            read_only=read_only,
            args_extra=args_extra,
            message_template=message_template,
            # Explicit-NOTIFY conflict: the mapping is ignored at runtime (no redirects), while the frozen
            # ``DeprecationConfig`` below keeps it so audit can still show what the caller configured.
            attrs_mapping=None if _explicit_notify_attrs else attrs_mapping,
            skip_if=skip_if,
        )
        object.__setattr__(self, "_DeprecatedProxy__config", cfg)
        # Static deprecation metadata stored as a dunder attribute — readable by audit tools via
        # __deprecation_config__; __deprecated__ carries the PEP-702-conformant rendered message string.
        dep_meta = DeprecationConfig(
            deprecated_in=deprecated_in,
            remove_in=remove_in,
            name=name,
            target=target,
            args_mapping=args_mapping,
            args_extra=args_extra,
            misconfigured=misconfigured,
            docstring_style=normalize_docstring_style(docstring_style),
            message_template=message_template,
            as_exception=as_exception,
            attrs_mapping=attrs_mapping,
            args_mapping_auto_expanded=tuple(_auto_expanded),
            args_mapping_positional_only=_incompatible,
            target_positional_only=_ctor_positional_only,
            target_positional_only_order=_ctor_positional_only_order,
        )
        object.__setattr__(self, "__deprecation_config__", dep_meta)
        object.__setattr__(self, "__deprecated__", _render_static_deprecation_message(dep_meta, name, name))
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
    ) -> Optional[str]:
        """Build the warning message for a deprecated ``attrs_mapping`` attribute access.

        Strips the ``"__attr__:"`` prefix from *arg_name* to recover the attribute name visible to
        callers.  The prefix must be preserved in the *arg_name* passed by the caller so that the
        per-attribute budget key in ``cfg.warned_args`` stays disjoint from any same-named
        ``args_mapping`` key — this method intentionally does not reassign *arg_name*.

        Returns:
            The formatted warning string, or ``None`` when *attr_name* is not registered in
            ``dep.attrs_mapping`` (caller should silently return without warning).

        """
        attr_name = arg_name[len("__attr__:") :]
        if attr_name not in attrs_mapping:
            return None
        new_attr = attrs_mapping[attr_name]
        if new_attr is not None:
            owner = self._target_display_name(target, dep.name) if not isinstance(target, TargetMode) else dep.name
            target_path = f"{owner}.{new_attr}"
            template = custom_template or TEMPLATE_WARNING_CALLABLE
            return template % {
                "source_name": attr_name,
                "source_path": attr_name,
                "deprecated_in": dep.deprecated_in,
                "remove_in": dep.remove_in,
                "target_name": new_attr,
                "target_path": target_path,
                "argument_map": "",
            }
        template = custom_template or TEMPLATE_WARNING_NO_TARGET
        return template % {
            "source_name": attr_name,
            "source_path": attr_name,
            "deprecated_in": dep.deprecated_in,
            "remove_in": dep.remove_in,
            "target_name": "",
            "target_path": "",
            "argument_map": "",
        }

    def _shall_skip(self) -> bool:
        """Evaluate the ``skip_if`` condition — ``True`` means the deprecation machinery is inactive.

        Mirrors the callable-decorator semantics: a ``bool`` is used as-is, a zero-argument callable is invoked
        and must return a strict ``bool``.

        Raises:
            TypeError: If ``skip_if`` is a callable that does not return a ``bool``.

        """
        skip_if = self._cfg.skip_if
        shall_skip = skip_if() if callable(skip_if) else bool(skip_if)
        if not isinstance(shall_skip, bool):
            raise TypeError(f"User function 'skip_if' shall return bool, but got: {type(shall_skip)}")
        return shall_skip

    def _warn(self, *, arg_name: Optional[str] = None) -> None:
        """Gate and deliver a deprecation warning in one step, for an operation that warns before it runs.

        Equivalent to :meth:`_gate` followed by :meth:`_deliver` on its decision. Operations that touch the wrapped
        object call the two halves themselves, so the operation and its warning share one ``skip_if`` evaluation.

        Args:
            arg_name: When given, use per-argument warning tracking (``cfg.warned_args``) instead of the global
                ``cfg.warned`` counter.  The warning is suppressed when the per-argument count has already reached
                ``cfg.num_warns``.  When provided alongside an ``args_mapping`` entry, the emitted message uses the
                per-argument template (`old -> new`) rather than the generic callable template — matching the
                decorator's argument-deprecation form.

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
        # Build the message before taking the lock: attrs_mapping miss returns None (no warning),
        # and message building is pure CPU work with no IO.
        msg = _build_proxy_warn_msg(self, arg_name, self._dep, cfg)
        if msg is None:
            return
        if not _consume_proxy_warn_budget(cfg, arg_name):
            return
        # Route the warning to the caller's frame rather than ``proxy.py``.  ``_emit`` decides from the stream's
        # signature whether to pass ``stacklevel``, so the stream is called exactly once and any ``TypeError`` it
        # raises propagates; the level itself is measured from ``_emit``'s frame.  A raising stream delivered
        # nothing, so the claimed slot goes back to the budget before the error propagates.
        try:
            _emit(stream, msg)
        except BaseException:
            _release_proxy_warn_budget(cfg, arg_name)
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

    def _apply_args_mapping(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Apply args_mapping to *kwargs*, renaming or dropping keys as configured.

        When both the old key and its mapped new-name key are present in *kwargs*, the explicit new-name value wins over
        the renamed old-name value.

        """
        args_mapping = self._dep.args_mapping
        if not args_mapping or not kwargs:
            return kwargs
        args_to_drop = {k for k, v in args_mapping.items() if v is None}
        explicit_new = _apply_args_mapping_collisions(
            args_mapping, kwargs, args_to_drop, self._dep.name, self._cfg.stream
        )
        result = {(args_mapping.get(k) or k): v for k, v in kwargs.items() if k not in args_to_drop}
        result.update(explicit_new)
        return result

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
        mutating_names = {
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
        return name in mutating_names

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
          intentionally not applied on ``__call__`` — :meth:`~deprecate._types.TargetMode._validate_proxy`
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
        # Callable target without args_mapping: warn globally; still merge args_extra so
        # injected defaults reach the target.
        self._warn()
        if callable(dep.target):
            return dep.target(*args, **self._merge_args_extra(kwargs))
        # NOTIFY: forward unchanged — args_extra is intentionally ignored (already warned at construction).
        if dep.target is TargetMode.NOTIFY:
            return self._get_active()(*args, **kwargs)
        # No target configured (e.g. deprecated_instance with no target): merge args_extra into the call so
        # wrappers can inject default kwargs even without a forwarding target.
        return self._get_active()(*args, **self._merge_args_extra(kwargs))

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
        new_cfg = replace(cfg, obj=copy.copy(cfg.obj), warned_args=dict(cfg.warned_args), lock=threading.Lock())
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
    # Context-manager protocol — entry warns (a use), exit is silent cleanup.  Entry, ``__anext__`` and
    # ``__await__`` have side effects on the wrapped object: ``_gate`` refuses a fatal deprecation before
    # they run. Entry warns after acquisition; async next/await warn when their awaitable/iterator is obtained,
    # before any deferred execution. Failed acquisition keeps the budget.
    # ------------------------------------------------------------------

    def __enter__(self) -> Any:  # noqa: ANN401
        """Enter the active context, unwinding it if warning delivery after acquisition fails."""
        active, live = self._gate()
        entered = active.__enter__()
        # Python will not invoke __exit__ when entry raises. Unwind the context we acquired,
        # even if the stream changed skip state, and never suppress the emission failure.
        self._deliver_or_release(live, _exit_acquired, active)
        return entered

    def __exit__(self, *exc_info: Any) -> Any:  # noqa: ANN401
        """Exit the active object's context without emitting a warning (cleanup half of the pair)."""
        return self._get_active().__exit__(*exc_info)

    async def __aenter__(self) -> Any:  # noqa: ANN401
        """Async-enter the active context, unwinding it if warning delivery after acquisition fails."""
        active, live = self._gate()
        entered = await active.__aenter__()
        try:
            self._deliver(live)
        except BaseException:
            # Entry has acquired the resource, but Python will not call __aexit__ on its failure.
            # Release the acquired context via __aexit__, then re-raise the delivery error unchanged, whatever
            # __aexit__ returns: its return value is ignored, so a suppressing context cannot swallow the error.
            await active.__aexit__(*sys.exc_info())
            raise
        return entered

    async def __aexit__(self, *exc_info: Any) -> Any:  # noqa: ANN401
        """Async-exit the active object's context without emitting a warning (cleanup half of the pair)."""
        return await self._get_active().__aexit__(*exc_info)

    def __aiter__(self) -> Any:  # noqa: ANN401
        """Return the active object's async iterator, emitting a deprecation warning once it is obtained."""
        active, live = self._gate()
        iterator = active.__aiter__()
        self._deliver(live)
        return iterator

    def __anext__(self) -> Any:  # noqa: ANN401
        """Return the active object's next awaitable, emitting a deprecation warning once it is obtained.

        If delivering the warning raises, a coroutine the wrapped ``__anext__`` created is closed unstarted before the
        error propagates (see :func:`_close_abandoned`).

        """
        active, live = self._gate()
        awaitable = active.__anext__()
        self._deliver_or_release(live, _close_abandoned, awaitable, active)
        return awaitable

    def __await__(self) -> Any:  # noqa: ANN401
        """Await the active object, emitting a deprecation warning once its await iterator is obtained.

        If delivering the warning raises, the await iterator of a coroutine the wrapped ``__await__`` created is closed
        unstarted before the error propagates (see :func:`_close_abandoned`).

        """
        active, live = self._gate()
        iterator = active.__await__()
        self._deliver_or_release(live, _close_abandoned, iterator, active)
        return iterator

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
    dep: DeprecationConfig,
    args: tuple[Any, ...],
    mapped_kwargs: dict[str, Any],
) -> Any:  # noqa: ANN401
    """Invoke *ctor*, forwarding kwargs bound to POSITIONAL_ONLY constructor params positionally.

    Adopts the ``@deprecated`` dispatcher's split approach: remapped values whose target parameter is positional-only
    are reordered into positional arguments by declaration order, after the ``consumed=len(args)`` slots the caller
    already filled positionally.  This keeps required positional-only params, immutable (frozen-dataclass-style)
    instances, and constructor-derived state working — unlike the historical pop-and-``setattr`` fallback.

    """
    if dep.target_positional_only:
        pos_args, kw_args = _split_positional_only_kwargs(
            dep.target_positional_only_order, mapped_kwargs, dep.target_positional_only, consumed=len(args)
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
        object.__setattr__(proxy, "__deprecated__", _render_static_deprecation_message(dep, dep.name, dep.name))
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
    proxy_type = _proxy_type_for(cfg.obj)
    proxy = proxy_type.__new__(proxy_type)
    object.__setattr__(proxy, "_DeprecatedProxy__config", cfg)
    object.__setattr__(proxy, "__deprecation_config__", dep)
    _restore_pep702_marker(proxy, dep, pep702_marker)
    if doc is not None:
        object.__setattr__(proxy, "__doc__", doc)
    proxy._set_ast_breadcrumbs(cfg.obj)
    return proxy


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
    mapped_kwargs = proxy._apply_args_mapping(kwargs)
    mapped_kwargs = proxy._merge_args_extra(mapped_kwargs)
    return _proxy_call_with_positional_split(cfg.obj, dep, args, mapped_kwargs)


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
    mapped_kwargs = proxy._apply_args_mapping(kwargs)
    mapped_kwargs = proxy._merge_args_extra(mapped_kwargs)
    return _proxy_call_with_positional_split(target_fn, dep, args, mapped_kwargs)


def _expand_dc_attrs_to_args(
    dc_check: Any,  # noqa: ANN401
    attrs_mapping: Optional[dict[str, Optional[str]]],
    args_mapping: Optional[dict[str, Optional[str]]],
) -> tuple[Optional[dict[str, Optional[str]]], list[str]]:
    """Auto-expand ``attrs_mapping`` entries into ``args_mapping`` for dataclass dual-surface deprecation.

    When the target (or wrapped object) is a ``@dataclass``, copies ``attrs_mapping`` entries whose redirect value names
    a dataclass ``__init__`` parameter into ``args_mapping`` so that both ``instance.old_field`` (attribute access) and
    ``DC(old_field=5)`` (constructor kwarg) emit a ``FutureWarning`` from a single ``deprecated_class()`` call.

    Returns ``(updated_args_mapping, auto_expanded_keys)``.  Returns the original ``args_mapping`` unchanged (possibly
    ``None``) when ``attrs_mapping`` is empty/``None`` or ``dc_check`` is not a dataclass.  Keys already present in
    ``args_mapping`` are never overwritten.

    """
    if not (attrs_mapping and _is_dataclass_target(dc_check)):
        return args_mapping, []
    auto_expanded: list[str] = []
    _init_param_names: set[str] = set(inspect.signature(dc_check).parameters)
    for _old_key, _mapping_val in attrs_mapping.items():
        if _mapping_val in _init_param_names:
            if args_mapping is None:
                args_mapping = {}
            if _old_key not in args_mapping:
                args_mapping[_old_key] = _mapping_val
                auto_expanded.append(_old_key)
    return args_mapping, auto_expanded


def _resolve_proxy_target(
    obj: Any,  # noqa: ANN401
    target: Any,  # noqa: ANN401
    args_mapping: Optional[dict[str, Optional[str]]],
    attrs_mapping: Optional[dict[str, Optional[str]]],
    dc_check: Any,  # noqa: ANN401
) -> "tuple[Any, Optional[dict[str, Optional[str]]], list[str], bool]":
    """Resolve the unset factory default (``None``) against the configured mappings.

    Only ``None`` — the ``deprecated_class`` default, meaning "no explicit target" — auto-resolves: a mapping present
    selects :attr:`~deprecate._types.TargetMode.ARGS_REMAP` / :attr:`~deprecate._types.TargetMode.ATTRS_REMAP` (or the
    wrapped object itself when dataclass auto-expand activated both surfaces); no mapping keeps ``None`` (a proxy that
    warns on access).  An **explicit** ``TargetMode.NOTIFY`` is a deliberate caller choice and is never overridden —
    with ``attrs_mapping`` present the pair is contradictory, so dataclass auto-expand is skipped and the flag in the
    returned tuple tells the proxy to ignore the mapping at runtime (the validators emit the misconfig ``UserWarning``).

    Returns ``(resolved_target, args_mapping, auto_expanded_keys, explicit_notify_attrs)``.

    """
    explicit_notify_attrs = target is TargetMode.NOTIFY and bool(attrs_mapping)
    # Dataclass dual-surface auto-expand: copy attrs_mapping entries that name a dataclass field
    # into args_mapping so both attribute access and constructor kwarg paths emit a FutureWarning.
    # Skipped under an explicit-NOTIFY conflict — the ignored mapping must not grow ``args_mapping``.
    auto_expanded: list[str] = []
    if not explicit_notify_attrs:
        args_mapping, auto_expanded = _expand_dc_attrs_to_args(dc_check, attrs_mapping, args_mapping)
    # When auto-expand produced a non-empty args_mapping and the user provided no explicit target,
    # set target = obj so the callable-target + both-mappings path activates both surfaces.
    if auto_expanded and target is None:
        target = obj
    # Auto-resolve: no explicit target (``None``, the factory default) but a mapping provided → the
    # matching remap mode. An explicit ``TargetMode.NOTIFY`` never auto-resolves — it is flagged instead.
    if target is None and args_mapping:
        target = TargetMode.ARGS_REMAP
    if target is None and attrs_mapping:
        target = TargetMode.ATTRS_REMAP
    return target, args_mapping, auto_expanded, explicit_notify_attrs


def _detect_ctor_positional_only(dc_check: Any) -> tuple[frozenset[str], tuple[str, ...]]:  # noqa: ANN401
    """Inspect the active class constructor for POSITIONAL_ONLY parameters.

    Proxy twin of :func:`~deprecate._dispatch._detect_positional_only`: pre-computes the split
    data consumed by :func:`_proxy_call_with_positional_split` so remapped kwargs bound to
    positional-only constructor params can be forwarded positionally at call time.

    Args:
        dc_check: The resolved active object (target class when configured, wrapped object
            otherwise).  Non-class objects yield empty results.

    Returns:
        Tuple of ``(positional_only, param_order)`` — the POSITIONAL_ONLY parameter names of the
        constructor and the full parameter-name sequence in declaration order (``self`` excluded,
        matching ``inspect.signature`` on a class).  Both empty when *dc_check* is not a class or
        its constructor declares no positional-only params.

    """
    if not isinstance(dc_check, type):
        return frozenset(), ()
    try:
        ctor_sig = inspect.signature(dc_check)
    except (TypeError, ValueError):
        return frozenset(), ()
    positional_only = frozenset(
        param_name
        for param_name, param in ctor_sig.parameters.items()
        if param.kind is inspect.Parameter.POSITIONAL_ONLY
    )
    param_order: tuple[str, ...] = tuple(ctor_sig.parameters) if positional_only else ()
    return positional_only, param_order


def _detect_proxy_positional_only(
    dc_check: Any,  # noqa: ANN401
    name: str,
    args_mapping: Optional[dict[str, Optional[str]]],
) -> tuple[str, ...]:
    """Detect ``args_mapping`` keys that remap to POSITIONAL_ONLY constructor parameters.

    Emits a ``UserWarning`` at decoration time and returns the incompatible old-key names so they can be recorded in
    :class:`~deprecate._types.DeprecationConfig` for audit surfacing. Returns an empty tuple when no incompatibilities
    are found.

    """
    if not (args_mapping and isinstance(dc_check, type)):
        return ()
    incompatible = _get_args_mapping_positional_only_keys(dc_check, args_mapping)
    if incompatible:
        warnings.warn(
            f"`args_mapping` on `{name}` remaps {list(incompatible)!r} to POSITIONAL_ONLY "
            f"constructor parameter(s) of `{dc_check.__name__}`. "
            "Calls using those deprecated names will pass the remapped values positionally "
            "to the constructor — consider removing `/` from the target signature or using "
            "`attrs_mapping` instead.",
            UserWarning,
            stacklevel=_caller_stacklevel(),
        )
    return incompatible


def _build_proxy_warn_msg(
    proxy: _DeprecatedProxy,
    arg_name: Optional[str],
    dep: DeprecationConfig,
    cfg: _ProxyConfig,
) -> Optional[str]:
    """Build the warning message string for a proxy warning emission.

    Returns ``None`` when the warning should be suppressed (``attrs_mapping`` lookup miss).

    """
    target: Any = dep.target
    args_mapping = dep.args_mapping
    custom_template = cfg.message_template

    if arg_name is not None and args_mapping and arg_name in args_mapping:
        new_arg = args_mapping[arg_name]
        argument_map = TEMPLATE_ARGUMENT_MAPPING % {"old_arg": arg_name, "new_arg": str(new_arg)}
        template = custom_template or TEMPLATE_WARNING_ARGUMENTS
        return template % {
            "source_name": dep.name,
            "source_path": dep.name,
            "deprecated_in": dep.deprecated_in,
            "remove_in": dep.remove_in,
            "target_name": str(new_arg) if new_arg is not None else "",
            "target_path": "",
            "argument_map": argument_map,
        }
    if arg_name is not None and arg_name.startswith("__attr__:") and dep.attrs_mapping is not None:
        return proxy._build_attr_warning_msg(arg_name, dep, dep.attrs_mapping, target, custom_template)
    if callable(target):
        # ``functools.partial`` and callable-instance targets carry no ``__name__``; the helper names them.
        target_name, target_path = _callable_display_name(target)
        template = custom_template or TEMPLATE_WARNING_CALLABLE
        return template % {
            "source_name": dep.name,
            "source_path": dep.name,
            "deprecated_in": dep.deprecated_in,
            "remove_in": dep.remove_in,
            "target_name": target_name,
            "target_path": target_path,
            "argument_map": "",
        }
    template = custom_template or TEMPLATE_WARNING_NO_TARGET
    return template % {
        "source_name": dep.name,
        "source_path": dep.name,
        "deprecated_in": dep.deprecated_in,
        "remove_in": dep.remove_in,
        "target_name": "",
        "target_path": "",
        "argument_map": "",
    }


#: What ``deprecated_class`` accepts for decoration: a plain class, or an already-wrapped proxy when
#: stacking (``deprecated_class(...)(deprecated_class(...)(Cls))``). ``DeprecationProxy[Any]`` is the
#: public spelling of a proxy, so annotating with it and passing it back in has to work too.
_ClassOrProxy = Union[type, "_DeprecatedProxy", DeprecationProxy[Any]]

#: Class object decorated by :func:`deprecated_class` (or the ``@deprecated`` front door): an identity signature over
#: the class object keeps ``type[Cls]`` intact, and a bare ``type`` (e.g. built with ``type(name, bases, ns)``) stays
#: ``type`` where the ``type[_T] -> type[_T]`` spelling would infer ``type[Never]``.
_ClassT = TypeVar("_ClassT", bound=type)

#: Replacement class passed as ``deprecated_class(target=...)``: the alias is statically that class.
_NewT = TypeVar("_NewT")
_NewT_co = TypeVar("_NewT_co", covariant=True)


class _ClassDecorator(Protocol):
    """Decorator returned by :func:`deprecated_class` when no replacement class is given: the source keeps its type.

    At runtime every input comes back wrapped in a :class:`_DeprecatedProxy`. For a class input the static type stays
    ``type[Cls]`` because the proxy is a truthful stand-in for it — ``isinstance`` (``__instancecheck__``), subclassing
    (``__mro_entries__``), construction, and attribute access all forward to the class — and it matches what type
    checkers (mypy, which ignores a class decorator's return type) infer for the ``@deprecated_class(...)`` decorator
    form. An already-wrapped proxy (stacking) keeps its concrete proxy type.

    """

    @overload
    def __call__(self, cls: _ClassT) -> _ClassT: ...

    @overload
    def __call__(self, cls: Union["_DeprecatedProxy", DeprecationProxy[Any]]) -> "_DeprecatedProxy": ...


class _RedirectDecorator(Protocol[_NewT_co]):
    """Decorator returned by :func:`deprecated_class` for ``target=NewCls``: the alias is typed as the target class.

    A redirecting proxy does not stand in for the class it wraps — ``isinstance``, construction, and attribute access
    all resolve against *target*, so ``deprecated_class(target=NewCls)(OldCls)`` is statically ``type[NewCls]``, never
    ``type[OldCls]`` (``OldCls`` instances are not instances of the alias).  The wrapped class is not part of the result
    type, so it may be any class — including a bare ``type`` — and need not be related to *target*.  A source that is
    already a proxy (stacking) keeps its concrete proxy type.

    """

    @overload
    def __call__(self, cls: type) -> type[_NewT_co]: ...

    @overload
    def __call__(self, cls: Union["_DeprecatedProxy", DeprecationProxy[Any]]) -> "_DeprecatedProxy": ...


# Typing fix: with a class ``target`` the proxy forwards ``isinstance``, construction and attribute access to *target*,
# not to the wrapped class, so ``deprecated_class(target=New)(Old)`` must be ``type[New]`` — not ``type[Old]``, which
# would claim ``Old`` instances satisfy the alias. Calls without a class ``target`` match the ``target: Any`` overload.
@overload
def deprecated_class(
    target: type[_NewT],
    *,
    deprecated_in: str = "",
    remove_in: str = "",
    num_warns: int = 1,
    stream: Optional[Callable[..., None]] = deprecation_warning,
    message_template: Optional[str] = None,
    args_mapping: Optional[dict[str, Optional[str]]] = None,
    args_extra: Optional[dict[str, Any]] = None,
    attrs_mapping: Optional[dict[str, Optional[str]]] = None,
    skip_if: Union[bool, Callable[[], bool]] = False,
    update_docstring: bool = False,
    docstring_style: Literal["auto", "rst", "mkdocs", "markdown"] = "auto",
    template_mgs: Optional[str] = None,
    as_exception: Optional[bool] = None,
    _misconfigured_override: bool = False,
) -> _RedirectDecorator[_NewT]: ...


@overload
def deprecated_class(
    target: Any = None,  # noqa: ANN401
    *,
    deprecated_in: str = "",
    remove_in: str = "",
    num_warns: int = 1,
    stream: Optional[Callable[..., None]] = deprecation_warning,
    message_template: Optional[str] = None,
    args_mapping: Optional[dict[str, Optional[str]]] = None,
    args_extra: Optional[dict[str, Any]] = None,
    attrs_mapping: Optional[dict[str, Optional[str]]] = None,
    skip_if: Union[bool, Callable[[], bool]] = False,
    update_docstring: bool = False,
    docstring_style: Literal["auto", "rst", "mkdocs", "markdown"] = "auto",
    template_mgs: Optional[str] = None,
    as_exception: Optional[bool] = None,
    _misconfigured_override: bool = False,
) -> _ClassDecorator:
    pass


def deprecated_class(
    target: Any = None,
    *,
    deprecated_in: str = "",
    remove_in: str = "",
    num_warns: int = 1,
    stream: Optional[Callable[..., None]] = deprecation_warning,
    message_template: Optional[str] = None,
    args_mapping: Optional[dict[str, Optional[str]]] = None,
    args_extra: Optional[dict[str, Any]] = None,
    attrs_mapping: Optional[dict[str, Optional[str]]] = None,
    skip_if: Union[bool, Callable[[], bool]] = False,
    update_docstring: bool = False,
    docstring_style: Literal["auto", "rst", "mkdocs", "markdown"] = "auto",
    template_mgs: Optional[str] = None,
    as_exception: Optional[bool] = None,
    _misconfigured_override: bool = False,
) -> Union[_ClassDecorator, _RedirectDecorator[Any]]:
    r"""Decorator factory for deprecating class definitions with optional target redirection.

    Apply ``@deprecated_class(...)`` to an Enum or dataclass to wrap the class in a
    :class:`~deprecate.proxy._DeprecatedProxy`.  All attribute, item, and call access on the resulting object will
    emit a deprecation warning and, if *target* is provided, will be forwarded to the replacement class.

    Args:
        target: Optional replacement class to redirect all access to.
        deprecated_in: Version string when the class was deprecated.
        remove_in: Version string when the class will be removed.
        num_warns: Maximum number of warnings to emit per proxy instance. ``1`` warns once; ``-1`` warns on every
            access.
        stream: Callable used to emit warnings. Defaults to :data:`~deprecate.deprecation.deprecation_warning`.
            A custom ``stream`` is called once per warning, with ``stacklevel=`` only when its signature declares a
            ``stacklevel`` parameter or ``**kwargs``; a C-level callable without an introspectable signature gets the
            message alone and loses caller attribution.
            The ``stacklevel`` passed is counted from the frame that calls ``stream``, so it points at the caller
            for :func:`warnings.warn` and a C-level :func:`functools.partial` of it; a pure-Python stream, and the
            module-level :func:`logging.warning` before Python 3.11, land one frame short (see
            ``deprecate._frames._caller_stacklevel`` for the full rule).
        message_template: Optional custom warning message template that overrides the built-in templates.  When ``None``
            (default), the built-in template for the active scenario is used (callable-target, no-target, or
            per-argument for ``args_mapping``).  Available ``%``-style placeholders:

            - ``%(source_name)s`` — the deprecated class name (taken from ``cls.__name__``)
            - ``%(deprecated_in)s`` — value of the ``deprecated_in`` argument
            - ``%(remove_in)s`` — value of the ``remove_in`` argument
            - ``%(target_name)s`` — target class name (only when *target* is callable)
            - ``%(target_path)s`` — fully-qualified target path (only when *target* is callable)
            - ``%(argument_map)s`` — formatted ``\`old\` -> \`new\``` string (only for per-argument warnings
              emitted by ``args_mapping``)

            Example: ``"v%(deprecated_in)s: ``%(source_name)s`` -> ``%(target_name)s``"``.
        args_mapping: Optional dict remapping keyword argument names when the decorated class is called.  Keys are
            old argument names; values are new names, or ``None`` to drop the argument entirely.  When provided
            without an explicit callable *target*, the mode auto-resolves to
            :attr:`~deprecate._types.TargetMode.ARGS_REMAP`: the proxy warns **only when an old argument name is
            actually used** in the call, matching the per-argument warning behaviour of
            ``@deprecated(target=TargetMode.ARGS_REMAP, args_mapping=...)``.  Passing ``args_mapping`` with no
            explicit *target* (the default) auto-resolves to ``ARGS_REMAP``; an **explicit**
            ``target=TargetMode.NOTIFY`` combined with ``args_mapping`` is contradictory — the mapping is
            ignored and a :class:`UserWarning` fires (:class:`TypeError` in v1.0).  ``target=TargetMode.ARGS_REMAP``
            without ``args_mapping`` still emits a :class:`UserWarning` at decoration time (will be
            :class:`TypeError` in v1.0).
        args_extra: Optional dict of extra keyword arguments merged into the forwarded call after ``args_mapping`` has
            been applied.  ``args_extra`` values win over any caller-supplied value with the same key (i.e.
            ``args_extra`` > explicit new-name kwarg > remapped old-name value > source defaults).  Ignored when
            ``target`` is :attr:`~deprecate._types.TargetMode.NOTIFY` (passing both emits a :class:`UserWarning` at
            decoration time; will be :class:`TypeError` in v1.0).
        attrs_mapping: Optional dict mapping deprecated attribute names to canonical names (or ``None`` for
            warn-only).  When set, only the listed attribute names emit a deprecation warning on access; all other
            attributes are forwarded silently.  The redirect applies to reads (``__getattr__``), writes
            (``__setattr__``), and deletes (``__delattr__``).  Every non-``None`` value must be an existing attribute
            on the target class when ``target`` is provided, or on the wrapped source class otherwise.
            ``None`` values keep the same attribute name but still resolve against the active class, so with
            ``target=SomeClass`` a warn-only mapping such as ``{"size": None}`` reads, writes, and deletes
            ``SomeClass.size``.
            Redirect chains such as ``{"a": "b", "b": "c"}`` are allowed at decoration time and reported by audit as
            :attr:`~deprecate.audit.ChainType.STACKED`. Cycles such as ``{"a": "b", "b": "a"}`` raise
            :class:`ValueError` at decoration time.

            Example: ``attrs_mapping={"color": "colour", "txt": "text"}`` warns on ``proxy.color`` access and
            returns ``proxy.colour``; ``proxy.colour`` is forwarded silently.

            When ``attrs_mapping`` is ``None`` (default), the existing behaviour is preserved: a warning is emitted
            on every attribute access through the proxy.

            **Callable target interaction**: when ``target=SomeClass`` is also provided, listed attribute aliases
            resolve against ``SomeClass``.  A ``None`` mapping keeps the original attribute name on ``SomeClass``.
            Unlisted attributes and calls continue to use the normal target-forwarding behaviour.

            **Validation limitation**: attribute existence is checked using :func:`inspect.getattr_static`,
            which detects class-level attributes and ``__slots__`` entries but not instance-only attributes
            set in ``__init__`` via ``self.x = ...``.  For dataclass targets without defaults, the field
            name appears in ``__dataclass_fields__`` but the attribute itself is instance-only —
            decoration-time validation will raise :class:`ValueError` even though access would succeed at
            runtime.  As a workaround, add a class-level default or use a ``None`` (warn-only) entry.

            The mode can also be set explicitly as ``target=TargetMode.ATTRS_REMAP`` — both forms are equivalent;
            the explicit form is self-documenting and enables validation at decoration time. Passing
            ``target=TargetMode.ATTRS_REMAP`` without ``attrs_mapping``, or passing an empty ``attrs_mapping={}``,
            emits a :class:`UserWarning` at decoration time (will be :class:`TypeError` in v1.0). Passing
            ``attrs_mapping`` with no explicit *target* (the default) auto-resolves
            to ``ATTRS_REMAP`` and applies the mapping.
        skip_if: Conditionally deactivate the deprecation machinery — a ``bool``, or a zero-argument ``Callable``
            returning ``bool``, evaluated at access time.  When it evaluates ``True``, the proxy transparently
            serves the wrapped source class with no warning, no ``attrs_mapping`` redirect, no
            ``args_mapping``/``args_extra`` handling, and no *target* forwarding — parity with
            ``deprecated_callable(skip_if=...)``, where a skipped call executes the source body unchanged.
            The condition may be consulted more than once per proxy operation, so keep the callable cheap and
            stable; a callable that returns a non-``bool`` raises :class:`TypeError` at access time.
        update_docstring: If ``True``, inject a deprecation notice into the class docstring at decoration time (same
            behaviour as ``@deprecated(update_docstring=True)``).
        docstring_style: Output style for the injected notice when ``update_docstring=True``.  ``"auto"`` detects the
            doc engine at decoration time; ``"rst"`` emits a ``.. deprecated::`` directive; ``"mkdocs"`` /
            ``"markdown"`` emit a ``!!! warning`` admonition.
        template_mgs: Deprecated alias for ``message_template`` (renamed in ``v0.12``; the old spelling was a
            typo).  Supplying it emits a :class:`FutureWarning` and its value is used as ``message_template``;
            supplying both raises :class:`TypeError`.  Removed in ``v1.0``.
        as_exception: Promote this deprecation from a warning to a raised
            :class:`~deprecate._fatal.DeprecatedError` — see :func:`~deprecate.routine.deprecated_callable`'s
            ``as_exception`` for the full contract. Every access that would warn raises instead (structural
            probes such as ``isinstance``, ``repr``, equality, and ``len`` stay silent, exactly as they already
            were), the warn budget does not apply, and ``stream=None`` silences only the message. ``None``
            (default) defers to the process-wide ``deprecate.AS_EXCEPTIONS`` switch.

    Returns:
        A decorator that wraps the class in a :class:`~deprecate.proxy._DeprecatedProxy`, which satisfies the public
        :class:`~deprecate._types.DeprecationProxy` Protocol at runtime.  Statically, the alias is typed as the class
        the proxy forwards to: with a class *target*, ``deprecated_class(target=NewCls, ...)(OldCls)`` is
        ``type[NewCls]`` whichever class is wrapped, so ``isinstance``/``issubclass`` checks, construction, and
        attribute access type-check against the replacement.  Without a class *target* (``args_mapping`` /
        ``attrs_mapping`` only, a :class:`~deprecate._types.TargetMode` member, or none) the alias keeps the wrapped
        class's own type, ``type[OldCls]``.  Stacking over an existing proxy keeps the concrete proxy type.  mypy
        reads the ``@deprecated_class(...)`` decorator form as the class statement itself, while pyright applies the
        decorator's return type (``type[NewCls]`` with a class *target*).  Static
        typing models the steady state: with ``skip_if`` the wrapped class is served while skipped, so it must stay
        compatible with *target*.  A ``**options`` dict of type ``dict[str, Any]`` hides whether *target* is passed,
        so the type checker cannot pick a result and infers ``Any`` — spell the keywords out to keep the precise type.

    Note:
        **Subclassing (PEP 560)**: the proxy implements ``__mro_entries__`` so
        ``class Child(deprecated_alias):`` resolves to ``class Child(target_or_source_class):``
        in the MRO — ``issubclass(Child, target_class)`` returns ``True`` and standard inheritance
        semantics are preserved.  One ``FutureWarning`` fires at class-definition time (not per
        instance).  With :attr:`~deprecate._types.TargetMode.ATTRS_REMAP` aliases (``attrs_mapping``
        without a callable *target*) and :attr:`~deprecate._types.TargetMode.ARGS_REMAP` aliases
        (``args_mapping`` without a callable *target*) subclassing stays **silent** — both modes
        scope deprecation to specific axes (attribute names or argument names), not the class name.

    Examples:
        >>> from enum import Enum
        >>> class NewColor(Enum):
        ...     RED = 1
        >>> @deprecated_class(target=NewColor, deprecated_in="1.0", remove_in="2.0", stream=None)
        ... class OldColor(Enum):
        ...     RED = 1
        >>> OldColor.RED is NewColor.RED
        True
        >>> OldColor(1) is NewColor.RED
        True

        When only argument names changed, omit *target* and supply ``args_mapping``. The proxy auto-resolves to
        :attr:`~deprecate._types.TargetMode.ARGS_REMAP` and warns **only when the old argument name is passed**:

        >>> class Config:
        ...     def __init__(self, timeout: int = 0) -> None:
        ...         self.timeout = timeout
        >>> LegacyConfig = deprecated_class(
        ...     args_mapping={"time_limit": "timeout"},
        ...     deprecated_in="1.5", remove_in="2.0", stream=None,
        ... )(Config)
        >>> LegacyConfig(timeout=30).timeout     # new name — no remap needed
        30
        >>> LegacyConfig(time_limit=30).timeout  # old name — remapped to timeout
        30

        Selective per-attribute deprecation via ``attrs_mapping``: only the listed attribute
        aliases emit a warning; other attribute accesses pass through silently.

        >>> @deprecated_class(
        ...     attrs_mapping={"color": "colour"},
        ...     deprecated_in="1.0",
        ...     remove_in="2.0",
        ...     stream=None,
        ... )
        ... class Palette:
        ...     colour = "red"
        ...     color = colour  # deprecated alias for ``colour``
        >>> Palette.colour     # canonical name — silent passthrough
        'red'
        >>> Palette.color      # deprecated alias — warns (suppressed by ``stream=None``)
        'red'

    """
    message_template = _resolve_message_template_alias(message_template, template_mgs)
    # ``TargetMode.AUTO`` is the ``@deprecated`` front-door default only — the strict form requires an
    # explicit choice (or the omitted default) so the decoration site documents its own intent.
    if target is TargetMode.AUTO:
        raise TypeError(
            "`TargetMode.AUTO` is only valid on the `@deprecated` front door, which infers the mode from "
            "the configuration. With `deprecated_class` omit `target` (a mapping auto-resolves the mode), "
            "or pass an explicit mode / replacement class."
        )

    def decorator(cls: _ClassOrProxy) -> "_DeprecatedProxy":
        # When cls is a _DeprecatedProxy (stacking case), cls.__name__ triggers __getattr__
        # which emits a spurious warning. Retrieve the name safely via the stored metadata.
        if isinstance(cls, _DeprecatedProxy):
            cls_config = get_deprecation_config(cls)
            if cls_config is None:
                raise TypeError("Cannot stack deprecated_class over a proxy without valid deprecation metadata.")
            cls_name = cls_config.name
        else:
            cls_name = cls.__name__
        if stream is not None and not deprecated_in and not message_template:
            warnings.warn(
                f"`@deprecated_class` on `{cls_name}` has no `deprecated_in` set."
                " Deprecation notices and generated documentation will omit the `deprecated_in` version."
                " Pass `deprecated_in` for a meaningful deprecation notice.",
                UserWarning,
                stacklevel=_caller_stacklevel(),
            )
        proxy = _DeprecatedProxy(
            obj=cls,
            name=cls_name,
            deprecated_in=deprecated_in,
            remove_in=remove_in,
            num_warns=num_warns,
            stream=stream,
            message_template=message_template,
            read_only=False,
            target=target,
            args_mapping=args_mapping,
            args_extra=args_extra,
            attrs_mapping=attrs_mapping,
            skip_if=skip_if,
            as_exception=as_exception,
            docstring_style=docstring_style,
            _misconfigured_override=_misconfigured_override,
        )
        if update_docstring:
            # Use a SimpleNamespace shim so _update_docstring_with_deprecation can set __doc__ normally; then store
            # the result on the proxy via object.__setattr__ (bypassing the proxy's forwarding __setattr__).
            shim = types.SimpleNamespace(
                __doc__=object.__getattribute__(proxy, "__doc__"), __deprecation_config__=proxy._dep
            )
            _update_docstring_with_deprecation(shim)
            object.__setattr__(proxy, "__doc__", shim.__doc__)
        return proxy

    # The runtime result is always a proxy; the overloads type it statically as the target class, or as the wrapped
    # class when nothing is redirected (see ``_RedirectDecorator`` / ``_ClassDecorator``).
    return cast(Union[_ClassDecorator, _RedirectDecorator[Any]], decorator)


def deprecated_instance(
    obj: Any,  # noqa: ANN401
    *,
    name: str = "",
    deprecated_in: str = "",
    remove_in: str = "",
    num_warns: int = 1,
    stream: Optional[Callable[..., None]] = deprecation_warning,
    message_template: Optional[str] = None,
    skip_if: Union[bool, Callable[[], bool]] = False,
    read_only: bool = False,
    args_extra: Optional[dict[str, Any]] = None,
    template_mgs: Optional[str] = None,
    as_exception: Optional[bool] = None,
) -> "_DeprecatedProxy":
    """Wrap any Python object with deprecation warnings.

    Returns a :class:`~deprecate.proxy._DeprecatedProxy` that transparently forwards all read access to *obj* while
    emitting a :class:`FutureWarning`.  In *read-only* mode any write attempt through the proxy raises
    :class:`AttributeError` (unless ``skip_if`` is active); writes never warn.  A proxy over a function stored as a
    class attribute binds like a method, so ``Model().scale(2)`` passes the instance and warns on the call.

    Args:
        obj: The object to deprecate (dict, list, custom object, …).
        name: Display name for *obj* used in the warning message. When omitted, the type name of *obj* is used
            (e.g. ``"dict"``).
        deprecated_in: Version string when *obj* was deprecated.
        remove_in: Version string when *obj* will be removed.
        num_warns: Maximum number of warnings to emit. ``1`` (default) warns once; ``-1`` warns on every access.
        stream: Callable used to emit warnings. Defaults to :data:`~deprecate.deprecation.deprecation_warning`
            (:class:`FutureWarning`).  Pass ``None`` to suppress warnings.  A custom ``stream`` is called once per
            warning, with ``stacklevel=`` only when its signature declares a ``stacklevel`` parameter or ``**kwargs``;
            a C-level callable without an introspectable signature gets the message alone and loses caller attribution.
            The ``stacklevel`` passed is counted from the frame that calls ``stream``, so it points at the caller
            for :func:`warnings.warn` and a C-level :func:`functools.partial` of it; a pure-Python stream, and the
            module-level :func:`logging.warning` before Python 3.11, land one frame short (see
            ``deprecate._frames._caller_stacklevel`` for the full rule).
        message_template: Optional custom warning message template that overrides the built-in templates.  When ``None``
            (default), the built-in template for the active scenario is used.  See
            :func:`~deprecate.proxy.deprecated_class` for the available ``%``-style placeholders.
        skip_if: Conditionally deactivate the deprecation machinery — a ``bool``, or a zero-argument ``Callable``
            returning ``bool``, evaluated at access time.  When it evaluates ``True``, the proxy transparently
            serves *obj* with no warning and no ``read_only`` enforcement.  The condition may be consulted more
            than once per proxy operation; a callable that returns a non-``bool`` raises :class:`TypeError` at
            access time.  For picklable proxies, use a module-level callable or a plain ``bool`` (same constraint
            as *stream*).
        read_only: If ``True``, raise :class:`AttributeError` on any write attempt through the proxy.
            Only the following standard collection mutator names are intercepted: ``append``, ``clear``,
            ``discard``, ``extend``, ``insert``, ``pop``, ``remove``, ``setdefault``, ``update``, ``add``.
            Custom method names (e.g. ``register()``, ``reload()``, ``set_value()``) are not blocked.
        args_extra: Optional dict of extra keyword arguments merged into the forwarded call when the proxy is invoked.
            ``args_extra`` values win over any caller-supplied value with the same key.
        template_mgs: Deprecated alias for ``message_template`` (renamed in ``v0.12``; the old spelling was a
            typo).  Supplying it emits a :class:`FutureWarning` and its value is used as ``message_template``;
            supplying both raises :class:`TypeError`.  Removed in ``v1.0``.
        as_exception: Promote this deprecation from a warning to a raised
            :class:`~deprecate._fatal.DeprecatedError` — see :func:`~deprecate.routine.deprecated_callable`'s
            ``as_exception`` for the full contract. Every access that would warn raises instead (structural
            probes such as ``isinstance``, ``repr``, equality, and ``len`` stay silent, exactly as they already
            were), the warn budget does not apply, and ``stream=None`` silences only the message. ``None``
            (default) defers to the process-wide ``deprecate.AS_EXCEPTIONS`` switch.

    Returns:
        A :class:`~deprecate.proxy._DeprecatedProxy` wrapping *obj*, satisfying the public
        :class:`~deprecate._types.DeprecationProxy` Protocol.  Its ``__wrapped__`` attribute is *obj* itself, and
        ``__signature__`` is *obj*'s signature — or ``None`` when *obj* has none to introspect (a plain ``dict``,
        any C-level type). Reading either emits no warning.

    Note:
        **Operator warnings**: arithmetic operators (``+``, ``-``, ``*``, ``**``, ``//``, ``%``, ``<<``,
        ``>>``, ``&``, ``|``, ``^``) and their reflected forms emit a deprecation warning and consume the
        ``num_warns`` budget.  In-place operators (``+=``, ``-=``, …) additionally enforce ``read_only``.
        Ordering comparisons (``<``, ``<=``, ``>``, ``>=``) and structural probes (``__eq__``, ``__len__``,
        ``__bool__``, ``__str__``) are **silent** — they do not consume the warn budget.

    Example:
        >>> cfg = {"threshold": 0.5, "enabled": True}
        >>> proxy = deprecated_instance(
        ...     cfg,
        ...     name="config_dict",
        ...     deprecated_in="1.0",
        ...     remove_in="2.0",
        ...     stream=None,
        ... )
        >>> proxy["threshold"]
        0.5
        >>> proxy.get("enabled")
        True

    """
    message_template = _resolve_message_template_alias(message_template, template_mgs)
    resolved_name = name or type(obj).__name__
    if stream is not None and not deprecated_in and not message_template:
        warnings.warn(
            f"`deprecated_instance()` on `{resolved_name}` has no `deprecated_in` set."
            " Deprecation notices and generated documentation will omit the `deprecated_in` version."
            " Pass `deprecated_in` for a meaningful deprecation notice.",
            UserWarning,
            stacklevel=_caller_stacklevel(),
        )
    return _proxy_type_for(obj)(
        obj=obj,
        name=resolved_name,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        num_warns=num_warns,
        stream=stream,
        message_template=message_template,
        skip_if=skip_if,
        as_exception=as_exception,
        read_only=read_only,
        args_extra=args_extra,
    )


# ----------------------------------------------------------------------
# Operator / protocol dunder forwarding (see the "Protocol forwarding" section of the
# _DeprecatedProxy docstring for the warn-policy contract).
#
# Implicit special-method lookup bypasses ``__getattr__`` — Python resolves dunders on the *type*,
# not the instance — so every operator, conversion, and protocol dunder must live on the class
# itself for the proxy to stay transparent. These are generated from a table (rather than ~60
# hand-written defs) and installed onto ``_DeprecatedProxy`` below.
# ----------------------------------------------------------------------


def _unwrap_operand(value: Any) -> Any:  # noqa: ANN401
    """Return the active object behind a proxy operand, leaving non-proxy operands untouched."""
    if isinstance(value, _DeprecatedProxy):
        return _DeprecatedProxy._get_static_attr_owner(value)
    return value


def _make_binary_forward(op_name: str, *, warn: bool) -> Callable[..., Any]:
    """Build a binary-operator dunder that forwards to the active object.

    Looks the operator up on the *type* of the active object and returns :data:`NotImplemented` when it is absent, so
    Python can try the reflected operand and ultimately raise its normal :class:`TypeError` — the proxy never masks an
    unsupported-operand error. Proxy operands are unwrapped to their active object first. A warning operator resolves
    first: one ``skip_if`` decision selects the operand and the warning, and a fatal deprecation is refused only once
    the operator has produced a result.

    """

    def _binary_dunder(self: "_DeprecatedProxy", other: Any, *extra: Any) -> Any:  # noqa: ANN401
        active, live = self._activation()
        impl = getattr(type(active), op_name, None)
        if impl is None:
            return NotImplemented
        result = impl(active, _unwrap_operand(other), *(_unwrap_operand(e) for e in extra))
        if result is NotImplemented:
            return NotImplemented
        if warn and live:
            self._refuse_fatal()
            self._deliver(live)
        return result

    _binary_dunder.__name__ = op_name
    _binary_dunder.__qualname__ = f"_DeprecatedProxy.{op_name}"
    return _binary_dunder


def _make_inplace_forward(op_name: str) -> Callable[..., Any]:
    """Build an in-place operator dunder that enforces ``read_only`` before forwarding.

    In-place operators mutate the active object, so they must honour the ``read_only=True`` contract.  This is a
    separate factory from :func:`_make_binary_forward` so that the check is not paid by the much more common
    arithmetic/reflected paths.  The mutation is a side effect, so one :meth:`~deprecate.proxy._DeprecatedProxy._gate`
    call checks ``read_only`` and refuses a fatal deprecation before the operator is looked up — a read-only proxy over
    a value without the in-place hook (an ``int``) still raises instead of letting Python fall back to the binary
    operator. The warning follows only a result other than :data:`NotImplemented` — a declined hook makes Python retry
    the binary operator, which warns on its own. The mutation has already happened when the warning is delivered, so it
    stays if that delivery raises.

    """
    read_only_op = f"In-place operation '{op_name}'"

    def _inplace_dunder(self: "_DeprecatedProxy", other: Any) -> Any:  # noqa: ANN401
        active, live = self._gate(read_only_op=read_only_op)
        impl = getattr(type(active), op_name, None)
        if impl is None:
            return NotImplemented
        result = impl(active, _unwrap_operand(other))
        if result is NotImplemented:
            return NotImplemented
        self._deliver(live)
        return result

    _inplace_dunder.__name__ = op_name
    _inplace_dunder.__qualname__ = f"_DeprecatedProxy.{op_name}"
    return _inplace_dunder


def _make_unary_forward(op_name: str, func: Callable[[Any], Any]) -> Callable[..., Any]:
    """Build a unary/conversion dunder that forwards through *func* (a builtin such as ``int`` or ``abs``).

    Delegating through the builtin preserves Python's own fallbacks (e.g. ``int()`` falling back to ``__index__``) and
    lets the natural :class:`TypeError` surface when the active object does not support the protocol.  The warning
    follows a successful call only, so a failing one keeps the warn budget; every forwarded hook is a read without a
    lasting effect, which is why ``__next__`` (it consumes an item) is hand-written to warn first instead.  Every
    forward first passes :meth:`~deprecate.proxy._DeprecatedProxy._gate`, so a fatal deprecation never runs the wrapped
    object's hook (``__index__`` or ``__fspath__`` may run user code).

    """

    def _unary_dunder(self: "_DeprecatedProxy") -> Any:  # noqa: ANN401
        active, live = self._gate()
        result = func(active)
        self._deliver(live)
        return result

    _unary_dunder.__name__ = op_name
    _unary_dunder.__qualname__ = f"_DeprecatedProxy.{op_name}"
    return _unary_dunder


#: Arithmetic, reflected, and in-place binary operators — all warn (a data-producing use).
_ARITHMETIC_OPS: tuple[str, ...] = (
    "__add__", "__sub__", "__mul__", "__matmul__", "__truediv__", "__floordiv__",
    "__mod__", "__divmod__", "__pow__", "__lshift__", "__rshift__", "__and__", "__xor__", "__or__",
)  # fmt: skip
_REFLECTED_OPS: tuple[str, ...] = (
    "__radd__", "__rsub__", "__rmul__", "__rmatmul__", "__rtruediv__", "__rfloordiv__",
    "__rmod__", "__rdivmod__", "__rpow__", "__rlshift__", "__rrshift__", "__rand__", "__rxor__", "__ror__",
)  # fmt: skip
_INPLACE_OPS: tuple[str, ...] = (
    "__iadd__", "__isub__", "__imul__", "__imatmul__", "__itruediv__", "__ifloordiv__",
    "__imod__", "__ipow__", "__ilshift__", "__irshift__", "__iand__", "__ixor__", "__ior__",
)  # fmt: skip
#: Ordering comparisons — silent (cheap probes, mirroring ``__eq__``/``__len__``).
_ORDERING_OPS: tuple[str, ...] = ("__lt__", "__le__", "__gt__", "__ge__")
#: Unary and conversion protocols forwarded through their builtin — all warn (a data-producing use).
_UNARY_OPS: dict[str, Callable[[Any], Any]] = {
    "__neg__": operator.neg,
    "__pos__": operator.pos,
    "__abs__": abs,
    "__invert__": operator.invert,
    "__int__": int,
    "__float__": float,
    "__complex__": complex,
    "__index__": operator.index,
    "__trunc__": math.trunc,
    "__floor__": math.floor,
    "__ceil__": math.ceil,
    "__reversed__": reversed,
    "__fspath__": os.fspath,
}


def _install_forwarding_dunders(cls: type) -> None:
    """Attach the generated operator/conversion dunders to *cls* at import time.

    Arithmetic and reflected binary operators are installed via :func:`_make_binary_forward`; in-place operators via
    :func:`_make_inplace_forward` (which also enforces ``read_only``); ordering comparisons via
    :func:`_make_binary_forward` (silent); unary and conversion protocols via :func:`_make_unary_forward`. Ordering
    comparisons are silent (structural probes); every other generated dunder warns (data use).

    """
    for _op in (*_ARITHMETIC_OPS, *_REFLECTED_OPS):
        setattr(cls, _op, _make_binary_forward(_op, warn=True))
    for _op in _INPLACE_OPS:
        setattr(cls, _op, _make_inplace_forward(_op))
    for _op in _ORDERING_OPS:
        setattr(cls, _op, _make_binary_forward(_op, warn=False))
    for _op, _func in _UNARY_OPS.items():
        setattr(cls, _op, _make_unary_forward(_op, _func))


_install_forwarding_dunders(_DeprecatedProxy)


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
