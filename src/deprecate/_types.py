"""Shared type definitions for pyDeprecate.

This module provides typed interfaces consumed by the decorator, proxy, and audit
modules so that static analysers can catch schema mismatches at analysis time rather
than silently returning ``None`` at runtime.
"""

import warnings
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional, Protocol, Union, runtime_checkable

if TYPE_CHECKING:
    from typing_extensions import TypeGuard


class TargetMode(Enum):
    """Selects ``@deprecated`` behaviour when no callable replacement is provided.

    Members:
        NOTIFY: Notify-only deprecation -- warn on every call; original body
            executes unchanged. Replaces ``target=None``. Passing
            ``args_mapping`` or ``args_extra`` with this mode emits a
            :class:`UserWarning` today; :class:`TypeError` is planned in v1.0.
        ARGS_REMAP: Deprecate argument names only -- warn only when deprecated
            argument names are passed; remaps kwargs via ``args_mapping`` before
            calling the original body. Replaces ``target=True``. This mode is
            strongly recommended with ``args_mapping``; omitting it emits a
            :class:`UserWarning` today, and :class:`TypeError` is planned in
            v1.0.

    Examples:
        >>> from deprecate import TargetMode
        >>> TargetMode.NOTIFY.value
        'notify'
        >>> TargetMode.ARGS_REMAP.value
        'args_remap'

    """

    NOTIFY = "notify"
    ARGS_REMAP = "args_remap"

    @classmethod
    def from_legacy(cls, target: Union[None, bool], *, warn: bool = True) -> "TargetMode":
        """Convert a legacy ``target`` sentinel (``None`` / ``True`` / ``False``) to a :class:`TargetMode`.

        Consolidates the v0.9 backwards-compatibility shim used by the
        ``@deprecated`` decorator. Each legacy sentinel is mapped to its modern
        :class:`TargetMode` equivalent; a deprecation warning is emitted unless
        ``warn=False``.

        Args:
            target: Legacy sentinel value:

                - ``None``   → :attr:`TargetMode.NOTIFY` (emits :class:`FutureWarning`)
                - ``True``   → :attr:`TargetMode.ARGS_REMAP`   (emits :class:`FutureWarning`)
                - ``False``  → :attr:`TargetMode.NOTIFY` (emits :class:`UserWarning`;
                  ``False`` is not a valid mode and is treated as transparent for
                  backwards compatibility — it will become :class:`TypeError` in v1.0).
            warn: When ``False``, suppress all deprecation warnings and silently
                return the mapped :class:`TargetMode`. Defaults to ``True``.

        Returns:
            The corresponding :class:`TargetMode` member.

        Raises:
            TypeError: If ``target`` is anything other than ``None``, ``True``, or ``False``
                (e.g. a callable, a :class:`TargetMode` member, or any other type).

        Examples:
            >>> TargetMode.from_legacy(None, warn=False)
            <TargetMode.NOTIFY: 'notify'>
            >>> TargetMode.from_legacy(True, warn=False)
            <TargetMode.ARGS_REMAP: 'args_remap'>
            >>> TargetMode.from_legacy(False, warn=False)
            <TargetMode.NOTIFY: 'notify'>

        """
        if target is None:
            if warn:
                warnings.warn(
                    "target=None is deprecated since v0.9; use TargetMode.NOTIFY instead. Will be removed in v1.0.",
                    FutureWarning,
                    stacklevel=3,
                )
            return cls.NOTIFY
        if target is True:
            if warn:
                warnings.warn(
                    "target=True is deprecated since v0.9; use TargetMode.ARGS_REMAP instead. Will be removed in v1.0.",
                    FutureWarning,
                    stacklevel=3,
                )
            return cls.ARGS_REMAP
        if target is False:
            if warn:
                warnings.warn(
                    "'target=False' is not a valid deprecation mode and will be treated as TargetMode.NOTIFY."
                    " This will be TypeError in v1.0.",
                    UserWarning,
                    stacklevel=3,
                )
            return cls.NOTIFY
        raise TypeError(
            f"`TargetMode.from_legacy` accepts only None, True, or False; got {type(target).__name__}: {target!r}."
        )


@dataclass(frozen=True)
class DeprecationConfig:
    """Static deprecation metadata attached to deprecated callables as ``__deprecated__``.

    All fields are always set — both ``@deprecated``-decorated functions and
    :class:`~deprecate.proxy._DeprecatedProxy` objects use this unified schema.

    Attributes:
        deprecated_in: Version string when the callable was deprecated.
        remove_in: Version string when the callable will be removed.
        name: Display the name of the deprecated source (function or class name).
        target: None, True, False (legacy sentinels, deprecated since v0.9),
            TargetMode.NOTIFY, TargetMode.ARGS_REMAP, or a callable.
        args_mapping: Optional dict remapping argument names; values may be ``None`` to
            drop the argument entirely.
        docstring_style: Docstring notice output style when ``update_docstring=True``.
    """

    deprecated_in: str = ""
    remove_in: str = ""
    name: str = ""
    target: Any = None
    args_mapping: Optional[dict[str, Optional[str]]] = None
    docstring_style: Literal["rst", "mkdocs"] = "rst"


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


@runtime_checkable
class _DeprecatedCallable(Protocol):
    """Structural type for a ``@deprecated``-decorated callable with mutable runtime state.

    This protocol describes the shape of a function or method after the ``@deprecated``
    decorator has been applied. It includes both static metadata (``__deprecated__``)
    and mutable runtime state (``_state``).

    Used to type-safely access ``_state`` on decorated callables without casting.
    """

    __deprecated__: DeprecationConfig
    _state: _WrapperState

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Call the deprecated callable."""
        raise NotImplementedError
