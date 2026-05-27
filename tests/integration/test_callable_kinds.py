"""Integration tests for generator and async callable kind support.

Covers four concerns:

- Round-trip equivalence under all three :class:`~deprecate.TargetMode` variants (NOTIFY, ARGS_REMAP, callable) for
  both sync generators (``yield``) and ``async def`` coroutines.
- Warning timing — for generators the warning fires when the generator object is created (before first
  :func:`next` call); for coroutines it fires when the coroutine is awaited, not when the coroutine object is
  created.
- Stacklevel — the warning must point to the user's call site, not to ``deprecation.py``.
Also covers order-agnostic classmethod rescue: ``@deprecated`` applied OUTSIDE ``@classmethod`` is silently rescued
at decoration time via transparent unwrap + rewrap, producing a working deprecated classmethod descriptor without
emitting a warning.

"""

import inspect
import warnings
from typing import cast

import pytest

from deprecate import deprecated
from deprecate._types import _DeprecatedCallable
from tests.collection_deprecate import (
    async_args_remap,
    async_callable,
    async_gen_args_remap,
    async_gen_callable,
    async_gen_notify,
    async_notify,
    gen_args_remap,
    gen_callable,
    gen_notify,
    gen_notify_unlimited,
    make_async_stacked_notify,
)

# Pair each wrapper with the argument it expects.  ``gen_args_remap`` is the only one with a
# non-default argument name because its source carries the legacy ``old_x`` parameter.
_WRAPPER_CASES = [
    pytest.param(gen_notify, {"x": 1}, id="notify"),
    pytest.param(gen_args_remap, {"old_x": 1}, id="args_remap"),
    pytest.param(gen_callable, {"x": 1}, id="callable"),
]


@pytest.fixture(autouse=True)
def _reset_gen_state() -> None:
    """Reset the warning count on each module-level generator wrapper before each test.

    Generator wrappers are module-level singletons whose warning counters persist across parametrize iterations.
    Default ``num_warns=1`` suppresses the second call's warning, so without reset the second parametrize case
    would see no warning.  Only ``warned_calls`` and ``warned_args`` are cleared — ``called`` and
    ``warned_misconfigured`` are left intact.

    Isolation note: this fixture is only safe because no other test module imports and calls these same wrappers
    directly.  If another file calls ``gen_notify`` / ``gen_args_remap`` / ``gen_callable`` without going through
    this fixture, state will leak across test files.  Long-term fix: use a factory function returning a fresh
    wrapper per test instead of resetting shared singletons.

    """
    for wrapper in (gen_notify, gen_args_remap, gen_callable, gen_notify_unlimited):
        state = cast(_DeprecatedCallable, wrapper)._state
        state.warned_calls = 0
        state.warned_args.clear()


@pytest.mark.parametrize(("wrapper", "call_kwargs"), _WRAPPER_CASES)
def test_generator_round_trip(wrapper: object, call_kwargs: dict) -> None:
    """Wrapped generator yields the same values as the underlying ``gen_target`` (1×, 2×, 3×)."""
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        result = wrapper(**call_kwargs)  # type: ignore[operator]
    assert list(result) == [1, 2, 3]


@pytest.mark.parametrize(("wrapper", "call_kwargs"), _WRAPPER_CASES)
def test_generator_warning_fires_eagerly(wrapper: object, call_kwargs: dict) -> None:
    """Warning fires at call time (when the generator is created), not on first ``next()``."""
    with warnings.catch_warnings(record=True) as warned:
        warnings.simplefilter("always")
        gen = wrapper(**call_kwargs)  # type: ignore[operator]
        assert warned, "Warning must fire eagerly at call time, before first next()"
        first = next(gen)
    assert first == 1


@pytest.mark.parametrize(("wrapper", "call_kwargs"), _WRAPPER_CASES)
def test_generator_warning_fires_once_per_call(wrapper: object, call_kwargs: dict) -> None:
    """Iteration of the wrapped generator does not re-emit the deprecation warning.

    The warning fires once inside ``_build_call_plan`` at call time.  Iterating the returned generator object runs the
    source body only — it does not re-enter ``wrapped_fn`` or ``_build_call_plan``, so no second warning is possible.

    """
    with warnings.catch_warnings(record=True) as warned:
        warnings.simplefilter("always")
        gen = wrapper(**call_kwargs)  # type: ignore[operator]
        warn_count_at_call = len(warned)
        # Drain the generator — must not emit any new deprecation warning.
        _ = list(gen)
    new_warnings = [w for w in warned[warn_count_at_call:] if w.category in (FutureWarning, DeprecationWarning)]
    assert warn_count_at_call >= 1, "Warning should fire eagerly at call time"
    assert not new_warnings, f"Iteration emitted unexpected new warnings: {[str(w.message) for w in new_warnings]}"


def test_generator_num_warns_unlimited_warns_every_call() -> None:
    """num_warns=-1 wrapper emits exactly one warning per call; iteration emits none.

    Verifies that each external call to a generator wrapper with ``num_warns=-1`` produces exactly
    one deprecation warning at call time, and that exhausting the generator does not re-emit.

    """
    with warnings.catch_warnings(record=True) as warned:
        warnings.simplefilter("always")
        _ = list(gen_notify_unlimited(x=1))
        first_call_warn_count = len([w for w in warned if w.category in (FutureWarning, DeprecationWarning)])
        _ = list(gen_notify_unlimited(x=2))
    dep_warns = [w for w in warned if w.category in (FutureWarning, DeprecationWarning)]
    assert first_call_warn_count == 1, f"Expected 1 warning after first call, got {first_call_warn_count}"
    assert len(dep_warns) == 2, f"Expected 2 warnings total (one per call), got {len(dep_warns)}"


@pytest.mark.parametrize(("wrapper", "call_kwargs"), _WRAPPER_CASES)
def test_generator_warning_stacklevel(wrapper: object, call_kwargs: dict) -> None:
    """Generator wrapper warning filename points to the caller's file, not to ``deprecation.py``."""
    with warnings.catch_warnings(record=True) as warned:
        warnings.simplefilter("always")
        wrapper(**call_kwargs)  # type: ignore[operator]
    assert warned
    w = warned[0]
    assert w.filename.endswith("test_callable_kinds.py"), f"Expected caller file, got {w.filename}"


def test_wrong_order_classmethod_silently_rescued() -> None:
    """``@deprecated`` applied OUTSIDE ``@classmethod`` is silently rescued: no warning emitted.

    The descriptor is transparently unwrapped and re-wrapped as ``classmethod(deprecated_wrapper)``.  The result
    is a working deprecated classmethod — deprecation warning fires on call, not at decoration time.
    """
    with warnings.catch_warnings(record=True) as warned:
        warnings.simplefilter("always")

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
    with warnings.catch_warnings(record=True) as call_warned:
        warnings.simplefilter("always")
        _Foo.old_method()
    assert any(issubclass(w.category, FutureWarning) for w in call_warned), "Deprecation warning must fire on call"


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


@pytest.fixture(autouse=True)
def _reset_async_state() -> None:
    """Reset the warning counter on each module-level async wrapper before each test.

    Mirrors ``_reset_gen_state`` for the async fixtures: ``num_warns=1`` (default) would otherwise suppress the
    second parametrize case.  Only ``warned_calls`` and ``warned_args`` are cleared — ``called`` and
    ``warned_misconfigured`` are left intact.

    """
    for wrapper in (async_notify, async_args_remap, async_callable):
        state = cast(_DeprecatedCallable, wrapper)._state
        state.warned_calls = 0
        state.warned_args.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(("wrapper", "call_kwargs"), _ASYNC_WRAPPER_CASES)
async def test_async_round_trip(wrapper: object, call_kwargs: dict) -> None:
    """Wrapped async fn returns the same value as the underlying ``async_target`` (``x * 2``)."""
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        result = await wrapper(**call_kwargs)  # type: ignore[operator]
    assert result == 2


@pytest.mark.asyncio
async def test_async_stacked_both_warnings_fire() -> None:
    """Two stacked ``@deprecated`` wrappers on ``async def`` each emit a warning when awaited."""
    fn = make_async_stacked_notify()
    with warnings.catch_warnings(record=True) as warned:
        warnings.simplefilter("always")
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
    with warnings.catch_warnings(record=True) as warned_before:
        warnings.simplefilter("always")
        coro = async_notify(x=1)
    pre_await_warnings = [w for w in warned_before if w.category in (FutureWarning, DeprecationWarning)]
    assert not pre_await_warnings, "Warning must not fire at call time — only fires on await"

    with warnings.catch_warnings(record=True) as warned_after:
        warnings.simplefilter("always")
        result = await coro
    post_await_warnings = [w for w in warned_after if w.category in (FutureWarning, DeprecationWarning)]
    assert post_await_warnings, "Warning must fire when coroutine is awaited"
    assert result == 2


@pytest.mark.asyncio
async def test_async_warning_stacklevel() -> None:
    """Async wrapper warning filename points to the caller's file, not to ``deprecation.py``."""
    with warnings.catch_warnings(record=True) as warned:
        warnings.simplefilter("always")
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
# The N4 path removes the previous ``inspect.isasyncgenfunction`` guard so async generator sources fall through
# to the sync ``wrapped_fn``: the wrapper is sync, fires the warning eagerly at call time, and returns the async
# generator object unchanged for the caller to drive with ``async for``.

_ASYNC_GEN_WRAPPER_CASES = [
    pytest.param(async_gen_notify, {"x": 1}, id="notify"),
    pytest.param(async_gen_args_remap, {"old_x": 1}, id="args_remap"),
    pytest.param(async_gen_callable, {"x": 1}, id="callable"),
]


@pytest.fixture(autouse=True)
def _reset_async_gen_state() -> None:
    """Reset the warning counter on each module-level async generator wrapper before each test.

    Mirrors ``_reset_gen_state`` and ``_reset_async_state``: ``num_warns=1`` (default) would otherwise suppress
    the second parametrize case.  Only ``warned_calls`` and ``warned_args`` are cleared — ``called`` and
    ``warned_misconfigured`` are left intact.

    """
    for wrapper in (async_gen_notify, async_gen_args_remap, async_gen_callable):
        state = cast(_DeprecatedCallable, wrapper)._state
        state.warned_calls = 0
        state.warned_args.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(("wrapper", "call_kwargs"), _ASYNC_GEN_WRAPPER_CASES)
async def test_async_gen_round_trip(wrapper: object, call_kwargs: dict) -> None:
    """Wrapped async generator yields the same values as ``async_gen_target`` (1×, 2×, 3×)."""
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        agen = wrapper(**call_kwargs)  # type: ignore[operator]
        collected = [item async for item in agen]
    assert collected == [1, 2, 3]


@pytest.mark.asyncio
@pytest.mark.parametrize(("wrapper", "call_kwargs"), _ASYNC_GEN_WRAPPER_CASES)
async def test_async_gen_warning_fires_eagerly(wrapper: object, call_kwargs: dict) -> None:
    """Warning fires at sync call time, before the first ``async for`` iteration.

    Async generator wrappers in N4 are sync (``wrapped_fn``) — calling the wrapper invokes ``_build_call_plan`` which
    fires the warning, then returns the async generator object unchanged.  Driving ``__anext__`` does not
    re-enter the wrapper, so the warning must be observable before any iteration starts.

    """
    with warnings.catch_warnings(record=True) as warned:
        warnings.simplefilter("always")
        agen = wrapper(**call_kwargs)  # type: ignore[operator]
        pre_iter_warnings = [w for w in warned if w.category in (FutureWarning, DeprecationWarning)]
        assert pre_iter_warnings, "Warning must fire eagerly at sync call time, before first async for iteration"
        first = await agen.__anext__()
    assert first == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("wrapper", "call_kwargs"), _ASYNC_GEN_WRAPPER_CASES)
async def test_async_gen_warning_fires_once_per_call(wrapper: object, call_kwargs: dict) -> None:
    """Iterating the wrapped async generator does not re-emit the deprecation warning.

    Mirrors ``test_generator_warning_fires_once_per_call`` for sync generators: the warning fires once inside
    ``_build_call_plan`` at call time, and iterating the returned async generator object runs only the source body —
    it does not re-enter ``wrapped_fn`` or ``_build_call_plan``, so no further deprecation warning is possible.

    """
    with warnings.catch_warnings(record=True) as warned:
        warnings.simplefilter("always")
        agen = wrapper(**call_kwargs)  # type: ignore[operator]
        warn_count_at_call = len([w for w in warned if w.category in (FutureWarning, DeprecationWarning)])
        # Drain the async generator — must not emit any new deprecation warning.
        _ = [item async for item in agen]
    new_dep_warnings = [w for w in warned if w.category in (FutureWarning, DeprecationWarning)]
    assert warn_count_at_call >= 1, "Warning should fire eagerly at call time"
    assert len(new_dep_warnings) == warn_count_at_call, (
        f"Iteration emitted new deprecation warnings: {[str(w.message) for w in new_dep_warnings]}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("wrapper", "call_kwargs"), _ASYNC_GEN_WRAPPER_CASES)
async def test_async_gen_warning_stacklevel(wrapper: object, call_kwargs: dict) -> None:
    """Async generator wrapper warning filename points to the caller's file, not to ``deprecation.py``."""
    with warnings.catch_warnings(record=True) as warned:
        warnings.simplefilter("always")
        agen = wrapper(**call_kwargs)  # type: ignore[operator]
        # Drain so the async generator is fully consumed and does not trigger async-generator cleanup warnings on GC.
        _ = [item async for item in agen]
    deprecation_warnings = [w for w in warned if w.category in (FutureWarning, DeprecationWarning)]
    assert deprecation_warnings, "Async generator wrapper must emit a deprecation warning"
    w = deprecation_warnings[0]
    assert w.filename.endswith("test_callable_kinds.py"), f"Expected caller file, got {w.filename}"
