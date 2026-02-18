"""Unit tests for private helpers in deprecate.utils."""

import inspect
import warnings

from deprecate.utils import _get_signature, _get_signature_cached, _warns_repr, get_func_arguments_types_defaults, void


class TestGetSignature:
    def test_returns_signature_for_hashable_func(self) -> None:
        def my_func(x: int, y: str = "hi") -> None:
            pass

        sig = _get_signature(my_func)
        assert isinstance(sig, inspect.Signature)
        assert list(sig.parameters) == ["x", "y"]

    def test_caches_result_for_same_func(self) -> None:
        def my_func(x: int) -> None:
            pass

        assert _get_signature(my_func) is _get_signature(my_func)

    def test_unhashable_callable_falls_back_to_uncached(self) -> None:
        class UnhashableCallable:
            __hash__ = None  # type: ignore[assignment]

            def __call__(self, x: int) -> int:
                return x

        obj = UnhashableCallable()
        sig = _get_signature(obj)
        assert isinstance(sig, inspect.Signature)
        assert "x" in sig.parameters


class TestGetSignatureCached:
    def test_returns_signature(self) -> None:
        def my_func(a: int, b: str = "hello") -> None:
            pass

        sig = _get_signature_cached(my_func)
        assert isinstance(sig, inspect.Signature)
        assert list(sig.parameters) == ["a", "b"]

    def test_repeated_calls_return_same_object(self) -> None:
        def my_func(x: int) -> None:
            pass

        assert _get_signature_cached(my_func) is _get_signature_cached(my_func)


class TestWarnsRepr:
    def test_extracts_message_from_single_warning(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            warnings.warn("hello world", UserWarning)

        result = _warns_repr(caught)
        assert len(result) == 1
        assert str(result[0]) == "hello world"

    def test_empty_list_returns_empty(self) -> None:
        assert _warns_repr([]) == []

    def test_preserves_order_for_multiple_warnings(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            warnings.warn("first", UserWarning)
            warnings.warn("second", FutureWarning)

        result = _warns_repr(caught)
        assert len(result) == 2
        assert str(result[0]) == "first"
        assert str(result[1]) == "second"


class TestVoid:
    def test_returns_none_with_no_args(self) -> None:
        assert void() is None

    def test_returns_none_with_positional_args(self) -> None:
        assert void(1, "two", 3.0) is None

    def test_returns_none_with_keyword_args(self) -> None:
        assert void(x=1, label="hello") is None

    def test_returns_none_with_mixed_args(self) -> None:
        assert void(42, key="value", flag=True) is None


class TestGetFuncArgumentsTypesDefaults:
    def test_extracts_names_types_and_defaults(self) -> None:
        def my_func(x: int, y: str = "hello", z=42) -> None:
            pass

        result = get_func_arguments_types_defaults(my_func)
        assert len(result) == 3
        names = [r[0] for r in result]
        assert names == ["x", "y", "z"]
        assert result[0][1] == int
        assert result[1][2] == "hello"
        assert result[2][2] == 42

    def test_unannotated_param_has_empty_annotation(self) -> None:
        def my_func(a, b: int) -> None:
            pass

        result = get_func_arguments_types_defaults(my_func)
        assert result[0][1] is inspect.Parameter.empty

    def test_param_without_default_has_empty_default(self) -> None:
        def my_func(required: str) -> None:
            pass

        result = get_func_arguments_types_defaults(my_func)
        assert result[0][2] is inspect.Parameter.empty

    def test_no_params_returns_empty_list(self) -> None:
        def my_func() -> None:
            pass

        assert get_func_arguments_types_defaults(my_func) == []
