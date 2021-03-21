import inspect
from functools import partial, wraps
from typing import Any, Callable, Dict, List, Optional, Tuple
from warnings import warn

#: Default template warning message
TEMPLATE_WARNING = (
    "The `%(source_name)s` was deprecated since v%(deprecated_in)s in favor of `%(target_path)s`."
    " It will be removed in v%(remove_in)s."
)
#: Default template warning message for no target func/method
TEMPLATE_WARNING_NO_TARGET = (
    "The `%(source_name)s` was deprecated since v%(deprecated_in)s. It will be removed in v%(remove_in)s."
)

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
    fn_kwargs.update(dict(zip(arg_names, fn_args)))
    # fill by source defaults
    fn_defaults = {arg[0]: arg[2] for arg in func_arg_type_val if arg[2] != inspect._empty}  # type: ignore
    fn_kwargs = dict(list(fn_defaults.items()) + list(fn_kwargs.items()))
    return fn_kwargs


def _raise_warn(
    stream: Callable,
    source: Callable,
    target: Optional[Callable],
    deprecated_in: str,
    remove_in: str,
    template_mgs: Optional[str] = None,
) -> None:
    """
    Raise deprecation warning with in given stream,

    Args:
        stream: a function which takes message as the only position argument
        source: function/methods which is wrapped
        target: function/methods which is mapping target
        deprecated_in: set version when source is deprecated
        remove_in: set version when source will be removed
        template_mgs: python formatted string message which has build-ins arguments:

            - ``source_name`` just the functions name such as "my_source_func"
            - ``source_path`` pythonic path to the function such as "my_package.with_module.my_source_func"
            - ``target_name`` just the functions name such as "my_target_func"
            - ``target_path`` pythonic path to the function such as "any_package.with_module.my_target_func"
            - ``deprecated_in`` version passed to wrapper
            - ``remove_in`` version passed to wrapper

    """
    if target:
        target_name = target.__name__
        target_path = f'{target.__module__}.{target_name}'
        template_mgs = TEMPLATE_WARNING if template_mgs is None else template_mgs
    else:
        target_name, target_path = "", ""
        template_mgs = TEMPLATE_WARNING_NO_TARGET if template_mgs is None else template_mgs
    source_name = source.__qualname__.split('.')[-2] if source.__name__ == "__init__" else source.__name__
    source_path = f'{source.__module__}.{source_name}'
    msg_args = dict(
        source_name=source_name,
        source_path=source_path,
        target_name=target_name,
        target_path=target_path,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
    )
    stream(template_mgs % msg_args)


def deprecated(
    target: Optional[Callable],
    deprecated_in: str = "",
    remove_in: str = "",
    stream: Optional[Callable] = deprecation_warning,
    num_warns: int = 1,
    template_mgs: Optional[str] = None,
    args_mapping: Optional[Dict[str, str]] = None,
    args_extra: Optional[Dict[str, Any]] = None,
) -> Callable:
    """
    Decorate a function or class ``__init__`` with warning message
     and pass all arguments directly to the target class/method.

    Args:
        target: Function or method to forward the call. If set ``None``, no forwarding is applied and only warn.
        deprecated_in: Define version when the wrapped function is deprecated.
        remove_in: Define version when the wrapped function will be removed.
        stream: Set stream for printing warning messages, by default is deprecation warning.
            Setting ``None``, no warning is shown to user.
        num_warns: Custom define number or warning raised. Negative value (-1) means no limit.
        template_mgs: python formatted string message which has build-ins arguments:
            ``source_name``, ``source_path``, ``target_name``, ``target_path``, ``deprecated_in``, ``remove_in``
            Example of a custom message is
            ``"v%(deprecated_in)s: `%(source_name)s` was deprecated in favor of `%(target_path)s`."``
        args_mapping: Custom argument mapping argument between source and target and options to suppress some,
            for example ``{'my_arg': 'their_arg`}`` passes "my_arg" from source as "their_arg" in target
            or ``{'my_arg': None}`` ignores the "my_arg" from source function.
        args_extra: Custom filling extra argument in target function, mostly if they are required
            or your needed default is different from target one, for example ``{'their_arg': 42}``

    Returns:
        wrapped function pointing to the target implementation with source arguments

    Raises:
        TypeError: if there are some argument in source function which are missing in target function

    """

    def packing(source: Callable) -> Callable:

        @wraps(source)
        def wrapped_fn(*args: Any, **kwargs: Any) -> Any:
            nb_warned = getattr(wrapped_fn, '_warned', 0)
            nb_called = getattr(wrapped_fn, '_called', 0)
            # warn user only once in lifetime
            if stream and (num_warns < 0 or nb_warned < num_warns):
                _raise_warn(
                    stream,
                    source=source,
                    target=target,
                    deprecated_in=deprecated_in,
                    remove_in=remove_in,
                    template_mgs=template_mgs,
                )
                setattr(wrapped_fn, "_warned", nb_warned + 1)
            setattr(wrapped_fn, "_called", nb_called + 1)

            kwargs = update_kwargs(source, args, kwargs)
            # short cycle with no target function
            if not target:
                return source(**kwargs)

            target_is_class = inspect.isclass(target)
            target_func = target.__init__ if target_is_class else target  # type: ignore
            target_args = [arg[0] for arg in get_func_arguments_types_defaults(target_func)]

            if args_mapping:
                # filter args which shall be skipped
                args_skip = [arg for arg in args_mapping if not args_mapping[arg]]
                # Look-Up-table mapping
                kwargs = {args_mapping.get(arg, arg): val for arg, val in kwargs.items() if arg not in args_skip}

            if args_extra:
                # update target argument by extra arguments
                kwargs.update(args_extra)

            missed = [arg for arg in kwargs if arg not in target_args]
            if missed:
                raise TypeError("Failed mapping, arguments missing in target source: %s" % missed)
            # all args were already moved to kwargs
            return target_func(**kwargs)

        return wrapped_fn

    return packing
