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

from typing import Any, Callable

from deprecate import TargetMode
from deprecate._types import DeprecationConfig, _DeprecatedCallable, _WrapperState
from deprecate.deprecation import _build_call_plan
from tests.collection_targets import double_value, identity_value


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
        _target=double_value,
        args=(),
        kwargs={"x": 3},
        dep_cfg=cfg,
        stream=None,  # suppress real warning emission
        num_warns=1,
        source_has_var_positional=False,
        _source_is_stacked=False,
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
        _target=TargetMode.ARGS_REMAP,
        args=(),
        kwargs={"x": 7},  # caller already migrated — uses new name
        dep_cfg=cfg,
        stream=None,
        num_warns=1,
        source_has_var_positional=False,
        _source_is_stacked=False,
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
        _target=TargetMode.NOTIFY,
        args=(),
        kwargs={"x": 5},
        dep_cfg=cfg,
        stream=None,
        num_warns=1,
        source_has_var_positional=False,
        _source_is_stacked=False,
    )

    assert plan.short_circuit is False
    assert plan.target_func is None
    # NOTIFY always treats every call as a callable-deprecation reason — no per-arg reason fires.
    assert plan.reason_argument == {}
