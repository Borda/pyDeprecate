"""Unit tests for private helpers in :mod:`deprecate.audit._lifecycle`."""

import importlib
import importlib.metadata
import importlib.util
import types
import warnings

import pytest

from deprecate import (
    TargetMode,
    deprecated,
    validate_deprecation_expiry,
)
from deprecate._types import DeprecationConfig
from deprecate.audit import (
    DeprecationStatus,
    DeprecationWrapperInfo,
    validate_deprecation_wrapper,
)
from deprecate.audit._lifecycle import (
    _check_expiry_for_callables,
    _get_deprecation_status,
    _get_package_version,
    _normalize_version_string,
    _parse_version,
)

_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None
_requires_packaging = pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging library")

# Optional dependency: ``packaging`` ships with the ``[audit]`` extra. Guard the import at module level so
# collection never fails; the tests that use ``Version`` are gated by ``@_requires_packaging``.
if _PACKAGING_AVAILABLE:
    from packaging.version import Version


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
