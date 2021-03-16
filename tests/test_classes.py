import pytest

from deprecate.deprecation import deprecated


class NewCls:

    def __init__(self, c, d="abc"):
        self.my_c = c
        self.my_d = d


class PastCls:

    @deprecated(target=NewCls, deprecated_in="0.2", remove_in="0.4")
    def __init__(self, c, d="efg"):
        pass


def test_deprecated_class():
    with pytest.deprecated_call(
        match='The `PastCls` was deprecated since v0.2 in favor of `test_classes.NewCls`. It will be removed in v0.4.'
    ):
        past = PastCls(2)
    assert past.my_c == 2
    assert past.my_d == "efg"

    # check that the warning is raised only once per function

    # check that the warning is raised only once per function
    with pytest.warns(None) as record:
        assert PastCls(c=2, d="")
    assert len(record) == 0
