"""Low-level helpers for the deprecation system.

This module provides two kinds of helpers:

**Internal** (used by :mod:`deprecate.deprecation` and :mod:`deprecate.audit`):
    - :func:`~deprecate.utils.get_func_arguments_types_defaults`: Extract parameter names, annotations, and defaults
      from a callable's signature. Used when applying ``args_mapping`` and when auditing wrapper configuration.
    - :func:`~deprecate.utils._signature_facts`: Read a callable's signature once into a frozen
      :class:`~deprecate.utils._SigFacts` record that the decorator and the proxy derive their call-time data from.

**Public — decorator companion** (exported via :mod:`deprecate`):
    - :func:`~deprecate.utils.void`: Accepts any arguments and returns ``None``. Used in deprecated function stubs
      to satisfy IDEs and mypy about unused parameters.

**Public — testing** (exported via :mod:`deprecate`):
    - :func:`~deprecate.utils.assert_no_warnings`: Context manager that asserts no warnings are raised during a
      block — the inverse of ``pytest.warns()``.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import inspect
import warnings
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from types import MappingProxyType, TracebackType
from typing import Any, Callable, Optional, Union

from deprecate._frames import _caller_stacklevel
from deprecate._types import TargetMode


def get_func_arguments_types_defaults(func: Callable) -> list[tuple[str, Any, Any]]:
    """Parse function arguments, types and default values.

    This introspection helper extracts the complete signature information from a function, including parameter names,
    type annotations, and default values.  Useful for dynamic argument handling and validation in wrapper functions.

    Args:
        func: A function to be examined.

    Returns:
        List of tuples, one per argument, each containing:
            - str: argument name
            - Any: argument type annotation (or inspect.Parameter.empty if no annotation)
            - Any: default value (or inspect.Parameter.empty if no default)

    Example:
        >>> def example_func(x: int, y: str = "hello", z=42) -> None:
        ...     pass
        >>> result = get_func_arguments_types_defaults(example_func)
        >>> for name, type_hint, default in result:
        ...     print(f"{name}: type={type_hint}, default={default}")
        x: type=<class 'int'>, default=<class 'inspect._empty'>
        y: type=<class 'str'>, default=hello
        z: type=<class 'inspect._empty'>, default=42

    Note:
        - Parameters without type annotations have annotation = inspect.Parameter.empty
        - Parameters without defaults have default = inspect.Parameter.empty
        - Excludes *args and **kwargs (use inspect.getfullargspec for those)

    """
    func_default_params = _get_signature(func).parameters
    func_arg_type_val = []
    for arg in func_default_params:
        param = func_default_params[arg]
        # Exclude ``*args`` / ``**kwargs`` (as the docstring promises): their names are not concrete
        # parameters, and leaking them into caller-argument validation yields a raw ``TypeError`` instead
        # of the curated "argument not accepted by target" message.
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        func_arg_type_val.append((arg, param.annotation, param.default))
    return func_arg_type_val


def _get_signature(func: Callable) -> inspect.Signature:
    """Read the signature of a callable with :func:`inspect.signature`, without caching it.

    Read when a callable is decorated (through :func:`_signature_facts`) and by the audit helpers, never on a
    deprecated call: the wrapper keeps the facts it needs.  No cache holds the callable, so reading its signature never
    keeps it alive.

    """
    return inspect.signature(func)


#: Parameter kinds that can be filled by a positional argument, in the order a signature declares them.
_POSITIONAL_KINDS = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)


@dataclass(frozen=True)
class _SigFacts:
    """Facts about one callable's signature, collected in a single read so no caller inspects it again.

    :func:`_signature_facts` builds it; :func:`deprecate._dispatch._build_call_facts` and the
    :class:`~deprecate.proxy._DeprecatedProxy` constructor derive their call-time data from it.  The default values
    describe a callable without parameters, which is also what ``_signature_facts(obj, empty_on_error=True)``
    returns for a signature it cannot read.

    Attributes:
        names: Every parameter name, the ``*args`` and ``**kwargs`` names included.
        positional_names: Names of the positional-only and positional-or-keyword parameters, in order.
        positional_only: Names of the POSITIONAL_ONLY parameters.
        order: Every parameter name in declaration order.
        var_positional: ``True`` when the signature declares ``*args``.
        var_keyword: ``True`` when the signature declares ``**kwargs``.
        defaults: Read-only mapping of each parameter that has a default to that default.  Left out of the hash, so
            the record stays hashable whatever the default values are.
        var_positional_index: Position of the ``*args`` parameter in :attr:`order`; ``None`` when there is none.

    """

    names: frozenset[str] = frozenset()
    positional_names: tuple[str, ...] = ()
    positional_only: frozenset[str] = frozenset()
    order: tuple[str, ...] = ()
    var_positional: bool = False
    var_keyword: bool = False
    defaults: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}), hash=False)
    var_positional_index: Optional[int] = None


def _signature_facts(obj: Callable[..., Any], *, empty_on_error: bool = False) -> _SigFacts:
    """Read the signature of a callable once and return every fact the decorator and the proxy need from it.

    A class reports its constructor parameters without ``self``, as :func:`inspect.signature` does.  The signature
    is read through :func:`_get_signature`.

    Args:
        obj: The callable to inspect.
        empty_on_error: Return an empty :class:`_SigFacts` instead of raising when the signature cannot be read
            (``TypeError`` or ``ValueError``).  Any other exception raised while reading it propagates either way.

    Returns:
        The signature facts of ``obj``.

    Raises:
        TypeError: When ``obj`` is not callable or its signature is not supported, unless ``empty_on_error`` is set.
        ValueError: When no signature can be provided for ``obj``, unless ``empty_on_error`` is set.

    Example:
        >>> def target(a, /, b=2, *rest, c, **extra): pass
        >>> facts = _signature_facts(target)
        >>> facts.positional_names, sorted(facts.positional_only), facts.var_positional_index, dict(facts.defaults)
        (('a', 'b'), ['a'], 2, {'b': 2})
        >>> _signature_facts(42, empty_on_error=True) == _SigFacts()
        True

    """
    try:
        params = list(_get_signature(obj).parameters.values())
    except (TypeError, ValueError):
        if not empty_on_error:
            raise
        return _SigFacts()
    var_positional_index = next(
        (index for index, param in enumerate(params) if param.kind is inspect.Parameter.VAR_POSITIONAL), None
    )
    return _SigFacts(
        names=frozenset(param.name for param in params),
        positional_names=tuple(param.name for param in params if param.kind in _POSITIONAL_KINDS),
        positional_only=frozenset(param.name for param in params if param.kind is inspect.Parameter.POSITIONAL_ONLY),
        order=tuple(param.name for param in params),
        var_positional=var_positional_index is not None,
        var_keyword=any(param.kind is inspect.Parameter.VAR_KEYWORD for param in params),
        defaults=MappingProxyType(
            {param.name: param.default for param in params if param.default is not inspect.Parameter.empty}
        ),
        var_positional_index=var_positional_index,
    )


def _is_dataclass_target(cls: Any) -> bool:  # noqa: ANN401
    """Return True if *cls* is a dataclass class (not an instance).

    Used by :class:`~deprecate.proxy._DeprecatedProxy` to detect ``@dataclass`` targets that benefit from automatic
    ``attrs_mapping`` → ``args_mapping`` expansion.

    """
    import dataclasses

    return isinstance(cls, type) and dataclasses.is_dataclass(cls)


def _remap_kwargs(
    mapping: Mapping[str, Optional[str]],
    kwargs: Mapping[str, Any],
    *,
    name: str,
    stream: Optional[Callable[..., Any]],
) -> dict[str, Any]:
    """Rename deprecated keyword arguments to their new names and drop the ones mapped to ``None``.

    Shared by the ``@deprecated`` dispatcher and the ``deprecated_class`` / ``deprecated_instance`` proxy.  A
    collision occurs when a caller supplies both a deprecated name and the new name it maps to: the new-name value
    always wins, whatever the keyword order, and one ``UserWarning`` per pair says the old name was ignored.  A key
    whose mapped value is another falsy value (``""``) keeps its old name.

    Args:
        mapping: The ``args_mapping`` — old argument name to new name, or ``None`` to drop the argument.
        kwargs: The keyword arguments to remap; not modified.
        name: Callable name used in the collision warning.
        stream: Warning stream; a falsy value (``None``) suppresses the collision warning, not the remap.

    Returns:
        A new dict with the remapped keyword arguments.

    Example:
        >>> _remap_kwargs({"old": "new", "gone": None}, {"old": 1, "gone": 2, "keep": 3}, name="f", stream=None)
        {'new': 1, 'keep': 3}
        >>> _remap_kwargs({"old": "new"}, {"new": 6, "old": 5}, name="f", stream=None)
        {'new': 6}

    """
    dropped = {old for old, new in mapping.items() if new is None}
    collisions = [
        (old, new)
        for old, new in mapping.items()
        if new and old in kwargs and new in kwargs and new not in dropped
    ]
    if stream:
        for old, new in collisions:
            warnings.warn(
                f"Both `{old}` (deprecated) and `{new}` were supplied to `{name}()`; `{old}` is ignored.",
                UserWarning,
                stacklevel=_caller_stacklevel(),
            )
    remapped = {(mapping.get(key) or key): value for key, value in kwargs.items() if key not in dropped}
    # The rename above is last-write-wins on the key order; restore every explicitly passed new-name value.
    remapped.update({new: kwargs[new] for _, new in collisions})
    return remapped


def _warns_repr(warns: list[warnings.WarningMessage]) -> list[Union[Warning, str]]:
    """Convert list of warning messages to their string representations.

    Args:
        warns: List of warning message objects captured during execution.

    Returns:
        List of warning messages as strings or Warning objects.

    """
    return [w.message for w in warns]


@contextmanager
def assert_no_warnings(warning_type: Optional[type[Warning]] = None, match: Optional[str] = None) -> Generator:
    """Context manager asserting that no warnings are raised — the inverse of ``pytest.warns()``.

    Useful for testing that refactored code properly avoids deprecated functionality or that new implementations don't
    trigger warnings.

    Args:
        warning_type: The warning type that must NOT be raised (e.g., :class:`FutureWarning`,
            :class:`DeprecationWarning`). If ``None``, asserts that no warnings of any type are raised.
        match: If given, only fail if a warning message contains this string. If ``None``, fails on any warning of
            the specified type.

    Raises:
        AssertionError: If a warning of the specified type (and optionally matching the message pattern) was raised
            during the context.

    Example:
        >>> # Assert new function doesn't trigger FutureWarning
        >>> import warnings
        >>> def new_func(x: int) -> int:
        ...     return x * 2
        >>> with assert_no_warnings(FutureWarning):
        ...     result = new_func(42)
        >>> result
        84

        >>> # Assert NO warnings at all are raised
        >>> def clean_function():
        ...     pass
        >>> with assert_no_warnings():
        ...     clean_function()

        >>> # Only fail if warning message matches pattern
        >>> def some_function():
        ...     warnings.warn("deprecated feature", FutureWarning)
        >>> # Passes because warning contains "feature", not "other"
        >>> with assert_no_warnings(FutureWarning, match="other"):
        ...     some_function()

    Note:
        This context manager is particularly useful in pytest for testing that refactored code properly uses new APIs
        without triggering deprecation warnings.

    """
    with warnings.catch_warnings(record=True) as called:
        # Cause all warnings to always be triggered.
        warnings.simplefilter("always")
        # Trigger a warning.
        yield
        # no warning raised
        if not called:
            return
        if not warning_type:
            raise AssertionError(f"While catching all warnings, these were found: {_warns_repr(called)}")
        # filter warnings by type
        warns = [w for w in called if issubclass(w.category, warning_type)]
        # Verify some things
        if not warns:
            return
        if not match:
            raise AssertionError(
                f"While catching `{warning_type.__name__}` warnings, these were found: {_warns_repr(warns)}"
            )
        found = [w for w in warns if match in w.message.__str__()]
        if found:
            raise AssertionError(
                f'While catching `{warning_type.__name__}` warnings with "{match}",'
                f" these were found: {_warns_repr(found)}"
            )


class no_warning_call:  # noqa: N801 - kept for backward compatibility with prior snake_case API
    """Deprecated alias for :func:`~deprecate.utils.assert_no_warnings`.

    This context manager is kept for backward compatibility so that existing imports like
    ``from deprecate.utils import no_warning_call`` continue to work until v1.0.

    Warning fires at instantiation — the ``no_warning_call(...)`` call line receives the
    deprecation notice, regardless of how the context manager is subsequently used.

    Args:
        warning_type: The :class:`Warning` subclass to watch for.  Defaults to :class:`Warning`
            (all warning categories).
        match: Optional substring that must appear in the warning message.  When ``None`` (default),
            any warning of the right category triggers an :class:`AssertionError`.

    Examples:
        >>> import warnings
        >>> with warnings.catch_warnings():
        ...     warnings.simplefilter("ignore", DeprecationWarning)
        ...     with no_warning_call():
        ...         pass  # no AssertionError means no warnings were emitted

    """

    def __init__(self, warning_type: Optional[type[Warning]] = None, match: Optional[str] = None) -> None:
        """Emit the alias-deprecation warning and capture args for the no-warning assertion."""
        warnings.warn(
            "`deprecate.utils.no_warning_call` is deprecated in `0.6` and will be removed in `1.0`; "
            "use `deprecate.utils.assert_no_warnings` instead.",
            DeprecationWarning,
            stacklevel=_caller_stacklevel(),
        )
        self._warning_type = warning_type
        self._match = match
        self._inner: Any = None  # ``assert_no_warnings`` returns a ``_GeneratorContextManager``

    def __enter__(self) -> None:
        """Enter the underlying ``assert_no_warnings`` context."""
        self._inner = assert_no_warnings(warning_type=self._warning_type, match=self._match)
        self._inner.__enter__()

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> Optional[bool]:
        """Forward exit to the underlying ``assert_no_warnings`` so its AssertionError still surfaces."""
        assert self._inner is not None  # noqa: S101 — internal invariant: __exit__ only runs after __enter__
        return self._inner.__exit__(exc_type, exc_val, exc_tb)


def void(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Empty function that accepts any arguments and returns None.

    This helper function is used to silence IDE warnings about unused parameters in deprecated functions where the
    body is never executed (calls are forwarded to a target function). It's purely a convenience for developers.

    Args:
        *args: Any positional arguments (ignored).
        **kwargs: Any keyword arguments (ignored).

    Returns:
        None always.

    Example:
        >>> from deprecate import deprecated, void
        >>>
        >>> def new_func(x: int) -> int:
        ...     return x * 2
        >>>
        >>> @deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
        ... def old_func(x: int) -> int:
        ...     void(x)  # Silences IDE warning about unused 'x'
        ...     # This line is never reached - call forwarded to new_func

    Note:
        This function has no runtime effect - it's purely for developer convenience. You can also use ``pass`` or
        just a docstring instead of calling ``void()``.

    """
    _, _ = args, kwargs


def _unwrap_descriptor_target(
    target: Union[bool, None, Callable, TargetMode, staticmethod, classmethod],
) -> Union[bool, None, Callable, TargetMode]:
    """Return ``target.__func__`` when *target* is a descriptor; pass through otherwise."""
    if isinstance(target, (staticmethod, classmethod)):
        return target.__func__
    return target


#: Upper bound on ``functools.partial`` layers walked by :func:`_partial_chain` — a guard against pathological
#: (or self-referencing) chains; real code nests one or two layers.
_MAX_PARTIAL_DEPTH = 32


def _partial_chain(obj: Any) -> tuple[partial, ...]:  # noqa: ANN401
    """Return the ``functools.partial`` layers wrapping *obj*, outermost first (empty for anything else).

    CPython flattens ``partial(partial(f, ...))`` into one layer unless the inner partial carries instance
    attributes, so a chain can be deeper than one; the walk stops after :data:`_MAX_PARTIAL_DEPTH` layers.

    Args:
        obj: Any object; usually a ``target`` handed to ``@deprecated``.

    Returns:
        The partial objects from the outermost to the innermost; the innermost one's ``.func`` is the wrapped base.

    Example:
        >>> from functools import partial
        >>> def scale(x, y=1): return x * y
        >>> [layer.keywords for layer in _partial_chain(partial(scale, y=5))]
        [{'y': 5}]
        >>> _partial_chain(scale)
        ()

    """
    layers: list[partial] = []
    while isinstance(obj, partial) and len(layers) < _MAX_PARTIAL_DEPTH:
        layers.append(obj)
        obj = obj.func
    return tuple(layers)
