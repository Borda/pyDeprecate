"""Low-level helpers for the deprecation system.

This module provides two kinds of helpers:

**Internal** (used by :mod:`deprecate.deprecation` and :mod:`deprecate.audit`):
    - :func:`~deprecate.utils.get_func_arguments_types_defaults`: Extract parameter names, annotations, and defaults
      from a callable's signature. Used when applying ``args_mapping`` and when auditing wrapper configuration.

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
from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache
from types import TracebackType
from typing import Any, Callable, Optional, Union


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
        arg_type = func_default_params[arg].annotation
        arg_default = func_default_params[arg].default
        func_arg_type_val.append((arg, arg_type, arg_default))
    return func_arg_type_val


@lru_cache(maxsize=256)
def _get_signature_cached(func: Callable) -> inspect.Signature:
    """Cache inspect.signature lookups for repeated calls.

    Uses an LRU cache (maxsize=256) since function signatures are stable at runtime. The size balances reuse for common
    callables without unbounded memory growth.

    """
    return inspect.signature(func)


def _get_signature(func: Callable) -> inspect.Signature:
    """Get function signature with caching when possible.

    Falls back to uncached lookup for unhashable callables.

    """
    try:
        return _get_signature_cached(func)
    except TypeError:
        return inspect.signature(func)


def _is_dataclass_target(cls: Any) -> bool:  # noqa: ANN401
    """Return True if *cls* is a dataclass class (not an instance).

    Used by :class:`~deprecate.proxy._DeprecatedProxy` to detect ``@dataclass`` targets that benefit from automatic
    ``attrs_mapping`` → ``args_mapping`` expansion.

    """
    import dataclasses

    return isinstance(cls, type) and dataclasses.is_dataclass(cls)


def _get_args_mapping_positional_only_keys(
    target_cls: Any,  # noqa: ANN401
    args_mapping: dict[str, Any],
) -> tuple[str, ...]:
    """Return ``args_mapping`` keys whose redirect target is a POSITIONAL_ONLY constructor param.

    When ``deprecated_class(args_mapping={"old": "new"}, ...)`` is applied to a target class whose
    constructor declares ``new`` as positional-only (``def __init__(self, new, /): ...``), calling
    the proxy with ``old=value`` would remap to ``new=value`` and then immediately raise
    ``TypeError`` because ``new`` cannot be passed as a keyword argument.

    This helper detects that mismatch at decoration time so the proxy can emit a ``UserWarning``
    and store the incompatible keys on :class:`~deprecate._types.DeprecationConfig` for audit
    surfacing.

    Args:
        target_cls: The resolved target class to inspect.
        args_mapping: The ``args_mapping`` dict being validated.

    Returns:
        Tuple of ``args_mapping`` old-key names whose remapped target name is positional-only in
        the target's constructor signature.  Empty tuple when no incompatibilities detected.

    """
    try:
        sig = inspect.signature(target_cls)
    except (TypeError, ValueError):
        return ()

    positional_only: set[str] = {
        name for name, p in sig.parameters.items() if p.kind is inspect.Parameter.POSITIONAL_ONLY
    }
    if not positional_only:
        return ()

    return tuple(old_key for old_key, mapping_val in args_mapping.items() if mapping_val in positional_only)


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
            stacklevel=2,
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


def void(*args: Any, **kwrgs: Any) -> Any:  # noqa: ANN401
    """Empty function that accepts any arguments and returns None.

    This helper function is used to silence IDE warnings about unused parameters in deprecated functions where the
    body is never executed (calls are forwarded to a target function). It's purely a convenience for developers.

    Args:
        *args: Any positional arguments (ignored).
        **kwrgs: Any keyword arguments (ignored).

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
    _, _ = args, kwrgs
