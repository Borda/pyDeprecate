"""
Copyright (C) 2020-2021 Jiri Borovec <...>
"""


def base_sum_kwargs(a: int = 0, b: int = 3) -> int:
    return a + b


def base_pow_args(a: float, b: int) -> float:
    return a**b


class NewCls:

    def __init__(self, c: float, d: str = "abc"):
        self.my_c = c
        self.my_d = d
