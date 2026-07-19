"""Property descriptor subclasses backing ``@deprecated`` on ``@property``.

Two :class:`property` subclasses live here, both free of the call-forwarding engine:

- :class:`_DeprecatedProperty` — re-wraps ``getter``/``setter``/``deleter`` results so chain-style
  rebinding keeps the deprecation warning on every accessor.
- :class:`_StrictProperty` — opt-in strict ``property`` replacement (``from deprecate import property``)
  that rejects the inner-order ``@property @deprecated`` mistake at class-body evaluation time.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

from typing import Callable, Optional

from deprecate._types import _has_deprecation_meta


class _DeprecatedProperty(property):
    """``property`` subclass that re-wraps ``getter``/``setter``/``deleter`` results.

    Built-in ``property.setter`` / ``property.deleter`` construct a fresh plain ``property``
    from the existing accessors plus the newly supplied one — discarding any deprecation
    wrapping applied to the original accessors. Overriding ``getter``/``setter``/``deleter``
    to return another ``_DeprecatedProperty`` — wrapping the new accessor with the same packing
    closure stored in ``_wrap`` — preserves the deprecation warning on every subsequent rebind.

    Example:
        Chain-style rebinding works because ``_DeprecatedProperty.setter`` re-wraps the
        new accessor rather than rebuilding a plain ``property``:

            @deprecated(deprecated_in="1.0", remove_in="2.0")
            @property
            def value(self): ...

            @value.setter
            def value(self, v): ...  # setter() returns _DeprecatedProperty, not plain property

    Args:
        fget: Getter callable, or ``None``.
        fset: Setter callable, or ``None``.
        fdel: Deleter callable, or ``None``.
        doc: Property docstring; ``None`` defers to ``fget.__doc__``.
        _wrap: Required packing closure to re-apply on accessor rebinds.

    Attributes:
        _wrap: Closure that re-applies the surrounding ``@deprecated`` decoration to a
            new accessor; captures the same template/stacklevel/config as the original
            wrap. Required — always set by ``packing()``; never ``None``.

    Note:
        ``_DeprecatedProperty`` itself does **not** carry a ``__deprecated__`` attribute —
        that attribute lives on the individual wrapped accessors (``fget``, ``fset``, ``fdel``).
        ``find_deprecation_wrappers`` discovers properties via whichever non-``None`` accessor
        carries ``__deprecated__`` first. A setter-only property (``fget=None``) is discovered
        via ``fset``; a plain-getter property whose ``fget`` is not deprecated but whose ``fset``
        is deprecated is likewise discovered via ``fset``.

        **Typing**: ``getter``/``setter``/``deleter`` return ``_DeprecatedProperty`` (covariant
        narrowing of ``property``'s ``-> property`` annotation). Static type is preserved for
        variables typed ``_DeprecatedProperty``; variables typed ``property`` lose the narrowing
        and mypy infers the rebuilt accessor as plain ``property`` — chain inference still works
        at runtime via dynamic dispatch.

    """

    _wrap: Callable[[Callable], Callable]

    def __init__(
        self,
        fget: Optional[Callable] = None,
        fset: Optional[Callable] = None,
        fdel: Optional[Callable] = None,
        doc: Optional[str] = None,
        *,
        _wrap: Callable[[Callable], Callable],
    ) -> None:
        super().__init__(fget, fset, fdel, doc)
        # ``property`` exposes no slot for arbitrary attributes via ``__init__``, but it
        # *does* permit attribute assignment on subclass instances.
        self._wrap = _wrap

    def _rewrap(self, accessor: Optional[Callable]) -> Optional[Callable]:
        """Apply the stored ``_wrap`` closure to ``accessor`` when present."""
        if accessor is None:
            return accessor
        return self._wrap(accessor)

    def getter(self, fget: Callable) -> "_DeprecatedProperty":
        """Return a new ``_DeprecatedProperty`` whose ``fget`` is freshly wrapped."""
        return _DeprecatedProperty(self._rewrap(fget), self.fset, self.fdel, self.__doc__, _wrap=self._wrap)

    def setter(self, fset: Callable) -> "_DeprecatedProperty":
        """Return a new ``_DeprecatedProperty`` whose ``fset`` is freshly wrapped."""
        return _DeprecatedProperty(self.fget, self._rewrap(fset), self.fdel, self.__doc__, _wrap=self._wrap)

    def deleter(self, fdel: Callable) -> "_DeprecatedProperty":
        """Return a new ``_DeprecatedProperty`` whose ``fdel`` is freshly wrapped."""
        return _DeprecatedProperty(self.fget, self.fset, self._rewrap(fdel), self.__doc__, _wrap=self._wrap)


class _StrictProperty(property):
    """Strict ``property`` replacement that rejects inner-order ``@deprecated`` at class-body evaluation time.

    Import as ``from deprecate import property`` to opt a module into a guard against the accidental *inner order*
    ``@property`` over ``@deprecated`` (``@deprecated`` closer to ``def``). That order wraps only ``fget``; any
    setter or deleter added afterwards is built from the plain :class:`property` base and never warns, so writes
    and deletes silently bypass the deprecation notice. ``_StrictProperty`` raises :class:`TypeError` the moment it
    is handed a getter that already carries ``__deprecated__`` metadata — before any instance is created — steering
    authors to the canonical *outer order* ``@deprecated(...) @property``.

    Because it subclasses the builtin :class:`property`, every ``isinstance(obj, property)`` branch in the decorator
    and audit machinery treats it transparently: the outer ``@deprecated`` converts it to a
    :class:`_DeprecatedProperty` exactly as it would a builtin ``property``.

    Modules that do not import the strict ``property`` keep the builtin behaviour untouched — the strictness is
    purely opt-in.

    Example:
        >>> from deprecate import deprecated, property as strict_property
        >>> @deprecated(deprecated_in="1.0", remove_in="2.0")
        ... def old_getter(self):
        ...     '''Already-deprecated getter.'''
        ...     return 42
        >>> try:
        ...     strict_property(old_getter)  # inner-order detected
        ... except TypeError:
        ...     print("TypeError raised")
        TypeError raised

    """

    def __init__(
        self,
        fget: Optional[Callable] = None,
        fset: Optional[Callable] = None,
        fdel: Optional[Callable] = None,
        doc: Optional[str] = None,
    ) -> None:
        """Construct the property, rejecting an already-deprecated getter.

        Args:
            fget: Getter callable, or ``None``. A :class:`TypeError` is raised when it carries ``__deprecated__``
                metadata (the inner-order signature). The guard fires on ``fget`` only; ``fset`` and ``fdel``
                are accepted without inspection — the decorator-stacking inner-order bug is structurally a
                getter-ordering issue.
            fset: Setter callable, or ``None``.
            fdel: Deleter callable, or ``None``.
            doc: Property docstring; ``None`` defers to ``fget.__doc__``.

        Raises:
            TypeError: When ``fget`` is already ``@deprecated``-decorated (inner-order ``@property @deprecated``).

        """
        if fget is not None and _has_deprecation_meta(fget):
            name = getattr(fget, "__qualname__", repr(fget))
            raise TypeError(
                f"Inner-order `@property @deprecated` detected on `{name}`. Only `fget` will warn —"
                " setter and deleter remain silent."
                " This check is active because `property` in this module is `deprecate._properties._StrictProperty`"
                " (imported via `from deprecate import property`)."
                " Swap the decorator order to the canonical outer order:"
                " `@deprecated(deprecated_in=..., remove_in=...) @property`."
            )
        super().__init__(fget, fset, fdel, doc)
