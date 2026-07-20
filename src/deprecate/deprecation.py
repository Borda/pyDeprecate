"""Backward-compatibility shim — the deprecation engine moved to sibling modules.

The public decorators are re-exported here so existing ``from deprecate.deprecation import deprecated`` imports (and
``deprecate.deprecation.deprecated`` cross-references) keep resolving. The real homes are now :mod:`deprecate.routine`
(function/method decorators + packing), :mod:`deprecate._dispatch` (target resolution + call-plan engine),
:mod:`deprecate.messaging` (warning templates + emitters), and :mod:`deprecate._properties` (property descriptors).

New code should import from those modules (or the top-level :mod:`deprecate` package). Internal helpers are
intentionally not re-exported here — import them from their real module.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

from deprecate.messaging import deprecation_warning as deprecation_warning
from deprecate.routine import deprecated as deprecated
from deprecate.routine import deprecated_callable as deprecated_callable
