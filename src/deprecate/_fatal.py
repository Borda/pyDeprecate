"""Fatal-deprecation switch: the public :class:`DeprecatedError` plus the global ``AS_EXCEPTIONS`` resolution.

A deprecation can be promoted from a warning to a raised :class:`DeprecatedError`, either per wrapper
(``as_exception=True``) or process-wide (``deprecate.AS_EXCEPTIONS`` / the ``DEPRECATE_AS_EXCEPTIONS``
environment variable).  Python's own ``-W error::FutureWarning`` covers neither of the two cases this exists
for: it cannot distinguish pyDeprecate's warnings from any other library's ``FutureWarning``, and it has no
effect at all when the wrapper routes through a non-``warnings`` ``stream`` such as :func:`logging.warning`.

This module deliberately imports nothing from the rest of the package, so every emission path
(:mod:`deprecate.messaging`, :mod:`deprecate.proxy`, :mod:`deprecate.module`) can import it without a cycle.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import os
import sys
from typing import Optional

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
    consumer running with ``DEPRECATE_AS_EXCEPTIONS=1`` gets a strict mode that no upstream wrapper can opt
    out of.  ``None`` (the default) simply follows the global.

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
