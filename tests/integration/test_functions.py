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

import pytest

from deprecate._types import DeprecationInfo
from deprecate.utils import no_warning_call
from tests.collection_deprecate import (
    depr_accuracy_extra,
    depr_accuracy_map,
    depr_accuracy_skip,
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


class TestDeprecatedWrapperForm:
    """Tests for deprecated() used in assignment (wrapper) form instead of @decorator syntax.

    The wrapper form ``new = deprecated(...)(old)`` is functionally equivalent to
    ``@deprecated(...) def old: ...`` but exercises a different code path in user
    code. These tests verify that the contract is identical for both forms.
    """

    @pytest.fixture(autouse=True)
    def _reset_wrapper_state(self) -> None:
        """Reset warning counters before each test for independence."""
        for wrapper in (
            wrapper_add,
            wrapper_add_mapped,
            wrapper_add_warn_inf,
            wrapper_add_warn_2,
            wrapper_add_silent,
            wrapper_add_custom_msg,
            wrapper_add_extra,
            wrapper_add_skip_true,
            wrapper_add_skip_func,
            wrapper_warn_only,
            wrapper_add_docstring,
        ):
            getattr(wrapper, "_state").warned_calls = 0
        # Also reset per-argument warning counters for self-deprecation wrapper
        getattr(wrapper_self_depr, "_state").warned_calls = 0
        getattr(wrapper_self_depr, "_state").warned_args.clear()

    def test_wrapper_form_produces_warning(self) -> None:
        """Wrapper form emits the same FutureWarning as decorator form."""
        with pytest.warns(
            FutureWarning,
            match="The `original_add` was deprecated since v0.5"
            " in favor of `tests.collection_targets.base_add`."
            " It will be removed in v1.0.",
        ):
            wrapper_add(1, 2)

    def test_wrapper_form_forwards_call(self) -> None:
        """Wrapper form correctly forwards the call to the target function."""
        with pytest.warns(FutureWarning):
            result = wrapper_add(3, 4)
        assert result == 7, f"Expected base_add(3, 4) == 7, got {result}"

    def test_wrapper_form_forwards_kwargs(self) -> None:
        """Wrapper form passes keyword arguments through to the target."""
        with pytest.warns(FutureWarning):
            result = wrapper_add(a=10, b=20)
        assert result == 30, f"Expected base_add(a=10, b=20) == 30, got {result}"

    def test_wrapper_form_uses_target_defaults(self) -> None:
        """Wrapper form inherits the target's default argument values."""
        with pytest.warns(FutureWarning):
            result = wrapper_add(5)
        assert result == 5, f"Expected base_add(5, b=0) == 5, got {result}"

    def test_wrapper_form_deprecated_attribute(self) -> None:
        """Wrapper form sets __deprecated__ with correct DeprecationInfo."""
        from tests.collection_targets import base_add

        assert hasattr(wrapper_add, "__deprecated__"), (
            "wrapper_add must have __deprecated__ attribute set at decoration time"
        )
        info = wrapper_add.__deprecated__
        assert isinstance(info, DeprecationInfo)
        assert info.deprecated_in == "0.5"
        assert info.remove_in == "1.0"
        assert info.target is base_add
        assert info.name == "original_add"

    def test_wrapper_form_warns_only_once_by_default(self) -> None:
        """Wrapper form with default num_warns=1 warns only on the first call."""
        with pytest.warns(FutureWarning):
            assert wrapper_add(1, 2) == 3
        with no_warning_call(FutureWarning):
            assert wrapper_add(3, 4) == 7

    def test_wrapper_form_with_args_mapping(self) -> None:
        """Wrapper form with args_mapping renames x->a and y->b before forwarding."""
        with pytest.warns(FutureWarning):
            result = wrapper_add_mapped(x=10, y=20)
        assert result == 30, f"Expected base_add(a=10, b=20) == 30, got {result}"

    def test_wrapper_form_with_args_mapping_attribute(self) -> None:
        """Wrapper form with args_mapping records the mapping in __deprecated__."""
        info: DeprecationInfo = getattr(wrapper_add_mapped, "__deprecated__")
        assert info.args_mapping == {"x": "a", "y": "b"}

    # ---------- num_warns=-1 (warn every call) ----------

    def test_wrapper_form_num_warns_inf(self) -> None:
        """Wrapper form with num_warns=-1 warns on every call."""

        def _call_wrapper() -> None:
            for _ in range(5):
                assert wrapper_add_warn_inf(1, 2) == 3

        with pytest.warns(FutureWarning) as record:
            _call_wrapper()
        assert len(record) == 5, f"Expected 5 warnings for num_warns=-1, got {len(record)}"

    # ---------- num_warns=2 (warn N times) ----------

    def test_wrapper_form_num_warns_limited(self) -> None:
        """Wrapper form with num_warns=2 warns only the first 2 calls."""

        def _call_wrapper() -> None:
            for _ in range(5):
                assert wrapper_add_warn_2(1, 2) == 3

        with pytest.warns(FutureWarning) as record:
            _call_wrapper()
        assert len(record) == 2, f"Expected 2 warnings for num_warns=2, got {len(record)}"

    # ---------- stream=None (silent mode) ----------

    def test_wrapper_form_stream_none(self) -> None:
        """Wrapper form with stream=None forwards without emitting any warning."""
        with no_warning_call(FutureWarning):
            result = wrapper_add_silent(3, 4)
        assert result == 7, f"Expected base_add(3, 4) == 7, got {result}"

    # ---------- template_mgs (custom warning text) ----------

    def test_wrapper_form_custom_template(self) -> None:
        """Wrapper form with template_mgs emits the custom-formatted warning."""
        with pytest.warns(
            FutureWarning,
            match=r"v0\.5: `original_add` is old, use `base_add`",
        ):
            result = wrapper_add_custom_msg(2, 3)
        assert result == 5, f"Expected base_add(2, 3) == 5, got {result}"

    # ---------- args_extra (inject extra args) ----------

    def test_wrapper_form_args_extra(self) -> None:
        """Wrapper form with args_extra overrides b=100 in the forwarded call."""
        with pytest.warns(FutureWarning):
            result = wrapper_add_extra(1, b=5)
        # args_extra={"b": 100} overrides user-provided b=5
        assert result == 101, f"Expected base_add(a=1, b=100) == 101 (args_extra overrides b), got {result}"

    def test_wrapper_form_args_extra_default(self) -> None:
        """Wrapper form with args_extra injects b=100 even when b is not passed."""
        with pytest.warns(FutureWarning):
            result = wrapper_add_extra(1)
        assert result == 101, f"Expected base_add(a=1, b=100) == 101, got {result}"

    # ---------- skip_if=True (static bypass) ----------

    def test_wrapper_form_skip_if_true(self) -> None:
        """Wrapper form with skip_if=True executes source body without warning or forwarding."""
        with no_warning_call(FutureWarning):
            result = wrapper_add_skip_true(3, 4)
        # skip_if=True means source body executes — original_add returns void(3, 4) = None
        assert result is None, f"Expected None from source body (void), got {result}"

    # ---------- skip_if=callable (dynamic bypass) ----------

    def test_wrapper_form_skip_if_callable(self) -> None:
        """Wrapper form with skip_if=lambda: True bypasses deprecation at runtime."""
        with no_warning_call(FutureWarning):
            result = wrapper_add_skip_func(5, 6)
        assert result is None, f"Expected None from source body (void), got {result}"

    # ---------- target=None (warn-only, no forwarding) ----------

    def test_wrapper_form_target_none_warns(self) -> None:
        """Wrapper form with target=None emits warning but executes the original body."""
        with pytest.warns(
            FutureWarning,
            match="The `original_warn_only` was deprecated since v0.5. It will be removed in v1.0.",
        ):
            result = wrapper_warn_only(3, 4)
        assert result == 7, f"Expected original body to execute and return 3+4==7, got {result}"

    def test_wrapper_form_target_none_attribute(self) -> None:
        """Wrapper form with target=None records target=None in __deprecated__."""
        info: DeprecationInfo = getattr(wrapper_warn_only, "__deprecated__")
        assert info.target is None, f"Expected __deprecated__.target is None, got {info.target!r}"

    # ---------- target=True (self-deprecation / arg rename) ----------

    def test_wrapper_form_self_deprecation_new_arg(self) -> None:
        """Wrapper form with target=True passes through when new arg is used (no warning)."""
        with no_warning_call():
            result = wrapper_self_depr(2.0, new_exp=3.0)
        assert result == 8.0, f"Expected 2.0**3.0 == 8.0, got {result}"

    def test_wrapper_form_self_deprecation_old_arg(self) -> None:
        """Wrapper form with target=True warns and remaps old_exp -> new_exp."""
        with pytest.warns(
            FutureWarning,
            match=r"`original_self_rename` uses deprecated arguments: `old_exp` -> `new_exp`",
        ):
            result = wrapper_self_depr(2.0, old_exp=3.0)
        assert result == 8.0, f"Expected 2.0**3.0 == 8.0 after remapping old_exp->new_exp, got {result}"

    def test_wrapper_form_self_deprecation_attribute(self) -> None:
        """Wrapper form with target=True records target=True in __deprecated__."""
        info: DeprecationInfo = getattr(wrapper_self_depr, "__deprecated__")
        assert info.target is True, f"Expected __deprecated__.target is True, got {info.target!r}"
        assert info.args_mapping == {"old_exp": "new_exp"}, (
            f"Expected args_mapping == {{'old_exp': 'new_exp'}}, got {info.args_mapping!r}"
        )

    # ---------- update_docstring=True ----------

    def test_wrapper_form_update_docstring(self) -> None:
        """Wrapper form with update_docstring=True appends deprecation notice to docstring."""
        doc = wrapper_add_docstring.__doc__
        assert doc is not None, "Expected docstring to be set"
        assert ".. deprecated:: 0.5" in doc, f"Expected '.. deprecated:: 0.5' in docstring, got:\n{doc}"
        assert "Will be removed in 1.0." in doc, f"Expected 'Will be removed in 1.0.' in docstring, got:\n{doc}"
        assert "base_add" in doc, f"Expected target name 'base_add' in docstring, got:\n{doc}"
