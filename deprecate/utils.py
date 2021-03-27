"""
Copyright (C) 2020-2021 Jiri Borovec <...>
"""
import warnings
from contextlib import contextmanager
from typing import Optional


def _warns_repr(warns) -> list:
    return [w.message for w in warns]


@contextmanager
def no_warning_call(warning_type: Optional[Warning] = None, match: Optional[str] = None):
    """

    Args:
        warning_type: specify catching warning
        match: match message, containing following string

    Returns:

    """
    with warnings.catch_warnings(record=True) as called:
        # Cause all warnings to always be triggered.
        warnings.simplefilter("always")
        # Trigger a warning.
        yield
        # no warning raised
        if not called:
            return
        elif not warning_type:
            raise AssertionError(f'While catching all warnings, these were found: {_warns_repr(called)}')
        # filter warnings by type
        warns = [w for w in called if issubclass(w.category, warning_type)]
        # Verify some things
        if not warns:
            return
        elif not match:
            raise AssertionError(
                f'While catching `{warning_type.__name__}` warnings, these were found: {_warns_repr(warns)}'
            )
        found = [w for w in warns if match in w.message.__str__()]
        if found:
            raise AssertionError(
                f'While catching `{warning_type.__name__}` warnings with "{match}",'
                f' these were found: {_warns_repr(found)}'
            )
