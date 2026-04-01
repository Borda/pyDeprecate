from deprecate import deprecated, void


def new_func(a: int, b: int) -> int:
    return a + b


@deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
def old_func(a: int, b: int) -> int:
    """Correctly deprecated function."""
    return void(a, b)


@deprecated(target=True, args_mapping={"old_arg": "new_arg"}, deprecated_in="1.0")
def renamed_arg_func(old_arg: int = 0, new_arg: int = 0) -> int:
    """Correctly renamed argument."""
    return new_arg
