"""Unit tests for private helpers in deprecate.deprecation."""

import inspect
from enum import Enum
from unittest.mock import MagicMock

import pytest

from deprecate.deprecation import (
    POSITIONAL_OR_KEYWORD,
    _convert_enum_value_args,
    _get_positional_params,
    _is_enum_value_case,
    _raise_warn,
    _raise_warn_arguments,
    _raise_warn_callable,
    _update_kwargs_with_args,
    _update_kwargs_with_defaults,
)


class TestGetPositionalParams:
    """Tests for _get_positional_params — filters a param list to POSITIONAL_OR_KEYWORD and POSITIONAL_ONLY kinds."""

    def test_returns_only_positional_params(self) -> None:
        """Keyword-only params (after *) are excluded; positional params are returned."""

        def my_func(a: int, b: str, *, kw_only: int = 0) -> None:
            pass

        params = list(inspect.signature(my_func).parameters.values())
        result = _get_positional_params(params)
        assert [p.name for p in result] == ["a", "b"]

    def test_excludes_var_positional_and_var_keyword(self) -> None:
        """*args and **kwargs are excluded; only the plain positional param is returned."""

        def my_func(a: int, *args: int, **kwargs: int) -> None:
            pass

        params = list(inspect.signature(my_func).parameters.values())
        result = _get_positional_params(params)
        assert [p.name for p in result] == ["a"]

    def test_empty_params(self) -> None:
        """A function with no parameters returns an empty list."""

        def my_func() -> None:
            pass

        params = list(inspect.signature(my_func).parameters.values())
        assert _get_positional_params(params) == []

    def test_all_kinds_filtered(self) -> None:
        """Confirms POSITIONAL_OR_KEYWORD is kept while KEYWORD_ONLY is dropped."""

        def my_func(pos_or_kw: int, *, kw_only: int) -> None:
            pass

        params = list(inspect.signature(my_func).parameters.values())
        assert params[0].kind == POSITIONAL_OR_KEYWORD
        assert params[1].kind == inspect.Parameter.KEYWORD_ONLY
        result = _get_positional_params(params)
        assert len(result) == 1
        assert result[0].name == "pos_or_kw"


class TestUpdateKwargsWithArgs:
    """Tests for _update_kwargs_with_args — merges positional call args into the kwargs dict by param name."""

    def test_no_positional_args_returns_kwargs_unchanged(self) -> None:
        """When no positional args are passed, the existing kwargs dict is returned as-is."""

        def my_func(a: int, b: int) -> None:
            pass

        result = _update_kwargs_with_args(my_func, (), {"a": 1})
        assert result == {"a": 1}

    def test_maps_positional_to_param_names(self) -> None:
        """Positional args are matched to param names in declaration order and added to kwargs."""

        def my_func(a: int, b: str, c: float = 3.0) -> None:
            pass

        result = _update_kwargs_with_args(my_func, (1, "hello"), {})
        assert result == {"a": 1, "b": "hello"}

    def test_merges_with_existing_kwargs(self) -> None:
        """Positional args are merged with already-present keyword args without overwriting them."""

        def my_func(a: int, b: int, c: int = 0) -> None:
            pass

        result = _update_kwargs_with_args(my_func, (10,), {"c": 99})
        assert result == {"a": 10, "c": 99}

    def test_stops_at_var_positional(self) -> None:
        """Extra positional args beyond a *args boundary are not mapped to named params."""

        def my_func(a: int, *args: int) -> None:
            pass

        result = _update_kwargs_with_args(my_func, (1, 2, 3), {})
        assert result == {"a": 1}

    def test_too_many_positional_raises_type_error(self) -> None:
        """Passing more positional args than the function has positional params raises TypeError."""

        def my_func(a: int, b: int) -> None:
            pass

        with pytest.raises(TypeError, match="takes 2 positional"):
            _update_kwargs_with_args(my_func, (1, 2, 3), {})


class TestUpdateKwargsWithDefaults:
    """Tests for _update_kwargs_with_defaults — fills missing kwargs with the target function's default values."""

    def test_fills_missing_defaults(self) -> None:
        """All defaulted params that are absent from kwargs are added with their default values."""

        def my_func(a: int = 1, b: int = 2, c: int = 3) -> None:
            pass

        result = _update_kwargs_with_defaults(my_func, {})
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_provided_kwargs_override_defaults(self) -> None:
        """Explicitly provided kwargs take precedence over the function's own defaults."""

        def my_func(a: int = 1, b: int = 2, c: int = 3) -> None:
            pass

        result = _update_kwargs_with_defaults(my_func, {"b": 20})
        assert result == {"a": 1, "b": 20, "c": 3}

    def test_params_without_defaults_not_included(self) -> None:
        """Required parameters (no default) are not injected into kwargs."""

        def my_func(required: int, optional: int = 5) -> None:
            pass

        result = _update_kwargs_with_defaults(my_func, {})
        assert result == {"optional": 5}
        assert "required" not in result

    def test_no_defaults_returns_provided_kwargs(self) -> None:
        """When the function has no defaults at all, the input kwargs dict is returned unchanged."""

        def my_func(a: int, b: str) -> None:
            pass

        result = _update_kwargs_with_defaults(my_func, {"a": 7})
        assert result == {"a": 7}


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


class TestIsEnumValueCase:
    """Tests for _is_enum_value_case — predicate that detects the Enum.__new__(value) call pattern."""

    class _OldEnum(Enum):
        A = "a"

    class _NewEnum(Enum):
        A = "a"

    def test_returns_true_for_enum_to_enum_with_value_missed(self) -> None:
        """Returns True when both source and target are Enums and the sole missed arg is 'value'."""
        assert _is_enum_value_case(self._OldEnum, self._NewEnum, ["value"], True, True) is True

    def test_returns_false_when_source_not_enum(self) -> None:
        """Returns False when the source callable is not an Enum subclass."""

        def plain_func() -> None:
            pass

        assert _is_enum_value_case(plain_func, self._NewEnum, ["value"], False, True) is False

    def test_returns_false_when_target_not_enum(self) -> None:
        """Returns False when the target callable is not an Enum subclass."""

        def plain_target() -> None:
            pass

        assert _is_enum_value_case(self._OldEnum, plain_target, ["value"], True, False) is False

    def test_returns_false_when_missed_arg_not_value(self) -> None:
        """Returns False when the missed argument name is not 'value' — not the Enum pattern."""
        assert _is_enum_value_case(self._OldEnum, self._NewEnum, ["other_arg"], True, True) is False

    def test_returns_false_when_multiple_missed_args(self) -> None:
        """Returns False when more than one arg is missed — the Enum pattern requires exactly one."""
        assert _is_enum_value_case(self._OldEnum, self._NewEnum, ["value", "extra"], True, True) is False

    def test_returns_false_when_no_missed_args(self) -> None:
        """Returns False when no args are missed — the Enum value arg must be the one missing."""
        assert _is_enum_value_case(self._OldEnum, self._NewEnum, [], True, True) is False


class TestConvertEnumValueArgs:
    """Tests for _convert_enum_value_args — adapts call args/kwargs for Enum.__new__(value) invocation."""

    class _SampleEnum(Enum):
        ALPHA = "alpha"
        BETA = "beta"

    def test_moves_value_kwarg_to_positional(self) -> None:
        """The 'value' kwarg is extracted and prepended as a positional arg for Enum lookup."""
        args, kwargs = _convert_enum_value_args(self._SampleEnum, (), {"value": "alpha"})
        assert args == ("alpha",)
        assert kwargs == {}

    def test_non_enum_target_returns_unchanged(self) -> None:
        """When the target is not an Enum, args and kwargs are returned without modification."""

        def plain_func(value: str) -> None:
            pass

        original_args = (1, 2)
        original_kwargs = {"value": "x"}
        args, kwargs = _convert_enum_value_args(plain_func, original_args, original_kwargs)
        assert args == original_args
        assert kwargs == original_kwargs

    def test_no_value_kwarg_returns_unchanged(self) -> None:
        """When 'value' is not in kwargs, args and kwargs are returned without modification."""
        args, kwargs = _convert_enum_value_args(self._SampleEnum, (), {"other": "x"})
        assert args == ()
        assert kwargs == {"other": "x"}

    def test_existing_positional_args_not_overwritten(self) -> None:
        """When positional args already exist, 'value' is removed from kwargs but not prepended again."""
        args, kwargs = _convert_enum_value_args(self._SampleEnum, ("alpha",), {"value": "alpha"})
        assert args == ("alpha",)
        assert "value" not in kwargs
