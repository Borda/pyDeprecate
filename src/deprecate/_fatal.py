"""Fatal deprecations: :class:`DeprecatedError`, the global switch and the scoped :func:`as_exceptions`.

A deprecation can be promoted from a warning to a raised :class:`DeprecatedError`, either per wrapper
(``as_exception=True``) or process-wide (``deprecate.AS_EXCEPTIONS`` / the ``DEPRECATE_AS_EXCEPTIONS``
environment variable).  Python's own ``-W error::FutureWarning`` covers neither of the two cases this exists
for: it cannot distinguish pyDeprecate's warnings from any other library's ``FutureWarning``, and it has no
effect at all when the wrapper routes through a non-``warnings`` ``stream`` such as :func:`logging.warning`.

This module deliberately imports nothing from the rest of the package, so every emission path
(:mod:`deprecate.messaging`, :mod:`deprecate.proxy`, :mod:`deprecate.module`) can import it without a cycle.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import inspect
import os
import sys
from contextlib import ContextDecorator
from typing import Any, Callable, Optional, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])

#: Environment variable read once at import to seed ``deprecate.AS_EXCEPTIONS``.  The prefix matches the
#: package's other environment variable, ``DEPRECATE_DOCSTRING_STYLE``.
_ENV_VAR = "DEPRECATE_AS_EXCEPTIONS"

#: Values that turn the switch on, compared case-insensitively.  Anything else — including an unset or empty
#: variable and any unrecognised word — is off; a garbage value never raises at import.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


class DeprecatedError(RuntimeError):
    """Raised instead of a deprecation warning when a deprecation is configured as fatal.

    Derives from :class:`RuntimeError` so an existing ``except Exception`` still catches it and no caller can
    mistake it for a :class:`Warning` subclass.  The message is the same text the warning would have carried.

    Examples:
        >>> raise DeprecatedError("The `old_func` was deprecated since v1.0.")
        Traceback (most recent call last):
        ...
        deprecate._fatal.DeprecatedError: The `old_func` was deprecated since v1.0.
        >>> issubclass(DeprecatedError, Warning)
        False

    """


def _env_as_exceptions(environ: Optional[dict] = None) -> bool:
    """Read the initial global switch value from the environment.

    Args:
        environ: Mapping to read instead of :data:`os.environ`, for tests.

    Returns:
        ``True`` when the variable is set to a recognised truthy word, else ``False``.

    Examples:
        >>> _env_as_exceptions({"DEPRECATE_AS_EXCEPTIONS": "1"})
        True
        >>> _env_as_exceptions({"DEPRECATE_AS_EXCEPTIONS": "ON"})
        True
        >>> _env_as_exceptions({"DEPRECATE_AS_EXCEPTIONS": "maybe"})
        False
        >>> _env_as_exceptions({})
        False

    """
    env = os.environ if environ is None else environ
    return env.get(_ENV_VAR, "").strip().lower() in _TRUTHY


#: Import-time snapshot of the environment, used to seed ``deprecate.AS_EXCEPTIONS`` and as the fallback for
#: :func:`_global_as_exceptions` while the package's own ``__init__`` is still executing.
_ENV_DEFAULT = _env_as_exceptions()


def _global_as_exceptions() -> bool:
    """Return the current value of the ``deprecate.AS_EXCEPTIONS`` package attribute.

    Read through :data:`sys.modules` on every call rather than imported once, so that assigning
    ``deprecate.AS_EXCEPTIONS = True`` (or monkeypatching it in a test) takes effect immediately.

    Returns:
        The package attribute when it is a ``bool``, else the import-time environment snapshot.

    Examples:
        >>> isinstance(_global_as_exceptions(), bool)
        True

    """
    value = getattr(sys.modules.get("deprecate"), "AS_EXCEPTIONS", None)
    return value if isinstance(value, bool) else _ENV_DEFAULT


def _resolve_as_exception(as_exception: Optional[bool]) -> bool:
    """Resolve a wrapper's ``as_exception`` against the global switch — monotonically.

    An explicit ``True`` always makes the wrapper fatal, so an author can promote one symbol ahead of the
    rest.  An explicit ``False`` means "not fatal by default" and still yields to a ``True`` global, so a
    consumer running with ``DEPRECATE_AS_EXCEPTIONS=1`` gets a strict mode that no wrapper's own
    ``as_exception=False`` can opt out of (only the consumer's own :func:`as_exceptions` scope can).  ``None``
    (the default) simply follows the global.

    Args:
        as_exception: The wrapper's configured value, or ``None`` to defer to the global.

    Returns:
        Whether this deprecation must raise instead of warn.

    Examples:
        >>> _resolve_as_exception(True)
        True
        >>> import deprecate
        >>> _restore = deprecate.AS_EXCEPTIONS  # this doctest toggles the global; put it back afterwards
        >>> deprecate.AS_EXCEPTIONS = False
        >>> _resolve_as_exception(None), _resolve_as_exception(False)
        (False, False)
        >>> deprecate.AS_EXCEPTIONS = True
        >>> _resolve_as_exception(None), _resolve_as_exception(False)
        (True, True)
        >>> deprecate.AS_EXCEPTIONS = _restore

    """
    return as_exception is True or _global_as_exceptions()


class _ScopedSwitch(ContextDecorator):
    """Context manager and function decorator returned by :func:`as_exceptions`.

    A class rather than a ``@contextmanager`` generator so that decorating a function whose body would run after the
    scope has closed fails at decoration time instead of silently doing nothing.

    """

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled
        self._previous: list[Any] = []  # one saved value per open entry, so a reused instance unwinds in order

    def _recreate_cm(self) -> "_ScopedSwitch":
        # a fresh instance per decorated call: a recursive or concurrent call then saves and restores its own value
        return _ScopedSwitch(self._enabled)

    def __call__(self, func: _F) -> _F:
        if any(
            inspect.iscoroutinefunction(candidate)
            or inspect.isgeneratorfunction(candidate)
            or inspect.isasyncgenfunction(candidate)
            for candidate in (func, inspect.unwrap(func))
        ):
            name = getattr(func, "__qualname__", repr(func))
            raise TypeError(
                f"`as_exceptions` cannot decorate `{name}`: its body runs after the scope would have closed;"
                " use `with as_exceptions():` inside the body instead"
            )
        return super().__call__(func)

    def __enter__(self) -> None:
        package: Any = sys.modules["deprecate"]  # the package's own attribute; typed Any to set it without a cycle
        self._previous.append(getattr(package, "AS_EXCEPTIONS", _ENV_DEFAULT))
        package.AS_EXCEPTIONS = self._enabled

    def __exit__(self, *exc_info: object) -> None:
        package: Any = sys.modules["deprecate"]
        package.AS_EXCEPTIONS = self._previous.pop()


def as_exceptions(enabled: bool = True) -> _ScopedSwitch:
    """Make deprecations fatal for one block of code, then restore the previous global setting.

    Sets ``deprecate.AS_EXCEPTIONS`` to ``enabled`` on entry and puts back the value it found on exit, also when
    the block raises, so nested scopes compose.  ``as_exceptions(False)`` exempts a block from a strict run
    (e.g. one seeded by ``DEPRECATE_AS_EXCEPTIONS=1``).  Precedence stays monotonic: a wrapper's own
    ``as_exception=True`` raises inside an ``as_exceptions(False)`` block too.  Like the global itself, this is
    the consumer's switch (an application or a test suite), not something library code should set.

    Also usable as a decorator, but only on plain synchronous functions: an ``async def``, generator or
    async-generator function is rejected with a :class:`TypeError` at decoration time, because its body runs after
    the scope would have closed.  Use ``with as_exceptions():`` inside such a body instead.

    The switch is a plain package attribute, so the scope is process-wide — not local to a thread or an
    ``asyncio`` task.  Code running concurrently with the block sees the same setting, and scopes opened from
    different threads or tasks that overlap out of order can leave the switch in the wrong state for the rest of
    the process (for example ``True`` after both scopes have closed).  Do not hold a scope across an ``await`` in
    concurrent code.

    Args:
        enabled: Value of ``deprecate.AS_EXCEPTIONS`` inside the block.

    Returns:
        A scope usable in a ``with`` statement or as a decorator for a synchronous function.

    Raises:
        TypeError: If ``enabled`` is not a ``bool`` — raised by the call itself, so a bare ``@as_exceptions``
            (without parentheses) fails at decoration instead of silently returning a wrapper.

    Examples:
        >>> import deprecate
        >>> from deprecate import DeprecatedError, TargetMode, deprecated
        >>> @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0", num_warns=-1)
        ... def legacy_total(a: int, b: int) -> int:
        ...     return a + b
        >>> _restore = deprecate.AS_EXCEPTIONS  # this doctest assumes the global is off; put it back afterwards
        >>> deprecate.AS_EXCEPTIONS = False
        >>> with as_exceptions():
        ...     legacy_total(1, 2)
        Traceback (most recent call last):
        ...
        deprecate._fatal.DeprecatedError: The `legacy_total` was deprecated since v1.0. It will be removed in v2.0.
        >>> deprecate.AS_EXCEPTIONS
        False
        >>> as_exceptions("1")
        Traceback (most recent call last):
        ...
        TypeError: `enabled` must be a bool, got str
        >>> deprecate.AS_EXCEPTIONS = _restore

    """
    if not isinstance(enabled, bool):
        # the global ignores non-bool values, so accepting one would open a block that silently changes nothing
        hint = "; use `@as_exceptions()` with parentheses" if callable(enabled) else ""
        raise TypeError(f"`enabled` must be a bool, got {type(enabled).__name__}{hint}")
    return _ScopedSwitch(enabled)
