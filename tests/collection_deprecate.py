"""
Copyright (C) 2020-2021 Jiri Borovec <...>
"""

from sklearn.metrics import accuracy_score

from deprecate import deprecated
from tests.collection_targets import base_pow_args, base_sum_kwargs

_SHORT_MSG = "`%(source_name)s` >> `%(target_path)s` in v%(deprecated_in)s rm v%(remove_in)s."

@deprecated(target=None, deprecated_in="0.2", remove_in="0.3")
def depr_sum_warn_only(a: int, b: int = 5) -> int:
    pass


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.5")
def depr_sum(a: int, b: int = 5) -> int:
    pass


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.6", stream=None)
def depr_sum_no_stream(a: int, b: int = 5) -> int:
    pass


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.7", num_warns=2)
def depr_sum_calls_2(a: int, b: int = 5) -> int:
    pass


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.7", num_warns=-1)
def depr_sum_calls_inf(a: int, b: int = 5) -> int:
    pass


@deprecated(
    target=base_sum_kwargs,
    deprecated_in="0.1",
    remove_in="0.5",
    template_mgs="v%(deprecated_in)s: `%(source_name)s` was deprecated, use `%(target_name)s`"
)
def depr_sum_msg(a: int, b: int = 5) -> int:
    pass


@deprecated(target=base_pow_args, deprecated_in="1.0", remove_in="1.3")
def depr_pow_args(a: float, b: float) -> float:
    pass


@deprecated(target=base_pow_args, deprecated_in="0.1", remove_in="0.5")
def depr_pow_mix(a: int, b: float = 4) -> float:
    pass


@deprecated(target=base_pow_args, deprecated_in="0.1", remove_in="0.5")
def depr_pow_wrong(a: int, c: float = 4) -> float:
    pass


@deprecated(target=accuracy_score, args_mapping={'preds': 'y_pred', 'yeah_arg': None})
def depr_accuracy_skip(preds: list, y_true: tuple = (0, 1, 1, 2), yeah_arg: float = 1.23) -> float:
    pass


@deprecated(target=accuracy_score, args_mapping={'preds': 'y_pred', 'truth': 'y_true'})
def depr_accuracy_map(preds: list, truth: tuple = (0, 1, 1, 2)) -> float:
    pass


@deprecated(target=accuracy_score, args_extra={'y_pred': (0, 1, 1, 1)})
def depr_accuracy_extra(y_pred: list, y_true: tuple = (0, 1, 1, 2)) -> float:
    pass


@deprecated(target=None, deprecated_in="0.1", remove_in="0.5", args_mapping={'pow': 'super_pow'})
def depr_pow_self(base: float, pow: float, super_pow: float) -> float:
    return base**super_pow
