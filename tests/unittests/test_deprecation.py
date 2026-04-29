"""Unit tests for private helpers in deprecate.deprecation."""

import inspect
import sys
import warnings
from dataclasses import dataclass
from enum import Enum
from typing import Any, Union, cast
from unittest.mock import MagicMock

import pytest

from deprecate import TargetMode, deprecated, void
from deprecate.deprecation import (
    POSITIONAL_OR_KEYWORD,
    _get_positional_params,
    _normalize_target,
    _prepare_target_call,
    _raise_warn,
    _raise_warn_arguments,
    _raise_warn_callable,
    _update_kwargs_with_args,
    _update_kwargs_with_defaults,
)
from deprecate.docstring.inject import (
    _update_docstring_with_deprecation,
    find_docstring_insertion_index,
    is_numpy_underline,
    normalize_docstring_style,
)
from deprecate.proxy import _DeprecatedProxy
from tests.collection_deprecate import CrossGuardModuleLevel, CrossGuardOldClass, CrossGuardSameClass
from tests.collection_targets import KeywordCallTarget, call_signature_source


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


def test_class_target_uses_call_signature_for_validation() -> None:
    """Class targets validate against metaclass __call__ when not forwarding __init__."""
    target_callable = _prepare_target_call(call_signature_source, KeywordCallTarget, {"value": "red"})
    assert target_callable is KeywordCallTarget


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


class TestDeprecatedClassGuard:
    """@deprecated emits UserWarning and delegates to @deprecated_class when applied to a class."""

    _WHOLE_PARAMS = [
        pytest.param(TargetMode.WHOLE, id="TargetMode.WHOLE"),
        pytest.param(None, marks=pytest.mark.filterwarnings("ignore::FutureWarning"), id="legacy-None"),
    ]

    @pytest.mark.parametrize("target_val", _WHOLE_PARAMS)
    def test_warns_for_plain_class(self, target_val: Union[TargetMode, None]) -> None:
        """Applying @deprecated to a plain class emits UserWarning and returns a proxy."""
        with pytest.warns(UserWarning, match="deprecated_class"):

            @deprecated(target=target_val, deprecated_in="1.0", remove_in="2.0")
            class _MyClass:
                pass

        assert isinstance(_MyClass, _DeprecatedProxy)

    @pytest.mark.parametrize("target_val", _WHOLE_PARAMS)
    def test_warns_for_enum_class(self, target_val: Union[TargetMode, None]) -> None:
        """Applying @deprecated to an Enum class emits UserWarning and returns a proxy."""
        with pytest.warns(UserWarning, match="deprecated_class"):

            @deprecated(target=target_val, deprecated_in="1.0", remove_in="2.0")
            class _MyEnum(Enum):
                A = "a"

        assert isinstance(_MyEnum, _DeprecatedProxy)

    @pytest.mark.parametrize("target_val", _WHOLE_PARAMS)
    def test_warns_for_dataclass(self, target_val: Union[TargetMode, None]) -> None:
        """Applying @deprecated to a dataclass emits UserWarning and returns a proxy."""
        with pytest.warns(UserWarning, match="deprecated_class"):

            @deprecated(target=target_val, deprecated_in="1.0", remove_in="2.0")
            @dataclass
            class _MyData:
                x: int

        assert isinstance(_MyData, _DeprecatedProxy)

    def test_stream_none_suppresses_meta_warning(self) -> None:
        """stream=None suppresses the UserWarning when @deprecated(target=None) is applied to a class."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            warnings.filterwarnings("ignore", category=FutureWarning)

            @deprecated(target=None, deprecated_in="1.0", remove_in="2.0", stream=None)
            class _MyClass:
                pass

        assert isinstance(_MyClass, _DeprecatedProxy)

    def test_stream_none_suppresses_meta_warning_whole_class(self) -> None:
        """stream=None suppresses the UserWarning when @deprecated(target=TargetMode.WHOLE) is applied to a plain class."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")

            @deprecated(target=TargetMode.WHOLE, deprecated_in="1.0", remove_in="2.0", stream=None)
            class _MyWholeClass:
                pass

        assert isinstance(_MyWholeClass, _DeprecatedProxy)

    @pytest.mark.parametrize("target_val", _WHOLE_PARAMS)
    def test_does_not_raise_for_function(self, target_val: Union[TargetMode, None]) -> None:
        """Applying @deprecated to a regular function does not raise."""

        @deprecated(target=target_val, deprecated_in="1.0", remove_in="2.0")
        def my_func() -> None:
            pass

        with pytest.warns(FutureWarning):
            my_func()

    @pytest.mark.parametrize("target_val", _WHOLE_PARAMS)
    def test_does_not_raise_for_init_method(self, target_val: Union[TargetMode, None]) -> None:
        """Applying @deprecated to __init__ (not the class itself) does not raise."""

        class MyClass:
            @deprecated(target=target_val, deprecated_in="1.0", remove_in="2.0")
            def __init__(self) -> None:
                pass

        with pytest.warns(FutureWarning):
            instance = MyClass()
        assert isinstance(instance, MyClass)


class TestCrossClassMethodGuard:
    """@deprecated raises TypeError when target is a method on a different class."""

    def test_raises_for_cross_class_method_target(self) -> None:
        """Forwarding to a method on a different class raises TypeError at decoration time.

        The misconfigured classes are defined inline (not in collection_deprecate.py)
        because placing ``@deprecated`` with an invalid cross-class target at module
        level would raise ``TypeError`` at import time, breaking every test that
        imports the collection module.
        """

        class OtherClass:
            def other_method(self, x: int) -> int:
                return x

        with pytest.raises(TypeError, match="cross-class method forwarding is not supported"):

            class MyClass:
                @deprecated(target=OtherClass.other_method, deprecated_in="1.0", remove_in="2.0")
                def old_method(self, x: int) -> int:
                    return void(x)

    def test_raises_for_class_target_on_non_init_method(self) -> None:
        """@deprecated(target=SomeClass) on a non-__init__ class method raises TypeError.

        Passing a class directly as target for a bound method would silently forward
        ``self`` of the wrong type.  Only ``__init__`` supports ``target=SomeClass``
        (auto-remapped to ``target=SomeClass.__init__``); for any other class method
        the caller must use a same-class method target or ``target=None``/``True``.

        Defined inline for the same import-time reason as the cross-class test above.
        """

        class Target:
            pass

        with pytest.raises(TypeError, match="only supported on `__init__`"):

            class _Owner:
                @deprecated(target=Target, deprecated_in="1.0", remove_in="2.0")
                def some_method(self) -> None:
                    pass

    def test_does_not_raise_for_same_class_method_target(self) -> None:
        """Forwarding to a method on the same class does not raise."""
        with pytest.warns(FutureWarning):
            assert CrossGuardSameClass().old_method(5) == 10

    def test_does_not_raise_for_module_level_function_target(self) -> None:
        """Forwarding a class method to a module-level function is allowed (no self passed)."""
        assert callable(CrossGuardModuleLevel.old_method)

    def test_does_not_raise_for_class_target(self) -> None:
        """Forwarding __init__ to a full class (constructor forwarding) is allowed."""
        with pytest.warns(FutureWarning):
            old = CrossGuardOldClass(3)
        assert isinstance(old, CrossGuardOldClass)
        assert old.x == 3


class TestDocstringStyleValidation:
    """Validation for ``docstring_style`` values."""

    _WHOLE_PARAMS = [
        pytest.param(TargetMode.WHOLE, id="TargetMode.WHOLE"),
        pytest.param(None, marks=pytest.mark.filterwarnings("ignore::FutureWarning"), id="legacy-None"),
    ]

    @pytest.mark.parametrize("target_val", _WHOLE_PARAMS)
    def test_invalid_docstring_style_raises_value_error(self, target_val: Union[TargetMode, None]) -> None:
        """Unsupported ``docstring_style`` values should fail fast at decoration time."""
        with pytest.raises(ValueError, match="Invalid `docstring_style` value"):

            @deprecated(
                target=target_val,
                deprecated_in="1.0",
                remove_in="2.0",
                update_docstring=True,
                docstring_style="unsupported-style",  # type: ignore[arg-type, unused-ignore]
            )
            def some_func() -> None:
                """A function."""

    @pytest.mark.parametrize("target_val", _WHOLE_PARAMS)
    def test_invalid_docstring_style_raises_even_without_update_docstring(
        self, target_val: Union[TargetMode, None]
    ) -> None:
        """``docstring_style`` is validated eagerly regardless of ``update_docstring``."""
        with pytest.raises(ValueError, match="Invalid `docstring_style` value"):

            @deprecated(
                target=target_val,
                deprecated_in="1.0",
                docstring_style="unsupported-style",  # type: ignore[arg-type, unused-ignore]
            )
            def some_func() -> None:
                """A function."""

    @pytest.mark.parametrize("style", ["RST", "MKDOCS", "Markdown", "MkDocs", "AUTO", "Auto"])
    def test_case_insensitive_normalization(self, style: str) -> None:
        """``docstring_style`` values are matched case-insensitively."""
        assert normalize_docstring_style(style) in ("rst", "mkdocs")

    def test_auto_style_resolves_to_rst_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``"auto"`` resolves to ``"rst"`` when no env var is set and argv is not mkdocs."""
        monkeypatch.setattr(sys, "argv", ["pytest"])
        monkeypatch.delenv("DEPRECATE_DOCSTRING_STYLE", raising=False)
        monkeypatch.delitem(sys.modules, "mkdocs", raising=False)
        result = normalize_docstring_style("auto")
        assert result == "rst"

    def test_auto_style_env_var_mkdocs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``DEPRECATE_DOCSTRING_STYLE=mkdocs`` forces MkDocs format."""
        monkeypatch.setenv("DEPRECATE_DOCSTRING_STYLE", "mkdocs")
        assert normalize_docstring_style("auto") == "mkdocs"

    def test_auto_style_env_var_rst(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``DEPRECATE_DOCSTRING_STYLE=rst`` forces RST format."""
        monkeypatch.setenv("DEPRECATE_DOCSTRING_STYLE", "rst")
        assert normalize_docstring_style("auto") == "rst"

    def test_auto_style_detects_mkdocs_from_argv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``sys.argv[0]`` containing ``mkdocs`` resolves ``"auto"`` to ``"mkdocs"``."""
        monkeypatch.setattr(sys, "argv", ["/usr/local/bin/mkdocs", "build"])
        monkeypatch.delenv("DEPRECATE_DOCSTRING_STYLE", raising=False)
        assert normalize_docstring_style("auto") == "mkdocs"

    @pytest.mark.parametrize("target_val", _WHOLE_PARAMS)
    def test_update_docstring_idempotent(self, target_val: Union[TargetMode, None]) -> None:
        """Calling ``_update_docstring_with_deprecation`` twice must not duplicate the notice."""

        @deprecated(target=target_val, deprecated_in="1.0", update_docstring=True)
        def some_func() -> None:
            """A function."""

        original_doc = some_func.__doc__
        _update_docstring_with_deprecation(some_func)
        assert some_func.__doc__ == original_doc

    @pytest.mark.parametrize("target_val", _WHOLE_PARAMS)
    def test_idempotency_guard_no_false_positive_on_version_prefix(self, target_val: Union[TargetMode, None]) -> None:
        """Guard must not suppress injection when the docstring mentions a longer version.

        ``deprecated_in="1"`` should inject ``.. deprecated:: 1`` even when the
        existing docstring contains ``.. deprecated:: 1.0`` in prose — the "1"
        string is a substring of "1.0" so a naive ``in`` check would cause a
        false positive.
        """

        @deprecated(target=target_val, deprecated_in="1", update_docstring=True, docstring_style="rst")
        def some_func() -> None:
            """Summary. See also .. deprecated:: 1.0 handling."""

        assert some_func.__doc__ is not None
        lines = [line.strip() for line in some_func.__doc__.splitlines()]
        assert ".. deprecated:: 1" in lines


class TestNumpyUnderlineDetection:
    """Tests for NumPy section underline detection helper."""

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("---", True),
            ("----------", True),
            (" -- ", False),
            ("===", False),
            ("abc", False),
            ("--", False),
        ],
    )
    def test_is_numpy_underline(self, line: str, expected: bool) -> None:
        """Underline helper should accept only 3+ dashes."""
        assert is_numpy_underline(line) is expected


class TestDocstringInsertionIndex:
    """Tests for Google/NumPy insertion index detection."""

    def test_detects_google_header_with_whitespace_and_case(self) -> None:
        """Google headers are detected case-insensitively with surrounding whitespace."""
        lines = ["Summary", "", "  ArGs:  ", "    a: value"]
        assert find_docstring_insertion_index(lines) == 2

    def test_detects_numpy_header_with_underline(self) -> None:
        """NumPy header should be detected only when followed by dashed underline."""
        lines = ["Summary", "", "Parameters", "----------", "a : int"]
        assert find_docstring_insertion_index(lines) == 2

    def test_does_not_detect_numpy_header_without_underline(self) -> None:
        """NumPy-like header without underline should fall back to append-at-end."""
        lines = ["Summary", "", "Parameters", "a : int"]
        assert find_docstring_insertion_index(lines) == len(lines)

    def test_boundary_header_last_line_does_not_crash(self) -> None:
        """Header on final line should not index past bounds and should append at end."""
        lines = ["Summary", "Parameters"]
        assert find_docstring_insertion_index(lines) == len(lines)


class TestDocstringStyleOutput:
    """Verify each docstring style alias produces the correct notice format."""

    @pytest.mark.parametrize(
        ("style", "expected_marker"),
        [
            ("rst", ".. deprecated:: 1.0"),
            ("mkdocs", '!!! warning "Deprecated in 1.0"'),
            ("markdown", '!!! warning "Deprecated in 1.0"'),
        ],
    )
    def test_notice_marker_for_explicit_style(self, style: str, expected_marker: str) -> None:
        """Each explicit style injects the expected notice format into the docstring."""

        @deprecated(target=None, deprecated_in="1.0", update_docstring=True, docstring_style=style)  # type: ignore[arg-type, unused-ignore]
        def _fn() -> None:
            """A simple function."""

        assert _fn.__doc__ is not None
        assert expected_marker in _fn.__doc__

    @pytest.mark.parametrize(
        ("style", "expected_marker"),
        [
            ("rst", ".. deprecated:: 1.0"),
            ("mkdocs", '!!! warning "Deprecated in 1.0"'),
            ("markdown", '!!! warning "Deprecated in 1.0"'),
        ],
    )
    def test_notice_inserted_before_google_args_for_style(self, style: str, expected_marker: str) -> None:
        """Notice is placed before ``Args:`` regardless of style."""

        @deprecated(target=None, deprecated_in="1.0", update_docstring=True, docstring_style=style)  # type: ignore[arg-type, unused-ignore]
        def _fn(x: int) -> None:
            """Summary.

            Args:
                x: A value.
            """

        assert _fn.__doc__ is not None
        doc = _fn.__doc__
        assert expected_marker in doc
        assert doc.index(expected_marker) < doc.index("Args:")

    @pytest.mark.parametrize(
        ("style", "absent_marker"),
        [
            ("rst", "!!! warning"),
            ("mkdocs", ".. deprecated::"),
            ("markdown", ".. deprecated::"),
        ],
    )
    def test_other_style_marker_absent(self, style: str, absent_marker: str) -> None:
        """The notice uses exactly one format — the other style's marker is absent."""

        @deprecated(target=None, deprecated_in="1.0", update_docstring=True, docstring_style=style)  # type: ignore[arg-type, unused-ignore]
        def _fn() -> None:
            """A simple function."""

        assert absent_marker not in (_fn.__doc__ or "")


# ---------------------------------------------------------------------------
# _normalize_target — invalid / unrecognised input types (#20)
# ---------------------------------------------------------------------------


class TestNormalizeTargetInvalidInputs:
    """_normalize_target passes unrecognised non-class values through unchanged."""

    @pytest.mark.parametrize("bad_target", [42, "not_callable", [], {}])
    def test_invalid_type_returned_unchanged(self, bad_target: object) -> None:
        """Non-callable, non-class, non-sentinel values pass through _normalize_target as-is."""

        def dummy() -> None:
            pass

        result = _normalize_target(source=dummy, target=cast(Any, bad_target))
        assert result is bad_target

    @pytest.mark.parametrize("bad_target", [42, "not_callable", [], {}])
    def test_invalid_target_source_body_runs(self, bad_target: object) -> None:
        """Non-callable target is never invoked; source body executes normally at call time."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            @deprecated(target=cast(Any, bad_target), deprecated_in="0.9", remove_in="1.0")
            def fn(x: int) -> int:
                return x + 1

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = fn(4)

        assert result == 5
