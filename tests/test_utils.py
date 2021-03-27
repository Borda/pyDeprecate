from warnings import warn

import pytest

from deprecate.utils import no_warning_call


def raise_pow(base, coef):
    warn("warning you!", UserWarning)
    return base**coef


def test_warning_raised():

    with pytest.raises(AssertionError, match='While catching all warnings, these were found:'):
        with no_warning_call():
            assert raise_pow(3, 2) == 9

    with pytest.raises(AssertionError, match='While catching `UserWarning` warnings, these were found:'):
        with no_warning_call(UserWarning):
            assert raise_pow(3, 2) == 9

    with pytest.raises(AssertionError, match='While catching `UserWarning` warnings with "you!", these were found:'):
        with no_warning_call(UserWarning, match="you!"):
            assert raise_pow(3, 2) == 9


def test_warning_others():

    with no_warning_call(UserWarning):
        assert pow(3, 2) == 9

    with no_warning_call(RuntimeWarning):
        assert raise_pow(3, 2) == 9

    with no_warning_call(UserWarning, match="no idea what"):
        assert raise_pow(3, 2) == 9
