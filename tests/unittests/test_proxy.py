"""Unit tests for _DeprecatedProxy internals and deprecated_class decorator behaviour."""

import warnings
from collections.abc import Callable

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
        """Constructor stores runtime config in __config and metadata in __deprecated__."""
        obj = {"a": 1}
        proxy = _DeprecatedProxy(obj=obj, name="x", deprecated_in="1.0", remove_in="2.0", num_warns=3, stream=None)
        cfg = object.__getattribute__(proxy, "_DeprecatedProxy__config")
        assert cfg["obj"] is obj
        assert cfg["num_warns"] == 3
        assert cfg["stream"] is None
        assert cfg["read_only"] is False
        assert cfg["warned"] == 0
        meta = object.__getattribute__(proxy, "__deprecated__")
        assert meta["name"] == "x"
        assert meta["deprecated_in"] == "1.0"
        assert meta["remove_in"] == "2.0"
        assert meta["target"] is None
        assert meta["args_mapping"] is None


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
        assert object.__getattribute__(proxy, "_DeprecatedProxy__config")["warned"] == 2

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

    def test_warn_message_includes_target_path_for_callable_target(self) -> None:
        """Warnings include replacement path when target is callable."""
        proxy = _DeprecatedProxy(
            obj={},
            name="old_color",
            deprecated_in="1.0",
            remove_in="2.0",
            target=TargetColorEnum,
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn()
        msg = str(caught[0].message)
        assert "old_color" in msg
        assert "tests.collection_targets.TargetColorEnum" in msg

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

    def test_writes_mutate_target_when_set(self) -> None:
        """Write operations mutate the active target object when a target is configured."""
        source = {"k": 1}
        target = {"k": 2}
        proxy = _DeprecatedProxy(obj=source, name="x", deprecated_in="1.0", remove_in="2.0", target=target, stream=None)
        proxy["k"] = 9
        assert source["k"] == 1
        assert target["k"] == 9


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

    def test_len_uses_target_when_set(self) -> None:
        """__len__ reflects the target when a target is configured."""
        proxy = _DeprecatedProxy(
            obj=[1],
            target=[1, 2, 3],
            name="x",
            deprecated_in="1.0",
            remove_in="2.0",
            stream=None,
        )
        assert len(proxy) == 3

    def test_contains_no_warn(self) -> None:
        """__contains__ delegates without warning."""
        proxy = _DeprecatedProxy(obj={"k": 1}, name="x", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert "k" in proxy
            assert "z" not in proxy
        assert not caught

    def test_contains_uses_target_when_set(self) -> None:
        """__contains__ reflects the target when a target is configured."""
        proxy = _DeprecatedProxy(
            obj={"old": 1},
            target={"new": 2},
            name="x",
            deprecated_in="1.0",
            remove_in="2.0",
            stream=None,
        )
        assert "new" in proxy
        assert "old" not in proxy

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
        with pytest.warns(FutureWarning, match=r"The `x` was deprecated since v1\.0"):
            val = proxy["k"]
        assert val == 99

    def test_getattr_warns(self) -> None:
        """__getattr__ emits warning."""
        proxy = _DeprecatedProxy(obj={"k": 1}, name="x", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning, match=r"The `x` was deprecated since v1\.0"):
            method = proxy.get
        assert callable(method)

    def test_iter_warns(self) -> None:
        """__iter__ emits warning."""
        proxy = _DeprecatedProxy(obj={"a": 1, "b": 2}, name="x", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning, match=r"The `x` was deprecated since v1\.0"):
            keys = list(proxy)
        assert set(keys) == {"a", "b"}

    def test_call_warns_and_invokes(self) -> None:
        """__call__ emits warning and invokes the active object."""
        proxy = _DeprecatedProxy(obj=lambda x: x * 2, name="fn", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning, match=r"The `fn` was deprecated since v1\.0"):
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
        name = object.__getattribute__(WarnOnlyColorEnum, "__deprecated__")["name"]
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

    @pytest.mark.parametrize(
        "action",
        [
            lambda: DeprecatedColorEnum(1),
            lambda: DeprecatedColorEnum.RED,
            lambda: DeprecatedColorEnum["RED"],
        ],
        ids=["call", "attribute", "item"],
    )
    def test_warns_and_redirects_to_target_member(self, action: Callable[[], object]) -> None:
        """Deprecated Enum call, attribute, and item access should warn and resolve to target member."""
        with pytest.warns(
            FutureWarning,
            match=(
                r"The `DeprecatedColorEnum` was deprecated since v1\.0 in favor of "
                r"`tests\.collection_targets\.TargetColorEnum`"
            ),
        ):
            result = action()
        assert result is TargetColorEnum.RED

    def test_no_target_warns_and_reads_source(self) -> None:
        """With target=None, deprecated Enum should warn and return members from the original source Enum."""
        with pytest.warns(FutureWarning, match=r"The `WarnOnlyColorEnum` was deprecated since v1\.0"):
            val = WarnOnlyColorEnum.A
        assert val.value == "a"

    def test_returns_deprecated_proxy(self) -> None:
        """Decorator should return proxy objects so decorated classes expose warning-forwarding behavior."""
        assert isinstance(DeprecatedColorEnum, _DeprecatedProxy)
        assert isinstance(WarnOnlyColorEnum, _DeprecatedProxy)


class TestDecoratorDataclass:
    """@deprecated_class applied to dataclasses."""

    def test_instantiation_warns_and_redirects(self) -> None:
        """Constructing deprecated dataclass should warn and instantiate the replacement dataclass type."""
        with pytest.warns(
            FutureWarning,
            match=(
                r"The `DeprecatedColorDataClass` was deprecated since v1\.0 in favor of "
                r"`tests\.collection_targets\.NewDataClass`"
            ),
        ):
            obj = DeprecatedColorDataClass(label="test", total=5)
        assert isinstance(obj, NewDataClass)
        assert obj.label == "test"
        assert obj.total == 5


class TestArgsMapping:
    """args_mapping remaps or drops kwargs when the proxy is called."""

    @pytest.mark.parametrize(
        ("kwargs", "expected_label", "expected_total"),
        [
            ({"name": "hello", "total": 7}, "hello", 7),
            ({"name": "world", "count": 3}, "world", 3),
        ],
    )
    def test_remap_kwargs(self, kwargs: dict[str, object], expected_label: str, expected_total: int) -> None:
        """Deprecated dataclass calls should remap renamed kwargs and preserve explicit non-remapped kwargs."""
        with pytest.warns(
            FutureWarning,
            match=(
                r"The `MappedDataClass` was deprecated since v1\.0 in favor of "
                r"`tests\.collection_targets\.NewDataClass`"
            ),
        ):
            result = MappedDataClass(**kwargs)  # type: ignore[arg-type]
        assert isinstance(result, NewDataClass)
        assert result.label == expected_label
        assert result.total == expected_total

    def test_drop_kwarg(self) -> None:
        """Args mapped to None should be dropped before forwarding, while mapped kwargs still reach target."""
        with pytest.warns(
            FutureWarning,
            match=(
                r"The `MappedDropArgDataClass` was deprecated since v1\.0 in favor of "
                r"`tests\.collection_targets\.NewDataClass`"
            ),
        ):
            result = MappedDropArgDataClass(name="x", legacy_flag=True)  # type: ignore[call-arg]
        assert isinstance(result, NewDataClass)
        assert result.label == "x"

    def test_args_mapping_stored_in_proxy(self) -> None:
        """Proxy should retain args_mapping so audit and introspection can verify remapping behavior."""
        mapping = object.__getattribute__(MappedDataClass, "__deprecated__")["args_mapping"]
        assert mapping == {"name": "label", "count": "total"}

    def test_enum_remap_kwarg(self) -> None:
        """Enum wrappers should apply args_mapping so old constructor kwargs still resolve target members."""
        with pytest.warns(
            FutureWarning,
            match=(
                r"The `MappedColorEnum` was deprecated since v1\.0 in favor of "
                r"`tests\.collection_targets\.TargetColorEnum`"
            ),
        ):
            result = MappedColorEnum(val=1)  # type: ignore[call-arg]
        assert result is TargetColorEnum.RED


class TestContainerProtocolWithTarget:
    """Container protocol behaviour when a target is set on the proxy.

    TODO: The source-vs-target behaviour is intentional for now but must be
    pinned so it is not silently changed. When target is set:
    - __len__ and __contains__ use _get_active() (typically the target)
    - __bool__, __iter__, __getitem__, __getattr__, __call__ also use _get_active() (typically the target)
    """

    @pytest.mark.skip(reason="TODO: pin target-based behaviour for __len__ with target set (uses _get_active())")
    def test_len_reads_from_target_when_set(self) -> None:
        """len(proxy) reflects the active object (target when set), not the original source."""

    @pytest.mark.skip(reason="TODO: pin target-based behaviour for __contains__ with target set (uses _get_active())")
    def test_contains_reads_from_target_when_set(self) -> None:
        """Membership test uses the active object (target when set), not the original source."""

    @pytest.mark.skip(reason="TODO: pin target-based behaviour for __bool__ with target set (uses _get_active())")
    def test_bool_reads_from_target_when_set(self) -> None:
        """bool(proxy) evaluates the truthiness of the active object (target when set), not the original source."""


class TestHashOnUnhashableType:
    """hash() behaviour for proxies wrapping unhashable objects."""

    @pytest.mark.skip(reason="TODO: document hash(proxy) raises TypeError for unhashable source (e.g. dict)")
    def test_hash_raises_for_unhashable_source(self) -> None:
        """hash(proxy) raises TypeError when the wrapped object is unhashable (e.g. dict).

        Current behaviour: propagates the TypeError from the underlying hash() call with
        no additional context. Pin this so we know if the behaviour changes.
        """


class TestDeprecatedClassReadOnly:
    """Constraints on deprecated_class — unsupported parameters."""

    @pytest.mark.skip(reason="TODO: assert deprecated_class rejects read_only=True with TypeError")
    def test_read_only_raises_type_error(self) -> None:
        """deprecated_class does not accept read_only; passing it must raise TypeError.

        deprecated_instance supports read_only; deprecated_class does not. This test
        makes the limitation explicit so it is not accidentally introduced later.
        """
