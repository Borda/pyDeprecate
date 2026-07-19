"""Unit tests for the target-resolution and call-plan engine (:mod:`deprecate._dispatch`)."""

import asyncio
import concurrent.futures
import inspect
import sys
import threading
import time
import warnings
from collections.abc import Iterator
from typing import Any, Callable, cast

import pytest

from deprecate import TargetMode, assert_no_warnings, deprecated, void
from deprecate._dispatch import (
    POSITIONAL_OR_KEYWORD,
    _build_call_plan,
    _find_class_body_qualname,
    _get_positional_params,
    _normalize_target,
    _precompute_target_facts,
    _prepare_target_call,
    _reject_bare_decorator,
    _split_positional_only_kwargs,
    _update_kwargs_with_args,
    _update_kwargs_with_defaults,
)
from deprecate._types import DeprecationConfig, _DeprecatedCallable, _WrapperState
from tests.collection_deprecate import (
    CrossGuardModuleLevel,
    CrossGuardOldClass,
    CrossGuardSameClass,
    OldPositionalOnlyClass,
    OldSelfOnlyClass,
    StaticGuardNewClass,
    StaticGuardOldClass,
    dep_async_cycle_fn_a,
    dep_async_cycle_fn_b,
    dep_async_non_cycle_old_fn,
    dep_cycle_fn_a,
    dep_cycle_fn_b,
    dep_fib_callable,
    dep_fib_notify,
    dep_fib_remap,
    dep_fib_silent,
    dep_non_cycle_old_fn,
    depr_pow_args,
    depr_target_mode_args_only_with_args_extra_injects_kwargs,
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
    make_depr_args_remap_notify_with_extra,
    make_depr_compute_power_stacked,
    make_depr_notify_callable_stacked,
    make_deprecated_positional_only_num_warns_one,
)
from tests.collection_misconfigured import target_false_deprecation
from tests.collection_targets import (
    KeywordCallTarget,
    call_signature_source,
    compute_power,
    double_value,
    identity_value,
    positional_only_target,
    positional_only_two_params_target,
    stacked_inner_target,
    stacked_outer_target,
)


class TestGetPositionalParams:
    """Tests for _get_positional_params — filters a param list to POSITIONAL_OR_KEYWORD and POSITIONAL_ONLY kinds."""

    def test_returns_only_positional_params(self) -> None:
        """Keyword-only params (after *) are excluded; positional params are returned."""

        def my_func(a: int, b: str, *, kw_only: int = 0) -> None:
            pass

        params = list(inspect.signature(my_func).parameters.values())
        result = _get_positional_params(params)
        assert [p.name for p in result] == ["a", "b"]

    def test_excludes_var_positional_and_var_keyword(self) -> None:
        """*args and **kwargs are excluded; only the plain positional param is returned."""

        def my_func(a: int, *args: int, **kwargs: int) -> None:
            pass

        params = list(inspect.signature(my_func).parameters.values())
        result = _get_positional_params(params)
        assert [p.name for p in result] == ["a"]

    def test_empty_params(self) -> None:
        """A function with no parameters returns an empty list."""

        def my_func() -> None:
            pass

        params = list(inspect.signature(my_func).parameters.values())
        assert _get_positional_params(params) == []

    def test_all_kinds_filtered(self) -> None:
        """Confirms POSITIONAL_OR_KEYWORD is kept while KEYWORD_ONLY is dropped."""

        def my_func(pos_or_kw: int, *, kw_only: int) -> None:
            pass

        params = list(inspect.signature(my_func).parameters.values())
        assert params[0].kind == POSITIONAL_OR_KEYWORD
        assert params[1].kind == inspect.Parameter.KEYWORD_ONLY
        result = _get_positional_params(params)
        assert len(result) == 1
        assert result[0].name == "pos_or_kw"


class TestUpdateKwargsWithArgs:
    """Tests for _update_kwargs_with_args — merges positional call args into the kwargs dict by param name."""

    def test_no_positional_args_returns_kwargs_unchanged(self) -> None:
        """When no positional args are passed, the existing kwargs dict is returned as-is."""

        def my_func(a: int, b: int) -> None:
            pass

        result = _update_kwargs_with_args(my_func, (), {"a": 1})
        assert result == {"a": 1}

    def test_maps_positional_to_param_names(self) -> None:
        """Positional args are matched to param names in declaration order and added to kwargs."""

        def my_func(a: int, b: str, c: float = 3.0) -> None:
            pass

        result = _update_kwargs_with_args(my_func, (1, "hello"), {})
        assert result == {"a": 1, "b": "hello"}

    def test_merges_with_existing_kwargs(self) -> None:
        """Positional args are merged with already-present keyword args without overwriting them."""

        def my_func(a: int, b: int, c: int = 0) -> None:
            pass

        result = _update_kwargs_with_args(my_func, (10,), {"c": 99})
        assert result == {"a": 10, "c": 99}

    def test_stops_at_var_positional(self) -> None:
        """Extra positional args beyond a *args boundary are not mapped to named params."""

        def my_func(a: int, *args: int) -> None:
            pass

        result = _update_kwargs_with_args(my_func, (1, 2, 3), {})
        assert result == {"a": 1}

    def test_too_many_positional_raises_type_error(self) -> None:
        """Passing more positional args than the function has positional params raises TypeError."""

        def my_func(a: int, b: int) -> None:
            pass

        with pytest.raises(TypeError, match="takes 2 positional"):
            _update_kwargs_with_args(my_func, (1, 2, 3), {})


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

        result = _update_kwargs_with_defaults(my_func, {})
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_provided_kwargs_override_defaults(self) -> None:
        """Explicitly provided kwargs take precedence over the function's own defaults."""

        def my_func(a: int = 1, b: int = 2, c: int = 3) -> None:
            pass

        result = _update_kwargs_with_defaults(my_func, {"b": 20})
        assert result == {"a": 1, "b": 20, "c": 3}

    def test_params_without_defaults_not_included(self) -> None:
        """Required parameters (no default) are not injected into kwargs."""

        def my_func(required: int, optional: int = 5) -> None:
            pass

        result = _update_kwargs_with_defaults(my_func, {})
        assert result == {"optional": 5}
        assert "required" not in result

    def test_no_defaults_returns_provided_kwargs(self) -> None:
        """When the function has no defaults at all, the input kwargs dict is returned unchanged."""

        def my_func(a: int, b: str) -> None:
            pass

        result = _update_kwargs_with_defaults(my_func, {"a": 7})
        assert result == {"a": 7}


class TestCrossClassMethodGuard:
    """@deprecated warns when target is a method on a different class."""

    def test_raises_for_cross_class_method_target(self) -> None:
        """Forwarding to a method on a different class raises TypeError at decoration time.

        The misconfigured classes are defined inline (not in collection_deprecate.py) because placing ``@deprecated``
        with a cross-class target at module level would raise TypeError at import time for every test that imports the
        collection module.

        """

        class OtherClass:
            def other_method(self, x: int) -> int:
                return x

        with pytest.raises(TypeError, match="cross-class method forwarding is not supported"):

            class MyClass:
                @deprecated(target=OtherClass.other_method, deprecated_in="1.0", remove_in="2.0")
                def old_method(self, x: int) -> int:
                    return void(x)

    def test_raises_for_class_target_on_non_init_method(self) -> None:
        """@deprecated(target=SomeClass) on a non-__init__ class method raises TypeError.

        Passing a class directly as target for a bound method would silently forward
        ``self`` of the wrong type.  Only ``__init__`` supports ``target=SomeClass``
        (auto-remapped to ``target=SomeClass.__init__``); for any other class method
        the caller must use a same-class method target or ``target=None``/``True``.

        Defined inline for the same import-time reason as the cross-class test above.
        """

        class Target:
            pass

        with pytest.raises(TypeError, match="only supported on `__init__`"):

            class _Owner:
                @deprecated(target=Target, deprecated_in="1.0", remove_in="2.0")
                def some_method(self) -> None:
                    pass

    def test_does_not_raise_for_same_class_method_target(self) -> None:
        """Forwarding to a method on the same class does not raise."""
        with pytest.warns(FutureWarning):
            assert CrossGuardSameClass().old_method(5) == 10

    def test_does_not_raise_for_module_level_function_target(self) -> None:
        """Forwarding a class method to a module-level function is allowed (no self passed)."""
        assert callable(CrossGuardModuleLevel.old_method)

    def test_does_not_raise_for_class_target(self) -> None:
        """Forwarding __init__ to a full class (constructor forwarding) is allowed."""
        with pytest.warns(FutureWarning):
            old = CrossGuardOldClass(3)
        assert isinstance(old, CrossGuardOldClass)
        assert old.x == 3

    def test_does_not_raise_for_cross_class_staticmethod_target(self) -> None:
        """A deprecated staticmethod forwarding to a staticmethod on another class decorates without raising.

        Regression test: a staticmethod receives no ``self``, so the cross-class guard's rationale ("self
        would carry the wrong type") does not apply.  ``StaticGuardOldClass.compute`` forwards to
        ``StaticGuardNewClass.compute`` — a *different* class — and must decorate at import time rather than
        raising the cross-class ``TypeError``.  The class object being importable already proves decoration
        succeeded.
        """
        assert callable(StaticGuardOldClass.compute)
        assert StaticGuardNewClass.compute(4) == 12

    def test_cross_class_staticmethod_forwards_and_warns(self) -> None:
        """The deprecated cross-class staticmethod forwards to the target and emits a FutureWarning.

        Beyond decorating cleanly, calling the deprecated staticmethod must actually run the replacement:
        ``StaticGuardOldClass.compute(4)`` returns ``StaticGuardNewClass.compute(4)`` (``4 * 3 == 12``) and
        raises the deprecation ``FutureWarning`` on the way.
        """
        with pytest.warns(FutureWarning):
            result = StaticGuardOldClass.compute(4)
        assert result == 12

    def test_metaclass_generated_qualname_skips_guard(self) -> None:
        """A target with a metaclass-style rewritten ``__qualname__`` is detected and the guard returns silently.

        The module-globals check verifies that the top-level class name in the target's qualname actually exists in the
        target callable's module.  When it does not (as for a synthetic ``FakeOwner.replacement`` qualname produced by
        ``type(...)`` or manual assignment), the qualname cannot be trusted and the guard short-circuits.

        """

        def replacement(instance: object, x: int) -> int:
            void(instance)
            return x

        # Simulate a metaclass / type(...) assigning a qualname that looks like a method on FakeOwner,
        # even though `replacement` is unrelated to any such class.  FakeOwner does not exist in this
        # test module, so the module-globals check in the guard detects the unreliable qualname.
        replacement.__qualname__ = "FakeOwner.replacement"

        class RealOwner:  # decoration must not raise TypeError
            @deprecated(target=replacement, deprecated_in="1.0", remove_in="2.0")
            def old_method(self, x: int) -> int:
                return void(x)

        # Guard returned silently: decoration completed and attached deprecation metadata rather than raising.
        # (The synthetic ``replacement`` fixture rejects the forwarded ``self``, so only decoration is asserted here.)
        assert hasattr(RealOwner.old_method, "__deprecated__")

    def test_decorator_rewriting_source_qualname_same_class_no_warning(self) -> None:
        """Frame inspection resolves the FP when a decorator corrupts source qualname on a same-class forward.

        Python sets ``__qualname__`` in the class body's locals at class-definition time, before any decorator runs.
        Reading it from ``sys._getframe`` therefore recovers the true enclosing class name even when a pre-applied
        decorator has overwritten ``fn.__qualname__`` on the source callable.

        """

        def rewrite_to_alien_class(fn: Callable[..., Any]) -> Callable[..., Any]:
            """Test fixture: outer decorator that retags the wrapped function as living on ``AlienClass``."""
            fn.__qualname__ = "AlienClass.method"
            return fn

        class MyClass:  # decoration must not raise TypeError
            def new_method(self, x: int) -> int:
                return x

            @deprecated(target=new_method, deprecated_in="1.0", remove_in="2.0")
            @rewrite_to_alien_class
            def old_method(self, x: int) -> int:
                return void(x)

        # Decoration succeeded and the wrapper forwards to the same-class target: calling it warns and returns 7.
        with pytest.warns(FutureWarning):
            result = MyClass().old_method(7)
        assert result == 7

    def test_decorator_rewriting_qualname_raises_for_cross_class(self) -> None:
        """A pre-applied decorator rewriting source qualname to a genuinely different class still raises TypeError.

        Fix 1 (frame inspection) overrides the corrupted source qualname with the true enclosing class taken from the
        class body's locals.  When the recovered class differs from the target's class, the guard still fires correctly
        — this guards against an over-eager FP suppression.

        """

        def rewrite_qualname(fn: Callable[..., Any]) -> Callable[..., Any]:
            """Test fixture: an outer decorator that retags the wrapped function as living on ``OtherOwner``."""
            fn.__qualname__ = "OtherOwner.rewritten_method"
            return fn

        class TargetOwner:
            def target_method(self, x: int) -> int:
                return x

        with pytest.raises(TypeError, match="cross-class method forwarding is not supported"):

            class RealOwner:
                @deprecated(target=TargetOwner.target_method, deprecated_in="1.0", remove_in="2.0")
                @rewrite_qualname
                def old_method(self, x: int) -> int:
                    return void(x)


class TestNormalizeTargetInvalidInputs:
    """_normalize_target passes unrecognised non-class values through unchanged.

    The inline ``@deprecated`` decorator in :meth:`test_invalid_target_source_body_runs`
    is parametrize-coupled (``target=bad_target`` resolves from the parametrize fixture),
    so it cannot be moved to :mod:`tests.collection_deprecate` — one wrapper-per-bad-value
    would require either four near-identical fixtures or a factory that obscures the test
    intent.  This is the AGENTS.md three-layer-rule exception for parametrize-coupled
    decorators.
    """

    @pytest.mark.parametrize("bad_target", [42, "not_callable", [], {}])
    def test_invalid_type_returned_unchanged(self, bad_target: object) -> None:
        """Non-callable, non-class, non-sentinel values pass through _normalize_target as-is."""

        def dummy() -> None:
            pass

        result = _normalize_target(source=dummy, target=cast(Any, bad_target))
        assert result is bad_target

    @pytest.mark.parametrize("bad_target", [42, "not_callable", [], {}])
    def test_invalid_target_source_body_runs(self, bad_target: object) -> None:
        """Non-callable target is never invoked; source body executes normally at call time."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            @deprecated(target=cast(Any, bad_target), deprecated_in="1.2", remove_in="2.0")
            def fn(x: int) -> int:
                return x + 1

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = fn(4)

        assert result == 5


class TestStackedCallableTargetGuard:
    """Stacking ``@deprecated(target=fn_a)`` over ``@deprecated(target=fn_b)`` warns at decoration time (B4).

    Callable-over-callable stacking silently raises ``TypeError`` at the first call because the inner wrapper's
    signature does not match the outer target's remapped kwargs. The guard surfaces this misconfiguration at decoration
    time so authors catch it without exercising the call path.

    """

    def test_stacked_callable_targets_warn_at_decoration(self) -> None:
        """Decorating a callable-target wrapper with another callable target emits ``UserWarning``."""
        inner = deprecated(target=stacked_inner_target, deprecated_in="0.8", remove_in="1.0")(stacked_outer_target)
        with pytest.warns(UserWarning, match="callable target stacked"):
            deprecated(target=stacked_outer_target, deprecated_in="0.8", remove_in="1.0")(inner)


class TestStackingGuards:
    """Decoration-time ``UserWarning`` for every unsupported stacking combination.

    Each guard surfaces a misconfiguration before the first call rather than producing
    silently wrong results or raising ``TypeError`` at runtime. The callable+callable
    case is covered by ``TestStackedCallableTargetGuard``; this class covers the other
    unsupported stacking combinations exercised below.
    """

    def _make_source(self, **kwargs: Any) -> Callable[..., int]:  # noqa: ANN401
        """Return a minimal function decorated with @deprecated using the given kwargs."""

        def fn(x: int = 0) -> int:
            return x

        return deprecated(**kwargs)(fn)

    def test_callable_over_args_remap_warns(self) -> None:
        """Callable-target outer stacked over ARGS_REMAP inner emits ``UserWarning``."""
        inner = self._make_source(
            target=TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"x": "y"}
        )
        with pytest.warns(UserWarning, match="callable target stacked over.*ARGS_REMAP") as record:
            deprecated(target=stacked_outer_target, deprecated_in="2.0", remove_in="3.0")(inner)
        assert record[0].filename.endswith("test__dispatch.py")

    def test_args_remap_over_callable_warns(self) -> None:
        """ARGS_REMAP outer stacked over callable-target inner emits ``UserWarning``."""
        inner = self._make_source(target=stacked_outer_target, deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(UserWarning, match="ARGS_REMAP.*stacked over a callable") as record:
            deprecated(target=TargetMode.ARGS_REMAP, deprecated_in="2.0", remove_in="3.0", args_mapping={"x": "y"})(
                inner
            )
        assert record[0].filename.endswith("test__dispatch.py")

    def test_notify_over_notify_warns(self) -> None:
        """Duplicate NOTIFY layers emit ``UserWarning`` at decoration time."""
        inner = self._make_source(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(UserWarning, match="duplicate.*NOTIFY") as record:
            deprecated(target=TargetMode.NOTIFY, deprecated_in="2.0", remove_in="3.0")(inner)
        assert record[0].filename.endswith("test__dispatch.py")

    def test_notify_over_args_remap_warns_with_order_hint(self) -> None:
        """NOTIFY outer + ARGS_REMAP inner (wrong order) emits ``UserWarning`` with order hint."""
        inner = self._make_source(
            target=TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"x": "y"}
        )
        with pytest.warns(UserWarning, match="Reverse the decorator order") as record:
            deprecated(target=TargetMode.NOTIFY, deprecated_in="2.0", remove_in="3.0")(inner)
        assert record[0].filename.endswith("test__dispatch.py")

    def test_callable_over_notify_warns(self) -> None:
        """Callable-target outer stacked over NOTIFY inner emits ``UserWarning``."""
        inner = self._make_source(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(UserWarning, match="callable target stacked over.*NOTIFY") as record:
            deprecated(target=stacked_outer_target, deprecated_in="2.0", remove_in="3.0")(inner)
        assert record[0].filename.endswith("test__dispatch.py")

    def test_args_remap_over_args_remap_does_not_warn(self) -> None:
        """Supported ARGS_REMAP+ARGS_REMAP stacking must not emit any UserWarning."""
        inner = self._make_source(
            target=TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"x": "y"}
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            deprecated(target=TargetMode.ARGS_REMAP, deprecated_in="2.0", remove_in="3.0", args_mapping={"y": "z"})(
                inner
            )
        assert not [w for w in caught if issubclass(w.category, UserWarning)]

    def test_non_stacked_args_remap_new_arg_silent(self) -> None:
        """Regression: non-stacked ARGS_REMAP (_source_is_stacked=False) is silent when new arg used.

        The early-return guard includes ``not _source_is_stacked``.  Verifies this condition still
        allows a non-stacked ARGS_REMAP function to short-circuit with no warning when the caller
        already uses the new argument name.
        """
        fn = self._make_source(
            target=TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"old": "x"}
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fn(x=5)
        assert not [w for w in caught if issubclass(w.category, FutureWarning)]


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
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
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
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
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

    def test_decoration_emits_user_warning(self) -> None:
        """Applying @deprecated to a callable target with POSITIONAL_ONLY params warns at decoration time.

        A developer deprecating ``old_fn`` in favour of ``positional_only_target`` (whose
        first parameter ``x`` is declared positional-only) should receive a ``UserWarning``
        at the ``@deprecated(...)`` line — before any call is made — so the incompatibility
        is surfaced early rather than crashing on first use.
        """
        with pytest.warns(UserWarning, match=r"POSITIONAL_ONLY"):
            deprecated(target=positional_only_target, deprecated_in="1.0", remove_in="2.0")(lambda x, y=0: None)

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
        ``deprecated_positional_only_stream_none`` is defined in ``collection_deprecate.py``
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
        ``deprecated_positional_only_num_warns_one`` is defined in ``collection_deprecate.py``
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
        The guard fires before the warning is emitted, so no FutureWarning is raised.
        """
        with pytest.raises(RuntimeError, match=r"Circular deprecation cycle detected.*dep_cycle_fn_a"):
            dep_cycle_fn_a(1)

    def test_cycle_entry_via_b_raises(self) -> None:
        """Calling dep_cycle_fn_b raises RuntimeError when target chain loops back through dep_cycle_fn_a.

        A maintainer renames a second deprecated symbol to point at the same cycle. When
        the entry point is dep_cycle_fn_b instead of dep_cycle_fn_a, the guard must fire
        at dep_cycle_fn_b's re-entry and name dep_cycle_fn_b in the error message, proving
        the detector is symmetric and reports the correct wrapper regardless of entry order.
        """
        with pytest.raises(RuntimeError, match=r"Circular deprecation cycle detected.*dep_cycle_fn_b"):
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
        with pytest.raises(RuntimeError, match=r"Circular deprecation cycle detected.*dep_async_cycle_fn_a"):
            await dep_async_cycle_fn_a(1)

    @pytest.mark.asyncio
    async def test_async_cycle_entry_via_b_raises(self) -> None:
        """Awaiting dep_async_cycle_fn_b raises RuntimeError when the async target chain cycles.

        Mirror of test_async_cycle_entry_via_a_raises — the async cycle is symmetric, so
        entering from dep_async_cycle_fn_b must also trigger the guard. The error message
        must name dep_async_cycle_fn_b as the re-entered wrapper.
        """
        with pytest.raises(RuntimeError, match=r"Circular deprecation cycle detected.*dep_async_cycle_fn_b"):
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
        results = await asyncio.gather(
            dep_async_non_cycle_old_fn(1), dep_async_non_cycle_old_fn(2), dep_async_non_cycle_old_fn(3)
        )
        assert results == [2, 4, 6]


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
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
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
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
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
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
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
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
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
        cfg = cast(_DeprecatedCallable, fn_shared_default).__deprecated__
        assert cfg.target_all_param_names == frozenset({"x", "level"})
        assert cfg.target_accepts_var_positional is False
        assert cfg.target_accepts_var_keyword is False

    def test_precompute_reports_var_args_and_kwargs(self) -> None:
        """`_precompute_target_facts` flags ``*args`` and ``**kwargs`` and collects every parameter name."""

        def _tgt(a: int, b: int = 2, *rest: int, **extra: int) -> None:
            """Throwaway signature carrying every parameter kind for the precompute helper."""

        names, var_pos, var_kw = _precompute_target_facts(_tgt)
        assert names == frozenset({"a", "b", "rest", "extra"})
        assert var_pos is True
        assert var_kw is True

    def test_precompute_empty_for_non_callable_target(self) -> None:
        """A :class:`TargetMode` (non-callable) target yields empty facts — the dispatcher never forwards to it."""
        names, var_pos, var_kw = _precompute_target_facts(TargetMode.NOTIFY)
        assert names == frozenset()
        assert (var_pos, var_kw) == (False, False)

    def test_repr_excludes_cache_fields(self) -> None:
        """The precomputed cache fields stay out of ``repr`` so audit output and doc examples remain stable."""
        text = repr(cast(_DeprecatedCallable, fn_shared_default).__deprecated__)
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


class TestBareDecoratorGuard:
    """Bare ``@deprecated`` (no parentheses) must fail with a guiding message."""

    def test_call_raises_typeerror_about_parentheses(self) -> None:
        """Calling a bare-decorated function raises a TypeError that names the missing parentheses.

        A user who forgets the parentheses (``@deprecated`` instead of ``@deprecated(...)``) binds the
        decorated function to ``target`` and gets ``packing`` back; the first call then arrives with the call
        argument as ``source``. The guard must name the missing-parentheses mistake rather than leak a cryptic
        ``AttributeError: 'int' object has no attribute '__name__'``.
        """

        @deprecated  # type: ignore[operator,call-overload]
        def old(x: int) -> int:
            return x

        with pytest.raises(TypeError, match="must be called with parentheses"):
            old(5)  # type: ignore[arg-type]  # bare decorator rebinds ``old`` to the packing decorator

    def test_helper_accepts_plain_callable(self) -> None:
        """The guard helper is a no-op for an ordinary callable so correct decoration is never disturbed."""

        def real(x: int) -> int:
            return x

        _reject_bare_decorator(real)  # no exception raised = a plain callable is accepted


class TestClassBodyQualnameWalk:
    """The cross-class guard locates the class body via a bounded frame walk, not a fixed depth."""

    def test_finds_enclosing_class_qualname(self) -> None:
        """Called inside a class body, the walk returns that class's ``__qualname__``.

        Called from within a class body the walk returns that class's ``__qualname__`` regardless of how many
        descriptor/packing frames sit between it and the class body — the fixed ``sys._getframe(2)`` it replaces
        silently missed the class body for descriptor-decorated methods, disabling the guard for them.
        """
        captured: dict[str, str] = {}

        class Sample:
            captured["qualname"] = _find_class_body_qualname()

        _ = Sample  # reference prevents static-analysis "unused class" warnings
        assert captured["qualname"].endswith("Sample")

    def test_cross_class_guard_fires_for_descriptor_decorated_method(self) -> None:
        """Cross-class guard raises TypeError at class-definition time even when the method uses a descriptor.

        With the old fixed ``sys._getframe(2)`` approach the extra stack frames introduced by
        ``@classmethod``/``@staticmethod``/``@property`` wrapping pushed the class body out of range,
        silently disabling the cross-class guard for descriptor-decorated methods.  The bounded frame
        walk locates the class body regardless of intervening descriptor frames.
        """

        class OtherClass:
            def other_method(self, x: int) -> int:
                return x

        with pytest.raises(TypeError, match="cross-class method forwarding is not supported"):

            class _Owner:
                @classmethod
                @deprecated(target=OtherClass.other_method, deprecated_in="1.0", remove_in="2.0")
                def old_classmethod(cls, x: int) -> int:
                    return void(x)


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
        deprecated_in="1.0", remove_in="2.0", name="src", target=TargetMode.ARGS_REMAP, args_mapping={"old_x": "x"}
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
    with warnings.catch_warnings(record=True) as warned:
        warnings.simplefilter("always")
        result = depr_target_mode_args_only_with_args_extra_injects_kwargs(old_x=5)
    assert warned
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
        deprecated_in="1.0", remove_in="2.0", name="src", target=TargetMode.ARGS_REMAP, args_mapping={"factor": "scale"}
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


# ---------------------------------------------------------------------------
# _split_positional_only_kwargs — slot-safe positional extraction
# ---------------------------------------------------------------------------


class TestSplitPositionalOnlyKwargs:
    """Unit contract of :func:`deprecate.deprecation._split_positional_only_kwargs`.

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

    ``_build_call_plan`` reads the warn counter, decides whether to emit, and increments — a check-then-act
    sequence.  Without synchronisation, concurrent first calls all read ``warned_calls == 0``, all pass the
    ``num_warns`` gate, and each emit a warning.  The state lock makes the check-and-increment atomic so exactly
    ``num_warns`` emissions happen regardless of thread interleaving.
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
