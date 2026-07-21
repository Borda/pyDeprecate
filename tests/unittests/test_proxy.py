"""Unit tests for _DeprecatedProxy internals and deprecated_class decorator behaviour."""

import abc
import copy
import inspect
import math
import os
import pickle
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass as dc_decorator
from typing import Any, cast

import pytest

from deprecate._types import TargetMode
from deprecate.deprecation import deprecated
from deprecate.proxy import _DeprecatedProxy, deprecated_class, deprecated_instance
from tests.collection_deprecate import (
    DepAutoExpandDC,
    DepAutoExpandInitFalseDC,
    DepAutoExpandOverriddenInitDC,
    DepAutoExpandReqDC,
    DepPositionalOnly,
    DepPositionalOnlyDerived,
    DepPositionalOnlyImmutable,
    DepPositionalOnlyMixed,
    DepPositionalOnlyRequired,
    DeprecatedAttrsExplicitMode,
    DeprecatedAttrsLegacyTrue,
    DeprecatedAttrsNotifyOnly,
    DeprecatedAttrsNotifyOnlyCallableTargetDecorated,
    DeprecatedAttrsNotifyOnlyCallableTargetWrapped,
    DeprecatedAttrsPalette,
    DeprecatedAttrsPaletteAllThree,
    DeprecatedAttrsPaletteCallableTarget,
    DeprecatedAttrsPaletteEnum,
    DeprecatedAttrsPaletteNested,
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
    depr_read_only_attrs_list,
    pep702_proxy_stacked,
)
from tests.collection_targets import (
    AsyncManagedResource,
    AutoExpandDC,
    ColorEnum,
    CombinedAttrsArgsSource,
    CombinedAttrsArgsTarget,
    LegacyBoolAttrsSource,
    ManagedResource,
    NewDataClass,
    Palette,
    PaletteEnum,
    PaletteOld,
    PositionalOnlyTarget,
    SomeTargetClass,
    SubclassableBase,
    WithInjected,
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
        proxy = _DeprecatedProxy(obj={}, name="old_color", deprecated_in="1.0", remove_in="2.0", target=ColorEnum)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn()
        msg = str(caught[0].message)
        assert "old_color" in msg
        assert "tests.collection_targets.ColorEnum" in msg

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

    def test_warn_filename_points_to_caller_on_args_remap_path(self) -> None:
        """_proxy_call_args_remap path attributes FutureWarning to the caller's frame.

        When a proxy with TargetMode.ARGS_REMAP receives a deprecated kwarg, it routes
        through ``_proxy_call_args_remap``. The extra call frame introduced by the helper
        must be compensated by ``_extra_frames=1`` so the warning attributes to this test
        file rather than ``proxy.py``.

        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ProxyArgsRemapForArgWarnMessage(old_key=5)  # deprecated kwarg via ARGS_REMAP
        future_warnings = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert future_warnings, "Expected FutureWarning from ARGS_REMAP path"
        assert future_warnings[0].filename.endswith("test_proxy.py")

    def test_warn_filename_points_to_caller_on_callable_with_mapping_path(self) -> None:
        """_proxy_call_callable_with_mapping path attributes FutureWarning to the caller's frame.

        When a proxy with a callable target and args_mapping receives a deprecated kwarg,
        it routes through ``_proxy_call_callable_with_mapping``. The extra call frame must
        be compensated by ``_extra_frames=1`` so the warning attributes to this test file
        rather than ``proxy.py``.

        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            MappedColorEnum(val=1)  # type: ignore[call-arg]  # deprecated kwarg via callable+args_mapping
        future_warnings = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert future_warnings, "Expected FutureWarning from callable+args_mapping path"
        assert future_warnings[0].filename.endswith("test_proxy.py")


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
            obj={}, name="legacy_obj", deprecated_in="1.0", remove_in="2.0", template_mgs="CUSTOM %(source_name)s"
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
            {"k": 1}, name="legacy_cfg", deprecated_in="1.0", remove_in="2.0", template_mgs="OVERRIDE %(source_name)s"
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
        proxy = _DeprecatedProxy(obj=[1], target=[1, 2, 3], name="x", deprecated_in="1.0", remove_in="2.0", stream=None)
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
            obj={"old": 1}, target={"new": 2}, name="x", deprecated_in="1.0", remove_in="2.0", stream=None
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
            assert DeprecatedColorEnum.RED is ColorEnum.RED

    def test_bare_call_default_target_is_notify_not_misconfigured(self) -> None:
        """``deprecated_class()`` called fresh with no explicit ``target=`` defaults to ``TargetMode.NOTIFY``.

        Existing coverage of the ``None`` -> ``TargetMode.NOTIFY`` default flip (this PR) goes through
        pre-decorated module-level fixtures; this locks the bare, zero-kwargs-beyond-versions path directly.

        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)

            @deprecated_class(deprecated_in="1.0", remove_in="2.0")
            class BareDefaultTargetClass:
                """Plain class deprecated with no explicit target — proves the factory default."""

        dep = object.__getattribute__(BareDefaultTargetClass, "__deprecated__")
        assert dep.target is TargetMode.NOTIFY
        assert dep.misconfigured is False

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
        self, raw_target: bool, warning_category: type[Warning], warning_message: str
    ) -> None:
        """Legacy boolean targets are normalized before proxy metadata and access use them."""
        with pytest.warns(warning_category) as caught:

            @deprecated_class(target=raw_target, deprecated_in="1.0", remove_in="2.0", stream=None)
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
                target=True, deprecated_in="1.0", remove_in="2.0", args_mapping={"old_attr": "new_attr"}, stream=None
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
                r"`tests\.collection_targets\.ColorEnum`"
            ),
        ):
            result = action()
        assert result is ColorEnum.RED

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
        with pytest.warns(FutureWarning, match=r"`MappedDataClass` uses deprecated arguments: `name` -> `label`"):
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
            FutureWarning, match=r"`MappedDropArgDataClass` uses deprecated arguments: `legacy_flag` -> `None`"
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
        with pytest.warns(FutureWarning, match=r"`MappedColorEnum` uses deprecated arguments: `val` -> `value`"):
            result = MappedColorEnum(val=1)  # type: ignore[call-arg]
        assert result is ColorEnum.RED

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

    def test_args_remap_new_key_wins_when_both_old_and_new_provided_old_first(self) -> None:
        """ARGS_REMAP proxy: explicit new-name value wins when both old and new kwargs passed (old first).

        ``proxy(old_key=5, new_key=6)`` must construct with ``new_key=6``; the remapped
        ``old_key→new_key=5`` must not overwrite the explicitly passed ``new_key=6``.
        """
        with pytest.warns(FutureWarning):
            instance = ProxyArgsRemapForArgWarnMessage(old_key=5, new_key=6)  # type: ignore[call-arg]
        assert instance.new_key == 6

    def test_args_remap_new_key_wins_when_both_old_and_new_provided_new_first(self) -> None:
        """ARGS_REMAP proxy: new-name value wins regardless of whether old or new kwarg is listed first.

        Before the precedence fix, ``proxy(new_key=6, old_key=5)`` produced ``new_key=5``
        because the dict-comprehension last-write-wins in ``_apply_args_mapping`` caused the
        ``old_key→new_key`` rename to overwrite the explicit ``new_key=6`` entry.
        This path exercises ``_proxy_call_args_remap`` (no callable target, ARGS_REMAP mode).
        """
        with pytest.warns(FutureWarning):
            instance = ProxyArgsRemapForArgWarnMessage(new_key=6, old_key=5)  # type: ignore[call-arg]
        assert instance.new_key == 6

    def test_args_remap_user_warning_emitted_when_both_old_and_new_provided(self) -> None:
        """ARGS_REMAP proxy: UserWarning fires naming the ignored argument when both old and new kwargs are passed.

        Calling ``ProxyArgsRemapForArgWarnMessage(old_key=5, new_key=6)`` must emit a ``UserWarning`` whose
        message identifies ``old_key`` as the ignored argument, in addition to the normal ``FutureWarning``.
        This test verifies parity between the ``@deprecated`` decorator path (which already emitted ``UserWarning``)
        and the ``deprecated_class`` proxy path after the proxy-path collision guard was added.

        """
        with pytest.warns(UserWarning, match=r"`old_key`.*is ignored"):
            ProxyArgsRemapForArgWarnMessage(old_key=5, new_key=6)  # type: ignore[call-arg]


class TestArgsExtra:
    """args_extra injects additional kwargs into deprecated_class() and deprecated_instance() forwarded calls."""

    def test_deprecated_class_accepts_args_extra_kwarg(self) -> None:
        """deprecated_class accepts args_extra without raising TypeError."""
        assert isinstance(ProxyClassWithArgsExtra, _DeprecatedProxy)

    def test_values_appear_in_forwarded_constructor_call(self) -> None:
        """Kwargs from args_extra are merged into the forwarded constructor call."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            instance = ProxyClassWithArgsExtra(new_key=7)
        assert instance.new_key == 7
        assert instance.injected == "from-extra"

    def test_merged_after_args_mapping_rename(self) -> None:
        """args_extra is applied after args_mapping renames kwargs."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            proxy = deprecated_class(
                target=WithInjected,
                deprecated_in="1.2",
                remove_in="2.0",
                args_mapping={"old_key": "new_key"},
                args_extra={"injected": "extra"},
                num_warns=-1,
            )(WithInjected)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            instance = proxy(old_key=11)
        assert instance.new_key == 11
        assert instance.injected == "extra"

    def test_deprecated_instance_accepts_args_extra_and_forwards(self) -> None:
        """deprecated_instance also accepts args_extra and merges it into forwarded calls."""
        proxy = deprecated_instance(
            WithInjected,
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

    def test_isinstance_raises_typeerror_for_non_type_active(self) -> None:
        """``isinstance(x, instance_proxy)`` raises TypeError like the builtin.

        Using an *instance* proxy (one wrapping a value rather than a class) as the second argument to
        ``isinstance`` is a misuse. Previously the proxy silently returned ``False``, hiding the mistake; it now
        raises the same ``TypeError`` the builtin raises when arg 2 is not a type.
        """
        proxy = _DeprecatedProxy(obj={"key": "val"}, name="old_cfg", deprecated_in="1.0", remove_in="2.0")
        with pytest.raises(TypeError, match="arg 2 must be a type"):
            isinstance(42, cast(Any, proxy))


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

    def test_callable_target_new_kwarg_wins_when_both_old_and_new_provided_old_first(self) -> None:
        """Callable-target proxy: explicit new-name value wins when both old and new kwargs passed (old first).

        A fresh proxy with ``target=SomeTargetClass`` and ``args_mapping={"old_key": "new_key"}`` is
        instantiated with ``old_key=5, new_key=6``; the remapped ``old_key→new_key=5`` must not
        overwrite the explicitly passed ``new_key=6``. Exercises ``_proxy_call_callable_with_mapping``.
        """
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
            instance = proxy(old_key=5, new_key=6)
        assert instance.new_key == 6

    def test_callable_target_new_kwarg_wins_when_both_old_and_new_provided_new_first(self) -> None:
        """Callable-target proxy: new-name value wins regardless of whether old or new kwarg is listed first.

        Before the precedence fix, calling with ``new_key=6, old_key=5`` produced ``new_key=5``
        because the dict-comprehension last-write-wins in ``_apply_args_mapping`` caused
        the ``old_key→new_key`` rename to overwrite the explicit ``new_key=6`` entry.
        Exercises ``_proxy_call_callable_with_mapping`` (has a callable target, not ARGS_REMAP mode).
        """
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
            instance = proxy(new_key=6, old_key=5)
        assert instance.new_key == 6

    def test_notify_with_args_mapping_auto_resolves_to_args_remap(self) -> None:
        """NOTIFY + args_mapping on proxy auto-resolves to ARGS_REMAP — no misconfig warning (since 2026-07-20)."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            @deprecated_class(
                deprecated_in="1.2", remove_in="2.0", target=TargetMode.NOTIFY, args_mapping={"old_key": "new_key"}
            )
            class _ProxyNotifyWithArgsMapping:
                pass

        misconfig_warns = [w for w in caught if "ignores `args_mapping`" in str(w.message)]
        assert not misconfig_warns
        meta = object.__getattribute__(_ProxyNotifyWithArgsMapping, "__deprecated__")
        assert meta.target is TargetMode.ARGS_REMAP
        assert meta.args_mapping == {"old_key": "new_key"}

    def test_args_remap_no_mapping_emits_misconfig_warning(self) -> None:
        """ARGS_REMAP without args_mapping on proxy emits UserWarning at decoration time."""
        with pytest.warns(UserWarning, match="args_mapping"):

            @deprecated_class(deprecated_in="1.2", remove_in="2.0", target=TargetMode.ARGS_REMAP)
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


class TestCombinedArgAttrsMapping:
    """Single ``deprecated_class()`` call combining ``args_mapping`` and ``attrs_mapping``.

    The canonical pattern for combining arg-rename and attr-rename deprecation is one ``deprecated_class()``
    call with both mappings and an explicit ``target=<NewClass>`` argument.  These tests pin that contract.

    Two decorators may also be stacked (see :class:`TestStackedDeprecatedClass` in ``test_stacking.py``)
    when each mapping layer needs an independent ``deprecated_in``/``remove_in`` version pair — e.g.
    ``old_attr`` deprecated in v1.0 while ``older_attr`` was deprecated earlier in v0.9.

    """

    def test_combined_args_and_attrs_mapping_call_path(self) -> None:
        """Constructor argument rename fires when calling the combined proxy.

        A single ``deprecated_class(target=NewClass, args_mapping=..., attrs_mapping=...)`` call must remap the
        old constructor argument on the call path.  The attribute path is independent — exhausting the call-path
        warning budget does not silence the attribute path and vice-versa.

        """

        class _New:
            lr: float = 0.01
            n_epochs: int = 10  # will be deprecated with warn-only mapping

            def __init__(self, lr: float = 0.01) -> None:
                self.lr = lr

        proxy = deprecated_class(
            target=_New,
            args_mapping={"learning_rate": "lr"},
            attrs_mapping={"n_epochs": None},  # warn on n_epochs, no rename
            deprecated_in="2.0",
            remove_in="3.0",
            num_warns=-1,
        )(_New)

        with pytest.warns(FutureWarning, match="learning_rate"):
            instance = proxy(learning_rate=0.05)  # type: ignore[call-arg]

        assert instance.lr == 0.05

    def test_combined_args_and_attrs_mapping_attr_path(self) -> None:
        """Attribute alias read fires when accessing a deprecated name on the combined proxy.

        The attribute-path warning budget is independent of the call-path budget.  Accessing a deprecated alias
        after exhausting the call-path budget must still warn exactly once (``num_warns=1``).

        """

        class _Config:
            lr: float = 0.01
            epochs: int = 10

        proxy = deprecated_class(
            target=_Config,
            args_mapping={"learning_rate": "lr"},
            attrs_mapping={"n_epochs": "epochs"},
            deprecated_in="2.0",
            remove_in="3.0",
        )(_Config)

        with pytest.warns(FutureWarning, match="n_epochs"):
            value = proxy.n_epochs  # type: ignore[attr-defined]

        assert value == 10

    def test_combined_isinstance_passes_through(self) -> None:
        """``isinstance()`` returns ``True`` for the combined single-call form.

        A single-call combined proxy has exactly one ``_DeprecatedProxy`` layer so ``__instancecheck__``
        resolves directly to the real class without recursion.  Stacked two-decorator forms also support
        ``isinstance()`` — see :class:`TestStackedDeprecatedClass` in ``test_stacking.py``.

        """

        class _Target:
            old_attr: str = "value"  # deprecated attribute — redirect target for isinstance test

        proxy = deprecated_class(
            target=_Target,
            attrs_mapping={"old_attr": None},  # warn on old_attr, no rename
            deprecated_in="1.0",
            remove_in="2.0",
            stream=None,
        )(_Target)

        instance = proxy()
        assert isinstance(instance, _Target)


class TestDeprecatedAttrs:
    """Selective per-attribute deprecation via ``attrs_mapping`` on ``deprecated_class``.

    Each test sets up an isolated proxy state because ``DeprecatedAttrsPalette`` and friends are
    module-level singletons whose per-attribute warning counters (``_cfg.warned_args``) persist
    across tests once consumed. The :meth:`_reset_proxy_state` autouse fixture clears those
    counters and re-seeds the canonical attribute values on the wrapped target so each test
    starts from the same baseline.

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
            DeprecatedAttrsNotifyOnlyCallableTargetDecorated,
            DeprecatedAttrsNotifyOnlyCallableTargetWrapped,
            DeprecatedAttrsPaletteEnum,
            DeprecatedAttrsPaletteWithStream,
            depr_read_only_attrs_list,
        ):
            cfg = object.__getattribute__(proxy, "_DeprecatedProxy__config")
            cfg.warned = 0
            cfg.warned_args.clear()
            source = cfg.obj
            if hasattr(source, "items"):
                source.items.clear()
            if hasattr(source, "size"):
                delattr(source, "size")
        # Restore canonical class attributes mutated by previous write-redirect tests.
        Palette.colour = "red"
        Palette.text = "hello"
        Palette.size = 42
        PaletteOld.color = "source_red"
        PaletteOld.colour = "source_colour"

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
        proxy = deprecated_class(attrs_mapping={"size": None}, deprecated_in="1.0", remove_in="2.0")(Palette)
        with pytest.warns(FutureWarning, match="size") as record:
            value = proxy.size  # type: ignore[attr-defined]
        assert value == 42
        # The no-target template does not include any "in favor of" phrase.
        assert "in favor of" not in str(record[0].message)

    def test_callable_target_notify_only_attr_uses_no_target_template(self) -> None:
        """A callable class target does not turn a ``None`` attr mapping into a redirect message.

        A migration can redirect some attributes through a replacement class while keeping another attribute as
        warn-only with ``attrs_mapping={"size": None}``.  Accessing that warn-only attribute must still read the
        active class attribute unchanged and render the no-target warning template, not an ``in favor of`` message
        pointing at a nonexistent replacement attribute.

        """
        proxy = deprecated_class(target=Palette, attrs_mapping={"size": None}, deprecated_in="1.0", remove_in="2.0")(
            Palette
        )
        with pytest.warns(FutureWarning, match="size") as record:
            value = proxy.size  # type: ignore[attr-defined]
        assert value == 42
        assert "in favor of" not in str(record[0].message)

    def test_read_only_attrs_mapping_blocks_mapped_mutator(self) -> None:
        """Read-only proxies block mutators returned through an attribute mapping.

        A project may keep a deprecated alias such as ``push`` while the replacement object exposes the standard
        list-like ``append`` method.  When the proxy is also read-only, resolving ``push`` through
        ``attrs_mapping={"push": "append"}`` must still return a guarded callable so calling it raises
        ``AttributeError`` and leaves the underlying collection unchanged.

        """
        source = object.__getattribute__(depr_read_only_attrs_list, "_DeprecatedProxy__config").obj

        with pytest.raises(AttributeError, match="read-only"):
            depr_read_only_attrs_list.push("x")  # type: ignore[attr-defined]

        assert source.items == []

    def test_read_only_proxy_with_attrs_mapping_blocks_set_on_deprecated_attr(self) -> None:
        """``read_only=True`` takes precedence over ``attrs_mapping`` on write — no deprecation warning is emitted.

        ``_check_read_only`` fires before the ``attrs_mapping`` branch in ``__setattr__`` and ``__delattr__``.
        Writing to a deprecated alias on a ``read_only`` proxy raises :class:`AttributeError` immediately, without
        emitting a ``FutureWarning``.  This is intentional — read-only is the stronger contract — but is pinned here
        so the precedence order is explicitly asserted and not accidentally reversed by future refactors.

        """
        proxy = _DeprecatedProxy(
            obj=Palette,
            name="Palette",
            deprecated_in="1.0",
            remove_in="2.0",
            read_only=True,
            stream=None,
            attrs_mapping={"color": "colour"},
        )

        with pytest.raises(AttributeError, match="read-only"):
            proxy.color = "blue"  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        "proxy",
        [
            pytest.param(DeprecatedAttrsNotifyOnlyCallableTargetDecorated, id="decorator"),
            pytest.param(DeprecatedAttrsNotifyOnlyCallableTargetWrapped, id="wrapper"),
        ],
    )
    def test_callable_target_notify_only_attr_uses_active_target_for_mutations(self, proxy: Any) -> None:  # noqa: ANN401
        """Warn-only attributes on a callable-target proxy resolve against the active target class.

        A replacement class may keep an attribute under the same name while the deprecated source class lacks that
        attribute entirely.  With ``target=Palette`` and ``attrs_mapping={"size": None}``, reads, writes, and
        deletes of ``proxy.size`` must operate on ``Palette.size``.  Falling back to the wrapped source class
        either raises ``AttributeError`` on read/delete or silently writes to the wrong class.  The behaviour must be
        identical for decorator-form and wrapper-form ``deprecated_class`` usage.

        """
        source = object.__getattribute__(proxy, "_DeprecatedProxy__config").obj
        original_target = Palette.size
        assert not hasattr(source, "size")

        with pytest.warns(FutureWarning, match="size") as read_record:
            value = proxy.size  # type: ignore[attr-defined]
        assert value == original_target
        assert "in favor of" not in str(read_record[0].message)

        with pytest.warns(FutureWarning, match="size"):
            proxy.size = 99  # type: ignore[attr-defined]
        assert Palette.size == 99
        assert not hasattr(PaletteOld, "size")

        with pytest.warns(FutureWarning, match="size"):
            del proxy.size  # type: ignore[attr-defined]
        assert not hasattr(Palette, "size")
        assert not hasattr(source, "size")

    def test_per_attribute_warning_budget_independent(self) -> None:
        """Each deprecated attribute name has its own warning budget under ``num_warns=1``.

        With two entries in ``attrs_mapping`` and ``num_warns=1``, accessing both deprecated names must emit one
        warning each (two warnings total), not just one warning shared across all deprecated names.  This mirrors the
        per-argument budget of ``args_mapping`` deprecation and ensures callers see the migration notice for every
        attribute they use.

        """
        proxy = deprecated_class(
            attrs_mapping={"color": "colour", "txt": "text"}, deprecated_in="1.0", remove_in="2.0", num_warns=1
        )(Palette)
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

    def test_per_attribute_warning_budget_num_warns_two(self) -> None:
        """``num_warns=2`` allows exactly two warnings per deprecated attribute before going silent.

        With ``attrs_mapping={"color": "colour"}`` and ``num_warns=2``, three successive reads of
        ``proxy.color`` must emit exactly two ``FutureWarning`` instances — one on the first access,
        one on the second, and none on the third.  This verifies the off-by-one boundary
        ``warned_args.get(key, 0) >= num_warns`` at the budget limit.

        """
        proxy = deprecated_class(attrs_mapping={"color": "colour"}, deprecated_in="1.0", remove_in="2.0", num_warns=2)(
            Palette
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = proxy.color  # type: ignore[attr-defined]
            _ = proxy.color  # type: ignore[attr-defined]
            _ = proxy.color  # type: ignore[attr-defined]
        future_warnings = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert len(future_warnings) == 2

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

        An enum ``PaletteEnum`` has ``COLOUR`` as the canonical member name.  A deprecated alias ``COLOR`` is
        registered via ``attrs_mapping={"COLOR": "COLOUR"}``.  Accessing ``DeprecatedAttrsPaletteEnum.COLOR`` must
        warn and return the same object as ``DeprecatedAttrsPaletteEnum.COLOUR``.

        """
        # Use a fresh stream-enabled proxy so we can assert on the FutureWarning.
        proxy = deprecated_class(attrs_mapping={"COLOR": "COLOUR"}, deprecated_in="1.0", remove_in="2.0")(PaletteEnum)
        with pytest.warns(FutureWarning, match="COLOR"):
            value = proxy.COLOR  # type: ignore[attr-defined]
        assert value is PaletteEnum.COLOUR

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
        assert "Palette.colour" in message

    def test_circular_redirect_raises_at_decoration_time(self) -> None:
        """Circular redirect mapping raises ``ValueError`` at decoration time.

        Passing ``attrs_mapping={"a": "b", "b": "a"}`` would create an infinite loop if both names were looked up
        through the proxy.  The decorator must detect this at class-decoration time and raise ``ValueError`` before any
        instance is created, making the misconfiguration visible immediately rather than at access time.

        """
        with pytest.raises(ValueError, match="circular redirects"):

            @deprecated_class(attrs_mapping={"a": "b", "b": "a"}, deprecated_in="1.0", remove_in="2.0")
            class _Circular:
                a = 1
                b = 2

    def test_redirect_chain_allowed_at_decoration_time(self) -> None:
        """Multi-hop attribute redirects are allowed for audit to report later.

        ``attrs_mapping={"a": "b", "b": "c"}`` is a mapping chain, but not a cycle. The decorator should keep import
        time usable and leave chain hygiene to audit tooling.

        """

        @deprecated_class(attrs_mapping={"a": "b", "b": "c"}, deprecated_in="1.0", remove_in="2.0", stream=None)
        class _Chained:
            a = 1
            b = 2
            c = 3

        assert _Chained.a == 2

    def test_attrs_mapping_stored_in_metadata(self) -> None:
        """``attrs_mapping`` is visible in ``__deprecated__`` metadata for audit tools.

        Audit tooling reads ``DeprecationConfig`` via ``obj.__deprecated__`` to build deprecation tables and enforce
        expiry policies.  The ``attrs_mapping`` dict must be stored in the frozen ``DeprecationConfig`` so that
        ``find_deprecation_wrappers`` can surface it without reading internal proxy state.

        """
        meta = object.__getattribute__(DeprecatedAttrsPalette, "__deprecated__")
        assert meta.attrs_mapping == {"color": "colour", "txt": "text"}

    def test_attrs_mapping_with_callable_target_resolves_against_target_namespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When ``target=SomeClass`` and ``attrs_mapping`` are both set, attr redirects use the target class.

        ``deprecated_class(target=Palette, attrs_mapping={"color": "colour"})(PaletteOld)``
        redirects mapped reads of ``proxy.color`` to ``Palette.colour``.  Without a callable target the redirect
        would stay on the wrapped source class.

        """
        monkeypatch.setattr(PaletteOld, "colour", "source_colour")
        monkeypatch.setattr(Palette, "colour", "red")

        value = DeprecatedAttrsPaletteCallableTarget.color  # type: ignore[attr-defined]
        assert value == Palette.colour
        assert value != PaletteOld.colour

        DeprecatedAttrsPaletteCallableTarget.color = "blue"  # type: ignore[attr-defined]
        assert Palette.colour == "blue"
        assert PaletteOld.colour == "source_colour"

    def test_attrs_mapping_validation_does_not_consume_target_proxy_warning_budget(self) -> None:
        """Validation does not call ``hasattr`` on a live target proxy.

        A target proxy can warn from ``__getattr__`` when queried for one of its deprecated aliases.  Decorating a
        second proxy with that object as ``target`` must not touch the target proxy during redirect-target validation,
        otherwise the target's one-warning budget can be consumed before any user access.

        """

        class TargetAlias:
            alias = "target_alias"
            canonical = "target_canonical"

        target_proxy = deprecated_class(
            attrs_mapping={"alias": "canonical"}, deprecated_in="1.0", remove_in="2.0", num_warns=1
        )(TargetAlias)
        target_cfg = object.__getattribute__(target_proxy, "_DeprecatedProxy__config")
        target_cfg.warned_args.clear()

        class SourceAlias:
            old = "source_old"
            alias = "source_alias"

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            deprecated_class(target=target_proxy, attrs_mapping={"old": "alias"}, deprecated_in="1.0", remove_in="2.0")(
                SourceAlias
            )
        assert not caught
        assert target_cfg.warned_args == {}

    def test_delete_redirect_warns_and_deletes_canonical(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deleting a deprecated attribute alias warns and deletes the canonical attribute.

        A class has ``colour`` as the canonical attribute and ``color`` registered as a deprecated alias in
        ``attrs_mapping={"color": "colour"}``.  Calling ``del proxy.color`` must emit a ``FutureWarning`` for ``color``
        and then delete ``Palette.colour``, so that callers migrating away from the deprecated name still trigger
        the expected deletion on the live canonical attribute.

        """
        monkeypatch.setattr(Palette, "colour", Palette.colour)
        proxy = deprecated_class(attrs_mapping={"color": "colour"}, deprecated_in="1.0", remove_in="2.0")(Palette)
        with pytest.warns(FutureWarning, match="color"):
            del proxy.color  # type: ignore[attr-defined]
        assert not hasattr(Palette, "colour")

    def test_delete_notify_only_warns_and_deletes_same_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deleting a warn-only attribute (``None`` redirect) warns and deletes the same-name attribute.

        When ``attrs_mapping={"size": None}``, the attribute name is both deprecated and canonical — ``None`` means
        "warn but keep the same name."  Calling ``del proxy.size`` must emit a ``FutureWarning`` for ``size`` and then
        delete ``Palette.size`` (the same attribute, not a redirect target), mirroring the read/write semantics
        for the notify-only case.

        """
        monkeypatch.setattr(Palette, "size", Palette.size)
        proxy = deprecated_class(attrs_mapping={"size": None}, deprecated_in="1.0", remove_in="2.0")(Palette)
        with pytest.warns(FutureWarning, match="size"):
            del proxy.size  # type: ignore[attr-defined]
        assert not hasattr(Palette, "size")

    def test_write_on_warn_only_attr_source_only_callable_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Writing a warn-only attr that lives only on the source falls back to the source class.

        The "being-removed" pattern places a deprecated attribute on the old source class only — the replacement target
        class dropped it.  With ``attrs_mapping={"color": None}`` and ``target=Palette`` (which has no ``color``),
        setting ``proxy.color = value`` must warn and write to ``PaletteOld.color``, not silently create a new class
        attribute on ``Palette``.  Without the source fallback, a regular class target accepts the spurious setattr
        without raising, leaving the source unchanged and the target polluted.

        """
        monkeypatch.setattr(PaletteOld, "color", PaletteOld.color)
        proxy = deprecated_class(target=Palette, attrs_mapping={"color": None}, deprecated_in="1.0", remove_in="2.0")(
            PaletteOld
        )
        assert not hasattr(Palette, "color")
        original = PaletteOld.color

        with pytest.warns(FutureWarning, match="color"):
            proxy.color = "updated"  # type: ignore[attr-defined]

        assert PaletteOld.color == "updated"
        assert not hasattr(Palette, "color"), "setattr must not pollute the target class"
        monkeypatch.setattr(PaletteOld, "color", original)

    def test_delete_on_warn_only_attr_source_only_callable_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deleting a warn-only attr that lives only on the source falls back to the source class.

        The "being-removed" pattern places a deprecated attribute on the old source class only.  With
        ``attrs_mapping={"color": None}`` and ``target=Palette`` (which has no ``color``), ``del proxy.color`` must
        warn and delete ``PaletteOld.color``.  Without the source fallback, ``delattr(Palette, "color")`` raises a bare
        ``AttributeError`` with no deprecation context, confusing callers who do not know the attribute lives on the
        old class.

        """
        monkeypatch.setattr(PaletteOld, "color", PaletteOld.color)
        proxy = deprecated_class(target=Palette, attrs_mapping={"color": None}, deprecated_in="1.0", remove_in="2.0")(
            PaletteOld
        )
        assert not hasattr(Palette, "color")

        with pytest.warns(FutureWarning, match="color"):
            del proxy.color  # type: ignore[attr-defined]

        assert not hasattr(PaletteOld, "color"), "delattr must remove from source class"
        assert not hasattr(Palette, "color"), "target must remain unaffected"

    def test_warn_only_key_missing_from_both_classes_raises_at_decoration_time(self) -> None:
        """A warn-only ``attrs_mapping`` key absent from both source and target raises at decoration time.

        When ``attrs_mapping={"nonexistent": None}`` is passed and neither the source class nor the target class has a
        ``nonexistent`` attribute, the decorator must raise ``ValueError`` immediately during class decoration rather
        than silently producing a proxy that raises ``AttributeError`` on first access.  A key present on only the
        source (being-removed pattern) or only the target (same-name warning) is valid and must not raise.

        """
        with pytest.raises(ValueError, match="warn-only keys not found on either class"):

            @deprecated_class(attrs_mapping={"nonexistent": None}, deprecated_in="1.0", remove_in="2.0")
            class _MissingWarnOnly:
                colour: str = "red"


class TestAttrsMappingCombinations:
    """Combination matrix for ``deprecated_class()`` ``attrs_mapping`` configurations.

    Covers the additive :attr:`~deprecate.TargetMode.ATTRS_REMAP` alias introduced for selective per-attribute
    deprecation, including:

    - Explicit ``target=TargetMode.ATTRS_REMAP`` form (equivalent to implicit auto-resolve)
    - Combined callable target + ``attrs_mapping`` + ``args_mapping`` on disjoint surfaces
    - Nested proxy semantics (blanket outer + selective inner)
    - Misconfiguration cases at decoration time (UserWarning emissions and TypeError on functions)

    Each test resets module-level fixture state before running because the combination fixtures share their per-attr
    warning budget with the rest of the test session. The :meth:`_reset_combination_state` autouse fixture clears
    the relevant counters and re-seeds canonical attribute values.

    """

    @pytest.fixture(autouse=True)
    def _reset_combination_state(self) -> None:
        """Reset module-level combination fixture proxies and re-seed wrapped target attributes between tests."""
        for proxy in (
            DeprecatedAttrsExplicitMode,
            DeprecatedAttrsPaletteAllThree,
            DeprecatedAttrsPaletteNested,
            DeprecatedAttrsPalette,
        ):
            cfg = object.__getattribute__(proxy, "_DeprecatedProxy__config")
            cfg.warned = 0
            cfg.warned_args.clear()
        # Re-seed canonical attributes so write-redirect tests start from baseline.
        Palette.colour = "red"
        Palette.text = "hello"
        Palette.size = 42
        CombinedAttrsArgsTarget.colour = "red"
        CombinedAttrsArgsSource.colour = "source_red"

    # ------------------------------------------------------------------
    # Explicit TargetMode.ATTRS_REMAP form (C1)
    # ------------------------------------------------------------------

    def test_explicit_attrs_remap_mode_reads_canonical(self) -> None:
        """Explicit ``target=TargetMode.ATTRS_REMAP`` reads the canonical attribute through the alias.

        Migrators who prefer self-documenting decorator config may write the mode explicitly as
        ``target=TargetMode.ATTRS_REMAP`` rather than relying on implicit auto-resolution from ``attrs_mapping``.
        Accessing ``proxy.color`` on a fixture declared with the explicit form must still emit a ``FutureWarning``
        (suppressed here by ``stream=None``) and return the canonical ``Palette.colour`` value, matching the
        behaviour of the implicit-form fixture ``DeprecatedAttrsPalette``.

        """
        value = DeprecatedAttrsExplicitMode.color  # type: ignore[attr-defined]
        assert value == "red"

    def test_explicit_attrs_remap_mode_is_equivalent_to_implicit(self) -> None:
        """Explicit ``target=TargetMode.ATTRS_REMAP`` is observationally equivalent to the implicit auto-resolve.

        Both forms read from the same canonical attribute, expose the same ``attrs_mapping`` metadata, and store the
        same resolved ``target`` on the frozen :class:`~deprecate._types.DeprecationConfig`. Callers must be able to
        migrate between the two forms without behavioural change.

        """
        explicit_value = DeprecatedAttrsExplicitMode.color  # type: ignore[attr-defined]
        implicit_value = DeprecatedAttrsPalette.color  # type: ignore[attr-defined]
        assert explicit_value == implicit_value
        explicit_meta = object.__getattribute__(DeprecatedAttrsExplicitMode, "__deprecated__")
        implicit_meta = object.__getattribute__(DeprecatedAttrsPalette, "__deprecated__")
        assert explicit_meta.target is implicit_meta.target is TargetMode.ATTRS_REMAP

    def test_explicit_attrs_remap_stored_in_dep_config_target(self) -> None:
        """Explicit ``target=TargetMode.ATTRS_REMAP`` is stored verbatim on the frozen DeprecationConfig.

        Audit tooling reads ``DeprecationConfig.target`` to introspect the resolved deprecation mode. Passing the
        enum member explicitly must preserve the same enum member in storage so that downstream consumers cannot
        distinguish the explicit from the implicit auto-resolve at the metadata level.

        """
        meta = object.__getattribute__(DeprecatedAttrsExplicitMode, "__deprecated__")
        assert meta.target is TargetMode.ATTRS_REMAP

    def test_legacy_true_with_attrs_mapping_does_not_raise_value_error(self) -> None:
        """Legacy ``target=True`` with ``attrs_mapping`` auto-resolves to ``ATTRS_REMAP``.

        A legacy caller can combine ``target=True`` with ``attrs_mapping`` while still defining the canonical
        attribute on the deprecated class itself.  ``target=True`` normalises to ``NOTIFY``, and a present
        mapping is always applied — the config auto-resolves to ``ATTRS_REMAP`` (no misconfiguration)
        rather than validating redirect targets against ``bool`` and raising ``ValueError`` before the proxy can be
        called.

        """
        instance = DeprecatedAttrsLegacyTrue()  # must not raise ValueError

        assert isinstance(instance, LegacyBoolAttrsSource)
        assert instance.ready is True
        meta = object.__getattribute__(DeprecatedAttrsLegacyTrue, "__deprecated__")
        assert meta.target is TargetMode.ATTRS_REMAP
        assert meta.misconfigured is False

    def test_attrs_remap_stored_in_dep_config_target_via_auto_resolve(self) -> None:
        """Auto-resolution from ``attrs_mapping`` to ``ATTRS_REMAP`` is reflected in stored metadata.

        When the caller omits ``target`` but provides ``attrs_mapping``, the proxy must store
        :attr:`~deprecate._types.TargetMode.ATTRS_REMAP` on the frozen config rather than the original ``None`` so
        that audit tooling has a single canonical mode marker regardless of how the caller spelled the config.

        """
        meta = object.__getattribute__(DeprecatedAttrsPalette, "__deprecated__")
        assert meta.target is TargetMode.ATTRS_REMAP

    # ------------------------------------------------------------------
    # Combined callable target + attrs_mapping + args_mapping (C3)
    # ------------------------------------------------------------------

    def test_callable_target_with_attrs_and_args_mapping_attr_path(self) -> None:
        """The attribute-access path uses ``attrs_mapping`` independently of the call path.

        ``DeprecatedAttrsPaletteAllThree`` is constructed with all three of ``target=CombinedAttrsArgsTarget``,
        ``attrs_mapping={"color": "colour"}``, and ``args_mapping={"old_arg": "new_arg"}``. Reading ``proxy.color``
        must redirect to the canonical ``colour`` attribute on the target class, demonstrating that
        ``attrs_mapping`` is active even when a callable target and ``args_mapping`` are also configured (each
        surface remains disjoint).

        """
        value = DeprecatedAttrsPaletteAllThree.color  # type: ignore[attr-defined]
        assert value == CombinedAttrsArgsTarget.colour

    def test_callable_target_with_attrs_and_args_mapping_call_path(self) -> None:
        """The call path uses ``args_mapping`` independently of the attribute-access path.

        Calling the proxy with the deprecated kwarg ``old_arg`` must remap to ``new_arg`` before forwarding to the
        ``CombinedAttrsArgsTarget`` constructor. The resulting instance's ``new_arg`` attribute reflects the value
        the caller originally passed under the old name, proving the rename happened through the call path even when
        ``attrs_mapping`` is also present on the same proxy.

        """
        result = DeprecatedAttrsPaletteAllThree(old_arg=42)  # type: ignore[call-arg]
        assert isinstance(result, CombinedAttrsArgsTarget)
        assert result.new_arg == 42

    # ------------------------------------------------------------------
    # Nested proxy semantics
    # ------------------------------------------------------------------

    def test_nested_proxy_blanket_warns_on_access(self) -> None:
        """A blanket-warn ``deprecated_class`` proxy wrapping another proxy intercepts every attribute access.

        Nesting an outer ``deprecated_class()`` (no ``attrs_mapping``, blanket-warn mode) around an inner selective
        proxy means the outer's ``__getattr__`` fires on every attribute lookup before delegating to the inner.
        Reading ``proxy.colour`` (a canonical attribute on the inner) must still emit a ``FutureWarning`` from the
        outer wrapper, and the returned value must round-trip through both proxies to the underlying target class
        attribute. ``stream=None`` is set on the fixture so this test asserts the round-trip value; warning content
        is exercised by the implicit/explicit equivalence tests above.

        """
        value = DeprecatedAttrsPaletteNested.colour  # type: ignore[attr-defined]
        assert value == "red"

    # ------------------------------------------------------------------
    # Misconfiguration cases (decoration-time signals)
    # ------------------------------------------------------------------

    def test_notify_plus_attrs_mapping_auto_resolves_to_attrs_remap(self) -> None:
        """``target=TargetMode.NOTIFY`` combined with ``attrs_mapping`` auto-resolves to ``ATTRS_REMAP``.

        Option C (2026-07-20) retires the NOTIFY+mapping misconfig guardrail: presence of ``attrs_mapping``
        is now the activation signal for selective per-attribute warning, exactly like the long-standing
        ``target=None``+``attrs_mapping`` auto-resolve — ``TargetMode.NOTIFY`` is treated the same way since
        it is now the ``deprecated_class`` default. No misconfig warning fires and
        :attr:`~deprecate._types.DeprecationConfig.misconfigured` stays ``False``.

        """

        class _NotifyAttrsAutoResolved:
            colour = "red"

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy = deprecated_class(
                target=TargetMode.NOTIFY,
                attrs_mapping={"color": "colour"},
                deprecated_in="1.0",
                remove_in="2.0",
                stream=None,
            )(_NotifyAttrsAutoResolved)

        misconfig_warns = [w for w in caught if "ignores `attrs_mapping`" in str(w.message)]
        assert not misconfig_warns
        meta = object.__getattribute__(proxy, "__deprecated__")
        assert meta.misconfigured is False
        assert meta.target is TargetMode.ATTRS_REMAP

    def test_attrs_remap_without_attrs_mapping_warns_at_decoration(self) -> None:
        """``target=TargetMode.ATTRS_REMAP`` without ``attrs_mapping`` emits a UserWarning at decoration time.

        Explicit selective mode without any deprecated attribute names listed means the proxy has zero selective
        effect — every attribute access falls through to the blanket-warn path. This is a developer error and must
        be flagged at decoration time so the misconfiguration cannot ship to production.

        """

        class _AttrsRemapMissingMapping:
            colour = "red"

        with pytest.warns(UserWarning, match="ATTRS_REMAP.*requires.*`attrs_mapping`"):
            proxy = deprecated_class(target=TargetMode.ATTRS_REMAP, deprecated_in="1.0", remove_in="2.0", stream=None)(
                _AttrsRemapMissingMapping
            )
        meta = object.__getattribute__(proxy, "__deprecated__")
        assert meta.misconfigured is True

    def test_empty_attrs_mapping_warns_at_decoration(self) -> None:
        """An empty ``attrs_mapping={}`` dict is a no-op misconfiguration and emits a UserWarning.

        ``attrs_mapping={}`` is a developer typo — the empty dict has no deprecated attribute entries so the
        proxy has zero selective effect (and auto-resolve to ``ATTRS_REMAP`` does not fire because the value is
        falsy). The validator must catch this at decoration time so the misconfiguration cannot ship silently.

        """

        class _EmptyAttrsMapping:
            colour = "red"

        with pytest.warns(UserWarning, match="empty dict"):
            proxy = deprecated_class(attrs_mapping={}, deprecated_in="1.0", remove_in="2.0", stream=None)(
                _EmptyAttrsMapping
            )
        meta = object.__getattribute__(proxy, "__deprecated__")
        assert meta.misconfigured is True

    def test_attrs_remap_on_function_raises_typeerror(self) -> None:
        """Applying ``@deprecated(target=TargetMode.ATTRS_REMAP)`` to a function raises ``TypeError``.

        :attr:`~deprecate._types.TargetMode.ATTRS_REMAP` is a proxy-only mode — it operates on class attribute
        access, which functions and methods do not have. Trying to apply it via ``@deprecated`` is a developer
        error that must fail at decoration time with a clear redirect to ``deprecated_class(attrs_mapping=...)``,
        not silently produce a wrapper whose stored target has no runtime effect.

        """
        with pytest.raises(TypeError, match="ATTRS_REMAP.*not valid.*deprecated_class"):

            @deprecated(target=TargetMode.ATTRS_REMAP, deprecated_in="1.0", remove_in="2.0")
            def _attempted_attrs_remap_fn(x: int) -> int:
                return x

    def test_both_mappings_without_explicit_target_emits_userwarning(self) -> None:
        """Providing both ``args_mapping`` and ``attrs_mapping`` without explicit ``target`` emits a ``UserWarning``.

        When no ``target`` is given, auto-resolution sets ``target=TargetMode.ARGS_REMAP`` (args_mapping takes
        precedence) and ``DeprecationConfig.target`` no longer reflects that ``attrs_mapping`` is also active.
        Audit tooling reading only ``DeprecationConfig.target`` cannot detect the selective attribute deprecation.
        The proxy must emit a ``UserWarning`` directing the caller to pass an explicit target so the metadata
        remains complete.

        """

        class _BothMappingsSource:
            colour = "red"

        with pytest.warns(UserWarning, match="both.*args_mapping.*attrs_mapping"):
            deprecated_class(
                args_mapping={"old_arg": "new_arg"},
                attrs_mapping={"color": "colour"},
                deprecated_in="1.0",
                remove_in="2.0",
                stream=None,
            )(_BothMappingsSource)

    def test_explicit_attrs_remap_with_args_mapping_emits_userwarning(self) -> None:
        """Explicit ``target=TargetMode.ATTRS_REMAP`` with ``args_mapping`` emits a ``UserWarning``.

        ``ATTRS_REMAP`` only governs attribute access; ``__call__`` has no dispatch branch for it, so any
        ``args_mapping`` provided alongside it is silently dead code.  The companion auto-resolve case
        (``ARGS_REMAP + attrs_mapping``) is already caught; this test pins the symmetric case where the user
        explicitly sets ``target=TargetMode.ATTRS_REMAP`` and also supplies ``args_mapping``, which would
        otherwise silently ignore the call-path renames.

        """

        class _ExplicitAttrsRemapSource:
            colour = "red"

        with pytest.warns(UserWarning, match="ignores.*args_mapping"):
            deprecated_class(
                target=TargetMode.ATTRS_REMAP,
                args_mapping={"old_arg": "new_arg"},
                attrs_mapping={"color": "colour"},
                deprecated_in="1.0",
                remove_in="2.0",
                stream=None,
            )(_ExplicitAttrsRemapSource)

    def test_validate_proxy_userwarning_points_to_decoration_call_site(self) -> None:
        """The ``UserWarning`` from ``_validate_proxy`` points to the file that called ``deprecated_class``.

        ``_validate_proxy`` is called from ``_DeprecatedProxy.__init__``, which is invoked through
        ``deprecated_class → inner_func → __init__``.  The stacklevel must be calibrated so that
        ``warning.filename`` reports the module that applied the decorator, not an internal pyDeprecate
        frame.  A wrong stacklevel (e.g. 4 instead of 5) would make the warning appear to originate
        from inside the library, making it hard for users to locate their misconfigured class.

        The trigger is ``target=TargetMode.ATTRS_REMAP`` without ``attrs_mapping`` — a misconfiguration
        ``_validate_proxy`` still flags (unlike ``NOTIFY + attrs_mapping``, which now auto-resolves and no
        longer warns).

        """

        class _MisconfiguredForStacklevel:
            colour = "red"

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            deprecated_class(
                target=TargetMode.ATTRS_REMAP,
                deprecated_in="1.0",
                remove_in="2.0",
                stream=None,
            )(_MisconfiguredForStacklevel)
        user_warnings = [w for w in caught if issubclass(w.category, UserWarning)]
        assert user_warnings, "Expected a UserWarning from _validate_proxy but got none"
        assert user_warnings[0].filename == __file__


# ---------------------------------------------------------------------------
# Dataclass dual-surface auto-expand
# ---------------------------------------------------------------------------


class TestDataclassAutoExpand:
    """attrs_mapping auto-expansion to args_mapping for ``@dataclass`` targets.

    When ``deprecated_class`` is applied to a ``@dataclass`` with only ``attrs_mapping``
    configured, the proxy must automatically expand into ``args_mapping`` so that both the
    attribute-access surface (post-construction ``__getattr__``) and the constructor-call
    surface (``DC(old_field=5)``) emit ``FutureWarning`` from a single decoration call.
    """

    def test_constructor_kwarg_warns_after_auto_expand(self) -> None:
        """``DepAutoExpandDC(old_field=5)`` emits FutureWarning after auto-expand.

        Before the fix, ``attrs_mapping``-only on a dataclass meant calling ``DC(old_field=5)``
        raised ``TypeError``.  After auto-expand the proxy has ``args_mapping={"old_field":
        "new_field"}`` and the FutureWarning fires; the instance is created with
        ``new_field=5``.
        """
        with pytest.warns(FutureWarning):
            instance = DepAutoExpandDC(old_field=5)  # type: ignore[call-arg]
        assert instance.new_field == 5

    def test_class_proxy_attribute_access_warns(self) -> None:
        """Accessing the deprecated alias via the class proxy emits FutureWarning.

        ``attrs_mapping`` operates at the class-proxy level: ``DepAutoExpandDC.old_field``
        routes through the proxy's ``__getattr__``, emits FutureWarning, and returns the
        value of the canonical field.  Instance-level attribute access is not proxied —
        instances returned by the callable target are plain dataclass objects.
        """
        with pytest.warns(FutureWarning):
            _ = DepAutoExpandDC.old_field  # class-proxy access, not instance attr

    def test_auto_expanded_keys_recorded_on_deprecated_meta(self) -> None:
        """``args_mapping_auto_expanded`` on ``__deprecated__`` lists the auto-copied key.

        Audit tools read ``DeprecationConfig.args_mapping_auto_expanded`` to distinguish
        auto-generated entries from user-supplied ones.
        """
        meta = object.__getattribute__(DepAutoExpandDC, "__deprecated__")
        assert "old_field" in meta.args_mapping_auto_expanded

    def test_explicit_args_mapping_not_overwritten(self) -> None:
        """Explicit ``args_mapping`` entry for the same key is not overwritten.

        When the user supplies ``args_mapping={"old_field": ...}`` explicitly, the auto-
        expand skips that key so the user-supplied value always wins.
        """
        proxy = deprecated_class(
            attrs_mapping={"old_field": "new_field"},
            args_mapping={"old_field": "new_field"},
            deprecated_in="1.0",
            remove_in="2.0",
        )(AutoExpandDC)
        meta = object.__getattribute__(proxy, "__deprecated__")
        assert "old_field" not in meta.args_mapping_auto_expanded

    def test_drop_mapping_entry_not_auto_expanded(self) -> None:
        """attrs_mapping={'field': None} (drop-mapping) is not auto-expanded into args_mapping.

        Drop entries (value=None) signal attribute removal, not renaming to a dataclass field.
        Auto-expansion only applies when the redirect value names a dataclass __init__ parameter.
        A None value has no target field name, so expansion must be skipped for that entry.

        """

        @dc_decorator
        class _Target:
            field: int = 0

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            proxy = deprecated_class(
                target=_Target, deprecated_in="1.0", remove_in="2.0", attrs_mapping={"field": None}
            )(_Target)
        # Drop-mapping entry must not appear in args_mapping (no auto-expansion for None values)
        dep = object.__getattribute__(proxy, "__deprecated__")
        assert dep.args_mapping is None or "field" not in (dep.args_mapping or {})

    def test_req_dc_constructor_kwarg_warns_after_auto_expand(self) -> None:
        """``DepAutoExpandReqDC(old_field=5)`` emits FutureWarning and returns a correctly populated instance.

        ``AutoExpandReqDC`` has a required (no-default) field ``new_field``.  After auto-expand
        the proxy has ``args_mapping={"old_field": "new_field"}`` so calling with the deprecated
        kwarg must (a) emit ``FutureWarning`` and (b) create an instance where ``new_field == 5``.
        Without auto-expand the call would raise ``TypeError`` because ``new_field`` is required
        and ``old_field`` is not recognised by the dataclass constructor.
        """
        with pytest.warns(FutureWarning):
            instance = DepAutoExpandReqDC(old_field=5)  # type: ignore[call-arg]
        assert instance.new_field == 5

    def test_req_dc_class_proxy_attribute_access_warns_then_raises(self) -> None:
        """Accessing ``DepAutoExpandReqDC.old_field`` emits FutureWarning then raises AttributeError.

        ``AutoExpandReqDC`` has a required field ``new_field`` with no class-level default.
        The proxy emits a ``FutureWarning`` (redirect from ``old_field`` → ``new_field``) before
        attempting the attribute read; because there is no class-level sentinel value for a required
        dataclass field, the lookup then raises ``AttributeError``.  Both signals must occur — the
        warning fires first, the error follows.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(AttributeError):
                _ = DepAutoExpandReqDC.old_field  # type: ignore[attr-defined]

        future_warns = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert len(future_warns) >= 1, "FutureWarning must be emitted before AttributeError"

    def test_init_false_field_excluded_from_auto_expand(self) -> None:
        """``field(init=False)`` entries must not appear in ``args_mapping_auto_expanded``.

        ``AutoExpandInitFalseDC`` has ``new_field`` (normal param) and ``computed_field``
        (``init=False``).  Only ``old_field`` → ``new_field`` should be auto-expanded;
        ``old_computed`` → ``computed_field`` must be excluded because ``computed_field``
        is not a valid ``__init__`` kwarg — passing it to the constructor raises ``TypeError``.
        """
        meta = object.__getattribute__(DepAutoExpandInitFalseDC, "__deprecated__")
        assert "old_field" in meta.args_mapping_auto_expanded
        assert "old_computed" not in meta.args_mapping_auto_expanded

    def test_init_false_field_constructor_not_erroring(self) -> None:
        """Calling the proxy with the normal kwarg succeeds; init=False kwarg is not passed.

        Before the fix, ``computed_field`` was included in ``args_mapping``, so passing
        ``old_computed=5`` would remap it to ``computed_field=5`` and pass it to the
        dataclass constructor, raising ``TypeError``.  After the fix the proxy never
        adds ``computed_field`` to ``args_mapping``, so construction via the normal
        param path succeeds.
        """
        with pytest.warns(FutureWarning):
            instance = DepAutoExpandInitFalseDC(old_field=3)  # type: ignore[call-arg]
        assert instance.new_field == 3
        assert instance.computed_field == 0  # default, not touched by constructor

    def test_overridden_init_in_range_field_auto_expanded(self) -> None:
        """Fields present in the overridden ``__init__`` signature are auto-expanded.

        ``AutoExpandOverriddenInitDC`` overrides ``__init__`` to accept only ``new_field``.
        ``old_field`` → ``new_field`` must appear in ``args_mapping_auto_expanded`` because
        ``new_field`` is in ``inspect.signature``.
        """
        meta = object.__getattribute__(DepAutoExpandOverriddenInitDC, "__deprecated__")
        assert "old_field" in meta.args_mapping_auto_expanded

    def test_overridden_init_absent_field_not_auto_expanded(self) -> None:
        """Fields absent from the overridden ``__init__`` signature are not auto-expanded.

        ``skipped_field`` is a dataclass field (``init=True`` by default in the ``@dataclass``
        descriptor) but intentionally absent from the overridden ``__init__``.  Using
        ``dataclasses.fields()`` would (incorrectly) include it; ``inspect.signature`` correctly
        excludes it.  Passing ``old_skipped`` to the constructor must not raise ``TypeError``.
        """
        meta = object.__getattribute__(DepAutoExpandOverriddenInitDC, "__deprecated__")
        assert "old_skipped" not in meta.args_mapping_auto_expanded

    def test_overridden_init_constructor_kwarg_warns(self) -> None:
        """Calling ``DepAutoExpandOverriddenInitDC`` with the deprecated kwarg warns and constructs.

        The old kwarg ``old_field`` is remapped to ``new_field`` via auto-expanded
        ``args_mapping``; the instance is created by the overridden ``__init__`` which
        accepts ``new_field``.
        """
        with pytest.warns(FutureWarning):
            instance = DepAutoExpandOverriddenInitDC(old_field=7)  # type: ignore[call-arg]
        assert instance.new_field == 7

    def test_positional_arg_warns_on_deprecated_class_construction(self) -> None:
        """``DepAutoExpandDC(5)`` warns because the class is deprecated even with no deprecated kwarg.

        The class itself is deprecated; the proxy always emits FutureWarning on construction,
        regardless of whether the caller uses the old or new field names.
        """
        with pytest.warns(FutureWarning):
            instance = DepAutoExpandDC(5)
        assert instance.new_field == 5

    def test_new_kwarg_wins_when_both_old_and_new_provided_old_first(self) -> None:
        """When old and new kwarg both passed (old first), the explicit new-name value wins.

        ``DepAutoExpandDC(old_field=5, new_field=6)`` must use ``new_field=6`` after
        remapping ``old_field`` to ``new_field``; the explicit new-name value takes precedence
        over the remapped old-field value.
        """
        with pytest.warns(FutureWarning):
            instance = DepAutoExpandDC(old_field=5, new_field=6)  # type: ignore[call-arg]
        assert instance.new_field == 6

    def test_new_kwarg_wins_when_both_old_and_new_provided_new_first(self) -> None:
        """New-name value wins regardless of whether old or new kwarg is listed first in the call.

        Before the precedence fix, ``DepAutoExpandDC(new_field=6, old_field=5)`` produced
        ``new_field=5`` because the dict-comprehension last-write-wins caused the
        ``old_field → new_field`` rename to overwrite the explicitly passed ``new_field=6``.
        """
        with pytest.warns(FutureWarning):
            instance = DepAutoExpandDC(new_field=6, old_field=5)  # type: ignore[call-arg]
        assert instance.new_field == 6


# ---------------------------------------------------------------------------
# Positional-only constructor guard + positional forwarding
# ---------------------------------------------------------------------------


class TestPositionalOnlyForwarding:
    """``args_mapping`` on a class with ``POSITIONAL_ONLY`` constructor parameters.

    The proxy must emit ``UserWarning`` at decoration time, record
    ``args_mapping_positional_only`` on ``DeprecationConfig``, and at call time reorder
    the remapped values into positional arguments by target-signature declaration order
    (the decorator's split approach) — forwarding ``new_val=7`` as a keyword would raise
    ``TypeError``, and the historical pop-and-``setattr`` fallback broke required params,
    immutable instances, and constructor-derived state.
    """

    def test_decoration_emits_user_warning(self) -> None:
        """Creating ``deprecated_class`` with ``args_mapping`` to a positional-only param warns.

        The ``UserWarning`` must mention that the target parameter is positional-only and
        that the remapped values are forwarded positionally, so developers understand the
        call shape their users will hit.
        """
        with pytest.warns(UserWarning, match="POSITIONAL_ONLY"):
            deprecated_class(
                args_mapping={"old_val": "new_val"},
                deprecated_in="1.0",
                remove_in="2.0",
            )(PositionalOnlyTarget)

    def test_incompatible_key_recorded_on_deprecated_meta(self) -> None:
        """``args_mapping_positional_only`` on ``__deprecated__`` lists the offending old key.

        The field is populated at decoration time so audit tools can surface it without
        re-inspecting the constructor signature at report time.
        """
        meta = object.__getattribute__(DepPositionalOnly, "__deprecated__")
        assert "old_val" in meta.args_mapping_positional_only

    def test_call_with_deprecated_kwarg_does_not_crash(self) -> None:
        """``DepPositionalOnly(old_val=7)`` succeeds via positional forwarding.

        Remapping ``old_val``→``new_val`` must not produce
        ``PositionalOnlyTarget(new_val=7)`` (a ``TypeError``); the proxy reorders the
        remapped value into the constructor's positional slot instead.
        """
        with pytest.warns(FutureWarning):
            instance = DepPositionalOnly(old_val=7)  # type: ignore[call-arg]
        assert instance.new_val == 7

    def test_required_positional_only_param_constructs(self) -> None:
        """A *required* positional-only constructor param is satisfied by the remapped value.

        A library renames ``old_val`` to a required positional-only ``new_val`` in the
        replacement class.  The historical ``setattr`` fallback crashed before it ever ran
        (``TypeError: missing 1 required positional argument``); positional forwarding
        must construct the instance correctly.
        """
        with pytest.warns(FutureWarning):
            instance = DepPositionalOnlyRequired(old_val=5)  # type: ignore[call-arg]
        assert instance.new_val == 5

    def test_immutable_target_constructs_via_positional_forwarding(self) -> None:
        """An immutable target (``__setattr__`` raises) works because no post-hoc setattr happens.

        Frozen-dataclass-style targets reject attribute assignment, so the old fallback
        raised even when the positional-only param had a default.  Forwarding the value
        through the constructor sidesteps mutation entirely.
        """
        with pytest.warns(FutureWarning):
            instance = DepPositionalOnlyImmutable(old_val=3)  # type: ignore[call-arg]
        assert instance.new_val == 3

    def test_constructor_derivation_runs(self) -> None:
        """State derived inside the constructor reflects the remapped value.

        The old ``setattr`` fallback assigned ``new_val`` *after* construction, silently
        bypassing any ``__post_init__``-style derivation — ``double`` stayed at its
        zero-argument value.  Positional forwarding runs the real constructor body.
        """
        with pytest.warns(FutureWarning):
            instance = DepPositionalOnlyDerived(old_val=7)  # type: ignore[call-arg]
        assert instance.new_val == 7
        assert instance.double == 14

    def test_positional_args_and_mapped_kwarg_combine(self) -> None:
        """Caller positional args fill the leading slots; the remapped value follows them.

        ``DepPositionalOnlyMixed(1, old_x=5)`` targets ``__init__(self, w, x, /)``: the
        caller's ``1`` binds to ``w`` and the remapped ``old_x``→``x`` value must land in
        the *next* positional slot — not trip the gap guard on ``w``.
        """
        with pytest.warns(FutureWarning):
            instance = DepPositionalOnlyMixed(1, old_x=5)  # type: ignore[call-arg]
        assert instance.w == 1
        assert instance.x == 5


class TestProxyIntrospectionProbes:
    """Introspection probes must not warn spuriously or exhaust the warn budget.

    With the default ``num_warns=1`` the single warning a user ever sees must not be consumed by
    machinery — ``hasattr`` duck-typing probes, ``copy.deepcopy`` protocol probes, or doc tools
    reading dunders — otherwise the first *real* deprecated access is silent.
    """

    def test_hasattr_missing_does_not_warn(self) -> None:
        """A library duck-types the config object with ``hasattr`` for an absent attribute.

        The failed probe must stay silent: the attribute is resolved first and the warning fires
        only on successful access.
        """
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert not hasattr(proxy, "missing_attr")
        assert not caught

    def test_missing_attribute_raises_without_burning_budget(self) -> None:
        """After a failed attribute lookup the first real access must still warn.

        The ``num_warns=1`` budget must survive the ``AttributeError`` path so the deprecation
        notice reaches actual user code.
        """
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", num_warns=1)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            with pytest.raises(AttributeError):
                _ = proxy.missing_attr
        with pytest.warns(FutureWarning, match=r"The `cfg` was deprecated since v1\.0"):
            _ = proxy.get

    def test_dunder_access_does_not_warn(self) -> None:
        """A doc tool reads ``__mro__`` on a deprecated class alias (sphinx-style introspection).

        Dunder reads are machinery, never a user migrating code — they must neither warn nor
        consume the budget, mirroring the ``__instancecheck__`` "structural check" rationale.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = DeprecatedColorEnum.__mro__
        assert not caught

    def test_deepcopy_does_not_consume_warn_budget(self) -> None:
        """``copy.deepcopy`` probes copy-protocol dunders before copying.

        Those probes must not consume the ``num_warns=1`` budget: the first real access on the
        original proxy after a deepcopy must still emit the warning.
        """
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", num_warns=1)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            copy.deepcopy(proxy)
        assert not caught
        with pytest.warns(FutureWarning, match=r"The `cfg` was deprecated since v1\.0"):
            _ = proxy.get


class TestProxyClassProperty:
    """``__class__`` forwarding gives instance-side type transparency.

    Downstream code type-checks deprecated objects (json encoders, pydantic validators, plain
    defensive ``isinstance``); wrapping an object in a proxy must not change those checks during
    the migration window.
    """

    def test_isinstance_of_wrapped_builtin_type(self) -> None:
        """Consumer code calls ``isinstance(cfg, dict)`` on a deprecated config dict."""
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert isinstance(proxy, dict)

    def test_isinstance_against_abc(self) -> None:
        """Consumer code checks the deprecated config against ``collections.abc.Mapping``."""
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert isinstance(proxy, Mapping)

    def test_isinstance_uses_target_when_set(self) -> None:
        """With a target configured, ``__class__`` reflects the active (target) object's type."""
        proxy = _DeprecatedProxy(obj=[1], target={"a": 1}, name="x", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert isinstance(proxy, dict)
        assert not isinstance(proxy, list)

    def test_isinstance_walks_stacked_proxies(self) -> None:
        """A proxy stacked over another proxy resolves ``__class__`` to the innermost object."""
        inner = deprecated_instance({"k": 1}, name="inner", deprecated_in="1.0", remove_in="2.0", stream=None)
        outer = deprecated_instance(inner, name="outer", deprecated_in="1.1", remove_in="2.0", stream=None)
        assert isinstance(outer, dict)

    def test_class_proxy_keeps_reporting_proxy_class(self) -> None:
        """A deprecated class alias must not claim to be a ``type``.

        Class-dispatching decorators (e.g. PEP 702 ``typing_extensions.deprecated`` stacked over
        the proxy) branch on ``isinstance(arg, type)`` — forwarding the metaclass would make them
        patch the wrapped class in place instead of wrapping the proxy callable.  Class-side
        transparency is covered by ``__instancecheck__``/``__subclasscheck__`` instead.
        """
        assert DeprecatedColorEnum.__class__ is _DeprecatedProxy
        assert not isinstance(DeprecatedColorEnum, type)

    def test_type_still_reveals_proxy(self) -> None:
        """``type(proxy)`` keeps returning the proxy class for code that needs the truth."""
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert type(proxy) is _DeprecatedProxy
        assert isinstance(proxy, _DeprecatedProxy)

    def test_isinstance_does_not_warn(self) -> None:
        """``isinstance(proxy, dict)`` is structural — no warning, budget untouched."""
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", num_warns=1)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            isinstance(proxy, dict)
        assert not caught
        with pytest.warns(FutureWarning, match=r"The `cfg` was deprecated since v1\.0"):
            _ = proxy.get


class TestProxyCopyPickle:
    """``copy.copy`` / ``copy.deepcopy`` / pickle round-trips reconstruct the proxy.

    A library ships a deprecated module-level config dict wrapped in ``deprecated_instance``;
    downstream consumers routinely snapshot such configs with ``copy``/``deepcopy`` or ship them
    across process boundaries via pickle.  All three must return a working proxy (deprecation
    semantics preserved) instead of crashing with ``RecursionError``.
    """

    def test_copy_returns_proxy_wrapping_copied_object(self) -> None:
        """A consumer takes a shallow working copy of a deprecated config dict.

        The copy must be a proxy again (the deprecation travels with the object) and must wrap
        an independent shallow copy — mutating the copy must not touch the original config.
        """
        proxy = deprecated_instance({"threshold": 0.5}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        dup = copy.copy(proxy)
        assert type(dup) is _DeprecatedProxy
        assert dup["threshold"] == 0.5
        dup["threshold"] = 0.9
        assert proxy["threshold"] == 0.5

    def test_deepcopy_returns_proxy_with_independent_nested_state(self) -> None:
        """A consumer deep-copies a deprecated nested config before mutating it.

        ``deepcopy`` of the proxy must deep-copy the wrapped object so nested containers are
        fully independent of the original.
        """
        proxy = deprecated_instance(
            {"limits": {"low": 1}}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None
        )
        dup = copy.deepcopy(proxy)
        assert type(dup) is _DeprecatedProxy
        dup["limits"]["low"] = 99
        assert proxy["limits"]["low"] == 1

    def test_copy_preserves_deprecation_metadata(self) -> None:
        """Audit tools must still discover a copied proxy via its ``__deprecated__`` metadata."""
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        dup = copy.copy(proxy)
        meta = object.__getattribute__(dup, "__deprecated__")
        assert meta.name == "cfg"
        assert meta.deprecated_in == "1.0"
        assert meta.remove_in == "2.0"

    def test_pickle_roundtrip_preserves_proxy_and_warning(self) -> None:
        """A deprecated config object crosses a process boundary via pickle.

        The unpickled object must be a proxy again with intact metadata and default warning
        stream — the first real access on the restored proxy still emits ``FutureWarning``.
        """
        proxy = deprecated_instance({"k": 41}, name="cfg", deprecated_in="1.0", remove_in="2.0")

        restored = pickle.loads(pickle.dumps(proxy))  # noqa: S301
        assert type(restored) is _DeprecatedProxy
        assert object.__getattribute__(restored, "__deprecated__").name == "cfg"
        with pytest.warns(FutureWarning, match=r"The `cfg` was deprecated since v1\.0"):
            assert restored["k"] == 41

    def test_deepcopy_of_class_proxy_keeps_wrapped_class(self) -> None:
        """Deep-copying a deprecated class alias keeps forwarding to the same target class.

        Classes are atomic under ``deepcopy``, so the copied proxy must forward to the identical
        target class object.
        """
        dup = copy.deepcopy(DeprecatedColorEnum)
        assert type(dup) is _DeprecatedProxy
        assert dup.RED is ColorEnum.RED

    def test_uninitialised_instance_raises_attribute_error(self) -> None:
        """Copy/pickle machinery creates instances via ``cls.__new__(cls)`` with no config set.

        Attribute access on such a half-initialised proxy must raise a clean ``AttributeError``
        (routing back into ``__getattr__`` must not recurse into the ``_cfg`` property again).
        """
        blank = _DeprecatedProxy.__new__(_DeprecatedProxy)
        with pytest.raises(AttributeError):
            _ = blank.anything

    def test_copy_of_exhausted_proxy_is_silent(self) -> None:
        """A copy of an already-warned proxy inherits the exhausted budget and stays silent.

        With ``num_warns=1`` the original can warn exactly once.  After that warning fires,
        copying the proxy snapshots the exhausted counter — the copy never warns, even though
        it was never accessed before the copy.  Each copy is independent of the original but
        inherits the counter value at copy time, not a fresh budget.
        """
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", num_warns=1)
        with pytest.warns(FutureWarning):
            _ = proxy["k"]  # exhaust the budget on the original
        dup = copy.copy(proxy)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            _ = dup["k"]  # must not warn — counter is snapshotted as exhausted

    def test_pickle_raises_for_nonpicklable_stream(self) -> None:
        """A proxy with a non-picklable stream (lambda) cannot be pickled.

        The ``stream`` callable is serialised as part of the internal config; lambdas and
        closures are not picklable by the standard pickle protocol.  This test documents the
        known limitation so future changes do not silently regress it.
        """
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=lambda msg: None)
        with pytest.raises((AttributeError, pickle.PicklingError)):
            pickle.dumps(proxy)

    def test_pickle_raises_for_class_proxy(self) -> None:
        """Pickling a deprecated_class proxy is not supported when the class name is replaced.

        When ``@deprecated_class`` replaces the module-level name ``DeprecatedColorEnum`` with
        a proxy, the wrapped class (``cfg.obj``) cannot be serialised by reference: pickle
        finds the proxy at ``tests.collection_deprecate.DeprecatedColorEnum``, not the original
        class.  This test documents the known limitation — ``deprecated_instance`` proxies
        wrapping plain objects (dicts, lists) remain fully picklable.
        """
        with pytest.raises(pickle.PicklingError):
            pickle.dumps(DeprecatedColorEnum)


class TestOperatorForwarding:
    """Type-level operator, conversion, and context-manager dunders forward to the active object."""

    @pytest.mark.parametrize(
        ("op", "expected"),
        [
            pytest.param(lambda p: p + 2, 5, id="add"),
            pytest.param(lambda p: p - 1, 2, id="sub"),
            pytest.param(lambda p: p * 3, 9, id="mul"),
            pytest.param(lambda p: p // 2, 1, id="floordiv"),
            pytest.param(lambda p: p % 2, 1, id="mod"),
            pytest.param(lambda p: p**2, 9, id="pow"),
            pytest.param(lambda p: p << 1, 6, id="lshift"),
            pytest.param(lambda p: p & 1, 1, id="and"),
            pytest.param(lambda p: 10 + p, 13, id="radd"),
            pytest.param(lambda p: 10 - p, 7, id="rsub"),
            pytest.param(lambda p: -p, -3, id="neg"),
            pytest.param(abs, 3, id="abs"),
            pytest.param(lambda p: ~p, -4, id="invert"),
        ],
    )
    def test_arithmetic_forwards_to_active(self, op: Callable[[Any], Any], expected: Any) -> None:  # noqa: ANN401
        """Arithmetic, reflected, and unary operators compute against the wrapped value.

        A project deprecating a module-level numeric constant wraps it in ``deprecated_instance``; downstream
        expressions such as ``THRESHOLD + 2`` must keep working during the migration window rather than raising
        ``TypeError`` because the operand is a proxy.
        """
        proxy = deprecated_instance(3, name="n", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert op(proxy) == expected

    @pytest.mark.parametrize(
        ("op", "expected"),
        [
            pytest.param(lambda p: p < 5, True, id="lt"),
            pytest.param(lambda p: p <= 3, True, id="le"),
            pytest.param(lambda p: p > 5, False, id="gt"),
            pytest.param(lambda p: p >= 3, True, id="ge"),
        ],
    )
    def test_ordering_forwards_to_active(self, op: Callable[[Any], Any], expected: bool) -> None:
        """Ordering comparisons delegate to the wrapped value so sorting and thresholding stay transparent.

        Code that keeps a proxied legacy value in a sorted container or gates on ``value >= limit`` must observe the
        underlying object's ordering, not a proxy-identity fallback.
        """
        proxy = deprecated_instance(3, name="n", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert op(proxy) is expected

    def test_unsupported_binary_op_raises_type_error(self) -> None:
        """An operator the wrapped object lacks raises the normal ``TypeError`` instead of being masked.

        Because the forwarding dunder returns ``NotImplemented`` when the active type has no implementation, Python's
        own operand-resolution path runs and produces the same ``TypeError`` a user would see without the proxy —
        adding a dict and an int here.
        """
        proxy: Any = deprecated_instance({"a": 1}, name="d", deprecated_in="1.0", remove_in="2.0", stream=None)
        with pytest.raises(TypeError):
            _ = proxy + 1

    def test_unsupported_binary_op_does_not_warn(self) -> None:
        """When the wrapped type has no implementation for an operator the proxy returns ``NotImplemented`` silently.

        A proxy wrapping a ``dict`` has no ``__add__``; Python first tries ``dict.__add__(proxy, 1)`` (the
        forwarding dunder returns ``NotImplemented``), then tries ``int.__radd__(1, proxy)`` (also
        ``NotImplemented``), and finally raises ``TypeError``. The warning must not fire during the
        ``NotImplemented`` phase because the operation did not succeed.
        """
        proxy: Any = deprecated_instance({"a": 1}, name="d", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(TypeError):
                _ = proxy + 1
        assert not [w for w in caught if issubclass(w.category, FutureWarning)]

    def test_three_arg_pow_unwraps_modulus_proxy(self) -> None:
        """``pow(proxy_base, exp, proxy_mod)`` unwraps both proxy operands before calling ``int.__pow__``.

        ``pow(base, exp, mod)`` calls ``__pow__(base, exp, mod)`` with three arguments; when ``mod`` is itself
        a proxy its raw form must reach the underlying ``int.__pow__`` implementation.  Without unwrapping,
        ``int.__pow__`` receives a ``_DeprecatedProxy`` as the modulus and raises ``TypeError``.
        """
        proxy_base: Any = deprecated_instance(7, name="base", deprecated_in="1.0", remove_in="2.0", stream=None)
        proxy_mod: Any = deprecated_instance(3, name="mod", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert pow(proxy_base, 2, proxy_mod) == 1  # 7**2 % 3 == 49 % 3 == 1

    def test_inplace_add_on_immutable_rebinds_to_result(self) -> None:
        """``+=`` on a proxied immutable rebinds the name to the computed result (an int), not a re-wrapped proxy."""
        proxy: Any = deprecated_instance(3, name="n", deprecated_in="1.0", remove_in="2.0", stream=None)
        proxy += 2
        assert (proxy, isinstance(proxy, int)) == (5, True)

    def test_inplace_add_on_mutable_mutates_and_returns_active(self) -> None:
        """``+=`` on a proxied list extends the wrapped list in place and yields the active list result."""
        proxy: Any = deprecated_instance([1, 2], name="lst", deprecated_in="1.0", remove_in="2.0", stream=None)
        proxy += [3]
        assert proxy == [1, 2, 3]

    def test_numeric_and_path_conversions_forward(self) -> None:
        """``int()``, ``float()``, ``__index__`` (via ``bin``) forward to the wrapped number.

        Legacy numeric handles are frequently passed to APIs that coerce with ``int()``/``float()`` or use the value
        as an index; all of these must transparently reach the wrapped object.
        """
        proxy = deprecated_instance(3, name="n", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert (int(proxy), float(proxy), bin(proxy)) == (3, 3.0, "0b11")

    def test_reversed_forwards(self) -> None:
        """``reversed()`` iterates the wrapped sequence in reverse."""
        proxy = deprecated_instance([1, 2, 3], name="lst", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert list(reversed(proxy)) == [3, 2, 1]

    def test_fspath_forwards(self) -> None:
        """``os.fspath()`` returns the wrapped path string so proxies work with filesystem APIs."""
        proxy = deprecated_instance("data/legacy.txt", name="p", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert os.fspath(proxy) == "data/legacy.txt"

    def test_context_manager_protocol_forwards(self) -> None:
        """A proxied context manager forwards ``__enter__``/``__exit__`` so ``with proxy:`` drives the resource.

        Wrapping a legacy resource handle (session, file-like object) in ``deprecated_instance`` must not break
        callers that still use it in a ``with`` block; both transitions reach the underlying resource.
        """
        resource = ManagedResource()
        proxy = deprecated_instance(resource, name="res", deprecated_in="1.0", remove_in="2.0", stream=None)
        with proxy as entered:
            pass
        assert (resource.entered, resource.exited, entered is resource) == (True, True, True)

    def test_arithmetic_warns_once(self) -> None:
        """Using the proxy in an arithmetic expression emits one deprecation warning (a data-producing use)."""
        proxy: Any = deprecated_instance(3, name="n", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning, match=r"The `n` was deprecated since v1\.0"):
            _ = proxy + 1

    def test_ordering_comparison_is_silent(self) -> None:
        """Ordering comparisons are cheap probes and emit no warning, mirroring ``__eq__`` and ``__len__``."""
        proxy: Any = deprecated_instance(3, name="n", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = proxy < 5
        assert not [w for w in caught if issubclass(w.category, FutureWarning)]

    def test_context_entry_warns_once_exit_silent(self) -> None:
        """Context entry warns once and exit stays silent, so a ``with`` block yields exactly one warning."""
        resource = ManagedResource()
        proxy = deprecated_instance(resource, name="res", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with proxy:
                pass
        assert len([w for w in caught if issubclass(w.category, FutureWarning)]) == 1

    def test_round_no_ndigits_forwards_and_warns(self) -> None:
        """``round(proxy)`` routes through ``__round__`` (branch 1: no ndigits) and emits FutureWarning.

        A project deprecating a legacy float constant must still be usable in ``round(LEGACY_CONST)``
        calls; and the round operation counts as data-producing use, so a warning is expected.
        """
        proxy = deprecated_instance(3.7, name="n", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning, match=r"The `n` was deprecated since v1\.0"):
            result = round(proxy)
        assert result == 4

    def test_round_with_ndigits_forwards_and_warns(self) -> None:
        """``round(proxy, 2)`` routes through ``__round__`` (branch 2: ndigits given) and emits FutureWarning.

        The two-argument form is the most common rounding call in numerical code; both the forwarding
        and the warning policy must hold for this signature.
        """
        proxy = deprecated_instance(3.789, name="n", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning, match=r"The `n` was deprecated since v1\.0"):
            result = round(proxy, 2)
        assert result == 3.79

    @pytest.mark.parametrize(
        ("func", "expected"),
        [
            pytest.param(math.trunc, 3, id="trunc"),
            pytest.param(math.floor, 3, id="floor"),
            pytest.param(math.ceil, 4, id="ceil"),
        ],
    )
    def test_math_rounding_functions_forward_and_warn(self, func: Callable[[Any], Any], expected: int) -> None:
        """``math.trunc/floor/ceil`` on a proxy forward to the wrapped value and each emit FutureWarning.

        Libraries and ORMs that coerce legacy float handles via ``math.floor`` or ``math.ceil`` must
        still receive the correct integer result; because these are numeric reads, each is a data-use
        operation that consumes the warn budget.
        """
        proxy = deprecated_instance(3.7, name="n", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning, match=r"The `n` was deprecated since v1\.0"):
            result = func(proxy)
        assert result == expected

    def test_next_forwards_value_and_warns(self) -> None:
        """``next(proxy)`` on a proxied iterator forwards to the active iterator and emits FutureWarning.

        Wrapping a legacy iterator handle (e.g. a generator or file pointer) in ``deprecated_instance``
        must not break code that advances it with ``next()``; the call is a data-producing read, so the
        warning fires.
        """
        proxy = deprecated_instance(iter([10, 20]), name="it", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning, match=r"The `it` was deprecated since v1\.0"):
            result = next(proxy)
        assert result == 10


class TestAsyncProtocolForwarding:
    """Async protocol dunders forward to the active object so ``async with`` / ``async for`` / ``await`` keep working.

    The proxy hand-forwards ``__aenter__``/``__aexit__``/``__aiter__``/``__anext__``/``__await__`` to the wrapped
    async resource. These exercise the real asyncio runtime paths, not just structural presence of the
    methods. Warning policy mirrors the sync analogues: data-driving entry/iteration/await warn once; the
    ``__aexit__`` cleanup half stays silent.
    """

    @pytest.mark.asyncio
    async def test_async_context_manager_forwards(self) -> None:
        """A proxied async context manager drives ``async with proxy:`` through to the wrapped resource.

        A migration wraps a legacy async session in ``deprecated_instance``; callers that still write
        ``async with session:`` must reach the underlying ``__aenter__``/``__aexit__`` and receive the real
        resource as the bound value.
        """
        resource = AsyncManagedResource()
        proxy = deprecated_instance(resource, name="res", deprecated_in="1.0", remove_in="2.0", stream=None)
        async with proxy as entered:
            pass
        assert (resource.entered, resource.exited, entered is resource) == (True, True, True)

    @pytest.mark.asyncio
    async def test_async_context_entry_warns_once_exit_silent(self) -> None:
        """Async context entry warns exactly once; the async exit half emits no warning."""
        resource = AsyncManagedResource()
        proxy = deprecated_instance(resource, name="res", deprecated_in="1.0", remove_in="2.0")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            async with proxy:
                pass
        assert len([w for w in caught if issubclass(w.category, FutureWarning)]) == 1

    @pytest.mark.asyncio
    async def test_async_iteration_forwards(self) -> None:
        """``async for`` over a proxied async iterator yields every item from the wrapped resource.

        A deprecated async stream handle wrapped in ``deprecated_instance`` must remain iterable so
        ``async for chunk in stream:`` keeps producing the underlying sequence during the migration window.
        """
        resource = AsyncManagedResource([1, 2, 3])
        proxy = deprecated_instance(resource, name="stream", deprecated_in="1.0", remove_in="2.0", stream=None)
        collected = [item async for item in proxy]
        assert collected == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_async_iteration_warns_on_aiter(self) -> None:
        """Entering async iteration emits a deprecation warning (a data-producing use, like sync ``__iter__``)."""
        resource = AsyncManagedResource([1, 2])
        proxy = deprecated_instance(resource, name="stream", deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(FutureWarning, match=r"The `stream` was deprecated since v1\.0"):
            _ = [item async for item in proxy]

    @pytest.mark.asyncio
    async def test_anext_forwards_to_active(self) -> None:
        """Calling ``__anext__`` on the proxy advances the wrapped async iterator directly.

        ``async for`` binds iteration to the object returned by ``__aiter__`` (the resource itself), so the proxy's
        own ``__anext__`` is only reached by an explicit ``await proxy.__anext__()`` — this pins that forwarding path.
        """
        resource = AsyncManagedResource([10, 20])
        proxy = deprecated_instance(resource, name="stream", deprecated_in="1.0", remove_in="2.0", stream=None)
        proxy.__aiter__()
        assert await proxy.__anext__() == 10

    @pytest.mark.asyncio
    async def test_await_forwards_to_active(self) -> None:
        """``await proxy`` awaits the wrapped awaitable and returns its result.

        Wrapping an awaitable handle in ``deprecated_instance`` must keep ``result = await handle`` working, routing
        through the proxy's ``__await__`` to the resource's coroutine.
        """
        resource = AsyncManagedResource()
        proxy = deprecated_instance(resource, name="awaitable", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert await proxy == "awaited"


class TestProxySubclassing:
    """PEP 560 subclassing of deprecated class aliases via ``__mro_entries__``."""

    def test_subclass_derives_from_active_class(self) -> None:
        """``class Child(DeprecatedAlias)`` transparently subclasses the wrapped class and inherits its behaviour.

        During a migration window a public base class is renamed and wrapped in ``deprecated_class``; existing
        downstream ``class Child(OldName): ...`` definitions must keep resolving to a real, usable base class.
        """
        alias = deprecated_class(deprecated_in="1.0", remove_in="2.0", stream=None)(SubclassableBase)

        class Child(alias):  # type: ignore[misc,valid-type]
            """Subclass built off the deprecated alias."""

        assert (issubclass(Child, SubclassableBase), Child().greet()) == (True, "hello")

    def test_subclassing_warns_once(self) -> None:
        """Subclassing a deprecated alias is a use of the deprecated name and emits one deprecation warning."""
        alias = deprecated_class(deprecated_in="1.0", remove_in="2.0")(SubclassableBase)
        with pytest.warns(FutureWarning, match=r"The `SubclassableBase` was deprecated since v1\.0"):

            class Child(alias):  # type: ignore[misc,valid-type]
                """Subclass whose creation should warn."""

        assert issubclass(Child, SubclassableBase)

    def test_attrs_remap_subclassing_does_not_warn(self) -> None:
        """An ATTRS_REMAP alias deprecates only listed attributes, so subclassing it stays silent.

        Mirrors the ``__call__`` policy: when only specific attribute aliases are deprecated, deriving a new
        class from the alias is not a deprecated surface and must not brand the whole class deprecated.
        """
        alias = deprecated_class(attrs_mapping={"marker": None}, deprecated_in="1.0", remove_in="2.0")(SubclassableBase)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            class Child(alias):  # type: ignore[misc,valid-type]
                """Subclass whose creation must stay silent under ATTRS_REMAP."""

        assert not [w for w in caught if issubclass(w.category, FutureWarning)]
        assert issubclass(Child, SubclassableBase)

    def test_args_remap_subclassing_does_not_warn(self) -> None:
        """An ARGS_REMAP alias deprecates only constructor argument names, so subclassing it stays silent.

        ARGS_REMAP and ATTRS_REMAP share the same subclassing guard in ``__mro_entries__``: both scope
        deprecation to a specific axis (argument names or attribute names) rather than the class name
        itself, so deriving a new class from the alias must not fire any deprecation warning.
        """
        alias = deprecated_class(args_mapping={"old_arg": "new_arg"}, deprecated_in="1.0", remove_in="2.0")(
            SubclassableBase
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            class Child(alias):  # type: ignore[misc,valid-type]
                """Subclass whose creation must stay silent under ARGS_REMAP."""

        assert not [w for w in caught if issubclass(w.category, FutureWarning)]
        assert issubclass(Child, SubclassableBase)


class TestAttrsRemapCallPolicy:
    """Non-stacked ATTRS_REMAP proxies do not warn on plain instantiation/call."""

    def test_instantiation_does_not_warn(self) -> None:
        """Constructing a class whose deprecation covers only listed attributes emits no class-level warning.

        ATTRS_REMAP means "only these attribute aliases are deprecated"; instantiating the class is not a deprecated
        surface, so plain construction must neither warn nor burn the global warn budget.
        """
        alias = deprecated_class(attrs_mapping={"color": "colour"}, deprecated_in="1.0", remove_in="2.0")(Palette)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = alias()
        assert not [w for w in caught if issubclass(w.category, FutureWarning)]

    def test_deprecated_attr_still_warns_after_instantiation(self) -> None:
        """The per-attribute warning still fires after instantiation, since the budget was not consumed by the call."""
        alias = deprecated_class(attrs_mapping={"color": "colour"}, deprecated_in="1.0", remove_in="2.0")(Palette)
        alias()
        with pytest.warns(FutureWarning, match="color"):
            _ = alias.color  # type: ignore[attr-defined]


class _P8Source:
    """Plain source class used as the deprecated ``obj`` of a target-forwarding proxy (fixture)."""


class _P8Target:
    """Plain replacement class used as the ``target`` a proxy actually serves (fixture)."""


class TestProxyIdentityUsesActive:
    """``__eq__`` / ``__hash__`` / ``__repr__`` / ``__str__`` reflect the served (active) object."""

    def test_eq_matches_active_target_not_source(self) -> None:
        """A target-forwarding proxy compares equal to the object it actually serves, not the deprecated source.

        Data access forwards to the active object (the ``target`` when set), so a proxy comparing equal to its
        source while never returning it was inconsistent. Identity now routes through the active object too.
        """
        proxy = _DeprecatedProxy(
            obj=_P8Source, target=_P8Target, name="_P8Source", deprecated_in="1.0", remove_in="2.0"
        )
        assert proxy == _P8Target
        assert proxy != _P8Source

    def test_repr_shows_active_target(self) -> None:
        """``repr`` of a target-forwarding proxy shows the active object rather than the deprecated source."""
        proxy = _DeprecatedProxy(
            obj=_P8Source, target=_P8Target, name="_P8Source", deprecated_in="1.0", remove_in="2.0"
        )
        assert repr(proxy) == repr(_P8Target)


class _P9Config:
    """Active class exposing ``new_attr`` so an ``attrs_mapping`` redirect target validates (fixture)."""

    new_attr = 1


class TestProxyAttrsMappingDefensiveCopy:
    """The frozen config must not alias the caller's mutable ``attrs_mapping`` dict."""

    def test_post_construction_mutation_ignored(self) -> None:
        """Mutating the caller's ``attrs_mapping`` after construction cannot alter the frozen proxy config.

        Storing the caller's dict by reference let a later mutation introduce a redirect cycle that decoration-time
        validation had already rejected; a defensive copy makes the stored mapping immune to caller mutation.
        """
        mapping: dict[str, Any] = {"old_attr": "new_attr"}
        proxy = _DeprecatedProxy(
            obj=_P9Config, name="_P9Config", deprecated_in="1.0", remove_in="2.0", attrs_mapping=mapping
        )
        mapping["old_attr"] = "hijacked"
        assert proxy.__deprecated__.attrs_mapping == {"old_attr": "new_attr"}


class TestProxyStreamStacklevelProbe:
    """A stacklevel-accepting stream that raises internally must not be re-invoked."""

    def test_internal_typeerror_not_swallowed_no_double_call(self) -> None:
        """An internal ``TypeError`` from a custom stream propagates and the stream runs exactly once.

        The old ``try/except TypeError`` fallback masked a ``TypeError`` raised inside a stacklevel-accepting
        stream and re-invoked it, producing duplicate warning side effects; the signature probe avoids that.
        """
        calls: list[str] = []

        def stream(msg: str, stacklevel: int = 1) -> None:
            calls.append(msg)
            raise TypeError("boom from inside the proxy stream")

        proxy = _DeprecatedProxy(
            obj=_P8Source, target=_P8Target, name="_P8Source", deprecated_in="1.0", remove_in="2.0", stream=stream
        )
        with pytest.raises(TypeError, match="boom from inside the proxy stream"):
            proxy._warn()
        assert len(calls) == 1


class TestProxySubclassCheckTypeError:
    """``issubclass`` with an instance proxy raises TypeError like the builtin."""

    def test_issubclass_raises_typeerror_for_non_type_active(self) -> None:
        """Using an instance proxy as ``issubclass`` arg 2 raises TypeError instead of silently returning False."""
        proxy = _DeprecatedProxy(obj={"key": "val"}, name="old_cfg", deprecated_in="1.0", remove_in="2.0")
        with pytest.raises(TypeError, match="arg 2 must be a class"):
            issubclass(int, cast(Any, proxy))
