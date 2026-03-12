"""Tests for deprecated functions.

TODO: Reconsider and/or refine the deprecation warning behavior for decorator syntax.
When using @decorator syntax, the warning is currently raised at decoration time
(during function/class definition or module import), not at call time.
This might differ from the intended behavior if warnings should be raised at runtime.
Consider whether the warning should be raised:
1. Only once during import/decoration (current behavior)
2. At every function call (runtime behavior)
3. Once per function call location (hybrid approach)
This could be a future improvement to make decorator deprecation more consistent
with function/class deprecation patterns.

Current test coverage:
- TestDeprecatedFunctionWrappers.test_with_decorator_syntax (line ~348)
- TestDeprecatedClassWrappers.test_with_decorator_syntax (line ~447)
"""

from typing import Callable

import pytest

from deprecate._types import DeprecationInfo
from deprecate.utils import no_warning_call
from tests.collection_deprecate import (
    depr_accuracy_extra,
    depr_accuracy_map,
    depr_accuracy_skip,
    wrap_depr_add,
    wrap_depr_add_custom_msg,
    wrap_depr_add_docstring,
    wrap_depr_add_extra,
    wrap_depr_add_mapped,
    wrap_depr_add_silent,
    wrap_depr_add_skip_func,
    wrap_depr_add_skip_true,
    wrap_depr_add_warn_2,
    wrap_depr_add_warn_inf,
    wrap_depr_self_depr,
    wrap_depr_warn_only,
    depr_make_new_cls,
    depr_make_new_cls_mapped,
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
    wrapper_add,
    wrapper_add_custom_msg,
    wrapper_add_docstring,
    wrapper_add_extra,
    wrapper_add_mapped,
    wrapper_add_silent,
    wrapper_add_skip_func,
    wrapper_add_skip_true,
    wrapper_add_warn_2,
    wrapper_add_warn_inf,
    wrapper_self_depr,
    wrapper_warn_only,
)

# ---------------------------------------------------------------------------
# Parametrize cases for TestDeprecatedWrapperForm: (wrapper-form, decorator-form)
# Each pair shares the same deprecated() configuration; the only difference is
# the source function name (reflected in warnings and __deprecated__.name).
# ---------------------------------------------------------------------------
_BASIC_CASES = [
    pytest.param(wrapper_add, "original_add", id="wrapper-form"),
    pytest.param(wrap_depr_add, "wrap_depr_add", id="decorator-form"),
]
_MAPPED_CASES = [
    pytest.param(wrapper_add_mapped, "original_add_mapped", id="wrapper-form"),
    pytest.param(wrap_depr_add_mapped, "wrap_depr_add_mapped", id="decorator-form"),
]
_WARN_INF_CASES = [
    pytest.param(wrapper_add_warn_inf, "original_add", id="wrapper-form"),
    pytest.param(wrap_depr_add_warn_inf, "wrap_depr_add_warn_inf", id="decorator-form"),
]
_WARN_2_CASES = [
    pytest.param(wrapper_add_warn_2, "original_add", id="wrapper-form"),
    pytest.param(wrap_depr_add_warn_2, "wrap_depr_add_warn_2", id="decorator-form"),
]
_SILENT_CASES = [
    pytest.param(wrapper_add_silent, "original_add", id="wrapper-form"),
    pytest.param(wrap_depr_add_silent, "wrap_depr_add_silent", id="decorator-form"),
]
_CUSTOM_MSG_CASES = [
    pytest.param(wrapper_add_custom_msg, "original_add", id="wrapper-form"),
    pytest.param(wrap_depr_add_custom_msg, "wrap_depr_add_custom_msg", id="decorator-form"),
]
_EXTRA_CASES = [
    pytest.param(wrapper_add_extra, "original_add", id="wrapper-form"),
    pytest.param(wrap_depr_add_extra, "wrap_depr_add_extra", id="decorator-form"),
]
_SKIP_TRUE_CASES = [
    pytest.param(wrapper_add_skip_true, "original_add", id="wrapper-form"),
    pytest.param(wrap_depr_add_skip_true, "wrap_depr_add_skip_true", id="decorator-form"),
]
_SKIP_FUNC_CASES = [
    pytest.param(wrapper_add_skip_func, "original_add", id="wrapper-form"),
    pytest.param(wrap_depr_add_skip_func, "wrap_depr_add_skip_func", id="decorator-form"),
]
_WARN_ONLY_CASES = [
    pytest.param(wrapper_warn_only, "original_warn_only", id="wrapper-form"),
    pytest.param(wrap_depr_warn_only, "wrap_depr_warn_only", id="decorator-form"),
]
_SELF_DEPR_CASES = [
    pytest.param(wrapper_self_depr, "original_self_rename", id="wrapper-form"),
    pytest.param(wrap_depr_self_depr, "wrap_depr_self_depr", id="decorator-form"),
]
_DOCSTRING_CASES = [
    pytest.param(wrapper_add_docstring, "original_add_with_docstring", id="wrapper-form"),
    pytest.param(wrap_depr_add_docstring, "wrap_depr_add_docstring", id="decorator-form"),
]


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

    def test_function_to_class_forwarding(self) -> None:
        """Deprecated function targeting a class should instantiate and return the class."""
        from tests.collection_targets import NewCls

        getattr(depr_make_new_cls, "_state").warned_calls = 0
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
        from tests.collection_targets import NewCls

        getattr(depr_make_new_cls_mapped, "_state").warned_calls = 0
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

    def test_default_once(self) -> None:
        """Check that the warning is raised only once per function."""
        # Pre-call to trigger the warning if it wasn't already triggered (though tests should be independent)
        # However, depr_sum might have been called in previous test if not careful,
        # but here we want to ensure that WITHIN a clean state it only warns once.
        # Note: depr_sum is imported from collection_deprecate, it might share state if not reset.
        getattr(depr_sum, "_state").warned_calls = 0
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
        for func in (depr_pow_self_double, depr_pow_self_twice):
            state = getattr(func, "_state")
            state.warned_calls = 0
            state.warned_args.clear()

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
        getattr(depr_pow_self_double, "_state").warned_args.clear()

        with no_warning_call():
            assert depr_pow_self_double(2, nc1=1, nc2=2) == 8

        # When both c1 and c2 are provided, warns about both together
        with pytest.warns(FutureWarning, match="The `depr_pow_self_double` uses depr. args:"):
            assert depr_pow_self_double(2, c1=3, c2=4, nc1=1) == 32

        # Need to reset after first warning because both counters were incremented
        getattr(depr_pow_self_double, "_state").warned_args.clear()

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
            TypeError, match=r"Failed mapping of `depr_pow_wrong`, arguments not accepted by target: \['c'\]"
        ):
            depr_pow_wrong(2)

    def test_incomplete_once(self) -> None:
        """Check that the warning is raised only once per function for incomplete mappings."""
        getattr(depr_pow_args, "_state").warned_calls = 0
        with pytest.warns(FutureWarning):
            depr_pow_args(2, 1)
        with no_warning_call(FutureWarning):
            assert depr_pow_args(2, 1) == 2

    def test_incomplete_independent(self) -> None:
        """Check that it does not affect other functions for incomplete mappings when called with kwargs."""
        # reset the warning
        getattr(depr_pow_args, "_state").warned_calls = 0
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
    assert depr_sum.__deprecated__ == DeprecationInfo(
        deprecated_in="0.1",
        remove_in="0.5",
        name="depr_sum",
        target=base_sum_kwargs,
        args_mapping=None,
    )


class TestDeprecatedFunctionWrappers:
    """Test suite for deprecating function-based wrapper/decorators."""

    @pytest.fixture(autouse=True)
    def reset_warnings(self) -> None:
        """Reset warning counters before each test for independence."""
        from tests.collection_deprecate import depr_timing_wrapper

        getattr(depr_timing_wrapper, "_state").warned_calls = 0

    def test_shows_warning(self) -> None:
        """Test that deprecated wrapper shows deprecation warning."""
        from tests.collection_deprecate import depr_timing_wrapper

        def sample_function(x: int) -> int:
            """A simple function for testing wrappers."""
            return x * 2

        with pytest.warns(FutureWarning, match="`depr_timing_wrapper` was deprecated"):
            wrapped_func = depr_timing_wrapper(sample_function)

        # Verify the wrapper was applied correctly
        assert callable(wrapped_func)

    def test_forwards_correctly(self) -> None:
        """Test that wrapper forwards to new implementation."""
        from tests.collection_deprecate import depr_timing_wrapper

        def sample_function(x: int) -> int:
            """A simple function for testing wrappers."""
            return x * 2

        with pytest.warns(FutureWarning, match="`depr_timing_wrapper` was deprecated"):
            wrapped_func = depr_timing_wrapper(sample_function)

        # Verify the wrapped function executes correctly
        result = wrapped_func(5)
        assert result == 10

    def test_new_implementation_no_warning(self) -> None:
        """Test that new implementation works without warnings."""
        from tests.collection_targets import timing_wrapper

        def sample_function(x: int) -> int:
            """A simple function for testing wrappers."""
            return x * 2

        with no_warning_call(FutureWarning):
            new_wrapped = timing_wrapper(sample_function)
            result = new_wrapped(7)
            assert result == 14

    def test_with_decorator_syntax(self) -> None:
        """Test warning when applied using @ decorator syntax."""
        from tests.collection_deprecate import depr_timing_wrapper

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
        from tests.collection_deprecate import DeprTimerDecorator

        getattr(DeprTimerDecorator.__init__, "_state").warned_calls = 0

    def test_shows_warning(self) -> None:
        """Test that deprecated wrapper shows deprecation warning."""
        from tests.collection_deprecate import DeprTimerDecorator

        def sample_function(x: int) -> int:
            """A simple function for testing class-based wrappers."""
            return x + 5

        with pytest.warns(FutureWarning, match="`DeprTimerDecorator` was deprecated"):
            wrapped_func = DeprTimerDecorator(sample_function)

        # Verify the wrapper was applied correctly
        assert callable(wrapped_func)

    def test_forwards_correctly(self) -> None:
        """Test that wrapper forwards to new implementation."""
        from tests.collection_deprecate import DeprTimerDecorator

        def sample_function(x: int) -> int:
            """A simple function for testing class-based wrappers."""
            return x + 5

        # Expect warning on first use
        with pytest.warns(FutureWarning, match="`DeprTimerDecorator` was deprecated"):
            wrapped_func = DeprTimerDecorator(sample_function)

        # Verify the wrapped function executes correctly
        result = wrapped_func(3)
        assert result == 8

    def test_preserves_attributes(self) -> None:
        """Test that wrapper preserves tracking attributes."""
        from tests.collection_deprecate import DeprTimerDecorator

        def sample_function(x: int) -> int:
            """A simple function for testing class-based wrappers."""
            return x + 5

        # Expect warning on first use, then test attributes
        with pytest.warns(FutureWarning, match="`DeprTimerDecorator` was deprecated"):
            wrapped_func = DeprTimerDecorator(sample_function)

        # Call the function once
        wrapped_func(3)

        # Verify tracking attributes exist and work (from TimerDecorator)
        assert hasattr(wrapped_func, "total_time")
        assert hasattr(wrapped_func, "calls")
        assert wrapped_func.calls == 1

    def test_new_implementation_no_warning(self) -> None:
        """Test that new implementation works without warnings."""
        from tests.collection_targets import TimerDecorator

        def sample_function(x: int) -> int:
            """A simple function for testing class-based wrappers."""
            return x + 5

        with no_warning_call(FutureWarning):
            new_wrapped = TimerDecorator(sample_function)
            result = new_wrapped(4)
            assert result == 9
            assert new_wrapped.calls == 1

    def test_with_decorator_syntax(self) -> None:
        """Test warning when applied using @ decorator syntax."""
        from tests.collection_deprecate import DeprTimerDecorator

        with pytest.warns(FutureWarning, match="`DeprTimerDecorator` was deprecated"):

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


class _WrapperFormBase:
    """Shared autouse reset fixture for both-form equivalence tests."""

    @pytest.fixture(autouse=True)
    def _reset_wrapper_state(self) -> None:
        """Reset warning counters before each test for independence."""
        for func in (
            wrapper_add, wrap_depr_add,
            wrapper_add_mapped, wrap_depr_add_mapped,
            wrapper_add_warn_inf, wrap_depr_add_warn_inf,
            wrapper_add_warn_2, wrap_depr_add_warn_2,
            wrapper_add_silent, wrap_depr_add_silent,
            wrapper_add_custom_msg, wrap_depr_add_custom_msg,
            wrapper_add_extra, wrap_depr_add_extra,
            wrapper_add_skip_true, wrap_depr_add_skip_true,
            wrapper_add_skip_func, wrap_depr_add_skip_func,
            wrapper_warn_only, wrap_depr_warn_only,
            wrapper_add_docstring, wrap_depr_add_docstring,
        ):
            getattr(func, "_state").warned_calls = 0
        for func in (wrapper_self_depr, wrap_depr_self_depr):
            getattr(func, "_state").warned_calls = 0
            getattr(func, "_state").warned_args.clear()


@pytest.mark.parametrize("func,name", _BASIC_CASES)
class TestWrapperFormBasic(_WrapperFormBase):
    """Both decorator and wrapper form produce identical behavior for basic forwarding."""

    def test_produces_warning(self, func: Callable, name: str) -> None:
        """Both forms emit FutureWarning with source name, version, and target path."""
        with pytest.warns(
            FutureWarning,
            match=f"The `{name}` was deprecated since v0.5"
            " in favor of `tests.collection_targets.base_add`."
            " It will be removed in v1.0.",
        ):
            func(1, 2)

    def test_forwards_call(self, func: Callable, name: str) -> None:
        """Both forms correctly forward positional arguments to the target."""
        with pytest.warns(FutureWarning):
            result = func(3, 4)
        assert result == 7

    def test_forwards_kwargs(self, func: Callable, name: str) -> None:
        """Both forms pass keyword arguments through to the target."""
        with pytest.warns(FutureWarning):
            result = func(a=10, b=20)
        assert result == 30

    def test_uses_target_defaults(self, func: Callable, name: str) -> None:
        """Both forms inherit default argument values from the source signature."""
        with pytest.warns(FutureWarning):
            result = func(5)
        assert result == 5

    def test_deprecated_attribute(self, func: Callable, name: str) -> None:
        """Both forms set __deprecated__ with correct DeprecationInfo at decoration time."""
        from tests.collection_targets import base_add

        assert hasattr(func, "__deprecated__")
        info: DeprecationInfo = getattr(func, "__deprecated__")
        assert isinstance(info, DeprecationInfo)
        assert info.deprecated_in == "0.5"
        assert info.remove_in == "1.0"
        assert info.target is base_add
        assert info.name == name

    def test_warns_only_once_by_default(self, func: Callable, name: str) -> None:
        """Both forms with default num_warns=1 warn only on the first call."""
        with pytest.warns(FutureWarning):
            assert func(1, 2) == 3
        with no_warning_call(FutureWarning):
            assert func(3, 4) == 7


@pytest.mark.parametrize("func,name", _MAPPED_CASES)
class TestWrapperFormMapped(_WrapperFormBase):
    """Both forms apply args_mapping to rename kwargs before forwarding."""

    def test_with_args_mapping(self, func: Callable, name: str) -> None:
        """Both forms with args_mapping rename x->a and y->b before forwarding."""
        with pytest.warns(FutureWarning):
            result = func(x=10, y=20)
        assert result == 30

    def test_with_args_mapping_attribute(self, func: Callable, name: str) -> None:
        """Both forms with args_mapping record the mapping in __deprecated__."""
        info: DeprecationInfo = getattr(func, "__deprecated__")
        assert info.args_mapping == {"x": "a", "y": "b"}


@pytest.mark.parametrize("func,name", _WARN_INF_CASES)
class TestWrapperFormNumWarnsInf(_WrapperFormBase):
    """Both forms with num_warns=-1 emit a warning on every call."""

    def test_num_warns_inf(self, func: Callable, name: str) -> None:
        """Both forms with num_warns=-1 warn on every call."""

        def _call() -> None:
            for _ in range(5):
                assert func(1, 2) == 3

        with pytest.warns(FutureWarning) as record:
            _call()
        assert len(record) == 5


@pytest.mark.parametrize("func,name", _WARN_2_CASES)
class TestWrapperFormNumWarnsCapped(_WrapperFormBase):
    """Both forms with num_warns=N warn only on the first N calls."""

    def test_num_warns_limited(self, func: Callable, name: str) -> None:
        """Both forms with num_warns=2 warn only the first 2 calls."""

        def _call() -> None:
            for _ in range(5):
                assert func(1, 2) == 3

        with pytest.warns(FutureWarning) as record:
            _call()
        assert len(record) == 2


@pytest.mark.parametrize("func,name", _SILENT_CASES)
class TestWrapperFormSilent(_WrapperFormBase):
    """Both forms with stream=None forward without emitting any warning."""

    def test_stream_none(self, func: Callable, name: str) -> None:
        """Both forms with stream=None forward without emitting any warning."""
        with no_warning_call(FutureWarning):
            result = func(3, 4)
        assert result == 7


@pytest.mark.parametrize("func,name", _CUSTOM_MSG_CASES)
class TestWrapperFormCustomMsg(_WrapperFormBase):
    """Both forms with template_mgs emit the custom-formatted warning."""

    def test_custom_template(self, func: Callable, name: str) -> None:
        """Both forms with template_mgs emit the custom-formatted warning."""
        with pytest.warns(
            FutureWarning,
            match=rf"v0\.5: `{name}` is old, use `base_add`",
        ):
            result = func(2, 3)
        assert result == 5


@pytest.mark.parametrize("func,name", _EXTRA_CASES)
class TestWrapperFormArgsExtra(_WrapperFormBase):
    """Both forms inject and override call arguments via args_extra."""

    def test_args_extra_overrides(self, func: Callable, name: str) -> None:
        """Both forms with args_extra override a user-provided argument."""
        with pytest.warns(FutureWarning):
            result = func(1, b=5)
        assert result == 101  # args_extra={"b": 100} overrides b=5

    def test_args_extra_default(self, func: Callable, name: str) -> None:
        """Both forms with args_extra inject b=100 even when b is not passed."""
        with pytest.warns(FutureWarning):
            result = func(1)
        assert result == 101


@pytest.mark.parametrize("func,name", _SKIP_TRUE_CASES)
class TestWrapperFormSkipTrue(_WrapperFormBase):
    """Both forms with skip_if=True execute the source body, bypassing deprecation."""

    def test_skip_if_true(self, func: Callable, name: str) -> None:
        """Both forms with skip_if=True execute source body without warning or forwarding."""
        with no_warning_call(FutureWarning):
            result = func(3, 4)
        assert result is None  # source body calls void() → None


@pytest.mark.parametrize("func,name", _SKIP_FUNC_CASES)
class TestWrapperFormSkipCallable(_WrapperFormBase):
    """Both forms with skip_if=callable bypass deprecation when the callable returns True."""

    def test_skip_if_callable(self, func: Callable, name: str) -> None:
        """Both forms with skip_if=lambda: True bypass deprecation at runtime."""
        with no_warning_call(FutureWarning):
            result = func(5, 6)
        assert result is None


@pytest.mark.parametrize("func,name", _WARN_ONLY_CASES)
class TestWrapperFormWarnOnly(_WrapperFormBase):
    """Both forms with target=None warn but still execute the original body."""

    def test_target_none_warns(self, func: Callable, name: str) -> None:
        """Both forms with target=None emit warning but execute the original body."""
        with pytest.warns(
            FutureWarning,
            match=f"The `{name}` was deprecated since v0.5. It will be removed in v1.0.",
        ):
            result = func(3, 4)
        assert result == 7

    def test_target_none_attribute(self, func: Callable, name: str) -> None:
        """Both forms with target=None record target=None in __deprecated__."""
        info: DeprecationInfo = getattr(func, "__deprecated__")
        assert info.target is None


@pytest.mark.parametrize("func,name", _SELF_DEPR_CASES)
class TestWrapperFormSelfDepr(_WrapperFormBase):
    """Both forms with target=True remap deprecated argument names in-place."""

    def test_self_deprecation_new_arg(self, func: Callable, name: str) -> None:
        """Both forms with target=True pass through silently when the new arg is used."""
        with no_warning_call():
            result = func(2.0, new_exp=3.0)
        assert result == 8.0

    def test_self_deprecation_old_arg(self, func: Callable, name: str) -> None:
        """Both forms with target=True warn and remap old_exp -> new_exp."""
        with pytest.warns(
            FutureWarning,
            match=rf"`{name}` uses deprecated arguments: `old_exp` -> `new_exp`",
        ):
            result = func(2.0, old_exp=3.0)
        assert result == 8.0

    def test_self_deprecation_attribute(self, func: Callable, name: str) -> None:
        """Both forms with target=True record target=True and args_mapping in __deprecated__."""
        info: DeprecationInfo = getattr(func, "__deprecated__")
        assert info.target is True
        assert info.args_mapping == {"old_exp": "new_exp"}


@pytest.mark.parametrize("func,name", _DOCSTRING_CASES)
class TestWrapperFormDocstring(_WrapperFormBase):
    """Both forms with update_docstring=True append a Sphinx-style deprecation notice."""

    def test_update_docstring(self, func: Callable, name: str) -> None:
        """Both forms with update_docstring=True append deprecation notice to docstring."""
        doc = func.__doc__
        assert doc is not None
        assert ".. deprecated:: 0.5" in doc
        assert "Will be removed in 1.0." in doc
        assert "base_add" in doc
