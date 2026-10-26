"""Unit tests for the packing decorators (:mod:`deprecate.routine`)."""

import asyncio
import inspect
import re
import warnings
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional, Union

import pytest
import typing_extensions

from deprecate import TargetMode, deprecated
from deprecate._dispatch import _build_call_facts, _WrapperContext
from deprecate._types import _WrapperState
from deprecate.proxy import _DeprecatedProxy
from deprecate.routine import _build_callable_config, _make_wrapper, _validate_callable_source
from tests.collections.api_current import (
    NewCls,
    Pep702StaticTarget,
    async_gen_target,
    async_target,
    double_value,
    pep702_target,
)
from tests.collections.api_deprecated_forwarding import pep702_stacked

#: Version kwargs shared by every ``_build_callable_config`` call below.
_DEPRS_CASE_CONFIG_ARGS: dict[str, Any] = {"deprecated_in": "1.0", "remove_in": "2.0"}

#: Attributes ``functools.wraps`` copies from the source onto the wrapper.
_WRAPS_ATTRIBUTES = ("__module__", "__name__", "__qualname__", "__doc__")


def _validate(
    source: Any,  # noqa: ANN401
    target: Any = TargetMode.NOTIFY,  # noqa: ANN401
    *,
    is_static: bool = False,
    message_template: Optional[str] = None,
    deprecated_in: str = "1.0",
    stream: Optional[Callable[..., Any]] = warnings.warn,
) -> None:
    """Run :func:`_validate_callable_source` with valid defaults, so each test varies one guard input."""
    _validate_callable_source(
        source,
        target,
        is_static=is_static,
        message_template=message_template,
        deprecated_in=deprecated_in,
        stream=stream,
    )


def _notify_context(source: Callable[..., Any]) -> _WrapperContext:
    """Return the context ``packing`` would build for a warn-only (``TargetMode.NOTIFY``) wrapper over ``source``.

    ``stream=None`` keeps calls silent, so a test only observes the wrapper's shape and its return value.

    """
    facts = _build_call_facts(source, TargetMode.NOTIFY, None)
    cfg = _build_callable_config(
        source=source,
        target=TargetMode.NOTIFY,
        facts=facts,
        function_misconfigured=False,
        **_DEPRS_CASE_CONFIG_ARGS,
    )
    return _WrapperContext(
        source=source,
        raw_target=TargetMode.NOTIFY,
        target=TargetMode.NOTIFY,
        cfg=cfg,
        facts=facts,
        state=_WrapperState(),
        stream=None,
        num_warns=1,
        skip_if=False,
        is_stacked=False,
    )


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
    block.  Moving the wrappers to :mod:`tests.collections.api_deprecated` would fire the guard
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

    PEP 702 ``typing_extensions.deprecated`` overwrites the inner wrapper's ``__deprecated__`` attribute with its
    own message string (and does not touch ``__deprecation_config__``). Before the fix predating this attribute
    split, ``wrapped_fn`` re-read the (then config-typed) ``__deprecated__`` at call time and crashed with
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


class TestValidateCallableSource:
    """``_validate_callable_source`` runs every decoration-time source guard of ``deprecated_callable``, in order."""

    def test_rejects_bare_decorator_call_argument(self) -> None:
        """A non-callable source means ``@deprecated`` was applied without parentheses.

        Bare ``@deprecated`` binds the function to ``target`` and returns the decorator, so the first real call hands
        its argument (here ``5``) to ``packing`` as the source. The guard must name the missing parentheses instead of
        failing later on ``5.__name__``.
        """
        with pytest.raises(TypeError, match="must be called with parentheses"):
            _validate(5)

    def test_rejects_class_source(self) -> None:
        """A class source raises the strict-form ``TypeError`` that names the two alternatives.

        ``deprecated_callable`` is the callable-only arm of ``@deprecated``; a developer who points it at a class must
        be sent to ``deprecated_class`` or the ``@deprecated`` front door, with the exact text users already see.
        """
        expected = (
            "`@deprecated_callable` cannot decorate class `NewCls` — it is the strict"
            " callable-only form. Use `@deprecated_class(...)` to deprecate a class, or `@deprecated`"
            " for automatic class/callable dispatch."
        )
        with pytest.raises(TypeError, match=re.escape(expected)):
            _validate(NewCls)

    def test_rejects_malformed_message_template(self) -> None:
        """A bare ``%s`` conversion in ``message_template`` fails at decoration time, not inside the wrapper.

        A typo in a custom template would otherwise surface only on the first deprecated call, in production code.
        """
        with pytest.raises(ValueError, match=re.escape("Invalid message_template: bare `%`-conversion found in")):
            _validate(double_value, message_template="%s")

    def test_warns_on_missing_deprecated_in(self) -> None:
        """An empty ``deprecated_in`` emits the exact ``UserWarning``, attributed to the caller's file.

        A maintainer who forgets the version gets a notice at import time; the warning must point at the decoration
        site (here this test file), not at a frame inside the ``deprecate`` package.
        """
        expected = (
            "`@deprecated` on `double_value` has no `deprecated_in` set."
            " Deprecation notices and generated documentation will omit the `deprecated_in` version."
            " Pass `deprecated_in` for a meaningful deprecation notice."
        )
        with pytest.warns(UserWarning, match=re.escape(expected)) as record:
            _validate(double_value, deprecated_in="")
        assert record[0].filename == __file__

    @pytest.mark.parametrize(
        ("stream", "message_template"),
        [
            pytest.param(None, None, id="stream-none"),
            pytest.param(warnings.warn, "custom %(source_name)s", id="custom-template"),
        ],
    )
    def test_missing_deprecated_in_silent(
        self, stream: Optional[Callable[..., Any]], message_template: Optional[str]
    ) -> None:
        """The missing-version notice stays silent when ``stream`` is ``None`` or a custom template is set.

        A caller that silenced the decorator, or that owns its message through ``message_template``, does not rely on
        ``deprecated_in`` and must not get the notice.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _validate(double_value, deprecated_in="", stream=stream, message_template=message_template)
        assert not caught

    def test_rejects_cross_class_method_target(self) -> None:
        """A method forwarding to a method of another class raises the cross-class ``TypeError``.

        Forwarding ``Pep702StaticTarget.doubled`` to ``NewCls.__init__`` would hand the wrong ``self`` type to the
        target, so decoration must fail before any call is made.
        """
        with pytest.raises(TypeError, match="cross-class method forwarding is not supported"):
            _validate(Pep702StaticTarget.doubled, NewCls.__init__)

    def test_staticmethod_source_skips_cross_class_guard(self) -> None:
        """The cross-class guard is skipped when ``packing`` marks the source as a staticmethod's function.

        A staticmethod receives no ``self``, so forwarding it to another class's method cannot pass a wrong-typed
        receiver; the same pairing that raises for a plain method must pass with ``is_static=True``.
        """
        _validate(Pep702StaticTarget.doubled, NewCls.__init__, is_static=True)  # must not raise

    def test_rejects_attrs_remap_target(self) -> None:
        """``TargetMode.ATTRS_REMAP`` on a callable raises the proxy-only ``TypeError`` with the exact text.

        ``ATTRS_REMAP`` intercepts attribute access, which a function wrapper has no surface for; the guard must
        redirect the developer to ``deprecated_class(attrs_mapping=...)``.
        """
        expected = (
            "`target=TargetMode.ATTRS_REMAP` is not valid for `@deprecated` on `double_value`. "
            "`TargetMode.ATTRS_REMAP` is a proxy-only mode — use "
            "`deprecated_class(attrs_mapping=...)` to deprecate class attribute names."
        )
        with pytest.raises(TypeError, match=re.escape(expected)):
            _validate(double_value, TargetMode.ATTRS_REMAP)

    def test_class_check_precedes_template_probe(self) -> None:
        """A class source with a malformed template reports the class, not the template.

        A source that trips several guards must report the same first problem as before the guards moved into one
        helper; the class rejection has always run before the ``message_template`` probe.
        """
        with pytest.raises(TypeError, match="cannot decorate class"):
            _validate(NewCls, message_template="%s")

    def test_version_notice_precedes_attrs_remap_raise(self) -> None:
        """An ``ATTRS_REMAP`` target without ``deprecated_in`` emits the version notice, then raises.

        The missing-version ``UserWarning`` has always fired before the ``ATTRS_REMAP`` rejection; checking the raw
        target inside the helper must not swap that order.
        """
        with (
            pytest.warns(UserWarning, match="has no `deprecated_in` set"),
            pytest.raises(TypeError, match="proxy-only"),
        ):
            _validate(double_value, TargetMode.ATTRS_REMAP, deprecated_in="")


class TestBuildCallableConfig:
    """``_build_callable_config`` derives the per-source ``DeprecationConfig`` fields and copies caller mappings."""

    def test_copies_caller_mappings(self) -> None:
        """Mutating the caller's ``args_mapping`` / ``args_extra`` after the build does not reach the config.

        The config is frozen and shared by every call of the wrapper; aliasing the caller's dicts would let a later
        ``mapping[...] = ...`` silently change forwarding at call time.
        """
        mapping: dict[str, Optional[str]] = {"old_arg": "new_arg"}
        extra: dict[str, Any] = {"flag": True}
        cfg = _build_callable_config(
            source=double_value,
            target=TargetMode.ARGS_REMAP,
            facts=_build_call_facts(double_value, TargetMode.ARGS_REMAP, None),
            function_misconfigured=False,
            args_mapping=mapping,
            args_extra=extra,
            **_DEPRS_CASE_CONFIG_ARGS,
        )
        mapping["old_arg"] = "hijacked"
        extra["flag"] = False
        assert cfg.args_mapping == {"old_arg": "new_arg"}
        assert cfg.args_extra == {"flag": True}
        assert cfg.name == "double_value"

    @pytest.mark.parametrize(
        ("target", "function_misconfigured", "expected"),
        [
            pytest.param(False, False, True, id="legacy-false"),
            pytest.param(TargetMode.NOTIFY, True, True, id="mode-misconfigured"),
            pytest.param(TargetMode.NOTIFY, False, False, id="clean"),
        ],
    )
    def test_misconfigured_flag(
        self, target: Union[bool, TargetMode], function_misconfigured: bool, expected: bool
    ) -> None:
        """``misconfigured`` is set by legacy ``target=False`` or by a misconfigured explicit mode.

        The flag drives the one-time call-time misconfiguration warning, so both decoration-time sources of it must
        reach the config.
        """
        cfg = _build_callable_config(
            source=double_value,
            target=target,
            facts=_build_call_facts(double_value, TargetMode.NOTIFY, None),
            function_misconfigured=function_misconfigured,
            **_DEPRS_CASE_CONFIG_ARGS,
        )
        assert cfg.misconfigured is expected


class TestMakeWrapper:
    """``_make_wrapper`` builds the sync or async wrapper and attaches the deprecation metadata."""

    def test_async_wrapper_for_coroutine_source(self) -> None:
        """``is_async=True`` yields a coroutine function that awaits the source.

        ``await old_func(...)`` must keep working for a deprecated ``async def``; a sync wrapper would hand back a bare
        coroutine and break ``inspect.iscoroutinefunction`` checks in frameworks.
        """
        wrapper = _make_wrapper(_notify_context(async_target), is_async=True, update_docstring=False)
        assert inspect.iscoroutinefunction(wrapper)
        assert asyncio.run(wrapper(3)) == 6

    def test_sync_wrapper_for_async_generator_source(self) -> None:
        """An async generator source gets the sync wrapper, which returns the async generator object.

        ``inspect.iscoroutinefunction`` is ``False`` for ``async def`` + ``yield``, so ``packing`` passes
        ``is_async=False``; the wrapper must return the generator unchanged for ``async for``.
        """
        wrapper = _make_wrapper(_notify_context(async_gen_target), is_async=False, update_docstring=False)
        assert not inspect.iscoroutinefunction(wrapper)
        agen = wrapper(2)
        assert inspect.isasyncgen(agen)
        asyncio.run(agen.aclose())

    def test_attaches_metadata(self) -> None:
        """The wrapper carries the context's config and state objects plus ``functools.wraps`` metadata.

        Audit tools read ``__deprecation_config__``, static checkers and ``inspect`` read ``__deprecated__`` and
        ``__wrapped__``, and a counter reset through ``wrapper._state`` must reach the call path.
        """
        ctx = _notify_context(double_value)
        wrapper = _make_wrapper(ctx, is_async=False, update_docstring=False)
        assert wrapper.__deprecation_config__ is ctx.cfg
        assert wrapper._state is ctx.state
        assert inspect.unwrap(wrapper) is double_value
        assert {name: getattr(wrapper, name) for name in _WRAPS_ATTRIBUTES} == {
            name: getattr(double_value, name) for name in _WRAPS_ATTRIBUTES
        }
        assert "double_value" in wrapper.__deprecated__
        assert "1.0" in wrapper.__deprecated__

    def test_update_docstring_injects_notice(self) -> None:
        """``update_docstring=True`` injects the reST deprecation directive into the wrapper's docstring.

        Generated API docs must show the notice without the maintainer editing the docstring by hand.
        """
        wrapper = _make_wrapper(_notify_context(double_value), is_async=False, update_docstring=True)
        assert ".. deprecated:: 1.0" in str(wrapper.__doc__)
