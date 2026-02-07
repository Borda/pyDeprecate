"""Deprecation wrapper and utilities for marking deprecated code.

This module provides the main @deprecated decorator for marking functions, methods,
and classes as deprecated while optionally forwarding calls to their replacements.

Key Components:
    - :func:`deprecated`: Main decorator for deprecation with automatic call forwarding
    - Warning templates for different deprecation scenarios
    - Internal helpers for argument mapping and warning management

Copyright (C) 2020-2026 Jiri Borovec <...>
"""

import inspect
from functools import lru_cache, partial, wraps
from typing import Any, Callable, Optional, Union
from warnings import warn

from deprecate.utils import get_func_arguments_types_defaults

#: Default template warning message for redirecting callable
TEMPLATE_WARNING_CALLABLE = (
    "The `%(source_name)s` was deprecated since v%(deprecated_in)s in favor of `%(target_path)s`."
    " It will be removed in v%(remove_in)s."
)
#: Default template warning message for changing argument mapping
TEMPLATE_WARNING_ARGUMENTS = (
    "The `%(source_name)s` uses deprecated arguments: %(argument_map)s."
    " They were deprecated since v%(deprecated_in)s and will be removed in v%(remove_in)s."
)
#: Template for mapping from old to new examples
TEMPLATE_ARGUMENT_MAPPING = "`%(old_arg)s` -> `%(new_arg)s`"
#: Default template warning message for no target func/method
TEMPLATE_WARNING_NO_TARGET = (
    "The `%(source_name)s` was deprecated since v%(deprecated_in)s. It will be removed in v%(remove_in)s."
)
#: Default template for documentation with deprecated callable
TEMPLATE_DOC_DEPRECATED = """
.. deprecated:: %(deprecated_in)s
   %(remove_text)s
   %(target_text)s
"""

deprecation_warning = partial(warn, category=FutureWarning)


@lru_cache(maxsize=256)
def _get_signature(func: Callable) -> inspect.Signature:
    """Cache inspect.signature lookups since signatures are stable at runtime.

    Uses a bounded cache (``maxsize=256``) to balance memory usage with reuse.
    """
    return inspect.signature(func)


def _positional_label(count: int) -> str:
    """Return a grammatically correct label for positional arguments."""
    return "argument" if count == 1 else "arguments"


def _get_positional_params(params: list[inspect.Parameter]) -> list[inspect.Parameter]:
    """Filter positional-only and positional-or-keyword parameters."""
    return [
        param
        for param in params
        if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]


def _update_kwargs_with_args(func: Callable, fn_args: tuple, fn_kwargs: dict) -> dict:
    """Convert positional arguments to keyword arguments using function signature.

    This helper function takes positional arguments and converts them to keyword
    arguments by matching them with parameter names from the function signature.
    This enables consistent argument handling in the deprecation wrapper.

    Args:
        func: Function whose signature provides parameter names.
        fn_args: Tuple of positional arguments passed to the function.
        fn_kwargs: Dictionary of keyword arguments already passed.

    Returns:
        Dictionary combining converted positional arguments and existing kwargs,
        where positional args are now mapped to their parameter names. Conversion
        stops when encountering var-positional parameters (``*args``) because
        they cannot be safely represented as keyword arguments.

    Example:
        >>> from pprint import pprint
        >>> def example_func(a, b, c=3): pass
        >>> pprint(_update_kwargs_with_args(example_func, (1, 2), {'c': 5}))
        {'a': 1, 'b': 2, 'c': 5}

    """
    if not fn_args:
        return fn_kwargs
    params = list(_get_signature(func).parameters.values())
    positional_params = _get_positional_params(params)
    has_var_positional = any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in params)

    if not has_var_positional and len(fn_args) > len(positional_params):
        required_positional_params = [
            param for param in positional_params if param.default is inspect.Parameter.empty
        ]
        if len(required_positional_params) == len(positional_params):
            expected_label = _positional_label(len(positional_params))
            received_label = _positional_label(len(fn_args))
            raise TypeError(
                f"{func.__qualname__}() takes {len(positional_params)} positional {expected_label} "
                f"but got {len(fn_args)} positional {received_label}"
            )
        max_positional_label = _positional_label(len(positional_params))
        received_label = _positional_label(len(fn_args))
        raise TypeError(
            f"{func.__qualname__}() takes {len(required_positional_params)} to {len(positional_params)} "
            f"positional {max_positional_label} but got {len(fn_args)} positional {received_label}"
        )
    updated_kwargs = dict(fn_kwargs)
    for index, arg in enumerate(fn_args):
        if index >= len(params):
            break
        param = params[index]
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            break
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            updated_kwargs[param.name] = arg
    return updated_kwargs


def _update_kwargs_with_defaults(func: Callable, fn_kwargs: dict) -> dict:
    """Merge function default values with provided keyword arguments.

    This helper fills in default parameter values from the function signature
    for any parameters not explicitly provided. Provided kwargs take precedence
    over defaults.

    Args:
        func: Function whose signature provides default parameter values.
        fn_kwargs: Dictionary of keyword arguments provided by caller.

    Returns:
        Dictionary with defaults merged with provided kwargs, where provided
        values override defaults.

    Example:
        >>> from pprint import pprint
        >>> def example_func(a=1, b=2, c=3): pass
        >>> pprint(_update_kwargs_with_defaults(example_func, {'b': 20}))
        {'a': 1, 'b': 20, 'c': 3}

    Note:
        Parameters without defaults (inspect._empty) are not included in the result.

    """
    func_arg_type_val = get_func_arguments_types_defaults(func)
    # fill by source defaults
    fn_defaults = {arg[0]: arg[2] for arg in func_arg_type_val if arg[2] != inspect._empty}
    return dict(list(fn_defaults.items()) + list(fn_kwargs.items()))


def _raise_warn(stream: Callable, source: Callable, template_mgs: str, **extras: str) -> None:
    """Issue a deprecation warning using the specified stream and message template.

    This is the core warning issuer that formats and emits deprecation warnings.
    It extracts source function metadata and combines it with provided template
    variables to generate the final warning message.

    Args:
        stream: Callable that outputs the warning (e.g., warnings.warn, logging.warning).
        source: The deprecated function/method being wrapped.
        template_mgs: Python format string with placeholders for message variables.
        **extras: Additional string values to substitute into the template
            (e.g., deprecated_in="1.0", remove_in="2.0").

    Note:
        Automatically extracts source_name and source_path from the source callable:
        - For regular functions: uses __name__
        - For __init__ methods: extracts class name from __qualname__

    Example:
        >>> import warnings
        >>> def old_func(): pass
        >>> _raise_warn(
        ...     warnings.warn,
        ...     old_func,
        ...     "%(source_name)s deprecated in %(version)s",
        ...     version="1.0"
        ... )

    """
    source_name = source.__qualname__.split(".")[-2] if source.__name__ == "__init__" else source.__name__
    source_path = f"{source.__module__}.{source_name}"
    msg_args = dict(source_name=source_name, source_path=source_path, **extras)
    stream(template_mgs % msg_args)


def _raise_warn_callable(
    stream: Callable,
    source: Callable,
    target: Union[None, bool, Callable],
    deprecated_in: str,
    remove_in: str,
    template_mgs: Optional[str] = None,
) -> None:
    """Issue deprecation warning for callable (function/class) deprecation.

    This specialized warning issuer handles deprecation of entire functions or
    classes that are being replaced by new implementations. It automatically
    determines the appropriate message template based on whether a target
    callable is specified.

    Args:
        stream: Callable that outputs the warning (e.g., warnings.warn, logging.warning).
        source: The deprecated function/method being wrapped.
        target: The replacement implementation:
            - Callable: Forward to this function/class
            - None: No forwarding (warning only mode)
            - bool: Not applicable for this function (use _raise_warn_arguments instead)
        deprecated_in: Version when the source was marked deprecated (e.g., "1.0.0").
        remove_in: Version when the source will be removed (e.g., "2.0.0").
        template_mgs: Custom message template. If None, uses default template based
            on whether target is callable or None.

    Template Variables Available:
        - source_name: Function name (e.g., "old_func")
        - source_path: Full path (e.g., "mymodule.old_func")
        - target_name: Target function name (only if target is callable)
        - target_path: Full target path (only if target is callable)
        - deprecated_in: Version parameter value
        - remove_in: Version parameter value

    Example:
        >>> import warnings
        >>> def new_func(): pass
        >>> def old_func(): pass
        >>> _raise_warn_callable(
        ...     stream=warnings.warn,
        ...     source=old_func,
        ...     target=new_func,
        ...     deprecated_in="1.0",
        ...     remove_in="2.0"
        ... )
        >>> # Outputs: "The `old_func` was deprecated since v1.0 in favor of
        >>> #           `__main__.new_func`. It will be removed in v2.0."

    """
    if callable(target):
        target_name = target.__name__
        target_path = f"{target.__module__}.{target_name}"
        template_mgs = template_mgs or TEMPLATE_WARNING_CALLABLE
    else:
        target_name, target_path = "", ""
        template_mgs = template_mgs or TEMPLATE_WARNING_NO_TARGET
    _raise_warn(
        stream=stream,
        source=source,
        template_mgs=template_mgs,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        target_name=target_name,
        target_path=target_path,
    )


def _raise_warn_arguments(
    stream: Callable,
    source: Callable,
    arguments: dict[str, str],
    deprecated_in: str,
    remove_in: str,
    template_mgs: Optional[str] = None,
) -> None:
    """Issue deprecation warning for deprecated function arguments.

    This specialized warning issuer handles deprecation of specific function
    parameters that are being renamed or removed. It generates a mapping
    string showing the old-to-new argument names.

    Args:
        stream: Callable that outputs the warning (e.g., warnings.warn, logging.warning).
        source: The function/method whose arguments are deprecated.
        arguments: Mapping from deprecated argument names to new names
            (e.g., {'old_arg': 'new_arg', 'removed_arg': None}).
        deprecated_in: Version when arguments were marked deprecated (e.g., "1.0.0").
        remove_in: Version when arguments will be removed (e.g., "2.0.0").
        template_mgs: Custom message template. If None, uses default template.

    Template Variables Available:
        - source_name: Function name (e.g., "my_func")
        - source_path: Full path (e.g., "mymodule.my_func")
        - argument_map: Formatted string showing mappings (e.g., "`old` -> `new`")
        - deprecated_in: Version parameter value
        - remove_in: Version parameter value

    Example:
        >>> import warnings
        >>> def my_func(old_arg=1, new_arg=1): pass
        >>> _raise_warn_arguments(
        ...     warnings.warn,
        ...     my_func,
        ...     {'old_arg': 'new_arg'},
        ...     "1.0",
        ...     "2.0"
        ... )
        >>> # Outputs: "The `my_func` uses deprecated arguments: `old_arg` -> `new_arg`.
        >>> #           They were deprecated since v1.0 and will be removed in v2.0."

    """
    args_map = ", ".join([TEMPLATE_ARGUMENT_MAPPING % {"old_arg": a, "new_arg": b} for a, b in arguments.items()])
    template_mgs = template_mgs or TEMPLATE_WARNING_ARGUMENTS
    _raise_warn(stream, source, template_mgs, deprecated_in=deprecated_in, remove_in=remove_in, argument_map=args_map)


def _update_docstring_with_deprecation(wrapped_fn: Callable) -> None:
    """Append deprecation notice to function's docstring in reStructuredText format.

    This helper automatically generates and appends a Sphinx-compatible deprecation
    notice to the wrapped function's docstring. The notice includes version information
    and target replacement (if applicable), making it visible in generated API documentation.

    The appended notice follows the Sphinx deprecated directive format:
        .. deprecated:: <version>
           Will be removed in <version>.
           Use `<target>` instead.

    Args:
        wrapped_fn: Function whose docstring should be updated. Must have
            __deprecated__ attribute set with deprecation metadata.

    Returns:
        None. Modifies the function's __doc__ attribute in-place.

    Metadata Used:
        The function's __deprecated__ attribute should contain:
        - deprecated_in: Version when deprecated
        - remove_in: Version when will be removed
        - target: Replacement callable (optional)

    Example:
        >>> def new_func(): pass
        >>> def old_func():
        ...     '''Original docstring.'''
        ...     pass
        >>> old_func.__deprecated__ = {
        ...     'deprecated_in': '1.0',
        ...     'remove_in': '2.0',
        ...     'target': new_func
        ... }
        >>> _update_docstring_with_deprecation(old_func)
        >>> print(old_func.__doc__) # doctest: +ELLIPSIS +NORMALIZE_WHITESPACE
        Original docstring.
        <BLANKLINE>
        .. deprecated:: 1.0
           Will be removed in 2.0.
           Use :func:`deprecate.deprecation.new_func` instead.

    Note:
        Does nothing if the function has no docstring or no __deprecated__ attribute.

    """
    if not hasattr(wrapped_fn, "__doc__") or not wrapped_fn.__doc__:
        return
    lines = wrapped_fn.__doc__.splitlines()
    dep_info = getattr(wrapped_fn, "__deprecated__", {})
    remove_in_val = dep_info.get("remove_in", "")
    target_val = dep_info.get("target")
    remove_text = f"Will be removed in {remove_in_val}." if remove_in_val else ""
    target_text = ""
    if callable(target_val):
        ref_type = "class" if inspect.isclass(target_val) else "func"
        target_text = f"Use :{ref_type}:`{target_val.__module__}.{target_val.__name__}` instead."
    lines.append(
        TEMPLATE_DOC_DEPRECATED
        % {
            "deprecated_in": dep_info.get("deprecated_in", ""),
            "remove_text": remove_text,
            "target_text": target_text,
        }
    )
    wrapped_fn.__doc__ = "\n".join(lines)


def deprecated(
    target: Union[bool, None, Callable],
    deprecated_in: str = "",
    remove_in: str = "",
    stream: Optional[Callable] = deprecation_warning,
    num_warns: int = 1,
    template_mgs: Optional[str] = None,
    args_mapping: Optional[dict[str, str]] = None,
    args_extra: Optional[dict[str, Any]] = None,
    skip_if: Union[bool, Callable] = False,
    update_docstring: bool = False,
) -> Callable:
    """Decorate a function or class with warning message and forward calls to target.

    This decorator marks a function or class as deprecated and can automatically forward
    all calls to a replacement implementation. It supports argument mapping, custom
    warning messages, and flexible warning control.

    Args:
        target: How to handle the deprecation:
            - ``Callable``: Forward all calls to this function/class
            - ``True``: Self-deprecation mode (deprecate arguments within same function)
            - ``None``: Warning-only mode (no forwarding, function body executes normally)
        deprecated_in: Version when the function was deprecated (e.g., "1.0.0").
            Default is empty string.
        remove_in: Version when the function will be removed (e.g., "2.0.0").
            Default is empty string.
        stream: Function to output warnings (default: :func:`deprecation_warning`, which is
            :func:`warnings.warn` with ``FutureWarning`` category).
            Set to ``None`` to disable warnings entirely.
        num_warns: Number of times to show warning per function or per deprecated argument:
            - ``1`` (default): Show warning once per function/argument
            - ``-1``: Show warning on every call
            - ``N > 1``: Show warning N times total
        template_mgs: Custom warning message template with format specifiers:
            - ``source_name``: Function name (e.g., "my_func")
            - ``source_path``: Full path (e.g., "module.my_func")
            - ``target_name``: Target function name (only for callable targets)
            - ``target_path``: Full target path (only for callable targets)
            - ``deprecated_in``: Value of deprecated_in parameter
            - ``remove_in``: Value of remove_in parameter
            - ``argument_map``: String showing argument mapping (for args deprecation only)
            Example: ``"v%(deprecated_in)s: `%(source_name)s` was deprecated."``
        args_mapping: Map or skip arguments when forwarding:
            - ``{'old_arg': 'new_arg'}``: Rename argument
            - ``{'old_arg': None}``: Skip argument (don't forward it)
            - ``{}``: Empty mapping (no remapping)
            Works with both ``target=Callable`` and ``target=True``.
        args_extra: Additional arguments to pass to target function when forwarding.
            Only used when target is a Callable.
            Example: ``{'new_required_arg': 42}``
        skip_if: Conditionally skip deprecation warning and forwarding:
            - ``bool``: Static condition (True = skip deprecation)
            - ``Callable``: Function returning bool (checked at runtime, must return bool)
            If condition is True, original function executes without warning.
        update_docstring: If True, automatically append deprecation information to
            the function's docstring in reStructuredText format. Useful for documentation
            generation tools like Sphinx.

    Returns:
        Decorator function that wraps the source function/class.

    Raises:
        TypeError: If skip_if is a callable that doesn't return a bool.
        TypeError: If arguments in args_mapping don't exist in target function
            and target doesn't accept **kwargs.

    Example:
        >>> # Basic forwarding
        >>> def new_func(x: int) -> int:
        ...     return x * 2
        >>> @deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
        ... def old_func(x: int) -> int:
        ...     pass

        >>> # Argument mapping
        >>> @deprecated(
        ...     target=new_func,
        ...     args_mapping={'old_name': 'new_name', 'unused': None}
        ... )
        ... def old_func(old_name: int, unused: str) -> int:
        ...     pass

        >>> # Self-deprecation
        >>> @deprecated(target=True, args_mapping={'old_arg': 'new_arg'})
        ... def my_func(old_arg: int = 0, new_arg: int = 0) -> int:
        ...     return new_arg * 2

    """

    def packing(source: Callable) -> Callable:
        needs_var_positional_fallback = target is None and any(
            param.kind == inspect.Parameter.VAR_POSITIONAL
            for param in _get_signature(source).parameters.values()
        )

        @wraps(source)
        def wrapped_fn(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            # check if user requested a skip
            shall_skip = skip_if() if callable(skip_if) else bool(skip_if)
            if not isinstance(shall_skip, bool):
                raise TypeError(f"User function 'skip_if' shall return bool, but got: {type(shall_skip)}")
            if shall_skip:
                return source(*args, **kwargs)

            nb_called = getattr(wrapped_fn, "_called", 0)
            setattr(wrapped_fn, "_called", nb_called + 1)
            # Preserve original kwargs for var-positional fallback before remapping.
            original_kwargs = dict(kwargs)
            # convert args to kwargs
            kwargs = _update_kwargs_with_args(source, args, kwargs)

            reason_callable = target is None or callable(target)
            reason_argument = {}
            if args_mapping and target:
                # Find which deprecated arguments were actually used in this call
                reason_argument = {a: b for a, b in args_mapping.items() if a in kwargs}
            # short cycle with no reason for redirect
            if not (reason_callable or reason_argument):
                # No forwarding needed: no target to forward to, and no deprecated args used
                return source(**kwargs)

            # warning per argument
            if reason_argument:
                # For argument deprecation, track warnings per argument
                # Use the minimum count across all deprecated args used in this call
                arg_warns = [getattr(wrapped_fn, f"_warned_{arg}", 0) for arg in reason_argument]
                nb_warned = min(arg_warns) if arg_warns else 0
            else:
                # For callable deprecation, track warnings per function
                nb_warned = getattr(wrapped_fn, "_warned", 0)

            # warn user only N times in lifetime or infinitely...
            if stream and (num_warns < 0 or nb_warned < num_warns):
                if reason_callable:
                    _raise_warn_callable(stream, source, target, deprecated_in, remove_in, template_mgs)
                    setattr(wrapped_fn, "_warned", nb_warned + 1)
                elif reason_argument:
                    _raise_warn_arguments(stream, source, reason_argument, deprecated_in, remove_in, template_mgs)
                    attrib_names = [f"_warned_{arg}" for arg in reason_argument]
                    for n in attrib_names:
                        setattr(wrapped_fn, n, getattr(wrapped_fn, n, 0) + 1)

            if reason_callable:
                kwargs = _update_kwargs_with_defaults(source, kwargs)
            if args_mapping and target:  # covers target as True and callable
                # Filter out arguments that should be skipped (mapped to None)
                args_skip = [arg for arg in args_mapping if not args_mapping[arg]]
                # Apply argument renaming: use mapped name if exists, otherwise keep original
                # Skip any arguments that were marked for skipping
                kwargs = {args_mapping.get(arg, arg): val for arg, val in kwargs.items() if arg not in args_skip}

            if args_extra and target:  # covers target as True and callable
                # update target argument by extra arguments
                kwargs.update(args_extra)

            if not callable(target):
                if needs_var_positional_fallback:
                    return source(*args, **original_kwargs)
                return source(**kwargs)

            # Validate that all arguments can be passed to target
            target_func = target.__init__ if inspect.isclass(target) else target
            target_args = [arg[0] for arg in get_func_arguments_types_defaults(target_func)]

            # get full args & name of varkw
            target_full_arg_spec = inspect.getfullargspec(target_func)
            varkw = target_full_arg_spec.varkw

            # Check for arguments that target doesn't accept
            missed = [arg for arg in kwargs if arg not in target_args]
            if missed and varkw is None:
                # Target doesn't accept these args and doesn't have **kwargs to catch them
                raise TypeError(f"Failed mapping of `{source.__name__}`, arguments missing in target source: {missed}")
            # all args were already moved to kwargs
            return target_func(**kwargs)

        # Set deprecation info for documentation
        setattr(
            wrapped_fn,
            "__deprecated__",
            {
                "deprecated_in": deprecated_in,
                "remove_in": remove_in,
                "target": target,
                "args_mapping": args_mapping,
            },
        )

        if update_docstring:
            _update_docstring_with_deprecation(wrapped_fn)

        return wrapped_fn

    return packing
