"""Tests for deprecated classes and methods."""

import pytest

from deprecate._types import DeprecationInfo
from deprecate.utils import no_warning_call
from tests.collection_deprecate import (
    DeprecatedDataClass,
    DeprecatedEnum,
    DeprecatedIntEnum,
    MappedEnum,
    MappedIntEnum,
    MappedValueEnum,
    PastCls,
    RedirectedDataClass,
    RedirectedEnum,
    SelfMappedEnum,
    ServiceCls,
    ThisCls,
)
from tests.collection_targets import NewCls, NewDataClass, NewEnum, NewIntEnum


class TestDeprecatedClass:
    """Tests for deprecated classes."""

    @pytest.fixture(autouse=True)
    def _reset_deprecation_state(self) -> None:
        """Reset deprecation state for PastCls.__init__."""
        getattr(PastCls.__init__, "_state").warned_calls = 0

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
        getattr(PastCls.__init__, "_state").warned_calls = 0
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
    """Tests for deprecated Enum wrappers backed by _DeprecatedProxy via @deprecated_class."""

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
        with pytest.warns(FutureWarning), pytest.raises(ValueError, match="nonexistent"):
            DeprecatedEnum("nonexistent")

    def test_enum_attribute_access_warns(self) -> None:
        """Test that attribute access on a deprecated Enum proxy emits a FutureWarning.

        Unlike the old _DeprecatedEnumWrapper, _DeprecatedProxy warns on every access
        (attribute, subscript, call) — consistent with deprecating the class as a whole.
        """
        with pytest.warns(FutureWarning):
            assert DeprecatedEnum.ALPHA.value == "alpha"
        with pytest.warns(FutureWarning):
            assert DeprecatedEnum["ALPHA"] is DeprecatedEnum.ALPHA

    def test_enum_redirects_to_replacement(self) -> None:
        """Test deprecated Enum forwarding to a replacement Enum."""
        with pytest.warns(FutureWarning):
            assert RedirectedEnum("alpha") is NewEnum.ALPHA

    def test_enum_argument_mapping_forwards(self) -> None:
        """Test argument mapping when forwarding deprecated Enum to replacement."""
        with pytest.warns(FutureWarning):
            assert MappedEnum(old_value="alpha") is NewEnum.ALPHA  # type: ignore[call-arg]

    def test_enum_argument_mapping_positional_value(self) -> None:
        """Test mapped Enum forwards positional value to replacement."""
        with pytest.warns(FutureWarning):
            assert MappedEnum("alpha") is NewEnum.ALPHA

    def test_enum_argument_mapping_int_forwards(self) -> None:
        """Test argument mapping for int enums forwarding to NewIntEnum."""
        with pytest.warns(FutureWarning):
            assert MappedIntEnum(old_value=1) is NewIntEnum.ALPHA  # type: ignore[call-arg]

    def test_enum_argument_mapping_value_differs(self) -> None:
        """Test mapping when enum values differ from the new enum."""
        with pytest.warns(FutureWarning):
            assert MappedValueEnum(old_value="alpha") is NewEnum.ALPHA  # type: ignore[call-arg]

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
        assert instance.label == "alpha"
        assert instance.total == 2
        with pytest.warns(FutureWarning):
            instance = DeprecatedDataClass(label="beta")
        assert instance.label == "beta"
        assert instance.total == 0

    def test_dataclass_forwarding(self) -> None:
        """Test deprecated dataclass forwarding to NewDataClass."""
        with pytest.warns(FutureWarning):
            instance = RedirectedDataClass("alpha", 2)
        assert instance.label == "alpha"
        assert instance.total == 2
        assert isinstance(instance, NewDataClass)


class TestDeprecatedClassMethod:
    """Tests for @deprecated applied to individual class methods (non-__init__)."""

    @pytest.fixture(autouse=True)
    def _reset_method_state(self) -> None:
        """Reset warned_calls and called so each test gets a fresh warning budget."""
        for method in (
            ServiceCls.old_warn_method,
            ServiceCls.old_redirect_method,
            ServiceCls.old_mapped_method,
            ServiceCls.self_renamed_method,
        ):
            state = getattr(method, "_state")
            state.called = 0
            state.warned_calls = 0

    def test_warn_only_method_emits_warning(self) -> None:
        """@deprecated(target=None) on a method emits FutureWarning."""
        svc = ServiceCls()
        with pytest.warns(FutureWarning, match="old_warn_method"):
            result = svc.old_warn_method(5)
        assert result == 10

    def test_warn_only_method_body_executes(self) -> None:
        """With target=None the method body still runs and returns its result."""
        svc = ServiceCls()
        with pytest.warns(FutureWarning):
            assert svc.old_warn_method(3) == 6

    def test_redirect_method_emits_warning(self) -> None:
        """@deprecated(target=compute) on a method emits FutureWarning."""
        svc = ServiceCls()
        with pytest.warns(FutureWarning, match="old_redirect_method"):
            result = svc.old_redirect_method(5)
        assert result == 10

    def test_redirect_method_forwards_to_target(self) -> None:
        """Deprecated method redirected to compute() returns the same result."""
        svc = ServiceCls()
        with pytest.warns(FutureWarning):
            redirected = svc.old_redirect_method(4)
        assert redirected == svc.compute(4)

    def test_warn_only_warning_content(self) -> None:
        """FutureWarning message contains source name, version info, and removal hint."""
        svc = ServiceCls()
        with pytest.warns(FutureWarning, match="deprecated since v1.0.*removed in v2.0"):
            svc.old_warn_method(1)

    def test_redirect_warning_content(self) -> None:
        """FutureWarning message for redirect contains source and target names."""
        svc = ServiceCls()
        with pytest.warns(FutureWarning, match="old_redirect_method.*compute"):
            svc.old_redirect_method(1)

    def test_args_mapping_renames_argument(self) -> None:
        """args_mapping renames x->value when forwarding to compute_scaled()."""
        svc = ServiceCls()
        with pytest.warns(FutureWarning, match="old_mapped_method"):
            result = svc.old_mapped_method(x=5)
        assert result == svc.compute_scaled(value=5)

    def test_args_mapping_positional_argument(self) -> None:
        """Positional call with args_mapping: positional x is renamed to value."""
        svc = ServiceCls()
        with pytest.warns(FutureWarning):
            result = svc.old_mapped_method(5)
        assert result == 10

    def test_self_rename_with_deprecated_arg_warns(self) -> None:
        """target=True with args_mapping renames old_x->x within the same method."""
        svc = ServiceCls()
        with pytest.warns(FutureWarning, match="self_renamed_method.*old_x.*x"):
            result = svc.self_renamed_method(old_x=5)
        assert result == 10

    def test_self_rename_with_new_arg_no_warning(self) -> None:
        """Calling with the new arg name (x) does not trigger a deprecation warning."""
        svc = ServiceCls()
        with no_warning_call():
            result = svc.self_renamed_method(x=3)
        assert result == 6


def test_deprecated_class_attribute_set_at_decoration_time() -> None:
    """Test that __deprecated__ attribute is set at decoration time, not call time.

    This verifies that the __deprecated__ attribute is available immediately
    after the decorator is applied, without needing to call the class first.
    """
    # Verify __deprecated__ is set on the __init__ WITHOUT instantiating the class
    assert hasattr(PastCls.__init__, "__deprecated__")
    assert PastCls.__init__.__deprecated__ == DeprecationInfo(
        deprecated_in="0.2",
        remove_in="0.4",
        name="__init__",
        target=NewCls,
        args_mapping=None,
    )
