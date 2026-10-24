"""Unit tests for private helpers in deprecate.utils."""

import inspect
import warnings
from collections.abc import Callable
from typing import Any, Optional, cast

import pytest

from deprecate import assert_no_warnings
from deprecate.utils import (
    _get_signature,
    _remap_kwargs,
    _SigFacts,
    _signature_facts,
    _warns_repr,
    get_func_arguments_types_defaults,
    void,
)
from tests.collection_targets import (
    HostileSignatureCallable,
    RequiredPositionalOnlyTarget,
    UnintrospectableStream,
    every_parameter_kind_target,
)

#: Not callable, so ``inspect.signature`` raises ``TypeError`` for it.
_NON_CALLABLE = cast(Callable[..., Any], 42)

#: ``args_mapping`` with one rename and one drop (``None``) entry, shared by the ``_remap_kwargs`` tests.
_RENAME_AND_DROP = {"old": "new", "gone": None}


class TestRemapKwargs:
    """Tests for _remap_kwargs, the one ``args_mapping`` rename shared by the decorator and the proxy."""

    def test_renames_and_drops(self) -> None:
        """A deprecated name is renamed, a ``None``-mapped name is dropped and other keywords pass through.

        This is the everyday migration: the caller still uses the old keyword, the mapping retires another one
        entirely, and unrelated keywords must reach the callee untouched, without any collision warning.
        """
        kwargs = {"old": 1, "gone": 2, "keep": 3}
        with assert_no_warnings():
            result = _remap_kwargs(_RENAME_AND_DROP, kwargs, name="old_func", stream=warnings.warn)
        assert result == {"new": 1, "keep": 3}
        assert kwargs == {"old": 1, "gone": 2, "keep": 3}

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({"new": 6, "old": 5}, id="new-first"),
            pytest.param({"old": 5, "new": 6}, id="old-first"),
        ],
    )
    def test_explicit_new_name_wins(self, kwargs: dict[str, int]) -> None:
        """When both the old and the new name are passed, the new value wins and one ``UserWarning`` names the pair.

        A half-migrated caller passes both names; the keyword order must not decide the outcome, and the warning
        must point at the line that made the call.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = _remap_kwargs(_RENAME_AND_DROP, kwargs, name="old_func", stream=warnings.warn)
        assert result == {"new": 6}
        assert [str(w.message) for w in caught] == [
            "Both `old` (deprecated) and `new` were supplied to `old_func()`; `old` is ignored."
        ]
        assert caught[0].category is UserWarning
        assert caught[0].filename == __file__

    def test_silent_without_stream(self) -> None:
        """``stream=None`` resolves a collision the same way but emits no warning.

        A wrapper silenced with ``stream=None``, or a second rename of keywords whose collision was already
        reported, must not warn again.
        """
        with assert_no_warnings():
            result = _remap_kwargs(_RENAME_AND_DROP, {"old": 5, "new": 6}, name="old_func", stream=None)
        assert result == {"new": 6}

    @pytest.mark.parametrize(
        ("mapping", "kwargs", "expected"),
        [
            # "" alone: the deprecated name stays as it is.
            pytest.param({"kept": ""}, {"kept": 1, "keep": 2}, {"kept": 1, "keep": 2}, id="keep-only"),
            # "" next to a rename and a drop: each entry is resolved on its own.
            pytest.param(
                {"kept": "", "old": "new", "gone": None},
                {"kept": 1, "old": 2, "gone": 3},
                {"kept": 1, "new": 2},
                id="keep-beside-rename-and-drop",
            ),
        ],
    )
    def test_empty_string_keeps_old_name(
        self, mapping: dict[str, Optional[str]], kwargs: dict[str, int], expected: dict[str, int]
    ) -> None:
        """A key mapped to ``""`` keeps its old name instead of being renamed or dropped.

        A library that retires a keyword's deprecation warning but not the keyword itself maps it to ``""``; the
        caller's value must still reach the callee under the original name, without any collision warning.
        """
        with assert_no_warnings():
            result = _remap_kwargs(mapping, kwargs, name="old_func", stream=warnings.warn)
        assert result == expected

    def test_empty_mapping_returns_copy(self) -> None:
        """An empty mapping returns an equal but separate dict.

        Callers merge ``args_extra`` into the result, so it must never alias the caller's keyword dict.
        """
        kwargs = {"keep": 1}
        result = _remap_kwargs({}, kwargs, name="old_func", stream=warnings.warn)
        assert result == kwargs
        assert result is not kwargs


class TestSignatureFacts:
    """Tests for _signature_facts, the one reader of a callable's signature at decoration time."""

    def test_classifies_every_parameter_kind(self) -> None:
        """One read reports names, kinds, order and defaults of a signature carrying every parameter kind.

        The decorator and the proxy derive all their call-time data (positional splits, ``*args`` forwarding,
        keyword validation) from these facts instead of inspecting the same signature again, so a target that mixes
        positional-only, positional-or-keyword, ``*args``, keyword-only and ``**kwargs`` parameters must come back
        fully and correctly classified.
        """
        assert _signature_facts(every_parameter_kind_target) == _SigFacts(
            names=frozenset({"a", "b", "rest", "c", "d", "extra"}),
            positional_names=("a", "b"),
            positional_only=frozenset({"a"}),
            order=("a", "b", "rest", "c", "d", "extra"),
            var_positional=True,
            var_keyword=True,
            defaults={"b": 2, "d": 4},
            var_positional_index=2,
        )

    def test_class_reports_constructor_without_self(self) -> None:
        """A class reports its constructor parameters, ``self`` excluded.

        ``deprecated_class`` reads the constructor of the active class to find positional-only parameters that a
        remapped keyword must be passed to positionally; ``self`` is supplied by Python and must not appear.
        """
        facts = _signature_facts(RequiredPositionalOnlyTarget)
        assert facts.order == ("new_val",)
        assert facts.positional_only == frozenset({"new_val"})
        assert facts.defaults == {}

    @pytest.mark.parametrize(
        ("obj", "error"),
        [
            pytest.param(UnintrospectableStream(), ValueError, id="no-signature"),
            pytest.param(_NON_CALLABLE, TypeError, id="non-callable"),
        ],
    )
    def test_unreadable_signature_raises(self, obj: Callable[..., Any], error: type[Exception]) -> None:
        """An unreadable signature raises the error ``inspect.signature`` raises.

        The decorator rejects a source whose signature it cannot read, so the strict default must surface the
        original ``TypeError`` or ``ValueError`` instead of silently describing a callable with no parameters.
        """
        with pytest.raises(error):
            _signature_facts(obj)

    @pytest.mark.parametrize(
        "obj",
        [
            pytest.param(UnintrospectableStream(), id="no-signature"),
            pytest.param(_NON_CALLABLE, id="non-callable"),
        ],
    )
    def test_empty_on_error_returns_empty_facts(self, obj: Callable[..., Any]) -> None:
        """``empty_on_error=True`` turns an unreadable signature into empty facts.

        A forwarding target or a proxied class constructor that cannot be inspected (a C-level callable, a hostile
        metaclass) must not break decoration: those callers treat it as having no positional-only parameters.
        """
        assert _signature_facts(obj, empty_on_error=True) == _SigFacts()

    def test_empty_on_error_keeps_other_errors(self) -> None:
        """Only ``TypeError`` and ``ValueError`` are swallowed; any other signature error propagates.

        A ``__signature__`` descriptor raising ``RuntimeError`` is a bug in the inspected object, not an
        unsupported signature, so it must reach the caller as it does from ``inspect.signature`` itself.
        """
        with pytest.raises(RuntimeError, match="deliberately unreadable"):
            _signature_facts(HostileSignatureCallable(), empty_on_error=True)


class TestSignatureFunctions:
    """Tests for _get_signature, the uncached signature read used when a callable is decorated."""

    def test_returns_valid_signature(self) -> None:
        """Returns a valid Signature with correct parameters for a standard annotated function."""

        def my_func(x: int, y: str = "hi") -> None:
            pass

        sig = _get_signature(my_func)
        assert isinstance(sig, inspect.Signature)
        assert list(sig.parameters) == ["x", "y"]

    def test_reads_unhashable_callable(self) -> None:
        """Unhashable callables are read like any other callable: without a cache nothing needs hashing."""

        class UnhashableCallable:
            __hash__ = None  # type: ignore[assignment]

            def __call__(self, x: int) -> int:
                return x

        obj = UnhashableCallable()
        sig = _get_signature(obj)
        assert isinstance(sig, inspect.Signature)
        assert "x" in sig.parameters


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
        """``void()`` with no arguments returns None, satisfying its role as a no-op stub body."""
        assert void() is None

    def test_returns_none_with_positional_args(self) -> None:
        """``void()`` accepts any positional arguments and still returns None."""
        assert void(1, "two", 3.0) is None

    def test_returns_none_with_keyword_args(self) -> None:
        """``void()`` accepts any keyword arguments and still returns None."""
        assert void(x=1, label="hello") is None

    def test_returns_none_with_mixed_args(self) -> None:
        """``void()`` accepts mixed positional and keyword arguments and still returns None."""
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

    def test_excludes_var_positional_and_var_keyword(self) -> None:
        """``*args`` / ``**kwargs`` are excluded as the docstring promises.

        The helper backs caller-argument validation; leaking the VAR_POSITIONAL / VAR_KEYWORD parameter names
        into that check made a forwarded call raise a raw ``TypeError`` on ``args``/``kwargs`` instead of the
        curated "argument not accepted by target" message, so only the concrete named parameters must appear.
        """

        def my_func(a: int, b: str = "x", *args: int, **kwargs: int) -> None:
            pass

        names = [name for name, _type, _default in get_func_arguments_types_defaults(my_func)]
        assert names == ["a", "b"]
