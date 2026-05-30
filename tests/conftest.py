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
  it would falsify that contract.
* ``called`` is **not** reset — it counts every invocation (including suppressed ones) and
  is not used to gate warning emission.

"""

from __future__ import annotations

import pytest

import tests.collection_deprecate as _collection_deprecate


def _iter_wrapper_states() -> list[object]:
    """Return every module-level attribute of ``collection_deprecate`` carrying ``_state``.

    The decorator may attach ``_state`` either directly to the wrapper (functions, methods,
    classmethods that are stored as module-level names) or, in the case of class proxies,
    indirectly via :attr:`__deprecated__`.  The reset only needs to find objects that have
    a directly accessible ``_state`` — class-proxy state lives on the inner wrapper, which
    is not part of the cross-test leak surface.

    """
    found: list[object] = []
    for name in dir(_collection_deprecate):
        if name.startswith("__"):
            continue
        try:
            obj = getattr(_collection_deprecate, name)
        except AttributeError:
            continue
        if hasattr(obj, "_state"):
            found.append(obj)
    return found


@pytest.fixture(autouse=True)
def _reset_collection_deprecate_state() -> None:
    """Reset every shared module-level wrapper's warning counters before each test.

    Runs before every test function (autouse).  See module docstring for full motivation.

    """
    for wrapper in _iter_wrapper_states():
        state = wrapper._state  # type: ignore[attr-defined]
        # Reset only the counter-style state.  ``warned_misconfigured`` and ``called``
        # are intentionally NOT cleared — see module docstring.
        state.warned_calls = 0
        state.warned_args.clear()
