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

from collections.abc import Iterator
from typing import Any, Callable, Optional

from deprecate.deprecation import TEMPLATE_WARNING_CALLABLE, TEMPLATE_WARNING_NO_TARGET, deprecation_warning


class _DeprecatedProxy:
    """Transparent proxy that emits deprecation warnings on attribute and item access.

    Wraps any Python object and forwards all read operations (attribute lookup,
    subscript, iteration, calls) to the underlying object — or to an optional
    *target* replacement — while emitting a configurable :class:`FutureWarning`.

    In *read-only* mode any attempt to mutate the proxied object via
    ``__setitem__``, ``__delitem__``, or ``__setattr__`` raises
    :class:`AttributeError`.

    Use :func:`~deprecate.proxy.deprecated_instance` or
    :func:`~deprecate.proxy.deprecated_class` to create instances rather than
    instantiating this class directly.

    Args:
        obj: The deprecated object to wrap (the *source*).
        name: Display name used in the warning message.
        deprecated_in: Version string when the object was deprecated.
        remove_in: Version string when the object will be removed.
        num_warns: Maximum number of warnings to emit.
            ``1`` (default) warns once; ``-1`` warns on every access.
        stream: Callable used to emit warnings.
            Defaults to :data:`~deprecate.deprecation.deprecation_warning`
            (:class:`FutureWarning`).  Pass ``None`` to suppress warnings.
        read_only: If ``True``, raise :class:`AttributeError` on any write
            attempt through the proxy.
        target: Optional replacement object.  When set, all attribute, item,
            and call access is forwarded to *target* instead of *obj*.
        args_mapping: Optional dict remapping keyword argument names when the
            proxy is called.  Keys are old argument names; values are new names,
            or ``None`` to drop the argument entirely.

    """

    def __init__(
        self,
        obj: Any,  # noqa: ANN401
        name: str,
        deprecated_in: str = "",
        remove_in: str = "",
        num_warns: int = 1,
        stream: Optional[Callable[..., None]] = deprecation_warning,
        read_only: bool = False,
        target: Any = None,  # noqa: ANN401
        args_mapping: Optional[dict] = None,
    ) -> None:
        """Initialise the proxy, storing all configuration via object.__setattr__."""
        object.__setattr__(self, "_DeprecatedProxy__obj", obj)
        object.__setattr__(self, "_DeprecatedProxy__target", target)
        object.__setattr__(self, "_DeprecatedProxy__name", name)
        object.__setattr__(self, "_DeprecatedProxy__deprecated_in", deprecated_in)
        object.__setattr__(self, "_DeprecatedProxy__remove_in", remove_in)
        object.__setattr__(self, "_DeprecatedProxy__num_warns", num_warns)
        object.__setattr__(self, "_DeprecatedProxy__stream", stream)
        object.__setattr__(self, "_DeprecatedProxy__read_only", read_only)
        object.__setattr__(self, "_DeprecatedProxy__warned", 0)
        object.__setattr__(self, "_DeprecatedProxy__args_mapping", args_mapping)
        object.__setattr__(
            self,
            "__deprecated__",
            {"deprecated_in": deprecated_in, "remove_in": remove_in, "name": name},
        )

    # ------------------------------------------------------------------
    # Internal helpers — must use object.__getattribute__ / object.__setattr__
    # to avoid triggering the proxy's own __getattr__ / __setattr__.
    # ------------------------------------------------------------------

    def _warn(self) -> None:
        """Emit a deprecation warning if the warn budget is not exhausted."""
        stream: Optional[Callable[..., None]] = object.__getattribute__(self, "_DeprecatedProxy__stream")
        if not stream:
            return
        num_warns: int = object.__getattribute__(self, "_DeprecatedProxy__num_warns")
        warned: int = object.__getattribute__(self, "_DeprecatedProxy__warned")
        if num_warns >= 0 and warned >= num_warns:
            return
        name: str = object.__getattribute__(self, "_DeprecatedProxy__name")
        deprecated_in: str = object.__getattribute__(self, "_DeprecatedProxy__deprecated_in")
        remove_in: str = object.__getattribute__(self, "_DeprecatedProxy__remove_in")
        target: Any = object.__getattribute__(self, "_DeprecatedProxy__target")
        if callable(target):
            target_name = target.__name__
            target_path = f"{target.__module__}.{target_name}"
            msg = TEMPLATE_WARNING_CALLABLE % {
                "source_name": name,
                "deprecated_in": deprecated_in,
                "remove_in": remove_in,
                "target_name": target_name,
                "target_path": target_path,
            }
        else:
            msg = TEMPLATE_WARNING_NO_TARGET % {
                "source_name": name,
                "deprecated_in": deprecated_in,
                "remove_in": remove_in,
            }
        stream(msg)
        object.__setattr__(self, "_DeprecatedProxy__warned", warned + 1)

    def _check_read_only(self, operation: str) -> None:
        """Raise AttributeError when the proxy is in read-only mode."""
        if object.__getattribute__(self, "_DeprecatedProxy__read_only"):
            name: str = object.__getattribute__(self, "_DeprecatedProxy__name")
            raise AttributeError(
                f"'{name}' is deprecated and read-only. {operation} is not allowed. Migrate away from this object."
            )

    def _get_active(self) -> Any:  # noqa: ANN401
        """Return the active object: *target* when set, otherwise *source*."""
        target = object.__getattribute__(self, "_DeprecatedProxy__target")
        if target is not None:
            return target
        return object.__getattribute__(self, "_DeprecatedProxy__obj")

    def _apply_args_mapping(self, kwargs: dict) -> dict:
        """Apply args_mapping to *kwargs*, renaming or dropping keys as configured."""
        args_mapping: Optional[dict] = object.__getattribute__(self, "_DeprecatedProxy__args_mapping")
        if not args_mapping or not kwargs:
            return kwargs
        args_to_drop = {k for k, v in args_mapping.items() if v is None}
        return {args_mapping.get(k, k): v for k, v in kwargs.items() if k not in args_to_drop}

    @staticmethod
    def _is_potential_mutator(name: str) -> bool:
        """Heuristic to detect common mutating methods on built-in collections.

        This is intentionally conservative and only covers the most common
        mutating APIs on built-in container types (lists, dicts, sets).
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

        In read-only mode, common mutating methods on built-in collections
        (for example, ``append`` or ``update``) are wrapped so that calling
        them raises :class:`AttributeError` instead of mutating the
        underlying object.
        """
        self._warn()
        attr = getattr(self._get_active(), name)
        # In read-only mode, guard common mutating methods accessed via attribute lookup.
        if (
            object.__getattribute__(self, "_DeprecatedProxy__read_only")
            and callable(attr)
            and self._is_potential_mutator(name)
        ):

            def _guarded_mutator(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
                self._check_read_only(f"Calling mutating method '{name}'")

            return _guarded_mutator
        return attr

    def __setattr__(self, name: str, value: Any) -> None:  # noqa: ANN401
        """Forward attribute mutation to the active object, raising in read-only mode."""
        self._check_read_only(f"Setting attribute '{name}'")
        setattr(self._get_active(), name, value)

    def __delattr__(self, name: str) -> None:
        """Forward attribute deletion to the active object, raising in read-only mode."""
        self._check_read_only(f"Deleting attribute '{name}'")
        delattr(self._get_active(), name)

    # ------------------------------------------------------------------
    # Subscript access
    # ------------------------------------------------------------------

    def __getitem__(self, key: Any) -> Any:  # noqa: ANN401
        """Forward subscript lookup to the active object, emitting a deprecation warning."""
        self._warn()
        return self._get_active()[key]

    def __setitem__(self, key: Any, value: Any) -> None:  # noqa: ANN401
        """Forward subscript mutation to the active object, raising in read-only mode."""
        self._check_read_only(f"Setting item '{key}'")
        self._get_active()[key] = value

    def __delitem__(self, key: Any) -> None:  # noqa: ANN401
        """Forward subscript deletion to the active object, raising in read-only mode."""
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
        """Call the active object, emitting a deprecation warning."""
        self._warn()
        mapped_kwargs = self._apply_args_mapping(kwargs)
        return self._get_active()(*args, **mapped_kwargs)

    # ------------------------------------------------------------------
    # Comparison and hashing
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        """Compare the wrapped object for equality."""
        obj = object.__getattribute__(self, "_DeprecatedProxy__obj")
        if isinstance(other, _DeprecatedProxy):
            other = object.__getattribute__(other, "_DeprecatedProxy__obj")
        return bool(obj == other)

    def __ne__(self, other: object) -> bool:
        """Return the inverse of equality."""
        return not self.__eq__(other)

    def __hash__(self) -> int:
        """Return the hash of the wrapped object."""
        return hash(object.__getattribute__(self, "_DeprecatedProxy__obj"))

    # ------------------------------------------------------------------
    # String representations — no warning emitted (used for debugging)
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        """Return repr of the wrapped object."""
        return repr(object.__getattribute__(self, "_DeprecatedProxy__obj"))

    def __str__(self) -> str:
        """Return str of the wrapped object."""
        return str(object.__getattribute__(self, "_DeprecatedProxy__obj"))

    def __bool__(self) -> bool:
        """Return bool of the wrapped object without emitting a warning."""
        return bool(object.__getattribute__(self, "_DeprecatedProxy__obj"))


def deprecated_class(
    target: Any = None,  # noqa: ANN401
    deprecated_in: str = "",
    remove_in: str = "",
    num_warns: int = 1,
    stream: Optional[Callable[..., None]] = deprecation_warning,
    args_mapping: Optional[dict] = None,
) -> Callable[[type], "_DeprecatedProxy"]:
    """Decorator factory for deprecating class definitions with optional target redirection.

    Apply ``@deprecated_class(...)`` to an Enum or dataclass to wrap the class
    in a :class:`~deprecate.proxy._DeprecatedProxy`.  All attribute, item, and call access on
    the resulting object will emit a deprecation warning and, if *target* is
    provided, will be forwarded to the replacement class.

    Args:
        target: Optional replacement class to redirect all access to.
        deprecated_in: Version string when the class was deprecated.
        remove_in: Version string when the class will be removed.
        num_warns: Maximum number of warnings to emit per proxy instance.
            ``1`` warns once; ``-1`` warns on every access.
        stream: Callable used to emit warnings.
            Defaults to :data:`~deprecate.deprecation.deprecation_warning`.
        args_mapping: Optional dict remapping keyword argument names when the
            decorated class is called.  Keys are old argument names; values
            are new names, or ``None`` to drop the argument entirely.

    Returns:
        A decorator that wraps the class in a :class:`~deprecate.proxy._DeprecatedProxy`.

    Example:
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

    """

    def decorator(cls: type) -> "_DeprecatedProxy":
        return _DeprecatedProxy(
            obj=cls,
            name=cls.__name__,
            deprecated_in=deprecated_in,
            remove_in=remove_in,
            num_warns=num_warns,
            stream=stream,
            read_only=False,
            target=target,
            args_mapping=args_mapping,
        )

    return decorator


def deprecated_instance(
    obj: Any,  # noqa: ANN401
    name: str = "",
    deprecated_in: str = "",
    remove_in: str = "",
    num_warns: int = 1,
    stream: Optional[Callable[..., None]] = deprecation_warning,
    read_only: bool = False,
) -> "_DeprecatedProxy":
    """Wrap any Python object with deprecation warnings.

    Returns a :class:`~deprecate.proxy._DeprecatedProxy` that transparently forwards all read
    access to *obj* while emitting a :class:`FutureWarning`.  In *read-only*
    mode any write attempt through the proxy raises :class:`AttributeError`.

    Args:
        obj: The object to deprecate (dict, list, custom object, …).
        name: Display name for *obj* used in the warning message.
            When omitted, the type name of *obj* is used (e.g. ``"dict"``).
        deprecated_in: Version string when *obj* was deprecated.
        remove_in: Version string when *obj* will be removed.
        num_warns: Maximum number of warnings to emit.
            ``1`` (default) warns once; ``-1`` warns on every access.
        stream: Callable used to emit warnings.
            Defaults to :data:`~deprecate.deprecation.deprecation_warning`
            (:class:`FutureWarning`).  Pass ``None`` to suppress warnings.
        read_only: If ``True``, raise :class:`AttributeError` on any write
            attempt through the proxy.

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
    return _DeprecatedProxy(
        obj=obj,
        name=resolved_name,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        num_warns=num_warns,
        stream=stream,
        read_only=read_only,
    )
