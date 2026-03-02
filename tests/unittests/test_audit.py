"""Unit tests for private helpers in deprecate.audit."""

import importlib
import importlib.metadata
import importlib.util
import types

import pytest

from deprecate import deprecated
from deprecate._types import DeprecationInfo, _has_deprecation_meta
from deprecate.audit import _get_package_version, _parse_version, validate_deprecated_callable
from deprecate.proxy import _DeprecatedProxy

_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None
_requires_packaging = pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging library")


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
        fake_module.__version__ = "2.3.4"  # type: ignore[attr-defined]
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
        """_DeprecatedProxy objects carry DeprecationInfo, so the guard returns True."""
        proxy = _DeprecatedProxy(obj={}, name="x", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert _has_deprecation_meta(proxy) is True

    def test_returns_true_for_deprecated_decorated_callable(self) -> None:
        """@deprecated-decorated callables carry __deprecated__, so the guard returns True."""

        @deprecated(deprecated_in="1.0", remove_in="2.0", target=True)
        def fn() -> None:
            pass

        assert _has_deprecation_meta(fn) is True

    def test_returns_false_for_plain_callable(self) -> None:
        """Undecorated callables have no __deprecated__, so the guard returns False."""

        def plain() -> None:
            pass

        assert _has_deprecation_meta(plain) is False

    def test_returns_false_for_non_callable(self) -> None:
        """Non-callables without __deprecated__ return False."""
        assert _has_deprecation_meta("string") is False
        assert _has_deprecation_meta(42) is False

    def test_meta_is_deprecation_info_instance(self) -> None:
        """The __deprecated__ attribute on a proxy is a typed DeprecationInfo dataclass."""
        proxy = _DeprecatedProxy(obj={}, name="cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        assert isinstance(object.__getattribute__(proxy, "__deprecated__"), DeprecationInfo)


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


@_requires_packaging
class TestValidateDeprecatedCallableWithProxy:
    """Unit tests for validate_deprecated_callable with inline _DeprecatedProxy objects.

    Uses _DeprecatedProxy directly (not collection fixtures) for true isolation.
    """

    def test_proxy_without_target_no_effect_false(self) -> None:
        """Proxy with no forwarding target is effective (still emits warnings) → no_effect=False."""
        proxy = _DeprecatedProxy(obj={}, name="legacy_cfg", deprecated_in="1.0", remove_in="2.0", stream=None)
        result = validate_deprecated_callable(proxy)
        assert result.function == "legacy_cfg"
        assert result.no_effect is False
        assert result.chain_type is None

    def test_proxy_with_callable_target_no_effect_false(self) -> None:
        """Proxy forwarding to a callable target is effective → no_effect=False."""
        from tests.collection_targets import TargetColorEnum

        proxy = _DeprecatedProxy(
            obj={}, name="old_enum", deprecated_in="1.0", remove_in="2.0", target=TargetColorEnum, stream=None
        )
        result = validate_deprecated_callable(proxy)
        assert result.function == "old_enum"
        assert result.deprecated_info.target is TargetColorEnum
        assert result.no_effect is False

    def test_proxy_with_args_mapping_reports_invalid_args(self) -> None:
        """Proxy args_mapping keys absent from the callable signature appear in invalid_args."""
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
        result = validate_deprecated_callable(proxy)
        assert result.deprecated_info.args_mapping == {"old_key": "value"}
        assert "old_key" in result.invalid_args

    def test_proxy_function_name_from_dep_info(self) -> None:
        """function field comes from dep_info.name, not from getattr(proxy, '__name__').

        Without this, getattr routes through __getattr__ and leaks the target's __name__.
        """
        from tests.collection_targets import TargetColorEnum

        proxy = _DeprecatedProxy(
            obj={}, name="SourceName", deprecated_in="1.0", remove_in="2.0", target=TargetColorEnum, stream=None
        )
        result = validate_deprecated_callable(proxy)
        assert result.function == "SourceName"
        assert result.function != TargetColorEnum.__name__

    def test_proxy_empty_mapping_true_when_no_args_mapping(self) -> None:
        """Proxy with args_mapping=None reports empty_mapping=True."""
        proxy = _DeprecatedProxy(obj={}, name="x", deprecated_in="1.0", remove_in="2.0", stream=None)
        result = validate_deprecated_callable(proxy)
        assert result.deprecated_info.args_mapping is None
        assert result.empty_mapping is True
