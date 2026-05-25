"""Integration tests for generator callable kind support.

Covers three concerns:

- Round-trip yield equivalence under all three :class:`~deprecate.TargetMode` variants (NOTIFY, ARGS_REMAP, callable).
- Eager warning timing — the deprecation warning must fire at call time (when the wrapped generator is *created*), not
  on the first :func:`next` call.  Generator bodies do not execute until iterated, so a naïve forwarder would defer the
  warning until iteration.  ``_dispatch`` fires the warning synchronously before returning the generator object.
- Stacklevel — the warning must point to the user's call site, not to ``deprecation.py``.

Also covers order-agnostic classmethod rescue: ``@deprecated`` applied OUTSIDE ``@classmethod`` is silently rescued
at decoration time via transparent unwrap + rewrap, producing a working deprecated classmethod descriptor without
emitting a warning.

"""

import warnings
from typing import cast

import pytest

from deprecate import deprecated
from deprecate._types import _DeprecatedCallable
from tests.collection_deprecate import gen_args_remap, gen_callable, gen_notify, gen_notify_unlimited

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

    The warning fires once inside ``_dispatch`` at call time.  Iterating the returned generator object runs the source
    body only — it does not re-enter ``wrapped_fn`` or ``_dispatch``, so no second warning is possible.

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
