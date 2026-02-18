"""Tests for deprecation documentation strings."""

from tests.collection_docstrings import (
    OldClass,
    OldClassPlain,
    old_function,
    old_function_plain,
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
