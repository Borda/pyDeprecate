"""Tests for deprecation documentation strings."""

from tests.collection_docstrings import (
    OldClass,
    OldClassPlain,
    old_function,
    old_function_plain,
    old_google_style_function,
    old_mkdocs_style_function,
    old_numpy_style_function,
)


class TestDeprecationDocstrings:
    """Tests for deprecation documentation strings."""

    def test_deprecated_func_docstring(self) -> None:
        """Test that deprecated functions have deprecation warning in their docstring."""
        assert old_function.__doc__ is not None
        assert ".. deprecated:: 0.1" in old_function.__doc__
        assert "Will be removed in 0.3." in old_function.__doc__
        assert "Use :func:`tests.collection_docstrings.new_function` instead." in old_function.__doc__

    def test_deprecated_func_docstring_plain(self) -> None:
        """Test that deprecated functions without docstrings do not have docstrings added."""
        assert old_function_plain.__doc__ is None

    def test_deprecated_class_docstring(self) -> None:
        """Test that deprecated classes have deprecation warning in their __init__ docstring."""
        assert OldClass.__init__.__doc__ is not None
        assert ".. deprecated:: 0.2" in OldClass.__init__.__doc__
        assert "Will be removed in 0.4." in OldClass.__init__.__doc__
        assert "Use :class:`tests.collection_docstrings.NewClass` instead." in OldClass.__init__.__doc__

    def test_deprecated_class_docstring_plain(self) -> None:
        """Test that deprecated classes without docstrings do not have docstrings added."""
        assert getattr(OldClassPlain.__init__, "__doc__") is None

    def test_google_docstring_inserts_before_args_section(self) -> None:
        """Deprecation notice should be injected before Google-style sections."""
        assert old_google_style_function.__doc__ is not None
        notice_idx = old_google_style_function.__doc__.index(".. deprecated:: 0.1")
        args_idx = old_google_style_function.__doc__.index("Args:")
        assert notice_idx < args_idx

    def test_numpy_docstring_inserts_before_parameters_section(self) -> None:
        """Deprecation notice should be injected before NumPy-style sections."""
        assert old_numpy_style_function.__doc__ is not None
        notice_idx = old_numpy_style_function.__doc__.index(".. deprecated:: 0.1")
        params_idx = old_numpy_style_function.__doc__.index("Parameters")
        assert notice_idx < params_idx

    def test_mkdocs_docstring_uses_admonition_format(self) -> None:
        """MkDocs style should emit Markdown admonition syntax."""
        assert old_mkdocs_style_function.__doc__ is not None
        assert '!!! warning "Deprecated in 0.1"' in old_mkdocs_style_function.__doc__
        assert "Use `tests.collection_docstrings.new_function` instead." in old_mkdocs_style_function.__doc__
        notice_idx = old_mkdocs_style_function.__doc__.index('!!! warning "Deprecated in 0.1"')
        args_idx = old_mkdocs_style_function.__doc__.index("Args:")
        assert notice_idx < args_idx
