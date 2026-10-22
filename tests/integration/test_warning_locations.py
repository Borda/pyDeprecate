"""Characterization tests: every pyDeprecate warning points at the first frame outside the ``deprecate`` package.

Warning filters keyed by module, ``-W error`` tracebacks and ``pytest.warns`` location checks all depend on the
``filename``/``lineno`` a warning reports.  Each test below triggers one warn site and asserts the exact location: the
calling line in this file for call-time warnings, the decorating line in :mod:`tests.collection_deprecate` for
decoration-time warnings.  The expected values describe today's behaviour so an internal refactor of the stacklevel
machinery is proven against it; sites that point into pyDeprecate today are marked ``xfail(strict=True)`` with the audit
finding that tracks them, and assert the *correct* location so the fix flips them.

Call-time sites whose trigger is a single expression run through a one-line ``lambda`` defined in the parametrize list:
the lambda frame is the user frame, so the expected line is the lambda's own first line.  Statement-shaped triggers
(``+=``, ``with``, ``class``, ``await``) and sites that need setup are spelled out in their own tests and capture the
expected line just before the warned statement.

"""

import functools
import inspect
import warnings
from typing import Any, Callable

import pytest

import tests.collection_modules.old_math as old_math
from tests import collection_deprecate
from tests.collection_deprecate import (
    LocDescriptorHost,
    LocProxyBase,
    loc_args_remap,
    loc_async_notify,
    loc_forward,
    loc_notify,
    loc_proxy_callable,
    loc_proxy_mapping,
    loc_proxy_number,
    loc_stack_notify_over_callable,
    loc_stack_remap_over_notify,
    loc_stack_remap_over_remap,
    make_loc_class_dispatch_notice,
    make_loc_decoration_misconfig,
    make_loc_legacy_none_sentinel,
    make_loc_missing_deprecated_in,
    make_loc_positional_only_target,
    make_loc_side_effect_box_proxy,
)
from tests.collection_misconfigured import args_only_no_mapping_deprecation

_XFAIL_CORE03_INNER = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="CORE-03: the inner layer of a supported stacked @deprecated warns from _dispatch.py, not the caller",
)
_XFAIL_CORE03_COLLISION = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="CORE-03: the inner stacked layer's argument-collision UserWarning points into _dispatch.py",
)
_XFAIL_CORE24 = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="CORE-24: the front-door legacy-sentinel FutureWarning points into deprecation.py, not the decoration site",
)


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
                marks=_XFAIL_CORE03_INNER,
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
                marks=_XFAIL_CORE03_INNER,
            ),
            pytest.param(
                lambda: loc_stack_remap_over_remap(b=1, c=2),
                UserWarning,
                "were supplied",
                id="stacked-args-collision",
                marks=_XFAIL_CORE03_COLLISION,
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
        ],
    )
    def test_points_at_calling_line(self, operation: Callable[[], Any], category: type[Warning], match: str) -> None:
        """The warning reports this file and the line of the expression that used the deprecated object.

        A user filters warnings per module or turns them into errors to find the code that still needs migrating; both
        only work when the reported location is the user's own line.  Every expression-shaped warn site of the
        callable, proxy, module and descriptor paths is exercised once, and stacked wrappers are checked layer by layer
        so the outer layer's correct location stays pinned while the inner layer's known defect is tracked.

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
                marks=_XFAIL_CORE24,
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
