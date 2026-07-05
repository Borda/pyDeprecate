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
import inspect
import math
import operator
import os
import threading
import types
import warnings
from collections.abc import Iterator
from dataclasses import replace
from typing import Any, Callable, Literal, Optional, SupportsIndex, Union, cast

from deprecate._types import (
    DeprecationConfig,
    TargetMode,
    _ProxyConfig,
)
from deprecate.deprecation import (
    TEMPLATE_ARGUMENT_MAPPING,
    TEMPLATE_WARNING_ARGUMENTS,
    TEMPLATE_WARNING_CALLABLE,
    TEMPLATE_WARNING_NO_TARGET,
    _split_positional_only_kwargs,
    _validate_template_mgs,
    deprecation_warning,
)
from deprecate.docstring.inject import _update_docstring_with_deprecation, normalize_docstring_style
from deprecate.utils import _apply_args_mapping_collisions, _get_args_mapping_positional_only_keys, _is_dataclass_target

#: Stacklevel from inside ``_warn`` to the caller's frame.
#: Chain: ``caller → __getattr__/__getitem__/__iter__/__call__ → _warn → stream → warnings.warn``.
#: From ``warnings.warn`` upwards: ``1=_warn``, ``2=accessor``, ``3=caller``.
#: Helpers one level deeper (e.g. ``_proxy_call_args_remap``) pass ``_extra_frames=1``.
_DEFAULT_STACKLEVEL_TO_CALLER: int = 3


def _is_dunder(name: str) -> bool:
    """Return whether *name* is a dunder attribute name (``__x__``) — introspection machinery, not user API."""
    return name.startswith("__") and name.endswith("__")


class _DeprecatedProxy:
    """Transparent proxy that emits deprecation warnings on attribute and item access.

    Wraps any Python object and forwards all read operations (attribute lookup, subscript, iteration, calls) to the
    underlying object — or to an optional *target* replacement — while emitting a configurable :class:`FutureWarning`.

    In *read-only* mode any attempt to mutate the proxied object via ``__setitem__``, ``__delitem__``, or
    ``__setattr__`` raises :class:`AttributeError`.

    Use :func:`~deprecate.proxy.deprecated_instance` or :func:`~deprecate.proxy.deprecated_class` to create
    instances rather than instantiating this class directly.

    Args:
        obj: The deprecated object to wrap (the *source*).
        name: Display name used in the warning message.
        deprecated_in: Version string when the object was deprecated.
        remove_in: Version string when the object will be removed.
        num_warns: Maximum number of warnings to emit. ``1`` (default) warns once; ``-1`` warns on every access.
        stream: Callable used to emit warnings. Defaults to :data:`~deprecate.deprecation.deprecation_warning`
            (:class:`FutureWarning`).  Pass ``None`` to suppress warnings.  **Note:** the built-in
            stacklevel budget assumes *stream* is :func:`warnings.warn` itself or a C-level
            :func:`functools.partial` of it; a Python-defined wrapper interposes an extra frame and
            the warning will appear to originate inside :mod:`deprecate.proxy` rather than the caller.
        template_mgs: Optional custom warning message template that overrides the built-in templates.  When ``None``
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
        ``__mro_entries__``). Because implicit special-method lookup bypasses ``__getattr__``, any dunder *not* listed
        here is not forwarded; a protocol the wrapped object does not implement raises the normal :class:`TypeError`
        (binary operators return :data:`NotImplemented` so reflected operands and Python's error path are preserved).

        **Warn policy** (consistent with ``__getitem__`` vs ``__len__``): operations that use or produce the wrapped
        data warn once against the shared budget — arithmetic and its reflected/in-place forms, numeric/path
        conversions, ``round``/``trunc``/``floor``/``ceil``, ``reversed``, ``next``, context entry (``__enter__`` /
        ``__aenter__``), async iteration, and subclassing. Cheap structural probes stay silent — ordering comparisons,
        ``format``, and context exit (``__exit__`` / ``__aexit__``) — mirroring the silent ``__eq__`` / ``__len__`` /
        ``__str__``. In-place operators return the active object's own result, so a proxied value assigned via ``+=``
        rebinds to that result rather than a re-wrapped proxy.

    Known limitations:
        **Structural ABC over-claim**: because dunder methods are installed unconditionally on the *type*
        (implicit special-method lookup bypasses ``__getattr__`` and cannot be conditional per-instance),
        ``isinstance(proxy, os.PathLike)`` returns ``True`` even when the wrapped object is not a path — then
        ``os.fspath(proxy)`` raises ``TypeError``.  The same applies to ``Awaitable``, ``AsyncIterable``, and
        any other structural ABC that keys on dunder presence.  Always check the *unwrapped* object's type
        when using structural ABCs as a dispatch guard, or call ``type(deprecated_instance_proxy.__class__)``
        to get the wrapped type (which is ``_DeprecatedProxy``).

    """

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
            dep = object.__getattribute__(obj, "__deprecated__")
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
        template_mgs: Optional[str] = None,
        read_only: bool = False,
        docstring_style: str = "auto",
        _misconfigured_override: bool = False,
    ) -> None:
        """Initialise the proxy with typed runtime/config dataclasses.

        ``__config`` stores private mutable runtime state in :class:`~deprecate._types._ProxyConfig` (obj, stream,
        num_warns, read_only, args_extra, warned counter).

        ``__deprecated__`` is the public metadata interface consumed by audit tools
        (:func:`~deprecate.audit.validate_deprecation_wrapper`,
        :func:`~deprecate.audit.find_deprecation_wrappers`, etc.)
        as a :class:`~deprecate._types.DeprecationConfig` instance aligned with the ``@deprecated`` schema.

        ``_misconfigured_override`` is a private hook used by :func:`~deprecate.deprecated` when it delegates to
        :func:`~deprecate.deprecated_class` for class targets: it lets the caller pre-compute misconfig signals
        (raw ``target=False`` plus NOTIFY+args_mapping / NOTIFY+args_extra detected upstream) before the proxy
        rewrites them away, so the final frozen :class:`~deprecate._types.DeprecationConfig` records every signal
        in one place.

        """
        # Probe ``template_mgs`` against every documented placeholder so typos and malformed conversion specifiers
        # fail at decoration time instead of on the first proxy access.
        _validate_template_mgs(template_mgs)
        # Reject true cycles in ``attrs_mapping`` at decoration time. Non-cyclic chains are allowed at runtime
        # and surfaced by audit as mapping chains.
        if attrs_mapping is not None:
            attr_check_obj = target if target is not None and not isinstance(target, (TargetMode, bool)) else obj
            self._validate_attrs_mapping(attrs_mapping, obj, attr_check_obj)
        # Track whether the raw ``target=False`` sentinel was passed so audit can flag it. The override
        # path lets upstream callers fold their own pre-validated misconfig signals into the same flag.
        misconfigured = target is False or _misconfigured_override
        if isinstance(target, bool):
            target = TargetMode._from_legacy_proxy(target, args_mapping=args_mapping, stacklevel=3)
        _dc_check = _DeprecatedProxy._get_static_attr_owner(
            target if target is not None and not isinstance(target, (TargetMode, bool)) else obj
        )
        if args_mapping is not None:
            args_mapping = dict(args_mapping)
        # Copy ``attrs_mapping`` too (``args_mapping`` is copied just above): the frozen config stores it, so
        # aliasing the caller's dict would let post-decoration mutation introduce the exact cycle validation rejected.
        if attrs_mapping is not None:
            attrs_mapping = dict(attrs_mapping)
        # Dataclass dual-surface auto-expand: copy attrs_mapping entries that name a dataclass field
        # into args_mapping so both attribute access and constructor kwarg paths emit a FutureWarning.
        args_mapping, _auto_expanded = _expand_dc_attrs_to_args(_dc_check, attrs_mapping, args_mapping)
        # When auto-expand produced a non-empty args_mapping and the user provided no explicit
        # target, set target = obj so the callable-target + both-mappings path activates both surfaces.
        if _auto_expanded and target is None:
            target = obj
        # Kwargs-compatibility guard: detect args_mapping keys that remap to POSITIONAL_ONLY params.
        _incompatible = _detect_proxy_positional_only(_dc_check, name, args_mapping)
        # Constructor POSITIONAL_ONLY split data: the call path forwards remapped values
        # positionally (same split machinery as the @deprecated dispatcher) instead of the
        # historical pop-and-setattr fallback, which failed for required params, immutable
        # instances, and constructor-derived state.
        _ctor_positional_only, _ctor_positional_only_order = _detect_ctor_positional_only(_dc_check)
        # Auto-resolve: no explicit target but args_mapping provided → ARGS_REMAP
        if target is None and args_mapping:
            target = TargetMode.ARGS_REMAP
        # Auto-resolve: no explicit target but attrs_mapping provided → ATTRS_REMAP
        # Mirrors the args_mapping auto-resolve above: presence of the dict is the activation
        # signal; explicit TargetMode.ATTRS_REMAP is the equivalent self-documenting form.
        if target is None and attrs_mapping:
            target = TargetMode.ATTRS_REMAP
        # Validate misconfig (NOTIFY+args_mapping, ARGS_REMAP+no-args_mapping, NOTIFY+args_extra). The
        # validator returns True when any signal fired so we extend ``misconfigured`` accordingly —
        # ``DeprecationConfig.misconfigured`` becomes a single source of truth for all four signals.
        if isinstance(target, TargetMode):
            misconfigured |= TargetMode._validate(
                target, name, args_mapping=args_mapping, args_extra=args_extra, stacklevel=4
            )
        # Proxy-specific validation: attrs_mapping combinations not covered by _validate().
        # stacklevel=4: caller → decorator(cls) → __init__ → _validate_proxy → warn
        # deprecated_class() itself is already off the stack when decorator(cls) runs.
        misconfigured |= TargetMode._validate_proxy(
            target,
            name,
            attrs_mapping=attrs_mapping,
            args_mapping=args_mapping,
            args_extra=args_extra,
            stacklevel=4,
        )
        # Private mutable runtime state — warn counter, stream, read-only flag, wrapped object, extras to merge
        # after args_mapping at call time, optional custom warning template.
        cfg = _ProxyConfig(
            obj=obj,
            stream=stream,
            num_warns=num_warns,
            read_only=read_only,
            args_extra=args_extra,
            template_mgs=template_mgs,
            attrs_mapping=attrs_mapping,
        )
        object.__setattr__(self, "_DeprecatedProxy__config", cfg)
        # Static deprecation metadata stored as a dunder attribute — readable by audit tools via __deprecated__.
        dep_meta = DeprecationConfig(
            deprecated_in=deprecated_in,
            remove_in=remove_in,
            name=name,
            target=target,
            args_mapping=args_mapping,
            args_extra=args_extra,
            misconfigured=misconfigured,
            docstring_style=normalize_docstring_style(docstring_style),
            template_mgs=template_mgs,
            attrs_mapping=attrs_mapping,
            args_mapping_auto_expanded=tuple(_auto_expanded),
            args_mapping_positional_only=_incompatible,
            target_positional_only=_ctor_positional_only,
            target_positional_only_order=_ctor_positional_only_order,
        )
        object.__setattr__(self, "__deprecated__", dep_meta)
        # Expose the wrapped object's docstring as an instance attribute so that external tools (autodoc,
        # mkdocstrings/griffe) see the source class's documentation rather than _DeprecatedProxy's own class docstring.
        _doc = getattr(obj, "__doc__", None)
        if _doc:
            object.__setattr__(self, "__doc__", _doc)

    # ------------------------------------------------------------------
    # Internal helpers — must use object.__getattribute__ / object.__setattr__
    # to avoid triggering the proxy's own __getattr__ / __setattr__.
    # ------------------------------------------------------------------

    @property
    def _cfg(self) -> _ProxyConfig:
        """Private mutable runtime state (warn counter, stream, read-only flag, wrapped object).

        Private to this class — not part of the public API and not consumed by audit tools.

        """
        return cast(_ProxyConfig, object.__getattribute__(self, "_DeprecatedProxy__config"))

    @property
    def _dep(self) -> DeprecationConfig:
        """Static deprecation metadata (versions, name, target, args_mapping).

        Stored as ``__deprecated__`` (dunder, not name-mangled) — audit tools and external code may read it directly;
        this property simply provides a typed view of the same object.

        """
        return cast(DeprecationConfig, object.__getattribute__(self, "__deprecated__"))

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

    def _warn(self, *, arg_name: Optional[str] = None, _extra_frames: int = 0) -> None:
        """Emit a deprecation warning if the warn budget is not exhausted.

        Args:
            arg_name: When given, use per-argument warning tracking (``cfg.warned_args``) instead of the global
                ``cfg.warned`` counter.  The warning is suppressed when the per-argument count has already reached
                ``cfg.num_warns``.  When provided alongside an ``args_mapping`` entry, the emitted message uses the
                per-argument template (`old -> new`) rather than the generic callable template — matching the
                decorator's argument-deprecation form.
            _extra_frames: Additional Python helper frames between the public accessor and ``_warn``.

        """
        cfg = self._cfg
        stream = cfg.stream
        if not stream:
            return
        # Build the message before taking the lock: attrs_mapping miss returns None (no warning),
        # and message building is pure CPU work with no IO.
        dep = self._dep
        msg = _build_proxy_warn_msg(self, arg_name, dep, cfg)
        if msg is None:
            return
        # Take the state lock around quota check + counter increment so concurrent first accesses
        # cannot all read the counter as 0, all pass the gate, and each emit a warning
        # (check-then-act race).  Mirrors the decorator-path pattern in ``deprecation.py``:
        # decide-and-increment under the lock, emit the warning outside to avoid serialising
        # callers on a potentially slow ``stream`` callable.
        should_warn = False
        with cfg.lock:
            if arg_name is not None:
                arg_count = cfg.warned_args.get(arg_name, 0)
                if cfg.num_warns < 0 or arg_count < cfg.num_warns:
                    should_warn = True
                    cfg.warned_args[arg_name] = arg_count + 1
            else:
                if cfg.num_warns < 0 or cfg.warned < cfg.num_warns:
                    should_warn = True
                    cfg.warned += 1
        if not should_warn:
            return
        # Route the warning to the caller's frame rather than ``proxy.py``.
        # A plain ``try/except TypeError`` would swallow a TypeError raised *inside* a
        # stacklevel-accepting stream and re-invoke it (double side-effect).
        # Checking the exception message distinguishes a keyword-rejection error from an
        # internal one — the stream is called exactly once in all but the keyword-rejection case.
        try:
            stream(msg, stacklevel=_DEFAULT_STACKLEVEL_TO_CALLER + _extra_frames)
        except TypeError as _exc:
            if "stacklevel" in str(_exc) or "keyword" in str(_exc):
                stream(msg)
            else:
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
        """Return the active object: *target* when set, otherwise *source*."""
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
        # caller → __call__ → _proxy_call_* → _apply_args_mapping → _apply_args_mapping_collisions → warn = stacklevel 5
        explicit_new = _apply_args_mapping_collisions(
            args_mapping, kwargs, args_to_drop, self._dep.name, self._cfg.stream, stacklevel=5
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

        Without ``attrs_mapping``, the attribute is resolved *first* and the warning fires only on successful access —
        failed introspection probes (``hasattr`` duck-typing, copy/pickle machinery) raise :class:`AttributeError`
        without warning or consuming the warn budget.  Dunder reads (``__mro__``, ``__wrapped__``, …) are forwarded
        silently: they are introspection machinery, not a use of the deprecated API — the same rationale as
        :meth:`~deprecate.proxy._DeprecatedProxy.__instancecheck__`.

        In read-only mode, common mutating methods on built-in collections (for example, ``append`` or ``update``) are
        wrapped so that calling them raises :class:`AttributeError` instead of mutating the underlying object.

        """
        # Half-initialised instance guard (``cls.__new__(cls)`` during copy/pickle reconstruction): the ``_cfg``
        # property raises AttributeError when ``__config`` is missing, which Python routes back into __getattr__ —
        # touching ``self._cfg`` here again would mutually recurse (property ↔ __getattr__) into RecursionError.
        try:
            cfg = cast(_ProxyConfig, object.__getattribute__(self, "_DeprecatedProxy__config"))
        except AttributeError:
            raise AttributeError(name) from None
        attrs_mapping = cfg.attrs_mapping
        if attrs_mapping is not None:
            if name in attrs_mapping:
                self._warn(arg_name=f"__attr__:{name}")
                redirect = attrs_mapping[name]
                active = self._get_active()
                attr_name = redirect if redirect is not None else name
                if redirect is None:
                    try:
                        attr = getattr(active, attr_name)
                    except AttributeError:
                        # Attribute absent from target — fall back to source. Covers the "being-removed" pattern where
                        # the deprecated attribute still lives on the old class but was dropped from the new target.
                        attr = getattr(cfg.obj, attr_name)
                    return self._guard_read_only_mutator(attr, attr_name)
                attr = getattr(active, attr_name)
                return self._guard_read_only_mutator(attr, attr_name)
            # Not a deprecated attr — silent passthrough, no warning.
            attr = getattr(self._get_active(), name)
            return self._guard_read_only_mutator(attr, name)
        # Resolve first so a missing attribute raises without warning or burning the warn budget.
        attr = getattr(self._get_active(), name)
        # ARGS_REMAP deprecates constructor argument names only; attribute access is unrelated
        # to the deprecated call signature and must not emit a spurious global warning.
        # Dunder reads are silent machinery — see the docstring above.
        if self._dep.target is not TargetMode.ARGS_REMAP and not _is_dunder(name):
            self._warn()
        return self._guard_read_only_mutator(attr, name)

    def __setattr__(self, name: str, value: Any) -> None:  # noqa: ANN401
        """Forward attribute mutation to the active object, raising in read-only mode.

        When ``attrs_mapping`` is configured and the attribute name is a deprecated alias, emits a warning and
        redirects the write to the canonical attribute name.

        Raises:
            AttributeError: If the proxy is in read-only mode.

        """
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
        redirects the deletion to the canonical attribute name.

        Raises:
            AttributeError: If the proxy is in read-only mode.

        """
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
        """Forward subscript lookup to the active object, emitting a deprecation warning."""
        self._warn()
        return self._get_active()[key]

    def __setitem__(self, key: Any, value: Any) -> None:  # noqa: ANN401
        """Forward subscript mutation to the active object, raising in read-only mode.

        Raises:
            AttributeError: If the proxy is in read-only mode.

        """
        self._check_read_only(f"Setting item '{key}'")
        self._get_active()[key] = value

    def __delitem__(self, key: Any) -> None:  # noqa: ANN401
        """Forward subscript deletion to the active object, raising in read-only mode.

        Raises:
            AttributeError: If the proxy is in read-only mode.

        """
        self._check_read_only(f"Deleting item '{key}'")
        del self._get_active()[key]

    # ------------------------------------------------------------------
    # Container protocol
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        """Return length of the active object without emitting a warning."""
        return len(self._get_active())

    def __iter__(self) -> Iterator[Any]:
        """Iterate over the active object, emitting a deprecation warning."""
        self._warn()
        return iter(self._get_active())

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
        dep = object.__getattribute__(self, "__deprecated__")
        cfg = object.__getattribute__(self, "_DeprecatedProxy__config")

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
    # ------------------------------------------------------------------

    def __copy__(self) -> "_DeprecatedProxy":
        """Return a new proxy wrapping a shallow copy of the wrapped object.

        Deprecation metadata (frozen, immutable) is shared; the mutable warn-budget counters are snapshotted into an
        independent config so the copy warns independently from the original.

        """
        cfg = self._cfg
        new_cfg = replace(cfg, obj=copy.copy(cfg.obj), warned_args=dict(cfg.warned_args), lock=threading.Lock())
        return _reconstruct_proxy(new_cfg, self._dep, object.__getattribute__(self, "__dict__").get("__doc__"))

    def __deepcopy__(self, memo: dict[int, Any]) -> "_DeprecatedProxy":
        """Return a new proxy deep-copying the wrapped object (and any instance target).

        Registers the new proxy in *memo* before descending so self-referential structures that contain the proxy deep-
        copy without infinite recursion.

        """
        cls = type(self)
        new = cls.__new__(cls)
        memo[id(self)] = new
        object.__setattr__(new, "_DeprecatedProxy__config", copy.deepcopy(self._cfg, memo))
        object.__setattr__(new, "__deprecated__", copy.deepcopy(self._dep, memo))
        doc = object.__getattribute__(self, "__dict__").get("__doc__")
        if doc is not None:
            object.__setattr__(new, "__doc__", doc)
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
        return (_reconstruct_proxy, (self._cfg, self._dep, doc))

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
    # Context-manager protocol — entry warns (a use), exit is silent cleanup
    # ------------------------------------------------------------------

    def __enter__(self) -> Any:  # noqa: ANN401
        """Enter the active object's context, emitting a deprecation warning."""
        self._warn()
        return self._get_active().__enter__()

    def __exit__(self, *exc_info: Any) -> Any:  # noqa: ANN401
        """Exit the active object's context without emitting a warning (cleanup half of the pair)."""
        return self._get_active().__exit__(*exc_info)

    async def __aenter__(self) -> Any:  # noqa: ANN401
        """Async-enter the active object's context, emitting a deprecation warning."""
        self._warn()
        return await self._get_active().__aenter__()

    async def __aexit__(self, *exc_info: Any) -> Any:  # noqa: ANN401
        """Async-exit the active object's context without emitting a warning (cleanup half of the pair)."""
        return await self._get_active().__aexit__(*exc_info)

    def __aiter__(self) -> Any:  # noqa: ANN401
        """Return the active object's async iterator, emitting a deprecation warning."""
        self._warn()
        return self._get_active().__aiter__()

    def __anext__(self) -> Any:  # noqa: ANN401
        """Return the active object's next awaitable, emitting a deprecation warning."""
        self._warn()
        return self._get_active().__anext__()

    def __await__(self) -> Any:  # noqa: ANN401
        """Await the active object, emitting a deprecation warning."""
        self._warn()
        return self._get_active().__await__()

    # ------------------------------------------------------------------
    # Numeric/string dunders with non-uniform signatures — hand-written
    # ------------------------------------------------------------------

    def __round__(self, ndigits: Any = None) -> Any:  # noqa: ANN401
        """Round the active object, emitting a deprecation warning (numeric read)."""
        self._warn()
        active = self._get_active()
        return round(active) if ndigits is None else round(active, ndigits)

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


def _reconstruct_proxy(
    cfg: _ProxyConfig,
    dep: DeprecationConfig,
    doc: Optional[str],
) -> "_DeprecatedProxy":
    """Rebuild a :class:`_DeprecatedProxy` from its config dataclasses without re-running ``__init__``.

    Used by ``__copy__`` and as the pickle reconstructor in ``__reduce_ex__``: re-running
    ``__init__`` would repeat decoration-time validation and misconfiguration warnings, so the
    state is re-attached directly via ``object.__setattr__`` (bypassing the proxy's forwarding
    ``__setattr__``).

    Args:
        cfg: Private mutable runtime state for the new proxy.
        dep: Frozen deprecation metadata (shared or copied by the caller as appropriate).
        doc: Instance-level ``__doc__`` mirrored from the wrapped object, or ``None`` when the
            original proxy carried no instance docstring.

    """
    proxy = _DeprecatedProxy.__new__(_DeprecatedProxy)
    object.__setattr__(proxy, "_DeprecatedProxy__config", cfg)
    object.__setattr__(proxy, "__deprecated__", dep)
    if doc is not None:
        object.__setattr__(proxy, "__doc__", doc)
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
            proxy._warn(arg_name=old_key, _extra_frames=1)
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
            proxy._warn(arg_name=old_key, _extra_frames=1)
    if not any(old_key in kwargs for old_key in mapping):
        proxy._warn(_extra_frames=1)
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


def _detect_ctor_positional_only(dc_check: Any) -> tuple[frozenset[str], tuple[str, ...]]:  # noqa: ANN401
    """Inspect the active class constructor for POSITIONAL_ONLY parameters.

    Proxy twin of :func:`~deprecate.deprecation._detect_positional_only`: pre-computes the split
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
            stacklevel=5,  # caller → decorator(cls) → __init__ → _detect_proxy_positional_only → warn
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
    custom_template = cfg.template_mgs

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
        target_name = target.__name__
        target_path = f"{target.__module__}.{target_name}"
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


def deprecated_class(
    target: Any = None,  # noqa: ANN401
    *,
    deprecated_in: str = "",
    remove_in: str = "",
    num_warns: int = 1,
    stream: Optional[Callable[..., None]] = deprecation_warning,
    template_mgs: Optional[str] = None,
    args_mapping: Optional[dict[str, Optional[str]]] = None,
    args_extra: Optional[dict[str, Any]] = None,
    attrs_mapping: Optional[dict[str, Optional[str]]] = None,
    update_docstring: bool = False,
    docstring_style: Literal["auto", "rst", "mkdocs", "markdown"] = "auto",
    _misconfigured_override: bool = False,
) -> Callable[[Union[type, "_DeprecatedProxy"]], "_DeprecatedProxy"]:
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
        template_mgs: Optional custom warning message template that overrides the built-in templates.  When ``None``
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
            ``@deprecated(target=TargetMode.ARGS_REMAP, args_mapping=...)``.  Passing ``args_mapping`` together with
            ``target=TargetMode.NOTIFY`` is a misconfiguration and emits a :class:`UserWarning` at decoration time
            (will be :class:`TypeError` in v1.0).  Similarly, ``target=TargetMode.ARGS_REMAP`` without
            ``args_mapping`` emits a :class:`UserWarning` at decoration time.
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
            ``attrs_mapping`` together with ``target=TargetMode.NOTIFY`` is also a misconfiguration and emits a
            :class:`UserWarning` at decoration time.
        update_docstring: If ``True``, inject a deprecation notice into the class docstring at decoration time (same
            behaviour as ``@deprecated(update_docstring=True)``).
        docstring_style: Output style for the injected notice when ``update_docstring=True``.  ``"auto"`` detects the
            doc engine at decoration time; ``"rst"`` emits a ``.. deprecated::`` directive; ``"mkdocs"`` /
            ``"markdown"`` emit a ``!!! warning`` admonition.

    Returns:
        A decorator that wraps the class in a :class:`~deprecate.proxy._DeprecatedProxy`.

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

    def decorator(cls: Union[type, "_DeprecatedProxy"]) -> "_DeprecatedProxy":
        # When cls is a _DeprecatedProxy (stacking case), cls.__name__ triggers __getattr__
        # which emits a spurious warning. Retrieve the name safely via the stored metadata.
        cls_name = (
            object.__getattribute__(cls, "__deprecated__").name if isinstance(cls, _DeprecatedProxy) else cls.__name__
        )
        if stream is not None and not deprecated_in and not template_mgs:
            warnings.warn(
                f"`@deprecated_class` on `{cls_name}` has no `deprecated_in` set."
                " Deprecation notices and generated documentation will omit the `deprecated_in` version."
                " Pass `deprecated_in` for a meaningful deprecation notice.",
                UserWarning,
                stacklevel=2,
            )
        proxy = _DeprecatedProxy(
            obj=cls,
            name=cls_name,
            deprecated_in=deprecated_in,
            remove_in=remove_in,
            num_warns=num_warns,
            stream=stream,
            template_mgs=template_mgs,
            read_only=False,
            target=target,
            args_mapping=args_mapping,
            args_extra=args_extra,
            attrs_mapping=attrs_mapping,
            docstring_style=docstring_style,
            _misconfigured_override=_misconfigured_override,
        )
        if update_docstring:
            # Use a SimpleNamespace shim so _update_docstring_with_deprecation can set __doc__ normally; then store
            # the result on the proxy via object.__setattr__ (bypassing the proxy's forwarding __setattr__).
            shim = types.SimpleNamespace(__doc__=object.__getattribute__(proxy, "__doc__"), __deprecated__=proxy._dep)
            _update_docstring_with_deprecation(shim)
            object.__setattr__(proxy, "__doc__", shim.__doc__)
        return proxy

    return decorator


def deprecated_instance(
    obj: Any,  # noqa: ANN401
    *,
    name: str = "",
    deprecated_in: str = "",
    remove_in: str = "",
    num_warns: int = 1,
    stream: Optional[Callable[..., None]] = deprecation_warning,
    template_mgs: Optional[str] = None,
    read_only: bool = False,
    args_extra: Optional[dict[str, Any]] = None,
) -> "_DeprecatedProxy":
    """Wrap any Python object with deprecation warnings.

    Returns a :class:`~deprecate.proxy._DeprecatedProxy` that transparently forwards all read access to *obj* while
    emitting a :class:`FutureWarning`.  In *read-only* mode any write attempt through the proxy raises
    :class:`AttributeError`.

    Args:
        obj: The object to deprecate (dict, list, custom object, …).
        name: Display name for *obj* used in the warning message. When omitted, the type name of *obj* is used
            (e.g. ``"dict"``).
        deprecated_in: Version string when *obj* was deprecated.
        remove_in: Version string when *obj* will be removed.
        num_warns: Maximum number of warnings to emit. ``1`` (default) warns once; ``-1`` warns on every access.
        stream: Callable used to emit warnings. Defaults to :data:`~deprecate.deprecation.deprecation_warning`
            (:class:`FutureWarning`).  Pass ``None`` to suppress warnings.
        template_mgs: Optional custom warning message template that overrides the built-in templates.  When ``None``
            (default), the built-in template for the active scenario is used.  See
            :func:`~deprecate.proxy.deprecated_class` for the available ``%``-style placeholders.
        read_only: If ``True``, raise :class:`AttributeError` on any write attempt through the proxy.
            Only the following standard collection mutator names are intercepted: ``append``, ``clear``,
            ``discard``, ``extend``, ``insert``, ``pop``, ``remove``, ``setdefault``, ``update``, ``add``.
            Custom method names (e.g. ``register()``, ``reload()``, ``set_value()``) are not blocked.
        args_extra: Optional dict of extra keyword arguments merged into the forwarded call when the proxy is invoked.
            ``args_extra`` values win over any caller-supplied value with the same key.

    Returns:
        A :class:`~deprecate.proxy._DeprecatedProxy` wrapping *obj*.

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
    resolved_name = name or type(obj).__name__
    if stream is not None and not deprecated_in and not template_mgs:
        warnings.warn(
            f"`deprecated_instance()` on `{resolved_name}` has no `deprecated_in` set."
            " Deprecation notices and generated documentation will omit the `deprecated_in` version."
            " Pass `deprecated_in` for a meaningful deprecation notice.",
            UserWarning,
            stacklevel=2,
        )
    return _DeprecatedProxy(
        obj=obj,
        name=resolved_name,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        num_warns=num_warns,
        stream=stream,
        template_mgs=template_mgs,
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
    unsupported-operand error. Proxy operands are unwrapped to their active object first.

    """

    def _binary_dunder(self: "_DeprecatedProxy", other: Any, *extra: Any) -> Any:  # noqa: ANN401
        active = self._get_active()
        impl = getattr(type(active), op_name, None)
        if impl is None:
            return NotImplemented
        result = impl(active, _unwrap_operand(other), *(_unwrap_operand(e) for e in extra))
        if result is NotImplemented:
            return NotImplemented
        if warn:
            self._warn()
        return result

    _binary_dunder.__name__ = op_name
    _binary_dunder.__qualname__ = f"_DeprecatedProxy.{op_name}"
    return _binary_dunder


def _make_inplace_forward(op_name: str) -> Callable[..., Any]:
    """Build an in-place operator dunder that enforces ``read_only`` before forwarding.

    In-place operators mutate the active object, so they must honour the ``read_only=True`` contract.  This is a
    separate factory from :func:`_make_binary_forward` so that the check is not paid by the much more common
    arithmetic/reflected paths.

    """

    def _inplace_dunder(self: "_DeprecatedProxy", other: Any) -> Any:  # noqa: ANN401
        self._check_read_only(f"In-place operation '{op_name}'")
        active = self._get_active()
        impl = getattr(type(active), op_name, None)
        if impl is None:
            return NotImplemented
        self._warn()
        return impl(active, _unwrap_operand(other))

    _inplace_dunder.__name__ = op_name
    _inplace_dunder.__qualname__ = f"_DeprecatedProxy.{op_name}"
    return _inplace_dunder


def _make_unary_forward(op_name: str, func: Callable[[Any], Any]) -> Callable[..., Any]:
    """Build a unary/conversion dunder that forwards through *func* (a builtin such as ``int`` or ``abs``).

    Delegating through the builtin preserves Python's own fallbacks (e.g. ``int()`` falling back to ``__index__``) and
    lets the natural :class:`TypeError` surface when the active object does not support the protocol.

    """

    def _unary_dunder(self: "_DeprecatedProxy") -> Any:  # noqa: ANN401
        self._warn()
        return func(self._get_active())

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
    "__next__": next,
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
