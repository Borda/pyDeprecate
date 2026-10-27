"""Unit tests for call-facts precomputation and the call-plan engine (:mod:`deprecate._dispatch`)."""

import asyncio
import concurrent.futures
import contextvars
import inspect
import sys
import threading
import time
import traceback
import warnings
from collections.abc import Iterator
from typing import Any, Callable, Optional, Union, cast
from unittest import mock

import pytest

from deprecate import TargetMode, assert_no_warnings, deprecated, void
from deprecate._dispatch import (
    _build_call_facts,
    _build_call_plan,
    _cycle_detection,
    _CycleEntry,
    _enter_cycle_guard,
    _exit_cycle_guard,
    _ForwardDefaults,
    _merge_source_defaults,
    _precompute_forward_defaults,
    _prepare_target_call,
    _raise_target_signature_error,
    _split_positional_only_kwargs,
    _target_signature_facts,
    _update_kwargs_with_args,
    _update_kwargs_with_defaults,
    _WrapperContext,
)
from deprecate._types import DeprecationConfig, _CallFacts, _DeprecatedCallable, _WrapperState
from deprecate.utils import _signature_facts
from tests._helpers import captured_warnings
from tests.collections.api_current import (
    KeywordCallTarget,
    NewCls,
    SignatureNeedsContextError,
    SignatureNotBuiltError,
    call_signature_source,
    compute_power,
    double_value,
    fn_shared_default_target,
    fn_with_default,
    identity_value,
    nested_partial_target,
    partial_target,
    positional_only_target,
    positional_only_two_params_target,
    source_sharing_level_default,
    source_with_forward_defaults,
    var_positional_target,
)
from tests.collections.api_deprecated import (
    dep_async_cycle_fn_a,
    dep_async_cycle_fn_b,
    dep_cycle_fn_a,
    dep_cycle_fn_b,
    depr_pow_args,
    depr_target_mode_args_only_with_args_extra_injects_kwargs,
    make_depr_args_remap_notify_with_extra,
    make_depr_compute_power_stacked,
    make_depr_notify_callable_stacked,
)
from tests.collections.api_deprecated_forwarding import (
    OldPositionalOnlyClass,
    OldSelfOnlyClass,
    dep_async_non_cycle_old_fn,
    dep_async_sibling_pair_old_fn,
    dep_async_task_cycle_fn,
    dep_fib_callable,
    dep_fib_notify,
    dep_fib_remap,
    dep_fib_silent,
    dep_non_cycle_old_fn,
    deprecated_args_remap_positional_only_source,
    deprecated_async_args_remap_positional_only_source,
    deprecated_async_gapped_positional_only_source,
    deprecated_async_notify_positional_only_source,
    deprecated_async_positional_only_source,
    deprecated_gapped_positional_only_first_source,
    deprecated_gapped_positional_only_full_source,
    deprecated_gapped_positional_only_source,
    deprecated_notify_positional_only_source,
    deprecated_positional_only_source,
    deprecated_positional_only_stream_none,
    deprecated_positional_only_two_params_source,
    deprecated_positional_only_with_args_mapping_source,
    fn_shared_default,
    make_deprecated_positional_only_num_warns_one,
)
from tests.collections.api_deprecated_regressions import depr_scale_partial_every_call
from tests.collections.misconfigured import target_false_deprecation


class TestUpdateKwargsWithArgs:
    """Tests for _update_kwargs_with_args — merges positional call args into the kwargs dict by param name."""

    def test_no_positional_args_returns_kwargs_unchanged(self) -> None:
        """When no positional args are passed, the existing kwargs dict is returned as-is."""

        def my_func(a: int, b: int) -> None:
            pass

        result = _update_kwargs_with_args((), {"a": 1}, _build_call_facts(my_func, TargetMode.NOTIFY, None))
        assert result == {"a": 1}

    def test_maps_positional_to_param_names(self) -> None:
        """Positional args are matched to param names in declaration order and added to kwargs."""

        def my_func(a: int, b: str, c: float = 3.0) -> None:
            pass

        result = _update_kwargs_with_args((1, "hello"), {}, _build_call_facts(my_func, TargetMode.NOTIFY, None))
        assert result == {"a": 1, "b": "hello"}

    def test_merges_with_existing_kwargs(self) -> None:
        """Positional args are merged with already-present keyword args without overwriting them."""

        def my_func(a: int, b: int, c: int = 0) -> None:
            pass

        result = _update_kwargs_with_args((10,), {"c": 99}, _build_call_facts(my_func, TargetMode.NOTIFY, None))
        assert result == {"a": 10, "c": 99}

    def test_stops_at_var_positional(self) -> None:
        """Extra positional args beyond a *args boundary are not mapped to named params."""

        def my_func(a: int, *args: int) -> None:
            pass

        result = _update_kwargs_with_args((1, 2, 3), {}, _build_call_facts(my_func, TargetMode.NOTIFY, None))
        assert result == {"a": 1}


def test_call_plan_rejects_surplus_positionals() -> None:
    """Passing more positional args than the source has positional params raises the curated TypeError.

    The arity check lives in ``_build_call_plan`` (before any warning or budget is spent) and runs once per call;
    ``_update_kwargs_with_args`` relies on it and no longer repeats it.
    """

    def my_func(a: int, b: int) -> None:
        pass

    cfg = DeprecationConfig(deprecated_in="1.0", remove_in="2.0", name="src", target=TargetMode.NOTIFY)
    ctx = _make_context(my_func, cfg, TargetMode.NOTIFY)

    with pytest.raises(TypeError, match="takes 2 positional"):
        _build_call_plan(ctx, (1, 2, 3), {})


def test_class_target_uses_call_signature_for_validation() -> None:
    """Class targets validate against metaclass __call__ when not forwarding __init__."""
    target_callable = _prepare_target_call(call_signature_source, KeywordCallTarget, {"value": "red"})
    assert target_callable is KeywordCallTarget


class TestUpdateKwargsWithDefaults:
    """Tests for _update_kwargs_with_defaults — fills missing kwargs with the target function's default values."""

    def test_fills_missing_defaults(self) -> None:
        """All defaulted params that are absent from kwargs are added with their default values."""

        def my_func(a: int = 1, b: int = 2, c: int = 3) -> None:
            pass

        result = _update_kwargs_with_defaults(_signature_facts(my_func).defaults, {})
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_provided_kwargs_override_defaults(self) -> None:
        """Explicitly provided kwargs take precedence over the function's own defaults."""

        def my_func(a: int = 1, b: int = 2, c: int = 3) -> None:
            pass

        result = _update_kwargs_with_defaults(_signature_facts(my_func).defaults, {"b": 20})
        assert result == {"a": 1, "b": 20, "c": 3}

    def test_params_without_defaults_not_included(self) -> None:
        """Required parameters (no default) are not injected into kwargs."""

        def my_func(required: int, optional: int = 5) -> None:
            pass

        result = _update_kwargs_with_defaults(_signature_facts(my_func).defaults, {})
        assert result == {"optional": 5}
        assert "required" not in result

    def test_no_defaults_returns_provided_kwargs(self) -> None:
        """When the function has no defaults at all, the input kwargs dict is returned unchanged."""

        def my_func(a: int, b: str) -> None:
            pass

        result = _update_kwargs_with_defaults(_signature_facts(my_func).defaults, {"a": 7})
        assert result == {"a": 7}


#: Outer-target value for each stacking kind; ATTRS_REMAP stands for any mode the table calls "other".
#: Existing deprecated wrapper carrying each inner stacking kind.


class TestStackedArgsRemapNotify:
    """Behaviour of the supported ARGS_REMAP-outer + NOTIFY-inner lifecycle stacking.

    Pattern: ``@deprecated(ARGS_REMAP, ...)`` on top, ``@deprecated(NOTIFY, ...)`` below.
    This matches the lifecycle where arguments are renamed in an earlier release and the
    whole function is deprecated in a later one.  Both warning layers must fire
    independently; the inner NOTIFY must run even when no deprecated argument is present.
    """

    def _make_fresh(self) -> Callable[..., float]:
        """Return a fresh stacked fixture each time to avoid num_warns counter exhaustion."""
        return make_depr_compute_power_stacked()

    def test_old_arg_fires_two_warnings(self) -> None:
        """Calling with the old arg name raises both the arg-rename and the function-deprecated warning."""
        fn = self._make_fresh()
        with pytest.warns(FutureWarning) as record:
            result = fn(2, factor=3)
        assert result == 8.0
        assert len(record) == 2

    def test_new_arg_fires_only_notify(self) -> None:
        """Calling with the new arg name raises only the function-deprecated (NOTIFY) warning."""
        fn = self._make_fresh()
        with pytest.warns(FutureWarning) as record:
            result = fn(2, scale=3)
        assert result == 8.0
        assert len(record) == 1

    def test_no_deprecated_args_fires_only_notify(self) -> None:
        """Calling with no arguments at all still fires the NOTIFY warning."""
        fn = self._make_fresh()
        with pytest.warns(FutureWarning) as record:
            result = fn(2)
        assert result == 2.0
        assert len(record) == 1

    def test_positional_call_fires_two_warnings(self) -> None:
        """Positional call mapping to the old arg position fires both warnings."""
        fn = self._make_fresh()
        # positional: base=2, factor=3 (old positional slot)
        with pytest.warns(FutureWarning) as record:
            result = fn(2, 3)
        assert result == 8.0
        assert len(record) == 2

    def test_counter_exhausted_fires_no_warnings_on_repeat(self) -> None:
        """Second call after counter exhaustion emits no FutureWarning."""
        fn = self._make_fresh()
        with pytest.warns(FutureWarning):
            fn(2, factor=3)  # exhausts both layer counters
        with captured_warnings() as caught:
            fn(2, factor=3)
        assert not [w for w in caught if issubclass(w.category, FutureWarning)]

    def test_args_extra_flows_through_notify_layer(self) -> None:
        """args_extra on the outer ARGS_REMAP layer reaches the target via the inner NOTIFY layer.

        Verifies that ``args_extra`` set on the outer ``ARGS_REMAP`` decorator is injected before
        the call is handed off to the inner ``NOTIFY`` wrapper, so the final function receives the
        extra kwargs correctly.
        """
        fn = make_depr_args_remap_notify_with_extra()
        with pytest.warns(FutureWarning) as record:
            result = fn(factor=3.0)
        assert result == 8.0
        assert len(record) == 2


class TestStackedNotifyCallable:
    """Call-time behaviour of the supported NOTIFY-outer + callable-target-inner stacking.

    Pattern: ``@deprecated(TargetMode.NOTIFY, ...)`` on top, ``@deprecated(target=<fn>, ...)``
    below.  The outer NOTIFY warns callers the function is going away; the inner callable-target
    layer warns and forwards to the final function.  Both ``FutureWarning`` instances must fire
    independently on every call until their counters are exhausted.
    """

    def _make_fresh(self) -> Callable[..., float]:
        """Return a fresh stacked fixture each time to avoid num_warns counter exhaustion."""
        return make_depr_notify_callable_stacked()

    def test_call_fires_two_warnings(self) -> None:
        """Calling the stacked wrapper emits both the NOTIFY and callable-target FutureWarnings."""
        fn = self._make_fresh()
        with pytest.warns(FutureWarning) as record:
            result = fn(2.0, scale=3.0)
        assert result == 8.0
        assert len(record) == 2

    def test_result_correctly_forwarded_to_target(self) -> None:
        """The callable-target inner layer correctly forwards the call to the final function."""
        fn = self._make_fresh()
        with pytest.warns(FutureWarning):
            result = fn(3.0, scale=2.0)
        assert result == 9.0

    def test_counter_exhausted_fires_no_warnings_on_repeat(self) -> None:
        """Second call after counter exhaustion emits no FutureWarning."""
        fn = self._make_fresh()
        with pytest.warns(FutureWarning):
            fn(2.0, scale=3.0)  # exhausts both layer counters
        with captured_warnings() as caught:
            fn(2.0, scale=3.0)
        assert not [w for w in caught if issubclass(w.category, FutureWarning)]


class TestPositionalOnlyTarget:
    """``@deprecated`` with a callable target that declares POSITIONAL_ONLY parameters.

    When a target function declares ``def fn(x, /): ...``, the wrapper must not
    blindly call ``target(**kwargs)`` — Python raises ``TypeError`` because ``x``
    cannot be passed as a keyword argument.  The decorator should detect this at
    decoration time (``UserWarning``) and split the dispatch at call time so
    positional-only params are forwarded positionally and remaining params as kwargs.
    """

    @pytest.mark.parametrize(
        ("call_args", "call_kwargs", "expected"),
        [
            pytest.param((5,), {}, 5, id="positional-arg"),
            pytest.param((), {"x": 5}, 5, id="keyword-arg"),
            pytest.param((3,), {"y": 4}, 7, id="both-args"),
        ],
    )
    def test_call_shape_forwards_correctly(self, call_args: tuple, call_kwargs: dict, expected: int) -> None:
        """Call-shape variations on a POSITIONAL_ONLY target all forward correctly.

        Verifies three call shapes that a user of ``deprecated_positional_only_source``
        might write — positional arg, keyword arg, and a mix — to ensure split dispatch
        handles each without ``TypeError`` or incorrect values.
        """
        with pytest.warns(FutureWarning):
            result = deprecated_positional_only_source(*call_args, **call_kwargs)
        assert result == expected

    def test_future_warning_fires_on_call(self) -> None:
        """The standard FutureWarning is still emitted on the positional-only dispatch path.

        The positional-only split must not suppress the deprecation warning —
        callers should still see ``FutureWarning`` so they know to migrate.
        """
        with pytest.warns(FutureWarning, match=r"deprecated_positional_only_source"):
            deprecated_positional_only_source(1)

    @pytest.mark.asyncio
    async def test_async_dispatch_forwards_positional_correctly(self) -> None:
        """The async dispatch path forwards POSITIONAL_ONLY params without TypeError.

        An async deprecated wrapper targeting an async function with a positional-only
        parameter must not call ``await target_func(**resolved_kwargs)`` — that raises
        ``TypeError``.  The split-dispatch path must fire in ``async_wrapped_fn`` so the
        async call succeeds and returns the correct value.
        """
        with pytest.warns(FutureWarning):
            result = await deprecated_async_positional_only_source(7)
        assert result == 7

    def test_two_positional_only_params_forwarded_in_order(self) -> None:
        """Two POSITIONAL_ONLY params are forwarded in declaration order.

        When ``positional_only_two_params_target`` is the target and both ``a`` and ``b``
        are positional-only, the split-dispatch must iterate parameters in declaration order
        (``a`` first, then ``b``) so ``deprecated_positional_only_two_params_source(10, 3)``
        returns the same value as ``positional_only_two_params_target(10, 3)`` — not a
        swapped or alphabetically-sorted dispatch.
        """
        with pytest.warns(FutureWarning):
            result = deprecated_positional_only_two_params_source(10, 3)
        assert result == positional_only_two_params_target(10, 3)
        assert result == 13

    def test_args_mapping_renames_before_positional_split(self) -> None:
        """args_mapping remap is applied before the POSITIONAL_ONLY split.

        A user calling ``deprecated_positional_only_with_args_mapping_source(old_x=5)``
        passes the deprecated argument name ``old_x``.  The wrapper must rename it to ``x``
        via ``args_mapping`` first, then split ``x`` out of ``resolved_kwargs`` and forward
        it positionally to ``positional_only_target``.  If the rename and split are reordered,
        ``x`` will not be in ``resolved_kwargs`` at split time and the call will fail with
        ``TypeError``.
        """
        with pytest.warns(FutureWarning):
            result = deprecated_positional_only_with_args_mapping_source(old_x=5)
        assert result == 5

    def test_stream_none_suppresses_warnings_but_call_still_forwards(self) -> None:
        """stream=None suppresses all warnings but split dispatch still forwards correctly.

        When ``@deprecated`` is configured with ``stream=None``, no ``UserWarning`` fires
        at decoration time and no ``FutureWarning`` fires at call time.  The underlying
        split dispatch must still execute so ``positional_only_target`` is called with ``x``
        as a positional arg (not as a kwarg), returning the correct value.
        ``deprecated_positional_only_stream_none`` is defined in ``api_deprecated_forwarding.py``
        with ``stream=None`` so no warnings are emitted at decoration time or call time.
        """
        result = deprecated_positional_only_stream_none(5)
        assert result == 5

    def test_call_succeeds_after_warning_quota_exhausted(self) -> None:
        """Split dispatch still forwards correctly after the FutureWarning quota is exhausted.

        With ``num_warns=1``, the first call emits ``FutureWarning``; the second call
        must still forward ``x`` positionally (the split dispatch must not be gated on
        the warning being emitted).  A caller silently migrating after the quota exhausts
        should not receive ``TypeError``.
        ``make_deprecated_positional_only_num_warns_one`` is defined in ``api_deprecated_forwarding.py``
        with ``num_warns=1`` so the warning fires exactly once.
        """
        quota_fn = make_deprecated_positional_only_num_warns_one()
        with pytest.warns(FutureWarning):
            result1 = quota_fn(5)
        assert result1 == 5

        result2 = quota_fn(10)  # no warning — quota exhausted
        assert result2 == 10

    def test_constructor_forwarding_positional_only_succeeds(self) -> None:
        """Constructor-forwarding path sets attribute correctly when target __init__ has a POSITIONAL_ONLY param.

        When ``@deprecated`` is applied to ``OldPositionalOnlyClass.__init__`` with
        ``target=PositionalOnlyTarget``, ``_normalize_target`` maps the class target to
        ``PositionalOnlyTarget.__init__`` (unbound).  The dispatch must include ``self`` in
        ``pos_args`` before ``new_val`` so that the unbound call
        ``PositionalOnlyTarget.__init__(instance, 5)`` succeeds — without the fix, ``5`` lands
        in the ``self`` slot positionally and ``self`` is also passed as a kwarg, raising
        ``TypeError: positional-only arguments passed as keyword arguments: 'self'``.
        """
        with pytest.warns(FutureWarning):
            obj = OldPositionalOnlyClass(5)
        assert obj.new_val == 5

    def test_self_only_positional_only_constructor_succeeds(self) -> None:
        """Constructor does not raise when self is the only POSITIONAL_ONLY target param.

        When ``@deprecated`` is applied to ``OldSelfOnlyClass.__init__`` with
        ``target=SelfOnlyPositionalOnlyTarget``, the target's only POSITIONAL_ONLY param
        is ``self``.  Before the fix, ``target_positional_only`` excluded ``self`` and was
        therefore an empty frozenset — the split-dispatch gate never fired, so the dispatcher
        called ``target_func(**{'self': instance})``, raising ``TypeError: positional-only
        arguments passed as keyword arguments: 'self'``.  The fix includes ``self``/``cls``
        in the stored set so the gate fires and the instance is forwarded positionally.
        """
        with pytest.warns(FutureWarning):
            obj = OldSelfOnlyClass()
        assert isinstance(obj, OldSelfOnlyClass)


class TestPositionalOnlySource:
    """``@deprecated`` on a *source* whose own signature declares POSITIONAL_ONLY parameters.

    NOTIFY (default) and ARGS_REMAP modes execute the source body.  The wrapper converts
    positional args to kwargs internally, so the dispatcher must split the source's
    positional-only params back out before calling ``source`` — otherwise every call in
    the default mode raises ``TypeError: got some positional-only arguments passed as
    keyword arguments``.
    """

    def test_notify_positional_call_executes_source(self) -> None:
        """Default warn-only mode executes a source with a ``/`` in its signature.

        A library author adds a plain ``@deprecated(deprecated_in=..., remove_in=...)``
        notice to an existing function that uses positional-only parameters.  Callers keep
        calling it exactly as before — the call must warn and return the source's result,
        not crash with ``TypeError`` on every invocation.
        """
        with pytest.warns(FutureWarning, match=r"deprecated_notify_positional_only_source"):
            result = deprecated_notify_positional_only_source(1)
        assert result == 3

    def test_notify_keyword_tail_preserved(self) -> None:
        """Keyword arguments after the ``/`` still reach the source alongside the positional split."""
        with pytest.warns(FutureWarning):
            result = deprecated_notify_positional_only_source(1, b=10)
        assert result == 11

    def test_args_remap_old_name_remapped(self) -> None:
        """ARGS_REMAP renames the deprecated kwarg and executes the positional-only source body.

        A caller still using the deprecated ``old_flag`` name on a function whose leading
        parameter is positional-only must get the rename warning and the correct result —
        the remapped kwargs dict must not swallow the positional-only ``a``.
        """
        with pytest.warns(FutureWarning, match=r"old_flag"):
            result = deprecated_args_remap_positional_only_source(1, old_flag=5)
        assert result == 6

    def test_args_remap_migrated_caller_short_circuits(self) -> None:
        """A migrated caller using the new kwarg name gets no warning and a working call.

        The migrated-caller fast path (short-circuit) also calls ``source(**kwargs)``
        internally, so it needs the same positional-only split as the warning path.
        """
        with assert_no_warnings(FutureWarning):
            result = deprecated_args_remap_positional_only_source(1, new_flag=5)
        assert result == 6

    @pytest.mark.asyncio
    async def test_async_notify_positional_call_executes_source(self) -> None:
        """The async dispatch twin executes an async source with a POSITIONAL_ONLY param.

        An async API deprecated with the plain notice must keep serving awaited calls;
        ``_invoke_async`` must split the positional-only params exactly like the sync path.
        """
        with pytest.warns(FutureWarning):
            result = await deprecated_async_notify_positional_only_source(1, b=10)
        assert result == 11

    @pytest.mark.asyncio
    async def test_async_args_remap_old_name_remapped(self) -> None:
        """The async ARGS_REMAP path renames the deprecated kwarg on a positional-only source."""
        with pytest.warns(FutureWarning, match=r"old_flag"):
            result = await deprecated_async_args_remap_positional_only_source(1, old_flag=5)
        assert result == 6


class TestGappedPositionalOnlyForwarding:
    """Forwarding to a target whose POSITIONAL_ONLY params are only partially supplied.

    ``gapped_positional_only_target(a=1, b=2, /, c=3)`` has defaulted positional-only
    params.  A source that supplies ``b`` but not ``a`` leaves a gap: positional binding
    would silently slide ``b``'s value into ``a``'s slot.  The split dispatch must raise
    ``TypeError`` instead of misbinding.
    """

    def test_gap_with_later_value_raises_type_error(self) -> None:
        """Supplying a later positional-only param while an earlier one is absent raises.

        A migration wrapper forwards only ``b`` to a target where ``a`` (also positional-only)
        precedes it.  Before the fix, ``b``'s value was silently bound to ``a`` — wrong data
        on every call.  The wrapper must now fail loudly at the call site.
        """
        with pytest.warns(FutureWarning), pytest.raises(TypeError, match=r"`a` was not supplied"):
            deprecated_gapped_positional_only_source()

    def test_full_prefix_forwards_in_order(self) -> None:
        """Supplying every positional-only param forwards each value to its own slot."""
        with pytest.warns(FutureWarning):
            result = deprecated_gapped_positional_only_full_source(9, 8)
        assert result == {"a": 9, "b": 8, "c": 3}

    def test_trailing_gap_uses_target_defaults(self) -> None:
        """A gap with no later positional-only value present falls back to the target defaults.

        A source declaring only ``a`` (the first positional-only target param) leaves a
        *trailing* gap at ``b`` — safe, because no later positional-only value can slide
        into the wrong slot.  ``b`` and ``c`` keep their target-side defaults, so the call
        must succeed rather than raise.
        """
        with pytest.warns(FutureWarning):
            result = deprecated_gapped_positional_only_first_source(4)
        assert result == {"a": 4, "b": 2, "c": 3}

    @pytest.mark.asyncio
    async def test_async_gap_with_later_value_raises_type_error(self) -> None:
        """The async dispatch twin raises the same TypeError on a positional-only gap."""
        with pytest.warns(FutureWarning), pytest.raises(TypeError, match=r"`a` was not supplied"):
            await deprecated_async_gapped_positional_only_source()


class TestCycleDetection:
    """Lazy runtime cycle detection — RuntimeError before RecursionError."""

    def test_cycle_entry_via_a_raises(self) -> None:
        """Calling dep_cycle_fn_a raises RuntimeError when target chain loops back through dep_cycle_fn_b.

        dep_cycle_fn_a forwards to dep_cycle_fn_b which forwards back to dep_cycle_fn_a,
        forming a two-node cycle. The lazy cycle detector intercepts the re-entry of
        dep_cycle_fn_a and raises RuntimeError instead of letting Python hit RecursionError.
        The guard fires only on that re-entry, so the first entry of each node still emits its FutureWarning.
        """
        with (
            pytest.warns(FutureWarning, match="dep_cycle_fn_"),
            pytest.raises(RuntimeError, match=r"Circular deprecation cycle detected.*dep_cycle_fn_a"),
        ):
            dep_cycle_fn_a(1)

    def test_cycle_entry_via_b_raises(self) -> None:
        """Calling dep_cycle_fn_b raises RuntimeError when target chain loops back through dep_cycle_fn_a.

        A maintainer renames a second deprecated symbol to point at the same cycle. When
        the entry point is dep_cycle_fn_b instead of dep_cycle_fn_a, the guard must fire
        at dep_cycle_fn_b's re-entry and name dep_cycle_fn_b in the error message, proving
        the detector is symmetric and reports the correct wrapper regardless of entry order.
        """
        with (
            pytest.warns(FutureWarning, match="dep_cycle_fn_"),
            pytest.raises(RuntimeError, match=r"Circular deprecation cycle detected.*dep_cycle_fn_b"),
        ):
            dep_cycle_fn_b(1)

    def test_non_cycle_callable_target_unaffected(self) -> None:
        """A deprecated wrapper with a non-cycling callable target is unaffected by cycle detection.

        When a deprecated wrapper's target is a plain (non-deprecated) function, the cycle
        detection set is populated then cleaned up via the finally-block without raising.
        Repeated calls must not accumulate stale id entries, so the second call succeeds
        identically to the first.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            assert dep_non_cycle_old_fn(3) == 6
            assert dep_non_cycle_old_fn(3) == 6  # second call — no stale id in active set

    @pytest.mark.asyncio
    async def test_async_cycle_entry_via_a_raises(self) -> None:
        """Awaiting dep_async_cycle_fn_a raises RuntimeError when the async target chain cycles.

        An async deprecated wrapper (routed through async_wrapped_fn) that forms a
        two-node cycle with dep_async_cycle_fn_b must raise RuntimeError on re-entry,
        proving the cycle guard in the async code path fires correctly.
        """
        with (
            pytest.warns(FutureWarning, match="dep_async_cycle_fn_"),
            pytest.raises(RuntimeError, match=r"Circular deprecation cycle detected.*dep_async_cycle_fn_a"),
        ):
            await dep_async_cycle_fn_a(1)

    @pytest.mark.asyncio
    async def test_async_cycle_entry_via_b_raises(self) -> None:
        """Awaiting dep_async_cycle_fn_b raises RuntimeError when the async target chain cycles.

        Mirror of test_async_cycle_entry_via_a_raises — the async cycle is symmetric, so
        entering from dep_async_cycle_fn_b must also trigger the guard. The error message
        must name dep_async_cycle_fn_b as the re-entered wrapper.
        """
        with (
            pytest.warns(FutureWarning, match="dep_async_cycle_fn_"),
            pytest.raises(RuntimeError, match=r"Circular deprecation cycle detected.*dep_async_cycle_fn_b"),
        ):
            await dep_async_cycle_fn_b(1)

    @pytest.mark.asyncio
    async def test_concurrent_async_calls_no_false_positive(self) -> None:
        """Concurrent asyncio.gather calls to the same async deprecated wrapper must all succeed.

        A production service may fan out several concurrent requests to a single deprecated
        async endpoint (e.g. via asyncio.gather). Before the ContextVar fix, threading.local
        caused every overlapping call beyond the first to raise a spurious RuntimeError because
        all coroutines on the same event-loop thread shared one active-id set. With ContextVar,
        each asyncio.Task gets its own copy, so concurrent calls are fully independent.
        """
        with pytest.warns(FutureWarning, match="dep_async_non_cycle_old_fn"):
            results = await asyncio.gather(
                dep_async_non_cycle_old_fn(1), dep_async_non_cycle_old_fn(2), dep_async_non_cycle_old_fn(3)
            )
        assert results == [2, 4, 6]

    @pytest.mark.asyncio
    async def test_sibling_tasks_spawned_inside_guarded_call_are_independent(self) -> None:
        """Sibling tasks gathered inside a guarded call never see each other's entries as a cycle.

        A deprecated endpoint forwards to a handler that fans out two concurrent calls of another deprecated
        coroutine, both of which stay in flight across a task switch.  The tasks copy the outer call's context; if they
        also shared its mutable active set, the second sibling would find the first one's entry there and raise a
        spurious "Circular deprecation cycle" although the call chain never loops.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = await dep_async_sibling_pair_old_fn(1)

        assert result == [2, 4]

    @pytest.mark.asyncio
    async def test_reentry_from_spawned_task_still_raises(self) -> None:
        """A wrapper called again from a task spawned inside its own in-flight call is still a cycle.

        The task copies the context of the call that is still running, so it inherits that call's entry: a target
        chain that loops back through a new task is as circular as one that loops back directly, and must raise
        instead of recursing until the event loop is exhausted.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            with pytest.raises(RuntimeError, match=r"Circular deprecation cycle detected.*dep_async_task_cycle_fn"):
                await dep_async_task_cycle_fn(1)


class TestRecursiveDeprecation:
    """Deprecated wrappers on recursive functions — warning fires once, recursion converges."""

    @pytest.mark.parametrize(
        ("n", "expected"),
        [
            pytest.param(0, 0, id="base-zero"),
            pytest.param(1, 1, id="base-one"),
            pytest.param(6, 8, id="fib-six"),
        ],
    )
    def test_notify_recursive_returns_correct_result(self, n: int, expected: int) -> None:
        """A recursive deprecated function in NOTIFY mode computes the correct result.

        A real Fibonacci function decorated with ``@deprecated`` (NOTIFY mode) calls itself
        recursively through the wrapper for each sub-problem. The decorator must not interfere
        with the recursion — the result must equal the standard Fibonacci value.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            assert dep_fib_notify(n) == expected

    def test_notify_recursive_warns_exactly_once(self) -> None:
        """A recursive deprecated function in NOTIFY mode emits exactly one warning.

        Even though the wrapper is re-entered on every recursive call (25 total calls for
        ``fib(6)``), ``num_warns=1`` (default) tracks the count in shared mutable state so
        only the first call emits a ``FutureWarning``. Subsequent recursive calls see
        ``warned_calls >= num_warns`` and skip the warning.
        """
        with captured_warnings() as caught:
            dep_fib_notify(6)
        assert len(caught) == 1
        assert caught[0].category is FutureWarning

    def test_notify_recursive_num_warns_zero_suppresses_all(self) -> None:
        """``num_warns=0`` suppresses all warnings even for a deeply recursive deprecated function.

        When the deprecation wrapper is configured with ``num_warns=0``, no warning must fire
        regardless of how many recursive re-entries occur.
        """
        with assert_no_warnings(FutureWarning):
            result = dep_fib_silent(6)
        assert result == 8

    @pytest.mark.parametrize(
        ("n", "expected"),
        [
            pytest.param(0, 0, id="base-zero"),
            pytest.param(1, 1, id="base-one"),
            pytest.param(6, 8, id="fib-six"),
        ],
    )
    def test_callable_target_self_recursive_warns_once_returns_correct_result(self, n: int, expected: int) -> None:
        """A deprecated wrapper whose target is a self-recursive function warns once and returns correctly.

        ``dep_fib_callable`` forwards to ``fib_recursive``, which recurses on itself directly
        without re-entering the deprecated wrapper. Cycle detection does not false-positive:
        ``id(source)`` is added to ``_active`` once and removed in the ``finally`` block; only
        one ``FutureWarning`` fires.
        """
        with captured_warnings() as caught:
            result = dep_fib_callable(n)
        assert result == expected
        assert len(caught) == 1
        assert caught[0].category is FutureWarning

    def test_callable_target_repeated_calls_no_stale_state(self) -> None:
        """A second independent call succeeds with no stale ``id(source)`` entries and no new warning.

        After the first call cleans up via the ``finally`` block, the cycle-detection set must be
        empty so a second call succeeds identically.  Since ``num_warns=1`` is already satisfied
        after the first call, the second call emits no additional ``FutureWarning`` instances.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            assert dep_fib_callable(5) == 5
        with captured_warnings() as caught:
            assert dep_fib_callable(5) == 5
        assert len(caught) == 0

    @pytest.mark.parametrize(
        ("n", "expected"),
        [
            pytest.param(0, 0, id="base-zero"),
            pytest.param(1, 1, id="base-one"),
            pytest.param(6, 8, id="fib-six"),
        ],
    )
    def test_args_remap_recursive_returns_correct_result(self, n: int, expected: int) -> None:
        """A recursive ARGS_REMAP deprecated function remaps the deprecated arg on every call and converges.

        ``dep_fib_remap`` accepts the deprecated ``n`` argument and remaps it to ``x`` on each
        recursive call. The recursion must still produce the correct Fibonacci value even though
        every level passes ``n`` (the deprecated name) and the wrapper remaps it to ``x``.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            assert dep_fib_remap(n=n) == expected

    def test_args_remap_recursive_warns_exactly_once(self) -> None:
        """A recursive ARGS_REMAP wrapper emits exactly one warning for the initial call.

        Even though the wrapper re-enters on each recursive step and applies the arg remap,
        ``num_warns=1`` (default) ensures only the first re-entry of the deprecated argument
        ``n`` triggers a ``FutureWarning``. Subsequent recursive calls see ``state.warned_args['n']``
        already satisfied and proceed silently.
        """
        with captured_warnings() as caught:
            dep_fib_remap(n=6)
        assert len(caught) == 1
        assert caught[0].category is FutureWarning


class TestSharedNameDefaultForwarding:
    """A non-renamed shared parameter forwards the source's own default (the migrated-from contract).

    The *target*'s default winning here was assessed as a
    misdiagnosis — forwarding the source default is the intended, documented behaviour (``decorated_sum`` /
    ``test_functions.py::test_default`` enforce the same contract).  These tests pin it against regression.
    """

    def test_neither_supplied_forwards_source_default(self) -> None:
        """With no ``args_mapping`` and only ``x`` supplied, the source's ``level=1`` is forwarded, not target's 99.

        ``fn_shared_default`` forwards to ``fn_shared_default_target``; both declare ``level`` but with diverging
        defaults (source 1, target 99).  The source signature is the contract the caller knows, so its default
        reaches the target body.  Only the *renamed* path drops stale defaults (see ``fn_old_default``).
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = fn_shared_default(5)
        assert result == 1

    def test_explicit_value_overrides_both_defaults(self) -> None:
        """A caller-supplied ``level`` reaches the target unchanged, overriding both source and target defaults."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = fn_shared_default(5, level=3)
        assert result == 3


class TestTargetFactsPrecompute:
    """Decoration-time-cached target signature facts feed the call-time kwarg validation."""

    def test_config_caches_target_signature_facts(self) -> None:
        """A callable-target wrapper stores the target's param names and var-arg flags.

        These facts previously came from an uncached ``inspect.getfullargspec`` on every forwarded call;
        caching them on the frozen config removes that per-call cost.
        """
        cfg = cast(_DeprecatedCallable, fn_shared_default).__deprecation_config__
        assert cfg.target_all_param_names == frozenset({"x", "level"})
        assert cfg.target_accepts_var_positional is False
        assert cfg.target_accepts_var_keyword is False

    def test_precompute_reports_var_args_and_kwargs(self) -> None:
        """`_signature_facts` flags ``*args`` and ``**kwargs`` and collects every parameter name."""

        def _tgt(a: int, b: int = 2, *rest: int, **extra: int) -> None:
            """Throwaway signature carrying every parameter kind for the precompute helper."""

        facts = _signature_facts(_tgt)
        assert facts.names == frozenset({"a", "b", "rest", "extra"})
        assert facts.var_positional is True
        assert facts.var_keyword is True

    def test_precompute_empty_for_non_callable_target(self) -> None:
        """A :class:`TargetMode` (non-callable) target yields empty facts — the dispatcher never forwards to it."""
        facts, _ = _target_signature_facts(TargetMode.NOTIFY)
        assert facts.names == frozenset()
        assert (facts.var_positional, facts.var_keyword) == (False, False)

    def test_repr_excludes_cache_fields(self) -> None:
        """The precomputed cache fields stay out of ``repr`` so audit output and doc examples remain stable."""
        text = repr(cast(_DeprecatedCallable, fn_shared_default).__deprecation_config__)
        assert "target_all_param_names" not in text
        assert "target_accepts_var_keyword" not in text

    def test_cache_fields_excluded_from_equality(self) -> None:
        """Two configs differing only in cached target facts compare equal — caches are not identity.

        The cache fields carry ``compare=False`` so stored-config equality assertions (and audit comparisons) are
        unaffected by the perf machinery.
        """
        plain = DeprecationConfig(deprecated_in="1.0", remove_in="2.0", name="x")
        with_cache = DeprecationConfig(
            deprecated_in="1.0",
            remove_in="2.0",
            name="x",
            target_all_param_names=frozenset({"a"}),
            target_accepts_var_keyword=True,
        )
        assert plain == with_cache

    def test_prepare_target_call_uses_supplied_facts(self) -> None:
        """`_prepare_target_call` validates against supplied facts and raises the curated TypeError on a bad kwarg."""

        def _tgt(a: int, b: int) -> None:
            """Two-parameter target that does not accept ``c``."""

        with pytest.raises(TypeError, match="not accepted by target"):
            _prepare_target_call(
                _tgt,
                _tgt,
                {"c": 1},
                target_arg_names=frozenset({"a", "b"}),
                accepts_var_positional=False,
                accepts_var_keyword=False,
            )


def _raise_signature_errors(facts: _CallFacts, times: int) -> list[BaseException]:
    """Run the forwarded-call signature error ``times`` times and return each error that reached the caller."""
    raised: list[BaseException] = []
    for _ in range(times):
        with pytest.raises((TypeError, ValueError)) as caught:
            _raise_target_signature_error(facts)
        raised.append(caught.value)
    return raised


def _traceback_depth(error: BaseException) -> int:
    """Return the number of frames recorded in the traceback of ``error``."""
    return len(list(traceback.walk_tb(error.__traceback__)))


_SIGNATURE_ERROR_CASES = [
    pytest.param(ValueError, ("no signature found",), ValueError, id="copyable"),
    pytest.param(SignatureNotBuiltError, ("lazy", "not built yet"), SignatureNotBuiltError, id="rebuilt-without-init"),
    pytest.param(SignatureNeedsContextError, ("lazy", "not built yet"), ValueError, id="base-type-fallback"),
]


class TestRaiseTargetSignatureError:
    """A forwarded call raises a fresh copy of the signature error stored at decoration, never the stored object."""

    @pytest.mark.parametrize(("error_type", "error_args", "expected_type"), _SIGNATURE_ERROR_CASES)
    def test_stored_error_is_never_raised(
        self, error_type: type[Exception], error_args: tuple[str, ...], expected_type: type[Exception]
    ) -> None:
        """Every forwarded call raises a new object and the stored error is left without a traceback.

        A library whose signature error cannot be rebuilt from its ``args`` used to be raised as stored: the one
        wrapper-lifetime object collected a longer traceback on every call and had its ``__context__`` overwritten
        by whichever thread raised it last.  Whatever way the copy is made, the stored object stays untouched.
        """
        stored = error_type(*error_args)
        raised = _raise_signature_errors(_CallFacts(target_signature_error=stored), 3)

        assert (
            [error is stored for error in raised],
            stored.__traceback__,
            len({id(error) for error in raised}),
        ) == ([False, False, False], None, 3)

    @pytest.mark.parametrize(("error_type", "error_args", "expected_type"), _SIGNATURE_ERROR_CASES)
    def test_traceback_length_stays_constant(
        self, error_type: type[Exception], error_args: tuple[str, ...], expected_type: type[Exception]
    ) -> None:
        """Repeated forwarded calls raise errors whose tracebacks are equally long.

        A service keeps calling a deprecated function whose target signature is unreadable and catches the error
        each time.  A traceback that grows by the frames of every call leaks memory for the life of the wrapper.
        """
        raised = _raise_signature_errors(_CallFacts(target_signature_error=error_type(*error_args)), 4)

        assert len({_traceback_depth(error) for error in raised}) == 1

    @pytest.mark.parametrize(("error_type", "error_args", "expected_type"), _SIGNATURE_ERROR_CASES)
    def test_raised_error_keeps_type_and_message(
        self, error_type: type[Exception], error_args: tuple[str, ...], expected_type: type[Exception]
    ) -> None:
        """The raised error has the stored type, or the base type it derives from, and the stored message.

        Callers catch the documented base type (``TypeError`` or ``ValueError``); a framework's own subclass is kept
        whenever it can be rebuilt, and degrades to that base type with its message when it cannot.
        """
        stored = error_type(*error_args)
        raised = _raise_signature_errors(_CallFacts(target_signature_error=stored), 1)[0]

        assert (type(raised), str(raised)) == (expected_type, str(stored))

    def test_rebuilt_error_keeps_its_attributes(self) -> None:
        """An error rebuilt without calling its constructor still carries the attributes it was created with.

        A framework error that records why the signature is missing in an attribute must expose it on the forwarded
        call exactly as on the error read at decoration.
        """
        stored = SignatureNotBuiltError("lazy", "not built yet")

        raised = _raise_signature_errors(_CallFacts(target_signature_error=stored), 1)[0]

        assert getattr(raised, "reason", None) == "not built yet"

    def test_no_stored_error_raises_nothing(self) -> None:
        """Facts without a stored signature error leave the forwarded call alone.

        A target whose signature was read at decoration (or hand-built facts) has nothing to re-raise, so the call
        returns instead of raising; an exception here fails the test.
        """
        _raise_target_signature_error(_CallFacts())


def _make_context(
    source: Callable[..., Any],
    cfg: DeprecationConfig,
    target: Union[Callable[..., Any], TargetMode],
    *,
    is_stacked: bool = False,
    forward_defaults: Optional[_ForwardDefaults] = None,
) -> _WrapperContext:
    """Return the decoration-time context a ``@deprecated`` wrapper over ``source`` would build, without a stream.

    The facts come from the real :func:`_build_call_facts` and the state is fresh; ``stream=None`` keeps
    :func:`_build_call_plan` from emitting warnings, so a test only reads the plan and ``ctx.state``.

    """
    return _WrapperContext(
        source=source,
        raw_target=target,
        target=target,
        cfg=cfg,
        facts=_build_call_facts(source, target, None),
        state=_WrapperState(),
        stream=None,
        num_warns=1,
        skip_if=False,
        is_stacked=is_stacked,
        forward_defaults=forward_defaults,
    )


# ---------------------------------------------------------------------------
# Callable-target dispatch — happy path
# ---------------------------------------------------------------------------


def test_callable_target_round_trip_returns_target_func() -> None:
    """Callable target with matching kwargs returns ``short_circuit=False`` and the resolved ``target_func``."""
    cfg = DeprecationConfig(deprecated_in="1.0", remove_in="2.0", name="src", target=double_value)
    ctx = _make_context(double_value, cfg, double_value)
    plan = _build_call_plan(ctx, (), {"x": 3})

    assert plan.short_circuit is False
    assert plan.target_func is double_value
    assert plan.resolved_kwargs == {"x": 3}
    assert plan.reason_argument == {}


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
        deprecated_in="1.0", remove_in="2.0", name="src", target=TargetMode.ARGS_REMAP, args_mapping={"old_x": "x"}
    )
    ctx = _make_context(identity_value, cfg, TargetMode.ARGS_REMAP)
    plan = _build_call_plan(ctx, (), {"x": 7})  # caller already migrated — uses new name

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
    ctx = _make_context(identity_value, cfg, TargetMode.NOTIFY)
    plan = _build_call_plan(ctx, (), {"x": 5})

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
    :func:`deprecate._dispatch._warn_misconfigured_once` (called from
    :func:`deprecate._dispatch._build_call_plan`).  The flag is **never** reset by
    :mod:`tests.conftest` — it implements an intentional once-per-wrapper-lifetime contract
    so noisy misconfig warnings do not flood test output.

    This test exercises ``target_false_deprecation`` from
    :mod:`tests.collections.misconfigured`, which sets ``misconfigured=True`` via the
    legacy ``target=False`` sentinel.  We bypass the FutureWarning by filtering only the
    ``UserWarning`` instances at the call site (and we explicitly reset
    ``warned_misconfigured`` here so the test is independent of import order).

    """
    # Pre-reset the sticky flag so the test is order-independent: a prior import or test
    # may have already exhausted the one-time slot.  ``conftest._reset_api_deprecated_state``
    # intentionally does not touch ``warned_misconfigured`` (see its docstring).
    target_false_deprecation._state.warned_misconfigured = False  # type: ignore[attr-defined]
    target_false_deprecation._state.warned_calls = 0  # type: ignore[attr-defined]

    with captured_warnings() as call1:
        target_false_deprecation(x=1)

    with captured_warnings() as call2:
        target_false_deprecation(x=2)

    misconfig_call1 = [w for w in call1 if w.category is UserWarning and "invalid deprecation config" in str(w.message)]
    misconfig_call2 = [w for w in call2 if w.category is UserWarning and "invalid deprecation config" in str(w.message)]
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
    with pytest.warns(FutureWarning, match="uses deprecated arguments"):
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
    with captured_warnings() as call1:
        depr_pow_args(2.0, 3.0)

    with captured_warnings() as call2:
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

    The short-circuit gate (``_is_short_circuit``) compresses three conditions:
    no callable/arg reason, no ``args_extra`` injection, and ``not source_is_stacked``.  When the
    outer wrapper sits over an already-``@deprecated`` source — the canonical
    ``ARGS_REMAP``-outer + ``NOTIFY``-inner stack from :func:`make_depr_compute_power_stacked` —
    the inner layer still needs to run so its own ``FutureWarning`` fires.  Skipping that path
    when ``source_is_stacked=True`` would silently drop the inner warning.

    The companion test :func:`test_args_remap_migrated_caller_short_circuits` pins the inverse:
    same migrated-caller kwargs with ``source_is_stacked=False`` *do* short-circuit.

    Two assertions are checked in isolation here:

    * direct call to ``_build_call_plan`` with a stacked context (``is_stacked=True``) returns
      ``short_circuit=False`` even when no reason fires (the bypass);
    * end-to-end call to the real stacked wrapper from
      :func:`make_depr_compute_power_stacked` with the migrated arg name emits the inner
      ``NOTIFY`` ``FutureWarning`` and returns the correct value.

    """
    cfg = DeprecationConfig(
        deprecated_in="1.0", remove_in="2.0", name="src", target=TargetMode.ARGS_REMAP, args_mapping={"factor": "scale"}
    )
    # ``is_stacked=True``: the source itself carries @deprecated meta.
    ctx = _make_context(compute_power, cfg, TargetMode.ARGS_REMAP, is_stacked=True)
    plan = _build_call_plan(ctx, (), {"base": 2.0, "scale": 3.0})  # caller already migrated — uses new name

    assert plan.short_circuit is False, "source_is_stacked=True must bypass the migrated-caller short-circuit"
    assert plan.target_func is None, "ARGS_REMAP never resolves a callable target_func"

    # End-to-end check: the real ARGS_REMAP-outer + NOTIFY-inner stack must still emit the
    # inner NOTIFY warning and return the correct value when the caller migrates to ``scale=``.
    fn = make_depr_compute_power_stacked()
    with pytest.warns(FutureWarning, match="compute_power"):
        result = fn(2.0, scale=3.0)
    assert result == 8.0, "Stacked wrapper must compute compute_power(2.0, scale=3.0) == 8.0"


# ---------------------------------------------------------------------------
# Forward-default facts — precomputed once per wrapper instead of on every forwarded call
# ---------------------------------------------------------------------------


class TestForwardDefaultsPrecompute:
    """Per-wrapper facts that decide which source defaults a forwarded call must not send to the target."""

    @pytest.mark.parametrize(
        ("target", "mapping", "stale_renamed", "partial_bound"),
        [
            pytest.param(
                fn_with_default,
                {"old_arg": "new_arg"},
                {"old_arg", "new_arg"},
                set(),
                id="rename-onto-target-default",
            ),
            pytest.param(partial_target, None, set(), {"y"}, id="partial-bound-keyword"),
            pytest.param(nested_partial_target, None, set(), {"y"}, id="nested-partial-inner-layer"),
            pytest.param(double_value, None, set(), set(), id="plain-callable-without-mapping"),
            pytest.param(
                TargetMode.ARGS_REMAP, {"old_arg": "new_arg"}, set(), set(), id="target-mode-forwards-nothing"
            ),
        ],
    )
    def test_collects_names_a_forwarded_call_must_not_resend(
        self,
        target: Union[Callable[..., Any], TargetMode],
        mapping: Optional[dict[str, Optional[str]]],
        stale_renamed: set[str],
        partial_bound: set[str],
    ) -> None:
        """The facts name the source defaults a rename makes stale and the keywords a partial target binds.

        A library maintainer renames ``old_arg`` to ``new_arg`` where the replacement already defaults
        ``new_arg``, or forwards to ``partial(new_scale, y=5)``. Both inputs are fixed once the function is
        decorated, so the dispatcher derives the names one time and every later call reuses them.
        """
        facts = _precompute_forward_defaults(target, mapping)

        assert facts == _ForwardDefaults(frozenset(stale_renamed), frozenset(partial_bound))

    def test_unreadable_target_signature_yields_no_facts(self) -> None:
        """A target whose signature cannot be read gives ``None`` so decoration still succeeds.

        A C-level callable can refuse ``inspect.signature``. Today the failure surfaces on the first forwarded call
        that carries an ``args_mapping``; computing the facts when the function is decorated must not move that
        failure to import time, so the call-time path stays responsible for it.
        """

        class _OpaqueTarget:
            @property
            def __signature__(self) -> inspect.Signature:
                raise ValueError("no signature found")

            def __call__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
                """Accept anything; only the unreadable signature matters here."""

        assert _precompute_forward_defaults(_OpaqueTarget(), {"old_arg": "new_arg"}) is None

    def test_decorated_call_reuses_decoration_time_facts(self) -> None:
        """A forwarded call through a decorated function never re-derives the facts.

        ``depr_scale_partial_every_call`` forwards to ``partial(new_scale, y=5)`` on every call. The decorator computed
        its facts when it wrapped the function, so a hot loop of calls must not walk the target signature or the
        partial chain again; the call still drops the stale source default and lets the partial's ``y=5`` win.
        """
        with mock.patch("deprecate._dispatch._derive_forward_defaults") as derive, pytest.warns(FutureWarning):
            result = depr_scale_partial_every_call(3)

        assert (derive.call_count, result) == (0, 15)

    @pytest.mark.parametrize(
        ("target", "mapping", "kwargs", "expected"),
        [
            pytest.param(
                fn_with_default, {"old_arg": "new_arg"}, {}, {"x": 0, "y": 0}, id="stale-renamed-default-dropped"
            ),
            pytest.param(partial_target, None, {}, {"x": 0, "old_arg": 1}, id="partial-bound-default-dropped"),
            pytest.param(partial_target, None, {"y": 7}, {"x": 0, "old_arg": 1, "y": 7}, id="caller-value-kept"),
            pytest.param(double_value, None, {}, {"x": 0, "old_arg": 1, "y": 0}, id="no-reason-keeps-every-default"),
        ],
    )
    def test_merge_gives_same_result_with_or_without_supplied_facts(
        self,
        target: Callable[..., Any],
        mapping: Optional[dict[str, Optional[str]]],
        kwargs: dict[str, Any],
        expected: dict[str, Any],
    ) -> None:
        """Passing the precomputed facts changes the speed of the merge, never its result.

        The decorator hands the facts it computed at decoration time to every call, while direct callers pass nothing
        and get them derived on the spot; both routes have to drop the same stale and partial-bound defaults.
        """
        facts = _precompute_forward_defaults(target, mapping)

        on_the_spot = _merge_source_defaults(
            _signature_facts(source_with_forward_defaults).defaults, target, dict(kwargs), mapping
        )
        precomputed = _merge_source_defaults(
            _signature_facts(source_with_forward_defaults).defaults, target, dict(kwargs), mapping, facts
        )

        assert (on_the_spot, precomputed) == (expected, expected)

    def test_merge_trusts_supplied_facts_over_the_target(self) -> None:
        """Supplied facts are used as given: the merge does not look at the target a second time.

        ``level`` is a plain shared parameter, so deriving the facts from ``fn_shared_default_target`` keeps its
        source default. Facts that mark ``level`` stale drop it, which can only happen if the supplied value wins.
        """
        facts = _ForwardDefaults(stale_renamed=frozenset({"level"}), partial_bound=frozenset())

        merged = _merge_source_defaults(
            _signature_facts(source_sharing_level_default).defaults, fn_shared_default_target, {"x": 5}, None, facts
        )

        assert merged == {"x": 5}

    def test_call_plan_forwards_supplied_facts_to_the_merge(self) -> None:
        """`_build_call_plan` hands the supplied facts down, so a wrapper computes them once at decoration.

        A forwarded call with ``level`` unset normally sends the source default ``level=1``; with facts that mark
        ``level`` stale the resolved keywords carry only ``x``, proving the plan used the facts it was given.
        """
        cfg = DeprecationConfig(deprecated_in="1.0", remove_in="2.0", name="src", target=fn_shared_default_target)
        facts = _ForwardDefaults(stale_renamed=frozenset({"level"}), partial_bound=frozenset())
        ctx = _make_context(source_sharing_level_default, cfg, fn_shared_default_target, forward_defaults=facts)

        plan = _build_call_plan(ctx, (), {"x": 5})

        assert plan.resolved_kwargs == {"x": 5}


# ---------------------------------------------------------------------------
# Decoration-time call facts, wrapper context and cycle guard
# ---------------------------------------------------------------------------


def _guarded_reentry_error(ctx: _WrapperContext) -> str:
    """Enter the cycle guard of ``ctx`` twice in one call stack and return the message of the second entry's error."""
    handle = _enter_cycle_guard(ctx)
    with pytest.raises(RuntimeError) as err:
        _enter_cycle_guard(ctx)
    _exit_cycle_guard(ctx, handle)
    return str(err.value)


def _guard_value_after_exit(ctx: _WrapperContext) -> Optional[tuple[_CycleEntry, ...]]:
    """Enter and exit the cycle guard of ``ctx`` once, then return what the guard's context variable holds."""
    _exit_cycle_guard(ctx, _enter_cycle_guard(ctx))
    return _cycle_detection.get()


def _context_copied_inside_guard(ctx: _WrapperContext) -> contextvars.Context:
    """Enter the cycle guard of ``ctx``, copy the current context while the call is in flight, then exit."""
    handle = _enter_cycle_guard(ctx)
    copied = contextvars.copy_context()
    _exit_cycle_guard(ctx, handle)
    return copied


def _chain_with_inner_inside_outer(outer: _WrapperContext, inner: _WrapperContext) -> tuple[int, ...]:
    """Enter the cycle guard of ``outer``, then of ``inner``, and return the source ids on the chain at that point."""
    outer_handle = _enter_cycle_guard(outer)
    inner_handle = _enter_cycle_guard(inner)
    chain = tuple(entry.source_id for entry in cast(tuple[_CycleEntry, ...], _cycle_detection.get()))
    _exit_cycle_guard(inner, inner_handle)
    _exit_cycle_guard(outer, outer_handle)
    return chain


async def _hold_guard_across_task_switch(ctx: _WrapperContext) -> set[int]:
    """Enter the cycle guard, yield to the event loop, and return the ids this task's chain holds before exiting."""
    handle = _enter_cycle_guard(ctx)
    await asyncio.sleep(0)
    active = {entry.source_id for entry in cast(tuple[_CycleEntry, ...], _cycle_detection.get())}
    _exit_cycle_guard(ctx, handle)
    return active


async def _gather_guarded_tasks(ctx: _WrapperContext) -> list[set[int]]:
    """Run two guarded tasks for the same wrapper concurrently and return each task's active set."""
    return list(await asyncio.gather(_hold_guard_across_task_switch(ctx), _hold_guard_across_task_switch(ctx)))


_FACTS_CFG = DeprecationConfig(deprecated_in="1.0", remove_in="2.0")


class TestBuildCallFacts:
    """Signature facts a wrapper derives once at decoration time so no call inspects a signature again."""

    @pytest.mark.parametrize(
        ("source", "target", "expected"),
        [
            pytest.param(
                positional_only_target,
                TargetMode.NOTIFY,
                _CallFacts(
                    source_positional_names=("x", "y"),
                    source_positional_only=frozenset({"x"}),
                    source_positional_only_order=("x", "y"),
                    target_all_param_names=frozenset(),
                    target_positional_names=(),
                ),
                id="positional-only-source",
            ),
            pytest.param(
                identity_value,
                positional_only_target,
                _CallFacts(
                    source_positional_names=("x",),
                    target_all_param_names=frozenset({"x", "y"}),
                    target_positional_only=frozenset({"x"}),
                    target_positional_only_order=("x", "y"),
                    target_positional_names=("x", "y"),
                ),
                id="positional-only-target",
            ),
            pytest.param(
                var_positional_target,
                double_value,
                _CallFacts(
                    source_positional_names=("a",),
                    source_has_var_positional=True,
                    source_var_positional_prefix=1,
                    target_all_param_names=frozenset({"x"}),
                    target_positional_names=("x",),
                ),
                id="var-positional-source",
            ),
            pytest.param(
                double_value,
                NewCls,
                _CallFacts(
                    source_positional_names=("x",),
                    target_all_param_names=frozenset({"c", "d", "kwargs"}),
                    target_accepts_var_keyword=True,
                    target_positional_names=("c", "d"),
                ),
                id="class-target-with-var-keyword",
            ),
            pytest.param(
                double_value,
                partial_target,
                _CallFacts(
                    source_positional_names=("x",),
                    target_all_param_names=frozenset({"x", "y"}),
                    target_positional_names=("x",),
                ),
                id="partial-target-binds-keyword",
            ),
        ],
    )
    def test_derives_source_and_target_facts(
        self, source: Callable[..., Any], target: Union[Callable[..., Any], TargetMode], expected: _CallFacts
    ) -> None:
        """Every source and target signature shape maps to the facts the call path reads.

        A deprecated function may declare ``/`` or ``*args`` and forward to a class whose ``__init__`` takes
        ``**kwargs`` or to a ``functools.partial`` that turns a bound argument keyword-only.  The decorator reads
        both signatures once; a wrong fact would silently misroute positionals on every later call.
        """
        assert _build_call_facts(source, target, None) == expected

    @pytest.mark.parametrize(
        ("source", "limit"),
        [
            pytest.param(positional_only_target, 2, id="fixed-positionals"),
            pytest.param(var_positional_target, None, id="var-positional-accepts-any"),
        ],
    )
    def test_limit_follows_the_source_signature(self, source: Callable[..., Any], limit: Optional[int]) -> None:
        """A source with ``*args`` has no limit; any other source accepts as many positionals as it names.

        Both the surplus-positional check and the call-plan fast path read this one value, so a source declaring
        ``*args`` is never rejected and a fixed-arity source is rejected on exactly one argument too many.
        """
        assert _build_call_facts(source, TargetMode.NOTIFY, None).source_positional_limit == limit


class TestWrapperContext:
    """The per-wrapper context the call path reads instead of re-deriving flags on every call."""

    @pytest.mark.parametrize(
        ("target", "remap_active", "cycle_guarded"),
        [
            pytest.param(TargetMode.NOTIFY, False, False, id="notify"),
            pytest.param(TargetMode.ARGS_REMAP, True, False, id="args-remap"),
            pytest.param(double_value, True, True, id="callable-target"),
        ],
    )
    def test_derives_flags_from_target(
        self, target: Union[Callable[..., Any], TargetMode], remap_active: bool, cycle_guarded: bool
    ) -> None:
        """``remap_active`` and ``cycle_guarded`` follow from the normalised target alone.

        ``args_mapping`` and ``args_extra`` apply to a self-rename and to a forwarding target but never to a warn-only
        wrapper, and only a forwarding target can lead back into itself, so only it runs under the cycle guard.
        """
        ctx = _make_context(identity_value, _FACTS_CFG, target)

        assert (ctx.remap_active, ctx.cycle_guarded) == (remap_active, cycle_guarded)


class TestCycleGuard:
    """Target-chain cycle detection, scoped per call stack and per asyncio task."""

    def test_reentry_in_same_call_stack_raises(self) -> None:
        """A wrapper entered again while it is still running in the same call stack raises ``RuntimeError``.

        A deprecated function whose target chain leads back to itself would recurse until Python's recursion limit;
        the guard names the wrapper instead.  The scenario runs in a fresh ``contextvars.Context`` so no guard state
        can leak into other tests.
        """
        ctx = _make_context(double_value, _FACTS_CFG, double_value)

        message = contextvars.Context().run(_guarded_reentry_error, ctx)

        assert message == (
            "Circular deprecation cycle detected: `double_value` re-entered via its own target chain."
            " Point to a non-deprecated final implementation."
        )

    def test_exit_restores_outer_value(self) -> None:
        """Leaving the outermost guarded call resets the context variable to its value before the call.

        The first guarded call in a call stack installs a chain holding its own entry; once it returns, a later
        unrelated call must start from an empty state again rather than inherit a stale chain.
        """
        ctx = _make_context(double_value, _FACTS_CFG, double_value)

        assert contextvars.Context().run(_guard_value_after_exit, ctx) is None

    def test_gathered_tasks_keep_separate_sets(self) -> None:
        """Two ``asyncio.gather`` tasks running the same wrapper concurrently never see each other's active set.

        An async service awaits the same deprecated coroutine from two tasks at once.  That is concurrency, not a
        cycle: each task holds the guard across a task switch, and neither may observe the other's entry.
        """
        ctx = _make_context(double_value, _FACTS_CFG, double_value)

        assert asyncio.run(_gather_guarded_tasks(ctx)) == [{id(double_value)}, {id(double_value)}]

    def test_copied_context_releases_finished_call(self) -> None:
        """A context copied while a guarded call was in flight does not report that call once it has finished.

        A target may snapshot its context to schedule later work, for example ``loop.call_soon`` with an explicit
        context.  When that work calls the same wrapper after the first call returned, it is a new call and not a
        re-entry, so the copy must not keep the finished call's entry alive.
        """
        ctx = _make_context(double_value, _FACTS_CFG, double_value)
        copied = contextvars.Context().run(_context_copied_inside_guard, ctx)

        message = copied.run(_guarded_reentry_error, ctx)

        assert message.startswith("Circular deprecation cycle detected: `double_value` re-entered")

    def test_unrelated_wrapper_in_copied_context_is_not_a_cycle(self) -> None:
        """A copied context that still holds an in-flight call lets a different wrapper run.

        While the first call is in flight, a thread or task started from its context calls another deprecated
        function.  Only a repeat of a wrapper already in flight is a cycle; the other wrapper just joins the chain.
        """
        outer = _make_context(double_value, _FACTS_CFG, double_value)
        inner = _make_context(identity_value, _FACTS_CFG, identity_value)

        assert contextvars.Context().run(_chain_with_inner_inside_outer, outer, inner) == (
            id(double_value),
            id(identity_value),
        )


# ---------------------------------------------------------------------------
# _split_positional_only_kwargs — slot-safe positional extraction
# ---------------------------------------------------------------------------


class TestSplitPositionalOnlyKwargs:
    """Unit contract of :func:`deprecate._dispatch._split_positional_only_kwargs`.

    The helper extracts positional-only values from a resolved kwargs dict in declaration
    order.  Positional binding at the call site is by *slot*, not by name, so a value
    supplied for a later positional-only parameter must never slide into the slot of an
    earlier, absent one — that case must raise ``TypeError``.
    """

    def test_contiguous_prefix_extracted_in_order(self) -> None:
        """All positional-only names present: values come back in declaration order."""
        pos_args, kw_args = _split_positional_only_kwargs(
            ("a", "b", "c"), {"a": 1, "b": 2, "c": 3}, frozenset({"a", "b"})
        )
        assert pos_args == [1, 2]
        assert kw_args == {"c": 3}

    def test_trailing_gap_is_safe(self) -> None:
        """An absent positional-only name with no later value present stops extraction cleanly."""
        pos_args, kw_args = _split_positional_only_kwargs(("a", "b", "c"), {"a": 1, "c": 3}, frozenset({"a", "b"}))
        assert pos_args == [1]
        assert kw_args == {"c": 3}

    def test_gap_before_present_value_raises(self) -> None:
        """A later positional-only value behind an absent earlier one raises instead of misbinding.

        Binding ``b``'s value positionally while ``a`` is absent would assign it to ``a``'s
        slot — silent wrong data at every call, which is strictly worse than the TypeError
        this machinery exists to prevent.
        """
        with pytest.raises(TypeError, match=r"`a` was not supplied"):
            _split_positional_only_kwargs(("a", "b", "c"), {"b": 2, "c": 3}, frozenset({"a", "b"}))

    def test_consumed_offset_skips_caller_filled_slots(self) -> None:
        """``consumed`` leading slots already filled by caller positionals are not treated as gaps.

        The proxy call path passes ``consumed=len(args)`` so a caller mixing positional args
        with a remapped kwarg (e.g. ``Alias(1, old_x=5)``) does not trip the gap guard on the
        slots its positional args already cover.
        """
        pos_args, kw_args = _split_positional_only_kwargs(("w", "x"), {"x": 5}, frozenset({"w", "x"}), consumed=1)
        assert pos_args == [5]
        assert kw_args == {}

    def test_leading_receiver_extracted_without_positional_only_flag(self) -> None:
        """A leading ``self`` receiver is extracted positionally even when not flagged positional-only."""
        instance = object()
        pos_args, kw_args = _split_positional_only_kwargs(("self", "x"), {"self": instance, "x": 5}, frozenset({"x"}))
        assert pos_args == [instance, 5]
        assert kw_args == {}


class TestWarnQuotaThreadSafety:
    """The warn quota holds under concurrency.

    ``_emit_call_warning`` (called from ``_build_call_plan``) reads the warn counter, decides whether to emit, and
    increments — a check-then-act sequence.  Without synchronisation, concurrent first calls all read
    ``warned_calls == 0``, all pass the ``num_warns`` gate, and each emit a warning.  The state lock makes the
    check-and-increment atomic so exactly ``num_warns`` emissions happen regardless of thread interleaving.
    """

    @pytest.fixture
    def _aggressive_thread_switching(self) -> Iterator[None]:
        """Force frequent GIL hand-offs so the check-then-act window is actually exercised, then restore it."""
        previous = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        yield
        sys.setswitchinterval(previous)

    @pytest.mark.usefixtures("_aggressive_thread_switching")
    def test_num_warns_one_emits_once_across_threads(self) -> None:
        """16 threads released together into a ``num_warns=1`` wrapper produce exactly one warning emission.

        A barrier releases all workers simultaneously so they contend on the quota at once; the counting stream
        sleeps briefly to widen the check-then-act window.  Before the lock this asserted 2-16 emissions; with the
        lock the count is a deterministic 1.
        """
        emissions: list[int] = []
        emit_lock = threading.Lock()

        def counting_stream(message: str, *args: object, **kwargs: object) -> None:
            """Record every emission; the tiny sleep is a yield point that exposes the race when unsynchronised."""
            time.sleep(0.001)
            with emit_lock:
                emissions.append(1)

        @deprecated(target=double_value, deprecated_in="1.0", remove_in="2.0", num_warns=1, stream=counting_stream)
        def racy(x: int) -> int:
            return void(x)

        n_threads = 16
        barrier = threading.Barrier(n_threads)

        def worker(_ignored: int) -> None:
            """Block until every thread has arrived, then hit the deprecated wrapper simultaneously."""
            barrier.wait()
            racy(1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as pool:
            list(pool.map(worker, range(n_threads)))
        assert len(emissions) == 1


class TestUserFacingTypeErrors:
    """Calls the dispatcher cannot forward fail with a curated ``TypeError`` that names the deprecated wrapper."""

    def test_async_target_behind_sync_wrapper_raises(self) -> None:
        """A sync ``@deprecated`` function pointing at an ``async def`` target refuses the call and says how to fix it.

        A team forwards a legacy sync helper to a new coroutine function. Calling the sync wrapper would hand back an
        un-awaited coroutine, so the call fails loudly after the deprecation warning, naming the target and telling the
        author to declare the wrapper ``async def`` or point it at a sync callable.

        """

        async def _async_double(x: int) -> int:
            """Coroutine replacement that a sync wrapper cannot await."""
            return x * 2

        @deprecated(target=_async_double, deprecated_in="1.0", remove_in="2.0")
        def legacy_double(x: int) -> int:
            return void(x)

        with (
            pytest.warns(FutureWarning, match="legacy_double"),
            pytest.raises(TypeError, match=r"Async target `.*_async_double` cannot be invoked from a sync") as err,
        ):
            legacy_double(2)
        assert "Declare `legacy_double` as `async def`, or replace the target with a sync callable" in str(err.value)

    def test_keyword_missing_from_var_positional_only_target_names_the_limitation(self) -> None:
        """A target taking only ``*args`` rejects an unmapped keyword and the message says ``*args`` cannot take it.

        The plain "arguments not accepted" text would send the author hunting for a missing parameter name; here the
        target does accept extra positionals, just never keywords, and the message must say so.

        """

        def _tgt(*args: int) -> int:
            """Target that accepts positionals only."""
            return sum(args)

        def _src(a: int) -> int:
            return a

        expected = r"`_src`, arguments not accepted by target \(target accepts \*args but these keyword"
        with pytest.raises(TypeError, match=expected):
            _prepare_target_call(
                _src,
                _tgt,
                {"c": 1},
                target_arg_names=frozenset(),
                accepts_var_positional=True,
                accepts_var_keyword=False,
            )
