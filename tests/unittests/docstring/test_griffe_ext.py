"""Unit tests for the Griffe extension in :mod:`deprecate.docstring.griffe_ext`."""

import importlib
import importlib.util
import sys
import types
import warnings
from typing import cast

import pytest

# Optional dependency: ``griffe`` ships with ``mkdocstrings[python]``. Guard at module level so collection never
# fails; the behavioural tests below are gated by ``_skipif_griffe_missing``.
_GRIFFE_AVAILABLE = importlib.util.find_spec("griffe") is not None
if _GRIFFE_AVAILABLE:
    import griffe

    from deprecate.docstring.griffe_ext import RuntimeDocstrings

_skipif_griffe_missing = pytest.mark.skipif(not _GRIFFE_AVAILABLE, reason="griffe not installed")


def _load_docstrings_module() -> "griffe.Module":
    """Load ``tests.collections.docstrings`` into a fresh Griffe module graph.

    A new :class:`~griffe.GriffeLoader` is created per call so that each test operates on an independent object
    graph — :meth:`RuntimeDocstrings.on_module` mutates docstrings in place, so a shared graph would leak state.

    """
    return griffe.GriffeLoader().load("tests.collections.docstrings")


@_skipif_griffe_missing
class TestRuntimeDocstrings:
    """Behavioural tests for the Griffe extension that mirrors runtime ``__doc__`` into the docs build.

    ``@deprecated(update_docstring=True)`` rewrites ``fn.__doc__`` at decoration time, but Griffe reads docstrings
    from the source AST and never sees that runtime change. :class:`RuntimeDocstrings` bridges the gap by importing
    the module at runtime and copying the live ``__doc__`` over the statically-parsed Griffe docstring — so that a
    ``mkdocstrings`` render shows the injected deprecation notice.

    """

    def test_on_module_injects_runtime_docstring(self) -> None:
        """A deprecated function's Griffe docstring is replaced with its runtime ``__doc__`` after ``on_module``.

        ``old_function`` is decorated with ``update_docstring=True``, so its runtime ``__doc__`` carries a
        ``.. deprecated:: 0.1`` block absent from the source AST.  Before the hook runs Griffe only knows the
        static summary; after ``on_module`` the Griffe docstring must equal the live ``__doc__``.

        """
        mod = _load_docstrings_module()

        assert mod["old_function"].docstring.value == "An old function that is deprecated."
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            RuntimeDocstrings().on_module(mod=mod, loader=griffe.GriffeLoader())

        patched = mod["old_function"].docstring.value
        assert ".. deprecated:: 0.1" in patched
        assert "new_function" in patched
        assert patched != "An old function that is deprecated."

    def test_on_module_recurses_into_class_members(self) -> None:
        """The hook descends into class members so a deprecated ``__init__`` also gets its runtime docstring.

        ``OldClass.__init__`` carries the ``@deprecated`` decorator; the notice lives on the method's runtime
        ``__doc__``.  ``_update_obj`` must recurse into the class body and patch the ``__init__`` docstring, not
        only top-level functions.

        """
        mod = _load_docstrings_module()

        assert mod["OldClass"].members["__init__"].docstring.value == "Initialize the old class."
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            RuntimeDocstrings().on_module(mod=mod, loader=griffe.GriffeLoader())

        patched_init = mod["OldClass"].members["__init__"].docstring.value
        assert ".. deprecated:: 0.2" in patched_init
        assert patched_init != "Initialize the old class."

    def test_replace_docstring_leaves_non_deprecated_object_untouched(self) -> None:
        """A callable without ``__deprecation_config__`` keeps its statically-parsed Griffe docstring.

        ``new_function`` is the plain replacement target — it has no ``__deprecation_config__`` attribute, so
        ``_replace_docstring`` must return early and leave the original source docstring intact.

        """
        mod = _load_docstrings_module()
        runtime_new = importlib.import_module("tests.collections.docstrings").new_function

        RuntimeDocstrings._replace_docstring(mod["new_function"], runtime_new)

        assert mod["new_function"].docstring.value == "A new function that is the target."

    def test_replace_docstring_skips_when_runtime_doc_is_empty(self) -> None:
        """A deprecated function whose runtime ``__doc__`` is ``None`` is left unchanged.

        ``old_function_plain`` has ``update_docstring=True`` but no source docstring, so its runtime ``__doc__``
        stays ``None``.  ``_replace_docstring`` must not raise and must not fabricate a docstring.

        """
        mod = _load_docstrings_module()
        runtime_plain = importlib.import_module("tests.collections.docstrings").old_function_plain

        RuntimeDocstrings._replace_docstring(mod["old_function_plain"], runtime_plain)

        assert mod["old_function_plain"].docstring is None

    def test_replace_docstring_no_op_when_griffe_docstring_missing(self) -> None:
        """When the Griffe object has no docstring to overwrite, a deprecated runtime object is a no-op.

        Simulates a callable whose source had no docstring (Griffe docstring ``None``) yet whose runtime object
        carries the injected notice — the guard at the ``griffe_obj.docstring is None`` branch must return without
        error rather than attempt to assign ``.value`` on ``None``.

        """
        mod = _load_docstrings_module()
        griffe_obj = mod["old_function"]
        griffe_obj.docstring = None  # type: ignore[assignment]
        runtime_old = importlib.import_module("tests.collections.docstrings").old_function

        RuntimeDocstrings._replace_docstring(griffe_obj, runtime_old)

        assert griffe_obj.docstring is None

    def test_import_module_fast_path_returns_importable_module(self) -> None:
        """A module already importable by name resolves via the fast path without touching ``sys.path``."""
        fake_mod = types.SimpleNamespace(name="os", filepath=None)

        assert RuntimeDocstrings._import_module(cast("griffe.Module", fake_mod)) is importlib.import_module("os")

    def test_import_module_returns_none_when_unresolvable(self) -> None:
        """An un-importable name with no ``filepath`` yields ``None`` instead of raising."""
        fake_mod = types.SimpleNamespace(name="deprecate_no_such_module_xyz", filepath=None)

        assert RuntimeDocstrings._import_module(cast("griffe.Module", fake_mod)) is None

    def test_update_obj_skips_when_runtime_attribute_absent(self) -> None:
        """A Griffe member with no matching runtime attribute is skipped, leaving its docstring untouched."""
        mod = _load_docstrings_module()
        original = mod["old_function"].docstring.value

        RuntimeDocstrings()._update_obj(mod["old_function"], object(), "missing_runtime_name")

        assert mod["old_function"].docstring.value == original


class TestWithoutGriffe:
    """The extension module stays importable when ``griffe`` is not installed."""

    def test_import_exposes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without ``griffe`` the module imports cleanly, defines no extension and exports nothing.

        A project that installs pyDeprecate without the mkdocs docs extras may still import every
        ``deprecate.docstring`` submodule, for example through an API documentation crawler. The import must not fail
        on the missing ``griffe``, and ``RuntimeDocstrings`` must be absent rather than half-defined. The module is
        executed fresh from its spec, so the copy already imported by other tests is left alone.

        """
        monkeypatch.setitem(sys.modules, "griffe", None)
        spec = importlib.util.find_spec("deprecate.docstring.griffe_ext")
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)

        spec.loader.exec_module(module)

        assert module.__all__ == []
        assert not hasattr(module, "RuntimeDocstrings")
