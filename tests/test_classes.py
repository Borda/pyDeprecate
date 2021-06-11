"""
Copyright (C) 2020-2021 Jiri Borovec <...>
"""
from typing import Any

import pytest

from deprecate.deprecation import deprecated
from deprecate.utils import no_warning_call
from tests.collection_targets import NewCls


class PastCls(NewCls):

    @deprecated(target=NewCls, deprecated_in="0.2", remove_in="0.4")
    def __init__(self, c: int, d: str = "efg", **kwargs: Any):
        pass


class ThisCls(NewCls):

    @deprecated(target=True, deprecated_in="0.3", remove_in="0.5", args_mapping={'c': 'nc'})
    def __init__(self, c: int = 3, nc: int = 5):
        self.my_c = nc


def test_deprecated_class_forward() -> None:
    with pytest.deprecated_call(
        match='The `PastCls` was deprecated since v0.2 in favor of `tests.collection_targets.NewCls`.'
        ' It will be removed in v0.4.'
    ):
        past = PastCls(2, e=0.1)
    assert past.my_c == 2
    assert past.my_d == "efg"
    assert past.my_e == 0.1
    assert isinstance(past, NewCls)
    assert isinstance(past, PastCls)

    # check that the warning is raised only once per function
    with no_warning_call():
        assert PastCls(c=2, d="", e=0.9999)

    PastCls.__init__._warned = False
    with pytest.deprecated_call(match='It will be removed in v0.4.'):
        PastCls(2)


def test_deprecated_class_self() -> None:
    with no_warning_call():
        this = ThisCls(nc=1)
    assert this.my_c == 1
    assert isinstance(this, ThisCls)

    with pytest.deprecated_call(
        match='The `ThisCls` uses deprecated arguments: `c` -> `nc`.'
        ' They were deprecated since v0.3 and will be removed in v0.5.'
    ):
        this = ThisCls(2)
    assert this.my_c == 2
    assert isinstance(this, ThisCls)
