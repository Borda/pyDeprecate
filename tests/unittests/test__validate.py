"""Unit tests for decoration-time target resolution and validation (:mod:`deprecate._validate`)."""

import re
import warnings
from dataclasses import replace
from typing import Any, Callable, Union, cast

import pytest

from deprecate import TargetMode, assert_no_warnings, deprecated, void
from deprecate._types import get_deprecation_config
from deprecate._validate import (
    _STACKING_MESSAGES,
    _find_class_body_qualname,
    _normalize_target,
    _reject_bare_decorator,
    _resolve_legacy_target,
    _stacking_kind,
    _validate_callable_mode,
    _warn_stacking_misconfiguration,
)
from tests.collections.api_current import (
    double_value,
    positional_only_target,
    stacked_inner_target,
    stacked_outer_target,
)
from tests.collections.api_deprecated import (
    CrossGuardModuleLevel,
    CrossGuardOldClass,
    CrossGuardSameClass,
    StaticGuardNewClass,
    StaticGuardOldClass,
    loc_stack_callable_inner,
    loc_stack_notify_inner,
    loc_stack_remap_inner,
)


class TestCrossClassMethodGuard:
    """@deprecated warns when target is a method on a different class."""

    def test_raises_for_cross_class_method_target(self) -> None:
        """Forwarding to a method on a different class raises TypeError at decoration time.

        The misconfigured classes are defined inline (not in api_deprecated.py) because placing ``@deprecated``
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

        Regression test: a staticmethod receives no ``self``, so the cross-class guard's rationale ("self
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

        # Guard returned silently: decoration completed and attached deprecation metadata rather than raising.
        # (The synthetic ``replacement`` fixture rejects the forwarded ``self``, so only decoration is asserted here.)
        assert hasattr(RealOwner.old_method, "__deprecation_config__")

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

        # Decoration succeeded and the wrapper forwards to the same-class target: calling it warns and returns 7.
        with pytest.warns(FutureWarning):
            result = MyClass().old_method(7)
        assert result == 7

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


class TestNormalizeTargetInvalidInputs:
    """_normalize_target passes unrecognised non-class values through unchanged.

    The inline ``@deprecated`` decorator in :meth:`test_invalid_target_source_body_runs`
    is parametrize-coupled (``target=bad_target`` resolves from the parametrize fixture),
    so it cannot be moved to :mod:`tests.collections.api_deprecated` — one wrapper-per-bad-value
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
        assert record[0].filename.endswith("test__validate.py")

    def test_args_remap_over_callable_warns(self) -> None:
        """ARGS_REMAP outer stacked over callable-target inner emits ``UserWarning``."""
        inner = self._make_source(target=stacked_outer_target, deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(UserWarning, match="ARGS_REMAP.*stacked over a callable") as record:
            deprecated(target=TargetMode.ARGS_REMAP, deprecated_in="2.0", remove_in="3.0", args_mapping={"x": "y"})(
                inner
            )
        assert record[0].filename.endswith("test__validate.py")

    def test_notify_over_notify_warns(self) -> None:
        """Duplicate NOTIFY layers emit ``UserWarning`` at decoration time."""
        inner = self._make_source(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(UserWarning, match="duplicate.*NOTIFY") as record:
            deprecated(target=TargetMode.NOTIFY, deprecated_in="2.0", remove_in="3.0")(inner)
        assert record[0].filename.endswith("test__validate.py")

    def test_notify_over_args_remap_warns_with_order_hint(self) -> None:
        """NOTIFY outer + ARGS_REMAP inner (wrong order) emits ``UserWarning`` with order hint."""
        inner = self._make_source(
            target=TargetMode.ARGS_REMAP, deprecated_in="1.0", remove_in="2.0", args_mapping={"x": "y"}
        )
        with pytest.warns(UserWarning, match="Reverse the decorator order") as record:
            deprecated(target=TargetMode.NOTIFY, deprecated_in="2.0", remove_in="3.0")(inner)
        assert record[0].filename.endswith("test__validate.py")

    def test_callable_over_notify_warns(self) -> None:
        """Callable-target outer stacked over NOTIFY inner emits ``UserWarning``."""
        inner = self._make_source(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
        with pytest.warns(UserWarning, match="callable target stacked over.*NOTIFY") as record:
            deprecated(target=stacked_outer_target, deprecated_in="2.0", remove_in="3.0")(inner)
        assert record[0].filename.endswith("test__validate.py")

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


_STACK_OUTER_TARGETS: dict[str, Union[TargetMode, Callable[..., Any]]] = {
    "callable": double_value,
    "notify": TargetMode.NOTIFY,
    "args_remap": TargetMode.ARGS_REMAP,
    "other": TargetMode.ATTRS_REMAP,
}


_STACK_INNER_SOURCES: dict[str, Callable[..., Any]] = {
    "callable": loc_stack_callable_inner,
    "notify": loc_stack_notify_inner,
    "args_remap": loc_stack_remap_inner,
}


_STACK_UNSUPPORTED = "has an unsupported @deprecated stacking combination."


class TestStackingTable:
    """The (outer kind, inner kind) table behind ``_warn_stacking_misconfiguration``."""

    def test_covers_every_kind_pair(self) -> None:
        """The table has exactly one entry for every pair of the four target kinds.

        A missing pair would raise ``KeyError`` at decoration time for that stack, so the table must stay complete
        when a kind is added.

        """
        kinds = set(_STACK_OUTER_TARGETS)
        assert set(_STACKING_MESSAGES) == {(outer, inner) for outer in kinds for inner in kinds}

    @pytest.mark.parametrize("kind", list(_STACK_OUTER_TARGETS))
    def test_kind_of_target(self, kind: str) -> None:
        """Each representative target maps to its own kind.

        The table lookup is only as correct as the classification feeding it: a callable, ``NOTIFY``,
        ``ARGS_REMAP`` and any other mode each land in their own row.

        """
        assert _stacking_kind(_STACK_OUTER_TARGETS[kind]) == kind

    @pytest.mark.parametrize(
        ("outer_kind", "inner_kind"),
        [
            pytest.param("args_remap", "args_remap", id="args_remap-over-args_remap"),
            pytest.param("args_remap", "notify", id="args_remap-over-notify"),
            pytest.param("notify", "callable", id="notify-over-callable"),
        ],
    )
    def test_supported_pair_is_silent(self, outer_kind: str, inner_kind: str) -> None:
        """The three supported stacks decorate without any warning.

        Maintainers layer an argument rename over another rename or over a NOTIFY layer, or a NOTIFY layer over a
        forwarding wrapper; none of these may produce a decoration-time ``UserWarning``.

        """
        with assert_no_warnings(UserWarning):
            _warn_stacking_misconfiguration(_STACK_INNER_SOURCES[inner_kind], _STACK_OUTER_TARGETS[outer_kind])

    @pytest.mark.parametrize(
        ("outer_kind", "inner_kind", "expected"),
        [
            pytest.param(
                "callable",
                "callable",
                "has a callable target stacked over another callable-target @deprecated.",
                id="callable-over-callable",
            ),
            pytest.param(
                "callable",
                "notify",
                "has a callable target stacked over @deprecated(NOTIFY).",
                id="callable-over-notify",
            ),
            pytest.param(
                "callable",
                "args_remap",
                "has a callable target stacked over @deprecated(ARGS_REMAP).",
                id="callable-over-args_remap",
            ),
            pytest.param("notify", "notify", "has duplicate @deprecated(NOTIFY) layers.", id="notify-over-notify"),
            pytest.param(
                "notify",
                "args_remap",
                "has @deprecated(NOTIFY) stacked over @deprecated(ARGS_REMAP).",
                id="notify-over-args_remap",
            ),
            pytest.param(
                "args_remap",
                "callable",
                "has @deprecated(ARGS_REMAP) stacked over a callable-target @deprecated.",
                id="args_remap-over-callable",
            ),
            pytest.param("other", "callable", _STACK_UNSUPPORTED, id="other-over-callable"),
            pytest.param("other", "notify", _STACK_UNSUPPORTED, id="other-over-notify"),
            pytest.param("other", "args_remap", _STACK_UNSUPPORTED, id="other-over-args_remap"),
        ],
    )
    def test_unsupported_pair_warns(self, outer_kind: str, inner_kind: str, expected: str) -> None:
        """Each unsupported stack emits one ``UserWarning`` naming the source, pointing at the caller.

        The message text is a migration hint users act on, so every row keeps its exact opening sentence and the
        shared ``v1.0`` sunset sentence, and the warning is attributed to the decorating code, not to pyDeprecate.

        """
        source = _STACK_INNER_SOURCES[inner_kind]
        with pytest.warns(UserWarning, match=re.escape(expected)) as caught:
            _warn_stacking_misconfiguration(source, _STACK_OUTER_TARGETS[outer_kind])
        assert len(caught) == 1
        assert str(caught[0].message).startswith(f"'{source.__name__}' {expected}")
        assert str(caught[0].message).endswith(" Will be `TypeError` in `v1.0`.")
        assert caught[0].filename == __file__

    @pytest.mark.parametrize("outer_kind", list(_STACK_OUTER_TARGETS))
    def test_other_inner_kind_warns(self, outer_kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """An inner layer whose stored mode is neither callable, NOTIFY nor ARGS_REMAP always gets the generic warning.

        The public decorator rejects such modes, so this row is a defensive fallback: inner metadata is rewritten to
        the proxy-only ``ATTRS_REMAP`` mode instead of building a wrapper the decorator would refuse.

        """
        source = loc_stack_notify_inner
        config = get_deprecation_config(source)
        assert config is not None
        monkeypatch.setattr(source, "__deprecation_config__", replace(config, target=TargetMode.ATTRS_REMAP))
        with pytest.warns(UserWarning, match=re.escape(_STACK_UNSUPPORTED)) as caught:
            _warn_stacking_misconfiguration(source, _STACK_OUTER_TARGETS[outer_kind])
        assert len(caught) == 1
        assert str(caught[0].message) == (f"'{source.__name__}' {_STACK_UNSUPPORTED} Will be `TypeError` in `v1.0`.")


class TestBareDecoratorGuard:
    """Bare ``@deprecated`` (no parentheses) must fail with a guiding message."""

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
            old(5)  # type: ignore[type-var]  # bare decorator rebinds ``old`` to the packing decorator

    def test_helper_accepts_plain_callable(self) -> None:
        """The guard helper is a no-op for an ordinary callable so correct decoration is never disturbed."""

        def real(x: int) -> int:
            return x

        _reject_bare_decorator(real)  # no exception raised = a plain callable is accepted


class TestClassBodyQualnameWalk:
    """The cross-class guard locates the class body via a bounded frame walk, not a fixed depth."""

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
        walk locates the class body regardless of intervening descriptor frames.
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


class TestFromLegacyErrors:
    """_resolve_legacy_target TypeError for non-sentinel inputs."""

    @pytest.mark.parametrize(
        "bad_value",
        [
            pytest.param(42, id="int"),
            pytest.param("notify", id="str"),
            pytest.param(object(), id="object"),
            pytest.param(TargetMode.NOTIFY, id="TargetMode_member"),
        ],
    )
    def test_raises_type_error_for_non_sentinel(self, bad_value: object) -> None:
        """A legacy ``target`` that is not None, True, or False raises a TypeError naming the user-facing input.

        A caller passes an unsupported value where the legacy boolean ``target`` sentinel is expected.  The error
        must describe that input, not an internal helper, so it stays accurate however the private resolver is named.
        """
        with pytest.raises(TypeError, match="^Legacy `target` accepts only None, True, or False; got "):
            _resolve_legacy_target(cast(bool, bad_value), emit=False)


class TestTargetModeValidate:
    """_validate_callable_mode — direct unit tests for all misconfig combinations."""

    def test_args_remap_without_mapping_returns_true(self) -> None:
        """ARGS_REMAP with no args_mapping is misconfigured; returns True."""
        assert _validate_callable_mode(TargetMode.ARGS_REMAP, "fn", args_mapping=None, emit=False) is True

    def test_notify_with_args_mapping_returns_true(self) -> None:
        """NOTIFY with args_mapping is misconfigured; returns True."""
        assert _validate_callable_mode(TargetMode.NOTIFY, "fn", args_mapping={"old": "new"}, emit=False) is True

    def test_notify_with_args_extra_returns_true(self) -> None:
        """NOTIFY with args_extra is misconfigured; returns True."""
        assert _validate_callable_mode(TargetMode.NOTIFY, "fn", args_extra={"bias": 1}, emit=False) is True

    def test_valid_notify_returns_false(self) -> None:
        """NOTIFY with no extra kwargs is valid; returns False."""
        assert _validate_callable_mode(TargetMode.NOTIFY, "fn", emit=False) is False

    def test_valid_args_remap_returns_false(self) -> None:
        """ARGS_REMAP with args_mapping is valid; returns False."""
        assert _validate_callable_mode(TargetMode.ARGS_REMAP, "fn", args_mapping={"old": "new"}, emit=False) is False

    def test_misconfig_emits_user_warning(self) -> None:
        """_validate emits UserWarning when misconfigured (emit=True, the default)."""
        with pytest.warns(UserWarning, match="args_mapping"):
            _validate_callable_mode(TargetMode.NOTIFY, "my_fn", args_mapping={"old": "new"})

    def test_valid_emits_no_warning(self) -> None:
        """_validate does not emit any warning when configuration is valid."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            _validate_callable_mode(TargetMode.NOTIFY, "my_fn")


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
