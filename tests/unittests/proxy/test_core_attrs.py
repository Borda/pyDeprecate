"""Unit tests for proxy attribute forwarding and mapping behavior."""

import warnings
from typing import Any

import pytest

from deprecate._types import TargetMode
from deprecate.deprecation import deprecated
from deprecate.proxy import deprecated_class
from deprecate.proxy.core import _DeprecatedProxy, _ProxySpec
from tests._helpers import captured_warnings
from tests.collections.api_current import (
    CombinedAttrsArgsSource,
    CombinedAttrsArgsTarget,
    LegacyBoolAttrsSource,
    Palette,
    PaletteEnum,
    PaletteOld,
)
from tests.collections.api_deprecated import _DEPRS_CASE_STD_ARGS, _DEPRS_CASE_STD_INF_ARGS, depr_read_only_attrs_list
from tests.collections.api_deprecated_forwarding import (
    DeprecatedAttrsExplicitMode,
    DeprecatedAttrsLegacyTrue,
    DeprecatedAttrsNotifyOnly,
    DeprecatedAttrsNotifyOnlyCallableTargetDecorated,
    DeprecatedAttrsNotifyOnlyCallableTargetWrapped,
    DeprecatedAttrsPalette,
    DeprecatedAttrsPaletteAllThree,
    DeprecatedAttrsPaletteCallableTarget,
    DeprecatedAttrsPaletteEnum,
    DeprecatedAttrsPaletteNested,
    DeprecatedAttrsPaletteWithStream,
)
from tests.unittests.proxy._helpers import _P9Config


class TestCombinedArgAttrsMapping:
    """Single ``deprecated_class()`` call combining ``args_mapping`` and ``attrs_mapping``.

    The canonical pattern for combining arg-rename and attr-rename deprecation is one ``deprecated_class()``
    call with both mappings and an explicit ``target=<NewClass>`` argument.  These tests pin that contract.

    Two decorators may also be stacked (see :class:`TestStackedDeprecatedClass` in ``test_stacking.py``)
    when each mapping layer needs an independent ``deprecated_in``/``remove_in`` version pair — e.g.
    ``old_attr`` deprecated in v1.0 while ``older_attr`` was deprecated earlier in v0.9.

    """

    def test_combined_args_and_attrs_mapping_call_path(self) -> None:
        """Constructor argument rename fires when calling the combined proxy.

        A single ``deprecated_class(target=NewClass, args_mapping=..., attrs_mapping=...)`` call must remap the
        old constructor argument on the call path.  The attribute path is independent — exhausting the call-path
        warning budget does not silence the attribute path and vice-versa.

        """

        class _New:
            lr: float = 0.01
            n_epochs: int = 10  # will be deprecated with warn-only mapping

            def __init__(self, lr: float = 0.01) -> None:
                self.lr = lr

        proxy = deprecated_class(
            target=_New,
            args_mapping={"learning_rate": "lr"},
            attrs_mapping={"n_epochs": None},  # warn on n_epochs, no rename
            deprecated_in="2.0",
            remove_in="3.0",
            num_warns=-1,
        )(_New)

        with pytest.warns(FutureWarning, match="learning_rate"):
            instance = proxy(learning_rate=0.05)  # type: ignore[call-arg]

        assert instance.lr == 0.05

    def test_combined_args_and_attrs_mapping_attr_path(self) -> None:
        """Attribute alias read fires when accessing a deprecated name on the combined proxy.

        The attribute-path warning budget is independent of the call-path budget.  Accessing a deprecated alias
        after exhausting the call-path budget must still warn exactly once (``num_warns=1``).

        """

        class _Config:
            lr: float = 0.01
            epochs: int = 10

        proxy = deprecated_class(
            target=_Config,
            args_mapping={"learning_rate": "lr"},
            attrs_mapping={"n_epochs": "epochs"},
            deprecated_in="2.0",
            remove_in="3.0",
        )(_Config)

        with pytest.warns(FutureWarning, match="n_epochs"):
            value = proxy.n_epochs  # type: ignore[attr-defined]

        assert value == 10

    def test_combined_isinstance_passes_through(self) -> None:
        """``isinstance()`` returns ``True`` for the combined single-call form.

        A single-call combined proxy has exactly one ``_DeprecatedProxy`` layer so ``__instancecheck__``
        resolves directly to the real class without recursion.  Stacked two-decorator forms also support
        ``isinstance()`` — see :class:`TestStackedDeprecatedClass` in ``test_stacking.py``.

        """

        class _Target:
            old_attr: str = "value"  # deprecated attribute — redirect target for isinstance test

        proxy = deprecated_class(
            target=_Target,
            attrs_mapping={"old_attr": None},  # warn on old_attr, no rename
            **_DEPRS_CASE_STD_ARGS,
            stream=None,
        )(_Target)

        instance = proxy()
        assert isinstance(instance, _Target)


class TestDeprecatedAttrs:
    """Selective per-attribute deprecation via ``attrs_mapping`` on ``deprecated_class``.

    Each test sets up an isolated proxy state because ``DeprecatedAttrsPalette`` and friends are
    module-level singletons whose per-attribute warning counters (``_cfg.state.warned_args``) persist
    across tests once consumed. The :meth:`_reset_proxy_state` autouse fixture clears those
    counters and re-seeds the canonical attribute values on the wrapped target so each test
    starts from the same baseline.

    """

    @pytest.fixture(autouse=True)
    def _reset_proxy_state(self) -> None:
        """Re-seed the wrapped target attributes of the module-level fixture proxies between tests.

        The proxies' warn budgets (``_cfg.state``) are reset by the project conftest, which covers every module-level
        proxy of :mod:`tests.collections.api_deprecated`; this fixture restores the wrapped objects' mutable attributes.

        """
        for proxy in (
            DeprecatedAttrsPalette,
            DeprecatedAttrsNotifyOnly,
            DeprecatedAttrsNotifyOnlyCallableTargetDecorated,
            DeprecatedAttrsNotifyOnlyCallableTargetWrapped,
            DeprecatedAttrsPaletteEnum,
            DeprecatedAttrsPaletteWithStream,
            depr_read_only_attrs_list,
        ):
            source = object.__getattribute__(proxy, "_DeprecatedProxy__config").obj
            if hasattr(source, "items"):
                source.items.clear()
            if hasattr(source, "size"):
                delattr(source, "size")
        # Restore canonical class attributes mutated by previous write-redirect tests.
        Palette.colour = "red"
        Palette.text = "hello"
        Palette.size = 42
        PaletteOld.color = "source_red"
        PaletteOld.colour = "source_colour"

    def test_read_redirect_warns_and_returns_canonical(self) -> None:
        """Accessing a deprecated attribute alias warns and transparently returns the canonical value.

        A class has both ``color`` (deprecated alias, misspelling) and ``colour`` (canonical name).  Wrapping the
        class with ``deprecated_class(attrs_mapping={"color": "colour"})`` ensures that reading ``proxy.color``
        emits a ``FutureWarning`` and returns the same value as ``proxy.colour``, so callers using the old name still
        get correct data during the migration window.

        """
        with pytest.warns(FutureWarning, match="color"):
            value = DeprecatedAttrsPaletteWithStream.color  # type: ignore[attr-defined]
        assert value == "red"

    def test_canonical_attr_no_warning(self) -> None:
        """Accessing the canonical (non-deprecated) attribute passes through silently.

        Only attribute names listed as keys in ``attrs_mapping`` trigger warnings.  Callers who have already migrated
        to the new name (e.g. ``colour`` instead of ``color``) must not receive any warning — the deprecation system
        should be invisible to migrated code.

        """
        with captured_warnings() as caught:
            value = DeprecatedAttrsPaletteWithStream.colour  # type: ignore[attr-defined]
        assert value == "red"
        assert not caught

    def test_write_redirect_warns_and_sets_canonical(self) -> None:
        """Writing to a deprecated attribute alias warns and sets the canonical attribute.

        A caller assigns to ``proxy.color = "blue"``.  The proxy emits a FutureWarning and then sets
        ``proxy.colour = "blue"``, so that subsequent reads of the canonical attribute reflect the written value.  This
        prevents split-brain state where the deprecated name and canonical name diverge in storage.

        """
        with pytest.warns(FutureWarning, match="color"):
            DeprecatedAttrsPaletteWithStream.color = "blue"  # type: ignore[attr-defined]
        # Reading the canonical attribute must now show the new value (no warning on canonical reads).
        with captured_warnings() as caught:
            assert DeprecatedAttrsPaletteWithStream.colour == "blue"  # type: ignore[attr-defined]
        assert not caught

    def test_notify_only_warns_no_redirect(self) -> None:
        """A ``None`` redirect value warns on access but does not rename the attribute.

        When ``attrs_mapping={"size": None}``, reading ``proxy.size`` emits a FutureWarning using the no-target
        template ("will be removed in v...") but still returns the value of ``proxy.size`` unchanged.  This is
        equivalent to ``TargetMode.NOTIFY`` for an individual attribute.

        """
        # Use a fresh proxy with stream enabled because DeprecatedAttrsNotifyOnly suppresses warnings.
        proxy = deprecated_class(attrs_mapping={"size": None}, **_DEPRS_CASE_STD_ARGS)(Palette)
        with pytest.warns(FutureWarning, match="size") as record:
            value = proxy.size  # type: ignore[attr-defined]
        assert value == 42
        # The no-target template does not include any "in favor of" phrase.
        assert "in favor of" not in str(record[0].message)

    def test_callable_target_notify_only_attr_uses_no_target_template(self) -> None:
        """A callable class target does not turn a ``None`` attr mapping into a redirect message.

        A migration can redirect some attributes through a replacement class while keeping another attribute as
        warn-only with ``attrs_mapping={"size": None}``.  Accessing that warn-only attribute must still read the
        active class attribute unchanged and render the no-target warning template, not an ``in favor of`` message
        pointing at a nonexistent replacement attribute.

        """
        proxy = deprecated_class(target=Palette, attrs_mapping={"size": None}, **_DEPRS_CASE_STD_ARGS)(Palette)
        with pytest.warns(FutureWarning, match="size") as record:
            value = proxy.size  # type: ignore[attr-defined]
        assert value == 42
        assert "in favor of" not in str(record[0].message)

    def test_read_only_attrs_mapping_blocks_mapped_mutator(self) -> None:
        """Read-only proxies block mutators returned through an attribute mapping.

        A project may keep a deprecated alias such as ``push`` while the replacement object exposes the standard
        list-like ``append`` method.  When the proxy is also read-only, resolving ``push`` through
        ``attrs_mapping={"push": "append"}`` must still return a guarded callable so calling it raises
        ``AttributeError`` and leaves the underlying collection unchanged.

        """
        source = object.__getattribute__(depr_read_only_attrs_list, "_DeprecatedProxy__config").obj

        with pytest.raises(AttributeError, match="read-only"):
            depr_read_only_attrs_list.push("x")  # type: ignore[attr-defined]

        assert source.items == []

    def test_read_only_proxy_with_attrs_mapping_blocks_set_on_deprecated_attr(self) -> None:
        """``read_only=True`` takes precedence over ``attrs_mapping`` on write — no deprecation warning is emitted.

        ``_check_read_only`` fires before the ``attrs_mapping`` branch in ``__setattr__`` and ``__delattr__``.
        Writing to a deprecated alias on a ``read_only`` proxy raises :class:`AttributeError` immediately, without
        emitting a ``FutureWarning``.  This is intentional — read-only is the stronger contract — but is pinned here
        so the precedence order is explicitly asserted and not accidentally reversed by future refactors.

        """
        proxy = _DeprecatedProxy(
            obj=Palette,
            spec=_ProxySpec(
                name="Palette",
                **_DEPRS_CASE_STD_ARGS,
                read_only=True,
                stream=None,
                attrs_mapping={"color": "colour"},
            ),
        )

        with pytest.raises(AttributeError, match="read-only"):
            proxy.color = "blue"  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        "proxy",
        [
            pytest.param(DeprecatedAttrsNotifyOnlyCallableTargetDecorated, id="decorator"),
            pytest.param(DeprecatedAttrsNotifyOnlyCallableTargetWrapped, id="wrapper"),
        ],
    )
    def test_callable_target_notify_only_attr_uses_active_target_for_mutations(self, proxy: Any) -> None:  # noqa: ANN401
        """Warn-only attributes on a callable-target proxy resolve against the active target class.

        A replacement class may keep an attribute under the same name while the deprecated source class lacks that
        attribute entirely.  With ``target=Palette`` and ``attrs_mapping={"size": None}``, reads, writes, and
        deletes of ``proxy.size`` must operate on ``Palette.size``.  Falling back to the wrapped source class
        either raises ``AttributeError`` on read/delete or silently writes to the wrong class.  The behaviour must be
        identical for decorator-form and wrapper-form ``deprecated_class`` usage.

        """
        source = object.__getattribute__(proxy, "_DeprecatedProxy__config").obj
        original_target = Palette.size
        assert not hasattr(source, "size")

        with pytest.warns(FutureWarning, match="size") as read_record:
            value = proxy.size  # type: ignore[attr-defined]
        assert value == original_target
        assert "in favor of" not in str(read_record[0].message)

        with pytest.warns(FutureWarning, match="size"):
            proxy.size = 99  # type: ignore[attr-defined]
        assert Palette.size == 99
        assert not hasattr(PaletteOld, "size")

        with pytest.warns(FutureWarning, match="size"):
            del proxy.size  # type: ignore[attr-defined]
        assert not hasattr(Palette, "size")
        assert not hasattr(source, "size")

    def test_per_attribute_warning_budget_independent(self) -> None:
        """Each deprecated attribute name has its own warning budget under ``num_warns=1``.

        With two entries in ``attrs_mapping`` and ``num_warns=1``, accessing both deprecated names must emit one
        warning each (two warnings total), not just one warning shared across all deprecated names.  This mirrors the
        per-argument budget of ``args_mapping`` deprecation and ensures callers see the migration notice for every
        attribute they use.

        """
        proxy = deprecated_class(
            attrs_mapping={"color": "colour", "txt": "text"}, deprecated_in="1.0", remove_in="2.0", num_warns=1
        )(Palette)
        with captured_warnings() as caught:
            _ = proxy.color  # type: ignore[attr-defined]
            _ = proxy.txt  # type: ignore[attr-defined]
        future_warnings = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert len(future_warnings) == 2
        # Subsequent access on either name is silent because each budget is exhausted.
        with captured_warnings() as caught_after:
            _ = proxy.color  # type: ignore[attr-defined]
            _ = proxy.txt  # type: ignore[attr-defined]
        assert not [w for w in caught_after if issubclass(w.category, FutureWarning)]

    def test_per_attribute_warning_budget_num_warns_two(self) -> None:
        """``num_warns=2`` allows exactly two warnings per deprecated attribute before going silent.

        With ``attrs_mapping={"color": "colour"}`` and ``num_warns=2``, three successive reads of
        ``proxy.color`` must emit exactly two ``FutureWarning`` instances — one on the first access,
        one on the second, and none on the third.  This verifies the off-by-one boundary
        ``warned_args.get(key, 0) >= num_warns`` at the budget limit.

        """
        proxy = deprecated_class(attrs_mapping={"color": "colour"}, deprecated_in="1.0", remove_in="2.0", num_warns=2)(
            Palette
        )
        with captured_warnings() as caught:
            _ = proxy.color  # type: ignore[attr-defined]
            _ = proxy.color  # type: ignore[attr-defined]
            _ = proxy.color  # type: ignore[attr-defined]
        future_warnings = [w for w in caught if issubclass(w.category, FutureWarning)]
        assert len(future_warnings) == 2

    def test_unlisted_attr_no_warning(self) -> None:
        """Attributes not listed in ``attrs_mapping`` pass through without any warning.

        The selective mode must not affect attributes that are not deprecated.  A class with
        ``attrs_mapping={"color": "colour"}`` should forward ``proxy.size`` silently — no warning, no redirect — so
        that the addition of ``attrs_mapping`` does not inadvertently alter performance-sensitive or hot-path
        attribute reads on non-deprecated names.

        """
        with captured_warnings() as caught:
            value = DeprecatedAttrsPaletteWithStream.size  # type: ignore[attr-defined]
        assert value == 42
        assert not caught

    def test_enum_member_redirect(self) -> None:
        """Enum member aliases redirect transparently through the proxy.

        An enum ``PaletteEnum`` has ``COLOUR`` as the canonical member name.  A deprecated alias ``COLOR`` is
        registered via ``attrs_mapping={"COLOR": "COLOUR"}``.  Accessing ``DeprecatedAttrsPaletteEnum.COLOR`` must
        warn and return the same object as ``DeprecatedAttrsPaletteEnum.COLOUR``.

        """
        # Use a fresh stream-enabled proxy so we can assert on the FutureWarning.
        proxy = deprecated_class(attrs_mapping={"COLOR": "COLOUR"}, **_DEPRS_CASE_STD_ARGS)(PaletteEnum)
        with pytest.warns(FutureWarning, match="COLOR"):
            value = proxy.COLOR  # type: ignore[attr-defined]
        assert value is PaletteEnum.COLOUR

    def test_warning_message_uses_callable_template(self) -> None:
        """Warning message for a redirect attr uses the callable template naming the canonical attr.

        The FutureWarning emitted for ``proxy.color`` (where ``color`` redirects to ``colour``) must contain the
        deprecated name ``color`` and the canonical name ``colour`` in the message text, so callers can immediately
        identify the migration action from the warning alone.

        """
        with pytest.warns(FutureWarning) as record:
            _ = DeprecatedAttrsPaletteWithStream.color  # type: ignore[attr-defined]
        message = str(record[0].message)
        assert "color" in message
        assert "colour" in message
        # The callable template includes the canonical class name and an "in favor of" phrase.
        assert "Palette.colour" in message

    def test_circular_redirect_raises_at_decoration_time(self) -> None:
        """Circular redirect mapping raises ``ValueError`` at decoration time.

        Passing ``attrs_mapping={"a": "b", "b": "a"}`` would create an infinite loop if both names were looked up
        through the proxy.  The decorator must detect this at class-decoration time and raise ``ValueError`` before any
        instance is created, making the misconfiguration visible immediately rather than at access time.

        """
        with pytest.raises(ValueError, match="circular redirects"):

            @deprecated_class(attrs_mapping={"a": "b", "b": "a"}, **_DEPRS_CASE_STD_ARGS)
            class _Circular:
                a = 1
                b = 2

    def test_redirect_chain_allowed_at_decoration_time(self) -> None:
        """Multi-hop attribute redirects are allowed for audit to report later.

        ``attrs_mapping={"a": "b", "b": "c"}`` is a mapping chain, but not a cycle. The decorator should keep import
        time usable and leave chain hygiene to audit tooling.

        """

        @deprecated_class(attrs_mapping={"a": "b", "b": "c"}, **_DEPRS_CASE_STD_ARGS, stream=None)
        class _Chained:
            a = 1
            b = 2
            c = 3

        assert _Chained.a == 2

    def test_attrs_mapping_stored_in_metadata(self) -> None:
        """``attrs_mapping`` is visible in ``__deprecation_config__`` metadata for audit tools.

        Audit tooling reads ``DeprecationConfig`` via ``obj.__deprecation_config__`` to build deprecation tables
        and enforce expiry policies.  The ``attrs_mapping`` dict must be stored in the frozen ``DeprecationConfig``
        so that ``find_deprecation_wrappers`` can surface it without reading internal proxy state.

        """
        meta = object.__getattribute__(DeprecatedAttrsPalette, "__deprecation_config__")
        assert meta.attrs_mapping == {"color": "colour", "txt": "text"}

    def test_attrs_mapping_with_callable_target_resolves_against_target_namespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When ``target=SomeClass`` and ``attrs_mapping`` are both set, attr redirects use the target class.

        ``deprecated_class(target=Palette, attrs_mapping={"color": "colour"})(PaletteOld)``
        redirects mapped reads of ``proxy.color`` to ``Palette.colour``.  Without a callable target the redirect
        would stay on the wrapped source class.

        """
        monkeypatch.setattr(PaletteOld, "colour", "source_colour")
        monkeypatch.setattr(Palette, "colour", "red")

        value = DeprecatedAttrsPaletteCallableTarget.color  # type: ignore[attr-defined]
        assert value == Palette.colour
        assert value != PaletteOld.colour

        DeprecatedAttrsPaletteCallableTarget.color = "blue"  # type: ignore[attr-defined]
        assert Palette.colour == "blue"
        assert PaletteOld.colour == "source_colour"

    def test_attrs_mapping_validation_does_not_consume_target_proxy_warning_budget(self) -> None:
        """Validation does not call ``hasattr`` on a live target proxy.

        A target proxy can warn from ``__getattr__`` when queried for one of its deprecated aliases.  Decorating a
        second proxy with that object as ``target`` must not touch the target proxy during redirect-target validation,
        otherwise the target's one-warning budget can be consumed before any user access.

        """

        class TargetAlias:
            alias = "target_alias"
            canonical = "target_canonical"

        target_proxy = deprecated_class(
            attrs_mapping={"alias": "canonical"}, deprecated_in="1.0", remove_in="2.0", num_warns=1
        )(TargetAlias)
        target_cfg = object.__getattribute__(target_proxy, "_DeprecatedProxy__config")
        target_cfg.state.warned_args.clear()

        class SourceAlias:
            old = "source_old"
            alias = "source_alias"

        with captured_warnings() as caught:
            deprecated_class(target=target_proxy, attrs_mapping={"old": "alias"}, **_DEPRS_CASE_STD_ARGS)(SourceAlias)
        assert not caught
        assert target_cfg.state.warned_args == {}

    def test_delete_redirect_warns_and_deletes_canonical(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deleting a deprecated attribute alias warns and deletes the canonical attribute.

        A class has ``colour`` as the canonical attribute and ``color`` registered as a deprecated alias in
        ``attrs_mapping={"color": "colour"}``.  Calling ``del proxy.color`` must emit a ``FutureWarning`` for ``color``
        and then delete ``Palette.colour``, so that callers migrating away from the deprecated name still trigger
        the expected deletion on the live canonical attribute.

        """
        monkeypatch.setattr(Palette, "colour", Palette.colour)
        proxy = deprecated_class(attrs_mapping={"color": "colour"}, **_DEPRS_CASE_STD_ARGS)(Palette)
        with pytest.warns(FutureWarning, match="color"):
            del proxy.color  # type: ignore[attr-defined]
        assert not hasattr(Palette, "colour")

    def test_delete_notify_only_warns_and_deletes_same_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deleting a warn-only attribute (``None`` redirect) warns and deletes the same-name attribute.

        When ``attrs_mapping={"size": None}``, the attribute name is both deprecated and canonical — ``None`` means
        "warn but keep the same name."  Calling ``del proxy.size`` must emit a ``FutureWarning`` for ``size`` and then
        delete ``Palette.size`` (the same attribute, not a redirect target), mirroring the read/write semantics
        for the notify-only case.

        """
        monkeypatch.setattr(Palette, "size", Palette.size)
        proxy = deprecated_class(attrs_mapping={"size": None}, **_DEPRS_CASE_STD_ARGS)(Palette)
        with pytest.warns(FutureWarning, match="size"):
            del proxy.size  # type: ignore[attr-defined]
        assert not hasattr(Palette, "size")

    def test_write_on_warn_only_attr_source_only_callable_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Writing a warn-only attr that lives only on the source falls back to the source class.

        The "being-removed" pattern places a deprecated attribute on the old source class only — the replacement target
        class dropped it.  With ``attrs_mapping={"color": None}`` and ``target=Palette`` (which has no ``color``),
        setting ``proxy.color = value`` must warn and write to ``PaletteOld.color``, not silently create a new class
        attribute on ``Palette``.  Without the source fallback, a regular class target accepts the spurious setattr
        without raising, leaving the source unchanged and the target polluted.

        """
        monkeypatch.setattr(PaletteOld, "color", PaletteOld.color)
        proxy = deprecated_class(target=Palette, attrs_mapping={"color": None}, **_DEPRS_CASE_STD_ARGS)(PaletteOld)
        assert not hasattr(Palette, "color")
        original = PaletteOld.color

        with pytest.warns(FutureWarning, match="color"):
            proxy.color = "updated"  # type: ignore[attr-defined]

        assert PaletteOld.color == "updated"
        assert not hasattr(Palette, "color"), "setattr must not pollute the target class"
        monkeypatch.setattr(PaletteOld, "color", original)

    def test_delete_on_warn_only_attr_source_only_callable_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deleting a warn-only attr that lives only on the source falls back to the source class.

        The "being-removed" pattern places a deprecated attribute on the old source class only.  With
        ``attrs_mapping={"color": None}`` and ``target=Palette`` (which has no ``color``), ``del proxy.color`` must
        warn and delete ``PaletteOld.color``.  Without the source fallback, ``delattr(Palette, "color")`` raises a bare
        ``AttributeError`` with no deprecation context, confusing callers who do not know the attribute lives on the
        old class.

        """
        monkeypatch.setattr(PaletteOld, "color", PaletteOld.color)
        proxy = deprecated_class(target=Palette, attrs_mapping={"color": None}, **_DEPRS_CASE_STD_ARGS)(PaletteOld)
        assert not hasattr(Palette, "color")

        with pytest.warns(FutureWarning, match="color"):
            del proxy.color  # type: ignore[attr-defined]

        assert not hasattr(PaletteOld, "color"), "delattr must remove from source class"
        assert not hasattr(Palette, "color"), "target must remain unaffected"

    def test_warn_only_key_missing_from_both_classes_raises_at_decoration_time(self) -> None:
        """A warn-only ``attrs_mapping`` key absent from both source and target raises at decoration time.

        When ``attrs_mapping={"nonexistent": None}`` is passed and neither the source class nor the target class has a
        ``nonexistent`` attribute, the decorator must raise ``ValueError`` immediately during class decoration rather
        than silently producing a proxy that raises ``AttributeError`` on first access.  A key present on only the
        source (being-removed pattern) or only the target (same-name warning) is valid and must not raise.

        """
        with pytest.raises(ValueError, match="warn-only keys not found on either class"):

            @deprecated_class(attrs_mapping={"nonexistent": None}, **_DEPRS_CASE_STD_ARGS)
            class _MissingWarnOnly:
                colour: str = "red"


class TestAttrsMappingCombinations:
    """Combination matrix for ``deprecated_class()`` ``attrs_mapping`` configurations.

    Covers the additive :attr:`~deprecate.TargetMode.ATTRS_REMAP` alias introduced for selective per-attribute
    deprecation, including:

    - Explicit ``target=TargetMode.ATTRS_REMAP`` form (equivalent to implicit auto-resolve)
    - Combined callable target + ``attrs_mapping`` + ``args_mapping`` on disjoint surfaces
    - Nested proxy semantics (blanket outer + selective inner)
    - Misconfiguration cases at decoration time (UserWarning emissions and TypeError on functions)

    Each test resets module-level fixture state before running because the combination fixtures share their per-attr
    warning budget with the rest of the test session. The :meth:`_reset_combination_state` autouse fixture clears
    the relevant counters and re-seeds canonical attribute values.

    """

    @pytest.fixture(autouse=True)
    def _reset_combination_state(self) -> None:
        """Re-seed wrapped target attributes between tests; the conftest resets the fixture proxies' warn budgets."""
        # Re-seed canonical attributes so write-redirect tests start from baseline.
        Palette.colour = "red"
        Palette.text = "hello"
        Palette.size = 42
        CombinedAttrsArgsTarget.colour = "red"
        CombinedAttrsArgsSource.colour = "source_red"

    # ------------------------------------------------------------------
    # Explicit TargetMode.ATTRS_REMAP form (C1)
    # ------------------------------------------------------------------

    def test_explicit_attrs_remap_mode_reads_canonical(self) -> None:
        """Explicit ``target=TargetMode.ATTRS_REMAP`` reads the canonical attribute through the alias.

        Migrators who prefer self-documenting decorator config may write the mode explicitly as
        ``target=TargetMode.ATTRS_REMAP`` rather than relying on implicit auto-resolution from ``attrs_mapping``.
        Accessing ``proxy.color`` on a fixture declared with the explicit form must still emit a ``FutureWarning``
        (suppressed here by ``stream=None``) and return the canonical ``Palette.colour`` value, matching the
        behaviour of the implicit-form fixture ``DeprecatedAttrsPalette``.

        """
        value = DeprecatedAttrsExplicitMode.color  # type: ignore[attr-defined]
        assert value == "red"

    def test_explicit_attrs_remap_mode_is_equivalent_to_implicit(self) -> None:
        """Explicit ``target=TargetMode.ATTRS_REMAP`` is observationally equivalent to the implicit auto-resolve.

        Both forms read from the same canonical attribute, expose the same ``attrs_mapping`` metadata, and store the
        same resolved ``target`` on the frozen :class:`~deprecate._types.DeprecationConfig`. Callers must be able to
        migrate between the two forms without behavioural change.

        """
        explicit_value = DeprecatedAttrsExplicitMode.color  # type: ignore[attr-defined]
        implicit_value = DeprecatedAttrsPalette.color  # type: ignore[attr-defined]
        assert explicit_value == implicit_value
        explicit_meta = object.__getattribute__(DeprecatedAttrsExplicitMode, "__deprecation_config__")
        implicit_meta = object.__getattribute__(DeprecatedAttrsPalette, "__deprecation_config__")
        assert explicit_meta.target is implicit_meta.target is TargetMode.ATTRS_REMAP

    def test_explicit_attrs_remap_stored_in_dep_config_target(self) -> None:
        """Explicit ``target=TargetMode.ATTRS_REMAP`` is stored verbatim on the frozen DeprecationConfig.

        Audit tooling reads ``DeprecationConfig.target`` to introspect the resolved deprecation mode. Passing the
        enum member explicitly must preserve the same enum member in storage so that downstream consumers cannot
        distinguish the explicit from the implicit auto-resolve at the metadata level.

        """
        meta = object.__getattribute__(DeprecatedAttrsExplicitMode, "__deprecation_config__")
        assert meta.target is TargetMode.ATTRS_REMAP

    def test_legacy_true_with_attrs_mapping_does_not_raise_value_error(self) -> None:
        """Legacy ``target=True`` with ``attrs_mapping`` auto-resolves to ``ATTRS_REMAP``.

        A legacy caller can combine ``target=True`` with ``attrs_mapping`` while still defining the canonical
        attribute on the deprecated class itself.  ``target=True`` normalises to ``NOTIFY``, and a present
        mapping is always applied — the config auto-resolves to ``ATTRS_REMAP`` (no misconfiguration)
        rather than validating redirect targets against ``bool`` and raising ``ValueError`` before the proxy can be
        called.

        """
        instance = DeprecatedAttrsLegacyTrue()  # must not raise ValueError

        assert isinstance(instance, LegacyBoolAttrsSource)
        assert instance.ready is True
        meta = object.__getattribute__(DeprecatedAttrsLegacyTrue, "__deprecation_config__")
        assert meta.target is TargetMode.ATTRS_REMAP
        assert meta.misconfigured is False

    def test_attrs_remap_stored_in_dep_config_target_via_auto_resolve(self) -> None:
        """Auto-resolution from ``attrs_mapping`` to ``ATTRS_REMAP`` is reflected in stored metadata.

        When the caller omits ``target`` but provides ``attrs_mapping``, the proxy must store
        :attr:`~deprecate._types.TargetMode.ATTRS_REMAP` on the frozen config rather than the original ``None`` so
        that audit tooling has a single canonical mode marker regardless of how the caller spelled the config.

        """
        meta = object.__getattribute__(DeprecatedAttrsPalette, "__deprecation_config__")
        assert meta.target is TargetMode.ATTRS_REMAP

    # ------------------------------------------------------------------
    # Combined callable target + attrs_mapping + args_mapping (C3)
    # ------------------------------------------------------------------

    def test_callable_target_with_attrs_and_args_mapping_attr_path(self) -> None:
        """The attribute-access path uses ``attrs_mapping`` independently of the call path.

        ``DeprecatedAttrsPaletteAllThree`` is constructed with all three of ``target=CombinedAttrsArgsTarget``,
        ``attrs_mapping={"color": "colour"}``, and ``args_mapping={"old_arg": "new_arg"}``. Reading ``proxy.color``
        must redirect to the canonical ``colour`` attribute on the target class, demonstrating that
        ``attrs_mapping`` is active even when a callable target and ``args_mapping`` are also configured (each
        surface remains disjoint).

        """
        value = DeprecatedAttrsPaletteAllThree.color  # type: ignore[attr-defined]
        assert value == CombinedAttrsArgsTarget.colour

    def test_callable_target_with_attrs_and_args_mapping_call_path(self) -> None:
        """The call path uses ``args_mapping`` independently of the attribute-access path.

        Calling the proxy with the deprecated kwarg ``old_arg`` must remap to ``new_arg`` before forwarding to the
        ``CombinedAttrsArgsTarget`` constructor. The resulting instance's ``new_arg`` attribute reflects the value
        the caller originally passed under the old name, proving the rename happened through the call path even when
        ``attrs_mapping`` is also present on the same proxy.

        """
        result = DeprecatedAttrsPaletteAllThree(old_arg=42)  # type: ignore[call-arg]
        assert isinstance(result, CombinedAttrsArgsTarget)
        assert result.new_arg == 42

    # ------------------------------------------------------------------
    # Nested proxy semantics
    # ------------------------------------------------------------------

    def test_nested_proxy_blanket_warns_on_access(self) -> None:
        """A blanket-warn ``deprecated_class`` proxy wrapping another proxy intercepts every attribute access.

        Nesting an outer ``deprecated_class()`` (no ``attrs_mapping``, blanket-warn mode) around an inner selective
        proxy means the outer's ``__getattr__`` fires on every attribute lookup before delegating to the inner.
        Reading ``proxy.colour`` (a canonical attribute on the inner) must still emit a ``FutureWarning`` from the
        outer wrapper, and the returned value must round-trip through both proxies to the underlying target class
        attribute. ``stream=None`` is set on the fixture so this test asserts the round-trip value; warning content
        is exercised by the implicit/explicit equivalence tests above.

        """
        value = DeprecatedAttrsPaletteNested.colour  # type: ignore[attr-defined]
        assert value == "red"

    # ------------------------------------------------------------------
    # Misconfiguration cases (decoration-time signals)
    # ------------------------------------------------------------------

    def test_explicit_notify_plus_attrs_mapping_warns_and_ignores_mapping(self) -> None:
        """Explicit ``target=TargetMode.NOTIFY`` + ``attrs_mapping`` is contradictory — flagged, mapping inert.

        ``NOTIFY`` means "warn on every access"; ``attrs_mapping`` means "warn only on the listed aliases".
        When the caller spelled out ``NOTIFY``, pyDeprecate cannot judge whether the target or the mapping
        is the mistake, so the explicit choice wins: the proxy stays in whole-class warn mode, the mapping
        performs no redirects at runtime, a ``UserWarning`` fires at decoration time, and audit metadata
        records ``misconfigured=True`` while preserving the configured ``attrs_mapping`` for inspection.
        Omit ``target`` (the factory default) to auto-resolve the mapping to ``ATTRS_REMAP`` instead.

        """

        class _NotifyAttrsContradiction:
            colour = "red"

        with pytest.warns(UserWarning, match="ignores `attrs_mapping`"):
            proxy = deprecated_class(
                target=TargetMode.NOTIFY,
                attrs_mapping={"color": "colour"},
                **_DEPRS_CASE_STD_ARGS,
                stream=None,
            )(_NotifyAttrsContradiction)

        meta = object.__getattribute__(proxy, "__deprecation_config__")
        assert meta.target is TargetMode.NOTIFY
        assert meta.misconfigured is True
        assert meta.attrs_mapping == {"color": "colour"}
        # Runtime ignores the mapping entirely: the alias is not redirected to ``colour``.
        with pytest.raises(AttributeError):
            _ = proxy.color  # type: ignore[attr-defined]

    def test_attrs_remap_without_attrs_mapping_warns_at_decoration(self) -> None:
        """``target=TargetMode.ATTRS_REMAP`` without ``attrs_mapping`` emits a UserWarning at decoration time.

        Explicit selective mode without any deprecated attribute names listed means the proxy has zero selective
        effect — every attribute access falls through to the blanket-warn path. This is a developer error and must
        be flagged at decoration time so the misconfiguration cannot ship to production.

        """

        class _AttrsRemapMissingMapping:
            colour = "red"

        with pytest.warns(UserWarning, match="ATTRS_REMAP.*requires.*`attrs_mapping`"):
            proxy = deprecated_class(target=TargetMode.ATTRS_REMAP, **_DEPRS_CASE_STD_ARGS, stream=None)(
                _AttrsRemapMissingMapping
            )
        meta = object.__getattribute__(proxy, "__deprecation_config__")
        assert meta.misconfigured is True

    def test_empty_attrs_mapping_warns_at_decoration(self) -> None:
        """An empty ``attrs_mapping={}`` dict is a no-op misconfiguration and emits a UserWarning.

        ``attrs_mapping={}`` is a developer typo — the empty dict has no deprecated attribute entries so the
        proxy has zero selective effect (and auto-resolve to ``ATTRS_REMAP`` does not fire because the value is
        falsy). The validator must catch this at decoration time so the misconfiguration cannot ship silently.

        """

        class _EmptyAttrsMapping:
            colour = "red"

        with pytest.warns(UserWarning, match="empty dict"):
            proxy = deprecated_class(attrs_mapping={}, **_DEPRS_CASE_STD_ARGS, stream=None)(_EmptyAttrsMapping)
        meta = object.__getattribute__(proxy, "__deprecation_config__")
        assert meta.misconfigured is True

    def test_attrs_remap_on_function_raises_typeerror(self) -> None:
        """Applying ``@deprecated(target=TargetMode.ATTRS_REMAP)`` to a function raises ``TypeError``.

        :attr:`~deprecate._types.TargetMode.ATTRS_REMAP` is a proxy-only mode — it operates on class attribute
        access, which functions and methods do not have. Trying to apply it via ``@deprecated`` is a developer
        error that must fail at decoration time with a clear redirect to ``deprecated_class(attrs_mapping=...)``,
        not silently produce a wrapper whose stored target has no runtime effect.

        """
        with pytest.raises(TypeError, match="ATTRS_REMAP.*not valid.*deprecated_class"):

            @deprecated(target=TargetMode.ATTRS_REMAP, **_DEPRS_CASE_STD_ARGS)
            def _attempted_attrs_remap_fn(x: int) -> int:
                return x

    def test_both_mappings_without_explicit_target_emits_userwarning(self) -> None:
        """Providing both ``args_mapping`` and ``attrs_mapping`` without explicit ``target`` emits a ``UserWarning``.

        When no ``target`` is given, auto-resolution sets ``target=TargetMode.ARGS_REMAP`` (args_mapping takes
        precedence) and ``DeprecationConfig.target`` no longer reflects that ``attrs_mapping`` is also active.
        Audit tooling reading only ``DeprecationConfig.target`` cannot detect the selective attribute deprecation.
        The proxy must emit a ``UserWarning`` directing the caller to pass an explicit target so the metadata
        remains complete.

        """

        class _BothMappingsSource:
            colour = "red"

        with pytest.warns(UserWarning, match="both.*args_mapping.*attrs_mapping"):
            deprecated_class(
                args_mapping={"old_arg": "new_arg"},
                attrs_mapping={"color": "colour"},
                **_DEPRS_CASE_STD_ARGS,
                stream=None,
            )(_BothMappingsSource)

    def test_explicit_attrs_remap_with_args_mapping_emits_userwarning(self) -> None:
        """Explicit ``target=TargetMode.ATTRS_REMAP`` with ``args_mapping`` emits a ``UserWarning``.

        ``ATTRS_REMAP`` only governs attribute access; ``__call__`` has no dispatch branch for it, so any
        ``args_mapping`` provided alongside it is silently dead code.  The companion auto-resolve case
        (``ARGS_REMAP + attrs_mapping``) is already caught; this test pins the symmetric case where the user
        explicitly sets ``target=TargetMode.ATTRS_REMAP`` and also supplies ``args_mapping``, which would
        otherwise silently ignore the call-path renames.

        """

        class _ExplicitAttrsRemapSource:
            colour = "red"

        with pytest.warns(UserWarning, match="ignores.*args_mapping"):
            deprecated_class(
                target=TargetMode.ATTRS_REMAP,
                args_mapping={"old_arg": "new_arg"},
                attrs_mapping={"color": "colour"},
                **_DEPRS_CASE_STD_ARGS,
                stream=None,
            )(_ExplicitAttrsRemapSource)

    def test_validate_proxy_userwarning_points_to_decoration_call_site(self) -> None:
        """The ``UserWarning`` from ``_validate_proxy`` points to the file that called ``deprecated_class``.

        ``_validate_proxy`` is called from ``_DeprecatedProxy.__init__``, which is invoked through
        ``deprecated_class → inner_func → __init__``.  The stacklevel must be calibrated so that
        ``warning.filename`` reports the module that applied the decorator, not an internal pyDeprecate
        frame.  A wrong stacklevel (e.g. 4 instead of 5) would make the warning appear to originate
        from inside the library, making it hard for users to locate their misconfigured class.

        The trigger is ``target=TargetMode.ATTRS_REMAP`` without ``attrs_mapping`` — a misconfiguration
        ``_validate_proxy`` still flags (unlike ``NOTIFY + attrs_mapping``, which now auto-resolves and no
        longer warns).

        """

        class _MisconfiguredForStacklevel:
            colour = "red"

        with captured_warnings() as caught:
            deprecated_class(
                target=TargetMode.ATTRS_REMAP,
                **_DEPRS_CASE_STD_ARGS,
                stream=None,
            )(_MisconfiguredForStacklevel)
        user_warnings = [w for w in caught if issubclass(w.category, UserWarning)]
        assert user_warnings, "Expected a UserWarning from _validate_proxy but got none"
        assert user_warnings[0].filename == __file__


class TestAttrsRemapCallPolicy:
    """Non-stacked ATTRS_REMAP proxies do not warn on plain instantiation/call."""

    def test_instantiation_does_not_warn(self) -> None:
        """Constructing a class whose deprecation covers only listed attributes emits no class-level warning.

        ATTRS_REMAP means "only these attribute aliases are deprecated"; instantiating the class is not a deprecated
        surface, so plain construction must neither warn nor burn the global warn budget.
        """
        alias = deprecated_class(attrs_mapping={"color": "colour"}, **_DEPRS_CASE_STD_ARGS)(Palette)
        with captured_warnings() as caught:
            _ = alias()
        assert not [w for w in caught if issubclass(w.category, FutureWarning)]

    def test_deprecated_attr_still_warns_after_instantiation(self) -> None:
        """The per-attribute warning still fires after instantiation, since the budget was not consumed by the call."""
        alias = deprecated_class(attrs_mapping={"color": "colour"}, **_DEPRS_CASE_STD_ARGS)(Palette)
        alias()
        with pytest.warns(FutureWarning, match="color"):
            _ = alias.color  # type: ignore[attr-defined]


class TestProxyAttrsMappingDefensiveCopy:
    """The frozen config must not alias the caller's mutable ``attrs_mapping`` dict."""

    def test_post_construction_mutation_ignored(self) -> None:
        """Mutating the caller's ``attrs_mapping`` after construction cannot alter the frozen proxy config.

        Storing the caller's dict by reference let a later mutation introduce a redirect cycle that decoration-time
        validation had already rejected; a defensive copy makes the stored mapping immune to caller mutation.
        """
        mapping: dict[str, Any] = {"old_attr": "new_attr"}
        proxy = _DeprecatedProxy(
            obj=_P9Config,
            spec=_ProxySpec(name="_P9Config", **_DEPRS_CASE_STD_ARGS, attrs_mapping=mapping),
        )
        mapping["old_attr"] = "hijacked"
        assert proxy.__deprecation_config__.attrs_mapping == {"old_attr": "new_attr"}


def _flagged_attr_proxy() -> tuple[Any, dict[str, bool], Any, Any]:
    """Build a ``color`` -> ``colour`` attribute-redirect proxy whose ``skip_if`` reads a flag the test flips.

    Returns the proxy, the mutable flag dict (``{"skip": ...}``), the wrapped source class and the target class — tiny
    mechanical classes used only to observe where a write or delete lands.

    """

    class _Source:
        color = "source-red"

    class _Target:
        colour = "target-red"

    flag = {"skip": True}
    proxy = deprecated_class(
        target=_Target,
        attrs_mapping={"color": "colour"},
        skip_if=lambda: flag["skip"],
        **_DEPRS_CASE_STD_INF_ARGS,
    )(_Source)
    return proxy, flag, _Source, _Target


class TestAttributeWritesUnderSkipIf:
    """``skip_if`` makes ``setattr``/``delattr`` on a deprecated alias behave as if the class were undecorated."""

    def test_write_while_skipped_lands_on_source_without_warning(self) -> None:
        """With ``skip_if`` true, ``proxy.color = x`` writes the source class under its own name and stays silent.

        A project disables the deprecation machinery (for example on the release that already migrated) yet old code
        still assigns the legacy name: the write must reach the original attribute with no warning and no redirect.

        """
        proxy, _, source, target = _flagged_attr_proxy()

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            proxy.color = "updated"

        assert source.color == "updated"
        assert target.colour == "target-red"

    def test_write_while_active_warns_and_redirects(self) -> None:
        """With ``skip_if`` false, the same write warns and lands on the canonical attribute of the target."""
        proxy, flag, source, target = _flagged_attr_proxy()
        flag["skip"] = False

        with pytest.warns(FutureWarning, match="color"):
            proxy.color = "updated"

        assert target.colour == "updated"
        assert source.color == "source-red"

    def test_delete_while_skipped_removes_source_attribute_without_warning(self) -> None:
        """With ``skip_if`` true, ``del proxy.color`` removes the source attribute and stays silent."""
        proxy, _, source, target = _flagged_attr_proxy()

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            del proxy.color

        assert not hasattr(source, "color")
        assert target.colour == "target-red"

    def test_delete_while_active_warns_and_redirects(self) -> None:
        """With ``skip_if`` false, the same delete warns and removes the canonical attribute from the target."""
        proxy, flag, source, target = _flagged_attr_proxy()
        flag["skip"] = False

        with pytest.warns(FutureWarning, match="color"):
            del proxy.color

        assert not hasattr(target, "colour")
        assert source.color == "source-red"

    def test_delete_of_unmapped_attribute_forwards_to_target(self) -> None:
        """Deleting an attribute not named in ``attrs_mapping`` removes it from the active target, silently."""
        proxy, flag, _, target = _flagged_attr_proxy()
        flag["skip"] = False
        target.extra = 1  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            del proxy.extra

        assert not hasattr(target, "extra")
