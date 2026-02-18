"""Unit tests for private helpers in deprecate.utils."""

import inspect
import warnings

from deprecate.utils import _get_signature, _get_signature_cached, _warns_repr, get_func_arguments_types_defaults, void


class TestGetSignature:
    """Tests for _get_signature — the LRU-cached signature fetcher."""

    def test_returns_signature_for_hashable_func(self) -> None:
        """Returns a valid Signature for a regular annotated function."""

        def my_func(x: int, y: str = "hi") -> None:
            pass

        sig = _get_signature(my_func)
        assert isinstance(sig, inspect.Signature)
        assert list(sig.parameters) == ["x", "y"]

    def test_caches_result_for_same_func(self) -> None:
        """Repeated calls return the identical Signature object, confirming LRU cache is active."""

        def my_func(x: int) -> None:
            pass

        assert _get_signature(my_func) is _get_signature(my_func)

    def test_unhashable_callable_falls_back_to_uncached(self) -> None:
        """Unhashable callables cannot enter the LRU cache; falls back to direct inspection without crashing."""

        class UnhashableCallable:
            __hash__ = None  # type: ignore[assignment]

            def __call__(self, x: int) -> int:
                return x

        obj = UnhashableCallable()
        sig = _get_signature(obj)
        assert isinstance(sig, inspect.Signature)
        assert "x" in sig.parameters


class TestGetSignatureCached:
    """Tests for _get_signature_cached — the raw LRU-cached layer."""

    def test_returns_signature(self) -> None:
        """Returns a valid Signature for a standard function."""

        def my_func(a: int, b: str = "hello") -> None:
            pass

        sig = _get_signature_cached(my_func)
        assert isinstance(sig, inspect.Signature)
        assert list(sig.parameters) == ["a", "b"]

    def test_repeated_calls_return_same_object(self) -> None:
        """Identity check confirms the LRU cache serves the same Signature object on subsequent calls."""

        def my_func(x: int) -> None:
            pass

        assert _get_signature_cached(my_func) is _get_signature_cached(my_func)


class TestWarnsRepr:
    """Tests for _warns_repr — converts caught WarningMessage records to string representations."""

    def test_extracts_message_from_single_warning(self) -> None:
        """String representation of a single captured warning matches the original message."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            warnings.warn("hello world", UserWarning)

        result = _warns_repr(caught)
        assert len(result) == 1
        assert str(result[0]) == "hello world"

    def test_empty_list_returns_empty(self) -> None:
        """Empty input produces an empty list — no warnings captured means nothing to format."""
        assert _warns_repr([]) == []

    def test_preserves_order_for_multiple_warnings(self) -> None:
        """Multiple warnings are returned in emission order, not sorted or reversed."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            warnings.warn("first", UserWarning)
            warnings.warn("second", FutureWarning)

        result = _warns_repr(caught)
        assert len(result) == 2
        assert str(result[0]) == "first"
        assert str(result[1]) == "second"


class TestVoid:
    """Tests for void() — the decorator companion that silences IDE/mypy unused-argument warnings."""

    def test_returns_none_with_no_args(self) -> None:
        """void() with no arguments returns None, satisfying its role as a no-op stub body."""
        assert void() is None

    def test_returns_none_with_positional_args(self) -> None:
        """void() accepts any positional arguments and still returns None."""
        assert void(1, "two", 3.0) is None

    def test_returns_none_with_keyword_args(self) -> None:
        """void() accepts any keyword arguments and still returns None."""
        assert void(x=1, label="hello") is None

    def test_returns_none_with_mixed_args(self) -> None:
        """void() accepts mixed positional and keyword arguments and still returns None."""
        assert void(42, key="value", flag=True) is None


class TestGetFuncArgumentsTypesDefaults:
    """Tests for get_func_arguments_types_defaults — extracts (name, annotation, default) triples."""

    def test_extracts_names_types_and_defaults(self) -> None:
        """Fully annotated and partially annotated params are all extracted with correct types and defaults."""

        def my_func(x: int, y: str = "hello", z=42) -> None:  # type: ignore[no-untyped-def]  # noqa: ANN001
            pass

        result = get_func_arguments_types_defaults(my_func)
        assert len(result) == 3
        names = [r[0] for r in result]
        assert names == ["x", "y", "z"]
        assert result[0][1] is int
        assert result[1][2] == "hello"
        assert result[2][2] == 42

    def test_unannotated_param_has_empty_annotation(self) -> None:
        """A parameter with no type annotation yields inspect.Parameter.empty as its annotation slot."""

        def my_func(a, b: int) -> None:  # type: ignore[no-untyped-def]  # noqa: ANN001
            pass

        result = get_func_arguments_types_defaults(my_func)
        assert result[0][1] is inspect.Parameter.empty

    def test_param_without_default_has_empty_default(self) -> None:
        """A required parameter (no default value) yields inspect.Parameter.empty in the default slot."""

        def my_func(required: str) -> None:
            pass

        result = get_func_arguments_types_defaults(my_func)
        assert result[0][2] is inspect.Parameter.empty

    def test_no_params_returns_empty_list(self) -> None:
        """A function with no parameters returns an empty list rather than raising."""

        def my_func() -> None:
            pass

        assert get_func_arguments_types_defaults(my_func) == []
