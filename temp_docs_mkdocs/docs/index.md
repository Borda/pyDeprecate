# pyDeprecate – Docstring Style Demo

This site demonstrates how `@deprecated(update_docstring=True)` injects
deprecation notices for different documentation stacks.

## Available styles

| `docstring_style`     | Output format                   | Best for         |
| --------------------- | ------------------------------- | ---------------- |
| `rst` *(default)*     | `.. deprecated::` RST directive | Sphinx / autodoc |
| `mkdocs` / `markdown` | `!!! warning` admonition        | MkDocs Material  |

## Injection strategy

Regardless of the chosen style, the deprecation notice is inserted **before**
the first Google-style (`Args:`) or NumPy-style (`Parameters` + underline)
section header. This keeps parameter tables intact in the rendered output.

When no known section headers are found, the notice is appended to the end of
the docstring.

## Examples

See the [API reference](api.md) for live rendered examples of each combination.
