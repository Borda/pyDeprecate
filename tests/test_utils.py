"""Test the package utility functions."""

import importlib.util
from warnings import warn

import pytest

import tests.collection_chains as chain_module
import tests.collection_misconfigured as sample_module
from deprecate import validate_deprecation_expiry
from deprecate.utils import (
    ChainType,
    DeprecatedCallableInfo,
    _check_deprecated_callable_expiry,
    _parse_version,
    find_deprecated_callables,
    no_warning_call,
    validate_deprecated_callable,
    validate_deprecation_chains,
)
from tests.collection_deprecate import depr_accuracy_target, depr_func_no_remove_in, depr_pow_self, depr_sum

# Removed redundant direct imports from tests.collection_misconfigured; use sample_module.<name> instead.

# Check if packaging is available for version comparison tests
_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None
_requires_packaging = pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging library")


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


class TestValidateDeprecationChains:
    """Tests for validate_deprecation_chains()."""

    def test_detects_chain(self) -> None:
        """Detects deprecated function whose target is itself deprecated.

        Examples:
            Developer has a deprecated wrapper that targets another deprecated function
            instead of the final implementation. The validation detects this chain.
        """
        issues = validate_deprecation_chains(chain_module, recursive=False)

        # Should find caller_sum_via_depr_sum (target=depr_sum which is deprecated)
        chain_funcs = [info.function for info in issues if info.function == "caller_sum_via_depr_sum"]
        assert len(chain_funcs) > 0
        info = next(i for i in issues if i.function == "caller_sum_via_depr_sum")
        assert info.chain_type is ChainType.TARGET

    def test_detects_chain_with_mapped_args(self) -> None:
        """Detects chain through a deprecated function that has arg mapping.

        Examples:
            Developer wraps a deprecated function that itself remaps arguments.
            The outer wrapper should skip the intermediate and target the final function.
        """
        issues = validate_deprecation_chains(chain_module, recursive=False)

        chain_funcs = [info.function for info in issues if info.function == "caller_acc_via_depr_map"]
        assert len(chain_funcs) > 0

    def test_detects_chain_with_composed_arg_mappings(self) -> None:
        """Detects chain where the outer wrapper also has its own args_mapping.

        Examples:
            Developer stacks two deprecated wrappers, each renaming arguments.
            Both hops must be collapsed: the outer wrapper should target the final
            function directly with the combined mapping.
        """
        issues = validate_deprecation_chains(chain_module, recursive=False)

        # caller_acc_comp_depr_map has target=depr_accuracy_map (deprecated)
        # AND its own args_mapping={"predictions": "preds", "labels": "truth"}
        chain_funcs = [info.function for info in issues if info.function == "caller_acc_comp_depr_map"]
        assert len(chain_funcs) > 0

        # The info must report ChainType.TARGET and expose the outer args_mapping
        info = next(i for i in issues if "caller_acc_comp_depr_map" in i.function)
        assert info.chain_type is ChainType.TARGET
        assert info.deprecated_info.get("args_mapping") == {"predictions": "preds", "labels": "truth"}

    def test_detects_stacked_self_deprecation(self) -> None:
        """Detects stacked target=True decorators whose arg mappings should be collapsed.

        Examples:
            Developer applies two ``@deprecated(True, args_mapping=...)`` decorators to
            the same function, each renaming a different argument. The two decorators
            should be merged into one with a combined args_mapping.
        """
        issues = validate_deprecation_chains(chain_module, recursive=False)

        stacked = [info for info in issues if "caller_stacked_args_map" in info.function]
        assert len(stacked) > 0
        assert stacked[0].chain_type is ChainType.STACKED

    def test_detects_stacked_via_callable_self_depr_target(self) -> None:
        """Detects STACKED chain when callable target is itself a self-deprecation with arg renaming.

        Examples:
            Developer's wrapper targets a deprecated function whose own target=True (self-renaming).
            The arg mappings from both layers compose: the caller's mapping feeds into the target's
            renaming, so both hops must be collapsed with a combined args_mapping.
        """
        issues = validate_deprecation_chains(chain_module, recursive=False)

        via_self = [info for info in issues if "caller_pow_via_self_depr" in info.function]
        assert len(via_self) > 0
        # Must be STACKED (not TARGET) — mappings compose through the self-deprecation layer
        assert via_self[0].chain_type is ChainType.STACKED

    def test_no_warning_for_clean_target(self) -> None:
        """Doesn't report when target is not deprecated.

        Examples:
            Developer has a deprecated wrapper pointing directly to a non-deprecated
            target. This is the correct pattern and should not trigger any warning.
        """
        issues = validate_deprecation_chains(chain_module, recursive=False)

        clean_funcs = [info.function for info in issues if "caller_sum_direct" in info.function]
        assert len(clean_funcs) == 0

    def test_returns_list_of_infos(self) -> None:
        """Returns a list of DeprecatedCallableInfo with chain_type set.

        Examples:
            Developer calls validate_deprecation_chains and receives structured
            DeprecatedCallableInfo objects for programmatic processing.
        """
        issues = validate_deprecation_chains(chain_module, recursive=False)
        assert isinstance(issues, list)
        assert len(issues) > 0
        for info in issues:
            assert isinstance(info, DeprecatedCallableInfo)
            assert info.chain_type is not None


@_requires_packaging
class TestCheckDeprecationExpiry:
    """Tests for _check_deprecated_callable_expiry()."""

    def test_not_expired(self) -> None:
        """Callable before removal deadline passes validation.

        Examples:
            Developer runs expiry check in CI for version 0.4 on a callable scheduled for
            removal in version 0.5. The check passes silently, allowing the release to proceed.
        """
        # Current version is less than remove_in - should not raise
        _check_deprecated_callable_expiry(depr_pow_self, "0.4")  # remove_in="0.5"

    def test_at_removal_version(self) -> None:
        """Callable at exact removal version triggers AssertionError.

        Examples:
            Developer releases version 0.5, which matches the remove_in deadline. CI runs
            expiry check and raises AssertionError, blocking the release until zombie code is deleted.
        """
        # Current version equals remove_in - should raise AssertionError
        with pytest.raises(
            AssertionError, match=r"was scheduled for removal in version 0\.5.*still exists in version 0\.5"
        ):
            _check_deprecated_callable_expiry(depr_pow_self, "0.5")  # remove_in="0.5"

    def test_past_removal_version(self) -> None:
        """Callable past removal deadline triggers AssertionError.

        Examples:
            Developer releases version 0.6 but forgot to delete deprecated code scheduled for
            removal in version 0.5. CI expiry check catches the zombie code and blocks the release.
        """
        # Current version is greater than remove_in - should raise AssertionError
        with pytest.raises(
            AssertionError, match=r"was scheduled for removal in version 0\.5.*still exists in version 0\.6"
        ):
            _check_deprecated_callable_expiry(depr_pow_self, "0.6")  # remove_in="0.5"

    def test_error_message(self) -> None:
        """Error message includes callable name, versions, and actionable guidance.

        Examples:
            Developer sees clear error message from CI identifying which deprecated callable
            needs deletion, showing both the removal deadline and current version for easy diagnosis.
        """
        # Test the error message contains all expected information
        with pytest.raises(AssertionError) as exc_info:
            _check_deprecated_callable_expiry(depr_pow_self, "1.0")  # remove_in="0.5"

        error_msg = str(exc_info.value)
        assert "depr_pow_self" in error_msg
        assert "0.5" in error_msg  # remove_in version
        assert "1.0" in error_msg  # current version
        assert "scheduled for removal" in error_msg
        assert "Please delete this deprecated code" in error_msg

    def test_no_deprecated_attr(self) -> None:
        """Non-deprecated callable raises ValueError immediately.

        Examples:
            Developer accidentally runs expiry check on a regular function without @deprecated
            decorator. Clear error message indicates the function is not decorated.
        """
        from tests.collection_targets import plain_function_target

        with pytest.raises(ValueError, match="does not have a __deprecated__ attribute"):
            _check_deprecated_callable_expiry(plain_function_target, "1.0")

    def test_no_remove_in(self) -> None:
        """Deprecated callable without remove_in raises ValueError.

        Examples:
            Developer runs expiry check on a warning-only deprecated function that has no
            removal deadline. Error message indicates remove_in parameter is required for enforcement.
        """
        with pytest.raises(ValueError, match="does not have a 'remove_in' version specified"):
            _check_deprecated_callable_expiry(depr_func_no_remove_in, "2.0")

    @pytest.mark.parametrize(
        "current_version",
        ["0.4.9", "0.5.0a1"],  # Pre-release versions before removal deadline
    )
    def test_semantic_versioning_before_deadline(self, current_version: str) -> None:
        """Version comparison follows PEP 440 semantic ordering for versions before deadline.

        Examples:
            Developer uses pre-release versions (0.5.0a1) and patch versions (0.4.9) in CI.
            Expiry check passes because these versions come before the 0.5 removal deadline.
        """
        # Test with semantic versions (depr_sum has remove_in="0.5")
        _check_deprecated_callable_expiry(depr_sum, current_version)

    @pytest.mark.parametrize(
        "current_version",
        ["0.5.0", "0.5.1"],  # Versions at or after removal deadline
    )
    def test_semantic_versioning_at_or_after_deadline(self, current_version: str) -> None:
        """Version comparison follows PEP 440 semantic ordering for versions at/after deadline.

        Examples:
            Developer reaches version 0.5.0 or 0.5.1 in their project. Expiry check raises
            AssertionError because these versions are at or past the 0.5 removal deadline.
        """
        # Should raise at 0.5.0 or later
        with pytest.raises(AssertionError):
            _check_deprecated_callable_expiry(depr_sum, current_version)

    def test_parse_version_stage_ordering(self) -> None:
        """Pre-release stages order correctly per PEP 440.

        Examples:
            Developer uses alpha, beta, RC, stable, and post-release versions in their project.
            Version parser correctly orders them so alpha < beta < rc < stable < post for accurate
            expiry checking across the release cycle.
        """
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
        """Malformed version string raises ValueError with clear message.

        Examples:
            Developer accidentally passes an invalid version string like "invalid-version" to
            expiry check. Clear error message indicates version parsing failed with PEP 440 requirements.
        """
        # Invalid version format should raise ValueError
        with pytest.raises(ValueError, match="Invalid current_version"):
            _check_deprecated_callable_expiry(depr_pow_self, "invalid-version")


@_requires_packaging
class TestCheckModuleDeprecationExpiry:
    """Tests for validate_deprecation_expiry()."""

    def test_no_expired(self) -> None:
        """Module scan before any removal deadlines returns empty list.

        Examples:
            Developer runs package-wide expiry check in CI for version 0.1 release. All
            deprecated callables have future removal deadlines, so CI passes cleanly.
        """
        # Check the test collection module with a version before any removal deadlines
        expired = validate_deprecation_expiry("tests.collection_deprecate", "0.1", recursive=False)
        assert expired == []

    def test_with_expired(self) -> None:
        """Module scan at removal deadline detects expired zombie code.

        Examples:
            Developer releases version 0.5 but forgets to delete some deprecated functions.
            CI package scan identifies all expired callables and blocks the release with clear error messages.
        """
        # Check with a version past some removal deadlines
        expired = validate_deprecation_expiry("tests.collection_deprecate", "0.5", recursive=False)

        # Should find at least depr_sum (remove_in="0.5") as expired
        assert len(expired) > 0

        # Check that error messages are properly formatted
        for msg in expired:
            assert "scheduled for removal" in msg
            assert "still exists" in msg

    def test_with_module_object(self) -> None:
        """Module object accepted instead of module path string.

        Examples:
            Developer prefers to import and pass module object directly rather than using
            string path. Both approaches work identically for expiry checking.
        """
        from tests import collection_deprecate

        # Pass module object directly
        expired = validate_deprecation_expiry(collection_deprecate, "0.1", recursive=False)
        assert expired == []

        # Now check with expired version
        expired = validate_deprecation_expiry(collection_deprecate, "1.0", recursive=False)
        assert len(expired) > 0

    def test_recursive(self) -> None:
        """Recursive scan checks entire package tree for expired code.

        Examples:
            Developer runs expiry check on root package with submodules. Recursive mode
            scans all subpackages and submodules, ensuring no zombie code hides in deep module hierarchy.
        """
        # Test with recursive=True (default)
        expired = validate_deprecation_expiry("tests", "10.0", recursive=True)

        # Should find multiple expired callables across the test package
        assert len(expired) > 0

    def test_skips_missing_remove_in(self) -> None:
        """Warning-only deprecations without remove_in are gracefully skipped.

        Examples:
            Developer has mix of removal-scheduled and warning-only deprecations. Package scan
            silently skips warning-only functions and only checks ones with removal deadlines.
        """
        # The collection_deprecate module has some functions without remove_in
        # This should not raise an error, just skip those functions
        expired = validate_deprecation_expiry("tests.collection_deprecate", "100.0", recursive=False)

        # Should find expired ones, but not crash on missing remove_in
        assert isinstance(expired, list)

    def test_return_format(self) -> None:
        """Returns list of error messages for all expired callables found.

        Examples:
            Developer wants to log all expired callables before failing CI. Return value is
            list of strings describing each expired callable, suitable for logging or aggregation.
        """
        # Get some expired callables
        expired = validate_deprecation_expiry("tests.collection_deprecate", "2.0", recursive=False)

        # All expired entries should be strings (error messages)
        assert all(isinstance(msg, str) for msg in expired)

        # Each message should contain key information
        for msg in expired:
            assert "Callable" in msg or "scheduled" in msg

    def test_handles_invalid_current_version(self) -> None:
        """Malformed current_version raises ValueError before scanning begins.

        Examples:
            Developer accidentally passes "invalid" as version string to package scan. Immediate
            fail-fast error prevents wasted time scanning when version parameter is wrong.
        """
        # Invalid version format in current_version should raise ValueError upfront
        # before any callable checking begins
        with pytest.raises(ValueError, match="Invalid current_version"):
            validate_deprecation_expiry("tests.collection_deprecate", "invalid", recursive=False)

    def test_gracefully_skips_import_errors(self) -> None:
        """Scan continues gracefully when individual callables fail to import.

        Examples:
            Developer has deprecated callables with optional dependencies or import errors.
            Package scan doesn't crash entirely, just skips problematic callables and continues.
        """
        # Test with a module that should work fine
        # The implementation should skip any callables that can't be imported
        expired = validate_deprecation_expiry("tests.collection_deprecate", "0.1", recursive=False)

        # Should return a list without crashing
        assert isinstance(expired, list)
        assert len(expired) == 0  # No expired at version 0.1

    def test_auto_detect_version(self) -> None:
        """Auto-detection extracts package version from installed metadata.

        Examples:
            Developer omits current_version parameter in CI for installed package. Function
            auto-detects version from importlib.metadata, simplifying CI configuration.
        """
        # Test with tests.collection_deprecate module (part of tests, not a real package)
        # Should try to auto-detect "tests" package version and fail gracefully
        # We expect ImportError because "tests" is not an installed package
        with pytest.raises(ImportError, match="Could not determine version"):
            validate_deprecation_expiry("tests.collection_deprecate", None, recursive=False)

    def test_auto_detect_version_with_module_object_without_name(self) -> None:
        """Auto-detection fails clearly when module object lacks __name__ attribute.

        Examples:
            Developer passes malformed or mock module object without __name__ attribute.
            Clear error message indicates auto-detection requires proper module objects.
        """

        # Create a mock module-like object without __name__
        class FakeModule:
            pass

        fake_mod = FakeModule()

        # Should raise ValueError when trying to auto-detect without __name__
        with pytest.raises(ValueError, match="Cannot auto-detect version.*__name__"):
            validate_deprecation_expiry(fake_mod, None, recursive=False)
