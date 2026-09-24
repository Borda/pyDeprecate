"""Unit tests for :mod:`deprecate._version` — current-version detection and Ft-2 escalation-note computation.

``_normalize_version_string``/``_parse_version`` are covered by ``tests/unittests/audit/test__lifecycle.py``
(they moved here from ``deprecate.audit._lifecycle`` but kept identical behaviour, see the module docstring);
this file covers only the two functions added for Ft-2 (``_detect_current_version``, ``_compute_escalation_note``)
plus the ``_resolve_escalation_note`` orchestrator that decoration-time call sites use.
"""

import sys
import types
import warnings
from collections.abc import Iterator

import pytest

from deprecate._version import (
    _cached_package_version,
    _compute_escalation_note,
    _detect_current_version,
    _resolve_escalation_note,
)


@pytest.fixture(autouse=True)
def _clear_package_version_cache() -> Iterator[None]:
    """Clear ``_cached_package_version``'s ``lru_cache`` after each test.

    Tests here register throwaway fake packages under unique names, so a leaked cache entry cannot
    collide with another test's assertion — but leaving it populated for the rest of the session is
    still a trap for whoever next parametrizes or renames one of these fixtures. Teardown, not setup,
    is what matters: the cache is empty until a test populates it.
    """
    yield
    _cached_package_version.cache_clear()


class TestDetectCurrentVersion:
    """``_detect_current_version`` resolves a module's top-level package version, or ``None`` — never raises."""

    def test_resolves_installed_package_via_importlib_metadata(self) -> None:
        """The installed ``deprecate`` package itself resolves through the ``importlib.metadata`` path.

        This is the common case: a real deprecated function inside an installed package whose version
        is registered with the interpreter's package metadata (the normal ``pip install`` path).
        """
        assert _detect_current_version("deprecate.routine") == _detect_current_version("deprecate")

    def test_falls_back_to_module_dunder_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A module not registered with ``importlib.metadata`` still resolves via its own ``__version__``.

        Covers a source tree imported without installation (e.g. a vendored subpackage, or a project
        run straight from a checkout without ``pip install -e .``) — the only version signal available
        is the plain module attribute.
        """
        fake = types.ModuleType("_test_fake_pkg_with_version")
        fake.__version__ = "3.1.4"  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "_test_fake_pkg_with_version", fake)
        assert _detect_current_version("_test_fake_pkg_with_version.sub") == "3.1.4"

    def test_returns_none_when_undetectable(self) -> None:
        """An uninstalled, unimportable package name returns ``None`` instead of raising.

        The escalation path is opt-in and must degrade silently — a typo'd or dynamically-generated
        module name must never crash decoration.
        """
        assert _detect_current_version("_no_such_package_xyz_123") is None


class TestComputeEscalationNote:
    """``_compute_escalation_note`` ramps message urgency from the current/deprecated/remove version triple."""

    def test_mid_window_returns_empty_string(self) -> None:
        """Comfortably inside the deprecation window (not near, not past ``remove_in``) adds no suffix.

        The base warning message already states the deprecated/removal versions; a ramp only earns its
        keep once removal is actually close, so mid-window stays identical to pre-Ft-2 output.
        """
        assert _compute_escalation_note("1.2", "1.0", "2.0") == ""

    def test_past_remove_in_points_at_upstream_release_notes(self) -> None:
        """The current version at or past ``remove_in`` gets the last tier, worded neutrally about cause.

        This tier is only reachable when the package shipped its own ``remove_in`` version without
        deleting the symbol — an upstream schedule slip, since a removal that actually happened would
        raise ``AttributeError`` rather than warn. The caller who triggered the warning is therefore not
        necessarily late, so the text points at the upstream release notes instead of ordering them to
        migrate immediately.
        """
        note = _compute_escalation_note("2.0", "1.0", "2.0")
        assert "planned removal" in note
        assert "v2.0" in note
        assert "release notes" in note
        assert "migrate immediately" not in note

    def test_prerelease_of_remove_in_base_warns_imminent(self) -> None:
        """A release-candidate of the ``remove_in`` version gets the "removal imminent" suffix.

        This is the "last chance" window: the removal version has not shipped yet, but its RC is out,
        so a caller has essentially one release left to migrate.
        """
        note = _compute_escalation_note("2.0rc1", "1.0", "2.0")
        assert "imminent" in note
        assert "v2.0" in note

    def test_missing_remove_in_returns_empty_string(self) -> None:
        """No ``remove_in`` configured means no removal deadline to ramp toward — no suffix."""
        assert _compute_escalation_note("1.5", "1.0", "") == ""

    def test_missing_current_version_returns_empty_string(self) -> None:
        """An undetectable current version (``None``) cannot be compared — no suffix, no crash."""
        assert _compute_escalation_note(None, "1.0", "2.0") == ""

    def test_unparsable_remove_in_returns_empty_string_not_crash(self) -> None:
        """A malformed ``remove_in`` (e.g. a typo) degrades to no suffix instead of raising.

        A misconfigured version string is already surfaced by other decoration-time paths (e.g. audit's
        policy lint); the escalation ramp must not additionally crash decoration over it.
        """
        assert _compute_escalation_note("1.5", "1.0", "not-a-version-!!!") == ""


class TestResolveEscalationNote:
    """``_resolve_escalation_note`` is the decoration-time orchestrator — gates on ``escalate`` and ``packaging``."""

    def test_escalate_false_short_circuits_without_version_detection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``escalate=False`` returns ``""`` without probing ``packaging`` or detecting a version at all.

        This is the default path every existing ``@deprecated`` call takes — it must stay a true no-op,
        not merely a no-op result reached through extra work.
        """
        monkeypatch.setattr(
            "deprecate._version._detect_current_version",
            lambda _: pytest.fail("must not detect a version when escalate=False"),
        )
        assert _resolve_escalation_note(False, "deprecate", "1.0", "2.0", stacklevel=2) == ""

    def test_escalate_true_computes_note_from_detected_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``escalate=True`` detects the current version and forwards it into the ramp computation."""
        monkeypatch.setattr("deprecate._version._detect_current_version", lambda _: "2.0")
        note = _resolve_escalation_note(True, "deprecate", "1.0", "2.0", stacklevel=2)
        assert "planned removal" in note

    def test_missing_packaging_warns_and_returns_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without the ``packaging`` extra, ``escalate=True`` emits ``UserWarning`` and disables the ramp.

        Escalation degrades to the phase-less base message rather than raising ``ImportError`` through
        decoration — a caller without the optional ``audit`` extra installed must not see decoration
        itself start failing just because they opted into ``escalate=True``.
        """
        real_import = __import__

        def _fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "packaging":
                raise ImportError("no packaging installed")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("builtins.__import__", _fake_import)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            note = _resolve_escalation_note(True, "deprecate", "1.0", "2.0", stacklevel=2)
        assert note == ""
        assert len(caught) == 1
        assert issubclass(caught[0].category, UserWarning)
        assert "packaging" in str(caught[0].message)
