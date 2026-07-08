"""Unit tests for private helpers in deprecate.docstring.inject."""

import importlib
import importlib.util
import types
import warnings
from typing import cast

import pytest

from deprecate._types import DeprecationConfig, TargetMode, _DeprecatedCallable
from deprecate.docstring.inject import (
    _annotate_google_style_arg,
    _annotate_sphinx_style_arg,
    _build_arg_deprecation_note,
    _find_google_arg_line,
    _find_google_args_section,
    _get_google_arg_indents,
    _has_deprecation_block,
    _update_docstring_with_deprecation,
)

# Optional dependency: ``griffe`` ships with ``mkdocstrings[python]``. Guard at module level so collection never
# fails; the behavioural tests below are gated by ``_skipif_griffe_missing``.
_GRIFFE_AVAILABLE = importlib.util.find_spec("griffe") is not None
if _GRIFFE_AVAILABLE:
    import griffe

    from deprecate.docstring.griffe_ext import RuntimeDocstrings

_skipif_griffe_missing = pytest.mark.skipif(not _GRIFFE_AVAILABLE, reason="griffe not installed")


def _load_docstrings_module() -> "griffe.Module":
    """Load ``tests.collection_docstrings`` into a fresh Griffe module graph.

    A new :class:`~griffe.GriffeLoader` is created per call so that each test operates on an independent object
    graph — :meth:`RuntimeDocstrings.on_module` mutates docstrings in place, so a shared graph would leak state.

    """
    return griffe.GriffeLoader().load("tests.collection_docstrings")


class TestBuildArgDeprecationNote:
    """Tests for _build_arg_deprecation_note — builds the inline arg-deprecation string."""

    def test_removed_arg_with_versions(self) -> None:
        """Removed arg (new_arg=None) uses 'no longer used' reason."""
        note = _build_arg_deprecation_note(None, "1.8", "1.9")
        assert note == "Deprecated since v1.8 — no longer used. Will be removed in v1.9."

    def test_renamed_arg_with_versions(self) -> None:
        """Renamed arg includes the new argument name in the reason."""
        note = _build_arg_deprecation_note("new_arg", "1.8", "1.9")
        assert note == "Deprecated since v1.8 — use `new_arg` instead. Will be removed in v1.9."

    def test_no_deprecated_in(self) -> None:
        """When deprecated_in is empty, 'since v...' is omitted."""
        note = _build_arg_deprecation_note(None, "", "2.0")
        assert "since" not in note
        assert "Will be removed in v2.0." in note

    def test_no_remove_in(self) -> None:
        """When remove_in is empty, 'Will be removed...' is omitted."""
        note = _build_arg_deprecation_note(None, "1.8", "")
        assert "Will be removed" not in note
        assert "Deprecated since v1.8" in note


class TestHasDeprecationBlock:
    """Tests for _has_deprecation_block — detects already-injected notices."""

    def test_empty_block_lines_returns_false(self) -> None:
        """Empty block_lines triggers fast-return False regardless of doc_lines content."""
        assert not _has_deprecation_block(["some content", "more content"], [])


class TestFindGoogleArgLine:
    """Tests for _find_google_arg_line — locates an arg entry inside a Google-style Args section."""

    def test_finds_exact_match(self) -> None:
        """Returns the line index of the exact arg name."""
        lines = [
            "    Args:",
            "        lr (float): Learning rate.",
            "        batch_size (int): Batch size.",
        ]
        section_start, section_indent = _find_google_args_section(lines)
        arg_indent, _ = _get_google_arg_indents(lines, section_start, section_indent)
        idx = _find_google_arg_line(lines, section_start, section_indent, arg_indent, "lr")
        assert idx == 1

    def test_prefix_collision_not_matched(self) -> None:
        """'lr' must not match 'lr_decay' — the boundary character check must reject the prefix."""
        lines = [
            "    Args:",
            "        lr (float): Learning rate.",
            "        lr_decay (float): Decay factor.",
        ]
        section_start, section_indent = _find_google_args_section(lines)
        arg_indent, _ = _get_google_arg_indents(lines, section_start, section_indent)
        idx = _find_google_arg_line(lines, section_start, section_indent, arg_indent, "lr")
        # Must match line 1 ("lr"), not line 2 ("lr_decay")
        assert idx == 1
        idx_decay = _find_google_arg_line(lines, section_start, section_indent, arg_indent, "lr_decay")
        assert idx_decay == 2

    def test_returns_minus_one_when_not_found(self) -> None:
        """Returns -1 when the arg name is absent from the section."""
        lines = [
            "    Args:",
            "        alpha (int): First.",
        ]
        section_start, section_indent = _find_google_args_section(lines)
        arg_indent, _ = _get_google_arg_indents(lines, section_start, section_indent)
        idx = _find_google_arg_line(lines, section_start, section_indent, arg_indent, "beta")
        assert idx == -1


class TestAnnotateGoogleStyleArg:
    """Tests for _annotate_google_style_arg — injects a note into a Google-style Args: section."""

    def test_found_and_inserts_note(self) -> None:
        """The note is inserted on a continuation-indented line after the matched arg."""
        lines = [
            "Summary.",
            "",
            "    Args:",
            "        my_arg (int): Description.",
            "    ",
        ]
        new_lines, found = _annotate_google_style_arg(lines, "my_arg", "Deprecated note.")
        assert found
        assert "            Deprecated note." in new_lines

    def test_not_found_returns_unchanged(self) -> None:
        """When the arg is absent the original lines and found=False are returned."""
        lines = ["    Args:", "        other (int): desc.", "    "]
        new_lines, found = _annotate_google_style_arg(lines, "missing", "note")
        assert not found
        assert new_lines == lines

    def test_no_args_section_returns_unchanged(self) -> None:
        """When there is no Args: header the original lines are returned unchanged."""
        lines = ["Summary.", "", "No args here."]
        new_lines, found = _annotate_google_style_arg(lines, "x", "note")
        assert not found
        assert new_lines == lines

    def test_note_placed_after_continuation_lines(self) -> None:
        """The note is appended after existing continuation lines, not before them."""
        lines = [
            "    Args:",
            "        my_arg (int): First line of description.",
            "            Continuation line.",
            "    ",
        ]
        new_lines, found = _annotate_google_style_arg(lines, "my_arg", "Deprecated.")
        assert found
        cont_idx = new_lines.index("            Continuation line.")
        note_idx = new_lines.index("            Deprecated.")
        assert note_idx > cont_idx

    def test_multiple_args_only_target_annotated(self) -> None:
        """Only the matched argument entry receives the note; others are left untouched."""
        lines = [
            "    Args:",
            "        alpha (int): First arg.",
            "        beta (str): Second arg.",
            "    ",
        ]
        new_lines, found = _annotate_google_style_arg(lines, "beta", "Note for beta.")
        assert found
        assert any("Note for beta." in ln for ln in new_lines)
        assert not any("Note for beta." in ln for ln in new_lines if "alpha" in ln)

    def test_idempotent_when_note_already_present(self) -> None:
        """Calling annotate twice does not insert the note a second time."""
        lines = [
            "    Args:",
            "        my_arg (int): Description.",
            "    ",
        ]
        lines, _ = _annotate_google_style_arg(lines, "my_arg", "Deprecated note.")
        lines, found = _annotate_google_style_arg(lines, "my_arg", "Deprecated note.")
        assert found
        assert sum("Deprecated note." in ln for ln in lines) == 1


class TestAnnotateSphinxStyleArg:
    """Tests for _annotate_sphinx_style_arg — injects a note under a Sphinx :param: field."""

    def test_found_and_inserts_note(self) -> None:
        """The note is inserted as an indented continuation line after the matched :param."""
        lines = [
            "Summary.",
            "",
            ":param my_arg: Description.",
            ":returns: Result.",
        ]
        new_lines, found = _annotate_sphinx_style_arg(lines, "my_arg", "Deprecated note.")
        assert found
        assert "    Deprecated note." in new_lines

    def test_not_found_returns_unchanged(self) -> None:
        """When the param is absent the original lines and found=False are returned."""
        lines = [":param other: desc.", ":returns: val."]
        new_lines, found = _annotate_sphinx_style_arg(lines, "missing", "note")
        assert not found
        assert new_lines == lines

    def test_typed_param_form(self) -> None:
        """The ``:param SomeType arg_name:`` form is also matched."""
        lines = [":param int my_arg: Description.", ":returns: Result."]
        new_lines, found = _annotate_sphinx_style_arg(lines, "my_arg", "Note.")
        assert found
        assert any("    Note." in ln for ln in new_lines)

    def test_note_placed_after_multiline_param(self) -> None:
        """The note is appended after existing indented continuation text."""
        lines = [
            ":param my_arg: First line.",
            "    Continued here.",
            ":returns: val.",
        ]
        new_lines, found = _annotate_sphinx_style_arg(lines, "my_arg", "Note.")
        assert found
        cont_idx = new_lines.index("    Continued here.")
        note_idx = new_lines.index("    Note.")
        assert note_idx > cont_idx

    def test_idempotent_when_note_already_present(self) -> None:
        """Calling annotate twice does not insert the note a second time."""
        lines = [":param my_arg: Description.", ":returns: val."]
        lines, _ = _annotate_sphinx_style_arg(lines, "my_arg", "Deprecated note.")
        lines, found = _annotate_sphinx_style_arg(lines, "my_arg", "Deprecated note.")
        assert found
        assert sum("Deprecated note." in ln for ln in lines) == 1


class TestUpdateDocstringIdempotent:
    """_update_docstring_with_deprecation called twice must not duplicate the inline note."""

    def test_google_style_double_call_deduplicates(self) -> None:
        """Two consecutive calls annotate the arg exactly once (Google-style docstring)."""

        def my_fn(old: str = "") -> str:
            """Do something.

            Args:
                old: Old argument.

            """
            return old

        config = DeprecationConfig(
            deprecated_in="1.0", remove_in="2.0", target=TargetMode.ARGS_REMAP, args_mapping={"old": None}
        )
        cast(_DeprecatedCallable, my_fn).__deprecated__ = config
        _update_docstring_with_deprecation(my_fn)
        _update_docstring_with_deprecation(my_fn)
        assert my_fn.__doc__ is not None
        assert my_fn.__doc__.count("Deprecated since v1.0") == 1

    def test_sphinx_style_double_call_deduplicates(self) -> None:
        """Two consecutive calls annotate the param exactly once (Sphinx-style docstring)."""

        def my_fn(old: str = "") -> str:
            """Do something.

            :param old: Old argument.
            :returns: Result.

            """
            return old

        config = DeprecationConfig(
            deprecated_in="1.0", remove_in="2.0", target=TargetMode.ARGS_REMAP, args_mapping={"old": None}
        )
        cast(_DeprecatedCallable, my_fn).__deprecated__ = config
        _update_docstring_with_deprecation(my_fn)
        _update_docstring_with_deprecation(my_fn)
        assert my_fn.__doc__ is not None
        assert my_fn.__doc__.count("Deprecated since v1.0") == 1


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
        """A callable without ``__deprecated__`` keeps its statically-parsed Griffe docstring.

        ``new_function`` is the plain replacement target — it has no ``__deprecated__`` attribute, so
        ``_replace_docstring`` must return early and leave the original source docstring intact.

        """
        mod = _load_docstrings_module()
        runtime_new = importlib.import_module("tests.collection_docstrings").new_function

        RuntimeDocstrings._replace_docstring(mod["new_function"], runtime_new)

        assert mod["new_function"].docstring.value == "A new function that is the target."

    def test_replace_docstring_skips_when_runtime_doc_is_empty(self) -> None:
        """A deprecated function whose runtime ``__doc__`` is ``None`` is left unchanged.

        ``old_function_plain`` has ``update_docstring=True`` but no source docstring, so its runtime ``__doc__``
        stays ``None``.  ``_replace_docstring`` must not raise and must not fabricate a docstring.

        """
        mod = _load_docstrings_module()
        runtime_plain = importlib.import_module("tests.collection_docstrings").old_function_plain

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
        runtime_old = importlib.import_module("tests.collection_docstrings").old_function

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
