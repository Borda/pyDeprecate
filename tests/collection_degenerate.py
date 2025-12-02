"""Collection of degenerated (zero-impact) deprecated functions for testing validation utilities.

These functions intentionally have invalid or ineffective deprecation configurations
to test the validate_deprecated_callable() and find_deprecated_callables() utilities.

Copyright (C) 2020-2023 Jiri Borovec <...>.
"""

from deprecate import deprecated, void
from tests.collection_targets import base_sum_kwargs

# =============================================================================
# Valid deprecations (have effect)
# =============================================================================


@deprecated(target=base_sum_kwargs, deprecated_in="0.1", remove_in="0.5")
def valid_deprecation(a: int, b: int = 5) -> int:
    """A properly configured deprecated function - has effect."""
    return void(a, b)


@deprecated(target=True, deprecated_in="0.1", remove_in="0.5", args_mapping={"old_arg": "new_arg"})
def valid_self_deprecation(old_arg: int = 1, new_arg: int = 2) -> int:
    """A properly configured self-deprecation with arg mapping - has effect."""
    return new_arg


# =============================================================================
# Degenerated deprecations (zero impact)
# =============================================================================


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


# Note: We can't easily create a true self-reference at module level because the function
# doesn't exist yet when the decorator is applied. But we can test this via validate_deprecated_callable()
# by passing target=func explicitly.
