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
# RST / Sphinx style (default)
# ---------------------------------------------------------------------------


@deprecated(
    target=new_add,
    deprecated_in="1.0",
    remove_in="2.0",
    update_docstring=True,
)
def old_rst_no_sections(x: int, y: int) -> int:
    """Add two integers (legacy, no sections).

    This is the default RST style — a ``.. deprecated::`` directive is
    appended to the end of the docstring when there are no Google/NumPy
    section headers.
    """
    return x + y


@deprecated(
    target=new_add,
    deprecated_in="1.0",
    remove_in="2.0",
    update_docstring=True,
)
def old_rst_google_sections(x: int, y: int) -> int:
    """Add two integers (legacy, Google sections).

    The deprecation notice is injected **before** the ``Args:`` section
    so that documentation parsers can still read the parameters.

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
)
def old_rst_numpy_sections(x: int, y: int) -> int:
    """Add two integers (legacy, NumPy sections).

    The deprecation notice is injected **before** the ``Parameters``
    section underline so that NumPy-style parsers keep working.

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
    """Add two integers (legacy, MkDocs style, no sections).

    The ``!!! warning`` admonition is appended to the end of the
    docstring when there are no Google/NumPy section headers.
    """
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

    The ``!!! warning`` admonition is injected **before** the ``Args:``
    section so that the parameter table renders correctly.

    Args:
        x: First operand.
        y: Second operand.

    Returns:
        The sum of *x* and *y*.
    """
    return x + y
