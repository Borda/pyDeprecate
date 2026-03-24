"""Demo module showcasing pyDeprecate docstring injection styles for Sphinx."""

from deprecate import deprecated, deprecated_class


def new_add(x: int, y: int) -> int:
    """Add two integers together.

    :param x: First operand.
    :param y: Second operand.
    :returns: The sum of *x* and *y*.
    """
    return x + y


@deprecated(
    target=new_add,
    args_mapping={"verbose": None},
    deprecated_in="1.0",
    remove_in="2.0",
    update_docstring=True,
    docstring_style="rst",
)
def old_add_with_verbose(x: int, y: int, verbose: bool = False) -> int:
    """Add two integers, formerly with optional logging (legacy).

    The ``verbose`` argument has been removed; the function itself
    is also deprecated in favour of ``new_add``.

    :param x: First operand.
    :param y: Second operand.
    :param verbose: Deprecated — logging is no longer supported.
    :returns: The sum of *x* and *y*.
    """
    return x + y


class NewCalculator:
    """Modern calculator."""

    def __init__(self, precision: int = 2) -> None:
        """Initialize with decimal precision.

        :param precision: Number of decimal places to round to.
        """
        self.precision = precision

    def add(self, x: float, y: float) -> float:
        """Return the sum of two numbers rounded to *precision* places.

        :param x: First operand.
        :param y: Second operand.
        :returns: Rounded sum.
        """
        return round(x + y, self.precision)


@deprecated_class(
    target=NewCalculator, deprecated_in="1.0", remove_in="2.0", update_docstring=True, docstring_style="rst"
)
class OldCalculator:
    """Legacy calculator — use :class:`NewCalculator` instead."""

    def __init__(self, precision: int = 2) -> None:
        """Initialize OldCalculator.

        :param precision: Number of decimal places to round to.
        """
        self.precision = precision

    def add(self, x: float, y: float) -> float:
        """Return the sum of two numbers.

        :param x: First operand.
        :param y: Second operand.
        :returns: Rounded sum.
        """
        return round(x + y, self.precision)
