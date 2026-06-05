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

import types
import warnings
from collections.abc import Iterator
from typing import Any, Callable, Literal, Optional, cast

from deprecate._types import DeprecationConfig, TargetMode, _ProxyConfig
from deprecate.deprecation import (
    TEMPLATE_ARGUMENT_MAPPING,
    TEMPLATE_WARNING_ARGUMENTS,
    TEMPLATE_WARNING_CALLABLE,
    TEMPLATE_WARNING_NO_TARGET,
    _validate_template_mgs,
    deprecation_warning,
)
from deprecate.docstring.inject import _update_docstring_with_deprecation, normalize_docstring_style

#: Stacklevel from inside ``_warn`` to the caller's frame.
#: Chain: ``caller → __getattr__/__getitem__/__iter__/__call__ → _warn → stream → warnings.warn``.
#: From ``warnings.warn`` upwards: ``1=_warn``, ``2=accessor`` (e.g. ``__getattr__``), ``3=caller``.
_DEFAULT_STACKLEVEL_TO_CALLER: int = 3


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

    """

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
        # Probe ``template_mgs`` against every documented placeholder so typos and malformed
        # conversion specifiers fail at decoration time instead of on the first proxy access.
        _validate_template_mgs(template_mgs)
        # Reject true cycle redirects in ``attrs_mapping`` at decoration time.  A cycle is when
        # following the redirect chain eventually loops back to a previously-visited key
        # (e.g. {"a": "b", "b": "a"}).  Multi-stage rename chains like {"a": "b", "b": "c"} are
        # valid: they terminate at "c" which is not a key in the mapping, so no loop occurs.
        if attrs_mapping is not None:
            seen_cycle_starters: list[str] = []
            for start in attrs_mapping:
                visited: set[str] = {start}
                current = attrs_mapping[start]
                while current is not None and current in attrs_mapping:
                    if current in visited:
                        seen_cycle_starters.append(start)
                        break
                    visited.add(current)
                    current = attrs_mapping[current]
            if seen_cycle_starters:
                raise ValueError(
                    f"`attrs_mapping` has circular redirects — the redirect chain starting from"
                    f" {sorted(set(seen_cycle_starters))} loops back to a previously visited key."
                    " Redirect chains must terminate at an attribute name not present as a key in `attrs_mapping`."
                )
        # Track whether the raw ``target=False`` sentinel was passed so audit can flag it. The override
        # path lets upstream callers fold their own pre-validated misconfig signals into the same flag.
        misconfigured = target is False or _misconfigured_override
        if isinstance(target, bool):
            target = TargetMode._from_legacy_proxy(target, args_mapping=args_mapping, stacklevel=3)
        # Auto-resolve: no explicit target but args_mapping provided → ARGS_REMAP
        if target is None and args_mapping:
            target = TargetMode.ARGS_REMAP
        # Validate misconfig (NOTIFY+args_mapping, ARGS_REMAP+no-args_mapping, NOTIFY+args_extra). The
        # validator returns True when any signal fired so we extend ``misconfigured`` accordingly —
        # ``DeprecationConfig.misconfigured`` becomes a single source of truth for all four signals.
        if isinstance(target, TargetMode):
            misconfigured |= TargetMode._validate(
                target, name, args_mapping=args_mapping, args_extra=args_extra, stacklevel=4
            )
        # Private mutable runtime state — warn counter, stream, read-only flag, wrapped object,
        # extras to merge after args_mapping at call time, optional custom warning template.
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
        )
        object.__setattr__(self, "__deprecated__", dep_meta)
        # Expose the wrapped object's docstring as an instance attribute so
        # that external tools (autodoc, mkdocstrings/griffe) see the source
        # class's documentation rather than _DeprecatedProxy's own class docstring.
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

    def _warn(self, *, arg_name: Optional[str] = None) -> None:
        """Emit a deprecation warning if the warn budget is not exhausted.

        Args:
            arg_name: When given, use per-argument warning tracking (``cfg.warned_args``) instead of the global
                ``cfg.warned`` counter.  The warning is suppressed when the per-argument count has already reached
                ``cfg.num_warns``.  When provided alongside an ``args_mapping`` entry, the emitted message uses the
                per-argument template (`old -> new`) rather than the generic callable template — matching the
                decorator's argument-deprecation form.

        """
        cfg = self._cfg
        stream = cfg.stream
        if not stream:
            return
        if arg_name is not None:
            # Per-argument warning budget
            arg_count = cfg.warned_args.get(arg_name, 0)
            if cfg.num_warns >= 0 and arg_count >= cfg.num_warns:
                return
        else:
            # Global warning budget
            if cfg.num_warns >= 0 and cfg.warned >= cfg.num_warns:
                return
        dep = self._dep
        target: Any = dep.target
        args_mapping = dep.args_mapping
        # ``cfg.template_mgs`` (when set) overrides the built-in template for every
        # branch.  Fallback semantics mirror the decorator's ``_raise_warn_callable``
        # and ``_raise_warn_arguments``: the built-in template appropriate for the
        # active scenario is used when no override is configured.
        custom_template = cfg.template_mgs
        # Per-argument warning: use the same template the decorator emits for
        # `args_mapping` deprecations so callers see `old -> new` rather than a
        # generic class-deprecation message.
        if arg_name is not None and args_mapping and arg_name in args_mapping:
            new_arg = args_mapping[arg_name]
            argument_map = TEMPLATE_ARGUMENT_MAPPING % {"old_arg": arg_name, "new_arg": str(new_arg)}
            template = custom_template or TEMPLATE_WARNING_ARGUMENTS
            msg = template % {
                "source_name": dep.name,
                "deprecated_in": dep.deprecated_in,
                "remove_in": dep.remove_in,
                "argument_map": argument_map,
            }
        elif arg_name is not None and dep.attrs_mapping is not None and arg_name in dep.attrs_mapping:
            # Per-attribute warning for ``attrs_mapping``: format the message so callers see the
            # deprecated attribute name and (when a non-``None`` redirect is configured) the canonical
            # replacement attribute path on the wrapped class.
            new_attr = dep.attrs_mapping[arg_name]
            if new_attr is not None:
                target_path = f"{dep.name}.{new_attr}"
                template = custom_template or TEMPLATE_WARNING_CALLABLE
                msg = template % {
                    "source_name": arg_name,
                    "deprecated_in": dep.deprecated_in,
                    "remove_in": dep.remove_in,
                    "target_name": new_attr,
                    "target_path": target_path,
                }
            else:
                template = custom_template or TEMPLATE_WARNING_NO_TARGET
                msg = template % {
                    "source_name": arg_name,
                    "deprecated_in": dep.deprecated_in,
                    "remove_in": dep.remove_in,
                }
        elif callable(target):
            target_name = target.__name__
            target_path = f"{target.__module__}.{target_name}"
            template = custom_template or TEMPLATE_WARNING_CALLABLE
            msg = template % {
                "source_name": dep.name,
                "deprecated_in": dep.deprecated_in,
                "remove_in": dep.remove_in,
                "target_name": target_name,
                "target_path": target_path,
            }
        else:
            template = custom_template or TEMPLATE_WARNING_NO_TARGET
            msg = template % {
                "source_name": dep.name,
                "deprecated_in": dep.deprecated_in,
                "remove_in": dep.remove_in,
            }
        # Route the warning to the caller's frame rather than ``proxy.py``.  Mirrors the
        # ``_raise_warn`` fallback in ``deprecation.py``: when ``stream`` does not accept a
        # ``stacklevel`` kwarg (e.g. ``print``, custom callables), fall back to a positional call.
        try:
            stream(msg, stacklevel=_DEFAULT_STACKLEVEL_TO_CALLER)
        except TypeError:
            stream(msg)
        if arg_name is not None:
            cfg.warned_args[arg_name] = cfg.warned_args.get(arg_name, 0) + 1
        else:
            cfg.warned += 1

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
        """Apply args_mapping to *kwargs*, renaming or dropping keys as configured."""
        args_mapping = self._dep.args_mapping
        if not args_mapping or not kwargs:
            return kwargs
        args_to_drop = {k for k, v in args_mapping.items() if v is None}
        return {(args_mapping.get(k) or k): v for k, v in kwargs.items() if k not in args_to_drop}

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

    # ------------------------------------------------------------------
    # Attribute access
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        """Forward attribute lookup to the active object, emitting a deprecation warning.

        When ``attrs_mapping`` is configured, only attributes listed in the mapping emit a warning; all other accesses
        are forwarded silently without a warning.  The redirect target name (value in the mapping) is used for the
        actual attribute lookup when the value is a non-``None`` string; ``None`` means warn-only with no rename.

        In read-only mode, common mutating methods on built-in collections (for example, ``append`` or ``update``) are
        wrapped so that calling them raises :class:`AttributeError` instead of mutating the underlying object.

        """
        attrs_mapping = self._cfg.attrs_mapping
        if attrs_mapping is not None:
            if name in attrs_mapping:
                self._warn(arg_name=name)
                redirect = attrs_mapping[name]
                active = self._get_active()
                return getattr(active, redirect if redirect is not None else name)
            # Not a deprecated attr — silent passthrough, no warning.
            return getattr(self._get_active(), name)
        self._warn()
        attr = getattr(self._get_active(), name)
        # In read-only mode, guard common mutating methods accessed via attribute lookup.
        if self._cfg.read_only and callable(attr) and self._is_potential_mutator(name):

            def _guarded_mutator(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
                self._check_read_only(f"Calling mutating method '{name}'")

            return _guarded_mutator
        return attr

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
            self._warn(arg_name=name)
            redirect = attrs_mapping[name]
            setattr(self._get_active(), redirect if redirect is not None else name, value)
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
            self._warn(arg_name=name)
            redirect = attrs_mapping[name]
            delattr(self._get_active(), redirect if redirect is not None else name)
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
        - :attr:`~deprecate._types.TargetMode.NOTIFY`: always warn (global budget) and forward kwargs unchanged;
          ``args_extra`` is intentionally ignored (misconfig).

        """
        dep = object.__getattribute__(self, "__deprecated__")
        cfg = object.__getattribute__(self, "_DeprecatedProxy__config")

        if dep.target is TargetMode.ARGS_REMAP:
            mapping = dep.args_mapping or {}
            for old_key in mapping:
                if old_key in kwargs:
                    self._warn(arg_name=old_key)
            mapped_kwargs = self._apply_args_mapping(kwargs)
            mapped_kwargs = self._merge_args_extra(mapped_kwargs)
            return cfg.obj(*args, **mapped_kwargs)
        if callable(dep.target) and dep.args_mapping:
            mapping = dep.args_mapping or {}
            for old_key in mapping:
                if old_key in kwargs:
                    self._warn(arg_name=old_key)
            if not any(old_key in kwargs for old_key in mapping):
                # No old-style args — still warn because the class itself is deprecated
                self._warn()
            mapped_kwargs = self._apply_args_mapping(kwargs)
            mapped_kwargs = self._merge_args_extra(mapped_kwargs)
            return dep.target(*args, **mapped_kwargs)
        # Callable target without args_mapping: warn globally; still merge args_extra so
        # injected defaults reach the target.
        self._warn()
        if callable(dep.target):
            return dep.target(*args, **self._merge_args_extra(kwargs))
        # NOTIFY: forward unchanged — args_extra is intentionally ignored (already warned at construction).
        if dep.target is TargetMode.NOTIFY:
            return self._get_active()(*args, **kwargs)
        # No target configured (e.g. deprecated_instance with no target): merge args_extra
        # into the call so wrappers can inject default kwargs even without a forwarding target.
        return self._get_active()(*args, **self._merge_args_extra(kwargs))

    # ------------------------------------------------------------------
    # Comparison and hashing
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        """Compare the source object for equality."""
        obj = self._cfg.obj
        if isinstance(other, _DeprecatedProxy):
            other = other._cfg.obj
        return bool(obj == other)

    def __ne__(self, other: object) -> bool:
        """Return the inverse of equality."""
        return not self.__eq__(other)

    def __hash__(self) -> int:
        """Return the hash of the source object."""
        return hash(self._cfg.obj)

    # ------------------------------------------------------------------
    # String representations — no warning emitted (used for debugging)
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        """Return repr of the source object."""
        return repr(self._cfg.obj)

    def __str__(self) -> str:
        """Return str of the source object."""
        return str(self._cfg.obj)

    def __bool__(self) -> bool:
        """Return bool of the active object without emitting a warning."""
        return bool(self._get_active())

    # ------------------------------------------------------------------
    # Type protocol — supports isinstance/issubclass against a proxy
    # ------------------------------------------------------------------

    def __instancecheck__(self, instance: object) -> bool:
        """Support ``isinstance(x, proxy)`` by delegating to the active class.

        Allows a proxy used as a deprecated class alias to work transparently with ``isinstance`` without emitting a
        warning — type checks are structural, not a use of the deprecated API.

        Returns False when the active object is not a type.

        """
        active = self._get_active()
        if isinstance(active, type):
            # Delegate via isinstance to preserve metaclass-defined instance checks.
            return isinstance(instance, active)
        return False

    def __subclasscheck__(self, subclass: type) -> bool:
        """Support ``issubclass(X, proxy)`` by delegating to the active class.

        Same rationale as :meth:`~deprecate.proxy._DeprecatedProxy.__instancecheck__` — no warning emitted.

        Returns False when the active object is not a type.

        """
        active = self._get_active()
        if isinstance(active, type):
            # Delegate via issubclass so that any metaclass-defined
            # __subclasscheck__ (e.g., from abc.ABCMeta) is respected.
            return issubclass(subclass, active)
        return False


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
) -> Callable[[type], "_DeprecatedProxy"]:
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
            been applied.  Caller-supplied values override entries with the same key.  Ignored when ``target`` is
            :attr:`~deprecate._types.TargetMode.NOTIFY` (passing both emits a :class:`UserWarning` at decoration
            time; will be :class:`TypeError` in v1.0).
        attrs_mapping: Optional dict mapping deprecated attribute names to canonical names (or ``None`` for
            warn-only).  When set, only the listed attribute names emit a deprecation warning on access; all other
            attributes are forwarded silently.  The redirect applies to reads (``__getattr__``), writes
            (``__setattr__``), and deletes (``__delattr__``).  Redirect chains must not form cycles (e.g. ``{"a": "b", "b": "a"}`` raises
            :class:`ValueError` at decoration time).  Multi-stage rename chains like ``{"a": "b", "b": "c"}``
            are valid because the chain terminates at ``"c"`` which is not a key in the mapping.

            Example: ``attrs_mapping={"color": "colour", "txt": "text"}`` warns on ``proxy.color`` access and
            returns ``proxy.colour``; ``proxy.colour`` is forwarded silently.

            When ``attrs_mapping`` is ``None`` (default), the existing behaviour is preserved: a warning is emitted
            on every attribute access through the proxy.
        update_docstring: If ``True``, inject a deprecation notice into the class docstring at decoration time (same
            behaviour as ``@deprecated(update_docstring=True)``).
        docstring_style: Output style for the injected notice when ``update_docstring=True``.  ``"auto"`` detects the
            doc engine at decoration time; ``"rst"`` emits a ``.. deprecated::`` directive; ``"mkdocs"`` /
            ``"markdown"`` emit a ``!!! warning`` admonition.

    Returns:
        A decorator that wraps the class in a :class:`~deprecate.proxy._DeprecatedProxy`.

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

    def decorator(cls: type) -> "_DeprecatedProxy":
        if stream is not None and not deprecated_in and not template_mgs:
            warnings.warn(
                f"`@deprecated_class` on `{cls.__name__}` has no `deprecated_in` set."
                " Deprecation notices and generated documentation will omit the `deprecated_in` version."
                " Pass `deprecated_in` for a meaningful deprecation notice.",
                UserWarning,
                stacklevel=2,
            )
        proxy = _DeprecatedProxy(
            obj=cls,
            name=cls.__name__,
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
            # Use a SimpleNamespace shim so _update_docstring_with_deprecation can set
            # __doc__ normally; then store the result on the proxy via object.__setattr__
            # (bypassing the proxy's forwarding __setattr__).
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
            Caller-supplied values override entries with the same key.

    Returns:
        A :class:`~deprecate.proxy._DeprecatedProxy` wrapping *obj*.

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
