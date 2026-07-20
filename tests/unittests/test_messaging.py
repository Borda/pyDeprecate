"""Unit tests for warning templates and emitters (:mod:`deprecate.messaging`)."""

from unittest.mock import MagicMock

import pytest

from deprecate import deprecated
from deprecate.messaging import _raise_warn, _raise_warn_arguments, _raise_warn_callable, _validate_template_mgs
from tests.collection_targets import base_sum_kwargs


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
        """A custom template_mgs overrides both built-in templates and receives the same substitutions."""
        stream = MagicMock()

        def old_func() -> None:
            pass

        _raise_warn_callable(stream, old_func, None, "1.0", "2.0", template_mgs="custom: %(source_name)s")
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
        """A custom template_mgs overrides the default argument-rename template."""
        stream = MagicMock()

        def my_func(old: int = 0, new: int = 0) -> None:
            pass

        _raise_warn_arguments(stream, my_func, {"old": "new"}, "1.0", "2.0", template_mgs="map: %(argument_map)s")
        assert stream.call_args[0][0].startswith("map: ")


class TestTemplateMgsValidation:
    """A malformed ``template_mgs`` is detected at decoration time, not at first call (B6)."""

    def test_unknown_placeholder_raises_at_decoration(self) -> None:
        """An unknown ``%(...)s`` key raises ``ValueError`` when ``@deprecated`` is applied — before any call."""
        with pytest.raises(ValueError, match="Invalid template_mgs"):
            deprecated(
                target=base_sum_kwargs, deprecated_in="0.8", remove_in="1.0", template_mgs="bad %(unknown_key)s"
            )(base_sum_kwargs)

    def test_valid_template_accepted_at_decoration(self) -> None:
        """A template using only documented placeholders is accepted at decoration time."""
        # Must not raise — covers happy path of the probe.
        wrapper = deprecated(
            target=base_sum_kwargs,
            deprecated_in="0.8",
            remove_in="1.0",
            template_mgs="`%(source_name)s` -> `%(target_name)s` since v%(deprecated_in)s",
        )(base_sum_kwargs)
        assert callable(wrapper)


class TestRaiseWarnStacklevel:
    """Stream called with ``stacklevel`` when accepted; internal TypeError propagates without double-call."""

    def test_internal_typeerror_not_swallowed_no_double_call(self) -> None:
        """An internal TypeError from a stacklevel-accepting stream propagates and the stream runs exactly once.

        A ``TypeError`` raised *inside* a stacklevel-accepting stream must propagate and the stream must run
        exactly once. A naive ``try/except TypeError`` would re-invoke the stream on any TypeError, producing
        duplicate side effects (double log lines) for anyone whose custom stream raised internally.  The
        message-based discrimination (``"stacklevel" in str(exc)``) prevents this.
        """
        calls: list[str] = []

        def stream(msg: str, stacklevel: int = 1) -> None:
            calls.append(msg)
            raise TypeError("boom from inside the stream")

        def old() -> None:
            pass

        with pytest.raises(TypeError, match="boom from inside the stream"):
            _raise_warn(stream, old, "%(source_name)s", stacklevel=3)
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

        _raise_warn(stream, src, "%(source_name)s", stacklevel=3)
        assert len(calls) == 1
        assert "stacklevel" in calls[0][1]


class TestTemplateBareConversion:
    """Bare ``%``-conversions in ``template_mgs`` must be rejected at decoration time."""

    def test_bare_s_rejected(self) -> None:
        """``"%s"`` silently renders the whole substitution mapping at call time, so it must be rejected up front."""
        with pytest.raises(ValueError, match="bare `%`-conversion"):
            _validate_template_mgs("Deprecated: %s")

    def test_escaped_percent_allowed(self) -> None:
        """A literal ``%%`` alongside a valid mapping key is legitimate and must pass validation."""
        _validate_template_mgs("100%% done: %(source_name)s")  # no exception raised = accepted

    def test_bare_conversion_raises_on_decoration(self) -> None:
        """The bare-conversion guard fires when the decorator is applied, not on first call."""
        with pytest.raises(ValueError, match="bare `%`-conversion"):

            @deprecated(deprecated_in="1.0", remove_in="2.0", template_mgs="gone %d")
            def old() -> int:
                return 1
