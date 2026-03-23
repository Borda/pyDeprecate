"""Tests for deprecation documentation strings."""

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
    sphinx_arg_not_in_docstring,
    sphinx_args_multiline,
    sphinx_args_removed,
)


class TestDeprecationDocstrings:
    """Tests for deprecation documentation strings."""

    def test_deprecated_func_docstring(self) -> None:
        """Deprecated function docstring gets a ``.. deprecated::`` block appended."""
        assert old_function.__doc__ == """An old function that is deprecated.

.. deprecated:: 0.1
   Will be removed in 0.3.
   Use :func:`tests.collection_docstrings.new_function` instead.
"""

    def test_deprecated_func_docstring_plain(self) -> None:
        """Function without docstring is left with ``__doc__ = None``."""
        assert old_function_plain.__doc__ is None

    def test_deprecated_class_docstring(self) -> None:
        """Deprecated __init__ gets a ``.. deprecated::`` block appended."""
        assert OldClass.__init__.__doc__ == """Initialize the old class.

.. deprecated:: 0.2
   Will be removed in 0.4.
   Use :class:`tests.collection_docstrings.NewClass` instead.
"""

    def test_deprecated_class_docstring_plain(self) -> None:
        """__init__ without docstring is left with ``__doc__ = None``."""
        assert getattr(OldClassPlain.__init__, "__doc__") is None


class TestArgsDocstringAnnotation:
    """Full-docstring equality checks for inline arg deprecation annotations."""

    def test_google_args_removed(self) -> None:
        """Removed arg: inline note inserted under the arg; no general block appended."""
        assert google_args_removed.__doc__ == """Train the model.

    Args:
        lr: Learning rate for training.
        train_config: Training configuration object.
            Deprecated since v1.8 — no longer used. Will be removed in v1.9.

    Returns:
        Training result.
    """

    def test_google_args_renamed(self) -> None:
        """Renamed arg: inline note names the replacement; no general block appended."""
        assert google_args_renamed.__doc__ == """Train the model.

    Args:
        lr: Learning rate for training.
        train_config: Old configuration parameter.
            Deprecated since v1.8 — use `config` instead. Will be removed in v1.9.
        config: New configuration parameter.

    Returns:
        Training result.
    """

    def test_sphinx_args_removed(self) -> None:
        """Sphinx-style: note inserted under ``:param``; no general block appended."""
        assert sphinx_args_removed.__doc__ == """Train the model.

    :param lr: Learning rate for training.
    :param train_config: Training configuration object.
        Deprecated since v1.8 — no longer used. Will be removed in v1.9.
    :returns: Training result.
    """

    def test_args_not_in_docstring(self) -> None:
        """Arg absent from the docstring falls back to a general ``.. deprecated::`` block."""
        assert args_not_in_docstring.__doc__ == """Train the model.

    Args:
        lr: Learning rate for training.

.. deprecated:: 1.8
   Will be removed in 1.9.
"""

    def test_google_multi_args_all_found(self) -> None:
        """Both deprecated args annotated inline in declaration order; no general block."""
        assert google_multi_args_all_found.__doc__ == """Run with two deprecated args, both present in the docstring.

    Args:
        new_a (int): The replacement for old_a.
        old_a (int): The first deprecated argument.
            Deprecated since v1.8 — use `new_a` instead. Will be removed in v1.9.
        old_b (str): The second deprecated argument.
            Deprecated since v1.8 — no longer used. Will be removed in v1.9.

    Returns:
        Result.
    """

    def test_google_partial_annotation(self) -> None:
        """One arg found inline, one missing: inline note present AND general block appended."""
        assert google_partial_annotation.__doc__ == """Run with two deprecated args, only one present in the docstring.

    Args:
        new_a: The replacement for old_a.
        old_a: The first deprecated argument.
            Deprecated since v1.8 — use `new_a` instead. Will be removed in v1.9.

    Returns:
        Result.

.. deprecated:: 1.8
   Will be removed in 1.9.
"""

    def test_google_arguments_header(self) -> None:
        """``Arguments:`` header treated identically to ``Args:``."""
        assert google_arguments_header.__doc__ == """Train the model using the ``Arguments:`` section header variant.

    Arguments:
        lr: Learning rate for training.
        train_config: Training configuration object.
            Deprecated since v1.8 — no longer used. Will be removed in v1.9.

    Returns:
        Training result.
    """

    def test_sphinx_arg_not_in_docstring(self) -> None:
        """Sphinx-style: absent param falls back to a general ``.. deprecated::`` block."""
        assert sphinx_arg_not_in_docstring.__doc__ == """Train the model.

    :param lr: Learning rate for training.
    :returns: Training result.

.. deprecated:: 1.8
   Will be removed in 1.9.
"""

    def test_google_args_multiline(self) -> None:
        """Note appended after all continuation lines of a multiline arg description."""
        assert google_args_multiline.__doc__ == """Train the model with a multiline arg description.

    Args:
        lr: Learning rate for training.
            Must be a positive float.
        train_config: Training configuration object.
            Passed directly to the trainer.
            Ignored when ``None``.
            Deprecated since v1.8 — no longer used. Will be removed in v1.9.

    Returns:
        Training result.
    """

    def test_sphinx_args_multiline(self) -> None:
        """Note appended after all continuation lines of a multiline Sphinx param."""
        assert sphinx_args_multiline.__doc__ == """Train the model with a multiline Sphinx param description.

    :param lr: Learning rate for training.
        Must be a positive float.
    :param train_config: Training configuration object.
        Passed directly to the trainer.
        Ignored when ``None``.
        Deprecated since v1.8 — no longer used. Will be removed in v1.9.
    :returns: Training result.
    """

    def test_callable_target_with_args_mapping(self) -> None:
        """Callable target: inline note inserted AND general block appended with :func: ref."""
        assert callable_target_with_args_mapping.__doc__ == (
            """Forward calls to new_function with a deprecated argument removed.

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
        )

    def test_no_target_with_args_mapping(self) -> None:
        """target=None: inline note inserted AND general block appended (no :func: ref)."""
        assert no_target_with_args_mapping.__doc__ == """Warning-only deprecation with a deprecated argument.

    Args:
        a: The main integer input.
        b: Deprecated configuration string — will be removed.
            Deprecated since v1.8 — no longer used. Will be removed in v1.9.

    Returns:
        Result.

.. deprecated:: 1.8
   Will be removed in 1.9.
"""
