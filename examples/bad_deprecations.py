"""Examples of bad deprecation patterns that the CLI should catch."""

from deprecate import deprecated


def new_func(a: int) -> int:
    """Target function."""
    return a


# BAD: Mapping an argument 'non_existent' that doesn't exist in the function signature
@deprecated(target=True, args_mapping={"non_existent": "a"}, deprecated_in="1.0")
def bad_mapping_func(a: int) -> int:
    """Function with invalid argument mapping."""
    return a


# BAD: Deprecation with NO EFFECT (target=True but no mapping)
@deprecated(target=True, deprecated_in="1.0")
def useless_deprecation(a: int) -> int:
    """Function with no-effect deprecation."""
    return a


# BAD: Identity mapping (mapping arg to itself)
@deprecated(target=True, args_mapping={"a": "a"}, deprecated_in="1.0")
def identity_mapping_func(a: int) -> int:
    """Function with identity argument mapping."""
    return a
