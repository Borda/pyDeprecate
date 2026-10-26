"""Unit tests for private helpers in :mod:`deprecate.audit._report`."""

import functools
import warnings
from collections.abc import Callable
from typing import Any

import pytest

import tests.collections.misconfigured as clean_module
from deprecate.audit._report import _format_report_target
from deprecate.proxy import deprecated_class
from tests.collections.api_current import (
    CallableAdder,
    NewCls,
    NonStringNameCallable,
    callable_adder_target,
    nested_partial_target,
    new_scale,
    partial_new_cls_target,
    partial_target,
)


class TestFormatReportProxyTarget:
    """_format_report_target reads a chained-proxy target statically, never via dynamic ``getattr``."""

    def test_chained_proxy_target_formatted_statically(self) -> None:
        """A target that is itself a deprecated_class proxy is formatted by its declared name, silently.

        When a deprecated alias forwards to *another* deprecated alias (the chain
        ``validate_deprecation_chains`` exists to flag), rendering its report row must show the immediate
        target's real declared name — not a fabricated path spliced from the proxy class's ``__module__``
        and the innermost target's ``__qualname__`` — and must not burn the chained proxy's warn budget
        from inside the audit tooling.
        """
        final_cls = type("FinalApi", (), {})
        mid: Any = deprecated_class(target=final_cls, deprecated_in="1.0", remove_in="2.0")(type("MidApi", (), {}))
        old = deprecated_class(target=mid, deprecated_in="1.0", remove_in="2.0")(type("OldApi", (), {}))
        target = object.__getattribute__(old, "__deprecation_config__").target

        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning emitted during formatting fails the test
            formatted = _format_report_target(target)

        mid_cfg = object.__getattribute__(mid, "_DeprecatedProxy__config")
        assert formatted == "MidApi"
        assert mid_cfg.state.warned_calls == 0

    def test_legacy_proxy_target_formatted_through_public_fallback(self) -> None:
        """Format a proxy target that carries only the pre-v0.13 metadata attribute.

        Mixed-version reports can contain a chained proxy created by an older pyDeprecate installation. Rendering
        must preserve its declared alias name without directly reading the absent new attribute or consuming a warning.
        """
        target = clean_module.make_legacy_metadata_proxy()

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            formatted = _format_report_target(target)

        proxy_config = object.__getattribute__(target, "_DeprecatedProxy__config")
        assert formatted == "LegacyClass"
        assert proxy_config.state.warned_calls == 0


class TestFormatReportCallableTarget:
    """_format_report_target names a callable target by a stable dotted path, as the runtime warning does."""

    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            pytest.param(new_scale, "tests.collections.api_current.new_scale", id="function"),
            pytest.param(NewCls, "tests.collections.api_current.NewCls", id="class"),
            pytest.param(NewCls.__init__, "tests.collections.api_current.NewCls.__init__", id="method-keeps-class"),
            pytest.param(partial_target, "tests.collections.api_current.new_scale", id="partial"),
            pytest.param(nested_partial_target, "tests.collections.api_current.new_scale", id="nested-partial"),
            pytest.param(partial_new_cls_target, "tests.collections.api_current.NewCls", id="partial-of-class"),
            pytest.param(
                functools.partial(CallableAdder.__call__),
                "tests.collections.api_current.CallableAdder.__call__",
                id="partial-of-method-keeps-class",
            ),
            pytest.param(callable_adder_target, "tests.collections.api_current.CallableAdder", id="callable-instance"),
            pytest.param(
                NonStringNameCallable(), "tests.collections.api_current.NonStringNameCallable", id="non-string-name"
            ),
        ],
    )
    def test_target_named_without_memory_address(self, target: Callable[..., Any], expected: str) -> None:
        """A partial or callable-instance target reports the same name on every run, never an address-bearing repr.

        The warning at call time names a ``functools.partial`` replacement by the callable it wraps and a callable
        instance by its type. The audit table and ``--report-json`` row for the same target must agree: a repr such
        as ``functools.functools.partial(<function new at 0x...>)`` differs on every run and cannot be diffed or
        matched against the warning. A method keeps its class in the name, since ``__qualname__`` stays the preferred
        source whenever the callable has one.
        """
        assert _format_report_target(target) == expected
