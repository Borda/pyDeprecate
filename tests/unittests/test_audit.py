"""Unit tests for private helpers in deprecate.audit."""

import dataclasses
import importlib
import importlib.metadata
import importlib.util
import types
import warnings
from functools import cached_property
from typing import Union

import pytest

from deprecate import TargetMode, deprecated
from deprecate._types import DeprecationConfig, _has_deprecation_meta
from deprecate.audit import (
    ChainType,
    DeprecationStatus,
    DeprecationWrapperInfo,
    _get_deprecation_status,
    _get_package_version,
    _parse_version,
    find_deprecation_wrappers,
    validate_deprecation_wrapper,
)
from deprecate.proxy import _DeprecatedProxy

_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None
_requires_packaging = pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging library")


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
        """@deprecated-decorated callables carry __deprecated__, so the guard returns True."""

        @deprecated(deprecated_in="1.0", remove_in="2.0", target=target_val)
        def fn() -> None:
            pass

        assert _has_deprecation_meta(fn) is True

    def test_returns_false_for_plain_callable(self) -> None:
        """Undecorated callables have no __deprecated__, so the guard returns False."""

        def plain() -> None:
            pass

        assert _has_deprecation_meta(plain) is False

    @pytest.mark.parametrize("obj", [pytest.param("string", id="str"), pytest.param(42, id="int")])
    def test_returns_false_for_non_callable(self, obj: object) -> None:
        """Non-callables without __deprecated__ return False."""
        assert _has_deprecation_meta(obj) is False

    def test_meta_is_deprecation_info_instance(self) -> None:
        """The __deprecated__ attribute on a proxy is a typed DeprecationConfig dataclass."""
        proxy = _DeprecatedProxy(obj={}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert isinstance(object.__getattribute__(proxy, "__deprecated__"), DeprecationConfig)


@_requires_packaging
class TestParseVersion:
    """Tests for _parse_version — wraps packaging.version.Version with a ValueError on bad input."""

    @pytest.mark.parametrize(
        "version",
        ["1.0", "2.3.4", "0.5.0a1", "1.0.0.post1", "1.0.0rc1"],
    )
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
        from packaging.version import Version

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
        from packaging.version import Version

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
        from packaging.version import Version

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
        from packaging.version import Version

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
        from packaging.version import Version

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
        from packaging.version import Version

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
        from tests.collection_targets import TargetColorEnum

        proxy = _DeprecatedProxy(
            obj={}, name="old_enum", deprecated_in="1.0", remove_in="2.0", target=TargetColorEnum, stream=None
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.function == "old_enum"
        assert result.deprecated_info.target is TargetColorEnum
        assert result.no_effect is False

    def test_proxy_with_args_mapping_skips_signature_validation(self) -> None:
        """Proxy __call__ is (*args, **kwargs) so signature check is skipped — invalid_args is always []."""
        from tests.collection_targets import TargetColorEnum

        proxy = _DeprecatedProxy(
            obj={},
            name="mapped",
            deprecated_in="1.0",
            remove_in="2.0",
            target=TargetColorEnum,
            args_mapping={"old_key": "value"},
            stream=None,
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.deprecated_info.args_mapping == {"old_key": "value"}
        assert result.invalid_args == []

    def test_proxy_with_identity_args_mapping_detected(self) -> None:
        """Proxy with an identity args_mapping entry still detects it — invalid_args stays []."""
        from tests.collection_targets import TargetColorEnum

        proxy = _DeprecatedProxy(
            obj={},
            name="identity_mapped",
            deprecated_in="1.0",
            remove_in="2.0",
            target=TargetColorEnum,
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
        from tests.collection_targets import TargetColorEnum

        proxy = _DeprecatedProxy(
            obj={}, name="SourceName", deprecated_in="1.0", remove_in="2.0", target=TargetColorEnum, stream=None
        )
        result = validate_deprecation_wrapper(proxy)
        assert result.function == "SourceName"
        assert result.function != TargetColorEnum.__name__

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
                function="f",
                deprecated_info=DeprecationConfig(),
                empty_mapping=True,
            )
        assert info.empty_args_mapping is True

    def test_old_identity_mapping_kwarg_is_translated(self) -> None:
        """DeprecationWrapperInfo(identity_mapping=[...]) emits DeprecationWarning and sets identity_args_mapping."""
        with pytest.warns(DeprecationWarning, match="renamed to 'identity_args_mapping'"):
            info = DeprecationWrapperInfo(  # type: ignore[call-arg]
                function="f",
                deprecated_info=DeprecationConfig(),
                identity_mapping=["a"],
            )
        assert info.identity_args_mapping == ["a"]

    def test_both_old_kwargs_each_emit_deprecation_warning(self) -> None:
        """Passing both old kwargs emits one DeprecationWarning per renamed field."""
        with pytest.warns(DeprecationWarning, match="renamed") as caught:
            DeprecationWrapperInfo(  # type: ignore[call-arg]
                function="f",
                deprecated_info=DeprecationConfig(),
                empty_mapping=True,
                identity_mapping=["b"],
            )
        categories = [str(w.message) for w in caught.list if issubclass(w.category, DeprecationWarning)]
        assert any("empty_args_mapping" in m for m in categories)
        assert any("identity_args_mapping" in m for m in categories)

    def test_conflict_old_name_wins_when_both_supplied(self) -> None:
        """When both old and new names are supplied (as in replace()), old-name value is honoured."""
        with pytest.warns(DeprecationWarning, match="renamed to 'empty_args_mapping'"):
            info = DeprecationWrapperInfo(  # type: ignore[call-arg]
                function="f",
                deprecated_info=DeprecationConfig(),
                empty_mapping=True,
                empty_args_mapping=False,
            )
        assert info.empty_args_mapping is True

    def test_replace_with_old_name_honoured_over_auto_injected_new(self) -> None:
        """dataclasses.replace() with old name honours caller intent over auto-injected new name.

        ``dataclasses.replace(info, empty_mapping=True)`` merges the caller's ``empty_mapping=True`` with the current
        ``empty_args_mapping=False`` (auto-injected by replace()).  The shim must detect this conflict, discard the
        auto-injected value, and honour the old-name value.

        """
        base = DeprecationWrapperInfo(
            function="f",
            deprecated_info=DeprecationConfig(),
            empty_args_mapping=False,
        )

        with pytest.warns(DeprecationWarning, match="renamed to 'empty_args_mapping'"):
            result = dataclasses.replace(base, empty_mapping=True)  # type: ignore[call-arg]

        assert result.empty_args_mapping is True


class TestFindDeprecationWrappersClassScan:
    """find_deprecation_wrappers discovers @deprecated on class members, peeking through descriptors."""

    def test_finds_deprecated_regular_method(self) -> None:
        """Deprecated regular method on a class is discovered by find_deprecation_wrappers."""
        mod = types.ModuleType("test_mod_method")

        @deprecated(deprecated_in="1.0", remove_in="2.0")
        def _new(self: object) -> int:
            return 1

        class OldCls:
            old_method = _new

        OldCls.__module__ = mod.__name__
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_method" in n for n in names)

    def test_finds_deprecated_classmethod(self) -> None:
        """Deprecated classmethod (correct @classmethod @deprecated order) is discovered."""
        mod = types.ModuleType("test_mod_cm")

        class OldCls:
            @classmethod
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_cm(cls: type) -> int:
                """Old classmethod."""
                return 1

        OldCls.__module__ = mod.__name__
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_cm" in n for n in names)

    def test_finds_deprecated_staticmethod(self) -> None:
        """Deprecated staticmethod (correct @staticmethod @deprecated order) is discovered."""
        mod = types.ModuleType("test_mod_sm")

        class OldCls:
            @staticmethod
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_sm() -> int:
                """Old staticmethod."""
                return 1

        OldCls.__module__ = mod.__name__
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_sm" in n for n in names)

    def test_finds_deprecated_property(self) -> None:
        """Deprecated property (correct @property @deprecated order) is discovered."""
        mod = types.ModuleType("test_mod_prop")

        class OldCls:
            @property
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_prop(self: object) -> int:
                """Old property."""
                return 1

        OldCls.__module__ = mod.__name__
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_prop" in n for n in names)

    def test_finds_deprecated_cached_property(self) -> None:
        """Deprecated cached_property (correct @cached_property @deprecated order) is discovered."""
        mod = types.ModuleType("test_mod_cp")

        class OldCls:
            @cached_property
            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_cp(self: object) -> int:
                """Old cached_property."""
                return 1

        OldCls.__module__ = mod.__name__
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_cp" in n for n in names)

    def test_finds_outer_deprecated_classmethod(self) -> None:
        """Outer @deprecated @classmethod order: wrapper is discovered by audit scan."""
        mod = types.ModuleType("test_mod_outer_cm")

        class OldCls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[arg-type]
            @classmethod
            def old_cm(cls: type) -> int:
                """Old classmethod."""
                return 1

        OldCls.__module__ = mod.__name__
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_cm" in n for n in names)

    def test_finds_outer_deprecated_staticmethod(self) -> None:
        """Outer @deprecated @staticmethod order: wrapper is discovered by audit scan."""
        mod = types.ModuleType("test_mod_outer_sm")

        class OldCls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[arg-type]
            @staticmethod
            def old_sm() -> int:
                """Old staticmethod."""
                return 1

        OldCls.__module__ = mod.__name__
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_sm" in n for n in names)

    def test_finds_outer_deprecated_property(self) -> None:
        """Outer @deprecated @property order: wrapper is discovered by audit scan."""
        mod = types.ModuleType("test_mod_outer_prop")

        class OldCls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @property
            def old_prop(self: object) -> int:
                """Old property."""
                return 1

        OldCls.__module__ = mod.__name__
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_prop" in n for n in names)

    def test_finds_outer_deprecated_cached_property(self) -> None:
        """Outer @deprecated @cached_property order: wrapper is discovered by audit scan."""
        mod = types.ModuleType("test_mod_outer_cp")

        class OldCls:
            @deprecated(deprecated_in="1.0", remove_in="2.0")  # type: ignore[prop-decorator]
            @cached_property
            def old_cp(self: object) -> int:
                """Old cached_property."""
                return 1

        OldCls.__module__ = mod.__name__
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("old_cp" in n for n in names)

    def test_finds_setter_only_property(self) -> None:
        """Explicit property(fget=None, fset=deprecated_fset) is discovered by audit scan."""
        mod = types.ModuleType("test_mod_setter_only")

        def _fset(self: object, v: int) -> None:
            pass

        class OldCls:
            write_only: property = deprecated(deprecated_in="1.0", remove_in="2.0")(property(None, _fset))  # type: ignore[assignment,arg-type]

        OldCls.__module__ = mod.__name__
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("write_only" in n for n in names)

    def test_finds_explicit_construction_fset_deprecated(self) -> None:
        """Explicit property(plain_fget, deprecated_fset): fset accessor is discovered."""
        mod = types.ModuleType("test_mod_explicit_fset")

        def _plain_fget(self: object) -> int:
            return 1

        def _fset(self: object, v: int) -> None:
            pass

        _deprecated_fset = deprecated(deprecated_in="1.0", remove_in="2.0")(_fset)

        class OldCls:
            rw_prop: property = property(_plain_fget, _deprecated_fset)

        OldCls.__module__ = mod.__name__
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("rw_prop" in n for n in names)

    def test_finds_deleter_only_property(self) -> None:
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

        OldCls.__module__ = mod.__name__
        mod.OldCls = OldCls  # type: ignore[attr-defined]

        with warnings.catch_warnings():
            warnings.simplefilter("always")
            results = find_deprecation_wrappers(mod)

        names = [r.function for r in results]
        assert any("delete_only" in n for n in names)
