"""Target and deprecated callables for testing update_docstring=True behaviour."""

from deprecate import deprecated


def new_function(a: int, b: str = "default") -> str:
    """A new function that is the target."""
    return f"{a} {b}"


class NewClass:
    """A new class."""

    def __init__(self, x: int) -> None:
        """Initialize NewClass."""
        self.x = x


@deprecated(target=new_function, deprecated_in="0.1", remove_in="0.3", update_docstring=True, docstring_style="rst")
def old_function(a: int, b: str = "old") -> str:
    """An old function that is deprecated."""
    return f"old {a} {b}"


@deprecated(target=new_function, deprecated_in="0.1", remove_in="0.3", update_docstring=True)
def old_function_plain(a: int, b: str = "old") -> str:
    return f"old {a} {b}"


@deprecated(target=new_function, deprecated_in="0.1", remove_in="0.3", update_docstring=True, docstring_style="rst")
def old_google_no_sections_function(a: int, b: str = "old") -> str:
    """Old Google-style function without explicit sections."""
    return f"old {a} {b}"


@deprecated(target=new_function, deprecated_in="0.1", remove_in="0.3", update_docstring=True, docstring_style="rst")
def old_numpy_no_sections_function(a: int, b: str = "old") -> str:
    """Old NumPy-style function without explicit sections."""
    return f"old {a} {b}"


@deprecated(target=new_function, deprecated_in="0.1", remove_in="0.3", update_docstring=True, docstring_style="rst")
def old_google_style_function(a: int, b: str = "old") -> str:
    """An old Google-style function.

    Args:
        a: Number argument.
        b: Text argument.

    Returns:
        A formatted output.
    """
    return f"old {a} {b}"


@deprecated(target=new_function, deprecated_in="0.1", remove_in="0.3", update_docstring=True, docstring_style="rst")
def old_numpy_style_function(a: int, b: str = "old") -> str:
    """An old NumPy-style function.

    Parameters
    ----------
    a : int
        Number argument.
    b : str
        Text argument.
    """
    return f"old {a} {b}"


@deprecated(target=new_function, deprecated_in="0.1", update_docstring=True, docstring_style="rst")
def old_no_remove_version_function(a: int, b: str = "old") -> str:
    """Old function without remove version."""
    return f"old {a} {b}"


@deprecated(target=None, deprecated_in="0.1", update_docstring=True, docstring_style="rst")
def old_no_target_function(a: int, b: str = "old") -> str:
    """Old function without target."""
    return f"old {a} {b}"


@deprecated(
    target=new_function,
    deprecated_in="0.1",
    remove_in="0.3",
    update_docstring=True,
    docstring_style="mkdocs",
)
def old_mkdocs_style_function(a: int, b: str = "old") -> str:
    """An old MkDocs-style function.

    Args:
        a: Number argument.
        b: Text argument.

    Returns:
        A formatted output.
    """
    return f"old {a} {b}"


@deprecated(
    target=new_function,
    deprecated_in="0.1",
    remove_in="0.3",
    update_docstring=True,
    docstring_style="markdown",
)
def old_markdown_alias_function(a: int, b: str = "old") -> str:
    """An old function using the ``markdown`` style alias.

    Args:
        a: Number argument.
        b: Text argument.

    Returns:
        A formatted output.
    """
    return f"old {a} {b}"


class OldClass:
    """An old class that is deprecated."""

    @deprecated(target=NewClass, deprecated_in="0.2", remove_in="0.4", update_docstring=True, docstring_style="rst")
    def __init__(self, x: int) -> None:
        """Initialize the old class."""
        self.x = x


class OldClassPlain:
    @deprecated(target=NewClass, deprecated_in="0.2", remove_in="0.4", update_docstring=True)
    def __init__(self, x: int) -> None:
        self.x = x


# ── args_mapping + update_docstring fixtures ─────────────────────────────────


@deprecated(
    target=True,
    args_mapping={"train_config": None},
    deprecated_in="1.8",
    remove_in="1.9",
    update_docstring=True,
)
def google_args_removed(lr: float = 0.01, train_config: object = None) -> str:
    """Train the model.

    Args:
        lr: Learning rate for training.
        train_config: Training configuration object.

    Returns:
        Training result.
    """
    return f"lr={lr}"


@deprecated(
    target=True,
    args_mapping={"train_config": "config"},
    deprecated_in="1.8",
    remove_in="1.9",
    update_docstring=True,
)
def google_args_renamed(lr: float = 0.01, train_config: object = None, config: object = None) -> str:
    """Train the model.

    Args:
        lr: Learning rate for training.
        train_config: Old configuration parameter.
        config: New configuration parameter.

    Returns:
        Training result.
    """
    return f"lr={lr}"


@deprecated(
    target=True,
    args_mapping={"train_config": None},
    deprecated_in="1.8",
    remove_in="1.9",
    update_docstring=True,
)
def sphinx_args_removed(lr: float = 0.01, train_config: object = None) -> str:
    """Train the model.

    :param lr: Learning rate for training.
    :param train_config: Training configuration object.
    :returns: Training result.
    """
    return f"lr={lr}"


@deprecated(
    target=True,
    args_mapping={"missing_arg": None},
    deprecated_in="1.8",
    remove_in="1.9",
    update_docstring=True,
    docstring_style="rst",
)
def args_not_in_docstring(lr: float = 0.01) -> str:
    """Train the model.

    Args:
        lr: Learning rate for training.
    """
    return f"lr={lr}"


@deprecated(
    target=True,
    args_mapping={"old_a": "new_a", "old_b": None},
    deprecated_in="1.8",
    remove_in="1.9",
    update_docstring=True,
)
def google_multi_args_all_found(new_a: int = 0, old_a: int = 0, old_b: str = "") -> str:
    """Run with two deprecated args, both present in the docstring.

    Args:
        new_a (int): The replacement for old_a.
        old_a (int): The first deprecated argument.
        old_b (str): The second deprecated argument.

    Returns:
        Result.
    """
    return f"{new_a}"


@deprecated(
    target=True,
    args_mapping={"old_a": "new_a", "missing_b": None},
    deprecated_in="1.8",
    remove_in="1.9",
    update_docstring=True,
    docstring_style="rst",
)
def google_partial_annotation(new_a: int = 0, old_a: int = 0) -> str:
    """Run with two deprecated args, only one present in the docstring.

    Args:
        new_a: The replacement for old_a.
        old_a: The first deprecated argument.

    Returns:
        Result.
    """
    return f"{new_a}"


@deprecated(
    target=True,
    args_mapping={"train_config": None},
    deprecated_in="1.8",
    remove_in="1.9",
    update_docstring=True,
)
def google_arguments_header(lr: float = 0.01, train_config: object = None) -> str:
    """Train the model using the ``Arguments:`` section header variant.

    Arguments:
        lr: Learning rate for training.
        train_config: Training configuration object.

    Returns:
        Training result.
    """
    return f"lr={lr}"


@deprecated(
    target=True,
    args_mapping={"missing_arg": None},
    deprecated_in="1.8",
    remove_in="1.9",
    update_docstring=True,
    docstring_style="rst",
)
def sphinx_arg_not_in_docstring(lr: float = 0.01) -> str:
    """Train the model.

    :param lr: Learning rate for training.
    :returns: Training result.
    """
    return f"lr={lr}"


@deprecated(
    target=True,
    args_mapping={"train_config": None},
    deprecated_in="1.8",
    remove_in="1.9",
    update_docstring=True,
)
def google_args_multiline(lr: float = 0.01, train_config: object = None) -> str:
    """Train the model with a multiline arg description.

    Args:
        lr: Learning rate for training.
            Must be a positive float.
        train_config: Training configuration object.
            Passed directly to the trainer.
            Ignored when ``None``.

    Returns:
        Training result.
    """
    return f"lr={lr}"


@deprecated(
    target=True,
    args_mapping={"train_config": None},
    deprecated_in="1.8",
    remove_in="1.9",
    update_docstring=True,
)
def sphinx_args_multiline(lr: float = 0.01, train_config: object = None) -> str:
    """Train the model with a multiline Sphinx param description.

    :param lr: Learning rate for training.
        Must be a positive float.
    :param train_config: Training configuration object.
        Passed directly to the trainer.
        Ignored when ``None``.
    :returns: Training result.
    """
    return f"lr={lr}"


@deprecated(
    target=new_function,
    args_mapping={"b": None},
    deprecated_in="1.8",
    remove_in="1.9",
    update_docstring=True,
    docstring_style="rst",
)
def callable_target_with_args_mapping(a: int, b: str = "old") -> str:
    """Forward calls to new_function with a deprecated argument removed.

    Args:
        a: The main integer input.
        b: Deprecated configuration string — will be removed.

    Returns:
        Forwarded result.
    """
    return new_function(a, b)


@deprecated(
    target=None,
    args_mapping={"b": None},
    deprecated_in="1.8",
    remove_in="1.9",
    update_docstring=True,
    docstring_style="rst",
)
def no_target_with_args_mapping(a: int, b: str = "old") -> str:
    """Warning-only deprecation with a deprecated argument.

    Args:
        a: The main integer input.
        b: Deprecated configuration string — will be removed.

    Returns:
        Result.
    """
    return f"{a}"


@deprecated(
    target=None,
    args_mapping={"b": None},
    deprecated_in="1.8",
    remove_in="1.9",
    update_docstring=True,
    docstring_style="mkdocs",
)
def mkdocs_no_target_with_args_mapping(a: int, b: str = "old") -> str:
    """Warning-only deprecation with a deprecated argument (MkDocs style).

    Args:
        a: The main integer input.
        b: Deprecated configuration string — will be removed.

    Returns:
        Result.
    """
    return f"{a}"
