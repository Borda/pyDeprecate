"""Test the package utility functions."""

from warnings import warn

import pytest

import tests.collection_misconfigured as sample_module
from deprecate.utils import (
    DeprecatedCallableInfo,
    find_deprecated_callables,
    no_warning_call,
    validate_deprecated_callable,
    validate_deprecation_chains,
)
from tests.collection_deprecate import depr_accuracy_target, depr_pow_self

# Removed redundant direct imports from tests.collection_misconfigured; use sample_module.<name> instead.


def raise_pow(base: float, coef: float) -> float:
    """Function that raises a warning for testing."""
    warn("warning you!", UserWarning)
    return base**coef


class TestWarningCall:
    """Test the no_warning_call utility."""

    def test_warning_raised(self) -> None:
        """Test that warnings are raised."""
        with pytest.raises(AssertionError, match="While catching all warnings, these were found:"), no_warning_call():
            assert raise_pow(3, 2) == 9

        with (
            pytest.raises(AssertionError, match="While catching `UserWarning` warnings, these were found:"),
            no_warning_call(UserWarning),
        ):
            assert raise_pow(3, 2) == 9

        with (
            pytest.raises(AssertionError, match='While catching `UserWarning` warnings with "you!", these were found:'),
            no_warning_call(UserWarning, match="you!"),
        ):
            assert raise_pow(3, 2) == 9

    def test_warning_others(self) -> None:
        """Test warnings for other categories."""
        with no_warning_call(UserWarning):
            assert pow(3, 2) == 9

        with no_warning_call(RuntimeWarning):
            assert raise_pow(3, 2) == 9

        with no_warning_call(UserWarning, match="no idea what"):
            assert raise_pow(3, 2) == 9


class TestValidateDeprecatedCallable:
    """Tests for validate_deprecated_callable()."""

    def test_valid_deprecation(self) -> None:
        """Test validate_deprecated_callable with a properly configured deprecated function."""
        result = validate_deprecated_callable(depr_pow_self)
        assert isinstance(result, DeprecatedCallableInfo)
        assert result.invalid_args == []
        assert result.empty_mapping is False
        assert result.identity_mapping == []
        assert result.self_reference is False
        assert result.no_effect is False

    def test_invalid_args(self) -> None:
        """Test validate_deprecated_callable detects invalid args_mapping keys."""
        result = validate_deprecated_callable(sample_module.invalid_args_deprecation)
        assert result.invalid_args == ["nonexistent_arg"]
        assert result.empty_mapping is False
        assert result.identity_mapping == []
        assert result.self_reference is False

    def test_empty_mapping(self) -> None:
        """Test validate_deprecated_callable detects empty args_mapping."""
        # Test with empty args_mapping (self-deprecation)
        result = validate_deprecated_callable(sample_module.empty_mapping_deprecation)
        assert result.invalid_args == []
        assert result.empty_mapping is True
        assert result.identity_mapping == []
        assert result.self_reference is False
        assert result.no_effect is True

        # Test with None args_mapping (self-deprecation)
        result = validate_deprecated_callable(sample_module.none_mapping_deprecation)
        assert result.invalid_args == []
        assert result.empty_mapping is True
        assert result.identity_mapping == []
        assert result.self_reference is False
        assert result.no_effect is True

    def test_single_identity_mapping(self) -> None:
        """Test validate_deprecated_callable detects single identity mapping."""
        result = validate_deprecated_callable(sample_module.identity_mapping_deprecation)
        assert result.invalid_args == []
        assert result.empty_mapping is False
        assert result.identity_mapping == ["arg1"]
        assert result.self_reference is False
        assert result.no_effect is True

    def test_all_identity_mappings(self) -> None:
        """Test validate_deprecated_callable detects all identity mappings."""
        result = validate_deprecated_callable(sample_module.all_identity_mapping_deprecation)
        assert result.identity_mapping == ["arg1", "arg2"]
        assert result.no_effect is True

    def test_partial_identity_mapping(self) -> None:
        """Test validate_deprecated_callable detects partial identity mapping."""
        result = validate_deprecated_callable(sample_module.partial_identity_mapping_deprecation)
        assert result.identity_mapping == ["arg1"]
        assert result.no_effect is False

    def test_self_reference(self) -> None:
        """Test validate_deprecated_callable detects self-referencing target."""
        # Self-reference - no effect
        result = validate_deprecated_callable(sample_module.self_referencing_deprecation)
        assert result.invalid_args == []
        assert result.empty_mapping is False
        assert result.identity_mapping == []
        assert result.self_reference is True
        assert result.no_effect is True

    def test_different_target(self) -> None:
        """Test validate_deprecated_callable with a different target function."""
        # Different target - has effect
        result = validate_deprecated_callable(depr_accuracy_target)
        assert result.invalid_args == []
        assert result.empty_mapping is False
        assert result.identity_mapping == []
        assert result.self_reference is False
        assert result.no_effect is False

    def test_no_deprecated_attr(self) -> None:
        """Test validate_deprecated_callable raises ValueError for non-deprecated functions."""

        def plain_function(x: int) -> int:
            return x

        with pytest.raises(ValueError, match="does not have a __deprecated__ attribute"):
            validate_deprecated_callable(plain_function)


class TestFindDeprecatedCallables:
    """Tests for find_deprecated_callables()."""

    def test_find_deprecated_callables(self) -> None:
        """Test find_deprecated_callables scans a module for deprecated functions."""
        results = find_deprecated_callables(sample_module, recursive=False)

        # Should find deprecated functions
        assert len(results) > 0

        # All results should be DeprecatedCallableInfo dataclasses with merged fields
        for r in results:
            assert isinstance(r, DeprecatedCallableInfo)

        # Check that known deprecated functions are found
        func_names = [r.function for r in results]
        assert "invalid_args_deprecation" in func_names
        assert "empty_mapping_deprecation" in func_names
        assert "identity_mapping_deprecation" in func_names

    def test_detects_no_effect(self) -> None:
        """Test find_deprecated_callables correctly identifies zero-impact wrappers."""
        results = find_deprecated_callables(sample_module, recursive=False)

        # Group by function name for easier testing
        by_name = {r.function: r for r in results}

        # Degenerated deprecations should have no effect or detect issues
        if "empty_mapping_deprecation" in by_name:
            assert by_name["empty_mapping_deprecation"].empty_mapping is True

        if "identity_mapping_deprecation" in by_name:
            assert "arg1" in by_name["identity_mapping_deprecation"].identity_mapping

    def test_with_string_module(self) -> None:
        """Test find_deprecated_callables accepts string module path."""
        results = find_deprecated_callables("tests.collection_deprecate", recursive=False)

        # Should find deprecated functions
        assert len(results) > 0

        # All results should be DeprecatedCallableInfo dataclasses with merged fields
        for r in results:
            assert isinstance(r, DeprecatedCallableInfo)
            assert hasattr(r, "module")
            assert hasattr(r, "function")
            assert hasattr(r, "deprecated_info")
            assert hasattr(r, "invalid_args")
            assert hasattr(r, "empty_mapping")
            assert hasattr(r, "identity_mapping")
            assert hasattr(r, "no_effect")

    def test_report_grouping(self) -> None:
        """Test that results can be grouped by issue type for reporting."""
        results = find_deprecated_callables(sample_module, recursive=False)

        # Group by issue type - now directly on DeprecatedCallableInfo
        invalid_args = [r for r in results if r.invalid_args]
        empty_mappings = [r for r in results if r.empty_mapping]
        identity_mappings = [r for r in results if r.identity_mapping]
        no_effect = [r for r in results if r.no_effect]

        # Should be able to group results
        assert isinstance(invalid_args, list)
        assert isinstance(empty_mappings, list)
        assert isinstance(identity_mappings, list)
        assert isinstance(no_effect, list)

        # We should find some degenerated deprecations
        assert len(empty_mappings) > 0 or len(identity_mappings) > 0 or len(invalid_args) > 0


# =============================================================================
# Tests for validate_deprecation_chains()
# =============================================================================


def test_validate_deprecation_chains_detects_call_with_target() -> None:
    """Test validate_deprecation_chains detects deprecated function calling another with target."""
    from tests.collection_chains import caller_calls_deprecated

    with pytest.warns(UserWarning, match="'caller_calls_deprecated' calls deprecated function 'deprecated_callee'"):
        validate_deprecation_chains(caller_calls_deprecated)


def test_validate_deprecation_chains_no_warning_for_no_target() -> None:
    """Test validate_deprecation_chains doesn't warn when callee has no target."""
    from tests.collection_chains import caller_calls_deprecated_no_target

    # Should not warn because deprecated_callee_no_target has no target
    with no_warning_call(UserWarning):
        validate_deprecation_chains(caller_calls_deprecated_no_target)


def test_validate_deprecation_chains_detects_deprecated_args() -> None:
    """Test validate_deprecation_chains detects passing deprecated arguments."""
    from tests.collection_chains import caller_passes_deprecated_arg

    with pytest.warns(UserWarning, match="'caller_passes_deprecated_arg' passes deprecated argument 'old_arg'"):
        validate_deprecation_chains(caller_passes_deprecated_arg)


def test_validate_deprecation_chains_non_deprecated_caller() -> None:
    """Test validate_deprecation_chains works with non-deprecated functions."""
    from tests.collection_chains import non_deprecated_caller

    with pytest.warns(UserWarning, match="'non_deprecated_caller' calls deprecated function 'deprecated_callee'"):
        validate_deprecation_chains(non_deprecated_caller)


def test_validate_deprecation_chains_no_warnings_clean() -> None:
    """Test validate_deprecation_chains doesn't warn for clean code."""
    from tests.collection_chains import caller_no_deprecated_calls

    # Should not warn because it doesn't call deprecated functions
    with no_warning_call(UserWarning):
        validate_deprecation_chains(caller_no_deprecated_calls)


def test_validate_deprecation_chains_builtin_function() -> None:
    """Test validate_deprecation_chains handles built-in functions gracefully."""
    # Should issue a warning for built-in functions (cannot get source) and return None
    with pytest.warns(UserWarning, match="validate_deprecation_chains: Cannot get source code"):
        validate_deprecation_chains(len)
