"""Tests for deprecation documentation strings."""

from tests.collection_docstrings import (
    OldClass,
    OldClassPlain,
    args_not_in_docstring,
    google_args_removed,
    google_args_renamed,
    old_function,
    old_function_plain,
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
