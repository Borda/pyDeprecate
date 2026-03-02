"""Unit tests for _DeprecatedProxy internals and deprecated_class decorator behaviour."""

import warnings

import pytest

from deprecate.proxy import _DeprecatedProxy
from tests.collection_deprecate import (
    DeprecatedColorDataClass,
    DeprecatedColorEnum,
    MappedColorEnum,
    MappedDataClass,
    MappedDropArgDataClass,
    WarnOnlyColorEnum,
)
from tests.collection_targets import NewDataClass, TargetColorEnum


class TestProxyInit:
    """Internal state initialisation for _DeprecatedProxy instances."""

    def test_internal_state_stored_correctly(self) -> None:
        """All constructor kwargs are stored in name-mangled attributes."""
        obj = {"a": 1}
        proxy = _DeprecatedProxy(obj=obj, name="x", deprecated_in="1.0", remove_in="2.0", num_warns=3, stream=None)
        assert object.__getattribute__(proxy, "_DeprecatedProxy__obj") is obj
        assert object.__getattribute__(proxy, "_DeprecatedProxy__name") == "x"
        assert object.__getattribute__(proxy, "_DeprecatedProxy__deprecated_in") == "1.0"
        assert object.__getattribute__(proxy, "_DeprecatedProxy__remove_in") == "2.0"
        assert object.__getattribute__(proxy, "_DeprecatedProxy__num_warns") == 3
        assert object.__getattribute__(proxy, "_DeprecatedProxy__stream") is None
        assert object.__getattribute__(proxy, "_DeprecatedProxy__read_only") is False
        assert object.__getattribute__(proxy, "_DeprecatedProxy__warned") == 0

    def test_deprecated_metadata_attribute(self) -> None:
        """__deprecated__ dict is set with correct keys."""
        proxy = _DeprecatedProxy(obj={}, name="x", deprecated_in="1.0", remove_in="2.0")
        meta = object.__getattribute__(proxy, "__deprecated__")
        assert meta["name"] == "x"
        assert meta["deprecated_in"] == "1.0"
        assert meta["remove_in"] == "2.0"


class TestProxyWarnBehavior:
    """Warning count and message logic."""

    def test_num_warns_zero_never_warns(self) -> None:
        """num_warns=0 means never warn."""
        proxy = _DeprecatedProxy(obj={"k": 1}, name="x", deprecated_in="1.0", remove_in="2.0", num_warns=0)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn()
            proxy._warn()
        assert not caught

    def test_warn_increments_counter(self) -> None:
        """Each emitted warning increments the internal counter."""
        proxy = _DeprecatedProxy(obj={}, name="x", deprecated_in="1.0", remove_in="2.0", num_warns=-1)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            proxy._warn()
            proxy._warn()
        assert object.__getattribute__(proxy, "_DeprecatedProxy__warned") == 2

    def test_warn_stops_after_limit(self) -> None:
        """Warnings stop once num_warns threshold is reached."""
        proxy = _DeprecatedProxy(obj={}, name="x", deprecated_in="1.0", remove_in="2.0", num_warns=2)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            for _ in range(5):
                proxy._warn()
        assert len(caught) == 2

    def test_warn_no_stream(self) -> None:
        """stream=None suppresses all warnings."""
        proxy = _DeprecatedProxy(obj={}, name="x", deprecated_in="1.0", remove_in="2.0", stream=None)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn()
        assert not caught

    def test_warn_message_contains_name_and_versions(self) -> None:
        """Warning message includes the name, deprecated_in and remove_in values."""
        proxy = _DeprecatedProxy(obj={}, name="legacy_cfg", deprecated_in="2.3", remove_in="4.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn()
        msg = str(caught[0].message)
        assert "legacy_cfg" in msg
        assert "2.3" in msg
        assert "4.0" in msg

    def test_warn_category_is_future_warning(self) -> None:
        """Default stream emits FutureWarning."""
        proxy = _DeprecatedProxy(obj={}, name="x", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn()
        assert caught[0].category is FutureWarning


class TestProxyReadOnly:
    """Read-only mode enforcement."""

    def test_raises_when_active(self) -> None:
        """_check_read_only raises AttributeError in read_only mode."""
        proxy = _DeprecatedProxy(obj={}, name="d", deprecated_in="1.0", remove_in="2.0", read_only=True)
        with pytest.raises(AttributeError, match="read-only"):
            proxy._check_read_only("Test operation")

    def test_silent_when_inactive(self) -> None:
        """_check_read_only does nothing when read_only is False."""
        proxy = _DeprecatedProxy(obj={}, name="d", deprecated_in="1.0", remove_in="2.0", read_only=False)
        proxy._check_read_only("Test operation")  # must not raise


class TestProxyGetActive:
    """Active object selection: source vs. target."""

    def test_returns_obj_when_no_target(self) -> None:
        """Without target, _get_active returns the source object."""
        obj = {"k": 1}
        proxy = _DeprecatedProxy(obj=obj, name="x", deprecated_in="1.0", remove_in="2.0")
        assert proxy._get_active() is obj

    def test_returns_target_when_set(self) -> None:
        """With target set, _get_active returns the target."""
        obj = {"k": 1}
        tgt = {"k": 2}
        proxy = _DeprecatedProxy(obj=obj, name="x", deprecated_in="1.0", remove_in="2.0", target=tgt)
        assert proxy._get_active() is tgt


class TestProxyNoWarnMethods:
    """Methods that delegate without emitting a warning."""

    def test_repr_no_warn(self) -> None:
        """__repr__ delegates to the source without warning."""
        inner = [1, 2, 3]
        proxy = _DeprecatedProxy(obj=inner, name="x", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            r = repr(proxy)
        assert r == repr(inner)
        assert not caught

    def test_str_no_warn(self) -> None:
        """__str__ delegates without warning."""
        inner = {"a": 1}
        proxy = _DeprecatedProxy(obj=inner, name="x", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert str(proxy) == str(inner)
        assert not caught

    def test_bool_no_warn(self) -> None:
        """__bool__ delegates without warning."""
        proxy_t = _DeprecatedProxy(obj=[1], name="x", deprecated_in="1.0", remove_in="2.0")
        proxy_f = _DeprecatedProxy(obj=[], name="x", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert bool(proxy_t)
            assert not bool(proxy_f)
        assert not caught

    def test_len_no_warn(self) -> None:
        """__len__ delegates without warning."""
        proxy = _DeprecatedProxy(obj=[1, 2, 3], name="x", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert len(proxy) == 3
        assert not caught

    def test_contains_no_warn(self) -> None:
        """__contains__ delegates without warning."""
        proxy = _DeprecatedProxy(obj={"k": 1}, name="x", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert "k" in proxy
            assert "z" not in proxy
        assert not caught

    def test_eq_no_warn(self) -> None:
        """__eq__ does not emit a warning."""
        inner = {"a": 1}
        proxy = _DeprecatedProxy(obj=inner, name="x", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = proxy == inner
        assert result
        assert not caught

    def test_eq_proxy_vs_proxy(self) -> None:
        """Two proxies wrapping equal objects compare equal."""
        inner = {"a": 1}
        p1 = _DeprecatedProxy(obj=inner, name="x", deprecated_in="1.0", remove_in="2.0")
        p2 = _DeprecatedProxy(obj=inner, name="y", deprecated_in="2.0", remove_in="3.0")
        assert p1 == p2

    def test_ne(self) -> None:
        """__ne__ is the inverse of __eq__."""
        p1 = _DeprecatedProxy(obj={"a": 1}, name="x", deprecated_in="1.0", remove_in="2.0")
        p2 = _DeprecatedProxy(obj={"a": 2}, name="x", deprecated_in="1.0", remove_in="2.0")
        assert p1 != p2


class TestProxyWarnMethods:
    """Methods that warn on access."""

    def test_getitem_warns(self) -> None:
        """__getitem__ emits warning."""
        proxy = _DeprecatedProxy(obj={"k": 99}, name="x", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning):
            val = proxy["k"]
        assert val == 99

    def test_getattr_warns(self) -> None:
        """__getattr__ emits warning."""
        proxy = _DeprecatedProxy(obj={"k": 1}, name="x", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning):
            method = proxy.get
        assert callable(method)

    def test_iter_warns(self) -> None:
        """__iter__ emits warning."""
        proxy = _DeprecatedProxy(obj={"a": 1, "b": 2}, name="x", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning):
            keys = list(proxy)
        assert set(keys) == {"a", "b"}

    def test_call_warns_and_invokes(self) -> None:
        """__call__ emits warning and invokes the active object."""
        proxy = _DeprecatedProxy(obj=lambda x: x * 2, name="fn", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning):
            result = proxy(5)
        assert result == 10

    def test_call_with_target_invokes_target(self) -> None:
        """__call__ with a target invokes the target, not the source."""
        source = lambda x: x  # noqa: E731
        target = lambda x: x * 3  # noqa: E731
        proxy = _DeprecatedProxy(
            obj=source, name="fn", deprecated_in="1.0", remove_in="2.0", target=target, stream=None
        )
        assert proxy(4) == 12


class TestDecoratorFactory:
    """deprecated_class used as a class decorator."""

    def test_decorated_class_is_deprecated_proxy(self) -> None:
        """@deprecated_class wraps the class in a _DeprecatedProxy."""
        assert isinstance(WarnOnlyColorEnum, _DeprecatedProxy)
        assert isinstance(DeprecatedColorEnum, _DeprecatedProxy)

    def test_uses_class_name_as_proxy_name(self) -> None:
        """The proxy name is taken from the decorated class __name__."""
        name = object.__getattribute__(WarnOnlyColorEnum, "_DeprecatedProxy__name")
        assert name == "WarnOnlyColorEnum"

    def test_no_target_reads_from_source(self) -> None:
        """Without a target, attribute access reads from the source class."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert WarnOnlyColorEnum.A.value == "a"

    def test_with_target_reads_from_target(self) -> None:
        """With a target, attribute access returns the target's member."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert DeprecatedColorEnum.RED is TargetColorEnum.RED


class TestDecoratorEnum:
    """@deprecated_class applied to Enum classes."""

    def test_call_warns_and_redirects(self) -> None:
        """Calling the deprecated enum warns and returns the target member."""
        with pytest.warns(FutureWarning, match="DeprecatedColorEnum"):
            result = DeprecatedColorEnum(1)
        assert result is TargetColorEnum.RED

    def test_attr_access_warns_and_redirects(self) -> None:
        """Attribute access warns and returns the target member."""
        with pytest.warns(FutureWarning, match="DeprecatedColorEnum"):
            result = DeprecatedColorEnum.RED
        assert result is TargetColorEnum.RED

    def test_item_access_warns_and_redirects(self) -> None:
        """Item access warns and returns the target member."""
        with pytest.warns(FutureWarning, match="DeprecatedColorEnum"):
            result = DeprecatedColorEnum["RED"]
        assert result is TargetColorEnum.RED

    def test_no_target_warns_and_reads_source(self) -> None:
        """@deprecated_class with no target warns and reads from source."""
        with pytest.warns(FutureWarning, match="WarnOnlyColorEnum"):
            val = WarnOnlyColorEnum.A
        assert val.value == "a"

    def test_returns_deprecated_proxy(self) -> None:
        """@deprecated_class wraps the class in a _DeprecatedProxy."""
        assert isinstance(DeprecatedColorEnum, _DeprecatedProxy)
        assert isinstance(WarnOnlyColorEnum, _DeprecatedProxy)


class TestDecoratorDataclass:
    """@deprecated_class applied to dataclasses."""

    def test_instantiation_warns_and_redirects(self) -> None:
        """Instantiation warns and returns an instance of the target class."""
        with pytest.warns(FutureWarning, match="DeprecatedColorDataClass"):
            obj = DeprecatedColorDataClass(label="test", total=5)
        assert isinstance(obj, NewDataClass)
        assert obj.label == "test"
        assert obj.total == 5


class TestArgMapping:
    """arg_mapping remaps or drops kwargs when the proxy is called."""

    def test_remap_single_kwarg(self) -> None:
        """arg_mapping renames a kwarg before forwarding the call."""
        with pytest.warns(FutureWarning):
            result = MappedDataClass(name="hello", total=7)
        assert isinstance(result, NewDataClass)
        assert result.label == "hello"
        assert result.total == 7

    def test_remap_multiple_kwargs(self) -> None:
        """arg_mapping renames multiple kwargs correctly."""
        with pytest.warns(FutureWarning):
            result = MappedDataClass(name="world", count=3)
        assert isinstance(result, NewDataClass)
        assert result.label == "world"
        assert result.total == 3

    def test_drop_kwarg(self) -> None:
        """arg_mapping drops kwargs mapped to None and remaps others."""
        with pytest.warns(FutureWarning):
            result = MappedDropArgDataClass(name="x", legacy_flag=True)
        assert isinstance(result, NewDataClass)
        assert result.label == "x"

    def test_arg_mapping_stored_in_proxy(self) -> None:
        """arg_mapping is stored in the proxy's internal state."""
        mapping = object.__getattribute__(MappedDataClass, "_DeprecatedProxy__arg_mapping")
        assert mapping == {"name": "label", "count": "total"}

    def test_enum_remap_kwarg(self) -> None:
        """arg_mapping works when the deprecated class wraps an Enum."""
        with pytest.warns(FutureWarning):
            result = MappedColorEnum(val=1)
        assert result is TargetColorEnum.RED
