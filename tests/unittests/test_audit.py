"""Unit tests for private helpers in deprecate.audit."""

import importlib
import importlib.metadata
import importlib.util
import types

import pytest

from deprecate.audit import _get_package_version

_PACKAGING_AVAILABLE = importlib.util.find_spec("packaging") is not None
_requires_packaging = pytest.mark.skipif(not _PACKAGING_AVAILABLE, reason="requires packaging library")


class TestGetPackageVersion:
    def test_returns_version_from_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(importlib.metadata, "version", lambda _name: "3.1.4")

        assert _get_package_version("anything") == "3.1.4"

    def test_falls_back_to_dunder_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(importlib.metadata, "version", lambda _name: (_ for _ in ()).throw(Exception("no meta")))

        fake_module = types.ModuleType("fake_pkg")
        fake_module.__version__ = "2.3.4"  # type: ignore[attr-defined]
        monkeypatch.setattr(importlib, "import_module", lambda _name: fake_module)

        assert _get_package_version("fake_pkg") == "2.3.4"

    def test_raises_import_error_when_both_methods_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_exc(_name: str) -> None:
            raise Exception("not found")

        monkeypatch.setattr(importlib.metadata, "version", raise_exc)
        monkeypatch.setattr(importlib, "import_module", raise_exc)

        with pytest.raises(ImportError, match="Could not determine version"):
            _get_package_version("nonexistent_package_xyz")

    def test_raises_import_error_when_module_has_no_dunder_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(importlib.metadata, "version", lambda _name: (_ for _ in ()).throw(Exception("no meta")))

        bare_module = types.ModuleType("bare_pkg")  # no __version__ attribute
        monkeypatch.setattr(importlib, "import_module", lambda _name: bare_module)

        with pytest.raises(ImportError, match="Could not determine version"):
            _get_package_version("bare_pkg")
