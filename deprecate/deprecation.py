import inspect
from functools import wraps
from typing import Any, Callable, List, Tuple
from warnings import warn

TEMPLATE_WARNING = "The `%(source_name)s` was deprecated since v%(deprecated_in)s in favor of `%(target_path)s`." \
                   " It will be removed in v%(remove_in)s."


def get_func_arguments_types_defaults(func: Callable) -> List[Tuple[str, Tuple, Any]]:
    """Parse function arguments, types and default values

    Example:
        >>> get_func_arguments_types_defaults(get_func_arguments_types_defaults)
        [('func', typing.Callable, <class 'inspect._empty'>)]
    """
    func_default_params = inspect.signature(func).parameters
    func_arg_type_val = []
    for arg in func_default_params:
        arg_type = func_default_params[arg].annotation
        arg_default = func_default_params[arg].default
        func_arg_type_val.append((arg, arg_type, arg_default))
    return func_arg_type_val


def _update_kwargs(func: Callable, f_args: tuple, f_kwargs: dict) -> dict:
    """in case any args passed move them to kwargs and add defaults"""
    if not f_args:
        return f_kwargs
    func_arg_type_val = get_func_arguments_types_defaults(func)
    # parse only the argument names
    arg_names = [arg[0] for arg in func_arg_type_val]
    # convert args to kwargs
    f_kwargs.update({k: v for k, v in zip(arg_names, f_args)})
    # fill by source defaults
    f_defaults = {arg[0]: arg[2] for arg in func_arg_type_val if arg[2] != inspect._empty}  # type: ignore
    f_kwargs = dict(list(f_defaults.items()) + list(f_kwargs.items()))
    return f_kwargs


def _raise_warn(source: Callable, target: Callable, remove_in: str, deprecated_in: str, is_class: bool) -> None:
    """raise deprecation warning"""
    target_path = f'{target.__module__}.{target.__name__}'
    source_name = source.__qualname__.split('.')[-2] if is_class else source.__name__
    warn(
        TEMPLATE_WARNING % dict(
            source_name=source_name,
            target_path=target_path,
            remove_in=remove_in,
            deprecated_in=deprecated_in,
        ), DeprecationWarning
    )


def deprecated(target: Callable, deprecated_in: str = "", remove_in: str = "") -> Callable:
    """
    Decorate a function or class ``__init__`` with warning message
     and pass all arguments directly to the target class/method.
    """

    def packing(source: Callable) -> Callable:

        @wraps(source)
        def wrapped_fn(*args: Any, **kwargs: Any) -> Any:
            is_class = inspect.isclass(target)
            target_func = target.__init__ if is_class else target  # type: ignore
            # warn user only once in lifetime
            if not getattr(wrapped_fn, '_warned', False):
                _raise_warn(source, target, remove_in, deprecated_in, is_class)
                setattr(wrapped_fn, "_warned", True)

            kwargs = _update_kwargs(source, args, kwargs)

            target_args = [arg[0] for arg in get_func_arguments_types_defaults(target_func)]
            assert all(arg in target_args for arg in kwargs), \
                "Failed mapping, arguments missing in target source: %s" % [arg not in target_args for arg in kwargs]
            # all args were already moved to kwargs
            return target_func(**kwargs)

        return wrapped_fn

    return packing
