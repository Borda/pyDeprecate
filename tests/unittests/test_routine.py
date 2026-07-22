"""Unit tests for the packing decorators (:mod:`deprecate.routine`)."""

import warnings
from dataclasses import dataclass
from enum import Enum
from typing import Any, Union

import pytest
import typing_extensions

from deprecate import TargetMode, deprecated
from deprecate.proxy import _DeprecatedProxy
from tests.collection_deprecate import pep702_stacked
from tests.collection_targets import pep702_target


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
        """@deprecated with message_template and no deprecated_in does not emit the empty-version UserWarning."""

        def new_fn() -> None:
            pass

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            @deprecated(target=new_fn, message_template="%(source_name)s is gone, use new_fn.")
            def old_fn_notify() -> None:
                pass

            @deprecated(
                target=TargetMode.ARGS_REMAP,
                args_mapping={"a": "b"},
                message_template="%(source_name)s arg 'a' renamed.",
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


class TestArgsMappingDefensiveCopy:
    """The frozen config must not alias the caller's mutable ``args_mapping`` dict."""

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
