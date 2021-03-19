import inspect
from functools import partial, wraps
from typing import Any, Callable, List, Optional, Tuple
from warnings import warn

TEMPLATE_WARNING = "The `%(source_name)s` was deprecated since v%(deprecated_in)s in favor of `%(target_path)s`." \
                   " It will be removed in v%(remove_in)s."

deprecation_warning = partial(warn, category=DeprecationWarning)


def get_func_arguments_types_defaults(func: Callable) -> List[Tuple[str, Tuple, Any]]:
    """
    Parse function arguments, types and default values

    Args:
        func: a function to be xeamined

    Returns:
        sequence of details for each position/keyward argument

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


def update_kwargs(func: Callable, fn_args: tuple, fn_kwargs: dict) -> dict:
    """
    Update in case any args passed move them to kwargs and add defaults

    Args:
        func: particular function
        fn_args: function position arguments
        fn_kwargs: function keyword arguments

    Returns:
        extended dictionary with all args as keyword arguments

    """
    if not fn_args:
        return fn_kwargs
    func_arg_type_val = get_func_arguments_types_defaults(func)
    # parse only the argument names
    arg_names = [arg[0] for arg in func_arg_type_val]
    # convert args to kwargs
    fn_kwargs.update({k: v for k, v in zip(arg_names, fn_args)})
    # fill by source defaults
    f_defaults = {arg[0]: arg[2] for arg in func_arg_type_val if arg[2] != inspect._empty}  # type: ignore
    fn_kwargs = dict(list(f_defaults.items()) + list(fn_kwargs.items()))
    return fn_kwargs


def _raise_warn(stream: Callable, source: Callable, target: Callable, deprecated_in: str, remove_in: str) -> None:
    """
    Raise deprecation warning with in given stream,

    Args:
        stream: a function which takes message as the only position argument
        source: function/methods which is wrapped
        target: function/methods which is mapping target
        deprecated_in: set version when source is deprecated
        remove_in: set version when source will be removed

    """
    is_class = inspect.isclass(target)
    target_path = f'{target.__module__}.{target.__name__}'
    source_name = source.__qualname__.split('.')[-2] if is_class else source.__name__
    stream(
        TEMPLATE_WARNING % dict(
            source_name=source_name,
            target_path=target_path,
            remove_in=remove_in,
            deprecated_in=deprecated_in,
        )
    )


def deprecated(
    target: Callable,
    deprecated_in: str = "",
    remove_in: str = "",
    stream: Optional[Callable] = deprecation_warning,
) -> Callable:
    """
    Decorate a function or class ``__init__`` with warning message
     and pass all arguments directly to the target class/method.

    Args:
        target: function or method to forward the call
        deprecated_in: define version when the wrapped function is deprecated
        remove_in: define version when the wrapped function will be removed
        stream: set stream for printing warning messages, by default is deprecation warning.
            Setting ``None``, no warning is shown to user.

    Returns:
        wrapped function pointing to the target implementation with source arguments

    Raises:
        TypeError: if there are some argument in source function which are missing in target function

    """

    def packing(source: Callable) -> Callable:

        @wraps(source)
        def wrapped_fn(*args: Any, **kwargs: Any) -> Any:
            is_class = inspect.isclass(target)
            target_func = target.__init__ if is_class else target  # type: ignore
            # warn user only once in lifetime
            if stream and not getattr(wrapped_fn, '_warned', False):
                _raise_warn(stream, source, target, deprecated_in, remove_in)
                setattr(wrapped_fn, "_warned", True)

            kwargs = update_kwargs(source, args, kwargs)

            target_args = [arg[0] for arg in get_func_arguments_types_defaults(target_func)]
            missed = [arg for arg in kwargs if arg not in target_args]
            if missed:
                raise TypeError("Failed mapping, arguments missing in target source: %s" % missed)
            # all args were already moved to kwargs
            return target_func(**kwargs)

        return wrapped_fn

    return packing
