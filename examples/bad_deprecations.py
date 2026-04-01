from deprecate import deprecated, void

def new_func(a: int) -> int:
    return a

# BAD: Mapping an argument 'non_existent' that doesn't exist in the function signature
@deprecated(target=True, args_mapping={"non_existent": "a"}, deprecated_in="1.0")
def bad_mapping_func(a: int) -> int:
    return a

# BAD: Deprecation with NO EFFECT (target=True but no mapping)
@deprecated(target=True, deprecated_in="1.0")
def useless_deprecation(a: int) -> int:
    return a

# BAD: Identity mapping (mapping arg to itself)
@deprecated(target=True, args_mapping={"a": "a"}, deprecated_in="1.0")
def identity_mapping_func(a: int) -> int:
    return a
