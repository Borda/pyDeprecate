"""Tests for ``deprecated_class`` stacking — two or more decorators on the same class.

Covers two orthogonal capabilities:

1. **Stacking** — two or more ``@deprecated_class`` decorators on the same class, each with its
   own ``deprecated_in``/``remove_in``.  ``isinstance()`` and ``issubclass()`` resolve through
   the full proxy chain; instantiation emits at most one global warning; per-attribute warnings
   carry the version string from the correct proxy layer.

2. **Stacking combinations** — blanket-outer + ATTRS_REMAP-inner, three-layer chains, canonical
   attribute silent passthrough, setattr/delattr propagation, ATTRS_REMAP outer + ARGS_REMAP inner.
"""

import warnings

import pytest

from deprecate.proxy import _DeprecatedProxy, deprecated_class
from tests.collection_deprecate import DepCombinedSingleCall, DepSelfCombinedTwoLayer, StackedAttrProxy
from tests.collection_targets import (
    SelfDeprecatedModel,
    StackingArgsAttrsBase,
    StackingBlanketBase,
    StackingDeepBase,
    StackingLeafBase,
    StackingMutableBase,
    StackingSilentBase,
)
from tests.collection_targets import StackedAttrTarget as _StackedAttrTarget

# ---------------------------------------------------------------------------
# Feature 1 & 2: Stacking two ATTRS_REMAP deprecated_class decorators
# ---------------------------------------------------------------------------


class TestStackedDeprecatedClass:
    """Two ``deprecated_class(attrs_mapping=...)`` decorators applied to the same class.

    Each decorator carries a different ``deprecated_in``/``remove_in`` pair so that
    callers migrating to the new API get accurate version information per attribute.

    ``StackedAttrProxy`` is a module-level singleton defined in ``collection_deprecate``.
    Both layers use ``stream=None``, which causes ``_warn()`` to return before incrementing
    ``_ProxyConfig.warned`` or ``warned_args`` — so warning state does not accumulate across
    tests despite the shared reference.  If ``stream`` is ever set to a non-``None`` value,
    tests become order-sensitive because ``num_warns=1`` (default) would silence later tests.

    """

    def test_stacked_proxy_outer_attr_access(self) -> None:
        """Outer layer: accessing ``old_attr`` returns the value from ``new_attr``.

        The outer proxy maps ``old_attr -> new_attr``.  The redirected attribute resolves
        through the inner proxy silently (``new_attr`` is not in the inner mapping) and
        returns the canonical value on the target class.

        """
        value = StackedAttrProxy.old_attr  # type: ignore[attr-defined]
        assert value == "value_b"

    def test_stacked_proxy_inner_attr_access(self) -> None:
        """Inner layer: accessing ``older_attr`` returns the value from ``newer_attr``.

        The inner proxy maps ``older_attr -> newer_attr``.  The outer proxy has no entry for
        ``older_attr`` so it passes through to the inner, which warns (suppressed by ``stream=None``)
        and redirects.

        """
        value = StackedAttrProxy.older_attr  # type: ignore[attr-defined]
        assert value == "value_new"

    def test_stacked_proxy_isinstance_works(self) -> None:
        """``isinstance(instance, StackedAttrProxy)`` returns ``True`` after Blocker 1 fix.

        Without the fix, ``__instancecheck__`` returns ``False`` because it only delegates
        when ``active`` is a ``type``.  After the fix it also delegates when ``active`` is
        a ``_DeprecatedProxy``, triggering recursive delegation down to the real class.

        """
        instance = StackedAttrProxy()
        assert isinstance(instance, _StackedAttrTarget)
        assert isinstance(instance, StackedAttrProxy)  # type: ignore[arg-type]  # <-- this is the Blocker 1 assertion

    def test_stacked_proxy_no_double_warn_on_instantiation(self) -> None:
        """Instantiating a two-layer ATTRS_REMAP proxy emits at most one warning, not two.

        Before Blocker 2 fix, both the outer AND inner ATTRS_REMAP proxy fall through to
        ``self._warn()`` in ``__call__``, producing two ``FutureWarning`` emissions.  After the
        fix the outer proxy delegates to the inner without its own global warn; the inner
        executes normally.

        """
        outer_proxy = deprecated_class(attrs_mapping={"old_attr": "new_attr"}, deprecated_in="1.0", remove_in="2.0")(
            deprecated_class(attrs_mapping={"older_attr": "newer_attr"}, deprecated_in="0.9", remove_in="1.0")(
                _StackedAttrTarget
            )
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            outer_proxy()  # instantiate

        future_warns = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert len(future_warns) <= 1, (
            f"Expected at most 1 FutureWarning on instantiation; got {len(future_warns)}: "
            f"{[str(w.message) for w in future_warns]}"
        )

    def test_stacked_outer_attr_warns_with_correct_version(self) -> None:
        """Outer proxy emits ``deprecated_in='1.0'`` for the outer-layer attribute.

        Each stacked layer retains its own ``deprecated_in``/``remove_in``.  The warning
        message for ``old_attr`` must reference ``1.0``; the warning for ``older_attr`` must
        reference ``0.9``.

        """
        outer = deprecated_class(
            attrs_mapping={"old_attr": "new_attr"}, deprecated_in="1.0", remove_in="2.0", num_warns=-1
        )(
            deprecated_class(
                attrs_mapping={"older_attr": "newer_attr"}, deprecated_in="0.9", remove_in="1.0", num_warns=-1
            )(_StackedAttrTarget)
        )

        with pytest.warns(FutureWarning, match="1.0"):
            _ = outer.old_attr  # type: ignore[attr-defined]

        with pytest.warns(FutureWarning, match="0.9"):
            _ = outer.older_attr  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Stacking combination matrix
# ---------------------------------------------------------------------------


class TestStackingCombinations:
    """Stacking combination matrix for ``deprecated_class`` proxy layers.

    Exercises combinations beyond the basic two-ATTRS_REMAP case: three layers, blanket-outer
    wrapping selective-inner, canonical attribute silent passthrough, setattr propagation,
    and ATTRS_REMAP outer combined with ARGS_REMAP inner.

    """

    def test_three_layer_stacking_deepest_attr(self) -> None:
        """Three stacked ATTRS_REMAP proxies: deepest-layer attr resolves correctly.

        A three-layer stack deprecates ``oldest_attr`` (layer 3, innermost),
        ``older_attr`` (layer 2), and ``old_attr`` (layer 1, outermost).  Accessing
        ``oldest_attr`` must pass through layers 1 and 2 silently and then be handled by
        layer 3 with its own ``deprecated_in`` version.

        """
        proxy = deprecated_class(
            attrs_mapping={"old_attr": "canonical"}, deprecated_in="1.2", remove_in="2.0", num_warns=-1
        )(
            deprecated_class(
                attrs_mapping={"older_attr": "canonical"}, deprecated_in="1.0", remove_in="2.0", num_warns=-1
            )(
                deprecated_class(
                    attrs_mapping={"oldest_attr": "canonical"}, deprecated_in="0.8", remove_in="1.0", num_warns=-1
                )(StackingDeepBase)
            )
        )

        with pytest.warns(FutureWarning, match="0.8"):
            value = proxy.oldest_attr  # type: ignore[attr-defined]
        assert value == "deep"

        with pytest.warns(FutureWarning, match="1.0"):
            value = proxy.older_attr  # type: ignore[attr-defined]
        assert value == "deep"

        with pytest.warns(FutureWarning, match="1.2"):
            value = proxy.old_attr  # type: ignore[attr-defined]
        assert value == "deep"

    def test_three_layer_isinstance_and_issubclass(self) -> None:
        """Three-layer stack: ``isinstance`` and ``issubclass`` resolve to the original class.

        Recursive ``__instancecheck__`` / ``__subclasscheck__`` delegation must bottom out at
        the original class regardless of the number of proxy layers.

        """
        proxy = deprecated_class(attrs_mapping={"a": "canonical"}, deprecated_in="1.2", remove_in="2.0", stream=None)(
            deprecated_class(attrs_mapping={"b": "canonical"}, deprecated_in="1.0", remove_in="2.0", stream=None)(
                deprecated_class(attrs_mapping={"c": "canonical"}, deprecated_in="0.8", remove_in="1.0", stream=None)(
                    StackingLeafBase
                )
            )
        )

        instance = proxy()
        assert isinstance(instance, StackingLeafBase)
        assert isinstance(instance, proxy)  # type: ignore[arg-type]

        class _Sub(StackingLeafBase):
            pass

        assert issubclass(_Sub, proxy)

    def test_canonical_attr_passes_through_both_layers_silently(self) -> None:
        """An attribute not in either proxy's mapping passes through both layers without warning.

        The outer proxy has ``attrs_mapping={"old_attr": ...}`` and the inner has
        ``attrs_mapping={"older_attr": ...}``.  Accessing ``canonical`` (listed in neither)
        must not emit any ``FutureWarning`` from either layer.

        """
        proxy = deprecated_class(attrs_mapping={"old_attr": "canonical"}, deprecated_in="1.0", remove_in="2.0")(
            deprecated_class(attrs_mapping={"older_attr": "canonical"}, deprecated_in="0.9", remove_in="1.0")(
                StackingSilentBase
            )
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            value = proxy.canonical

        future_warns = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert future_warns == [], f"Expected no FutureWarning for canonical attr; got {future_warns}"
        assert value == "silent"

    def test_blanket_outer_wrapping_selective_inner(self) -> None:
        """Blanket-warn outer + ATTRS_REMAP inner: outer always warns, inner warns selectively.

        When the outer proxy has no ``attrs_mapping`` (blanket mode) and the inner uses
        ``attrs_mapping``, the outer fires on every attribute access; the inner fires only for
        its listed deprecated names.  Accessing the canonical name through the stack emits one
        warning (outer blanket); accessing a deprecated alias emits two (outer blanket + inner
        selective).

        """
        inner = deprecated_class(attrs_mapping={"color": "colour"}, deprecated_in="1.0", remove_in="2.0", num_warns=-1)(
            StackingBlanketBase
        )

        outer = deprecated_class(deprecated_in="2.0", remove_in="3.0", num_warns=-1)(inner)  # type: ignore[arg-type]

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = outer.colour  # canonical — outer warns, inner silent
        future_warns = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert len(future_warns) == 1

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = outer.color  # type: ignore[attr-defined]  # deprecated alias — outer + inner warn
        future_warns = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert len(future_warns) == 2

    def test_setattr_propagates_through_stacked_proxy(self) -> None:
        """Writes to a deprecated alias propagate through a stacked proxy to the canonical attr.

        The outer proxy maps ``old_attr -> new_attr``.  Writing ``proxy.old_attr = value``
        must warn (for the outer layer) and set the value on the actual class using the
        canonical name ``new_attr``.

        """
        proxy = deprecated_class(
            attrs_mapping={"old_attr": "new_attr"}, deprecated_in="1.0", remove_in="2.0", num_warns=-1
        )(
            deprecated_class(
                attrs_mapping={"older_attr": "new_attr"},
                deprecated_in="0.9",
                remove_in="1.0",
                num_warns=-1,
                stream=None,
            )(StackingMutableBase)
        )

        with pytest.warns(FutureWarning, match="old_attr"):
            proxy.old_attr = "updated"  # type: ignore[attr-defined]

        assert proxy.new_attr == "updated"

    def test_three_layer_instantiation_emits_at_most_one_warning(self) -> None:
        """Three stacked ATTRS_REMAP proxies: instantiation emits at most one FutureWarning.

        Each proxy layer is ATTRS_REMAP (``attrs_mapping`` only, no callable target).  On
        instantiation the outer proxy detects the wrapped object is itself a
        ``_DeprecatedProxy`` and delegates without emitting its own global warning.  The
        chain bottoms out at ``StackingLeafBase``, which emits exactly one warning.  The
        count must stay at or below 1 regardless of chain depth.
        """
        proxy = deprecated_class(attrs_mapping={"a": "canonical"}, deprecated_in="1.2", remove_in="2.0", num_warns=-1)(
            deprecated_class(attrs_mapping={"b": "canonical"}, deprecated_in="1.0", remove_in="2.0", num_warns=-1)(
                deprecated_class(attrs_mapping={"c": "canonical"}, deprecated_in="0.8", remove_in="1.0", num_warns=-1)(
                    StackingLeafBase
                )
            )
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            proxy()

        future_warns = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert len(future_warns) <= 1, (
            f"Expected at most 1 FutureWarning on instantiation of three-layer ATTRS_REMAP stack; "
            f"got {len(future_warns)}: {[str(w.message) for w in future_warns]}"
        )

    def test_stacked_attrs_remap_outer_args_remap_inner(self) -> None:
        """ATTRS_REMAP outer + ARGS_REMAP inner: each layer warns only for its own concern.

        The outer proxy deprecates an attribute name (``old_attr``); the inner deprecates a
        constructor argument name (``old_arg``).  Accessing ``old_attr`` must emit exactly one
        ``FutureWarning`` (from the outer, v1.0) — the inner ARGS_REMAP proxy must NOT emit a
        spurious global warning on attribute access.  Calling with ``old_arg`` must emit exactly
        one warning (from the inner, v0.9).

        """
        outer = deprecated_class(
            attrs_mapping={"old_attr": "new_attr"}, deprecated_in="1.0", remove_in="2.0", num_warns=-1
        )(
            deprecated_class(args_mapping={"old_arg": "new_arg"}, deprecated_in="0.9", remove_in="1.0", num_warns=-1)(
                StackingArgsAttrsBase
            )
        )

        with pytest.warns(FutureWarning, match="1.0") as record:
            value = outer.old_attr  # type: ignore[attr-defined]
        future_warns = [w for w in record if issubclass(w.category, FutureWarning)]
        assert len(future_warns) == 1
        assert value == "b"

        with pytest.warns(FutureWarning, match="0.9") as record:
            inst = outer(old_arg=7)  # type: ignore[call-arg]
        future_warns = [w for w in record if issubclass(w.category, FutureWarning)]
        assert len(future_warns) == 1
        assert inst.new_arg == 7

        assert isinstance(inst, StackingArgsAttrsBase)
        assert isinstance(inst, outer)  # type: ignore[arg-type]


class TestDeprecatedProxyNonTypeFallback:
    """``_DeprecatedProxy`` wrapping a non-type object: ``__instancecheck__`` / ``__subclasscheck__`` fallback.

    When ``_DeprecatedProxy._get_active()`` returns something that is neither a ``type`` nor a
    ``_DeprecatedProxy``, both dunder methods must return ``False`` without raising.  This guards
    against proxy misuse (e.g. wrapping a plain dict instance rather than a class) and ensures the
    implementation short-circuits cleanly rather than forwarding to ``type.__instancecheck__``, which
    would raise ``TypeError``.
    """

    def test_isinstance_returns_false_when_active_is_not_a_type(self) -> None:
        """``isinstance(obj, proxy)`` returns ``False`` when the proxy wraps a non-type object.

        ``_DeprecatedProxy`` with a plain dict as ``obj`` has no ``type`` to delegate to.
        ``__instancecheck__`` must return ``False`` rather than raising ``TypeError``.
        """
        proxy = _DeprecatedProxy(
            obj={},
            name="legacy_dict",
            deprecated_in="1.0",
            remove_in="2.0",
        )
        result = isinstance({}, proxy)  # type: ignore[arg-type]
        assert result is False

    def test_issubclass_returns_false_when_active_is_not_a_type(self) -> None:
        """``issubclass(cls, proxy)`` returns ``False`` when the proxy wraps a non-type object.

        ``_DeprecatedProxy`` with a plain dict as ``obj`` has no ``type`` to delegate to.
        ``__subclasscheck__`` must return ``False`` rather than raising ``TypeError``.
        """
        proxy = _DeprecatedProxy(
            obj={},
            name="legacy_dict",
            deprecated_in="1.0",
            remove_in="2.0",
        )
        result = issubclass(int, proxy)  # type: ignore[arg-type]
        assert result is False


class TestCombinedSingleCallProxy:
    """Single ``deprecated_class()`` with both ``attrs_mapping`` and ``args_mapping`` active.

    ``DepCombinedSingleCall`` wraps ``StackingArgsAttrsBase`` with a single proxy that deprecates
    ``old_attr`` → ``new_attr`` (class-level attribute redirect) and ``old_arg`` → ``new_arg``
    (constructor kwarg rename).  Both surfaces must warn independently and resolve to the correct
    values without interfering with each other.

    ``StackingArgsAttrsBase`` carries ``new_attr: str = "b"`` as a class attribute and accepts
    ``new_arg: int = 0`` as its constructor keyword.  Both ``stream=None`` and ``num_warns=-1``
    are set on the fixture so warning budgets do not accumulate across tests sharing the
    module-level proxy constant.

    """

    def test_attr_redirect_warns_and_resolves(self) -> None:
        """Accessing ``old_attr`` via the proxy warns and redirects to ``new_attr`` on the target.

        The ``attrs_mapping`` surface intercepts ``__getattr__`` on the proxy and forwards the
        lookup to ``StackingArgsAttrsBase.new_attr``.  The returned value must equal the canonical
        class attribute; the warning must name the deprecated version pair.

        """
        with pytest.warns(FutureWarning, match="1.0"):
            value = DepCombinedSingleCall.old_attr  # type: ignore[attr-defined]
        assert value == StackingArgsAttrsBase.new_attr

    def test_constructor_kwarg_rename_warns_and_forwards(self) -> None:
        """Calling with deprecated kwarg ``old_arg`` warns and remaps to ``new_arg`` on the target.

        The ``args_mapping`` surface intercepts the ``__call__`` path and rewrites ``old_arg``
        to ``new_arg`` before forwarding the construction call to ``StackingArgsAttrsBase``.
        The returned instance must carry the passed value under ``new_arg`` and be an instance
        of the real target class.

        """
        with pytest.warns(FutureWarning, match="1.0"):
            inst = DepCombinedSingleCall(old_arg=42)  # type: ignore[call-arg]
        assert inst.new_arg == 42
        assert isinstance(inst, StackingArgsAttrsBase)

    def test_attr_and_call_surfaces_are_independent(self) -> None:
        """Exhausting the attribute warning budget does not affect the call warning budget.

        Each deprecated name maintains its own counter.  With ``num_warns=-1`` both budgets
        are unlimited; with the default ``num_warns=1`` each deprecated name warns exactly once
        independently.  This test constructs with the canonical kwarg (silent) then reads the
        deprecated attribute (warns) to verify the call path leaves the attribute counter intact.

        """
        with pytest.warns(FutureWarning):
            inst = DepCombinedSingleCall(new_arg=5)  # canonical kwarg: args_mapping silent, callable-target warns
        with pytest.warns(FutureWarning, match="old_attr"):
            value = DepCombinedSingleCall.old_attr  # type: ignore[attr-defined]
        assert inst.new_arg == 5
        assert value == StackingArgsAttrsBase.new_attr


class TestCombinedNoTargetTwoLayer:
    """Two stacked ``deprecated_class()`` decorators with no callable target on either layer.

    ``DepSelfCombinedTwoLayer`` wraps ``SelfDeprecatedModel`` with two proxies, neither
    forwarding to a different class.  The inner proxy (``args_mapping={"n_layers": "num_layers"}``,
    auto-resolved to ``ARGS_REMAP``) renames a constructor kwarg in-place.  The outer proxy
    (``attrs_mapping={"cuda": None, "gpu": "device"}``, auto-resolved to ``ATTRS_REMAP``)
    deprecates class-level attributes in-place.  When the outer proxy's ``__call__`` is invoked
    it detects the wrapped object is itself a ``_DeprecatedProxy`` and delegates directly,
    so only the inner proxy's ``args_mapping`` logic fires — no double warning.

    ``SelfDeprecatedModel`` carries ``cuda: bool = False`` and ``device: str = "cpu"`` as class
    attributes and accepts ``num_layers: int = 4`` in its constructor.  Both layers use
    ``num_warns=-1`` so warning budgets do not accumulate across tests sharing the module-level
    fixture.

    """

    def test_cuda_warn_only_serves_value(self) -> None:
        """Accessing ``cuda`` warns and still returns the class attribute value.

        The outer ``attrs_mapping`` proxy intercepts ``cuda`` with a ``None`` redirect
        (warn-only): the deprecated name is preserved on ``SelfDeprecatedModel`` and served
        unchanged while emitting a ``FutureWarning`` about its upcoming removal.

        """
        with pytest.warns(FutureWarning, match="cuda"):
            value = DepSelfCombinedTwoLayer.cuda  # type: ignore[attr-defined]
        assert value == SelfDeprecatedModel.cuda

    def test_gpu_redirects_to_device(self) -> None:
        """Accessing ``gpu`` warns and returns the value of the replacement attribute ``device``.

        The outer ``attrs_mapping`` proxy redirects ``gpu`` → ``device`` on the underlying
        class.  The returned value must equal ``SelfDeprecatedModel.device``; the warning must
        name the deprecated name.

        """
        with pytest.warns(FutureWarning, match="gpu"):
            value = DepSelfCombinedTwoLayer.gpu  # type: ignore[attr-defined]
        assert value == SelfDeprecatedModel.device

    def test_constructor_kwarg_rename_warns_and_constructs(self) -> None:
        """Calling with deprecated kwarg ``n_layers`` warns and remaps to ``num_layers``.

        The inner ``args_mapping`` proxy intercepts ``__call__`` and rewrites ``n_layers``
        → ``num_layers`` before constructing ``SelfDeprecatedModel``.  The returned instance
        must carry the passed value under ``num_layers`` and be an instance of the real class.

        """
        with pytest.warns(FutureWarning, match="1.0"):
            inst = DepSelfCombinedTwoLayer(n_layers=8)  # type: ignore[call-arg]
        assert inst.num_layers == 8
        assert isinstance(inst, SelfDeprecatedModel)

    def test_canonical_call_is_silent(self) -> None:
        """Constructing with the canonical kwarg emits no warning.

        ``DepSelfCombinedTwoLayer(num_layers=3)`` bypasses both mapping surfaces:
        the outer ``ATTRS_REMAP`` proxy delegates the call to the inner proxy without
        firing a global warning, and the inner ``ARGS_REMAP`` proxy finds no match for
        ``num_layers`` in its ``args_mapping`` keys, so construction is silent.

        """
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            inst = DepSelfCombinedTwoLayer(num_layers=3)
        assert inst.num_layers == 3

    def test_attr_and_call_surfaces_are_independent(self) -> None:
        """Attribute and constructor warning surfaces are independent.

        Accessing ``cuda`` after a silent canonical construction warns exactly once on
        the attribute surface, confirming the call path left the attribute counter intact.

        """
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            inst = DepSelfCombinedTwoLayer(num_layers=5)
        with pytest.warns(FutureWarning, match="cuda"):
            value = DepSelfCombinedTwoLayer.cuda  # type: ignore[attr-defined]
        assert inst.num_layers == 5
        assert value == SelfDeprecatedModel.cuda
