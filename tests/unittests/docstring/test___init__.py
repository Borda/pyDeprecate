"""Smoke tests for the optional docs-build extensions of :mod:`deprecate.docstring`."""

import importlib
import importlib.util

import pytest


class TestDocstringSubpackageImports:
    """Smoke tests: docstring sub-modules are importable and expose expected symbols."""

    @pytest.mark.skipif(importlib.util.find_spec("griffe") is None, reason="griffe not installed")
    def test_griffe_ext_importable(self) -> None:
        """deprecate.docstring.griffe_ext imports without error and exposes RuntimeDocstrings."""
        mod = importlib.import_module("deprecate.docstring.griffe_ext")
        assert hasattr(mod, "RuntimeDocstrings")

    def test_sphinx_ext_importable(self) -> None:
        """deprecate.docstring.sphinx_ext imports without error and exposes setup()."""
        mod = importlib.import_module("deprecate.docstring.sphinx_ext")
        assert callable(mod.setup)
