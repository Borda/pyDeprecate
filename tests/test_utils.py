"""Test the package utility."""

from warnings import warn

import pytest

from deprecate.utils import find_deprecated_callables, no_warning_call, validate_deprecated_callable


def raise_pow(base: float, coef: float) -> float:
    warn("warning you!", UserWarning)
    return base**coef


def test_warning_raised() -> None:
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


def test_warning_others() -> None:
    with no_warning_call(UserWarning):
        assert pow(3, 2) == 9

    with no_warning_call(RuntimeWarning):
        assert raise_pow(3, 2) == 9

    with no_warning_call(UserWarning, match="no idea what"):
        assert raise_pow(3, 2) == 9


# =============================================================================
# Tests for validate_deprecated_callable()
# =============================================================================


def test_validate_deprecated_callable_valid_deprecation() -> None:
    """Test validate_deprecated_callable with a properly configured deprecated function."""
    from tests.collection_degenerate import valid_self_deprecation

    result = validate_deprecated_callable(valid_self_deprecation, {"old_arg": "new_arg"})
    assert result["invalid_args"] == []
    assert result["empty_mapping"] is False
    assert result["identity_mapping"] == []
    assert result["self_reference"] is False
    assert result["no_effect"] is False


def test_validate_deprecated_callable_invalid_args() -> None:
    """Test validate_deprecated_callable detects invalid args_mapping keys."""
    from tests.collection_degenerate import invalid_args_deprecation

    result = validate_deprecated_callable(invalid_args_deprecation, {"nonexistent_arg": "new_arg"}, target=True)
    assert result["invalid_args"] == ["nonexistent_arg"]
    assert result["empty_mapping"] is False
    assert result["identity_mapping"] == []
    assert result["self_reference"] is False


def test_validate_deprecated_callable_empty_mapping() -> None:
    """Test validate_deprecated_callable detects empty args_mapping."""
    from tests.collection_degenerate import empty_mapping_deprecation, none_mapping_deprecation

    # Test with empty args_mapping (self-deprecation)
    result = validate_deprecated_callable(empty_mapping_deprecation, {}, target=True)
    assert result["invalid_args"] == []
    assert result["empty_mapping"] is True
    assert result["identity_mapping"] == []
    assert result["self_reference"] is False
    assert result["no_effect"] is True

    # Test with None args_mapping (self-deprecation)
    result = validate_deprecated_callable(none_mapping_deprecation, None, target=True)
    assert result["invalid_args"] == []
    assert result["empty_mapping"] is True
    assert result["identity_mapping"] == []
    assert result["self_reference"] is False
    assert result["no_effect"] is True


def test_validate_deprecated_callable_identity_mapping() -> None:
    """Test validate_deprecated_callable detects identity mappings."""
    from tests.collection_degenerate import (
        all_identity_mapping_deprecation,
        identity_mapping_deprecation,
        partial_identity_mapping_deprecation,
    )

    # Single identity mapping - no effect (all mappings are identity)
    # target=True because the function uses self-deprecation
    result = validate_deprecated_callable(identity_mapping_deprecation, {"arg1": "arg1"}, target=True)
    assert result["invalid_args"] == []
    assert result["empty_mapping"] is False
    assert result["identity_mapping"] == ["arg1"]
    assert result["self_reference"] is False
    assert result["no_effect"] is True

    # All identity mappings - no effect
    result = validate_deprecated_callable(
        all_identity_mapping_deprecation, {"arg1": "arg1", "arg2": "arg2"}, target=True
    )
    assert result["identity_mapping"] == ["arg1", "arg2"]
    assert result["no_effect"] is True

    # Partial identity - still has effect (one valid mapping)
    result = validate_deprecated_callable(
        partial_identity_mapping_deprecation, {"arg1": "arg1", "arg2": "new_arg2"}, target=True
    )
    assert result["identity_mapping"] == ["arg1"]
    assert result["no_effect"] is False


def test_validate_deprecated_callable_self_reference() -> None:
    """Test validate_deprecated_callable detects self-referencing target."""
    from tests.collection_degenerate import valid_self_deprecation

    # Self-reference - no effect
    result = validate_deprecated_callable(valid_self_deprecation, {"old_arg": "new_arg"}, target=valid_self_deprecation)
    assert result["invalid_args"] == []
    assert result["empty_mapping"] is False
    assert result["identity_mapping"] == []
    assert result["self_reference"] is True
    assert result["no_effect"] is True


def test_validate_deprecated_callable_different_target() -> None:
    """Test validate_deprecated_callable with a different target function."""
    from tests.collection_degenerate import _self_ref_target, valid_self_deprecation

    # Different target - has effect
    result = validate_deprecated_callable(valid_self_deprecation, {"old_arg": "new_arg"}, target=_self_ref_target)
    assert result["invalid_args"] == []
    assert result["empty_mapping"] is False
    assert result["identity_mapping"] == []
    assert result["self_reference"] is False
    assert result["no_effect"] is False


# =============================================================================
# Tests for find_deprecated_callables()
# =============================================================================


def test_find_deprecated_callables() -> None:
    """Test find_deprecated_callables scans a module for deprecated functions."""
    import tests.collection_degenerate as test_module

    results = find_deprecated_callables(test_module, recursive=False)

    # Should find deprecated functions
    assert len(results) > 0

    # All results should have required keys
    for r in results:
        assert "module" in r
        assert "function" in r
        assert "deprecated_info" in r
        assert "validation" in r
        assert "has_effect" in r

    # Check that known deprecated functions are found
    func_names = [r["function"] for r in results]
    assert "valid_deprecation" in func_names
    assert "invalid_args_deprecation" in func_names
    assert "empty_mapping_deprecation" in func_names
    assert "identity_mapping_deprecation" in func_names


def test_find_deprecated_callables_detects_no_effect() -> None:
    """Test find_deprecated_callables correctly identifies zero-impact wrappers."""
    import tests.collection_degenerate as test_module

    results = find_deprecated_callables(test_module, recursive=False)

    # Group by function name for easier testing
    by_name = {r["function"]: r for r in results}

    # Valid deprecations should have effect
    if "valid_deprecation" in by_name:
        assert by_name["valid_deprecation"]["has_effect"] is True

    # Degenerated deprecations should have no effect or detect issues
    if "empty_mapping_deprecation" in by_name:
        assert by_name["empty_mapping_deprecation"]["validation"]["empty_mapping"] is True

    if "identity_mapping_deprecation" in by_name:
        assert "arg1" in by_name["identity_mapping_deprecation"]["validation"]["identity_mapping"]


def test_find_deprecated_callables_with_string_module() -> None:
    """Test find_deprecated_callables accepts string module path."""
    results = find_deprecated_callables("tests.collection_degenerate", recursive=False)

    # Should find deprecated functions
    assert len(results) > 0

    # All results should have required keys
    for r in results:
        assert "module" in r
        assert "function" in r
        assert "deprecated_info" in r
        assert "validation" in r
        assert "has_effect" in r


def test_find_deprecated_callables_report_grouping() -> None:
    """Test that results can be grouped by issue type for reporting."""
    import tests.collection_degenerate as test_module

    results = find_deprecated_callables(test_module, recursive=False)

    # Group by issue type
    invalid_args = [r for r in results if r["validation"]["invalid_args"]]
    empty_mappings = [r for r in results if r["validation"]["empty_mapping"]]
    identity_mappings = [r for r in results if r["validation"]["identity_mapping"]]
    no_effect = [r for r in results if not r["has_effect"]]

    # Should be able to group results
    assert isinstance(invalid_args, list)
    assert isinstance(empty_mappings, list)
    assert isinstance(identity_mappings, list)
    assert isinstance(no_effect, list)

    # We should find some degenerated deprecations
    assert len(empty_mappings) > 0 or len(identity_mappings) > 0 or len(invalid_args) > 0
