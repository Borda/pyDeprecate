"""Deprecation wrapper and utilities for marking deprecated code.

This module provides the main ``@deprecated`` decorator for marking functions and
methods as deprecated while optionally forwarding calls to their replacements.
Class-level deprecation is handled by :func:`deprecate.proxy.deprecated_class`.

Key Components:
    - :func:`~deprecate.deprecation.deprecated`: Main decorator for deprecation with automatic call forwarding
    - Warning templates for different deprecation scenarios
    - Internal helpers for argument mapping and warning management

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>
"""

import inspect
import warnings
from functools import partial, wraps
from inspect import Parameter
from typing import Any, Callable, Literal, Optional, Union, cast
from warnings import warn

from deprecate._types import DeprecationConfig, TargetMode, _DeprecatedCallable, _WrapperState
from deprecate.docstring.inject import _update_docstring_with_deprecation, normalize_docstring_style
from deprecate.utils import _get_signature, get_func_arguments_types_defaults

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
POSITIONAL_ONLY = Parameter.POSITIONAL_ONLY
POSITIONAL_OR_KEYWORD = Parameter.POSITIONAL_OR_KEYWORD
deprecation_warning = partial(warn, category=FutureWarning)

ArgsMapping = dict[str, Optional[str]]


def _get_positional_params(params: list[inspect.Parameter]) -> list[inspect.Parameter]:
    """Filter positional-only and positional-or-keyword parameters."""
    return [param for param in params if param.kind in (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD)]


def _check_cross_class_method_target(source: Callable, target: Callable) -> None:
    """Warn when target is a method on a different class than source.

    Forwarding a class method to a method on a *different* class silently passes ``self`` of the wrong type, causing
    runtime attribute errors.  This guard detects the misconfiguration at decoration time by comparing the immediate
    class name extracted from each callable's ``__qualname__``.

    Qualname patterns and how they are handled:

    - ``"MyClass.method"``                   → class ``MyClass``
    - ``"outer.<locals>.MyClass.method"``    → class ``MyClass`` (class inside a function)
    - ``"outer.<locals>.<lambda>"``          → skipped; prefix ends with ``<locals>``
    - ``"base_sum_kwargs"``                  → skipped; no dot means module-level function

    Known limitations — ``__qualname__`` is a display string, not an ownership API.  These scenarios may produce
    false positive warnings that are irresolvable at decoration time:

    - **Metaclass-generated classes**: ``type("Name", bases, ns)`` or ``__init_subclass__`` may assign qualnames
      like ``"Outer.Inner"`` even for unrelated types; the guard treats them as methods on ``Outer`` and warns.
    - **Decorators that rewrite ``__qualname__``**: a decorator applied before ``@deprecated`` that sets
      ``fn.__qualname__ = "SomeClass.method"`` (e.g. some dataclass field descriptors) causes false positives.

    Both cases are irresolvable at decoration time without inspecting the live call frame.  Suppress false positives
    via ``warnings.filterwarnings("ignore", category=UserWarning, module=...)``.

    Args:
        source: The callable being decorated with ``@deprecated``.
        target: The replacement callable supplied as the ``target`` argument.

    """
    # Constructor-to-constructor forwarding (__init__ → __init__) is always valid,
    # including across different classes, because PastCls inherits NewCls so `self`
    # is a valid NewCls instance.
    if source.__name__ == "__init__" and getattr(target, "__name__", "") == "__init__":
        return
    src_qualname = getattr(source, "__qualname__", "")
    tgt_qualname = getattr(target, "__qualname__", "")
    src_parts = src_qualname.rsplit(".", 1)
    tgt_parts = tgt_qualname.rsplit(".", 1)
    if len(src_parts) != 2 or len(tgt_parts) != 2:
        return
    src_prefix, tgt_prefix = src_parts[0], tgt_parts[0]
    # Skip nested functions / lambdas whose prefix ends with "<locals>"
    if src_prefix.endswith("<locals>") or tgt_prefix.endswith("<locals>"):
        return
    src_class_name = src_prefix.rsplit(".", 1)[-1]
    tgt_class_name = tgt_prefix.rsplit(".", 1)[-1]
    src_owner = f"{getattr(source, '__module__', '')}.{src_prefix}"
    tgt_owner = f"{getattr(target, '__module__', '')}.{tgt_prefix}"
    if src_owner == tgt_owner:
        return
    warnings.warn(
        f"Cannot use @deprecated on '{source.__qualname__}' with target "
        f"'{target.__qualname__}': cross-class method forwarding is not supported "
        f"because `self` would carry the wrong type. "
        f"The target must be a method on the same class ('{src_class_name}') "
        f"or a full class (use target={tgt_class_name} for class migration). "
        "Suppress via warnings.filterwarnings("
        "'ignore', message='.*cross-class method forwarding', category=UserWarning).",
        UserWarning,
        stacklevel=3,
    )


def _normalize_target(
    source: Callable,
    target: Union[bool, None, Callable, TargetMode],
) -> Union[TargetMode, Callable]:
    """Normalise the effective target callable before the wrapper closure captures it.

    Converts legacy sentinel values to :class:`~deprecate._types.TargetMode` enum members with a deprecation
    warning, and handles class targets:

    Legacy sentinel conversion (emits warning at decoration time):

    - ``target=None`` → :attr:`TargetMode.NOTIFY` + :class:`FutureWarning`
    - ``target=True`` → :attr:`TargetMode.ARGS_REMAP` + :class:`FutureWarning`
    - ``target=False`` → :attr:`TargetMode.NOTIFY` + :class:`UserWarning`

    Class target handling (unchanged from previous behaviour):

    1. ``source`` is ``__init__`` → remap ``target=NewCls`` to ``target=NewCls.__init__``
       (constructor forwarding; ``self`` is the new instance so the call is valid).
    2. ``source`` is a class method (non-``__init__``) → raise :exc:`TypeError`; passing a class as target for a
       bound method silently passes ``self`` of the wrong type.
    3. ``source`` is a module-level function → keep ``target=NewCls`` as-is; calling ``NewCls(**kwargs)`` creates
       a new instance directly.

    Args:
        source: The callable being decorated with ``@deprecated``.
        target: Raw ``target`` argument from the ``@deprecated`` call.

    Returns:
        Normalised target suitable for use inside ``wrapped_fn``.

    Raises:
        TypeError: When a class target is used on a non-``__init__`` class method.

    """
    # --- Legacy sentinel conversion (v0.8 compat shim; removed in v1.0) ---
    # stacklevel=4: warn() → _from_legacy() → _normalize_target() → packing() → @decorator application site
    if target is None or isinstance(target, bool):
        return TargetMode._from_legacy(target, stacklevel=4)

    # --- TargetMode enum pass-through ---
    if isinstance(target, TargetMode):
        return target

    # --- Class target handling ---
    if inspect.isclass(target):
        src_qualname = getattr(source, "__qualname__", "")
        src_parts = src_qualname.rsplit(".", 1)
        source_is_class_method = len(src_parts) == 2 and not src_parts[0].endswith("<locals>")
        if source.__name__ == "__init__":
            return target.__init__
        if source_is_class_method:
            raise TypeError(
                f"Cannot use a class as `target` for @deprecated on '{source.__qualname__}'. "
                f"Constructor forwarding via target=ClassName is only supported on `__init__`. "
                f"Use target={target.__name__}.__init__ explicitly, or apply the decorator to `__init__`."
            )
        return target  # module-level function: instantiate directly

    # --- Callable target (function/method) ---
    return target


def _prepare_target_call(
    source: Callable,
    target: Callable,
    kwargs: dict[str, Any],
) -> Callable:
    """Validate mapped keyword arguments and return the target callable.

    ``packing()`` normalises the target before ``wrapped_fn`` runs — class targets are remapped to
    ``target.__init__`` — so by the time this function is called, ``target`` is always a plain callable, never a class.

    Args:
        source: Deprecated callable being wrapped.
        target: Target callable to invoke (shall not be a class).
        kwargs: Keyword arguments after mapping and defaults.

    Returns:
        ``target`` unchanged, after validating that it accepts ``kwargs``.

    Example:
        >>> from deprecate.deprecation import _prepare_target_call
        >>> def source(a: int, b: int) -> int:
        ...     return a + b
        >>> def target(a: int, b: int) -> int:
        ...     return a - b
        >>> _prepare_target_call(source, target, {"c": 1})
        Traceback (most recent call last):
        ...
        TypeError: Failed mapping of `source`, arguments not accepted by target: ['c']

    """
    target_args = [arg[0] for arg in get_func_arguments_types_defaults(target)]
    target_full_arg_spec = inspect.getfullargspec(target)
    var_args = target_full_arg_spec.varargs
    var_kw = target_full_arg_spec.varkw

    missed = [arg for arg in kwargs if arg not in target_args]
    if missed and var_kw is None:
        if var_args is None:
            raise TypeError(f"Failed mapping of `{source.__name__}`, arguments not accepted by target: {missed}")
        raise TypeError(
            f"Failed mapping of `{source.__name__}`, arguments not accepted by target (target accepts *args but "
            f"these keyword arguments are not allowed): {missed}"
        )
    return target


def _update_kwargs_with_args(func: Callable, fn_args: tuple[Any, ...], fn_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Convert positional arguments to keyword arguments using function signature.

    This helper function takes positional arguments and converts them to keyword arguments by matching them with
    parameter names from the function signature.  This enables consistent argument handling in the deprecation wrapper.

    Args:
        func: Function whose signature provides parameter names.
        fn_args: Tuple of positional arguments passed to the function.
        fn_kwargs: Dictionary of keyword arguments already passed.

    Returns:
        Dictionary combining converted positional arguments and existing kwargs, where positional args are now mapped
        to their parameter names.  Conversion stops when encountering var-positional parameters (``*args``) because
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
        required_positional_params = [param for param in positional_params if param.default is inspect.Parameter.empty]
        if len(required_positional_params) == len(positional_params):
            raise TypeError(
                f"{func.__qualname__}() takes {len(positional_params)} positional argument(s) but got "
                f"{len(fn_args)} positional argument(s)"
            )
        raise TypeError(
            f"{func.__qualname__}() takes {len(required_positional_params)} to {len(positional_params)} "
            f"positional argument(s) but got {len(fn_args)} positional argument(s)"
        )
    updated_kwargs = dict(fn_kwargs)
    for index, arg in enumerate(fn_args):
        if index >= len(params):
            break
        param = params[index]
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            break
        if param.kind in (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD):
            updated_kwargs[param.name] = arg
    return updated_kwargs


def _update_kwargs_with_defaults(func: Callable, fn_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Merge function default values with provided keyword arguments.

    This helper fills in default parameter values from the function signature for any parameters not explicitly
    provided.  Provided kwargs take precedence over defaults.

    Args:
        func: Function whose signature provides default parameter values.
        fn_kwargs: Dictionary of keyword arguments provided by caller.

    Returns:
        Dictionary with defaults merged with provided kwargs, where provided values override defaults.

    Example:
        >>> from pprint import pprint
        >>> def example_func(a=1, b=2, c=3): pass
        >>> pprint(_update_kwargs_with_defaults(example_func, {'b': 20}))
        {'a': 1, 'b': 20, 'c': 3}

    Note:
        Parameters without defaults (inspect.Parameter.empty) are not included in the result.

    """
    func_arg_type_val = get_func_arguments_types_defaults(func)
    # fill by source defaults
    fn_defaults = {arg[0]: arg[2] for arg in func_arg_type_val if arg[2] != inspect.Parameter.empty}
    return dict(list(fn_defaults.items()) + list(fn_kwargs.items()))


def _raise_warn(stream: Callable, source: Callable, template_mgs: str, **extras: str) -> None:
    """Issue a deprecation warning using the specified stream and message template.

    This is the core warning issuer that formats and emits deprecation warnings.  It extracts source function metadata
    and combines it with provided template variables to generate the final warning message.

    Args:
        stream: Callable that outputs the warning (e.g., warnings.warn, logging.warning).
        source: The deprecated function/method being wrapped.
        template_mgs: Python format string with placeholders for message variables.
        **extras: Additional string values to substitute into the template (e.g., deprecated_in="1.0", remove_in="2.0").

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
    source_name = _source_display_name(source)
    source_path = f"{source.__module__}.{source_name}"
    msg_args = dict(source_name=source_name, source_path=source_path, **extras)
    stream(template_mgs % msg_args)


def _source_display_name(source: Callable) -> str:
    """Return display name: class name for __init__, function name otherwise."""
    return source.__qualname__.split(".")[-2] if source.__name__ == "__init__" else source.__name__


def _raise_warn_callable(
    stream: Callable,
    source: Callable,
    target: Union[None, bool, Callable, TargetMode],
    deprecated_in: str,
    remove_in: str,
    template_mgs: Optional[str] = None,
) -> None:
    """Issue deprecation warning for callable (function/class) deprecation.

    This specialized warning issuer handles deprecation of entire functions or classes that are being replaced by new
    implementations.  It automatically determines the appropriate message template based on whether a target callable
    is specified.

    Args:
        stream: Callable that outputs the warning (e.g., warnings.warn, logging.warning).
        source: The deprecated function/method being wrapped.
        target: The replacement implementation:
            - Callable: Forward to this function/class
            - None: No forwarding (warning only mode)
            - bool: Not applicable for this function (use _raise_warn_arguments instead)
        deprecated_in: Version when the source was marked deprecated (e.g., "1.0.0").
        remove_in: Version when the source will be removed (e.g., "2.0.0").
        template_mgs: Custom message template. If None, uses :data:`TEMPLATE_WARNING_CALLABLE` when a target
            callable is provided, otherwise :data:`TEMPLATE_WARNING_NO_TARGET`.

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
        template = template_mgs or TEMPLATE_WARNING_CALLABLE
    else:
        target_name, target_path = "", ""
        template = template_mgs or TEMPLATE_WARNING_NO_TARGET
    _raise_warn(
        stream=stream,
        source=source,
        template_mgs=template,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        target_name=target_name,
        target_path=target_path,
    )


def _raise_warn_arguments(
    stream: Callable,
    source: Callable,
    arguments: ArgsMapping,
    deprecated_in: str,
    remove_in: str,
    template_mgs: Optional[str] = None,
) -> None:
    """Issue deprecation warning for deprecated function arguments.

    This specialized warning issuer handles deprecation of specific function parameters that are being renamed or
    removed.  It generates a mapping string showing the old-to-new argument names.

    Args:
        stream: Callable that outputs the warning (e.g., warnings.warn, logging.warning).
        source: The function/method whose arguments are deprecated.
        arguments: Mapping from deprecated argument names to new names (e.g., ``{'old_arg': 'new_arg',
            'removed_arg': None}``).
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
    args_map = ", ".join([TEMPLATE_ARGUMENT_MAPPING % {"old_arg": a, "new_arg": str(b)} for a, b in arguments.items()])
    _raise_warn(
        stream,
        source,
        template_mgs or TEMPLATE_WARNING_ARGUMENTS,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        argument_map=args_map,
    )


def deprecated(
    target: Union[bool, None, Callable, TargetMode] = TargetMode.NOTIFY,
    deprecated_in: str = "",
    remove_in: str = "",
    stream: Optional[Callable] = deprecation_warning,
    num_warns: int = 1,
    template_mgs: Optional[str] = None,
    args_mapping: Optional[ArgsMapping] = None,
    args_extra: Optional[dict[str, Any]] = None,
    skip_if: Union[bool, Callable] = False,
    update_docstring: bool = False,
    docstring_style: Literal["auto", "rst", "mkdocs", "markdown"] = "auto",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate a function/method with warning message and forward calls to target.

    This decorator marks a function or method as deprecated and can automatically forward all calls to a replacement
    implementation.  It supports argument mapping, custom warning messages, and flexible warning control.

    Args:
        target: How to handle the deprecation. Defaults to :attr:`~deprecate.TargetMode.NOTIFY` (warn-only; source
            body executes unchanged). Pass an explicit value to forward calls or remap arguments:

            - ``Callable``: Forward all calls to this callable (function, method, or class target). The
              decorated function's body is **not executed** under normal forwarding — use ``pass`` or ``...``
              as the body. **Exception**: when ``skip_if`` evaluates ``True`` at call time, the source body
              executes as a fallback, so keep a working implementation if you combine ``target=Callable``
              with ``skip_if``.
            - :attr:`~deprecate.TargetMode.ARGS_REMAP` (or legacy ``True``): Self-deprecation — deprecate argument
              names only, remapping them within the same function body
            - :attr:`~deprecate.TargetMode.NOTIFY` (default): Warning-only mode — no forwarding, source body executes
              normally

            Omitting ``target`` is the preferred way to express warn-only deprecation.  Passing ``target=None``
            is a legacy synonym that also resolves to :attr:`~deprecate.TargetMode.NOTIFY` but emits a
            :class:`FutureWarning` directing you to use the enum form.

        deprecated_in: Version when the function was deprecated (e.g., "1.0.0"). Default is empty string.
        remove_in: Version when the function will be removed (e.g., "2.0.0"). Default is empty string.
        stream: Function to output warnings (default: :func:`~deprecate.deprecation.deprecation_warning`, which is
            :func:`warnings.warn` with ``FutureWarning`` category). Set to ``None`` to disable warnings entirely.
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
        args_extra: Additional arguments merged into kwargs before the call. Used when target is a Callable or
            ``TargetMode.ARGS_REMAP`` (with ``args_mapping``). Ignored when target is ``TargetMode.NOTIFY``.
            Example: ``{'new_required_arg': 42}``
        skip_if: Conditionally skip deprecation warning and forwarding:
            - ``bool``: Static condition (True = skip deprecation)
            - ``Callable``: Function returning bool (checked at runtime, must return bool)
            If condition is True, original function executes without warning.
        update_docstring: If True, automatically inject a deprecation notice into the function's docstring (inserted
            before Google/NumPy-style sections when present, otherwise appended at the end).
        docstring_style: Output style for injected deprecation notice when ``update_docstring=True``. Supported values:
            - ``"auto"`` (default): Automatically choose a style based on the current environment (e.g., loaded
              modules, CLI/tooling context). This may resolve to either ``"rst"`` or ``"mkdocs"``/``"markdown"``
              at decoration time.
            - ``"rst"``: Explicitly force Sphinx-style ``.. deprecated::`` directive.
            - ``"mkdocs"`` or ``"markdown"``: Explicitly force a Markdown admonition of the form
              ``!!! warning "Deprecated in X"``.
            Validated eagerly at decoration time regardless of ``update_docstring``.

    Returns:
        Decorator function that wraps the source function/method.

    Warns:
        UserWarning: If applied directly to a class. The decorator delegates to
            :func:`~deprecate.proxy.deprecated_class` and emits this warning. Use ``@deprecated_class()`` directly
            to suppress it. Suppressed when ``stream=None``.
        UserWarning: If the source is a class method and target appears to be a method on a *different* class
            (cross-class method forwarding). The heuristic uses ``__qualname__`` and may produce false positives for
            metaclass-generated or decorator-rewritten qualnames — suppress with ``warnings.filterwarnings``. This
            warning is emitted with ``stacklevel=3``; this skips the internal ``packing`` frame, so the attributed
            line is the ``@deprecated`` decoration site rather than the helper internals.
            For CI enforcement (to restore the pre-0.8 hard-failure behaviour), escalate this warning to an error::

                import warnings
                warnings.filterwarnings("error", message=".*cross-class method forwarding", category=UserWarning)

        UserWarning: If ``deprecated_in`` is absent, ``stream`` is not ``None``, no ``template_mgs`` is set,
            and the decorated source is not a class. Fired at decoration time (not call time) to catch missing
            version metadata early. Suppressed by passing ``stream=None`` or ``template_mgs``.

    Raises:
        TypeError: If skip_if is a callable that doesn't return a bool.
        TypeError: If arguments in args_mapping don't exist in target function and target doesn't accept **kwargs.

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
        >>> from deprecate import TargetMode
        >>> @deprecated(target=TargetMode.ARGS_REMAP, args_mapping={'old_arg': 'new_arg'})
        ... def my_func(old_arg: int = 0, new_arg: int = 0) -> int:
        ...     return new_arg * 2

        >>> # Warn-only (default — no target needed)
        >>> @deprecated(deprecated_in="1.0", remove_in="2.0")
        ... def legacy_func(x: int) -> int:
        ...     return x

    """
    normalized_docstring_style = normalize_docstring_style(docstring_style)

    def packing(source: Callable) -> Callable:
        # Note: template_mgs intentionally bypasses this guard — callers with custom templates
        # control their own messaging and may not rely on deprecated_in being present.
        if not deprecated_in and stream is not None and not template_mgs and not inspect.isclass(source):
            warnings.warn(
                f"`@deprecated` on `{source.__name__}` has no `deprecated_in` set."
                " Deprecation notices and generated documentation will omit the `deprecated_in` version."
                " Pass `deprecated_in` for a meaningful deprecation notice.",
                UserWarning,
                stacklevel=2,
            )
        if inspect.isclass(source):
            import importlib

            proxy_module = importlib.import_module("deprecate.proxy")
            deprecated_class = proxy_module.deprecated_class

            message = (
                f"Direct use of `@deprecated` on class `{source.__name__}` is deprecated since `v0.6.0`."
                " Use `@deprecated_class(...)` instead. This will become a `TypeError` in a future release."
            )
            if target is not None and not inspect.isclass(target) and not isinstance(target, TargetMode):
                message += (
                    " Note: non-class `target` values are ignored when deprecating classes;"
                    " use `@deprecated_class(target=...)` instead."
                )
            if stream is not None:
                warnings.warn(message, UserWarning, stacklevel=2)

            # _DeprecatedProxy auto-promotes ``None+args_mapping`` to ARGS_REMAP and reads
            # ``misconfigured`` from its own ``target is False`` check — by that point
            # the original sentinel is already gone.
            class_misconfigured = target is False
            if isinstance(target, TargetMode):
                forward_target: Any = target
            elif callable(target) and inspect.isclass(target):
                forward_target = target
            elif target is None or isinstance(target, bool):
                # None/True/False on a class is a class-misconfiguration, not a callable
                # deprecation sentinel — the class misconfig UserWarning is the relevant signal.
                forward_target = TargetMode._from_legacy(target, stacklevel=3)
            else:
                forward_target = TargetMode.NOTIFY

            # Capture all misconfig signals *before* rewriting forward_args_mapping / forward_args_extra
            # so we can forward them via ``_misconfigured_override`` instead of mutating the frozen
            # ``DeprecationConfig`` after construction. NOTIFY + (args_mapping or args_extra) is the
            # second misconfig source the proxy can no longer detect once we strip those fields.
            notify_misconfig = forward_target is TargetMode.NOTIFY and bool(args_mapping or args_extra)
            force_misconfigured = class_misconfigured or notify_misconfig

            # Proxy metadata is immutable after construction; stale mapping persists to audit tools.
            forward_args_mapping = args_mapping
            forward_args_extra = args_extra
            if forward_target is TargetMode.NOTIFY:
                TargetMode._validate(
                    forward_target, source.__name__, args_mapping=args_mapping, args_extra=args_extra, stacklevel=3
                )
                forward_args_mapping = None
                forward_args_extra = None

            return deprecated_class(
                target=forward_target,
                deprecated_in=deprecated_in,
                remove_in=remove_in,
                num_warns=num_warns,
                stream=stream,
                args_mapping=forward_args_mapping,
                args_extra=forward_args_extra,
                update_docstring=update_docstring,
                docstring_style=docstring_style,
                _misconfigured_override=force_misconfigured,
            )(source)
        # Cross-class guard runs before remapping; class targets skip it because
        # constructor forwarding (target=NewCls on __init__) is always valid.
        if callable(target) and not inspect.isclass(target):
            _check_cross_class_method_target(source, target)
        _target = _normalize_target(source, target)

        # Skip for legacy sentinels: _normalize_target already fired a FutureWarning;
        # re-running the guard here would report the wrong migration path.
        _function_misconfigured = False
        if isinstance(_target, TargetMode) and isinstance(target, TargetMode):
            _function_misconfigured = TargetMode._validate(
                _target, source.__name__, args_mapping=args_mapping, args_extra=args_extra, stacklevel=3
            )

        source_has_var_positional = any(
            param.kind == inspect.Parameter.VAR_POSITIONAL for param in _get_signature(source).parameters.values()
        )

        @wraps(source)
        def wrapped_fn(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            shall_skip = skip_if() if callable(skip_if) else bool(skip_if)
            if not isinstance(shall_skip, bool):
                raise TypeError(f"User function 'skip_if' shall return bool, but got: {type(shall_skip)}")
            if shall_skip:
                return source(*args, **kwargs)

            state = cast(_DeprecatedCallable, wrapped_fn)._state
            state.called += 1
            dep_cfg = cast(_DeprecatedCallable, wrapped_fn).__deprecated__
            if dep_cfg.misconfigured and stream and not state.warned_misconfigured:
                warnings.warn(
                    f"'{source.__name__}' has an invalid deprecation configuration;"
                    " verify your `@deprecated(target=...)` arguments. Will be TypeError in v1.0.",
                    UserWarning,
                    stacklevel=2,
                )
                state.warned_misconfigured = True
            # *args sources need the unremapped tuple; remapping happens on kwargs only.
            original_kwargs = dict(kwargs)
            kwargs = _update_kwargs_with_args(source, args, kwargs)

            reason_callable = _target is TargetMode.NOTIFY or callable(_target)
            reason_argument = {}
            if args_mapping and (_target is TargetMode.ARGS_REMAP or callable(_target)):
                reason_argument = {a: b for a, b in args_mapping.items() if a in kwargs}
            # Migrated callers (using the new arg name) produce empty reason_argument;
            # without the args_extra guard they short-circuit before extras are injected.
            if not (reason_callable or reason_argument) and not (args_extra and _target is TargetMode.ARGS_REMAP):
                return source(**kwargs)

            if reason_argument:
                nb_warned = min((state.warned_args.get(arg, 0) for arg in reason_argument), default=0)
            else:
                nb_warned = state.warned_calls

            if stream and (num_warns < 0 or nb_warned < num_warns):
                if reason_callable:
                    # Use original `target` (not remapped _target) so the warning
                    # names the class (e.g. "NewCls") rather than "__init__".
                    _raise_warn_callable(stream, source, target, deprecated_in, remove_in, template_mgs)
                    state.warned_calls += 1
                elif reason_argument:
                    _raise_warn_arguments(stream, source, reason_argument, deprecated_in, remove_in, template_mgs)
                    for arg in reason_argument:
                        state.warned_args[arg] = state.warned_args.get(arg, 0) + 1

            if reason_callable:
                # Source defaults for renamed args survive _update_kwargs_with_defaults and
                # would be forwarded under the new name, silently overriding the target's own
                # default. Drop only when the caller never supplied the old or new name.
                if args_mapping and (_target is TargetMode.ARGS_REMAP or callable(_target)):
                    caller_keys = set(kwargs)
                    rename_targets = {v for v in args_mapping.values() if v}
                    rename_sources = set(args_mapping)
                    # For ARGS_REMAP, source IS the target; Python applies its own default
                    # when the kwarg is absent, so treating rename_targets as target_defaults is safe.
                    if callable(_target):
                        target_defaults = {
                            arg[0]
                            for arg in get_func_arguments_types_defaults(_target)
                            if arg[2] is not inspect.Parameter.empty
                        }
                    else:
                        target_defaults = rename_targets
                    full_defaults = _update_kwargs_with_defaults(source, kwargs)

                    def is_default_dropped(k: str) -> bool:
                        remapped = k in rename_sources and args_mapping.get(k) in target_defaults
                        return k not in rename_targets and not remapped

                    kwargs = {k: v for k, v in full_defaults.items() if k in caller_keys or is_default_dropped(k)}
                else:
                    kwargs = _update_kwargs_with_defaults(source, kwargs)
            if args_mapping and (_target is TargetMode.ARGS_REMAP or callable(_target)):
                args_skip = [arg for arg in args_mapping if not args_mapping[arg]]
                kwargs = {(args_mapping.get(arg) or arg): val for arg, val in kwargs.items() if arg not in args_skip}

            if args_extra and (_target is TargetMode.ARGS_REMAP or callable(_target)):
                kwargs.update(args_extra)

            if not callable(_target):
                if source_has_var_positional:
                    call_kwargs = original_kwargs if not reason_argument else kwargs
                    return source(*args, **call_kwargs)
                return source(**kwargs)
            target_func = _prepare_target_call(source, _target, kwargs)
            return target_func(**kwargs)

        # Enum-normalised target stored so audit does not re-derive from raw sentinel.
        # Class targets kept verbatim: the class→__init__ remap is call-time only;
        # audit and docstring consumers expect the user-facing class, not __init__.
        if target is None or isinstance(target, bool):
            stored_target: Any = TargetMode._from_legacy(target, stacklevel=None)
        elif isinstance(target, TargetMode):
            stored_target = target
        else:
            stored_target = target
        misconfigured = target is False or _function_misconfigured
        dep_meta = DeprecationConfig(
            deprecated_in=deprecated_in,
            remove_in=remove_in,
            name=source.__name__,
            target=stored_target,
            args_mapping=args_mapping,
            args_extra=args_extra,
            misconfigured=misconfigured,
            docstring_style=normalized_docstring_style,
        )
        wrapped_fn_typed = cast(_DeprecatedCallable, wrapped_fn)
        wrapped_fn_typed.__deprecated__ = dep_meta
        wrapped_fn_typed._state = _WrapperState()

        if update_docstring:
            _update_docstring_with_deprecation(wrapped_fn)

        return wrapped_fn

    return packing
