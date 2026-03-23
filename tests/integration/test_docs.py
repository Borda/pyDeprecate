"""Tests for deprecation documentation strings."""

from tests.collection_docstrings import (
    OldClass,
    OldClassPlain,
    args_not_in_docstring,
    callable_target_with_args_mapping,
    google_args_multiline,
    google_args_removed,
    google_args_renamed,
    google_arguments_header,
    google_multi_args_all_found,
    google_partial_annotation,
    no_target_with_args_mapping,
    old_function,
    old_function_plain,
    sphinx_arg_not_in_docstring,
    sphinx_args_multiline,
    sphinx_args_removed,
)


class TestDeprecationDocstrings:
    """Tests for deprecation documentation strings."""

    def test_deprecated_func_docstring(self) -> None:
        """Test that deprecated functions have deprecation warning in their docstring."""
        assert old_function.__doc__ == (
            "An old function that is deprecated.\n\n"
            ".. deprecated:: 0.1\n"
            "   Will be removed in 0.3.\n"
            "   Use :func:`tests.collection_docstrings.new_function` instead.\n"
        )

    def test_deprecated_func_docstring_plain(self) -> None:
        """Test that deprecated functions without docstrings do not have docstrings added."""
        assert old_function_plain.__doc__ is None

    def test_deprecated_class_docstring(self) -> None:
        """Test that deprecated classes have deprecation warning in their __init__ docstring."""
        assert OldClass.__init__.__doc__ == (
            "Initialize the old class.\n\n"
            ".. deprecated:: 0.2\n"
            "   Will be removed in 0.4.\n"
            "   Use :class:`tests.collection_docstrings.NewClass` instead.\n"
        )

    def test_deprecated_class_docstring_plain(self) -> None:
        """Test that deprecated classes without docstrings do not have docstrings added."""
        assert getattr(OldClassPlain.__init__, "__doc__") is None


class TestArgsDocstringAnnotation:
    """Tests for inline arg deprecation annotations in docstrings."""

    def test_google_args_removed_inlines_note(self) -> None:
        """Removed arg gets an inline note in the Google-style Args section."""
        doc = google_args_removed.__doc__
        assert doc is not None
        assert "Deprecated since v1.8 — no longer used. Will be removed in v1.9." in doc
        # The note should appear after the train_config entry
        tc_pos = doc.index("train_config")
        note_pos = doc.index("Deprecated since v1.8")
        assert note_pos > tc_pos

    def test_google_args_removed_no_general_notice(self) -> None:
        """When all args are found inline, no general ``.. deprecated::`` notice is appended."""
        doc = google_args_removed.__doc__
        assert doc is not None
        assert ".. deprecated::" not in doc

    def test_google_args_renamed_inlines_note(self) -> None:
        """Renamed arg gets an inline note referencing the new arg name."""
        doc = google_args_renamed.__doc__
        assert doc is not None
        assert "Deprecated since v1.8 — use `config` instead. Will be removed in v1.9." in doc

    def test_google_args_renamed_no_general_notice(self) -> None:
        """When all args are found inline, no general ``.. deprecated::`` notice is appended."""
        doc = google_args_renamed.__doc__
        assert doc is not None
        assert ".. deprecated::" not in doc

    def test_sphinx_args_removed_inlines_note(self) -> None:
        """Removed arg gets an inline note beneath its ``:param`` field."""
        doc = sphinx_args_removed.__doc__
        assert doc is not None
        assert "Deprecated since v1.8 — no longer used. Will be removed in v1.9." in doc
        param_pos = doc.index(":param train_config:")
        note_pos = doc.index("Deprecated since v1.8")
        assert note_pos > param_pos

    def test_sphinx_args_removed_no_general_notice(self) -> None:
        """When all args are found inline, no general ``.. deprecated::`` notice is appended."""
        doc = sphinx_args_removed.__doc__
        assert doc is not None
        assert ".. deprecated::" not in doc

    def test_fallback_for_arg_not_in_docstring(self) -> None:
        """Arg not found in docstring triggers the general ``.. deprecated::`` fallback notice."""
        doc = args_not_in_docstring.__doc__
        assert doc is not None
        assert ".. deprecated:: 1.8" in doc
        assert "Will be removed in 1.9." in doc

    def test_multi_args_all_found_inline(self) -> None:
        """Both deprecated args annotated inline; no general notice appended."""
        doc = google_multi_args_all_found.__doc__
        assert doc is not None
        assert "Deprecated since v1.8 — use `new_a` instead. Will be removed in v1.9." in doc
        assert "Deprecated since v1.8 — no longer used. Will be removed in v1.9." in doc
        assert ".. deprecated::" not in doc
        # Each note appears after its own arg entry
        old_a_pos = doc.index("old_a")
        old_b_pos = doc.index("old_b")
        new_a_note_pos = doc.index("use `new_a`")
        removed_note_pos = doc.index("no longer used")
        assert new_a_note_pos > old_a_pos
        assert removed_note_pos > old_b_pos

    def test_partial_annotation_mixed_state(self) -> None:
        """First arg found inline; second arg missing triggers the general notice on top."""
        doc = google_partial_annotation.__doc__
        assert doc is not None
        # The found arg (old_a) gets an inline note
        assert "Deprecated since v1.8 — use `new_a` instead. Will be removed in v1.9." in doc
        # The missing arg (missing_b) triggers the general .. deprecated:: block
        assert ".. deprecated:: 1.8" in doc

    def test_arguments_header_variant(self) -> None:
        """``Arguments:`` section header is handled identically to ``Args:``."""
        doc = google_arguments_header.__doc__
        assert doc is not None
        assert "Deprecated since v1.8 — no longer used. Will be removed in v1.9." in doc
        assert ".. deprecated::" not in doc

    def test_sphinx_fallback_for_arg_not_in_docstring(self) -> None:
        """Sphinx-style: arg absent from ``:param`` fields falls back to general notice."""
        doc = sphinx_arg_not_in_docstring.__doc__
        assert doc is not None
        assert ".. deprecated:: 1.8" in doc
        assert "Will be removed in 1.9." in doc

    def test_callable_target_with_args_mapping_has_inline_note(self) -> None:
        """Callable target + args_mapping: deprecated arg gets inline annotation."""
        doc = callable_target_with_args_mapping.__doc__
        assert doc is not None
        assert "Deprecated since v1.8 — no longer used. Will be removed in v1.9." in doc

    def test_callable_target_with_args_mapping_keeps_general_notice(self) -> None:
        """Callable target + args_mapping: general ``.. deprecated::`` block is still present."""
        doc = callable_target_with_args_mapping.__doc__
        assert doc is not None
        assert ".. deprecated:: 1.8" in doc
        assert "Will be removed in 1.9." in doc
        assert "Use :func:" in doc

    def test_no_target_with_args_mapping_has_inline_note(self) -> None:
        """target=None + args_mapping: deprecated arg gets inline annotation."""
        doc = no_target_with_args_mapping.__doc__
        assert doc is not None
        assert "Deprecated since v1.8 — no longer used. Will be removed in v1.9." in doc

    def test_no_target_with_args_mapping_keeps_general_notice(self) -> None:
        """target=None + args_mapping: general ``.. deprecated::`` block is still present."""
        doc = no_target_with_args_mapping.__doc__
        assert doc is not None
        assert ".. deprecated:: 1.8" in doc
        assert "Will be removed in 1.9." in doc

    def test_google_args_multiline_inlines_note_after_continuation(self) -> None:
        """Deprecated arg with multiline description gets the note after the last continuation line."""
        doc = google_args_multiline.__doc__
        assert doc is not None
        assert "Deprecated since v1.8 — no longer used. Will be removed in v1.9." in doc
        # Note must appear after the last continuation line of the train_config entry
        last_continuation_pos = doc.index("Ignored when")
        note_pos = doc.index("Deprecated since v1.8")
        assert note_pos > last_continuation_pos
        assert ".. deprecated::" not in doc

    def test_sphinx_args_multiline_inlines_note_after_continuation(self) -> None:
        """Sphinx deprecated param with multiline description gets the note after the last continuation line."""
        doc = sphinx_args_multiline.__doc__
        assert doc is not None
        assert "Deprecated since v1.8 — no longer used. Will be removed in v1.9." in doc
        # Note must appear after the last continuation line of the train_config param
        last_continuation_pos = doc.index("Ignored when")
        note_pos = doc.index("Deprecated since v1.8")
        assert note_pos > last_continuation_pos
        assert ".. deprecated::" not in doc
