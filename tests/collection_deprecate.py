"""
Copyright (C) 2020-2021 Jiri Borovec <...>
"""

from sklearn.metrics import accuracy_score

from deprecate import deprecated, void
from tests.collection_targets import base_pow_args, base_sum_kwargs

_SHORT_MSG_FUNC = "`%(source_name)s` >> `%(target_name)s` in v%(deprecated_in)s rm v%(remove_in)s."
_SHORT_MSG_ARGS = "Depr: v%(deprecated_in)s rm v%(remove_in)s for args: %(argument_map)s."


@deprecated(target=None, deprecated_in="0.2", remove_in="0.3")
def depr_sum_warn_only(a: int, b: int = 5) -> int:
    void(a, b)


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.5")
def depr_sum(a: int, b: int = 5) -> int:
    void(a, b)


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.6", stream=None)
def depr_sum_no_stream(a: int, b: int = 5) -> int:
    void(a, b)


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.7", num_warns=2)
def depr_sum_calls_2(a: int, b: int = 5) -> int:
    void(a, b)


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.7", num_warns=-1)
def depr_sum_calls_inf(a: int, b: int = 5) -> int:
    void(a, b)


@deprecated(
    target=base_sum_kwargs,
    deprecated_in="0.1",
    remove_in="0.5",
    template_mgs="v%(deprecated_in)s: `%(source_name)s` was deprecated, use `%(target_name)s`"
)
def depr_sum_msg(a: int, b: int = 5) -> int:
    void(a, b)


@deprecated(target=base_pow_args, deprecated_in="1.0", remove_in="1.3", template_mgs=_SHORT_MSG_FUNC)
def depr_pow_args(a: float, b: float) -> float:
    void(a, b)


@deprecated(target=base_pow_args, deprecated_in="0.1", remove_in="0.5")
def depr_pow_mix(a: int, b: float = 4) -> float:
    void(a, b)


@deprecated(target=base_pow_args, deprecated_in="0.1", remove_in="0.5")
def depr_pow_wrong(a: int, c: float = 4) -> float:
    void(a, c)


@deprecated(target=accuracy_score, args_mapping={'preds': 'y_pred', 'yeah_arg': None})
def depr_accuracy_skip(preds: list, y_true: tuple = (0, 1, 1, 2), yeah_arg: float = 1.23) -> float:
    void(preds, y_true, yeah_arg)


@deprecated(target=accuracy_score, args_mapping={'preds': 'y_pred', 'truth': 'y_true'})
def depr_accuracy_map(preds: list, truth: tuple = (0, 1, 1, 2)) -> float:
    void(preds, truth)


@deprecated(target=accuracy_score, args_extra={'y_pred': (0, 1, 1, 1)})
def depr_accuracy_extra(y_pred: list, y_true: tuple = (0, 1, 1, 2)) -> float:
    void(y_pred, y_true)


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={'coef': 'new_coef'})
def depr_pow_self(base: float, coef: float = 0, new_coef: float = 0) -> float:
    return base**new_coef


@deprecated(
    target=True,
    template_mgs="The `%(source_name)s` uses depr. args: %(argument_map)s.",
    args_mapping=dict(c1='nc1', c2='nc2')
)
def depr_pow_self_double(base: float, c1: float = 0, c2: float = 0, nc1: float = 1, nc2: float = 2) -> float:
    return base**(c1 + c2 + nc1 + nc2)


@deprecated(True, "0.3", "0.6", args_mapping=dict(c1='nc1'), template_mgs=_SHORT_MSG_ARGS)
@deprecated(True, "0.4", "0.7", args_mapping=dict(nc1='nc2'), template_mgs=_SHORT_MSG_ARGS)
def depr_pow_self_twice(base: float, c1: float = 0, nc1: float = 0, nc2: float = 2) -> float:
    return base**(c1 + nc1 + nc2)
