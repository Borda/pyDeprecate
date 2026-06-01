"""Project-wide pytest configuration.

This module provides an :func:`pytest.fixture` (``autouse=True``) that resets the mutable
``_WrapperState`` on every module-level deprecated wrapper exported from
:mod:`tests.collection_deprecate` before each test runs.

Why this matters
----------------

Module-level :func:`@deprecated <deprecate.deprecation.deprecated>` wrappers in
``tests/collection_deprecate.py`` are singletons whose warning counters (``warned_calls`` and
``warned_args``) persist across tests once that module is imported.  When a wrapper is
configured with ``num_warns=1`` (the default), the first test that exercises it exhausts the
budget; any subsequent test that asserts "no warning fired" via patterns like
``no_warning_call`` will silently pass for the wrong reason — the counter is spent, not the
underlying behaviour.

Two file-scoped ``autouse`` fixtures already cover the **async** and **generator** wrappers
in ``tests/integration/test_callable_kinds.py`` (see ``_reset_gen_state``, ``_reset_async_state``,
``_reset_async_gen_state``).  This conftest extends that reset to every other wrapper that
lives at the module level of :mod:`tests.collection_deprecate`, including plain synchronous
functions and ``deprecated_class`` proxies.

Reset semantics
---------------

Only the *counter-style* state is cleared:

* ``warned_calls`` → ``0``
* ``warned_args`` → empty :class:`dict`

The following are intentionally preserved:

* ``warned_misconfigured`` is **not** reset — it implements a one-time UserWarning per
  wrapper lifetime (see ``test_callable_kinds.py`` autouse-fixture docstrings).  Resetting
  it would falsify that contract.  Tests that assert misconfig warnings emit must explicitly
  reset ``_state.warned_misconfigured = False`` in their own setup — do not rely on fixture
  ordering.
* ``called`` is **not** reset — it counts every invocation (including suppressed ones) and
  is not used to gate warning emission.

What this fixture does *not* cover
-----------------------------------

Class-proxy state (``_ProxyConfig.warned``) on ``_DeprecatedProxy`` instances is **separate**
from ``_state`` and is a real cross-test leak surface for proxies configured with
``num_warns=1`` (e.g. ``_class_deprecation_enum``, ``_class_deprecation_dataclass``).  Those
proxies are reset by ``_ClassFormBase._reset_proxy_state`` in
``tests/integration/test_classes.py``.  New tests that use ``num_warns=1`` proxies from
``collection_deprecate`` must either inherit ``_ClassFormBase`` or add their own proxy-state
reset.

"""

from __future__ import annotations

import pytest


def _iter_wrapper_states(module: object) -> list[object]:
    """Return every module-level attribute of *module* that carries a ``_state`` attribute.

    Uses ``vars(module)`` (direct ``__dict__`` lookup) and checks for ``_state`` via the
    instance ``__dict__`` rather than ``hasattr``, so ``_DeprecatedProxy.__getattr__`` is
    never triggered.  This avoids emitting spurious ``FutureWarning``s and consuming
    ``num_warns`` budgets during fixture traversal.

    """
    found: list[object] = []
    for obj in vars(module).values():  # type: ignore[arg-type]
        if "_state" in getattr(obj, "__dict__", {}):
            found.append(obj)
    return found


@pytest.fixture(autouse=True)
def _reset_collection_deprecate_state() -> None:
    """Reset every shared module-level wrapper's warning counters before each test.

    Runs before every test function (autouse).  See module docstring for full motivation.
    The import is deferred to avoid loading ``tests.collection_deprecate`` at conftest
    parse time, which conflicts with ``--doctest-modules`` collection when pytest resolves
    ``src/`` imports from the installed package rather than from ``src/``.

    """
    import tests.collection_deprecate as _collection_deprecate

    for wrapper in _iter_wrapper_states(_collection_deprecate):
        state = wrapper._state  # type: ignore[attr-defined]
        # Reset only the counter-style state.  ``warned_misconfigured`` and ``called``
        # are intentionally NOT cleared — see module docstring.
        state.warned_calls = 0
        state.warned_args.clear()
