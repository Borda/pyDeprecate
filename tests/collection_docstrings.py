"""Target and deprecated callables for testing update_docstring=True behaviour."""

from deprecate import deprecated


def new_function(a: int, b: str = "default") -> str:
    """A new function that is the target."""
    return f"{a} {b}"


class NewClass:
    """A new class."""

    def __init__(self, x: int) -> None:
        """Initialize NewClass."""
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
