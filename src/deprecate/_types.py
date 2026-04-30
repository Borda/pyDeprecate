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
    def from_legacy(
        cls,
        target: Union[None, bool],
        *,
        stacklevel: Optional[int] = 3,
    ) -> "TargetMode":
        """Convert a legacy ``target`` sentinel (``None`` / ``True`` / ``False``) to a :class:`TargetMode`.

        Backwards-compatibility shim for the ``@deprecated`` decorator.  Each
        sentinel is mapped to its modern equivalent and a deprecation warning is
        emitted.  Pass ``stacklevel=None`` to suppress all warnings.

        Args:
            target: Legacy sentinel — ``None`` → :attr:`TargetMode.NOTIFY`,
                ``True`` → :attr:`TargetMode.ARGS_REMAP`, ``False`` →
                :attr:`TargetMode.NOTIFY` (not a valid mode; :class:`TypeError`
                in v1.0).
            stacklevel: Stack level forwarded to :func:`warnings.warn`.  Pass
                ``None`` to suppress all warnings entirely.  Defaults to ``3``.

        Returns:
            The corresponding :class:`TargetMode` member.

        Raises:
            TypeError: If ``target`` is anything other than ``None``, ``True``,
                or ``False``.

        Examples:
            >>> TargetMode.from_legacy(None, stacklevel=None)
            <TargetMode.NOTIFY: 'notify'>
            >>> TargetMode.from_legacy(True, stacklevel=None)
            <TargetMode.ARGS_REMAP: 'args_remap'>
            >>> TargetMode.from_legacy(False, stacklevel=None)
            <TargetMode.NOTIFY: 'notify'>

        """
        if target is None:
            if stacklevel is not None:
                warnings.warn(
                    "target=None is deprecated since v0.9; use TargetMode.NOTIFY instead. Will be removed in v1.0.",
                    FutureWarning,
                    stacklevel=stacklevel,
                )
            return cls.NOTIFY
        if target is True:
            if stacklevel is not None:
                warnings.warn(
                    "target=True is deprecated since v0.9; use TargetMode.ARGS_REMAP instead. Will be removed in v1.0.",
                    FutureWarning,
                    stacklevel=stacklevel,
                )
            return cls.ARGS_REMAP
        if target is False:
            if stacklevel is not None:
                warnings.warn(
                    "'target=False' is not a valid deprecation mode and will be treated as TargetMode.NOTIFY."
                    " This will be TypeError in v1.0.",
                    UserWarning,
                    stacklevel=stacklevel,
                )
            return cls.NOTIFY
        raise TypeError(
            f"`TargetMode.from_legacy` accepts only None, True, or False; got {type(target).__name__}: {target!r}."
        )

    @classmethod
    def from_legacy_proxy(
        cls,
        target: Any,  # noqa: ANN401
        *,
        args_mapping: Optional[dict] = None,
        stacklevel: Optional[int] = 3,
    ) -> Optional["TargetMode"]:
        """Normalise a proxy-specific legacy ``target`` sentinel for :func:`~deprecate.proxy.deprecated_class`.

        ``True`` and ``False`` are invalid for the proxy context and normalise to
        :attr:`TargetMode.NOTIFY` (or :attr:`TargetMode.ARGS_REMAP` when ``args_mapping`` is non-empty)
        after emitting a warning.  All other values pass through unchanged.
        Pass ``stacklevel=None`` to suppress warnings.

        Args:
            target: Raw ``target`` value from the caller.  Only ``True`` and
                ``False`` trigger normalisation; any other value is returned as-is.
            args_mapping: The ``args_mapping`` dict supplied to ``deprecated_class``, or
                ``None`` when not provided.  When ``target=True`` and ``args_mapping`` is
                non-empty the result is :attr:`TargetMode.ARGS_REMAP` instead of
                :attr:`TargetMode.NOTIFY`, and a :class:`FutureWarning` is emitted.
            stacklevel: Stack level forwarded to :func:`warnings.warn`.  Pass
                ``None`` to suppress all warnings.  Defaults to ``3``.

        Returns:
            :attr:`TargetMode.ARGS_REMAP` when ``target=True`` and ``args_mapping`` is non-empty;
            :attr:`TargetMode.NOTIFY` when ``target`` was ``True`` (without ``args_mapping``) or
            ``False``; otherwise ``target`` unchanged.

        Examples:
            >>> TargetMode.from_legacy_proxy(True, stacklevel=None)
            <TargetMode.NOTIFY: 'notify'>
            >>> TargetMode.from_legacy_proxy(True, args_mapping={"old": "new"}, stacklevel=None)
            <TargetMode.ARGS_REMAP: 'args_remap'>
            >>> TargetMode.from_legacy_proxy(False, stacklevel=None)
            <TargetMode.NOTIFY: 'notify'>
            >>> TargetMode.from_legacy_proxy(None) is None
            True
            >>> TargetMode.from_legacy_proxy(TargetMode.NOTIFY)
            <TargetMode.NOTIFY: 'notify'>

        """
        if target is True:
            if args_mapping:
                if stacklevel is not None:
                    warnings.warn(
                        "target=True with args_mapping will resolve to TargetMode.ARGS_REMAP."
                        " Will be TypeError in v1.0.",
                        FutureWarning,
                        stacklevel=stacklevel,
                    )
                return cls.ARGS_REMAP
            if stacklevel is not None:
                warnings.warn(
                    "target=True is not valid for deprecated_class() and is ignored. Will be TypeError in v1.0.",
                    FutureWarning,
                    stacklevel=stacklevel,
                )
            return cls.NOTIFY
        if target is False:
            if stacklevel is not None:
                warnings.warn(
                    "target=False is not valid for deprecated_class(). Will be TypeError in v1.0.",
                    UserWarning,
                    stacklevel=stacklevel,
                )
            return cls.NOTIFY
        return target

    @classmethod
    def validate(
        cls,
        mode: "TargetMode",
        source_name: str,
        args_mapping: Optional[dict] = None,
        args_extra: Optional[dict] = None,
        *,
        stacklevel: int = 2,
    ) -> None:
        """Validate a :class:`TargetMode` against the supplied configuration and emit misconfig warnings.

        Checks three misconfiguration combinations and emits a :class:`UserWarning`
        for each one found.  These warnings will become :class:`TypeError` in v1.0.

        Misconfiguration warnings are always emitted via :func:`warnings.warn` with
        ``UserWarning`` — they are construction-time guards independent of the
        runtime ``stream`` callable used for deprecation notices.

        Args:
            mode: The resolved :class:`TargetMode` to validate.
            source_name: ``__name__`` of the decorated source callable, used in
                warning messages.
            args_mapping: The ``args_mapping`` dict supplied to ``@deprecated``, or
                ``None`` when not provided.
            args_extra: The ``args_extra`` dict supplied to ``@deprecated``, or
                ``None`` when not provided.
            stacklevel: Stack level forwarded to :func:`warnings.warn` so that
                reported locations point at the decorator application site.
                Defaults to ``2``.

        Returns:
            None

        Examples:
            >>> import warnings
            >>> with warnings.catch_warnings(record=True) as w:
            ...     warnings.simplefilter("always")
            ...     TargetMode.validate(TargetMode.ARGS_REMAP, "my_func", args_mapping=None)
            ...     assert len(w) == 1
            ...     assert "args_mapping" in str(w[0].message)
            >>> with warnings.catch_warnings(record=True) as w:
            ...     warnings.simplefilter("always")
            ...     TargetMode.validate(TargetMode.NOTIFY, "my_func", args_mapping={"old": "new"})
            ...     assert len(w) == 1
            ...     assert "args_mapping" in str(w[0].message)
            >>> with warnings.catch_warnings(record=True) as w:
            ...     warnings.simplefilter("always")
            ...     TargetMode.validate(TargetMode.NOTIFY, "my_func", args_extra={"bias": 1})
            ...     assert len(w) == 1
            ...     assert "args_extra" in str(w[0].message)

        """
        if mode is cls.ARGS_REMAP and not args_mapping:
            warnings.warn(
                f"`@deprecated(target=TargetMode.ARGS_REMAP)` on `{source_name}` requires "
                "`args_mapping` to specify which arguments are being renamed. Without it the "
                "decorator has zero effect. This will be TypeError in v1.0.",
                UserWarning,
                stacklevel=stacklevel,
            )
        if mode is cls.NOTIFY and args_mapping:
            warnings.warn(
                f"`@deprecated(target=TargetMode.NOTIFY)` on `{source_name}` ignores "
                "`args_mapping`. Use `TargetMode.ARGS_REMAP` to rename arguments, or pass a "
                "callable target to forward the call. This will be TypeError in v1.0.",
                UserWarning,
                stacklevel=stacklevel,
            )
        if mode is cls.NOTIFY and args_extra:
            warnings.warn(
                f"`@deprecated(target=TargetMode.NOTIFY)` on `{source_name}` ignores "
                "`args_extra`. Use a callable target to forward with extra arguments. "
                "This will be TypeError in v1.0.",
                UserWarning,
                stacklevel=stacklevel,
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
        warned: Mutable counter tracking how many global (callable-level) warnings have been emitted so far.
        warned_args: Per-argument warning counts for argument-level deprecations.
            Keys are deprecated argument names; values are emission counts.
    """

    obj: Any
    stream: Optional[Callable[..., None]]
    num_warns: int
    read_only: bool
    warned: int = 0
    warned_args: dict[str, int] = field(default_factory=dict)


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
