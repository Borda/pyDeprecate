"""Unit tests for _DeprecatedProxy internals and deprecated_class decorator behaviour."""

import inspect
import warnings
from collections.abc import Callable
from typing import Any, cast

import pytest

from deprecate._types import TargetMode
from deprecate.proxy import _DeprecatedProxy, deprecated_class, deprecated_instance
from tests.collection_deprecate import (
    DeprecatedAttrsNotifyOnly,
    DeprecatedAttrsPalette,
    DeprecatedAttrsPaletteEnum,
    DeprecatedAttrsPaletteWithStream,
    DeprecatedColorDataClass,
    DeprecatedColorEnum,
    MappedColorEnum,
    MappedDataClass,
    MappedDropArgDataClass,
    ProxyArgsRemapAuto,
    ProxyArgsRemapForArgWarnMessage,
    ProxyCallableWithArgsMapping,
    ProxyClassWithArgsExtra,
    WarnOnlyColorEnum,
    pep702_proxy_stacked,
)
from tests.collection_targets import (
    NewDataClass,
    TargetColorEnum,
    TargetPalette,
    TargetPaletteEnum,
    TargetWithInjected,
    _Pep702ProxyTarget,
)


class TestProxyInit:
    """Internal state initialisation for _DeprecatedProxy instances."""

    def test_internal_state_stored_correctly(self) -> None:
        """Constructor stores runtime config in ``__config`` and metadata in ``__deprecated__``."""
        obj = {"a": 1}
        proxy = _DeprecatedProxy(obj=obj, name="x", deprecated_in="1.0", remove_in="2.0", num_warns=3, stream=None)
        cfg = object.__getattribute__(proxy, "_DeprecatedProxy__config")
        assert cfg.obj is obj
        assert cfg.num_warns == 3
        assert cfg.stream is None
        assert cfg.read_only is False
        assert cfg.warned == 0
        meta = object.__getattribute__(proxy, "__deprecated__")
        assert meta.name == "x"
        assert meta.deprecated_in == "1.0"
        assert meta.remove_in == "2.0"
        assert meta.target is None
        assert meta.args_mapping is None


class TestProxyWarnBehavior:
    """Warning count and message logic."""

    def test_num_warns_zero_never_warns(self) -> None:
        """``num_warns=0`` means never warn."""
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
        assert object.__getattribute__(proxy, "_DeprecatedProxy__config").warned == 2

    def test_warn_stops_after_limit(self) -> None:
        """Warnings stop once num_warns threshold is reached."""
        proxy = _DeprecatedProxy(obj={}, name="x", deprecated_in="1.0", remove_in="2.0", num_warns=2)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            for _ in range(5):
                proxy._warn()
        assert len(caught) == 2

    def test_warn_no_stream(self) -> None:
        """``stream=None`` suppresses all warnings."""
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
        """Default stream emits FutureWarning attributed to the caller's frame."""
        proxy = _DeprecatedProxy(obj={"k": 1}, name="x", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            # Trigger via subscript access — exercises the realistic accessor path
            # (``__getitem__ → _warn → stream``) that the stacklevel fix targets.
            _ = proxy["k"]
        assert caught[0].category is FutureWarning
        # ``_DeprecatedProxy._warn`` forwards ``stacklevel=_DEFAULT_STACKLEVEL_TO_CALLER`` to ``stream``
        # so the warning is attributed to this test file rather than ``proxy.py``.
        assert caught[0].filename.endswith("test_proxy.py")


class TestProxyTemplateMgs:
    """``template_mgs`` overrides the built-in warning-message templates on proxies.

    Mirrors the parity that ``@deprecated`` already offers, so that switching from ``@deprecated`` to
    ``deprecated_class``/``deprecated_instance`` does not cause the loss of custom warning-message control.

    """

    def test_custom_template_used_in_warning_message_no_target(self) -> None:
        """``template_mgs`` overrides the no-target template when no target is set."""
        proxy = _DeprecatedProxy(
            obj={},
            name="legacy_obj",
            deprecated_in="1.0",
            remove_in="2.0",
            template_mgs="CUSTOM %(source_name)s deprecated_in=%(deprecated_in)s",
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn()
        msg = str(caught[0].message)
        assert msg == "CUSTOM legacy_obj deprecated_in=1.0"

    def test_custom_template_used_in_warning_message_callable_target(self) -> None:
        """``template_mgs`` overrides the callable-target template, exposing target placeholders."""

        def replacement() -> None:
            """Replacement target used to confirm ``target_path`` substitution."""

        proxy = _DeprecatedProxy(
            obj={},
            name="legacy_obj",
            deprecated_in="1.0",
            remove_in="2.0",
            target=replacement,
            template_mgs="OVERRIDE %(source_name)s -> %(target_name)s",
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn()
        msg = str(caught[0].message)
        assert "OVERRIDE" in msg
        assert "legacy_obj" in msg
        assert "replacement" in msg

    def test_custom_template_used_in_per_argument_warning(self) -> None:
        """``template_mgs`` overrides ``TEMPLATE_WARNING_ARGUMENTS`` for per-argument warnings."""
        proxy = _DeprecatedProxy(
            obj=lambda **_: None,
            name="LegacyConfig",
            deprecated_in="1.0",
            remove_in="2.0",
            args_mapping={"old_key": "new_key"},
            template_mgs="ARGS-OVERRIDE %(source_name)s :: %(argument_map)s",
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn(arg_name="old_key")
        msg = str(caught[0].message)
        assert msg.startswith("ARGS-OVERRIDE LegacyConfig :: ")
        assert "`old_key` -> `new_key`" in msg

    def test_default_template_used_when_template_mgs_is_none(self) -> None:
        """Without ``template_mgs`` the built-in default template is rendered verbatim."""
        proxy = _DeprecatedProxy(obj={}, name="legacy_obj", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn()
        msg = str(caught[0].message)
        # Default no-target template begins with the built-in prefix and includes both versions.
        assert "The `legacy_obj` was deprecated since v1.0" in msg
        assert "It will be removed in v2.0" in msg

    def test_template_mgs_stored_on_deprecation_config(self) -> None:
        """``template_mgs`` is recorded on ``DeprecationConfig`` for audit/introspection."""
        proxy = _DeprecatedProxy(
            obj={},
            name="legacy_obj",
            deprecated_in="1.0",
            remove_in="2.0",
            template_mgs="CUSTOM %(source_name)s",
        )
        dep = object.__getattribute__(proxy, "__deprecated__")
        assert dep.template_mgs == "CUSTOM %(source_name)s"

    def test_deprecated_class_custom_template_applied(self) -> None:
        """``deprecated_class(template_mgs=...)`` propagates the override to ``_warn``."""

        class NewCfg:
            """Replacement class used as forwarding target."""

        @deprecated_class(
            target=NewCfg,
            deprecated_in="1.0",
            remove_in="2.0",
            template_mgs="OVERRIDE %(source_name)s -> %(target_name)s",
        )
        class OldCfg:
            """Source class wrapped by the proxy."""

        with pytest.warns(FutureWarning) as caught:
            OldCfg()
        # Decorator-form proxy still warns for callable target — assert override is used.
        msg = str(caught[0].message)
        assert "OVERRIDE" in msg
        assert "OldCfg" in msg
        assert "NewCfg" in msg

    def test_deprecated_instance_custom_template_applied(self) -> None:
        """``deprecated_instance(template_mgs=...)`` propagates the override to ``_warn``."""
        proxy = deprecated_instance(
            {"k": 1},
            name="legacy_cfg",
            deprecated_in="1.0",
            remove_in="2.0",
            template_mgs="OVERRIDE %(source_name)s",
        )
        with pytest.warns(FutureWarning) as caught:
            _ = proxy["k"]
        msg = str(caught[0].message)
        assert msg == "OVERRIDE legacy_cfg"

    def test_deprecated_class_default_template_when_template_mgs_omitted(self) -> None:
        """Without ``template_mgs`` ``deprecated_class`` keeps the built-in template."""

        class NewCfg2:
            """Replacement class used as forwarding target."""

        @deprecated_class(target=NewCfg2, deprecated_in="1.0", remove_in="2.0")
        class OldCfg2:
            """Source class wrapped by the proxy."""

        with pytest.warns(FutureWarning) as caught:
            OldCfg2()
        msg = str(caught[0].message)
        # Built-in callable-target template prefix.
        assert "The `OldCfg2` was deprecated since v1.0 in favor of" in msg


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

    @pytest.mark.parametrize(
        "operation",
        [
            pytest.param(lambda p: p.__setitem__("k", 2), id="setitem"),
            pytest.param(lambda p: p.__delitem__("k"), id="delitem"),
            pytest.param(lambda p: setattr(p, "some_attr", "value"), id="setattr"),
            pytest.param(lambda p: delattr(p, "some_attr"), id="delattr"),
        ],
    )
    def test_mutation_raises_when_read_only(self, operation: Callable) -> None:
        """All write operations raise AttributeError in read_only mode."""
        proxy = _DeprecatedProxy(
            obj={"k": 1}, name="d", deprecated_in="1.0", remove_in="2.0", read_only=True, stream=None
        )
        with pytest.raises(AttributeError, match="read-only"):
            operation(proxy)

    def test_setitem_forwards_to_source(self) -> None:
        """``__setitem__`` mutates the source object when not read-only, without emitting a warning."""
        inner = {"k": 1}
        proxy = _DeprecatedProxy(obj=inner, name="d", deprecated_in="1.0", remove_in="2.0", stream=None)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy["k"] = 99
        assert inner["k"] == 99
        assert not caught

    def test_delitem_removes_from_source(self) -> None:
        """``__delitem__`` removes the key from the source object when not read-only."""
        inner = {"k": 1, "m": 2}
        proxy = _DeprecatedProxy(obj=inner, name="d", deprecated_in="1.0", remove_in="2.0", stream=None)
        del proxy["k"]
        assert "k" not in inner
        assert "m" in inner

    def test_custom_mutator_bypasses_read_only_guard(self) -> None:
        """Custom method names not in the blocked set pass through ``read_only=True`` (known limitation)."""

        class RegistryWithCustomMutator:
            def __init__(self) -> None:
                self.items: list[str] = []

            def register(self, item: str) -> None:
                self.items.append(item)

        obj = RegistryWithCustomMutator()
        proxy = deprecated_instance(obj, deprecated_in="1.0", remove_in="2.0", read_only=True, stream=None)
        # `register` is not in the blocked set — it must NOT raise
        proxy.register("x")
        assert obj.items == ["x"]


class TestProxyGetActive:
    """Active object selection: source vs target."""

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
        """``__repr__`` delegates to the source without warning."""
        inner = [1, 2, 3]
        proxy = _DeprecatedProxy(obj=inner, name="x", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            r = repr(proxy)
        assert r == repr(inner)
        assert not caught

    def test_str_no_warn(self) -> None:
        """``__str__`` delegates without warning."""
        inner = {"a": 1}
        proxy = _DeprecatedProxy(obj=inner, name="x", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert str(proxy) == str(inner)
        assert not caught

    def test_bool_no_warn(self) -> None:
        """``__bool__`` delegates without warning."""
        proxy_t = _DeprecatedProxy(obj=[1], name="x", deprecated_in="1.0", remove_in="2.0")
        proxy_f = _DeprecatedProxy(obj=[], name="x", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert bool(proxy_t)
            assert not bool(proxy_f)
        assert not caught

    def test_len_no_warn(self) -> None:
        """``__len__`` delegates without warning."""
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

    def test_hash_matches_inner(self) -> None:
        """``hash(proxy)`` equals hash(wrapped object) for hashable types, without emitting a warning."""
        inner = (1, 2, 3)
        proxy = _DeprecatedProxy(obj=inner, name="t", deprecated_in="1.0", remove_in="2.0", stream=None)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            h = hash(proxy)
        assert h == hash(inner)
        assert not caught


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
        """__iter__ emits warning and yields all elements of the wrapped iterable."""
        proxy = _DeprecatedProxy(obj=[10, 20, 30], name="x", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning, match=r"The `x` was deprecated since v1\.0"):
            items = list(proxy)
        assert items == [10, 20, 30]

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
        name = object.__getattribute__(WarnOnlyColorEnum, "__deprecated__").name
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

    @pytest.mark.parametrize(
        ("raw_target", "warning_category", "warning_message"),
        [
            (
                True,
                FutureWarning,
                "`target=True` without `args_mapping` resolves to `TargetMode.NOTIFY`"
                " (warns on every access). Will be `TypeError` in `v1.0`.",
            ),
            (
                False,
                UserWarning,
                "`target=False` is not valid for `deprecated_class()`. Will be `TypeError` in `v1.0`.",
            ),
        ],
    )
    def test_boolean_target_is_normalized_and_class_access_still_works(
        self,
        raw_target: bool,
        warning_category: type[Warning],
        warning_message: str,
    ) -> None:
        """Legacy boolean targets are normalized before proxy metadata and access use them."""
        with pytest.warns(warning_category) as caught:

            @deprecated_class(
                target=raw_target,
                deprecated_in="1.0",
                remove_in="2.0",
                stream=None,
            )
            class OldClass:
                def method(self) -> str:
                    return "ok"

        assert len(caught) == 1
        assert str(caught[0].message) == warning_message

        dep = object.__getattribute__(OldClass, "__deprecated__")
        assert dep.target is TargetMode.NOTIFY

        obj = OldClass()
        assert obj.method() == "ok"

    def test_true_with_args_mapping_resolves_to_args_remap(self) -> None:
        """``target=True`` + non-empty args_mapping resolves to ARGS_REMAP with FutureWarning."""
        with pytest.warns(FutureWarning, match="TargetMode.ARGS_REMAP") as caught:

            @deprecated_class(
                target=True,
                deprecated_in="1.0",
                remove_in="2.0",
                args_mapping={"old_attr": "new_attr"},
                stream=None,
            )
            class OldClass:
                def method(self) -> str:
                    return "ok"

        assert len(caught) == 1
        dep = object.__getattribute__(OldClass, "__deprecated__")
        assert dep.target is TargetMode.ARGS_REMAP


class TestDecoratorEnum:
    """@deprecated_class applied to Enum classes."""

    @pytest.mark.parametrize(
        "action",
        [
            pytest.param(lambda: DeprecatedColorEnum(1), id="call"),
            pytest.param(lambda: DeprecatedColorEnum.RED, id="attribute"),
            pytest.param(lambda: DeprecatedColorEnum["RED"], id="item"),
        ],
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
        """With ``target=None``, deprecated Enum should warn and return members from the original source Enum."""
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
        """Deprecated dataclass calls should remap renamed kwargs and preserve explicit non-remapped kwargs.

        When an old kwarg name is passed (e.g. ``name`` mapped to ``label``), the proxy emits the per-argument
        deprecation template (``old -> new``) — matching the decorator's argument-deprecation form.

        """
        with pytest.warns(
            FutureWarning,
            match=r"`MappedDataClass` uses deprecated arguments: `name` -> `label`",
        ):
            result = MappedDataClass(**kwargs)  # type: ignore[arg-type]
        assert isinstance(result, NewDataClass)
        assert result.label == expected_label
        assert result.total == expected_total

    def test_drop_kwarg(self) -> None:
        """Args mapped to None should be dropped before forwarding, while mapped kwargs still reach target.

        Old kwarg names (``name`` and the dropped ``legacy_flag``) emit per-argument deprecation messages;
        ``legacy_flag`` is dropped before forwarding.

        """
        with pytest.warns(
            FutureWarning,
            match=r"`MappedDropArgDataClass` uses deprecated arguments: `legacy_flag` -> `None`",
        ):
            result = MappedDropArgDataClass(name="x", legacy_flag=True)  # type: ignore[call-arg]
        assert isinstance(result, NewDataClass)
        assert result.label == "x"

    def test_args_mapping_stored_in_proxy(self) -> None:
        """Proxy should retain args_mapping so audit and introspection can verify remapping behavior."""
        mapping = object.__getattribute__(MappedDataClass, "__deprecated__").args_mapping
        assert mapping == {"name": "label", "count": "total"}

    def test_enum_remap_kwarg(self) -> None:
        """Enum wrappers should apply args_mapping so old constructor kwargs still resolve target members.

        Passing the old kwarg name (``val``) triggers the per-argument warning template (``val -> value``), matching the
        decorator's argument-deprecation form.

        """
        with pytest.warns(
            FutureWarning,
            match=r"`MappedColorEnum` uses deprecated arguments: `val` -> `value`",
        ):
            result = MappedColorEnum(val=1)  # type: ignore[call-arg]
        assert result is TargetColorEnum.RED

    def test_target_mode_args_remap_emits_per_argument_warning(self) -> None:
        """TargetMode.ARGS_REMAP path emits old -> new arg names in the warning message."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ProxyArgsRemapForArgWarnMessage(old_key=5)
        assert len(caught) >= 1
        msg = str(caught[0].message)
        assert "old_key" in msg
        assert "new_key" in msg
        assert "->" in msg


class TestArgsExtra:
    """args_extra injects additional kwargs into deprecated_class() and deprecated_instance() forwarded calls."""

    def test_deprecated_class_accepts_args_extra_kwarg(self) -> None:
        """deprecated_class accepts args_extra without raising TypeError."""
        assert isinstance(ProxyClassWithArgsExtra, _DeprecatedProxy)

    def test_args_extra_values_appear_in_forwarded_constructor_call(self) -> None:
        """Kwargs from args_extra are merged into the forwarded constructor call."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            instance = ProxyClassWithArgsExtra(new_key=7)
        assert instance.new_key == 7
        assert instance.injected == "from-extra"

    def test_args_extra_merged_after_args_mapping_rename(self) -> None:
        """args_extra is applied after args_mapping renames kwargs."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            proxy = deprecated_class(
                target=TargetWithInjected,
                deprecated_in="1.2",
                remove_in="2.0",
                args_mapping={"old_key": "new_key"},
                args_extra={"injected": "extra"},
                num_warns=-1,
            )(TargetWithInjected)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            instance = proxy(old_key=11)
        assert instance.new_key == 11
        assert instance.injected == "extra"

    def test_deprecated_instance_accepts_args_extra_and_forwards(self) -> None:
        """deprecated_instance also accepts args_extra and merges it into forwarded calls."""
        proxy = deprecated_instance(
            TargetWithInjected,
            name="LegacyTarget",
            deprecated_in="1.2",
            remove_in="2.0",
            args_extra={"injected": "via-instance"},
            stream=None,
        )
        instance = proxy(new_key=3)
        assert instance.new_key == 3
        assert instance.injected == "via-instance"


class TestContainerProtocolWithTarget:
    """Container protocol behaviour when a target is set on the proxy.

    Pins the source-vs-target routing so it is not silently changed. __len__, __contains__, and __bool__ all use
    _get_active() (the target when set). See TestProxyNoWarnMethods for the no-target variants of __len__ and
    __contains__.

    """

    def test_bool_reads_from_target_when_set(self) -> None:
        """``bool(proxy)`` evaluates the active object (target when set), not the original source."""
        proxy = _DeprecatedProxy(
            obj=[1, 2, 3],  # truthy source
            target=[],  # falsy target
            name="x",
            deprecated_in="1.0",
            remove_in="2.0",
            stream=None,
        )
        assert not bool(proxy)


class TestHashOnUnhashableType:
    """``hash()`` behaviour for proxies wrapping unhashable objects."""

    def test_hash_raises_for_unhashable_source(self) -> None:
        """``hash(proxy)`` raises TypeError when the wrapped object is unhashable (e.g. dict).

        Propagates TypeError from the underlying hash() call with no additional context.

        """
        proxy = _DeprecatedProxy(obj={"k": 1}, name="d", deprecated_in="1.0", remove_in="2.0", stream=None)
        with pytest.raises(TypeError):
            hash(proxy)


class TestDeprecatedClassReadOnly:
    """Constraints on deprecated_class — unsupported parameters."""

    def test_read_only_not_in_signature(self) -> None:
        """deprecated_class does not expose read_only in its public API."""
        assert "read_only" not in inspect.signature(deprecated_class).parameters


class TestEmptyVersionGuard:
    """Decoration-time UserWarning when both ``deprecated_in`` and ``remove_in`` are empty.

    Both ``deprecated_class()`` and ``deprecated_instance()`` warn at construction time when neither version string is
    provided, because the rendered notice would otherwise contain empty ``v`` placeholders. ``stream=None`` suppresses
    the guard so callers that opt out of warnings entirely remain silent.

    """

    def test_deprecated_class_empty_versions_warns(self) -> None:
        """``@deprecated_class()`` with empty versions emits UserWarning at decoration time."""
        with pytest.warns(UserWarning, match=r"no `deprecated_in` set"):

            @deprecated_class()
            class OldEmptyVersions:
                """Source class with no version metadata supplied."""

                pass

    def test_deprecated_class_empty_versions_stream_none_silent(self) -> None:
        """``@deprecated_class(stream=None)`` suppresses the empty-versions UserWarning."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            @deprecated_class(stream=None)
            class OldEmptyVersionsSilent:
                """Source class with ``stream=None`` — guard must stay silent."""

                pass

        assert not caught

    def test_deprecated_instance_empty_versions_warns(self) -> None:
        """``deprecated_instance()`` with empty versions emits UserWarning at instantiation time."""
        with pytest.warns(UserWarning, match=r"no `deprecated_in` set"):
            deprecated_instance({"k": 1})

    def test_deprecated_instance_empty_versions_stream_none_silent(self) -> None:
        """``deprecated_instance(stream=None)`` suppresses the empty-versions UserWarning."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            deprecated_instance({"k": 1}, stream=None)
        assert not caught


class TestDeprecatedInstance:
    """deprecated_instance() wraps any Python object with transparent deprecation warnings."""

    def test_returns_deprecated_proxy(self) -> None:
        """deprecated_instance always returns a _DeprecatedProxy instance."""
        proxy = deprecated_instance({}, deprecated_in="1.0", remove_in="2.0", stream=None)
        assert isinstance(proxy, _DeprecatedProxy)

    def test_name_auto_inferred_from_type(self) -> None:
        """Without name=, proxy name defaults to type(obj).__name__."""
        proxy = deprecated_instance({"k": 1}, deprecated_in="1.0", remove_in="2.0", stream=None)
        dep = object.__getattribute__(proxy, "__deprecated__")
        assert dep.name == "dict"

    def test_name_auto_inferred_for_list(self) -> None:
        """Type name inference works for any built-in type."""
        proxy = deprecated_instance([1, 2], deprecated_in="1.0", remove_in="2.0", stream=None)
        dep = object.__getattribute__(proxy, "__deprecated__")
        assert dep.name == "list"

    def test_name_explicitly_set(self) -> None:
        """Explicit name= overrides the type-based inference."""
        proxy = deprecated_instance({}, name="my_config", deprecated_in="1.0", remove_in="2.0", stream=None)
        dep = object.__getattribute__(proxy, "__deprecated__")
        assert dep.name == "my_config"

    def test_version_metadata_stored(self) -> None:
        """deprecated_in and remove_in are stored verbatim in DeprecationConfig."""
        proxy = deprecated_instance([], deprecated_in="2.0", remove_in="3.5", stream=None)
        dep = object.__getattribute__(proxy, "__deprecated__")
        assert dep.deprecated_in == "2.0"
        assert dep.remove_in == "3.5"

    def test_warns_once_by_default(self) -> None:
        """Default ``num_warns=1`` means only the first access emits a warning.

        This is specific to deprecated_instance() — unlike _DeprecatedProxy which requires an explicit num_warns,
        deprecated_instance() defaults to num_warns=1.

        """
        proxy = deprecated_instance({"k": 1}, deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = proxy["k"]
            _ = proxy["k"]
        assert len(caught) == 1

    def test_stream_none_suppresses_on_item_access(self) -> None:
        """``stream=None`` suppresses warnings even when items are accessed via __getitem__."""
        proxy = deprecated_instance({"k": "v"}, name="x", deprecated_in="1.0", remove_in="2.0", stream=None)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = proxy["k"]
        assert not caught


class TestTypeProtocol:
    """Tests for __instancecheck__ and __subclasscheck__ on _DeprecatedProxy."""

    def test_isinstance_delegates_to_target_class(self) -> None:
        """``isinstance(x, proxy)`` returns True when x is an instance of the target class."""

        class NewConfig:
            pass

        @deprecated_class(target=NewConfig, deprecated_in="1.0", remove_in="2.0", stream=None)
        class OldConfig:
            pass

        obj = NewConfig()
        assert isinstance(obj, OldConfig)

    def test_isinstance_returns_false_for_unrelated_type(self) -> None:
        """``isinstance(x, proxy)`` returns False when x is not an instance of the target."""

        class NewConfig:
            pass

        @deprecated_class(target=NewConfig, deprecated_in="1.0", remove_in="2.0", stream=None)
        class OldConfig:
            pass

        assert not isinstance(42, OldConfig)

    def test_isinstance_no_warning_emitted(self) -> None:
        """``isinstance(x, proxy)`` is a structural check — must not consume the warning budget."""

        class Target:
            pass

        proxy = _DeprecatedProxy(obj=Target, name="old", deprecated_in="1.0", remove_in="2.0", num_warns=1)
        obj = Target()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            isinstance(obj, cast(Any, proxy))

        assert not caught  # no warning from isinstance
        with pytest.warns(FutureWarning):
            proxy()  # warning budget remains untouched

    def test_issubclass_delegates_to_target_class(self) -> None:
        """``issubclass(Sub, proxy)`` returns True when Sub is a subclass of the target."""

        class Base:
            pass

        class Sub(Base):
            pass

        @deprecated_class(target=Base, deprecated_in="1.0", remove_in="2.0", stream=None)
        class OldBase:
            pass

        assert issubclass(Sub, OldBase)

    def test_issubclass_respects_metaclass_semantics(self) -> None:
        """Issubclass uses the target metaclass logic (including virtual subclasses)."""
        import abc

        class AbstractBase(metaclass=abc.ABCMeta):
            pass

        class VirtualSubclass:
            pass

        AbstractBase.register(VirtualSubclass)

        @deprecated_class(target=AbstractBase, deprecated_in="1.0", remove_in="2.0", stream=None)
        class OldAbstractBase:
            pass

        assert issubclass(VirtualSubclass, OldAbstractBase)

    def test_issubclass_no_warning_emitted(self) -> None:
        """``issubclass(Sub, proxy)`` is structural and must not consume warning budget."""

        class Base:
            pass

        class Sub(Base):
            pass

        proxy = _DeprecatedProxy(obj=Base, name="old_cls", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            issubclass(Sub, cast(Any, proxy))
        assert not caught
        with pytest.warns(FutureWarning):
            proxy()  # warning budget remains untouched

    def test_isinstance_returns_false_for_non_type_active(self) -> None:
        """``isinstance(x, proxy)`` returns False when the active object is not a type."""
        proxy = _DeprecatedProxy(obj={"key": "val"}, name="old_cfg", deprecated_in="1.0", remove_in="2.0")
        assert not isinstance(42, cast(Any, proxy))


class TestProxyArgsMappingBehavior:
    """Conditional warning behavior when args_mapping is provided on a proxy."""

    def test_auto_args_remap_warns_on_old_arg(self) -> None:
        """Proxy with args_mapping and no explicit target warns when old arg name is used."""
        with pytest.warns(FutureWarning):
            ProxyArgsRemapAuto(old_key=1)

    def test_auto_args_remap_silent_on_new_arg(self) -> None:
        """Proxy with args_mapping and no explicit target does NOT warn when new arg name is used."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ProxyArgsRemapAuto(new_key=1)
        assert not caught

    def test_callable_target_with_args_mapping_warns_on_old_arg(self) -> None:
        """Proxy forwarding to callable target warns per old arg name when present in kwargs."""
        with pytest.warns(FutureWarning):
            ProxyCallableWithArgsMapping(old_key=1)

    def test_callable_target_with_args_mapping_warns_on_new_arg(self) -> None:
        """Proxy forwarding to callable target always warns (class deprecated) even with new arg name."""
        from tests.collection_targets import SomeTargetClass

        proxy = _DeprecatedProxy(
            obj=SomeTargetClass,
            name="SomeTargetClass",
            deprecated_in="1.2",
            remove_in="2.0",
            num_warns=-1,
            target=SomeTargetClass,
            args_mapping={"old_key": "new_key"},
        )
        with pytest.warns(FutureWarning):
            proxy(new_key=1)

    def test_notify_with_args_mapping_emits_misconfig_warning(self) -> None:
        """NOTIFY + args_mapping on proxy emits UserWarning at decoration time."""
        with pytest.warns(UserWarning, match="args_mapping"):

            @deprecated_class(
                deprecated_in="1.2",
                remove_in="2.0",
                target=TargetMode.NOTIFY,
                args_mapping={"old_key": "new_key"},
            )
            class _ProxyNotifyWithArgsMapping:
                pass

    def test_args_remap_no_mapping_emits_misconfig_warning(self) -> None:
        """ARGS_REMAP without args_mapping on proxy emits UserWarning at decoration time."""
        with pytest.warns(UserWarning, match="args_mapping"):

            @deprecated_class(
                deprecated_in="1.2",
                remove_in="2.0",
                target=TargetMode.ARGS_REMAP,
            )
            class _ProxyArgsRemapNoMapping:
                pass

    def test_num_warns_respected_per_arg(self) -> None:
        """Per-argument warn budget: second call with same old arg does not warn."""
        proxy = _DeprecatedProxy(
            obj=dict,
            name="budget_test",
            deprecated_in="1.0",
            remove_in="2.0",
            num_warns=1,
            target=TargetMode.ARGS_REMAP,
            args_mapping={"old_key": "new_key"},
        )
        with pytest.warns(FutureWarning):
            proxy(old_key=1)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy(old_key=2)
        assert not caught


class TestPEP702ProxyStackingRegression:
    """Stacking ``typing_extensions.deprecated`` outside ``deprecated_class`` does not break the proxy (B1b).

    PEP 702's ``typing_extensions.deprecated`` assigns ``arg.__deprecated__ = msg`` on the
    object it decorates.  For a ``_DeprecatedProxy`` instance, that assignment routes
    through the proxy's forwarding ``__setattr__`` and lands on the wrapped class — it
    does **not** clobber the proxy's own instance ``__dict__`` slot (which was set via
    ``object.__setattr__`` at construction time and is read back via
    ``object.__getattribute__`` in ``_dep`` and ``__call__``).  These tests guard against
    a future refactor re-introducing a clobber path on the proxy.

    """

    def test_pep702_proxy_stacked_instantiation_does_not_crash(self) -> None:
        """Stacked PEP 702 + ``deprecated_class`` proxy instantiates and dispatches methods."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            instance = pep702_proxy_stacked()
            assert instance.value() == 42

    def test_pep702_proxy_stacked_returns_target_instance(self) -> None:
        """The stacked wrapper produces an instance of the wrapped target class."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            instance = pep702_proxy_stacked()
        assert isinstance(instance, _Pep702ProxyTarget)

    def test_pep702_proxy_stacked_emits_pep702_deprecation_warning(self) -> None:
        """Outer ``typing_extensions.deprecated`` emits its DeprecationWarning on call."""
        with pytest.warns(DeprecationWarning, match="use `Pep702ProxyTarget`"):
            pep702_proxy_stacked()


class TestDeprecatedAttrs:
    """Selective per-attribute deprecation via ``attrs_mapping`` on ``deprecated_class``.

    Each test sets up an isolated proxy state because ``DeprecatedAttrsPalette`` and friends are module-level singletons
    whose per-attribute warning counters (``_cfg.warned_args``) persist across tests once consumed.  The
    :meth:`_reset_proxy_state` autouse fixture clears those counters and re-seeds the canonical attribute values on the
    wrapped target so each test starts from the same baseline.

    """

    @pytest.fixture(autouse=True)
    def _reset_proxy_state(self) -> None:
        """Reset module-level fixture proxies and re-seed the wrapped target attributes between tests.

        Required because ``_ProxyConfig.warned_args`` is *not* covered by the project conftest's ``_state`` reset
        (the conftest targets ``@deprecated`` wrappers only).  Without this reset, a previous test consuming the
        per-attribute budget would silently invalidate any subsequent test that asserts a warning fires.

        """
        for proxy in (
            DeprecatedAttrsPalette,
            DeprecatedAttrsNotifyOnly,
            DeprecatedAttrsPaletteEnum,
            DeprecatedAttrsPaletteWithStream,
        ):
            cfg = object.__getattribute__(proxy, "_DeprecatedProxy__config")
            cfg.warned = 0
            cfg.warned_args.clear()
        # Restore canonical class attributes mutated by previous write-redirect tests.
        TargetPalette.colour = "red"
        TargetPalette.text = "hello"
        TargetPalette.size = 42

    def test_read_redirect_warns_and_returns_canonical(self) -> None:
        """Accessing a deprecated attribute alias warns and transparently returns the canonical value.

        A class has both ``color`` (deprecated alias, misspelling) and ``colour`` (canonical name).  Wrapping the
        class with ``deprecated_class(attrs_mapping={"color": "colour"})`` ensures that reading ``proxy.color``
        emits a ``FutureWarning`` and returns the same value as ``proxy.colour``, so callers using the old name still
        get correct data during the migration window.

        """
        with pytest.warns(FutureWarning, match="color"):
            value = DeprecatedAttrsPaletteWithStream.color  # type: ignore[attr-defined]
        assert value == "red"

    def test_canonical_attr_no_warning(self) -> None:
        """Accessing the canonical (non-deprecated) attribute passes through silently.

        Only attribute names listed as keys in ``attrs_mapping`` trigger warnings.  Callers who have already migrated
        to the new name (e.g. ``colour`` instead of ``color``) must not receive any warning — the deprecation system
        should be invisible to migrated code.

        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            value = DeprecatedAttrsPaletteWithStream.colour  # type: ignore[attr-defined]
        assert value == "red"
        assert not caught

    def test_write_redirect_warns_and_sets_canonical(self) -> None:
        """Writing to a deprecated attribute alias warns and sets the canonical attribute.

        A caller assigns to ``proxy.color = "blue"``.  The proxy emits a FutureWarning and then sets
        ``proxy.colour = "blue"``, so that subsequent reads of the canonical attribute reflect the written value.  This
        prevents split-brain state where the deprecated name and canonical name diverge in storage.

        """
        with pytest.warns(FutureWarning, match="color"):
            DeprecatedAttrsPaletteWithStream.color = "blue"  # type: ignore[attr-defined]
        # Reading the canonical attribute must now show the new value (no warning on canonical reads).
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert DeprecatedAttrsPaletteWithStream.colour == "blue"  # type: ignore[attr-defined]
        assert not caught

    def test_notify_only_warns_no_redirect(self) -> None:
        """A ``None`` redirect value warns on access but does not rename the attribute.

        When ``attrs_mapping={"size": None}``, reading ``proxy.size`` emits a FutureWarning using the no-target
        template ("will be removed in v...") but still returns the value of ``proxy.size`` unchanged.  This is
        equivalent to ``TargetMode.NOTIFY`` for an individual attribute.

        """
        # Use a fresh proxy with stream enabled because DeprecatedAttrsNotifyOnly suppresses warnings.
        proxy = deprecated_class(
            attrs_mapping={"size": None},
            deprecated_in="1.0",
            remove_in="2.0",
        )(TargetPalette)
        with pytest.warns(FutureWarning, match="size") as record:
            value = proxy.size  # type: ignore[attr-defined]
        assert value == 42
        # The no-target template does not include any "in favor of" phrase.
        assert "in favor of" not in str(record[0].message)

    def test_per_attribute_warning_budget_independent(self) -> None:
        """Each deprecated attribute name has its own warning budget under ``num_warns=1``.

        With two entries in ``attrs_mapping`` and ``num_warns=1``, accessing both deprecated names must emit one
        warning each (two warnings total), not just one warning shared across all deprecated names.  This mirrors the
        per-argument budget of ``args_mapping`` deprecation and ensures callers see the migration notice for every
        attribute they use.

        """
        proxy = deprecated_class(
            attrs_mapping={"color": "colour", "txt": "text"},
            deprecated_in="1.0",
            remove_in="2.0",
            num_warns=1,
        )(TargetPalette)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = proxy.color  # type: ignore[attr-defined]
            _ = proxy.txt  # type: ignore[attr-defined]
        future_warnings = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert len(future_warnings) == 2
        # Subsequent access on either name is silent because each budget is exhausted.
        with warnings.catch_warnings(record=True) as caught_after:
            warnings.simplefilter("always")
            _ = proxy.color  # type: ignore[attr-defined]
            _ = proxy.txt  # type: ignore[attr-defined]
        assert not [w for w in caught_after if issubclass(w.category, FutureWarning)]

    def test_unlisted_attr_no_warning(self) -> None:
        """Attributes not listed in ``attrs_mapping`` pass through without any warning.

        The selective mode must not affect attributes that are not deprecated.  A class with
        ``attrs_mapping={"color": "colour"}`` should forward ``proxy.size`` silently — no warning, no redirect — so
        that the addition of ``attrs_mapping`` does not inadvertently alter performance-sensitive or hot-path
        attribute reads on non-deprecated names.

        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            value = DeprecatedAttrsPaletteWithStream.size  # type: ignore[attr-defined]
        assert value == 42
        assert not caught

    def test_enum_member_redirect(self) -> None:
        """Enum member aliases redirect transparently through the proxy.

        An enum ``TargetPaletteEnum`` has ``COLOUR`` as the canonical member name.  A deprecated alias ``COLOR`` is
        registered via ``attrs_mapping={"COLOR": "COLOUR"}``.  Accessing ``DeprecatedAttrsPaletteEnum.COLOR`` must
        warn and return the same object as ``DeprecatedAttrsPaletteEnum.COLOUR``.

        """
        # Use a fresh stream-enabled proxy so we can assert on the FutureWarning.
        proxy = deprecated_class(
            attrs_mapping={"COLOR": "COLOUR"},
            deprecated_in="1.0",
            remove_in="2.0",
        )(TargetPaletteEnum)
        with pytest.warns(FutureWarning, match="COLOR"):
            value = proxy.COLOR  # type: ignore[attr-defined]
        assert value is TargetPaletteEnum.COLOUR

    def test_warning_message_uses_callable_template(self) -> None:
        """Warning message for a redirect attr uses the callable template naming the canonical attr.

        The FutureWarning emitted for ``proxy.color`` (where ``color`` redirects to ``colour``) must contain the
        deprecated name ``color`` and the canonical name ``colour`` in the message text, so callers can immediately
        identify the migration action from the warning alone.

        """
        with pytest.warns(FutureWarning) as record:
            _ = DeprecatedAttrsPaletteWithStream.color  # type: ignore[attr-defined]
        message = str(record[0].message)
        assert "color" in message
        assert "colour" in message
        # The callable template includes the canonical class name and an "in favor of" phrase.
        assert "TargetPalette.colour" in message

    def test_circular_redirect_raises_at_decoration_time(self) -> None:
        """Circular redirect mapping raises ``ValueError`` at decoration time.

        Passing ``attrs_mapping={"a": "b", "b": "a"}`` would create an infinite loop if both names were looked up
        through the proxy.  The decorator must detect this at class-decoration time and raise ``ValueError`` before any
        instance is created, making the misconfiguration visible immediately rather than at access time.

        """
        with pytest.raises(ValueError, match="circular"):

            @deprecated_class(
                attrs_mapping={"a": "b", "b": "a"},
                deprecated_in="1.0",
                remove_in="2.0",
            )
            class _Circular:
                a = 1
                b = 2

    def test_attrs_mapping_stored_in_metadata(self) -> None:
        """``attrs_mapping`` is visible in ``__deprecated__`` metadata for audit tools.

        Audit tooling reads ``DeprecationConfig`` via ``obj.__deprecated__`` to build deprecation tables and enforce
        expiry policies.  The ``attrs_mapping`` dict must be stored in the frozen ``DeprecationConfig`` so that
        ``find_deprecation_wrappers`` can surface it without reading internal proxy state.

        """
        meta = object.__getattribute__(DeprecatedAttrsPalette, "__deprecated__")
        assert meta.attrs_mapping == {"color": "colour", "txt": "text"}
