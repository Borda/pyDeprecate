"""Proxy utilities for deprecating Python object instances and classes.

Provides :func:`~deprecate.proxy.deprecated_instance` for wrapping any Python object with
transparent deprecation warnings and optional read-only enforcement, and
:func:`~deprecate.proxy.deprecated_class` as a class-level decorator with optional target
redirection. Automatic class docstring injection preserves ``None`` when the wrapped class has no docstring.

The public factories and typing contracts live here. Runtime and copy/pickle support live in
:mod:`deprecate.proxy.core`, target and attribute validation in :mod:`deprecate.proxy.resolve`, and protocol
forwarding in :mod:`deprecate.proxy.dunders`. The proxy classes and the pickle reconstruction helpers keep
``deprecate.proxy`` as their module and stay importable from it.

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

import types
import warnings
from typing import (
    Any,
    Callable,
    Literal,
    Optional,
    Protocol,
    TypeVar,
    Union,
    cast,
    overload,
)

from deprecate._frames import _caller_stacklevel
from deprecate._types import (
    DeprecationProxy,
    TargetMode,
    get_deprecation_config,
)
from deprecate.docstring.inject import _update_docstring_with_deprecation
from deprecate.messaging import (
    _resolve_message_template_alias,
    deprecation_warning,
)
from deprecate.proxy import dunders  # noqa: F401 # imported for its side effect: installs the forwarders
from deprecate.proxy.core import (
    _BindingDeprecatedProxy,  # noqa: F401 # re-exported: its module is deprecate.proxy (pickle by reference)
    _DeprecatedProxy,
    _proxy_call_global,  # noqa: F401 # re-exported: imported by tests
    _proxy_type_for,
    _ProxySpec,
    _reconstruct_proxy,  # noqa: F401 # re-exported: pickle path of 0.14 payloads
    _restore_pep702_marker,  # noqa: F401 # re-exported: pickle path
)

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
    ``type[Cls]`` because the proxy is a truthful stand-in for it — ``isinstance`` (``__instancecheck__``),
    subclassing (``__mro_entries__``), construction, and attribute access all forward to the class — and it matches
    what type checkers (mypy, which ignores a class decorator's return type) infer for the ``@deprecated_class(...)``
    decorator form. An already-wrapped proxy (stacking) keeps its concrete proxy type.

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
    type, so it may be any class — including a bare ``type`` — and need not be related to *target*.  A source that
    is already a proxy (stacking) keeps its concrete proxy type.

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
            behaviour as ``@deprecated(update_docstring=True)``). A class with no docstring keeps ``__doc__ = None``.
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
            spec=_ProxySpec(
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
            ),
        )
        if update_docstring:
            # Use a SimpleNamespace shim so _update_docstring_with_deprecation can set __doc__ normally; then store
            # the result on the proxy via object.__setattr__ (bypassing the proxy's forwarding __setattr__).
            # Read only instance metadata so a docless source cannot inherit the internal proxy class docstring.
            shim = types.SimpleNamespace(
                __doc__=object.__getattribute__(proxy, "__dict__").get("__doc__"), __deprecation_config__=proxy._dep
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
        spec=_ProxySpec(
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
        ),
    )
