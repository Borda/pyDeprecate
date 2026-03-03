"""Test the utility helper functions."""

from collections.abc import Callable

import pytest

from deprecate.utils import no_warning_call
from tests.collection_targets import raise_pow


class TestWarningCall:
    """Test the no_warning_call utility."""

    @pytest.mark.parametrize(
        ("ctx_args", "ctx_kwargs", "expected_msg"),
        [
            ((), {}, "While catching all warnings, these were found:"),
            ((UserWarning,), {}, r"While catching `UserWarning` warnings, these were found:"),
            ((UserWarning,), {"match": "you!"}, r'While catching `UserWarning` warnings with "you!", these were found:'),
        ],
        ids=["all-warnings", "UserWarning", "UserWarning-with-match"],
    )
    def test_warning_raised(self, ctx_args: tuple, ctx_kwargs: dict, expected_msg: str) -> None:
        """no_warning_call raises AssertionError with a descriptive message when a warning IS raised."""
        with pytest.raises(AssertionError, match=expected_msg), no_warning_call(*ctx_args, **ctx_kwargs):
            assert raise_pow(3, 2) == 9

    @pytest.mark.parametrize(
        ("ctx_args", "ctx_kwargs", "call_fn"),
        [
            ((UserWarning,), {}, pow),
            ((RuntimeWarning,), {}, raise_pow),
            ((UserWarning,), {"match": "no idea what"}, raise_pow),
        ],
        ids=["UserWarning-clean", "RuntimeWarning-different-category", "UserWarning-mismatch"],
    )
    def test_warning_others(self, ctx_args: tuple, ctx_kwargs: dict, call_fn: Callable[..., int]) -> None:
        """no_warning_call does not raise when the specific warning category or pattern is not triggered."""
        with no_warning_call(*ctx_args, **ctx_kwargs):
            assert call_fn(3, 2) == 9
