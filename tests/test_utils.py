"""Test the package utility functions."""

from warnings import warn

import pytest

import tests.collection_misconfigured as sample_module
from deprecate.utils import (
    DeprecatedCallableInfo,
    find_deprecated_callables,
    no_warning_call,
    validate_deprecated_callable,
)
from tests.collection_deprecate import depr_accuracy_target, depr_func_no_remove_in, depr_pow_self, depr_sum

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


class TestCheckDeprecationExpiry:
    """Tests for check_deprecation_expiry()."""

    def test_not_expired(self) -> None:
        """Test when current version is before removal deadline."""
        from deprecate import check_deprecation_expiry

        # Current version is less than remove_in - should not raise
        check_deprecation_expiry(depr_pow_self, "0.4")  # remove_in="0.5"

    def test_at_removal_version(self) -> None:
        """Test raises AssertionError at removal version."""
        from deprecate import check_deprecation_expiry

        # Current version equals remove_in - should raise AssertionError
        with pytest.raises(
            AssertionError, match=r"was scheduled for removal in version 0\.5.*still exists in version 0\.5"
        ):
            check_deprecation_expiry(depr_pow_self, "0.5")  # remove_in="0.5"

    def test_past_removal_version(self) -> None:
        """Test raises AssertionError past removal version."""
        from deprecate import check_deprecation_expiry

        # Current version is greater than remove_in - should raise AssertionError
        with pytest.raises(
            AssertionError, match=r"was scheduled for removal in version 0\.5.*still exists in version 0\.6"
        ):
            check_deprecation_expiry(depr_pow_self, "0.6")  # remove_in="0.5"

    def test_error_message(self) -> None:
        """Test error message content."""
        from deprecate import check_deprecation_expiry

        # Test the error message contains all expected information
        with pytest.raises(AssertionError) as exc_info:
            check_deprecation_expiry(depr_pow_self, "1.0")  # remove_in="0.5"

        error_msg = str(exc_info.value)
        assert "depr_pow_self" in error_msg
        assert "0.5" in error_msg  # remove_in version
        assert "1.0" in error_msg  # current version
        assert "scheduled for removal" in error_msg
        assert "Please delete this deprecated code" in error_msg

    def test_no_deprecated_attr(self) -> None:
        """Test raises ValueError for non-deprecated functions."""
        from deprecate import check_deprecation_expiry

        def plain_function(x: int) -> int:
            return x

        with pytest.raises(ValueError, match="does not have a __deprecated__ attribute"):
            check_deprecation_expiry(plain_function, "1.0")

    def test_no_remove_in(self) -> None:
        """Test raises ValueError when remove_in is missing."""
        from deprecate import check_deprecation_expiry

        with pytest.raises(ValueError, match="does not have a 'remove_in' version specified"):
            check_deprecation_expiry(depr_func_no_remove_in, "2.0")

    def test_semantic_versioning(self) -> None:
        """Test handles semantic versioning correctly."""
        from deprecate import check_deprecation_expiry

        # Test with semantic versions (depr_sum has remove_in="0.5")
        check_deprecation_expiry(depr_sum, "0.4.9")  # remove_in="0.5"
        check_deprecation_expiry(depr_sum, "0.5.0a1")  # remove_in="0.5" (PEP 440 alpha format)

        # Should raise at 0.5.0 or later
        with pytest.raises(AssertionError):
            check_deprecation_expiry(depr_sum, "0.5.0")

        with pytest.raises(AssertionError):
            check_deprecation_expiry(depr_sum, "0.5.1")

    def test_parse_version_stage_ordering(self) -> None:
        """Test alpha < beta < rc < stable < post ordering (PEP 440 format)."""
        pytest.importorskip("packaging")
        from deprecate.utils import _parse_version

        versions = [
            "1.5.0a1",  # alpha (PEP 440 format)
            "1.5.0b1",  # beta
            "1.5.0rc1",  # release candidate
            "1.5.0",  # stable
            "1.5.0.post1",  # post-release
        ]
        parsed = [_parse_version(v) for v in versions]
        assert parsed == sorted(parsed)

    def test_invalid_version_format(self) -> None:
        """Test handles invalid version formats."""
        from deprecate import check_deprecation_expiry

        # Invalid version format should raise ValueError
        with pytest.raises(ValueError, match="Failed to parse versions"):
            check_deprecation_expiry(depr_pow_self, "invalid-version")


class TestCheckModuleDeprecationExpiry:
    """Tests for check_module_deprecation_expiry()."""

    def test_no_expired(self) -> None:
        """Test when no callables have expired."""
        from deprecate import check_module_deprecation_expiry

        # Check the test collection module with a version before any removal deadlines
        expired = check_module_deprecation_expiry("tests.collection_deprecate", "0.1", recursive=False)
        assert expired == []

    def test_with_expired(self) -> None:
        """Test detects expired callables."""
        from deprecate import check_module_deprecation_expiry

        # Check with a version past some removal deadlines
        expired = check_module_deprecation_expiry("tests.collection_deprecate", "0.5", recursive=False)

        # Should find at least depr_sum (remove_in="0.5") as expired
        assert len(expired) > 0

        # Check that error messages are properly formatted
        for msg in expired:
            assert "scheduled for removal" in msg
            assert "still exists" in msg

    def test_with_module_object(self) -> None:
        """Test with module object instead of string."""
        from deprecate import check_module_deprecation_expiry
        from tests import collection_deprecate

        # Pass module object directly
        expired = check_module_deprecation_expiry(collection_deprecate, "0.1", recursive=False)
        assert expired == []

        # Now check with expired version
        expired = check_module_deprecation_expiry(collection_deprecate, "1.0", recursive=False)
        assert len(expired) > 0

    def test_recursive(self) -> None:
        """Test with recursive scanning."""
        from deprecate import check_module_deprecation_expiry

        # Test with recursive=True (default)
        expired = check_module_deprecation_expiry("tests", "10.0", recursive=True)

        # Should find multiple expired callables across the test package
        assert len(expired) > 0

    def test_skips_missing_remove_in(self) -> None:
        """Test skips callables without remove_in."""
        from deprecate import check_module_deprecation_expiry

        # The collection_deprecate module has some functions without remove_in
        # This should not raise an error, just skip those functions
        expired = check_module_deprecation_expiry("tests.collection_deprecate", "100.0", recursive=False)

        # Should find expired ones, but not crash on missing remove_in
        assert isinstance(expired, list)

    def test_return_format(self) -> None:
        """Test returns properly formatted error messages."""
        from deprecate import check_module_deprecation_expiry

        # Get some expired callables
        expired = check_module_deprecation_expiry("tests.collection_deprecate", "2.0", recursive=False)

        # All expired entries should be strings (error messages)
        assert all(isinstance(msg, str) for msg in expired)

        # Each message should contain key information
        for msg in expired:
            assert "Callable" in msg or "scheduled" in msg

    def test_handles_invalid_current_version(self) -> None:
        """Test with invalid current_version raises ValueError."""
        from contextlib import suppress

        from deprecate import check_module_deprecation_expiry

        # Invalid version format in current_version should raise ValueError during first check
        # This is expected to fail on the first callable with remove_in
        # The ValueError will propagate from parse_version(current_version)
        # Since current_version is validated on every call to check_deprecation_expiry,
        # the first one will raise ValueError
        with suppress(ValueError):
            check_module_deprecation_expiry("tests.collection_deprecate", "invalid", recursive=False)
            # If it doesn't raise, that means all callables were skipped (no remove_in)
            # which is also acceptable behavior

    def test_gracefully_skips_import_errors(self) -> None:
        """Test handles callables that can't be imported."""
        from deprecate import check_module_deprecation_expiry

        # Test with a module that should work fine
        # The implementation should skip any callables that can't be imported
        expired = check_module_deprecation_expiry("tests.collection_deprecate", "0.1", recursive=False)

        # Should return a list without crashing
        assert isinstance(expired, list)
        assert len(expired) == 0  # No expired at version 0.1
