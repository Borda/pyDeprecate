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

______________________________________________________________________

## Combined: deprecated function + deprecated argument

The function below is deprecated *and* has one argument (`verbose`) that
has been removed. Both the inline argument annotation **and** the general
deprecation notice are injected into the docstring.

::: demo.old_add_with_verbose

______________________________________________________________________

## Deprecated class

`OldCalculator` is deprecated in favour of `NewCalculator`. The
deprecation notice is injected into `__init__`'s docstring.

::: demo.NewCalculator

::: demo.OldCalculator
