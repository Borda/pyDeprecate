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


@deprecated(target=new_function, deprecated_in="0.1", remove_in="0.3", update_docstring=True)
def old_function(a: int, b: str = "old") -> str:
    """An old function that is deprecated."""
    return f"old {a} {b}"


@deprecated(target=new_function, deprecated_in="0.1", remove_in="0.3", update_docstring=True)
def old_function_plain(a: int, b: str = "old") -> str:  # noqa: D103
    return f"old {a} {b}"


class OldClass:
    """An old class that is deprecated."""

    @deprecated(target=NewClass, deprecated_in="0.2", remove_in="0.4", update_docstring=True)
    def __init__(self, x: int) -> None:
        """Initialize the old class."""
        self.x = x


class OldClassPlain:  # noqa: D101
    @deprecated(target=NewClass, deprecated_in="0.2", remove_in="0.4", update_docstring=True)
    def __init__(self, x: int) -> None:  # noqa: D107
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
        lr (float): Learning rate for training.
        train_config (object): Training configuration object.

    Returns:
        str: Training result.
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
        lr (float): Learning rate for training.
        train_config (object): Old configuration parameter.
        config (object): New configuration parameter.

    Returns:
        str: Training result.
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
)
def args_not_in_docstring(lr: float = 0.01) -> str:
    """Train the model.

    Args:
        lr (float): Learning rate for training.
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
        str: Result.
    """
    return f"{new_a}"


@deprecated(
    target=True,
    args_mapping={"old_a": "new_a", "missing_b": None},
    deprecated_in="1.8",
    remove_in="1.9",
    update_docstring=True,
)
def google_partial_annotation(new_a: int = 0, old_a: int = 0) -> str:
    """Run with two deprecated args, only one present in the docstring.

    Args:
        new_a (int): The replacement for old_a.
        old_a (int): The first deprecated argument.

    Returns:
        str: Result.
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
        lr (float): Learning rate for training.
        train_config (object): Training configuration object.

    Returns:
        str: Training result.
    """
    return f"lr={lr}"


@deprecated(
    target=True,
    args_mapping={"missing_arg": None},
    deprecated_in="1.8",
    remove_in="1.9",
    update_docstring=True,
)
def sphinx_arg_not_in_docstring(lr: float = 0.01) -> str:
    """Train the model.

    :param lr: Learning rate for training.
    :returns: Training result.
    """
    return f"lr={lr}"
