import pytest

from deprecate.deprecation import deprecated


def base_sum_kwargs(a=0, b=3):
    return a + b


def base_pow_args(a, b):
    return a ** b


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.5")
def depr_sum(a, b=5):
    pass


@deprecated(target=base_pow_args, deprecated_in="1.0", remove_in="1.3")
def depr_pow_args(a, b):
    pass


@deprecated(target=base_pow_args, deprecated_in="0.1", remove_in="0.5")
def depr_pow_mix(a, b=4):
    pass


def test_deprecated_func():
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


def test_deprecated_func_incomplete():

    # missing required argument
    with pytest.raises(TypeError, match="missing 1 required positional argument: 'b'"):
        depr_pow_args(2)

    # check that the warning is raised only once per function
    with pytest.warns(None) as record:
        assert depr_pow_args(2, 1) == 2
    assert len(record) == 0

    # reset the warning
    depr_pow_args.warned = False
    # does not affect other functions
    with pytest.deprecated_call(
        match='The `depr_pow_args` was deprecated since v1.0 in favor of `test_functions.base_pow_args`.'
              ' It will be removed in v1.3.'
    ):
        assert depr_pow_args(b=2, a=1) == 1
