"""Tests for deprecated classes and methods."""

from functools import partial
from typing import Any
from warnings import warn

import pytest

from deprecate.deprecation import deprecated
from deprecate.utils import no_warning_call
from tests.collection_deprecate import (
    DeprecatedDataClass,
    DeprecatedEnum,
    DeprecatedIntEnum,
    MappedEnum,
    MappedIntEnum,
    MappedValueEnum,
    RedirectedDataClass,
    RedirectedEnum,
    SelfMappedEnum,
)
from tests.collection_targets import NewCls, NewDataClass, NewEnum, NewIntEnum

_deprecation_warning = partial(warn, category=DeprecationWarning)


class PastCls(NewCls):
    """Deprecated class inheriting from NewCls."""

    @deprecated(target=NewCls, deprecated_in="0.2", remove_in="0.4", stream=_deprecation_warning)
    def __init__(self, c: int, d: str = "efg", **kwargs: Any) -> None:  # noqa: ANN401
        """Initialize PastCls."""
        super().__init__(c)


class ThisCls(NewCls):
    """Class with deprecated __init__ method."""

    @deprecated(
        target=True, deprecated_in="0.3", remove_in="0.5", args_mapping={"c": "nc"}, stream=_deprecation_warning
    )
    def __init__(self, c: int = 3, nc: int = 5) -> None:
        """Initialize ThisCls."""
        super().__init__(c=nc)


class TestDeprecatedClass:
    """Tests for deprecated classes."""

    @pytest.fixture(autouse=True)
    def _reset_deprecation_state(self) -> None:
        """Reset deprecation state for PastCls.__init__."""
        if hasattr(PastCls.__init__, "_warned"):
            PastCls.__init__._warned = False

    def test_class_forward(self) -> None:
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

    def test_class_forward_once(self) -> None:
        """Check that the warning is raised only on the first call to the wrapped __init__."""
        PastCls.__init__._warned = False
        with pytest.warns(DeprecationWarning, match="It will be removed in v0.4."):
            PastCls(2)
        with no_warning_call():
            assert PastCls(c=2, d="", e=0.9999)

    def test_class_self_new_args(self) -> None:
        """Test deprecated class with self-referencing __init__, using new arguments."""
        with no_warning_call():
            this = ThisCls(nc=1)
        assert this.my_c == 1
        assert isinstance(this, ThisCls)

    def test_class_self_deprecated_args(self) -> None:
        """Test deprecated class with self-referencing __init__, using deprecated arguments."""
        with pytest.warns(
            DeprecationWarning,
            match="The `ThisCls` uses deprecated arguments: `c` -> `nc`."
            " They were deprecated since v0.3 and will be removed in v0.5.",
        ):
            this = ThisCls(2)
        assert this.my_c == 2
        assert isinstance(this, ThisCls)


class TestDeprecatedEnums:
    """Tests for deprecated Enum wrappers."""

    def test_enum_by_string_value(self) -> None:
        """Test that deprecated Enum can be instantiated by string value."""
        with pytest.warns(FutureWarning):
            assert DeprecatedEnum("alpha") is DeprecatedEnum.ALPHA

    def test_enum_by_int_value(self) -> None:
        """Test that deprecated Enum can be instantiated by int value."""
        with pytest.warns(FutureWarning):
            assert DeprecatedIntEnum(1) is DeprecatedIntEnum.ONE

    def test_enum_invalid_value_raises_value_error(self) -> None:
        """Test that invalid Enum values still raise ValueError."""
        with pytest.warns(FutureWarning), pytest.raises(ValueError):
            DeprecatedEnum("nonexistent")

    def test_enum_attribute_access(self) -> None:
        """Test attribute access on deprecated Enum."""
        with no_warning_call(FutureWarning):
            assert DeprecatedEnum.ALPHA.value == "alpha"

    def test_enum_redirects_to_replacement(self) -> None:
        """Test deprecated Enum forwarding to a replacement Enum."""
        with pytest.warns(FutureWarning):
            assert RedirectedEnum("alpha") is NewEnum.ALPHA

    def test_enum_argument_mapping_forwards(self) -> None:
        """Test argument mapping when forwarding deprecated Enum to replacement."""
        with pytest.warns(FutureWarning):
            assert MappedEnum(old_value="alpha") is NewEnum.ALPHA

    def test_enum_argument_mapping_positional_value(self) -> None:
        """Test mapped Enum forwards positional value to replacement."""
        with pytest.warns(FutureWarning):
            assert MappedEnum("alpha") is NewEnum.ALPHA

    def test_enum_argument_mapping_int_forwards(self) -> None:
        """Test argument mapping for int enums forwarding to NewIntEnum."""
        with pytest.warns(FutureWarning):
            assert MappedIntEnum(old_value=1) is NewIntEnum.ALPHA

    def test_enum_argument_mapping_value_differs(self) -> None:
        """Test mapping when enum values differ from the new enum."""
        with pytest.warns(FutureWarning):
            assert MappedValueEnum(old_value="alpha") is NewEnum.ALPHA

    def test_enum_self_argument_mapping(self) -> None:
        """Test argument mapping when deprecating within the same enum."""
        with pytest.warns(FutureWarning):
            assert SelfMappedEnum(old_value="alpha") is SelfMappedEnum.ALPHA


class TestDeprecatedDataclasses:
    """Tests for deprecated dataclass wrappers."""

    def test_dataclass_positional_and_keyword_init(self) -> None:
        """Test that deprecated dataclass supports positional and keyword args."""
        with pytest.warns(FutureWarning):
            instance = DeprecatedDataClass("alpha", 2)
        assert instance.name == "alpha"
        assert instance.count == 2
        with pytest.warns(FutureWarning):
            instance = DeprecatedDataClass(name="beta")
        assert instance.name == "beta"
        assert instance.count == 0

    def test_dataclass_forwarding(self) -> None:
        """Test deprecated dataclass forwarding to NewDataClass."""
        with pytest.warns(FutureWarning):
            instance = RedirectedDataClass("alpha", 2)
        assert instance.name == "alpha"
        assert instance.count == 2
        assert isinstance(instance, NewDataClass)


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
