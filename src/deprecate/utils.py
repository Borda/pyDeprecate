"""Handy tools for deprecations.

Copyright (C) 2020-2023 Jiri Borovec <...>

"""

import inspect
import warnings
from contextlib import contextmanager
from typing import Any, Callable, Generator, List, Optional, Tuple, Type, Union


def get_func_arguments_types_defaults(func: Callable) -> List[Tuple[str, Tuple, Any]]:
    """Parse function arguments, types and default values.

    Args:
        func: a function to be xeamined

    Returns:
        sequence of details for each position/keyword argument

    Example:
        >>> get_func_arguments_types_defaults(get_func_arguments_types_defaults)
        [('func', typing.Callable, <class 'inspect._empty'>)]

    """
    func_default_params = inspect.signature(func).parameters
    func_arg_type_val = []
    for arg in func_default_params:
        arg_type = func_default_params[arg].annotation
        arg_default = func_default_params[arg].default
        func_arg_type_val.append((arg, arg_type, arg_default))
    return func_arg_type_val


def _warns_repr(warns: List[warnings.WarningMessage]) -> List[Union[Warning, str]]:
    return [w.message for w in warns]


@contextmanager
def no_warning_call(warning_type: Optional[Type[Warning]] = None, match: Optional[str] = None) -> Generator:
    """Check that no warning was raised.

    Args:
        warning_type: specify catching warning, if None catching all
        match: match message, containing following string, if None catches all.

    Raises:
        AssertionError: if specified warning was called

    """
    with warnings.catch_warnings(record=True) as called:
        # Cause all warnings to always be triggered.
        warnings.simplefilter("always")
        # Trigger a warning.
        yield
        # no warning raised
        if not called:
            return
        if not warning_type:
            raise AssertionError(f"While catching all warnings, these were found: {_warns_repr(called)}")
        # filter warnings by type
        warns = [w for w in called if issubclass(w.category, warning_type)]
        # Verify some things
        if not warns:
            return
        if not match:
            raise AssertionError(
                f"While catching `{warning_type.__name__}` warnings, these were found: {_warns_repr(warns)}"
            )
        found = [w for w in warns if match in w.message.__str__()]
        if found:
            raise AssertionError(
                f'While catching `{warning_type.__name__}` warnings with "{match}",'
                f" these were found: {_warns_repr(found)}"
            )


def void(*args: Any, **kwrgs: Any) -> Any:
    """Empty function which does nothing, just let your IDE stop complaining about unused arguments."""
    _, _ = args, kwrgs
