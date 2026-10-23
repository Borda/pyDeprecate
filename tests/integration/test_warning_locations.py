"""Characterization tests: every pyDeprecate warning points at the first frame outside the ``deprecate`` package.

Warning filters keyed by module, ``-W error`` tracebacks and ``pytest.warns`` location checks all depend on the
``filename``/``lineno`` a warning reports.  Each test below triggers one warn site and asserts the exact location: the
calling line in this file for call-time warnings, the decorating line in :mod:`tests.collection_deprecate` for
decoration-time warnings.  The expected values are the correct user locations and every site meets them, so any
refactor of the location machinery (:func:`deprecate._frames._caller_stacklevel`) is proven against them.

Call-time sites whose trigger is a single expression run through a one-line ``lambda`` defined in the parametrize list:
the lambda frame is the user frame, so the expected line is the lambda's own first line.  Statement-shaped triggers
(``+=``, ``with``, ``class``, ``await``) and sites that need setup are spelled out in their own tests and capture the
expected line just before the warned statement.

"""

import functools
import importlib.util
import inspect
import pkgutil
import sys
import types
import warnings
from pathlib import Path
from typing import Any, Callable

import pytest

import tests.collection_modules.old_math as old_math
from deprecate import TargetMode
from deprecate.audit import (
    DeprecationWrapperInfo,
    find_deprecation_wrappers,
    validate_deprecation_expiry,
    validate_deprecation_policy,
)
from deprecate.utils import no_warning_call
from tests import collection_deprecate
from tests.collection_deprecate import (
    LocDescriptorHost,
    LocProxyBase,
    MappedDataClass,
    loc_args_remap,
    loc_async_notify,
    loc_forward,
    loc_notify,
    loc_notify_wrapped_stream,
    loc_proxy_callable,
    loc_proxy_ctor_remap,
    loc_proxy_mapping,
    loc_proxy_number,
    loc_stack_callable_inner,
    loc_stack_notify_inner,
    loc_stack_notify_over_callable,
    loc_stack_remap_inner,
    loc_stack_remap_over_notify,
    loc_stack_remap_over_remap,
    make_loc_audit_wrapper,
    make_loc_callable_direct,
    make_loc_class_direct,
    make_loc_class_dispatch_notice,
    make_loc_decoration_misconfig,
    make_loc_front_door,
    make_loc_instance,
    make_loc_legacy_none_sentinel,
    make_loc_missing_deprecated_in,
    make_loc_module,
    make_loc_positional_only_target,
    make_loc_side_effect_box_proxy,
)
from tests.collection_misconfigured import args_only_no_mapping_deprecation
from tests.collection_targets import (
    LocCustomModuleType,
    NewDataClass,
    PositionalOnlyTarget,
    double_value,
    identity_value,
)

_requires_packaging = pytest.mark.skipif(
    importlib.util.find_spec("packaging") is None, reason="requires packaging library"
)
#: Module the audit gates scan; its own top-level name keeps the scanner from treating the
#: ``tests.collection_deprecate`` wrappers as re-exports of their defining module.
_LOC_AUDIT_MODULE_NAME = "tests_loc_audit_module"
#: Body of a submodule whose import fails, arming the scanner's per-submodule "skipped" diagnostic.
_LOC_BROKEN_SUBMODULE_SRC = 'raise RuntimeError("boom at import")\n'
#: Module name registered in ``sys.modules`` (per test, via ``monkeypatch``) for the ``deprecated_module`` sites.
_LOC_MODULE_NAME = "tests_loc_deprecated_module"
#: Valid custom message template for the ``template_mgs`` alias rows.
_LOC_TEMPLATE = "`%(source_name)s` is deprecated."


def _next_line() -> int:
    """Return the line number just below the caller's current line, where the warned statement sits."""
    frame = inspect.currentframe()
    assert frame is not None
    assert frame.f_back is not None
    return frame.f_back.f_lineno + 1


def _line_ending_with(func: Callable[..., Any], suffix: str) -> int:
    """Return the line number of the single source line of *func* that ends with *suffix*."""
    lines, first = inspect.getsourcelines(func)
    matches = [first + offset for offset, line in enumerate(lines) if line.rstrip().endswith(suffix)]
    assert len(matches) == 1, f"expected exactly one line ending with {suffix!r} in {func.__qualname__}: {matches}"
    return matches[0]


def _location(caught: list[warnings.WarningMessage], category: type[Warning], match: str) -> tuple[str, int]:
    """Return ``(filename, lineno)`` of the single caught *category* warning whose message contains *match*."""
    hits = [w for w in caught if issubclass(w.category, category) and match in str(w.message)]
    assert len(hits) == 1, [f"{w.category.__name__}: {w.message}" for w in caught]
    return hits[0].filename, hits[0].lineno


class TestCallTimeExpressionSites:
    """Call-time warnings raised by a single expression point at the line of that expression."""

    @pytest.mark.parametrize(
        ("operation", "category", "match"),
        [
            pytest.param(lambda: loc_notify(1), FutureWarning, "`loc_notify` was deprecated", id="notify-call"),
            pytest.param(
                lambda: loc_notify_wrapped_stream(1),
                UserWarning,
                "`loc_notify_wrapped_stream` was deprecated",
                id="notify-call-wraps-decorated-stream",
            ),
            pytest.param(lambda: loc_args_remap(old_x=1), FutureWarning, "uses deprecated arguments", id="args-remap"),
            pytest.param(lambda: loc_forward(1), FutureWarning, "in favor of", id="callable-target-forward"),
            pytest.param(lambda: loc_args_remap(x=2, old_x=1), UserWarning, "were supplied", id="args-collision"),
            pytest.param(
                lambda: loc_stack_remap_over_notify(old_x=1),
                FutureWarning,
                "uses deprecated arguments",
                id="stacked-remap-over-notify-outer",
            ),
            pytest.param(
                lambda: loc_stack_remap_over_notify(old_x=1),
                FutureWarning,
                "was deprecated since v1.0",
                id="stacked-remap-over-notify-inner",
            ),
            pytest.param(
                lambda: loc_stack_notify_over_callable(1),
                FutureWarning,
                "since v1.5",
                id="stacked-notify-over-callable-outer",
            ),
            pytest.param(
                lambda: loc_stack_notify_over_callable(1),
                FutureWarning,
                "in favor of",
                id="stacked-notify-over-callable-inner",
            ),
            pytest.param(
                lambda: loc_stack_remap_over_remap(b=1, c=2),
                UserWarning,
                "were supplied",
                id="stacked-args-collision",
            ),
            pytest.param(lambda: loc_proxy_mapping.keys, FutureWarning, "loc_proxy_mapping", id="proxy-getattr"),
            pytest.param(
                lambda: loc_proxy_mapping["threshold"], FutureWarning, "loc_proxy_mapping", id="proxy-getitem"
            ),
            pytest.param(lambda: iter(loc_proxy_mapping), FutureWarning, "loc_proxy_mapping", id="proxy-iter"),
            pytest.param(lambda: loc_proxy_callable(2), FutureWarning, "loc_proxy_callable", id="proxy-call"),
            pytest.param(lambda: loc_proxy_number + 1, FutureWarning, "loc_proxy_number", id="binary-op"),  # type: ignore[operator]
            pytest.param(lambda: -loc_proxy_number, FutureWarning, "loc_proxy_number", id="unary-op"),  # type: ignore[operator]
            pytest.param(lambda: old_math.square, FutureWarning, "module was deprecated", id="module-attribute"),
            pytest.param(lambda: LocDescriptorHost().legacy_value, FutureWarning, "legacy_value", id="property"),
            pytest.param(
                lambda: LocDescriptorHost.legacy_factory(1), FutureWarning, "legacy_factory", id="classmethod"
            ),
            pytest.param(lambda: LocDescriptorHost.legacy_helper(1), FutureWarning, "legacy_helper", id="staticmethod"),
            pytest.param(
                lambda: loc_proxy_ctor_remap(name="a"),
                FutureWarning,
                "`name`",
                id="proxy-ctor-remap-argument",
            ),
            pytest.param(
                lambda: loc_proxy_ctor_remap(name="a", label="b"),
                UserWarning,
                "were supplied",
                id="proxy-ctor-remap-collision",
            ),
            pytest.param(
                lambda: MappedDataClass(name="a"),  # type: ignore[call-arg]
                FutureWarning,
                "`name`",
                id="proxy-ctor-forward-argument",
            ),
            pytest.param(
                lambda: MappedDataClass(label="a"), FutureWarning, "MappedDataClass", id="proxy-ctor-forward-class"
            ),
            pytest.param(
                lambda: MappedDataClass(name="a", label="b"),  # type: ignore[call-arg]
                UserWarning,
                "were supplied",
                id="proxy-ctor-forward-collision",
            ),
        ],
    )
    def test_points_at_calling_line(self, operation: Callable[[], Any], category: type[Warning], match: str) -> None:
        """The warning reports this file and the line of the expression that used the deprecated object.

        A user filters warnings per module or turns them into errors to find the code that still needs migrating; both
        only work when the reported location is the user's own line.  Every expression-shaped warn site of the
        callable, proxy, module and descriptor paths is exercised once, and each layer of a stacked wrapper is checked
        separately, so the outer and the inner layer both have to report the user's line.

        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            operation()

        assert _location(caught, category, match) == (__file__, operation.__code__.co_firstlineno)


class TestCallTimeStatementSites:
    """Call-time warnings raised by a statement, or needing setup, point at the line of the warned statement."""

    def test_call_time_misconfiguration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The one-time call-time misconfiguration ``UserWarning`` reports the calling line.

        A wrapper declared ``TargetMode.ARGS_REMAP`` without any ``args_mapping`` warns once, at its first call, that
        its configuration is invalid.  The one-time flag is cleared for this test only, so the warning is observable
        regardless of which earlier test already called the shared misconfigured fixture.

        """
        monkeypatch.setattr(args_only_no_mapping_deprecation._state, "warned_misconfigured", False)  # type: ignore[attr-defined]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            expected_line = _next_line()
            args_only_no_mapping_deprecation(1)

        assert _location(caught, UserWarning, "invalid deprecation configuration") == (__file__, expected_line)

    def test_cached_property_reports_functools_frame(self) -> None:
        """A ``@cached_property`` source warns from the line in ``functools.cached_property.__get__`` that runs it.

        Unlike the C-level ``property``, ``functools.cached_property`` is pure Python, so its ``__get__`` frame is the
        first frame outside the ``deprecate`` package and the warning reports that stdlib line instead of the user's
        attribute access.  Pinned as today's behaviour: a frame walk that skips only ``deprecate.*`` frames keeps it,
        so any change here is deliberate.  The instance is fresh because a cached value bypasses the getter.

        """
        expected = (functools.__file__, _line_ending_with(functools.cached_property.__get__, "self.func(instance)"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = LocDescriptorHost().legacy_cached

        assert _location(caught, FutureWarning, "legacy_cached") == expected

    def test_proxy_inplace_operator(self) -> None:
        """An in-place operator on a deprecated instance warns at the augmented-assignment line.

        ``box += 3`` on a proxied container runs the wrapped object's ``__iadd__``; the warning has to name the line of
        the augmented assignment, the only place a user can see that a deprecated object is being mutated.

        """
        box = make_loc_side_effect_box_proxy()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            expected_line = _next_line()
            box += 3

        assert _location(caught, FutureWarning, "loc_proxy_box") == (__file__, expected_line)

    def test_proxy_enter(self) -> None:
        """Entering a deprecated instance as a context manager warns at the ``with`` line.

        A deprecated resource handle is typically used as ``with legacy_handle:``; the warning must point at that
        ``with`` statement rather than at the proxy's ``__enter__`` forwarding code.

        """
        box = make_loc_side_effect_box_proxy()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            expected_line = _next_line()
            with box:
                pass

        assert _location(caught, FutureWarning, "loc_proxy_box") == (__file__, expected_line)

    def test_proxy_mro_entries(self) -> None:
        """Subclassing a deprecated class alias warns at the ``class`` statement.

        Downstream code still writes ``class Child(OldBase): ...`` during a migration window; the PEP 560
        ``__mro_entries__`` hook warns, and the location must be the user's class statement.

        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            expected_line = _next_line() + 1  # +1: the formatter keeps one blank line above a nested class

            class _Child(LocProxyBase):  # type: ignore[misc,valid-type]
                """Subclass built off the deprecated alias."""

        assert _location(caught, FutureWarning, "SubclassableBase") == (__file__, expected_line)

    @pytest.mark.asyncio
    async def test_async_source_awaited(self) -> None:
        """An async deprecated source warns at the ``await`` line of the user coroutine.

        The warning of an ``async def`` source fires when the coroutine is awaited, not when it is created; the frame
        above the wrapper's coroutine is the awaiting user coroutine, whose ``await`` line must be reported.

        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            expected_line = _next_line()
            await loc_async_notify(1)

        assert _location(caught, FutureWarning, "`loc_async_notify` was deprecated") == (__file__, expected_line)


class TestDecorationTimeSites:
    """Decoration-time warnings point at the decorating statement in :mod:`tests.collection_deprecate`."""

    @pytest.mark.parametrize(
        ("factory", "category", "match"),
        [
            pytest.param(
                make_loc_legacy_none_sentinel,
                FutureWarning,
                "target=None is deprecated",
                id="front-door-legacy-sentinel",
            ),
            pytest.param(make_loc_decoration_misconfig, UserWarning, "ignores `args_mapping`", id="misconfig"),
            pytest.param(
                make_loc_missing_deprecated_in, UserWarning, "has no `deprecated_in` set", id="missing-deprecated-in"
            ),
            pytest.param(make_loc_positional_only_target, UserWarning, "POSITIONAL_ONLY", id="positional-only-target"),
            pytest.param(
                make_loc_class_dispatch_notice,
                UserWarning,
                "now dispatches to `@deprecated_class`",
                id="class-dispatch",
            ),
        ],
    )
    def test_points_at_decorating_line(self, factory: Callable[[], Any], category: type[Warning], match: str) -> None:
        """The warning reports the module and line where the user applied the decorator.

        Decoration-time warnings flag a configuration mistake or a legacy spelling in the code that declares the
        deprecation; the maintainer can only fix it when the warning names that declaration.  Each factory applies the
        decorator on its ``# warn-site`` line, so that line in the collection module is the expected location.

        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            factory()

        expected = (collection_deprecate.__file__, _line_ending_with(factory, "# warn-site"))
        assert _location(caught, category, match) == expected


class TestDecorationTimeConfiguredSites:
    """Decoration-time warnings of every entry point point at the configurable factory's ``# warn-site`` line."""

    @pytest.mark.parametrize(
        ("factory", "source", "config", "category", "match"),
        [
            pytest.param(
                make_loc_front_door,
                loc_stack_callable_inner,
                {"target": double_value},
                UserWarning,
                "stacked over another callable-target",
                id="stack-callable-over-callable",
            ),
            pytest.param(
                make_loc_front_door,
                loc_stack_remap_inner,
                {"target": double_value},
                UserWarning,
                "stacked over @deprecated(ARGS_REMAP)",
                id="stack-callable-over-remap",
            ),
            pytest.param(
                make_loc_front_door,
                loc_stack_notify_inner,
                {"target": double_value},
                UserWarning,
                "stacked over @deprecated(NOTIFY)",
                id="stack-callable-over-notify",
            ),
            pytest.param(
                make_loc_front_door,
                loc_stack_callable_inner,
                {"target": TargetMode.ARGS_REMAP, "args_mapping": {"old_x": "x"}},
                UserWarning,
                "stacked over a callable-target",
                id="stack-remap-over-callable",
            ),
            pytest.param(
                make_loc_front_door,
                loc_stack_notify_inner,
                {"target": TargetMode.NOTIFY},
                UserWarning,
                "duplicate @deprecated(NOTIFY)",
                id="stack-notify-over-notify",
            ),
            pytest.param(
                make_loc_front_door,
                loc_stack_remap_inner,
                {"target": TargetMode.NOTIFY},
                UserWarning,
                "Reverse the decorator order",
                id="stack-notify-over-remap",
            ),
            pytest.param(
                make_loc_callable_direct,
                loc_stack_notify_inner,
                {"target": TargetMode.NOTIFY},
                UserWarning,
                "duplicate @deprecated(NOTIFY)",
                id="stack-notify-over-notify-direct",
            ),
            pytest.param(
                make_loc_callable_direct,
                identity_value,
                {"target": None},
                FutureWarning,
                "target=None is deprecated",
                id="legacy-none-direct",
            ),
            pytest.param(
                make_loc_callable_direct,
                identity_value,
                {"target": True, "args_mapping": {"old_x": "x"}},
                FutureWarning,
                "target=True is deprecated",
                id="legacy-true-direct",
            ),
            pytest.param(
                make_loc_callable_direct,
                identity_value,
                {"target": False},
                UserWarning,
                "`target=False` is not a valid deprecation mode",
                id="legacy-false-direct",
            ),
            pytest.param(
                make_loc_front_door,
                identity_value,
                {"target": True, "args_mapping": {"old_x": "x"}},
                FutureWarning,
                "target=True is deprecated",
                id="legacy-true-front-door",
            ),
            pytest.param(
                make_loc_front_door,
                identity_value,
                {"target": False},
                UserWarning,
                "`target=False` is not a valid deprecation mode",
                id="legacy-false-front-door",
            ),
            pytest.param(
                make_loc_front_door,
                NewDataClass,
                {"target": None},
                FutureWarning,
                "target=None is deprecated",
                id="class-legacy-none-front-door",
            ),
            pytest.param(
                make_loc_front_door,
                NewDataClass,
                {"target": True},
                FutureWarning,
                "target=True is deprecated",
                id="class-legacy-true-front-door",
            ),
            pytest.param(
                make_loc_front_door,
                NewDataClass,
                {"target": False},
                UserWarning,
                "`target=False` is not a valid deprecation mode",
                id="class-legacy-false-front-door",
            ),
            pytest.param(
                make_loc_class_direct,
                NewDataClass,
                {"target": True},
                FutureWarning,
                "`target=True` without `args_mapping`",
                id="proxy-legacy-true",
            ),
            pytest.param(
                make_loc_class_direct,
                NewDataClass,
                {"target": True, "args_mapping": {"name": "label"}},
                FutureWarning,
                "`target=True` with `args_mapping`",
                id="proxy-legacy-true-mapping",
            ),
            pytest.param(
                make_loc_class_direct,
                NewDataClass,
                {"target": False},
                UserWarning,
                "`target=False` is not valid for `deprecated_class()`",
                id="proxy-legacy-false",
            ),
            pytest.param(
                make_loc_class_direct,
                NewDataClass,
                {"target": TargetMode.NOTIFY, "args_mapping": {"name": "label"}},
                UserWarning,
                "ignores `args_mapping`",
                id="proxy-misconfig",
            ),
            pytest.param(
                make_loc_front_door,
                NewDataClass,
                {"target": TargetMode.NOTIFY, "args_mapping": {"name": "label"}},
                UserWarning,
                "ignores `args_mapping`",
                id="proxy-misconfig-front-door",
            ),
            pytest.param(
                make_loc_class_direct,
                NewDataClass,
                {"attrs_mapping": {}},
                UserWarning,
                "empty dict",
                id="proxy-attrs-misconfig",
            ),
            pytest.param(
                make_loc_class_direct,
                PositionalOnlyTarget,
                {"args_mapping": {"old_val": "new_val"}},
                UserWarning,
                "POSITIONAL_ONLY constructor",
                id="proxy-positional-only",
            ),
            pytest.param(
                make_loc_front_door,
                PositionalOnlyTarget,
                {"args_mapping": {"old_val": "new_val"}},
                UserWarning,
                "POSITIONAL_ONLY constructor",
                id="proxy-positional-only-front-door",
            ),
            pytest.param(
                make_loc_callable_direct,
                identity_value,
                {"deprecated_in": ""},
                UserWarning,
                "has no `deprecated_in` set",
                id="missing-deprecated-in-direct",
            ),
            pytest.param(
                make_loc_front_door,
                property(identity_value),
                {"deprecated_in": ""},
                UserWarning,
                "has no `deprecated_in` set",
                id="missing-deprecated-in-property",
            ),
            pytest.param(
                make_loc_front_door,
                classmethod(identity_value),  # type: ignore[arg-type]
                {"deprecated_in": ""},
                UserWarning,
                "has no `deprecated_in` set",
                id="missing-deprecated-in-classmethod",
            ),
            pytest.param(
                make_loc_front_door,
                staticmethod(identity_value),
                {"deprecated_in": ""},
                UserWarning,
                "has no `deprecated_in` set",
                id="missing-deprecated-in-staticmethod",
            ),
            pytest.param(
                make_loc_class_direct,
                NewDataClass,
                {"deprecated_in": ""},
                UserWarning,
                "has no `deprecated_in` set",
                id="missing-deprecated-in-class",
            ),
            pytest.param(
                make_loc_front_door,
                NewDataClass,
                {"deprecated_in": ""},
                UserWarning,
                "has no `deprecated_in` set",
                id="missing-deprecated-in-class-front-door",
            ),
            pytest.param(
                make_loc_instance,
                {"threshold": 0.5},
                {"deprecated_in": ""},
                UserWarning,
                "has no `deprecated_in` set",
                id="missing-deprecated-in-instance",
            ),
            pytest.param(
                make_loc_front_door,
                identity_value,
                {"template_mgs": _LOC_TEMPLATE},
                FutureWarning,
                "`template_mgs` is deprecated",
                id="template-alias-front-door",
            ),
            pytest.param(
                make_loc_callable_direct,
                identity_value,
                {"template_mgs": _LOC_TEMPLATE},
                FutureWarning,
                "`template_mgs` is deprecated",
                id="template-alias-callable",
            ),
            pytest.param(
                make_loc_class_direct,
                NewDataClass,
                {"template_mgs": _LOC_TEMPLATE},
                FutureWarning,
                "`template_mgs` is deprecated",
                id="template-alias-class",
            ),
            pytest.param(
                make_loc_instance,
                {"threshold": 0.5},
                {"template_mgs": _LOC_TEMPLATE},
                FutureWarning,
                "`template_mgs` is deprecated",
                id="template-alias-instance",
            ),
        ],
    )
    def test_points_at_decorating_line(
        self, factory: Callable[..., Any], source: object, config: dict[str, Any], category: type[Warning], match: str
    ) -> None:
        """The warning reports the factory's ``# warn-site`` line, where the user's decoration statement sits.

        Each entry point (``@deprecated`` front door, strict ``deprecated_callable``, ``deprecated_class``,
        ``deprecated_instance``) reaches the shared warn sites through a different number of library frames, so a fixed
        ``stacklevel`` that is right for one is wrong for another.  Every row applies one entry point to one source with
        the configuration that trips one decoration-time warning — stacking misconfigurations, legacy ``target``
        sentinels, proxy misconfigurations, missing ``deprecated_in`` and the ``template_mgs`` alias — and pins where
        the maintainer is sent to fix it.

        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            factory(source, **config)

        expected = (collection_deprecate.__file__, _line_ending_with(factory, "# warn-site"))
        assert _location(caught, category, match) == expected


class TestModuleSites:
    """``deprecated_module`` warnings point at the ``deprecated_module(...)`` call or at the attribute access."""

    @pytest.mark.parametrize(
        ("module_type", "module_attrs", "config", "match"),
        [
            pytest.param(
                types.ModuleType, {}, {"deprecated_in": ""}, "has no `deprecated_in` set", id="missing-version"
            ),
            pytest.param(LocCustomModuleType, {}, {}, "custom `__class__`", id="custom-module-class"),
            pytest.param(
                types.ModuleType,
                {"__getattr__": identity_value},
                {},
                "pre-existing `__getattr__`",
                id="chained-getattr",
            ),
        ],
    )
    def test_install_points_at_call(
        self,
        monkeypatch: pytest.MonkeyPatch,
        module_type: type[types.ModuleType],
        module_attrs: dict[str, Any],
        config: dict[str, Any],
        match: str,
    ) -> None:
        """An install-time ``UserWarning`` reports the ``deprecated_module(...)`` call line.

        A maintainer deprecates a module from its own ``__init__``; a missing version, a lazy-loader module class that
        gets overwritten, or a chained PEP 562 ``__getattr__`` are flagged once at that call, which is where the warning
        has to send them.  The module is registered for this test only, so the class swap never leaks.

        """
        module = module_type(_LOC_MODULE_NAME)
        vars(module).update(module_attrs)
        monkeypatch.setitem(sys.modules, _LOC_MODULE_NAME, module)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            make_loc_module(_LOC_MODULE_NAME, **config)

        expected = (collection_deprecate.__file__, _line_ending_with(make_loc_module, "# warn-site"))
        assert _location(caught, UserWarning, match) == expected

    def test_reconfiguration_points_at_second_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Re-deprecating a module with a different configuration warns at the second ``deprecated_module`` call.

        After an ``importlib.reload`` or a copy-paste, a module can be deprecated twice with different versions; the
        second call is ignored, and the ``UserWarning`` saying so must name that ignored call.

        """
        monkeypatch.setitem(sys.modules, _LOC_MODULE_NAME, types.ModuleType(_LOC_MODULE_NAME))
        make_loc_module(_LOC_MODULE_NAME)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            make_loc_module(_LOC_MODULE_NAME, remove_in="3.0")

        expected = (collection_deprecate.__file__, _line_ending_with(make_loc_module, "# warn-site"))
        assert _location(caught, UserWarning, "different configuration") == expected

    def test_custom_stream_access_points_at_access(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A module deprecated with a custom ``stream`` reports the attribute-access line.

        A custom stream takes the ``_emit`` delivery path instead of the default ``warnings.warn`` call, with its own
        frame count; ``warnings.warn`` as the stream accepts ``stacklevel`` and must still land on the user's access.

        """
        module = types.ModuleType(_LOC_MODULE_NAME)
        vars(module)["value"] = 1
        monkeypatch.setitem(sys.modules, _LOC_MODULE_NAME, module)
        make_loc_module(_LOC_MODULE_NAME, stream=warnings.warn)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            expected_line = _next_line()
            _ = module.value

        assert _location(caught, UserWarning, "module was deprecated") == (__file__, expected_line)


class TestLibraryAliasSites:
    """pyDeprecate's own renamed APIs warn at the line that still uses the old spelling."""

    @pytest.mark.parametrize(
        ("operation", "match"),
        [
            pytest.param(lambda: no_warning_call(), "no_warning_call", id="no-warning-call"),
            pytest.param(lambda: DeprecationWrapperInfo().empty_mapping, "'empty_mapping'", id="empty-mapping-read"),
            pytest.param(
                lambda: DeprecationWrapperInfo().identity_mapping, "'identity_mapping'", id="identity-mapping-read"
            ),
            pytest.param(
                lambda: DeprecationWrapperInfo(empty_mapping=True),  # type: ignore[call-arg]
                "'empty_mapping'",
                id="empty-mapping-ctor",
            ),
        ],
    )
    def test_points_at_calling_line(self, operation: Callable[[], Any], match: str) -> None:
        """The ``DeprecationWarning`` reports this file and the line of the old-spelling expression.

        Downstream test suites still construct ``no_warning_call()`` or read the pre-0.8 ``DeprecationWrapperInfo``
        field names; the warning must point at that use, not at the alias shim inside pyDeprecate.

        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            operation()

        assert _location(caught, DeprecationWarning, match) == (__file__, operation.__code__.co_firstlineno)


def _write_scan_package(root: Path, name: str, broken_parts: tuple[str, ...]) -> None:
    """Create package *name* under *root* with a ``sub`` subpackage and, when *broken_parts* is set, a broken module."""
    (root / name / "sub").mkdir(parents=True)
    (root / name / "__init__.py").write_text("")
    (root / name / "sub" / "__init__.py").write_text("")
    if broken_parts:
        (root / name).joinpath(*broken_parts).write_text(_LOC_BROKEN_SUBMODULE_SRC)


def _no_walk(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Stand-in for ``pkgutil.iter_modules`` that fails the way an unreadable package directory does."""
    raise OSError("no walk")


class TestAuditDiagnosticSites:
    """Audit-gate diagnostics warn at the line that runs the gate, not inside ``deprecate.audit``."""

    @_requires_packaging
    @pytest.mark.parametrize(
        ("gate", "remove_in", "match"),
        [
            pytest.param(
                lambda module: validate_deprecation_expiry(module, "2.0", recursive=False),
                "not.a.version!!",
                "unparsable `remove_in`",
                id="expiry-unparsable-remove-in",
            ),
            pytest.param(
                lambda module: validate_deprecation_policy(module, recursive=False),
                "not.a.version!!",
                "unparsable `remove_in`",
                id="policy-unparsable-remove-in",
            ),
            pytest.param(
                lambda module: validate_deprecation_policy(module, recursive=False),
                "1!1.0",
                "PEP 440 epoch change",
                id="policy-epoch-change",
            ),
        ],
    )
    def test_version_diagnostic_points_at_gate_call(
        self, monkeypatch: pytest.MonkeyPatch, gate: Callable[[types.ModuleType], Any], remove_in: str, match: str
    ) -> None:
        """A wrapper with an unusable version string is reported at the line that runs the CI gate.

        A project runs ``validate_deprecation_expiry`` or ``validate_deprecation_policy`` from its own CI script; a
        typo'd ``remove_in`` or an epoch change makes the gate skip that wrapper with a ``UserWarning``.  The
        warning must name the gate call, because the module-scoped filters and ``-W error`` the project uses are
        keyed on that location, not on whichever audit helper happened to notice the problem.

        """
        module = types.ModuleType(_LOC_AUDIT_MODULE_NAME)
        module.__dict__["source"] = make_loc_audit_wrapper(remove_in)
        monkeypatch.setitem(sys.modules, _LOC_AUDIT_MODULE_NAME, module)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            gate(module)

        assert _location(caught, UserWarning, match) == (__file__, gate.__code__.co_firstlineno)

    @pytest.mark.parametrize(
        ("package_name", "broken_parts", "match"),
        [
            pytest.param("tests_loc_scan_flat", ("bad.py",), "skipped tests_loc_scan_flat.bad", id="flat"),
            pytest.param("tests_loc_scan_nested", ("sub", "bad.py"), "skipped tests_loc_scan_nested.sub", id="nested"),
        ],
    )
    def test_skipped_submodule_points_at_scan_call(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        package_name: str,
        broken_parts: tuple[str, ...],
        match: str,
    ) -> None:
        """A submodule that fails to import is reported at the ``find_deprecation_wrappers`` call.

        A recursive scan of a package whose optional-dependency submodule raises at import time keeps going and
        warns per skipped submodule.  The scanner walks nested subpackages through a chain of generators, so the
        nested row checks that the depth of the broken module does not shift the reported location.

        """
        _write_scan_package(tmp_path, package_name, broken_parts)
        monkeypatch.syspath_prepend(str(tmp_path))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            expected_line = _next_line()
            find_deprecation_wrappers(package_name, recursive=True)

        assert _location(caught, UserWarning, match) == (__file__, expected_line)

    def test_stopped_walk_points_at_scan_call(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A submodule walk that cannot list the package directory is reported at the scan call.

        An unreadable package directory stops the walk early; the scan returns what it found and warns once,
        and that warning must point at the CI line that asked for the recursive scan.

        """
        _write_scan_package(tmp_path, "tests_loc_scan_walk", ())
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.setattr(pkgutil, "iter_modules", _no_walk)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            expected_line = _next_line()
            find_deprecation_wrappers("tests_loc_scan_walk", recursive=True)

        assert _location(caught, UserWarning, "stopped early") == (__file__, expected_line)
