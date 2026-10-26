"""Deprecated module configured as fatal (``as_exception=True``) — Ft-14 fixture.

Every public attribute access raises :class:`~deprecate.DeprecatedError` instead of warning, including the
first one, since a fatal deprecation bypasses the warn budget.
"""

import deprecate

WIDGET_LIMIT = 7

deprecate.deprecated_module(__name__, deprecated_in="1.0", remove_in="2.0", as_exception=True)
