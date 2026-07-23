"""Target resolution and call-plan engine for callable deprecation.

Decoration-time target normalization and guards (``_normalize_target``, cross-class checks, positional-only detection,
stacking-misconfiguration warnings) plus the call-time dispatch core (``_build_call_plan`` and the sync/async invokers)
that forwards a deprecated call to its replacement.  :mod:`deprecate.routine` builds its ``packing`` closures and the
public ``deprecated`` / ``deprecated_callable`` decorators on top of these helpers.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import inspect
import sys
import warnings
from functools import cached_property
from inspect import Parameter
from typing import Any, Callable, Optional, Union, cast

from deprecate._types import (
    DeprecationConfig,
    TargetMode,
    _CallPlan,
    _DeprecatedCallable,
    _HasDeprecationMeta,
)
from deprecate.messaging import (
    _DEFAULT_STACKLEVEL_TO_CALLER,
    _consume_warn_budget,
    _raise_warn_arguments,
    _raise_warn_callable,
)
from deprecate.utils import _apply_args_mapping_collisions, _get_signature, get_func_arguments_types_defaults

_MAJOR_BREAK_VERSION = "v1.0"

POSITIONAL_ONLY = Parameter.POSITIONAL_ONLY
POSITIONAL_OR_KEYWORD = Parameter.POSITIONAL_OR_KEYWORD


def _get_positional_params(params: list[inspect.Parameter]) -> list[inspect.Parameter]:
    """Filter positional-only and positional-or-keyword parameters."""
    return [param for param in params if param.kind in (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD)]


def _reject_bare_decorator(source: Any) -> None:  # noqa: ANN401
    """Raise a clear ``TypeError`` when ``@deprecated`` was applied without parentheses.

    Bare ``@deprecated`` makes Python call ``deprecated(source)`` — binding the decorated object to
    ``target`` — and return the ``packing`` decorator; the first real call then arrives in ``packing`` with
    the *call argument* as ``source`` (e.g. ``old(5)`` → ``packing(5)``).  A non-callable, non-descriptor
    ``source`` therefore signals the missing-parentheses mistake, so raise here instead of leaking a downstream
    ``AttributeError: 'int' object has no attribute '__name__'``.

    Args:
        source: The object ``packing`` received as its decoration target.

    """
    if not callable(source) and not isinstance(source, (classmethod, staticmethod, property, cached_property)):
        raise TypeError(
            f"`@deprecated` must be called with parentheses, e.g. "
            f"`@deprecated(target=..., deprecated_in=..., remove_in=...)`; got a non-callable "
            f"`{type(source).__name__}` as the decoration target, which usually means `@deprecated` "
            f"was used without arguments."
        )


# Descriptor source types the callable path handles via ``_packing_descriptor`` even though some are
# neither ``callable`` nor carry ``__name__`` (``property``/``cached_property``). They must be exempt
# from the dispatcher's "plain object" reject guard so class/instance-style misuse stays the only reject.
_DESCRIPTOR_SOURCE_TYPES = (classmethod, staticmethod, property, cached_property)

# Reject message for a non-callable / callable-without-``__name__`` source handed to ``@deprecated``.
_PLAIN_OBJECT_REJECT = "cannot deprecate a plain object with `@deprecated` — use `deprecated_instance(obj, ...)`"


def _reject_non_callable_source(source: Any, target: Any) -> None:  # noqa: ANN401
    """Raise ``TypeError`` for a non-callable, or callable-without-``__name__``, decoration source.

    Third dispatch bucket: a non-callable object, or a callable instance lacking ``__name__`` (a
    ``__call__`` object or ``functools.partial``), would crash downstream at ``source.__name__``. Reject
    up front with actionable guidance. Descriptors are exempt — the callable path handles them via
    ``_packing_descriptor`` even though some are neither ``callable`` nor carry ``__name__``.

    Disambiguates two look-alike shapes: a bare ``@deprecated`` (no parens) binds the user's callable to
    ``target`` and this call arrives with the call ARGUMENT as ``source`` — a callable ``target`` that is
    not a ``TargetMode`` is that signal, so it delegates to :func:`_reject_bare_decorator`. Otherwise
    (dispatcher default ``target`` is a ``TargetMode``) it is a genuine attempt to deprecate a plain
    object → point at ``deprecated_instance``.

    Args:
        source: The object ``packing`` received as its decoration target.
        target: The raw ``target`` argument given to ``@deprecated``, used only to disambiguate the
            bare-decorator misuse case.

    Raises:
        TypeError: When ``source`` is neither a descriptor, nor a named callable.

    """
    if not isinstance(source, _DESCRIPTOR_SOURCE_TYPES) and (not callable(source) or not hasattr(source, "__name__")):
        if callable(target) and not isinstance(target, TargetMode):
            _reject_bare_decorator(source)  # raises the missing-parentheses TypeError
        raise TypeError(_PLAIN_OBJECT_REJECT)


def _find_class_body_qualname(max_depth: int = 10) -> str:
    """Return the ``__qualname__`` of the nearest enclosing class body on the call stack.

    Python populates ``__qualname__`` (alongside ``__module__``) in the class-body execution namespace at
    class-definition time.  A fixed :func:`sys._getframe` depth is fragile — descriptor-decorated methods
    (``@property``/``@classmethod``/``@staticmethod`` under ``@deprecated``) insert extra ``_packing_descriptor``
    and ``packing`` frames, so the class body sits deeper than the plain-callable case.  This bounded walk
    searches upward for the first frame whose locals carry both ``__qualname__`` and ``__module__`` (the
    signature of a class body) instead of assuming a single layout, and returns ``""`` when none is found.

    Args:
        max_depth: Maximum number of parent frames to inspect (safety bound against unbounded walks).

    """
    for depth in range(1, max_depth + 1):
        try:
            frame = sys._getframe(depth)
        except ValueError:
            break
        qualname = frame.f_locals.get("__qualname__", "")
        # Class-body namespaces expose both ``__qualname__`` and ``__module__``; a function frame that merely
        # has a local named ``__qualname__`` (e.g. a decorator rewrite) lacks ``__module__`` in its locals.
        if qualname and "__module__" in frame.f_locals and not qualname.rsplit(".", 1)[-1].startswith("<"):
            return qualname
    return ""


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
    # authoritative source class name.  A bounded upward walk locates the class-body frame
    # regardless of how many descriptor/packing frames sit between here and it (see
    # :func:`_find_class_body_qualname`) — a fixed ``sys._getframe(2)`` silently missed the
    # class body for descriptor-decorated methods, disabling the guard for them.
    frame_qn = _find_class_body_qualname()
    if frame_qn:
        src_prefix = frame_qn

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


def _warn_stacking_misconfiguration(
    source: _HasDeprecationMeta, outer_target: Union[TargetMode, Callable], stacklevel: int = 3
) -> None:
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
            f" Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=stacklevel,
        )
    elif callable(outer_target) and inner_target is TargetMode.ARGS_REMAP:
        warnings.warn(
            f"'{name}' has a callable target stacked over @deprecated(ARGS_REMAP)."
            " The arg-rename warning will not fire at call time; the inner layer is bypassed."
            " Collapse to: @deprecated(target=<callable>, args_mapping={...})."
            f" Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=stacklevel,
        )
    elif callable(outer_target) and inner_target is TargetMode.NOTIFY:
        warnings.warn(
            f"'{name}' has a callable target stacked over @deprecated(NOTIFY)."
            " The inner function-deprecated warning will not fire at call time; the inner layer is bypassed"
            " while the callable target is still invoked."
            " Collapse to a single @deprecated(target=<callable>) and remove the inner @deprecated(NOTIFY)."
            f" Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=stacklevel,
        )
    elif outer_target is TargetMode.ARGS_REMAP and callable(inner_target):
        warnings.warn(
            f"'{name}' has @deprecated(ARGS_REMAP) stacked over a callable-target @deprecated."
            " Update the inner @deprecated(target=<callable>, args_mapping={...}) instead of stacking."
            f" Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=stacklevel,
        )
    elif outer_target is TargetMode.NOTIFY and inner_target is TargetMode.NOTIFY:
        warnings.warn(
            f"'{name}' has duplicate @deprecated(NOTIFY) layers."
            " Update the existing decorator's `deprecated_in`, `remove_in`, or `message_template` instead."
            f" Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=stacklevel,
        )
    elif outer_target is TargetMode.NOTIFY and inner_target is TargetMode.ARGS_REMAP:
        warnings.warn(
            f"'{name}' has @deprecated(NOTIFY) stacked over @deprecated(ARGS_REMAP)."
            " Reverse the decorator order: put @deprecated(ARGS_REMAP, ...) outermost (on top)"
            " and @deprecated(NOTIFY, ...) below it."
            f" Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=stacklevel,
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
            f" Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=stacklevel,
        )


def _normalize_target(
    source: Callable,
    target: Union[bool, None, Callable, TargetMode, staticmethod, classmethod],
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

    Descriptor unwrapping:

    - ``target=staticmethod(fn)`` → unwrapped to ``fn`` (``.__func__``); enables ``target=bbb`` inside a class
      body where ``bbb`` is still the raw ``staticmethod`` descriptor, not yet bound.
    - ``target=classmethod(fn)`` → unwrapped to ``fn`` (``.__func__``); same pattern, but only when ``source``
      accepts ``cls`` as its first positional parameter.  Asymmetric usage (non-classmethod source targeting a
      ``classmethod`` descriptor) raises :exc:`TypeError` at decoration time because ``cls`` would be missing at
      call time otherwise.

    Note on the ``classmethod`` guard: the check ``src_params[0] != "cls"`` relies on the Python convention that
    classmethods name their first parameter ``cls`` (PEP 8 / all major linters enforce this).  If your classmethod
    uses an unconventional name (e.g. ``klass``) or ``source`` is a ``functools.partial`` whose first argument is
    already bound, the guard may raise spuriously.  In that case pass the unwrapped target explicitly:
    ``target=your_classmethod.__func__``.

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

    # --- Descriptor unwrap (staticmethod/classmethod passed from class body before binding) ---
    if isinstance(target, (staticmethod, classmethod)):
        if isinstance(target, classmethod):
            # Guard: classmethod.__func__ expects cls as its first arg.  If source does not
            # accept cls, it will never be supplied → TypeError at call time.  Raise early.
            try:
                src_params = list(inspect.signature(source).parameters)
            except (ValueError, TypeError):
                src_params = []
            if not src_params or src_params[0] != "cls":
                func_name = getattr(target.__func__, "__name__", "target")
                raise TypeError(
                    f"@deprecated(target=<classmethod descriptor>) on '{source.__name__}': "
                    "descriptor targets require the source to accept a leading class argument "
                    "(typically named 'cls') so it can be forwarded to the replacement. "
                    f"Either make '{source.__name__}' a @classmethod, or pass a bound target like "
                    f"'target=<YourClass>.{func_name}' after the class is defined."
                )
        return target.__func__

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


def _precompute_target_facts(
    target: Union[Callable, TargetMode],
) -> tuple[frozenset[str], bool, bool]:
    """Extract decoration-time-stable signature facts of a forwarding target.

    Computed once at decoration time and stored on :class:`~deprecate._types.DeprecationConfig`
    so the call-time kwarg validation never re-inspects the target (previously an uncached
    ``inspect.getfullargspec`` on every forwarded call).

    Args:
        target: Normalised target — a :class:`TargetMode` member or a callable.  Non-callables
            return empty/``False`` facts because the dispatcher never forwards to them.

    Returns:
        Tuple ``(all_param_names, accepts_var_positional, accepts_var_keyword)`` where
        ``all_param_names`` mirrors :func:`get_func_arguments_types_defaults` (every signature
        parameter, including ``*args`` / ``**kwargs`` names).

    """
    if not callable(target):
        return frozenset(), False, False
    try:
        params = _get_signature(target).parameters
    except (TypeError, ValueError):
        return frozenset(), False, False
    all_names = frozenset(params)
    accepts_var_positional = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params.values())
    accepts_var_keyword = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    return all_names, accepts_var_positional, accepts_var_keyword


def _prepare_target_call(
    source: Callable,
    target: Callable,
    kwargs: dict[str, Any],
    *,
    target_arg_names: Optional[frozenset[str]] = None,
    accepts_var_positional: bool = False,
    accepts_var_keyword: bool = False,
) -> Callable:
    """Validate mapped keyword arguments and return the target callable.

    ``packing()`` normalises the target before ``wrapped_fn`` runs — class targets are remapped to
    ``target.__init__`` — so by the time this function is called, ``target`` is always a plain callable, never a class.

    Args:
        source: Deprecated callable being wrapped.
        target: Target callable to invoke (shall not be a class).
        kwargs: Keyword arguments after mapping and defaults.
        target_arg_names: Pre-computed target parameter names (see :func:`_precompute_target_facts`).  When
            ``None`` the facts are derived from ``target`` on the spot — the decorator path always passes the
            cached values so the fallback only runs for direct/test callers.
        accepts_var_positional: Whether ``target`` declares ``*args`` (from the same pre-computed facts).
        accepts_var_keyword: Whether ``target`` declares ``**kwargs`` (from the same pre-computed facts).

    Returns:
        ``target`` unchanged, after validating that it accepts ``kwargs``.

    Example:
        >>> from deprecate._dispatch import _prepare_target_call
        >>> def source(a: int, b: int) -> int:
        ...     return a + b
        >>> def target(a: int, b: int) -> int:
        ...     return a - b
        >>> _prepare_target_call(source, target, {"c": 1})
        Traceback (most recent call last):
        ...
        TypeError: Failed mapping of `source`, arguments not accepted by target: ['c']

    """
    if target_arg_names is None:
        target_arg_names, accepts_var_positional, accepts_var_keyword = _precompute_target_facts(target)

    missed = [arg for arg in kwargs if arg not in target_arg_names]
    if missed and not accepts_var_keyword:
        if not accepts_var_positional:
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


def _split_positional_only_kwargs(
    param_order: tuple[str, ...],
    resolved_kwargs: dict[str, Any],
    positional_only: frozenset[str],
    *,
    consumed: int = 0,
) -> tuple[list[Any], dict[str, Any]]:
    """Split ``resolved_kwargs`` into positional args and remaining kwargs for a callable with POSITIONAL_ONLY params.

    Extracts values for ``positional_only`` names from ``resolved_kwargs`` in parameter-declaration order
    so they can be forwarded positionally.  Also extracts ``self``/``cls`` when they are the *first*
    parameter in ``param_order`` and present in ``resolved_kwargs``, so that unbound ``__init__`` /
    classmethod targets receive the instance in the first positional slot rather than as a keyword
    argument.  The first-parameter restriction avoids incorrectly extracting a non-receiver parameter
    that happens to be named ``self`` or ``cls``.  Remaining entries stay in the returned kwargs dict.

    Positional binding at the call site is by *slot*, not by name: when an earlier positional-only
    parameter is absent from ``resolved_kwargs`` (a *gap* — safe only when nothing follows, since
    defaults trail), a value present for a *later* positional-only parameter would silently bind to
    the wrong slot.  That case raises :class:`TypeError` instead of misbinding.

    Args:
        param_order: Pre-computed parameter-name sequence of the callable in declaration order.
            Stored on :attr:`~deprecate._types.DeprecationConfig.target_positional_only_order` /
            :attr:`~deprecate._types.DeprecationConfig.source_positional_only_order` to avoid
            re-calling ``inspect.signature`` on every dispatch.
        resolved_kwargs: Full kwargs dict assembled by :func:`_build_call_plan` (or the proxy's
            mapped kwargs).
        positional_only: Names of POSITIONAL_ONLY parameters — O(1) membership check.
        consumed: Number of leading slots already filled by caller-supplied positional args
            (used by the proxy call path, which keeps the caller's ``*args`` positional); those
            slots are skipped and never treated as gaps.

    Returns:
        Tuple of ``(pos_args, kw_args)`` where ``pos_args`` contains the instance (``self``/``cls``,
        when present) followed by positional-only param values in declaration order, and ``kw_args``
        contains the remaining kwargs.

    Raises:
        TypeError: When a positional-only name is present in ``resolved_kwargs`` while an earlier
            positional-only parameter is absent — forwarding positionally would misbind the value.

    """
    kw_args = dict(resolved_kwargs)
    pos_args: list[Any] = []
    missing: Optional[str] = None
    for i, name in enumerate(param_order):
        if i < consumed:
            continue
        # POSITIONAL_ONLY params form a contiguous prefix of the signature, so the first
        # non-extractable name ends the scan — no positional-only names can follow it.
        if name not in positional_only and not (i == 0 and name in {"self", "cls"}):
            break
        if name not in kw_args:
            missing = name  # gap — safe only while no later positional-only value shows up
            continue
        if missing is not None:
            raise TypeError(
                f"Cannot forward `{name}` positionally: the earlier positional-only parameter"
                f" `{missing}` was not supplied, so `{name}`'s value would bind to the wrong slot."
                f" Supply `{missing}` explicitly, or remove `/` from the target signature."
            )
        pos_args.append(kw_args.pop(name))
    return pos_args, kw_args


def _build_call_plan(  # noqa: C901, PLR0912
    wrapper_fn: Callable[..., Any],
    source: Callable[..., Any],
    target: Union[bool, None, Callable[..., Any], TargetMode, staticmethod, classmethod],
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
            ``deprecated_in``, ``remove_in``, and ``message_template`` are all read from this object.
            Precedence when keys collide: explicit new-name kwarg wins over the remapped old-name value;
            ``args_extra`` wins over both (it is merged last).
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
            f" verify your `@deprecated(target=...)` arguments. Will be TypeError in {_MAJOR_BREAK_VERSION}.",
            UserWarning,
            stacklevel=3,  # caller → wrapper_fn → _build_call_plan → warn
        )
        state.warned_misconfigured = True

    # *args sources need the unremapped tuple; remapping happens on kwargs only.
    original_kwargs = dict(kwargs)
    kwargs = _update_kwargs_with_args(source, args, kwargs)

    reason_callable = normalized_target is TargetMode.NOTIFY or callable(normalized_target)
    reason_argument: dict[str, Optional[str]] = {}
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

    # +1 stacklevel: extraction added one frame (caller → wrapper_fn → _build_call_plan → _raise_warn_*)
    # over the previous in-wrapper call chain.  Async path has the same frame depth:
    # caller → coroutine `async_wrapped_fn` body → _build_call_plan → _raise_warn_* — the asyncio runner
    # frames sit *below* the caller and are skipped by warnings.warn's stacklevel walk.
    _stacklevel_to_caller = _DEFAULT_STACKLEVEL_TO_CALLER + 1
    # Double-checked fast path: avoid the lock when the warn budget is clearly exhausted.
    # Under CPython the int read is atomic (GIL); after num_warns=1 fires once,
    # warned_calls >= num_warns on every subsequent call — no benefit from acquiring the lock.
    # The authoritative check-then-increment still runs under the lock when a warning may fire.
    # Argument-specific budgets are not pre-checked (rare path, dict lookup not worth the complexity).
    should_warn = False
    if stream and (num_warns < 0 or reason_argument or state.warned_calls < num_warns):
        with state.lock:
            should_warn = _consume_warn_budget(state, num_warns, reason_callable, reason_argument)
    if should_warn:
        assert stream is not None  # noqa: S101 — should_warn is only set while holding the lock when stream is truthy
        if reason_callable:
            # Use original `target` (not remapped normalized_target) so the warning
            # names the class (e.g. "NewCls") rather than "__init__".
            _raise_warn_callable(
                stream=stream,
                source=source,
                target=target,
                deprecated_in=dep_cfg.deprecated_in,
                remove_in=dep_cfg.remove_in,
                message_template=dep_cfg.message_template,
                stacklevel=_stacklevel_to_caller,
            )
        elif reason_argument:
            _raise_warn_arguments(
                stream=stream,
                source=source,
                arguments=reason_argument,
                deprecated_in=dep_cfg.deprecated_in,
                remove_in=dep_cfg.remove_in,
                message_template=dep_cfg.message_template,
                stacklevel=_stacklevel_to_caller,
            )

    if reason_callable:
        # Source defaults for renamed args survive _update_kwargs_with_defaults and would be forwarded
        # under the new name, silently overriding the target's own default. Drop only the *renamed*
        # old-arg default when the caller supplied neither the old nor the new name.
        # NOTE: non-renamed shared parameters intentionally keep their source default — the source
        # signature is the contract the caller migrated from, so its default is forwarded (see
        # ``decorated_sum`` / ``test_functions.py::test_default``). Dropping these too
        # would conflict with the tested behaviour and is deliberately NOT applied.
        if dep_cfg.args_mapping and (normalized_target is TargetMode.ARGS_REMAP or callable(normalized_target)):
            _am = dep_cfg.args_mapping  # narrowed: non-None inside this branch; needed for nested closure
            caller_keys = set(kwargs)
            rename_targets: set[str] = {r for r in _am.values() if r is not None}
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

            def is_source_default_kept(k: str) -> bool:
                # A renamed old-arg default is stale when the target has its own default → drop it;
                # everything else (non-renamed params) keeps its source default.
                renamed_with_target_default = k in rename_sources and _am.get(k) in target_defaults
                return k not in rename_targets and not renamed_with_target_default

            kwargs = {k: v for k, v in full_defaults.items() if k in caller_keys or is_source_default_kept(k)}
        else:
            kwargs = _update_kwargs_with_defaults(source, kwargs)
    if dep_cfg.args_mapping and (normalized_target is TargetMode.ARGS_REMAP or callable(normalized_target)):
        args_skip = [arg for arg in dep_cfg.args_mapping if not dep_cfg.args_mapping[arg]]
        # caller → wrapper_fn → _build_call_plan → _apply_args_mapping_collisions → warn = stacklevel 4
        _explicit_new = _apply_args_mapping_collisions(
            dep_cfg.args_mapping, kwargs, args_skip, source.__name__, stream, stacklevel=4
        )
        kwargs = {(dep_cfg.args_mapping.get(arg) or arg): val for arg, val in kwargs.items() if arg not in args_skip}
        if _explicit_new:
            kwargs.update(_explicit_new)

    if dep_cfg.args_extra and (normalized_target is TargetMode.ARGS_REMAP or callable(normalized_target)):
        kwargs.update(dep_cfg.args_extra)

    # ``source_has_var_positional`` is accepted for symmetry with the wrapper closure: the helper itself does
    # not branch on it (the wrapper consumes it after reading the plan to decide whether to forward positional
    # args or kwargs to the source).  Keeping it in the signature lets future callers pass a single,
    # uniform argument set even if the helper later needs to switch on var-positional shape.
    target_func: Optional[Callable[..., Any]] = None
    if callable(normalized_target):
        # ``None`` means facts were not precomputed (manual DeprecationConfig construction or proxy path);
        # _prepare_target_call recomputes from target. ``frozenset()`` is a valid cached value for zero-arg
        # targets — gate on identity (``is not None``), not truthiness, to preserve the zero-arg cache hit.
        _cached_target_names = dep_cfg.target_all_param_names
        target_func = _prepare_target_call(
            source,
            normalized_target,
            kwargs,
            target_arg_names=_cached_target_names,
            accepts_var_positional=dep_cfg.target_accepts_var_positional,
            accepts_var_keyword=dep_cfg.target_accepts_var_keyword,
        )

    return _CallPlan(
        short_circuit=False,
        original_kwargs=original_kwargs,
        resolved_kwargs=kwargs,
        reason_argument=reason_argument,
        target_func=target_func,
    )


def _detect_positional_only(
    target: Union[Callable, TargetMode],
    source: Callable,
    stream: Optional[Callable],
    warn_stacklevel: int,
) -> tuple[frozenset[str], tuple[str, ...]]:
    """Inspect *target* for POSITIONAL_ONLY parameters and emit a ``UserWarning`` when found.

    Called at decoration time so the warning fires once and the results are stored in
    :class:`~deprecate._types.DeprecationConfig` for the call-time dispatcher.

    Args:
        target: Normalised target from :func:`_normalize_target` — may be a
            :class:`TargetMode` or a callable.  Non-callables return empty results immediately.
        source: The decorated callable; used only in the warning message.
        stream: Warning stream; when ``None`` the warning is suppressed.
        warn_stacklevel: ``stacklevel`` forwarded to ``warnings.warn``.  Caller passes
            ``packing``'s ``_stacklevel + 1`` to account for the extra frame.

    Returns:
        Tuple of ``(target_positional_only, target_positional_only_order)`` where
        ``target_positional_only`` is the frozenset of POSITIONAL_ONLY param names and
        ``target_positional_only_order`` is the full parameter-name tuple in declaration order.

    """
    if not callable(target):
        return frozenset(), ()
    try:
        tgt_sig = inspect.signature(target)
    except (TypeError, ValueError):
        return frozenset(), ()
    target_positional_only = frozenset(
        name for name, p in tgt_sig.parameters.items() if p.kind is inspect.Parameter.POSITIONAL_ONLY
    )
    target_positional_only_order: tuple[str, ...] = tuple(tgt_sig.parameters.keys()) if target_positional_only else ()
    warn_set = target_positional_only - {"self", "cls"}
    if warn_set and stream is not None:
        warnings.warn(
            f"`@deprecated(target={getattr(target, '__name__', repr(target))!r})` on"
            f" `{source.__name__}`: target parameter(s)"
            f" {sorted(warn_set)!r} are POSITIONAL_ONLY and cannot be"
            " forwarded as kwargs. Calls will pass these values positionally."
            " Consider removing `/` from the target signature.",
            UserWarning,
            stacklevel=warn_stacklevel,
        )
    return target_positional_only, target_positional_only_order


def _resolve_stored_target(
    target: Union[bool, None, Callable, TargetMode, staticmethod, classmethod],
) -> Union[TargetMode, Callable]:
    """Normalise *target* for storage in :class:`~deprecate._types.DeprecationConfig`.

    Converts legacy bool/None sentinels to :class:`TargetMode` members and strips
    descriptor wrappers so audit tools see the underlying callable rather than a raw
    descriptor object.

    Args:
        target: Raw ``target`` argument from ``@deprecated``.

    Returns:
        Normalised value suitable for ``DeprecationConfig.target``.

    Note:
        Class targets are kept verbatim — :func:`_normalize_target` remaps
        ``class → __init__`` at decoration time for the wrapper closure, but the stored
        value must preserve the user-facing class so audit and docstring consumers see
        the original target.

    """
    if target is None or isinstance(target, bool):
        # Enum-normalised target stored so audit does not re-derive from raw sentinel.
        return TargetMode._from_legacy(target, stacklevel=None)
    if isinstance(target, TargetMode):
        return target
    if isinstance(target, (staticmethod, classmethod)):
        return target.__func__  # audit sees the function, not the descriptor
    return target


def _reorder_kwargs_for_surplus(
    source: Callable,
    target_func: Callable,
    surplus: tuple[Any, ...],
    resolved_kwargs: dict[str, Any],
) -> tuple[list[Any], dict[str, Any]]:
    """Convert leading positional-capable kwargs back to positional form so a surplus tail can follow.

    A source declaring ``*args`` may receive more positional arguments than it has named
    positional parameters; the surplus tail must be forwarded to the target *positionally*.
    Python rejects positional arguments after keyword arguments, so every kwarg bound to one of
    the target's leading positional slots is popped back into positional form first, in
    declaration order.  The fill stops at the first leading slot absent from
    ``resolved_kwargs`` — the surplus then binds to the remaining slots (and/or the target's
    own ``*args``), which is the natural positional call shape.

    Args:
        source: The decorated callable — named in the curated ``TypeError``.
        target_func: The forwarding target whose signature defines the positional slots.
        surplus: Positional tail beyond the source's named positional parameters
            (``args[dep_cfg.source_var_positional_prefix:]``).
        resolved_kwargs: Final kwargs assembled by :func:`_build_call_plan`.

    Returns:
        Tuple of ``(pos_args, kw_args)``; the caller appends ``surplus`` after ``pos_args``.

    Raises:
        TypeError: When the target declares no ``*args`` and its unfilled positional slots
            cannot absorb the surplus — raising loudly instead of silently dropping data.

    """
    params = list(_get_signature(target_func).parameters.values())
    positional_names: list[str] = []
    accepts_var_positional = False
    for param in params:
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            accepts_var_positional = True
            break
        if param.kind not in (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD):
            break
        positional_names.append(param.name)
    kw_args = dict(resolved_kwargs)
    pos_args: list[Any] = []
    for name in positional_names:
        if name not in kw_args:
            break
        pos_args.append(kw_args.pop(name))
    free_slots = len(positional_names) - len(pos_args)
    if not accepts_var_positional and len(surplus) > free_slots:
        raise TypeError(
            f"Failed mapping of `{source.__name__}`: {len(surplus)} extra positional argument(s) from `*args`"
            f" cannot be forwarded to target `{getattr(target_func, '__name__', repr(target_func))}` —"
            f" it accepts at most {len(positional_names)} positional argument(s) and no `*args`."
        )
    return pos_args, kw_args


def _resolve_source_call_shape(
    plan: _CallPlan,
    dep_cfg: DeprecationConfig,
    source_has_var_positional: bool,
    args: tuple[Any, ...],
) -> tuple[list[Any], dict[str, Any]]:
    """Return the ``(pos_args, kw_args)`` shape for invoking the *source* body.

    Covers the short-circuit (migrated caller) and no-target (NOTIFY / ARGS_REMAP) branches:

    - ``*args`` sources keep their original positional tuple; kwargs are the original caller-
      supplied keywords only (never ``resolved_kwargs``, which also contains positional-to-keyword
      conversions that would double-pass with ``*args``).  When an arg-rename reason fires,
      the mapping is applied to ``original_kwargs`` directly, and ``args_extra`` is merged last.
    - Sources declaring POSITIONAL_ONLY params get those split back out of the resolved
      kwargs — the wrapper converted them to keyword form internally, but the source body
      cannot accept them as keywords.
    - All other sources are invoked with the resolved kwargs only.

    """
    if source_has_var_positional:
        if plan.short_circuit or not plan.reason_argument:
            kw_args = plan.original_kwargs
        else:
            # Use caller-supplied keywords only (not resolved_kwargs, which also contains
            # positional-to-keyword conversions that would double-pass with *args).
            mapping = dep_cfg.args_mapping or {}
            kw_args = {
                (mapping.get(k) or k): v
                for k, v in plan.original_kwargs.items()
                if k not in mapping or mapping[k] is not None
            }
            if dep_cfg.args_extra:
                kw_args.update(dep_cfg.args_extra)
        return list(args), kw_args
    if dep_cfg.source_positional_only:
        return _split_positional_only_kwargs(
            dep_cfg.source_positional_only_order, plan.resolved_kwargs, dep_cfg.source_positional_only
        )
    return [], plan.resolved_kwargs


def _resolve_target_call_shape(
    source: Callable,
    plan: _CallPlan,
    dep_cfg: DeprecationConfig,
    source_has_var_positional: bool,
    args: tuple[Any, ...],
) -> tuple[list[Any], dict[str, Any]]:
    """Return the ``(pos_args, kw_args)`` shape for invoking the forwarding *target*.

    - A ``*args`` source with a surplus positional tail (values past its named positionals)
      forwards the tail positionally — leading kwargs are reordered into positional form via
      :func:`_reorder_kwargs_for_surplus` so the tail can legally follow them.
    - A target with POSITIONAL_ONLY params gets those split out of the resolved kwargs via
      :func:`_split_positional_only_kwargs`.
    - Otherwise the target receives the resolved kwargs only.

    """
    target_func = cast(Callable, plan.target_func)
    surplus = args[dep_cfg.source_var_positional_prefix :] if source_has_var_positional else ()
    if surplus:
        pos_args, kw_args = _reorder_kwargs_for_surplus(source, target_func, surplus, plan.resolved_kwargs)
        return [*pos_args, *surplus], kw_args
    if dep_cfg.target_positional_only:
        return _split_positional_only_kwargs(
            dep_cfg.target_positional_only_order, plan.resolved_kwargs, dep_cfg.target_positional_only
        )
    return [], plan.resolved_kwargs


async def _invoke_async(
    source: Callable,
    plan: _CallPlan,
    dep_cfg: DeprecationConfig,
    source_has_var_positional: bool,
    args: tuple[Any, ...],
) -> Any:  # noqa: ANN401
    """Dispatch async call after :func:`_build_call_plan` has resolved the outcome."""
    if plan.short_circuit or plan.target_func is None:
        pos_args, kw_args = _resolve_source_call_shape(plan, dep_cfg, source_has_var_positional, args)
        return await source(*pos_args, **kw_args)
    pos_args, kw_args = _resolve_target_call_shape(source, plan, dep_cfg, source_has_var_positional, args)
    # Sync target under async source: invoke directly so callers can migrate without forcing
    # every legacy target to be redeclared ``async def``.
    if inspect.iscoroutinefunction(plan.target_func):
        return await plan.target_func(*pos_args, **kw_args)
    return plan.target_func(*pos_args, **kw_args)


def _invoke_sync(
    source: Callable,
    plan: _CallPlan,
    dep_cfg: DeprecationConfig,
    source_has_var_positional: bool,
    args: tuple[Any, ...],
) -> Any:  # noqa: ANN401
    """Dispatch sync call after :func:`_build_call_plan` has resolved the outcome."""
    if plan.short_circuit or plan.target_func is None:
        pos_args, kw_args = _resolve_source_call_shape(plan, dep_cfg, source_has_var_positional, args)
        return source(*pos_args, **kw_args)
    if inspect.iscoroutinefunction(plan.target_func):
        raise TypeError(
            f"Async target `{plan.target_func.__name__}` cannot be invoked from a sync wrapper."
            f" Declare `{source.__name__}` as `async def`, or replace the target with a sync callable."
        )
    pos_args, kw_args = _resolve_target_call_shape(source, plan, dep_cfg, source_has_var_positional, args)
    return plan.target_func(*pos_args, **kw_args)
