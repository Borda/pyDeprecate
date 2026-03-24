"""Demo module showcasing pyDeprecate docstring injection styles for Sphinx.

This module provides example functions that demonstrate how
``@deprecated(update_docstring=True)`` renders deprecation notices in
Sphinx-built documentation (RST ``.. deprecated::`` directive).
"""

from deprecate import deprecated


def new_add(x: int, y: int) -> int:
    """Add two integers together.

    :param x: First operand.
    :param y: Second operand.
    :returns: The sum of *x* and *y*.
    """
    return x + y


# ---------------------------------------------------------------------------
# Auto-detected style (resolves to ``.. deprecated::`` under Sphinx)
# ---------------------------------------------------------------------------


@deprecated(
    target=new_add,
    deprecated_in="1.0",
    remove_in="2.0",
    update_docstring=True,
    docstring_style="auto",
)
def old_auto_no_sections(x: int, y: int) -> int:
    """Add two integers (legacy, no sections)."""
    return x + y


@deprecated(
    target=new_add,
    deprecated_in="1.0",
    remove_in="2.0",
    update_docstring=True,
    docstring_style="auto",
)
def old_auto_google_sections(x: int, y: int) -> int:
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
def old_auto_numpy_sections(x: int, y: int) -> int:
    """Add two integers (legacy, NumPy sections).

    Parameters
    ----------
    x : int
        First operand.
    y : int
        Second operand.

    Returns:
    -------
    int
        The sum of *x* and *y*.
    """
    return x + y


# ---------------------------------------------------------------------------
# Explicit RST style
# ---------------------------------------------------------------------------


@deprecated(
    target=new_add,
    deprecated_in="1.0",
    remove_in="2.0",
    update_docstring=True,
    docstring_style="rst",
)
def old_rst_no_sections(x: int, y: int) -> int:
    """Add two integers (legacy, RST style, no sections)."""
    return x + y


@deprecated(
    target=new_add,
    deprecated_in="1.0",
    remove_in="2.0",
    update_docstring=True,
    docstring_style="rst",
)
def old_rst_google_sections(x: int, y: int) -> int:
    """Add two integers (legacy, RST style, Google sections).

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
    docstring_style="rst",
)
def old_rst_sphinx_sections(x: int, y: int) -> int:
    """Add two integers (legacy, RST style, Sphinx field list).

    :param x: First operand.
    :param y: Second operand.
    :returns: The sum of *x* and *y*.
    """
    return x + y
