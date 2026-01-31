"""Collection of degenerated (zero-impact) deprecated functions for testing validation utilities.

These functions intentionally have invalid or ineffective deprecation configurations
to test the validate_deprecated_callable() and find_deprecated_callables() utilities.

Copyright (C) 2020-2023 Jiri Borovec <...>.
"""

from deprecate import deprecated, void


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={"nonexistent_arg": "new_arg"})
def invalid_args_deprecation(real_arg: int = 1) -> int:
    """Deprecation with invalid args_mapping key - nonexistent_arg doesn't exist."""
    return real_arg


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={})
def empty_mapping_deprecation(arg1: int = 1, arg2: int = 2) -> int:
    """Deprecation with empty args_mapping - has no effect."""
    return arg1 + arg2


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping=None)
def none_mapping_deprecation(arg1: int = 1, arg2: int = 2) -> int:
    """Deprecation with None args_mapping - has no effect."""
    return arg1 + arg2


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={"arg1": "arg1"})
def identity_mapping_deprecation(arg1: int = 1, arg2: int = 2) -> int:
    """Deprecation with identity mapping (arg mapped to itself) - has no effect."""
    return arg1 + arg2


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={"arg1": "arg1", "arg2": "arg2"})
def all_identity_mapping_deprecation(arg1: int = 1, arg2: int = 2) -> int:
    """Deprecation where ALL args are identity mapped - has no effect."""
    return arg1 + arg2


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={"arg1": "arg1", "arg2": "new_arg2"})
def partial_identity_mapping_deprecation(arg1: int = 1, arg2: int = 0, new_arg2: int = 2) -> int:
    """Deprecation with mix of identity and valid mappings - still has some effect."""
    return arg1 + new_arg2


def _self_ref_target(a: int = 1, b: int = 2) -> int:
    """Target function for self-reference test."""
    return a + b


# Create a self-referencing deprecation using a workaround:
# We define a function that targets another function, but then we manually
# set the target to itself after decoration.
@deprecated(target=_self_ref_target, deprecated_in="0.1", remove_in="0.5", args_mapping={"old_arg": "new_arg"})
def self_referencing_deprecation(old_arg: int = 1, new_arg: int = 2) -> int:
    """Deprecation that self-references - has no effect."""
    return void(old_arg, new_arg)


# Manually update the __deprecated__ attribute to make it self-referencing
self_referencing_deprecation.__deprecated__["target"] = self_referencing_deprecation
