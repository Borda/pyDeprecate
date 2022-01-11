"""Copyright (C) 2020-2021 Jiri Borovec <...>"""
import pytest

from deprecate.utils import no_warning_call
from tests.collection_deprecate import (
    depr_accuracy_extra,
    depr_accuracy_map,
    depr_accuracy_skip,
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
)


def test_deprecated_func_warn_only() -> None:
    with pytest.warns(
        FutureWarning, match="The `depr_sum_warn_only` was deprecated since v0.2. It will be removed in v0.3."
    ):
        assert depr_sum_warn_only(2) is None


def test_deprecated_func_arguments() -> None:
    """Test deprecation function arguments."""
    with no_warning_call():
        assert depr_pow_self(2, new_coef=3) == 8

    with pytest.warns(
        FutureWarning,
        match="The `depr_pow_self` uses deprecated arguments: `coef` -> `new_coef`."
        " They were deprecated since v0.1 and will be removed in v0.5.",
    ):
        assert depr_pow_self(2, 3) == 8

    with pytest.warns(FutureWarning, match="The `depr_pow_self_double` uses depr. args: `c1` -> `nc1`."):
        assert depr_pow_self_double(2, c1=3) == 32

    with no_warning_call():
        assert depr_pow_self_double(2, c1=3) == 32

    with pytest.warns(FutureWarning, match="The `depr_pow_self_double` uses depr. args: `c2` -> `nc2`."):
        assert depr_pow_self_double(2, c2=2) == 8

    with no_warning_call():
        assert depr_pow_self_double(2, c1=3, c2=4) == 128

    # testing that preferable use the new arguments
    with no_warning_call():
        assert depr_pow_self_double(2, c1=3, c2=4, nc1=1, nc2=2) == 8

    with no_warning_call():
        assert depr_pow_self_double(2, c1=3, c2=4, nc1=1) == 32


def test_deprecated_func_chain() -> None:
    """Test chaining deprecation wrappers."""
    with pytest.warns(FutureWarning) as warns:
        assert depr_pow_self_twice(2, 3) == 8
    assert len(warns) == 2

    with no_warning_call():
        assert depr_pow_self_twice(2, c1=3) == 8


def test_deprecated_func_default() -> None:
    """Testing some base/default configurations."""
    with pytest.warns(
        FutureWarning,
        match="The `depr_sum` was deprecated since v0.1 in favor of `tests.collection_targets.base_sum_kwargs`."
        " It will be removed in v0.5.",
    ):
        assert depr_sum(2) == 7

    # check that the warning is raised only once per function
    with no_warning_call(FutureWarning):
        assert depr_sum(3) == 8

    # and does not affect other functions
    with pytest.warns(
        FutureWarning,
        match="The `depr_pow_mix` was deprecated since v0.1 in favor of `tests.collection_targets.base_pow_args`."
        " It will be removed in v0.5.",
    ):
        assert depr_pow_mix(2, 1) == 2


def test_deprecated_func_stream_calls() -> None:
    # check that the warning is raised only once per function
    with no_warning_call(FutureWarning):
        assert depr_sum_no_stream(3) == 8

    # check that the warning is raised only once per function
    with pytest.warns(FutureWarning) as record:
        for _ in range(5):
            assert depr_sum_calls_2(3) == 8
    assert len(record) == 2

    # check that the warning is raised only once per function
    with pytest.warns(FutureWarning) as record:
        for _ in range(5):
            assert depr_sum_calls_inf(3) == 8
    assert len(record) == 5

    with pytest.warns(FutureWarning, match="v0.1: `depr_sum_msg` was deprecated, use `base_sum_kwargs`"):
        assert depr_sum_msg(3) == 8


def test_deprecated_func_incomplete() -> None:
    # missing required argument
    with pytest.raises(TypeError, match="missing 1 required positional argument: 'b'"):
        depr_pow_args(2)

    # missing argument in target
    with pytest.raises(
        TypeError, match=r"Failed mapping of `depr_pow_wrong`, arguments missing in target source: \['c'\]"
    ):
        depr_pow_wrong(2)

    # check that the warning is raised only once per function
    with no_warning_call(FutureWarning):
        assert depr_pow_args(2, 1) == 2

    # reset the warning
    depr_pow_args._warned = False
    # does not affect other functions
    with pytest.warns(FutureWarning, match="`depr_pow_args` >> `base_pow_args` in v1.0 rm v1.3."):
        assert depr_pow_args(b=2, a=1) == 1


def test_deprecated_func_skip_if() -> None:
    """Test conditional wrapper skip."""
    with no_warning_call():
        assert depr_pow_skip_if_true(2, c1=2) == 2

    with no_warning_call():
        assert depr_pow_skip_if_func(2, c1=2) == 2

    with pytest.warns(FutureWarning, match="Depr: v0.1 rm v0.2 for args: `c1` -> `nc1`."):
        assert depr_pow_skip_if_true_false(2, c1=2) == 0.5

    with pytest.warns(FutureWarning, match="Depr: v0.1 rm v0.2 for args: `c1` -> `nc1`."):
        assert depr_pow_skip_if_false_true(2, c1=2) == 0.5

    with pytest.raises(TypeError, match="User function `shall_skip` shall return bool, but got: <class 'int'>"):
        assert depr_pow_skip_if_func_int(2, c1=2)


def test_deprecated_func_mapping() -> None:
    """Test mapping to external functions."""
    with pytest.warns(FutureWarning):
        assert depr_accuracy_map([1, 0, 1, 2]) == 0.5

    with pytest.warns(FutureWarning):
        assert depr_accuracy_skip([1, 0, 1, 2]) == 0.5

    with pytest.warns(FutureWarning):
        assert depr_accuracy_extra([1, 0, 1, 2]) == 0.75
