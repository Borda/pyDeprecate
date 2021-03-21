import pytest

from deprecate.deprecation import deprecated


class NewCls:

    def __init__(self, c: float, d: str = "abc"):
        self.my_c = c
        self.my_d = d


class PastCls(NewCls):

    @deprecated(target=NewCls, deprecated_in="0.2", remove_in="0.4")
    def __init__(self, c: int, d: str = "efg"):
        pass


def test_deprecated_class() -> None:
    with pytest.deprecated_call(
        match='The `PastCls` was deprecated since v0.2 in favor of `test_classes.NewCls`. It will be removed in v0.4.'
    ):
        past = PastCls(2)
    assert past.my_c == 2
    assert past.my_d == "efg"

    # check that the warning is raised only once per function
    with pytest.warns(None) as record:
        assert PastCls(c=2, d="")
    assert len(record) == 0

    PastCls.__init__._warned = False
    with pytest.deprecated_call(match='It will be removed in v0.4.'):
        PastCls(2)
