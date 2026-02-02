"""Tests for deprecated functions."""

import pytest

from deprecate.utils import no_warning_call
from tests.collection_deprecate import (
    depr_accuracy_extra,
    depr_accuracy_map,
    depr_accuracy_skip,
    depr_pow_args,
    depr_pow_mix,
    depr_pow_self,
    depr_pow_self_double,
    depr_pow_self_twice,
    depr_pow_skip_if_false_true,
    depr_pow_skip_if_func,
    depr_pow_skip_if_func_int,
    depr_pow_skip_if_true,
    depr_pow_skip_if_true_false,
    depr_pow_wrong,
    depr_sum,
    depr_sum_calls_2,
    depr_sum_calls_inf,
    depr_sum_msg,
    depr_sum_no_stream,
    depr_sum_warn_only,
)


class TestDeprecationWarnings:
    """Tests for basic deprecation warning behavior."""

    def test_warn_only(self) -> None:
        """Test deprecated function that only warns."""
        with pytest.warns(
            FutureWarning, match="The `depr_sum_warn_only` was deprecated since v0.2. It will be removed in v0.3."
        ):
            assert depr_sum_warn_only(2) is None

    def test_default(self) -> None:
        """Testing some base/default configurations."""
        with pytest.warns(
            FutureWarning,
            match="The `depr_sum` was deprecated since v0.1 in favor of `tests.collection_targets.base_sum_kwargs`."
            " It will be removed in v0.5.",
        ):
            assert depr_sum(2) == 7

    def test_default_once(self) -> None:
        """Check that the warning is raised only once per function."""
        # Pre-call to trigger the warning if it wasn't already triggered (though tests should be independent)
        # However, depr_sum might have been called in previous test if not careful,
        # but here we want to ensure that WITHIN a clean state it only warns once.
        # Note: depr_sum is imported from collection_deprecate, it might share state if not reset.
        depr_sum._warned = 0
        with pytest.warns(FutureWarning):
            assert depr_sum(2) == 7
        with no_warning_call(FutureWarning):
            assert depr_sum(3) == 8

    def test_default_independent(self) -> None:
        """Check that it does not affect other functions when called with positional args."""
        with pytest.warns(
            FutureWarning,
            match="The `depr_pow_mix` was deprecated since v0.1 in favor of `tests.collection_targets.base_pow_args`."
            " It will be removed in v0.5.",
        ):
            assert depr_pow_mix(2, 1) == 2

    def test_stream_calls_no_stream(self) -> None:
        """Check that the warning is NOT raised when stream is None."""
        with no_warning_call(FutureWarning):
            assert depr_sum_no_stream(3) == 8

    def test_stream_calls_limit(self) -> None:
        """Check that the warning is raised only N times."""

        def _call_depr_sum_calls_2() -> None:
            for _ in range(5):
                assert depr_sum_calls_2(3) == 8

        with pytest.warns(FutureWarning) as record:
            _call_depr_sum_calls_2()
        assert len(record) == 2

    def test_stream_calls_inf(self) -> None:
        """Check that the warning is raised infinitely."""

        def _call_depr_sum_calls_inf() -> None:
            for _ in range(5):
                assert depr_sum_calls_inf(3) == 8

        with pytest.warns(FutureWarning) as record:
            _call_depr_sum_calls_inf()
        assert len(record) == 5

    def test_stream_calls_msg(self) -> None:
        """Test deprecated function with custom message."""
        with pytest.warns(FutureWarning, match="v0.1: `depr_sum_msg` was deprecated, use `base_sum_kwargs`"):
            assert depr_sum_msg(3) == 8


class TestArgumentMapping:
    """Tests for deprecated function arguments mapping."""

    @pytest.fixture(autouse=True)
    def _reset_deprecation_state(self) -> None:
        """Reset deprecation state for functions with chained or multiple deprecations."""
        # List of functions and their warning attributes to reset
        reset_config = [
            (depr_pow_self_double, ["_warned", "_warned_c1", "_warned_c2"]),
            (depr_pow_self_twice, ["_warned", "_warned_c1", "_warned_nc1"]),
        ]

        for func, attrs in reset_config:
            for attr in attrs:
                if hasattr(func, attr):
                    # Use False for boolean _warned, 0 for counter attributes
                    setattr(func, attr, False if attr == "_warned" else 0)

    def test_arguments_new_only(self) -> None:
        """Test calling with new arguments only (no warning)."""
        with no_warning_call():
            assert depr_pow_self(2, new_coef=3) == 8

    def test_arguments_deprecated(self) -> None:
        """Test calling with deprecated argument (should warn)."""
        with pytest.warns(
            FutureWarning,
            match="The `depr_pow_self` uses deprecated arguments: `coef` -> `new_coef`."
            " They were deprecated since v0.1 and will be removed in v0.5.",
        ):
            assert depr_pow_self(2, 3) == 8

    def test_arguments_double_deprecated_c1(self) -> None:
        """Test double mapping, calling with first deprecated argument."""
        with pytest.warns(FutureWarning, match="The `depr_pow_self_double` uses depr. args: `c1` -> `nc1`."):
            assert depr_pow_self_double(2, c1=3) == 32

    def test_arguments_double_deprecated_c2(self) -> None:
        """Test double mapping, calling with second deprecated argument."""
        with pytest.warns(FutureWarning, match="The `depr_pow_self_double` uses depr. args: `c2` -> `nc2`."):
            assert depr_pow_self_double(2, c2=2) == 8

    def test_arguments_double_mixed(self) -> None:
        """Testing that preferable use the new arguments when both are provided."""
        # Reset warning state to ensure test independence
        if hasattr(depr_pow_self_double, "_warned_c1"):
            depr_pow_self_double._warned_c1 = 0
        if hasattr(depr_pow_self_double, "_warned_c2"):
            depr_pow_self_double._warned_c2 = 0

        with no_warning_call():
            assert depr_pow_self_double(2, nc1=1, nc2=2) == 8

        # When both c1 and c2 are provided, warns about both together
        with pytest.warns(FutureWarning, match="The `depr_pow_self_double` uses depr. args:"):
            assert depr_pow_self_double(2, c1=3, c2=4, nc1=1) == 32

        # Need to reset after first warning because both counters were incremented
        depr_pow_self_double._warned_c1 = 0
        depr_pow_self_double._warned_c2 = 0

        # When both c1 and c2 are provided, warns about both together
        # Result is 32 because: c1->nc1=3, c2->nc2=4, but nc2=2 (user wins), so 2**(0+0+3+2)=32
        with pytest.warns(FutureWarning, match="The `depr_pow_self_double` uses depr. args:"):
            assert depr_pow_self_double(2, c1=3, c2=4, nc2=2) == 32

    def test_chain_deprecated(self) -> None:
        """Test chaining deprecation wrappers, calling with deprecated argument."""
        with pytest.warns(FutureWarning) as warns:
            assert depr_pow_self_twice(2, 3) == 8
        assert len(warns) == 2

    def test_chain_new(self) -> None:
        """Test chaining deprecation wrappers, calling with new argument."""
        # First, trigger the deprecation warning so that per-argument counters are incremented.
        with pytest.warns(FutureWarning):
            assert depr_pow_self_twice(2, 3) == 8
        # After the initial warning, calling with the new argument should be quiet.
        with no_warning_call():
            assert depr_pow_self_twice(2, c1=3) == 8


def test_skip_if_true() -> None:
    """Test conditional wrapper skip when skip_if=True."""
    with no_warning_call():
        assert depr_pow_skip_if_true(2, c1=2) == 2


def test_skip_if_func() -> None:
    """Test conditional wrapper skip when skip_if is a function returning True."""
    with no_warning_call():
        assert depr_pow_skip_if_func(2, c1=2) == 2


def test_skip_if_true_false() -> None:
    """Test conditional wrapper skip with decorator order: @skip_if=True then @skip_if=False (outer skips)."""
    with pytest.warns(FutureWarning, match="Depr: v0.1 rm v0.2 for args: `c1` -> `nc1`."):
        assert depr_pow_skip_if_true_false(2, c1=2) == 0.5


def test_skip_if_false_true() -> None:
    """Test conditional wrapper skip with decorator order: @skip_if=False then @skip_if=True (outer skips)."""
    with pytest.warns(FutureWarning, match="Depr: v0.1 rm v0.2 for args: `c1` -> `nc1`."):
        assert depr_pow_skip_if_false_true(2, c1=2) == 0.5


class TestErrorHandling:
    """Test error handling in deprecated functions."""

    def test_incomplete_missing_arg(self) -> None:
        """Test missing required argument."""
        with pytest.raises(TypeError, match="missing 1 required positional argument: 'b'"):
            depr_pow_args(2)

    def test_incomplete_missing_target(self) -> None:
        """Test missing argument in target."""
        with pytest.raises(
            TypeError, match=r"Failed mapping of `depr_pow_wrong`, arguments missing in target source: \['c'\]"
        ):
            depr_pow_wrong(2)

    def test_incomplete_once(self) -> None:
        """Check that the warning is raised only once per function for incomplete mappings."""
        depr_pow_args._warned = False
        with pytest.warns(FutureWarning):
            depr_pow_args(2, 1)
        with no_warning_call(FutureWarning):
            assert depr_pow_args(2, 1) == 2

    def test_incomplete_independent(self) -> None:
        """Check that it does not affect other functions for incomplete mappings when called with kwargs."""
        # reset the warning
        depr_pow_args._warned = False
        with pytest.warns(FutureWarning, match="`depr_pow_args` >> `base_pow_args` in v1.0 rm v1.3."):
            assert depr_pow_args(b=2, a=1) == 1

    def test_invalid_skip_if(self) -> None:
        """Test invalid skip_if return value."""
        with pytest.raises(TypeError, match="User function 'skip_if' shall return bool, but got: <class 'int'>"):
            depr_pow_skip_if_func_int(2, c1=2)


def test_deprecated_func_accuracy_map() -> None:
    """Test mapping to external accuracy_map function."""
    with pytest.warns(FutureWarning):
        assert depr_accuracy_map([1, 0, 1, 2]) == 0.5


def test_deprecated_func_accuracy_skip() -> None:
    """Test mapping to external accuracy_skip function."""
    with pytest.warns(FutureWarning):
        assert depr_accuracy_skip([1, 0, 1, 2]) == 0.5


def test_deprecated_func_accuracy_extra() -> None:
    """Test mapping to external accuracy_extra function."""
    with pytest.warns(FutureWarning):
        assert depr_accuracy_extra([1, 0, 1, 2]) == 0.75


def test_deprecated_func_attribute_set_at_decoration_time() -> None:
    """Test that __deprecated__ attribute is set at decoration time, not call time.

    This verifies that the __deprecated__ attribute is available immediately
    after the decorator is applied, without needing to call the function first.
    """
    from tests.collection_targets import base_sum_kwargs

    # Verify __deprecated__ is set WITHOUT calling the function (using depr_sum from collection_deprecate)
    assert hasattr(depr_sum, "__deprecated__")
    assert depr_sum.__deprecated__ == {
        "deprecated_in": "0.1",
        "remove_in": "0.5",
        "target": base_sum_kwargs,
        "args_mapping": None,
    }


class TestDeprecatedFunctionWrappers:
    """Test suite for deprecating function-based wrapper/decorators."""

    def test_deprecated_wrapper_shows_warning(self) -> None:
        """Test that deprecated wrapper function shows deprecation warning."""
        from tests.collection_deprecate import depr_timing_wrapper

        # Reset warning counter
        if hasattr(depr_timing_wrapper, "_warned"):
            depr_timing_wrapper._warned = 0

        def sample_function(x: int) -> int:
            """A simple function for testing wrappers."""
            return x * 2

        with pytest.warns(
            FutureWarning,
            match="The `depr_timing_wrapper` was deprecated since v1.0 in favor of "
            "`tests.collection_targets.timing_wrapper`. It will be removed in v2.0.",
        ):
            wrapped_func = depr_timing_wrapper(sample_function)

        # Verify the wrapper was applied correctly
        assert callable(wrapped_func)

    def test_deprecated_wrapper_forwards_correctly(self) -> None:
        """Test that deprecated wrapper function forwards to new implementation."""
        from tests.collection_deprecate import depr_timing_wrapper

        # Ensure this test does not depend on previous tests to set the warned state
        if hasattr(depr_timing_wrapper, "_warned"):
            depr_timing_wrapper._warned = 1

        def sample_function(x: int) -> int:
            """A simple function for testing wrappers."""
            return x * 2

        # On subsequent use, no deprecation warning should be emitted
        with no_warning_call(FutureWarning):
            wrapped_func = depr_timing_wrapper(sample_function)

        # Verify the wrapped function executes correctly
        result = wrapped_func(5)
        assert result == 10

    def test_new_wrapper_no_warning(self) -> None:
        """Test that new wrapper function works without warnings."""
        from tests.collection_targets import timing_wrapper

        def sample_function(x: int) -> int:
            """A simple function for testing wrappers."""
            return x * 2

        with no_warning_call(FutureWarning):
            new_wrapped = timing_wrapper(sample_function)
            result = new_wrapped(7)
            assert result == 14

    def test_deprecated_wrapper_with_decorator_syntax(self) -> None:
        """Test that deprecated wrapper shows warning when applied using @ decorator syntax."""
        from tests.collection_deprecate import depr_timing_wrapper

        # Reset warning counter
        if hasattr(depr_timing_wrapper, "_warned"):
            depr_timing_wrapper._warned = 0

        with pytest.warns(
            FutureWarning,
            match="The `depr_timing_wrapper` was deprecated since v1.0 in favor of "
            "`tests.collection_targets.timing_wrapper`. It will be removed in v2.0.",
        ):

            @depr_timing_wrapper
            def sample_function(x: int) -> int:
                """A simple function for testing wrappers with @ syntax."""
                return x * 3

        # Verify the wrapped function works correctly
        result = sample_function(4)
        assert result == 12


class TestDeprecatedClassWrappers:
    """Test suite for deprecating class-based wrapper/decorators."""

    def test_deprecated_wrapper_shows_warning(self) -> None:
        """Test that deprecated class-based wrapper shows deprecation warning."""
        from tests.collection_deprecate import DeprTimerDecorator

        # Reset warning counter
        if hasattr(DeprTimerDecorator.__init__, "_warned"):
            DeprTimerDecorator.__init__._warned = 0

        def sample_function(x: int) -> int:
            """A simple function for testing class-based wrappers."""
            return x + 5

        with pytest.warns(
            FutureWarning,
            match="The `DeprTimerDecorator` was deprecated since v1.0 in favor of "
            "`tests.collection_targets.TimerDecorator`. It will be removed in v2.0.",
        ):
            wrapped_func = DeprTimerDecorator(sample_function)

        # Verify the wrapper was applied correctly
        assert callable(wrapped_func)

    def test_deprecated_wrapper_forwards_correctly(self) -> None:
        """Test that deprecated class-based wrapper forwards to new implementation."""
        from tests.collection_deprecate import DeprTimerDecorator

        # Reset warning counter to ensure consistent behavior
        if hasattr(DeprTimerDecorator.__init__, "_warned"):
            DeprTimerDecorator.__init__._warned = 0

        def sample_function(x: int) -> int:
            """A simple function for testing class-based wrappers."""
            return x + 5

        # Expect warning on first use
        with pytest.warns(FutureWarning):
            wrapped_func = DeprTimerDecorator(sample_function)

        # Verify the wrapped function executes correctly
        result = wrapped_func(3)
        assert result == 8

    def test_deprecated_wrapper_preserves_attributes(self) -> None:
        """Test that deprecated class-based wrapper preserves tracking attributes."""
        from tests.collection_deprecate import DeprTimerDecorator

        def sample_function(x: int) -> int:
            """A simple function for testing class-based wrappers."""
            return x + 5

        # Don't expect warning if already warned in previous test
        wrapped_func = DeprTimerDecorator(sample_function)

        # Call the function once
        wrapped_func(3)

        # Verify tracking attributes exist and work (from TimerDecorator)
        assert hasattr(wrapped_func, "total_time")
        assert hasattr(wrapped_func, "calls")
        assert wrapped_func.calls == 1

    def test_new_wrapper_no_warning(self) -> None:
        """Test that new class-based wrapper works without warnings."""
        from tests.collection_targets import TimerDecorator

        def sample_function(x: int) -> int:
            """A simple function for testing class-based wrappers."""
            return x + 5

        with no_warning_call(FutureWarning):
            new_wrapped = TimerDecorator(sample_function)
            result = new_wrapped(4)
        assert result == 9
        assert new_wrapped.calls == 1

    def test_deprecated_wrapper_with_decorator_syntax(self) -> None:
        """Test that deprecated class-based wrapper shows warning when applied using @ decorator syntax."""
        from tests.collection_deprecate import DeprTimerDecorator

        # Reset warning counter
        if hasattr(DeprTimerDecorator.__init__, "_warned"):
            DeprTimerDecorator.__init__._warned = 0

        with pytest.warns(
            FutureWarning,
            match="The `DeprTimerDecorator` was deprecated since v1.0 in favor of "
            "`tests.collection_targets.TimerDecorator`. It will be removed in v2.0.",
        ):

            @DeprTimerDecorator
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
