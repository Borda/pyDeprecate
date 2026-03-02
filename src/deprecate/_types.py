"""Shared type definitions for pyDeprecate.

This module provides typed interfaces consumed by the decorator, proxy, and audit
modules so that static analysers can catch schema mismatches at analysis time rather
than silently returning ``None`` at runtime.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class DeprecationInfo:
    """Static deprecation metadata attached to deprecated callables as ``__deprecated__``.

    All five fields are always set — both ``@deprecated``-decorated functions and
    :class:`~deprecate.proxy._DeprecatedProxy` objects use this unified schema.

    Attributes:
        deprecated_in: Version string when the callable was deprecated.
        remove_in: Version string when the callable will be removed.
        name: Display name of the deprecated source (function or class name).
        target: Replacement target — a callable, ``True`` for self-deprecation, or ``None``.
        args_mapping: Optional dict remapping argument names; values may be ``None`` to
            drop the argument entirely.
    """

    deprecated_in: str = ""
    remove_in: str = ""
    name: str = ""
    target: Any = None
    args_mapping: Optional[dict[str, Optional[str]]] = None


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
        warned: Number of callable-level deprecation warnings emitted so far.
        warned_args: Per-argument warning counts for argument-level deprecations.
            Keys are deprecated argument names; values are emission counts.
    """

    called: int = 0
    warned_calls: int = 0
    warned_args: dict[str, int] = field(default_factory=dict)
