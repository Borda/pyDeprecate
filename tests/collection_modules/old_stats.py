"""Deprecated in-place module with no migration guidance (policy-lint fixture).

Unlike ``old_math`` (which supplies a custom ``message_template`` naming its replacement), this module
passes no ``target``, no ``attrs_mapping``, and no ``message_template`` — callers only ever see the
built-in notice. It exists so ``validate_deprecation_policy()``'s ``message-required`` rule has a
module-level violation to flag, alongside ``old_math``'s module-level pass.
"""

import deprecate


def mean(values: list[float]) -> float:
    """Compute the mean of a list of numbers."""
    return sum(values) / len(values)


deprecate.deprecated_module(__name__, deprecated_in="1.0", remove_in="2.0")
