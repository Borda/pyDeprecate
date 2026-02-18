"""Unit tests for private helpers in deprecate.deprecation."""

import inspect
import warnings
from enum import Enum
from unittest.mock import MagicMock

import pytest

from deprecate.deprecation import (
    POSITIONAL_ONLY,
    POSITIONAL_OR_KEYWORD,
    TEMPLATE_WARNING_ARGUMENTS,
    TEMPLATE_WARNING_CALLABLE,
    TEMPLATE_WARNING_NO_TARGET,
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
    def test_returns_only_positional_params(self) -> None:
        def my_func(a: int, b: str, *, kw_only: int = 0) -> None:
            pass

        params = list(inspect.signature(my_func).parameters.values())
        result = _get_positional_params(params)
        assert [p.name for p in result] == ["a", "b"]

    def test_excludes_var_positional_and_var_keyword(self) -> None:
        def my_func(a: int, *args: int, **kwargs: int) -> None:
            pass

        params = list(inspect.signature(my_func).parameters.values())
        result = _get_positional_params(params)
        assert [p.name for p in result] == ["a"]

    def test_empty_params(self) -> None:
        def my_func() -> None:
            pass

        params = list(inspect.signature(my_func).parameters.values())
        assert _get_positional_params(params) == []

    def test_all_kinds_filtered(self) -> None:
        # Verify each param kind individually
        def my_func(pos_or_kw: int, *, kw_only: int) -> None:
            pass

        params = list(inspect.signature(my_func).parameters.values())
        assert params[0].kind == POSITIONAL_OR_KEYWORD
        assert params[1].kind == inspect.Parameter.KEYWORD_ONLY
        result = _get_positional_params(params)
        assert len(result) == 1
        assert result[0].name == "pos_or_kw"


class TestUpdateKwargsWithArgs:
    def test_no_positional_args_returns_kwargs_unchanged(self) -> None:
        def my_func(a: int, b: int) -> None:
            pass

        result = _update_kwargs_with_args(my_func, (), {"a": 1})
        assert result == {"a": 1}

    def test_maps_positional_to_param_names(self) -> None:
        def my_func(a: int, b: str, c: float = 3.0) -> None:
            pass

        result = _update_kwargs_with_args(my_func, (1, "hello"), {})
        assert result == {"a": 1, "b": "hello"}

    def test_merges_with_existing_kwargs(self) -> None:
        def my_func(a: int, b: int, c: int = 0) -> None:
            pass

        result = _update_kwargs_with_args(my_func, (10,), {"c": 99})
        assert result == {"a": 10, "c": 99}

    def test_stops_at_var_positional(self) -> None:
        def my_func(a: int, *args: int) -> None:
            pass

        # Extra positional args should not be mapped (stops at *args)
        result = _update_kwargs_with_args(my_func, (1, 2, 3), {})
        assert result == {"a": 1}

    def test_too_many_positional_raises_type_error(self) -> None:
        def my_func(a: int, b: int) -> None:
            pass

        with pytest.raises(TypeError, match="takes 2 positional"):
            _update_kwargs_with_args(my_func, (1, 2, 3), {})


class TestUpdateKwargsWithDefaults:
    def test_fills_missing_defaults(self) -> None:
        def my_func(a: int = 1, b: int = 2, c: int = 3) -> None:
            pass

        result = _update_kwargs_with_defaults(my_func, {})
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_provided_kwargs_override_defaults(self) -> None:
        def my_func(a: int = 1, b: int = 2, c: int = 3) -> None:
            pass

        result = _update_kwargs_with_defaults(my_func, {"b": 20})
        assert result == {"a": 1, "b": 20, "c": 3}

    def test_params_without_defaults_not_included(self) -> None:
        def my_func(required: int, optional: int = 5) -> None:
            pass

        result = _update_kwargs_with_defaults(my_func, {})
        assert result == {"optional": 5}
        assert "required" not in result

    def test_no_defaults_returns_provided_kwargs(self) -> None:
        def my_func(a: int, b: str) -> None:
            pass

        result = _update_kwargs_with_defaults(my_func, {"a": 7})
        assert result == {"a": 7}


class TestRaiseWarn:
    def test_calls_stream_with_formatted_message(self) -> None:
        stream = MagicMock()

        def old_func() -> None:
            pass

        _raise_warn(stream, old_func, "%(source_name)s deprecated since %(version)s", version="1.0")
        stream.assert_called_once()
        assert "old_func" in stream.call_args[0][0]
        assert "1.0" in stream.call_args[0][0]

    def test_extracts_class_name_from_init(self) -> None:
        stream = MagicMock()

        class MyClass:
            def __init__(self) -> None:
                pass

        _raise_warn(stream, MyClass.__init__, "%(source_name)s", )
        called_msg = stream.call_args[0][0]
        assert "MyClass" in called_msg

    def test_source_path_contains_module_and_name(self) -> None:
        stream = MagicMock()

        def my_func() -> None:
            pass

        _raise_warn(stream, my_func, "%(source_path)s")
        called_msg = stream.call_args[0][0]
        assert "my_func" in called_msg


class TestRaiseWarnCallable:
    def test_callable_target_uses_default_template(self) -> None:
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
        stream = MagicMock()

        def old_func() -> None:
            pass

        _raise_warn_callable(stream, old_func, None, "1.0", "2.0")
        msg = stream.call_args[0][0]
        assert "old_func" in msg
        # no-target template should not mention a replacement
        assert "new_func" not in msg

    def test_custom_template_overrides_default(self) -> None:
        stream = MagicMock()

        def old_func() -> None:
            pass

        _raise_warn_callable(stream, old_func, None, "1.0", "2.0", template_mgs="custom: %(source_name)s")
        assert stream.call_args[0][0] == "custom: old_func"


class TestRaiseWarnArguments:
    def test_formats_argument_mapping(self) -> None:
        stream = MagicMock()

        def my_func(old_arg: int = 1, new_arg: int = 1) -> None:
            pass

        _raise_warn_arguments(stream, my_func, {"old_arg": "new_arg"}, "1.0", "2.0")
        msg = stream.call_args[0][0]
        assert "my_func" in msg
        assert "old_arg" in msg
        assert "new_arg" in msg

    def test_multiple_argument_mappings(self) -> None:
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
        stream = MagicMock()

        def my_func(old: int = 0, new: int = 0) -> None:
            pass

        _raise_warn_arguments(stream, my_func, {"old": "new"}, "1.0", "2.0", template_mgs="map: %(argument_map)s")
        assert stream.call_args[0][0].startswith("map: ")


class TestIsEnumValueCase:
    class _OldEnum(Enum):
        A = "a"

    class _NewEnum(Enum):
        A = "a"

    def test_returns_true_for_enum_to_enum_with_value_missed(self) -> None:
        assert _is_enum_value_case(self._OldEnum, self._NewEnum, ["value"], True, True) is True

    def test_returns_false_when_source_not_enum(self) -> None:
        def plain_func() -> None:
            pass

        assert _is_enum_value_case(plain_func, self._NewEnum, ["value"], False, True) is False

    def test_returns_false_when_target_not_enum(self) -> None:
        def plain_target() -> None:
            pass

        assert _is_enum_value_case(self._OldEnum, plain_target, ["value"], True, False) is False

    def test_returns_false_when_missed_arg_not_value(self) -> None:
        assert _is_enum_value_case(self._OldEnum, self._NewEnum, ["other_arg"], True, True) is False

    def test_returns_false_when_multiple_missed_args(self) -> None:
        assert _is_enum_value_case(self._OldEnum, self._NewEnum, ["value", "extra"], True, True) is False

    def test_returns_false_when_no_missed_args(self) -> None:
        assert _is_enum_value_case(self._OldEnum, self._NewEnum, [], True, True) is False


class TestConvertEnumValueArgs:
    class _SampleEnum(Enum):
        ALPHA = "alpha"
        BETA = "beta"

    def test_moves_value_kwarg_to_positional(self) -> None:
        args, kwargs = _convert_enum_value_args(self._SampleEnum, (), {"value": "alpha"})
        assert args == ("alpha",)
        assert kwargs == {}

    def test_non_enum_target_returns_unchanged(self) -> None:
        def plain_func(value: str) -> None:
            pass

        original_args = (1, 2)
        original_kwargs = {"value": "x"}
        args, kwargs = _convert_enum_value_args(plain_func, original_args, original_kwargs)
        assert args == original_args
        assert kwargs == original_kwargs

    def test_no_value_kwarg_returns_unchanged(self) -> None:
        args, kwargs = _convert_enum_value_args(self._SampleEnum, (), {"other": "x"})
        assert args == ()
        assert kwargs == {"other": "x"}

    def test_existing_positional_args_not_overwritten(self) -> None:
        # If positional args already exist, value is removed from kwargs but not prepended
        args, kwargs = _convert_enum_value_args(self._SampleEnum, ("alpha",), {"value": "alpha"})
        assert args == ("alpha",)
        assert "value" not in kwargs
