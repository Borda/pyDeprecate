"""Call-facts precomputation and call-plan engine for callable deprecation.

Signature facts computed at decoration time feed the call-time dispatch core (``_build_call_plan`` and the sync/async
invokers) that forwards a deprecated call to its replacement.  :mod:`deprecate.routine` builds its ``packing`` closures
and the public ``deprecated`` / ``deprecated_callable`` decorators on top of these helpers.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import contextlib
import copy
import inspect
import warnings
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
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
)
from deprecate._validate import _MAJOR_BREAK_VERSION, _classmethod_target_error, _warn_target_positional_only
from deprecate.messaging import (
    _callable_display_name,
    _consume_argument_warn_budget,
    _consume_warn_budget,
    _raise_warn_arguments,
    _raise_warn_callable,
)
from deprecate.utils import _partial_chain, _remap_kwargs, _SigFacts, _signature_facts

POSITIONAL_ONLY = Parameter.POSITIONAL_ONLY
POSITIONAL_OR_KEYWORD = Parameter.POSITIONAL_OR_KEYWORD


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
        target_arg_names: Pre-computed target parameter names (see :func:`_build_call_facts`).  When
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
        target_facts = _signature_facts(target, empty_on_error=True)
        target_arg_names = target_facts.names
        accepts_var_positional, accepts_var_keyword = target_facts.var_positional, target_facts.var_keyword

    missed = [arg for arg in kwargs if arg not in target_arg_names]
    if missed and not accepts_var_keyword:
        if not accepts_var_positional:
            raise TypeError(f"Failed mapping of `{source.__name__}`, arguments not accepted by target: {missed}")
        raise TypeError(
            f"Failed mapping of `{source.__name__}`, arguments not accepted by target (target accepts *args but "
            f"these keyword arguments are not allowed): {missed}"
        )
    return target


def _raise_positional_arity_error(
    func: Callable, positional_names: tuple[str, ...], defaults: Mapping[str, Any], n_args: int
) -> NoReturn:
    """Raise the curated ``TypeError`` for a call that passes more positional arguments than ``func`` declares.

    Args:
        func: Callable whose positional slots were exceeded; the message names it by ``__qualname__``.
        positional_names: Names of the positional-only and positional-or-keyword parameters of ``func``, in order.
        defaults: Each parameter of ``func`` that has a default, mapped to that default.
        n_args: Number of positional arguments the caller passed.

    Raises:
        TypeError: Always; the message states the accepted count, or the accepted range when some slots have
            defaults.

    """
    required_positional_names = [name for name in positional_names if name not in defaults]
    if len(required_positional_names) == len(positional_names):
        raise TypeError(
            f"{func.__qualname__}() takes {len(positional_names)} positional argument(s) but got "
            f"{n_args} positional argument(s)"
        )
    raise TypeError(
        f"{func.__qualname__}() takes {len(required_positional_names)} to {len(positional_names)} "
        f"positional argument(s) but got {n_args} positional argument(s)"
    )


def _check_source_arity(source: Callable, args: tuple[Any, ...], facts: _CallFacts) -> None:
    """Reject surplus positional arguments before any deprecation warning fires or any warn budget is spent.

    A caller passing one positional argument too many gets the same curated ``TypeError`` whether or not the wrapper
    goes on to rewrite the call, so the check runs on the decoration-time parameter names instead of on the converted
    keyword arguments.  A source declaring ``*args`` accepts any number of positionals and is never rejected.

    Args:
        source: The decorated callable.
        args: The positional arguments the caller passed.
        facts: The wrapper's signature facts; ``source_positional_names``, ``source_has_var_positional`` and
            ``source_defaults`` are read from them.

    Raises:
        TypeError: When ``source`` has no ``*args`` and ``args`` is longer than ``facts.source_positional_names``.

    Example:
        >>> def source(a, b=0): pass
        >>> facts = _CallFacts(source_positional_names=("a", "b"), source_defaults={"b": 0})
        >>> _check_source_arity(source, (1, 2), facts)
        >>> _check_source_arity(source, (1, 2, 3), facts)
        Traceback (most recent call last):
        ...
        TypeError: source() takes 1 to 2 positional argument(s) but got 3 positional argument(s)

    """
    limit = facts.source_positional_limit
    if limit is None or len(args) <= limit:
        return
    _raise_positional_arity_error(source, facts.source_positional_names, facts.source_defaults, len(args))


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
    mapping_names = (dep_cfg.args_mapping or {}).keys()
    # No old name passed by keyword, none filling a positional slot; ``isdisjoint`` runs at C speed on a dict.
    return mapping_names.isdisjoint(kwargs) and mapping_names.isdisjoint(source_positional_names[: len(args)])


def _update_kwargs_with_args(fn_args: tuple[Any, ...], fn_kwargs: dict[str, Any], facts: _CallFacts) -> dict[str, Any]:
    """Convert positional arguments to keyword arguments using the decoration-time parameter names.

    This helper takes positional arguments and converts them to keyword arguments by matching them with the source's
    positional parameter names, read from its signature when it was decorated.  This enables consistent argument
    handling in the deprecation wrapper.

    The caller has already rejected surplus positionals with :func:`_check_source_arity` (:func:`_build_call_plan` does
    so before any other work), so this helper does not check the arity again.  Values beyond the named positional
    parameters are left unconverted instead of raising.

    Args:
        fn_args: Tuple of positional arguments passed to the function.
        fn_kwargs: Dictionary of keyword arguments already passed.
        facts: The wrapper's signature facts (see :func:`_check_source_arity`).

    Returns:
        Dictionary combining converted positional arguments and existing kwargs, where positional args are now mapped
        to their parameter names.  Conversion stops at the var-positional parameter (``*args``) because its values
        cannot be safely represented as keyword arguments.

    Example:
        >>> from pprint import pprint
        >>> def example_func(a, b, c=3): pass
        >>> facts = _build_call_facts(example_func, TargetMode.NOTIFY, None)
        >>> pprint(_update_kwargs_with_args((1, 2), {'c': 5}, facts))
        {'a': 1, 'b': 2, 'c': 5}

    """
    if not fn_args:
        return fn_kwargs
    updated_kwargs = dict(fn_kwargs)
    # Positional parameters precede ``*args`` in every signature, so ``zip`` stops where the old loop broke off.
    updated_kwargs.update(zip(facts.source_positional_names, fn_args))
    return updated_kwargs


def _update_kwargs_with_defaults(defaults: Mapping[str, Any], fn_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Merge function default values with provided keyword arguments.

    This helper fills in the default parameter values, read from the signature when the function was decorated, for
    any parameters not explicitly provided.  Provided kwargs take precedence over defaults.

    Args:
        defaults: Each parameter that has a default, mapped to that default, in declaration order.
        fn_kwargs: Dictionary of keyword arguments provided by caller.

    Returns:
        Dictionary with defaults merged with provided kwargs, where provided values override defaults.

    Example:
        >>> from pprint import pprint
        >>> def example_func(a=1, b=2, c=3): pass
        >>> pprint(_update_kwargs_with_defaults(_signature_facts(example_func).defaults, {'b': 20}))
        {'a': 1, 'b': 20, 'c': 3}

    Note:
        Parameters without defaults (inspect.Parameter.empty) are not included in the result.

    """
    return {**defaults, **fn_kwargs}


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
    target_defaults = _signature_facts(normalized_target).defaults
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
    source_defaults: Mapping[str, Any],
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
        source_defaults: The decorated callable's defaults, read from its signature at decoration
            (:attr:`~deprecate._types._CallFacts.source_defaults`).
        normalized_target: The normalised target (callable or :class:`TargetMode`).
        kwargs: The caller's arguments, positionals already mapped to their parameter names.
        args_mapping: The wrapper's ``args_mapping``, or ``None``.
        forward_defaults: Facts precomputed at decoration time (:func:`_precompute_forward_defaults`). ``None``
            derives them from ``normalized_target`` and ``args_mapping`` on the spot, on every call.

    Returns:
        The caller's arguments plus every source default the target does not own.

    """
    full_defaults = _update_kwargs_with_defaults(source_defaults, kwargs)
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
            :func:`~deprecate._validate._normalize_target`.
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
        fast_notify: Derived; ``True`` for ``TargetMode.NOTIFY`` unless ``as_exception=True`` makes every call fatal,
            so a call after the warn budget is spent may skip the call plan (:func:`_skips_call_plan`).
        fast_remap: Derived; ``True`` for ``TargetMode.ARGS_REMAP`` without ``args_extra`` over a source that is not
            itself a ``@deprecated`` wrapper, so a caller using none of the deprecated names may skip the call plan.
        source_receiver: Derived; for a ``classmethod`` descriptor target, the name of the source's first positional
            parameter, which receives the class whatever it is called (``cls``, ``klass``, ``mcs``); ``None``
            otherwise.  Its value is checked to be a class on every call (:func:`_check_classmethod_receiver`).
        target_receiver: Derived; for a ``classmethod`` descriptor target, the name of the target's own receiver
            parameter, under which the source's receiver is forwarded (:func:`_forward_receiver_name`); ``None``
            when there is none or the target signature is unreadable.

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
    fast_notify: bool = field(init=False)
    fast_remap: bool = field(init=False)
    source_receiver: Optional[str] = field(init=False)
    target_receiver: Optional[str] = field(init=False)

    def __post_init__(self) -> None:
        """Derive the per-wrapper flags the call path would otherwise recompute on every call."""
        forwards = callable(self.target)
        object.__setattr__(self, "cycle_guarded", forwards)
        object.__setattr__(self, "remap_active", forwards or self.target is TargetMode.ARGS_REMAP)
        object.__setattr__(self, "fast_notify", self.target is TargetMode.NOTIFY and self.cfg.as_exception is not True)
        object.__setattr__(
            self,
            "fast_remap",
            self.target is TargetMode.ARGS_REMAP and not self.cfg.args_extra and not self.is_stacked,
        )
        source_receiver, target_receiver = _classmethod_receivers(self.raw_target, self.facts)
        object.__setattr__(self, "source_receiver", source_receiver)
        object.__setattr__(self, "target_receiver", target_receiver)


def _classmethod_receivers(
    raw_target: Union[bool, None, Callable[..., Any], TargetMode, staticmethod, classmethod],
    facts: _CallFacts,
) -> tuple[Optional[str], Optional[str]]:
    """Name the parameters that carry the class from the source to a ``classmethod`` descriptor target.

    A classmethod's receiver is its first positional parameter, whatever it is called, so the names are read by
    position rather than matched against ``cls``.  The decoration guard
    (:func:`~deprecate._validate._normalize_target`) already rejected a source with no leading positional parameter.

    Args:
        raw_target: The ``target`` argument as given to ``@deprecated``, before the descriptor was unwrapped.
        facts: Signature facts of the source and the target.

    Returns:
        Tuple of ``(source_receiver, target_receiver)``: both ``None`` unless ``raw_target`` is a ``classmethod``;
        ``target_receiver`` is also ``None`` when the target has no positional parameter or its signature is
        unreadable.

    Example:
        >>> def make(kls, value): pass
        >>> def old(klass, value): pass
        >>> _classmethod_receivers(classmethod(make), _build_call_facts(old, make, None))
        ('klass', 'kls')
        >>> _classmethod_receivers(make, _build_call_facts(old, make, None))
        (None, None)

    """
    if not isinstance(raw_target, classmethod) or not facts.source_positional_names:
        return None, None
    target_names = facts.target_positional_names
    return facts.source_positional_names[0], (target_names[0] if target_names else None)


#: Marks a receiver with no value: neither the call nor the source's default supplies one
#: (:func:`_check_classmethod_receiver`), or a forwarded call did not take it out of the keywords
#: (:func:`_resolve_forward_kwargs`).
_NO_RECEIVER = object()


def _check_classmethod_receiver(ctx: _WrapperContext, kwargs: Mapping[str, Any]) -> None:
    """Raise when the value a source hands to its ``classmethod`` descriptor target as the receiver is not a class.

    A plain source, or a static one written ``@staticmethod`` above ``@deprecated``, pointed at a ``classmethod``
    descriptor decorates fine, because the receiver's name is free, but its first argument is a caller's value:
    forwarding a value that is not a class would run the replacement with a wrong ``cls``.  Only the type is checked,
    so a class the caller passes as that first argument is forwarded as the receiver; the decorator cannot tell such a
    source from a classmethod.  When the call leaves the first argument out, the source's default for it is checked
    instead, because the forwarded call fills that default in later.  Runs before any warning is emitted or any budget
    is spent, like :func:`_check_source_arity`.

    The caller's keywords must not carry the target's receiver name either, when it differs from the source's: the
    receiver is forwarded under that name, so the keyword would take the class's place.  A later source parameter of
    that name is rejected at decoration (``_validate._receiver_name_clash``), so at this point such a keyword can only
    arrive through the source's ``**kwargs``.  A name that is an ``args_mapping`` key is exempt: the rename moves it
    away before the receiver is forwarded.

    Args:
        ctx: The wrapper's context; :attr:`_WrapperContext.source_receiver` must be set.
        kwargs: The caller's arguments, positionals already mapped to their parameter names.

    Raises:
        TypeError: When the receiver is missing with no default, when it or its default is not a class, or when the
            caller passed the target's receiver name as a keyword.

    """
    name = cast(str, ctx.source_receiver)
    target_name = ctx.target_receiver
    # Defaults are merged only on the forward path, after the warning, so look the default up here.
    receiver = kwargs[name] if name in kwargs else ctx.facts.source_defaults.get(name, _NO_RECEIVER)
    if receiver is _NO_RECEIVER:
        problem = f"the call supplied no value for its first parameter `{name}`"
    elif not isinstance(receiver, type):
        problem = f"its first parameter `{name}` received a value of type `{type(receiver).__name__}`, not a class"
    elif (
        target_name is not None
        and target_name != name
        and target_name in kwargs
        and target_name not in (ctx.cfg.args_mapping or {})
    ):
        problem = (
            f"the call also passed `{target_name}`, the target's receiver name, as a keyword argument that would"
            " replace the class; drop that keyword"
        )
    else:
        return
    raise _classmethod_target_error(ctx.source.__name__, cast(Callable, ctx.target), problem)


def _forward_receiver_name(ctx: _WrapperContext, kwargs: dict[str, Any], receiver: Any) -> dict[str, Any]:  # noqa: ANN401
    """Put the source's receiver back into the forwarded keywords under the target's receiver name.

    The call path forwards by keyword, so a source receiver named ``klass`` would reach a target whose receiver is
    ``cls`` as a keyword the target does not accept.  :func:`_resolve_forward_kwargs` takes the receiver out of the
    keywords before the ``args_mapping`` rename and the ``args_extra`` merge and hands it here afterwards, so neither
    can move or replace it: a rename onto a target parameter that shares the source receiver's name
    (``args_mapping={"old_value": "klass"}`` for a target ``make(cls, klass)``) would otherwise collide with the class.
    Put first under the target's receiver name, the class sits in the target's receiver slot, and every later step
    (the target-accepts check, the POSITIONAL_ONLY split, the ``*args`` surplus reorder) handles it as it handles a
    ``cls`` named on both sides.

    Args:
        ctx: The wrapper's context; :attr:`_WrapperContext.target_receiver` must be set.
        kwargs: The keyword arguments for the target, without the receiver.
        receiver: The class the source received.

    Returns:
        A new dict holding the receiver under the target's receiver name, followed by ``kwargs``.

    Raises:
        TypeError: When ``kwargs`` already holds the target's receiver name, which ``args_mapping`` or ``args_extra``
            put there: forwarding it would replace the class or drop that value.

    """
    name = cast(str, ctx.target_receiver)
    if name in kwargs:
        raise TypeError(
            f"Failed mapping of `{ctx.source.__name__}`: the target's receiver `{name}` would receive both the class"
            " and a value from `args_mapping` or `args_extra`. Map that value to another target parameter."
        )
    return {name: receiver, **kwargs}


class _CycleEntry:
    """Track one guarded call's lifetime across contexts copied while it is running.

    A task or ``contextvars.copy_context()`` made inside the call keeps the entry it copied after the call returned.
    The copy shares this object, so clearing :attr:`active` on exit expires the entry in every copy at once.  A plain
    ``__slots__`` class rather than a dataclass: one is created per guarded call, and its construction shows up in the
    per-call benchmark.

    Attributes:
        source_id: ``id()`` of the guarded wrapper's source callable.
        active: ``True`` while the call is in flight.

    """

    __slots__ = ("active", "source_id")

    def __init__(self, source_id: int) -> None:
        self.source_id = source_id
        self.active = True


#: Handle returned by :func:`_enter_cycle_guard`: the call's entry and the token that installed the chain containing it,
#: or ``None`` when the wrapper does not run under the guard.
_CycleGuardHandle = Optional[tuple[_CycleEntry, "Token[Optional[tuple[_CycleEntry, ...]]]"]]

# ContextVar holding the chain of guarded calls in flight for the current async task or sync call stack.
# The chain is an immutable tuple, replaced on every entry and restored on every exit, never mutated in place, so a
# task or context spawned inside a guarded call starts from a snapshot of the chain and sets its own successor
# without touching its siblings.  Sibling tasks therefore never see each other's calls as a cycle, while a call that
# re-enters a wrapper still in flight on its own chain, from the same stack or from a spawned task, does raise.
# The entries are shared with copied contexts only so that a finished call can expire in them (see ``_CycleEntry``).
# Only ``_enter_cycle_guard`` / ``_exit_cycle_guard`` below read or write it.
_cycle_detection: ContextVar[Optional[tuple[_CycleEntry, ...]]] = ContextVar("_cycle_detection", default=None)


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
    source_id = id(ctx.source)
    chain = _cycle_detection.get()
    entry = _CycleEntry(source_id)
    if chain:
        # Entries of calls that finished after a context was copied are dropped, so a stale copy cannot raise.
        chain = tuple(other for other in chain if other.active)
        if any(other.source_id == source_id for other in chain):
            source_name = getattr(ctx.source, "__qualname__", repr(ctx.source))
            raise RuntimeError(
                f"Circular deprecation cycle detected: `{source_name}` re-entered"
                " via its own target chain. Point to a non-deprecated final implementation."
            )
        chain += (entry,)
    else:
        chain = (entry,)
    return entry, _cycle_detection.set(chain)


def _exit_cycle_guard(ctx: _WrapperContext, handle: _CycleGuardHandle) -> None:
    """Undo :func:`_enter_cycle_guard`: expire the call's entry and restore the chain from before the call.

    ``ctx`` is not read; it stays so both guard functions take the wrapper context the same way.

    """
    if handle is None:
        return
    entry, token = handle
    entry.active = False
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
    # Positional parameters precede ``*args`` in every signature, so they are exactly the leading positional slots.
    facts = _signature_facts(func)
    return facts.positional_names, facts.var_positional


def _target_signature_facts(
    target: Union[Callable[..., Any], TargetMode],
) -> tuple[_SigFacts, Optional[Exception]]:
    """Read a normalised target's signature once, keeping apart a mode target and an unreadable callable.

    Args:
        target: The normalised target returned by :func:`~deprecate._validate._normalize_target`.

    Returns:
        Tuple of ``(facts, error)``.  A :class:`TargetMode` gives empty facts and no error; a callable whose signature
        cannot be read gives empty facts and the ``TypeError`` or ``ValueError`` the read raised, without its
        traceback, so its forwarded call can raise that error itself; any other callable gives its facts and no error.

    Example:
        >>> def target(a, /, b, *rest, c=1): pass
        >>> facts, error = _target_signature_facts(target)
        >>> facts.positional_names, sorted(facts.positional_only), error
        (('a', 'b'), ['a'], None)
        >>> _target_signature_facts(TargetMode.NOTIFY) == (_SigFacts(), None)
        True

    """
    if not callable(target):
        return _SigFacts(), None
    try:
        return _signature_facts(target), None
    except (TypeError, ValueError) as error:
        return _SigFacts(), error.with_traceback(None)


def _rebuild_exception(error: Exception) -> Exception:
    """Return a new exception of the same type as ``error`` without calling its constructor.

    Copies ``args`` and the instance ``__dict__``, so an error type whose constructor takes more than the message
    (and cannot be rebuilt from ``args``) keeps its attributes.  Values held in ``__slots__`` are not carried over.

    Raises:
        TypeError: When the type's ``__new__`` needs arguments.

    """
    rebuilt = type(error).__new__(type(error))
    rebuilt.args = error.args
    rebuilt.__dict__.update(vars(error))
    return rebuilt


def _fresh_signature_error(error: Exception) -> Exception:
    """Return a never-raised error equal to ``error``, so raising it again cannot touch ``error``.

    Tries, in order, :func:`copy.copy`, a rebuild that skips the constructor (:func:`_rebuild_exception`), and finally a
    plain ``TypeError`` or ``ValueError`` carrying the message.  The last step loses the error's own type but is still
    caught by the documented base type; no step returns ``error`` itself.

    """
    # Broad on purpose: a user-defined ``__reduce__``, ``__new__`` or ``__init__`` may raise anything.
    with contextlib.suppress(Exception):
        return copy.copy(error)
    with contextlib.suppress(Exception):
        return _rebuild_exception(error)
    return (TypeError if isinstance(error, TypeError) else ValueError)(*error.args)


def _raise_target_signature_error(facts: _CallFacts) -> None:
    """Raise, on a forwarded call, the error the target's signature read raised when the function was decorated.

    A target whose signature cannot be read still decorates; the forwarded call that needs the signature raises a
    fresh copy of the decoration-time error (:func:`_fresh_signature_error`), as the call-time read this replaces did.
    The stored error is never raised itself: raising one shared object would grow its traceback on every call and
    overwrite its ``__context__`` across threads.  Does nothing when the read succeeded or the facts were built by
    hand.

    Raises:
        TypeError: A copy of :attr:`~deprecate._types._CallFacts.target_signature_error`, when it is one.
        ValueError: A copy of :attr:`~deprecate._types._CallFacts.target_signature_error`, when it is one.

    """
    error = facts.target_signature_error
    if error is None:
        return
    raise _fresh_signature_error(error)


def _build_call_facts(
    source: Callable[..., Any],
    normalized_target: Union[Callable[..., Any], TargetMode],
    stream: Optional[Callable[..., Any]],
) -> _CallFacts:
    """Derive the :class:`~deprecate._types._CallFacts` of a source and its normalised target at decoration time.

    Reads the source signature and the target signature once each through :func:`~deprecate.utils._signature_facts`,
    so no call inspects either again.

    Args:
        source: The decorated callable.
        normalized_target: The normalised target returned by :func:`~deprecate._validate._normalize_target`.
        stream: Warning stream; the POSITIONAL_ONLY target warning of
            :func:`~deprecate._validate._warn_target_positional_only` fires through ``warnings.warn`` unless it is
            ``None``.

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
    source_facts = _signature_facts(source)
    target_facts, target_error = _target_signature_facts(normalized_target)
    _warn_target_positional_only(normalized_target, source, target_facts.positional_only, stream)
    # Source-side POSITIONAL_ONLY params: a call the wrapper rewrites (a triggered ARGS_REMAP rename, or ARGS_REMAP
    # with args_extra) converts positional args to kwargs internally, so the dispatcher must split these back out
    # before invoking the source body.  Verbatim calls keep the caller's shape and need no split.  Both sides keep
    # their declaration order only when they have POSITIONAL_ONLY params to split.
    return _CallFacts(
        source_positional_names=source_facts.positional_names,
        source_has_var_positional=source_facts.var_positional,
        source_var_positional_prefix=source_facts.var_positional_index or 0,
        source_positional_only=source_facts.positional_only,
        source_positional_only_order=source_facts.order if source_facts.positional_only else (),
        target_all_param_names=target_facts.names,
        target_accepts_var_positional=target_facts.var_positional,
        target_accepts_var_keyword=target_facts.var_keyword,
        target_positional_only=target_facts.positional_only,
        target_positional_only_order=target_facts.order if target_facts.positional_only else (),
        target_positional_names=None if target_error is not None else target_facts.positional_names,
        # A plain dict, not the read-only view: the record must stay picklable (a proxy pickles its facts).
        source_defaults=dict(source_facts.defaults),
        target_signature_error=target_error,
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
    last).  For a ``classmethod`` descriptor target with a readable receiver, the source's receiver is taken out after
    the defaults merge, so the rename and the extras never see it, and is put back under the target's receiver name
    after the extras (:func:`_forward_receiver_name`).

    Args:
        ctx: The wrapper's context.
        kwargs: The caller's arguments, positionals already mapped to their parameter names.
        reason_callable: ``True`` when the whole callable is deprecated (``NOTIFY`` or a callable target).

    Returns:
        Tuple of ``(kwargs, target_func)``; ``target_func`` is ``None`` unless the target is callable.

    """
    source, target, mapping, extra = ctx.source, ctx.target, ctx.cfg.args_mapping, ctx.cfg.args_extra
    if reason_callable:
        if ctx.forward_defaults is None:
            # Not derivable at decoration because the target signature is unreadable: raise that error now.
            _raise_target_signature_error(ctx.facts)
        kwargs = _merge_source_defaults(ctx.facts.source_defaults, target, kwargs, mapping, ctx.forward_defaults)
    # Taken out after the defaults merge, which fills a defaulted receiver by its source name; a target without a
    # readable receiver keeps it under that name.  ``kwargs`` is the merge's own new dict here: a set
    # ``target_receiver`` means a classmethod descriptor target, hence a callable target and a callable reason.
    receiver = _NO_RECEIVER
    if ctx.target_receiver is not None:
        receiver_name = cast(str, ctx.source_receiver)
        receiver = kwargs.pop(receiver_name, ctx.facts.source_defaults.get(receiver_name, _NO_RECEIVER))
    if mapping and ctx.remap_active:
        kwargs = _remap_kwargs(mapping, kwargs, name=source.__name__, stream=ctx.stream)
    if extra and ctx.remap_active:
        kwargs.update(extra)
    if receiver is not _NO_RECEIVER:
        kwargs = _forward_receiver_name(ctx, kwargs, receiver)
    if not callable(target):
        return kwargs, None
    facts = ctx.facts
    if kwargs:
        # Empty cached names can mean an unreadable signature, not a target that rejects every keyword.
        _raise_target_signature_error(facts)
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


def _skips_call_plan(ctx: _WrapperContext, args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> bool:
    """Decide whether this call warns nothing and reaches the source unchanged, so the call plan can be skipped.

    The wrappers ask after ``skip_if`` and before the cycle guard, which neither mode below needs (it guards callable
    targets only).  A call qualifies only when :func:`_build_call_plan` would emit nothing and invoke
    ``source(*args, **kwargs)`` as made:

    - ``TargetMode.NOTIFY`` (:attr:`_WrapperContext.fast_notify`) once ``stream`` is ``None`` or the warn budget is
      spent, and only while the deprecation is not fatal: the fatal switch is resolved on every call, because the
      global, the :func:`~deprecate._fatal.as_exceptions` scope and the environment seed can change at runtime.
    - ``TargetMode.ARGS_REMAP`` (:attr:`_WrapperContext.fast_remap`) when the caller uses none of the deprecated names
      (:func:`_is_verbatim_call`), the call the plan short-circuits before any warning or fatal check.

    Neither qualifies while the one-time misconfiguration warning is still due, nor with surplus positionals, for
    which the plan raises the curated ``TypeError`` of :func:`_check_source_arity`.

    Args:
        ctx: The wrapper's context.
        args: The positional arguments the caller passed.
        kwargs: The keyword arguments the caller passed.

    Returns:
        ``True`` when the wrapper may call ``ctx.source(*args, **kwargs)`` directly.

    """
    if not (ctx.fast_notify or ctx.fast_remap):
        return False
    state, facts = ctx.state, ctx.facts
    misconfig_due = ctx.cfg.misconfigured and ctx.stream and not state.warned_misconfigured
    limit = facts.source_positional_limit
    if misconfig_due or (limit is not None and len(args) > limit):
        return False
    if ctx.fast_notify:
        spent = not ctx.stream or (ctx.num_warns >= 0 and state.warned_calls >= ctx.num_warns)
        return spent and not _resolve_as_exception(ctx.cfg.as_exception)
    return _is_verbatim_call(ctx.target, ctx.cfg, args, kwargs, facts.source_positional_names)


def _warn_misconfigured_once(ctx: _WrapperContext) -> None:
    """Emit the one-time call-time ``UserWarning`` of a misconfigured wrapper, unless ``stream`` is ``None``.

    The flag check-then-set runs under ``ctx.state.lock``, so concurrent first calls flip it exactly once; only the call
    that flipped it emits, outside the lock.

    """
    state = ctx.state
    # Lock-free pre-check: a correctly configured or already-notified wrapper never touches the lock.
    if not (ctx.cfg.misconfigured and ctx.stream) or state.warned_misconfigured:
        return
    with state.lock:
        flipped = not state.warned_misconfigured
        state.warned_misconfigured = True
    if flipped:
        warnings.warn(
            f"'{ctx.source.__name__}' has an invalid deprecation configuration;"
            f" verify your `@deprecated(target=...)` arguments. Will be TypeError in {_MAJOR_BREAK_VERSION}.",
            UserWarning,
            stacklevel=_caller_stacklevel(),
        )


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
    :func:`_raise_warn_arguments`, naming only the arguments still under their own budget (a fatal deprecation names
    all of them); the budget check-then-increment runs under ``ctx.state.lock``.

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
    should_warn, arguments = fatal, reason_argument
    if not fatal and ctx.stream and (num_warns < 0 or reason_argument or state.warned_calls < num_warns):
        with state.lock:
            if reason_callable:
                should_warn = _consume_warn_budget(state, num_warns, reason_callable=True)
            else:
                # Name only the arguments still under their own budget: one already warned about up to
                # ``num_warns`` times is not listed again when it reappears next to a fresh one.
                arguments = _consume_argument_warn_budget(state, num_warns, reason_argument)
                should_warn = bool(arguments)
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
    elif arguments:
        _raise_warn_arguments(
            stream=ctx.stream,
            source=ctx.source,
            arguments=arguments,
            deprecated_in=cfg.deprecated_in,
            remove_in=cfg.remove_in,
            message_template=cfg.message_template,
            as_exception=fatal,
        )


def _build_call_plan(ctx: _WrapperContext, args: tuple[Any, ...], kwargs: dict[str, Any]) -> _CallPlan:
    """Compute the dispatch plan shared by the sync and async wrappers inside :func:`deprecated`.

    The wrappers differ only by ``await`` on the final source or target call.  In order, this emits the one-time
    misconfiguration warning (:func:`_warn_misconfigured_once`), rejects surplus positionals
    (:func:`_check_source_arity`), decides the verbatim pass-through (:func:`_is_verbatim_call`), maps positionals to
    keywords, resolves the warning reasons (:func:`_resolve_reasons`), short-circuits a migrated caller, emits the
    budgeted deprecation warning (:func:`_emit_call_warning`) and assembles the forwarded keywords
    (:func:`_resolve_forward_kwargs`).

    Side effects: mutates ``ctx.state`` (optionally bumps ``warned_calls`` / ``warned_args``, and sets
    ``warned_misconfigured`` on the first misconfiguration warning) and emits the warnings named above.

    Args:
        ctx: The wrapper's decoration-time context.
        args: The positional arguments the caller passed to the wrapper.
        kwargs: The keyword arguments the caller passed to the wrapper.

    Returns:
        A :class:`_CallPlan` describing the resolved dispatch outcome.  When :attr:`_CallPlan.verbatim` is set, the
        positionals were not converted to keywords and the source is invoked with the caller's arguments unchanged.

    """
    _warn_misconfigured_once(ctx)
    facts = ctx.facts
    # Surplus positionals raise the curated TypeError before any warning fires or any budget is spent.
    _check_source_arity(ctx.source, args, facts)
    # No rename, no injected extras and no forwarding target: the source gets the caller's arguments unchanged.
    verbatim = _is_verbatim_call(ctx.target, ctx.cfg, args, kwargs, facts.source_positional_names)
    # *args sources need the unremapped tuple; remapping happens on kwargs only.
    original_kwargs = dict(kwargs)
    if not verbatim:
        kwargs = _update_kwargs_with_args(args, kwargs, facts)
    if ctx.source_receiver is not None:
        # A classmethod descriptor target needs a class as the receiver; reject any other value before warning.
        _check_classmethod_receiver(ctx, kwargs)
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
        target_func: The forwarding target; its signature is read only when hand-built facts carry no slot names.
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
        # A target signature unreadable at decoration raises its error on this forwarded call; hand-built facts carry
        # no slot names and no error, so the slots are read from the target here.
        _raise_target_signature_error(ctx.facts)
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


def _var_positional_source_call(
    ctx: _WrapperContext, plan: _CallPlan, args: tuple[Any, ...]
) -> tuple[list[Any], dict[str, Any]]:
    """Return the ``(pos_args, kw_args)`` a source declaring ``*args`` receives.

    Only the caller-supplied keywords are used, never ``resolved_kwargs``, which also holds the positional-to-keyword
    conversions that would double-pass with ``*args``.  A rewritten call applies the ``args_mapping`` rename to them (a
    no-op for a caller already on the new names; an explicit new-name value wins over a renamed old one) and merges
    ``args_extra`` last, so the injected keywords reach the source whether or not the caller used a deprecated name.

    The caller's positional tuple fills the leading named parameters, so a name the rewrite adds as a keyword while
    the tuple already binds it would be passed twice.  It is reconciled against the bound prefix instead, with the same
    precedence a source without ``*args`` gets:

    - a keyword the *rename* produced for a positionally bound name is dropped, because the positional value wins; a
      keyword the caller passed itself is kept, so a genuine duplicate still raises Python's own ``TypeError``;
    - an ``args_extra`` value for a positionally bound name replaces that positional value, because ``args_extra``
      wins over both.

    """
    if plan.short_circuit:
        return list(args), plan.original_kwargs
    pos_args = list(args)
    bound = ctx.facts.source_positional_names[: len(args)]
    original = plan.original_kwargs
    # Silent rename (``stream=None``): these keywords are a subset of the resolved keywords, whose rename in
    # _resolve_forward_kwargs already emitted every collision UserWarning once for this call.
    kw_args = _remap_kwargs(ctx.cfg.args_mapping or {}, original, name=ctx.source.__name__, stream=None)
    for name in bound:
        if name not in original:
            kw_args.pop(name, None)
    extra = ctx.cfg.args_extra
    if extra:
        kw_args.update(extra)
        for index, name in enumerate(bound):
            if name in extra:
                pos_args[index] = kw_args.pop(name)
    return pos_args, kw_args


def _resolve_source_call_shape(
    ctx: _WrapperContext,
    plan: _CallPlan,
    args: tuple[Any, ...],
) -> tuple[list[Any], dict[str, Any]]:
    """Return the ``(pos_args, kw_args)`` shape for invoking the *source* body.

    Covers the short-circuit (migrated caller) and no-target (NOTIFY / ARGS_REMAP) branches:

    - A :attr:`~deprecate._types._CallPlan.verbatim` plan returns the caller's own positional tuple and keywords
      unchanged — no rename, no injected extras and no defaults merge apply, so no split is needed either.
    - ``*args`` sources keep their original positional tuple, reconciled with the rewritten keywords by
      :func:`_var_positional_source_call` so no parameter is bound twice.
    - Sources declaring POSITIONAL_ONLY params get those split back out of the resolved
      kwargs — the wrapper converted them to keyword form internally, but the source body
      cannot accept them as keywords.
    - All other sources are invoked with the resolved kwargs only.

    """
    if plan.verbatim:
        return list(args), plan.original_kwargs
    facts = ctx.facts
    if facts.source_has_var_positional:
        return _var_positional_source_call(ctx, plan, args)
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
    """Dispatch sync call after :func:`_build_call_plan` has resolved the outcome.

    A coroutine function target raises :class:`TypeError`, since nothing here awaits it; the advice names the fix that
    fits the source, which for an async generator source is a target returning an async iterable.

    """
    if plan.short_circuit or plan.target_func is None:
        pos_args, kw_args = _resolve_source_call_shape(ctx, plan, args)
        return ctx.source(*pos_args, **kw_args)
    if inspect.iscoroutinefunction(plan.target_func):
        target_name = _callable_display_name(plan.target_func)[0]
        # An async generator source is already ``async def``; its target must return an async iterable instead.
        if inspect.isasyncgenfunction(ctx.source):
            raise TypeError(
                f"Coroutine function target `{target_name}` cannot replace async generator `{ctx.source.__name__}`:"
                " an async generator's target must return an async iterable. Make the target an async generator"
                " function, or a callable that returns an async iterator."
            )
        raise TypeError(
            f"Async target `{target_name}` cannot be invoked from a sync wrapper."
            f" Declare `{ctx.source.__name__}` as `async def`, or replace the target with a sync callable."
        )
    pos_args, kw_args = _resolve_target_call_shape(ctx, plan, args)
    return plan.target_func(*pos_args, **kw_args)
