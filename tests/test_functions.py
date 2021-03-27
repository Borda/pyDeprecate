"""
Copyright (C) 2020-2021 Jiri Borovec <...>
"""
import pytest

from tests.collection_deprecate import (
    depr_accuracy_extra,
    depr_accuracy_map,
    depr_accuracy_skip,
    depr_pow_args,
    depr_pow_mix,
    depr_pow_wrong,
    depr_sum,
    depr_sum_calls_2,
    depr_sum_calls_inf,
    depr_sum_msg,
    depr_sum_no_stream,
    depr_sum_warn_only,
)


def test_deprecated_func_warn_only() -> None:
    with pytest.deprecated_call(
        match='The `depr_sum_warn_only` was deprecated since v0.2. It will be removed in v0.3.'
    ):
        assert depr_sum_warn_only(2) is None


def test_deprecated_func_default() -> None:
    with pytest.deprecated_call(
        match='The `depr_sum` was deprecated since v0.1 in favor of `tests.collection_targets.base_sum_kwargs`.'
        ' It will be removed in v0.5.'
    ):
        assert depr_sum(2) == 7

    # check that the warning is raised only once per function
    with pytest.warns(None) as record:
        assert depr_sum(3) == 8
    assert len(record) == 0

    # and does not affect other functions
    with pytest.deprecated_call(
        match='The `depr_pow_mix` was deprecated since v0.1 in favor of `tests.collection_targets.base_pow_args`.'
        ' It will be removed in v0.5.'
    ):
        assert depr_pow_mix(2, 1) == 2


def test_deprecated_func_stream_calls() -> None:
    # check that the warning is raised only once per function
    with pytest.warns(None) as record:
        assert depr_sum_no_stream(3) == 8
    assert len(record) == 0

    # check that the warning is raised only once per function
    with pytest.warns(DeprecationWarning) as record:
        for _ in range(5):
            assert depr_sum_calls_2(3) == 8
    assert len(record) == 2

    # check that the warning is raised only once per function
    with pytest.warns(DeprecationWarning) as record:
        for _ in range(5):
            assert depr_sum_calls_inf(3) == 8
    assert len(record) == 5

    with pytest.deprecated_call(match='v0.1: `depr_sum_msg` was deprecated, use `base_sum_kwargs`'):
        assert depr_sum_msg(3) == 8


def test_deprecated_func_incomplete() -> None:
    # missing required argument
    with pytest.raises(TypeError, match="missing 1 required positional argument: 'b'"):
        depr_pow_args(2)

    # missing argument in target
    with pytest.raises(TypeError, match=r"Failed mapping, arguments missing in target source: \['c'\]"):
        depr_pow_wrong(2)

    # check that the warning is raised only once per function
    with pytest.warns(None) as record:
        assert depr_pow_args(2, 1) == 2
    assert len(record) == 0

    # reset the warning
    depr_pow_args._warned = False
    # does not affect other functions
    with pytest.deprecated_call(
        match='The `depr_pow_args` was deprecated since v1.0 in favor of `tests.collection_targets.base_pow_args`.'
        ' It will be removed in v1.3.'
    ):
        assert depr_pow_args(b=2, a=1) == 1


def test_deprecated_func_mapping() -> None:
    assert depr_accuracy_map([1, 0, 1, 2]) == 0.5

    assert depr_accuracy_skip([1, 0, 1, 2]) == 0.5

    assert depr_accuracy_extra([1, 0, 1, 2]) == 0.75
