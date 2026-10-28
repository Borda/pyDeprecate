"""Unit tests for private helpers in :mod:`deprecate.audit._wrappers`."""

import dataclasses
import importlib.util
import warnings

import pytest

from deprecate import (
    DeprecatedError,
    as_exceptions,
    deprecated,
    find_deprecation_wrappers,
    validate_deprecation_expiry,
)
from deprecate._types import DeprecationConfig
from deprecate.audit import (
    ChainType,
    DeprecationWrapperInfo,
    validate_deprecation_wrapper,
)
from deprecate.audit._wrappers import _classify_member_api_type
from deprecate.proxy import _DeprecatedProxy, _ProxySpec
from tests.collections.api_current import ColorEnum
from tests.collections.chains import caller_via_notify_layer

_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None
_requires_packaging = pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging library")


@_requires_packaging
class TestValidateDeprecationWrapperWithProxy:
    """Unit tests for validate_deprecation_wrapper with inline _DeprecatedProxy objects.

    Uses _DeprecatedProxy directly (not collection fixtures) for true isolation.

    """

    def test_proxy_without_target_no_effect_false(self) -> None:
        """Proxy with no forwarding target is effective (still emits warnings) → no_effect=False."""
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(name="legacy_cfg", deprecated_in="1.0", remove_in="2.0", stream=None),
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.function == "legacy_cfg"
        assert result.no_effect is False
        assert result.chain_type is None

    def test_proxy_with_callable_target_no_effect_false(self) -> None:
        """Proxy forwarding to a callable target is effective → no_effect=False."""
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(name="old_enum", deprecated_in="1.0", remove_in="2.0", target=ColorEnum, stream=None),
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.function == "old_enum"
        assert result.deprecated_info.target is ColorEnum
        assert result.no_effect is False

    def test_proxy_with_args_mapping_skips_signature_validation(self) -> None:
        """Proxy __call__ is (*args, **kwargs) so signature check is skipped — invalid_args is always []."""
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(
                name="mapped",
                deprecated_in="1.0",
                remove_in="2.0",
                target=ColorEnum,
                args_mapping={"old_key": "value"},
                stream=None,
            ),
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.deprecated_info.args_mapping == {"old_key": "value"}
        assert result.invalid_args == []

    def test_proxy_with_identity_args_mapping_detected(self) -> None:
        """Proxy with an identity args_mapping entry still detects it — invalid_args stays []."""
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(
                name="identity_mapped",
                deprecated_in="1.0",
                remove_in="2.0",
                target=ColorEnum,
                args_mapping={"value": "value"},
                stream=None,
            ),
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.identity_args_mapping == ["value"]
        assert result.invalid_args == []

    def test_proxy_no_target_with_args_mapping(self) -> None:
        """Proxy with target=None and args_mapping: invalid_args=[] and no_effect=False (still warns)."""
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(
                name="warn_only_mapped",
                deprecated_in="1.0",
                remove_in="2.0",
                target=None,
                args_mapping={"x": "y"},
                stream=None,
            ),
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.invalid_args == []
        assert result.no_effect is False

    def test_proxy_attrs_mapping_chain_detected_as_stacked_chain(self) -> None:
        """Audit reports an ``attrs_mapping`` chain without decoration-time failure."""

        class Palette:
            a = 1
            b = 2
            c = 3

        proxy = _DeprecatedProxy(
            obj=Palette,
            spec=_ProxySpec(
                name="Palette",
                deprecated_in="1.0",
                remove_in="2.0",
                attrs_mapping={"a": "b", "b": "c"},
                stream=None,
            ),
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.chain_type is ChainType.STACKED

    def test_proxy_function_name_from_dep_info(self) -> None:
        """Function field comes from dep_info.name, not from getattr(proxy, '__name__').

        Without this, getattr routes through __getattr__ and leaks the target's __name__.

        """
        proxy = _DeprecatedProxy(
            obj={},
            spec=_ProxySpec(name="SourceName", deprecated_in="1.0", remove_in="2.0", target=ColorEnum, stream=None),
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.function == "SourceName"
        assert result.function != ColorEnum.__name__

    def test_proxy_empty_args_mapping_true_when_no_args_mapping(self) -> None:
        """Proxy with args_mapping=None reports empty_args_mapping=True."""
        proxy = _DeprecatedProxy(obj={}, spec=_ProxySpec(name="x", deprecated_in="1.0", remove_in="2.0", stream=None))
        result = validate_deprecation_wrapper(proxy)
        assert result.deprecated_info.args_mapping is None
        assert result.empty_args_mapping is True

    def test_callable_targeting_notify_wrapper_is_target_chain(self) -> None:
        """A callable target pointing to a NOTIFY wrapper is a forwarding TARGET chain.

        A team first turns an old helper into a warning-only shim, then later redirects another wrapper at that
        shim instead of at the surviving implementation. The audit must report the outer wrapper as a chain even
        though the inner link forwards nowhere further.
        """
        result = validate_deprecation_wrapper(caller_via_notify_layer)
        assert result.chain_type is ChainType.TARGET


class TestDeprecationWrapperInfoEmptyVersions:
    """DeprecationWrapperInfo.empty_deprecated_in reflects missing version metadata (F1b)."""

    def test_empty_deprecated_in_true_when_both_missing(self) -> None:
        """empty_deprecated_in=True when both deprecated_in and remove_in are absent."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)

            @deprecated()
            def fn_no_versions() -> None:
                pass

        info = validate_deprecation_wrapper(fn_no_versions)
        assert info.empty_deprecated_in is True

    def test_empty_deprecated_in_false_when_remove_in_only_missing(self) -> None:
        """empty_deprecated_in=False when deprecated_in is set but remove_in is omitted — valid use case."""

        @deprecated(deprecated_in="1.0")
        def fn_partial() -> None:
            pass

        info = validate_deprecation_wrapper(fn_partial)
        assert info.empty_deprecated_in is False

    def test_empty_deprecated_in_false_when_both_present(self) -> None:
        """empty_deprecated_in=False when both deprecated_in and remove_in are set."""

        @deprecated(deprecated_in="1.0", remove_in="2.0")
        def fn_complete() -> None:
            pass

        info = validate_deprecation_wrapper(fn_complete)
        assert info.empty_deprecated_in is False


class TestDeprecationWrapperInfoCompatAliases:
    """Deprecated @property aliases are deprecated through pyDeprecate itself and so are audit-visible (H3)."""

    def _make_info(self) -> DeprecationWrapperInfo:
        """Return a minimal DeprecationWrapperInfo for alias access tests."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)

            @deprecated()
            def _fn() -> None:
                pass

        return validate_deprecation_wrapper(_fn)

    @pytest.mark.parametrize(
        ("alias", "replacement"),
        [
            pytest.param("empty_mapping", "empty_args_mapping", id="empty_mapping"),
            pytest.param("identity_mapping", "identity_args_mapping", id="identity_mapping"),
        ],
    )
    def test_alias_emits_future_warning(self, alias: str, replacement: str) -> None:
        """Accessing a legacy alias emits a FutureWarning naming the replacement.

        The alias is wrapped with pyDeprecate's own decorator, so it warns with the library's category rather
        than a plain ``DeprecationWarning`` that default filters hide from the code reading the audit result.
        """
        info = self._make_info()
        with pytest.warns(FutureWarning, match=f"renamed to '{replacement}'"):
            getattr(info, alias)

    @pytest.mark.parametrize(
        ("alias", "replacement"),
        [
            pytest.param("empty_mapping", "empty_args_mapping", id="empty_mapping"),
            pytest.param("identity_mapping", "identity_args_mapping", id="identity_mapping"),
        ],
    )
    def test_alias_returns_replacement_value(self, alias: str, replacement: str) -> None:
        """Accessing a legacy alias returns the same value as its replacement field."""
        info = self._make_info()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            assert getattr(info, alias) == getattr(info, replacement)

    @pytest.mark.parametrize("alias", ["empty_mapping", "identity_mapping"])
    def test_alias_warns_on_every_read(self, alias: str) -> None:
        """A legacy alias warns on each read, not only the first (``num_warns=-1``).

        A caller reading the alias in several places must be told at every one of them; a one-shot warning would
        leave the second and later call sites quietly on the removed name until v1.0 breaks them.
        """
        info = self._make_info()
        with pytest.warns(FutureWarning, match="renamed to") as caught:
            [getattr(info, alias) for _ in range(2)]
        assert len([w for w in caught if issubclass(w.category, FutureWarning)]) == 2

    @pytest.mark.parametrize("alias", ["empty_mapping", "identity_mapping"])
    def test_alias_raises_inside_as_exceptions(self, alias: str) -> None:
        """Reading a legacy alias inside ``as_exceptions()`` raises ``DeprecatedError`` instead of warning.

        A strict CI section that sweeps for deprecated usage must catch the audit result's old field names too,
        since the aliases are ordinary pyDeprecate wrappers deferring to the global switch.
        """
        info = self._make_info()
        with as_exceptions(), pytest.raises(DeprecatedError, match="renamed to"):
            getattr(info, alias)

    @pytest.mark.parametrize("alias", ["empty_mapping", "identity_mapping"])
    def test_alias_is_listed_by_find_deprecation_wrappers(self, alias: str) -> None:
        """The package's own audit lists each legacy alias with its 0.8 deprecation and 1.0 removal versions.

        The scheduled removal of these aliases is tracked by the same audit the package offers its users, so the
        alias must be discoverable as a class member of the module defining it, with the versions it documents.
        """
        found = {
            item.function: item for item in find_deprecation_wrappers("deprecate.audit._wrappers", recursive=False)
        }
        config = found[f"DeprecationWrapperInfo.{alias}"].deprecated_info
        assert (config.deprecated_in, config.remove_in) == ("0.8", "1.0")

    @_requires_packaging
    @pytest.mark.parametrize("alias", ["empty_mapping", "identity_mapping"])
    @pytest.mark.parametrize(
        ("current_version", "flagged"),
        [
            pytest.param("0.9", False, id="before-removal"),
            pytest.param("1.0", True, id="at-removal"),
            pytest.param("1.1", True, id="past-removal"),
        ],
    )
    def test_alias_is_flagged_by_expiry_audit_from_v1_0(self, alias: str, current_version: str, flagged: bool) -> None:
        """The expiry audit flags a legacy alias once the current version reaches its scheduled 1.0 removal.

        The aliases exist so that the project's own CI turns red when v1.0 is cut with them still in place; at
        0.9 they are still within their deprecation period and must not be reported.
        """
        expired = validate_deprecation_expiry("deprecate.audit._wrappers", current_version, recursive=False)
        assert (
            any(f"`deprecate.audit._wrappers.DeprecationWrapperInfo.{alias}`" in message for message in expired)
            is flagged
        )


class TestDwiCompatInit:
    """_dwi_compat_init shim translates legacy constructor kwargs with DeprecationWarning (H4)."""

    def test_old_empty_mapping_kwarg_is_translated(self) -> None:
        """DeprecationWrapperInfo(empty_mapping=True) emits DeprecationWarning and sets empty_args_mapping."""
        with pytest.warns(DeprecationWarning, match="renamed to 'empty_args_mapping'"):
            info = DeprecationWrapperInfo(  # type: ignore[call-arg]
                function="f", deprecated_info=DeprecationConfig(), empty_mapping=True
            )
        assert info.empty_args_mapping is True

    def test_old_identity_mapping_kwarg_is_translated(self) -> None:
        """DeprecationWrapperInfo(identity_mapping=[...]) emits DeprecationWarning and sets identity_args_mapping."""
        with pytest.warns(DeprecationWarning, match="renamed to 'identity_args_mapping'"):
            info = DeprecationWrapperInfo(  # type: ignore[call-arg]
                function="f", deprecated_info=DeprecationConfig(), identity_mapping=["a"]
            )
        assert info.identity_args_mapping == ["a"]

    def test_both_old_kwargs_each_emit_deprecation_warning(self) -> None:
        """Passing both old kwargs emits one DeprecationWarning per renamed field."""
        with pytest.warns(DeprecationWarning, match="renamed") as caught:
            DeprecationWrapperInfo(  # type: ignore[call-arg]
                function="f", deprecated_info=DeprecationConfig(), empty_mapping=True, identity_mapping=["b"]
            )
        categories = [str(w.message) for w in caught.list if issubclass(w.category, DeprecationWarning)]
        assert any("empty_args_mapping" in m for m in categories)
        assert any("identity_args_mapping" in m for m in categories)

    def test_conflict_old_name_wins_when_both_supplied(self) -> None:
        """When both old and new names are supplied (as in replace()), old-name value is honoured."""
        with pytest.warns(DeprecationWarning, match="renamed to 'empty_args_mapping'"):
            info = DeprecationWrapperInfo(  # type: ignore[call-arg]
                function="f", deprecated_info=DeprecationConfig(), empty_mapping=True, empty_args_mapping=False
            )
        assert info.empty_args_mapping is True

    def test_replace_with_old_name_honoured_over_auto_injected_new(self) -> None:
        """dataclasses.replace() with old name honours caller intent over auto-injected new name.

        ``dataclasses.replace(info, empty_mapping=True)`` merges the caller's ``empty_mapping=True`` with the current
        ``empty_args_mapping=False`` (auto-injected by replace()).  The shim must detect this conflict, discard the
        auto-injected value, and honour the old-name value.

        """
        base = DeprecationWrapperInfo(function="f", deprecated_info=DeprecationConfig(), empty_args_mapping=False)

        with pytest.warns(DeprecationWarning, match="renamed to 'empty_args_mapping'"):
            result = dataclasses.replace(base, empty_mapping=True)  # type: ignore[call-arg]

        assert result.empty_args_mapping is True


class TestClassifyMemberApiType:
    """Tests for _classify_member_api_type — labels class-member audit rows."""

    def test_init_without_mapping_returns_class_constructor(self) -> None:
        """member_name='__init__' with has_mapping=False returns 'class constructor'."""
        assert _classify_member_api_type("__init__", None, False) == "class constructor"

    def test_init_with_mapping_returns_class_constructor_args(self) -> None:
        """member_name='__init__' with has_mapping=True returns 'class constructor args'."""
        assert _classify_member_api_type("__init__", None, True) == "class constructor args"


class _AudTargetCls:
    """Plain class used both as the wrapped object and target of a self-referential proxy (fixture)."""


class _AudAttrsMappingTarget:
    """Fixture class with both old and new attribute names for the attrs_mapping self-reference test."""

    old_attr: int = 0
    new_attr: int = 0


class TestProxySelfReferenceDetection:
    """A proxy whose deprecated target is its own wrapped object is a self-reference."""

    def test_self_reference_detected_for_proxy(self) -> None:
        """``target is func.wrapped`` marks the proxy as self-referential even though ``target is func`` is False.

        The wrapper object is the proxy while the deprecated target is the wrapped class, so the plain identity
        check never matched and a no-op self-referential proxy was reported as effective.
        """
        proxy = _DeprecatedProxy(
            obj=_AudTargetCls,
            spec=_ProxySpec(target=_AudTargetCls, name="_AudTargetCls", deprecated_in="1.0", remove_in="2.0"),
        )
        info = validate_deprecation_wrapper(proxy)
        assert info.self_reference is True

    def test_effective_proxy_with_args_mapping_not_self_reference(self) -> None:
        """Same target as wrapped but non-empty ``args_mapping`` means the proxy is NOT a self-reference.

        A _DeprecatedProxy whose target matches its wrapped object but also carries an active
        ``args_mapping`` still performs meaningful argument remapping and must not be flagged as
        a no-op self-reference. The self-reference predicate narrows to the zero-remapping
        case only; a proxy with mapping is an effective wrapper even when target is func.wrapped.
        """
        proxy = _DeprecatedProxy(
            obj=_AudTargetCls,
            spec=_ProxySpec(
                target=_AudTargetCls,
                name="_AudTargetCls",
                deprecated_in="1.0",
                remove_in="2.0",
                args_mapping={"old_x": "new_x"},
            ),
        )
        info = validate_deprecation_wrapper(proxy)
        assert info.self_reference is False
        assert info.no_effect is False

    def test_effective_proxy_with_attrs_mapping_not_self_reference(self) -> None:
        """Same target as wrapped but non-empty ``attrs_mapping`` means the proxy is NOT a self-reference.

        A proxy carrying an active ``attrs_mapping`` remaps attribute access on the deprecated wrapper,
        so it performs meaningful work even when target is func.wrapped.  Both ``args_mapping`` and
        ``attrs_mapping`` independently disqualify the self-reference label.
        """
        proxy = _DeprecatedProxy(
            obj=_AudAttrsMappingTarget,
            spec=_ProxySpec(
                target=_AudAttrsMappingTarget,
                name="_AudAttrsMappingTarget",
                deprecated_in="1.0",
                remove_in="2.0",
                attrs_mapping={"old_attr": "new_attr"},
            ),
        )
        info = validate_deprecation_wrapper(proxy)
        assert info.self_reference is False
        assert info.no_effect is False
