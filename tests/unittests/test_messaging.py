"""Unit tests for warning templates and emitters (:mod:`deprecate.messaging`)."""

from __future__ import annotations

import functools
import gc
import inspect
import logging
import warnings
import weakref
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest

from deprecate import TargetMode, assert_no_warnings, deprecated
from deprecate._types import DeprecationConfig, _WrapperState
from deprecate.messaging import (
    _STREAM_DECISIONS,
    _callable_display_name,
    _consume_warn_budget,
    _emit,
    _eval_skip_if,
    _raise_warn,
    _raise_warn_arguments,
    _raise_warn_callable,
    _release_warn_budget,
    _render_static_deprecation_message,
    _resolve_message_template_alias,
    _stream_accepts_stacklevel,
    _validate_message_template,
)
from deprecate.utils import _partial_chain
from tests.collection_deprecate import make_sum_deprecation_with_stream
from tests.collection_targets import (
    CallableAdder,
    EqualMessageOnlyStream,
    EqualStacklevelStream,
    FlakyTypeErrorStream,
    KeywordTypeErrorStream,
    MessageNamedStacklevelRecordingStream,
    MessageOnlyRecordingStream,
    NewCls,
    NonStringNameCallable,
    PassThroughMessageOnlyStream,
    RecordingStream,
    RuntimeSignatureStream,
    SlottedStream,
    UnhashableStream,
    UnintrospectableStream,
    base_sum_kwargs,
    callable_adder_target,
    make_stream_cycle,
    moduleless_function,
    nested_partial_target,
    partial_target,
    stream_keyword_only_stacklevel,
    stream_message_named_stacklevel,
    stream_message_only,
    stream_passthrough_message_only,
    stream_passthrough_of_narrow_wraps,
    stream_passthrough_of_passthrough,
    stream_passthrough_warn,
    stream_passthrough_with_stacklevel,
    stream_positional_only_message,
    stream_positional_only_stacklevel,
    stream_var_keyword,
    stream_var_positional_keyword_only_stacklevel,
    stream_var_positional_named_stacklevel,
    stream_with_stacklevel,
    stream_wraps_logging_warning,
)


class TestWarnBudgetRelease:
    """``_release_warn_budget`` gives back exactly the slot ``_consume_warn_budget`` claimed."""

    @pytest.mark.parametrize(
        ("reason_callable", "reason_argument"),
        [pytest.param(True, None, id="callable"), pytest.param(False, {"old": None}, id="argument")],
    )
    def test_released_slot_warns_again(
        self, reason_callable: bool, reason_argument: dict[str, str | None] | None
    ) -> None:
        """A released slot can be claimed again by the next use.

        When the ``stream`` raises (a warning filter set to ``"error"``, a failing logging sink) the caller never
        saw the notice, so the proxy returns the slot and the next access must still warn, for the proxy-wide
        counter and for a per-argument key alike.
        """
        state = _WrapperState()
        with state.lock:
            first = _consume_warn_budget(state, 1, reason_callable=reason_callable, reason_argument=reason_argument)
            _release_warn_budget(state, 1, reason_callable=reason_callable, reason_argument=reason_argument)
            again = _consume_warn_budget(state, 1, reason_callable=reason_callable, reason_argument=reason_argument)
            spent = _consume_warn_budget(state, 1, reason_callable=reason_callable, reason_argument=reason_argument)
        assert (first, again, spent) == (True, True, False)

    def test_unlimited_budget_untouched(self) -> None:
        """With ``num_warns=-1`` there is no slot to return, so the counter keeps its value.

        An unlimited budget never blocks a warning; giving a slot back must not drive the counter below what was
        actually emitted.
        """
        state = _WrapperState()
        with state.lock:
            _consume_warn_budget(state, -1, reason_callable=True)
            _release_warn_budget(state, -1, reason_callable=True)
        assert state.warned_calls == 1


class TestCallableDisplayName:
    """``_callable_display_name`` renders ``(name, dotted_path)`` for any target object without raising."""

    @pytest.mark.parametrize(
        ("obj", "expected"),
        [
            pytest.param(
                base_sum_kwargs,
                ("base_sum_kwargs", "tests.collection_targets.base_sum_kwargs"),
                id="function",
            ),
            pytest.param(NewCls.__init__, ("__init__", "tests.collection_targets.__init__"), id="method"),
            pytest.param(NewCls, ("NewCls", "tests.collection_targets.NewCls"), id="class"),
            pytest.param(partial_target, ("new_scale", "tests.collection_targets.new_scale"), id="partial"),
            pytest.param(
                nested_partial_target,
                ("new_scale", "tests.collection_targets.new_scale"),
                id="nested-partial",
            ),
            pytest.param(
                callable_adder_target,
                ("CallableAdder", "tests.collection_targets.CallableAdder"),
                id="callable-instance",
            ),
            pytest.param(
                NonStringNameCallable(),
                ("NonStringNameCallable", "tests.collection_targets.NonStringNameCallable"),
                id="non-string-name",
            ),
            pytest.param(max, ("max", "builtins.max"), id="builtin"),
            pytest.param(moduleless_function, ("moduleless_function", "moduleless_function"), id="module-none"),
            pytest.param(object(), ("object", "builtins.object"), id="plain-object"),
        ],
    )
    def test_renders_name_and_path(self, obj: object, expected: tuple[str, str]) -> None:
        """Each target kind maps to the name and dotted path the deprecation warning shows the caller.

        Functions, classes and builtins keep the ``module.name`` form every earlier release produced; partials are
        unwrapped to the function they wrap, objects without a string ``__name__`` are named by their type, and a
        missing module drops the ``None.`` prefix instead of rendering it.

        """
        assert _callable_display_name(obj) == expected

    def test_instance_and_class_render_alike(self) -> None:
        """A callable instance renders exactly like its class, so the warning names the type a caller can import.

        Users read the path to find the replacement; ``CallableAdder`` is importable, the instance is not.

        """
        assert _callable_display_name(CallableAdder(offset=3)) == _callable_display_name(CallableAdder)

    def test_nested_partial_fixture_is_two_layers(self) -> None:
        """The nested-partial fixture really is a two-layer chain, so the nested rows test unwrapping at all.

        CPython flattens ``partial(partial(f))`` unless the inner layer carries attributes; if that ever changed,
        the nested cases would silently degrade to single-layer ones.

        """
        assert len(_partial_chain(nested_partial_target)) == 2


class TestResolveMessageTemplateAlias:
    """``_resolve_message_template_alias`` folds the deprecated ``template_mgs`` alias into ``message_template``."""

    def test_passthrough_when_alias_absent(self) -> None:
        """With no legacy ``template_mgs`` supplied, the canonical ``message_template`` value passes through silently.

        The common case: callers use the current ``message_template`` name. The resolver must return that value
        unchanged and emit no warning, so the alias machinery is invisible to code already on the new name.

        """
        with assert_no_warnings():
            resolved = _resolve_message_template_alias("keep %(source_name)s", None)
        assert resolved == "keep %(source_name)s"

    def test_alias_used_and_warns(self) -> None:
        """Supplying only the legacy ``template_mgs`` returns its value and emits a ``FutureWarning``.

        A caller still on the old, typo'd name (``mgs`` for ``msg``) must keep working during the deprecation
        window: the resolver adopts their template as ``message_template`` and fires a ``FutureWarning`` naming
        the rename so they migrate before the alias is removed in v1.0.

        """
        with pytest.warns(FutureWarning, match="`template_mgs` is deprecated"):
            resolved = _resolve_message_template_alias(None, "legacy %(remove_in)s")
        assert resolved == "legacy %(remove_in)s"

    def test_both_supplied_raises(self) -> None:
        """Passing both the canonical name and the legacy alias raises ``TypeError`` — no silent merge.

        There is no meaningful way to combine two message templates, so a caller who supplies both
        ``message_template`` and ``template_mgs`` has made a mistake that must fail loudly rather than pick one
        arbitrarily.

        """
        with pytest.raises(TypeError, match="pass only one"):
            _resolve_message_template_alias("a", "b")


class TestRaiseWarn:
    """Tests for _raise_warn — low-level helper that formats a template string and calls the stream."""

    def test_calls_stream_with_formatted_message(self) -> None:
        """Stream is called exactly once with the template variables substituted correctly."""
        stream = MagicMock()

        def old_func() -> None:
            pass

        _raise_warn(stream, old_func, "%(source_name)s deprecated since %(version)s", version="1.0")
        stream.assert_called_once()
        assert "old_func" in stream.call_args[0][0]
        assert "1.0" in stream.call_args[0][0]

    def test_extracts_class_name_from_init(self) -> None:
        """When the source callable is __init__, the enclosing class name is used as source_name."""
        stream = MagicMock()

        class MyClass:
            def __init__(self) -> None:
                pass

        _raise_warn(stream, MyClass.__init__, "%(source_name)s")
        called_msg = stream.call_args[0][0]
        assert "MyClass" in called_msg

    def test_source_path_contains_module_and_name(self) -> None:
        """The %(source_path)s placeholder is substituted with a dotted module.name string."""
        stream = MagicMock()

        def my_func() -> None:
            pass

        _raise_warn(stream, my_func, "%(source_path)s")
        called_msg = stream.call_args[0][0]
        assert "my_func" in called_msg


class TestRaiseWarnCallable:
    """Tests for _raise_warn_callable — warning variant for deprecated callables forwarding to a replacement."""

    def test_callable_target_uses_default_template(self) -> None:
        """When a replacement target is provided, both old and new names appear in the default message."""
        stream = MagicMock()

        def old_func() -> None:
            pass

        def new_func() -> None:
            pass

        _raise_warn_callable(stream, old_func, new_func, "1.0", "2.0")
        msg = stream.call_args[0][0]
        assert "old_func" in msg
        assert "new_func" in msg
        assert "1.0" in msg
        assert "2.0" in msg

    def test_none_target_uses_no_target_template(self) -> None:
        """When target=None, the no-target template is used and no replacement name appears."""
        stream = MagicMock()

        def old_func() -> None:
            pass

        _raise_warn_callable(stream, old_func, None, "1.0", "2.0")
        msg = stream.call_args[0][0]
        assert "old_func" in msg
        assert "new_func" not in msg

    def test_custom_template_overrides_default(self) -> None:
        """A custom message_template overrides both built-in templates and receives the same substitutions."""
        stream = MagicMock()

        def old_func() -> None:
            pass

        _raise_warn_callable(stream, old_func, None, "1.0", "2.0", message_template="custom: %(source_name)s")
        assert stream.call_args[0][0] == "custom: old_func"


class TestRaiseWarnArguments:
    """Tests for _raise_warn_arguments — warning variant for deprecated argument renames."""

    def test_formats_argument_mapping(self) -> None:
        """Function name and both old and new argument names appear in the formatted message."""
        stream = MagicMock()

        def my_func(old_arg: int = 1, new_arg: int = 1) -> None:
            pass

        _raise_warn_arguments(stream, my_func, {"old_arg": "new_arg"}, "1.0", "2.0")
        msg = stream.call_args[0][0]
        assert "my_func" in msg
        assert "old_arg" in msg
        assert "new_arg" in msg

    def test_multiple_argument_mappings(self) -> None:
        """All renamed argument pairs appear in the message when multiple mappings are provided."""
        stream = MagicMock()

        def my_func(a: int = 0, b: int = 0, x: int = 0, y: int = 0) -> None:
            pass

        _raise_warn_arguments(stream, my_func, {"a": "x", "b": "y"}, "1.0", "2.0")
        msg = stream.call_args[0][0]
        assert "a" in msg
        assert "x" in msg
        assert "b" in msg
        assert "y" in msg

    def test_custom_template_overrides_default(self) -> None:
        """A custom message_template overrides the default argument-rename template."""
        stream = MagicMock()

        def my_func(old: int = 0, new: int = 0) -> None:
            pass

        _raise_warn_arguments(stream, my_func, {"old": "new"}, "1.0", "2.0", message_template="map: %(argument_map)s")
        assert stream.call_args[0][0].startswith("map: ")


class TestTemplateMgsValidation:
    """A malformed ``message_template`` is detected at decoration time, not at first call (B6)."""

    def test_unknown_placeholder_raises_at_decoration(self) -> None:
        """An unknown ``%(...)s`` key raises ``ValueError`` when ``@deprecated`` is applied — before any call."""
        with pytest.raises(ValueError, match="Invalid message_template"):
            deprecated(
                target=base_sum_kwargs, deprecated_in="0.8", remove_in="1.0", message_template="bad %(unknown_key)s"
            )(base_sum_kwargs)

    def test_valid_template_accepted_at_decoration(self) -> None:
        """A template using only documented placeholders is accepted at decoration time."""
        # Must not raise — covers happy path of the probe.
        wrapper = deprecated(
            target=base_sum_kwargs,
            deprecated_in="0.8",
            remove_in="1.0",
            message_template="`%(source_name)s` -> `%(target_name)s` since v%(deprecated_in)s",
        )(base_sum_kwargs)
        assert callable(wrapper)

    @pytest.mark.parametrize(
        ("target", "message_template", "expected"),
        [
            pytest.param(TargetMode.NOTIFY, "%(target_path)s|%(argument_map)s", "|", id="notify"),
            pytest.param(base_sum_kwargs, "%(argument_map)s", "", id="callable"),
            pytest.param(TargetMode.ARGS_REMAP, "%(target_path)s", "", id="args-remap"),
        ],
    )
    def test_every_valid_placeholder_renders_for_each_target_shape(
        self, target: Callable[..., Any] | TargetMode, message_template: str, expected: str
    ) -> None:
        """Render valid placeholders even when the selected target mode cannot populate them.

        Custom templates are validated independently of target mode, so decoration must use empty values for
        inapplicable placeholders instead of accepting the template and then failing with ``KeyError``.
        """
        config = DeprecationConfig(
            name="old_api",
            target=target,
            args_mapping={"old": "new"} if target is TargetMode.ARGS_REMAP else None,
            deprecated_in="1.0",
            remove_in="2.0",
            message_template=message_template,
        )

        assert _render_static_deprecation_message(config, "old_api", "pkg.old_api") == expected


class TestRaiseWarnStacklevel:
    """Stream called with ``stacklevel`` when accepted; internal TypeError propagates without double-call."""

    def test_internal_typeerror_not_swallowed_no_double_call(self) -> None:
        """An internal TypeError from a stacklevel-accepting stream propagates and the stream runs exactly once.

        A ``TypeError`` raised *inside* a stacklevel-accepting stream must propagate and the stream must run
        exactly once. A naive ``try/except TypeError`` would re-invoke the stream on any TypeError, producing
        duplicate side effects (double log lines) for anyone whose custom stream raised internally.  Deciding from
        the stream's signature, before the call, leaves nothing to retry.
        """
        calls: list[str] = []

        def stream(msg: str, stacklevel: int = 1) -> None:
            calls.append(msg)
            raise TypeError("boom from inside the stream")

        def old() -> None:
            pass

        with pytest.raises(TypeError, match="boom from inside the stream"):
            _raise_warn(stream, old, "%(source_name)s")
        assert len(calls) == 1

    def test_varkw_stream_receives_stacklevel_exactly_once(self) -> None:
        """A ``**kwargs``-accepting stream receives ``stacklevel`` and is called exactly once.

        A custom stream declared as ``def my_stream(msg, **kwargs)`` accepts ``stacklevel`` via ``**kwargs``.
        The caller must forward ``stacklevel`` in a single call — never via a fallback retry.
        """
        calls: list[tuple[str, dict[str, object]]] = []

        def stream(msg: str, **kwargs: object) -> None:
            calls.append((msg, kwargs))

        def src() -> None:
            pass

        _raise_warn(stream, src, "%(source_name)s")
        assert len(calls) == 1
        assert "stacklevel" in calls[0][1]

    @pytest.mark.parametrize(
        "as_partial", [pytest.param(False, id="adapter"), pytest.param(True, id="partial-of-adapter")]
    )
    def test_passthrough_stream_delivers_through_decorator(self, as_partial: bool) -> None:
        """A ``functools.wraps``-style pass-through around a message-only sink works as ``@deprecated(stream=...)``.

        A team wraps its message-only logging sink in a bare ``(*args, **kwargs)`` adapter and plugs the adapter in as
        ``stream=``, directly or through a ``functools.partial``. Calling the deprecated function must deliver the
        message once and still forward the call; it must not fail on every call with "unexpected keyword argument
        'stacklevel'" because the adapter's ``**kwargs`` looked as if it accepted the keyword.
        """
        stream = PassThroughMessageOnlyStream()
        deprecated_sum = make_sum_deprecation_with_stream(functools.partial(stream) if as_partial else stream)
        result = deprecated_sum(1, 2)
        assert (result, stream.calls) == (
            3,
            [
                "The `original_sum` was deprecated since v1.0 in favor of `tests.collection_targets.base_sum_kwargs`."
                " It will be removed in v2.0."
            ],
        )

    def test_keyword_typeerror_from_stream_propagates_after_one_call(self) -> None:
        """A stream's own ``TypeError`` mentioning "keyword" propagates after a single invocation.

        A logging handler used as ``stream=`` has a bug that raises ``TypeError("unexpected keyword in handler")``.
        The text looks like a rejected ``stacklevel`` keyword, and sniffing it used to trigger a second call — the
        handler logged twice and still failed.  The decorator's emitter must call it once and let the error through.
        """
        stream = KeywordTypeErrorStream()
        with pytest.raises(TypeError, match="unexpected keyword in handler"):
            _raise_warn(stream, base_sum_kwargs, "%(source_name)s")
        assert len(stream.calls) == 1


class TestStreamAcceptsStacklevel:
    """``_stream_accepts_stacklevel`` decides once, from the signature, whether a stream gets ``stacklevel=``."""

    @pytest.mark.parametrize(
        ("stream", "expected"),
        [
            pytest.param(warnings.warn, True, id="warnings-warn"),
            pytest.param(functools.partial(warnings.warn, category=FutureWarning), True, id="partial-of-warn"),
            pytest.param(
                functools.partial(functools.partial(warnings.warn, category=FutureWarning), stacklevel=2),
                True,
                id="nested-partial-of-warn",
            ),
            pytest.param(logging.warning, True, id="logging-warning"),
            pytest.param(print, False, id="print"),
            pytest.param(stream_message_only, False, id="message-only"),
            pytest.param(stream_wraps_logging_warning, False, id="wraps-of-logging-warning"),
            pytest.param(stream_with_stacklevel, True, id="stacklevel-param"),
            pytest.param(stream_positional_only_message, True, id="positional-only-message"),
            pytest.param(stream_positional_only_stacklevel, False, id="positional-only-stacklevel"),
            pytest.param(stream_keyword_only_stacklevel, True, id="keyword-only-stacklevel"),
            pytest.param(stream_var_keyword, True, id="var-keyword"),
            pytest.param(stream_var_positional_named_stacklevel, False, id="var-positional-named-stacklevel"),
            pytest.param(stream_message_named_stacklevel, False, id="message-bound-stacklevel-name"),
            pytest.param(
                stream_var_positional_keyword_only_stacklevel, True, id="var-positional-keyword-only-stacklevel"
            ),
            pytest.param(stream_passthrough_message_only, False, id="passthrough-of-message-only"),
            pytest.param(stream_passthrough_with_stacklevel, True, id="passthrough-of-stacklevel-stream"),
            pytest.param(stream_passthrough_of_passthrough, False, id="passthrough-of-passthrough"),
            pytest.param(stream_passthrough_of_narrow_wraps, False, id="passthrough-of-narrow-wraps-logging"),
            pytest.param(stream_passthrough_warn, True, id="passthrough-of-warn"),
            pytest.param(make_stream_cycle(), False, id="wrapped-cycle"),
            pytest.param(functools.partial(stream_with_stacklevel), True, id="partial-of-python-stream"),
            pytest.param(functools.partial(stream_message_only), False, id="partial-of-message-only"),
            pytest.param(
                functools.partial(stream_passthrough_message_only), False, id="partial-of-passthrough-of-message-only"
            ),
            pytest.param(
                functools.partial(stream_passthrough_with_stacklevel), True, id="partial-of-passthrough-of-stacklevel"
            ),
            pytest.param(
                functools.partial(stream_passthrough_with_stacklevel, "fixed"),
                False,
                id="partial-binding-message-of-passthrough",
            ),
            pytest.param(Mock(), True, id="mock"),
            pytest.param(UnintrospectableStream(), False, id="signature-raises"),
        ],
    )
    def test_decision_table(self, stream: Callable[..., Any], expected: bool) -> None:
        """Each supported stream shape is classified by whether it binds ``stream(msg, stacklevel=...)``.

        Users pass ``warnings.warn``, partials of it, ``logging.warning``, ``print``, mocks and hand-written
        callables as ``stream=``. Only a stream whose signature binds the positional message *and* a ``stacklevel``
        keyword may receive ``stacklevel=`` — a lone ``def sink(stacklevel)`` fails the second half, because the
        message already filled that parameter; ``warnings.warn`` and its partials are recognised by identity so the
        answer does not hinge on builtin signature support, and an unintrospectable stream is never sent the keyword.
        A stream decorated with ``functools.wraps`` is judged by its own parameters, not by the ``__wrapped__``
        callable whose metadata it copied, because the stream itself is what gets called — unless those parameters
        are just ``(*args, **kwargs)``: such a pass-through says nothing about what it forwards, so the wrapped
        callable decides (layer by layer, stopping at the first layer that declares parameters of its own). A
        ``__wrapped__`` loop cannot be resolved, so it is treated like unreadable metadata.
        """
        assert _stream_accepts_stacklevel(stream) is expected

    def test_decision_memoised_per_stream(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A hashable stream's signature is inspected only on the first emission, not on every warning.

        A wrapper with ``num_warns=-1`` warns on every call, so re-inspecting its stream each time would add
        signature-introspection cost to every deprecated call. The decision is cached per stream object.
        """
        spy = MagicMock(wraps=inspect.signature)
        monkeypatch.setattr(inspect, "signature", spy)
        stream = RecordingStream()
        decisions = (_stream_accepts_stacklevel(stream), _stream_accepts_stacklevel(stream))
        assert decisions == (True, True)
        assert spy.call_count == 1

    def test_unhashable_stream_memoised_by_identity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unhashable stream is inspected once, because the memo is keyed by identity and never hashes it.

        A user-written callable class that defines ``__eq__`` without ``__hash__`` cannot be a hash-table key. The
        decision must still be correct and, like any other stream's, made only on the first emission.
        """
        spy = MagicMock(wraps=inspect.signature)
        monkeypatch.setattr(inspect, "signature", spy)
        stream = UnhashableStream()
        decisions = (_stream_accepts_stacklevel(stream), _stream_accepts_stacklevel(stream))
        assert decisions == (True, True)
        assert spy.call_count == 1

    def test_equal_streams_with_different_signatures_decided_separately(self) -> None:
        """Two streams that compare and hash equal still each get the decision their own signature earns.

        A value-object style callable class makes every instance ``==`` to the others; a hand-written message-only
        sink and a ``stacklevel``-capable sink built from such classes must not share the first decision made, or the
        second would be called with a keyword it rejects and the deprecation message would never be delivered.
        """
        message_only, with_stacklevel = EqualMessageOnlyStream(), EqualStacklevelStream()
        assert message_only == with_stacklevel
        assert hash(message_only) == hash(with_stacklevel)
        decisions = (_stream_accepts_stacklevel(message_only), _stream_accepts_stacklevel(with_stacklevel))
        assert decisions == (False, True)

    def test_memo_does_not_keep_stream_alive(self) -> None:
        """Dropping the last outside reference to a stream frees it and its memo entry.

        A long-lived process that builds streams dynamically — one handler per request, say — must not accumulate
        them through the decision memo; the entry goes away with the stream it describes.
        """
        stream = RecordingStream()
        _stream_accepts_stacklevel(stream)
        key, ref = id(stream), weakref.ref(stream)
        del stream
        gc.collect()
        assert (ref(), key in _STREAM_DECISIONS) == (None, False)

    def test_recycled_id_does_not_borrow_another_streams_decision(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A memo entry whose weak reference resolves to a different object is ignored and replaced.

        After a stream is garbage-collected its id can be handed to a new object. Simulate the stale entry such a
        collision would leave behind: the new stream must be classified from its own signature, not from the entry
        recorded for the object that used to live at that address.
        """
        stream, impostor = RecordingStream(), MessageOnlyRecordingStream()
        monkeypatch.setitem(_STREAM_DECISIONS, id(stream), (weakref.ref(impostor), False))
        assert _stream_accepts_stacklevel(stream) is True

    def test_stream_without_weakref_support_inspected_every_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A stream that cannot be weakly referenced is classified correctly, just without memoisation.

        An instance of a ``__slots__`` class has no ``__weakref__`` slot, so no weak reference can anchor a memo
        entry to it. It must be re-inspected on each emission rather than rejected or pinned strongly.
        """
        spy = MagicMock(wraps=inspect.signature)
        monkeypatch.setattr(inspect, "signature", spy)
        stream = SlottedStream()
        decisions = (_stream_accepts_stacklevel(stream), _stream_accepts_stacklevel(stream))
        assert decisions == (True, True)
        assert spy.call_count == 2


class TestEmit:
    """``_emit`` writes the message to the stream exactly once and never catches what the stream raises."""

    def test_stacklevel_forwarded_when_accepted(self) -> None:
        """A stream with a ``stacklevel`` parameter receives the message and the measured ``stacklevel`` in one call.

        A custom sink that forwards ``stacklevel`` to :func:`warnings.warn` needs the exact value so the warning
        lands on the user's line.  Called straight from this test, ``_emit`` measures ``2``: its own frame, then
        this external test frame.
        """
        stream = RecordingStream()
        _emit(stream, "old is deprecated")
        assert stream.calls == [("old is deprecated", {"stacklevel": 2})]

    @pytest.mark.parametrize(
        ("stream_cls", "expected_call"),
        [
            pytest.param(MessageOnlyRecordingStream, "old is deprecated", id="message-only"),
            pytest.param(
                MessageNamedStacklevelRecordingStream, "old is deprecated", id="message-bound-stacklevel-name"
            ),
            pytest.param(PassThroughMessageOnlyStream, "old is deprecated", id="passthrough-of-message-only"),
            pytest.param(UnintrospectableStream, ("old is deprecated", {}), id="signature-raises"),
        ],
    )
    def test_message_only_when_stacklevel_not_accepted(self, stream_cls: type, expected_call: object) -> None:
        """A stream that cannot (or cannot be shown to) take ``stacklevel`` receives just the message, once.

        A minimal sink without a ``stacklevel`` parameter, a sink whose only parameter is *named* ``stacklevel`` (the
        message lands in it, so passing the keyword too would raise "multiple values"), or a callable whose
        signature cannot be read, must be called a single time with the message alone — never first with
        ``stacklevel=`` and then again.
        """
        stream = stream_cls()
        _emit(stream, "old is deprecated")
        assert stream.calls == [expected_call]

    def test_equal_streams_each_receive_the_call_they_accept(self) -> None:
        """Equal-by-value streams with different signatures are each delivered to in the form they accept.

        Two sinks of the same value-object style class compare equal, yet one takes only the message and the other
        also takes ``stacklevel``. Emitting to both must reach both exactly once, not raise "unexpected keyword
        argument" because the first sink's classification was reused for the second.
        """
        message_only, with_stacklevel = EqualMessageOnlyStream(), EqualStacklevelStream()
        _emit(message_only, "old is deprecated")
        _emit(with_stacklevel, "old is deprecated")
        assert (message_only.calls, with_stacklevel.calls) == (
            ["old is deprecated"],
            [("old is deprecated", {"stacklevel": 2})],
        )

    def test_print_writes_message_once(self, capsys: pytest.CaptureFixture[str]) -> None:
        """``print`` as a stream prints the message a single time.

        ``print`` rejects ``stacklevel=`` and its signature support differs across Python versions; whichever rule
        classifies it, the outcome must be one printed line.
        """
        _emit(print, "old is deprecated")
        assert capsys.readouterr().out == "old is deprecated\n"

    def test_logging_warning_logs_once(self, caplog: pytest.LogCaptureFixture) -> None:
        """``logging.warning`` as a stream logs the message a single time.

        The recommended CI-friendly stream accepts ``stacklevel`` through ``**kwargs``; it must produce exactly one
        record per warning.
        """
        with caplog.at_level(logging.WARNING):
            _emit(logging.warning, "old is deprecated")
        assert [record.getMessage() for record in caplog.records] == ["old is deprecated"]

    def test_mock_stream_called_once(self) -> None:
        """A ``Mock`` stream is called once with the message as its first argument.

        Test suites commonly pass a ``Mock`` as ``stream=`` and assert on its calls; it must see one call.
        """
        stream = Mock()
        _emit(stream, "old is deprecated")
        assert stream.call_count == 1
        assert stream.call_args.args == ("old is deprecated",)

    @pytest.mark.parametrize(
        "stream",
        [
            pytest.param(warnings.warn, id="warnings-warn"),
            pytest.param(functools.partial(warnings.warn, category=FutureWarning), id="partial-of-warn"),
        ],
    )
    def test_warning_points_at_emit_caller(self, stream: Callable[..., Any]) -> None:
        """``_emit`` measures the level itself, so the warning is attributed to the code that called it.

        Every emitter in the package routes custom streams through ``_emit``, which walks past its own frame and
        any other ``deprecate`` frame; called straight from this test, the warning must land in this file.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _emit(stream, "old is deprecated")
        assert len(caught) == 1
        assert caught[0].filename.endswith("test_messaging.py")

    @pytest.mark.parametrize(
        "stream_cls",
        [
            pytest.param(KeywordTypeErrorStream, id="typeerror-mentions-keyword"),
            pytest.param(FlakyTypeErrorStream, id="unrelated-typeerror"),
        ],
    )
    def test_stream_typeerror_propagates_after_one_call(self, stream_cls: type[RecordingStream]) -> None:
        """A ``TypeError`` raised inside the stream propagates unchanged and the stream is not re-invoked.

        Whether the handler's own error mentions "keyword" or not, ``_emit`` must not interpret it: the decision
        about ``stacklevel`` was already made from the signature, so the only correct reaction is to let it through.
        """
        stream = stream_cls()
        with pytest.raises(TypeError):
            _emit(stream, "old is deprecated")
        assert len(stream.calls) == 1

    def test_unhashable_stream_receives_stacklevel(self) -> None:
        """An unhashable stream that accepts ``stacklevel`` is still called once with it.

        The memo cache cannot hold such a stream, but the fallback path must deliver the same behaviour.
        """
        stream = UnhashableStream()
        _emit(stream, "old is deprecated")
        assert stream.calls == [("old is deprecated", {"stacklevel": 2})]


class TestTemplateBareConversion:
    """Bare ``%``-conversions in ``message_template`` must be rejected at decoration time."""

    def test_bare_s_rejected(self) -> None:
        """``"%s"`` silently renders the whole substitution mapping at call time, so it must be rejected up front."""
        with pytest.raises(ValueError, match="bare `%`-conversion"):
            _validate_message_template("Deprecated: %s")

    def test_escaped_percent_allowed(self) -> None:
        """A literal ``%%`` alongside a valid mapping key is legitimate and must pass validation."""
        _validate_message_template("100%% done: %(source_name)s")  # no exception raised = accepted

    def test_bare_conversion_raises_on_decoration(self) -> None:
        """The bare-conversion guard fires when the decorator is applied, not on first call."""
        with pytest.raises(ValueError, match="bare `%`-conversion"):

            @deprecated(deprecated_in="1.0", remove_in="2.0", message_template="gone %d")
            def old() -> int:
                return 1


class TestFailingStreamMetadata:
    """Metadata failures must not prevent delivery to a usable stream."""

    @pytest.mark.parametrize("fail", [False, True])
    def test_delivers_once_despite_descriptor_error(self, fail: bool) -> None:
        """Fall back to a message-only call while preserving callback failures.

        A user-defined stream may expose dynamic signature metadata that raises even
        though the callback works. Its real callback error must still reach the caller.
        """
        stream = RuntimeSignatureStream(fail=fail)
        if fail:
            with pytest.raises(RuntimeError, match="^callback failed$"):
                _emit(stream, "old is deprecated")
        else:
            _emit(stream, "old is deprecated")
        assert stream.calls == [("old is deprecated", {})]


class TestEvalSkipIf:
    """The one ``skip_if`` evaluation shared by the callable wrappers and the deprecation proxy."""

    @pytest.mark.parametrize(
        ("skip_if", "expected"),
        [
            pytest.param(True, True, id="static-true"),
            pytest.param(False, False, id="static-false"),
            pytest.param(1, True, id="truthy-static-value"),
            pytest.param(lambda: True, True, id="callable-true"),
            pytest.param(lambda: False, False, id="callable-false"),
        ],
    )
    def test_returns_evaluated_condition(self, skip_if: Any, expected: bool) -> None:  # noqa: ANN401
        """A static value is converted with ``bool()`` and a callable is invoked, each giving a strict ``bool``.

        A library gates its deprecation on a feature flag, either fixed at import time or read from configuration on
        every call; both forms must switch the deprecation machinery off exactly when they evaluate truthy.
        """
        assert _eval_skip_if(skip_if) is expected

    def test_non_bool_callable_result_raises(self) -> None:
        """A callable returning anything but a ``bool`` raises ``TypeError`` naming the returned type.

        A flag reader that returns ``1`` or a string by mistake would otherwise be treated as truthy and silently
        disable the warning; the error names the offending type instead.
        """
        with pytest.raises(TypeError, match=r"^User function 'skip_if' shall return bool, but got: <class 'int'>$"):
            _eval_skip_if(lambda: 1)  # type: ignore[arg-type, return-value]
