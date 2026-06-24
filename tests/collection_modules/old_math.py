"""Deprecated in-place module (Mode 1 fixture).

This module is deprecated; use ``new_math`` instead.
The ``square`` function is a real member defined in ``__dict__`` — PEP 562 ``__getattr__``
fires *only* for names **not** already in ``__dict__``, so ``old_math.square`` will NOT warn.
Only accesses to names absent from ``__dict__`` (e.g. ``old_math.nonexistent_attr``) trigger
the deprecation warning.
"""

import deprecate


def square(x: int) -> int:
    """Square a number."""
    return x * x


deprecate.deprecated_module(
    __name__,
    deprecated_in="1.0",
    remove_in="2.0",
    message="Use new_math instead.",
)
