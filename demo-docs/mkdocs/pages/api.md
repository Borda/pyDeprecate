# API Reference

The examples below are rendered directly from the `demo` module included in
this documentation tree. Each deprecated item carries `@deprecated(update_docstring=True)`
so you can see exactly how the injected notice looks once rendered.

______________________________________________________________________

## Deprecated function with deprecated argument

`old_add_with_verbose` is deprecated *and* has one argument (`verbose`) that
has been removed. Both the inline argument annotation **and** the general
deprecation notice are injected into the docstring.

::: demo.old_add_with_verbose

______________________________________________________________________

## Deprecated class

`OldCalculator` is deprecated in favour of `NewCalculator`. The
deprecation notice is injected into `__init__`'s docstring.

::: demo.OldCalculator
