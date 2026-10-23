"""Tests for deprecated functions.

Decorator-syntax warning timing (definition-time vs call-time) is by design, not an open question:
decorating a function IS calling the deprecated wrapper, so the warning fires at decoration/import
time. See docs/troubleshooting.md and issue #96 for the resolved design discussion.

Current test coverage:
- TestDeprecatedFunctionWrappers.test_with_decorator_syntax
- TestDeprecatedClassWrappers.test_with_decorator_syntax

"""

import asyncio
import gc
import warnings
import weakref
from typing import Any, Callable, cast

import pytest

import deprecate
from deprecate import DeprecatedError, as_exceptions, assert_no_warnings, deprecated
from deprecate._types import DeprecationConfig, _DeprecatedCallable
from tests.collection_depr_legacy import decorated_pow_self as legacy_pow_self
from tests.collection_depr_legacy import decorated_sum_warn_only as legacy_sum_warn_only
from tests.collection_depr_legacy import depr_pow_self_double as legacy_pow_self_double
from tests.collection_deprecate import (
    DeprecatedTimerDecorator,
    decorated_pow_self,
    decorated_pow_skip_if_func,
    decorated_pow_skip_if_true,
    decorated_sum,
    decorated_sum_calls_2,
    decorated_sum_calls_inf,
    decorated_sum_msg,
    decorated_sum_no_stream,
    decorated_sum_warn_only,
    depr_accuracy_extra,
    depr_accuracy_map,
    depr_accuracy_skip,
    depr_make_new_cls,
    depr_make_new_cls_mapped,
    depr_pow_args,
    depr_pow_mix,
    depr_pow_self_double,
    depr_pow_self_twice,
    depr_pow_skip_if_false_true,
    depr_pow_skip_if_func_int,
    depr_pow_skip_if_true_false,
    depr_pow_wrong,
    depr_timing_wrapper,
    depr_two_renames_warn_once,
    wrapped_pow_self,
    wrapped_pow_skip_if_func,
    wrapped_pow_skip_if_true,
    wrapped_sum,
    wrapped_sum_calls_2,
    wrapped_sum_calls_inf,
    wrapped_sum_msg,
    wrapped_sum_no_stream,
    wrapped_sum_warn_only,
)
from tests.collection_deprecate_forwarding import (
    depr_collision_old_new,
    depr_collision_old_new_extra_wins,
    depr_uncomparable_default,
    deprecated_async_var_positional_forward,
    deprecated_var_positional_forward,
    deprecated_var_positional_overflow,
    deprecated_var_positional_remap,
    deprecated_var_positional_to_fixed,
)
from tests.collection_deprecate_regressions import (
    LegacyPresetCls,
    depr_add_callable_instance,
    depr_builtin_max_target,
    depr_lazy_signature_target_renamed,
    depr_non_string_name_target,
    depr_notify_skip_counted,
    depr_notify_spent_budget,
    depr_notify_spent_budget_async,
    depr_opaque_target_renamed,
    depr_opaque_target_var_positional,
    depr_scale_nested_partial,
    depr_scale_partial,
    depr_scale_partial_every_call,
    depr_scale_partial_no_default,
    depr_scale_partial_renamed,
    depr_sync_over_partial_async,
    make_opaque_source_wrapper,
    make_transient_notify_pair,
    notify_skip_condition,
)
from tests.collection_misconfigured import make_notify_mapping_no_budget
from tests.collection_targets import (
    NewCls,
    SignatureNotBuiltError,
    TimerDecorator,
    base_sum_kwargs,
    sample_function,
    timing_wrapper,
    uncomparable_default,
)


class TestDeprecationWarnings:
    """Tests for basic deprecation warning behavior."""

    def test_forwarded_default_uses_identity(self) -> None:
        """Forward an omitted default without invoking its equality operators.

        A deprecated callable may use an array-like default whose equality and inequality operators raise
        TypeError. Filtering out inspect.Parameter.empty must preserve that default by identity so the
        replacement receives the original object and the caller still gets a FutureWarning.
        """
        with pytest.warns(FutureWarning, match="The `depr_uncomparable_default` was deprecated since v1.0"):
            result = depr_uncomparable_default()
        assert result is uncomparable_default

    @pytest.mark.parametrize(
        ("func", "name"),
        [
            pytest.param(decorated_sum_warn_only, "decorated_sum_warn_only", id="decorated-form"),
            pytest.param(wrapped_sum_warn_only, "original_sum_warn_only", id="wrapped-form"),
            pytest.param(legacy_sum_warn_only, "decorated_sum_warn_only", id="legacy"),
        ],
    )
    def test_warn_only(self, func: Callable, name: str) -> None:
        """Test deprecated function that only warns."""
        with pytest.warns(FutureWarning, match=f"The `{name}` was deprecated since v0.2. It will be removed in v0.3."):
            assert func(2) is None

    @pytest.mark.parametrize(
        ("func", "name"),
        [
            pytest.param(decorated_sum, "decorated_sum", id="decorated-form"),
            pytest.param(wrapped_sum, "original_sum", id="wrapped-form"),
        ],
    )
    def test_default(self, func: Callable, name: str) -> None:
        """Testing some base/default configurations."""
        with pytest.warns(
            FutureWarning,
            match=f"The `{name}` was deprecated since v0.1 in favor of `tests.collection_targets.base_sum_kwargs`."
            " It will be removed in v0.5.",
        ):
            assert func(2) == 7

    def test_function_to_class_forwarding(self) -> None:
        """Deprecated function targeting a class should instantiate and return the class."""
        cast(_DeprecatedCallable, depr_make_new_cls)._state.warned_calls = 0
        with pytest.warns(
            FutureWarning,
            match="The `depr_make_new_cls` was deprecated since v0.2 in favor of `tests.collection_targets.NewCls`."
            " It will be removed in v0.4.",
        ):
            instance = depr_make_new_cls(2, e=0.9)

        assert isinstance(instance, NewCls)
        assert instance.my_c == 2
        assert instance.my_d == "abc"
        assert instance.my_e == 0.9

    def test_function_to_class_forwarding_with_args_mapping(self) -> None:
        """Deprecated function with args_mapping should rename old_c→c before forwarding to NewCls."""
        cast(_DeprecatedCallable, depr_make_new_cls_mapped)._state.warned_calls = 0
        with pytest.warns(
            FutureWarning,
            match="The `depr_make_new_cls_mapped` was deprecated since v0.2 in favor of"
            " `tests.collection_targets.NewCls`. It will be removed in v0.4.",
        ):
            instance = depr_make_new_cls_mapped(old_c=3, e=0.7)

        assert isinstance(instance, NewCls)
        assert instance.my_c == 3
        assert instance.my_d == "abc"
        assert instance.my_e == 0.7

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(decorated_sum, id="decorated-form"),
            pytest.param(wrapped_sum, id="wrapped-form"),
        ],
    )
    def test_default_once(self, func: Callable) -> None:
        """Check that the warning is raised only once per function."""
        cast(_DeprecatedCallable, func)._state.warned_calls = 0
        with pytest.warns(FutureWarning):
            assert func(2) == 7
        with assert_no_warnings(FutureWarning):
            assert func(3) == 8

    def test_default_independent(self) -> None:
        """Check that it does not affect other functions when called with positional args."""
        with pytest.warns(
            FutureWarning,
            match="The `depr_pow_mix` was deprecated since v0.1 in favor of `tests.collection_targets.base_pow_args`."
            " It will be removed in v0.5.",
        ):
            assert depr_pow_mix(2, 1) == 2

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(decorated_sum_no_stream, id="decorated-form"),
            pytest.param(wrapped_sum_no_stream, id="wrapped-form"),
        ],
    )
    def test_stream_calls_no_stream(self, func: Callable) -> None:
        """Check that the warning is NOT raised when stream is None."""
        with assert_no_warnings(FutureWarning):
            assert func(3) == 8

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(decorated_sum_calls_2, id="decorated-form"),
            pytest.param(wrapped_sum_calls_2, id="wrapped-form"),
        ],
    )
    def test_stream_calls_limit(self, func: Callable) -> None:
        """Check that the warning is raised only N times."""

        def _call() -> None:
            for _ in range(5):
                assert func(3) == 8

        with pytest.warns(FutureWarning) as record:
            _call()
        assert len(record) == 2

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(decorated_sum_calls_inf, id="decorated-form"),
            pytest.param(wrapped_sum_calls_inf, id="wrapped-form"),
        ],
    )
    def test_stream_calls_inf(self, func: Callable) -> None:
        """Check that the warning is raised infinitely."""

        def _call() -> None:
            for _ in range(5):
                assert func(3) == 8

        with pytest.warns(FutureWarning) as record:
            _call()
        assert len(record) == 5

    @pytest.mark.parametrize(
        ("func", "name"),
        [
            pytest.param(decorated_sum_msg, "decorated_sum_msg", id="decorated-form"),
            pytest.param(wrapped_sum_msg, "original_sum", id="wrapped-form"),
        ],
    )
    def test_stream_calls_msg(self, func: Callable, name: str) -> None:
        """Test deprecated function with custom message."""
        with pytest.warns(FutureWarning, match=f"v0.1: `{name}` was deprecated, use `base_sum_kwargs`"):
            assert func(3) == 8


class TestArgumentMapping:
    """Tests for deprecated function arguments mapping."""

    @pytest.fixture(autouse=True)
    def _reset_deprecation_state(self) -> None:
        """Reset deprecation state for functions with chained or multiple deprecations."""
        for func in (
            decorated_pow_self,
            depr_collision_old_new,
            depr_pow_self_double,
            depr_pow_self_twice,
            wrapped_pow_self,
            legacy_pow_self,
            legacy_pow_self_double,
        ):
            state = cast(_DeprecatedCallable, func)._state
            state.warned_calls = 0
            state.warned_args.clear()

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(decorated_pow_self, id="decorated-form"),
            pytest.param(wrapped_pow_self, id="wrapped-form"),
            pytest.param(legacy_pow_self, id="legacy"),
        ],
    )
    def test_arguments_new_only(self, func: Callable) -> None:
        """Test calling with new arguments only (no warning)."""
        with assert_no_warnings():
            assert func(2, new_coef=3) == 8

    @pytest.mark.parametrize(
        ("func", "name"),
        [
            pytest.param(decorated_pow_self, "decorated_pow_self", id="decorated-form"),
            pytest.param(wrapped_pow_self, "original_pow_self", id="wrapped-form"),
            pytest.param(legacy_pow_self, "decorated_pow_self", id="legacy"),
        ],
    )
    def test_arguments_deprecated(self, func: Callable, name: str) -> None:
        """Test calling with deprecated argument (should warn)."""
        with pytest.warns(
            FutureWarning,
            match=f"The `{name}` uses deprecated arguments: `coef` -> `new_coef`."
            " They were deprecated since v0.1 and will be removed in v0.5.",
        ):
            assert func(2, 3) == 8

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(depr_pow_self_double, id="modern"),
            pytest.param(legacy_pow_self_double, id="legacy"),
        ],
    )
    @pytest.mark.parametrize(
        ("kwargs", "match_fragment", "expected"),
        [
            pytest.param({"c1": 3}, r"`c1` -> `nc1`", 32, id="c1-deprecated"),
            pytest.param({"c2": 2}, r"`c2` -> `nc2`", 8, id="c2-deprecated"),
        ],
    )
    def test_arguments_double_deprecated(
        self, func: Callable, kwargs: dict, match_fragment: str, expected: int
    ) -> None:
        """Test double mapping, calling with each deprecated argument individually."""
        with pytest.warns(FutureWarning, match=f"The `depr_pow_self_double` uses depr. args: {match_fragment}."):
            assert func(2, **kwargs) == expected

    def test_arguments_double_mixed(self) -> None:
        """Testing that preferable use the new arguments when both are provided."""
        # Reset warning state to ensure test independence
        cast(_DeprecatedCallable, depr_pow_self_double)._state.warned_args.clear()

        with assert_no_warnings():
            assert depr_pow_self_double(2, nc1=1, nc2=2) == 8

        # When both c1 and c2 are provided, warns about both together
        with pytest.warns(FutureWarning, match="The `depr_pow_self_double` uses depr. args:"):
            assert depr_pow_self_double(2, c1=3, c2=4, nc1=1) == 32

        # Need to reset after first warning because both counters were incremented
        cast(_DeprecatedCallable, depr_pow_self_double)._state.warned_args.clear()

        # When both c1 and c2 are provided, warns about both together
        # Result is 32 because: c1->nc1=3, c2->nc2=4, but nc2=2 (user wins), so 2**(0+0+3+2)=32
        with pytest.warns(FutureWarning, match="The `depr_pow_self_double` uses depr. args:"):
            assert depr_pow_self_double(2, c1=3, c2=4, nc2=2) == 32

    def test_spent_argument_not_listed_again(self) -> None:
        """A later rename warning names only the arguments whose own budget is not spent yet.

        With ``num_warns=1`` a caller first passes ``old_x`` and receives its one notice. A later call passes
        ``old_x`` and ``old_y`` together: ``old_y`` still owes its notice, but the budget of ``old_x`` is spent, so
        naming it again would break the one-warning promise and repeat a migration hint the caller already has.
        """
        with pytest.warns(FutureWarning, match="`old_x` -> `x`"):
            depr_two_renames_warn_once(old_x=1)
        with pytest.warns(FutureWarning) as record:
            assert depr_two_renames_warn_once(old_x=1, old_y=2) == 3
        assert [str(w.message) for w in record] == [
            "The `depr_two_renames_warn_once` uses deprecated arguments: `old_y` -> `y`."
            " They were deprecated since v1.0 and will be removed in v2.0."
        ]

    def test_chain_deprecated(self) -> None:
        """Test chaining deprecation wrappers, calling with deprecated argument."""
        with pytest.warns(FutureWarning) as warns:
            assert depr_pow_self_twice(2, 3) == 8
        assert len(warns) == 2
        # Pin the warning category explicitly on each recorded entry.  ``pytest.warns(FutureWarning)``
        # only asserts that *at least one* matching warning fires — a regression that smuggled in a
        # different category alongside the expected one would still pass.  Asserting on every entry closes that hole.
        assert warns[0].category is FutureWarning
        assert warns[1].category is FutureWarning

    def test_chain_new(self) -> None:
        """Test chaining deprecation wrappers, calling with new argument."""
        # First, trigger the deprecation warning so that per-argument counters are incremented.
        with pytest.warns(FutureWarning):
            assert depr_pow_self_twice(2, 3) == 8
        # After the initial warning, calling with the new argument should be quiet.
        with assert_no_warnings():
            assert depr_pow_self_twice(2, c1=3) == 8

    def test_args_mapping_with_source_having_both_old_and_new_params(self) -> None:
        """When source has both old and new param names, caller's old=X reaches target as new=X."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = depr_collision_old_new(old=42)
        assert result == 42

    def test_new_kwarg_wins_when_both_old_and_new_provided_old_first(self) -> None:
        """When old and new kwarg both passed (old first), the explicit new-name value wins.

        ``depr_collision_old_new(old=5, new=6)`` must forward ``new=6`` to the callable target;
        the remapped ``old→new=5`` must not overwrite the explicitly passed ``new=6``.
        """
        with pytest.warns(FutureWarning):
            result = depr_collision_old_new(old=5, new=6)
        assert result == 6

    def test_new_kwarg_wins_when_both_old_and_new_provided_new_first(self) -> None:
        """New-name value wins regardless of whether old or new kwarg is listed first in the call.

        Before the precedence fix, ``depr_collision_old_new(new=6, old=5)`` returned ``5``
        because the dict-comprehension last-write-wins caused the ``old→new`` rename to
        overwrite the explicitly passed ``new=6``.
        """
        with pytest.warns(FutureWarning):
            result = depr_collision_old_new(new=6, old=5)
        assert result == 6

    def test_new_kwarg_wins_when_old_passed_positionally(self) -> None:
        """New-name value wins when old name passed positionally and new name passed as kwarg.

        ``depr_collision_old_new(5, new=6)`` resolves positional ``5`` to ``old=5`` via
        ``_update_kwargs_with_args`` before ``_resolve_forward_kwargs`` computes ``_explicit_new``;
        the fix then fires identically to the kwarg-only collision case.
        """
        with pytest.warns(FutureWarning):
            result = depr_collision_old_new(5, new=6)
        assert result == 6

    def test_args_extra_wins_over_explicit_new_kwarg(self) -> None:
        """args_extra overwrites explicit new-name kwarg — args_extra merge runs last and always wins.

        ``depr_collision_old_new_extra_wins(old=5, new=6)`` has ``args_extra={"new": 99}``;
        the ``args_extra`` merge runs after the ``explicit_new`` restore, so the target always
        receives ``new=99`` regardless of what the caller passed, pinning the priority contract.
        """
        with pytest.warns(FutureWarning):
            result = depr_collision_old_new_extra_wins(old=5, new=6)
        assert result == 99

    def test_user_warning_emitted_when_both_old_and_new_supplied(self) -> None:
        """Supplying both old and new names emits a UserWarning naming the ignored old arg.

        When a caller passes both the deprecated ``old`` kwarg and the replacement ``new``
        kwarg, the deprecation machinery silently drops ``old`` in favour of ``new``.
        Without an explicit signal the caller has no way to know their ``old`` value was
        discarded.  This test pins that a ``UserWarning`` identifying the ignored argument
        is always emitted alongside the normal ``FutureWarning``.
        """
        with pytest.warns(UserWarning, match=r"`old` \(deprecated\) and `new`.*`old` is ignored"):
            depr_collision_old_new(old=5, new=6)


@pytest.mark.parametrize(
    "func",
    [
        pytest.param(decorated_pow_skip_if_true, id="decorated-form"),
        pytest.param(wrapped_pow_skip_if_true, id="wrapped-form"),
    ],
)
def test_skip_if_true(func: Callable) -> None:
    """Test conditional wrapper skip when skip_if=True."""
    with assert_no_warnings():
        assert func(2, c1=2) == 2


@pytest.mark.parametrize(
    "func",
    [
        pytest.param(decorated_pow_skip_if_func, id="decorated-form"),
        pytest.param(wrapped_pow_skip_if_func, id="wrapped-form"),
    ],
)
def test_skip_if_func(func: Callable) -> None:
    """Test conditional wrapper skip when skip_if is a function returning True."""
    with assert_no_warnings():
        assert func(2, c1=2) == 2


@pytest.mark.parametrize(
    "func",
    [
        pytest.param(depr_pow_skip_if_true_false, id="outer-skips"),
        pytest.param(depr_pow_skip_if_false_true, id="inner-skips"),
    ],
)
def test_skip_if_mixed(func: Callable) -> None:
    """Test conditional wrapper skip with mixed skip_if=True/False decorator stacking."""
    with pytest.warns(FutureWarning, match="Depr: v0.1 rm v0.2 for args: `c1` -> `nc1`."):
        assert func(2, c1=2) == 0.5


class TestErrorHandling:
    """Test error handling in deprecated functions."""

    def test_incomplete_missing_arg(self) -> None:
        """Test missing required argument."""
        with pytest.raises(TypeError, match="missing 1 required positional argument: 'b'"):
            depr_pow_args(2)  # type: ignore[call-arg]

    def test_incomplete_missing_target(self) -> None:
        """Test missing argument in target."""
        with pytest.raises(
            TypeError, match=r"Failed mapping of `depr_pow_wrong`, arguments not accepted by target: \['c'\]"
        ):
            depr_pow_wrong(2)

    def test_incomplete_once(self) -> None:
        """Check that the warning is raised only once per function for incomplete mappings."""
        cast(_DeprecatedCallable, depr_pow_args)._state.warned_calls = 0
        with pytest.warns(FutureWarning):
            depr_pow_args(2, 1)
        with assert_no_warnings(FutureWarning):
            assert depr_pow_args(2, 1) == 2

    def test_incomplete_independent(self) -> None:
        """Check that it does not affect other functions for incomplete mappings when called with kwargs."""
        # reset the warning
        cast(_DeprecatedCallable, depr_pow_args)._state.warned_calls = 0
        with pytest.warns(FutureWarning, match="`depr_pow_args` >> `base_pow_args` in v1.0 rm v1.3."):
            assert depr_pow_args(b=2, a=1) == 1

    def test_invalid_skip_if(self) -> None:
        """Test invalid skip_if return value."""
        with pytest.raises(TypeError, match="User function 'skip_if' shall return bool, but got: <class 'int'>"):
            depr_pow_skip_if_func_int(2, c1=2)


@pytest.mark.parametrize(
    ("func", "expected"),
    [
        pytest.param(depr_accuracy_map, 0.5, id="accuracy-map"),
        pytest.param(depr_accuracy_skip, 0.5, id="accuracy-skip"),
        pytest.param(depr_accuracy_extra, 0.75, id="accuracy-extra"),
    ],
)
def test_deprecated_func_accuracy(func: Callable, expected: float) -> None:
    """Test deprecated accuracy wrappers with various arg mapping strategies."""
    with pytest.warns(FutureWarning):
        assert func([1, 0, 1, 2]) == expected


def test_deprecated_func_attribute_set_at_decoration_time() -> None:
    """Test that __deprecation_config__ attribute is set at decoration time, not call time.

    This verifies that the __deprecation_config__ attribute is available immediately after the decorator is
    applied, without needing to call the function first; and that __deprecated__ is already a rendered
    message string at the same point.

    """
    # Verify both attrs are set WITHOUT calling the function (using decorated_sum from collection_deprecate)
    assert hasattr(decorated_sum, "__deprecation_config__")
    assert hasattr(decorated_sum, "__deprecated__")
    assert decorated_sum.__deprecation_config__ == DeprecationConfig(
        deprecated_in="0.1", remove_in="0.5", name="decorated_sum", target=base_sum_kwargs, args_mapping=None
    )
    assert isinstance(decorated_sum.__deprecated__, str)
    assert "decorated_sum" in decorated_sum.__deprecated__
    assert "0.1" in decorated_sum.__deprecated__


class TestDeprecatedFunctionWrappers:
    """Test suite for deprecating function-based wrapper/decorators."""

    @pytest.fixture(autouse=True)
    def reset_warnings(self) -> None:
        """Reset warning counters before each test for independence."""
        cast(_DeprecatedCallable, depr_timing_wrapper)._state.warned_calls = 0

    def test_shows_warning(self) -> None:
        """Test that deprecated wrapper shows deprecation warning."""
        with pytest.warns(FutureWarning, match="`depr_timing_wrapper` was deprecated"):
            wrapped_func = depr_timing_wrapper(sample_function)

        # Verify the wrapper was applied correctly
        assert callable(wrapped_func)

    def test_forwards_correctly(self) -> None:
        """Test that wrapper forwards to new implementation."""
        with pytest.warns(FutureWarning, match="`depr_timing_wrapper` was deprecated"):
            wrapped_func = depr_timing_wrapper(sample_function)

        # Verify the wrapped function executes correctly
        result = wrapped_func(5)
        assert result == 10

    def test_new_implementation_no_warning(self) -> None:
        """Test that new implementation works without warnings."""
        with assert_no_warnings(FutureWarning):
            new_wrapped = timing_wrapper(sample_function)
            result = new_wrapped(7)
            assert result == 14

    def test_with_decorator_syntax(self) -> None:
        """Test warning when applied using @ decorator syntax."""
        with pytest.warns(FutureWarning, match="`depr_timing_wrapper` was deprecated"):

            @depr_timing_wrapper
            def sample_function(x: int) -> int:
                """A simple function for testing wrappers with @ syntax."""
                return x * 3

        # Verify the wrapped function works correctly
        result = sample_function(4)
        assert result == 12


class TestDeprecatedClassWrappers:
    """Test suite for deprecating class-based wrapper/decorators."""

    @pytest.fixture(autouse=True)
    def reset_warnings(self) -> None:
        """Reset warning counters before each test for independence."""
        cast(_DeprecatedCallable, DeprecatedTimerDecorator.__init__)._state.warned_calls = 0

    def test_shows_warning(self) -> None:
        """Test that deprecated wrapper shows deprecation warning."""

        def sample_function(x: int) -> int:
            """A simple function for testing class-based wrappers."""
            return x + 5

        with pytest.warns(FutureWarning, match="`DeprecatedTimerDecorator` was deprecated"):
            wrapped_func = DeprecatedTimerDecorator(sample_function)

        # Verify the wrapper was applied correctly
        assert callable(wrapped_func)

    def test_forwards_correctly(self) -> None:
        """Test that wrapper forwards to new implementation."""

        def sample_function(x: int) -> int:
            """A simple function for testing class-based wrappers."""
            return x + 5

        # Expect warning on first use
        with pytest.warns(FutureWarning, match="`DeprecatedTimerDecorator` was deprecated"):
            wrapped_func = DeprecatedTimerDecorator(sample_function)

        # Verify the wrapped function executes correctly
        result = wrapped_func(3)
        assert result == 8

    def test_preserves_attributes(self) -> None:
        """Test that wrapper preserves tracking attributes."""

        def sample_function(x: int) -> int:
            """A simple function for testing class-based wrappers."""
            return x + 5

        # Expect warning on first use, then test attributes
        with pytest.warns(FutureWarning, match="`DeprecatedTimerDecorator` was deprecated"):
            wrapped_func = DeprecatedTimerDecorator(sample_function)

        # Call the function once
        wrapped_func(3)

        # Verify tracking attributes exist and work (from TimerDecorator)
        assert hasattr(wrapped_func, "total_time")
        assert hasattr(wrapped_func, "calls")
        assert wrapped_func.calls == 1

    def test_new_implementation_no_warning(self) -> None:
        """Test that new implementation works without warnings."""

        def sample_function(x: int) -> int:
            """A simple function for testing class-based wrappers."""
            return x + 5

        with assert_no_warnings(FutureWarning):
            new_wrapped = TimerDecorator(sample_function)
            result = new_wrapped(4)
            assert result == 9
            assert new_wrapped.calls == 1

    def test_with_decorator_syntax(self) -> None:
        """Test warning when applied using @ decorator syntax."""
        with pytest.warns(FutureWarning, match="`DeprecatedTimerDecorator` was deprecated"):

            @DeprecatedTimerDecorator
            def sample_function(x: int) -> int:
                """A simple function for testing class-based wrappers with @ syntax."""
                return x + 5

        # Verify the wrapped function works correctly
        result = sample_function(3)
        assert result == 8

        # Verify tracking attributes exist
        assert hasattr(sample_function, "total_time")
        assert hasattr(sample_function, "calls")
        assert sample_function.calls == 1


class TestVarPositionalForwarding:
    """Forwarding a deprecated ``*args`` source to a callable target.

    Positional-to-keyword conversion stops at the source's ``*args``, so everything past
    the named positionals used to be silently dropped when forwarding — ``old_sum(1, 2, 3)``
    returned ``1``.  The surplus tail must now reach the target positionally, and a target
    that cannot accept it must raise a curated ``TypeError`` instead of losing data.
    """

    def test_surplus_tail_forwarded_to_var_positional_target(self) -> None:
        """The surplus positional tail reaches the target's ``*args``.

        A user migrates ``old_sum(1, 2, 3)`` behaviour to ``var_positional_target``:
        the wrapper must produce ``1 + 2 + 3 == 6``, not silently drop ``(2, 3)``.
        """
        with pytest.warns(FutureWarning):
            result = deprecated_var_positional_forward(1, 2, 3)
        assert result == 6

    def test_no_surplus_forwards_named_positionals_only(self) -> None:
        """Calls without a surplus tail keep the plain keyword-forwarding path."""
        with pytest.warns(FutureWarning):
            result = deprecated_var_positional_forward(4)
        assert result == 4

    def test_surplus_fills_target_fixed_positional_slots(self) -> None:
        """A target without ``*args`` absorbs the surplus into its unfilled positional slots.

        ``trio_positional_target(a, b=0, c=0)`` has two free slots after ``a``; forwarding
        ``(1, 2, 3)`` must bind ``b=2`` and ``c=3`` — the natural positional call shape.
        """
        with pytest.warns(FutureWarning):
            result = deprecated_var_positional_to_fixed(1, 2, 3)
        assert result == 123

    def test_surplus_overflow_raises_curated_type_error(self) -> None:
        """A surplus tail the target cannot accept raises loudly instead of being dropped.

        ``single_positional_target(a)`` has no free slots and no ``*args``; forwarding
        ``(1, 2, 3)`` must raise the curated ``TypeError`` naming the failed mapping.
        """
        with pytest.warns(FutureWarning), pytest.raises(TypeError, match=r"cannot be forwarded"):
            deprecated_var_positional_overflow(1, 2, 3)

    @pytest.mark.asyncio
    async def test_async_surplus_tail_forwarded(self) -> None:
        """The async dispatch twin forwards the surplus tail to an async target."""
        with pytest.warns(FutureWarning):
            result = await deprecated_async_var_positional_forward(1, 2, 3)
        assert result == 6

    def test_args_remap_with_var_positional_no_double_pass(self) -> None:
        """ARGS_REMAP on a ``*args`` source must not double-pass named positionals.

        When a source declares ``*args`` and ``args_mapping`` fires (caller used the old kwarg
        name), the wrapper used to build ``kw_args`` from ``resolved_kwargs``, which contains
        positional-to-keyword conversions for every named positional (e.g. ``a``).  Calling
        ``source(*args, **kw_args)`` then raised ``TypeError: got multiple values for argument 'a'``
        even though the call was perfectly valid.  The fix builds ``kw_args`` from
        ``original_kwargs`` (caller-supplied keywords only) and applies the mapping there, so
        the positional slot ``a`` is carried by ``*args`` and never appears in ``kw_args``.
        """
        with pytest.warns(FutureWarning):
            result = deprecated_var_positional_remap(1, 2, 3, old_kwarg="hello")
        assert result == (1, 2, 3, "hello")

    def test_args_remap_with_var_positional_migrated_caller(self) -> None:
        """Migrated caller (using new kwarg name) must not warn or raise.

        A caller already using ``new_kwarg`` produces an empty ``reason_argument`` — the
        short-circuit path should pass through without any warning.
        """
        result = deprecated_var_positional_remap(1, 2, new_kwarg="world")
        assert result == (1, 2, "world")

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({"new_kwarg": "world", "old_kwarg": "hello"}, id="new-first"),
            pytest.param({"old_kwarg": "hello", "new_kwarg": "world"}, id="old-first"),
        ],
    )
    def test_args_remap_with_var_positional_new_kwarg_wins(self, kwargs: dict[str, str]) -> None:
        """The new-name value wins on a ``*args`` source when both names are passed, whatever their order.

        A half-migrated caller passes both ``old_kwarg`` and ``new_kwarg`` to an ARGS_REMAP source that declares
        ``*args``.  The collision ``UserWarning`` says the old name is ignored, so the source must receive the new
        value; before the fix the ``*args`` branch renamed the caller's keywords with a last-write-wins comprehension
        and the old value won whenever it was passed after the new one.  The warning fires once, from the caller's
        line.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = deprecated_var_positional_remap(1, 2, **kwargs)
        collisions = [w for w in caught if issubclass(w.category, UserWarning)]
        assert result == (1, 2, "world")
        assert [str(w.message) for w in collisions] == [
            "Both `old_kwarg` (deprecated) and `new_kwarg` were supplied to `deprecated_var_positional_remap()`;"
            " `old_kwarg` is ignored."
        ]
        assert collisions[0].filename == __file__


class TestTemplateMgsAliasOnFunctionEntryPoint:
    """The deprecated ``template_mgs`` alias resolves through the ``@deprecated`` function entry point.

    A maintainer who never migrated off the old, typo'd ``template_mgs`` spelling decorates a plain
    function (not a class) with it directly — this must keep working end-to-end through the full
    decoration path, not just the isolated ``_resolve_message_template_alias`` unit test.
    """

    def test_alias_alone_warns_and_resolves(self) -> None:
        """Supplying only ``template_mgs`` warns ``FutureWarning`` and is adopted as ``message_template``."""
        with pytest.warns(FutureWarning, match="`template_mgs` is deprecated"):
            wrapped = deprecated(
                target=base_sum_kwargs,
                deprecated_in="1.0",
                remove_in="2.0",
                template_mgs="Alias notice for `%(source_name)s`.",
            )(base_sum_kwargs)
        assert (
            cast(_DeprecatedCallable, wrapped).__deprecation_config__.message_template
            == "Alias notice for `%(source_name)s`."
        )

    def test_alias_and_message_template_together_raises(self) -> None:
        """Supplying both ``template_mgs`` and ``message_template`` raises ``TypeError`` — no silent merge."""
        with pytest.raises(TypeError, match="pass only one"):
            deprecated(
                target=base_sum_kwargs,
                deprecated_in="1.0",
                remove_in="2.0",
                message_template="Canonical notice.",
                template_mgs="Legacy notice.",
            )(base_sum_kwargs)


class TestCallableKindTargets:
    """Targets that are callable but carry no ``__name__`` of their own: ``functools.partial`` and instances."""

    def test_partial_target_warns_and_forwards(self) -> None:
        """A ``functools.partial`` target warns once, names the wrapped function and forwards the call.

        A maintainer redirects ``depr_scale_partial`` to ``partial(new_scale, y=5)`` to preset an argument. The
        partial object has no ``__name__``, so the warning must name the function it wraps instead of crashing
        with ``AttributeError`` on the first call (which also used to burn the one-warning budget silently).

        """
        with pytest.warns(FutureWarning, match=r"in favor of `tests\.collection_targets\.new_scale`"):
            assert depr_scale_partial(2) == 10

    def test_partial_target_warns_only_once(self) -> None:
        """After the first warning, the default ``num_warns=1`` budget keeps later calls silent.

        The budget must count a real emission: a crash inside the warning path used to consume it without any
        warning reaching the caller, so the second call is the one that proves the warning was actually issued.

        """
        with pytest.warns(FutureWarning):
            depr_scale_partial(1)
        with assert_no_warnings(FutureWarning):
            assert depr_scale_partial(1) == 5

    def test_partial_target_unlimited_budget_warns_every_call(self) -> None:
        """With ``num_warns=-1`` a partial target warns on each call instead of raising on each call.

        Unlimited budgets re-enter the warning path every time, so a broken target-name lookup used to turn the
        deprecated function into one that always raises; both calls here must warn and return the target result.

        """
        with pytest.warns(FutureWarning) as record:
            first, second = depr_scale_partial_every_call(1), depr_scale_partial_every_call(2)
        assert (first, second, len(record)) == (5, 10, 2)

    def test_callable_instance_target_names_its_class(self) -> None:
        """A callable-instance target is named by its class and receives the forwarded call.

        Replacing a function with a configured callable object (``CallableAdder(offset=10)``) is a common
        pattern; the instance has no ``__name__``, so the warning names ``CallableAdder`` by its module path.

        """
        with pytest.warns(FutureWarning, match=r"in favor of `tests\.collection_targets\.CallableAdder`"):
            assert depr_add_callable_instance(1) == 11

    def test_non_string_name_falls_back_to_type_name(self) -> None:
        """A target whose ``__name__`` is a non-string property is named by its type, not by that value.

        Objects can expose ``__name__`` as an arbitrary attribute; trusting it would render ``42`` (or crash on
        concatenation), so only a string ``__name__`` counts as a name.

        """
        with pytest.warns(FutureWarning, match=r"in favor of `tests\.collection_targets\.NonStringNameCallable`"):
            assert depr_non_string_name_target(3) == 3

    def test_nested_partial_names_innermost_function(self) -> None:
        """A two-level partial chain is unwrapped to the innermost function for the warning text.

        ``partial(partial(f, ...))`` stays nested when the inner layer carries attributes, so a single ``.func``
        step would still land on a partial; the display walks the whole chain.

        """
        with pytest.warns(FutureWarning, match=r"in favor of `tests\.collection_targets\.new_scale`"):
            assert depr_scale_nested_partial(1) == 5

    def test_partial_target_static_message(self) -> None:
        """The PEP 702 ``__deprecated__`` string names the wrapped function, not the partial's ``repr``.

        Static checkers and IDEs show ``__deprecated__`` verbatim; ``functools.partial(<function ...>)`` with a
        memory address is unreadable there and differs between runs.

        """
        message = cast(Any, depr_scale_partial).__deprecated__
        assert "in favor of `tests.collection_targets.new_scale`" in message

    def test_builtin_target_keeps_builtins_path(self) -> None:
        """A C builtin target (``max``) keeps its ``builtins.max`` path in the static message.

        Builtins carry both ``__name__`` and ``__module__``, so the shared display helper must reproduce the
        message every release before this one rendered for them, byte for byte.

        """
        message = cast(Any, depr_builtin_max_target).__deprecated__
        assert "in favor of `builtins.max`" in message

    def test_async_partial_target_error_names_wrapped_function(self) -> None:
        """A sync source over ``partial(async_fn)`` raises the curated ``TypeError`` naming ``async_target``.

        ``inspect.iscoroutinefunction`` sees through ``functools.partial``, so the dispatcher refuses the call;
        the refusal message itself must not crash on the partial's missing ``__name__``.

        """
        with pytest.warns(FutureWarning), pytest.raises(TypeError, match=r"Async target `async_target` cannot"):
            depr_sync_over_partial_async(1)

    def test_class_partial_target_warns_and_constructs(self) -> None:
        """``deprecated_class`` with ``target=partial(NewCls, d=...)`` warns once and builds a ``NewCls``.

        A legacy class is retired in favour of the new class with one constructor argument preset. The proxy's
        warning path read ``target.__name__`` directly and crashed; it must name ``NewCls`` and forward.

        """
        with pytest.warns(FutureWarning, match=r"in favor of `tests\.collection_targets\.NewCls`"):
            built = cast(NewCls, LegacyPresetCls(1.5))
        assert (type(built), built.my_c, built.my_d) == (NewCls, 1.5, "preset")


class TestPartialTargetPrecedence:
    """Which value reaches a ``functools.partial`` target: caller > partial-bound keyword > source default."""

    def test_partial_keyword_beats_source_default(self) -> None:
        """When the caller omits ``y``, the partial's bound ``y=5`` wins over the source default ``y=0``.

        The maintainer chose ``partial(new_scale, y=5)`` precisely to preset ``y``; forwarding the legacy
        signature's default would silently override that choice and return ``0`` instead of ``5``.

        """
        with pytest.warns(FutureWarning):
            assert depr_scale_partial(1) == 5

    @pytest.mark.parametrize(
        ("args", "kwargs"),
        [pytest.param((1,), {"y": 7}, id="keyword"), pytest.param((1, 7), {}, id="positional")],
    )
    def test_caller_value_beats_partial_keyword(self, args: tuple[int, ...], kwargs: dict[str, int]) -> None:
        """A ``y`` supplied by the caller, by keyword or by position, still overrides the partial's ``y=5``.

        Dropping the source default must not drop the caller's own value: an explicit argument is the strongest
        signal of intent, so it keeps winning exactly as it would when calling the partial directly.

        """
        with pytest.warns(FutureWarning):
            assert depr_scale_partial(*args, **kwargs) == 7

    def test_source_without_default_uses_partial_keyword(self) -> None:
        """A source with no ``y`` parameter forwards nothing for ``y``, so the partial's ``y=5`` applies.

        Characterises the path that already worked: nothing to drop, the partial keyword is the only value.

        """
        with pytest.warns(FutureWarning):
            assert depr_scale_partial_no_default(1) == 5

    def test_renamed_default_does_not_override_partial_keyword(self) -> None:
        """A renamed argument (``factor`` -> ``y``) whose new name the partial binds keeps the partial's value.

        The source default ``factor=0`` would be forwarded as ``y=0``; like any other source default, it yields to
        the keyword the partial binds when the caller did not pass ``factor``.

        """
        with pytest.warns(FutureWarning):
            assert depr_scale_partial_renamed(1) == 5

    def test_renamed_caller_value_beats_partial_keyword(self) -> None:
        """A caller passing the old name ``factor=3`` reaches the target as ``y=3``, overriding the partial.

        The caller's value travels through the rename unchanged; only defaults the caller never supplied yield.

        """
        with pytest.warns(FutureWarning):
            assert depr_scale_partial_renamed(1, factor=3) == 3

    def test_nested_partial_keyword_beats_source_default(self) -> None:
        """A keyword bound on the inner layer of a nested partial chain also beats the source default.

        The bound-keyword set is collected over every layer of the chain, not only the outermost partial.

        """
        with pytest.warns(FutureWarning):
            assert depr_scale_nested_partial(1) == 5


class TestSpentBudgetFastPath:
    """Calls after the warn budget is spent skip the call plan but keep every behaviour of the full path."""

    def test_spent_budget_is_silent(self) -> None:
        """A call after the one warning was delivered emits nothing and still returns the source's result.

        A training loop calls a warn-only deprecated helper on every batch. The first call delivers the single
        ``FutureWarning``; every later call must run the source body with the caller's own arguments, silently.
        """
        with pytest.warns(FutureWarning):
            depr_notify_spent_budget(1)
        with assert_no_warnings():
            result = depr_notify_spent_budget(2, y=3)
        assert result == 5

    def test_async_spent_budget_is_silent(self) -> None:
        """An awaited call after the one warning was delivered emits nothing and still returns the coroutine's result.

        The async wrapper takes the same shortcut as the sync one once the budget is spent: an event-loop worker that
        awaits a deprecated coroutine on every request warns once, then only pays for the awaited body.
        """
        with pytest.warns(FutureWarning):
            asyncio.run(depr_notify_spent_budget_async(1))
        with assert_no_warnings():
            result = asyncio.run(depr_notify_spent_budget_async(2, y=3))
        assert result == 5

    def test_global_switch_after_spent_budget_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Turning ``deprecate.AS_EXCEPTIONS`` on after the budget is spent makes the next call raise.

        A test suite switches to strict mode part-way through a session, after a deprecated helper already warned
        once. The fatal switch is read on every call, so the spent budget must not let the call through, and the
        raise must leave the budget counter untouched.
        """
        with pytest.warns(FutureWarning):
            depr_notify_spent_budget(1)
        monkeypatch.setattr(deprecate, "AS_EXCEPTIONS", True)
        with pytest.raises(DeprecatedError):
            depr_notify_spent_budget(1)
        assert cast(_DeprecatedCallable, depr_notify_spent_budget)._state.warned_calls == 1

    def test_scoped_switch_after_spent_budget_raises(self) -> None:
        """An ``as_exceptions()`` block entered after the budget is spent makes the call inside it raise.

        A consumer guards one critical section with ``with deprecate.as_exceptions():`` after the deprecated helper
        already warned elsewhere. The scope is read on every call, so the call inside it raises and the budget counter
        stays where the earlier warning left it.
        """
        with pytest.warns(FutureWarning):
            depr_notify_spent_budget(1)
        with as_exceptions(), pytest.raises(DeprecatedError):
            depr_notify_spent_budget(1)
        assert cast(_DeprecatedCallable, depr_notify_spent_budget)._state.warned_calls == 1

    def test_surplus_positional_raises_curated_error(self) -> None:
        """A call with one positional argument too many raises the curated ``TypeError`` after the budget is spent.

        The full path rejects surplus positionals with pyDeprecate's own message before anything else; the shortcut
        taken once the budget is spent must not hand the call to the source and surface Python's native message
        instead.
        """
        with pytest.warns(FutureWarning):
            depr_notify_spent_budget(1)
        with pytest.raises(TypeError, match=r"takes 1 to 2 positional argument\(s\) but got 3"):
            cast(Callable[..., int], depr_notify_spent_budget)(1, 2, 3)

    def test_misconfigured_notice_fires_once(self) -> None:
        """A misconfigured warn-only deprecation with ``num_warns=0`` emits its one-time ``UserWarning`` exactly once.

        With a zero budget no ``FutureWarning`` is ever due, so every call qualifies for the shortcut. The one-time
        misconfiguration notice must still reach the first caller, and only the first.
        """
        wrapper = make_notify_mapping_no_budget()
        with pytest.warns(UserWarning, match="invalid deprecation configuration") as first_call:
            wrapper(1)
        with assert_no_warnings():
            wrapper(2)
        assert len(first_call) == 1

    def test_skip_if_evaluated_on_every_call(self) -> None:
        """A ``skip_if`` callable is consulted on every call, before and after the budget is spent.

        A feature flag decides per call whether the deprecation applies; caching its answer once the budget is spent
        would ignore a flag that flips later. Three calls must consult it three times.
        """
        calls_before = notify_skip_condition.calls
        with pytest.warns(FutureWarning):
            depr_notify_skip_counted(1)
        depr_notify_skip_counted(2)
        depr_notify_skip_counted(3)
        assert notify_skip_condition.calls - calls_before == 3


class TestUnreadableSignature:
    """A signature that cannot be read fails at the same moment as before signature facts were read at decoration."""

    def test_source_error_raised_at_decoration(self) -> None:
        """Decorating a callable whose own signature cannot be read raises the ``inspect.signature`` error.

        The wrapper needs the source's parameters for every call, so a C-level source without signature metadata
        fails when it is decorated, at import time, rather than on its first call.
        """
        with pytest.raises(ValueError, match="no signature found"):
            make_opaque_source_wrapper()

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(depr_opaque_target_renamed, id="rename-needs-target-defaults"),
            pytest.param(depr_opaque_target_var_positional, id="surplus-needs-target-slots"),
        ],
    )
    def test_target_error_raised_at_forwarded_call(self, func: Callable[..., Any]) -> None:
        """A target whose signature cannot be read decorates fine and raises the signature error on the forwarded call.

        The module that defines the wrapper imports cleanly; the error surfaces, after the deprecation warning, on the
        call that needs the target's signature (its defaults for a rename, or its positional slots for a ``*args``
        tail), as a ``ValueError`` from ``inspect.signature``.
        """
        with pytest.warns(FutureWarning), pytest.raises(ValueError, match="no signature found"):
            func(1, 2)

    def test_target_error_of_custom_type_raised(self) -> None:
        """A target signature error whose type needs more than the message still reaches the caller with that type.

        A framework that builds signatures lazily raises its own ``ValueError`` subclass, whose constructor takes the
        callable's name and a reason, until the signature exists. The forwarded call must raise that error as it was
        read at decoration, not a ``TypeError`` from trying to rebuild it from its ``args``.
        """
        with (
            pytest.warns(FutureWarning),
            pytest.raises(SignatureNotBuiltError, match="^lazy_callable: not built yet$"),
        ):
            depr_lazy_signature_target_renamed(1, 2)


class TestDecoratedFunctionLifetime:
    """Decorating a function does not keep it alive once the caller drops it."""

    def test_source_released_with_wrapper(self) -> None:
        """A decorated function and its wrapper are garbage-collected once both are dropped.

        Plugins and tests build deprecated functions on the fly. A signature cache keyed by the function object would
        keep every one of them, and whatever its closure holds, alive for the life of the process.
        """
        source, wrapper = make_transient_notify_pair()
        source_ref = weakref.ref(source)
        del source, wrapper
        gc.collect()
        assert source_ref() is None
