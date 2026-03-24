"""Demo module showcasing pyDeprecate docstring injection styles.

This module provides example functions that demonstrate how
``@deprecated(update_docstring=True)`` renders deprecation notices for
different documentation stacks (Sphinx RST, MkDocs, Google/NumPy sections).
"""

from deprecate import deprecated


def new_add(x: int, y: int) -> int:
    """Add two integers together.

    Args:
        x: First operand.
        y: Second operand.

    Returns:
        The sum of *x* and *y*.
    """
    return x + y


# ---------------------------------------------------------------------------
# Auto-detect style (renders as MkDocs admonition when built with mkdocs)
# ---------------------------------------------------------------------------


@deprecated(
    target=new_add,
    deprecated_in="1.0",
    remove_in="2.0",
    update_docstring=True,
    docstring_style="auto",
)
def old_rst_no_sections(x: int, y: int) -> int:
    """Add two integers (legacy, no sections)."""
    return x + y


@deprecated(
    target=new_add,
    deprecated_in="1.0",
    remove_in="2.0",
    update_docstring=True,
    docstring_style="auto",
)
def old_rst_google_sections(x: int, y: int) -> int:
    """Add two integers (legacy, Google sections).

    Args:
        x: First operand.
        y: Second operand.

    Returns:
        The sum of *x* and *y*.
    """
    return x + y


@deprecated(
    target=new_add,
    deprecated_in="1.0",
    remove_in="2.0",
    update_docstring=True,
    docstring_style="auto",
)
def old_rst_numpy_sections(x: int, y: int) -> int:
    """Add two integers (legacy, NumPy sections).

    Parameters
    ----------
    x : int
        First operand.
    y : int
        Second operand.

    Returns
    -------
    result : int
        The sum of *x* and *y*.
    """
    return x + y


# ---------------------------------------------------------------------------
# MkDocs / Markdown style
# ---------------------------------------------------------------------------


@deprecated(
    target=new_add,
    deprecated_in="1.0",
    remove_in="2.0",
    update_docstring=True,
    docstring_style="mkdocs",
)
def old_mkdocs_no_sections(x: int, y: int) -> int:
    """Add two integers (legacy, MkDocs style, no sections)."""
    return x + y


@deprecated(
    target=new_add,
    deprecated_in="1.0",
    remove_in="2.0",
    update_docstring=True,
    docstring_style="mkdocs",
)
def old_mkdocs_google_sections(x: int, y: int) -> int:
    """Add two integers (legacy, MkDocs style, Google sections).

    Args:
        x: First operand.
        y: Second operand.

    Returns:
        The sum of *x* and *y*.
    """
    return x + y


# ---------------------------------------------------------------------------
# Combined: deprecated function + deprecated argument
# ---------------------------------------------------------------------------


@deprecated(
    target=new_add,
    args_mapping={"verbose": None},
    deprecated_in="1.0",
    remove_in="2.0",
    update_docstring=True,
    docstring_style="mkdocs",
)
def old_add_with_verbose(x: int, y: int, verbose: bool = False) -> int:
    """Add two integers, formerly with optional logging (legacy).

    The ``verbose`` argument has been removed; the function itself
    is also deprecated in favour of ``new_add``.

    Args:
        x: First operand.
        y: Second operand.
        verbose: Deprecated — logging is no longer supported.

    Returns:
        The sum of *x* and *y*.
    """
    return x + y


# ---------------------------------------------------------------------------
# Deprecated class
# ---------------------------------------------------------------------------


class NewCalculator:
    """Modern calculator."""

    def __init__(self, precision: int = 2) -> None:
        """Initialize with decimal precision.

        Args:
            precision: Number of decimal places to round to.
        """
        self.precision = precision

    def add(self, x: float, y: float) -> float:
        """Return the sum of two numbers rounded to *precision* places.

        Args:
            x: First operand.
            y: Second operand.

        Returns:
            Rounded sum.
        """
        return round(x + y, self.precision)


class OldCalculator:
    """Legacy calculator — use :class:`NewCalculator` instead."""

    @deprecated(
        target=NewCalculator,
        deprecated_in="1.0",
        remove_in="2.0",
        update_docstring=True,
        docstring_style="mkdocs",
    )
    def __init__(self, precision: int = 2) -> None:
        """Initialize OldCalculator.

        Args:
            precision: Number of decimal places to round to.
        """
        self.precision = precision

    def add(self, x: float, y: float) -> float:
        """Return the sum of two numbers.

        Args:
            x: First operand.
            y: Second operand.

        Returns:
            Rounded sum.
        """
        return round(x + y, self.precision)
