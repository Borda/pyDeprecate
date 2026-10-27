"""Unit tests for proxy calls, argument forwarding, and descriptor binding."""

import contextlib
import copy
import inspect
import pickle
import warnings
from collections.abc import Callable
from types import MethodType
from typing import Any, cast

import pytest

from deprecate import DeprecatedError
from deprecate._types import TargetMode
from deprecate.proxy import deprecated_class, deprecated_instance
from deprecate.proxy.core import _DeprecatedProxy, _proxy_call_global, _ProxySpec
from tests._helpers import captured_warnings
from tests.collections.api_current import ColorEnum, NewDataClass, SomeTargetClass, WithInjected, base_sum_kwargs
from tests.collections.api_deprecated import (
    _DEPRS_CASE_STD_ARGS,
    MappedColorEnum,
    MappedDataClass,
    MappedDropArgDataClass,
)
from tests.collections.api_deprecated_forwarding import (
    ProxyArgsMappedToEmpty,
    ProxyArgsRemapAuto,
    ProxyArgsRemapForArgWarnMessage,
    ProxyCallableWithArgsMapping,
    ProxyClassWithArgsExtra,
)
from tests.collections.api_deprecated_regressions import (
    make_deprecated_class_attrs_skip_if_true,
    make_deprecated_class_skip_if_flag,
    make_deprecated_class_skip_if_non_bool,
    make_deprecated_class_skip_if_true,
    make_deprecated_data_host,
    make_deprecated_instance_skip_if_true_read_only,
    make_deprecated_method_host,
    make_deprecated_partial_host,
    make_read_only_settings_proxy,
)
from tests.unittests.proxy._helpers import _future_warning_count


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
                    **_DEPRS_CASE_STD_ARGS,
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
        with captured_warnings() as repeated:
            _proxy_call_global(proxy, proxy._dep, (8,), kwargs)
        assert not repeated

    def test_warning_failure_precedes_invocation(self) -> None:
        """Deliver a fatal warning before entering the source callable.

        Calling an iterator's next method would consume its first item, so warning failure must leave that item
        available for a later retry. This exercises a real side effect without mocking the call machinery.
        """
        source = iter([11, 22])
        proxy = _DeprecatedProxy(obj=source.__next__, spec=_ProxySpec(name="legacy_next", **_DEPRS_CASE_STD_ARGS))
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
        proxy = _DeprecatedProxy(obj=base_sum_kwargs, spec=_ProxySpec(name="legacy_sum", **_DEPRS_CASE_STD_ARGS))
        with pytest.warns(FutureWarning), pytest.raises(TypeError, match="unexpected keyword argument 'unknown'"):
            _proxy_call_global(proxy, proxy._dep, (), {"unknown": 1})
        assert proxy._cfg.state.warned_calls == 1


class TestArgsMapping:
    """args_mapping remaps or drops kwargs when the proxy is called."""

    @pytest.mark.parametrize(
        ("kwargs", "expected_label", "expected_total", "extra_warning"),
        [
            pytest.param({"name": "hello", "total": 7}, "hello", 7, contextlib.nullcontext, id="new-total-kept"),
            pytest.param(
                {"name": "world", "count": 3},
                "world",
                3,
                lambda: pytest.warns(FutureWarning, match=r"`MappedDataClass` uses deprecated arguments: `count`"),
                id="old-count-remapped",
            ),
        ],
    )
    def test_remap_kwargs(
        self,
        kwargs: dict[str, object],
        expected_label: str,
        expected_total: int,
        extra_warning: Callable[[], contextlib.AbstractContextManager[object]],
    ) -> None:
        """DeprecationProxy dataclass calls should remap renamed kwargs and preserve explicit non-remapped kwargs.

        When an old kwarg name is passed (e.g. ``name`` mapped to ``label``), the proxy emits the per-argument
        deprecation template (``old -> new``) — matching the decorator's argument-deprecation form. A caller that
        also passes the old ``count`` name gets a second per-argument warning for ``count -> total``, while a caller
        already using ``total`` gets none.

        """
        with (
            pytest.warns(FutureWarning, match=r"`MappedDataClass` uses deprecated arguments: `name` -> `label`"),
            extra_warning(),
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
        with (
            pytest.warns(
                FutureWarning, match=r"`MappedDropArgDataClass` uses deprecated arguments: `legacy_flag` -> `None`"
            ),
            pytest.warns(FutureWarning, match=r"`MappedDropArgDataClass` uses deprecated arguments: `name`"),
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
        with captured_warnings() as caught:
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
        with pytest.warns(FutureWarning), pytest.warns(UserWarning, match=r"`old_key` is ignored"):
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
        with pytest.warns(FutureWarning), pytest.warns(UserWarning, match=r"`old_key` is ignored"):
            instance = ProxyArgsRemapForArgWarnMessage(new_key=6, old_key=5)  # type: ignore[call-arg]
        assert instance.new_key == 6

    def test_args_remap_user_warning_emitted_when_both_old_and_new_provided(self) -> None:
        """ARGS_REMAP proxy: UserWarning fires naming the ignored argument when both old and new kwargs are passed.

        Calling ``ProxyArgsRemapForArgWarnMessage(old_key=5, new_key=6)`` must emit a ``UserWarning`` whose
        message identifies ``old_key`` as the ignored argument, in addition to the normal ``FutureWarning``.
        This test verifies parity between the ``@deprecated`` decorator path (which already emitted ``UserWarning``)
        and the ``deprecated_class`` proxy path after the proxy-path collision guard was added.

        """
        with pytest.warns(FutureWarning), pytest.warns(UserWarning, match=r"`old_key`.*is ignored"):
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


class TestProxyArgsMappingBehavior:
    """Conditional warning behavior when args_mapping is provided on a proxy."""

    def test_auto_args_remap_warns_on_old_arg(self) -> None:
        """Proxy with args_mapping and no explicit target warns when old arg name is used."""
        with pytest.warns(FutureWarning):
            ProxyArgsRemapAuto(old_key=1)  # type: ignore[call-arg]

    def test_auto_args_remap_silent_on_new_arg(self) -> None:
        """Proxy with args_mapping and no explicit target does NOT warn when new arg name is used."""
        with captured_warnings() as caught:
            ProxyArgsRemapAuto(new_key=1)
        assert not caught

    def test_empty_string_mapping_keeps_old_name_and_warns(self) -> None:
        """A proxy with ``args_mapping={"new_key": ""}`` forwards ``new_key`` unchanged and still warns.

        The caller passes a keyword whose mapping says "keep this name"; the constructor must receive the value under
        the original name (not fall back to its default) while the deprecated class still emits its warning.
        """
        with pytest.warns(FutureWarning):
            instance = ProxyArgsMappedToEmpty(new_key=3)
        assert instance.new_key == 3

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
        with pytest.warns(FutureWarning), pytest.warns(UserWarning, match=r"`old_key` is ignored"):
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
        with pytest.warns(FutureWarning), pytest.warns(UserWarning, match=r"`old_key` is ignored"):
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
        with captured_warnings() as caught:

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
        with captured_warnings() as caught:
            proxy(old_key=2)
        assert not caught


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

        with captured_warnings() as caught:
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

        with captured_warnings() as caught:
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
