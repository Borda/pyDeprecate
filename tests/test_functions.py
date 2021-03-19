import pytest
from sklearn.metrics import accuracy_score

from deprecate.deprecation import deprecated


def base_sum_kwargs(a: int = 0, b: int = 3) -> int:
    return a + b


def base_pow_args(a: float, b: int) -> float:
    return a**b


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.5")
def depr_sum(a: int, b: int = 5) -> int:
    pass


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.5", stream=None)
def depr_sum_no_stream(a: int, b: int = 5) -> int:
    pass


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.5", num_warns=2)
def depr_sum_calls_2(a: int, b: int = 5) -> int:
    pass


@deprecated(
    target=base_sum_kwargs,
    deprecated_in="0.1",
    remove_in="0.5",
    template_mgs="v%(deprecated_in)s: `%(source_name)s` was deprecated, use `%(target_path)s`"
)
def depr_sum_msg(a: int, b: int = 5) -> int:
    pass


@deprecated(target=base_pow_args, deprecated_in="1.0", remove_in="1.3")
def depr_pow_args(a: float, b: float) -> float:
    pass


@deprecated(target=base_pow_args, deprecated_in="0.1", remove_in="0.5")
def depr_pow_mix(a: int, b: float = 4) -> float:
    pass


def test_deprecated_func() -> None:
    with pytest.deprecated_call(
        match='The `depr_sum` was deprecated since v0.1 in favor of `test_functions.base_sum_kwargs`.'
        ' It will be removed in v0.5.'
    ):
        assert depr_sum(2) == 7

    # check that the warning is raised only once per function
    with pytest.warns(None) as record:
        assert depr_sum(3) == 8
    assert len(record) == 0

    # and does not affect other functions
    with pytest.deprecated_call(
        match='The `depr_pow_mix` was deprecated since v0.1 in favor of `test_functions.base_pow_args`.'
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

    with pytest.deprecated_call(match='v0.1: `depr_sum_msg` was deprecated, use `test_functions.base_sum_kwargs`'):
        assert depr_sum_msg(3) == 8


def test_deprecated_func_incomplete() -> None:
    # missing required argument
    with pytest.raises(TypeError, match="missing 1 required positional argument: 'b'"):
        depr_pow_args(2)

    # check that the warning is raised only once per function
    with pytest.warns(None) as record:
        assert depr_pow_args(2, 1) == 2
    assert len(record) == 0

    # reset the warning
    depr_pow_args._warned = False
    # does not affect other functions
    with pytest.deprecated_call(
        match='The `depr_pow_args` was deprecated since v1.0 in favor of `test_functions.base_pow_args`.'
        ' It will be removed in v1.3.'
    ):
        assert depr_pow_args(b=2, a=1) == 1


@deprecated(target=accuracy_score, args_mapping={'preds': 'y_pred', 'truth': 'y_true'})
def depr_accuracy_map(preds: list, truth=(0, 1, 1, 2)) -> float:
    pass


@deprecated(target=accuracy_score, args_mapping={'preds': 'y_pred', 'any': None})
def depr_accuracy_skip(preds: list, y_true=(0, 1, 1, 2), any: float = 1.23) -> float:
    pass


@deprecated(target=accuracy_score, args_extra={'y_pred': (0, 1, 1, 1)})
def depr_accuracy_extra(y_pred: list, y_true=(0, 1, 1, 2)) -> float:
    pass


def test_deprecated_func_mapping() -> None:
    assert depr_accuracy_map([1, 0, 1, 2]) == 0.5

    assert depr_accuracy_skip([1, 0, 1, 2]) == 0.5

    assert depr_accuracy_extra([1, 0, 1, 2]) == 0.75
