# API Reference

The examples below are rendered directly from the `demo` module included in
this documentation tree. Each function carries `@deprecated(update_docstring=True)`
so you can see exactly how the injected notice looks once rendered.

______________________________________________________________________

## Auto-detected style

These functions use `docstring_style="auto"`. When built by MkDocs the
notice is rendered as a `!!! warning` admonition; when built by Sphinx
it falls back to a `.. deprecated::` RST directive.

### No section headers

::: demo.old_rst_no_sections

### Google-style sections

::: demo.old_rst_google_sections

### NumPy-style sections

::: demo.old_rst_numpy_sections

______________________________________________________________________

## MkDocs / Markdown style

These functions use `docstring_style="mkdocs"`. The injected
`!!! warning` admonition renders as a highlighted warning box in
MkDocs Material.

### No section headers

::: demo.old_mkdocs_no_sections

### Google-style sections

::: demo.old_mkdocs_google_sections
