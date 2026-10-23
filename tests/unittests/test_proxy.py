"""Unit tests for _DeprecatedProxy internals and deprecated_class decorator behaviour."""

import abc
import asyncio
import concurrent.futures
import contextlib
import copy
import gc
import inspect
import itertools
import math
import os
import pickle
import sys
import threading
import warnings
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import FrozenInstanceError
from dataclasses import dataclass as dc_decorator
from enum import Enum
from types import MethodType
from typing import TYPE_CHECKING, Any, cast

import pytest

import deprecate
from deprecate import DeprecatedError, DeprecationProxy
from deprecate._types import TargetMode
from deprecate.deprecation import deprecated
from deprecate.messaging import deprecation_warning
from deprecate.proxy import (
    _DeprecatedProxy,
    _proxy_call_global,
    _ProxySpec,
    deprecated_class,
    deprecated_instance,
)
from tests.collection_deprecate import (
    DepAutoExpandDC,
    DepPositionalOnly,
    DepPositionalOnlyDerived,
    DepPositionalOnlyImmutable,
    DepPositionalOnlyMixed,
    DepPositionalOnlyRequired,
    DeprecatedColorDataClass,
    DeprecatedColorEnum,
    DeprecatedColorEnumFunctional,
    DeprecatedPaletteFunctionalFallback,
    MappedColorEnum,
    MappedDataClass,
    MappedDropArgDataClass,
    WarnOnlyColorEnum,
    ast_breadcrumb_source_dict,
    depr_ast_breadcrumb_dict,
    depr_read_only_attrs_list,
)
from tests.collection_deprecate_forwarding import (
    DepAutoExpandInitFalseDC,
    DepAutoExpandOverriddenInitDC,
    DepAutoExpandReqDC,
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
    ProxyArgsRemapAuto,
    ProxyArgsRemapForArgWarnMessage,
    ProxyCallableWithArgsMapping,
    ProxyClassWithArgsExtra,
    pep702_proxy_stacked,
)
from tests.collection_deprecate_regressions import (
    DeprSourcePathArgs,
    DeprSourcePathAttrs,
    DeprSourcePathClass,
    DeprSourcePathStacked,
    depr_source_path_instance,
    make_deprecated_class_attrs_skip_if_true,
    make_deprecated_class_skip_if_flag,
    make_deprecated_class_skip_if_non_bool,
    make_deprecated_class_skip_if_true,
    make_deprecated_data_host,
    make_deprecated_hostile_signature_instance,
    make_deprecated_instance_over_marked_object,
    make_deprecated_instance_skip_if_true_read_only,
    make_deprecated_method_host,
    make_deprecated_partial_host,
    make_flaky_attr_alias,
    make_read_only_inplace_proxy,
    make_read_only_settings_proxy,
    make_silent_proxy_stack,
    make_skip_gated_target_proxy,
    make_switching_entry_proxy,
    make_warn_after_success_proxy,
)
from tests.collection_misconfigured import make_stacked_legacy_proxy
from tests.collection_pep702 import StackedAlias
from tests.collection_targets import (
    AsyncManagedResource,
    AutoExpandDC,
    CachedStepIterator,
    ColorEnum,
    CombinedAttrsArgsSource,
    CombinedAttrsArgsTarget,
    CountingSkipCondition,
    DecliningInplaceOperand,
    DeferredFailureAwaitable,
    FlakyProtocolBox,
    FlakyTypeErrorStream,
    HostileSignatureCallable,
    KeywordTypeErrorStream,
    LegacyBoolAttrsSource,
    ManagedResource,
    MessageOnlyRecordingStream,
    NewDataClass,
    Palette,
    PaletteEnum,
    PaletteOld,
    PendingSnapshotBox,
    Pep702StaticTarget,
    PositionalOnlyTarget,
    RecordingEntryContext,
    RecordingStream,
    SlowRecordingStream,
    SomeTargetClass,
    SubclassableBase,
    WithInjected,
    _Pep702ProxyTarget,
    base_sum_kwargs,
    legacy_snapshot,
    legacy_tick_stream,
    make_flaky_attr_host,
)


class TestProxySpec:
    """Frozen construction settings kept separate from serialized runtime state."""

    def test_defaults(self) -> None:
        """Keep the original constructor defaults.

        A private caller can provide only a display name and retain the same warn-once, writable proxy policy.
        """
        spec = _ProxySpec(name="legacy")
        assert spec.name == "legacy"
        assert spec.target is None
        assert spec.args_mapping is None
        assert spec.args_extra is None
        assert spec.attrs_mapping is None
        assert spec.deprecated_in == ""
        assert spec.remove_in == ""
        assert spec.num_warns == 1
        assert spec.stream is deprecation_warning
        assert spec.message_template is None
        assert spec.skip_if is False
        assert spec.as_exception is None
        assert spec.read_only is False
        assert spec.docstring_style == "auto"
        assert spec._misconfigured_override is False

    def test_frozen(self) -> None:
        """Reject reassignment of construction settings.

        Once a caller has described a proxy, later field assignments must fail rather than silently change its spec.
        """
        spec = _ProxySpec(name="legacy", deprecated_in="1.0", remove_in="2.0")
        with pytest.raises(FrozenInstanceError):
            setattr(spec, "name", "changed")
        assert spec.name == "legacy"


class TestProxyInit:
    """Internal state initialisation for _DeprecatedProxy instances."""

    def test_internal_state_stored_correctly(self) -> None:
        """Store runtime state separately from public audit metadata.

        Proxy construction must keep mutable warning state private while exposing an immutable configuration through
        the v0.13 metadata contract.
        """
        obj = {"a": 1}
        proxy = _DeprecatedProxy(
            obj=obj,
            spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0", num_warns=3, stream=None),
        )
        cfg = object.__getattribute__(proxy, "_DeprecatedProxy__config")
        assert cfg.obj is obj
        assert cfg.num_warns == 3
        assert cfg.stream is None
        assert cfg.read_only is False
        assert cfg.state.warned_calls == 0
        meta = object.__getattribute__(proxy, "__deprecation_config__")
        assert meta.name == "x"
        assert meta.deprecated_in == "1.0"
        assert meta.remove_in == "2.0"
        assert meta.target is None

    def test_stacking_reads_legacy_proxy_metadata_through_accessor(self) -> None:
        """Stack a current class proxy over metadata created with the legacy layout.

        Rolling upgrades may expose a pre-v0.13 proxy as the input to a current ``deprecated_class`` call. The new
        proxy must preserve its declared name without dereferencing an attribute the inner proxy does not have.
        """
        proxy = make_stacked_legacy_proxy()

        meta = object.__getattribute__(proxy, "__deprecation_config__")
        assert meta.name == "LegacyClass"
        assert meta.args_mapping is None


class TestProxyWarnBehavior:
    """Warning count and message logic."""

    def test_num_warns_zero_never_warns(self) -> None:
        """``num_warns=0`` means never warn."""
        proxy = _DeprecatedProxy(
            obj={"k": 1},
            spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0", num_warns=0),
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn()
            proxy._warn()
        assert not caught

    def test_warn_increments_counter(self) -> None:
        """Each emitted warning increments the internal counter."""
        proxy = _DeprecatedProxy(obj={}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0", num_warns=-1))
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            proxy._warn()
            proxy._warn()
        assert object.__getattribute__(proxy, "_DeprecatedProxy__config").state.warned_calls == 2

    def test_warn_stops_after_limit(self) -> None:
        """Warnings stop once num_warns threshold is reached."""
        proxy = _DeprecatedProxy(obj={}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0", num_warns=2))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            for _ in range(5):
                proxy._warn()
        assert len(caught) == 2

    def test_warn_no_stream(self) -> None:
        """``stream=None`` suppresses all warnings."""
        proxy = _DeprecatedProxy(obj={}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0", stream=None))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn()
        assert not caught

    def test_warn_message_contains_name_and_versions(self) -> None:
        """Warning message includes the name, deprecated_in and remove_in values."""
        proxy = _DeprecatedProxy(obj={}, spec=_ProxySpec(name="legacy_cfg", deprecated_in="2.3", remove_in="4.0"))
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
            spec=_ProxySpec(name="old_color", deprecated_in="1.0", remove_in="2.0", target=ColorEnum),
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn()
        msg = str(caught[0].message)
        assert "old_color" in msg
        assert "tests.collection_targets.ColorEnum" in msg

    def test_warn_category_is_future_warning(self) -> None:
        """Default stream emits FutureWarning attributed to the caller's frame."""
        proxy = _DeprecatedProxy(obj={"k": 1}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            # Trigger via subscript access — exercises the realistic accessor path
            # (``__getitem__ → _warn → stream``) that the stacklevel fix targets.
            _ = proxy["k"]
        assert caught[0].category is FutureWarning
        # ``_DeprecatedProxy._warn`` hands ``stream`` (through ``messaging._emit``) a ``stacklevel`` that counts every
        # pyDeprecate frame, so the warning is attributed to this test file rather than ``proxy.py``.
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
            ProxyArgsRemapForArgWarnMessage(old_key=5)  # type: ignore[call-arg]  # deprecated kwarg via ARGS_REMAP
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


class TestProxyWarnStream:
    """A custom ``stream`` on a proxy is invoked exactly once per warning, and its own errors reach the caller."""

    @pytest.mark.parametrize(
        "stream_cls",
        [
            pytest.param(KeywordTypeErrorStream, id="typeerror-mentions-keyword"),
            pytest.param(FlakyTypeErrorStream, id="unrelated-typeerror"),
        ],
    )
    def test_stream_typeerror_propagates_after_one_call(self, stream_cls: type[RecordingStream]) -> None:
        """A ``TypeError`` raised inside a ``stacklevel``-accepting stream propagates; the stream is not retried.

        A project routes the deprecation notice of a legacy config object through its own logging handler, and the
        handler has a bug that raises ``TypeError("unexpected keyword in handler")``. The proxy must hand that error
        to the caller after one invocation — re-invoking the handler on the strength of the word "keyword" would
        duplicate its side effects (two log lines, two metric increments) and then fail anyway.
        """
        stream = stream_cls()
        proxy = deprecated_instance({"a": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=stream)
        with pytest.raises(TypeError):
            _ = proxy["a"]
        assert len(stream.calls) == 1

    def test_message_only_stream_called_once(self) -> None:
        """A stream without a ``stacklevel`` parameter receives just the message, once.

        A user plugs in a minimal sink that takes only the message text. The proxy must deliver the notice in a
        single call instead of first trying ``stacklevel=`` and retrying after the resulting ``TypeError``.
        """
        stream = MessageOnlyRecordingStream()
        proxy = deprecated_instance({"a": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=stream)
        assert proxy["a"] == 1
        assert len(stream.calls) == 1
        assert "cfg" in stream.calls[0]


class TestProxyTemplateMgs:
    """``message_template`` overrides the built-in warning-message templates on proxies.

    Mirrors the parity that ``@deprecated`` already offers, so that switching from ``@deprecated`` to
    ``deprecated_class``/``deprecated_instance`` does not cause the loss of custom warning-message control.

    """

    def test_custom_template_used_in_warning_message_no_target(self) -> None:
        """``message_template`` overrides the no-target template when no target is set."""
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(
                name="legacy_obj",
                deprecated_in="1.0",
                remove_in="2.0",
                message_template="CUSTOM %(source_name)s deprecated_in=%(deprecated_in)s",
            ),
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn()
        msg = str(caught[0].message)
        assert msg == "CUSTOM legacy_obj deprecated_in=1.0"

    def test_custom_template_used_in_warning_message_callable_target(self) -> None:
        """``message_template`` overrides the callable-target template, exposing target placeholders."""

        def replacement() -> None:
            """Replacement target used to confirm ``target_path`` substitution."""

        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(
                name="legacy_obj",
                deprecated_in="1.0",
                remove_in="2.0",
                target=replacement,
                message_template="OVERRIDE %(source_name)s -> %(target_name)s",
            ),
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn()
        msg = str(caught[0].message)
        assert "OVERRIDE" in msg
        assert "legacy_obj" in msg
        assert "replacement" in msg

    def test_custom_template_used_in_per_argument_warning(self) -> None:
        """``message_template`` overrides ``TEMPLATE_WARNING_ARGUMENTS`` for per-argument warnings."""
        proxy = _DeprecatedProxy(
            obj=lambda **_: None,
            spec=_ProxySpec(
                name="LegacyConfig",
                deprecated_in="1.0",
                remove_in="2.0",
                args_mapping={"old_key": "new_key"},
                message_template="ARGS-OVERRIDE %(source_name)s :: %(argument_map)s",
            ),
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn(arg_name="old_key")
        msg = str(caught[0].message)
        assert msg.startswith("ARGS-OVERRIDE LegacyConfig :: ")
        assert "`old_key` -> `new_key`" in msg

    def test_default_template_used_when_message_template_is_none(self) -> None:
        """Without ``message_template`` the built-in default template is rendered verbatim."""
        proxy = _DeprecatedProxy(obj={}, spec=_ProxySpec(name="legacy_obj", deprecated_in="1.0", remove_in="2.0"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy._warn()
        msg = str(caught[0].message)
        # Default no-target template begins with the built-in prefix and includes both versions.
        assert "The `legacy_obj` was deprecated since v1.0" in msg
        assert "It will be removed in v2.0" in msg

    def test_message_template_stored_on_deprecation_config(self) -> None:
        """``message_template`` is recorded on ``DeprecationConfig`` for audit/introspection."""
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(
                name="legacy_obj",
                deprecated_in="1.0",
                remove_in="2.0",
                message_template="CUSTOM %(source_name)s",
            ),
        )
        dep = object.__getattribute__(proxy, "__deprecation_config__")
        assert dep.message_template == "CUSTOM %(source_name)s"

    def test_deprecated_class_custom_template_applied(self) -> None:
        """``deprecated_class(message_template=...)`` propagates the override to ``_warn``."""

        class NewCfg:
            """Replacement class used as forwarding target."""

        @deprecated_class(
            target=NewCfg,
            deprecated_in="1.0",
            remove_in="2.0",
            message_template="OVERRIDE %(source_name)s -> %(target_name)s",
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
        """``deprecated_instance(message_template=...)`` propagates the override to ``_warn``."""
        proxy = deprecated_instance(
            {"k": 1},
            name="legacy_cfg",
            deprecated_in="1.0",
            remove_in="2.0",
            message_template="OVERRIDE %(source_name)s",
        )
        with pytest.warns(FutureWarning) as caught:
            _ = proxy["k"]
        msg = str(caught[0].message)
        assert msg == "OVERRIDE legacy_cfg"

    def test_deprecated_class_default_template_when_message_template_omitted(self) -> None:
        """Without ``message_template`` ``deprecated_class`` keeps the built-in template."""

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
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(name="d", deprecated_in="1.0", remove_in="2.0", read_only=True),
        )
        with pytest.raises(AttributeError, match="read-only"):
            proxy._check_read_only("Test operation")

    def test_silent_when_inactive(self) -> None:
        """_check_read_only does nothing when read_only is False."""
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(name="d", deprecated_in="1.0", remove_in="2.0", read_only=False),
        )
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
            obj={"k": 1},
            spec=_ProxySpec(name="d", deprecated_in="1.0", remove_in="2.0", read_only=True, stream=None),
        )
        with pytest.raises(AttributeError, match="read-only"):
            operation(proxy)

    def test_setitem_forwards_to_source(self) -> None:
        """``__setitem__`` mutates the source object when not read-only, without emitting a warning."""
        inner = {"k": 1}
        proxy = _DeprecatedProxy(
            obj=inner,
            spec=_ProxySpec(name="d", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy["k"] = 99
        assert inner["k"] == 99
        assert not caught

    def test_delitem_removes_from_source(self) -> None:
        """``__delitem__`` removes the key from the source object when not read-only."""
        inner = {"k": 1, "m": 2}
        proxy = _DeprecatedProxy(
            obj=inner,
            spec=_ProxySpec(name="d", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
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
        proxy = _DeprecatedProxy(obj=obj, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        assert proxy._get_active() is obj

    def test_returns_target_when_set(self) -> None:
        """With target set, _get_active returns the target."""
        obj = {"k": 1}
        tgt = {"k": 2}
        proxy = _DeprecatedProxy(obj=obj, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0", target=tgt))
        assert proxy._get_active() is tgt

    def test_writes_mutate_target_when_set(self) -> None:
        """Write operations mutate the active target object when a target is configured."""
        source = {"k": 1}
        target = {"k": 2}
        proxy = _DeprecatedProxy(
            obj=source,
            spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0", target=target, stream=None),
        )
        proxy["k"] = 9
        assert source["k"] == 1
        assert target["k"] == 9


class TestProxyNoWarnMethods:
    """Methods that delegate without emitting a warning."""

    def test_repr_no_warn(self) -> None:
        """``__repr__`` delegates to the source without warning."""
        inner = [1, 2, 3]
        proxy = _DeprecatedProxy(obj=inner, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            r = repr(proxy)
        assert r == repr(inner)
        assert not caught

    def test_str_no_warn(self) -> None:
        """``__str__`` delegates without warning."""
        inner = {"a": 1}
        proxy = _DeprecatedProxy(obj=inner, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert str(proxy) == str(inner)
        assert not caught

    def test_bool_no_warn(self) -> None:
        """``__bool__`` delegates without warning."""
        proxy_t = _DeprecatedProxy(obj=[1], spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        proxy_f = _DeprecatedProxy(obj=[], spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert bool(proxy_t)
            assert not bool(proxy_f)
        assert not caught

    def test_len_no_warn(self) -> None:
        """``__len__`` delegates without warning."""
        proxy = _DeprecatedProxy(obj=[1, 2, 3], spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert len(proxy) == 3
        assert not caught

    def test_len_uses_target_when_set(self) -> None:
        """__len__ reflects the target when a target is configured."""
        proxy = _DeprecatedProxy(
            obj=[1],
            spec=_ProxySpec(target=[1, 2, 3], name="x", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
        assert len(proxy) == 3

    def test_contains_no_warn(self) -> None:
        """__contains__ delegates without warning."""
        proxy = _DeprecatedProxy(obj={"k": 1}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert "k" in proxy
            assert "z" not in proxy
        assert not caught

    def test_contains_uses_target_when_set(self) -> None:
        """__contains__ reflects the target when a target is configured."""
        proxy = _DeprecatedProxy(
            obj={"old": 1},
            spec=_ProxySpec(target={"new": 2}, name="x", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
        assert "new" in proxy
        assert "old" not in proxy

    def test_eq_no_warn(self) -> None:
        """__eq__ does not emit a warning."""
        inner = {"a": 1}
        proxy = _DeprecatedProxy(obj=inner, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = proxy == inner
        assert result
        assert not caught

    def test_eq_proxy_vs_proxy(self) -> None:
        """Two proxies wrapping equal objects compare equal."""
        inner = {"a": 1}
        p1 = _DeprecatedProxy(obj=inner, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        p2 = _DeprecatedProxy(obj=inner, spec=_ProxySpec(name="y", deprecated_in="2.0", remove_in="3.0"))
        assert p1 == p2

    def test_ne(self) -> None:
        """__ne__ is the inverse of __eq__."""
        p1 = _DeprecatedProxy(obj={"a": 1}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        p2 = _DeprecatedProxy(obj={"a": 2}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        assert p1 != p2

    def test_hash_matches_inner(self) -> None:
        """``hash(proxy)`` equals hash(wrapped object) for hashable types, without emitting a warning."""
        inner = (1, 2, 3)
        proxy = _DeprecatedProxy(
            obj=inner,
            spec=_ProxySpec(name="t", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            h = hash(proxy)
        assert h == hash(inner)
        assert not caught


class TestProxyWarnMethods:
    """Methods that warn on access."""

    def test_getitem_warns(self) -> None:
        """__getitem__ emits warning."""
        proxy = _DeprecatedProxy(obj={"k": 99}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with pytest.warns(FutureWarning, match=r"The `x` was deprecated since v1\.0"):
            val = proxy["k"]
        assert val == 99

    def test_getattr_warns(self) -> None:
        """__getattr__ emits warning."""
        proxy = _DeprecatedProxy(obj={"k": 1}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with pytest.warns(FutureWarning, match=r"The `x` was deprecated since v1\.0"):
            method = proxy.get
        assert callable(method)

    def test_iter_warns(self) -> None:
        """__iter__ emits warning and yields all elements of the wrapped iterable."""
        proxy = _DeprecatedProxy(obj=[10, 20, 30], spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0"))
        with pytest.warns(FutureWarning, match=r"The `x` was deprecated since v1\.0"):
            items = list(proxy)
        assert items == [10, 20, 30]

    def test_call_warns_and_invokes(self) -> None:
        """__call__ emits warning and invokes the active object."""
        proxy = _DeprecatedProxy(obj=lambda x: x * 2, spec=_ProxySpec(name="fn", deprecated_in="1.0", remove_in="2.0"))
        with pytest.warns(FutureWarning, match=r"The `fn` was deprecated since v1\.0"):
            result = proxy(5)
        assert result == 10

    def test_call_with_target_invokes_target(self) -> None:
        """__call__ with a target invokes the target, not the source."""
        source = lambda x: x  # noqa: E731
        target = lambda x: x * 3  # noqa: E731
        proxy = _DeprecatedProxy(
            obj=source,
            spec=_ProxySpec(name="fn", deprecated_in="1.0", remove_in="2.0", target=target, stream=None),
        )
        assert proxy(4) == 12


class TestProxyCallGlobal:
    """Direct coverage of globally warned call forwarding."""

    @pytest.mark.parametrize(
        ("target", "expected_injected"),
        [
            pytest.param(None, "configured", id="source-merges-extras"),
            pytest.param(TargetMode.NOTIFY, "caller", id="notify-ignores-extras"),
            pytest.param(WithInjected, "configured", id="target-merges-extras"),
        ],
    )
    def test_forwarding(self, target: object, expected_injected: str) -> None:
        """Forward positional and keyword values according to the configured target.

        A legacy constructor injects a fixed keyword except in NOTIFY mode, which must preserve caller kwargs.
        Each direct helper call spends only the shared callable warning budget and leaves the caller dict intact.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            proxy = _DeprecatedProxy(
                obj=WithInjected,
                spec=_ProxySpec(
                    name="WithInjected",
                    target=target,
                    deprecated_in="1.0",
                    remove_in="2.0",
                    args_extra={"injected": "configured"},
                ),
            )
        kwargs = {"injected": "caller"}
        with pytest.warns(FutureWarning, match="WithInjected.*deprecated") as caught:
            result = _proxy_call_global(proxy, proxy._dep, (7,), kwargs)
        assert len(caught) == 1
        assert caught[0].filename == __file__
        assert type(result) is WithInjected
        assert result.new_key == 7
        assert result.injected == expected_injected
        assert kwargs == {"injected": "caller"}
        with warnings.catch_warnings(record=True) as repeated:
            warnings.simplefilter("always")
            _proxy_call_global(proxy, proxy._dep, (8,), kwargs)
        assert not repeated

    def test_warning_failure_precedes_invocation(self) -> None:
        """Deliver a fatal warning before entering the source callable.

        Calling an iterator's next method would consume its first item, so warning failure must leave that item
        available for a later retry. This exercises a real side effect without mocking the call machinery.
        """
        source = iter([11, 22])
        proxy = _DeprecatedProxy(
            obj=source.__next__, spec=_ProxySpec(name="legacy_next", deprecated_in="1.0", remove_in="2.0")
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(FutureWarning):
                _proxy_call_global(proxy, proxy._dep, (), {})
        assert next(source) == 11
        assert proxy._cfg.state.warned_calls == 0

    def test_call_error_follows_warning(self) -> None:
        """Spend the warning budget before a failing call.

        A deprecated function receiving an unknown keyword must still warn before its normal TypeError, matching
        the original call path's warning-before-invocation policy.
        """
        proxy = _DeprecatedProxy(
            obj=base_sum_kwargs, spec=_ProxySpec(name="legacy_sum", deprecated_in="1.0", remove_in="2.0")
        )
        with pytest.warns(FutureWarning), pytest.raises(TypeError, match="unexpected keyword argument 'unknown'"):
            _proxy_call_global(proxy, proxy._dep, (), {"unknown": 1})
        assert proxy._cfg.state.warned_calls == 1


class TestDecoratorFactory:
    """deprecated_class used as a class decorator."""

    def test_decorated_class_is_deprecated_proxy(self) -> None:
        """@deprecated_class wraps the class in a _DeprecatedProxy."""
        assert isinstance(WarnOnlyColorEnum, _DeprecatedProxy)
        assert isinstance(DeprecatedColorEnum, _DeprecatedProxy)

    def test_uses_class_name_as_proxy_name(self) -> None:
        """The proxy name is taken from the decorated class __name__."""
        name = object.__getattribute__(WarnOnlyColorEnum, "__deprecation_config__").name
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

    def test_bare_call_default_target_is_none_not_misconfigured(self) -> None:
        """``deprecated_class()`` called fresh with no explicit ``target=`` records ``None`` as its target.

        ``None`` is the proxy's unset value: ``TargetMode.NOTIFY`` appears in audit metadata only when the
        caller explicitly chose it, so tools can distinguish "warn-only by default" from "warn-only on
        purpose"; the warn-on-access behaviour is identical either way.

        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)

            @deprecated_class(deprecated_in="1.0", remove_in="2.0")
            class BareDefaultTargetClass:
                """Plain class deprecated with no explicit target — proves the factory default."""

        dep = object.__getattribute__(BareDefaultTargetClass, "__deprecation_config__")
        assert dep.target is None
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

        dep = object.__getattribute__(OldClass, "__deprecation_config__")
        assert dep.target is None

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
        dep = object.__getattribute__(OldClass, "__deprecation_config__")
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
        """DeprecationProxy Enum call, attribute, and item access should warn and resolve to target member."""
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
        """DeprecationProxy dataclass calls should remap renamed kwargs and preserve explicit non-remapped kwargs.

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
        mapping = object.__getattribute__(MappedDataClass, "__deprecation_config__").args_mapping
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
            ProxyArgsRemapForArgWarnMessage(old_key=5)  # type: ignore[call-arg]
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
        because the dict-comprehension last-write-wins in the proxy's ``args_mapping`` rename (now
        ``deprecate.utils._remap_kwargs``) caused the ``old_key→new_key`` rename to overwrite the explicit
        ``new_key=6`` entry.
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
        assert instance.injected == "from-extra"  # type: ignore[attr-defined]

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
            instance = proxy(old_key=11)  # type: ignore[call-arg]
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
            obj=[1, 2, 3],
            spec=_ProxySpec(target=[], name="x", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
        assert not bool(proxy)


class TestHashOnUnhashableType:
    """``hash()`` behaviour for proxies wrapping unhashable objects."""

    def test_hash_raises_for_unhashable_source(self) -> None:
        """``hash(proxy)`` raises TypeError when the wrapped object is unhashable (e.g. dict).

        Propagates TypeError from the underlying hash() call with no additional context.

        """
        proxy = _DeprecatedProxy(
            obj={"k": 1},
            spec=_ProxySpec(name="d", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
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
        dep = object.__getattribute__(proxy, "__deprecation_config__")
        assert dep.name == "dict"

    def test_name_auto_inferred_for_list(self) -> None:
        """Type name inference works for any built-in type."""
        proxy = deprecated_instance([1, 2], deprecated_in="1.0", remove_in="2.0", stream=None)
        dep = object.__getattribute__(proxy, "__deprecation_config__")
        assert dep.name == "list"

    def test_name_explicitly_set(self) -> None:
        """Explicit name= overrides the type-based inference."""
        proxy = deprecated_instance({}, name="my_config", deprecated_in="1.0", remove_in="2.0", stream=None)
        dep = object.__getattribute__(proxy, "__deprecation_config__")
        assert dep.name == "my_config"

    def test_version_metadata_stored(self) -> None:
        """deprecated_in and remove_in are stored verbatim in DeprecationConfig."""
        proxy = deprecated_instance([], deprecated_in="2.0", remove_in="3.5", stream=None)
        dep = object.__getattribute__(proxy, "__deprecation_config__")
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

        proxy = _DeprecatedProxy(
            obj=Target,
            spec=_ProxySpec(name="old", deprecated_in="1.0", remove_in="2.0", num_warns=1),
        )
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

        proxy = _DeprecatedProxy(obj=Base, spec=_ProxySpec(name="old_cls", deprecated_in="1.0", remove_in="2.0"))
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
        proxy = _DeprecatedProxy(
            obj={"key": "val"},
            spec=_ProxySpec(name="old_cfg", deprecated_in="1.0", remove_in="2.0"),
        )
        with pytest.raises(TypeError, match="arg 2 must be a type"):
            isinstance(42, cast(Any, proxy))


class TestProxyArgsMappingBehavior:
    """Conditional warning behavior when args_mapping is provided on a proxy."""

    def test_auto_args_remap_warns_on_old_arg(self) -> None:
        """Proxy with args_mapping and no explicit target warns when old arg name is used."""
        with pytest.warns(FutureWarning):
            ProxyArgsRemapAuto(old_key=1)  # type: ignore[call-arg]

    def test_auto_args_remap_silent_on_new_arg(self) -> None:
        """Proxy with args_mapping and no explicit target does NOT warn when new arg name is used."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ProxyArgsRemapAuto(new_key=1)
        assert not caught

    def test_callable_target_with_args_mapping_warns_on_old_arg(self) -> None:
        """Proxy forwarding to callable target warns per old arg name when present in kwargs."""
        with pytest.warns(FutureWarning):
            ProxyCallableWithArgsMapping(old_key=1)  # type: ignore[call-arg]

    def test_callable_target_with_args_mapping_warns_on_new_arg(self) -> None:
        """Proxy forwarding to callable target always warns (class deprecated) even with new arg name."""
        proxy = _DeprecatedProxy(
            obj=SomeTargetClass,
            spec=_ProxySpec(
                name="SomeTargetClass",
                deprecated_in="1.2",
                remove_in="2.0",
                num_warns=-1,
                target=SomeTargetClass,
                args_mapping={"old_key": "new_key"},
            ),
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
            spec=_ProxySpec(
                name="SomeTargetClass",
                deprecated_in="1.2",
                remove_in="2.0",
                num_warns=-1,
                target=SomeTargetClass,
                args_mapping={"old_key": "new_key"},
            ),
        )
        with pytest.warns(FutureWarning):
            instance = proxy(old_key=5, new_key=6)
        assert instance.new_key == 6

    def test_callable_target_new_kwarg_wins_when_both_old_and_new_provided_new_first(self) -> None:
        """Callable-target proxy: new-name value wins regardless of whether old or new kwarg is listed first.

        Before the precedence fix, calling with ``new_key=6, old_key=5`` produced ``new_key=5``
        because the dict-comprehension last-write-wins in the proxy's ``args_mapping`` rename (now
        ``deprecate.utils._remap_kwargs``) caused the ``old_key→new_key`` rename to overwrite the explicit
        ``new_key=6`` entry.
        Exercises ``_proxy_call_callable_with_mapping`` (has a callable target, not ARGS_REMAP mode).
        """
        proxy = _DeprecatedProxy(
            obj=SomeTargetClass,
            spec=_ProxySpec(
                name="SomeTargetClass",
                deprecated_in="1.2",
                remove_in="2.0",
                num_warns=-1,
                target=SomeTargetClass,
                args_mapping={"old_key": "new_key"},
            ),
        )
        with pytest.warns(FutureWarning):
            instance = proxy(new_key=6, old_key=5)
        assert instance.new_key == 6

    def test_explicit_notify_with_args_mapping_warns_and_keeps_mode(self) -> None:
        """Explicit ``target=TargetMode.NOTIFY`` + ``args_mapping`` is contradictory — flagged, never overridden.

        Only the omitted default auto-resolves a mapping. When the caller explicitly picked ``NOTIFY``,
        pyDeprecate cannot tell whether the target or the mapping is the mistake, so it must not silently
        rewrite the user's configuration: the mode stays ``NOTIFY``, the mapping is inert, a
        ``UserWarning`` fires at decoration time, and audit metadata records ``misconfigured=True``.

        """
        with pytest.warns(UserWarning, match="ignores `args_mapping`"):

            @deprecated_class(
                deprecated_in="1.2", remove_in="2.0", target=TargetMode.NOTIFY, args_mapping={"old_key": "new_key"}
            )
            class _ProxyNotifyWithArgsMapping:
                pass

        meta = object.__getattribute__(_ProxyNotifyWithArgsMapping, "__deprecation_config__")
        assert meta.target is TargetMode.NOTIFY
        assert meta.misconfigured is True

    def test_omitted_target_with_args_mapping_auto_resolves_to_args_remap(self) -> None:
        """Omitting ``target`` while passing ``args_mapping`` auto-resolves to ``ARGS_REMAP`` — no warning.

        The factory default means "no explicit target", so the presence of the mapping is the activation
        signal: the proxy resolves to per-argument deprecation exactly as if ``TargetMode.ARGS_REMAP`` had
        been spelled out, with no misconfig warning and clean audit metadata.

        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            @deprecated_class(deprecated_in="1.2", remove_in="2.0", args_mapping={"old_key": "new_key"})
            class _ProxyOmittedWithArgsMapping:
                pass

        misconfig_warns = [w for w in caught if "ignores `args_mapping`" in str(w.message)]
        assert not misconfig_warns
        meta = object.__getattribute__(_ProxyOmittedWithArgsMapping, "__deprecation_config__")
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
            spec=_ProxySpec(
                name="budget_test",
                deprecated_in="1.0",
                remove_in="2.0",
                num_warns=1,
                target=TargetMode.ARGS_REMAP,
                args_mapping={"old_key": "new_key"},
            ),
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
    object it decorates.  For a ``_DeprecatedProxy`` instance, ``__setattr__`` stores that
    message on the proxy itself — never on the wrapped class — while pyDeprecate's
    configuration stays on ``__deprecation_config__`` untouched.  These tests guard both
    halves: the proxy keeps working, and the replacement class is never marked deprecated.

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
        """Outer ``typing_extensions.deprecated`` emits its DeprecationWarning on call, and the inner proxy still warns.

        A library stacks the static-checker decorator over a ``deprecated_class`` alias.  One call must deliver both the
        PEP 702 ``DeprecationWarning`` and pyDeprecate's own ``FutureWarning``; neither layer may swallow the other.

        """
        with (
            pytest.warns(FutureWarning, match="`_Pep702ProxyTarget` was deprecated since v0.8"),
            pytest.warns(DeprecationWarning, match="use `Pep702ProxyTarget`"),
        ):
            pep702_proxy_stacked()

    @pytest.mark.parametrize(
        "target_cls",
        [
            pytest.param(_Pep702ProxyTarget, id="default-category"),
            pytest.param(Pep702StaticTarget, id="category-none"),
        ],
    )
    def test_replacement_class_stays_undeprecated(self, target_cls: type) -> None:
        """The PEP 702 marker written onto a stacked proxy never reaches the replacement class.

        A library stacks ``typing_extensions.deprecated`` over a ``deprecated_class`` alias so type checkers flag the
        old name. PEP 702 writes ``__deprecated__`` onto the object it decorates — the proxy. Forwarding that write to
        the wrapped class would mark the *replacement* deprecated for every tool that reads the attribute.

        """
        assert "__deprecated__" not in vars(target_cls)

    def test_stacked_alias_carries_pep702_message(self) -> None:
        """The proxy itself holds the PEP 702 message after stacking, as PEP 702 specifies for the decorated object.

        Runtime tools reading ``StackedAlias.__deprecated__`` must see the message the author gave the static-checker
        decorator, while pyDeprecate's own configuration stays reachable through ``get_deprecation_config``.

        """
        assert object.__getattribute__(StackedAlias, "__deprecated__") == "Use `Pep702StaticTarget` instead."

    def test_pep702_marker_write_ignores_read_only_and_skip_if(self) -> None:
        """Writing ``__deprecated__`` on a read-only, skipped proxy updates proxy metadata instead of raising.

        ``__deprecated__`` is metadata about the proxy, not state of the wrapped object: the read-only guard and the
        ``skip_if`` pass-through (which would forward the write to a plain ``list`` and raise) must not apply to it.

        """
        proxy = make_deprecated_instance_skip_if_true_read_only()
        proxy.__deprecated__ = "Use `new_list` instead."
        assert object.__getattribute__(proxy, "__deprecated__") == "Use `new_list` instead."

    def test_pep702_marker_delete_ignores_read_only_and_skip_if(self) -> None:
        """Deleting proxy metadata leaves the wrapped object unchanged under skip and read-only settings.

        A static checker decorator may remove its own marker during cleanup. The proxy must delete its local marker
        without forwarding the deletion to the read-only list it wraps.

        """
        proxy = make_deprecated_instance_skip_if_true_read_only()
        wrapped = proxy.__wrapped__
        before = list(wrapped)

        del proxy.__deprecated__

        assert "__deprecated__" not in vars(proxy)
        assert wrapped == before

    def test_deleted_marker_reads_as_missing(self) -> None:
        """After ``del proxy.__deprecated__`` the marker reads as missing, never as the wrapped object's own marker.

        A tool that removes the proxy's PEP 702 marker and then probes for it must see ``AttributeError`` — falling
        through to the wrapped object would report *its* marker as the proxy's, and a second ``del`` must fail the
        same way it does on any object whose attribute is already gone.

        """
        proxy = make_deprecated_instance_over_marked_object()
        del proxy.__deprecated__

        with pytest.raises(AttributeError):
            _ = proxy.__deprecated__
        with pytest.raises(AttributeError):
            del proxy.__deprecated__

    @pytest.mark.parametrize(
        "duplicate",
        [
            pytest.param(copy.copy, id="copy"),
            pytest.param(copy.deepcopy, id="deepcopy"),
            pytest.param(lambda obj: pickle.loads(pickle.dumps(obj)), id="pickle"),  # noqa: S301
        ],
    )
    def test_copy_keeps_user_set_marker(self, duplicate: Callable[[Any], Any]) -> None:
        """A PEP 702 message written onto the proxy survives copy, deepcopy, and a pickle round-trip.

        A library stacks a PEP 702 decorator over a deprecated alias, and later code copies or pickles that alias
        (config snapshots, multiprocessing). The duplicate must carry the message the author wrote, not a freshly
        rendered pyDeprecate message that silently replaces it.

        """
        proxy = make_deprecated_instance_over_marked_object()
        proxy.__deprecated__ = "Use `new_settings` instead."

        duplicated = duplicate(proxy)

        assert object.__getattribute__(duplicated, "__deprecated__") == "Use `new_settings` instead."

    @pytest.mark.parametrize(
        "duplicate",
        [
            pytest.param(copy.copy, id="copy"),
            pytest.param(copy.deepcopy, id="deepcopy"),
            pytest.param(lambda obj: pickle.loads(pickle.dumps(obj)), id="pickle"),  # noqa: S301
        ],
    )
    def test_copy_keeps_deleted_marker_deleted(self, duplicate: Callable[[Any], Any]) -> None:
        """A proxy whose PEP 702 marker was deleted stays marker-less after copy, deepcopy, and a pickle round-trip.

        A static-checker decorator removes its marker during cleanup, and later code copies or pickles the alias. The
        duplicate must not re-add a freshly rendered ``__deprecated__`` that the author deliberately removed, nor
        expose the wrapped object's own marker in its place.

        """
        proxy = make_deprecated_instance_over_marked_object()
        del proxy.__deprecated__

        duplicated = duplicate(proxy)

        assert "__deprecated__" not in vars(duplicated)
        with pytest.raises(AttributeError):
            _ = duplicated.__deprecated__

    def test_legacy_payload_renders_default_marker(self) -> None:
        """A pickle payload written before the marker was recorded rebuilds a proxy with the default message.

        Pickles stored by an older release carry only ``(cfg, dep, doc)``; loading one must still give a proxy whose
        ``__deprecated__`` is the rendered default message, not a missing attribute and not the wrapped object's own
        marker. The payload is built from the current ``__reduce_ex__`` result with the marker argument dropped.

        """
        proxy = make_deprecated_instance_over_marked_object()
        reconstructor, args = proxy.__reduce_ex__(2)[:2]

        rebuilt = reconstructor(*args[:3])

        marker = object.__getattribute__(rebuilt, "__deprecated__")
        assert marker == object.__getattribute__(proxy, "__deprecated__")
        assert marker != "wrapped object's own marker"


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
    module-level singletons whose per-attribute warning counters (``_cfg.state.warned_args``) persist
    across tests once consumed. The :meth:`_reset_proxy_state` autouse fixture clears those
    counters and re-seeds the canonical attribute values on the wrapped target so each test
    starts from the same baseline.

    """

    @pytest.fixture(autouse=True)
    def _reset_proxy_state(self) -> None:
        """Re-seed the wrapped target attributes of the module-level fixture proxies between tests.

        The proxies' warn budgets (``_cfg.state``) are reset by the project conftest, which covers every module-level
        proxy of :mod:`tests.collection_deprecate`; this fixture restores the wrapped objects' mutable attributes.

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
            source = object.__getattribute__(proxy, "_DeprecatedProxy__config").obj
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
            spec=_ProxySpec(
                name="Palette",
                deprecated_in="1.0",
                remove_in="2.0",
                read_only=True,
                stream=None,
                attrs_mapping={"color": "colour"},
            ),
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
        """``attrs_mapping`` is visible in ``__deprecation_config__`` metadata for audit tools.

        Audit tooling reads ``DeprecationConfig`` via ``obj.__deprecation_config__`` to build deprecation tables
        and enforce expiry policies.  The ``attrs_mapping`` dict must be stored in the frozen ``DeprecationConfig``
        so that ``find_deprecation_wrappers`` can surface it without reading internal proxy state.

        """
        meta = object.__getattribute__(DeprecatedAttrsPalette, "__deprecation_config__")
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
        target_cfg.state.warned_args.clear()

        class SourceAlias:
            old = "source_old"
            alias = "source_alias"

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            deprecated_class(target=target_proxy, attrs_mapping={"old": "alias"}, deprecated_in="1.0", remove_in="2.0")(
                SourceAlias
            )
        assert not caught
        assert target_cfg.state.warned_args == {}

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
        """Re-seed wrapped target attributes between tests; the conftest resets the fixture proxies' warn budgets."""
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
        explicit_meta = object.__getattribute__(DeprecatedAttrsExplicitMode, "__deprecation_config__")
        implicit_meta = object.__getattribute__(DeprecatedAttrsPalette, "__deprecation_config__")
        assert explicit_meta.target is implicit_meta.target is TargetMode.ATTRS_REMAP

    def test_explicit_attrs_remap_stored_in_dep_config_target(self) -> None:
        """Explicit ``target=TargetMode.ATTRS_REMAP`` is stored verbatim on the frozen DeprecationConfig.

        Audit tooling reads ``DeprecationConfig.target`` to introspect the resolved deprecation mode. Passing the
        enum member explicitly must preserve the same enum member in storage so that downstream consumers cannot
        distinguish the explicit from the implicit auto-resolve at the metadata level.

        """
        meta = object.__getattribute__(DeprecatedAttrsExplicitMode, "__deprecation_config__")
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
        meta = object.__getattribute__(DeprecatedAttrsLegacyTrue, "__deprecation_config__")
        assert meta.target is TargetMode.ATTRS_REMAP
        assert meta.misconfigured is False

    def test_attrs_remap_stored_in_dep_config_target_via_auto_resolve(self) -> None:
        """Auto-resolution from ``attrs_mapping`` to ``ATTRS_REMAP`` is reflected in stored metadata.

        When the caller omits ``target`` but provides ``attrs_mapping``, the proxy must store
        :attr:`~deprecate._types.TargetMode.ATTRS_REMAP` on the frozen config rather than the original ``None`` so
        that audit tooling has a single canonical mode marker regardless of how the caller spelled the config.

        """
        meta = object.__getattribute__(DeprecatedAttrsPalette, "__deprecation_config__")
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

    def test_explicit_notify_plus_attrs_mapping_warns_and_ignores_mapping(self) -> None:
        """Explicit ``target=TargetMode.NOTIFY`` + ``attrs_mapping`` is contradictory — flagged, mapping inert.

        ``NOTIFY`` means "warn on every access"; ``attrs_mapping`` means "warn only on the listed aliases".
        When the caller spelled out ``NOTIFY``, pyDeprecate cannot judge whether the target or the mapping
        is the mistake, so the explicit choice wins: the proxy stays in whole-class warn mode, the mapping
        performs no redirects at runtime, a ``UserWarning`` fires at decoration time, and audit metadata
        records ``misconfigured=True`` while preserving the configured ``attrs_mapping`` for inspection.
        Omit ``target`` (the factory default) to auto-resolve the mapping to ``ATTRS_REMAP`` instead.

        """

        class _NotifyAttrsContradiction:
            colour = "red"

        with pytest.warns(UserWarning, match="ignores `attrs_mapping`"):
            proxy = deprecated_class(
                target=TargetMode.NOTIFY,
                attrs_mapping={"color": "colour"},
                deprecated_in="1.0",
                remove_in="2.0",
                stream=None,
            )(_NotifyAttrsContradiction)

        meta = object.__getattribute__(proxy, "__deprecation_config__")
        assert meta.target is TargetMode.NOTIFY
        assert meta.misconfigured is True
        assert meta.attrs_mapping == {"color": "colour"}
        # Runtime ignores the mapping entirely: the alias is not redirected to ``colour``.
        with pytest.raises(AttributeError):
            _ = proxy.color  # type: ignore[attr-defined]

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
        meta = object.__getattribute__(proxy, "__deprecation_config__")
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
        meta = object.__getattribute__(proxy, "__deprecation_config__")
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
            _ = DepAutoExpandDC.old_field  # type: ignore[attr-defined]  # class-proxy access, not instance attr

    def test_auto_expanded_keys_recorded_on_deprecated_meta(self) -> None:
        """``args_mapping_auto_expanded`` on ``__deprecation_config__`` lists the auto-copied key.

        Audit tools read ``DeprecationConfig.args_mapping_auto_expanded`` to distinguish
        auto-generated entries from user-supplied ones.
        """
        meta = object.__getattribute__(DepAutoExpandDC, "__deprecation_config__")
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
        meta = object.__getattribute__(proxy, "__deprecation_config__")
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
        dep = object.__getattribute__(proxy, "__deprecation_config__")
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

    def test_req_dc_class_proxy_attribute_access_raises_without_warning(self) -> None:
        """Accessing ``DepAutoExpandReqDC.old_field`` raises AttributeError and emits no FutureWarning.

        ``AutoExpandReqDC`` has a required field ``new_field`` with no class-level default, so the redirected
        class-level read of ``old_field`` → ``new_field`` raises ``AttributeError``. The proxy resolves the mapped
        attribute before warning, so this failed lookup — the same shape as a ``hasattr`` probe — emits nothing and
        leaves the ``old_field`` warn budget unspent.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(AttributeError):
                _ = DepAutoExpandReqDC.old_field  # type: ignore[attr-defined]

        assert [w for w in caught if issubclass(w.category, FutureWarning)] == []

    def test_init_false_field_excluded_from_auto_expand(self) -> None:
        """``field(init=False)`` entries must not appear in ``args_mapping_auto_expanded``.

        ``AutoExpandInitFalseDC`` has ``new_field`` (normal param) and ``computed_field``
        (``init=False``).  Only ``old_field`` → ``new_field`` should be auto-expanded;
        ``old_computed`` → ``computed_field`` must be excluded because ``computed_field``
        is not a valid ``__init__`` kwarg — passing it to the constructor raises ``TypeError``.
        """
        meta = object.__getattribute__(DepAutoExpandInitFalseDC, "__deprecation_config__")
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
        meta = object.__getattribute__(DepAutoExpandOverriddenInitDC, "__deprecation_config__")
        assert "old_field" in meta.args_mapping_auto_expanded

    def test_overridden_init_absent_field_not_auto_expanded(self) -> None:
        """Fields absent from the overridden ``__init__`` signature are not auto-expanded.

        ``skipped_field`` is a dataclass field (``init=True`` by default in the ``@dataclass``
        descriptor) but intentionally absent from the overridden ``__init__``.  Using
        ``dataclasses.fields()`` would (incorrectly) include it; ``inspect.signature`` correctly
        excludes it.  Passing ``old_skipped`` to the constructor must not raise ``TypeError``.
        """
        meta = object.__getattribute__(DepAutoExpandOverriddenInitDC, "__deprecation_config__")
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
        """``args_mapping_positional_only`` on ``__deprecation_config__`` lists the offending old key.

        The field is populated at decoration time so audit tools can surface it without
        re-inspecting the constructor signature at report time.
        """
        meta = object.__getattribute__(DepPositionalOnly, "__deprecation_config__")
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
        assert instance.new_val == 3  # type: ignore[attr-defined]

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
        proxy = _DeprecatedProxy(
            obj=[1],
            spec=_ProxySpec(target={"a": 1}, name="x", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
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
        """Preserve deprecation metadata when copying a proxy.

        Applications commonly copy configuration-like proxies before mutation; the duplicate must remain visible to
        audit tooling with the same name and lifecycle versions.
        """
        proxy = deprecated_instance({"k": 1}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        dup = copy.copy(proxy)
        meta = object.__getattribute__(dup, "__deprecation_config__")
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
        assert object.__getattribute__(restored, "__deprecation_config__").name == "cfg"
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

    def test_copy_preserves_ast_breadcrumbs(self) -> None:
        """A shallow copy carries the same AST breadcrumbs as a freshly constructed proxy.

        Reconstruction bypasses ``__init__``, so the breadcrumbs have to be re-established explicitly.
        Without that, a consumer who snapshots a deprecated symbol with ``copy.copy`` gets a proxy that
        ``inspect.unwrap`` can no longer see through and that no longer structurally satisfies the public
        ``DeprecationProxy`` protocol — the "every proxy carries ``__wrapped__``" contract would hold only until
        somebody copied one.
        """
        proxy = deprecated_instance(
            base_sum_kwargs, name="legacy_sum", deprecated_in="1.0", remove_in="2.0", stream=None
        )

        dup = copy.copy(proxy)

        assert dup.__wrapped__ is base_sum_kwargs
        assert dup.__signature__ == inspect.signature(base_sum_kwargs)
        assert isinstance(dup, DeprecationProxy)

    def test_deepcopy_rebinds_breadcrumb_to_copied_object(self) -> None:
        """A deep copy points ``__wrapped__`` at its *own* wrapped object, not the original's.

        ``deepcopy`` duplicates the wrapped payload, so a breadcrumb naively carried over from the source
        proxy would describe an object the copy does not actually serve — mutating the copy would leave
        ``inspect.unwrap`` reporting stale state. The breadcrumb must track the object the copy forwards to.
        """
        proxy = deprecated_instance(
            {"limits": {"low": 1}}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None
        )

        dup = copy.deepcopy(proxy)

        assert dup.__wrapped__ is object.__getattribute__(dup, "_DeprecatedProxy__config").obj
        assert dup.__wrapped__ is not proxy.__wrapped__

    def test_deepcopy_of_class_proxy_preserves_breadcrumbs(self) -> None:
        """Deep-copying a deprecated class alias keeps both breadcrumbs intact.

        Classes are atomic under ``deepcopy``, so the copy forwards to the identical source class and must
        report exactly the same ``__wrapped__`` and ``__signature__`` as the original alias — this is the
        path Sphinx autodoc and IDEs walk when they encounter a copied module namespace.
        """
        original = cast(DeprecationProxy[Any], DeprecatedColorEnum)

        dup = cast(DeprecationProxy[Any], copy.deepcopy(DeprecatedColorEnum))

        assert dup.__wrapped__ is original.__wrapped__
        assert dup.__signature__ == original.__signature__
        assert dup.__signature__ is not None

    def test_pickle_roundtrip_preserves_ast_breadcrumbs(self) -> None:
        """A pickled proxy comes back with its breadcrumbs restored.

        ``__reduce_ex__`` rebuilds the proxy through the same reconstruction helper as ``copy``, so a proxy
        shipped across a process boundary must arrive introspectable rather than silently degraded. The
        signature is recomputed from the restored source instead of travelling through the pickle stream,
        which keeps ``Signature`` objects out of the payload entirely.
        """
        proxy = deprecated_instance(
            base_sum_kwargs, name="legacy_sum", deprecated_in="1.0", remove_in="2.0", stream=None
        )

        restored = pickle.loads(pickle.dumps(proxy))  # noqa: S301

        assert restored.__wrapped__ is base_sum_kwargs
        assert restored.__signature__ == inspect.signature(base_sum_kwargs)


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
            obj=_P8Source,
            spec=_ProxySpec(target=_P8Target, name="_P8Source", deprecated_in="1.0", remove_in="2.0"),
        )
        assert proxy == _P8Target
        assert proxy != _P8Source

    def test_repr_shows_active_target(self) -> None:
        """``repr`` of a target-forwarding proxy shows the active object rather than the deprecated source."""
        proxy = _DeprecatedProxy(
            obj=_P8Source,
            spec=_ProxySpec(target=_P8Target, name="_P8Source", deprecated_in="1.0", remove_in="2.0"),
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
            obj=_P9Config,
            spec=_ProxySpec(name="_P9Config", deprecated_in="1.0", remove_in="2.0", attrs_mapping=mapping),
        )
        mapping["old_attr"] = "hijacked"
        assert proxy.__deprecation_config__.attrs_mapping == {"old_attr": "new_attr"}


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
            obj=_P8Source,
            spec=_ProxySpec(target=_P8Target, name="_P8Source", deprecated_in="1.0", remove_in="2.0", stream=stream),
        )
        with pytest.raises(TypeError, match="boom from inside the proxy stream"):
            proxy._warn()
        assert len(calls) == 1


class TestProxySubclassCheckTypeError:
    """``issubclass`` with an instance proxy raises TypeError like the builtin."""

    def test_issubclass_raises_typeerror_for_non_type_active(self) -> None:
        """Using an instance proxy as ``issubclass`` arg 2 raises TypeError instead of silently returning False."""
        proxy = _DeprecatedProxy(
            obj={"key": "val"},
            spec=_ProxySpec(name="old_cfg", deprecated_in="1.0", remove_in="2.0"),
        )
        with pytest.raises(TypeError, match="arg 2 must be a class"):
            issubclass(int, cast(Any, proxy))


class TestProxySkipIf:
    """``skip_if`` on proxies — an active skip condition deactivates the whole deprecation machinery."""

    def test_true_serves_source_without_warning(self) -> None:
        """With ``skip_if=True`` attribute reads resolve on the wrapped source, silently.

        A library gates a class deprecation on an environment condition (e.g. a dependency version): while the
        condition holds, callers must see the original class untouched — source attribute values, no
        ``FutureWarning``, no forwarding to the configured target.

        """
        proxy = make_deprecated_class_skip_if_true()

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            value = proxy.colour

        assert value == "source_colour"

    def test_true_call_returns_source_instance(self) -> None:
        """With ``skip_if=True`` instantiation runs the wrapped source class, not the target.

        Parity with ``deprecated_callable(skip_if=True)``, where a skipped call executes the source body: a
        skipped proxy call must construct the source class silently instead of forwarding to the target.

        """
        proxy = make_deprecated_class_skip_if_true()

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            instance = proxy()

        assert type(instance).__name__ == "PaletteOld"

    def test_flag_flip_reactivates_machinery(self) -> None:
        """A callable ``skip_if`` is re-evaluated per access — flipping the flag re-arms warning and forwarding.

        The canonical use case is a runtime feature flag: while it is set the deprecation is dormant; once it
        flips, the very next access must warn and forward to the target with no re-decoration required.

        """
        proxy, flag = make_deprecated_class_skip_if_flag()

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            skipped = proxy.colour
        flag["skip"] = False
        with pytest.warns(FutureWarning):
            active = proxy.colour

        assert skipped == "source_colour"
        assert active == "red"

    def test_true_disables_attrs_redirect(self) -> None:
        """With ``skip_if=True`` a deprecated attribute alias reads the source attribute, no redirect.

        ``attrs_mapping={"color": "colour"}`` normally rewrites ``proxy.color`` to the canonical name; under an
        active skip the alias must behave as plain attribute access on the wrapped source — no rename, no
        warning.

        """
        proxy = make_deprecated_class_attrs_skip_if_true()

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            value = proxy.color

        assert value == "source_red"

    def test_non_bool_return_raises_type_error(self) -> None:
        """A ``skip_if`` callable returning a non-bool raises ``TypeError`` at access time.

        Same strict contract as the callable decorator: a developer accidentally returning ``42`` from the
        condition must get an immediate error naming ``skip_if``, not a silently-truthy skip.

        """
        proxy = make_deprecated_class_skip_if_non_bool()

        with pytest.raises(TypeError, match="User function 'skip_if' shall return bool"):
            proxy()

    def test_instance_skip_bypasses_read_only_guard(self) -> None:
        """``deprecated_instance(skip_if=True, read_only=True)`` serves the object with no mutator guard.

        A read-only deprecated constant whose deprecation window is gated by a flag: while skipped, the proxy
        is fully transparent — standard collection mutators succeed silently instead of raising the read-only
        ``AttributeError``.

        """
        proxy = make_deprecated_instance_skip_if_true_read_only()

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            proxy.append(3)

        assert list(proxy) == [1, 2, 3]

    @pytest.mark.parametrize(
        ("operation", "expected"),
        [
            pytest.param(lambda p: p.__setitem__("threshold", 0.9), {"threshold": 0.9, "enabled": True}, id="setitem"),
            pytest.param(lambda p: p.__delitem__("enabled"), {"threshold": 0.5}, id="delitem"),
        ],
    )
    def test_instance_skip_allows_item_writes(
        self, operation: Callable[[Any], None], expected: dict[str, object]
    ) -> None:
        """With ``skip_if=True`` item writes and deletes reach a read-only source silently, like attribute writes.

        A read-only deprecated settings mapping whose deprecation is gated by a flag: while the flag holds, the proxy
        must be fully transparent, so ``settings["threshold"] = 0.9`` and ``del settings["enabled"]`` mutate the
        wrapped dict instead of raising the read-only ``AttributeError`` — the same rule ``__setattr__`` follows.

        """
        proxy = make_read_only_settings_proxy(skip_if=True)

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            operation(proxy)

        assert proxy.wrapped == expected

    @pytest.mark.parametrize(
        "operation",
        [
            pytest.param(lambda p: p.__setitem__("threshold", 0.9), id="setitem"),
            pytest.param(lambda p: p.__delitem__("enabled"), id="delitem"),
        ],
    )
    def test_inactive_skip_keeps_item_write_guard(self, operation: Callable[[Any], None]) -> None:
        """With ``skip_if=False`` the read-only guard still rejects item writes and deletes.

        Honouring ``skip_if`` must not weaken the default: once the gating flag is off, a write through a read-only
        deprecated mapping raises the read-only ``AttributeError`` and leaves the wrapped dict untouched.

        """
        proxy = make_read_only_settings_proxy(skip_if=False)

        with pytest.raises(AttributeError, match="read-only"):
            operation(proxy)

        assert proxy.wrapped == {"threshold": 0.5, "enabled": True}


class TestProxyDescriptorBinding:
    """A proxy stored as a class attribute binds a wrapped function like a method, via ``__get__``."""

    @pytest.mark.parametrize(
        ("host_factory", "attr", "expected"),
        [
            pytest.param(make_deprecated_method_host, "scale", True, id="function"),
            pytest.param(make_deprecated_data_host, "defaults", False, id="dict"),
            pytest.param(make_deprecated_data_host, "Palette", False, id="class-alias"),
            pytest.param(make_deprecated_partial_host, "double", False, id="partial"),
        ],
    )
    def test_only_bindable_proxies_report_as_routines(
        self, host_factory: Callable[[], type[Any]], attr: str, expected: bool
    ) -> None:
        """``inspect.isroutine`` is ``True`` for a proxy over a function and ``False`` for data and class proxies.

        Documentation and introspection tools (``pydoc``, ``inspect.getmembers(module, inspect.isroutine)``, griffe in
        dynamic mode) sort members by this predicate. Only a proxy that actually binds may carry ``__get__``; a
        deprecated constant or class alias must keep being reported as what it stands in for, not as a function.

        """
        stored = vars(host_factory())[attr]

        assert inspect.isroutine(stored) is expected

    @pytest.mark.parametrize(
        "duplicate",
        [
            pytest.param(copy.copy, id="copy"),
            pytest.param(copy.deepcopy, id="deepcopy"),
            pytest.param(lambda proxy: pickle.loads(pickle.dumps(proxy)), id="pickle"),  # noqa: S301
        ],
    )
    def test_duplicate_keeps_binding(self, duplicate: Callable[[Any], Any]) -> None:
        """A copied, deep-copied or unpickled function proxy is still a binding proxy.

        Caches and worker processes copy class namespaces; the rebuilt proxy must keep ``__get__`` so a method stored
        on a class keeps receiving its instance after the round trip.

        """
        stored = vars(make_deprecated_method_host())["scale"]

        assert type(duplicate(stored)) is type(stored)

    def test_instance_calls_bind_and_share_one_warning(self) -> None:
        """Calls through two instances each receive their own instance and together emit one warning.

        A library replaces ``class Model: scale = legacy_scale`` by ``scale = deprecated_instance(legacy_scale, ...)``:
        ``Model().scale(2)`` must keep passing the instance as the first argument, and the default ``num_warns=1``
        budget belongs to the deprecated object — not to each instance or to each attribute access.

        """
        host_cls = make_deprecated_method_host()
        first, second = host_cls(), host_cls()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            results = (first.scale(1), second.scale(2), host_cls.scale(first, 3))

        assert results == ((first, 1), (second, 2), (first, 3))
        assert _future_warning_count(caught) == 1

    def test_instance_access_returns_method_over_proxy(self) -> None:
        """Instance access yields a bound method over the stored proxy, with ``self`` dropped from its signature.

        Pins the binding mechanism: the deprecation config, the PEP 702 marker and the warn budget stay reachable
        through ``__func__``, and introspection (``inspect.signature``, IDE help) shows the signature callers use.

        """
        host_cls = make_deprecated_method_host()
        host = host_cls()
        proxy = host_cls.__dict__["scale"]

        bound = host.scale

        assert isinstance(bound, MethodType)
        assert bound.__func__ is proxy
        assert bound.__self__ is host
        assert list(inspect.signature(bound).parameters) == ["factor"]
        assert cast(Any, bound).__deprecated__ == cast(Any, proxy).__deprecated__

    def test_class_access_returns_stored_proxy(self) -> None:
        """Class access returns the stored proxy itself, so an explicit-instance call works and warns as before.

        Code calling ``Model.scale(model, 2)`` — unbound style, or a subclass delegating to its parent — keeps today's
        behaviour: a plain function read from its class does not bind, so the proxy must not either.

        """
        host_cls = make_deprecated_method_host()
        host = host_cls()

        with pytest.warns(FutureWarning, match="scale"):
            result = host_cls.scale(host, 3)

        assert host_cls.scale is host_cls.__dict__["scale"]
        assert result == (host, 3)

    def test_bound_call_warning_points_at_caller(self) -> None:
        """The warning from a bound call names this test file and the calling line.

        Binding adds no Python frame between the caller and the proxy's ``__call__``, so warning filters keyed on the
        calling module and ``-W error`` tracebacks keep pointing at user code, not into ``deprecate``.

        """
        host = make_deprecated_method_host()()
        frame = inspect.currentframe()
        assert frame is not None

        call_line = frame.f_lineno + 2  # the ``host.scale(4)`` line two lines below
        with pytest.warns(FutureWarning) as record:
            host.scale(4)

        assert record[0].filename == __file__
        assert record[0].lineno == call_line

    def test_unlimited_budget_warns_per_bound_call(self) -> None:
        """With ``num_warns=-1`` every bound call warns, exactly as direct calls on the proxy do.

        A strict migration window that wants every use reported must not be undercut by binding: each call through an
        instance spends from the proxy's own unlimited budget.

        """
        host = make_deprecated_method_host(num_warns=-1)()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            results = (host.scale(1), host.scale(2))

        assert results == ((host, 1), (host, 2))
        assert _future_warning_count(caught) == 2

    def test_skip_if_serves_bound_source_silently(self) -> None:
        """With ``skip_if=True`` a bound call runs the wrapped function with the instance, without warning.

        The deprecation is gated off by a flag: the method must behave exactly like the undeprecated function stored
        on the class — bound to the instance, silent.

        """
        host = make_deprecated_method_host(skip_if=True)()

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            result = host.scale(5)

        assert result == (host, 5)

    def test_fatal_bound_call_raises(self) -> None:
        """With ``as_exception=True`` a bound call raises ``DeprecatedError`` instead of running the method.

        Attribute access stays silent (it is how ``hasattr`` and introspection read members); the call is the use of
        the deprecated API, so it is where the fatal deprecation fires.

        """
        host = make_deprecated_method_host(as_exception=True)()
        bound = host.scale

        with pytest.raises(DeprecatedError, match="scale"):
            bound(6)

    @pytest.mark.parametrize("attr", ["defaults", "Palette"])
    def test_non_descriptor_attribute_returns_stored_proxy(self, attr: str) -> None:
        """Instance and class access to a proxy over a non-descriptor return the stored proxy unchanged.

        Deprecated constants and class aliases held on a class (``Config.DEFAULTS``, ``Toolkit.OldWidget``) must not
        be re-wrapped or bound: a ``dict`` instance and a class are not descriptors, so the proxy is served as stored.

        """
        host_cls = make_deprecated_data_host()
        stored = host_cls.__dict__[attr]

        from_instance, from_class = getattr(host_cls(), attr), getattr(host_cls, attr)

        assert from_instance is stored
        assert from_class is stored

    def test_partial_attribute_served_unbound_without_warning(self) -> None:
        """Instance access to a proxy over ``functools.partial`` returns the stored proxy and emits nothing.

        Python 3.13 gives ``functools.partial`` a ``__get__`` that warns ``functools.partial will be a method
        descriptor``, and 3.14 makes it bind. A deprecated partial held on a class must behave the same on every
        version — served as stored, silently — so the access runs under an all-errors filter.
        """
        host_cls = make_deprecated_partial_host()
        host = host_cls()

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            from_instance = host.double

        assert from_instance is host_cls.__dict__["double"]

    def test_partial_call_through_instance_passes_no_instance(self) -> None:
        """Calling a deprecated partial through an instance calls the partial with the caller's arguments only.

        ``host.double(3)`` must reach ``double_value(3)``; binding would prepend the instance and fail with
        ``TypeError``. The call is the use of the deprecated object, so it warns once.
        """
        host = make_deprecated_partial_host()()

        with pytest.warns(FutureWarning, match="double") as record:
            result = host.double(3)

        assert (result, len(record)) == (6, 1)


def _future_warning_count(caught: list[warnings.WarningMessage]) -> int:
    """Count the deprecation ``FutureWarning`` records captured by ``catch_warnings(record=True)``."""
    return sum(issubclass(record.category, FutureWarning) for record in caught)


def _drive_getitem(proxy: Any) -> object:  # noqa: ANN401
    """Subscript the proxy."""
    return proxy[0]


def _drive_iter(proxy: Any) -> object:  # noqa: ANN401
    """Start iterating the proxy."""
    return iter(proxy)


def _drive_neg(proxy: Any) -> object:  # noqa: ANN401
    """Negate the proxy (a unary forward)."""
    return -proxy


def _drive_round(proxy: Any) -> object:  # noqa: ANN401
    """Round the proxy."""
    return round(proxy)


def _drive_aiter(proxy: Any) -> object:  # noqa: ANN401
    """Start async iteration over the proxy."""
    return proxy.__aiter__()


def _drive_next(proxy: Any) -> object:  # noqa: ANN401
    """Advance the proxy as an iterator."""
    return next(proxy)


def _drive_enter(proxy: Any) -> object:  # noqa: ANN401
    """Enter and leave the proxy as a context manager."""
    with proxy as entered:
        return entered


def _drive_iadd(proxy: Any) -> object:  # noqa: ANN401
    """Apply an in-place addition through the proxy."""
    proxy += 3
    return proxy


async def _enter_async(proxy: Any) -> object:  # noqa: ANN401
    """Enter and leave the proxy as an async context manager."""
    async with proxy as entered:
        return entered


def _drive_aenter(proxy: Any) -> object:  # noqa: ANN401
    """Run ``async with proxy`` on a fresh event loop."""
    return asyncio.run(_enter_async(proxy))


def _drive_anext(proxy: Any) -> object:  # noqa: ANN401
    """Request the next async item from the proxy and await it on a fresh event loop."""
    return asyncio.run(proxy.__anext__())


async def _await_proxy(proxy: Any) -> object:  # noqa: ANN401
    """Await the proxy."""
    return await proxy


def _drive_await(proxy: Any) -> object:  # noqa: ANN401
    """Await the proxy on a fresh event loop."""
    return asyncio.run(_await_proxy(proxy))


#: Operations that only read the wrapped object: fatal gate first, then compute, then warn.
_PURE_PROXY_OPS = [
    pytest.param("__getitem__", _drive_getitem, id="getitem"),
    pytest.param("__iter__", _drive_iter, id="iter"),
    pytest.param("__neg__", _drive_neg, id="unary-neg"),
    pytest.param("__round__", _drive_round, id="round"),
    pytest.param("__aiter__", _drive_aiter, id="aiter"),
]
#: Operations with side effects on the wrapped object: fatal gate first, then run, then warn.
_SIDE_EFFECTING_PROXY_OPS = [
    pytest.param("__enter__", _drive_enter, id="enter"),
    pytest.param("__iadd__", _drive_iadd, id="inplace-add"),
    pytest.param("__aenter__", _drive_aenter, id="aenter"),
    pytest.param("__anext__", _drive_anext, id="anext"),
    pytest.param("__await__", _drive_await, id="await"),
]
#: Operations that consume what they read: fatal gate first, then warn, then run.
_WARN_FIRST_PROXY_OPS = [pytest.param("__next__", _drive_next, id="next")]
#: Every operation that refuses a fatal deprecation before it touches the wrapped object.
_GATED_PROXY_OPS = [*_PURE_PROXY_OPS, *_SIDE_EFFECTING_PROXY_OPS, *_WARN_FIRST_PROXY_OPS]


class TestWarnAfterSuccess:
    """Proxy data-use operations warn only once the forwarded operation has succeeded.

    Duck-typing code probes a deprecated object inside ``try``/``except`` (``cfg[key]`` with a ``KeyError`` fallback,
    an operation the wrapped object does not support). A warning emitted before the operation would spend the default
    one-warning budget on a probe that failed, leaving the real use silent. Every one of these operations still refuses
    a fatal deprecation before it runs, so ``as_exception=True`` never reads, advances or mutates the wrapped object.
    ``next`` is the exception that warns first, see :class:`TestNextWarnsBeforeAdvancing`.
    """

    @pytest.mark.parametrize(("operation", "drive"), [*_PURE_PROXY_OPS, *_SIDE_EFFECTING_PROXY_OPS])
    def test_failed_operation_keeps_budget(self, operation: str, drive: Callable[[Any], object]) -> None:
        """A failing operation emits no warning; the same operation warns once when it later succeeds.

        A legacy object wrapped with the default ``num_warns=1`` budget is first used while the wrapped operation
        raises (a probe that fails), then again once it works. Only the successful use may spend the budget — the
        failed one must leave it intact so the migration notice still reaches the caller.
        """
        box = FlakyProtocolBox(fail=True)
        proxy = make_warn_after_success_proxy(box)
        with warnings.catch_warnings(record=True) as failed:
            warnings.simplefilter("always")
            with pytest.raises(RuntimeError, match=f"{operation} failed on purpose"):
                drive(proxy)
        box.fail = False
        with warnings.catch_warnings(record=True) as succeeded:
            warnings.simplefilter("always")
            drive(proxy)
        assert (_future_warning_count(failed), _future_warning_count(succeeded), box.calls[operation]) == (0, 1, 2)

    @pytest.mark.parametrize(("operation", "drive"), _GATED_PROXY_OPS)
    def test_fatal_operation_never_runs(self, operation: str, drive: Callable[[Any], object]) -> None:
        """A fatal deprecation refuses the operation before the wrapped object is touched.

        With ``as_exception=True`` the proxy must raise ``DeprecatedError`` without subscripting, iterating, negating,
        rounding, advancing, entering or mutating the wrapped object. A fatal deprecation means the legacy code does not
        run at all, so moving the warning after a successful operation must not let that code run first.
        """
        box = FlakyProtocolBox()
        proxy = make_warn_after_success_proxy(box, as_exception=True)
        with pytest.raises(DeprecatedError, match="legacy_box"):
            drive(proxy)
        assert box.calls[operation] == 0

    @pytest.mark.parametrize(("operation", "drive"), _GATED_PROXY_OPS)
    def test_skip_if_bypasses_warning_and_gate(self, operation: str, drive: Callable[[Any], object]) -> None:
        """An inactive deprecation neither warns nor applies the fatal gate.

        ``skip_if=True`` turns the deprecation off even when it is configured fatal: every operation runs on the
        wrapped object exactly once and no warning is emitted.
        """
        box = FlakyProtocolBox()
        proxy = make_warn_after_success_proxy(box, as_exception=True, skip_if=True)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            drive(proxy)
        assert (_future_warning_count(caught), box.calls[operation]) == (0, 1)

    def test_missing_key_probe_keeps_budget(self) -> None:
        """A ``KeyError`` probe on a deprecated mapping emits no warning; the next successful lookup warns once.

        ``try: cfg[key] except KeyError: ...`` is the usual duck-typed optional-setting lookup. The miss must not use
        up the one warning the real ``cfg["threshold"]`` read is meant to deliver.
        """
        proxy = make_warn_after_success_proxy({"threshold": 0.5})
        with warnings.catch_warnings(record=True) as missed:
            warnings.simplefilter("always")
            with pytest.raises(KeyError):
                _ = proxy["missing"]
        with warnings.catch_warnings(record=True) as found:
            warnings.simplefilter("always")
            value = proxy["threshold"]
        assert (_future_warning_count(missed), value, _future_warning_count(found)) == (0, 0.5, 1)

    def test_missing_key_under_fatal_raises_deprecated_error(self) -> None:
        """A missing key on a fatal mapping raises ``DeprecatedError``, not ``KeyError``.

        The fatal deprecation refuses the subscript before the lookup runs, the same as for a present key, so code that
        still reads the deprecated mapping fails loudly even when it only probes it with ``try: cfg[key] except
        KeyError``.
        """
        proxy = make_warn_after_success_proxy({"threshold": 0.5}, as_exception=True)
        with pytest.raises(DeprecatedError, match="legacy_box"):
            _ = proxy["missing"]

    def test_fatal_missing_key_leaves_defaultdict_unchanged(self) -> None:
        """A fatal subscript on a ``defaultdict`` raises without inserting the missing key.

        Reading a missing key from a ``defaultdict`` creates the entry as a side effect. Under ``as_exception=True`` the
        deprecated mapping must stay exactly as it was, so the proxy has to refuse the read before ``__getitem__``
        reaches ``__missing__``.
        """
        source: defaultdict[str, list[int]] = defaultdict(list)
        proxy = make_warn_after_success_proxy(source, as_exception=True)
        with pytest.raises(DeprecatedError, match="legacy_box"):
            _ = proxy["missing"]
        assert dict(source) == {}

    def test_declined_inplace_operator_warns_once(self) -> None:
        """An in-place hook that returns ``NotImplemented`` adds no warning of its own to the ``__add__`` fallback.

        ``proxy += 1`` on a value whose ``__iadd__`` declines makes Python retry ``__add__`` on the proxy. The
        statement is one use of the deprecated object, so even an unlimited budget must record exactly one warning.
        """
        proxy = make_warn_after_success_proxy(DecliningInplaceOperand(5), num_warns=-1)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy += 1
        assert (_future_warning_count(caught), proxy.value) == (1, 6)

    def test_attrs_mapping_failed_lookup_keeps_budget(self) -> None:
        """A failing read of a renamed attribute emits no warning; the same read warns once after it succeeds.

        ``attrs_mapping`` keeps one budget per deprecated name. When the redirected lookup raises, that name's budget
        must stay unspent so the next successful ``Legacy.legacy_total`` read still announces the rename.
        """
        host = make_flaky_attr_host()
        alias = make_flaky_attr_alias(host)
        host.fail = True
        with warnings.catch_warnings(record=True) as failed:
            warnings.simplefilter("always")
            with pytest.raises(RuntimeError, match="failed on purpose"):
                _ = alias.legacy_total
        host.fail = False
        with warnings.catch_warnings(record=True) as succeeded:
            warnings.simplefilter("always")
            value = alias.legacy_total
        assert (_future_warning_count(failed), value, _future_warning_count(succeeded)) == (0, 3, 1)

    def test_attrs_mapping_fatal_read_never_runs_getter(self) -> None:
        """A fatal renamed-attribute read raises ``DeprecatedError`` before the redirected getter runs.

        ``Legacy.legacy_total`` resolves through a descriptor on the canonical ``total`` attribute. A fatal deprecation
        must refuse the deprecated name before that descriptor executes, so the host's read counter stays at zero.
        """
        host = make_flaky_attr_host()
        alias = make_flaky_attr_alias(host, as_exception=True)
        with pytest.raises(DeprecatedError, match="legacy_total"):
            _ = alias.legacy_total
        assert host.reads == 0

    def test_call_still_warns_before_invoking(self) -> None:
        """Calling the proxy keeps warn-before: a call that raises still reports the deprecation.

        ``__call__`` mirrors ``@deprecated``, which warns before invoking, so a deprecated constructor or callable that
        fails still tells the caller the API is deprecated.
        """
        box = FlakyProtocolBox(fail=True)
        proxy = make_warn_after_success_proxy(box)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(RuntimeError, match="__call__ failed on purpose"):
                proxy()
        assert _future_warning_count(caught) == 1


class TestNextWarnsBeforeAdvancing:
    """``next(proxy)`` delivers its warning before the wrapped iterator moves, so a failed delivery consumes no item."""

    @pytest.mark.parametrize("raising_stream", [False, True])
    def test_failed_delivery_does_not_advance(self, raising_stream: bool) -> None:
        """A warning that cannot be delivered leaves the wrapped iterator where it was.

        A test suite running with ``FutureWarning`` as an error, or an application whose logging sink raises, sees the
        first ``next`` on a deprecated iterator fail. The item it would have returned must still be there for the
        next read, so the wrapped ``__next__`` must not run before the warning is out.
        """
        box = FlakyProtocolBox(items=(10, 20, 30))
        stream = KeywordTypeErrorStream()
        proxy = make_warn_after_success_proxy(box, **({"stream": stream} if raising_stream else {}))
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(TypeError if raising_stream else FutureWarning):
                next(proxy)
        assert box.calls["__next__"] == 0

    def test_failing_next_still_warns(self) -> None:
        """A wrapped ``__next__`` that raises still reports the deprecation.

        The warning is delivered before the iterator is touched, so the caller learns that the iterator is deprecated
        even when advancing it fails.
        """
        proxy = make_warn_after_success_proxy(FlakyProtocolBox(fail=True))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(RuntimeError, match="__next__ failed on purpose"):
                next(proxy)
        assert _future_warning_count(caught) == 1

    def test_exhausting_next_spends_budget(self) -> None:
        """An exhausting ``next(proxy)`` warns once; the item produced afterwards is silent.

        Warning first means the call that ends iteration with ``StopIteration`` is reported like any other use. With the
        default one-warning budget it is the only notice, matching how ``__anext__`` spends the budget before async
        exhaustion.
        """
        box = FlakyProtocolBox(items=())
        proxy = make_warn_after_success_proxy(box)
        with warnings.catch_warnings(record=True) as exhausted:
            warnings.simplefilter("always")
            with pytest.raises(StopIteration):
                next(proxy)
        box.items.append(7)
        with warnings.catch_warnings(record=True) as produced:
            warnings.simplefilter("always")
            item = next(proxy)
        assert (_future_warning_count(exhausted), item, _future_warning_count(produced)) == (1, 7, 0)

    def test_non_iterator_raises_without_warning(self) -> None:
        """``next`` on a proxy over a non-iterator raises the builtin ``TypeError`` and emits nothing.

        Duck-typing code calls ``next(obj)`` and falls back on ``TypeError`` when it holds an iterable instead of an
        iterator. That unsupported protocol is not a use of the deprecated object, so it must not spend the budget.
        """
        proxy = make_warn_after_success_proxy([1, 2])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(TypeError, match="is not an iterator"):
                next(proxy)
        assert _future_warning_count(caught) == 0


# Statically `DeprecatedColorEnum` is still the class statement: mypy does not rebind a decorated
# class to the proxy it becomes at runtime, which is the decorator-form limitation documented on
# `DeprecationProxy`. Cast once so the breadcrumb reads below type-check against the real runtime object.
_ast_class_proxy = cast(DeprecationProxy[Any], DeprecatedColorEnum)


if TYPE_CHECKING:
    # The static type of a `deprecated_class` alias follows what the proxy forwards to at runtime. Pinned statically
    # because the runtime object is a `_DeprecatedProxy` either way: a regression back to the proxy type, or a widening
    # to `Any`, would leave every runtime test passing. mypy analyses this block (always true for a type checker) but
    # the interpreter never executes it, so the calls construct nothing and emit no warnings.
    # NOTE: `[tool.mypy] mypy_path = "src"` is what makes these bite — without it `deprecate` is
    # unresolvable from `tests/**`, `ignore_missing_imports` turns every symbol into `Any`, and
    # `assert_type` degrades to a silent no-op that passes against any signature whatsoever.
    from typing_extensions import assert_type

    # Without a replacement class the alias stands in for the wrapped class and keeps its own type.
    assert_type(DeprecatedPaletteFunctionalFallback, type[Palette])
    # Using a proxy-typed alias would break downstream: `isinstance`/`issubclass` narrowing, construction, and handing
    # the alias on wherever a class object is expected.
    _any_object: object = Palette()
    if isinstance(_any_object, DeprecatedPaletteFunctionalFallback):
        assert_type(_any_object, Palette)
    _any_class: type = Palette
    if issubclass(_any_class, DeprecatedPaletteFunctionalFallback):
        assert_type(_any_class, type[Palette])
    assert_type(DeprecatedPaletteFunctionalFallback(), Palette)
    # With `target=NewCls` the alias is the target: the proxy resolves `isinstance`, construction and attribute access
    # against it, so the private source it wraps never shows up in the type (its instances are not instances of it).
    assert_type(DeprecatedColorEnumFunctional, type[ColorEnum])
    if isinstance(_any_object, DeprecatedColorEnumFunctional):
        assert_type(_any_object, ColorEnum)
    assert_type(DeprecatedColorEnumFunctional(1), ColorEnum)
    # The wrapped class does not matter for the result: a bare `type` (built with `type(name, bases, ns)`) is accepted
    # and the alias is still the target. Stacking another alias or a proxy-typed object is accepted as well.
    assert_type(
        deprecated_class(target=Palette, deprecated_in="1.0", remove_in="2.0")(type("_Dynamic", (), {})), type[Palette]
    )
    assert_type(
        deprecated_class(target=Palette, deprecated_in="1.0", remove_in="2.0")(DeprecatedColorEnumFunctional),
        type[Palette],
    )
    assert_type(
        deprecated_class(target=Palette, deprecated_in="1.0", remove_in="2.0")(_ast_class_proxy), _DeprecatedProxy
    )
    # Stacking over a class-typed alias keeps the class type; a class built with `type(name, bases, ns)` stays a plain
    # `type` (a `type[_T] -> type[_T]` signature would infer `type[Never]` there); an object already typed as a proxy
    # keeps the concrete proxy type.
    assert_type(
        deprecated_class(deprecated_in="1.0", remove_in="2.0")(DeprecatedPaletteFunctionalFallback), type[Palette]
    )
    assert_type(deprecated_class(deprecated_in="1.0", remove_in="2.0")(type("_Dynamic", (), {})), type)
    assert_type(deprecated_class(deprecated_in="1.0", remove_in="2.0")(_ast_class_proxy), _DeprecatedProxy)
    # `skip_if` cannot be modelled: the steady-state target type is used, so a source that is served while skipped
    # must stay compatible with it.
    assert_type(deprecated_class(target=Palette, skip_if=True, stream=None)(Palette), type[Palette])

    # Decorator form: the alias is a class statement, so it is also valid in annotations and as a base class.
    # A call-form alias is a variable, which no type checker accepts in a type expression.
    def _accepts_legacy(config: DeprecatedColorDataClass) -> DeprecatedColorDataClass:
        return config

    class _LegacySubclass(DeprecatedColorDataClass):
        pass


class TestProxyAstFriendliness:
    """The proxy exposes ``__wrapped__`` and ``__signature__`` for static-analysis tools.

    Tools like Sphinx autodoc and ``inspect.signature`` need a stable breadcrumb back to the
    original callable so they can render the source's documentation and signature rather than
    the proxy's own internals. This set of tests pins down the contract.
    """

    def test_wrapped_points_to_source_on_class_proxy(self) -> None:
        """``DeprecatedColorEnum.__wrapped__`` is the source enum class.

        A user inspecting the deprecated symbol via ``inspect.unwrap`` or an AST-aware tool
        that walks ``__wrapped__`` should land on the original class definition. Here the
        source is the enum class itself (defined in the same fixture, wrapped by the proxy).
        """
        wrapped = _ast_class_proxy.__wrapped__
        cfg = object.__getattribute__(DeprecatedColorEnum, "_DeprecatedProxy__config")
        assert wrapped is cfg.obj
        assert inspect.isclass(wrapped)
        assert issubclass(wrapped, Enum)

    def test_unwrap_reaches_source_on_class_proxy(self) -> None:
        """``inspect.unwrap`` walks the proxy's ``__wrapped__`` chain down to the source class.

        Sphinx autodoc and similar tools call ``inspect.unwrap`` rather than reading ``__wrapped__``
        directly. Since the source enum is not itself a wrapper, unwrapping must terminate there
        instead of looping or stopping at the proxy.
        """
        assert inspect.unwrap(DeprecatedColorEnum) is _ast_class_proxy.__wrapped__

    def test_signature_matches_source_on_class_proxy(self) -> None:
        """``DeprecatedColorEnum.__signature__`` equals ``inspect.signature`` of its source.

        ``inspect.signature`` follows ``__signature__`` when present, so static tools that do
        not walk ``__wrapped__`` still see the source's constructor signature.
        """
        source = _ast_class_proxy.__wrapped__
        assert _ast_class_proxy.__signature__ == inspect.signature(source)

    def test_inspect_signature_on_proxy_returns_source_signature(self) -> None:
        """``inspect.signature(proxy)`` returns the source's signature.

        This is the user-visible payoff: tools that call ``inspect.signature`` directly on the
        deprecated symbol now get the real signature instead of an unhelpful ``(*args, **kwargs)``
        or ``ValueError``.
        """
        source = _ast_class_proxy.__wrapped__
        assert inspect.signature(DeprecatedColorEnum) == inspect.signature(source)

    def test_wrapped_set_on_instance_proxy(self) -> None:
        """``deprecated_instance`` also sets ``__wrapped__`` to the wrapped object.

        Both public entry points must preserve the breadcrumb so the contract is consistent
        across class and instance deprecation.
        """
        assert depr_ast_breadcrumb_dict.__wrapped__ is ast_breadcrumb_source_dict

    def test_signature_silently_skipped_for_non_introspectable_object(self) -> None:
        """``__signature__`` is ``None`` when ``inspect.signature(obj)`` raises.

        Builtins and objects without a Python-level signature (e.g. ``dict``) make
        ``inspect.signature`` raise ``ValueError``/``TypeError``. The proxy must not propagate
        the exception at decoration time — wrapping must stay infallible. The attribute is
        always set (to ``None``) so ``@runtime_checkable`` Protocol checks succeed uniformly.
        """
        assert depr_ast_breadcrumb_dict.__signature__ is None

    def test_signature_fallback_survives_hostile_descriptor(self) -> None:
        """A source whose ``__signature__`` descriptor raises ``RuntimeError`` still wraps successfully.

        ``ValueError``/``TypeError`` are only the *normalised* introspection failures. A wrapped object may
        expose a ``__signature__`` property that raises anything at all — mocks, lazily built extension
        wrappers, and some model metaclasses do — and ``inspect.signature`` forwards that exception verbatim
        instead of converting it. Since the breadcrumbs are a convenience for static tooling, never a
        precondition of the deprecation itself, such a source must degrade to ``__signature__ = None`` rather
        than turning a routine ``deprecated_instance`` call into an import-time crash.
        """
        proxy = make_deprecated_hostile_signature_instance()
        assert proxy.__signature__ is None
        assert isinstance(proxy.__wrapped__, HostileSignatureCallable)

    def test_existing_runtime_behavior_unchanged(self) -> None:
        """Adding ``__wrapped__``/``__signature__`` does not affect runtime forwarding.

        A regression guard: the new attributes must be purely additive. Attribute access,
        ``isinstance``, and item access continue to behave exactly as before.
        """
        with pytest.warns(FutureWarning):
            assert DeprecatedColorEnum.RED is ColorEnum.RED


class TestDeprecatedProtocol:
    """The ``DeprecationProxy`` Protocol is part of the public API surface.

    Provides a documented, public type for the result of ``deprecated_class`` and
    ``deprecated_instance`` so user code can annotate against it without importing a private name.
    """

    def test_public_symbol_exported(self) -> None:
        """``DeprecationProxy`` is importable from ``deprecate`` and listed in ``__all__``.

        This is the contract users rely on for type annotations without needing to know the private
        ``_DeprecatedProxy`` name; a symbol missing from ``__all__`` is invisible to star-imports and
        to the docs build, so both halves are pinned here.
        """
        assert deprecate.DeprecationProxy is DeprecationProxy
        assert "DeprecationProxy" in deprecate.__all__

    @pytest.mark.parametrize(
        "legacy_name",
        [
            pytest.param("Deprecated", id="bare-protocol-alias"),
            pytest.param("DeprecatedClass", id="class-alias"),
            pytest.param("DeprecatedInstance", id="instance-alias"),
        ],
    )
    def test_legacy_aliases_removed(self, legacy_name: str) -> None:
        """The pre-rename aliases are gone from the package surface, not merely undocumented.

        ``Deprecated``, ``DeprecatedClass`` and ``DeprecatedInstance`` were all bound to the same object
        before the rename to ``DeprecationProxy``. Absence is currently only a matter of nobody
        re-adding them; without this guard a stray re-export — or an ``__all__`` entry left behind by a
        merge — would restore names the rename exists to retire, and no other test would notice.
        """
        assert not hasattr(deprecate, legacy_name)
        assert legacy_name not in deprecate.__all__

    def test_runtime_checkable_protocol(self) -> None:
        """``DeprecationProxy`` is ``@runtime_checkable``: ``isinstance`` works on proxy instances.

        Although the Protocol is structural, runtime checks let user code write
        ``isinstance(my_proxy, DeprecationProxy)`` without a cast. Both ``deprecated_class`` and
        ``deprecated_instance`` outputs must satisfy the Protocol at runtime.
        """
        assert isinstance(DeprecatedColorEnum, DeprecationProxy)
        assert isinstance(depr_ast_breadcrumb_dict, DeprecationProxy)

    def test_issubclass_rejects_data_protocol(self) -> None:
        """``issubclass`` against ``DeprecationProxy`` raises ``TypeError``.

        ``DeprecationProxy`` declares attributes (``__wrapped__``, ``__deprecation_config__``), which makes it a
        *data* Protocol — Python only supports ``isinstance`` for those. Users reaching for
        ``issubclass`` get a hard error rather than a wrong answer, so the docstring warning is pinned
        here to catch any future change that silently loosens it.
        """
        with pytest.raises(TypeError, match="non-method members"):
            issubclass(dict, DeprecationProxy)  # type: ignore[misc]

    def test_wrapped_is_typed_as_attribute_on_proxy_class(self) -> None:
        """``_DeprecatedProxy`` declares ``__wrapped__`` and ``__signature__`` at class level.

        Static type-checkers cannot introspect attributes set only in ``__init__`` without a
        class-level declaration. The annotation lets mypy/pyright see the breadcrumb attributes.
        """
        # Inspect class annotations directly — attributes set only in __init__ are absent from __annotations__.
        assert "__wrapped__" in _DeprecatedProxy.__annotations__
        assert "__signature__" in _DeprecatedProxy.__annotations__

    def test_functional_form_forwards_like_decorator_form(self) -> None:
        """The functional/assignment form forwards construction exactly as the decorator form does.

        ``deprecated_class`` returns the concrete ``_DeprecatedProxy`` in every call shape, so the choice
        between ``deprecated_class(target=ColorEnum)(OldCls)`` and ``@deprecated_class(target=ColorEnum)``
        is a static-annotation concern only. This pins that it stays a static one: whichever form a caller
        picks — annotated or not — the proxy must forward construction to the target identically at runtime.
        """
        # Runtime: calling the proxy forwards to the target constructor, exactly as the decorator form does.
        assert DeprecatedColorEnumFunctional(1) is ColorEnum.RED
        assert isinstance(DeprecatedColorEnumFunctional, DeprecationProxy)


class TestEntryWarningCleanup:
    """Failed warning delivery must unwind resources acquired by proxy entry."""

    @pytest.mark.parametrize("drive", [pytest.param(_drive_enter, id="sync"), pytest.param(_drive_aenter, id="async")])
    @pytest.mark.parametrize("suppress", [False, True])
    @pytest.mark.parametrize("raising_stream", [False, True])
    def test_emission_failure_unwinds_acquired_context(
        self, drive: Callable[[Any], object], suppress: bool, raising_stream: bool
    ) -> None:
        """Clean up acquired contexts even if their exit hook would suppress errors.

        Applications treating warnings as errors, or using a failing logging sink,
        never reach the with body. The acquired resource must still receive the exact
        emission exception once and that exception must remain visible to the caller.
        """
        resource = RecordingEntryContext(suppress=suppress)
        stream = KeywordTypeErrorStream()
        proxy = make_warn_after_success_proxy(resource, **({"stream": stream} if raising_stream else {}))
        expected = TypeError if raising_stream else FutureWarning
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(expected) as raised:
                drive(proxy)
        assert resource.entries == 1
        assert len(resource.exits) == 1
        exc_type, exc, traceback = resource.exits[0]
        assert exc_type is expected
        assert exc is raised.value
        assert traceback is not None
        if raising_stream:
            assert len(stream.calls) == 1

    @pytest.mark.parametrize("drive", [pytest.param(_drive_enter, id="sync"), pytest.param(_drive_aenter, id="async")])
    def test_cleanup_uses_acquired_context_after_skip_changes(self, drive: Callable[[Any], object]) -> None:
        """Unwind the acquired target even if the failing stream changes skip state.

        A logging callback can activate a feature flag before it raises. Cleanup must
        use the context already acquired, although future proxy operations serve source.
        """
        source = RecordingEntryContext()
        target = RecordingEntryContext()
        proxy = make_switching_entry_proxy(source, target)
        with pytest.raises(RuntimeError, match="^delivery failed$") as raised:
            drive(proxy)
        assert (source.entries, source.exits, target.entries) == (0, [], 1)
        assert len(target.exits) == 1
        assert target.exits[0][1] is raised.value

    def test_warning_error_releases_real_lock(self) -> None:
        """Release a real lock when entry fails because a warning becomes an error.

        A subsequent nonblocking acquisition proves the warning path does not leave
        shared application state locked after Python declines to enter the with body.
        """
        lock = threading.Lock()
        proxy = make_warn_after_success_proxy(lock)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", FutureWarning)
                with pytest.raises(FutureWarning):
                    _drive_enter(proxy)
            assert not lock.locked()
            assert lock.acquire(blocking=False)
        finally:
            if lock.locked():
                lock.release()

    @pytest.mark.parametrize("drive", [pytest.param(_drive_enter, id="sync"), pytest.param(_drive_aenter, id="async")])
    @pytest.mark.parametrize("fail", [False, True])
    def test_underlying_entry_controls_cleanup(self, drive: Callable[[Any], object], fail: bool) -> None:
        """Clean up exactly once after successful entry and never after failed entry.

        A context whose own acquisition raises has nothing to unwind. Normal entry
        must still warn once, return the acquired resource and exit without an error.
        """
        resource = RecordingEntryContext(fail=fail)
        proxy = make_warn_after_success_proxy(resource)
        if fail:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with pytest.raises(RuntimeError, match="^entry failed$"):
                    drive(proxy)
            assert not caught
            assert resource.exits == []
        else:
            with pytest.warns(FutureWarning) as caught:
                assert drive(proxy) is resource
            assert len(caught) == 1
            assert resource.exits == [(None, None, None)]
        assert resource.entries == 1


class TestSkippedInplaceReadOnly:
    """Read-only in-place operators honor the same dynamic skip as other writes."""

    def test_skip_bypasses_guards_then_rearms_without_mutation(self) -> None:
        """Permit skipped mutation and reject the same operation once skip is off.

        A feature flag disables a fatal read-only deprecation during a transition.
        The source list may mutate while skipped, but reactivation must reject the
        in-place operation before it changes the list or emits a warning.
        """
        source = [1, 2]
        flag = {"skip": True}
        proxy = make_read_only_inplace_proxy(source, flag)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = proxy.__iadd__([3])
            flag["skip"] = False
            with pytest.raises(AttributeError, match="read-only"):
                proxy.__iadd__([4])
        assert result is source
        assert source == [1, 2, 3]
        assert not caught


class TestDeferredAsyncWarningTiming:
    """Acquiring an awaitable spends the budget before deferred execution fails."""

    def test_exhaustion_after_acquisition_spends_budget(self) -> None:
        """Warn before an async iterator reports exhaustion during awaiting.

        An async iterator obtains its coroutine successfully but has no items left.
        The acquisition warning consumes the single notice before StopAsyncIteration.
        """
        source = FlakyProtocolBox(items=())
        proxy = make_warn_after_success_proxy(source)
        with pytest.warns(FutureWarning) as caught:
            pending = proxy.__anext__()
        assert len(caught) == 1
        with pytest.raises(StopAsyncIteration):
            asyncio.run(pending)
        source.items.append(7)
        with warnings.catch_warnings(record=True) as later:
            warnings.simplefilter("always")
            assert _drive_anext(proxy) == 7
        assert not later

    def test_await_failure_after_acquisition_spends_budget(self) -> None:
        """Warn before driving an await iterator that subsequently raises.

        A lazy awaitable acquires its iterator normally and fails only when awaited.
        Retrying the await still fails but cannot issue another default-budget notice.
        """
        proxy = make_warn_after_success_proxy(DeferredFailureAwaitable())
        with pytest.warns(FutureWarning) as caught, pytest.raises(RuntimeError, match="^deferred await failed$"):
            _drive_await(proxy)
        assert len(caught) == 1
        with warnings.catch_warnings(record=True) as later:
            warnings.simplefilter("always")
            with pytest.raises(RuntimeError, match="^deferred await failed$"):
                _drive_await(proxy)
        assert not later


def _drive_getattr(proxy: Any) -> object:  # noqa: ANN401
    """Read a plain attribute through the proxy."""
    return proxy.items


#: Proxy operations that each run exactly one forwarded hook, so ``skip_if`` must be consulted exactly once.
_SINGLE_HOOK_PROXY_OPS = [
    *_PURE_PROXY_OPS,
    pytest.param("__next__", _drive_next, id="next"),
    pytest.param("__iadd__", _drive_iadd, id="inplace-add"),
    pytest.param("__anext__", _drive_anext, id="anext"),
    pytest.param("__await__", _drive_await, id="await"),
    pytest.param("items", _drive_getattr, id="getattr"),
]


class TestSingleSkipDecision:
    """Each proxy operation evaluates ``skip_if`` once and serves and warns on that single answer."""

    @pytest.mark.parametrize(("operation", "drive"), _SINGLE_HOOK_PROXY_OPS)
    def test_skip_condition_consulted_once(self, operation: str, drive: Callable[[Any], object]) -> None:
        """A forwarded operation calls the ``skip_if`` callable exactly once.

        A migration flag read from settings or the environment is evaluated on every proxy access. Re-reading it for
        the fatal check, the active object and the warning multiplies that cost per access and lets the three reads
        disagree, so the operation must take one decision and reuse it.
        """
        condition = CountingSkipCondition()
        proxy = make_warn_after_success_proxy(FlakyProtocolBox(), skip_if=condition)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            drive(proxy)
        assert condition.calls == 1

    def test_flipping_condition_serves_and_warns_consistently(self) -> None:
        """A ``skip_if`` that turns true after its first read still serves the target and warns for that access.

        The flag flips while the operation runs, as when another thread finishes a migration. The access that read the
        flag as live must forward to the replacement and report the deprecation together; serving the old object
        silently would mix the two decisions.
        """
        condition = CountingSkipCondition(answers=(False, True))
        proxy = make_skip_gated_target_proxy(["legacy"], ["replacement"], condition)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            value = proxy[0]
        assert (value, _future_warning_count(caught)) == ("replacement", 1)


def _acquire_anext(proxy: Any) -> object:  # noqa: ANN401
    """Obtain the next awaitable from the proxy without awaiting it."""
    return proxy.__anext__()


def _acquire_await(proxy: Any) -> object:  # noqa: ANN401
    """Obtain the proxy's await iterator without driving it."""
    return proxy.__await__()


async def _collect(stream: Any) -> list[Any]:  # noqa: ANN401
    """Drain an async iterable into a list."""
    return [item async for item in stream]


async def _await_once(awaitable: Any) -> Any:  # noqa: ANN401
    """Await *awaitable* once and return its result."""
    return await awaitable


class TestAsyncAcquisitionCleanup:
    """A failed warning delivery after ``__anext__``/``__await__`` must not abandon what the acquisition created."""

    @pytest.mark.parametrize(
        "acquire", [pytest.param(_acquire_anext, id="anext"), pytest.param(_acquire_await, id="await")]
    )
    @pytest.mark.parametrize("raising_stream", [False, True])
    def test_failed_delivery_leaves_no_unawaited_coroutine(
        self, acquire: Callable[[Any], object], raising_stream: bool
    ) -> None:
        """Closing the acquired coroutine leaves no ``coroutine ... was never awaited`` warning behind.

        ``async for`` and ``await`` over a deprecated object fail on their first step when ``FutureWarning`` is an
        error or the logging sink raises. The coroutine the wrapped ``__anext__``/``__await__`` created never runs, so
        it must be closed rather than left for the garbage collector to report as a second, misleading warning.
        """
        stream = KeywordTypeErrorStream()
        proxy = make_warn_after_success_proxy(FlakyProtocolBox(), **({"stream": stream} if raising_stream else {}))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(TypeError if raising_stream else FutureWarning):
                acquire(proxy)
            gc.collect()
        assert [str(record.message) for record in caught if issubclass(record.category, RuntimeWarning)] == []

    def test_failed_delivery_keeps_async_generator_open(self) -> None:
        """A failed ``__anext__`` delivery over an async generator leaves every item for the next iteration.

        The awaitable an async generator hands out is a view of the generator itself; on CPython 3.13+ closing it
        finishes the generator, so a later ``async for`` would end silently with no items. The proxy must not close it.
        """
        source = legacy_tick_stream()
        proxy = make_warn_after_success_proxy(source)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(FutureWarning):
                _acquire_anext(proxy)
            gc.collect()
        assert asyncio.run(_collect(source)) == [1, 2]

    def test_failed_delivery_keeps_wrapped_coroutine_awaitable(self) -> None:
        """A failed ``await`` delivery over a wrapped coroutine object leaves that coroutine usable.

        When the deprecated object is itself a coroutine, its await iterator belongs to it; closing that iterator would
        close the wrapped coroutine, and awaiting it again would raise ``cannot reuse already awaited coroutine``.
        """
        source = legacy_snapshot()
        proxy = make_warn_after_success_proxy(source)
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(FutureWarning):
                _acquire_await(proxy)
        assert asyncio.run(source) == [1, 2]

    def test_failed_delivery_keeps_stored_coroutine_awaitable(self) -> None:
        """A failed ``await`` delivery leaves usable a coroutine that the awaitable stores and reuses.

        A custom awaitable creates its coroutine once and returns that coroutine's await iterator from every
        ``__await__``. The iterator has the same type as one created fresh, but closing it would close the stored
        coroutine, so the caller's retry would raise ``cannot reuse already awaited coroutine``.
        """
        box = PendingSnapshotBox()
        proxy = make_warn_after_success_proxy(box)
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(FutureWarning):
                _acquire_await(proxy)
        assert asyncio.run(_await_once(box)) == [1, 2]

    def test_failed_delivery_keeps_cached_step_awaitable(self) -> None:
        """A failed ``__anext__`` delivery leaves usable a coroutine that the iterator caches for its next step.

        A synchronous ``__anext__`` can return a coroutine the iterator keeps instead of creating one per step. That
        coroutine is a ``coroutine`` like a fresh one, so the proxy must see that the iterator still holds it.
        """
        source = CachedStepIterator()
        proxy = make_warn_after_success_proxy(source)
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(FutureWarning):
                _acquire_anext(proxy)
        assert asyncio.run(_await_once(source.__anext__())) == [1, 2]

    @pytest.mark.parametrize("silent_layers", [1, 2])
    def test_failed_delivery_through_stacked_proxy_keeps_wrapped_coroutine(self, silent_layers: int) -> None:
        """A failed ``await`` delivery on an outer proxy leaves usable the coroutine inner proxies wrap.

        The outer proxy acquires the await iterator from the next proxy down, not from the coroutine, so the owner is a
        proxy. With two inner layers that owner wraps another proxy, so the coroutine sits at the bottom of the stack;
        it is still the wrapped object and must not be closed.
        """
        source = legacy_snapshot()
        inner = make_silent_proxy_stack(source, silent_layers)
        proxy = make_warn_after_success_proxy(inner)
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(FutureWarning):
                _acquire_await(proxy)
        assert asyncio.run(_await_once(source)) == [1, 2]


class TestFailedDeliveryRestoresBudget:
    """A warning the ``stream`` failed to deliver does not spend the proxy's warn budget."""

    def test_raising_stream_retries_proxy_wide_slot(self) -> None:
        """After a stream fails on the first access, the next access delivers the one allowed warning.

        A logging sink that is not configured yet raises on its first call and works afterwards. With the default
        ``num_warns=1`` the migration notice must still reach that sink once it works, not be lost to the failure.
        """
        stream = FlakyTypeErrorStream()
        proxy = make_warn_after_success_proxy(FlakyProtocolBox(), stream=stream)
        with pytest.raises(TypeError, match="unsupported operand"):
            _ = proxy[0]
        _ = proxy[0]
        assert len(stream.calls) == 2

    def test_raising_stream_retries_per_attribute_slot(self) -> None:
        """A renamed attribute whose first warning failed warns again on its next read.

        ``attrs_mapping`` keeps one budget per deprecated name; a failed delivery for ``legacy_total`` must give that
        name's slot back so the rename is still announced once the sink works.
        """
        stream = FlakyTypeErrorStream()
        alias = make_flaky_attr_alias(make_flaky_attr_host(), stream=stream)
        with pytest.raises(TypeError, match="unsupported operand"):
            _ = alias.legacy_total
        _ = alias.legacy_total
        assert len(stream.calls) == 2

    def test_error_filter_failure_keeps_context_entry_warning(self) -> None:
        """A context entry that failed under an error filter warns on the next successful entry.

        A test run that treats ``FutureWarning`` as an error aborts the first ``with proxy`` (the acquired context is
        unwound); once the filter is lifted, the next entry must still report the deprecation.
        """
        resource = RecordingEntryContext()
        proxy = make_warn_after_success_proxy(resource)
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            with pytest.raises(FutureWarning):
                _drive_enter(proxy)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _drive_enter(proxy)
        assert _future_warning_count(caught) == 1


#: Budget-spending proxy operations raced across threads; each factory builds a fresh source that is safe to share.
_THREADED_BUDGET_OPS = [
    pytest.param(_drive_getitem, lambda: [1, 2], id="getitem"),
    pytest.param(_drive_enter, contextlib.nullcontext, id="enter"),
    pytest.param(_drive_next, itertools.count, id="next"),
]


class TestWarnBudgetThreadSafety:
    """The proxy's warn budget holds when many threads use the proxy at the same moment.

    Spending the budget is a check-then-act sequence: read the warned counter, compare it with ``num_warns``, then
    increment it. Without the proxy's lock, concurrent first uses could all read an unspent counter and each emit a
    warning. Mirrors ``TestWarnQuotaThreadSafety`` in ``tests/unittests/test__dispatch.py`` for the callable path.
    """

    @pytest.fixture
    def _aggressive_thread_switching(self) -> Iterator[None]:
        """Force frequent GIL hand-offs so the check-then-act window is actually exercised, then restore it."""
        previous = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        yield
        sys.setswitchinterval(previous)

    @pytest.mark.usefixtures("_aggressive_thread_switching")
    @pytest.mark.parametrize(("drive", "make_source"), _THREADED_BUDGET_OPS)
    def test_num_warns_one_emits_once_across_threads(
        self, drive: Callable[[Any], object], make_source: Callable[[], object]
    ) -> None:
        """16 threads released together onto a ``num_warns=1`` proxy deliver exactly one warning.

        A worker pool shares one deprecated object: every worker subscripts it, enters it as a context, or pulls the
        next value from it, all at once. The migration notice must reach the sink once, not once per thread that
        happened to arrive before the first delivery finished. A barrier releases all workers together and the sink
        pauses while delivering, so the threads contend on the budget at the same time. ``getitem`` and context entry
        warn after the operation succeeds and ``next`` warns before it advances; both orders share one budget.
        """
        stream = SlowRecordingStream()
        proxy = make_warn_after_success_proxy(make_source(), stream=stream)
        n_threads = 16
        barrier = threading.Barrier(n_threads)

        def worker(_ignored: int) -> None:
            """Block until every thread has arrived, then use the deprecated proxy simultaneously."""
            barrier.wait()
            drive(proxy)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as pool:
            list(pool.map(worker, range(n_threads)))
        assert len(stream.calls) == 1


#: Module-qualified path of :class:`tests.collection_targets.SourcePathTarget`, as a proxy template renders it.
_SOURCE_PATH_CLASS = "tests.collection_targets.SourcePathTarget"


class TestProxySourcePath:
    """``%(source_path)s`` in a proxy ``message_template`` renders the documented module-qualified path.

    The placeholder table documents ``source_path`` as the fully qualified path of the deprecated object.  Proxies
    used to render the bare name in its place, so a template written to show where the deprecated class lives lost the
    module.  Default templates never use the placeholder, so their text is unchanged.
    """

    def test_class_call_renders_module_path(self) -> None:
        """Instantiating a deprecated class renders ``module.ClassName``.

        A maintainer deprecates a whole class with a template that names its import path, so users can grep their
        code for it; constructing the class must show that path, not the bare class name.
        """
        with pytest.warns(FutureWarning) as record:
            DeprSourcePathClass()
        assert str(record[0].message) == f"{_SOURCE_PATH_CLASS} :: SourcePathTarget"

    def test_argument_renders_module_path(self) -> None:
        """A renamed constructor keyword renders the class path in its per-argument warning.

        A caller still passing the deprecated ``old`` keyword gets the argument warning; its ``source_path`` is the
        class path, the same as for the whole-class warning.
        """
        with pytest.warns(FutureWarning) as record:
            DeprSourcePathArgs(old=1)
        assert [str(w.message) for w in record] == [f"{_SOURCE_PATH_CLASS} :: SourcePathTarget"]

    @pytest.mark.parametrize(
        "attr", [pytest.param("old_attr", id="redirect"), pytest.param("gone_attr", id="warn-only")]
    )
    def test_attribute_renders_attribute_path(self, attr: str) -> None:
        """A deprecated ``attrs_mapping`` attribute renders ``module.ClassName.attr``.

        Selective attribute deprecation names the attribute in ``source_name``; its ``source_path`` extends the class
        path with the attribute, for a redirected and for a warn-only (``None``-mapped) entry alike.
        """
        with pytest.warns(FutureWarning) as record:
            getattr(DeprSourcePathAttrs, attr)
        assert str(record[0].message) == f"{_SOURCE_PATH_CLASS}.{attr} :: {attr}"

    def test_instance_renders_name(self) -> None:
        """An instance proxy renders its ``name``: an instance has no module of its own.

        ``deprecated_instance`` wraps an object such as a module-level config value; the only stable path it has is
        the name the maintainer gave it.
        """
        with pytest.warns(FutureWarning) as record:
            _ = depr_source_path_instance.new
        assert str(record[0].message) == "source_path_target :: source_path_target"

    def test_static_message_renders_module_path(self) -> None:
        """The PEP 702 ``__deprecated__`` string renders the same path as the runtime warning.

        Type checkers and IDEs show ``__deprecated__`` without running the code; it must agree with what the warning
        says at runtime.
        """
        assert DeprSourcePathClass.__deprecated__ == f"{_SOURCE_PATH_CLASS} :: SourcePathTarget"

    def test_stacked_renders_wrapped_module_path(self) -> None:
        """An outer proxy stacked over another renders the path of the class both layers wrap.

        Stacking a second ``deprecated_class`` over an existing proxy must not degrade the outer warning to the bare
        name: the outer layer names the same class, so it shows the same module-qualified path.
        """
        with pytest.warns(FutureWarning) as record:
            DeprSourcePathStacked()
        assert {str(w.message) for w in record} == {f"{_SOURCE_PATH_CLASS} :: SourcePathTarget"}
