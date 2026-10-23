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
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from functools import cached_property, partial, partialmethod, singledispatchmethod
from inspect import Parameter
from typing import Any, Callable, NamedTuple, NoReturn, Optional, Union, cast

from deprecate._fatal import _resolve_as_exception
from deprecate._frames import _caller_stacklevel
from deprecate._types import (
    DeprecationConfig,
    TargetMode,
    _CallFacts,
    _CallPlan,
    _WrapperState,
    get_deprecation_config,
)
from deprecate.messaging import (
    _callable_display_name,
    _consume_warn_budget,
    _raise_warn_arguments,
    _raise_warn_callable,
)
from deprecate.utils import (
    _apply_args_mapping_collisions,
    _get_signature,
    _partial_chain,
    get_func_arguments_types_defaults,
)

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
    _reject_functools_wrapper_source(source)
    if not callable(source) and not isinstance(source, (classmethod, staticmethod, property, cached_property)):
        raise TypeError(
            f"`@deprecated` must be called with parentheses, e.g. "
            f"`@deprecated(target=..., deprecated_in=..., remove_in=...)`; got a non-callable "
            f"`{type(source).__name__}` as the decoration target, which usually means `@deprecated` "
            f"was used without arguments."
        )


# Descriptor source types the callable path handles via ``_packing_descriptor`` even though some are
# neither ``callable`` nor carry ``__name__`` (``property``/``cached_property``). They must be exempt
# from the dispatcher's "plain object" reject guard so only class/instance-style misuse and the ``functools``
# wrapper kinds (``_FUNCTOOLS_WRAPPER_SOURCE_TYPES``, rejected first with their own message) are rejected.
_DESCRIPTOR_SOURCE_TYPES = (classmethod, staticmethod, property, cached_property)

# Reject message for a non-callable / callable-without-``__name__`` source handed to ``@deprecated``.
_PLAIN_OBJECT_REJECT = "cannot deprecate a plain object with `@deprecated` — use `deprecated_instance(obj, ...)`"

#: ``functools`` wrapper kinds rejected as ``@deprecated`` sources with their own message: none has a function body
#: the wrapper could take over, and ``partialmethod`` / ``singledispatchmethod`` are class-body descriptors that
#: ``deprecated_instance`` (the plain-object advice) does not fit either.
_FUNCTOOLS_WRAPPER_SOURCE_TYPES = (partial, partialmethod, singledispatchmethod)


def _reject_functools_wrapper_source(source: Any) -> None:  # noqa: ANN401
    """Raise ``TypeError`` when *source* is a ``functools.partial``, ``partialmethod`` or ``singledispatchmethod``.

    Runs first in both decoration-time guards (:func:`_reject_bare_decorator` for ``deprecated_callable``, and
    :func:`_reject_non_callable_source` for the ``@deprecated`` front door), so every entry point gives the same
    advice instead of "use ``deprecated_instance``", "must be called with parentheses", or a late
    ``AttributeError``. The message names the wrapper type and the workaround: wrap it in a plain ``def``. Its
    subject is "pyDeprecate", not ``@deprecated``, because the ``deprecated_callable`` guard raises it too.

    Args:
        source: The object ``packing`` received as its decoration target.

    Raises:
        TypeError: When ``source`` is an instance of one of :data:`_FUNCTOOLS_WRAPPER_SOURCE_TYPES`.

    """
    if isinstance(source, _FUNCTOOLS_WRAPPER_SOURCE_TYPES):
        kind = f"{type(source).__module__}.{type(source).__qualname__}"
        raise TypeError(
            f"pyDeprecate cannot deprecate a `{kind}` object: it has no function body of its own to wrap."
            " Wrap it in a plain `def` that calls it, and decorate that `def` instead"
            " (in a class body: `def method(self, *args, **kwargs): return ...`)."
        )


def _reject_non_callable_source(source: Any, target: Any) -> None:  # noqa: ANN401
    """Raise ``TypeError`` for a non-callable, or callable-without-``__name__``, decoration source.

    Third dispatch bucket: a non-callable object, or a callable instance lacking ``__name__`` (a hand-written
    ``__call__`` object), would crash downstream at ``source.__name__``. Reject up front with actionable
    guidance. ``functools`` wrapper kinds (``partial``, ``partialmethod``, ``singledispatchmethod``) are rejected
    first with their own message (:func:`_reject_functools_wrapper_source`). Descriptors are exempt — the callable
    path handles them via ``_packing_descriptor`` even though some are neither ``callable`` nor carry ``__name__``.

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
    _reject_functools_wrapper_source(source)
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


def _warn_stacking_misconfiguration(source: Callable, outer_target: Union[TargetMode, Callable]) -> None:
    """Emit ``UserWarning`` at decoration time for unsupported stacking combinations.

    Only called when ``source`` already carries ``__deprecation_config__`` metadata (i.e. is itself a
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
    inner_config = get_deprecation_config(source)
    if inner_config is None:
        raise TypeError(f"'{source.__name__}' no longer carries valid deprecation metadata.")
    inner_target = inner_config.target
    name = source.__name__

    if callable(outer_target) and callable(inner_target):
        warnings.warn(
            f"'{name}' has a callable target stacked over another callable-target @deprecated."
            " Stacking a callable target over another callable target is not supported."
            " This will raise `TypeError` at call time."
            f" Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=_caller_stacklevel(),
        )
    elif callable(outer_target) and inner_target is TargetMode.ARGS_REMAP:
        warnings.warn(
            f"'{name}' has a callable target stacked over @deprecated(ARGS_REMAP)."
            " The arg-rename warning will not fire at call time; the inner layer is bypassed."
            " Collapse to: @deprecated(target=<callable>, args_mapping={...})."
            f" Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=_caller_stacklevel(),
        )
    elif callable(outer_target) and inner_target is TargetMode.NOTIFY:
        warnings.warn(
            f"'{name}' has a callable target stacked over @deprecated(NOTIFY)."
            " The inner function-deprecated warning will not fire at call time; the inner layer is bypassed"
            " while the callable target is still invoked."
            " Collapse to a single @deprecated(target=<callable>) and remove the inner @deprecated(NOTIFY)."
            f" Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=_caller_stacklevel(),
        )
    elif outer_target is TargetMode.ARGS_REMAP and callable(inner_target):
        warnings.warn(
            f"'{name}' has @deprecated(ARGS_REMAP) stacked over a callable-target @deprecated."
            " Update the inner @deprecated(target=<callable>, args_mapping={...}) instead of stacking."
            f" Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=_caller_stacklevel(),
        )
    elif outer_target is TargetMode.NOTIFY and inner_target is TargetMode.NOTIFY:
        warnings.warn(
            f"'{name}' has duplicate @deprecated(NOTIFY) layers."
            " Update the existing decorator's `deprecated_in`, `remove_in`, or `message_template` instead."
            f" Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=_caller_stacklevel(),
        )
    elif outer_target is TargetMode.NOTIFY and inner_target is TargetMode.ARGS_REMAP:
        warnings.warn(
            f"'{name}' has @deprecated(NOTIFY) stacked over @deprecated(ARGS_REMAP)."
            " Reverse the decorator order: put @deprecated(ARGS_REMAP, ...) outermost (on top)"
            " and @deprecated(NOTIFY, ...) below it."
            f" Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
            UserWarning,
            stacklevel=_caller_stacklevel(),
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
            stacklevel=_caller_stacklevel(),
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
    if target is None or isinstance(target, bool):
        return TargetMode._from_legacy(target)

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


def _raise_positional_arity_error(func: Callable, positional_params: list[inspect.Parameter], n_args: int) -> NoReturn:
    """Raise the curated ``TypeError`` for a call that passes more positional arguments than ``func`` declares.

    Args:
        func: Callable whose positional slots were exceeded; the message names it by ``__qualname__``.
        positional_params: The positional-only and positional-or-keyword parameters of ``func``, in order.
        n_args: Number of positional arguments the caller passed.

    Raises:
        TypeError: Always; the message states the accepted count, or the accepted range when some slots have
            defaults.

    """
    required_positional_params = [param for param in positional_params if param.default is inspect.Parameter.empty]
    if len(required_positional_params) == len(positional_params):
        raise TypeError(
            f"{func.__qualname__}() takes {len(positional_params)} positional argument(s) but got "
            f"{n_args} positional argument(s)"
        )
    raise TypeError(
        f"{func.__qualname__}() takes {len(required_positional_params)} to {len(positional_params)} "
        f"positional argument(s) but got {n_args} positional argument(s)"
    )


def _check_source_arity(
    source: Callable,
    args: tuple[Any, ...],
    source_positional_names: tuple[str, ...],
    source_has_var_positional: bool,
) -> None:
    """Reject surplus positional arguments before any deprecation warning fires or any warn budget is spent.

    A caller passing one positional argument too many gets the same curated ``TypeError`` whether or not the wrapper
    goes on to rewrite the call, so the check runs on the decoration-time parameter names instead of on the converted
    keyword arguments.  A source declaring ``*args`` accepts any number of positionals and is never rejected.

    Args:
        source: The decorated callable.
        args: The positional arguments the caller passed.
        source_positional_names: Names of the source's positional parameters before ``*args``, in order.
        source_has_var_positional: ``True`` when ``source`` declares ``*args``.

    Raises:
        TypeError: When ``source`` has no ``*args`` and ``args`` is longer than ``source_positional_names``.

    Example:
        >>> def source(a, b=0): pass
        >>> _check_source_arity(source, (1, 2), ("a", "b"), False)
        >>> _check_source_arity(source, (1, 2, 3), ("a", "b"), False)
        Traceback (most recent call last):
        ...
        TypeError: source() takes 1 to 2 positional argument(s) but got 3 positional argument(s)

    """
    if source_has_var_positional or len(args) <= len(source_positional_names):
        return
    params = list(_get_signature(source).parameters.values())
    _raise_positional_arity_error(source, _get_positional_params(params), len(args))


def _is_verbatim_call(
    normalized_target: Union[Callable[..., Any], TargetMode],
    dep_cfg: DeprecationConfig,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
    source_positional_names: tuple[str, ...],
) -> bool:
    """Decide whether the source can be called with the caller's own ``(*args, **kwargs)``, unchanged.

    The wrapper has to rewrite a call only when something changes on the way to the source: a callable target is
    forwarded by keyword, a deprecated argument is renamed, or ``args_extra`` is injected.  Every other call is passed
    through exactly as made, so a source whose runtime call shape differs from its signature
    (``functools.singledispatch``, a patched ``__signature__``, a ``functools.wraps`` forwarder) keeps working under
    ``TargetMode.NOTIFY`` and under a ``TargetMode.ARGS_REMAP`` the caller does not trigger.  A deprecated argument
    counts as used when its old name is passed by keyword or when a positional argument fills the slot that carries
    the old name.

    Args:
        normalized_target: The normalised target (callable or :class:`TargetMode`).
        dep_cfg: The wrapper's configuration; ``args_mapping`` and ``args_extra`` are read from it.
        args: The positional arguments the caller passed.
        kwargs: The keyword arguments the caller passed.
        source_positional_names: Names of the source's positional parameters before ``*args``, in order.

    Returns:
        ``True`` when the source can be invoked as ``source(*args, **kwargs)``.

    Example:
        >>> from deprecate._types import DeprecationConfig, TargetMode
        >>> cfg = DeprecationConfig(deprecated_in="1.0", remove_in="2.0", args_mapping={"old": "new"})
        >>> _is_verbatim_call(TargetMode.ARGS_REMAP, cfg, (1,), {}, ("new", "old"))
        True
        >>> _is_verbatim_call(TargetMode.ARGS_REMAP, cfg, (1, 2), {}, ("new", "old"))
        False
        >>> _is_verbatim_call(TargetMode.ARGS_REMAP, cfg, (), {"old": 1}, ("new", "old"))
        False

    """
    if callable(normalized_target):
        return False
    if normalized_target is not TargetMode.ARGS_REMAP:
        return True
    if dep_cfg.args_extra:
        return False
    mapping = dep_cfg.args_mapping or {}
    filled_slots = source_positional_names[: len(args)]
    return not (any(name in kwargs for name in mapping) or any(name in mapping for name in filled_slots))


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
        _raise_positional_arity_error(func, positional_params, len(fn_args))
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


def _renamed_defaults_owned_by_target(
    normalized_target: Union[Callable[..., Any], TargetMode],
    args_mapping: Mapping[str, Optional[str]],
) -> frozenset[str]:
    """Return the source parameter names whose default an ``args_mapping`` rename makes stale.

    A source default for a renamed argument survives :func:`_update_kwargs_with_defaults` and would be forwarded under
    the new name, silently overriding the target's own default. Two kinds of name are stale: a source parameter that
    already carries a rename's *new* name, and a renamed *old* name whose new name has a default on the target. Only a
    callable target with a non-empty mapping can have any.

    """
    if not args_mapping or not callable(normalized_target):
        return frozenset()
    rename_targets = {new for new in args_mapping.values() if new is not None}
    target_defaults = {
        arg[0] for arg in get_func_arguments_types_defaults(normalized_target) if arg[2] is not inspect.Parameter.empty
    }
    renamed_with_target_default = {old for old, new in args_mapping.items() if new in target_defaults}
    return frozenset(rename_targets | renamed_with_target_default)


class _ForwardDefaults(NamedTuple):
    """Names that keep a source default out of a forwarded call, fixed once the function is decorated.

    Both sets depend only on the target and on ``args_mapping``, never on the call, so a wrapper can derive them one
    time (:func:`_precompute_forward_defaults`) and hand the same instance to every call instead of re-deriving them
    on each one. Keep that instance on the wrapper's :class:`_WrapperContext`, not on
    :class:`~deprecate._types.DeprecationConfig`, which is public through ``get_deprecation_config``.

    Attributes:
        stale_renamed: Source parameter names whose default an ``args_mapping`` rename makes stale (see
            :func:`_renamed_defaults_owned_by_target`).
        partial_bound: Keyword names bound anywhere in the target's ``functools.partial`` chain.

    """

    stale_renamed: frozenset[str]
    partial_bound: frozenset[str]


def _derive_forward_defaults(
    normalized_target: Union[Callable[..., Any], TargetMode],
    args_mapping: Optional[Mapping[str, Optional[str]]],
) -> _ForwardDefaults:
    """Derive the :class:`_ForwardDefaults` of one target and mapping on the spot.

    Args:
        normalized_target: The normalised target (callable or :class:`TargetMode`).
        args_mapping: The wrapper's ``args_mapping``, or ``None``.

    Returns:
        The names a rename makes stale and the keywords the target's ``functools.partial`` chain binds.

    Raises:
        TypeError: When the target's signature cannot be read.
        ValueError: When the target's signature cannot be read.

    """
    stale_renamed = _renamed_defaults_owned_by_target(normalized_target, args_mapping or {})
    partial_bound = frozenset().union(*(layer.keywords for layer in _partial_chain(normalized_target)))
    return _ForwardDefaults(stale_renamed, partial_bound)


def _precompute_forward_defaults(
    normalized_target: Union[Callable[..., Any], TargetMode],
    args_mapping: Optional[Mapping[str, Optional[str]]],
) -> Optional[_ForwardDefaults]:
    """Derive a wrapper's :class:`_ForwardDefaults` once, at decoration time.

    Store the result on the wrapper's :class:`_WrapperContext`; the forwarded-call path then skips the target
    signature read and the ``functools.partial`` chain walk that would otherwise repeat per call.

    Args:
        normalized_target: The normalised target (callable or :class:`TargetMode`).
        args_mapping: The wrapper's ``args_mapping``, or ``None``.

    Returns:
        The facts, or ``None`` when the target's signature cannot be read. Passing ``None`` on makes the call path
        derive them itself, so the error an unreadable signature raises still surfaces on the first forwarded call
        and decoration (import time) never fails because of it.

    Example:
        >>> from functools import partial
        >>> def new(x, y=1, z=2): pass
        >>> _precompute_forward_defaults(partial(new, z=5), None)
        _ForwardDefaults(stale_renamed=frozenset(), partial_bound=frozenset({'z'}))
        >>> sorted(_precompute_forward_defaults(new, {"old": "y"}).stale_renamed)
        ['old', 'y']

    """
    try:
        return _derive_forward_defaults(normalized_target, args_mapping)
    except (TypeError, ValueError):
        return None


def _merge_source_defaults(
    source: Callable[..., Any],
    normalized_target: Union[Callable[..., Any], TargetMode],
    kwargs: dict[str, Any],
    args_mapping: Optional[Mapping[str, Optional[str]]],
    forward_defaults: Optional[_ForwardDefaults] = None,
) -> dict[str, Any]:
    """Fill the source's defaults into the caller's *kwargs*, leaving out defaults the target owns.

    Precedence for one argument: a value the caller supplied (by keyword or by position — positionals are already
    folded into *kwargs*) > a keyword a ``functools.partial`` target binds > the source default. Two kinds of
    source default are therefore dropped, unless the caller supplied that name:

    - a renamed argument's default the rename makes stale (:func:`_renamed_defaults_owned_by_target`);
    - a default whose forwarded name (``args_mapping.get(name) or name``) is bound by a keyword anywhere in the
      target's ``functools.partial`` chain — the partial's own value must reach the target.

    Every other shared parameter keeps its source default: the source signature is the contract callers migrated
    from, so its default is forwarded (see ``decorated_sum`` / ``test_functions.py::test_default``). Positional
    arguments a partial binds are not considered — forwarding by name collides with the bound slot.

    Args:
        source: The decorated callable whose signature provides the defaults.
        normalized_target: The normalised target (callable or :class:`TargetMode`).
        kwargs: The caller's arguments, positionals already mapped to their parameter names.
        args_mapping: The wrapper's ``args_mapping``, or ``None``.
        forward_defaults: Facts precomputed at decoration time (:func:`_precompute_forward_defaults`). ``None``
            derives them from ``normalized_target`` and ``args_mapping`` on the spot, on every call.

    Returns:
        The caller's arguments plus every source default the target does not own.

    """
    full_defaults = _update_kwargs_with_defaults(source, kwargs)
    if forward_defaults is None:
        forward_defaults = _derive_forward_defaults(normalized_target, args_mapping)
    stale_renamed, partial_bound = forward_defaults
    if not stale_renamed and not partial_bound:
        return full_defaults
    mapping = args_mapping or {}
    return {
        name: value
        for name, value in full_defaults.items()
        if name in kwargs or (name not in stale_renamed and (mapping.get(name) or name) not in partial_bound)
    }


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


@dataclass(frozen=True)
class _WrapperContext:
    """Everything a ``@deprecated`` wrapper needs at call time, fixed once the callable is decorated.

    ``packing`` builds one instance per wrapper and the wrapper's closure hands it to every call, so the call-time
    helpers below read configuration, signature facts and counters from one place.  The wrapper reads ``cfg`` from
    here rather than from its own ``__deprecated__`` attribute: a PEP 702 ``typing_extensions.deprecated`` decorator
    stacked outside this one overwrites that attribute with a plain string.

    Attributes:
        source: The decorated callable.
        raw_target: The ``target`` argument as given to ``@deprecated``; warnings name a class target by it rather
            than by its ``__init__``.
        target: The normalised target (a :class:`TargetMode` member or a callable) returned by
            :func:`_normalize_target`.
        cfg: The wrapper's frozen :class:`~deprecate._types.DeprecationConfig`; ``args_mapping``, ``args_extra``,
            ``deprecated_in``, ``remove_in``, ``message_template``, ``misconfigured`` and ``as_exception`` are read
            from it.
        facts: Signature facts of the source and the target (see :func:`_build_call_facts`).
        state: The wrapper's mutable counters, the same object as ``wrapper._state``, so a counter reset through the
            wrapper is seen by the next call.
        stream: Warning stream, or ``None`` to suppress emission.
        num_warns: Warning budget per wrapper and per renamed argument; negative means unlimited.
        skip_if: The ``skip_if`` condition, evaluated on every call by :func:`~deprecate.messaging._eval_skip_if`.
        is_stacked: ``True`` when ``source`` is itself a ``@deprecated`` wrapper.
        forward_defaults: Decoration-time facts for the forwarded-call path (see
            :func:`_precompute_forward_defaults`); ``None`` derives them on every forwarded call.
        remap_active: Derived; ``True`` when ``args_mapping`` and ``args_extra`` apply to the target, that is for
            ``TargetMode.ARGS_REMAP`` and for a callable target.
        cycle_guarded: Derived; ``True`` when the target is callable, so every call runs under the target-chain cycle
            guard (:func:`_enter_cycle_guard`).

    """

    source: Callable[..., Any]
    raw_target: Union[bool, None, Callable[..., Any], TargetMode, staticmethod, classmethod]
    target: Union[Callable[..., Any], TargetMode]
    cfg: DeprecationConfig
    facts: _CallFacts
    state: _WrapperState
    stream: Optional[Callable[..., Any]]
    num_warns: int
    skip_if: Union[bool, Callable[[], bool]]
    is_stacked: bool
    forward_defaults: Optional[_ForwardDefaults] = None
    remap_active: bool = field(init=False)
    cycle_guarded: bool = field(init=False)

    def __post_init__(self) -> None:
        """Derive the per-wrapper flags the call path would otherwise recompute on every call."""
        forwards = callable(self.target)
        object.__setattr__(self, "cycle_guarded", forwards)
        object.__setattr__(self, "remap_active", forwards or self.target is TargetMode.ARGS_REMAP)


#: Handle returned by :func:`_enter_cycle_guard`: the active id-set and the token that installed it, or ``None`` when
#: the wrapper does not run under the guard.
_CycleGuardHandle = Optional[tuple[set[int], Optional["Token[Optional[set[int]]]"]]]

# ContextVar storing the active-wrapper id-set for the current async task or sync call stack.
# Each asyncio.Task inherits a snapshot of the parent context at creation time; because this
# ContextVar defaults to None and is only set() to a fresh set() inside the wrapper call, tasks
# spawned from user code (e.g. asyncio.gather) see None and create independent sets — no sharing.
# A synchronous recursive chain (same task/stack) shares one set — correct for cycle detection.
# Only ``_enter_cycle_guard`` / ``_exit_cycle_guard`` below read or write it.
_cycle_detection: ContextVar[Optional[set[int]]] = ContextVar("_cycle_detection", default=None)


def _enter_cycle_guard(ctx: _WrapperContext) -> _CycleGuardHandle:
    """Mark ``ctx.source`` as running in the current call stack or task, or raise when it is already running.

    A wrapper whose callable target leads back to itself would otherwise recurse until the interpreter's recursion
    limit.  Wrappers without a callable target are not guarded and get ``None``.  Pass the handle to
    :func:`_exit_cycle_guard` in a ``finally``; a raise from this function leaves nothing to undo.

    Args:
        ctx: The wrapper's context.

    Returns:
        The handle :func:`_exit_cycle_guard` needs, or ``None`` when the wrapper is not guarded.

    Raises:
        RuntimeError: When ``ctx.source`` re-enters through its own target chain.

    """
    if not ctx.cycle_guarded:
        return None
    active = _cycle_detection.get()
    token: Optional[Token[Optional[set[int]]]] = None
    if active is None:
        active = set()
        token = _cycle_detection.set(active)
    if id(ctx.source) in active:
        source_name = getattr(ctx.source, "__qualname__", repr(ctx.source))
        raise RuntimeError(
            f"Circular deprecation cycle detected: `{source_name}` re-entered"
            " via its own target chain. Point to a non-deprecated final implementation."
        )
    active.add(id(ctx.source))
    return active, token


def _exit_cycle_guard(ctx: _WrapperContext, handle: _CycleGuardHandle) -> None:
    """Undo :func:`_enter_cycle_guard`: drop ``ctx.source`` from the active set and restore the outer value."""
    if handle is None:
        return
    active, token = handle
    active.discard(id(ctx.source))
    if token is not None:
        _cycle_detection.reset(token)


def _leading_positional_slots(func: Callable[..., Any]) -> tuple[tuple[str, ...], bool]:
    """Return ``func``'s leading positional parameter names and whether a ``*args`` parameter ends them.

    Raises:
        TypeError: When the signature of ``func`` cannot be read.
        ValueError: When the signature of ``func`` cannot be read.

    Example:
        >>> def target(a, /, b, *rest, c=1): pass
        >>> _leading_positional_slots(target)
        (('a', 'b'), True)

    """
    names: list[str] = []
    for param in _get_signature(func).parameters.values():
        if param.kind is Parameter.VAR_POSITIONAL:
            return tuple(names), True
        if param.kind not in (POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD):
            break
        names.append(param.name)
    return tuple(names), False


def _precompute_target_positional_names(target: Union[Callable[..., Any], TargetMode]) -> Optional[tuple[str, ...]]:
    """Return the target's leading positional names, ``()`` for a non-callable, ``None`` for an unreadable signature."""
    if not callable(target):
        return ()
    try:
        return _leading_positional_slots(target)[0]
    except (TypeError, ValueError):
        return None


def _build_call_facts(
    source: Callable[..., Any],
    normalized_target: Union[Callable[..., Any], TargetMode],
    stream: Optional[Callable[..., Any]],
) -> _CallFacts:
    """Derive the :class:`~deprecate._types._CallFacts` of a source and its normalised target at decoration time.

    Reads the source signature once and the target signature through :func:`_detect_positional_only`,
    :func:`_precompute_target_facts` and :func:`_leading_positional_slots`, so no call inspects either again.

    Args:
        source: The decorated callable.
        normalized_target: The normalised target returned by :func:`_normalize_target`.
        stream: Warning stream; the POSITIONAL_ONLY target warning of :func:`_detect_positional_only` fires through
            ``warnings.warn`` unless it is ``None``.

    Returns:
        The facts the call-time dispatcher reads.

    Raises:
        TypeError: When the source signature cannot be read.
        ValueError: When the source signature cannot be read.

    Example:
        >>> def source(a, /, b, *rest, c=1): pass
        >>> facts = _build_call_facts(source, TargetMode.NOTIFY, None)
        >>> facts.source_positional_names, facts.source_var_positional_prefix, sorted(facts.source_positional_only)
        (('a', 'b'), 2, ['a'])

    """
    source_params = list(_get_signature(source).parameters.values())
    var_positional_at = next((i for i, p in enumerate(source_params) if p.kind is Parameter.VAR_POSITIONAL), None)
    # Source-side POSITIONAL_ONLY params: a call the wrapper rewrites (a triggered ARGS_REMAP rename, or ARGS_REMAP
    # with args_extra) converts positional args to kwargs internally, so the dispatcher must split these back out
    # before invoking the source body.  Verbatim calls keep the caller's shape and need no split.
    source_positional_only = frozenset(p.name for p in source_params if p.kind is POSITIONAL_ONLY)
    target_positional_only, target_positional_only_order = _detect_positional_only(normalized_target, source, stream)
    target_all_names, accepts_var_positional, accepts_var_keyword = _precompute_target_facts(normalized_target)
    return _CallFacts(
        source_positional_names=tuple(p.name for p in _get_positional_params(source_params)),
        source_has_var_positional=var_positional_at is not None,
        source_var_positional_prefix=var_positional_at or 0,
        source_positional_only=source_positional_only,
        source_positional_only_order=tuple(p.name for p in source_params) if source_positional_only else (),
        target_all_param_names=target_all_names,
        target_accepts_var_positional=accepts_var_positional,
        target_accepts_var_keyword=accepts_var_keyword,
        target_positional_only=target_positional_only,
        target_positional_only_order=target_positional_only_order,
        target_positional_names=_precompute_target_positional_names(normalized_target),
    )


def _resolve_forward_kwargs(
    ctx: _WrapperContext,
    kwargs: dict[str, Any],
    reason_callable: bool,
) -> tuple[dict[str, Any], Optional[Callable[..., Any]]]:
    """Build the final keyword arguments, and the validated target, for a call the wrapper has to rewrite.

    Runs only when :func:`_is_verbatim_call` rejected the pass-through.  Applies, in order: the source defaults (for a
    callable reason only), the ``args_mapping`` rename with its collision check, the ``args_extra`` merge, and, for a
    callable target, the check that the target accepts every resulting keyword.  Precedence when keys collide: an
    explicit new-name keyword wins over the remapped old-name value; ``args_extra`` wins over both (it is merged
    last).

    Args:
        ctx: The wrapper's context.
        kwargs: The caller's arguments, positionals already mapped to their parameter names.
        reason_callable: ``True`` when the whole callable is deprecated (``NOTIFY`` or a callable target).

    Returns:
        Tuple of ``(kwargs, target_func)``; ``target_func`` is ``None`` unless the target is callable.

    """
    source, target, mapping, extra = ctx.source, ctx.target, ctx.cfg.args_mapping, ctx.cfg.args_extra
    if reason_callable:
        kwargs = _merge_source_defaults(source, target, kwargs, mapping, ctx.forward_defaults)
    if mapping and ctx.remap_active:
        args_skip = [arg for arg in mapping if not mapping[arg]]
        _explicit_new = _apply_args_mapping_collisions(mapping, kwargs, args_skip, source.__name__, ctx.stream)
        kwargs = {(mapping.get(arg) or arg): val for arg, val in kwargs.items() if arg not in args_skip}
        if _explicit_new:
            kwargs.update(_explicit_new)
    if extra and ctx.remap_active:
        kwargs.update(extra)
    if not callable(target):
        return kwargs, None
    facts = ctx.facts
    # ``None`` means the facts were not precomputed (a hand-built ``_CallFacts``); _prepare_target_call recomputes
    # them from the target. ``frozenset()`` is a valid cached value for zero-arg targets — it gates on identity
    # (``is not None``), not truthiness, to preserve the zero-arg cache hit.
    target_func = _prepare_target_call(
        source,
        target,
        kwargs,
        target_arg_names=facts.target_all_param_names,
        accepts_var_positional=facts.target_accepts_var_positional,
        accepts_var_keyword=facts.target_accepts_var_keyword,
    )
    return kwargs, target_func


def _warn_misconfigured_once(ctx: _WrapperContext) -> None:
    """Emit the one-time call-time ``UserWarning`` of a misconfigured wrapper, unless ``stream`` is ``None``."""
    state = ctx.state
    if ctx.cfg.misconfigured and ctx.stream and not state.warned_misconfigured:
        warnings.warn(
            f"'{ctx.source.__name__}' has an invalid deprecation configuration;"
            f" verify your `@deprecated(target=...)` arguments. Will be TypeError in {_MAJOR_BREAK_VERSION}.",
            UserWarning,
            stacklevel=_caller_stacklevel(),
        )
        state.warned_misconfigured = True


def _resolve_reasons(ctx: _WrapperContext, kwargs: Mapping[str, Any]) -> tuple[bool, dict[str, Optional[str]]]:
    """Return why this call warns: the whole callable, and the deprecated argument names the caller used.

    Args:
        ctx: The wrapper's context.
        kwargs: The caller's arguments; positionals are already mapped to their parameter names unless the call is
            verbatim.

    Returns:
        Tuple ``(reason_callable, reason_argument)``: ``reason_callable`` is ``True`` for ``TargetMode.NOTIFY`` and a
        callable target, ``reason_argument`` maps each used old name of ``args_mapping`` to its new name.

    """
    reason_callable = ctx.target is TargetMode.NOTIFY or callable(ctx.target)
    mapping = ctx.cfg.args_mapping
    reason_argument = {a: b for a, b in mapping.items() if a in kwargs} if mapping and ctx.remap_active else {}
    return reason_callable, reason_argument


def _is_short_circuit(ctx: _WrapperContext, reason_callable: bool, reason_argument: dict[str, Optional[str]]) -> bool:
    """Decide whether the call skips warning and rewriting: a migrated caller with nothing to inject or to delegate."""
    # Migrated callers (using the new arg name) produce empty reason_argument;
    # without the args_extra guard they short-circuit before extras are injected.
    # When source is a stacked @deprecated wrapper (e.g. ARGS_REMAP outer + NOTIFY inner),
    # do not short-circuit even with no reason — the inner layer may still need to run.
    injects_extra = bool(ctx.cfg.args_extra) and ctx.target is TargetMode.ARGS_REMAP
    return not (reason_callable or reason_argument or injects_extra or ctx.is_stacked)


def _emit_call_warning(
    ctx: _WrapperContext,
    reason_callable: bool,
    reason_argument: dict[str, Optional[str]],
) -> None:
    """Spend the warn budget and deliver this call's deprecation warning, or raise it when the deprecation is fatal.

    The callable reason warns through :func:`_raise_warn_callable`, the argument-rename reason through
    :func:`_raise_warn_arguments`; the budget check-then-increment runs under ``ctx.state.lock``.

    Args:
        ctx: The wrapper's context.
        reason_callable: ``True`` when the whole callable is deprecated.
        reason_argument: Deprecated argument names the caller used, mapped to their new names.

    """
    # Double-checked fast path: avoid the lock when the warn budget is clearly exhausted.
    # Under CPython the int read is atomic (GIL); after num_warns=1 fires once,
    # warned_calls >= num_warns on every subsequent call — no benefit from acquiring the lock.
    # The authoritative check-then-increment still runs under the lock when a warning may fire.
    # Argument-specific budgets are not pre-checked (rare path, dict lookup not worth the complexity).
    # A fatal deprecation is resolved first and sidesteps both gates below: the warn budget (it must raise on
    # every call, not once — otherwise a caller catching DeprecatedError sails through on the second attempt)
    # and the ``stream`` check (``stream=None`` silences the message, it does not open the door).  The budget
    # counters stay untouched, so flipping the global switch off mid-process restores ordinary warn-once.
    state, cfg, num_warns = ctx.state, ctx.cfg, ctx.num_warns
    fatal = _resolve_as_exception(cfg.as_exception)
    should_warn = fatal
    if not fatal and ctx.stream and (num_warns < 0 or reason_argument or state.warned_calls < num_warns):
        with state.lock:
            should_warn = _consume_warn_budget(state, num_warns, reason_callable, reason_argument)
    if not should_warn:
        return
    if reason_callable:
        # Use the raw ``target`` (not the normalised one) so the warning
        # names the class (e.g. "NewCls") rather than "__init__".
        _raise_warn_callable(
            stream=ctx.stream,
            source=ctx.source,
            target=ctx.raw_target,
            deprecated_in=cfg.deprecated_in,
            remove_in=cfg.remove_in,
            message_template=cfg.message_template,
            as_exception=fatal,
        )
    elif reason_argument:
        _raise_warn_arguments(
            stream=ctx.stream,
            source=ctx.source,
            arguments=reason_argument,
            deprecated_in=cfg.deprecated_in,
            remove_in=cfg.remove_in,
            message_template=cfg.message_template,
            as_exception=fatal,
        )


def _build_call_plan(ctx: _WrapperContext, args: tuple[Any, ...], kwargs: dict[str, Any]) -> _CallPlan:
    """Compute the dispatch plan shared by the sync and async wrappers inside :func:`deprecated`.

    The wrappers differ only by ``await`` on the final source or target call.  In order, this counts the call, emits
    the one-time misconfiguration warning (:func:`_warn_misconfigured_once`), rejects surplus positionals
    (:func:`_check_source_arity`), decides the verbatim pass-through (:func:`_is_verbatim_call`), maps positionals to
    keywords, resolves the warning reasons (:func:`_resolve_reasons`), short-circuits a migrated caller, emits the
    budgeted deprecation warning (:func:`_emit_call_warning`) and assembles the forwarded keywords
    (:func:`_resolve_forward_kwargs`).

    Side effects: mutates ``ctx.state`` (bumps ``called``, optionally ``warned_calls`` / ``warned_args``, and sets
    ``warned_misconfigured`` on the first misconfiguration warning) and emits the warnings named above.

    Args:
        ctx: The wrapper's decoration-time context.
        args: The positional arguments the caller passed to the wrapper.
        kwargs: The keyword arguments the caller passed to the wrapper.

    Returns:
        A :class:`_CallPlan` describing the resolved dispatch outcome.  When :attr:`_CallPlan.verbatim` is set, the
        positionals were not converted to keywords and the source is invoked with the caller's arguments unchanged.

    """
    ctx.state.called += 1
    _warn_misconfigured_once(ctx)
    facts = ctx.facts
    # Surplus positionals raise the curated TypeError before any warning fires or any budget is spent.
    _check_source_arity(ctx.source, args, facts.source_positional_names, facts.source_has_var_positional)
    # No rename, no injected extras and no forwarding target: the source gets the caller's arguments unchanged.
    verbatim = _is_verbatim_call(ctx.target, ctx.cfg, args, kwargs, facts.source_positional_names)
    # *args sources need the unremapped tuple; remapping happens on kwargs only.
    original_kwargs = dict(kwargs)
    if not verbatim:
        kwargs = _update_kwargs_with_args(ctx.source, args, kwargs)
    reason_callable, reason_argument = _resolve_reasons(ctx, kwargs)
    if _is_short_circuit(ctx, reason_callable, reason_argument):
        return _CallPlan(short_circuit=True, original_kwargs=original_kwargs, resolved_kwargs=kwargs, verbatim=verbatim)
    _emit_call_warning(ctx, reason_callable, reason_argument)
    target_func: Optional[Callable[..., Any]] = None
    if not verbatim:
        kwargs, target_func = _resolve_forward_kwargs(ctx, kwargs, reason_callable)
    return _CallPlan(
        short_circuit=False,
        original_kwargs=original_kwargs,
        resolved_kwargs=kwargs,
        reason_argument=reason_argument,
        target_func=target_func,
        verbatim=verbatim,
    )


def _detect_positional_only(
    target: Union[Callable, TargetMode],
    source: Callable,
    stream: Optional[Callable],
) -> tuple[frozenset[str], tuple[str, ...]]:
    """Inspect *target* for POSITIONAL_ONLY parameters and emit a ``UserWarning`` when found.

    Called at decoration time so the warning fires once and the results are stored in
    :class:`~deprecate._types.DeprecationConfig` for the call-time dispatcher.

    Args:
        target: Normalised target from :func:`_normalize_target` — may be a
            :class:`TargetMode` or a callable.  Non-callables return empty results immediately.
        source: The decorated callable; used only in the warning message.
        stream: Warning stream; when ``None`` the warning is suppressed.

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
            stacklevel=_caller_stacklevel(),
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
        return TargetMode._from_legacy(target, emit=False)
    if isinstance(target, TargetMode):
        return target
    if isinstance(target, (staticmethod, classmethod)):
        return target.__func__  # audit sees the function, not the descriptor
    return target


def _reorder_kwargs_for_surplus(
    ctx: _WrapperContext,
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
        ctx: The wrapper's context; the slots come from ``ctx.facts.target_positional_names`` and
            ``target_accepts_var_positional``, and the source named in the curated ``TypeError`` is ``ctx.source``.
        target_func: The forwarding target; its signature is read only when the facts carry no slot names.
        surplus: Positional tail beyond the source's named positional parameters
            (``args[ctx.facts.source_var_positional_prefix:]``).
        resolved_kwargs: Final kwargs assembled by :func:`_build_call_plan`.

    Returns:
        Tuple of ``(pos_args, kw_args)``; the caller appends ``surplus`` after ``pos_args``.

    Raises:
        TypeError: When the target declares no ``*args`` and its unfilled positional slots
            cannot absorb the surplus — raising loudly instead of silently dropping data.

    """
    positional_names = ctx.facts.target_positional_names
    accepts_var_positional = ctx.facts.target_accepts_var_positional
    if positional_names is None:
        # Not precomputed (hand-built facts, or a target signature unreadable at decoration): read it now, so an
        # unreadable signature raises its own error on the forwarded call.
        positional_names, accepts_var_positional = _leading_positional_slots(target_func)
    source = ctx.source
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


def _var_positional_source_kwargs(ctx: _WrapperContext, plan: _CallPlan) -> dict[str, Any]:
    """Return the keywords a source declaring ``*args`` receives next to the caller's own positional tuple.

    Only the caller-supplied keywords are used, never ``resolved_kwargs``, which also holds the positional-to-keyword
    conversions that would double-pass with ``*args``.  A rewritten call applies the ``args_mapping`` rename to them (a
    no-op for a caller already on the new names) and merges ``args_extra`` last, so the injected keywords reach the
    source whether or not the caller used a deprecated name.

    """
    if plan.short_circuit:
        return plan.original_kwargs
    mapping = ctx.cfg.args_mapping or {}
    kw_args = {
        (mapping.get(k) or k): v for k, v in plan.original_kwargs.items() if k not in mapping or mapping[k] is not None
    }
    if ctx.cfg.args_extra:
        kw_args.update(ctx.cfg.args_extra)
    return kw_args


def _resolve_source_call_shape(
    ctx: _WrapperContext,
    plan: _CallPlan,
    args: tuple[Any, ...],
) -> tuple[list[Any], dict[str, Any]]:
    """Return the ``(pos_args, kw_args)`` shape for invoking the *source* body.

    Covers the short-circuit (migrated caller) and no-target (NOTIFY / ARGS_REMAP) branches:

    - A :attr:`~deprecate._types._CallPlan.verbatim` plan returns the caller's own positional tuple and keywords
      unchanged — no rename, no injected extras and no defaults merge apply, so no split is needed either.
    - ``*args`` sources keep their original positional tuple; the keywords come from
      :func:`_var_positional_source_kwargs`.
    - Sources declaring POSITIONAL_ONLY params get those split back out of the resolved
      kwargs — the wrapper converted them to keyword form internally, but the source body
      cannot accept them as keywords.
    - All other sources are invoked with the resolved kwargs only.

    """
    if plan.verbatim:
        return list(args), plan.original_kwargs
    facts = ctx.facts
    if facts.source_has_var_positional:
        return list(args), _var_positional_source_kwargs(ctx, plan)
    if facts.source_positional_only:
        return _split_positional_only_kwargs(
            facts.source_positional_only_order, plan.resolved_kwargs, facts.source_positional_only
        )
    return [], plan.resolved_kwargs


def _resolve_target_call_shape(
    ctx: _WrapperContext,
    plan: _CallPlan,
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
    facts = ctx.facts
    surplus = args[facts.source_var_positional_prefix :] if facts.source_has_var_positional else ()
    if surplus:
        pos_args, kw_args = _reorder_kwargs_for_surplus(ctx, target_func, surplus, plan.resolved_kwargs)
        return [*pos_args, *surplus], kw_args
    if facts.target_positional_only:
        return _split_positional_only_kwargs(
            facts.target_positional_only_order, plan.resolved_kwargs, facts.target_positional_only
        )
    return [], plan.resolved_kwargs


async def _invoke_async(ctx: _WrapperContext, plan: _CallPlan, args: tuple[Any, ...]) -> Any:  # noqa: ANN401
    """Dispatch async call after :func:`_build_call_plan` has resolved the outcome."""
    if plan.short_circuit or plan.target_func is None:
        pos_args, kw_args = _resolve_source_call_shape(ctx, plan, args)
        return await ctx.source(*pos_args, **kw_args)
    pos_args, kw_args = _resolve_target_call_shape(ctx, plan, args)
    # Sync target under async source: invoke directly so callers can migrate without forcing
    # every legacy target to be redeclared ``async def``.
    if inspect.iscoroutinefunction(plan.target_func):
        return await plan.target_func(*pos_args, **kw_args)
    return plan.target_func(*pos_args, **kw_args)


def _invoke_sync(ctx: _WrapperContext, plan: _CallPlan, args: tuple[Any, ...]) -> Any:  # noqa: ANN401
    """Dispatch sync call after :func:`_build_call_plan` has resolved the outcome."""
    if plan.short_circuit or plan.target_func is None:
        pos_args, kw_args = _resolve_source_call_shape(ctx, plan, args)
        return ctx.source(*pos_args, **kw_args)
    if inspect.iscoroutinefunction(plan.target_func):
        raise TypeError(
            f"Async target `{_callable_display_name(plan.target_func)[0]}` cannot be invoked from a sync wrapper."
            f" Declare `{ctx.source.__name__}` as `async def`, or replace the target with a sync callable."
        )
    pos_args, kw_args = _resolve_target_call_shape(ctx, plan, args)
    return plan.target_func(*pos_args, **kw_args)
