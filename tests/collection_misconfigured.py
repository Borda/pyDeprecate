"""Collection of misconfigured deprecated functions for testing validation utilities.

These functions intentionally have invalid or ineffective deprecation configurations.
They simulate common mistakes developers make when setting up deprecation wrappers:

- Referencing arguments that don't exist in the function signature
- Providing an empty or None args_mapping (no-op deprecation)
- Mapping an argument to itself (identity mapping, no actual rename)
- Mixing identity and valid mappings
- Creating a self-referencing deprecation (wrapper targets itself)

Used by `validate_deprecated_callable()` and `find_deprecated_callables()` to verify
that the validation tooling correctly detects these misconfigurations.

Copyright (C) 2020-2026 Jiri Borovec <...>.
"""

from deprecate import deprecated, void


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={"nonexistent_arg": "new_arg"})
def invalid_args_deprecation(real_arg: int = 1) -> int:
    """Nonexistent argument name in args_mapping.

    Examples:
        Developer typos an arg name — `nonexistent_arg` is not a parameter
        of this function, so the mapping has no effect.
    """
    return real_arg


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={})
def empty_mapping_deprecation(arg1: int = 1, arg2: int = 2) -> int:
    """Empty args_mapping dict.

    Examples:
        Developer passes an empty `dict` — the decorator does nothing useful.
    """
    return arg1 + arg2


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping=None)
def none_mapping_deprecation(arg1: int = 1, arg2: int = 2) -> int:
    """`None` passed as `args_mapping`.

    Examples:
        Developer passes `None` instead of a `dict` — equivalent to no mapping.
    """
    return arg1 + arg2


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={"arg1": "arg1"})
def identity_mapping_deprecation(arg1: int = 1, arg2: int = 2) -> int:
    """Single identity mapping (arg mapped to itself).

    Examples:
        Developer maps `arg1` -> `arg1` — no actual rename happens.
    """
    return arg1 + arg2


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={"arg1": "arg1", "arg2": "arg2"})
def all_identity_mapping_deprecation(arg1: int = 1, arg2: int = 2) -> int:
    """All arguments are identity mapped.

    Examples:
        Every arg is mapped to itself — the entire mapping is a no-op.
    """
    return arg1 + arg2


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={"arg1": "arg1", "arg2": "new_arg2"})
def partial_identity_mapping_deprecation(arg1: int = 1, arg2: int = 0, new_arg2: int = 2) -> int:
    """Mix of identity and valid mappings.

    Examples:
        `arg1` -> `arg1` is a no-op, but `arg2` -> `new_arg2` is valid.
        The deprecation still has some effect despite the identity mapping.
    """
    return arg1 + new_arg2


def _self_ref_target(a: int = 1, b: int = 2) -> int:
    """Target function for self-reference test."""
    return a + b


@deprecated(target=_self_ref_target, deprecated_in="0.1", remove_in="0.5", args_mapping={"old_arg": "new_arg"})
def self_referencing_deprecation(old_arg: int = 1, new_arg: int = 2) -> int:
    """Self-referencing deprecation (wrapper targets itself).

    The deprecation is created using a workaround:
    We define a function that targets another function, but then we manually
    set the target to itself after decoration.

    Examples:
        The `__deprecated__` target is manually set to the wrapper itself after
        decoration, creating a circular reference that makes the deprecation meaningless.
    """
    return void(old_arg, new_arg)


# Manually update the __deprecated__ attribute to make it self-referencing
self_referencing_deprecation.__deprecated__["target"] = self_referencing_deprecation
