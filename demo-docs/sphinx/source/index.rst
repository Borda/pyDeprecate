pyDeprecate
===========

**pyDeprecate** is a zero-dependency Python library that turns deprecation from a chore into
a one-liner.  Decorate a function, method, or class with ``@deprecated(...)`` and the library
handles everything else: runtime ``FutureWarning`` emission, transparent call forwarding to the
replacement, and — crucially — automatic documentation.

The problem it solves
---------------------

When you deprecate an API, you typically need to:

1. Emit a runtime warning so callers know at call time.
2. Update the docstring so the rendered docs show a deprecation notice.
3. Keep the parameter tables intact so the docs remain readable.

Steps 2 and 3 are easy to forget and tedious to maintain.  pyDeprecate automates both.

Automatic docstring injection
-----------------------------

Pass ``update_docstring=True`` and pyDeprecate rewrites the function's ``__doc__`` at
decoration time — before Sphinx ever sees it.

.. code-block:: python

   from deprecate import deprecated

   def new_add(x: int, y: int) -> int: ...

   @deprecated(target=new_add, deprecated_in="1.0", remove_in="2.0", update_docstring=True)
   def old_add(x: int, y: int) -> int:
       """Add two integers.

       :param x: First operand.
       :param y: Second operand.
       :returns: The sum of *x* and *y*.
       """
       return x + y

The injected ``__doc__`` becomes::

   Add two integers.

   .. deprecated:: 1.0
      Will be removed in 2.0.
      Use :func:`...new_add` instead.

   :param x: First operand.
   ...

The notice is inserted **before** the first field list / ``Args:`` section so parameter
tables render correctly.  Developers writing or reviewing the deprecation do not need to touch
the docstring at all — the notice appears automatically in the rendered output.

Docstring styles
----------------

+--------------------+------------------------------+----------------------------------+
| ``docstring_style``| Output                       | Use when                         |
+====================+==============================+==================================+
| ``"auto"``         | Detects engine at import time| Most projects — just works       |
+--------------------+------------------------------+----------------------------------+
| ``"rst"``          | ``.. deprecated::`` directive| Sphinx / autodoc                 |
+--------------------+------------------------------+----------------------------------+
| ``"mkdocs"``       | ``!!! warning`` admonition   | MkDocs Material                  |
+--------------------+------------------------------+----------------------------------+

``"auto"`` checks ``sys.modules`` and ``sys.argv`` to detect whether MkDocs or Sphinx is
driving the build, so the same decorator works in both stacks without changes.

Live examples
-------------

The :doc:`api` page shows two real cases rendered by this Sphinx build:

- **Deprecated function with a removed argument** — inline per-argument note *and* a general
  deprecation block, both injected automatically.
- **Deprecated class** — notice injected into the class docstring via ``deprecated_class``.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   api
