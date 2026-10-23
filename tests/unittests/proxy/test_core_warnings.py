"""Unit tests for proxy warning rendering, delivery, timing, and budgets."""

import concurrent.futures
import sys
import threading
import warnings
from collections import defaultdict
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from deprecate import DeprecatedError
from deprecate.proxy import deprecated_class, deprecated_instance
from deprecate.proxy.core import _DeprecatedProxy, _ProxySpec
from tests.collection_deprecate import MappedColorEnum
from tests.collection_deprecate_forwarding import ProxyArgsRemapForArgWarnMessage
from tests.collection_deprecate_regressions import (
    DeprSourcePathArgs,
    DeprSourcePathAttrs,
    DeprSourcePathClass,
    DeprSourcePathStacked,
    depr_source_path_instance,
    make_flaky_attr_alias,
    make_skip_gated_target_proxy,
    make_warn_after_success_proxy,
)
from tests.collection_targets import (
    ColorEnum,
    CountingSkipCondition,
    DecliningInplaceOperand,
    FlakyProtocolBox,
    FlakyTypeErrorStream,
    KeywordTypeErrorStream,
    MessageOnlyRecordingStream,
    RecordingEntryContext,
    RecordingStream,
    SlowRecordingStream,
    make_flaky_attr_host,
)
from tests.unittests.proxy._helpers import (
    _GATED_PROXY_OPS,
    _PURE_PROXY_OPS,
    _SIDE_EFFECTING_PROXY_OPS,
    _SINGLE_HOOK_PROXY_OPS,
    _SOURCE_PATH_CLASS,
    _THREADED_BUDGET_OPS,
    _drive_enter,
    _future_warning_count,
    _P8Source,
    _P8Target,
)


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
        assert caught[0].filename.endswith("test_core_warnings.py")

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
        assert future_warnings[0].filename.endswith("test_core_warnings.py")

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
        assert future_warnings[0].filename.endswith("test_core_warnings.py")


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
