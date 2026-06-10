"""Tests for deprecated_class stacking and DeprecationEntry per-entry version metadata.

Covers three orthogonal capabilities:

1. **Stacking** — two or more ``@deprecated_class`` decorators on the same class, each with its
   own ``deprecated_in``/``remove_in``.  ``isinstance()`` and ``issubclass()`` resolve through
   the full proxy chain; instantiation emits at most one global warning; per-attribute warnings
   carry the version string from the correct proxy layer.

2. **Stacking combinations** — blanket-outer + ATTRS_REMAP-inner, three-layer chains, canonical
   attribute silent passthrough, setattr/delattr propagation, ``DeprecationEntry`` in a stacked
   layer.

3. **DeprecationEntry** — per-attribute and per-arg version overrides on a single proxy call.
"""

import sys
import warnings

import pytest

from deprecate import DeprecationEntry
from deprecate.proxy import _DeprecatedProxy, deprecated_class
from tests.collection_deprecate import (
    DeprecationEntryAttrProxy,
    StackedAttrProxy,
    depr_fn_with_entry_args_mapping,
)
from tests.collection_targets import (
    DeprEntryArgsInitTarget,
    DeprEntryMixedTarget,
    DeprEntryRedirectTarget,
    DeprEntrySizeTarget,
    StackingArgsAttrsBase,
    StackingBlanketBase,
    StackingDeepBase,
    StackingEntryBase,
    StackingLeafBase,
    StackingMutableBase,
    StackingSilentBase,
)
from tests.collection_targets import (
    V09TwoAttrClass as _V09Class,
)

# ---------------------------------------------------------------------------
# Feature 1 & 2: Stacking two ATTRS_REMAP deprecated_class decorators
# ---------------------------------------------------------------------------


class TestStackedDeprecatedClass:
    """Two ``deprecated_class(attrs_mapping=...)`` decorators applied to the same class.

    Each decorator carries a different ``deprecated_in``/``remove_in`` pair so that
    callers migrating to the new API get accurate version information per attribute.

    """

    @pytest.fixture
    def stacked_proxy(self) -> _DeprecatedProxy:
        """Two-layer stacked proxy with disjoint attrs_mapping and distinct version pairs."""
        return StackedAttrProxy  # type: ignore[return-value]

    def test_stacked_proxy_outer_attr_access(self, stacked_proxy: _DeprecatedProxy) -> None:
        """Outer layer: accessing ``old_attr`` returns the value from ``new_attr``.

        The outer proxy maps ``old_attr -> new_attr``.  The redirected attribute resolves
        through the inner proxy silently (``new_attr`` is not in the inner mapping) and
        returns the canonical value on the target class.

        """
        value = stacked_proxy.old_attr  # type: ignore[attr-defined]
        assert value == "value_b"

    def test_stacked_proxy_inner_attr_access(self, stacked_proxy: _DeprecatedProxy) -> None:
        """Inner layer: accessing ``older_attr`` returns the value from ``newer_attr``.

        The inner proxy maps ``older_attr -> newer_attr``.  The outer proxy has no entry for
        ``older_attr`` so it passes through to the inner, which warns (suppressed by ``stream=None``)
        and redirects.

        """
        value = stacked_proxy.older_attr  # type: ignore[attr-defined]
        assert value == "value_new"

    def test_stacked_proxy_isinstance_works(self, stacked_proxy: _DeprecatedProxy) -> None:
        """``isinstance(instance, stacked_proxy)`` returns ``True`` after Blocker 1 fix.

        Without the fix, ``__instancecheck__`` returns ``False`` because it only delegates
        when ``active`` is a ``type``.  After the fix it also delegates when ``active`` is
        a ``_DeprecatedProxy``, triggering recursive delegation down to the real class.

        """
        instance = stacked_proxy()
        assert isinstance(instance, _V09Class)
        assert isinstance(instance, stacked_proxy)  # type: ignore[arg-type]  # <-- this is the Blocker 1 assertion

    def test_stacked_proxy_no_double_warn_on_instantiation(self, stacked_proxy: _DeprecatedProxy) -> None:
        """Instantiating a two-layer ATTRS_REMAP proxy emits at most one warning, not two.

        Before Blocker 2 fix, both the outer AND inner ATTRS_REMAP proxy fall through to
        ``self._warn()`` in ``__call__``, producing two ``FutureWarning`` emissions.  After the
        fix the outer proxy delegates to the inner without its own global warn; the inner
        executes normally.

        """
        outer_proxy = deprecated_class(attrs_mapping={"old_attr": "new_attr"}, deprecated_in="1.0", remove_in="2.0")(
            deprecated_class(attrs_mapping={"older_attr": "newer_attr"}, deprecated_in="0.9", remove_in="1.0")(
                _V09Class
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
            )(_V09Class)
        )

        with pytest.warns(FutureWarning, match="1.0"):
            _ = outer.old_attr  # type: ignore[attr-defined]

        with pytest.warns(FutureWarning, match="0.9"):
            _ = outer.older_attr  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Feature 3: DeprecationEntry for per-entry version overrides (single proxy)
# ---------------------------------------------------------------------------


class TestDeprecationEntry:
    """Single ``deprecated_class`` call with per-attribute version overrides via ``DeprecationEntry``.

    ``DeprecationEntry`` carries ``(target, deprecated_in, remove_in)`` so that each entry in
    ``attrs_mapping`` or ``args_mapping`` can express an independent deprecation timeline.
    The proxy-level ``deprecated_in``/``remove_in`` acts as a fallback for plain ``str`` entries.

    """

    def test_deprecation_entry_import(self) -> None:
        """``DeprecationEntry`` is importable from the public ``deprecate`` namespace."""
        assert DeprecationEntry is not None

    def test_deprecation_entry_per_attr_version_in_warning(self) -> None:
        """Per-entry ``deprecated_in`` appears in the warning message for that attribute.

        When ``attrs_mapping={"old_attr": DeprecationEntry("new_attr", deprecated_in="0.9")}``,
        accessing ``proxy.old_attr`` must emit a ``FutureWarning`` mentioning ``0.9``, not the
        proxy-level ``deprecated_in="1.0"`` fallback.

        """
        proxy = deprecated_class(
            attrs_mapping={
                "old_attr": DeprecationEntry("new_attr", deprecated_in="0.9", remove_in="1.0"),
            },
            deprecated_in="1.0",
            remove_in="2.0",
            num_warns=-1,
        )(DeprEntryRedirectTarget)

        with pytest.warns(FutureWarning, match="0.9"):
            _ = proxy.old_attr  # type: ignore[attr-defined]

    def test_deprecation_entry_fallback_for_plain_str_entry(self) -> None:
        """Plain string entries fall back to the proxy-level ``deprecated_in``/``remove_in``.

        A mixed ``attrs_mapping`` with one ``DeprecationEntry`` and one plain string must use
        the per-entry version for the entry-typed key and the proxy-level version for the
        plain-string key.

        """
        proxy = deprecated_class(
            attrs_mapping={
                "old_attr": DeprecationEntry("new_attr", deprecated_in="0.9", remove_in="1.0"),
                "old_attr2": "new_attr2",  # plain str — uses proxy-level versions
            },
            deprecated_in="1.0",
            remove_in="2.0",
            num_warns=-1,
        )(DeprEntryMixedTarget)

        with pytest.warns(FutureWarning, match="0.9"):
            _ = proxy.old_attr  # type: ignore[attr-defined]

        with pytest.warns(FutureWarning, match="1.0"):
            _ = proxy.old_attr2  # type: ignore[attr-defined]

    def test_deprecation_entry_redirects_correctly(self) -> None:
        """``DeprecationEntry`` attribute redirect returns the canonical attribute value."""
        proxy = deprecated_class(
            attrs_mapping={
                "old_attr": DeprecationEntry("new_attr", deprecated_in="0.9", remove_in="1.0"),
            },
            deprecated_in="1.0",
            remove_in="2.0",
            stream=None,
        )(DeprEntryRedirectTarget)

        value = proxy.old_attr  # type: ignore[attr-defined]
        assert value == "canonical"

    def test_deprecation_entry_stored_in_deprecated_meta(self) -> None:
        """``DeprecationEntry`` values are preserved in ``__deprecated__.attrs_mapping``.

        Audit tools read ``DeprecationConfig.attrs_mapping`` to discover version metadata.
        Entries stored as ``DeprecationEntry`` instances must survive round-trip through the
        frozen ``DeprecationConfig``.

        """
        entry = DeprecationEntry("new_attr", deprecated_in="0.9", remove_in="1.0")
        proxy = deprecated_class(attrs_mapping={"old_attr": entry}, deprecated_in="1.0", remove_in="2.0", stream=None)(
            DeprEntryRedirectTarget
        )

        meta = object.__getattribute__(proxy, "__deprecated__")
        stored = meta.attrs_mapping["old_attr"]
        assert stored == entry

    def test_deprecation_entry_args_mapping_per_arg_version(self) -> None:
        """Per-arg ``DeprecationEntry`` in ``args_mapping`` uses the entry-level version string.

        Calling the proxy with the old argument name must emit a warning referencing the
        per-arg ``deprecated_in``, not the proxy-level fallback.

        """
        proxy = deprecated_class(
            args_mapping={
                "old_arg": DeprecationEntry("new_arg", deprecated_in="0.8", remove_in="1.0"),
            },
            deprecated_in="1.0",
            remove_in="2.0",
            num_warns=-1,
        )(DeprEntryArgsInitTarget)

        with pytest.warns(FutureWarning, match="0.8"):
            instance = proxy(old_arg=5)  # type: ignore[call-arg]

        assert instance.new_arg == 5

    def test_deprecation_entry_warn_only_attr(self) -> None:
        """``DeprecationEntry(None, ...)`` is a warn-only entry — warns but does not rename.

        A ``None`` target means "warn but still serve the attribute under the same name".  The
        per-entry ``deprecated_in`` must appear in the warning even though no redirect happens.

        """
        proxy = deprecated_class(
            attrs_mapping={
                "size": DeprecationEntry(None, deprecated_in="0.7", remove_in="1.0"),
            },
            deprecated_in="1.0",
            remove_in="2.0",
            num_warns=-1,
        )(DeprEntrySizeTarget)

        with pytest.warns(FutureWarning, match="0.7"):
            value = proxy.size  # type: ignore[attr-defined]

        assert value == 42


# ---------------------------------------------------------------------------
# Stacking combination matrix
# ---------------------------------------------------------------------------


class TestStackingCombinations:
    """Stacking combination matrix for ``deprecated_class`` proxy layers.

    Exercises combinations beyond the basic two-ATTRS_REMAP case: three layers, blanket-outer
    wrapping selective-inner, canonical attribute silent passthrough, setattr propagation, and
    ``DeprecationEntry`` mixed into a stacked layer.

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

    def test_deprecation_entry_in_stacked_outer_layer(self) -> None:
        """``DeprecationEntry`` in the outer stacked layer uses its per-entry version.

        When the outer proxy's ``attrs_mapping`` contains a ``DeprecationEntry`` with
        ``deprecated_in="1.5"``, the warning message for that attribute must reference ``1.5``
        even though the outer proxy itself has ``deprecated_in="1.0"`` and the inner proxy has
        ``deprecated_in="0.9"``.

        """
        proxy = deprecated_class(
            attrs_mapping={
                "old_attr": DeprecationEntry("canonical", deprecated_in="1.5", remove_in="2.0"),
            },
            deprecated_in="1.0",
            remove_in="2.0",
            num_warns=-1,
        )(
            deprecated_class(
                attrs_mapping={"older_attr": "canonical"},
                deprecated_in="0.9",
                remove_in="1.0",
                num_warns=-1,
                stream=None,
            )(StackingEntryBase)
        )

        with pytest.warns(FutureWarning, match="1.5"):
            value = proxy.old_attr  # type: ignore[attr-defined]
        assert value == "value"

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


# ---------------------------------------------------------------------------
# Feature 3b: @deprecated with DeprecationEntry in args_mapping (non-proxy path)
# ---------------------------------------------------------------------------


class TestDeprFnWithEntryArgsMapping:
    """``@deprecated(args_mapping=DeprecationEntry(...))`` on the non-proxy ``@deprecated`` path.

    Verifies that ``_raise_warn_arguments`` emits per-entry ``deprecated_in``/``remove_in``
    from the ``DeprecationEntry`` rather than the proxy-level fallback when a caller passes
    the deprecated argument name.
    """

    def test_entry_per_arg_deprecated_in_in_warning(self) -> None:
        """Warning carries per-entry ``deprecated_in`` from ``DeprecationEntry``, not proxy-level fallback.

        ``depr_fn_with_entry_args_mapping`` maps ``old_arg`` to
        ``DeprecationEntry("new_arg", deprecated_in="0.9", remove_in="1.0")``.
        The proxy-level fallback is ``deprecated_in="0.9"`` / ``remove_in="2.0"``.
        When calling with ``old_arg=``, the emitted warning must reference ``remove_in="1.0"``
        (per-entry), not the proxy-level ``remove_in="2.0"``.
        """
        with pytest.warns(FutureWarning, match="1.0") as record:
            result = depr_fn_with_entry_args_mapping(old_arg=42)  # type: ignore[call-arg]
        assert result == 42
        assert any("1.0" in str(w.message) for w in record if issubclass(w.category, FutureWarning))

    def test_canonical_arg_no_warning(self) -> None:
        """No warning when caller uses the canonical ``new_arg`` name."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = depr_fn_with_entry_args_mapping(new_arg=7)
        future_warns = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert future_warns == []
        assert result == 7


# ---------------------------------------------------------------------------
# Audit integration: find_deprecation_wrappers with DeprecationEntry in attrs_mapping
# ---------------------------------------------------------------------------


class TestFindDeprecationWrappersWithEntry:
    """``find_deprecation_wrappers()`` returns ``DeprecationEntry`` instances verbatim.

    The CHANGELOG states: "DeprecationEntry values are stored verbatim in
    ``__deprecated__.attrs_mapping`` and visible to audit tools."  This class validates
    that claim through the public audit API.
    """

    def test_attrs_mapping_entry_survives_audit_round_trip(self) -> None:
        """``find_deprecation_wrappers`` returns ``DeprecationEntry`` objects in ``attrs_mapping``.

        ``DeprecationEntryAttrProxy`` wraps ``V09TwoAttrClass`` with two ``DeprecationEntry``
        values in ``attrs_mapping``.  Scanning ``tests.collection_deprecate`` with
        ``find_deprecation_wrappers`` must return an entry whose
        ``deprecated_info.attrs_mapping["old_attr"]`` is a ``DeprecationEntry`` with the
        expected per-entry ``deprecated_in``/``remove_in`` fields.
        """
        from deprecate.audit import find_deprecation_wrappers

        col = sys.modules["tests.collection_deprecate"]
        results = find_deprecation_wrappers(col)
        expected_mapping = DeprecationEntryAttrProxy.__deprecated__.attrs_mapping
        entry_results = [r for r in results if r.deprecated_info.attrs_mapping == expected_mapping]
        assert entry_results, "find_deprecation_wrappers did not surface DeprecationEntryAttrProxy in module scan"
        info = entry_results[0]
        mapping = info.deprecated_info.attrs_mapping
        assert mapping is not None
        old_attr_entry = mapping.get("old_attr")
        assert isinstance(old_attr_entry, DeprecationEntry), (
            f"Expected DeprecationEntry for 'old_attr', got {type(old_attr_entry)}"
        )
        assert old_attr_entry.deprecated_in == "1.0"
        assert old_attr_entry.remove_in == "2.0"
