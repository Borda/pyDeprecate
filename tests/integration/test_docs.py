"""Tests for deprecation documentation strings."""

import inspect
from typing import Optional

from tests.collection_docstrings import (
    OldClass,
    OldClassPlain,
    args_not_in_docstring,
    callable_target_with_args_mapping,
    google_args_multiline,
    google_args_removed,
    google_args_renamed,
    google_arguments_header,
    google_multi_args_all_found,
    google_partial_annotation,
    no_target_with_args_mapping,
    old_function,
    old_function_plain,
    old_google_no_sections_function,
    old_google_style_function,
    old_mkdocs_style_function,
    old_no_remove_version_function,
    old_no_target_function,
    old_numpy_no_sections_function,
    old_numpy_style_function,
    sphinx_arg_not_in_docstring,
    sphinx_args_multiline,
    sphinx_args_removed,
)


def _normalize_doc(doc: Optional[str]) -> Optional[str]:
    """Normalise docstring indentation for cross-version comparison.

    Python 3.13+ strips common leading indent from ``__doc__`` at compile time;
    earlier versions preserve it.  ``inspect.cleandoc`` alone cannot normalise a
    docstring that already contains a ``.. deprecated::`` directive at column 0,
    because that zero-indent line sets the minimum and prevents any stripping.
    This helper splits on the notice separator, cleandocs the body, then
    re-attaches the notice so both halves are handled correctly.
    """
    if doc is None:
        return None
    _notice = "\n\n.. deprecated::"
    if _notice in doc:
        body, notice = doc.split(_notice, 1)
        return inspect.cleandoc(body) + _notice + notice
    return inspect.cleandoc(doc)


class TestDeprecationDocstrings:
    """Tests for deprecation documentation strings."""

    def test_deprecated_func_docstring(self) -> None:
        """Deprecated function docstring gets a ``.. deprecated::`` block appended."""
        expected = """An old function that is deprecated.

.. deprecated:: 0.1
   Will be removed in 0.3.
   Use :func:`tests.collection_docstrings.new_function` instead.
"""
        assert _normalize_doc(old_function.__doc__) == expected

    def test_deprecated_class_docstring(self) -> None:
        """Deprecated __init__ gets a ``.. deprecated::`` block appended."""
        expected = """Initialize the old class.

.. deprecated:: 0.2
   Will be removed in 0.4.
   Use :class:`tests.collection_docstrings.NewClass` instead.
"""
        assert _normalize_doc(OldClass.__init__.__doc__) == expected

    def test_deprecated_func_docstring_plain(self) -> None:
        """Function without docstring is left with ``__doc__ = None``."""
        assert old_function_plain.__doc__ is None

    def test_deprecated_class_docstring_plain(self) -> None:
        """__init__ without docstring is left with ``__doc__ = None``."""
        assert getattr(OldClassPlain.__init__, "__doc__") is None

    def test_google_docstring_inserts_before_args_section(self) -> None:
        """Deprecation notice should be injected before Google-style sections."""
        assert old_google_style_function.__doc__ is not None
        assert "Args:" in old_google_style_function.__doc__
        notice_idx = old_google_style_function.__doc__.index(".. deprecated:: 0.1")
        args_idx = old_google_style_function.__doc__.index("Args:")
        assert notice_idx < args_idx

    def test_google_docstring_without_sections_appends_notice_to_end(self) -> None:
        """Without sections, deprecation notice is appended to the docstring tail."""
        assert old_google_no_sections_function.__doc__ is not None
        summary = "Old Google-style function without explicit sections."
        notice_idx = old_google_no_sections_function.__doc__.index(".. deprecated:: 0.1")
        assert notice_idx > old_google_no_sections_function.__doc__.index(summary)

    def test_numpy_docstring_inserts_before_parameters_section(self) -> None:
        """Deprecation notice should be injected before NumPy-style sections."""
        assert old_numpy_style_function.__doc__ is not None
        assert "Parameters" in old_numpy_style_function.__doc__
        notice_idx = old_numpy_style_function.__doc__.index(".. deprecated:: 0.1")
        params_idx = old_numpy_style_function.__doc__.index("Parameters")
        assert notice_idx < params_idx

    def test_numpy_docstring_without_sections_appends_notice_to_end(self) -> None:
        """Without NumPy headers, deprecation notice is appended to the docstring tail."""
        assert old_numpy_no_sections_function.__doc__ is not None
        summary = "Old NumPy-style function without explicit sections."
        notice_idx = old_numpy_no_sections_function.__doc__.index(".. deprecated:: 0.1")
        assert notice_idx > old_numpy_no_sections_function.__doc__.index(summary)

    def test_mkdocs_docstring_uses_admonition_format(self) -> None:
        """MkDocs style should emit Markdown admonition syntax."""
        assert old_mkdocs_style_function.__doc__ is not None
        assert '!!! warning "Deprecated in 0.1"' in old_mkdocs_style_function.__doc__
        assert "Use `tests.collection_docstrings.new_function` instead." in old_mkdocs_style_function.__doc__
        assert "Args:" in old_mkdocs_style_function.__doc__
        notice_idx = old_mkdocs_style_function.__doc__.index('!!! warning "Deprecated in 0.1"')
        args_idx = old_mkdocs_style_function.__doc__.index("Args:")
        assert notice_idx < args_idx

    def test_remove_version_line_omitted_when_remove_in_is_empty(self) -> None:
        """Docstring notice omits remove-version line when remove_in is not provided."""
        assert old_no_remove_version_function.__doc__ is not None
        assert "Will be removed in" not in old_no_remove_version_function.__doc__

    def test_target_line_omitted_when_target_is_none(self) -> None:
        """Docstring notice omits target line when no target callable is provided."""
        assert old_no_target_function.__doc__ is not None
        assert "Use :" not in old_no_target_function.__doc__
        assert "Use `" not in old_no_target_function.__doc__


class TestArgsDocstringAnnotation:
    """Full-docstring equality checks for inline arg deprecation annotations."""

    def test_google_args_removed(self) -> None:
        """Removed arg: inline note inserted under the arg; no general block appended."""
        expected = """Train the model.

Args:
    lr: Learning rate for training.
    train_config: Training configuration object.
        Deprecated since v1.8 — no longer used. Will be removed in v1.9.

Returns:
    Training result."""
        assert _normalize_doc(google_args_removed.__doc__) == expected

    def test_google_args_renamed(self) -> None:
        """Renamed arg: inline note names the replacement; no general block appended."""
        expected = """Train the model.

Args:
    lr: Learning rate for training.
    train_config: Old configuration parameter.
        Deprecated since v1.8 — use `config` instead. Will be removed in v1.9.
    config: New configuration parameter.

Returns:
    Training result."""
        assert _normalize_doc(google_args_renamed.__doc__) == expected

    def test_sphinx_args_removed(self) -> None:
        """Sphinx-style: note inserted under ``:param``; no general block appended."""
        expected = """Train the model.

:param lr: Learning rate for training.
:param train_config: Training configuration object.
    Deprecated since v1.8 — no longer used. Will be removed in v1.9.
:returns: Training result."""
        assert _normalize_doc(sphinx_args_removed.__doc__) == expected

    def test_args_not_in_docstring(self) -> None:
        """Arg absent from the docstring falls back to a general ``.. deprecated::`` block."""
        expected = """Train the model.

Args:
    lr: Learning rate for training.

.. deprecated:: 1.8
   Will be removed in 1.9.
"""
        assert _normalize_doc(args_not_in_docstring.__doc__) == expected

    def test_google_multi_args_all_found(self) -> None:
        """Both deprecated args annotated inline in declaration order; no general block."""
        expected = """Run with two deprecated args, both present in the docstring.

Args:
    new_a (int): The replacement for old_a.
    old_a (int): The first deprecated argument.
        Deprecated since v1.8 — use `new_a` instead. Will be removed in v1.9.
    old_b (str): The second deprecated argument.
        Deprecated since v1.8 — no longer used. Will be removed in v1.9.

Returns:
    Result."""
        assert _normalize_doc(google_multi_args_all_found.__doc__) == expected

    def test_google_partial_annotation(self) -> None:
        """One arg found inline, one missing: inline note present AND general block appended."""
        expected = """Run with two deprecated args, only one present in the docstring.

Args:
    new_a: The replacement for old_a.
    old_a: The first deprecated argument.
        Deprecated since v1.8 — use `new_a` instead. Will be removed in v1.9.

Returns:
    Result.

.. deprecated:: 1.8
   Will be removed in 1.9.
"""
        assert _normalize_doc(google_partial_annotation.__doc__) == expected

    def test_google_arguments_header(self) -> None:
        """``Arguments:`` header treated identically to ``Args:``."""
        expected = """Train the model using the ``Arguments:`` section header variant.

Arguments:
    lr: Learning rate for training.
    train_config: Training configuration object.
        Deprecated since v1.8 — no longer used. Will be removed in v1.9.

Returns:
    Training result."""
        assert _normalize_doc(google_arguments_header.__doc__) == expected

    def test_sphinx_arg_not_in_docstring(self) -> None:
        """Sphinx-style: absent param falls back to a general ``.. deprecated::`` block appended at end."""
        expected = """Train the model.

:param lr: Learning rate for training.
:returns: Training result.

.. deprecated:: 1.8
   Will be removed in 1.9.
"""
        assert _normalize_doc(sphinx_arg_not_in_docstring.__doc__) == expected

    def test_google_args_multiline(self) -> None:
        """Note appended after all continuation lines of a multiline arg description."""
        expected = """Train the model with a multiline arg description.

Args:
    lr: Learning rate for training.
        Must be a positive float.
    train_config: Training configuration object.
        Passed directly to the trainer.
        Ignored when ``None``.
        Deprecated since v1.8 — no longer used. Will be removed in v1.9.

Returns:
    Training result."""
        assert _normalize_doc(google_args_multiline.__doc__) == expected

    def test_sphinx_args_multiline(self) -> None:
        """Note appended after all continuation lines of a multiline Sphinx param."""
        expected = """Train the model with a multiline Sphinx param description.

:param lr: Learning rate for training.
    Must be a positive float.
:param train_config: Training configuration object.
    Passed directly to the trainer.
    Ignored when ``None``.
    Deprecated since v1.8 — no longer used. Will be removed in v1.9.
:returns: Training result."""
        assert _normalize_doc(sphinx_args_multiline.__doc__) == expected

    def test_callable_target_with_args_mapping(self) -> None:
        """Callable target: inline note inserted AND general block appended with :func: ref."""
        expected = """Forward calls to new_function with a deprecated argument removed.

Args:
    a: The main integer input.
    b: Deprecated configuration string — will be removed.
        Deprecated since v1.8 — no longer used. Will be removed in v1.9.

Returns:
    Forwarded result.

.. deprecated:: 1.8
   Will be removed in 1.9.
   Use :func:`tests.collection_docstrings.new_function` instead.
"""
        assert _normalize_doc(callable_target_with_args_mapping.__doc__) == expected

    def test_no_target_with_args_mapping(self) -> None:
        """target=None: inline note inserted AND general block appended (no :func: ref)."""
        expected = """Warning-only deprecation with a deprecated argument.

Args:
    a: The main integer input.
    b: Deprecated configuration string — will be removed.
        Deprecated since v1.8 — no longer used. Will be removed in v1.9.

Returns:
    Result.

.. deprecated:: 1.8
   Will be removed in 1.9.
"""
        assert _normalize_doc(no_target_with_args_mapping.__doc__) == expected
