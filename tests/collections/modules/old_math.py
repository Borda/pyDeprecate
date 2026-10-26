"""Deprecated in-place module (Mode 1 fixture).

This module is deprecated; use ``new_math`` instead.
Every public attribute access — including the real ``square`` function — emits a ``FutureWarning``
because ``deprecated_module()`` replaces the module's ``__class__`` with ``_DeprecatedModuleWrapper``,
which overrides ``__getattribute__`` to intercept all attribute lookups.
"""

import deprecate


def square(x: int) -> int:
    """Square a number."""
    return x * x


deprecate.deprecated_module(
    __name__,
    deprecated_in="1.0",
    remove_in="2.0",
    # message_template replaces the built-in notice; %-placeholders keep the deprecation context
    message_template=(
        "The `%(source_name)s` module was deprecated since v%(deprecated_in)s;"
        " use `new_math` instead. It will be removed in v%(remove_in)s."
    ),
)
