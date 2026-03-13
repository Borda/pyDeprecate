"""Test the utility helper functions."""

from collections.abc import Callable

import pytest

from deprecate import assert_no_warnings
from tests.collection_targets import raise_pow, raise_pow_future


class TestWarningCall:
    """Test the assert_no_warnings utility."""

    @pytest.mark.parametrize(
        ("ctx_args", "ctx_kwargs", "expected_msg", "call_fn"),
        [
            pytest.param((), {}, "While catching all warnings, these were found:", raise_pow, id="all-warnings"),
            pytest.param(
                (UserWarning,),
                {},
                r"While catching `UserWarning` warnings, these were found:",
                raise_pow,
                id="UserWarning",
            ),
            pytest.param(
                (UserWarning,),
                {"match": "you!"},
                r'While catching `UserWarning` warnings with "you!", these were found:',
                raise_pow,
                id="UserWarning-with-match",
            ),
            pytest.param(
                (FutureWarning,),
                {},
                r"While catching `FutureWarning` warnings, these were found:",
                raise_pow_future,
                id="FutureWarning",
            ),
        ],
    )
    def test_warning_raised(
        self, ctx_args: tuple, ctx_kwargs: dict, expected_msg: str, call_fn: Callable[..., float]
    ) -> None:
        """assert_no_warnings raises AssertionError with a descriptive message when a warning IS raised."""
        with pytest.raises(AssertionError, match=expected_msg), assert_no_warnings(*ctx_args, **ctx_kwargs):
            assert call_fn(3, 2) == 9

    @pytest.mark.parametrize(
        ("ctx_args", "ctx_kwargs", "call_fn"),
        [
            pytest.param((UserWarning,), {}, pow, id="UserWarning-clean"),
            pytest.param((RuntimeWarning,), {}, raise_pow, id="RuntimeWarning-different-category"),
            pytest.param((UserWarning,), {"match": "no idea what"}, raise_pow, id="UserWarning-mismatch"),
            pytest.param((FutureWarning,), {}, raise_pow, id="FutureWarning-different-category"),
            pytest.param((FutureWarning,), {"match": "no idea what"}, raise_pow_future, id="FutureWarning-mismatch"),
        ],
    )
    def test_warning_others(self, ctx_args: tuple, ctx_kwargs: dict, call_fn: Callable[..., int]) -> None:
        """assert_no_warnings does not raise when the specific warning category or pattern is not triggered."""
        with assert_no_warnings(*ctx_args, **ctx_kwargs):
            assert call_fn(3, 2) == 9
