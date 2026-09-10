"""Unit tests for private helpers in deprecate.audit."""

import dataclasses
import importlib
import importlib.metadata
import importlib.util
import types
import warnings
from functools import cached_property
from typing import NoReturn, Union

import pytest

import tests.collection_deprecate as col
import tests.collection_misconfigured as clean_module
from deprecate import (
    PolicyRule,
    TargetMode,
    VersionBump,
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
    _build_policy_spec,
    _check_expiry_for_callables,
    _check_policy_for_callables,
    _classify_member_api_type,
    _format_report_target,
    _get_deprecation_status,
    _get_package_version,
    _has_migration_guidance,
    _member_has_deprecation_meta,
    _normalize_version_string,
    _parse_grace_window,
    _parse_version,
    _satisfies_grace_window,
    _satisfies_removal_cadence,
    _scan_class,
    find_deprecation_wrappers,
    validate_deprecation_wrapper,
)
from deprecate.proxy import _DeprecatedProxy, deprecated_class
from tests.collection_targets import ColorEnum, PositionalOnlyTarget

_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None
_requires_packaging = pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging library")

# Optional dependency: ``packaging`` ships with the ``[audit]`` extra. Guard the import at module level so
# collection never fails; the tests that use ``Version`` are gated by ``@_requires_packaging``.
if _PACKAGING_AVAILABLE:
    from packaging.version import Version


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


class TestParseGraceWindow:
    """Parsing of the ``min_grace`` specification string."""

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            pytest.param("1 minor", (1, VersionBump.MINOR), id="singular-unit"),
            pytest.param("2 majors", (2, VersionBump.MAJOR), id="plural-unit"),
            pytest.param("1 patches", (1, VersionBump.PATCH), id="english-plural-of-patch"),
            pytest.param("  3patch ", (3, VersionBump.PATCH), id="no-space-and-padding"),
            pytest.param("1 MINOR", (1, VersionBump.MINOR), id="upper-case-unit"),
            pytest.param("0 minor", (0, VersionBump.MINOR), id="zero-count-disables-distance"),
        ],
    )
    def test_accepts_documented_spellings(self, spec: str, expected: tuple[int, VersionBump]) -> None:
        """Every documented spelling of a grace window parses to its count and unit.

        A policy is configured from a CLI flag or a keyword argument typed by hand, so the accepted spellings
        have to cover the plural, the missing space, and the shouted unit a real invocation produces. That
        includes the English plural of *patch*: someone writing ``"3 patches"`` in a CI config means the same
        window as ``"3 patch"``, and rejecting it sends them hunting through the source for the spelling.
        """
        assert _parse_grace_window(spec) == expected

    @pytest.mark.parametrize(
        "spec",
        [
            pytest.param("one minor", id="word-count"),
            pytest.param("1 release", id="unknown-unit"),
            pytest.param("minor", id="count-missing"),
            pytest.param("", id="empty"),
        ],
    )
    def test_rejects_unparseable_specification(self, spec: str) -> None:
        """An unrecognised grace window fails loudly instead of silently disabling the rule.

        A typo such as ``"1 release"`` that quietly turned the grace rule off would leave a CI gate reporting
        green while checking nothing at all, so the specification is validated before the scan starts.
        """
        with pytest.raises(ValueError, match="Invalid `min_grace` specification"):
            _parse_grace_window(spec)


class TestBuildPolicySpec:
    """Validation of the raw policy arguments before any wrapper is scanned."""

    def test_disabled_rules_carry_none(self) -> None:
        """Passing ``None`` for a rule disables it rather than falling back to the default.

        A project that removes APIs at minor releases needs the cadence rule off while keeping the rest, so a
        disabled rule must survive as ``None`` all the way into the per-wrapper checks.
        """
        spec = _build_policy_spec(None, None, False, False)
        assert spec.grace is None
        assert spec.removal_cadence is None

    def test_accepts_version_bump_member_for_cadence(self) -> None:
        """``remove_only_at`` accepts a VersionBump member as well as its string value.

        Python callers hold the enum, CLI callers hold the string; both spellings must reach the same
        configuration so the rule cannot behave differently depending on the entry point.
        """
        assert _build_policy_spec(None, VersionBump.MINOR, True, True).removal_cadence is VersionBump.MINOR

    def test_rejects_unknown_cadence_level(self) -> None:
        """An unknown removal cadence raises instead of silently skipping the rule.

        ``remove_only_at="release"`` is a plausible typo; accepting it silently would drop the rule from a CI
        gate that still reports success.
        """
        with pytest.raises(ValueError, match="Invalid `remove_only_at` level"):
            _build_policy_spec(None, "release", True, True)


class TestSatisfiesGraceWindow:
    """Version-distance arithmetic behind the ``min-grace`` rule."""

    @pytest.mark.parametrize(
        ("deprecated_in", "remove_in", "count", "unit", "expected"),
        [
            pytest.param("1.0", "1.1", 1, VersionBump.MINOR, True, id="exactly-one-minor"),
            pytest.param("1.0", "1.0", 1, VersionBump.MINOR, False, id="same-release"),
            pytest.param("1.2", "2.0", 1, VersionBump.MINOR, True, id="major-bump-clears-minor-window"),
            pytest.param("1.0", "1.1", 2, VersionBump.MINOR, False, id="two-minors-required"),
            pytest.param("1.0", "2.0", 1, VersionBump.MAJOR, True, id="one-major"),
            pytest.param("1.0", "1.9", 1, VersionBump.MAJOR, False, id="minors-do-not-clear-major-window"),
            pytest.param("1.0.0", "1.0.1", 1, VersionBump.PATCH, True, id="one-patch"),
            pytest.param("1.0.0", "1.1.0", 1, VersionBump.PATCH, True, id="minor-bump-clears-patch-window"),
            pytest.param("1.0", "2.0rc1", 1, VersionBump.MINOR, True, id="pre-release-of-next-major"),
            pytest.param("1.0", "1!1.0", 1, VersionBump.MAJOR, True, id="epoch-bump-clears-any-window"),
            pytest.param("1!1.0", "2.0", 1, VersionBump.MAJOR, False, id="epoch-drop-is-not-a-later-version"),
        ],
    )
    @_requires_packaging
    def test_distance_between_versions(
        self, deprecated_in: str, remove_in: str, count: int, unit: VersionBump, expected: bool
    ) -> None:
        """The grace window measures distance in its own unit, with coarser bumps counting as satisfied.

        A wrapper deprecated in ``1.2`` and removed in ``2.0`` has a *smaller* minor number at removal even
        though callers got a whole major cycle; measuring the components naively would flag that healthy
        schedule while letting a same-release removal through.
        """
        assert (
            _satisfies_grace_window(_parse_version(deprecated_in), _parse_version(remove_in), count, unit) is expected
        )


class TestSatisfiesRemovalCadence:
    """Release-level restriction behind the ``remove-only-at`` rule."""

    @pytest.mark.parametrize(
        ("remove_in", "cadence", "expected"),
        [
            pytest.param("2.0", VersionBump.MAJOR, True, id="major-release"),
            pytest.param("2.0.0", VersionBump.MAJOR, True, id="explicit-zero-patch"),
            pytest.param("2.1", VersionBump.MAJOR, False, id="minor-under-major-cadence"),
            pytest.param("2.0.1", VersionBump.MAJOR, False, id="patch-under-major-cadence"),
            pytest.param("2.1", VersionBump.MINOR, True, id="minor-under-minor-cadence"),
            pytest.param("2.1.3", VersionBump.MINOR, False, id="patch-under-minor-cadence"),
            pytest.param("2.1.3", VersionBump.PATCH, True, id="patch-cadence-allows-everything"),
            pytest.param("2.0rc1", VersionBump.MAJOR, True, id="pre-release-of-a-major"),
            pytest.param("1!2.0", VersionBump.MAJOR, True, id="epoch-does-not-change-release-shape"),
            pytest.param("2", VersionBump.MAJOR, True, id="bare-major-without-minor-or-patch"),
            pytest.param("2.0.0.1", VersionBump.MAJOR, False, id="fourth-component-under-major-cadence"),
            pytest.param("2.1.0.1", VersionBump.MINOR, False, id="fourth-component-under-minor-cadence"),
        ],
    )
    @_requires_packaging
    def test_removal_version_against_cadence(self, remove_in: str, cadence: VersionBump, expected: bool) -> None:
        """A removal version is judged by the release level it lands on, not by its distance from anything.

        A team that promises "breaking changes only in majors" needs ``2.0.1`` rejected even though it is far
        past the deprecation; the rule reads the version shape, which is exactly the promise callers rely on.
        PEP 440 allows more than three release components, and a project on a four-part scheme ships ``2.0.0.1``
        as a follow-up release — reading only ``minor``/``micro`` would wave it through as a clean major.
        """
        assert _satisfies_removal_cadence(_parse_version(remove_in), cadence) is expected


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
                DeprecationConfig(target=TargetMode.ARGS_REMAP, args_mapping={}),
                False,
                id="remap-mode-with-empty-mapping-renames-nothing",
            ),
            pytest.param(
                DeprecationConfig(target=TargetMode.ATTRS_REMAP),
                False,
                id="attrs-remap-mode-without-mapping",
            ),
        ],
    )
    def test_guidance_sources(self, config: DeprecationConfig, expected: bool) -> None:
        """Any of a target, a mapping, or a custom message counts as telling callers where to go.

        The rule exists to catch the dead-end warning ("this is deprecated", full stop); a wrapper that renames
        arguments or carries a hand-written migration sentence is not a dead end even without a target.
        """
        info = DeprecationWrapperInfo(module="pkg", function="old_api", deprecated_info=config)
        assert _has_migration_guidance(info) is expected

    def test_module_rendered_message_is_not_guidance(self) -> None:
        """A deprecated module's pre-rendered warning text does not count as migration guidance.

        ``deprecated_module()`` stores its already-substituted warning in ``message_template``, so treating that
        field as guidance would make the rule permanently inert for every deprecated module.
        """
        info = DeprecationWrapperInfo(
            module="pkg.old_mod",
            deprecated_info=DeprecationConfig(target=None, message_template="`pkg.old_mod` is deprecated"),
            api_type="module",
        )
        assert _has_migration_guidance(info) is False


class TestValidateDeprecationPolicy:
    """End-to-end policy scan over the ``tests.collection_policy`` fixtures."""

    @pytest.mark.parametrize(
        ("wrapper_name", "rule"),
        [
            pytest.param("no_grace_window", PolicyRule.MIN_GRACE, id="min-grace"),
            pytest.param("removed_at_patch", PolicyRule.REMOVE_ONLY_AT, id="remove-only-at"),
            pytest.param("warns_without_replacement", PolicyRule.MESSAGE_REQUIRED, id="message-required"),
            pytest.param("WarnOnlyLegacyClass", PolicyRule.MESSAGE_REQUIRED, id="message-required-proxy"),
            pytest.param(
                "deprecated_in_the_future", PolicyRule.DEPRECATED_IN_NOT_FUTURE, id="deprecated-in-not-future"
            ),
        ],
    )
    @_requires_packaging
    def test_each_fixture_trips_its_rule(self, wrapper_name: str, rule: PolicyRule) -> None:
        """Every governance rule fires on the wrapper that breaks it, and reports it under its own slug.

        This is the reviewer-facing contract: a PR that schedules a removal one patch after the deprecation, or
        warns without naming a replacement, has to come back with a message naming *which* policy it broke. The
        sweep runs with every rule switched on, including the opt-in future-dating one, so each fixture is judged
        by the rule it was written for.
        """
        violations = validate_deprecation_policy(
            "tests.collection_policy", "2.0", recursive=False, deprecated_in_not_future=True
        )
        matching = [v for v in violations if wrapper_name in v]
        assert len(matching) == 1
        assert matching[0].startswith(f"[{rule.value}]")

    @_requires_packaging
    def test_future_dating_rule_is_off_by_default(self) -> None:
        """A ``deprecated_in`` ahead of the released version is not reported unless the rule is asked for.

        Stamping a wrapper with the release it will ship in is the ordinary development workflow — the version is
        ahead of the published one until that release is cut — so a default-on rule would flag routine PRs and
        train the team to ignore the gate.
        """
        violations = validate_deprecation_policy("tests.collection_policy", "2.0", recursive=False)
        assert not [v for v in violations if PolicyRule.DEPRECATED_IN_NOT_FUTURE.value in v]

    @_requires_packaging
    def test_compliant_wrapper_is_not_reported(self) -> None:
        """A wrapper deprecated one release before a major removal, with a target, trips no rule.

        The gate is only useful if the disciplined case passes silently — a policy that flags every wrapper is
        one a team turns off in its first week.
        """
        violations = validate_deprecation_policy("tests.collection_policy", "2.0", recursive=False)
        assert not [v for v in violations if "compliant_forward" in v]

    @_requires_packaging
    def test_disabled_rule_stops_reporting(self) -> None:
        """Setting a rule to ``None`` removes its violations without affecting the other rules.

        Projects that ship removals at minor releases must be able to keep the grace-window and guidance rules
        while dropping the cadence rule, instead of abandoning the whole gate.
        """
        violations = validate_deprecation_policy("tests.collection_policy", "2.0", recursive=False, remove_only_at=None)
        assert not [v for v in violations if PolicyRule.REMOVE_ONLY_AT.value in v]
        assert [v for v in violations if PolicyRule.MIN_GRACE.value in v]

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
        spec = _build_policy_spec("1 minor", "major", True, True)
        assert _check_policy_for_callables([info], "2.0", spec) == []

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
        spec = _build_policy_spec("1 minor", "major", True, True)
        with pytest.warns(UserWarning, match="unparsable `remove_in`"):
            assert _check_policy_for_callables([info], "2.0", spec) == []

    @pytest.mark.parametrize(
        ("deprecated_in", "remove_in"),
        [
            pytest.param("1.0", "1!1.0", id="epoch-raised"),
            pytest.param("1!1.0", "2.0", id="epoch-dropped"),
        ],
    )
    @_requires_packaging
    def test_epoch_change_warns_and_skips_the_grace_window(self, deprecated_in: str, remove_in: str) -> None:
        """A removal that crosses a PEP 440 epoch is reported as unmeasurable instead of quietly passing.

        An epoch bump is how a project restarts its numbering after changing versioning schemes, and release
        numbers either side of one are not comparable — ``2.0`` is *older* than ``1!1.0``. Letting the epoch
        clear the window silently would stamp a wrapper that gave callers no warning cycle at all as
        policy-clean, so the skip has to be visible to whoever reads the CI log.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="epoch_switch",
            deprecated_info=DeprecationConfig(deprecated_in=deprecated_in, remove_in=remove_in, target=str),
        )
        spec = _build_policy_spec("1 minor", None, False, False)

        with pytest.warns(UserWarning, match="epoch"):
            violations = _check_policy_for_callables([info], "2.0", spec)

        assert not [v for v in violations if PolicyRule.MIN_GRACE.value in v]

    @_requires_packaging
    def test_unresolved_current_version_keeps_version_distance_rules(self) -> None:
        """Without a current version the future-dating rule is skipped while the other rules still run.

        Scanning a package that is not installed (a checkout in CI before ``pip install``) should still catch a
        removal scheduled at a patch release — only the rule that genuinely needs the released version drops out.
        """
        info = DeprecationWrapperInfo(
            module="pkg",
            function="removed_at_patch",
            deprecated_info=DeprecationConfig(deprecated_in="9.0", remove_in="9.0.1", target=str),
        )
        spec = _build_policy_spec("1 minor", "major", True, True)
        violations = _check_policy_for_callables([info], None, spec)
        assert [v for v in violations if PolicyRule.REMOVE_ONLY_AT.value in v]
        assert not [v for v in violations if PolicyRule.DEPRECATED_IN_NOT_FUTURE.value in v]

    @_requires_packaging
    def test_rejects_invalid_current_version(self) -> None:
        """An unparsable ``current_version`` fails fast rather than silently disabling the future-dating rule.

        The version usually arrives from a CI variable; a malformed value has to surface as an error at the call
        site instead of quietly shrinking the rule set the pipeline believes it is running.
        """
        spec = _build_policy_spec(None, None, False, True)
        with pytest.raises(ValueError, match="Invalid current_version"):
            _check_policy_for_callables([], "not.a.version!!", spec)


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
        monkeypatch.setattr("deprecate.audit._parse_version", _reject_version_parse)
        info = DeprecationWrapperInfo(
            module="pkg",
            function="warns_without_replacement",
            deprecated_info=DeprecationConfig(deprecated_in="1.0", remove_in="2.0", target=TargetMode.NOTIFY),
        )
        spec = _build_policy_spec(None, None, True, False)

        violations = _check_policy_for_callables([info], "2.0", spec)

        assert [v for v in violations if PolicyRule.MESSAGE_REQUIRED.value in v]

    def test_version_rule_still_reports_missing_packaging(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With a version-comparison rule switched on, a missing ``packaging`` still surfaces the install hint.

        The same CI job turns the grace window back on. That rule cannot be evaluated without version
        arithmetic, so skipping it silently would report a green gate that checked nothing — the ImportError,
        which the CLI renders as an install hint, is the honest answer.
        """
        monkeypatch.setattr("deprecate.audit._parse_version", _reject_version_parse)
        info = DeprecationWrapperInfo(
            module="pkg",
            function="no_grace_window",
            deprecated_info=DeprecationConfig(deprecated_in="2.0", remove_in="2.0", target=str),
        )
        spec = _build_policy_spec("1 minor", None, False, False)

        with pytest.raises(ImportError, match="packaging"):
            _check_policy_for_callables([info], "2.0", spec)

    @_requires_packaging
    def test_caller_supplied_version_is_validated_with_version_rules_off(self) -> None:
        """A malformed version the caller passed in is rejected even when no enabled rule would read it.

        The value normally arrives from a CI variable, so a typo has to fail at the call site; letting it
        through would leave the pipeline believing it validated a version that was never looked at, until
        someone enables the future-dating rule months later and the error surfaces in an unrelated PR.
        """
        spec = _build_policy_spec(None, None, True, False)

        with pytest.raises(ValueError, match="Invalid current_version"):
            _check_policy_for_callables([], "not.a.version!!", spec, version_explicit=True)

    def test_auto_detected_version_is_left_alone_when_no_rule_reads_it(self) -> None:
        """An unparsable *installed* version does not break a policy run that never consults it.

        A package built from a checkout can advertise a non-PEP-440 version (a ``git describe`` string, for
        one). Nobody asked for that version — it was auto-detected — so with every version rule off the
        guidance rules must still run instead of the gate dying on a value it does not need.
        """
        spec = _build_policy_spec(None, None, True, False)

        assert _check_policy_for_callables([], "not.a.version!!", spec) == []
