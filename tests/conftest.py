"""Project-wide pytest configuration.

This module provides an :func:`pytest.fixture` (``autouse=True``) that resets the mutable
``_WrapperState`` on every module-level deprecated wrapper exported from
the shared ``tests.collections.api_deprecated*`` modules before each test runs, excluding the legacy collection.

Why this matters
----------------

Module-level :func:`@deprecated <deprecate.deprecation.deprecated>` wrappers in
``tests/collections/api_deprecated*.py`` are singletons whose warning counters (``warned_calls`` and
``warned_args``) persist across tests once their module is imported.  When a wrapper is
configured with ``num_warns=1`` (the default), the first test that exercises it exhausts the
budget; any subsequent test that asserts "no warning fired" via patterns like
``no_warning_call`` will silently pass for the wrong reason — the counter is spent, not the
underlying behaviour.

Two file-scoped ``autouse`` fixtures already cover the **async** and **generator** wrappers
in ``tests/integration/test_callable_kinds.py`` (see ``_reset_gen_state``, ``_reset_async_state``,
``_reset_async_gen_state``).  This conftest extends that reset to every other wrapper that
lives at the module level of the ``tests.collections.api_deprecated*`` modules, including plain synchronous
functions, and every module-level ``_DeprecatedProxy`` (created by ``deprecated_class`` /
``deprecated_instance``), whose budget is the same ``_WrapperState`` kept in ``_cfg.state``.

Reset semantics
---------------

Only the *counter-style* state is cleared:

* ``warned_calls`` → ``0``
* ``warned_args`` → empty :class:`dict`

The following are intentionally preserved:

* ``warned_misconfigured`` is **not** reset — it implements a one-time UserWarning per
  wrapper lifetime (see ``test_callable_kinds.py`` autouse-fixture docstrings).  Resetting
  it would falsify that contract.  Tests that assert misconfig warnings **emit** must explicitly
  reset ``_state.warned_misconfigured = False`` in their own setup — do not rely on fixture
  ordering.  Symmetrically, tests that assert misconfig warnings are **not** emitted against a
  shared misconfigured wrapper depend silently on whether a prior test already consumed the
  one-time slot: ``assert_no_warnings(UserWarning)`` may pass because the counter is spent, not
  because the wrapper is correctly configured.  Both directions require explicit per-test setup.

What this fixture does *not* cover
-----------------------------------

Wrappers and proxies defined outside the shared ``tests.collections.api_deprecated*`` modules, including the legacy
collection, a test module, an unrelated collection module, or the package itself, keep
their counters across tests; tests using them reset ``_state`` / ``_cfg.state`` themselves.

"""

from __future__ import annotations

import warnings
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from deprecate._types import _WrapperState


def _iter_wrapper_states() -> list[_WrapperState]:
    """Return each module-level wrapper or proxy's warning state once across all sibling collections.

    Discover the shared ``api_deprecated*.py`` modules, including modules unused by the selected tests.
    Read each object's own dictionary so proxy attribute lookup cannot emit warnings or spend budgets.
    Shared imports and aliases can expose the same state repeatedly; deduplicate by identity.
    """
    found: dict[int, _WrapperState] = {}
    for path in sorted((Path(__file__).parent / "collections").glob("api_deprecated*.py")):
        # Legacy wrappers were not included before the move and retain their original state lifecycle.
        if path.stem == "api_deprecated_legacy":
            continue
        module = import_module(f"tests.collections.{path.stem}")
        for obj in vars(module).values():
            own = getattr(obj, "__dict__", {})
            state = own.get("_state")
            if state is None and "_DeprecatedProxy__config" in own:
                state = own["_DeprecatedProxy__config"].state
            if state is not None:
                found[id(state)] = state
    return list(found.values())


#: Fixture modules that must not be *directly collected* as a test module by ``--doctest-modules``.
#: ``fatal_utils`` is a whole-module deprecation with ``as_exception=True``: every non-underscore
#: attribute access raises ``DeprecatedError`` (the module's ``__getattribute__`` exempts only names
#: starting with ``_``, so ``__test__`` is already safe, but public ``pytestmark`` is not). Direct
#: collection makes pytest read ``pytestmark`` off the module itself, which raises and aborts
#: collection. Importing the module from a test file — as ``tests/integration/test_as_exception.py``
#: does at module scope — is unaffected, since pytest never probes an imported symbol's own attributes.
#: This is the fatal-module contract working as designed, not a workaround — any attribute-walking tool
#: (pytest's direct collection, Sphinx autodoc, pickle) hits the same wall, which ``docs/troubleshooting.md``
#: documents. The other fixture modules there are whole-module deprecations too: collecting them makes pytest's own
#: attribute probes (``pytest_plugins``, ``pytestmark``) emit their deprecation warnings at collection time. None
#: holds a doctest, so the whole directory is left out of collection.
collect_ignore_glob = ["collections/modules/*.py"]


#: Generated docs example tests (``make docs-tests``); gitignored, so a fresh clone has none.
_DOCS_TESTS_DIR = Path(__file__).parent / "docs"


def pytest_collection_finish(session: pytest.Session) -> None:
    """Warn when a run that covers ``tests/docs/`` finds no generated docs example tests there.

    The ``tests/docs/test_*.py`` files are generated from ``docs/**/*.md`` by ``make docs-tests`` and are not
    tracked in git. On a fresh clone the directory holds only its ``conftest.py``, so ``pytest .`` passes while
    every documentation example goes untested. The warning names the command that generates them; a run that
    targets other paths only (``pytest tests/unittests``) stays quiet.

    """
    if any(_DOCS_TESTS_DIR.glob("test_*.py")):
        return
    roots = [Path(str(arg).split("::")[0]).resolve() for arg in session.config.args]
    if not any(root == _DOCS_TESTS_DIR or root in _DOCS_TESTS_DIR.parents for root in roots):
        return
    warnings.warn(
        pytest.PytestWarning(
            "No generated docs example tests in tests/docs/: the documentation examples were not run."
            " Generate them with `make docs-tests`."
        ),
        stacklevel=1,
    )


@pytest.fixture(autouse=True)
def _reset_api_deprecated_state() -> None:
    """Reset every shared module-level wrapper's warning counters before each test.

    Runs before every test function (autouse).  See module docstring for full motivation.
    Collection imports are deferred to avoid loading ``tests.collections.api_deprecated*`` at conftest
    parse time, which conflicts with ``--doctest-modules`` collection when pytest resolves
    ``src/`` imports from the installed package rather than from ``src/``.

    """
    import deprecate.deprecation as _deprecation  # lazy: avoid poisoning sys.modules before src collection

    # Reset the one-time ``@deprecated``-on-class dispatch cache so per-qualname warnings are observable
    # in every test regardless of order (the cache is process-global; without this reset the first test to
    # decorate a given class qualname consumes its one-time warning and later same-qualname tests see none).
    _deprecation._CLASS_DISPATCH_NOTIFIED.clear()

    for state in _iter_wrapper_states():
        # Reset only the counter-style state.  ``warned_misconfigured`` is
        # intentionally NOT cleared — see module docstring.
        state.warned_calls = 0
        state.warned_args.clear()
