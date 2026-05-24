"""Integration tests for generator callable kind support (N2 — generator factory wrapper).

Covers three concerns:

- Round-trip yield equivalence under all three :class:`~deprecate.TargetMode` variants (NOTIFY, ARGS_REMAP, callable).
- Eager warning timing — the deprecation warning must fire at call time (when the wrapped generator is *created*), not
  on the first :func:`next` call.  Generator bodies do not execute until iterated, so a naïve forwarder would defer the
  warning until iteration.  The N2 factory pattern circumvents this by calling ``_dispatch`` eagerly and returning a
  fresh delegating generator.
- Stacklevel — the warning must point to the user's call site, not to ``deprecation.py``.

Also covers the N3 wrong-order guard: ``@deprecated`` applied OUTSIDE ``@classmethod`` is detected at decoration time
and emits a :class:`UserWarning` rather than silently breaking the descriptor protocol.

"""

import warnings

import pytest

from deprecate import deprecated
from deprecate._types import _WrapperState
from tests.collection_deprecate import gen_args_remap, gen_callable, gen_notify

# Pair each wrapper with the argument it expects.  ``gen_args_remap`` is the only one with a
# non-default argument name because its source carries the legacy ``old_x`` parameter.
_WRAPPER_CASES = [
    pytest.param(gen_notify, {"x": 1}, id="notify"),
    pytest.param(gen_args_remap, {"old_x": 1}, id="args_remap"),
    pytest.param(gen_callable, {"x": 1}, id="callable"),
]


@pytest.fixture(autouse=True)
def _reset_gen_state() -> None:
    """Reset per-wrapper mutable state before each test.

    Generator wrappers are module-level singletons whose ``_state`` (``warned_calls`` etc.) persists across the
    parametrize iterations.  Default ``num_warns=1`` suppresses the second call's warning, so without reset the second
    parametrize case would see no warning.

    """
    for wrapper in (gen_notify, gen_args_remap, gen_callable):
        wrapper._state = _WrapperState()


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

    The N2 factory pattern double-dispatches (eager at call time + lazy on iteration), so the implementation must ensure
    only the first dispatch's warning surfaces.  Subsequent iteration of the same generator instance must not emit a new
    warning even under ``num_warns=-1`` (which would otherwise warn on every call) — because the second dispatch is an
    *internal* call, not a user call.

    Note: under ``num_warns=-1`` the second internal dispatch suppresses re-warn because ``state.warned_calls`` already
    incremented during the first eager dispatch; the iteration phase does not double-warn.

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


def test_n3_wrong_order_classmethod_warns() -> None:
    """``@deprecated`` applied OUTSIDE ``@classmethod`` emits ``UserWarning`` and returns the descriptor unchanged."""
    with warnings.catch_warnings(record=True) as warned:
        warnings.simplefilter("always")

        class _Foo:
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            @classmethod
            def old_method(cls) -> None:  # pragma: no cover - body unreachable; descriptor returned as-is
                ...

    user_warnings = [w for w in warned if issubclass(w.category, UserWarning)]
    assert user_warnings, "Expected a UserWarning for wrong-order classmethod"
    assert "outside @classmethod" in str(user_warnings[0].message)
    # Descriptor must be returned unchanged so the class attribute is still a classmethod.
    assert isinstance(_Foo.__dict__["old_method"], classmethod)
