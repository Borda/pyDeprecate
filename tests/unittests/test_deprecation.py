"""Unit tests for private helpers in deprecate.deprecation."""

import asyncio
import inspect
import sys
import warnings
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Union, cast
from unittest.mock import MagicMock

import pytest
import typing_extensions

from deprecate import TargetMode, assert_no_warnings, deprecated, void
from deprecate._types import DeprecationConfig, _DeprecatedCallable
from deprecate.deprecation import (
    POSITIONAL_OR_KEYWORD,
    _find_class_body_qualname,
    _get_positional_params,
    _normalize_target,
    _precompute_target_facts,
    _prepare_target_call,
    _raise_warn,
    _raise_warn_arguments,
    _raise_warn_callable,
    _reject_bare_decorator,
    _update_kwargs_with_args,
    _update_kwargs_with_defaults,
    _validate_template_mgs,
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
    OldPositionalOnlyClass,
    OldSelfOnlyClass,
    StaticGuardNewClass,
    StaticGuardOldClass,
    dep_async_cycle_fn_a,
    dep_async_cycle_fn_b,
    dep_async_non_cycle_old_fn,
    dep_cycle_fn_a,
    dep_cycle_fn_b,
    dep_fib_callable,
    dep_fib_notify,
    dep_fib_remap,
    dep_fib_silent,
    dep_non_cycle_old_fn,
    deprecated_args_remap_positional_only_source,
    deprecated_async_args_remap_positional_only_source,
    deprecated_async_gapped_positional_only_source,
    deprecated_async_notify_positional_only_source,
    deprecated_async_positional_only_source,
    deprecated_gapped_positional_only_first_source,
    deprecated_gapped_positional_only_full_source,
    deprecated_gapped_positional_only_source,
    deprecated_notify_positional_only_source,
    deprecated_positional_only_source,
    deprecated_positional_only_stream_none,
    deprecated_positional_only_two_params_source,
    deprecated_positional_only_with_args_mapping_source,
    fn_shared_default,
    make_depr_args_remap_notify_with_extra,
    make_depr_compute_power_stacked,
    make_depr_notify_callable_stacked,
    make_deprecated_positional_only_num_warns_one,
    pep702_stacked,
)
from tests.collection_targets import (
    KeywordCallTarget,
    base_sum_kwargs,
    call_signature_source,
    pep702_target,
    positional_only_target,
    positional_only_two_params_target,
    stacked_inner_target,
    stacked_outer_target,
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

    def test_does_not_raise_for_cross_class_staticmethod_target(self) -> None:
        """A deprecated staticmethod forwarding to a staticmethod on another class decorates without raising.

        CORE-6 regression: a staticmethod receives no ``self``, so the cross-class guard's rationale ("self
        would carry the wrong type") does not apply.  ``StaticGuardOldClass.compute`` forwards to
        ``StaticGuardNewClass.compute`` — a *different* class — and must decorate at import time rather than
        raising the cross-class ``TypeError``.  The class object being importable already proves decoration
        succeeded.
        """
        assert callable(StaticGuardOldClass.compute)
        assert StaticGuardNewClass.compute(4) == 12

    def test_cross_class_staticmethod_forwards_and_warns(self) -> None:
        """The deprecated cross-class staticmethod forwards to the target and emits a FutureWarning.

        Beyond decorating cleanly, calling the deprecated staticmethod must actually run the replacement:
        ``StaticGuardOldClass.compute(4)`` returns ``StaticGuardNewClass.compute(4)`` (``4 * 3 == 12``) and
        raises the deprecation ``FutureWarning`` on the way.
        """
        with pytest.warns(FutureWarning):
            result = StaticGuardOldClass.compute(4)
        assert result == 12

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
                target=TargetMode.ARGS_REMAP, args_mapping={"a": "b"}, template_mgs="%(source_name)s arg 'a' renamed."
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


class TestStackedCallableTargetGuard:
    """Stacking ``@deprecated(target=fn_a)`` over ``@deprecated(target=fn_b)`` warns at decoration time (B4).

    Callable-over-callable stacking silently raises ``TypeError`` at the first call because the inner wrapper's
    signature does not match the outer target's remapped kwargs. The guard surfaces this misconfiguration at decoration
    time so authors catch it without exercising the call path.

    """

    def test_stacked_callable_targets_warn_at_decoration(self) -> None:
        """Decorating a callable-target wrapper with another callable target emits ``UserWarning``."""
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
        inner = self._make_source(
            target=TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"x": "y"}
        )
        with pytest.warns(UserWarning, match="callable target stacked over.*ARGS_REMAP") as record:
            deprecated(target=stacked_outer_target, deprecated_in="2.0", remove_in="3.0")(inner)
        assert record[0].filename.endswith("test_deprecation.py")

    def test_args_remap_over_callable_warns(self) -> None:
        """ARGS_REMAP outer stacked over callable-target inner emits ``UserWarning``."""
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


class TestPositionalOnlyTarget:
    """``@deprecated`` with a callable target that declares POSITIONAL_ONLY parameters.

    When a target function declares ``def fn(x, /): ...``, the wrapper must not
    blindly call ``target(**kwargs)`` — Python raises ``TypeError`` because ``x``
    cannot be passed as a keyword argument.  The decorator should detect this at
    decoration time (``UserWarning``) and split the dispatch at call time so
    positional-only params are forwarded positionally and remaining params as kwargs.
    """

    def test_decoration_emits_user_warning(self) -> None:
        """Applying @deprecated to a callable target with POSITIONAL_ONLY params warns at decoration time.

        A developer deprecating ``old_fn`` in favour of ``positional_only_target`` (whose
        first parameter ``x`` is declared positional-only) should receive a ``UserWarning``
        at the ``@deprecated(...)`` line — before any call is made — so the incompatibility
        is surfaced early rather than crashing on first use.
        """
        with pytest.warns(UserWarning, match=r"POSITIONAL_ONLY"):
            deprecated(target=positional_only_target, deprecated_in="1.0", remove_in="2.0")(lambda x, y=0: None)

    @pytest.mark.parametrize(
        ("call_args", "call_kwargs", "expected"),
        [
            pytest.param((5,), {}, 5, id="positional-arg"),
            pytest.param((), {"x": 5}, 5, id="keyword-arg"),
            pytest.param((3,), {"y": 4}, 7, id="both-args"),
        ],
    )
    def test_call_shape_forwards_correctly(self, call_args: tuple, call_kwargs: dict, expected: int) -> None:
        """Call-shape variations on a POSITIONAL_ONLY target all forward correctly.

        Verifies three call shapes that a user of ``deprecated_positional_only_source``
        might write — positional arg, keyword arg, and a mix — to ensure split dispatch
        handles each without ``TypeError`` or incorrect values.
        """
        with pytest.warns(FutureWarning):
            result = deprecated_positional_only_source(*call_args, **call_kwargs)
        assert result == expected

    def test_future_warning_fires_on_call(self) -> None:
        """The standard FutureWarning is still emitted on the positional-only dispatch path.

        The positional-only split must not suppress the deprecation warning —
        callers should still see ``FutureWarning`` so they know to migrate.
        """
        with pytest.warns(FutureWarning, match=r"deprecated_positional_only_source"):
            deprecated_positional_only_source(1)

    @pytest.mark.asyncio
    async def test_async_dispatch_forwards_positional_correctly(self) -> None:
        """The async dispatch path forwards POSITIONAL_ONLY params without TypeError.

        An async deprecated wrapper targeting an async function with a positional-only
        parameter must not call ``await target_func(**resolved_kwargs)`` — that raises
        ``TypeError``.  The split-dispatch path must fire in ``async_wrapped_fn`` so the
        async call succeeds and returns the correct value.
        """
        with pytest.warns(FutureWarning):
            result = await deprecated_async_positional_only_source(7)
        assert result == 7

    def test_two_positional_only_params_forwarded_in_order(self) -> None:
        """Two POSITIONAL_ONLY params are forwarded in declaration order.

        When ``positional_only_two_params_target`` is the target and both ``a`` and ``b``
        are positional-only, the split-dispatch must iterate parameters in declaration order
        (``a`` first, then ``b``) so ``deprecated_positional_only_two_params_source(10, 3)``
        returns the same value as ``positional_only_two_params_target(10, 3)`` — not a
        swapped or alphabetically-sorted dispatch.
        """
        with pytest.warns(FutureWarning):
            result = deprecated_positional_only_two_params_source(10, 3)
        assert result == positional_only_two_params_target(10, 3)
        assert result == 13

    def test_args_mapping_renames_before_positional_split(self) -> None:
        """args_mapping remap is applied before the POSITIONAL_ONLY split.

        A user calling ``deprecated_positional_only_with_args_mapping_source(old_x=5)``
        passes the deprecated argument name ``old_x``.  The wrapper must rename it to ``x``
        via ``args_mapping`` first, then split ``x`` out of ``resolved_kwargs`` and forward
        it positionally to ``positional_only_target``.  If the rename and split are reordered,
        ``x`` will not be in ``resolved_kwargs`` at split time and the call will fail with
        ``TypeError``.
        """
        with pytest.warns(FutureWarning):
            result = deprecated_positional_only_with_args_mapping_source(old_x=5)
        assert result == 5

    def test_stream_none_suppresses_warnings_but_call_still_forwards(self) -> None:
        """stream=None suppresses all warnings but split dispatch still forwards correctly.

        When ``@deprecated`` is configured with ``stream=None``, no ``UserWarning`` fires
        at decoration time and no ``FutureWarning`` fires at call time.  The underlying
        split dispatch must still execute so ``positional_only_target`` is called with ``x``
        as a positional arg (not as a kwarg), returning the correct value.
        ``deprecated_positional_only_stream_none`` is defined in ``collection_deprecate.py``
        with ``stream=None`` so no warnings are emitted at decoration time or call time.
        """
        result = deprecated_positional_only_stream_none(5)
        assert result == 5

    def test_call_succeeds_after_warning_quota_exhausted(self) -> None:
        """Split dispatch still forwards correctly after the FutureWarning quota is exhausted.

        With ``num_warns=1``, the first call emits ``FutureWarning``; the second call
        must still forward ``x`` positionally (the split dispatch must not be gated on
        the warning being emitted).  A caller silently migrating after the quota exhausts
        should not receive ``TypeError``.
        ``deprecated_positional_only_num_warns_one`` is defined in ``collection_deprecate.py``
        with ``num_warns=1`` so the warning fires exactly once.
        """
        quota_fn = make_deprecated_positional_only_num_warns_one()
        with pytest.warns(FutureWarning):
            result1 = quota_fn(5)
        assert result1 == 5

        result2 = quota_fn(10)  # no warning — quota exhausted
        assert result2 == 10

    def test_constructor_forwarding_positional_only_succeeds(self) -> None:
        """Constructor-forwarding path sets attribute correctly when target __init__ has a POSITIONAL_ONLY param.

        When ``@deprecated`` is applied to ``OldPositionalOnlyClass.__init__`` with
        ``target=PositionalOnlyTarget``, ``_normalize_target`` maps the class target to
        ``PositionalOnlyTarget.__init__`` (unbound).  The dispatch must include ``self`` in
        ``pos_args`` before ``new_val`` so that the unbound call
        ``PositionalOnlyTarget.__init__(instance, 5)`` succeeds — without the fix, ``5`` lands
        in the ``self`` slot positionally and ``self`` is also passed as a kwarg, raising
        ``TypeError: positional-only arguments passed as keyword arguments: 'self'``.
        """
        with pytest.warns(FutureWarning):
            obj = OldPositionalOnlyClass(5)
        assert obj.new_val == 5

    def test_self_only_positional_only_constructor_succeeds(self) -> None:
        """Constructor does not raise when self is the only POSITIONAL_ONLY target param.

        When ``@deprecated`` is applied to ``OldSelfOnlyClass.__init__`` with
        ``target=SelfOnlyPositionalOnlyTarget``, the target's only POSITIONAL_ONLY param
        is ``self``.  Before the fix, ``target_positional_only`` excluded ``self`` and was
        therefore an empty frozenset — the split-dispatch gate never fired, so the dispatcher
        called ``target_func(**{'self': instance})``, raising ``TypeError: positional-only
        arguments passed as keyword arguments: 'self'``.  The fix includes ``self``/``cls``
        in the stored set so the gate fires and the instance is forwarded positionally.
        """
        with pytest.warns(FutureWarning):
            obj = OldSelfOnlyClass()
        assert isinstance(obj, OldSelfOnlyClass)


class TestPositionalOnlySource:
    """``@deprecated`` on a *source* whose own signature declares POSITIONAL_ONLY parameters.

    NOTIFY (default) and ARGS_REMAP modes execute the source body.  The wrapper converts
    positional args to kwargs internally, so the dispatcher must split the source's
    positional-only params back out before calling ``source`` — otherwise every call in
    the default mode raises ``TypeError: got some positional-only arguments passed as
    keyword arguments``.
    """

    def test_notify_positional_call_executes_source(self) -> None:
        """Default warn-only mode executes a source with a ``/`` in its signature.

        A library author adds a plain ``@deprecated(deprecated_in=..., remove_in=...)``
        notice to an existing function that uses positional-only parameters.  Callers keep
        calling it exactly as before — the call must warn and return the source's result,
        not crash with ``TypeError`` on every invocation.
        """
        with pytest.warns(FutureWarning, match=r"deprecated_notify_positional_only_source"):
            result = deprecated_notify_positional_only_source(1)
        assert result == 3

    def test_notify_keyword_tail_preserved(self) -> None:
        """Keyword arguments after the ``/`` still reach the source alongside the positional split."""
        with pytest.warns(FutureWarning):
            result = deprecated_notify_positional_only_source(1, b=10)
        assert result == 11

    def test_args_remap_old_name_remapped(self) -> None:
        """ARGS_REMAP renames the deprecated kwarg and executes the positional-only source body.

        A caller still using the deprecated ``old_flag`` name on a function whose leading
        parameter is positional-only must get the rename warning and the correct result —
        the remapped kwargs dict must not swallow the positional-only ``a``.
        """
        with pytest.warns(FutureWarning, match=r"old_flag"):
            result = deprecated_args_remap_positional_only_source(1, old_flag=5)
        assert result == 6

    def test_args_remap_migrated_caller_short_circuits(self) -> None:
        """A migrated caller using the new kwarg name gets no warning and a working call.

        The migrated-caller fast path (short-circuit) also calls ``source(**kwargs)``
        internally, so it needs the same positional-only split as the warning path.
        """
        with assert_no_warnings(FutureWarning):
            result = deprecated_args_remap_positional_only_source(1, new_flag=5)
        assert result == 6

    @pytest.mark.asyncio
    async def test_async_notify_positional_call_executes_source(self) -> None:
        """The async dispatch twin executes an async source with a POSITIONAL_ONLY param.

        An async API deprecated with the plain notice must keep serving awaited calls;
        ``_invoke_async`` must split the positional-only params exactly like the sync path.
        """
        with pytest.warns(FutureWarning):
            result = await deprecated_async_notify_positional_only_source(1, b=10)
        assert result == 11

    @pytest.mark.asyncio
    async def test_async_args_remap_old_name_remapped(self) -> None:
        """The async ARGS_REMAP path renames the deprecated kwarg on a positional-only source."""
        with pytest.warns(FutureWarning, match=r"old_flag"):
            result = await deprecated_async_args_remap_positional_only_source(1, old_flag=5)
        assert result == 6


class TestGappedPositionalOnlyForwarding:
    """Forwarding to a target whose POSITIONAL_ONLY params are only partially supplied.

    ``gapped_positional_only_target(a=1, b=2, /, c=3)`` has defaulted positional-only
    params.  A source that supplies ``b`` but not ``a`` leaves a gap: positional binding
    would silently slide ``b``'s value into ``a``'s slot.  The split dispatch must raise
    ``TypeError`` instead of misbinding.
    """

    def test_gap_with_later_value_raises_type_error(self) -> None:
        """Supplying a later positional-only param while an earlier one is absent raises.

        A migration wrapper forwards only ``b`` to a target where ``a`` (also positional-only)
        precedes it.  Before the fix, ``b``'s value was silently bound to ``a`` — wrong data
        on every call.  The wrapper must now fail loudly at the call site.
        """
        with pytest.warns(FutureWarning), pytest.raises(TypeError, match=r"`a` was not supplied"):
            deprecated_gapped_positional_only_source()

    def test_full_prefix_forwards_in_order(self) -> None:
        """Supplying every positional-only param forwards each value to its own slot."""
        with pytest.warns(FutureWarning):
            result = deprecated_gapped_positional_only_full_source(9, 8)
        assert result == {"a": 9, "b": 8, "c": 3}

    def test_trailing_gap_uses_target_defaults(self) -> None:
        """A gap with no later positional-only value present falls back to the target defaults.

        A source declaring only ``a`` (the first positional-only target param) leaves a
        *trailing* gap at ``b`` — safe, because no later positional-only value can slide
        into the wrong slot.  ``b`` and ``c`` keep their target-side defaults, so the call
        must succeed rather than raise.
        """
        with pytest.warns(FutureWarning):
            result = deprecated_gapped_positional_only_first_source(4)
        assert result == {"a": 4, "b": 2, "c": 3}

    @pytest.mark.asyncio
    async def test_async_gap_with_later_value_raises_type_error(self) -> None:
        """The async dispatch twin raises the same TypeError on a positional-only gap."""
        with pytest.warns(FutureWarning), pytest.raises(TypeError, match=r"`a` was not supplied"):
            await deprecated_async_gapped_positional_only_source()


class TestCycleDetection:
    """Lazy runtime cycle detection — RuntimeError before RecursionError."""

    def test_cycle_entry_via_a_raises(self) -> None:
        """Calling dep_cycle_fn_a raises RuntimeError when target chain loops back through dep_cycle_fn_b.

        dep_cycle_fn_a forwards to dep_cycle_fn_b which forwards back to dep_cycle_fn_a,
        forming a two-node cycle. The lazy cycle detector intercepts the re-entry of
        dep_cycle_fn_a and raises RuntimeError instead of letting Python hit RecursionError.
        The guard fires before the warning is emitted, so no FutureWarning is raised.
        """
        with pytest.raises(RuntimeError, match=r"Circular deprecation cycle detected.*dep_cycle_fn_a"):
            dep_cycle_fn_a(1)

    def test_cycle_entry_via_b_raises(self) -> None:
        """Calling dep_cycle_fn_b raises RuntimeError when target chain loops back through dep_cycle_fn_a.

        A maintainer renames a second deprecated symbol to point at the same cycle. When
        the entry point is dep_cycle_fn_b instead of dep_cycle_fn_a, the guard must fire
        at dep_cycle_fn_b's re-entry and name dep_cycle_fn_b in the error message, proving
        the detector is symmetric and reports the correct wrapper regardless of entry order.
        """
        with pytest.raises(RuntimeError, match=r"Circular deprecation cycle detected.*dep_cycle_fn_b"):
            dep_cycle_fn_b(1)

    def test_non_cycle_callable_target_unaffected(self) -> None:
        """A deprecated wrapper with a non-cycling callable target is unaffected by cycle detection.

        When a deprecated wrapper's target is a plain (non-deprecated) function, the cycle
        detection set is populated then cleaned up via the finally-block without raising.
        Repeated calls must not accumulate stale id entries, so the second call succeeds
        identically to the first.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            assert dep_non_cycle_old_fn(3) == 6
            assert dep_non_cycle_old_fn(3) == 6  # second call — no stale id in active set

    @pytest.mark.asyncio
    async def test_async_cycle_entry_via_a_raises(self) -> None:
        """Awaiting dep_async_cycle_fn_a raises RuntimeError when the async target chain cycles.

        An async deprecated wrapper (routed through async_wrapped_fn) that forms a
        two-node cycle with dep_async_cycle_fn_b must raise RuntimeError on re-entry,
        proving the cycle guard in the async code path fires correctly.
        """
        with pytest.raises(RuntimeError, match=r"Circular deprecation cycle detected.*dep_async_cycle_fn_a"):
            await dep_async_cycle_fn_a(1)

    @pytest.mark.asyncio
    async def test_async_cycle_entry_via_b_raises(self) -> None:
        """Awaiting dep_async_cycle_fn_b raises RuntimeError when the async target chain cycles.

        Mirror of test_async_cycle_entry_via_a_raises — the async cycle is symmetric, so
        entering from dep_async_cycle_fn_b must also trigger the guard. The error message
        must name dep_async_cycle_fn_b as the re-entered wrapper.
        """
        with pytest.raises(RuntimeError, match=r"Circular deprecation cycle detected.*dep_async_cycle_fn_b"):
            await dep_async_cycle_fn_b(1)

    @pytest.mark.asyncio
    async def test_concurrent_async_calls_no_false_positive(self) -> None:
        """Concurrent asyncio.gather calls to the same async deprecated wrapper must all succeed.

        A production service may fan out several concurrent requests to a single deprecated
        async endpoint (e.g. via asyncio.gather). Before the ContextVar fix, threading.local
        caused every overlapping call beyond the first to raise a spurious RuntimeError because
        all coroutines on the same event-loop thread shared one active-id set. With ContextVar,
        each asyncio.Task gets its own copy, so concurrent calls are fully independent.
        """
        results = await asyncio.gather(
            dep_async_non_cycle_old_fn(1), dep_async_non_cycle_old_fn(2), dep_async_non_cycle_old_fn(3)
        )
        assert results == [2, 4, 6]


class TestRecursiveDeprecation:
    """Deprecated wrappers on recursive functions — warning fires once, recursion converges."""

    @pytest.mark.parametrize(
        ("n", "expected"),
        [
            pytest.param(0, 0, id="base-zero"),
            pytest.param(1, 1, id="base-one"),
            pytest.param(6, 8, id="fib-six"),
        ],
    )
    def test_notify_recursive_returns_correct_result(self, n: int, expected: int) -> None:
        """A recursive deprecated function in NOTIFY mode computes the correct result.

        A real Fibonacci function decorated with ``@deprecated`` (NOTIFY mode) calls itself
        recursively through the wrapper for each sub-problem. The decorator must not interfere
        with the recursion — the result must equal the standard Fibonacci value.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            assert dep_fib_notify(n) == expected

    def test_notify_recursive_warns_exactly_once(self) -> None:
        """A recursive deprecated function in NOTIFY mode emits exactly one warning.

        Even though the wrapper is re-entered on every recursive call (25 total calls for
        ``fib(6)``), ``num_warns=1`` (default) tracks the count in shared mutable state so
        only the first call emits a ``FutureWarning``. Subsequent recursive calls see
        ``warned_calls >= num_warns`` and skip the warning.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            dep_fib_notify(6)
        assert len(caught) == 1
        assert caught[0].category is FutureWarning

    def test_notify_recursive_num_warns_zero_suppresses_all(self) -> None:
        """``num_warns=0`` suppresses all warnings even for a deeply recursive deprecated function.

        When the deprecation wrapper is configured with ``num_warns=0``, no warning must fire
        regardless of how many recursive re-entries occur.
        """
        with assert_no_warnings(FutureWarning):
            result = dep_fib_silent(6)
        assert result == 8

    @pytest.mark.parametrize(
        ("n", "expected"),
        [
            pytest.param(0, 0, id="base-zero"),
            pytest.param(1, 1, id="base-one"),
            pytest.param(6, 8, id="fib-six"),
        ],
    )
    def test_callable_target_self_recursive_warns_once_returns_correct_result(self, n: int, expected: int) -> None:
        """A deprecated wrapper whose target is a self-recursive function warns once and returns correctly.

        ``dep_fib_callable`` forwards to ``fib_recursive``, which recurses on itself directly
        without re-entering the deprecated wrapper. Cycle detection does not false-positive:
        ``id(source)`` is added to ``_active`` once and removed in the ``finally`` block; only
        one ``FutureWarning`` fires.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = dep_fib_callable(n)
        assert result == expected
        assert len(caught) == 1
        assert caught[0].category is FutureWarning

    def test_callable_target_repeated_calls_no_stale_state(self) -> None:
        """A second independent call succeeds with no stale ``id(source)`` entries and no new warning.

        After the first call cleans up via the ``finally`` block, the cycle-detection set must be
        empty so a second call succeeds identically.  Since ``num_warns=1`` is already satisfied
        after the first call, the second call emits no additional ``FutureWarning`` instances.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            assert dep_fib_callable(5) == 5
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert dep_fib_callable(5) == 5
        assert len(caught) == 0

    @pytest.mark.parametrize(
        ("n", "expected"),
        [
            pytest.param(0, 0, id="base-zero"),
            pytest.param(1, 1, id="base-one"),
            pytest.param(6, 8, id="fib-six"),
        ],
    )
    def test_args_remap_recursive_returns_correct_result(self, n: int, expected: int) -> None:
        """A recursive ARGS_REMAP deprecated function remaps the deprecated arg on every call and converges.

        ``dep_fib_remap`` accepts the deprecated ``n`` argument and remaps it to ``x`` on each
        recursive call. The recursion must still produce the correct Fibonacci value even though
        every level passes ``n`` (the deprecated name) and the wrapper remaps it to ``x``.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            assert dep_fib_remap(n=n) == expected

    def test_args_remap_recursive_warns_exactly_once(self) -> None:
        """A recursive ARGS_REMAP wrapper emits exactly one warning for the initial call.

        Even though the wrapper re-enters on each recursive step and applies the arg remap,
        ``num_warns=1`` (default) ensures only the first re-entry of the deprecated argument
        ``n`` triggers a ``FutureWarning``. Subsequent recursive calls see ``state.warned_args['n']``
        already satisfied and proceed silently.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            dep_fib_remap(n=6)
        assert len(caught) == 1
        assert caught[0].category is FutureWarning


class TestSharedNameDefaultForwarding:
    """A non-renamed shared parameter forwards the source's own default (the migrated-from contract).

    Review finding CORE-4 proposed that the *target*'s default should win here; that was assessed as a
    misdiagnosis — forwarding the source default is the intended, documented behaviour (``decorated_sum`` /
    ``test_functions.py::test_default`` enforce the same contract).  These tests pin it against regression.
    """

    def test_neither_supplied_forwards_source_default(self) -> None:
        """With no ``args_mapping`` and only ``x`` supplied, the source's ``level=1`` is forwarded, not target's 99.

        ``fn_shared_default`` forwards to ``fn_shared_default_target``; both declare ``level`` but with diverging
        defaults (source 1, target 99).  The source signature is the contract the caller knows, so its default
        reaches the target body.  Only the *renamed* path drops stale defaults (see ``fn_old_default``).
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = fn_shared_default(5)
        assert result == 1

    def test_explicit_value_overrides_both_defaults(self) -> None:
        """A caller-supplied ``level`` reaches the target unchanged, overriding both source and target defaults."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = fn_shared_default(5, level=3)
        assert result == 3


class TestTargetFactsPrecompute:
    """Decoration-time-cached target signature facts feed the call-time kwarg validation (CORE-7)."""

    def test_config_caches_target_signature_facts(self) -> None:
        """A callable-target wrapper stores the target's param names and var-arg flags.

        These facts previously came from an uncached ``inspect.getfullargspec`` on every forwarded call;
        caching them on the frozen config removes that per-call cost.
        """
        cfg = cast(_DeprecatedCallable, fn_shared_default).__deprecated__
        assert cfg.target_all_param_names == frozenset({"x", "level"})
        assert cfg.target_accepts_var_positional is False
        assert cfg.target_accepts_var_keyword is False

    def test_precompute_reports_var_args_and_kwargs(self) -> None:
        """`_precompute_target_facts` flags ``*args`` and ``**kwargs`` and collects every parameter name."""

        def _tgt(a: int, b: int = 2, *rest: int, **extra: int) -> None:
            """Throwaway signature carrying every parameter kind for the precompute helper."""

        names, var_pos, var_kw = _precompute_target_facts(_tgt)
        assert names == frozenset({"a", "b", "rest", "extra"})
        assert var_pos is True
        assert var_kw is True

    def test_precompute_empty_for_non_callable_target(self) -> None:
        """A :class:`TargetMode` (non-callable) target yields empty facts — the dispatcher never forwards to it."""
        names, var_pos, var_kw = _precompute_target_facts(TargetMode.NOTIFY)
        assert names == frozenset()
        assert (var_pos, var_kw) == (False, False)

    def test_repr_excludes_cache_fields(self) -> None:
        """The precomputed cache fields stay out of ``repr`` so audit output and doc examples remain stable."""
        text = repr(cast(_DeprecatedCallable, fn_shared_default).__deprecated__)
        assert "target_all_param_names" not in text
        assert "target_accepts_var_keyword" not in text

    def test_cache_fields_excluded_from_equality(self) -> None:
        """Two configs differing only in cached target facts compare equal — caches are not identity.

        The cache fields carry ``compare=False`` so stored-config equality assertions (and audit comparisons) are
        unaffected by the perf machinery.
        """
        plain = DeprecationConfig(deprecated_in="1.0", remove_in="2.0", name="x")
        with_cache = DeprecationConfig(
            deprecated_in="1.0",
            remove_in="2.0",
            name="x",
            target_all_param_names=frozenset({"a"}),
            target_accepts_var_keyword=True,
        )
        assert plain == with_cache

    def test_prepare_target_call_uses_supplied_facts(self) -> None:
        """`_prepare_target_call` validates against supplied facts and raises the curated TypeError on a bad kwarg."""

        def _tgt(a: int, b: int) -> None:
            """Two-parameter target that does not accept ``c``."""

        with pytest.raises(TypeError, match="not accepted by target"):
            _prepare_target_call(
                _tgt,
                _tgt,
                {"c": 1},
                target_arg_names=frozenset({"a", "b"}),
                accepts_var_positional=False,
                accepts_var_keyword=False,
            )


class TestBareDecoratorGuard:
    """CORE-8 — bare ``@deprecated`` (no parentheses) must fail with a guiding message."""

    def test_call_raises_typeerror_about_parentheses(self) -> None:
        """Calling a bare-decorated function raises a TypeError that names the missing parentheses.

        A user who forgets the parentheses (``@deprecated`` instead of ``@deprecated(...)``) binds the
        decorated function to ``target`` and gets ``packing`` back; the first call then arrives with the call
        argument as ``source``. The guard must name the missing-parentheses mistake rather than leak a cryptic
        ``AttributeError: 'int' object has no attribute '__name__'``.
        """

        @deprecated  # type: ignore[operator,call-overload]
        def old(x: int) -> int:
            return x

        with pytest.raises(TypeError, match="must be called with parentheses"):
            old(5)  # type: ignore[arg-type]  # bare decorator rebinds ``old`` to the packing decorator

    def test_helper_accepts_plain_callable(self) -> None:
        """The guard helper is a no-op for an ordinary callable so correct decoration is never disturbed."""

        def real(x: int) -> int:
            return x

        _reject_bare_decorator(real)  # no exception raised = a plain callable is accepted


class TestRaiseWarnStacklevel:
    """CORE-10 — stream called with ``stacklevel`` when accepted; internal TypeError propagates without double-call."""

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
    """CORE-11 — bare ``%``-conversions in ``template_mgs`` must be rejected at decoration time."""

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


class TestArgsMappingDefensiveCopy:
    """CORE-12 — the frozen config must not alias the caller's mutable ``args_mapping`` dict."""

    def test_post_decoration_mutation_ignored(self) -> None:
        """Mutating the caller's ``args_mapping`` dict after decoration does not change forwarding behavior.

        Mutating the ``args_mapping`` dict after decoration used to change forwarding behavior because the
        frozen ``DeprecationConfig`` stored the caller's dict by reference. A defensive copy makes the wrapper
        immune to later mutation of the caller-owned dict.
        """

        def new(**kwargs: int) -> dict[str, int]:
            return kwargs

        mapping: dict[str, Any] = {"old_a": "new_a"}

        @deprecated(target=new, deprecated_in="1.0", remove_in="2.0", args_mapping=mapping)
        def old(old_a: int = 1) -> dict[str, int]:
            return {"old_a": old_a}

        mapping["old_a"] = "hijacked"  # would redirect to a bogus name if the dict were aliased
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = old(old_a=5)
        assert result == {"new_a": 5}


class TestClassBodyQualnameWalk:
    """CORE-13 — the cross-class guard locates the class body via a bounded frame walk, not a fixed depth."""

    def test_finds_enclosing_class_qualname(self) -> None:
        """Called inside a class body, the walk returns that class's ``__qualname__``.

        Called from within a class body the walk returns that class's ``__qualname__`` regardless of how many
        descriptor/packing frames sit between it and the class body — the fixed ``sys._getframe(2)`` it replaces
        silently missed the class body for descriptor-decorated methods, disabling the guard for them.
        """
        captured: dict[str, str] = {}

        class Sample:
            captured["qualname"] = _find_class_body_qualname()

        _ = Sample  # reference prevents static-analysis "unused class" warnings
        assert captured["qualname"].endswith("Sample")

    def test_cross_class_guard_fires_for_descriptor_decorated_method(self) -> None:
        """Cross-class guard raises TypeError at class-definition time even when the method uses a descriptor.

        With the old fixed ``sys._getframe(2)`` approach the extra stack frames introduced by
        ``@classmethod``/``@staticmethod``/``@property`` wrapping pushed the class body out of range,
        silently disabling the cross-class guard for descriptor-decorated methods.  The bounded frame
        walk introduced in CORE-13 locates the class body regardless of intervening descriptor frames.
        """

        class OtherClass:
            def other_method(self, x: int) -> int:
                return x

        with pytest.raises(TypeError, match="cross-class method forwarding is not supported"):

            class _Owner:
                @classmethod
                @deprecated(target=OtherClass.other_method, deprecated_in="1.0", remove_in="2.0")
                def old_classmethod(cls, x: int) -> int:
                    return void(x)
