"""Sample module for testing deprecation documentation.

This module contains example functions and classes to demonstrate
deprecation warnings in generated documentation.
"""

from deprecate import deprecated


def new_function(a: int, b: str = "default") -> str:
    """A new function that is the target."""
    return f"{a} {b}"


class NewClass:
    """A new class."""

    def __init__(self, x: int) -> None:
        self.x = x


@deprecated(target=new_function, deprecated_in="0.1", remove_in="0.3", update_docstring=True)
def old_function(a: int, b: str = "old") -> str:
    """An old function that is deprecated."""
    return f"old {a} {b}"


@deprecated(target=new_function, deprecated_in="0.1", remove_in="0.3", update_docstring=True)
def old_function_plain(a: int, b: str = "old") -> str:
    return f"old {a} {b}"


class OldClass:
    """An old class that is deprecated."""

    @deprecated(target=NewClass, deprecated_in="0.2", remove_in="0.4", update_docstring=True)
    def __init__(self, x: int) -> None:
        """Initialize the old class."""
        self.x = x


class OldClassPlain:
    @deprecated(target=NewClass, deprecated_in="0.2", remove_in="0.4", update_docstring=True)
    def __init__(self, x: int) -> None:
        self.x = x


def test_deprecated_func_docstring() -> None:
    """Test that deprecated functions have deprecation warning in their docstring."""
    assert old_function.__doc__ == (
        "An old function that is deprecated."
        "\n\n.. deprecated:: 0.1 Will be removed in 0.3."
        " Use tests.test_docs.new_function instead.\n"
    )


def test_deprecated_func_docstring_plain() -> None:
    """Test that deprecated functions without docstrings do not have docstrings added."""
    assert old_function_plain.__doc__ is None


def test_deprecated_class_docstring() -> None:
    """Test that deprecated classes have deprecation warning in their __init__ docstring."""
    assert OldClass.__init__.__doc__ == (
        "Initialize the old class."
        "\n\n.. deprecated:: 0.2 Will be removed in 0.4."
        " Use tests.test_docs.NewClass instead.\n"
    )


def test_deprecated_class_docstring_plain() -> None:
    """Test that deprecated classes without docstrings do not have docstrings added."""
    assert getattr(OldClassPlain.__init__, "__doc__") is None


def test_deprecated_attribute_set_at_decoration_time() -> None:
    """Test that __deprecated__ attribute is set at decoration time, not call time.

    This verifies that the __deprecated__ attribute is available immediately
    after the decorator is applied, without needing to call the function first.
    """

    # Define a new function to ensure it's never been called
    @deprecated(target=new_function, deprecated_in="1.0", remove_in="2.0")
    def never_called_function(a: int) -> int:
        """A function that will never be called in this test."""
        return a

    # Verify __deprecated__ is set WITHOUT calling the function
    assert hasattr(never_called_function, "__deprecated__")
    assert never_called_function.__deprecated__ == {
        "deprecated_in": "1.0",
        "remove_in": "2.0",
        "target": new_function,
        "args_mapping": None,
    }
