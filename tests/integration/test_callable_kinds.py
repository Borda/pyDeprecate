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

import inspect

import pytest

from deprecate import DeprecatedError, as_exceptions, deprecated, find_deprecation_wrappers
from tests._helpers import captured_warnings
from tests.collections import api_deprecated_forwarding
from tests.collections.api_deprecated import decorated_sum_calls_inf, make_async_stacked_notify
from tests.collections.api_deprecated_forwarding import (
    async_args_remap,
    async_callable,
    async_gen_args_remap,
    async_gen_callable,
    async_gen_notify,
    async_gen_stacked,
    async_notify,
    deprecated_async_positional_only_to_sync,
    deprecated_async_running_total,
    deprecated_async_var_positional_to_sync,
    deprecated_countdown,
    deprecated_cursor,
    deprecated_running_total,
    deprecated_squares,
    deprecated_walk,
    gen_args_remap,
    gen_callable,
    gen_notify,
    gen_notify_skipped,
    gen_notify_unlimited,
    gen_stacked,
)

#: Loads the ``pytester`` fixture, which drives a deprecated ``yield`` fixture through a real pytest run.
pytest_plugins = ["pytester"]


def _deprecation_warnings(warned: list) -> list:
    """Return the deprecation warnings (``FutureWarning`` / ``DeprecationWarning``) among the captured ones."""
    return [w for w in warned if w.category in (FutureWarning, DeprecationWarning)]


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
    """num_warns=-1 wrapper emits exactly one warning per call; iteration emits none.

    Verifies that each external call to a generator wrapper with ``num_warns=-1`` produces exactly
    one deprecation warning at call time, and that exhausting the generator does not re-emit.

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

from tests.collections.api_deprecated_forwarding import legacy_db_session

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

from tests.collections.api_deprecated_forwarding import legacy_async_db_session

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


def test_generator_recursing_through_its_deprecated_name_raises_cycle_error() -> None:
    """A target that recurses through the deprecated generator before its first ``yield`` raises the cycle error.

    The cycle guard stays held through a generator wrapper's first step, so a cycle of generator wrappers is caught
    instead of nesting until ``RecursionError``. Recursion through the deprecated name during that step looks the same
    and raises ``RuntimeError``, as a plain deprecated function recursing through its own name does; recursing
    through the replacement's own name is the fix.

    """
    with captured_warnings(), pytest.raises(RuntimeError, match="Circular deprecation cycle detected"):
        list(deprecated_walk(2, deprecated_walk))


def test_generator_skip_if_runs_the_source_without_warning() -> None:
    """``skip_if=True`` on a generator wrapper runs the source generator unchanged and never warns.

    A library keeps an old generator API working for one platform while ``skip_if`` turns the deprecation off there.
    The skipped path must iterate the source body itself, with no warning, as the function wrapper does for a call.

    """
    with captured_warnings() as warned:
        items = list(gen_notify_skipped(x=2))
    assert items == [2, 4, 6]
    assert not _deprecation_warnings(warned)


def test_stacked_generator_wrappers_warn_per_layer_on_first_step() -> None:
    """A NOTIFY layer over a callable-target generator wrapper warns once per layer, on the first step only.

    Both layers are generator functions: calling the outer one runs no code, its first step runs the outer
    deprecation logic and then delegates to the inner wrapper, whose own first step warns before it forwards.

    """
    with captured_warnings() as warned:
        gen = gen_stacked(x=1)
        at_call = len(_deprecation_warnings(warned))
        items = list(gen)
    assert at_call == 0
    assert len(_deprecation_warnings(warned)) == 2
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
