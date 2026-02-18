"""Test the utility helper functions."""

from warnings import warn

import pytest

from deprecate.utils import no_warning_call


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
