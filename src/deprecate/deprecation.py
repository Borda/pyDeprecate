"""Deprecation wrapper and utilities for marking deprecated code.

This module provides the main ``@deprecated`` decorator for marking functions and
methods as deprecated while optionally forwarding calls to their replacements.
Class-level deprecation is handled by :func:`~deprecate.proxy.deprecated_class`.

Key Components:
    - :func:`~deprecate.deprecation.deprecated`: Main decorator for deprecation with automatic call forwarding
    - Warning templates for different deprecation scenarios
    - Internal helpers for argument mapping and warning management

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import inspect
import sys
import warnings
from collections.abc import Mapping
from functools import cached_property, partial, wraps
from inspect import Parameter
from typing import Any, Callable, Literal, Optional, Union, cast
from warnings import warn

from deprecate._types import (
    DeprecationConfig,
    TargetMode,
    _CallPlan,
    _DeprecatedCallable,
    _get_entry_deprecated_in,
    _get_entry_remove_in,
    _has_deprecation_meta,
    _HasDeprecationMeta,
    _MappingValue,
    _resolve_mapping_redirect,
    _WrapperState,
)
from deprecate.docstring.inject import _update_docstring_with_deprecation, normalize_docstring_style
from deprecate.utils import _get_signature, get_func_arguments_types_defaults

_V1_BREAK_VERSION = "v1.0"
# caller → wrapped_fn → _raise_warn_callable/_raise_warn_arguments → _raise_warn → warnings.warn
_DEFAULT_STACKLEVEL_TO_CALLER: int = 4

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

ArgsMapping = dict[str, _MappingValue]

#: All ``%``-style placeholders accepted by the built-in warning templates.  Probing a user-supplied
#: ``template_mgs`` against this mapping at decoration time surfaces typos (``%(wrong_name_or_typo)s``) and
#: malformed conversion specifiers (``%(source_name)d``) before any call site ever triggers them.
_TEMPLATE_MGS_PROBE_ARGS: dict[str, str] = {
    "source_name": "x",
    "source_path": "x.y",
    "deprecated_in": "0.0",
    "remove_in": "1.0",
    "target_name": "x",
    "target_path": "x.y",
    "argument_map": "x -> y",
}


def _validate_template_mgs(template_mgs: Optional[str]) -> None:
    """Probe ``template_mgs`` with every documented placeholder, raising at decoration time on failure.

    Args:
        template_mgs: User-supplied warning message template, or ``None``.  ``None`` and empty strings are
            no-ops because the call sites already fall back to the built-in templates.

    Raises:
        ValueError: When ``template_mgs`` references an unknown ``%(...)s`` key, uses a malformed conversion
            specifier, or otherwise fails ``%``-formatting against the full placeholder set.

    """
    if not template_mgs:
        return
    try:
        template_mgs % _TEMPLATE_MGS_PROBE_ARGS
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid template_mgs: {exc!r}. Available placeholders: {list(_TEMPLATE_MGS_PROBE_ARGS)}"
        ) from exc


def _get_positional_params(params: list[inspect.Parameter]) -> list[inspect.Parameter]:
    """Filter positional-only and positional-or-keyword parameters."""
    return [param for param in params if param.kind in (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD)]


class _DeprecatedProperty(property):
    """``property`` subclass that re-wraps ``getter``/``setter``/``deleter`` results.

    Built-in ``property.setter`` / ``property.deleter`` construct a fresh plain ``property``
    from the existing accessors plus the newly supplied one — discarding any deprecation
    wrapping applied to the original accessors. Overriding ``getter``/``setter``/``deleter``
    to return another ``_DeprecatedProperty`` — wrapping the new accessor with the same packing
    closure stored in ``_wrap`` — preserves the deprecation warning on every subsequent rebind.

    Example:
        Chain-style rebinding works because ``_DeprecatedProperty.setter`` re-wraps the
        new accessor rather than rebuilding a plain ``property``:

            @deprecated(deprecated_in="1.0", remove_in="2.0")
            @property
            def value(self): ...

            @value.setter
            def value(self, v): ...  # setter() returns _DeprecatedProperty, not plain property

    Args:
        fget: Getter callable, or ``None``.
        fset: Setter callable, or ``None``.
        fdel: Deleter callable, or ``None``.
        doc: Property docstring; ``None`` defers to ``fget.__doc__``.
        _wrap: Required packing closure to re-apply on accessor rebinds.

    Attributes:
        _wrap: Closure that re-applies the surrounding ``@deprecated`` decoration to a
            new accessor; captures the same template/stacklevel/config as the original
            wrap. Required — always set by ``packing()``; never ``None``.

    Note:
        ``_DeprecatedProperty`` itself does **not** carry a ``__deprecated__`` attribute —
        that attribute lives on the individual wrapped accessors (``fget``, ``fset``, ``fdel``).
        ``find_deprecation_wrappers`` discovers properties via whichever non-``None`` accessor
        carries ``__deprecated__`` first. A setter-only property (``fget=None``) is discovered
        via ``fset``; a plain-getter property whose ``fget`` is not deprecated but whose ``fset``
        is deprecated is likewise discovered via ``fset``.

        **Typing**: ``getter``/``setter``/``deleter`` return ``_DeprecatedProperty`` (covariant
        narrowing of ``property``'s ``-> property`` annotation). Static type is preserved for
        variables typed ``_DeprecatedProperty``; variables typed ``property`` lose the narrowing
        and mypy infers the rebuilt accessor as plain ``property`` — chain inference still works
        at runtime via dynamic dispatch.

    """

    _wrap: Callable[[Callable], Callable]

    def __init__(
        self,
        fget: Optional[Callable] = None,
        fset: Optional[Callable] = None,
        fdel: Optional[Callable] = None,
        doc: Optional[str] = None,
        *,
        _wrap: Callable[[Callable], Callable],
    ) -> None:
        super().__init__(fget, fset, fdel, doc)
        # ``property`` exposes no slot for arbitrary attributes via ``__init__``, but it
        # *does* permit attribute assignment on subclass instances.
        self._wrap = _wrap

    def _rewrap(self, accessor: Optional[Callable]) -> Optional[Callable]:
        """Apply the stored ``_wrap`` closure to ``accessor`` when present."""
        if accessor is None:
            return accessor
        return self._wrap(accessor)

    def getter(self, fget: Callable) -> "_DeprecatedProperty":
        """Return a new ``_DeprecatedProperty`` whose ``fget`` is freshly wrapped."""
        return _DeprecatedProperty(self._rewrap(fget), self.fset, self.fdel, self.__doc__, _wrap=self._wrap)

    def setter(self, fset: Callable) -> "_DeprecatedProperty":
        """Return a new ``_DeprecatedProperty`` whose ``fset`` is freshly wrapped."""
        return _DeprecatedProperty(self.fget, self._rewrap(fset), self.fdel, self.__doc__, _wrap=self._wrap)

    def deleter(self, fdel: Callable) -> "_DeprecatedProperty":
        """Return a new ``_DeprecatedProperty`` whose ``fdel`` is freshly wrapped."""
        return _DeprecatedProperty(self.fget, self.fset, self._rewrap(fdel), self.__doc__, _wrap=self._wrap)


def _check_cross_class_method_target(source: Callable, target: Callable) -> None:
    """Raise ``TypeError`` when target is a method on a different class than source.

    Forwarding a class method to a method on a *different* class silently passes ``self`` of the wrong type, causing
    runtime attribute errors.  This guard detects the misconfiguration at decoration time by comparing the immediate
    class name extracted from each callable's ``__qualname__``.

    Qualname patterns and how they are handled:

    - ``"MyClass.method"``                   → class ``MyClass``
    - ``"outer.<locals>.MyClass.method"``    → class ``MyClass`` (class inside a function)
    - ``"outer.<locals>.<lambda>"``          → skipped; prefix ends with ``<locals>``
    - ``"base_sum_kwargs"``                  → skipped; no dot means module-level function

    False positive resolution — ``__qualname__`` is a display string, not an ownership API, so two scenarios used to
    yield spurious warnings.  Both are now handled:

    - **Decorators that rewrite ``__qualname__``** (e.g. a decorator applied before
      :func:`~deprecate.deprecated` that sets ``fn.__qualname__ = "OtherClass.method"``): resolved by reading
      ``__qualname__`` from the enclosing class
      body frame via :func:`sys._getframe`.  Python itself sets ``__qualname__`` in the class-body locals at
      class-definition time, so this value reflects the true enclosing class regardless of any decorator that
      mutated the source callable's ``__qualname__`` attribute.
    - **Metaclass-generated classes** (``type("Name", bases, ns)``, ``__init_subclass__``, or manual assignment
      producing qualnames like ``"FakeOwner.method"`` for unrelated types): resolved by verifying that the
      top-level class name in the qualname prefix actually exists in the callable's module globals.  When the
      referenced class does not exist, the qualname is unreliable and the guard returns without raising.

    Args:
        source: The callable being decorated with ``@deprecated``.
        target: The replacement callable supplied as the ``target`` argument.

    """
    # Constructor-to-constructor forwarding (__init__ → __init__) is always valid,
    # including across different classes, because PastCls inherits NewCls so `self` is a valid NewCls instance.
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

    # Fix 1 — decorator-rewriting FP: a decorator applied before @deprecated may have
    # mutated source.__qualname__.  Python sets __qualname__ in the class body's locals
    # at class-definition time, before any decorator runs, so the frame value is the
    # authoritative source class name.  Frame layout when this helper executes:
    #   0: _check_cross_class_method_target
    #   1: packing() (closure inside `deprecated`)
    #   2: enclosing class body (where `@deprecated(...)` is written)
    # The final-segment bracket filter rejects lambda/comprehension/genexp scopes
    # (whose qualname's last component is ``<lambda>`` / ``<listcomp>`` / etc.); class
    # bodies always end in a plain identifier, even when nested inside a function (e.g. ``"outer.<locals>.MyClass"``).
    try:
        frame_qn = sys._getframe(2).f_locals.get("__qualname__", "")
        if frame_qn and not frame_qn.rsplit(".", 1)[-1].startswith("<"):
            src_prefix = frame_qn
    except (ValueError, AttributeError):
        pass

    # Fix 2 — metaclass/synthetic-qualname FP: a target whose __qualname__ refers to a
    # class that does not actually exist in the target's module is unreliable; the guard
    # has no way to verify the cross-class claim, so it must skip rather than raise.
    # Applied to the target only — the source class is mid-definition when this helper
    # runs, so it cannot appear in module globals yet; applying the check to the source
    # would silently disable the guard for all module-level class definitions.
    tgt_top_class = tgt_prefix.split(".", 1)[0]
    tgt_module = sys.modules.get(getattr(target, "__module__", ""), None)
    if tgt_module is not None and not hasattr(tgt_module, tgt_top_class):
        return

    src_class_name = src_prefix.rsplit(".", 1)[-1]
    tgt_class_name = tgt_prefix.rsplit(".", 1)[-1]
    src_owner = f"{getattr(source, '__module__', '')}.{src_prefix}"
    tgt_owner = f"{getattr(target, '__module__', '')}.{tgt_prefix}"
    if src_owner == tgt_owner:
        return
    raise TypeError(
        f"Cannot use @deprecated on '{source.__qualname__}' with target "
        f"'{target.__qualname__}': cross-class method forwarding is not supported "
        f"because `self` would carry the wrong type. "
        f"The target must be a method on the same class ('{src_class_name}') "
        f"or a full class (use target={tgt_class_name} for class migration).",
    )


def _warn_stacking_misconfiguration(source: _HasDeprecationMeta, outer_target: Union[TargetMode, Callable]) -> None:
    """Emit ``UserWarning`` at decoration time for unsupported stacking combinations.

    Only called when ``source`` already carries ``__deprecated__`` metadata (i.e. is itself a
    ``@deprecated`` wrapper).  Supported combinations are silently accepted:

    - ``ARGS_REMAP`` (outer) + ``ARGS_REMAP`` (inner): multi-step arg renames across versions.
    - ``ARGS_REMAP`` (outer) + ``NOTIFY`` (inner): lifecycle pattern — rename args first, deprecate
      the whole function later.
    - ``NOTIFY`` (outer) + ``callable`` (inner): outer NOTIFY warns callers the function is going
      away; inner callable handles forwarding.

    Unsupported combinations (six cases) produce ``UserWarning`` at decoration time; all others
    are silently accepted.  The three supported combinations are: ``ARGS_REMAP`` (outer) +
    ``ARGS_REMAP`` (inner), ``ARGS_REMAP`` (outer) + ``NOTIFY`` (inner), and ``NOTIFY`` (outer) +
    ``callable`` (inner).

    """
    inner_target = source.__deprecated__.target
    name = source.__name__

    if callable(outer_target) and callable(inner_target):
        warnings.warn(
            f"'{name}' has a callable target stacked over another callable-target @deprecated."
            " Stacking a callable target over another callable target is not supported."
            " This will raise `TypeError` at call time."
            f" Will be `TypeError` in `{_V1_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=3,
        )
    elif callable(outer_target) and inner_target is TargetMode.ARGS_REMAP:
        warnings.warn(
            f"'{name}' has a callable target stacked over @deprecated(ARGS_REMAP)."
            " The arg-rename warning will not fire at call time; the inner layer is bypassed."
            " Collapse to: @deprecated(target=<callable>, args_mapping={...})."
            f" Will be `TypeError` in `{_V1_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=3,
        )
    elif callable(outer_target) and inner_target is TargetMode.NOTIFY:
        warnings.warn(
            f"'{name}' has a callable target stacked over @deprecated(NOTIFY)."
            " The inner function-deprecated warning will not fire at call time; the inner layer is bypassed"
            " while the callable target is still invoked."
            " Collapse to a single @deprecated(target=<callable>) and remove the inner @deprecated(NOTIFY)."
            f" Will be `TypeError` in `{_V1_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=3,
        )
    elif outer_target is TargetMode.ARGS_REMAP and callable(inner_target):
        warnings.warn(
            f"'{name}' has @deprecated(ARGS_REMAP) stacked over a callable-target @deprecated."
            " Update the inner @deprecated(target=<callable>, args_mapping={...}) instead of stacking."
            f" Will be `TypeError` in `{_V1_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=3,
        )
    elif outer_target is TargetMode.NOTIFY and inner_target is TargetMode.NOTIFY:
        warnings.warn(
            f"'{name}' has duplicate @deprecated(NOTIFY) layers."
            " Update the existing decorator's `deprecated_in`, `remove_in`, or `template_mgs` instead."
            f" Will be `TypeError` in `{_V1_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=3,
        )
    elif outer_target is TargetMode.NOTIFY and inner_target is TargetMode.ARGS_REMAP:
        warnings.warn(
            f"'{name}' has @deprecated(NOTIFY) stacked over @deprecated(ARGS_REMAP)."
            " Reverse the decorator order: put @deprecated(ARGS_REMAP, ...) outermost (on top)"
            " and @deprecated(NOTIFY, ...) below it."
            f" Will be `TypeError` in `{_V1_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=3,
        )
    elif (
        (outer_target is TargetMode.ARGS_REMAP and inner_target is TargetMode.ARGS_REMAP)
        or (outer_target is TargetMode.ARGS_REMAP and inner_target is TargetMode.NOTIFY)
        or (outer_target is TargetMode.NOTIFY and callable(inner_target))
    ):
        pass  # supported combinations — silently accepted
    else:
        warnings.warn(
            f"'{name}' has an unsupported @deprecated stacking combination."
            f" Will be `TypeError` in `{_V1_BREAK_VERSION}`.",
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


def _raise_warn(
    stream: Callable,
    source: Callable,
    template_mgs: str,
    stacklevel: int = _DEFAULT_STACKLEVEL_TO_CALLER,
    **extras: str,
) -> None:
    """Issue a deprecation warning using the specified stream and message template.

    This is the core warning issuer that formats and emits deprecation warnings.  It extracts source function metadata
    and combines it with provided template variables to generate the final warning message.

    Args:
        stream: Callable that outputs the warning (e.g., warnings.warn, logging.warning).
        source: The deprecated function/method being wrapped.
        template_mgs: Python format string with placeholders for message variables.
        stacklevel: Passed to ``warnings.warn`` so the warning points to the user's call site.  Default 4 accounts for
            the ``_raise_warn → _raise_warn_callable/_raise_warn_arguments → wrapped_fn → caller`` chain.
        **extras: Additional string values to substitute into the template (e.g., deprecated_in="1.0", remove_in="2.0").

    Note:
        Automatically extracts source_name and source_path from the source callable:
        - For regular functions: uses ``__name__``
        - For ``__init__`` methods: extracts class name from ``__qualname__``

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
    msg = template_mgs % msg_args
    try:
        stream(msg, stacklevel=stacklevel)
    except TypeError:
        # stream does not accept stacklevel (e.g. print, logging.warning, custom callables).
        stream(msg)


def _source_display_name(source: Callable) -> str:
    """Return display name: class name for ``__init__``, function name otherwise."""
    return source.__qualname__.split(".")[-2] if source.__name__ == "__init__" else source.__name__


def _raise_warn_callable(
    stream: Callable,
    source: Callable,
    target: Union[None, bool, Callable, TargetMode],
    deprecated_in: str,
    remove_in: str,
    template_mgs: Optional[str] = None,
    stacklevel: int = _DEFAULT_STACKLEVEL_TO_CALLER,
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
        stacklevel: Passed through to :func:`_raise_warn`; default 4 points to the user's call site.

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
        template_warn = TEMPLATE_WARNING_CALLABLE
    else:
        target_name, target_path = "", ""
        template_warn = TEMPLATE_WARNING_NO_TARGET
    _raise_warn(
        stream=stream,
        source=source,
        template_mgs=template_mgs or template_warn,
        stacklevel=stacklevel,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        target_name=target_name,
        target_path=target_path,
    )


def _raise_warn_arguments(
    stream: Callable,
    source: Callable,
    arguments: Mapping[str, _MappingValue],
    deprecated_in: str,
    remove_in: str,
    template_mgs: Optional[str] = None,
    stacklevel: int = _DEFAULT_STACKLEVEL_TO_CALLER,
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
        stacklevel: Passed through to :func:`_raise_warn`; default 4 points to the user's call site.

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
    # Group arguments by their effective (deprecated_in, remove_in) pair so that
    # DeprecationEntry per-arg version overrides produce separate, version-accurate warnings.
    groups: dict[tuple[str, str], list[tuple[str, _MappingValue]]] = {}
    for a, b in arguments.items():
        key = (_get_entry_deprecated_in(b, deprecated_in), _get_entry_remove_in(b, remove_in))
        groups.setdefault(key, []).append((a, b))
    for (entry_deprecated_in, entry_remove_in), args in groups.items():
        args_map = ", ".join(
            TEMPLATE_ARGUMENT_MAPPING % {"old_arg": a, "new_arg": str(_resolve_mapping_redirect(b))} for a, b in args
        )
        _raise_warn(
            stream,
            source,
            template_mgs or TEMPLATE_WARNING_ARGUMENTS,
            stacklevel=stacklevel,
            deprecated_in=entry_deprecated_in,
            remove_in=entry_remove_in,
            argument_map=args_map,
        )


def _build_call_plan(
    wrapper_fn: Callable[..., Any],
    source: Callable[..., Any],
    target: Union[bool, None, Callable[..., Any], TargetMode],
    normalized_target: Union[Callable[..., Any], TargetMode],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    dep_cfg: DeprecationConfig,
    stream: Optional[Callable[..., None]],
    num_warns: int,
    source_has_var_positional: bool,
    source_is_stacked: bool,
) -> _CallPlan:
    """Compute the dispatch plan shared by the sync and async wrappers inside :func:`deprecated`.

    Extracted verbatim from the body of ``wrapped_fn`` / ``async_wrapped_fn`` so that the wrappers differ only by
    ``await`` on the final source/target call.  All closure variables that the wrapper needs are passed explicitly so
    this helper has no dependency on the enclosing ``packing`` scope and can be unit-tested in isolation.

    Side effects (carried over from the original inline logic):

    - Mutates ``wrapper_fn._state`` — bumps ``called``, optionally bumps ``warned_calls`` / ``warned_args``, and sets
      ``warned_misconfigured`` on the first misconfiguration warning.
    - Emits the one-time misconfiguration ``UserWarning`` via :func:`warnings.warn` when ``dep_cfg.misconfigured`` is
      set, ``stream`` is non-``None``, and the state has not yet seen it.
    - Emits the deprecation warning through ``stream`` when the per-call quota allows it — callable-reason via
      :func:`_raise_warn_callable`, argument-rename-reason via :func:`_raise_warn_arguments`.

    Args:
        wrapper_fn: The wrapping function itself, used to read mutable ``_state`` via the
            :class:`_DeprecatedCallable` protocol.  Passing the wrapper instead of a bare state lets the wrapper
            preserve its existing ``cast(_DeprecatedCallable, ...)._state`` access pattern.
        source: The decorated callable.
        target: The raw ``target`` argument given to ``@deprecated`` — preserved for warning emission so callable
            targets that are classes are named by their user-facing name rather than ``__init__``.
        normalized_target: The normalised target (a :class:`TargetMode` member or callable) returned by
            :func:`_normalize_target`.
        args: The positional arguments the caller passed to the wrapper.
        kwargs: The keyword arguments the caller passed to the wrapper.
        dep_cfg: The frozen :class:`DeprecationConfig` for this wrapper.  ``args_mapping``, ``args_extra``,
            ``deprecated_in``, ``remove_in``, and ``template_mgs`` are all read from this object.
        stream: Warning stream (typically :func:`warnings.warn` partial), or ``None`` to suppress.
        num_warns: Maximum number of times to emit the warning per wrapper / per renamed argument.
        source_has_var_positional: ``True`` when ``source`` declares ``*args`` — affects fast-path dispatch in the
            wrapper but is also needed inside this helper for the short-circuit branch.
        source_is_stacked: ``True`` when ``source`` is itself a ``@deprecated`` wrapper.

    Returns:
        A :class:`_CallPlan` describing the resolved dispatch outcome.

    """
    state = cast(_DeprecatedCallable, wrapper_fn)._state
    state.called += 1
    if dep_cfg.misconfigured and stream and not state.warned_misconfigured:
        warnings.warn(
            f"'{source.__name__}' has an invalid deprecation configuration;"
            f" verify your `@deprecated(target=...)` arguments. Will be TypeError in {_V1_BREAK_VERSION}.",
            UserWarning,
            stacklevel=3,  # caller → wrapper_fn → _build_call_plan → warn
        )
        state.warned_misconfigured = True

    # *args sources need the unremapped tuple; remapping happens on kwargs only.
    original_kwargs = dict(kwargs)
    kwargs = _update_kwargs_with_args(source, args, kwargs)

    reason_callable = normalized_target is TargetMode.NOTIFY or callable(normalized_target)
    reason_argument: dict[str, _MappingValue] = {}
    if dep_cfg.args_mapping and (normalized_target is TargetMode.ARGS_REMAP or callable(normalized_target)):
        reason_argument = {a: b for a, b in dep_cfg.args_mapping.items() if a in kwargs}
    # Migrated callers (using the new arg name) produce empty reason_argument;
    # without the args_extra guard they short-circuit before extras are injected.
    # When source is a stacked @deprecated wrapper (e.g. ARGS_REMAP outer + NOTIFY inner),
    # do not short-circuit even with no reason — the inner layer may still need to run.
    if (
        not (reason_callable or reason_argument)
        and not (dep_cfg.args_extra and normalized_target is TargetMode.ARGS_REMAP)
        and not source_is_stacked
    ):
        return _CallPlan(
            short_circuit=True,
            original_kwargs=original_kwargs,
            resolved_kwargs=kwargs,
            reason_argument={},
            target_func=None,
        )

    if reason_argument:
        nb_warned = min((state.warned_args.get(arg, 0) for arg in reason_argument), default=0)
    else:
        nb_warned = state.warned_calls

    # +1 stacklevel: extraction added one frame (caller → wrapper_fn → _build_call_plan → _raise_warn_*)
    # over the previous in-wrapper call chain.  Async path has the same frame depth:
    # caller → coroutine `async_wrapped_fn` body → _build_call_plan → _raise_warn_* — the asyncio runner
    # frames sit *below* the caller and are skipped by warnings.warn's stacklevel walk.
    _stacklevel_to_caller = _DEFAULT_STACKLEVEL_TO_CALLER + 1
    if stream and (num_warns < 0 or nb_warned < num_warns):
        if reason_callable:
            # Use original `target` (not remapped normalized_target) so the warning
            # names the class (e.g. "NewCls") rather than "__init__".
            _raise_warn_callable(
                stream=stream,
                source=source,
                target=target,
                deprecated_in=dep_cfg.deprecated_in,
                remove_in=dep_cfg.remove_in,
                template_mgs=dep_cfg.template_mgs,
                stacklevel=_stacklevel_to_caller,
            )
            state.warned_calls += 1
        elif reason_argument:
            _raise_warn_arguments(
                stream=stream,
                source=source,
                arguments=reason_argument,
                deprecated_in=dep_cfg.deprecated_in,
                remove_in=dep_cfg.remove_in,
                template_mgs=dep_cfg.template_mgs,
                stacklevel=_stacklevel_to_caller,
            )
            for arg in reason_argument:
                state.warned_args[arg] = state.warned_args.get(arg, 0) + 1

    if reason_callable:
        # Source defaults for renamed args survive _update_kwargs_with_defaults and
        # would be forwarded under the new name, silently overriding the target's own
        # default. Drop only when the caller never supplied the old or new name.
        if dep_cfg.args_mapping and (normalized_target is TargetMode.ARGS_REMAP or callable(normalized_target)):
            _am = dep_cfg.args_mapping  # narrowed: non-None inside this branch; needed for nested closure
            caller_keys = set(kwargs)
            rename_targets: set[str] = {
                r for r in (_resolve_mapping_redirect(v) for v in _am.values()) if r is not None
            }
            rename_sources = set(_am)
            # For ARGS_REMAP, source IS the target; Python applies its own default
            # when the kwarg is absent, so treating rename_targets as target_defaults is safe.
            if callable(normalized_target):
                target_defaults = {
                    arg[0]
                    for arg in get_func_arguments_types_defaults(normalized_target)
                    if arg[2] is not inspect.Parameter.empty
                }
            else:
                target_defaults = rename_targets
            full_defaults = _update_kwargs_with_defaults(source, kwargs)

            def is_default_dropped(k: str) -> bool:
                remapped = k in rename_sources and _resolve_mapping_redirect(_am.get(k)) in target_defaults
                return k not in rename_targets and not remapped

            kwargs = {k: v for k, v in full_defaults.items() if k in caller_keys or is_default_dropped(k)}
        else:
            kwargs = _update_kwargs_with_defaults(source, kwargs)
    if dep_cfg.args_mapping and (normalized_target is TargetMode.ARGS_REMAP or callable(normalized_target)):
        args_skip = [arg for arg in dep_cfg.args_mapping if not _resolve_mapping_redirect(dep_cfg.args_mapping[arg])]
        kwargs = {
            (_resolve_mapping_redirect(dep_cfg.args_mapping.get(arg)) or arg): val
            for arg, val in kwargs.items()
            if arg not in args_skip
        }

    if dep_cfg.args_extra and (normalized_target is TargetMode.ARGS_REMAP or callable(normalized_target)):
        kwargs.update(dep_cfg.args_extra)

    # ``source_has_var_positional`` is accepted for symmetry with the wrapper closure: the helper itself does
    # not branch on it (the wrapper consumes it after reading the plan to decide whether to forward positional
    # args or kwargs to the source).  Keeping it in the signature lets future callers pass a single,
    # uniform argument set even if the helper later needs to switch on var-positional shape.
    target_func: Optional[Callable[..., Any]] = None
    if callable(normalized_target):
        target_func = _prepare_target_call(source, normalized_target, kwargs)

    return _CallPlan(
        short_circuit=False,
        original_kwargs=original_kwargs,
        resolved_kwargs=kwargs,
        reason_argument=reason_argument,
        target_func=target_func,
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

    For **generator functions** (``def gen(): yield``) and **async generator functions** (``async def gen(): yield``),
    the deprecation warning fires at call time — when the (async) generator object is created — not at first
    iteration.  The generator body executes lazily as normal when iterated (``next()`` / ``async for``).

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
            - ``0``: Suppress deprecation warnings emitted for the decorated function/argument
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
            :attr:`~deprecate._types.TargetMode.ARGS_REMAP` (with ``args_mapping``). Ignored when target is
            :attr:`~deprecate._types.TargetMode.NOTIFY`.
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
        UserWarning: If ``deprecated_in`` is absent, ``stream`` is not ``None``, no ``template_mgs`` is set,
            and the decorated source is not a class. Fired at decoration time (not call time) to catch missing
            version metadata early. Suppressed by passing ``stream=None`` or ``template_mgs``.

    Raises:
        TypeError: If the source is a class method and target is a method on a *different* class (cross-class
            method forwarding detected at decoration time via ``__qualname__`` comparison). Skipped silently
            when the target's qualname prefix names a class absent from the target's module globals.
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

    def packing(
        source: Union[Callable, classmethod, staticmethod, property, cached_property],
        _stacklevel: int = 2,
    ) -> Callable:
        # Order-agnostic @classmethod/@staticmethod: unwrap → deprecate inner function → rewrap.
        # Both @classmethod orders produce classmethod(deprecated_wrapper);
        # both @staticmethod orders produce staticmethod(deprecated_wrapper).
        if isinstance(source, (classmethod, staticmethod)):
            wrapped_inner = packing(source.__func__, _stacklevel + 1)
            return classmethod(wrapped_inner) if isinstance(source, classmethod) else staticmethod(wrapped_inner)  # type: ignore[return-value]
        # Order-agnostic @property: unwrap → deprecate fget/fset/fdel → rewrap preserving doc.
        # All three accessors are wrapped so attribute read, write, and delete each fire the warning.
        if isinstance(source, property):
            if isinstance(source, _DeprecatedProperty):
                # Double-decorating an already-deprecated property would wrap every accessor twice,
                # emitting two FutureWarnings per access and triggering _warn_stacking_misconfiguration
                # three times. Raise early with a clear message instead of silently double-wrapping.
                _accessor = source.fget or source.fset or source.fdel
                _src_name = _accessor.__qualname__ if _accessor is not None else "<property>"
                raise TypeError(
                    f"`@deprecated` cannot be applied twice to the already-deprecated property `{_src_name}`."
                    " Apply `@deprecated(...)` once; use `.setter()`/`.deleter()` rebinding for additional accessors."
                )
            if args_mapping:
                raise TypeError(f"`args_mapping` is not supported when decorating a `property`. Got: {args_mapping!r}.")
            if args_extra:
                raise TypeError(f"`args_extra` is not supported when decorating a `property`. Got: {args_extra!r}.")
            if callable(target):
                raise TypeError(
                    f"`target` as a callable is not supported when decorating a `property`. Got: {target!r}."
                    " Use `TargetMode.NOTIFY` or omit `target`."
                )
            if target is True or target is TargetMode.ARGS_REMAP:
                raise TypeError(
                    f"`target=TargetMode.ARGS_REMAP` (or legacy `True`) is not supported when decorating a `property`."
                    f" Got: {target!r}. Use `TargetMode.NOTIFY` or omit `target`."
                )
            if target is TargetMode.ATTRS_REMAP:
                raise TypeError(
                    "`target=TargetMode.ATTRS_REMAP` is not valid for `@deprecated` on a `property`."
                    " `TargetMode.ATTRS_REMAP` is a proxy-only mode — use "
                    "`deprecated_class(attrs_mapping=...)` to deprecate class attribute names."
                )
            # Guard against pre-deprecated individual accessors fed into property(...) then
            # decorated again: property(deprecated_fget) wrapped with @deprecated would double-wrap
            # fget, emitting two FutureWarnings per read. The _DeprecatedProperty guard above only
            # catches property-objects that are themselves already _DeprecatedProperty instances.
            for _acc_name, _acc in (("fget", source.fget), ("fset", source.fset), ("fdel", source.fdel)):
                if _acc is not None and _has_deprecation_meta(_acc):
                    raise TypeError(
                        f"`@deprecated` cannot wrap accessor `{getattr(_acc, '__qualname__', repr(_acc))}` of property"
                        f" `{_acc_name}` — it is already decorated with `@deprecated`."
                        " Apply `@deprecated` once per accessor."
                    )
            # Preserve explicit doc only when it differs from fget's doc (author override)
            # or when fget is absent (setter/deleter-only property with doc= supplied).
            # Otherwise pass None so property() inherits the deprecation-injected fget.__doc__.
            explicit_doc = source.__doc__ if (source.fget is None or source.__doc__ != source.fget.__doc__) else None

            # Closure captured on the returned ``_DeprecatedProperty`` so chain-style
            # ``@value.setter`` / ``@value.deleter`` can re-wrap freshly-supplied accessors
            # with the same packing config (template_mgs, stream, deprecated_in, remove_in,
            # num_warns, skip_if, stacklevel). args_mapping / args_extra / callable target are
            # blocked above by TypeError guards and are never reachable here.
            # Without this, ``property.setter(fn)`` would build a plain ``property`` whose new
            # accessor is raw — silently dropping the deprecation warning on attribute writes.
            _accessor_sl = _stacklevel + 1

            def _wrap_accessor(fn: Callable) -> Callable:
                """Apply packing to a property accessor with the adjusted stacklevel."""
                return packing(fn, _accessor_sl)

            return _DeprecatedProperty(  # type: ignore[return-value]
                packing(source.fget, _stacklevel + 1) if source.fget is not None else None,
                packing(source.fset, _stacklevel + 1) if source.fset is not None else None,
                packing(source.fdel, _stacklevel + 1) if source.fdel is not None else None,
                explicit_doc,
                _wrap=_wrap_accessor,
            )
        # Order-agnostic @cached_property: unwrap → deprecate func → rewrap.
        if isinstance(source, cached_property):
            return cached_property(packing(source.func, _stacklevel + 1))  # type: ignore[return-value]
        # Probe ``template_mgs`` against every documented placeholder so typos and malformed
        # conversion specifiers fail at decoration time instead of inside ``wrapped_fn``.
        _validate_template_mgs(template_mgs)
        # Note: template_mgs intentionally bypasses this guard — callers with custom templates
        # control their own messaging and may not rely on deprecated_in being present.
        if not deprecated_in and stream is not None and not template_mgs and not inspect.isclass(source):
            warnings.warn(
                f"`@deprecated` on `{source.__name__}` has no `deprecated_in` set."
                " Deprecation notices and generated documentation will omit the `deprecated_in` version."
                " Pass `deprecated_in` for a meaningful deprecation notice.",
                UserWarning,
                stacklevel=_stacklevel,
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
                warnings.warn(message, UserWarning, stacklevel=_stacklevel)

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
                forward_target = TargetMode._from_legacy(target, stacklevel=_stacklevel + 1)
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
                    forward_target,
                    source.__name__,
                    args_mapping=args_mapping,
                    args_extra=args_extra,
                    stacklevel=_stacklevel + 1,
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
        # ATTRS_REMAP is a proxy-only mode — it is meaningless on @deprecated functions/methods
        # because there is no attribute-access surface to intercept. Raise at decoration time
        # rather than silently producing a wrapper whose stored target has no runtime effect.
        if _target is TargetMode.ATTRS_REMAP:
            raise TypeError(
                f"`target=TargetMode.ATTRS_REMAP` is not valid for `@deprecated` on `{source.__name__}`. "
                "`TargetMode.ATTRS_REMAP` is a proxy-only mode — use "
                "`deprecated_class(attrs_mapping=...)` to deprecate class attribute names."
            )

        if _has_deprecation_meta(source):
            _source_is_stacked = True
            _warn_stacking_misconfiguration(source, _target)
        else:
            _source_is_stacked = False

        # Skip for legacy sentinels: _normalize_target already fired a FutureWarning;
        # re-running the guard here would report the wrong migration path.
        _function_misconfigured = False
        if isinstance(_target, TargetMode) and isinstance(target, TargetMode):
            _function_misconfigured = TargetMode._validate(
                _target, source.__name__, args_mapping=args_mapping, args_extra=args_extra, stacklevel=_stacklevel + 1
            )

        source_has_var_positional = any(
            param.kind == inspect.Parameter.VAR_POSITIONAL for param in _get_signature(source).parameters.values()
        )

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
            template_mgs=template_mgs,
        )
        _dep_cfg = dep_meta

        #
        # Known false-negatives of ``inspect.iscoroutinefunction`` — these sources silently receive the sync
        # wrapper, meaning ``await wrapper(...)`` will fail or return a bare coroutine:
        #   • async function wrapped by a decorator that does NOT propagate ``__wrapped__`` / use
        #     ``functools.wraps`` (``inspect.iscoroutinefunction`` walks ``__wrapped__``, not ``__call__``).
        #   • callable objects whose ``__call__`` is ``async def`` — use ``async def`` thin wrapper instead.
        #   • ``functools.partial(async_fn)`` on Python ≤ 3.11 (``partial`` does not copy ``__wrapped__``).
        # Workaround for all three: wrap the callable in a plain ``async def my_wrapper(*a, **kw): return
        # await callable(*a, **kw)`` before applying ``@deprecated``.
        if inspect.iscoroutinefunction(source):

            @wraps(source)
            async def async_wrapped_fn(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
                shall_skip = skip_if() if callable(skip_if) else bool(skip_if)
                if not isinstance(shall_skip, bool):
                    raise TypeError(f"User function 'skip_if' shall return bool, but got: {type(shall_skip)}")
                if shall_skip:
                    return await source(*args, **kwargs)

                # Read DeprecationConfig from the closure rather than re-reading
                # ``async_wrapped_fn.__deprecated__``: a PEP 702 ``typing_extensions.deprecated``
                # decorator stacked outside this one overwrites that attribute with a plain string.
                plan = _build_call_plan(
                    wrapper_fn=async_wrapped_fn,
                    source=source,
                    target=target,
                    normalized_target=_target,
                    args=args,
                    kwargs=kwargs,
                    dep_cfg=_dep_cfg,
                    stream=stream,
                    num_warns=num_warns,
                    source_has_var_positional=source_has_var_positional,
                    source_is_stacked=_source_is_stacked,
                )

                if plan.short_circuit:
                    if source_has_var_positional:
                        return await source(*args, **plan.original_kwargs)
                    return await source(**plan.resolved_kwargs)

                if plan.target_func is None:
                    if source_has_var_positional:
                        call_kwargs = plan.original_kwargs if not plan.reason_argument else plan.resolved_kwargs
                        return await source(*args, **call_kwargs)
                    return await source(**plan.resolved_kwargs)
                # Sync target under async source: invoke directly so callers can migrate from a sync to async
                # API in one step without forcing every legacy target to be redeclared ``async def``.
                if inspect.iscoroutinefunction(plan.target_func):
                    return await plan.target_func(**plan.resolved_kwargs)
                return plan.target_func(**plan.resolved_kwargs)

            async_wrapped_fn_typed = cast(_DeprecatedCallable, async_wrapped_fn)
            async_wrapped_fn_typed.__deprecated__ = dep_meta
            async_wrapped_fn_typed._state = _WrapperState()

            if update_docstring:
                _update_docstring_with_deprecation(async_wrapped_fn)

            return async_wrapped_fn

        # Async generator sources (``async def`` + ``yield``) fall through to the sync ``wrapped_fn`` below:
        # ``source(**kwargs)`` returns the async generator object without executing any body code — same as
        # sync generators.  Warning fires at sync call time; callers iterate with ``async for``.  The
        # ``iscoroutinefunction`` guard below does not fire for async gen targets (they are not coroutines).

        @wraps(source)
        def wrapped_fn(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            shall_skip = skip_if() if callable(skip_if) else bool(skip_if)
            if not isinstance(shall_skip, bool):
                raise TypeError(f"User function 'skip_if' shall return bool, but got: {type(shall_skip)}")
            if shall_skip:
                return source(*args, **kwargs)

            # Read DeprecationConfig from the closure rather than re-reading
            # ``wrapped_fn.__deprecated__``: a PEP 702 ``typing_extensions.deprecated``
            # decorator stacked outside this one overwrites that attribute with a plain
            # string, which then crashes on ``.misconfigured`` access.
            plan = _build_call_plan(
                wrapper_fn=wrapped_fn,
                source=source,
                target=target,
                normalized_target=_target,
                args=args,
                kwargs=kwargs,
                dep_cfg=_dep_cfg,
                stream=stream,
                num_warns=num_warns,
                source_has_var_positional=source_has_var_positional,
                source_is_stacked=_source_is_stacked,
            )

            if plan.short_circuit:
                if source_has_var_positional:
                    return source(*args, **plan.original_kwargs)
                return source(**plan.resolved_kwargs)

            if plan.target_func is None:
                if source_has_var_positional:
                    call_kwargs = plan.original_kwargs if not plan.reason_argument else plan.resolved_kwargs
                    return source(*args, **call_kwargs)
                return source(**plan.resolved_kwargs)
            if inspect.iscoroutinefunction(plan.target_func):
                raise TypeError(
                    f"Async target `{plan.target_func.__name__}` cannot be invoked from a sync wrapper."
                    f" Declare `{source.__name__}` as `async def`, or replace the target with a sync callable."
                )
            return plan.target_func(**plan.resolved_kwargs)

        wrapped_fn_typed = cast(_DeprecatedCallable, wrapped_fn)
        wrapped_fn_typed.__deprecated__ = dep_meta
        wrapped_fn_typed._state = _WrapperState()

        if update_docstring:
            _update_docstring_with_deprecation(wrapped_fn)

        return wrapped_fn

    return packing
