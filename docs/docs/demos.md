# Documentation Demos

pyDeprecate supports both major Python documentation stacks. Each demo shows the same deprecation
patterns rendered by a different engine so you can pick the one that matches your project.

<div class="grid cards" markdown>

- :simple-sphinx: **Sphinx**

  ______________________________________________________________________

  Injects a `.. deprecated::` RST directive rendered by `sphinx.ext.autodoc` + Napoleon.
  Supports Google-style, NumPy-style, and Sphinx field-list docstrings.

  [:octicons-arrow-right-24: View Sphinx demo](../sphinx/)

- :simple-materialformkdocs: **MkDocs Material**

  ______________________________________________________________________

  Injects a `!!! warning` admonition rendered by `mkdocstrings`. A built-in Griffe
  extension bridges the static AST parser with the runtime-modified `__doc__`.

  [:octicons-arrow-right-24: View MkDocs demo](../mkdocs/)

</div>
