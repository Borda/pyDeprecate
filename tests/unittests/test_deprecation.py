"""Unit tests for private helpers in deprecate.deprecation."""

import inspect
import sys
import warnings
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from typing import Any, Callable, Optional, Union, cast
from unittest.mock import MagicMock

import pytest
import typing_extensions

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
from tests.collection_deprecate import (
    CrossGuardModuleLevel,
    CrossGuardOldClass,
    CrossGuardSameClass,
    make_depr_args_remap_notify_with_extra,
    make_depr_compute_power_stacked,
    make_depr_notify_callable_stacked,
    pep702_stacked,
)
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

    _NOTIFY_PARAMS = [
        pytest.param(TargetMode.NOTIFY, id="TargetMode.NOTIFY"),
        pytest.param(None, marks=pytest.mark.filterwarnings("ignore::FutureWarning"), id="legacy-None"),
    ]

    @pytest.mark.parametrize("target_val", _NOTIFY_PARAMS)
    def test_warns_for_plain_class(self, target_val: Union[TargetMode, None]) -> None:
        """Applying @deprecated to a plain class emits UserWarning and returns a proxy."""
        with pytest.warns(UserWarning, match="deprecated_class"):

            @deprecated(target=target_val, deprecated_in="1.0", remove_in="2.0")
            class _MyClass:
                pass

        assert isinstance(_MyClass, _DeprecatedProxy)

    @pytest.mark.parametrize("target_val", _NOTIFY_PARAMS)
    def test_warns_for_enum_class(self, target_val: Union[TargetMode, None]) -> None:
        """Applying @deprecated to an Enum class emits UserWarning and returns a proxy."""
        with pytest.warns(UserWarning, match="deprecated_class"):

            @deprecated(target=target_val, deprecated_in="1.0", remove_in="2.0")
            class _MyEnum(Enum):
                A = "a"

        assert isinstance(_MyEnum, _DeprecatedProxy)

    @pytest.mark.parametrize("target_val", _NOTIFY_PARAMS)
    def test_warns_for_dataclass(self, target_val: Union[TargetMode, None]) -> None:
        """Applying @deprecated to a dataclass emits UserWarning and returns a proxy."""
        with pytest.warns(UserWarning, match="deprecated_class"):

            @deprecated(target=target_val, deprecated_in="1.0", remove_in="2.0")
            @dataclass
            class _MyData:
                x: int

        assert isinstance(_MyData, _DeprecatedProxy)

    def test_stream_none_suppresses_meta_warning(self) -> None:
        """``stream=None`` suppresses the UserWarning when @deprecated(target=None) is applied to a class."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            warnings.filterwarnings("ignore", category=FutureWarning)

            @deprecated(target=None, deprecated_in="1.0", remove_in="2.0", stream=None)
            class _MyClass:
                pass

        assert isinstance(_MyClass, _DeprecatedProxy)

    def test_stream_none_suppresses_meta_warning_whole_class(self) -> None:
        """``stream=None`` suppresses the UserWarning when @deprecated(target=NOTIFY) is applied to a plain class."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")

            @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0", stream=None)
            class _MyWholeClass:
                pass

        assert isinstance(_MyWholeClass, _DeprecatedProxy)

    @pytest.mark.parametrize("target_val", _NOTIFY_PARAMS)
    def test_does_not_raise_for_function(self, target_val: Union[TargetMode, None]) -> None:
        """Applying @deprecated to a regular function does not raise."""

        @deprecated(target=target_val, deprecated_in="1.0", remove_in="2.0")
        def my_func() -> None:
            pass

        with pytest.warns(FutureWarning):
            my_func()

    @pytest.mark.parametrize("target_val", _NOTIFY_PARAMS)
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
    """@deprecated warns when target is a method on a different class."""

    def test_raises_for_cross_class_method_target(self) -> None:
        """Forwarding to a method on a different class raises TypeError at decoration time.

        The misconfigured classes are defined inline (not in collection_deprecate.py) because placing ``@deprecated``
        with a cross-class target at module level would raise TypeError at import time for every test that imports the
        collection module.

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

    def test_metaclass_generated_qualname_skips_guard(self) -> None:
        """A target with a metaclass-style rewritten ``__qualname__`` is detected and the guard returns silently.

        The module-globals check verifies that the top-level class name in the target's qualname actually exists in the
        target callable's module.  When it does not (as for a synthetic ``FakeOwner.replacement`` qualname produced by
        ``type(...)`` or manual assignment), the qualname cannot be trusted and the guard short-circuits.

        """

        def replacement(instance: object, x: int) -> int:
            void(instance)
            return x

        # Simulate a metaclass / type(...) assigning a qualname that looks like a method on FakeOwner,
        # even though `replacement` is unrelated to any such class.  FakeOwner does not exist in this
        # test module, so the module-globals check in the guard detects the unreliable qualname.
        replacement.__qualname__ = "FakeOwner.replacement"

        class RealOwner:  # decoration must not raise TypeError
            @deprecated(target=replacement, deprecated_in="1.0", remove_in="2.0")
            def old_method(self, x: int) -> int:
                return void(x)

    def test_decorator_rewriting_source_qualname_same_class_no_warning(self) -> None:
        """Frame inspection resolves the FP when a decorator corrupts source qualname on a same-class forward.

        Python sets ``__qualname__`` in the class body's locals at class-definition time, before any decorator runs.
        Reading it from ``sys._getframe`` therefore recovers the true enclosing class name even when a pre-applied
        decorator has overwritten ``fn.__qualname__`` on the source callable.

        """

        def rewrite_to_alien_class(fn: Callable[..., Any]) -> Callable[..., Any]:
            """Test fixture: outer decorator that retags the wrapped function as living on ``AlienClass``."""
            fn.__qualname__ = "AlienClass.method"
            return fn

        class MyClass:  # decoration must not raise TypeError
            def new_method(self, x: int) -> int:
                return x

            @deprecated(target=new_method, deprecated_in="1.0", remove_in="2.0")
            @rewrite_to_alien_class
            def old_method(self, x: int) -> int:
                return void(x)

    def test_decorator_rewriting_qualname_raises_for_cross_class(self) -> None:
        """A pre-applied decorator rewriting source qualname to a genuinely different class still raises TypeError.

        Fix 1 (frame inspection) overrides the corrupted source qualname with the true enclosing class taken from the
        class body's locals.  When the recovered class differs from the target's class, the guard still fires correctly
        — this guards against an over-eager FP suppression.

        """

        def rewrite_qualname(fn: Callable[..., Any]) -> Callable[..., Any]:
            """Test fixture: an outer decorator that retags the wrapped function as living on ``OtherOwner``."""
            fn.__qualname__ = "OtherOwner.rewritten_method"
            return fn

        class TargetOwner:
            def target_method(self, x: int) -> int:
                return x

        with pytest.raises(TypeError, match="cross-class method forwarding is not supported"):

            class RealOwner:
                @deprecated(target=TargetOwner.target_method, deprecated_in="1.0", remove_in="2.0")
                @rewrite_qualname
                def old_method(self, x: int) -> int:
                    return void(x)


class TestDocstringStyleValidation:
    """Validation for ``docstring_style`` values."""

    _NOTIFY_PARAMS = [
        pytest.param(TargetMode.NOTIFY, id="TargetMode.NOTIFY"),
        pytest.param(None, marks=pytest.mark.filterwarnings("ignore::FutureWarning"), id="legacy-None"),
    ]

    @pytest.mark.parametrize("target_val", _NOTIFY_PARAMS)
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

    @pytest.mark.parametrize("target_val", _NOTIFY_PARAMS)
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

    @pytest.mark.parametrize("target_val", _NOTIFY_PARAMS)
    def test_update_docstring_idempotent(self, target_val: Union[TargetMode, None]) -> None:
        """Calling ``_update_docstring_with_deprecation`` twice must not duplicate the notice."""

        @deprecated(target=target_val, deprecated_in="1.0", update_docstring=True)
        def some_func() -> None:
            """A function."""

        original_doc = some_func.__doc__
        _update_docstring_with_deprecation(some_func)
        assert some_func.__doc__ == original_doc

    @pytest.mark.parametrize("target_val", _NOTIFY_PARAMS)
    def test_idempotency_guard_no_false_positive_on_version_prefix(self, target_val: Union[TargetMode, None]) -> None:
        """Guard must not suppress injection when the docstring mentions a longer version.

        ``deprecated_in="1"`` should inject ``.. deprecated:: 1`` even when the
        existing docstring contains ``.. deprecated:: 1.0`` in prose — the "1"
        string is a substring of "1.0" so a naive ``in`` check would cause a
        false positive.

        """

        @deprecated(target=target_val, deprecated_in="1", update_docstring=True, docstring_style="rst")
        def some_func() -> None:
            """Summary.

            See also .. deprecated:: 1.0 handling.

            """

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
    """Verify each docstring style alias produces the correct notice format.

    The inline ``@deprecated`` decorators in this class are parametrize-coupled
    (``docstring_style=style`` resolves from the parametrize fixture), so they cannot be
    moved to :mod:`tests.collection_deprecate` without losing the per-case configuration.
    This is one of the AGENTS.md three-layer-rule exceptions: a decorator whose config
    depends on the parametrize value must be defined inside the test method itself.
    """

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


class TestNormalizeTargetInvalidInputs:
    """_normalize_target passes unrecognised non-class values through unchanged.

    The inline ``@deprecated`` decorator in :meth:`test_invalid_target_source_body_runs`
    is parametrize-coupled (``target=bad_target`` resolves from the parametrize fixture),
    so it cannot be moved to :mod:`tests.collection_deprecate` — one wrapper-per-bad-value
    would require either four near-identical fixtures or a factory that obscures the test
    intent.  This is the AGENTS.md three-layer-rule exception for parametrize-coupled
    decorators.
    """

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

            @deprecated(target=cast(Any, bad_target), deprecated_in="1.2", remove_in="2.0")
            def fn(x: int) -> int:
                return x + 1

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = fn(4)

        assert result == 5


class TestEmptyVersionGuardOnFunctions:
    """@deprecated() on a function with no version strings emits UserWarning at decoration time.

    Mirrors the proxy-side coverage in tests/unittests/test_proxy.py so the function form of
    @deprecated is held to the same contract: a single UserWarning when both ``deprecated_in``
    and ``remove_in`` are absent, suppressed when ``stream=None``.
    """

    def test_function_empty_versions_warns_once(self) -> None:
        """@deprecated() on a function with no version strings emits exactly one UserWarning."""
        with pytest.warns(UserWarning, match=r"no `deprecated_in` set") as caught:

            @deprecated()
            def _fn_no_versions() -> None:
                """Source function with no version metadata supplied."""

        user_warnings = [w for w in caught.list if issubclass(w.category, UserWarning)]
        assert len(user_warnings) == 1

    def test_function_empty_versions_stream_none_silent(self) -> None:
        """@deprecated(stream=None) on a function with no version strings emits no UserWarning."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            @deprecated(stream=None)
            def _fn_no_versions_silent() -> None:
                """Source function with stream=None — guard must stay silent."""

        assert not caught


class TestEmptyVersionGuardOnClasses:
    """@deprecated() on a class with no version strings emits exactly one empty-version guard warning.

    When ``@deprecated`` is applied to a class, ``packing()`` delegates to ``deprecated_class()``.
    The empty-version guard must fire at the proxy layer only — duplicating it inside
    ``packing()`` would surface two UserWarnings for a single decoration. The inline class
    fixtures here are mechanical one-offs per the AGENTS.md test-three-layer exception.
    """

    def test_class_empty_versions_warns_once(self) -> None:
        """@deprecated() applied to a class with no version strings emits exactly one empty-version guard warning."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            @deprecated()
            class _OldClassNoVersions:
                """Source class with no version metadata supplied."""

        user_warnings = [
            w for w in caught if issubclass(w.category, UserWarning) and "no `deprecated_in` set" in str(w.message)
        ]
        assert len(user_warnings) == 1

    def test_class_empty_versions_stream_none_silent(self) -> None:
        """@deprecated(stream=None) applied to a class with no version strings emits no UserWarning."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            @deprecated(stream=None)
            class _OldClassNoVersionsSilent:
                """Source class with stream=None — guard must stay silent."""

        user_warnings = [w for w in caught if issubclass(w.category, UserWarning)]
        assert not user_warnings


class TestEmptyVersionGuardSymmetry:
    """Guard fires for all target shapes when deprecated_in and remove_in are absent (F1b).

    The inline ``@deprecated`` decorators in this class test *decoration-time* behavior —
    each scenario asserts that ``UserWarning`` fires (or stays silent) at the moment the
    decorator is applied, captured inside a ``with pytest.warns(...)`` / ``catch_warnings``
    block.  Moving the wrappers to :mod:`tests.collection_deprecate` would fire the guard
    warning at module import time, outside any catch context, defeating the test.  This is
    the AGENTS.md three-layer-rule exception for guard tests.
    """

    def test_guard_fires_for_callable_target(self) -> None:
        """@deprecated(target=<callable>) with no versions emits UserWarning at decoration time."""

        def new_fn() -> None:
            pass

        with pytest.warns(UserWarning, match="no `deprecated_in` set"):

            @deprecated(target=new_fn)
            def old_fn() -> None:
                pass

    def test_guard_fires_for_args_remap_target(self) -> None:
        """@deprecated(target=ARGS_REMAP) with no versions emits UserWarning at decoration time."""
        with pytest.warns(UserWarning, match="no `deprecated_in` set"):

            @deprecated(target=TargetMode.ARGS_REMAP, args_mapping={"old": "new"})
            def old_fn(old: int = 0, new: int = 0) -> int:
                return new

    def test_guard_silent_when_stream_none(self) -> None:
        """@deprecated(target=<callable>, stream=None) with no versions does not emit UserWarning."""

        def new_fn() -> None:
            pass

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            @deprecated(target=new_fn, stream=None)
            def old_fn() -> None:
                pass

        assert not [w for w in caught if issubclass(w.category, UserWarning)]

    def test_guard_fires_when_remove_in_set_but_deprecated_in_absent(self) -> None:
        """@deprecated(remove_in='2.0') with no deprecated_in still emits the empty-version UserWarning."""

        def new_fn() -> None:
            pass

        with pytest.warns(UserWarning, match="no `deprecated_in` set") as caught:

            @deprecated(target=new_fn, remove_in="2.0")
            def old_fn() -> None:
                pass

        user_warnings = [w for w in caught.list if issubclass(w.category, UserWarning)]
        assert len(user_warnings) == 1

    def test_guard_silent_when_template_msg_provided(self) -> None:
        """@deprecated with template_mgs and no deprecated_in does not emit the empty-version UserWarning."""

        def new_fn() -> None:
            pass

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            @deprecated(target=new_fn, template_mgs="%(source_name)s is gone, use new_fn.")
            def old_fn_notify() -> None:
                pass

            @deprecated(
                target=TargetMode.ARGS_REMAP,
                args_mapping={"a": "b"},
                template_mgs="%(source_name)s arg 'a' renamed.",
            )
            def old_fn_remap(a: int = 0, b: int = 0) -> int:
                return b

        assert not [w for w in caught if issubclass(w.category, UserWarning)]


class TestPEP702StackingRegression:
    """Stacking ``typing_extensions.deprecated`` outside ``@deprecated`` no longer crashes (B1a).

    PEP 702 ``typing_extensions.deprecated`` overwrites the inner wrapper's ``__deprecated__`` attribute with the
    message string. Before the fix, ``wrapped_fn`` re-read that attribute at call time and crashed with
    ``AttributeError: 'str' object has no attribute 'misconfigured'``. The fix captures the ``DeprecationConfig``
    instance in a closure variable so the call path survives arbitrary outer decorators rewriting ``__deprecated__``.

    """

    def test_pep702_stacked_call_does_not_crash(self) -> None:
        """Stacked PEP 702 + pyDeprecate wrapper forwards the call and returns the target's result."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = pep702_stacked(1)
        assert result == 2

    def test_pep702_stacked_emits_pep702_deprecation_warning(self) -> None:
        """Outer ``typing_extensions.deprecated`` emits its DeprecationWarning at call time."""
        with pytest.warns(DeprecationWarning, match="use `pep702_target`"):
            pep702_stacked(2)

    def test_pep702_stacked_emits_pydeprecate_warning_on_first_call(self) -> None:
        """Inner ``@deprecated`` still emits its FutureWarning naming the target.

        Uses a freshly-built wrapper so the pyDeprecate ``_state.warned_calls`` counter is zero — the module-level
        ``pep702_stacked`` fixture may already have warned in earlier tests under ``num_warns=1``.

        """
        from tests.collection_targets import pep702_target

        inner = deprecated(target=pep702_target, deprecated_in="0.8", remove_in="1.0")(lambda x: x)
        stacked = typing_extensions.deprecated("use `pep702_target`")(inner)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = stacked(3)

        future_warnings = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert future_warnings, "expected at least one FutureWarning from pyDeprecate"
        assert any("pep702_target" in str(w.message) for w in future_warnings)
        assert result == 6


class TestTemplateMgsValidation:
    """A malformed ``template_mgs`` is detected at decoration time, not at first call (B6)."""

    def test_unknown_placeholder_raises_at_decoration(self) -> None:
        """An unknown ``%(...)s`` key raises ``ValueError`` when ``@deprecated`` is applied — before any call."""
        from tests.collection_targets import base_sum_kwargs

        with pytest.raises(ValueError, match="Invalid template_mgs"):
            deprecated(
                target=base_sum_kwargs,
                deprecated_in="0.8",
                remove_in="1.0",
                template_mgs="bad %(unknown_key)s",
            )(base_sum_kwargs)

    def test_valid_template_accepted_at_decoration(self) -> None:
        """A template using only documented placeholders is accepted at decoration time."""
        from tests.collection_targets import base_sum_kwargs

        # Must not raise — covers happy path of the probe.
        wrapper = deprecated(
            target=base_sum_kwargs,
            deprecated_in="0.8",
            remove_in="1.0",
            template_mgs="`%(source_name)s` -> `%(target_name)s` since v%(deprecated_in)s",
        )(base_sum_kwargs)
        assert callable(wrapper)


class TestStackedCallableTargetGuard:
    """Stacking ``@deprecated(target=fn_a)`` over ``@deprecated(target=fn_b)`` warns at decoration time (B4).

    Callable-over-callable stacking silently raises ``TypeError`` at the first call because the inner wrapper's
    signature does not match the outer target's remapped kwargs. The guard surfaces this misconfiguration at decoration
    time so authors catch it without exercising the call path.

    """

    def test_stacked_callable_targets_warn_at_decoration(self) -> None:
        """Decorating a callable-target wrapper with another callable target emits ``UserWarning``."""
        from tests.collection_targets import stacked_inner_target, stacked_outer_target

        inner = deprecated(target=stacked_inner_target, deprecated_in="0.8", remove_in="1.0")(stacked_outer_target)
        with pytest.warns(UserWarning, match="callable target stacked"):
            deprecated(target=stacked_outer_target, deprecated_in="0.8", remove_in="1.0")(inner)


class TestStackingGuards:
    """Decoration-time ``UserWarning`` for every unsupported stacking combination.

    Each guard surfaces a misconfiguration before the first call rather than producing
    silently wrong results or raising ``TypeError`` at runtime. The callable+callable
    case is covered by ``TestStackedCallableTargetGuard``; this class covers the other
    unsupported stacking combinations exercised below.
    """

    def _make_source(self, **kwargs: Any) -> Callable[..., int]:  # noqa: ANN401
        """Return a minimal function decorated with @deprecated using the given kwargs."""

        def fn(x: int = 0) -> int:
            return x

        return deprecated(**kwargs)(fn)

    def test_callable_over_args_remap_warns(self) -> None:
        """Callable-target outer stacked over ARGS_REMAP inner emits ``UserWarning``."""
        from tests.collection_targets import stacked_outer_target

        inner = self._make_source(
            target=TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"x": "y"}
        )
        with pytest.warns(UserWarning, match="callable target stacked over.*ARGS_REMAP") as record:
            deprecated(target=stacked_outer_target, deprecated_in="2.0", remove_in="3.0")(inner)
        assert record[0].filename.endswith("test_deprecation.py")

    def test_args_remap_over_callable_warns(self) -> None:
        """ARGS_REMAP outer stacked over callable-target inner emits ``UserWarning``."""
        from tests.collection_targets import stacked_outer_target

        inner = self._make_source(target=stacked_outer_target, deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(UserWarning, match="ARGS_REMAP.*stacked over a callable") as record:
            deprecated(target=TargetMode.ARGS_REMAP, deprecated_in="2.0", remove_in="3.0", args_mapping={"x": "y"})(
                inner
            )
        assert record[0].filename.endswith("test_deprecation.py")

    def test_notify_over_notify_warns(self) -> None:
        """Duplicate NOTIFY layers emit ``UserWarning`` at decoration time."""
        inner = self._make_source(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(UserWarning, match="duplicate.*NOTIFY") as record:
            deprecated(target=TargetMode.NOTIFY, deprecated_in="2.0", remove_in="3.0")(inner)
        assert record[0].filename.endswith("test_deprecation.py")

    def test_notify_over_args_remap_warns_with_order_hint(self) -> None:
        """NOTIFY outer + ARGS_REMAP inner (wrong order) emits ``UserWarning`` with order hint."""
        inner = self._make_source(
            target=TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"x": "y"}
        )
        with pytest.warns(UserWarning, match="Reverse the decorator order") as record:
            deprecated(target=TargetMode.NOTIFY, deprecated_in="2.0", remove_in="3.0")(inner)
        assert record[0].filename.endswith("test_deprecation.py")

    def test_callable_over_notify_warns(self) -> None:
        """Callable-target outer stacked over NOTIFY inner emits ``UserWarning``."""
        from tests.collection_targets import stacked_outer_target

        inner = self._make_source(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(UserWarning, match="callable target stacked over.*NOTIFY") as record:
            deprecated(target=stacked_outer_target, deprecated_in="2.0", remove_in="3.0")(inner)
        assert record[0].filename.endswith("test_deprecation.py")

    def test_args_remap_over_args_remap_does_not_warn(self) -> None:
        """Supported ARGS_REMAP+ARGS_REMAP stacking must not emit any UserWarning."""
        inner = self._make_source(
            target=TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"x": "y"}
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            deprecated(target=TargetMode.ARGS_REMAP, deprecated_in="2.0", remove_in="3.0", args_mapping={"y": "z"})(
                inner
            )
        assert not [w for w in caught if issubclass(w.category, UserWarning)]

    def test_non_stacked_args_remap_new_arg_silent(self) -> None:
        """Regression: non-stacked ARGS_REMAP (_source_is_stacked=False) is silent when new arg used.

        The early-return guard includes ``not _source_is_stacked``.  Verifies this condition still
        allows a non-stacked ARGS_REMAP function to short-circuit with no warning when the caller
        already uses the new argument name.
        """
        fn = self._make_source(
            target=TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"old": "x"}
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fn(x=5)
        assert not [w for w in caught if issubclass(w.category, FutureWarning)]


class TestStackedArgsRemapNotify:
    """Behaviour of the supported ARGS_REMAP-outer + NOTIFY-inner lifecycle stacking.

    Pattern: ``@deprecated(ARGS_REMAP, ...)`` on top, ``@deprecated(NOTIFY, ...)`` below.
    This matches the lifecycle where arguments are renamed in an earlier release and the
    whole function is deprecated in a later one.  Both warning layers must fire
    independently; the inner NOTIFY must run even when no deprecated argument is present.
    """

    def _make_fresh(self) -> Callable[..., float]:
        """Return a fresh stacked fixture each time to avoid num_warns counter exhaustion."""
        return make_depr_compute_power_stacked()

    def test_old_arg_fires_two_warnings(self) -> None:
        """Calling with the old arg name raises both the arg-rename and the function-deprecated warning."""
        fn = self._make_fresh()
        with pytest.warns(FutureWarning) as record:
            result = fn(2, factor=3)
        assert result == 8.0
        assert len(record) == 2

    def test_new_arg_fires_only_notify(self) -> None:
        """Calling with the new arg name raises only the function-deprecated (NOTIFY) warning."""
        fn = self._make_fresh()
        with pytest.warns(FutureWarning) as record:
            result = fn(2, scale=3)
        assert result == 8.0
        assert len(record) == 1

    def test_no_deprecated_args_fires_only_notify(self) -> None:
        """Calling with no arguments at all still fires the NOTIFY warning."""
        fn = self._make_fresh()
        with pytest.warns(FutureWarning) as record:
            result = fn(2)
        assert result == 2.0
        assert len(record) == 1

    def test_positional_call_fires_two_warnings(self) -> None:
        """Positional call mapping to the old arg position fires both warnings."""
        fn = self._make_fresh()
        # positional: base=2, factor=3 (old positional slot)
        with pytest.warns(FutureWarning) as record:
            result = fn(2, 3)
        assert result == 8.0
        assert len(record) == 2

    def test_counter_exhausted_fires_no_warnings_on_repeat(self) -> None:
        """Second call after counter exhaustion emits no FutureWarning."""
        fn = self._make_fresh()
        with pytest.warns(FutureWarning):
            fn(2, factor=3)  # exhausts both layer counters
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fn(2, factor=3)
        assert not [w for w in caught if issubclass(w.category, FutureWarning)]

    def test_args_extra_flows_through_notify_layer(self) -> None:
        """args_extra on the outer ARGS_REMAP layer reaches the target via the inner NOTIFY layer.

        Verifies that ``args_extra`` set on the outer ``ARGS_REMAP`` decorator is injected before
        the call is handed off to the inner ``NOTIFY`` wrapper, so the final function receives the
        extra kwargs correctly.
        """
        fn = make_depr_args_remap_notify_with_extra()
        with pytest.warns(FutureWarning) as record:
            result = fn(factor=3.0)
        assert result == 8.0
        assert len(record) == 2


class TestStackedNotifyCallable:
    """Call-time behaviour of the supported NOTIFY-outer + callable-target-inner stacking.

    Pattern: ``@deprecated(TargetMode.NOTIFY, ...)`` on top, ``@deprecated(target=<fn>, ...)``
    below.  The outer NOTIFY warns callers the function is going away; the inner callable-target
    layer warns and forwards to the final function.  Both ``FutureWarning`` instances must fire
    independently on every call until their counters are exhausted.
    """

    def _make_fresh(self) -> Callable[..., float]:
        """Return a fresh stacked fixture each time to avoid num_warns counter exhaustion."""
        return make_depr_notify_callable_stacked()

    def test_call_fires_two_warnings(self) -> None:
        """Calling the stacked wrapper emits both the NOTIFY and callable-target FutureWarnings."""
        fn = self._make_fresh()
        with pytest.warns(FutureWarning) as record:
            result = fn(2.0, scale=3.0)
        assert result == 8.0
        assert len(record) == 2

    def test_result_correctly_forwarded_to_target(self) -> None:
        """The callable-target inner layer correctly forwards the call to the final function."""
        fn = self._make_fresh()
        with pytest.warns(FutureWarning):
            result = fn(3.0, scale=2.0)
        assert result == 9.0

    def test_counter_exhausted_fires_no_warnings_on_repeat(self) -> None:
        """Second call after counter exhaustion emits no FutureWarning."""
        fn = self._make_fresh()
        with pytest.warns(FutureWarning):
            fn(2.0, scale=3.0)  # exhausts both layer counters
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fn(2.0, scale=3.0)
        assert not [w for w in caught if issubclass(w.category, FutureWarning)]


class TestDescriptorOrderAgnostic:
    """@deprecated on classmethod/staticmethod works in both decorator orders.

    Inner-deprecated order: ``@classmethod @deprecated`` (``@deprecated`` closer to ``def``).
    Outer-deprecated order: ``@deprecated @classmethod`` (``@deprecated`` outermost).
    Transparent unwrap+rewrap preserves the descriptor type: both @classmethod orders produce
    classmethod(deprecated_wrapper), both @staticmethod orders produce staticmethod(deprecated_wrapper).
    The deprecation warning fires at call time in both cases; no UserWarning fires at decoration time.
    """

    def test_inner_deprecated_classmethod_fires_on_call(self) -> None:
        """Inner @classmethod @deprecated order: deprecation FutureWarning fires on call, descriptor preserved."""

        class _Cls:
            @classmethod
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_method(cls, x: int) -> int:
                """Old classmethod."""
                return x

        with pytest.warns(FutureWarning):
            result = _Cls.old_method(5)
        assert result == 5
        assert isinstance(_Cls.__dict__["old_method"], classmethod)

    def test_outer_deprecated_classmethod_fires_on_call(self) -> None:
        """Outer @deprecated @classmethod order: deprecation FutureWarning still fires on call, descriptor preserved."""

        class _Cls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            @classmethod
            def old_method(cls, x: int) -> int:
                """Old classmethod."""
                return x

        with pytest.warns(FutureWarning):
            result = _Cls.old_method(5)
        assert result == 5
        assert isinstance(_Cls.__dict__["old_method"], classmethod)

    def test_outer_deprecated_classmethod_no_decoration_time_warning(self) -> None:
        """Outer @deprecated @classmethod order: no UserWarning at decoration time (transparent unwrap)."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            class _Cls:
                @deprecated(deprecated_in="1.0", remove_in="2.0")
                @classmethod
                def old_method(cls, x: int) -> int:
                    """Old classmethod."""
                    return x

        assert not [w for w in caught if issubclass(w.category, UserWarning)]

    def test_inner_deprecated_staticmethod_fires_on_call(self) -> None:
        """Inner @staticmethod @deprecated order: deprecation FutureWarning fires on call, descriptor preserved."""

        class _Cls:
            @staticmethod
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_method(x: int) -> int:
                """Old staticmethod."""
                return x

        with pytest.warns(FutureWarning):
            result = _Cls.old_method(5)
        assert result == 5
        assert isinstance(_Cls.__dict__["old_method"], staticmethod)

    def test_outer_deprecated_staticmethod_fires_on_call(self) -> None:
        """Outer @deprecated @staticmethod order: deprecation FutureWarning fires on call, descriptor preserved."""

        class _Cls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            @staticmethod
            def old_method(x: int) -> int:
                """Old staticmethod."""
                return x

        with pytest.warns(FutureWarning):
            result = _Cls.old_method(5)
        assert result == 5
        assert isinstance(_Cls.__dict__["old_method"], staticmethod)

    def test_outer_deprecated_staticmethod_no_decoration_time_warning(self) -> None:
        """Outer @deprecated @staticmethod order: no UserWarning at decoration time (transparent unwrap)."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            class _Cls:
                @deprecated(deprecated_in="1.0", remove_in="2.0")
                @staticmethod
                def old_method(x: int) -> int:
                    """Old staticmethod."""
                    return x

        assert not [w for w in caught if issubclass(w.category, UserWarning)]


class TestPropertyOrderAgnostic:
    """@deprecated on property/cached_property works in both decorator orders.

    Inner-deprecated order: ``@property @deprecated`` (``@deprecated`` closer to ``def``).
    Outer-deprecated order: ``@deprecated @property`` (``@deprecated`` outermost).
    Transparent unwrap+rewrap makes both orders produce ``property(deprecated_fget)`` — functionally identical.
    The deprecation warning fires at attribute access time in both cases; no UserWarning at decoration time.
    """

    def test_inner_deprecated_property_fires_on_access(self) -> None:
        """Inner @property @deprecated order: FutureWarning fires on attribute access, descriptor preserved."""

        class _Cls:
            @property
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def value(self) -> int:
                """Old property."""
                return 42

        obj = _Cls()
        with pytest.warns(FutureWarning):
            result = obj.value
        assert result == 42
        assert isinstance(_Cls.__dict__["value"], property)

    def test_outer_deprecated_property_fires_on_access(self) -> None:
        """Outer @deprecated @property order: FutureWarning fires on attribute access, descriptor preserved."""

        class _Cls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @property
            def value(self) -> int:
                """Old property."""
                return 42

        obj = _Cls()
        with pytest.warns(FutureWarning):
            result = obj.value
        assert result == 42
        assert isinstance(_Cls.__dict__["value"], property)

    def test_outer_deprecated_property_no_decoration_time_warning(self) -> None:
        """Outer @deprecated @property order: no UserWarning at decoration time (transparent unwrap)."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            class _UnusedCls:
                @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
                @property
                def value(self) -> int:
                    """Old property."""
                    return 42

        assert not [w for w in caught if issubclass(w.category, UserWarning)]

    def test_inner_deprecated_cached_property_fires_on_access(self) -> None:
        """Inner @cached_property @deprecated order: FutureWarning fires on first access."""

        class _Cls:
            @cached_property
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def value(self) -> int:
                """Old cached_property."""
                return 42

        obj = _Cls()
        with pytest.warns(FutureWarning):
            result = obj.value
        assert result == 42
        assert isinstance(_Cls.__dict__["value"], cached_property)

    def test_outer_deprecated_cached_property_fires_on_access(self) -> None:
        """Outer @deprecated @cached_property order: FutureWarning fires on first access, descriptor preserved."""

        class _Cls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @cached_property
            def value(self) -> int:
                """Old cached_property."""
                return 42

        obj = _Cls()
        with pytest.warns(FutureWarning):
            result = obj.value
        assert result == 42
        assert isinstance(_Cls.__dict__["value"], cached_property)

    def test_outer_deprecated_cached_property_no_decoration_time_warning(self) -> None:
        """Outer @deprecated @cached_property order: no UserWarning at decoration time."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            class _UnusedCls:
                @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
                @cached_property
                def value(self) -> int:
                    """Old cached_property."""
                    return 42

        assert not [w for w in caught if issubclass(w.category, UserWarning)]

    def test_property_setter_fires_warning(self) -> None:
        """Outer @deprecated on property with fset: FutureWarning fires on attribute assignment."""

        def _get_value(self: Any) -> int:
            """Old property."""
            return self._value

        def _set_value(self: Any, new_value: int) -> None:
            self._value = new_value

        wrapped = deprecated(deprecated_in="1.0", remove_in="2.0")(property(_get_value, _set_value))

        class _Cls:
            value = wrapped

            def __init__(self) -> None:
                self._value = 0

        obj = _Cls()
        with pytest.warns(FutureWarning):
            obj.value = 99
        assert obj._value == 99

    def test_property_deleter_fires_warning(self) -> None:
        """Outer @deprecated on property with fdel: FutureWarning fires on attribute deletion."""

        def _get_value(self: Any) -> Optional[int]:
            """Old property."""
            return self._value

        def _del_value(self: Any) -> None:
            self._value = None

        wrapped = deprecated(deprecated_in="1.0", remove_in="2.0")(property(_get_value, None, _del_value))

        class _Cls:
            value = wrapped

            def __init__(self) -> None:
                self._value: Optional[int] = 42

        obj = _Cls()
        with pytest.warns(FutureWarning):
            del obj.value
        assert obj._value is None

    def test_property_setter_only_fires_warning(self) -> None:
        """Setter-only property (fget is None): FutureWarning fires on assignment via wrapped fset."""

        def _set_value(self: Any, new_value: int) -> None:
            self._value = new_value

        wrapped_setter = deprecated(deprecated_in="1.0", remove_in="2.0")(property(None, _set_value))

        class _Cls:
            value = wrapped_setter

            def __init__(self) -> None:
                self._value = 0

        obj = _Cls()
        with pytest.warns(FutureWarning):
            obj.value = 7
        assert obj._value == 7

    def test_chain_style_setter_fires_warning(self) -> None:
        """Chain-style ``@value.setter`` after outer ``@deprecated @property``: FutureWarning fires on read AND write.

        Validates the ``_DeprecatedProperty.setter`` override: built-in ``property.setter`` rebuilds a
        plain ``property``, losing the deprecation wrap; the subclass re-wraps the new accessor with the
        same packing config so the warning fires on both attribute read and attribute write.
        """

        class _Cls:
            def __init__(self) -> None:
                self._value: int = 0

            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @property
            def value(self) -> int:
                """Old chain-style property."""
                return self._value

            @value.setter  # type: ignore[no-redef, prop-decorator]
            def value(self, v: int) -> None:
                self._value = v

        obj = _Cls()
        with pytest.warns(FutureWarning):
            result = obj.value
        assert result == 0
        with pytest.warns(FutureWarning):
            obj.value = 99
        assert obj._value == 99

    def test_chain_style_deleter_fires_warning(self) -> None:
        """Chain-style ``@value.deleter`` after outer ``@deprecated @property``: FutureWarning fires on ``del``.

        Validates ``_DeprecatedProperty.deleter``: ensures the freshly-supplied ``fdel`` is wrapped with
        the same packing closure so attribute deletion still emits the deprecation warning.
        """

        class _Cls:
            def __init__(self) -> None:
                self._value: Optional[int] = 42

            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @property
            def value(self) -> Optional[int]:
                """Old chain-style property."""
                return self._value

            @value.deleter  # type: ignore[no-redef, prop-decorator]
            def value(self) -> None:
                self._value = None

        obj = _Cls()
        with pytest.warns(FutureWarning):
            del obj.value
        assert obj._value is None
