"""Project-wide pytest configuration.

This module provides an :func:`pytest.fixture` (``autouse=True``) that resets the mutable
``_WrapperState`` of every deprecated wrapper defined in the ``tests/collections/*.py`` modules before each test runs.

Why this matters
----------------

Module-level :func:`@deprecated <deprecate.deprecation.deprecated>` wrappers in
``tests/collections/*.py`` are singletons whose warning counters (``warned_calls`` and
``warned_args``) persist across tests once their module is imported.  When a wrapper is
configured with ``num_warns=1`` (the default), the first test that exercises it exhausts the
budget; any subsequent test that asserts "no warning fired" via patterns like
``no_warning_call`` will silently pass for the wrong reason — the counter is spent, not the
underlying behaviour.  A test that asserts a warning then fails or passes depending on which tests ran first.

The reset reaches every wrapper a collection module holds:

* module-level wrappers, synchronous, async and generator alike;
* every module-level ``_DeprecatedProxy`` (created by ``deprecated_class`` / ``deprecated_instance``), whose budget
  is the same ``_WrapperState`` kept in its config;
* deprecated methods, ``__init__`` and descriptor functions stored in the ``__dict__`` of a module-level class;
* every inner layer of a stacked wrapper, followed through ``__wrapped__``.

Reset semantics
---------------

Only the *counter-style* state is cleared:

* ``warned_calls`` → ``0``
* ``warned_args`` → empty :class:`dict`

The following are intentionally preserved:

* ``warned_misconfigured`` is **not** reset — it implements a one-time UserWarning per
  wrapper lifetime (see ``tests/unittests/test__dispatch.py``).  Resetting
  it would falsify that contract.  Tests that assert misconfig warnings **emit** must explicitly
  reset ``_state.warned_misconfigured = False`` in their own setup — do not rely on fixture
  ordering.  Symmetrically, tests that assert misconfig warnings are **not** emitted against a
  shared misconfigured wrapper depend silently on whether a prior test already consumed the
  one-time slot: ``assert_no_warnings(UserWarning)`` may pass because the counter is spent, not
  because the wrapper is correctly configured.  Both directions require explicit per-test setup.

What this fixture does *not* cover
-----------------------------------

Wrappers built by a factory call, defined in a test module, in the whole-module fixtures under
``tests/collections/modules/``, or in the package itself keep their counters across tests; a factory returns a
fresh wrapper per call, and tests using the others reset ``_state`` / ``_cfg.state`` themselves.

"""

from __future__ import annotations

import functools
import warnings
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from deprecate._types import _WrapperState


#: Every collection wrapper's state, gathered once: the collection modules and their objects never change.
_WRAPPER_STATES: list[_WrapperState] = []


def _own_state(obj: object) -> _WrapperState | None:
    """Return the warning state an object holds in its own ``__dict__``, without any attribute lookup on it.

    Reading the instance dictionary keeps a proxy's attribute hooks out of the way, so collecting states can neither
    warn nor spend a budget.

    """
    own = getattr(obj, "__dict__", {})
    state = own.get("_state")
    if state is None and "_DeprecatedProxy__config" in own:
        state = own["_DeprecatedProxy__config"].state
    return state


def _wrapper_layers(obj: object) -> list[object]:
    """Return ``obj`` and every inner layer of a stacked wrapper, following ``__wrapped__`` from its own dictionary."""
    layers: list[object] = []
    while obj is not None and all(obj is not layer for layer in layers):
        layers.append(obj)
        obj = getattr(obj, "__dict__", {}).get("__wrapped__")
    return layers


def _class_members(obj: object) -> list[object]:
    """Return the functions stored in a class's own ``__dict__``, unwrapping methods, properties and cached ones.

    ``type(obj)`` is used for the class check because a proxy can report a different ``__class__``; a proxy is never
    walked as a class, so its hooks are not triggered.

    """
    if not issubclass(type(obj), type) or "_DeprecatedProxy__config" in getattr(obj, "__dict__", {}):
        return []
    members: list[object] = []
    for member in vars(obj).values():
        if isinstance(member, (staticmethod, classmethod)):
            members.append(member.__func__)
        elif isinstance(member, functools.cached_property):
            members.append(member.func)
        elif isinstance(member, property):
            members.extend(fn for fn in (member.fget, member.fset, member.fdel) if fn is not None)
        else:
            members.append(member)
    return members


def _iter_wrapper_states() -> list[_WrapperState]:
    """Return the warning state of every wrapper the collection modules hold, each state once.

    Discover every ``tests/collections/*.py`` module, including modules unused by the selected tests, and visit its
    module-level objects, the members of its module-level classes, and every stacked layer of each. Shared imports and
    aliases can expose the same state repeatedly; deduplicate by identity. The result is computed once per session.

    """
    if _WRAPPER_STATES:
        return _WRAPPER_STATES
    found: dict[int, _WrapperState] = {}
    for path in sorted((Path(__file__).parent / "collections").glob("*.py")):
        if path.stem == "__init__":
            continue
        module = import_module(f"tests.collections.{path.stem}")
        for obj in list(vars(module).values()):
            for candidate in [obj, *_class_members(obj)]:
                for layer in _wrapper_layers(candidate):
                    state = _own_state(layer)
                    if state is not None:
                        found[id(state)] = state
    _WRAPPER_STATES.extend(found.values())
    return _WRAPPER_STATES


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
            "No generated docs example tests in tests/docs/: the documentation and README examples were not run."
            " Generate them (and tests/integration/test_readme.py) with `make docs-tests`."
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
