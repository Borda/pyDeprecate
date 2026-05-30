"""Unit tests for the internal :func:`deprecate.deprecation._build_call_plan` helper.

``_build_call_plan`` is the shared dispatch core used by both the sync ``wrapped_fn`` and the async
``async_wrapped_fn`` produced by :func:`deprecate.deprecation.deprecated`.  It is normally invoked
from within ``packing()``'s closure, but its signature accepts every dependency explicitly so it can
be tested in isolation here.  These tests pin the public dispatch contract:

- callable-target round-trip resolves ``target_func`` and returns the warning-emitted plan;
- ``skip_if=True`` is handled by the *wrapper*, not by ``_build_call_plan``, so we instead pin the
  short-circuit branch when the caller already uses the new arg name (migrated-caller fast path);
- :attr:`~deprecate.TargetMode.NOTIFY` returns ``target_func=None`` and leaves the source body to
  the wrapper.

Each test constructs a minimal wrapper stub carrying the ``_state`` and ``__deprecated__``
attributes that :func:`_build_call_plan` reads, plus a fresh :class:`~deprecate._types.DeprecationConfig`.

"""

import warnings
from typing import Any, Callable

from deprecate import TargetMode
from deprecate._types import DeprecationConfig, _DeprecatedCallable, _WrapperState
from deprecate.deprecation import _build_call_plan
from tests.collection_deprecate import (
    depr_pow_args,
    depr_target_mode_args_only_with_args_extra_injects_kwargs,
    make_depr_compute_power_stacked,
)
from tests.collection_misconfigured import target_false_deprecation
from tests.collection_targets import compute_power, double_value, identity_value


def _make_wrapper_stub(source: Callable[..., Any], dep_cfg: DeprecationConfig) -> _DeprecatedCallable:
    """Return a minimal callable shaped like a ``@deprecated`` wrapper for unit testing.

    The real wrapper carries mutable ``_state`` and frozen ``__deprecated__`` attributes that
    :func:`_build_call_plan` reads via :class:`~deprecate._types._DeprecatedCallable`.  Wrapping the
    bare ``source`` here suffices because the helper never invokes ``wrapper_fn`` itself — it only
    reads ``wrapper_fn._state``.

    """

    def _stub(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - signature mirrors real wrappers
        return source(*args, **kwargs)

    _stub._state = _WrapperState()  # type: ignore[attr-defined]
    _stub.__deprecated__ = dep_cfg  # type: ignore[attr-defined]
    return _stub  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Callable-target dispatch — happy path
# ---------------------------------------------------------------------------


def test_callable_target_round_trip_returns_target_func() -> None:
    """Callable target with matching kwargs returns ``short_circuit=False`` and the resolved ``target_func``."""
    cfg = DeprecationConfig(deprecated_in="1.0", remove_in="2.0", name="src", target=double_value)
    wrapper = _make_wrapper_stub(double_value, cfg)
    plan = _build_call_plan(
        wrapper_fn=wrapper,
        source=double_value,
        target=double_value,
        normalized_target=double_value,
        args=(),
        kwargs={"x": 3},
        dep_cfg=cfg,
        stream=None,  # suppress real warning emission
        num_warns=1,
        source_has_var_positional=False,
        source_is_stacked=False,
    )

    assert plan.short_circuit is False
    assert plan.target_func is double_value
    assert plan.resolved_kwargs == {"x": 3}
    assert plan.reason_argument == {}
    # State must be bumped exactly once per call.
    assert wrapper._state.called == 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Short-circuit branch — caller using the new name with no extras
# ---------------------------------------------------------------------------


def test_args_remap_migrated_caller_short_circuits() -> None:
    """When the caller passes only the new arg name and no extras are configured the plan short-circuits.

    The short-circuit branch is the documented "migrated caller using the new arg name" fast path:
    no warning fires, no remap runs, no target lookup happens.  The wrapper then invokes the source
    directly with ``resolved_kwargs``.

    """
    cfg = DeprecationConfig(
        deprecated_in="1.0",
        remove_in="2.0",
        name="src",
        target=TargetMode.ARGS_REMAP,
        args_mapping={"old_x": "x"},
    )
    wrapper = _make_wrapper_stub(identity_value, cfg)
    plan = _build_call_plan(
        wrapper_fn=wrapper,
        source=identity_value,
        target=TargetMode.ARGS_REMAP,
        normalized_target=TargetMode.ARGS_REMAP,
        args=(),
        kwargs={"x": 7},  # caller already migrated — uses new name
        dep_cfg=cfg,
        stream=None,
        num_warns=1,
        source_has_var_positional=False,
        source_is_stacked=False,
    )

    assert plan.short_circuit is True
    assert plan.target_func is None
    assert plan.reason_argument == {}
    assert plan.resolved_kwargs == {"x": 7}


# ---------------------------------------------------------------------------
# NOTIFY mode — body runs in the wrapper; ``target_func`` is always None
# ---------------------------------------------------------------------------


def test_notify_mode_returns_none_target_func() -> None:
    """:attr:`TargetMode.NOTIFY` never resolves a target; the wrapper must execute the source body."""
    cfg = DeprecationConfig(deprecated_in="1.0", remove_in="2.0", name="src", target=TargetMode.NOTIFY)
    wrapper = _make_wrapper_stub(identity_value, cfg)
    plan = _build_call_plan(
        wrapper_fn=wrapper,
        source=identity_value,
        target=TargetMode.NOTIFY,
        normalized_target=TargetMode.NOTIFY,
        args=(),
        kwargs={"x": 5},
        dep_cfg=cfg,
        stream=None,
        num_warns=1,
        source_has_var_positional=False,
        source_is_stacked=False,
    )

    assert plan.short_circuit is False
    assert plan.target_func is None
    # NOTIFY always treats every call as a callable-deprecation reason — no per-arg reason fires.
    assert plan.reason_argument == {}


# ---------------------------------------------------------------------------
# Misconfigured wrappers — ``warned_misconfigured`` is sticky after first emit
# ---------------------------------------------------------------------------


def test_misconfigured_warning_fires_exactly_once() -> None:
    """A misconfigured wrapper emits its ``UserWarning`` only on the first call.

    The misconfiguration ``UserWarning`` is gated by ``state.warned_misconfigured`` in
    :func:`deprecate.deprecation._build_call_plan` (see lines around the
    ``state.warned_misconfigured = True`` assignment).  The flag is **never** reset by
    :mod:`tests.conftest` — it implements an intentional once-per-wrapper-lifetime contract
    so noisy misconfig warnings do not flood test output.

    This test exercises ``target_false_deprecation`` from
    :mod:`tests.collection_misconfigured`, which sets ``misconfigured=True`` via the
    legacy ``target=False`` sentinel.  We bypass the FutureWarning by filtering only the
    ``UserWarning`` instances at the call site (and we explicitly reset
    ``warned_misconfigured`` here so the test is independent of import order).

    """
    # Pre-reset the sticky flag so the test is order-independent: a prior import or test
    # may have already exhausted the one-time slot.  ``conftest._reset_collection_deprecate_state``
    # intentionally does not touch ``warned_misconfigured`` (see its docstring).
    target_false_deprecation._state.warned_misconfigured = False  # type: ignore[attr-defined]
    target_false_deprecation._state.warned_calls = 0  # type: ignore[attr-defined]

    with warnings.catch_warnings(record=True) as call1:
        warnings.simplefilter("always")
        target_false_deprecation(x=1)

    with warnings.catch_warnings(record=True) as call2:
        warnings.simplefilter("always")
        target_false_deprecation(x=2)

    misconfig_call1 = [
        w for w in call1 if w.category is UserWarning and "invalid deprecation config" in str(w.message)
    ]
    misconfig_call2 = [
        w for w in call2 if w.category is UserWarning and "invalid deprecation config" in str(w.message)
    ]
    assert len(misconfig_call1) == 1, "Misconfigured UserWarning must fire on the first call"
    assert misconfig_call2 == [], "Misconfigured UserWarning must NOT fire on subsequent calls (sticky flag)"


# ---------------------------------------------------------------------------
# ``args_extra`` injection — ARGS_REMAP path must merge extras into kwargs
# ---------------------------------------------------------------------------


def test_args_extra_injection_reaches_target() -> None:
    """``args_extra`` configured on an ARGS_REMAP wrapper is injected into the call kwargs.

    The fixture ``depr_target_mode_args_only_with_args_extra_injects_kwargs`` is configured
    with ``args_mapping={"old_x": "x"}`` and ``args_extra={"y": 10}``.  The source body returns
    ``add_values(x, y)``.  Calling with ``old_x=5`` (only) must:

    * remap ``old_x`` → ``x=5``,
    * inject ``y=10`` from ``args_extra``,
    * and return ``add_values(5, 10) == 15``.

    """
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        result = depr_target_mode_args_only_with_args_extra_injects_kwargs(old_x=5)
    assert result == 15, "args_extra must inject y=10 alongside the remapped old_x→x=5"


# ---------------------------------------------------------------------------
# ``num_warns`` exhaustion — second call must not re-fire after budget spent
# ---------------------------------------------------------------------------


def test_num_warns_one_exhausts_after_first_call() -> None:
    """A wrapper with ``num_warns=1`` (default) fires its ``FutureWarning`` once.

    The conftest autouse fixture resets ``warned_calls`` per test, so we can call the same
    module-level wrapper twice inside a single test and observe exhaustion on the second call.
    ``depr_pow_args`` uses the default ``num_warns=1`` (no override in its decorator config).

    """
    with warnings.catch_warnings(record=True) as call1:
        warnings.simplefilter("always")
        depr_pow_args(2.0, 3.0)

    with warnings.catch_warnings(record=True) as call2:
        warnings.simplefilter("always")
        depr_pow_args(2.0, 3.0)

    future_call1 = [w for w in call1 if w.category is FutureWarning]
    future_call2 = [w for w in call2 if w.category is FutureWarning]
    assert len(future_call1) == 1, "FutureWarning must fire on the first call when num_warns=1"
    assert future_call2 == [], "FutureWarning must NOT fire on the second call after num_warns budget is exhausted"


# ---------------------------------------------------------------------------
# ``source_is_stacked=True`` — bypasses the migrated-caller short-circuit
# ---------------------------------------------------------------------------


def test_source_is_stacked_skips_positional_conversion() -> None:
    """When ``source_is_stacked=True`` the helper must not short-circuit on a migrated caller.

    The short-circuit gate (see ``_build_call_plan`` lines 728–739) compresses three conditions:
    no callable/arg reason, no ``args_extra`` injection, and ``not source_is_stacked``.  When the
    outer wrapper sits over an already-``@deprecated`` source — the canonical
    ``ARGS_REMAP``-outer + ``NOTIFY``-inner stack from :func:`make_depr_compute_power_stacked` —
    the inner layer still needs to run so its own ``FutureWarning`` fires.  Skipping that path
    when ``source_is_stacked=True`` would silently drop the inner warning.

    The companion test :func:`test_args_remap_migrated_caller_short_circuits` pins the inverse:
    same migrated-caller kwargs with ``source_is_stacked=False`` *do* short-circuit.

    Two assertions are checked in isolation here:

    * direct call to ``_build_call_plan`` with ``source_is_stacked=True`` returns
      ``short_circuit=False`` even when no reason fires (the bypass);
    * end-to-end call to the real stacked wrapper from
      :func:`make_depr_compute_power_stacked` with the migrated arg name emits the inner
      ``NOTIFY`` ``FutureWarning`` and returns the correct value.

    """
    cfg = DeprecationConfig(
        deprecated_in="1.0",
        remove_in="2.0",
        name="src",
        target=TargetMode.ARGS_REMAP,
        args_mapping={"factor": "scale"},
    )
    wrapper = _make_wrapper_stub(compute_power, cfg)
    plan = _build_call_plan(
        wrapper_fn=wrapper,
        source=compute_power,
        target=TargetMode.ARGS_REMAP,
        normalized_target=TargetMode.ARGS_REMAP,
        args=(),
        kwargs={"base": 2.0, "scale": 3.0},  # caller already migrated — uses new name
        dep_cfg=cfg,
        stream=None,
        num_warns=1,
        source_has_var_positional=False,
        source_is_stacked=True,  # source itself carries @deprecated meta
    )

    assert plan.short_circuit is False, "source_is_stacked=True must bypass the migrated-caller short-circuit"
    assert plan.target_func is None, "ARGS_REMAP never resolves a callable target_func"

    # End-to-end check: the real ARGS_REMAP-outer + NOTIFY-inner stack must still emit the
    # inner NOTIFY warning and return the correct value when the caller migrates to ``scale=``.
    fn = make_depr_compute_power_stacked()
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        result = fn(2.0, scale=3.0)
    future_warnings = [w for w in record if w.category is FutureWarning]
    assert result == 8.0, "Stacked wrapper must compute compute_power(2.0, scale=3.0) == 8.0"
    assert len(future_warnings) >= 1, "Inner NOTIFY layer must still fire its FutureWarning on a migrated caller"
