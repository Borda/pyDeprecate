"""Integration tests for generator and async callable kind support.

Covers four concerns:

- Round-trip equivalence under all three :class:`~deprecate.TargetMode` variants (NOTIFY, ARGS_REMAP, callable) for
  both sync generators (``yield``) and ``async def`` coroutines.
- Warning timing — the wrapper of a generator (async generator) function is itself a generator (async generator)
  function, so the warning fires on the first :func:`next` (``async for``) step, not when the generator object is
  created; for coroutines it fires when the coroutine is awaited, not when the coroutine object is created.
- Stacklevel — the warning must point to the user's call site, not to ``deprecation.py``.
- Generator protocol — ``send``/``throw``/``close`` and the return value (``asend``/``athrow``/``aclose`` for async
  generators) reach the inner generator, and pytest runs a deprecated ``yield`` fixture as one (``pytester``).
Also covers order-agnostic classmethod rescue: ``@deprecated`` applied OUTSIDE ``@classmethod`` is silently rescued
at decoration time via transparent unwrap + rewrap, producing a working deprecated classmethod descriptor without
emitting a warning.

"""

import asyncio
import gc
import inspect
import os
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractContextManager, nullcontext
from operator import methodcaller

import pytest

from deprecate import DeprecatedError, as_exceptions, deprecated, find_deprecation_wrappers
from tests._helpers import captured_warnings
from tests.collections.api_current.forwarding import walk_down
from tests.collections.api_deprecated import forwarding as api_deprecated_forwarding
from tests.collections.api_deprecated.core import decorated_sum_calls_inf, make_async_stacked_notify
from tests.collections.api_deprecated.forwarding import (
    MultiplesReader,
    async_args_remap,
    async_callable,
    async_gen_args_remap,
    async_gen_callable,
    async_gen_notify,
    async_gen_stacked,
    async_notify,
    deprecated_ask_once,
    deprecated_async_gen_to_coroutine,
    deprecated_async_parse_rows,
    deprecated_async_positional_only_to_sync,
    deprecated_async_running_total,
    deprecated_async_squares,
    deprecated_async_var_positional_to_sync,
    deprecated_countdown,
    deprecated_cursor,
    deprecated_fetch_page,
    deprecated_flush_jobs,
    deprecated_generator_based_double,
    deprecated_load_batches,
    deprecated_parse_rows,
    deprecated_running_total,
    deprecated_running_total_fatal,
    deprecated_squares,
    deprecated_sync_stopping,
    deprecated_walk,
    gen_args_remap,
    gen_callable,
    gen_notify,
    gen_notify_skipped,
    gen_notify_unlimited,
    gen_stacked,
    make_gen_skip_if_flag,
)

#: Loads the ``pytester`` fixture, which drives a deprecated ``yield`` fixture through a real pytest run.
pytest_plugins = ["pytester"]


def _deprecation_warnings(warned: list) -> list:
    """Return the deprecation warnings (``FutureWarning`` / ``DeprecationWarning``) among the captured ones."""
    return [w for w in warned if w.category in (FutureWarning, DeprecationWarning)]


async def _drain(agen: AsyncIterator[int]) -> list[int]:
    """Collect every item of ``agen``: one consumer coroutine for ``asyncio.gather`` to run as its own task."""
    return [item async for item in agen]


# Pair each wrapper with the argument it expects.  ``gen_args_remap`` is the only one with a
# non-default argument name because its source carries the legacy ``old_x`` parameter.
_WRAPPER_CASES = [
    pytest.param(gen_notify, {"x": 1}, id="notify"),
    pytest.param(gen_args_remap, {"old_x": 1}, id="args_remap"),
    pytest.param(gen_callable, {"x": 1}, id="callable"),
]


@pytest.mark.parametrize(("wrapper", "call_kwargs"), _WRAPPER_CASES)
def test_generator_round_trip(wrapper: object, call_kwargs: dict) -> None:
    """Wrapped generator yields the same values as the underlying ``gen_target`` (1×, 2×, 3×)."""
    with captured_warnings():
        result = list(wrapper(**call_kwargs))  # type: ignore[operator]
    assert result == [1, 2, 3]


@pytest.mark.parametrize(("wrapper", "call_kwargs"), _WRAPPER_CASES)
def test_generator_warning_fires_on_first_next(wrapper: object, call_kwargs: dict) -> None:
    """Warning fires on the first ``next()``, not when the generator object is created.

    The wrapper of a generator function is itself a generator function, so calling it only builds the generator
    object and runs no wrapper code; the deprecation logic runs when the consumer takes the first item.

    """
    with captured_warnings() as warned:
        gen = wrapper(**call_kwargs)  # type: ignore[operator]
        at_call = len(_deprecation_warnings(warned))
        first = next(gen)
    assert at_call == 0, "Creating the generator must not warn"
    assert len(_deprecation_warnings(warned)) == 1, "The first next() must warn once"
    assert first == 1


@pytest.mark.parametrize(("wrapper", "call_kwargs"), _WRAPPER_CASES)
def test_generator_warning_fires_once_per_call(wrapper: object, call_kwargs: dict) -> None:
    """Draining the wrapped generator emits the deprecation warning exactly once, on its first step.

    The wrapper runs the deprecation logic once before it delegates to the inner generator; every later step goes
    straight to the inner generator through ``yield from``, so no second warning is possible.

    """
    with captured_warnings() as warned:
        gen = wrapper(**call_kwargs)  # type: ignore[operator]
        next(gen)
        after_first = len(_deprecation_warnings(warned))
        _ = list(gen)
    assert after_first == 1, "The first step should warn once"
    assert len(_deprecation_warnings(warned)) == 1, "Later steps must not warn again"


def test_generator_num_warns_unlimited_warns_every_call() -> None:
    """num_warns=-1 wrapper emits exactly one warning per generator, on its first step; later steps emit none.

    Verifies that each generator created from a wrapper with ``num_warns=-1`` produces exactly one deprecation
    warning, on its first iteration step, and that later steps up to exhaustion do not re-emit.

    """
    with captured_warnings() as warned:
        _ = list(gen_notify_unlimited(x=1))
        first_call_warn_count = len([w for w in warned if w.category in (FutureWarning, DeprecationWarning)])
        _ = list(gen_notify_unlimited(x=2))
    dep_warns = [w for w in warned if w.category in (FutureWarning, DeprecationWarning)]
    assert first_call_warn_count == 1, f"Expected 1 warning after first call, got {first_call_warn_count}"
    assert len(dep_warns) == 2, f"Expected 2 warnings total (one per call), got {len(dep_warns)}"


@pytest.mark.parametrize(("wrapper", "call_kwargs"), _WRAPPER_CASES)
def test_generator_warning_stacklevel(wrapper: object, call_kwargs: dict) -> None:
    """Generator wrapper warning filename points to the iterating caller's file, not to ``deprecation.py``."""
    with captured_warnings() as warned:
        _ = list(wrapper(**call_kwargs))  # type: ignore[operator]
    dep_warns = [w for w in warned if w.category in (FutureWarning, DeprecationWarning)]
    # Single-warning invariant: one drained generator must emit exactly one deprecation
    # warning, not zero (silent regression) and not multiple (loop re-entry bug).
    assert len(dep_warns) == 1, f"Expected exactly 1 deprecation warning per call, got {len(dep_warns)}"
    w = dep_warns[0]
    assert w.filename.endswith("test_callable_kinds.py"), f"Expected caller file, got {w.filename}"


def test_sync_deprecated_warning_points_to_caller() -> None:
    """Synchronous ``@deprecated`` wrapper warning filename points to the caller's file.

    The async and generator paths already pin this via ``test_async_warning_stacklevel``,
    ``test_generator_warning_stacklevel``, and ``test_async_gen_warning_stacklevel``.  This
    test adds the same guarantee for the plain synchronous path — the most common callable
    shape — which previously relied on the ``_DEFAULT_STACKLEVEL_TO_CALLER = 4`` constant
    without explicit caller-file verification.

    ``decorated_sum_calls_inf`` is used because ``num_warns=-1`` makes its warning fire on
    every call: the assertion does not depend on autouse state reset.

    """
    with captured_warnings() as warned:
        decorated_sum_calls_inf(1, 2)
    dep_warns = [w for w in warned if w.category in (FutureWarning, DeprecationWarning)]
    # Single-warning invariant: one call must emit exactly one deprecation warning.
    # ``num_warns=-1`` on the wrapper makes the warning fire every call (no exhaustion);
    # any deviation from 1 signals a regression in the sync dispatch path.
    assert len(dep_warns) == 1, f"Expected exactly 1 deprecation warning per call, got {len(dep_warns)}"
    w = dep_warns[0]
    assert w.category is FutureWarning, f"Expected FutureWarning (project default), got {w.category!r}"
    assert w.filename.endswith("test_callable_kinds.py"), f"Expected caller file, got {w.filename}"


def test_wrong_order_classmethod_silently_rescued() -> None:
    """``@deprecated`` applied OUTSIDE ``@classmethod`` is silently rescued: no warning emitted.

    The descriptor is transparently unwrapped and re-wrapped as ``classmethod(deprecated_wrapper)``.  The result
    is a working deprecated classmethod — deprecation warning fires on call, not at decoration time.
    """
    with captured_warnings() as warned:

        class _Foo:
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            @classmethod
            def old_method(cls) -> None:
                pass

    decoration_warnings = [w for w in warned if issubclass(w.category, UserWarning)]
    msgs = [str(w.message) for w in decoration_warnings]
    assert not decoration_warnings, f"classmethod rescue must not warn at decoration time, got: {msgs}"
    # The descriptor must still be a classmethod after rescue.
    assert isinstance(_Foo.__dict__["old_method"], classmethod)
    # Deprecation warning fires at call time, not decoration time.
    with pytest.warns(FutureWarning):
        _Foo.old_method()


# ========== Async ``def`` wrapper integration tests ==========
# Async wrappers under test:
#   - ``async_notify``      — TargetMode.NOTIFY; source body runs unchanged.
#   - ``async_args_remap``  — TargetMode.ARGS_REMAP; legacy arg ``old_x`` is mapped to ``x`` before body executes.
#   - ``async_callable``    — callable target (``async_target``); source body never executes.
# Each call expects ``x=1`` (or ``old_x=1`` for args_remap) and produces ``2``.

_ASYNC_WRAPPER_CASES = [
    pytest.param(async_notify, {"x": 1}, id="notify"),
    pytest.param(async_args_remap, {"old_x": 1}, id="args_remap"),
    pytest.param(async_callable, {"x": 1}, id="callable"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("wrapper", "call_kwargs"), _ASYNC_WRAPPER_CASES)
async def test_async_round_trip(wrapper: object, call_kwargs: dict) -> None:
    """Wrapped async fn returns the same value as the underlying ``async_target`` (``x * 2``)."""
    with captured_warnings():
        result = await wrapper(**call_kwargs)  # type: ignore[operator]
    assert result == 2


@pytest.mark.asyncio
@pytest.mark.filterwarnings(r"ignore:'_async_source_notify' has duplicate @deprecated\(NOTIFY\) layers:UserWarning")
async def test_async_stacked_both_warnings_fire() -> None:
    """Two stacked ``@deprecated`` wrappers on ``async def`` each emit a warning when awaited."""
    fn = make_async_stacked_notify()
    with captured_warnings() as warned:
        result = await fn(x=1)
    dep_warns = [w for w in warned if w.category in (FutureWarning, DeprecationWarning)]
    assert len(dep_warns) == 2, f"Expected 2 warnings (one per stack layer), got {len(dep_warns)}"
    assert result == 2


@pytest.mark.asyncio
async def test_async_warning_fires_on_await_not_on_call() -> None:
    """Warning fires when coroutine is awaited, not when the wrapper is called.

    The async wrapper body runs lazily — calling the wrapper creates an unawaited coroutine
    with no side effects. The deprecation warning fires on the first ``await``.

    """
    with captured_warnings() as warned_before:
        coro = async_notify(x=1)
    pre_await_warnings = [w for w in warned_before if w.category in (FutureWarning, DeprecationWarning)]
    assert not pre_await_warnings, "Warning must not fire at call time — only fires on await"

    with pytest.warns((FutureWarning, DeprecationWarning), match="`_async_source_notify` was deprecated"):
        result = await coro
    assert result == 2


@pytest.mark.asyncio
async def test_async_warning_stacklevel() -> None:
    """Async wrapper warning filename points to the caller's file, not to ``deprecation.py``."""
    with captured_warnings() as warned:
        await async_notify(x=1)
    deprecation_warnings = [w for w in warned if w.category in (FutureWarning, DeprecationWarning)]
    assert deprecation_warnings, "Async wrapper must emit a deprecation warning"
    w = deprecation_warnings[0]
    assert w.filename.endswith("test_callable_kinds.py"), f"Expected caller file, got {w.filename}"


def test_async_callable_wrapper_is_coroutine_function() -> None:
    """The async-source wrapper itself must remain a coroutine function after decoration.

    ``functools.wraps`` preserves ``__wrapped__``, but ``inspect.iscoroutinefunction`` checks the wrapper's own
    ``CO_COROUTINE`` flag.  The dedicated async branch in ``packing()`` defines the wrapper with ``async def`` so
    callers and frameworks (e.g. ``asyncio.run``, FastAPI dependency injection) recognise it.

    """
    assert inspect.iscoroutinefunction(async_notify), "async_notify wrapper must be a coroutine function"
    assert inspect.iscoroutinefunction(async_args_remap), "async_args_remap wrapper must be a coroutine function"
    assert inspect.iscoroutinefunction(async_callable), "async_callable wrapper must be a coroutine function"


# ========== Async generator wrapper integration tests ==========
# Async generator wrappers under test:
#   - ``async_gen_notify``      — TargetMode.NOTIFY; source body (an async generator) runs unchanged.
#   - ``async_gen_args_remap``  — TargetMode.ARGS_REMAP; legacy arg ``old_x`` mapped to ``x`` before body iterates.
#   - ``async_gen_callable``    — callable target (``async_gen_target``); source body never executes.
# Each call expects ``x=1`` (or ``old_x=1`` for args_remap) and produces an async iterator yielding [1, 2, 3].
# The wrapper is itself an async generator function: calling it runs no wrapper code, the warning fires on the first
# ``async for`` step, and the wrapper then forwards every step to the inner async generator.

_ASYNC_GEN_WRAPPER_CASES = [
    pytest.param(async_gen_notify, {"x": 1}, id="notify"),
    pytest.param(async_gen_args_remap, {"old_x": 1}, id="args_remap"),
    pytest.param(async_gen_callable, {"x": 1}, id="callable"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("wrapper", "call_kwargs"), _ASYNC_GEN_WRAPPER_CASES)
async def test_async_gen_round_trip(wrapper: object, call_kwargs: dict) -> None:
    """Wrapped async generator yields the same values as ``async_gen_target`` (1×, 2×, 3×)."""
    with captured_warnings():
        agen = wrapper(**call_kwargs)  # type: ignore[operator]
        collected = [item async for item in agen]
    assert collected == [1, 2, 3]


@pytest.mark.asyncio
@pytest.mark.parametrize(("wrapper", "call_kwargs"), _ASYNC_GEN_WRAPPER_CASES)
async def test_async_gen_warning_fires_on_first_step(wrapper: object, call_kwargs: dict) -> None:
    """Warning fires on the first ``async for`` step, not when the async generator object is created.

    The wrapper of an async generator function is itself an async generator function, so calling it only builds the
    async generator object; the deprecation logic runs when the consumer awaits the first item.

    """
    with captured_warnings() as warned:
        agen = wrapper(**call_kwargs)  # type: ignore[operator]
        at_call = len(_deprecation_warnings(warned))
        first = await agen.__anext__()
        await agen.aclose()
    assert at_call == 0, "Creating the async generator must not warn"
    assert len(_deprecation_warnings(warned)) == 1, "The first step must warn once"
    assert first == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("wrapper", "call_kwargs"), _ASYNC_GEN_WRAPPER_CASES)
async def test_async_gen_warning_fires_once_per_call(wrapper: object, call_kwargs: dict) -> None:
    """Draining the wrapped async generator emits the deprecation warning exactly once, on its first step.

    Mirrors ``test_generator_warning_fires_once_per_call`` for sync generators: the wrapper runs the deprecation logic
    once before it starts forwarding, and every later step goes straight to the inner async generator.

    """
    with captured_warnings() as warned:
        agen = wrapper(**call_kwargs)  # type: ignore[operator]
        await agen.__anext__()
        after_first = len(_deprecation_warnings(warned))
        _ = [item async for item in agen]
    assert after_first == 1, "The first step should warn once"
    assert len(_deprecation_warnings(warned)) == 1, "Later steps must not warn again"


@pytest.mark.asyncio
@pytest.mark.parametrize(("wrapper", "call_kwargs"), _ASYNC_GEN_WRAPPER_CASES)
async def test_async_gen_warning_stacklevel(wrapper: object, call_kwargs: dict) -> None:
    """Async generator wrapper warning filename points to the caller's file, not to ``deprecation.py``."""
    with captured_warnings() as warned:
        agen = wrapper(**call_kwargs)  # type: ignore[operator]
        # Drain so the async generator is fully consumed and does not trigger async-generator cleanup warnings on GC.
        _ = [item async for item in agen]
    deprecation_warnings = [w for w in warned if w.category in (FutureWarning, DeprecationWarning)]
    assert deprecation_warnings, "Async generator wrapper must emit a deprecation warning"
    w = deprecation_warnings[0]
    assert w.filename.endswith("test_callable_kinds.py"), f"Expected caller file, got {w.filename}"


@pytest.mark.asyncio
async def test_async_args_remap_user_warning_emitted_when_both_old_and_new_provided() -> None:
    """Collision guard fires UserWarning when both old and new kwarg names are passed to an async ARGS_REMAP wrapper.

    ``async_args_remap`` wraps ``_async_source_remap`` with ``args_mapping={"old_x": "x"}`` and source body
    ``return x * 2``.  When called as ``await async_args_remap(old_x=5, x=6)`` the explicit ``x=6`` wins over the
    remapped ``old_x→x=5`` (new-name precedence), so the result is ``12``.  The collision guard must also emit a
    ``UserWarning`` naming the ignored argument, matching the behaviour of the ``@deprecated`` decorator path.

    """
    with captured_warnings() as warned:
        result = await async_args_remap(old_x=5, x=6)
    user_warns = [w for w in warned if w.category is UserWarning]
    assert user_warns, "UserWarning must fire when both old and new kwarg names are passed"
    assert "is ignored" in str(user_warns[0].message)
    assert result == 12


# ========== Async source forwarding to a SYNC callable target ==========
# An ``async def`` wrapper may forward to a *synchronous* callable target — legacy targets are
# not forced to be redeclared ``async def`` for callers to migrate.  The async dispatcher invokes
# such a target directly (no ``await``) via its non-coroutine branch.  The earlier async wrappers
# in this module all forward to *async* targets, so this branch — combined with the var-positional
# surplus-reorder and POSITIONAL_ONLY split shapes — was previously unexercised.
#   - ``deprecated_async_var_positional_to_sync`` — async ``*args`` source → sync ``var_positional_target``.
#   - ``deprecated_async_positional_only_to_sync`` — async source → sync target with a POSITIONAL_ONLY param.

_ASYNC_TO_SYNC_TARGET_CASES = [
    pytest.param(deprecated_async_var_positional_to_sync, (1, 2, 3), {}, 6, id="var_positional"),
    pytest.param(deprecated_async_positional_only_to_sync, (), {"x": 5, "y": 2}, 7, id="positional_only"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("wrapper", "call_args", "call_kwargs", "expected"), _ASYNC_TO_SYNC_TARGET_CASES)
async def test_async_source_forwards_to_sync_target(
    wrapper: object, call_args: tuple, call_kwargs: dict, expected: int
) -> None:
    """An async wrapper forwards correctly to a synchronous callable target and returns its result.

    A team migrating an ``async def`` entry point to a replacement often keeps the replacement
    synchronous — there is no reason to make ``var_positional_target`` or ``positional_only_target``
    a coroutine just because the deprecated shim awaits.  The async dispatcher must therefore call a
    non-coroutine target directly (without ``await``) while still applying the same argument-shaping
    it uses for async targets: the var-positional case reorders leading kwargs so the surplus tail
    ``(2, 3)`` reaches the target's ``*args`` (``1 + 2 + 3 == 6``), and the positional-only case
    splits ``x`` back out of the resolved kwargs so it binds positionally (``5 + 2 == 7``).

    """
    with pytest.warns((FutureWarning, DeprecationWarning), match="deprecated"):
        result = await wrapper(*call_args, **call_kwargs)  # type: ignore[operator]
    assert result == expected


# ========== Generator wrappers are real generators ==========
# The wrapper of a generator (async generator) function is a generator (async generator) function itself, so code
# that branches on ``inspect.isgeneratorfunction`` / ``inspect.isasyncgenfunction`` (pytest's fixture runner first)
# treats the deprecated function exactly like the original.  Its deprecation logic runs on the first step, then every
# protocol call is forwarded to the inner generator.

_SYNC_GENERATOR_WRAPPERS = [
    pytest.param(gen_notify, id="notify"),
    pytest.param(gen_args_remap, id="args_remap"),
    pytest.param(gen_callable, id="callable"),
    pytest.param(gen_notify_skipped, id="skip_if"),
    pytest.param(gen_stacked, id="stacked"),
    pytest.param(deprecated_squares, id="list-target"),
]

_ASYNC_GENERATOR_WRAPPERS = [
    pytest.param(async_gen_notify, id="notify"),
    pytest.param(async_gen_args_remap, id="args_remap"),
    pytest.param(async_gen_callable, id="callable"),
    pytest.param(async_gen_stacked, id="stacked"),
]

#: A deprecated sync ``yield`` fixture: pytest must run setup, hand over the yielded value and run the teardown.
_SYNC_YIELD_FIXTURE_PROJECT = """
import pytest

from tests.collections.api_deprecated.forwarding import legacy_db_session

db_session = pytest.fixture(name="db_session")(legacy_db_session)


def test_uses_session(db_session):
    assert db_session == "session"
"""

#: Configuration of the ``pytester`` project: the fixture loop scope pytest-asyncio asks every project to set.
_YIELD_FIXTURE_PROJECT_INI = """
[pytest]
asyncio_default_fixture_loop_scope = function
"""

#: The async twin: pytest-asyncio must pick its async-generator fixture runner for the deprecated fixture.
_ASYNC_YIELD_FIXTURE_PROJECT = """
import pytest
import pytest_asyncio

from tests.collections.api_deprecated.forwarding import legacy_async_db_session

async_db_session = pytest_asyncio.fixture(name="async_db_session")(legacy_async_db_session)


@pytest.mark.asyncio
async def test_uses_session(async_db_session):
    assert async_db_session == "async session"
"""


@pytest.mark.parametrize(
    ("project", "teardown_marker"),
    [
        pytest.param(_SYNC_YIELD_FIXTURE_PROJECT, "legacy db session closed", id="sync"),
        pytest.param(_ASYNC_YIELD_FIXTURE_PROJECT, "legacy async db session closed", id="async"),
    ],
)
def test_deprecated_yield_fixture_runs_setup_value_and_teardown(
    pytester: pytest.Pytester, project: str, teardown_marker: str
) -> None:
    """A deprecated ``yield`` fixture hands its yielded value to the test, runs its teardown and still warns.

    A suite deprecates a shared ``yield`` fixture and keeps registering it with ``pytest.fixture`` (or
    ``pytest_asyncio.fixture``). pytest picks the fixture runner from ``inspect.isgeneratorfunction`` /
    ``inspect.isasyncgenfunction``; a wrapper that lost the flag made pytest pass the generator object itself as the
    fixture value and skip the teardown entirely. The run must pass, print the teardown marker and report the
    deprecation warning.

    """
    # pytest-asyncio warns when a project leaves its fixture loop scope unset; the inner run must not leak that warning
    pytester.makeini(_YIELD_FIXTURE_PROJECT_INI)
    pytester.makepyfile(test_fixture_project=project)
    result = pytester.runpytest("-s", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines([f"*{teardown_marker}*", "*FutureWarning*was deprecated since v1.0*"])


@pytest.mark.parametrize("wrapper", _SYNC_GENERATOR_WRAPPERS)
def test_generator_wrapper_is_a_generator_function(wrapper: object) -> None:
    """The wrapper of a generator function keeps ``inspect.isgeneratorfunction`` and is not a coroutine function.

    Frameworks such as pytest, Click and contextlib-style helpers decide how to run a callable from these flags, so a
    deprecated generator must report exactly what the original reported.

    """
    assert inspect.isgeneratorfunction(wrapper)
    assert not inspect.iscoroutinefunction(wrapper)
    assert not inspect.isasyncgenfunction(wrapper)


@pytest.mark.parametrize("wrapper", _ASYNC_GENERATOR_WRAPPERS)
def test_async_generator_wrapper_is_an_async_generator_function(wrapper: object) -> None:
    """The wrapper of an async generator function keeps ``inspect.isasyncgenfunction`` and is not a coroutine function.

    pytest-asyncio and other async frameworks run an async ``yield`` callable only when this flag is set, so the
    deprecated function must keep it.

    """
    assert inspect.isasyncgenfunction(wrapper)
    assert not inspect.iscoroutinefunction(wrapper)
    assert not inspect.isgeneratorfunction(wrapper)


def test_generator_never_iterated_never_warns_nor_spends_the_budget() -> None:
    """A generator that is created and dropped without a step neither warns nor uses the ``num_warns=1`` budget.

    A caller builds a deprecated generator but drops it before reading anything: no deprecated code ran, so nothing
    warns, and the single-warning budget is still available for the first generator that is actually iterated.

    """
    with captured_warnings() as warned:
        unused = gen_notify(x=1)
        del unused
        before_iteration = len(_deprecation_warnings(warned))
        items = list(gen_notify(x=1))
    assert before_iteration == 0
    assert len(_deprecation_warnings(warned)) == 1, "The first iterated generator must still get the warning"
    assert items == [1, 2, 3]


@pytest.mark.parametrize(
    ("action", "expectation"),
    [
        pytest.param(methodcaller("close"), nullcontext(), id="close"),
        pytest.param(
            methodcaller("throw", ValueError("abort before start")),
            pytest.raises(ValueError, match="abort before start"),
            id="throw",
        ),
    ],
)
def test_generator_closed_or_thrown_into_before_its_first_step_never_starts(
    action: Callable[[object], object], expectation: AbstractContextManager[object]
) -> None:
    """``close()`` or ``throw()`` before the first step neither warns nor runs the target body.

    A consumer creates a deprecated generator and cancels it before reading anything. As for any unstarted generator,
    ``close()`` finishes silently and ``throw()`` re-raises the thrown exception at the caller. No deprecated code ran,
    so no warning fires, the target's ``finally`` never logs, and the wrapper is finished afterwards.

    """
    log: list[str] = []
    with captured_warnings() as warned:
        gen = deprecated_running_total(1, log)
        with expectation:
            action(gen)
    assert not _deprecation_warnings(warned)
    assert log == []
    assert inspect.getgeneratorstate(gen) == inspect.GEN_CLOSED


def test_generator_forwards_send_throw_and_return_value() -> None:
    """``send``, ``throw`` and the return value pass through the wrapper to the target generator unchanged.

    A coroutine-style consumer drives a deprecated running-total generator: sent values must reach the target, a
    thrown ``ValueError`` must be handled by the target (which resets the total), and the target's return value must
    arrive as the ``StopIteration.value`` of the wrapper, exactly as ``yield from`` delegation promises.

    """
    log: list[str] = []
    with captured_warnings():
        gen = deprecated_running_total(1, log)
        seen = [next(gen), gen.send(2), gen.send(3), gen.throw(ValueError()), gen.send(5)]
        with pytest.raises(StopIteration) as stop:
            next(gen)
    assert seen == [1, 3, 6, 0, 5]
    assert stop.value.value == 5
    assert log == ["closed"]


def test_generator_close_after_early_break_reaches_the_inner_generator() -> None:
    """Breaking out of a loop and closing the wrapper closes the target generator, so its cleanup runs.

    A consumer reads the first item and breaks; when the wrapper is closed (explicitly or by garbage collection),
    ``GeneratorExit`` must reach the target generator so that its ``finally`` block releases the resource.

    """
    log: list[str] = []
    with captured_warnings():
        gen = deprecated_running_total(1, log)
        for _ in gen:
            break
        gen.close()
    assert log == ["closed"]


def test_generator_throw_into_a_target_without_throw_reraises_at_the_caller() -> None:
    """An exception thrown in while the first item is held re-raises at the caller when the target has no ``throw``.

    A deprecated generator now forwards to a replacement that returns a plain list. A consumer reads the first item
    and then throws an exception in to abort; a list iterator has no ``throw`` to handle it, so the exception must come
    back out of the consumer's ``throw`` call, as ``yield from`` does, and the wrapper is finished afterwards.

    """
    with captured_warnings():
        gen = deprecated_squares(3)
        next(gen)
        with pytest.raises(ValueError, match="abort the read"):
            gen.throw(ValueError("abort the read"))
    assert inspect.getgeneratorstate(gen) == inspect.GEN_CLOSED


def test_generator_close_on_a_target_without_close_finishes_the_wrapper() -> None:
    """Closing the wrapper while the first item is held succeeds when the target has no ``close``.

    A consumer of a deprecated generator whose replacement returns a plain list reads one item and closes the
    generator. A list iterator has nothing to close, so ``close()`` must still complete without error and leave the
    wrapper closed, instead of failing on the missing method.

    """
    with captured_warnings():
        gen = deprecated_squares(3)
        next(gen)
        gen.close()
    assert inspect.getgeneratorstate(gen) == inspect.GEN_CLOSED


@pytest.mark.parametrize(
    ("resume", "expected_return"),
    [
        pytest.param(methodcaller("send", "yes"), "yes", id="send"),
        pytest.param(methodcaller("throw", ValueError("abort")), "cancelled", id="throw"),
    ],
)
def test_generator_ends_when_the_target_returns_on_a_held_item_resumption(
    resume: Callable[[object], object], expected_return: str
) -> None:
    """A target that returns in reply to a sent value or a thrown exception ends the wrapper with its return value.

    A deprecated prompt generator yields its question once and then finishes, returning the reply sent in or
    ``"cancelled"`` when the caller throws ``ValueError`` in. The wrapper holds the first item and forwards that
    resumption by hand, so the replacement's return must still end the wrapper with ``StopIteration`` whose ``value``
    is the replacement's return value.

    """
    with captured_warnings():
        gen = deprecated_ask_once("Proceed?")
        next(gen)
        with pytest.raises(StopIteration) as stop:
            resume(gen)
    assert stop.value.value == expected_return


def test_two_suspended_generators_of_one_wrapper_raise_no_false_cycle() -> None:
    """Two live generators of one callable-target wrapper on one thread do not trip the target-chain cycle guard.

    The cycle guard is released before the wrapper delegates, so a generator suspended at a ``yield`` holds nothing:
    a consumer that interleaves two running totals (a merge, a zip) must not see a "Circular deprecation" error.

    """
    first_log: list[str] = []
    second_log: list[str] = []
    with captured_warnings():
        first = deprecated_running_total(1, first_log)
        second = deprecated_running_total(10, second_log)
        merged = [next(first), next(second), first.send(1), second.send(1)]
        first.close()
        second.close()
    assert merged == [1, 10, 2, 11]


def test_generator_with_list_target_yields_the_items() -> None:
    """A generator source forwarded to a target that returns a list yields the list's items.

    The replacement of a deprecated generator may return a list instead; the wrapper stays a generator function, so
    callers that iterate keep working, while the call itself now returns a generator rather than the list.

    """
    with captured_warnings() as warned:
        result = deprecated_squares(3)
        items = list(result)
    assert inspect.isgenerator(result)
    assert items == [1, 4, 9]
    assert len(_deprecation_warnings(warned)) == 1


@pytest.mark.parametrize(
    ("wrapper", "call_args", "expected_return"),
    [
        pytest.param(deprecated_squares, (0,), None, id="empty-list-target"),
        pytest.param(deprecated_flush_jobs, ("emails",), "emails: no pending jobs", id="returns-without-yield"),
    ],
)
def test_generator_ends_on_its_first_step_when_the_target_has_no_items(
    wrapper: object, call_args: tuple, expected_return: object
) -> None:
    """A target with no items warns once on the first ``next()`` and ends the wrapper there with its return value.

    The replacement of a deprecated generator may have nothing to hand out: it returns an empty list, or it is a
    generator that returns a summary before its first ``yield``. The first step still runs the deprecation logic, so
    it warns, and then the wrapper stops at once, carrying the replacement's return value as ``StopIteration.value``
    (``None`` for a list). A ``skip_if`` call is not a case here: it delegates to the source with ``yield from`` and
    never reaches this first-step end.

    """
    with captured_warnings() as warned:
        gen = wrapper(*call_args)  # type: ignore[operator]
        with pytest.raises(StopIteration) as stop:
            next(gen)
    assert stop.value.value == expected_return
    assert len(_deprecation_warnings(warned)) == 1


def test_generator_frees_the_first_item_once_the_consumer_moves_on() -> None:
    """The wrapper keeps no reference to its first item once the consumer has taken the second one.

    A deprecated data loader yields large batches, and its consumer processes them one at a time, expecting each batch
    to be freed as soon as it moves on. The wrapper takes the first item from the replacement before it yields it, so
    it must drop its own hold on that item instead of keeping the first batch alive until the stream ends.

    """
    with captured_warnings():
        batches = deprecated_load_batches(3)
        first_ref = weakref.ref(next(batches))
        second = next(batches)
        gc.collect()
    assert first_ref() is None, "The wrapper must not keep the first batch alive after the second one is taken"
    assert second.index == 1


def test_generator_recursing_through_its_deprecated_name_raises_cycle_error() -> None:
    """A target that recurses through the deprecated generator before its first ``yield`` raises the cycle error.

    The cycle guard stays held through a generator wrapper's first step, so a cycle of generator wrappers is caught
    instead of nesting until ``RecursionError``. Recursion through the deprecated name during that step looks the same
    and raises ``RuntimeError``, as a plain deprecated function recursing through its own name does; recursing
    through the replacement's own name is the fix.

    """
    with captured_warnings(), pytest.raises(RuntimeError, match="Circular deprecation cycle detected"):
        list(deprecated_walk(2, deprecated_walk))


@pytest.mark.parametrize(
    ("wrapper", "failing_args", "error", "error_match", "healthy_args", "expected_items"),
    [
        pytest.param(
            deprecated_parse_rows,
            (["1", "x"],),
            ValueError,
            "is not an integer",
            (["1", "2"],),
            [1, 2],
            id="target-raises-before-yield",
        ),
        pytest.param(
            deprecated_walk,
            (2, deprecated_walk),
            RuntimeError,
            "Circular deprecation cycle detected",
            (2, walk_down),
            [0, 1, 2],
            id="cycle-error",
        ),
    ],
)
def test_generator_first_step_failure_releases_the_cycle_guard(
    wrapper: object,
    failing_args: tuple,
    error: type[Exception],
    error_match: str,
    healthy_args: tuple,
    expected_items: list[int],
) -> None:
    """A first step that raises releases the cycle guard, so the next call of the same wrapper works and warns.

    The guard is held while the wrapper creates the inner generator and takes its first item. When that step fails,
    because the replacement rejects its input or because the call recursed through the deprecated name, the guard
    must still be released: a consumer that retries with good input on the same thread must get the items and the
    one deprecation warning, not a spurious "Circular deprecation" error left over from the failed call.

    """
    with captured_warnings(), pytest.raises(error, match=error_match):
        next(wrapper(*failing_args))  # type: ignore[operator]
    with captured_warnings() as warned:
        items = list(wrapper(*healthy_args))  # type: ignore[operator]
    assert items == expected_items
    assert len(_deprecation_warnings(warned)) == 1


def test_generator_skip_if_runs_the_source_without_warning() -> None:
    """``skip_if=True`` on a generator wrapper runs the source generator unchanged and never warns.

    A library keeps an old generator API working for one platform while ``skip_if`` turns the deprecation off there.
    The skipped path must iterate the source body itself, with no warning, as the function wrapper does for a call.

    """
    with captured_warnings() as warned:
        items = list(gen_notify_skipped(x=2))
    assert items == [2, 4, 6]
    assert not _deprecation_warnings(warned)


@pytest.mark.parametrize(
    ("skip_at_creation", "skip_at_first_step", "expected_items", "expected_warns"),
    [
        pytest.param(True, False, [1, 2, 3], 1, id="skip-lifted-before-first-step"),
        pytest.param(False, True, [], 0, id="skip-set-before-first-step"),
    ],
)
def test_generator_skip_if_is_read_on_the_first_step(
    skip_at_creation: bool, skip_at_first_step: bool, expected_items: list[int], expected_warns: int
) -> None:
    """A callable ``skip_if`` flipped between creating the generator and its first step: the first-step value decides.

    A feature flag behind ``skip_if`` changes after the consumer created the deprecated generator but before it reads
    anything. The wrapper evaluates ``skip_if`` on the first step, so lifting the skip makes it forward and warn, while
    setting the skip runs the placeholder source, which yields nothing, without a warning.

    """
    wrapper, flag = make_gen_skip_if_flag()
    flag["skip"] = skip_at_creation
    with captured_warnings() as warned:
        gen = wrapper(x=1)
        flag["skip"] = skip_at_first_step
        items = list(gen)
    assert items == expected_items
    assert len(_deprecation_warnings(warned)) == expected_warns


def test_generator_method_warns_once_on_first_step_and_yields_the_target_values() -> None:
    """A deprecated generator method forwards through ``self`` and warns once, on its first step.

    A class renamed its generator method and keeps the old name forwarding to the new one. Calling the old method on an
    instance only creates the generator; its first step warns once and the items come from the replacement method
    bound to the same instance.

    """
    reader = MultiplesReader(base=2)
    with captured_warnings() as warned:
        gen = reader.read_multiples()
        at_call = len(_deprecation_warnings(warned))
        items = list(gen)
    assert at_call == 0
    assert len(_deprecation_warnings(warned)) == 1
    assert items == [2, 4, 6]


def test_stacked_generator_wrappers_warn_per_layer_on_first_step() -> None:
    """A NOTIFY layer over a callable-target generator wrapper warns once per layer, on the first step only.

    Both layers are generator functions: calling the outer one runs no code, its first step runs the outer
    deprecation logic and then delegates to the inner wrapper, whose own first step warns before it forwards.

    """
    with captured_warnings() as warned:
        gen = gen_stacked(x=1)
        at_call = len(_deprecation_warnings(warned))
        items = list(gen)
    dep_warns = _deprecation_warnings(warned)
    assert at_call == 0
    assert len(dep_warns) == 2
    assert {os.path.basename(w.filename) for w in dep_warns} == {"test_callable_kinds.py"}, "Both must blame the caller"
    assert items == [1, 2, 3]


def test_generator_as_exceptions_raises_on_first_step() -> None:
    """Inside ``as_exceptions()`` creating a deprecated generator succeeds and the first step raises.

    A strict test run makes deprecations fatal; with a generator wrapper the deprecated code starts on the first
    ``next()``, so that is where ``DeprecatedError`` surfaces, and the generator is finished afterwards.

    """
    with as_exceptions():
        gen = gen_notify_unlimited(x=1)
        with pytest.raises(DeprecatedError):
            next(gen)
    with pytest.raises(StopIteration):
        next(gen)


def test_generator_with_as_exception_raises_on_first_step_without_running_the_target() -> None:
    """A generator wrapper with ``as_exception=True`` raises on its first step, and the target body never runs.

    A library marks an old generator API fatal. Creating the generator still succeeds, because the call runs no code;
    the first ``next()`` raises ``DeprecatedError`` before the target starts, so the target's ``finally`` never logs,
    and the generator is finished afterwards.

    """
    log: list[str] = []
    gen = deprecated_running_total_fatal(1, log)
    with pytest.raises(DeprecatedError):
        next(gen)
    gc.collect()
    assert log == []
    assert inspect.getgeneratorstate(gen) == inspect.GEN_CLOSED


@pytest.mark.parametrize("name", ["deprecated_running_total", "deprecated_async_running_total"])
def test_audit_scan_finds_generator_wrappers(name: str) -> None:
    """The audit scan still finds generator and async generator wrappers and reads their metadata.

    ``find_deprecation_wrappers`` relies on ``__deprecation_config__`` and ``__wrapped__``, which the generator
    wrappers carry like every other ``@deprecated`` wrapper.

    """
    found = {info.function: info for info in find_deprecation_wrappers(api_deprecated_forwarding)}
    assert name in found
    assert found[name].deprecated_info.deprecated_in == "1.0"


@pytest.mark.asyncio
async def test_async_generator_forwards_asend_athrow_and_aclose() -> None:
    """``asend``, ``athrow`` and ``aclose`` pass through the wrapper to the target async generator.

    An async consumer drives a deprecated running-total stream: sent values must reach the target, a thrown
    ``ValueError`` must be handled by the target (which resets the total) and ``aclose`` must run its cleanup.

    """
    log: list[str] = []
    with captured_warnings() as warned:
        agen = deprecated_async_running_total(1, log)
        seen = [await agen.__anext__(), await agen.asend(2), await agen.athrow(ValueError()), await agen.asend(4)]
        await agen.aclose()
    assert seen == [1, 3, 0, 4]
    assert log == ["closed"]
    assert len(_deprecation_warnings(warned)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expectation"),
    [
        pytest.param(methodcaller("aclose"), nullcontext(), id="aclose"),
        pytest.param(
            methodcaller("athrow", ValueError("abort before start")),
            pytest.raises(ValueError, match="abort before start"),
            id="athrow",
        ),
    ],
)
async def test_async_generator_closed_or_thrown_into_before_its_first_step_never_starts(
    action: Callable[[object], Awaitable[object]], expectation: AbstractContextManager[object]
) -> None:
    """``aclose()`` or ``athrow()`` before the first step neither warns nor runs the target body.

    An async consumer creates a deprecated stream and cancels it before reading anything. As for any unstarted async
    generator, ``aclose()`` finishes silently and ``athrow()`` re-raises the thrown exception at the caller. No
    deprecated code ran, so no warning fires and the target's ``finally`` never logs.

    """
    log: list[str] = []
    with captured_warnings() as warned:
        agen = deprecated_async_running_total(1, log)
        with expectation:
            await action(agen)
    assert not _deprecation_warnings(warned)
    assert log == []


@pytest.mark.asyncio
async def test_async_generator_never_iterated_never_warns_nor_spends_the_budget() -> None:
    """An async generator created and dropped without a step neither warns nor uses the ``num_warns=1`` budget.

    Async twin of the sync case: a caller builds a deprecated async generator but drops it before any ``async for``
    step, so nothing warns, and the single-warning budget is still available for the first one actually iterated.

    """
    with captured_warnings() as warned:
        unused = async_gen_notify(x=1)
        del unused
        before_iteration = len(_deprecation_warnings(warned))
        items = await _drain(async_gen_notify(x=1))
    assert before_iteration == 0
    assert len(_deprecation_warnings(warned)) == 1, "The first iterated async generator must still get the warning"
    assert items == [1, 2, 3]


@pytest.mark.asyncio
async def test_async_generator_method_warns_once_on_first_step_and_yields_the_target_values() -> None:
    """A deprecated async generator method forwards through ``self`` and warns once, on its first step.

    Async twin of the generator method case: calling the old async generator method on an instance only creates the
    async generator; its first ``async for`` step warns once and the items come from the replacement method bound to
    the same instance.

    """
    reader = MultiplesReader(base=2)
    with captured_warnings() as warned:
        agen = reader.aread_multiples()
        at_call = len(_deprecation_warnings(warned))
        items = await _drain(agen)
    assert at_call == 0
    assert len(_deprecation_warnings(warned)) == 1
    assert items == [2, 4, 6]


@pytest.mark.asyncio
async def test_async_generator_forwards_to_non_native_async_generator() -> None:
    """``asend``, ``athrow`` and ``aclose`` reach a target object that implements them without being an async generator.

    A database driver's replacement API may return a cursor class with the full async generator interface. Values the
    consumer sends must reach it instead of being replaced by ``None``, and closing the wrapper must close the cursor
    so its resources are released.

    """
    log: list[object] = []
    with captured_warnings():
        agen = deprecated_cursor(log)
        seen = [await agen.__anext__(), await agen.asend(5), await agen.athrow(ValueError()), await agen.asend(2)]
        await agen.aclose()
    assert seen == [0, 5, 0, 2]
    assert log == [("send", None), ("send", 5), ("throw", "ValueError"), ("send", 2), "close"]


@pytest.mark.asyncio
async def test_async_generator_athrow_into_a_target_without_athrow_reraises_at_the_caller() -> None:
    """An exception thrown in while the first item is held re-raises at the caller when the target has no ``athrow``.

    A deprecated stream now forwards to a replacement that returns a plain async iterator. A consumer awaits the
    first item and then throws an exception in to abort; the iterator has no ``athrow`` to handle it, so the exception
    must come back out of the consumer's ``athrow`` call and the wrapper is finished afterwards.

    """
    with captured_warnings():
        agen = deprecated_countdown(3)
        await agen.__anext__()
        with pytest.raises(ValueError, match="abort the countdown"):
            await agen.athrow(ValueError("abort the countdown"))
    with pytest.raises(StopAsyncIteration):
        await agen.__anext__()


@pytest.mark.asyncio
async def test_async_generator_aclose_on_a_target_without_aclose_finishes_the_wrapper() -> None:
    """Closing the wrapper while the first item is held succeeds when the target has no ``aclose``.

    A consumer of a deprecated stream whose replacement returns a plain async iterator awaits one item and closes the
    stream. The iterator has nothing to close, so ``aclose()`` must still complete without error and leave the
    wrapper finished, instead of failing on the missing method.

    """
    with captured_warnings():
        agen = deprecated_countdown(3)
        await agen.__anext__()
        await agen.aclose()
    with pytest.raises(StopAsyncIteration):
        await agen.__anext__()


@pytest.mark.asyncio
async def test_async_generator_with_async_iterator_target_yields_its_items() -> None:
    """An async generator source forwarded to a target returning a plain async iterator streams that iterator's items.

    The replacement of a deprecated async generator may be a function returning an async iterator object rather than
    an async generator; the wrapper stays an async generator function and drives the iterator with ``async for``.

    """
    with captured_warnings() as warned:
        items = [item async for item in deprecated_countdown(3)]
    assert inspect.isasyncgenfunction(deprecated_countdown)
    assert items == [3, 2, 1]
    assert len(_deprecation_warnings(warned)) == 1


@pytest.mark.asyncio
async def test_async_generator_ends_on_its_first_step_when_the_target_has_no_items() -> None:
    """An async target with no items warns once on the first step and ends the wrapper there.

    The async twin of the sync case: the replacement of a deprecated stream returns an async iterator that is empty
    from the start. The first step still runs the deprecation logic, so it warns, and then the wrapper ends with
    ``StopAsyncIteration``. An async generator cannot return a value, so there is no return-without-yield case.

    """
    with captured_warnings() as warned:
        agen = deprecated_countdown(0)
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()
    assert len(_deprecation_warnings(warned)) == 1


@pytest.mark.asyncio
async def test_async_generator_ends_when_the_target_ends() -> None:
    """A plain step that makes the target async generator finish ends the wrapper with ``StopAsyncIteration``.

    An async stream consumer drives the deprecated generator by hand with ``__anext__`` until it is exhausted. The
    wrapper must end exactly when the replacement ends, so the consumer's ``StopAsyncIteration`` handling still runs.

    """
    log: list[str] = []
    with captured_warnings():
        agen = deprecated_async_running_total(1, log)
        first = await agen.__anext__()
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()
    assert first == 1
    assert log == ["closed"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("count", "expected"), [pytest.param(0, [], id="empty"), pytest.param(2, [1, 2], id="exhausted-after-items")]
)
async def test_async_generator_ends_when_the_target_stops_synchronously(count: int, expected: list[int]) -> None:
    """A target async iterator whose plain ``__anext__`` raises ``StopAsyncIteration`` directly ends the wrapper.

    The replacement of a deprecated async generator may return a hand-written async iterator whose ``__anext__`` is a
    plain ``def``: it returns an awaitable per item and raises ``StopAsyncIteration`` itself once the items run out.
    Native ``async for`` treats that as the end of the stream, so the wrapper must too, whether the iterator is empty
    from the start or runs out after some items, instead of failing with "async generator raised StopAsyncIteration".

    """
    with captured_warnings():
        items = [item async for item in deprecated_sync_stopping(count)]
    assert items == expected


@pytest.mark.asyncio
async def test_async_generator_ends_when_the_target_athrow_stops_synchronously() -> None:
    """A target whose plain ``athrow`` raises ``StopAsyncIteration`` directly ends the wrapper with that exception.

    A consumer throws an exception into a deprecated stream whose replacement handles it by ending the iteration, and
    that replacement's ``athrow`` raises ``StopAsyncIteration`` before it returns any awaitable. The wrapper must end
    the same way a native async generator does, so the consumer's ``athrow`` call raises ``StopAsyncIteration``.

    """
    with captured_warnings():
        agen = deprecated_sync_stopping(2)
        first = await agen.__anext__()
        with pytest.raises(StopAsyncIteration):
            await agen.athrow(ValueError())
    assert first == 1


@pytest.mark.asyncio
async def test_generator_based_coroutine_source_stays_awaitable() -> None:
    """A deprecated ``@types.coroutine`` generator warns at the call and still works with ``await``.

    Legacy asyncio code written before ``async def`` uses generator-based coroutines: generator functions whose code
    object carries ``CO_ITERABLE_COROUTINE``. A generator wrapper lacks that flag and ``await`` would reject it, so
    such a source keeps the plain wrapper, which warns at the call and returns the awaitable source generator.

    """
    with captured_warnings() as warned:
        awaitable = deprecated_generator_based_double(3)
        at_call = len(_deprecation_warnings(warned))
        result = await awaitable
    assert at_call == 1
    assert result == 6


@pytest.mark.asyncio
async def test_async_generator_with_sync_iterable_target_raises_curated_type_error() -> None:
    """An async generator forwarded to a target returning a list fails on the first step with a ``TypeError``.

    A maintainer points a deprecated async generator at a replacement that returns a plain list. That is a
    misconfiguration, since an async generator's target must return an async iterable, and the first ``async for``
    step must say so, naming the source, the target and the returned type, instead of a bare ``AttributeError``.

    """
    with captured_warnings():
        agen = deprecated_async_squares(3)
        with pytest.raises(TypeError, match=r"Target `squares_list` of async generator `_async_squares_source`") as err:
            await agen.__anext__()
    assert "`list`, which is not an async iterable" in str(err.value)


@pytest.mark.asyncio
async def test_async_generator_with_coroutine_target_raises_matching_advice() -> None:
    """An async generator forwarded to an ``async def`` coroutine function gets advice that fits an async generator.

    The source is already declared ``async def``, so the advice given to a sync source (declare it ``async def``) does
    not apply. The error raised on the first step must instead say that the target has to return an async iterable.

    """
    with captured_warnings():
        agen = deprecated_async_gen_to_coroutine(3)
        with pytest.raises(TypeError, match=r"cannot replace async generator `_async_gen_coroutine_source`") as err:
            await agen.__anext__()
    assert "Declare" not in str(err.value)


@pytest.mark.asyncio
async def test_two_suspended_async_generators_of_one_wrapper_raise_no_false_cycle() -> None:
    """Two live async generators of one callable-target wrapper in one task do not trip the cycle guard.

    The async twin of the sync case: the guard is released before the wrapper starts forwarding, so interleaving
    two deprecated streams in one task (a merge) must not raise a "Circular deprecation" error.

    """
    first_log: list[str] = []
    second_log: list[str] = []
    with captured_warnings():
        first = deprecated_async_running_total(1, first_log)
        second = deprecated_async_running_total(10, second_log)
        merged = [await first.__anext__(), await second.__anext__(), await first.asend(1), await second.asend(1)]
        await first.aclose()
        await second.aclose()
    assert merged == [1, 10, 2, 11]
    assert first_log == second_log == ["closed"]


@pytest.mark.asyncio
async def test_async_generator_first_step_failure_releases_the_cycle_guard() -> None:
    """A first async step that raises releases the cycle guard, so the next call of the same wrapper works and warns.

    The async twin of the sync case: the replacement of a deprecated stream rejects a malformed input on its first
    step, and the consumer retries in the same task with good input. The guard held across that failed step must be
    released, so the retry gets its rows and the one deprecation warning instead of a "Circular deprecation" error.

    """
    with captured_warnings(), pytest.raises(ValueError, match="is not an integer"):
        await deprecated_async_parse_rows(["1", "x"]).__anext__()
    with captured_warnings() as warned:
        rows = await _drain(deprecated_async_parse_rows(["1", "2"]))
    assert rows == [1, 2]
    assert len(_deprecation_warnings(warned)) == 1


@pytest.mark.asyncio
async def test_overlapping_async_generator_first_steps_raise_no_false_cycle() -> None:
    """Two calls of one wrapper whose first steps suspend under the cycle guard overlap without a false cycle error.

    A service fetches two pages of a deprecated paged stream concurrently with ``asyncio.gather``. Each first step
    holds the guard while the replacement awaits the network before its first item, so both guarded steps are in
    flight at once. They run in separate tasks, so neither may see the other as a re-entry of its own target chain.

    """
    with captured_warnings() as warned:
        pages = await asyncio.gather(_drain(deprecated_fetch_page(1)), _drain(deprecated_fetch_page(2)))
    assert pages == [[10, 11], [20, 21]]
    assert len(_deprecation_warnings(warned)) == 2
