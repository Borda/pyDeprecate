"""Shared type definitions for pyDeprecate.

This module provides typed interfaces consumed by the decorator, proxy, and audit
modules so that static analysers can catch schema mismatches at analysis time rather
than silently returning ``None`` at runtime.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from typing_extensions import TypeGuard


@dataclass(frozen=True)
class DeprecationConfig:
    """Static deprecation metadata attached to deprecated callables as ``__deprecated__``.

    All fields are always set — both ``@deprecated``-decorated functions and
    :class:`~deprecate.proxy._DeprecatedProxy` objects use this unified schema.

    Attributes:
        deprecated_in: Version string when the callable was deprecated.
        remove_in: Version string when the callable will be removed.
        name: Display the name of the deprecated source (function or class name).
        target: Replacement target — a callable, ``True`` for self-deprecation, or ``None``.
        args_mapping: Optional dict remapping argument names; values may be ``None`` to
            drop the argument entirely.
        docstring_style: Docstring notice output style when ``update_docstring=True``.
    """

    deprecated_in: str = ""
    remove_in: str = ""
    name: str = ""
    target: Any = None
    args_mapping: Optional[dict[str, Optional[str]]] = None
    docstring_style: str = "rst"


@runtime_checkable
class _HasDeprecationMeta(Protocol):
    """Structural type for any callable that carries ``__deprecated__`` metadata.

    Both ``@deprecated``-decorated functions and :class:`~deprecate.proxy._DeprecatedProxy`
    instances satisfy this protocol once the decorator has been applied.

    Used as a TypeGuard target so that a ``hasattr`` guard narrows the type of an
    arbitrary callable to one whose ``__deprecated__`` attribute is typed — eliminating
    the need for a ``cast`` after the guard.
    """

    __deprecated__: DeprecationConfig

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Call the deprecated object."""
        raise NotImplementedError


def _has_deprecation_meta(obj: Any) -> "TypeGuard[_HasDeprecationMeta]":  # noqa: ANN401
    """Return ``True`` if *obj* carries typed :class:`DeprecationConfig` metadata.

    Using this as a guard narrows the type of *obj* from ``Any`` / ``Callable`` to
    :class:`_HasDeprecationMeta`, allowing direct typed access to ``obj.__deprecated__``
    without a ``cast``.

    Args:
        obj: Any object to test.

    Returns:
        ``True`` if ``__deprecated__`` exists and is a
        :class:`~deprecate._types.DeprecationConfig`; ``False`` otherwise.

    """
    return isinstance(getattr(obj, "__deprecated__", None), DeprecationConfig)


@dataclass
class _ProxyConfig:
    """Private mutable runtime state for :class:`~deprecate.proxy._DeprecatedProxy`.

    This is an internal type — not exported from the package and not referenced
    outside of :mod:`deprecate.proxy`.

    Attributes:
        obj: The wrapped (source) object.
        stream: Callable used to emit warnings, or ``None`` to suppress them.
        num_warns: Maximum number of warnings to emit; ``-1`` means unlimited.
        read_only: When ``True``, write operations through the proxy raise :class:`AttributeError`.
        warned: Mutable counter tracking how many warnings have been emitted so far.
    """

    obj: Any
    stream: Optional[Callable[..., None]]
    num_warns: int
    read_only: bool
    warned: int = 0


@dataclass
class _WrapperState:
    """Private mutable runtime state for ``@deprecated``-decorated callables.

    This is an internal type — not exported from the package and not referenced
    outside of :mod:`deprecate.deprecation`.

    Attributes:
        called: Total invocation count, including calls where the warning was suppressed.
        warned_calls: Number of callable-level deprecation warnings emitted so far.
        warned_args: Per-argument warning counts for argument-level deprecations.
            Keys are deprecated argument names; values are emission counts.
    """

    called: int = 0
    warned_calls: int = 0
    warned_args: dict[str, int] = field(default_factory=dict)
