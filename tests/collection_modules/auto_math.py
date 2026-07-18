"""Deprecated in-place module exercising zero-arg ``module_name`` auto-detection (Mode 1 fixture).

Unlike ``old_math`` (which passes ``__name__`` explicitly), this module calls ``deprecated_module()``
with no ``module_name`` from its own top level, so the caller frame's ``f_locals`` IS its ``f_globals``
and auto-detection resolves ``__name__`` correctly.  It exists so the module-level auto-detect success
path is covered alongside the function-scope misuse guard.
"""

import deprecate


def cube(x: int) -> int:
    """Cube a number."""
    return x * x * x


deprecate.deprecated_module(deprecated_in="1.0", remove_in="2.0", message="Use new_math instead.")
