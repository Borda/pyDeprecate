"""Tests for deprecated classes and methods."""

from functools import partial
from typing import Any
from warnings import warn

import pytest

from deprecate.deprecation import deprecated
from deprecate.utils import no_warning_call
from tests.collection_targets import NewCls

_deprecation_warning = partial(warn, category=DeprecationWarning)


class PastCls(NewCls):
    """Deprecated class inheriting from NewCls."""

    @deprecated(target=NewCls, deprecated_in="0.2", remove_in="0.4", stream=_deprecation_warning)
    def __init__(self, c: int, d: str = "efg", **kwargs: Any) -> None:  # noqa: ANN401
        """Initialize PastCls."""
        pass


class ThisCls(NewCls):
    """Class with deprecated __init__ method."""

    @deprecated(
        target=True, deprecated_in="0.3", remove_in="0.5", args_mapping={"c": "nc"}, stream=_deprecation_warning
    )
    def __init__(self, c: int = 3, nc: int = 5) -> None:
        """Initialize ThisCls."""
        self.my_c = nc


def test_deprecated_class_forward() -> None:
    """Test deprecated class that forwards to another class."""
    with pytest.warns(
        DeprecationWarning,
        match="The `PastCls` was deprecated since v0.2 in favor of `tests.collection_targets.NewCls`."
        " It will be removed in v0.4.",
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
    with pytest.warns(DeprecationWarning, match="It will be removed in v0.4."):
        PastCls(2)


def test_deprecated_class_self() -> None:
    """Test deprecated class with self-referencing __init__."""
    with no_warning_call():
        this = ThisCls(nc=1)
    assert this.my_c == 1
    assert isinstance(this, ThisCls)

    with pytest.warns(
        DeprecationWarning,
        match="The `ThisCls` uses deprecated arguments: `c` -> `nc`."
        " They were deprecated since v0.3 and will be removed in v0.5.",
    ):
        this = ThisCls(2)
    assert this.my_c == 2
    assert isinstance(this, ThisCls)


def test_deprecated_class_attribute_set_at_decoration_time() -> None:
    """Test that __deprecated__ attribute is set at decoration time, not call time.

    This verifies that the __deprecated__ attribute is available immediately
    after the decorator is applied, without needing to call the class first.
    """
    # Verify __deprecated__ is set on the __init__ WITHOUT instantiating the class
    assert hasattr(PastCls.__init__, "__deprecated__")
    assert PastCls.__init__.__deprecated__ == {
        "deprecated_in": "0.2",
        "remove_in": "0.4",
        "target": NewCls,
        "args_mapping": None,
    }
