"""Proxy utilities for deprecating Python object instances and classes.

Provides :func:`~deprecate.structs.deprecated_instance` for wrapping any Python object with
transparent deprecation warnings and optional read-only enforcement, and
:class:`~deprecate.structs.DeprecatedStruct` as both a class-level decorator factory and the
resulting proxy — eliminating the need for a separate internal class.

Typical use cases:

- Deprecating module-level config dicts, constants, or legacy singletons
  while still allowing reads during a migration window.
- Deprecating an Enum or dataclass in favour of a replacement type, with
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

from deprecate.deprecation import TEMPLATE_WARNING_NO_TARGET, deprecation_warning


class DeprecatedStruct:
    """Transparent proxy for deprecated objects, and decorator factory for deprecated classes.

    ``DeprecatedStruct`` operates in two modes distinguished by how it is constructed:

    **Factory mode** — ``DeprecatedStruct(target=X, deprecated_in="1.0", ...)``

        Stores deprecation configuration.  When called with a class, it returns a *proxy-mode*
        instance wrapping that class.  Used as a class decorator::

            @DeprecatedStruct(target=NewColor, deprecated_in="1.0", remove_in="2.0")
            class OldColor(Enum):
                RED = 1

    **Proxy mode** — created internally via :meth:`_as_proxy`

        Wraps a Python object and forwards all attribute, item, and call access while emitting
        a configurable :class:`FutureWarning`.  In *read-only* mode any attempt to mutate the
        proxied object raises :class:`AttributeError`.

    Use :func:`~deprecate.structs.deprecated_instance` to create proxy-mode instances for
    arbitrary objects rather than instantiating this class directly.

    Args:
        target: Optional replacement class (or object) to redirect all access to.
        deprecated_in: Version string when the class was deprecated.
        remove_in: Version string when the class will be removed.
        num_warns: Maximum number of warnings to emit per proxy instance.
            ``1`` warns once; ``-1`` warns on every access.
        stream: Callable used to emit warnings.
            Defaults to :data:`~deprecate.deprecation.deprecation_warning`.
        arg_mapping: Optional dict remapping keyword argument names when the proxy is called.
            Keys are old argument names; values are new names, or ``None`` to drop the argument.
            Only relevant when ``@DeprecatedStruct`` is used as a class decorator (not for
            :func:`~deprecate.structs.deprecated_instance`).

    Example:
        >>> from enum import Enum
        >>> class NewColor(Enum):
        ...     RED = 1
        >>> @DeprecatedStruct(target=NewColor, deprecated_in="1.0", remove_in="2.0", stream=None)
        ... class OldColor(Enum):
        ...     RED = 1
        >>> OldColor.RED is NewColor.RED
        True
        >>> OldColor(1) is NewColor.RED
        True

    """

    # ------------------------------------------------------------------
    # Construction — factory mode
    # ------------------------------------------------------------------

    def __init__(
        self,
        target: Any = None,  # noqa: ANN401
        deprecated_in: str = "",
        remove_in: str = "",
        num_warns: int = 1,
        stream: Optional[Callable[..., None]] = deprecation_warning,
        arg_mapping: Optional[dict] = None,
    ) -> None:
        """Initialise in factory mode, storing deprecation configuration."""
        object.__setattr__(self, "_DeprecatedStruct__mode", "factory")
        object.__setattr__(self, "_DeprecatedStruct__f_target", target)
        object.__setattr__(self, "_DeprecatedStruct__f_deprecated_in", deprecated_in)
        object.__setattr__(self, "_DeprecatedStruct__f_remove_in", remove_in)
        object.__setattr__(self, "_DeprecatedStruct__f_num_warns", num_warns)
        object.__setattr__(self, "_DeprecatedStruct__f_stream", stream)
        object.__setattr__(self, "_DeprecatedStruct__f_arg_mapping", arg_mapping)

    # ------------------------------------------------------------------
    # Factory mode: apply decorator to a class
    # ------------------------------------------------------------------

    @classmethod
    def _as_proxy(
        cls,
        obj: Any,  # noqa: ANN401
        name: str,
        deprecated_in: str = "",
        remove_in: str = "",
        num_warns: int = 1,
        stream: Optional[Callable[..., None]] = deprecation_warning,
        read_only: bool = False,
        target: Any = None,  # noqa: ANN401
        arg_mapping: Optional[dict] = None,
    ) -> "DeprecatedStruct":
        """Create a proxy-mode instance, bypassing ``__init__``."""
        instance = cls.__new__(cls)
        object.__setattr__(instance, "_DeprecatedStruct__mode", "proxy")
        object.__setattr__(instance, "_DeprecatedStruct__obj", obj)
        object.__setattr__(instance, "_DeprecatedStruct__target", target)
        object.__setattr__(instance, "_DeprecatedStruct__name", name)
        object.__setattr__(instance, "_DeprecatedStruct__deprecated_in", deprecated_in)
        object.__setattr__(instance, "_DeprecatedStruct__remove_in", remove_in)
        object.__setattr__(instance, "_DeprecatedStruct__num_warns", num_warns)
        object.__setattr__(instance, "_DeprecatedStruct__stream", stream)
        object.__setattr__(instance, "_DeprecatedStruct__read_only", read_only)
        object.__setattr__(instance, "_DeprecatedStruct__warned", 0)
        object.__setattr__(instance, "_DeprecatedStruct__arg_mapping", arg_mapping)
        object.__setattr__(
            instance,
            "__deprecated__",
            {"deprecated_in": deprecated_in, "remove_in": remove_in, "name": name},
        )
        return instance

    # ------------------------------------------------------------------
    # Internal helpers — always use object.__getattribute__ to avoid
    # triggering the proxy's own __getattr__ / __setattr__.
    # ------------------------------------------------------------------

    def _warn(self) -> None:
        """Emit a deprecation warning if the warn budget is not exhausted."""
        stream: Optional[Callable[..., None]] = object.__getattribute__(self, "_DeprecatedStruct__stream")
        if not stream:
            return
        num_warns: int = object.__getattribute__(self, "_DeprecatedStruct__num_warns")
        warned: int = object.__getattribute__(self, "_DeprecatedStruct__warned")
        if num_warns >= 0 and warned >= num_warns:
            return
        name: str = object.__getattribute__(self, "_DeprecatedStruct__name")
        deprecated_in: str = object.__getattribute__(self, "_DeprecatedStruct__deprecated_in")
        remove_in: str = object.__getattribute__(self, "_DeprecatedStruct__remove_in")
        msg = TEMPLATE_WARNING_NO_TARGET % {
            "source_name": name,
            "deprecated_in": deprecated_in,
            "remove_in": remove_in,
        }
        stream(msg)
        object.__setattr__(self, "_DeprecatedStruct__warned", warned + 1)

    def _check_read_only(self, operation: str) -> None:
        """Raise AttributeError when the proxy is in read-only mode."""
        if object.__getattribute__(self, "_DeprecatedStruct__read_only"):
            name: str = object.__getattribute__(self, "_DeprecatedStruct__name")
            raise AttributeError(
                f"'{name}' is deprecated and read-only. {operation} is not allowed. Migrate away from this object."
            )

    def _get_active(self) -> Any:  # noqa: ANN401
        """Return the active object: *target* when set, otherwise *source*."""
        target = object.__getattribute__(self, "_DeprecatedStruct__target")
        if target is not None:
            return target
        return object.__getattribute__(self, "_DeprecatedStruct__obj")

    def _apply_arg_mapping(self, kwargs: dict) -> dict:
        """Apply arg_mapping to *kwargs*, renaming or dropping keys as configured."""
        arg_mapping: Optional[dict] = object.__getattribute__(self, "_DeprecatedStruct__arg_mapping")
        if not arg_mapping or not kwargs:
            return kwargs
        args_to_drop = {k for k, v in arg_mapping.items() if v is None}
        return {arg_mapping.get(k, k): v for k, v in kwargs.items() if k not in args_to_drop}

    # ------------------------------------------------------------------
    # __call__ — dual mode
    # ------------------------------------------------------------------

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """In factory mode: apply decorator to a class.  In proxy mode: call the active object."""
        mode = object.__getattribute__(self, "_DeprecatedStruct__mode")
        if mode == "factory":
            cls = args[0]
            return DeprecatedStruct._as_proxy(
                obj=cls,
                name=cls.__name__,
                target=object.__getattribute__(self, "_DeprecatedStruct__f_target"),
                deprecated_in=object.__getattribute__(self, "_DeprecatedStruct__f_deprecated_in"),
                remove_in=object.__getattribute__(self, "_DeprecatedStruct__f_remove_in"),
                num_warns=object.__getattribute__(self, "_DeprecatedStruct__f_num_warns"),
                stream=object.__getattribute__(self, "_DeprecatedStruct__f_stream"),
                read_only=False,
                arg_mapping=object.__getattribute__(self, "_DeprecatedStruct__f_arg_mapping"),
            )
        # proxy mode
        self._warn()
        mapped_kwargs = self._apply_arg_mapping(kwargs)
        return self._get_active()(*args, **mapped_kwargs)

    # ------------------------------------------------------------------
    # Attribute access
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        """Forward attribute lookup to the active object, emitting a deprecation warning."""
        self._warn()
        return getattr(self._get_active(), name)

    def __setattr__(self, name: str, value: Any) -> None:  # noqa: ANN401
        """Forward attribute mutation to the source object, raising in read-only mode."""
        self._check_read_only(f"Setting attribute '{name}'")
        setattr(object.__getattribute__(self, "_DeprecatedStruct__obj"), name, value)

    def __delattr__(self, name: str) -> None:
        """Forward attribute deletion to the source object, raising in read-only mode."""
        self._check_read_only(f"Deleting attribute '{name}'")
        delattr(object.__getattribute__(self, "_DeprecatedStruct__obj"), name)

    # ------------------------------------------------------------------
    # Subscript access
    # ------------------------------------------------------------------

    def __getitem__(self, key: Any) -> Any:  # noqa: ANN401
        """Forward subscript lookup to the active object, emitting a deprecation warning."""
        self._warn()
        return self._get_active()[key]

    def __setitem__(self, key: Any, value: Any) -> None:  # noqa: ANN401
        """Forward subscript mutation to the source object, raising in read-only mode."""
        self._check_read_only(f"Setting item '{key}'")
        object.__getattribute__(self, "_DeprecatedStruct__obj")[key] = value

    def __delitem__(self, key: Any) -> None:  # noqa: ANN401
        """Forward subscript deletion to the source object, raising in read-only mode."""
        self._check_read_only(f"Deleting item '{key}'")
        del object.__getattribute__(self, "_DeprecatedStruct__obj")[key]

    # ------------------------------------------------------------------
    # Container protocol
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        """Return length of the wrapped object without emitting a warning."""
        return len(object.__getattribute__(self, "_DeprecatedStruct__obj"))

    def __iter__(self) -> Iterator[Any]:
        """Iterate over the active object, emitting a deprecation warning."""
        self._warn()
        return iter(self._get_active())

    def __contains__(self, item: Any) -> bool:  # noqa: ANN401
        """Check membership without emitting a warning."""
        return item in object.__getattribute__(self, "_DeprecatedStruct__obj")

    # ------------------------------------------------------------------
    # Comparison and hashing
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        """Compare the wrapped object for equality."""
        obj = object.__getattribute__(self, "_DeprecatedStruct__obj")
        if isinstance(other, DeprecatedStruct):
            other = object.__getattribute__(other, "_DeprecatedStruct__obj")
        return bool(obj == other)

    def __ne__(self, other: object) -> bool:
        """Return the inverse of equality."""
        return not self.__eq__(other)

    def __hash__(self) -> int:
        """Return the hash of the wrapped object."""
        return hash(object.__getattribute__(self, "_DeprecatedStruct__obj"))

    # ------------------------------------------------------------------
    # String representations — no warning emitted (used for debugging)
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        """Return repr of the wrapped object."""
        return repr(object.__getattribute__(self, "_DeprecatedStruct__obj"))

    def __str__(self) -> str:
        """Return str of the wrapped object."""
        return str(object.__getattribute__(self, "_DeprecatedStruct__obj"))

    def __bool__(self) -> bool:
        """Return bool of the wrapped object without emitting a warning."""
        return bool(object.__getattribute__(self, "_DeprecatedStruct__obj"))


def deprecated_instance(
    obj: Any,  # noqa: ANN401
    name: str = "",
    deprecated_in: str = "",
    remove_in: str = "",
    num_warns: int = 1,
    stream: Optional[Callable[..., None]] = deprecation_warning,
    read_only: bool = False,
) -> DeprecatedStruct:
    """Wrap any Python object with deprecation warnings.

    Returns a :class:`~deprecate.structs.DeprecatedStruct` that transparently forwards all read
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
        A :class:`~deprecate.structs.DeprecatedStruct` wrapping *obj*.

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
    return DeprecatedStruct._as_proxy(
        obj=obj,
        name=resolved_name,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        num_warns=num_warns,
        stream=stream,
        read_only=read_only,
    )
