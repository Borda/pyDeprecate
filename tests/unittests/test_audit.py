"""Unit tests for private helpers in deprecate.audit."""

import dataclasses
import importlib
import importlib.metadata
import importlib.util
import sys
import types
import warnings
from functools import cached_property
from typing import Any, NoReturn, Union

import pytest

import tests.collection_deprecate as col
import tests.collection_misconfigured as clean_module
from deprecate import (
    PolicyRule,
    TargetMode,
    deprecated,
    get_deprecation_config,
    validate_deprecation_expiry,
    validate_deprecation_policy,
    validate_mapping_compatibility,
)
from deprecate._types import DeprecationConfig, _has_deprecation_meta
from deprecate.audit import (
    ChainType,
    DeprecationStatus,
    DeprecationWrapperInfo,
    GraceWindow,
    GraceWindowSpec,
    VersionBump,
    find_deprecation_wrappers,
    validate_deprecation_wrapper,
)
from deprecate.audit._lifecycle import (
    _check_expiry_for_callables,
    _get_deprecation_status,
    _get_package_version,
    _normalize_version_string,
    _parse_version,
)
from deprecate.audit._policy import (
    _build_policy_spec,
    _check_policy_for_callables,
    _has_migration_guidance,
    _satisfies_grace_window,
)
from deprecate.audit._report import _format_report_target
from deprecate.audit._scan import _member_has_deprecation_meta, _scan_class
from deprecate.audit._wrappers import _classify_member_api_type
from deprecate.proxy import _DeprecatedProxy, deprecated_class
from tests.collection_targets import ColorEnum, PositionalOnlyTarget

_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None
_requires_packaging = pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging library")

# Optional dependency: ``packaging`` ships with the ``[audit]`` extra. Guard the import at module level so
# collection never fails; the tests that use ``Version`` are gated by ``@_requires_packaging``.
if _PACKAGING_AVAILABLE:
    from packaging.version import Version

#: Identity fields ``deprecated_module()`` records for a module deprecated in ``1.0`` and removed in ``2.0``.
_MODULE_IDENTITY: dict[str, Any] = {"name": "pkg.old_mod", "deprecated_in": "1.0", "remove_in": "2.0"}
#: The notice ``deprecated_module()`` renders into ``message_template`` for that module when the author passes none.
_MODULE_BUILT_IN_NOTICE = "The `pkg.old_mod` module was deprecated since v1.0. It will be removed in v2.0."
#: Replacement module for the redirect case; ``deprecated_module()`` reads only its ``__name__``.
_MODULE_TARGET = types.ModuleType("pkg.new_mod")


class _SideEffectScanModule:
    """Test double that mimics module-level dynamic attribute side effects."""

    def __init__(self, proxy: _DeprecatedProxy) -> None:
        """Store proxy and expose a module-like name."""
        self.__name__ = "fake_scan_mod"
        self.scan_proxy = proxy

    def __dir__(self) -> list[str]:
        """Expose one dynamic name that would trigger __getattr__ under getmembers()."""
        return ["__name__", "scan_proxy", "trigger_side_effect"]

    def __getattr__(self, name: str) -> str:
        """Trigger proxy access when dynamic attr lookup is attempted."""
        if name == "trigger_side_effect":
            self.scan_proxy.get("x")
            return "triggered"
        raise AttributeError(name)


class TestGetPackageVersion:
    """Tests for _get_package_version — resolves a package version string via two fallback strategies."""

    def test_returns_version_from_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: importlib.metadata.version() returns the version string directly."""
        monkeypatch.setattr(importlib.metadata, "version", lambda _name: "3.1.4")

        assert _get_package_version("anything") == "3.1.4"

    def test_falls_back_to_dunder_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When importlib.metadata fails, falls back to reading __version__ from the imported module."""
        monkeypatch.setattr(importlib.metadata, "version", lambda _name: (_ for _ in ()).throw(Exception("no meta")))

        fake_module = types.ModuleType("fake_pkg")
        setattr(fake_module, "__version__", "2.3.4")
        monkeypatch.setattr(importlib, "import_module", lambda _name: fake_module)

        assert _get_package_version("fake_pkg") == "2.3.4"

    def test_raises_import_error_when_both_methods_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When both importlib.metadata and import_module fail, raises ImportError with a clear message."""

        def raise_exc(_name: str) -> None:
            raise Exception("not found")

        monkeypatch.setattr(importlib.metadata, "version", raise_exc)
        monkeypatch.setattr(importlib, "import_module", raise_exc)

        with pytest.raises(ImportError, match="Could not determine version"):
            _get_package_version("nonexistent_package_xyz")

    def test_raises_import_error_when_module_has_no_dunder_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When importlib.metadata fails and the imported module has no __version__, raises ImportError."""
        monkeypatch.setattr(importlib.metadata, "version", lambda _name: (_ for _ in ()).throw(Exception("no meta")))

        bare_module = types.ModuleType("bare_pkg")  # no __version__ attribute
        monkeypatch.setattr(importlib, "import_module", lambda _name: bare_module)

        with pytest.raises(ImportError, match="Could not determine version"):
            _get_package_version("bare_pkg")


class TestHasDeprecationMeta:
    """Tests for _has_deprecation_meta TypeGuard."""

    def test_returns_true_for_deprecated_proxy(self) -> None:
        """_DeprecatedProxy objects carry DeprecationConfig, so the guard returns True."""
        proxy = _DeprecatedProxy(obj={}, name="x", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert _has_deprecation_meta(proxy) is True

    @pytest.mark.parametrize(
        "target_val",
        [
            pytest.param(TargetMode.ARGS_REMAP, id="TargetMode.ARGS_REMAP"),
            pytest.param(True, marks=pytest.mark.filterwarnings("ignore::FutureWarning"), id="legacy-True"),
        ],
    )
    def test_returns_true_for_deprecated_decorated_callable(self, target_val: Union[TargetMode, bool]) -> None:
        """Return true for both current target representations after the metadata split.

        Applications migrating from the legacy boolean target spelling must receive the same metadata-discovery
        result as applications already using ``TargetMode``.
        """

        @deprecated(deprecated_in="1.0", remove_in="2.0", target=target_val)
        def fn() -> None:
            pass

        assert _has_deprecation_meta(fn) is True

    def test_legacy_callable_is_validated_through_public_fallback(self) -> None:
        """Validate a callable carrying only the pre-v0.13 metadata attribute.

        Mixed-version applications can expose wrappers created by an older pyDeprecate installation. Audit must
        consume their ``DeprecationConfig`` through the supported fallback instead of dereferencing the new attribute.
        """
        info = validate_deprecation_wrapper(clean_module._legacy_metadata_only)

        assert info.function == "legacy_metadata_only"
        assert info.deprecated_info.target is TargetMode.NOTIFY

    def test_legacy_module_is_discovered_through_public_fallback(self) -> None:
        """Discover module metadata created before the v0.13 attribute split.

        A mixed-version audit can encounter a module that stores its config only in ``__deprecated__``. The module
        must remain reportable without triggering an ``AttributeError`` for ``__deprecation_config__``.
        """
        results = find_deprecation_wrappers(clean_module._legacy_metadata_module)

        assert len(results) == 1
        assert results[0].module == "legacy_metadata_module"
        assert results[0].api_type == "module"

    def test_current_wrapper_can_stack_over_legacy_metadata(self) -> None:
        """Stack a current argument rename over a pre-v0.13 warn-only wrapper.

        A rolling upgrade can decorate an older wrapper again before every package has moved its metadata. Stacking
        must inspect the legacy config through the accessor and retain the current outer configuration.
        """
        wrapper = clean_module.make_stacked_legacy_metadata_wrapper()

        config = get_deprecation_config(wrapper)
        assert config is not None
        assert config.target is TargetMode.ARGS_REMAP

    def test_returns_false_for_plain_callable(self) -> None:
        """Undecorated callables have no __deprecation_config__, so the guard returns False."""

        def plain() -> None:
            pass

        assert _has_deprecation_meta(plain) is False

    @pytest.mark.parametrize("obj", [pytest.param("string", id="str"), pytest.param(42, id="int")])
    def test_returns_false_for_non_callable(self, obj: object) -> None:
        """Non-callables without __deprecation_config__ return False."""
        assert _has_deprecation_meta(obj) is False

    def test_meta_is_deprecation_info_instance(self) -> None:
        """The __deprecation_config__ attribute on a proxy is a typed DeprecationConfig dataclass."""
        proxy = _DeprecatedProxy(obj={}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert isinstance(object.__getattribute__(proxy, "__deprecation_config__"), DeprecationConfig)


@_requires_packaging
class TestParseVersion:
    """Tests for _parse_version — wraps packaging.version.Version with a ValueError on bad input."""

    @pytest.mark.parametrize("version", ["1.0", "2.3.4", "0.5.0a1", "1.0.0.post1", "1.0.0rc1"])
    def test_parses_valid_pep440_strings(self, version: str) -> None:
        """All valid PEP 440 version strings parse without error."""
        parsed = _parse_version(version)
        assert parsed is not None

    def test_invalid_version_raises_value_error(self) -> None:
        """Non-PEP-440 strings raise ValueError with a clear message."""
        with pytest.raises(ValueError, match="Failed to parse version"):
            _parse_version("not-a-version")

    def test_version_ordering_follows_pep440(self) -> None:
        """Pre-release stages order: alpha < beta < rc < stable < post."""
        stages = ["1.0.0a1", "1.0.0b1", "1.0.0rc1", "1.0.0", "1.0.0.post1"]
        parsed = [_parse_version(v) for v in stages]
        assert parsed == sorted(parsed)

    def test_major_minor_comparison(self) -> None:
        """Major and minor version components compare correctly."""
        assert _parse_version("1.0") < _parse_version("2.0")
        assert _parse_version("1.0") < _parse_version("1.1")
        assert _parse_version("1.0.0") == _parse_version("1.0")


class TestGetDeprecationStatus:
    """Tests for _get_deprecation_status lifecycle classification return values."""

    def test_status_no_removal_target_without_current_version_and_remove(self) -> None:
        """Missing current version with no remove_in maps to No Removal Target."""

        @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in=None)  # type: ignore[arg-type]
        def function() -> None:
            pass

        info = validate_deprecation_wrapper(function)
        status = _get_deprecation_status(info, current_version=None)
        assert status is DeprecationStatus.NO_REMOVAL_TARGET
        assert status.value == DeprecationStatus.NO_REMOVAL_TARGET.value

    @_requires_packaging
    def test_status_active_warning_with_current_version_and_future_remove_in(self) -> None:
        """Current version before remove_in maps to Deprecation Active."""

        @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
        def function() -> None:
            pass

        info = validate_deprecation_wrapper(function)
        status = _get_deprecation_status(info, current_version=Version("1.5"))
        assert status is DeprecationStatus.ACTIVE_WARNING
        assert status.value == DeprecationStatus.ACTIVE_WARNING.value

    @_requires_packaging
    def test_status_invalid_removal_target(self) -> None:
        """Non-parseable remove_in maps to Invalid Removal Target."""

        @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="not-a-version")
        def function() -> None:
            pass

        info = validate_deprecation_wrapper(function)
        status = _get_deprecation_status(info, current_version=Version("1.5"))
        assert status is DeprecationStatus.INVALID_REMOVAL_TARGET
        assert status.value == DeprecationStatus.INVALID_REMOVAL_TARGET.value

    @_requires_packaging
    def test_status_scheduled_deprecation_current_below_deprecated_in(self) -> None:
        """``current_version < deprecated_in`` maps to ``SCHEDULED_DEPRECATION``.

        See ``_get_deprecation_status`` — when ``deprecated_in`` parses cleanly and the current
        version is below it, the symbol is not yet emitting warnings to end users.

        """

        @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="9.0")
        def function() -> None:
            pass

        info = validate_deprecation_wrapper(function)
        status = _get_deprecation_status(info, current_version=Version("0.5"))
        assert status is DeprecationStatus.SCHEDULED_DEPRECATION
        assert status.value == DeprecationStatus.SCHEDULED_DEPRECATION.value

    @_requires_packaging
    def test_status_removal_imminent_on_prerelease_of_same_base(self) -> None:
        """Pre-release (``dev``/``a``/``b``) of the same base as ``remove_in`` → ``REMOVAL_IMMINENT``.

        The current release is a development pre-release of the eventual ``remove_in`` base, so
        the audit elevates the status above plain ``ACTIVE_WARNING`` to flag impending removal.

        """

        @deprecated(target=TargetMode.NOTIFY, deprecated_in="0.1", remove_in="0.10")
        def function() -> None:
            pass

        info = validate_deprecation_wrapper(function)
        # ``0.10.dev0`` parses as a dev pre-release with base ``0.10`` matching remove_in's base.
        status = _get_deprecation_status(info, current_version=Version("0.10.dev0"))
        assert status is DeprecationStatus.REMOVAL_IMMINENT
        assert status.value == DeprecationStatus.REMOVAL_IMMINENT.value

    @_requires_packaging
    def test_status_remove_before_release_on_rc_of_same_base(self) -> None:
        """RC pre-release of the same base as ``remove_in`` → ``REMOVE_BEFORE_RELEASE``.

        Same elevation path as ``REMOVAL_IMMINENT`` but RC pre-releases trip the higher
        ``REMOVE_BEFORE_RELEASE`` bucket (see ``audit._get_deprecation_status``: it inspects
        ``current_version.pre[0] == "rc"`` after confirming ``same_base``).

        """

        @deprecated(target=TargetMode.NOTIFY, deprecated_in="0.1", remove_in="0.9")
        def function() -> None:
            pass

        info = validate_deprecation_wrapper(function)
        status = _get_deprecation_status(info, current_version=Version("0.9rc1"))
        assert status is DeprecationStatus.REMOVE_BEFORE_RELEASE
        assert status.value == DeprecationStatus.REMOVE_BEFORE_RELEASE.value

    @_requires_packaging
    def test_status_past_removal_date_when_current_at_or_above_remove_in(self) -> None:
        """``current_version >= remove_in`` maps to ``PAST_REMOVAL_DATE``.

        The symbol should have been deleted before this release; audit surfaces it as overdue.

        """

        @deprecated(target=TargetMode.NOTIFY, deprecated_in="0.1", remove_in="0.9")
        def function() -> None:
            pass

        info = validate_deprecation_wrapper(function)
        status = _get_deprecation_status(info, current_version=Version("1.0"))
        assert status is DeprecationStatus.PAST_REMOVAL_DATE
        assert status.value == DeprecationStatus.PAST_REMOVAL_DATE.value

    @pytest.mark.filterwarnings("ignore::UserWarning")
    def test_status_unknown_when_current_version_none_and_remove_in_set(self) -> None:
        """``current_version=None`` with a ``remove_in`` set → ``STATUS_UNKNOWN``.

        Without a current version the audit cannot place the symbol on the lifecycle timeline,
        but the presence of ``remove_in`` distinguishes this from ``NO_REMOVAL_TARGET``.  No
        packaging requirement because the function returns before any version parsing runs.

        """

        @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
        def function() -> None:
            pass

        info = validate_deprecation_wrapper(function)
        status = _get_deprecation_status(info, current_version=None)
        assert status is DeprecationStatus.STATUS_UNKNOWN
        assert status.value == DeprecationStatus.STATUS_UNKNOWN.value


@_requires_packaging
class TestValidateDeprecationWrapperWithProxy:
    """Unit tests for validate_deprecation_wrapper with inline _DeprecatedProxy objects.

    Uses _DeprecatedProxy directly (not collection fixtures) for true isolation.

    """

    def test_proxy_without_target_no_effect_false(self) -> None:
        """Proxy with no forwarding target is effective (still emits warnings) → no_effect=False."""
        proxy = _DeprecatedProxy(obj={}, name="legacy_cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        result = validate_deprecation_wrapper(proxy)
        assert result.function == "legacy_cfg"
        assert result.no_effect is False
        assert result.chain_type is None

    def test_proxy_with_callable_target_no_effect_false(self) -> None:
        """Proxy forwarding to a callable target is effective → no_effect=False."""
        proxy = _DeprecatedProxy(
            obj={}, name="old_enum", deprecated_in="1.0", remove_in="2.0", target=ColorEnum, stream=None
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.function == "old_enum"
        assert result.deprecated_info.target is ColorEnum
        assert result.no_effect is False

    def test_proxy_with_args_mapping_skips_signature_validation(self) -> None:
        """Proxy __call__ is (*args, **kwargs) so signature check is skipped — invalid_args is always []."""
        proxy = _DeprecatedProxy(
            obj={},
            name="mapped",
            deprecated_in="1.0",
            remove_in="2.0",
            target=ColorEnum,
            args_mapping={"old_key": "value"},
            stream=None,
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.deprecated_info.args_mapping == {"old_key": "value"}
        assert result.invalid_args == []

    def test_proxy_with_identity_args_mapping_detected(self) -> None:
        """Proxy with an identity args_mapping entry still detects it — invalid_args stays []."""
        proxy = _DeprecatedProxy(
            obj={},
            name="identity_mapped",
            deprecated_in="1.0",
            remove_in="2.0",
            target=ColorEnum,
            args_mapping={"value": "value"},
            stream=None,
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.identity_args_mapping == ["value"]
        assert result.invalid_args == []

    def test_proxy_no_target_with_args_mapping(self) -> None:
        """Proxy with target=None and args_mapping: invalid_args=[] and no_effect=False (still warns)."""
        proxy = _DeprecatedProxy(
            obj={},
            name="warn_only_mapped",
            deprecated_in="1.0",
            remove_in="2.0",
            target=None,
            args_mapping={"x": "y"},
            stream=None,
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
            name="Palette",
            deprecated_in="1.0",
            remove_in="2.0",
            attrs_mapping={"a": "b", "b": "c"},
            stream=None,
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.chain_type is ChainType.STACKED

    def test_proxy_function_name_from_dep_info(self) -> None:
        """Function field comes from dep_info.name, not from getattr(proxy, '__name__').

        Without this, getattr routes through __getattr__ and leaks the target's __name__.

        """
        proxy = _DeprecatedProxy(
            obj={}, name="SourceName", deprecated_in="1.0", remove_in="2.0", target=ColorEnum, stream=None
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.function == "SourceName"
        assert result.function != ColorEnum.__name__

    def test_proxy_empty_args_mapping_true_when_no_args_mapping(self) -> None:
        """Proxy with args_mapping=None reports empty_args_mapping=True."""
        proxy = _DeprecatedProxy(obj={}, name="x", deprecated_in="1.0", remove_in="2.0", stream=None)
        result = validate_deprecation_wrapper(proxy)
        assert result.deprecated_info.args_mapping is None
        assert result.empty_args_mapping is True

    def test_callable_targeting_notify_wrapper_is_target_chain(self) -> None:
        """A callable target pointing to a NOTIFY wrapper is a forwarding TARGET chain."""

        @deprecated(TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
        def notify_layer(value: int) -> int:
            return value

        @deprecated(target=notify_layer, deprecated_in="1.0", remove_in="2.0")
        def caller(value: int) -> int:
            return value

        result = validate_deprecation_wrapper(caller)
        assert result.chain_type is ChainType.TARGET


class TestFindDeprecationWrappersWarningBudget:
    """Scanning must not consume proxy warning budgets."""

    def test_find_deprecation_wrappers_does_not_consume_warning_budget(self) -> None:
        """Scanning must avoid dynamic attribute access paths that burn warn budget.

        ``inspect.getmembers()`` triggers ``getattr()`` for names from ``__dir__``, which can execute module-level
        ``__getattr__`` side effects. This fixture reproduces that pattern: a dynamic name touches the proxy during
        lookup. Static inspection must avoid consuming the proxy warning budget.

        """
        proxy = _DeprecatedProxy(obj={}, name="scan_test", deprecated_in="1.0", remove_in="2.0", num_warns=1)
        fake_mod = _SideEffectScanModule(proxy)

        find_deprecation_wrappers(fake_mod, recursive=False)

        # Budget should be untouched — scanning must not consume it
        with pytest.warns(FutureWarning):
            proxy.get("x")  # triggers __getattr__ → _warn() → should still fire


class TestFindDeprecationWrappersReexport:
    """Re-exported wrappers are attributed to their defining module and never double-counted."""

    def test_reexport_dropped_in_importing_module(self) -> None:
        """A same-package re-export is skipped in the importing module to avoid double-counting.

        A library commonly surfaces a deprecated shim from a private submodule through its package
        ``__init__``. A recursive audit must attribute that wrapper to the module that defines it, not
        report it once per importing module — inflated counts break any CI gate summing ``len(results)``.

        The attribution filter only fires when the defining module shares the same top-level package
        (``_same_top_package`` guard). Wrappers from a wholly different package (e.g. an external
        library re-exposed) are never skipped — they will not be visited elsewhere and must be reported
        where they appear.
        """
        importer = types.ModuleType("importer_mod")

        @deprecated(deprecated_in="1.0", remove_in="2.0")
        def defined_elsewhere() -> None:
            """Wrapper defined in a sibling submodule of the same package."""

        # Simulate a same-package re-export: __module__ points to a submodule of the same top
        # package so _same_top_package("importer_mod.sub", "importer_mod") → True → skip fires.
        defined_elsewhere.__module__ = "importer_mod.sub"
        importer.defined_elsewhere = defined_elsewhere  # type: ignore[attr-defined]

        results = find_deprecation_wrappers(importer)

        assert [r for r in results if r.function == "defined_elsewhere"] == []

    def test_aliased_object_counted_once(self) -> None:
        """The same wrapper object bound under two names in one module is reported once.

        Mirrors the real ``self_ref_typed = cast(..., self_referencing_deprecation)`` alias in the
        misconfigured collection: two names, one underlying object — id-based dedup must collapse them
        so the wrapper is counted exactly once rather than inflating the scan by every extra binding.
        """
        mod = types.ModuleType("alias_mod")

        @deprecated(deprecated_in="1.0", remove_in="2.0")
        def canonical() -> None:
            """Single wrapper object exposed under two names."""

        canonical.__module__ = mod.__name__
        mod.canonical = canonical  # type: ignore[attr-defined]
        mod.alias = canonical  # type: ignore[attr-defined]  # same object, second binding

        results = find_deprecation_wrappers(mod)

        assert len([r for r in results if r.function in ("canonical", "alias")]) == 1


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
        mid = deprecated_class(target=final_cls, deprecated_in="1.0", remove_in="2.0")(type("MidApi", (), {}))
        old = deprecated_class(target=mid, deprecated_in="1.0", remove_in="2.0")(type("OldApi", (), {}))
        target = object.__getattribute__(old, "__deprecation_config__").target

        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning emitted during formatting fails the test
            formatted = _format_report_target(target)

        mid_cfg = object.__getattribute__(mid, "_DeprecatedProxy__config")
        assert formatted == "MidApi"
        assert mid_cfg.warned == 0

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
        assert proxy_config.warned == 0


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
    """Deprecated @property aliases emit DeprecationWarning on access (H3)."""

    def _make_info(self) -> DeprecationWrapperInfo:
        """Return a minimal DeprecationWrapperInfo for alias access tests."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)

            @deprecated()
            def _fn() -> None:
                pass

        return validate_deprecation_wrapper(_fn)

    def test_empty_mapping_alias_emits_deprecation_warning(self) -> None:
        """Accessing .empty_mapping emits DeprecationWarning naming the replacement."""
        info = self._make_info()
        with pytest.warns(DeprecationWarning, match="renamed to 'empty_args_mapping'"):
            _ = info.empty_mapping

    def test_empty_mapping_alias_returns_correct_value(self) -> None:
        """Accessing .empty_mapping returns the same value as .empty_args_mapping."""
        info = self._make_info()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            assert info.empty_mapping == info.empty_args_mapping

    def test_identity_mapping_alias_emits_deprecation_warning(self) -> None:
        """Accessing .identity_mapping emits DeprecationWarning naming the replacement."""
        info = self._make_info()
        with pytest.warns(DeprecationWarning, match="renamed to 'identity_args_mapping'"):
            _ = info.identity_mapping

    def test_identity_mapping_alias_returns_correct_value(self) -> None:
        """Accessing .identity_mapping returns the same value as .identity_args_mapping."""
        info = self._make_info()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            assert info.identity_mapping == info.identity_args_mapping


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


@_requires_packaging
class TestValidateDeprecationExpiryDefaults:
    """validate_deprecation_expiry must cover class members by default — it is the CI enforcement gate."""

    def test_default_includes_expired_class_members(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A deprecated method past its deadline is reported without passing ``include_members``.

        A team wires the documented one-liner ``validate_deprecation_expiry(my_package, __version__)``
        into CI and believes all deprecations are enforced. If the default excluded class members, every
        deprecated method, constructor, classmethod, staticmethod, and property would silently outlive its
        ``remove_in`` deadline while discovery (``find_deprecation_wrappers``) and reporting
        (``generate_deprecation_table``) show them by default.

        """
        mod = types.ModuleType("test_mod_expiry_member_default")

        # one-off mechanical fixture: wrapper belongs to a dynamically-built class attached to types.ModuleType
        class Service:
            @deprecated(deprecated_in="0.1", remove_in="0.5")
            def old_compute(self, x: int) -> int:
                """Deprecated self-deprecation past its removal deadline."""
                return x

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(Service, "__module__", mod.__name__)
        mod.Service = Service  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            expired = validate_deprecation_expiry(mod, "1.0")

        assert len(expired) == 1
        assert "old_compute" in expired[0]


class TestFindDeprecationWrappersClassScan:
    """find_deprecation_wrappers discovers @deprecated on class members, peeking through descriptors."""

    def test_finds_deprecated_regular_method(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deprecated regular method on a class is discovered by find_deprecation_wrappers."""
        mod = types.ModuleType("test_mod_method")

        @deprecated(deprecated_in="1.0", remove_in="2.0")
        def _new(self: object) -> int:
            return 1

        class OldCls:
            old_method = _new

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_method" in n for n in names)

    def test_finds_deprecated_classmethod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deprecated classmethod (correct @classmethod @deprecated order) is discovered."""
        mod = types.ModuleType("test_mod_cm")

        class OldCls:
            @classmethod
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_cm(cls: type) -> int:
                """Old classmethod."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_cm" in n for n in names)

    def test_finds_deprecated_staticmethod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deprecated staticmethod (correct @staticmethod @deprecated order) is discovered."""
        mod = types.ModuleType("test_mod_sm")

        class OldCls:
            @staticmethod
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_sm() -> int:
                """Old staticmethod."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_sm" in n for n in names)

    def test_finds_deprecated_property(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deprecated property (correct @property @deprecated order) is discovered."""
        mod = types.ModuleType("test_mod_prop")

        class OldCls:
            @property
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_prop(self: object) -> int:
                """Old property."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_prop" in n for n in names)

    def test_finds_deprecated_cached_property(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deprecated cached_property (correct @cached_property @deprecated order) is discovered."""
        mod = types.ModuleType("test_mod_cp")

        class OldCls:
            @cached_property
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_cp(self: object) -> int:
                """Old cached_property."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_cp" in n for n in names)

    def test_finds_outer_deprecated_classmethod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Outer @deprecated @classmethod order: wrapper is discovered by audit scan."""
        mod = types.ModuleType("test_mod_outer_cm")

        class OldCls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[arg-type]
            @classmethod
            def old_cm(cls: type) -> int:
                """Old classmethod."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_cm" in n for n in names)

    def test_finds_outer_deprecated_staticmethod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Outer @deprecated @staticmethod order: wrapper is discovered by audit scan."""
        mod = types.ModuleType("test_mod_outer_sm")

        class OldCls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[arg-type]
            @staticmethod
            def old_sm() -> int:
                """Old staticmethod."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_sm" in n for n in names)

    def test_finds_outer_deprecated_property(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Outer @deprecated @property order: wrapper is discovered by audit scan."""
        mod = types.ModuleType("test_mod_outer_prop")

        class OldCls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @property
            def old_prop(self: object) -> int:
                """Old property."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_prop" in n for n in names)

    def test_finds_outer_deprecated_cached_property(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Outer @deprecated @cached_property order: wrapper is discovered by audit scan."""
        mod = types.ModuleType("test_mod_outer_cp")

        class OldCls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @cached_property
            def old_cp(self: object) -> int:
                """Old cached_property."""
                return 1

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_cp" in n for n in names)

    def test_finds_setter_only_property(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit property(fget=None, fset=deprecated_fset) is discovered by audit scan."""
        mod = types.ModuleType("test_mod_setter_only")

        def _fset(self: object, v: int) -> None:
            pass

        class OldCls:
            write_only: property = deprecated(deprecated_in="1.0", remove_in="2.0")(property(None, _fset))  # type: ignore[assignment,arg-type]

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("write_only" in n for n in names)

    def test_finds_explicit_construction_fset_deprecated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit property(plain_fget, deprecated_fset): fset accessor is discovered."""
        mod = types.ModuleType("test_mod_explicit_fset")

        def _plain_fget(self: object) -> int:
            return 1

        def _fset(self: object, v: int) -> None:
            pass

        _deprecated_fset = deprecated(deprecated_in="1.0", remove_in="2.0")(_fset)

        class OldCls:
            rw_prop: property = property(_plain_fget, _deprecated_fset)

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("rw_prop" in n for n in names)

    def test_finds_deleter_only_property(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit property(None, None, deprecated_fdel) is discovered by audit scan.

        Symmetric to :meth:`test_finds_setter_only_property` for the fdel accessor: when the only
        deprecation-wrapped accessor on a property is ``fdel``, :func:`find_deprecation_wrappers`
        must traverse the deleter and surface the wrapper.
        """
        mod = types.ModuleType("test_mod_deleter_only")

        def _fdel(self: object) -> None:
            pass

        _deprecated_fdel = deprecated(deprecated_in="1.0", remove_in="2.0")(_fdel)

        class OldCls:
            delete_only: property = property(None, None, _deprecated_fdel)

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("delete_only" in n for n in names)


class TestValidateMappingCompatibility:
    """``validate_mapping_compatibility`` surfaces positional-only incompatibilities."""

    def test_finds_positional_only_wrapper(self) -> None:
        """``DepPositionalOnly`` appears in ``validate_mapping_compatibility`` results.

        The wrapper remaps ``old_val``→``new_val`` which is POSITIONAL_ONLY on
        ``PositionalOnlyTarget``; the validator must surface it.
        """
        results = validate_mapping_compatibility(col, recursive=False)
        names = [r.function for r in results]
        assert "DepPositionalOnly" in names

    def test_dataclass_auto_expanded_visible_in_audit(self) -> None:
        """``find_deprecation_wrappers`` populates ``args_mapping_auto_expanded`` for ``DepAutoExpandDC``.

        After auto-expand the ``DeprecationConfig`` stores the auto-copied keys; the
        ``DeprecationWrapperInfo`` returned by the audit walk must reflect this.
        """
        results = find_deprecation_wrappers(col, recursive=False)
        dc_results = [r for r in results if r.function == "DepAutoExpandDC"]
        assert dc_results, "DepAutoExpandDC not found by find_deprecation_wrappers"
        assert "old_field" in dc_results[0].args_mapping_auto_expanded

    def test_returns_empty_list_for_module_without_positional_only_wrappers(self) -> None:
        """``validate_mapping_compatibility`` returns [] when no wrapper targets POSITIONAL_ONLY params.

        ``tests.collection_misconfigured`` contains only ``@deprecated``-decorated functions
        (not ``deprecated_class`` proxies with ``args_mapping`` to positional-only constructor
        params), so the validator must return an empty list — no false positives.
        """
        results = validate_mapping_compatibility(clean_module, recursive=False)
        assert results == [], (
            f"Expected no positional-only incompatibilities in collection_misconfigured; got: "
            f"{[r.function for r in results]}"
        )

    def test_none_value_in_args_mapping_is_not_false_positive(self) -> None:
        """A ``deprecated_class`` with ``args_mapping={old: None}`` must NOT appear in results.

        ``args_mapping`` values of ``None`` denote warn-only (drop) entries — the proxy never
        attempts to forward the key as a kwarg, so there is no positional-only incompatibility
        to report.  ``_get_args_mapping_positional_only_keys`` correctly skips ``None`` values;
        this test pins that behaviour so a future refactor cannot introduce a false positive.
        """
        # Construct the proxy with a warn-only (None) mapping to the positional-only param name.
        # Suppress the decoration-time UserWarning that fires when a real remap key is positional-only;
        # here "old_val" maps to None (drop), so no UserWarning fires — but wrap defensively.
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            proxy = deprecated_class(
                args_mapping={"old_val": None},
                deprecated_in="1.0",
                remove_in="2.0",
            )(PositionalOnlyTarget)

        info = validate_deprecation_wrapper(proxy)
        assert info.args_mapping_positional_only == [], (
            f"args_mapping={{old_val: None}} must not produce args_mapping_positional_only; "
            f"got: {info.args_mapping_positional_only}"
        )


class TestInnerOrderPropertyAudit:
    """``find_deprecation_wrappers`` flags inner-order ``@property @deprecated`` definitions.

    Inner-order means ``@property`` sits outermost and ``@deprecated`` closer to ``def``, so only ``fget`` gets
    wrapped.  Any setter or deleter rebound afterwards is built from the plain :class:`property` base class and is
    therefore silently unprotected — writes and deletes never warn.  A library author who adopts the inner order by
    habit (mirroring how ``@property`` is normally placed outermost) creates a silent gap that an audit must surface.
    The ``inner_order_property`` flag lets CI pipelines reject this configuration and steer authors toward the
    canonical outer order ``@deprecated(...) @property``.
    """

    def test_inner_order_property_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A class using inner-order property with setter/deleter is flagged ``inner_order_property=True``.

        The shared ``InnerOrderDeprecatedPropCls`` fixture wraps only ``fget`` while exposing a plain setter and
        deleter; scanning the module that holds it must mark the discovered wrapper so maintainers can see at a
        glance that the write and delete paths are unprotected.
        """
        mod = types.ModuleType("test_mod_inner_order_prop")
        monkeypatch.setattr(col.InnerOrderDeprecatedPropCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.InnerOrderDeprecatedPropCls = col.InnerOrderDeprecatedPropCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        prop_results = [r for r in results if r.function.endswith(".value")]
        assert prop_results, f"property 'value' not discovered; got {[r.function for r in results]}"
        assert all(r.inner_order_property for r in prop_results)

    def test_outer_order_property_not_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An outer-order ``_DeprecatedProperty`` is NOT flagged ``inner_order_property``.

        The canonical order ``@deprecated(...) @property`` produces a :class:`_DeprecatedProperty` whose setter and
        deleter re-wrap every rebound accessor, so all paths warn.  This configuration is correct and the audit flag
        must stay ``False`` to avoid false positives that would punish the recommended usage.
        """
        mod = types.ModuleType("test_mod_outer_order_prop")

        class OldCls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @property
            def value(self) -> int:
                """Outer-order deprecated property."""
                return 42

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        prop_results = [r for r in results if r.function.endswith(".value")]
        assert prop_results, f"property 'value' not discovered; got {[r.function for r in results]}"
        assert not any(r.inner_order_property for r in prop_results)

    def test_getter_only_inner_order_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A getter-only inner-order property is ALSO flagged, not just ones carrying a setter.

        The stance is that outer order is canonical: any plain :class:`property` whose ``fget`` is deprecated was
        almost certainly written with the decorators in the wrong order.  Even without a setter the author has
        signalled intent to deprecate the attribute and should migrate to the order that survives future setter or
        deleter additions, so the flag fires for the getter-only shape too.
        """
        mod = types.ModuleType("test_mod_getter_only_inner")

        class OldCls:
            @property
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def value(self) -> int:
                """Inner-order deprecated getter-only property."""
                return 42

        # find_deprecation_wrappers filters by __module__; inline class defaults to test-file module
        monkeypatch.setattr(OldCls, "__module__", mod.__name__)
        # inject into mod.__dict__ so inspect.getmembers() finds it
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        prop_results = [r for r in results if r.function.endswith(".value")]
        assert prop_results, f"property 'value' not discovered; got {[r.function for r in results]}"
        assert all(r.inner_order_property for r in prop_results)

    def test_inner_order_flag_survives_dataclass_replace(self) -> None:
        """``dataclasses.replace`` on a flagged info preserves ``inner_order_property``.

        Audit results flow through several ``replace`` calls during scanning and report assembly; a regular field
        (not ``init=False``) must round-trip through ``replace`` unchanged so downstream consumers that copy the
        info to adjust an unrelated field do not silently lose the flag.
        """
        info = DeprecationWrapperInfo(
            function="OldCls.value",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="2.0"),
            inner_order_property=True,
        )
        replaced = dataclasses.replace(info, module="some.module")
        assert replaced.inner_order_property is True
        assert replaced.module == "some.module"


def _aud_new_impl() -> int:
    """Replacement callable used as a deprecation target for the private-member scan fixture."""
    return 1


class _AudPrivateMembers:
    """Fixture class carrying deprecated private members across all descriptor kinds."""

    @deprecated(target=_aud_new_impl, deprecated_in="1.0", remove_in="2.0")
    def _legacy(self) -> int:
        return 0

    @classmethod
    @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
    def _cls_legacy(cls) -> int:
        return 0

    @staticmethod
    @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
    def _static_legacy() -> int:
        return 0

    @cached_property
    @deprecated(target=TargetMode.NOTIFY, deprecated_in="1.0", remove_in="2.0")
    def _cached_legacy(self) -> int:
        return 0


class _AudTargetCls:
    """Plain class used both as the wrapped object and target of a self-referential proxy (fixture)."""


class _AudAttrsMappingTarget:
    """Fixture class with both old and new attribute names for the attrs_mapping self-reference test."""

    old_attr: int = 0
    new_attr: int = 0


class TestNormalizeVersionStringLocalSegment:
    """The label-normalizing regex must not touch the PEP 440 local segment, and only one leading v strips."""

    def test_preserves_local_segment(self) -> None:
        """A legitimate local like ``1.2.3+cuda`` must survive verbatim instead of becoming ``1.2.3+cuda0``.

        The trailing ``a`` of ``cuda`` used to be treated as a bare ``a`` pre-release label because the regex ran
        over the whole string; splitting off the local segment first keeps real-world CUDA/build locals intact.
        """
        assert _normalize_version_string("1.2.3+cuda") == "1.2.3+cuda"

    def test_normalizes_public_but_not_local(self) -> None:
        """A bare ``dev`` label in the public part is normalized while the local segment is left untouched."""
        assert _normalize_version_string("1.0.dev+build.a") == "1.0.dev0+build.a"

    def test_strips_only_single_leading_v(self) -> None:
        """Only one left-anchored ``v`` is removed, not every leading ``v`` (``lstrip`` stripped all)."""
        assert _normalize_version_string("vv1.0") == "v1.0"


class TestScanClassPrivateDeprecated:
    """Deprecated private/dunder members carry ``__deprecation_config__`` and must be surfaced so they can expire."""

    def test_member_meta_peeks_through_descriptor(self) -> None:
        """The helper detects deprecation metadata stored on a descriptor's underlying callable."""
        assert _member_has_deprecation_meta(_AudPrivateMembers.__dict__["_legacy"]) is True

    def test_member_meta_peeks_through_classmethod_descriptor(self) -> None:
        """The helper detects deprecation metadata stored on a classmethod's underlying ``__func__``.

        ``classmethod`` objects store the wrapped function in ``__func__``; ``_member_has_deprecation_meta``
        must unwrap it to find ``__deprecation_config__`` rather than inspecting the ``classmethod`` itself.
        """
        assert _member_has_deprecation_meta(_AudPrivateMembers.__dict__["_cls_legacy"]) is True

    def test_member_meta_peeks_through_staticmethod_descriptor(self) -> None:
        """The helper detects deprecation metadata stored on a staticmethod's underlying ``__func__``."""
        assert _member_has_deprecation_meta(_AudPrivateMembers.__dict__["_static_legacy"]) is True

    def test_member_meta_peeks_through_cached_property_descriptor(self) -> None:
        """The helper detects deprecation metadata stored on a cached_property's ``.func`` attribute."""
        assert _member_has_deprecation_meta(_AudPrivateMembers.__dict__["_cached_legacy"]) is True

    def test_scan_surfaces_deprecated_private_method(self) -> None:
        """A deprecated ``_legacy`` method is included in the scan even though it starts with an underscore.

        Previously ``_scan_class`` skipped every ``_*`` member except ``__init__``, so a deprecated private or
        dunder member could never be flagged as expired — a zombie that outlived its ``remove_in`` unnoticed.
        """
        results = _scan_class(_AudPrivateMembers, "tests.unittests.test_audit", "_AudPrivateMembers")
        functions = [info.function for info in results]
        assert any("_legacy" in fn for fn in functions)

    def test_scan_surfaces_deprecated_private_classmethod(self) -> None:
        """A deprecated private classmethod is discovered by the scan via the ``classmethod.__func__`` path."""
        results = _scan_class(_AudPrivateMembers, "tests.unittests.test_audit", "_AudPrivateMembers")
        functions = [info.function for info in results]
        assert any("_cls_legacy" in fn for fn in functions)

    def test_scan_surfaces_deprecated_private_staticmethod(self) -> None:
        """A deprecated private staticmethod is discovered by the scan via the ``staticmethod.__func__`` path."""
        results = _scan_class(_AudPrivateMembers, "tests.unittests.test_audit", "_AudPrivateMembers")
        functions = [info.function for info in results]
        assert any("_static_legacy" in fn for fn in functions)

    def test_scan_surfaces_deprecated_private_cached_property(self) -> None:
        """A deprecated private cached_property is discovered by the scan via the ``cached_property.func`` path."""
        results = _scan_class(_AudPrivateMembers, "tests.unittests.test_audit", "_AudPrivateMembers")
        functions = [info.function for info in results]
        assert any("_cached_legacy" in fn for fn in functions)


class TestProxySelfReferenceDetection:
    """A proxy whose deprecated target is its own wrapped object is a self-reference."""

    def test_self_reference_detected_for_proxy(self) -> None:
        """``target is func.wrapped`` marks the proxy as self-referential even though ``target is func`` is False.

        The wrapper object is the proxy while the deprecated target is the wrapped class, so the plain identity
        check never matched and a no-op self-referential proxy was reported as effective.
        """
        proxy = _DeprecatedProxy(
            obj=_AudTargetCls, target=_AudTargetCls, name="_AudTargetCls", deprecated_in="1.0", remove_in="2.0"
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
            target=_AudTargetCls,
            name="_AudTargetCls",
            deprecated_in="1.0",
            remove_in="2.0",
            args_mapping={"old_x": "new_x"},
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
            target=_AudAttrsMappingTarget,
            name="_AudAttrsMappingTarget",
            deprecated_in="1.0",
            remove_in="2.0",
            attrs_mapping={"old_attr": "new_attr"},
        )
        info = validate_deprecation_wrapper(proxy)
        assert info.self_reference is False
        assert info.no_effect is False


class TestForeignObjectDeprecationMetaGuard:
    """``_has_deprecation_meta`` must not crash a scan on a foreign object raising a non-AttributeError."""

    def test_hostile_getattr_returns_false(self) -> None:
        """An object whose ``__deprecated__`` property raises is treated as carrying no metadata, not crashed on.

        A recursive audit scan can encounter arbitrary third-party objects; ``getattr(..., default)`` only swallows
        ``AttributeError``, so without the guard a lazy proxy raising ``RuntimeError`` would abort the whole scan.
        Using a ``@property`` that raises on access exercises the ``try/except Exception`` guard without making
        ``__getattr__`` raise for every attribute (which CodeQL flags as broadly hazardous).
        """

        class _Hostile:
            @property
            def __deprecated__(self) -> object:
                raise RuntimeError("attribute access forbidden")

        assert _has_deprecation_meta(_Hostile()) is False


class TestBatchExpiryUnparsableVersion:
    """An unparsable ``remove_in`` in a batch scan warns instead of being silently skipped."""

    @_requires_packaging
    def test_unparsable_remove_in_warns_and_is_not_expired(self) -> None:
        """A typo'd ``remove_in`` emits a UserWarning and is excluded from the expired list, not dropped silently.

        A permanently un-expirable wrapper (broken version string) would otherwise pass the CI expiry gate forever
        with no signal; warning per skip surfaces the misconfiguration while the rest of the batch keeps scanning.
        """
        info = DeprecationWrapperInfo(
            function="pkg.broken",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="not.a.version!!"),
        )
        with pytest.warns(UserWarning, match="unparsable"):
            expired = _check_expiry_for_callables([info], "2.0")
        assert expired == []


class TestGraceWindow:
    """The strict ``min_grace`` form -- one unit, one count -- and parsing of every user-facing spelling into it."""

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            pytest.param("0.1", GraceWindow(1, VersionBump.MINOR), id="one-minor"),
            pytest.param("2.0", GraceWindow(2, VersionBump.MAJOR), id="two-majors"),
            pytest.param("0.0.3", GraceWindow(3, VersionBump.PATCH), id="three-patches"),
            pytest.param(" 0.1 ", GraceWindow(1, VersionBump.MINOR), id="padding"),
            pytest.param(1.0, GraceWindow(1, VersionBump.MAJOR), id="float-major"),
            pytest.param(0.3, GraceWindow(3, VersionBump.MINOR), id="float-minor"),
            pytest.param("0.0", GraceWindow(0, VersionBump.MINOR), id="zero-count-disables-distance"),
            pytest.param({"major": 1}, GraceWindow(1, VersionBump.MAJOR), id="table-major"),
            pytest.param({"minor": 3}, GraceWindow(3, VersionBump.MINOR), id="table-minor"),
            pytest.param({"patch": 2}, GraceWindow(2, VersionBump.PATCH), id="table-patch"),
            pytest.param({VersionBump.MINOR: 3}, GraceWindow(3, VersionBump.MINOR), id="table-enum-key"),
            pytest.param({"minor": 0}, GraceWindow(0, VersionBump.MINOR), id="table-zero-count"),
            pytest.param(GraceWindow(1, VersionBump.MAJOR), GraceWindow(1, VersionBump.MAJOR), id="passthrough"),
        ],
    )
    def test_parse_accepts_documented_spellings(self, spec: GraceWindowSpec, expected: GraceWindow) -> None:
        """Every documented spelling of a grace window converts to the same strict form.

        A policy is configured from a CLI flag, a ``pyproject.toml`` table, or a keyword argument typed by hand.
        The dotted delta reads like a version -- the same shape as ``deprecated_in`` and ``remove_in`` -- so
        ``"0.1"`` means one minor step and ``"0.0.3"`` three patch steps, and a float spells the same thing
        (``0.3``, which is also what Fire hands the CLI for an unquoted flag value); the table names the unit
        outright, which is what a TOML inline table (``{ minor = 3 }``) arrives as. Whatever the spelling, the
        scan only ever sees a ``GraceWindow``, and an instance built by hand passes through untouched.
        """
        assert GraceWindow.parse(spec) == expected

    @pytest.mark.parametrize(
        ("count", "unit"),
        [
            pytest.param(-1, VersionBump.MINOR, id="negative-count"),
            pytest.param(True, VersionBump.MINOR, id="bool-count"),
            pytest.param("3", VersionBump.MINOR, id="string-count"),
            pytest.param(1, "minor", id="string-unit"),
        ],
    )
    def test_construction_rejects_invalid_fields(self, count: object, unit: object) -> None:
        """Building a ``GraceWindow`` by hand is held to the same rules as parsing one.

        The strict form is the single place the scan trusts, so a window assembled in code (a test helper, a
        config loader that bypasses ``parse``) must not be able to carry a value no spelling could produce --
        otherwise the version arithmetic downstream would compare against a bool or a string.
        """
        with pytest.raises(ValueError, match="Invalid `min_grace` specification"):
            GraceWindow(count, unit)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "spec",
        [
            pytest.param("1", id="bare-major-ambiguous"),
            pytest.param(1, id="bare-int"),
            pytest.param(True, id="bool"),
            pytest.param("1 minor", id="legacy-count-unit-spelling"),
            pytest.param("1.2", id="two-non-zero-components"),
            pytest.param("0.0.0.1", id="four-components"),
            pytest.param("one", id="word-count"),
            pytest.param("", id="empty"),
            pytest.param({}, id="empty-table"),
            pytest.param({"major": 1, "minor": 2}, id="two-unit-table"),
            pytest.param({"release": 1}, id="unknown-unit"),
            pytest.param({"minor": "3"}, id="string-count"),
            pytest.param({"minor": -1}, id="negative-count"),
            pytest.param({"minor": True}, id="bool-count"),
        ],
    )
    def test_rejects_unparseable_specification(self, spec: object) -> None:
        """An unrecognised grace window fails loudly instead of silently disabling the rule.

        A typo that quietly turned the grace rule off would leave a CI gate reporting green while checking
        nothing at all, so the specification is validated before the scan starts. A bare ``"1"`` is rejected as
        ambiguous -- one *what*? -- and must be written ``"1.0"`` or ``{"major": 1}``; ``"1.2"`` has no meaning
        under the coarser-bump rule; a table can only ever name one unit with an integer count, which is the
        policy's own rule made structural.
        """
        with pytest.raises(ValueError, match="Invalid `min_grace` specification"):
            GraceWindow.parse(spec)  # type: ignore[arg-type]


class TestBuildPolicySpec:
    """Validation of the raw policy arguments before any wrapper is scanned."""

    def test_disabled_rules_carry_none(self) -> None:
        """Passing ``None`` for the grace window disables it rather than falling back to the default.

        A project that only wants the guidance rule needs the grace window off while keeping the rest, so a
        disabled rule must survive as ``None`` all the way into the per-wrapper checks.
        """
        spec = _build_policy_spec(None, False)
        assert spec.grace is None
        assert spec.message_required is False


class TestSatisfiesGraceWindow:
    """Version-distance arithmetic behind the ``min-grace`` rule."""

    @pytest.mark.parametrize(
        ("deprecated_in", "remove_in", "count", "unit", "expected"),
        [
            pytest.param("1.0", "1.1", 1, VersionBump.MINOR, True, id="exactly-one-minor"),
            pytest.param("1.0", "1.0", 1, VersionBump.MINOR, False, id="same-release"),
            pytest.param("1.2", "1.5", 3, VersionBump.MINOR, True, id="three-minors"),
            pytest.param("1.2", "1.4", 3, VersionBump.MINOR, False, id="two-minors-short-of-three"),
            pytest.param("1.2", "2.0", 3, VersionBump.MINOR, True, id="major-bump-clears-minor-window"),
            pytest.param("1.2", "2.3", 3, VersionBump.MINOR, False, id="mixed-major-and-minor-bump"),
            pytest.param("1.0", "2.0", 1, VersionBump.MAJOR, True, id="one-major"),
            pytest.param("1.0", "1.9", 1, VersionBump.MAJOR, False, id="minors-do-not-clear-major-window"),
            pytest.param("1.2", "2.1", 1, VersionBump.MAJOR, False, id="major-bump-with-non-zero-minor"),
            pytest.param("1.0.0", "1.0.1", 1, VersionBump.PATCH, True, id="one-patch"),
            pytest.param("1.0.0", "1.1.0", 1, VersionBump.PATCH, True, id="minor-bump-clears-patch-window"),
            pytest.param("1.2.3", "1.3.0", 1, VersionBump.MINOR, True, id="lower-components-reset-on-bump"),
            pytest.param("1.2.3", "1.3.1", 1, VersionBump.MINOR, False, id="patch-after-minor-bump"),
            pytest.param("1.2.3", "1.2.5", 1, VersionBump.MINOR, False, id="patch-bump-finer-than-minor-unit"),
            pytest.param("1.0", "1.1", 0, VersionBump.MINOR, True, id="zero-count-accepts-any-minor"),
            pytest.param("1.0", "1.0.1", 0, VersionBump.MINOR, False, id="zero-count-still-rejects-finer-bump"),
            pytest.param("2", "3", 1, VersionBump.MAJOR, True, id="bare-major-versions"),
            pytest.param("1.0", "2.0.0.1", 1, VersionBump.MAJOR, False, id="fourth-component-is-not-a-clean-major"),
            pytest.param("1.0", "2.0rc1", 1, VersionBump.MAJOR, True, id="pre-release-of-next-major"),
            pytest.param("1.0", "2.0.post1", 1, VersionBump.MAJOR, True, id="post-release-of-next-major"),
        ],
    )
    @_requires_packaging
    def test_distance_between_versions(
        self, deprecated_in: str, remove_in: str, count: int, unit: VersionBump, expected: bool
    ) -> None:
        """The removal must be one clean bump of a single component, at least ``count`` steps in ``unit``.

        A wrapper deprecated in ``1.2`` and removed in ``2.0`` has a *smaller* minor number at removal even
        though callers got a whole major cycle, so a coarser bump clears the window; ``2.3`` mixes a major and
        a minor step and is never a release boundary a project promises removals on, so it fails whatever the
        window. Components below the bumped one restart at zero (``1.2.3`` → ``1.3.0``), and a pre- or
        post-release of a clean version is the same release line. PEP 440 allows more than three release
        components, so ``2.0.0.1`` is read as a follow-up release, not a clean major.
        """
        window = GraceWindow(count, unit)
        assert _satisfies_grace_window(_parse_version(deprecated_in), _parse_version(remove_in), window) is expected

    @pytest.mark.parametrize(
        ("deprecated_in", "remove_in"),
        [
            pytest.param("1.0", "1!1.0", id="forward-epoch-bump"),
            pytest.param("1!1.0", "2.0", id="backward-epoch-drop"),
        ],
    )
    @_requires_packaging
    def test_refuses_versions_from_different_epochs(self, deprecated_in: str, remove_in: str) -> None:
        """A pair of versions from different PEP 440 epochs is refused instead of being measured.

        Release numbers are only comparable inside one epoch -- ``2.0`` is *older* than ``1!1.0`` -- so no count
        of majors, minors, or patches describes the distance across an epoch change. A verdict either way would
        silently misjudge the window (a forward bump used to clear every window, in whichever direction), so the
        predicate raises and leaves the case to ``_grace_window_violation``, which intercepts it first and warns.
        """
        window = GraceWindow(1, VersionBump.MAJOR)
        with pytest.raises(ValueError, match="epoch"):
            _satisfies_grace_window(_parse_version(deprecated_in), _parse_version(remove_in), window)


class TestHasMigrationGuidance:
    """Detection of whether a wrapper tells callers what to migrate to."""

    @pytest.mark.parametrize(
        ("config", "expected"),
        [
            pytest.param(DeprecationConfig(target=str), True, id="callable-target"),
            pytest.param(DeprecationConfig(target=TargetMode.NOTIFY), False, id="warn-only"),
            pytest.param(DeprecationConfig(target=None), False, id="unset-target"),
            pytest.param(
                DeprecationConfig(target=TargetMode.ARGS_REMAP, args_mapping={"old": "new"}),
                True,
                id="args-mapping-names-replacement",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.ATTRS_REMAP, attrs_mapping={"old": "new"}),
                True,
                id="attrs-mapping-names-replacement",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.NOTIFY, message_template="use `new_api` instead"),
                True,
                id="custom-message-spells-it-out",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.NOTIFY, message_template=""),
                False,
                id="empty-message-template-selects-the-built-in-text",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.ARGS_REMAP, args_mapping={}),
                False,
                id="remap-mode-with-empty-mapping-renames-nothing",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.ATTRS_REMAP),
                False,
                id="attrs-remap-mode-without-mapping",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.NOTIFY, args_mapping={"old": "new"}),
                False,
                id="notify-ignores-its-args-mapping",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.NOTIFY, attrs_mapping={"old": "new"}),
                False,
                id="notify-ignores-its-attrs-mapping",
            ),
            pytest.param(
                DeprecationConfig(
                    target=TargetMode.NOTIFY,
                    args_mapping={"old": "new"},
                    message_template="use `new_api` instead",
                ),
                True,
                id="notify-with-a-message-guides-despite-the-ignored-mapping",
            ),
            pytest.param(
                DeprecationConfig(target=None, args_mapping={"old": "new"}),
                True,
                id="unset-target-still-applies-its-mapping",
            ),
        ],
    )
    def test_guidance_sources(self, config: DeprecationConfig, expected: bool) -> None:
        """Any of a target, a live mapping, or a custom message counts as telling callers where to go.

        The rule exists to catch the dead-end warning ("this is deprecated", full stop); a wrapper that renames
        arguments or carries a hand-written migration sentence is not a dead end even without a target. A mapping
        counts only where it survives to call time: an empty one renames nothing, and one paired with an explicit
        ``TargetMode.NOTIFY`` is discarded at decoration time, so both leave callers the same dead end.
        """
        info = DeprecationWrapperInfo(module="pkg", function="old_api", deprecated_info=config)
        assert _has_migration_guidance(info) is expected

    @pytest.mark.parametrize(
        ("config", "expected"),
        [
            pytest.param(
                DeprecationConfig(
                    target=TargetMode.NOTIFY, message_template=_MODULE_BUILT_IN_NOTICE, **_MODULE_IDENTITY
                ),
                False,
                id="built-in-notice-only",
            ),
            pytest.param(
                DeprecationConfig(
                    target=TargetMode.NOTIFY, message_template="use `pkg.new_mod` instead", **_MODULE_IDENTITY
                ),
                True,
                id="custom-template-spells-it-out",
            ),
            pytest.param(
                DeprecationConfig(
                    target=TargetMode.NOTIFY,
                    message_template=_MODULE_BUILT_IN_NOTICE,
                    attrs_mapping={"old_name": "new_name"},
                    **_MODULE_IDENTITY,
                ),
                True,
                id="attrs-mapping-without-target-is-applied",
            ),
            pytest.param(
                DeprecationConfig(
                    target=_MODULE_TARGET,
                    message_template=(
                        "The `pkg.old_mod` module was deprecated since v1.0 in favor of `pkg.new_mod`."
                        " It will be removed in v2.0."
                    ),
                    **_MODULE_IDENTITY,
                ),
                True,
                id="redirect-target",
            ),
        ],
    )
    def test_module_guidance_sources(self, config: DeprecationConfig, expected: bool) -> None:
        """A deprecated module is judged on its target, its attribute mapping, and a template of its own.

        ``deprecated_module()`` differs from the other factories in two ways the rule has to see through: it
        renders the warning up front and stores the result in ``message_template`` -- the built-in notice when
        the author passed none, their own text otherwise -- and it stores ``TargetMode.NOTIFY`` for "no
        replacement module" rather than leaving the target unset, while still applying ``attrs_mapping`` on
        every access. Reading the stored text as guidance would make the rule inert for every module; reading
        the sentinel as an explicit opt-out would discard a live mapping and flag a module whose author wrote a
        migration sentence by hand. Only the built-in notice with nothing else is the dead end.
        """
        info = DeprecationWrapperInfo(module="pkg.old_mod", deprecated_info=config, api_type="module")
        assert _has_migration_guidance(info) is expected


class TestMessageRequiredRemedy:
    """The ``message-required`` violation names only the knobs the wrapper's own factory accepts."""

    @pytest.mark.parametrize(
        ("api_type", "config", "named", "absent"),
        [
            pytest.param(
                "data",
                DeprecationConfig(target=None, name="old_cfg"),
                ["Instance `pkg.old_cfg`", "a custom `message_template`"],
                ["`target`", "`args_mapping`", "`attrs_mapping`"],
                id="instance-has-only-a-template",
            ),
            pytest.param(
                "module",
                DeprecationConfig(
                    target=TargetMode.NOTIFY, message_template=_MODULE_BUILT_IN_NOTICE, **_MODULE_IDENTITY
                ),
                ["Module `pkg.old_cfg`", "a `target`", "an `attrs_mapping`", "a custom `message_template`"],
                ["`args_mapping`"],
                id="module-has-no-args-mapping",
            ),
            pytest.param(
                "callable",
                DeprecationConfig(target=TargetMode.NOTIFY),
                [
                    "Callable `pkg.old_cfg`",
                    "a `target`",
                    "an `args_mapping`/`attrs_mapping`",
                    "a custom `message_template`",
                ],
                [],
                id="callable-lists-every-knob",
            ),
        ],
    )
    def test_remedy_matches_the_factory(
        self, api_type: str, config: DeprecationConfig, named: list[str], absent: list[str]
    ) -> None:
        """The remedy text lists the arguments the wrapper's factory actually takes, under the matching subject noun.

        A maintainer reads the violation and reaches for the first option it names. ``deprecated_instance()`` has
        no ``target`` or mapping argument at all, and ``deprecated_module()`` has no ``args_mapping``, so a remedy
        copied from the callable case sends them to a keyword that raises ``TypeError`` -- and calling an instance
        proxy a *Callable* points them at the wrong factory to begin with.
        """
        info = DeprecationWrapperInfo(module="pkg", function="old_cfg", deprecated_info=config, api_type=api_type)
        spec = _build_policy_spec(None, True)

        (violation,) = _check_policy_for_callables([info], spec)

        assert violation.startswith(f"[{PolicyRule.MESSAGE_REQUIRED.value}] ")
        assert all(fragment in violation for fragment in named)
        assert not any(fragment in violation for fragment in absent)


class TestValidateDeprecationPolicy:
    """End-to-end policy scan over the ``tests.collection_policy`` fixtures."""

    @pytest.mark.parametrize(
        ("wrapper_name", "rule"),
        [
            pytest.param("no_grace_window", PolicyRule.MIN_GRACE, id="min-grace-no-distance"),
            pytest.param("removed_at_patch", PolicyRule.MIN_GRACE, id="min-grace-off-boundary"),
            pytest.param("short_minor_runway", PolicyRule.MIN_GRACE, id="min-grace-insufficient-distance"),
            pytest.param("warns_without_replacement", PolicyRule.MESSAGE_REQUIRED, id="message-required"),
            pytest.param("WarnOnlyLegacyClass", PolicyRule.MESSAGE_REQUIRED, id="message-required-proxy"),
            pytest.param(
                "warns_without_template_instance", PolicyRule.MESSAGE_REQUIRED, id="message-required-instance"
            ),
        ],
    )
    @_requires_packaging
    def test_each_fixture_trips_its_rule(self, wrapper_name: str, rule: PolicyRule) -> None:
        """Every governance rule fires on the wrapper that breaks it, and reports it under its own slug.

        This is the reviewer-facing contract: a PR that schedules a removal in the same release, off a clean
        release boundary, or warns without naming a replacement, has to come back with a message naming *which*
        policy it broke. The sweep runs under the default policy, so each fixture is judged by the rule it was
        written for.
        """
        violations = validate_deprecation_policy("tests.collection_policy", recursive=False)
        matching = [v for v in violations if wrapper_name in v]
        assert len(matching) == 1
        assert matching[0].startswith(f"[{rule.value}]")

    @pytest.mark.parametrize(
        ("min_grace", "expected_window"),
        [
            pytest.param("0.1", "at least 1 minor release, landing on a clean major or minor boundary.", id="singular"),
            pytest.param("2.0", "at least 2 major releases, landing on a clean major boundary.", id="plural"),
        ],
    )
    @_requires_packaging
    def test_violation_message_echoes_configured_window(self, min_grace: str, expected_window: str) -> None:
        """The reported grace window reads back as prose naming the count, the unit, and the allowed boundaries.

        A maintainer who configured ``min_grace="2.0"`` reads the CI log to learn what the gate expected; a
        message echoing the bare ``2`` would leave them guessing which component it counts, so the message
        spells out ``2 major releases`` -- a one-unit window still reads as the singular -- and names the
        release levels a removal may land on, so a mixed-bump violation is explained by the same sentence.
        """
        violations = validate_deprecation_policy("tests.collection_policy", recursive=False, min_grace=min_grace)
        matching = [v for v in violations if "no_grace_window" in v]
        assert len(matching) == 1
        assert matching[0].endswith(expected_window)

    @pytest.mark.parametrize(
        "wrapper_name",
        [
            pytest.param("compliant_forward", id="target-forward"),
            pytest.param("args_mapping_only_guidance", id="args-mapping-only"),
            pytest.param("AttrsMappingOnlyGuidance", id="attrs-mapping-only"),
            pytest.param("message_template_only_guidance", id="message-template-only"),
            pytest.param("warns_with_template_instance", id="instance-with-template"),
        ],
    )
    @_requires_packaging
    def test_compliant_wrapper_is_not_reported(self, wrapper_name: str) -> None:
        """A wrapper that offers guidance through any single accepted channel trips no rule.

        A wrapper's guidance may come from a forwarding ``target`` (``compliant_forward``), a live
        ``args_mapping`` or ``attrs_mapping`` with no ``target`` at all (``args_mapping_only_guidance``,
        ``AttrsMappingOnlyGuidance``), a hand-written ``message_template`` with no ``target`` or mapping
        (``message_template_only_guidance``), or a ``deprecated_instance()`` template
        (``warns_with_template_instance``). The gate is only useful if every one of these disciplined
        cases passes silently — a policy that flags every wrapper is one a team turns off in its first
        week.
        """
        violations = validate_deprecation_policy("tests.collection_policy", recursive=False)
        assert not [v for v in violations if wrapper_name in v]

    @_requires_packaging
    def test_instance_remedy_names_only_the_message_template(self) -> None:
        """A bare ``deprecated_instance`` violation tells the reader to set ``message_template`` and nothing else.

        ``deprecated_instance()`` accepts no ``target`` or mapping, so a remedy listing those would send a
        maintainer after knobs that do not exist; the message must name the one knob the API actually has.
        """
        violations = validate_deprecation_policy("tests.collection_policy", recursive=False)

        (violation,) = [v for v in violations if "warns_without_template_instance" in v]

        assert "Instance" in violation
        assert "configure a custom `message_template`" in violation
        assert "args_mapping" not in violation
        assert "`target`" not in violation

    @_requires_packaging
    def test_module_with_custom_template_is_not_reported(self) -> None:
        """A ``deprecated_module()`` fixture with a custom ``message_template`` naming a replacement trips no rule.

        ``tests.collection_modules.old_math`` is deprecated in place with a ``message_template`` that names
        ``new_math`` as the replacement and no ``target``. Since ``_module_has_migration_guidance`` reads a
        template that differs from the built-in notice as guidance, ``validate_deprecation_policy()`` must
        report no ``message-required`` violation for the module itself.
        """
        violations = validate_deprecation_policy("tests.collection_modules.old_math", recursive=False)
        assert not [v for v in violations if PolicyRule.MESSAGE_REQUIRED.value in v]

    @_requires_packaging
    def test_module_with_only_the_built_in_notice_is_reported(self) -> None:
        """A ``deprecated_module()`` fixture with no ``target``, mapping, or custom template is flagged.

        ``tests.collection_modules.old_stats`` is deprecated in place with none of the three module-level
        guidance channels, so callers only ever see the built-in notice — the module-level dead end.
        ``validate_deprecation_policy()`` must flag it under ``message-required``, and the remedy must name
        only the arguments ``deprecated_module()`` accepts (a ``target``, an ``attrs_mapping``, or a custom
        ``message_template``) -- never ``args_mapping``, which that factory has no such keyword for.
        """
        violations = validate_deprecation_policy("tests.collection_modules.old_stats", recursive=False)
        (violation,) = [v for v in violations if PolicyRule.MESSAGE_REQUIRED.value in v]
        assert "a `target`" in violation
        assert "an `attrs_mapping`" in violation
        assert "a custom `message_template`" in violation
        assert "args_mapping" not in violation

    @_requires_packaging
    def test_all_rules_disabled_reports_nothing_for_a_maximally_violating_wrapper(self) -> None:
        """Disabling every governance rule silences a wrapper that would otherwise trip both at once.

        A wrapper deprecated at `9.0` and removed one patch later (`9.0.1`), warning without naming a
        replacement, breaks `min-grace` and `message-required` simultaneously — the worst case for the gate.
        If disabling both did not also disable the check for this wrapper, a project could never fully opt out
        of the policy gate on a single incorrigible case.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="everything_wrong",
            deprecated_info=DeprecationConfig(deprecated_in="9.0", remove_in="9.0.1", target=TargetMode.NOTIFY),
        )
        enabled_spec = _build_policy_spec("0.1", True)
        assert len(_check_policy_for_callables([info], enabled_spec)) == 2

        disabled_spec = _build_policy_spec(None, False)
        assert _check_policy_for_callables([info], disabled_spec) == []

    @_requires_packaging
    def test_disabled_rule_stops_reporting(self) -> None:
        """Setting a rule to ``None`` removes its violations without affecting the other rule.

        Projects with no fixed release cadence must be able to keep the guidance rule while dropping the
        grace-window rule, instead of abandoning the whole gate.
        """
        violations = validate_deprecation_policy("tests.collection_policy", recursive=False, min_grace=None)
        assert not [v for v in violations if PolicyRule.MIN_GRACE.value in v]
        assert [v for v in violations if PolicyRule.MESSAGE_REQUIRED.value in v]

    @_requires_packaging
    def test_missing_versions_are_not_violations(self) -> None:
        """A wrapper with no ``remove_in`` is skipped by the version-distance rules rather than flagged.

        Deprecating without scheduling a removal is a deliberate, common choice; treating it as a policy breach
        would flood the report with entries the team already decided about.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="warn_forever",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", target=str),
        )
        spec = _build_policy_spec("0.1", True)
        assert _check_policy_for_callables([info], spec) == []

    @_requires_packaging
    def test_unparsable_version_warns_and_skips_dependent_rules(self) -> None:
        """A typo'd ``remove_in`` warns once and skips only the rules that need it, instead of aborting the scan.

        One broken version string in a large package must not take the whole CI gate down, but it also must not
        vanish — the wrapper would otherwise stay permanently unlintable with no signal at all.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="broken_version",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="not.a.version!!", target=str),
        )
        spec = _build_policy_spec("0.1", True)
        with pytest.warns(UserWarning, match="unparsable `remove_in`"):
            assert _check_policy_for_callables([info], spec) == []

    @_requires_packaging
    def test_forward_epoch_change_warns_and_skips_the_grace_window(self) -> None:
        """A removal that crosses a PEP 440 epoch forward is reported as unmeasurable instead of quietly passing.

        An epoch bump is how a project restarts its numbering after changing versioning schemes, and release
        numbers either side of one are not comparable — ``2.0`` is *older* than ``1!1.0``. Letting the epoch
        clear the window silently would stamp a wrapper that gave callers no warning cycle at all as
        policy-clean, so the skip has to be visible to whoever reads the CI log.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="epoch_switch",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="1!1.0", target=str),
        )
        spec = _build_policy_spec("0.1", False)

        with pytest.warns(UserWarning, match="epoch"):
            violations = _check_policy_for_callables([info], spec)

        assert not [v for v in violations if PolicyRule.MIN_GRACE.value in v]

    @_requires_packaging
    def test_backward_epoch_change_reports_min_grace_violation(self) -> None:
        """A removal that sorts at or before its deprecation across an epoch drop is flagged, not skipped.

        ``remove_in="2.0"`` sorts *before* ``deprecated_in="1!1.0"`` once the epoch is taken into account, so
        no grace window at all was given. Treating this the same as an unmeasurable forward epoch bump would let
        a removal scheduled for the same release — or an earlier one — pass the ``min-grace`` gate silently.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="epoch_switch",
            deprecated_info=DeprecationConfig(deprecated_in="1!1.0", remove_in="2.0", target=str),
        )
        spec = _build_policy_spec("0.1", False)

        violations = _check_policy_for_callables([info], spec)

        assert [v for v in violations if PolicyRule.MIN_GRACE.value in v]

    @_requires_packaging
    def test_same_epoch_backward_removal_reports_min_grace_violation(self) -> None:
        """A backwards patch release fails even when its minor-component delta is zero.

        A repository can accidentally schedule removal in ``1.0.0`` after declaring the deprecation in
        ``1.0.1``. A zero-minor window must not mask that impossible schedule merely because both versions
        share the same minor component.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="backwards_patch",
            deprecated_info=DeprecationConfig(deprecated_in="1.0.1", remove_in="1.0.0", target=str),
        )
        spec = _build_policy_spec("0.0", False)

        violations = _check_policy_for_callables([info], spec)

        assert [v for v in violations if PolicyRule.MIN_GRACE.value in v]

    @_requires_packaging
    def test_same_version_removal_fails_a_zero_count_window(self) -> None:
        """Removing in the very release that deprecated is a violation even under a zero-count window.

        A zero-count window (``"0.0"``, ``{"minor": 0}``) switches the distance check off but still demands a
        clean bump of that unit or coarser, so ``deprecated_in="1.0", remove_in="1.0"`` gives callers no
        warning cycle at all and must keep failing ``min-grace`` — the contract reviewers repeatedly questioned.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="same_release",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="1.0", target=str),
        )
        spec = _build_policy_spec(GraceWindow(0, VersionBump.MINOR), False)

        violations = _check_policy_for_callables([info], spec)

        assert [v for v in violations if PolicyRule.MIN_GRACE.value in v]

    @_requires_packaging
    def test_mixed_batch_warns_once_and_still_evaluates_the_parsable_wrapper(self) -> None:
        """One unparsable version in a batch warns for that wrapper only; the parsable one is still linted.

        A large package with a single typo'd ``remove_in`` must not lose the verdicts of every other wrapper in
        the same scan, and the warning must name the broken wrapper so the typo can be found.
        """
        broken = DeprecationWrapperInfo(
            module="pkg",
            function="broken_version",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="not.a.version!!", target=str),
        )
        too_close = DeprecationWrapperInfo(
            module="pkg",
            function="too_close",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="1.1", target=str),
        )
        spec = _build_policy_spec("0.3", False)

        with pytest.warns(UserWarning, match="broken_version") as record:
            violations = _check_policy_for_callables([broken, too_close], spec)

        assert len(record) == 1
        assert [v for v in violations if PolicyRule.MIN_GRACE.value in v and "too_close" in v]
        assert not [v for v in violations if "broken_version" in v]


def _reject_version_parse(_version_string: str) -> NoReturn:
    """Stand in for ``_parse_version`` on an install that lacks the optional ``packaging`` library."""
    raise ImportError(
        "Version comparison requires the 'packaging' library. Install with: pip install pyDeprecate[audit]"
    )


class TestPolicyVersionParsingIsLazy:
    """A version string is only parsed when a switched-on rule actually reads it."""

    def test_guidance_only_policy_needs_no_version_machinery(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A ``message_required``-only policy reports its violations without the ``packaging`` library.

        A team installs the package without the ``[audit]`` extra and gates CI on one promise — every
        deprecation names a replacement. No version comparison is enabled, so demanding ``packaging`` there
        would turn a check that needs no version arithmetic into an install error.
        """
        monkeypatch.setattr("deprecate.audit._policy._parse_version", _reject_version_parse)
        info = DeprecationWrapperInfo(
            module="pkg",
            function="warns_without_replacement",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="2.0", target=TargetMode.NOTIFY),
        )
        spec = _build_policy_spec(None, True)

        violations = _check_policy_for_callables([info], spec)

        assert [v for v in violations if PolicyRule.MESSAGE_REQUIRED.value in v]

    def test_version_rule_still_reports_missing_packaging(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With the grace-window rule switched on, a missing ``packaging`` still surfaces the install hint.

        The same CI job turns the grace window back on. That rule cannot be evaluated without version
        arithmetic, so skipping it silently would report a green gate that checked nothing — the ImportError,
        which the CLI renders as an install hint, is the honest answer.
        """
        monkeypatch.setattr("deprecate.audit._policy._parse_version", _reject_version_parse)
        info = DeprecationWrapperInfo(
            module="pkg",
            function="no_grace_window",
            deprecated_info=DeprecationConfig(deprecated_in="2.0", remove_in="2.0", target=str),
        )
        spec = _build_policy_spec("0.1", False)

        with pytest.raises(ImportError, match="packaging"):
            _check_policy_for_callables([info], spec)

    def test_genuinely_missing_packaging_import_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A real install without ``packaging`` fails the same way the hand-constructed-``ImportError`` tests assume.

        The other tests in this class fake the failure with ``monkeypatch.setattr(_parse_version, ...)``, which
        proves the *caller* handles an ``ImportError`` correctly but never exercises ``_parse_version``'s own
        ``except ImportError`` branch. Blocking both ``packaging`` and the already-imported ``packaging.version``
        submodule in ``sys.modules`` (the parent alone is insufficient once the submodule is cached from an
        earlier test) forces `from packaging.version import ...` to genuinely fail, so this test exercises the
        real import-failure path instead of a stand-in for it.
        """
        monkeypatch.setitem(sys.modules, "packaging", None)
        monkeypatch.setitem(sys.modules, "packaging.version", None)
        info = DeprecationWrapperInfo(
            module="pkg",
            function="no_grace_window",
            deprecated_info=DeprecationConfig(deprecated_in="2.0", remove_in="2.0", target=str),
        )
        spec = _build_policy_spec("0.1", False)

        with pytest.raises(ImportError, match="packaging"):
            _check_policy_for_callables([info], spec)
