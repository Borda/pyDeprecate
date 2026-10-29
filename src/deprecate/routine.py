"""Function/method deprecation decorators.

This module hosts :func:`deprecated_callable` (the strict callable-only decorator) and :func:`deprecated`
(the friendly front door that dispatches classes to :func:`~deprecate.proxy.deprecated_class` and routes
callables to :func:`deprecated_callable`), plus the decoration-time ``packing`` descriptors.  The target
resolution lives in :mod:`deprecate._validate` and the call-plan machinery in :mod:`deprecate._dispatch`; the warning
templates and emitters live in :mod:`deprecate.messaging`.  The legacy import path :mod:`deprecate.deprecation`
re-exports the public decorators for backward compatibility.

Callable targets with unreadable signatures can be decorated; forwarding that needs their signature raises the
original introspection error at call time, including keyword validation without argument remapping.

Key Components:
    - :func:`~deprecate.routine.deprecated_callable`: Canonical strict decorator (rejects classes)
    - :func:`~deprecate.routine.deprecated`: Front-door dispatcher over callables and classes
    - Warning templates for different deprecation scenarios
    - Internal helpers for argument mapping and warning management

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import inspect
import warnings
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Generator, Iterator
from enum import Enum
from functools import cached_property, wraps
from typing import Any, Callable, Literal, Optional, TypeVar, Union, cast

from deprecate._dispatch import (
    _build_call_facts,
    _build_call_plan,
    _enter_cycle_guard,
    _exit_cycle_guard,
    _invoke_async,
    _invoke_sync,
    _precompute_forward_defaults,
    _skips_call_plan,
    _WrapperContext,
)
from deprecate._frames import _caller_stacklevel
from deprecate._properties import _DeprecatedProperty
from deprecate._types import (
    DeprecationConfig,
    TargetMode,
    _CallFacts,
    _DeprecatedCallable,
    _has_deprecation_meta,
    _WrapperState,
)
from deprecate._validate import (
    _DESCRIPTOR_SOURCE_TYPES,
    _check_cross_class_method_target,
    _normalize_target,
    _reject_bare_decorator,
    _resolve_stored_target,
    _validate_callable_mode,
    _warn_stacking_misconfiguration,
)
from deprecate.docstring.inject import _update_docstring_with_deprecation, normalize_docstring_style
from deprecate.messaging import (
    _eval_skip_if,
    _render_static_deprecation_message,
    _resolve_message_template_alias,
    _source_display_name,
    _validate_message_template,
    deprecation_warning,
)
from deprecate.utils import _unwrap_descriptor_target

__all__ = ["deprecated_callable"]

#: Decorated source type: preserve callable/descriptor signatures while rejecting non-callable values statically.
_SourceT = TypeVar("_SourceT", bound=Union[Callable[..., Any], classmethod, staticmethod, property, cached_property])


def _reject_property_options(
    source: property,
    target: Union[bool, None, Callable, TargetMode, staticmethod, classmethod],
    args_mapping: Optional[dict[str, Optional[str]]],
    args_extra: Optional[dict[str, Any]],
) -> None:
    """Reject a re-deprecated ``property`` and the ``@deprecated`` options a ``property`` source cannot honour.

    First guard of the ``property`` arm of :func:`_packing_descriptor`, run before any accessor is wrapped. The
    checks run in this order: an already-deprecated property, then ``args_mapping``, ``args_extra``, a callable
    ``target``, ``TargetMode.ARGS_REMAP`` (or legacy ``True``) and ``TargetMode.ATTRS_REMAP``.

    Args:
        source: The ``property`` being decorated.
        target: Raw ``target`` argument from ``@deprecated``.
        args_mapping: Raw ``args_mapping`` argument.
        args_extra: Raw ``args_extra`` argument.

    Raises:
        TypeError: When *source* is already a ``_DeprecatedProperty``, or when any of the options above is set.

    """
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


def _reject_deprecated_accessors(source: property) -> None:
    """Reject a ``property`` whose ``fget``, ``fset`` or ``fdel`` is already a ``@deprecated`` wrapper.

    Second guard of the ``property`` arm of :func:`_packing_descriptor`, run after :func:`_reject_property_options`.

    Args:
        source: The ``property`` being decorated.

    Raises:
        TypeError: When any accessor already carries deprecation metadata; the first one found, in ``fget``,
            ``fset``, ``fdel`` order, is named.

    """
    # Guard against pre-deprecated individual accessors fed into property(...) then
    # decorated again: property(deprecated_fget) wrapped with @deprecated would double-wrap
    # fget, emitting two FutureWarnings per read. The _DeprecatedProperty guard in
    # _reject_property_options only catches property-objects that are themselves already
    # _DeprecatedProperty instances.
    for _acc_name, _acc in (("fget", source.fget), ("fset", source.fset), ("fdel", source.fdel)):
        if _acc is not None and _has_deprecation_meta(_acc):
            raise TypeError(
                f"`@deprecated` cannot wrap accessor `{getattr(_acc, '__qualname__', repr(_acc))}` of property"
                f" `{_acc_name}` — it is already decorated with `@deprecated`."
                " Apply `@deprecated` once per accessor."
            )


def _packing_descriptor(
    source: Union[classmethod, staticmethod, property, cached_property],
    packing_fn: Callable,
    target: Union[bool, None, Callable, TargetMode, staticmethod, classmethod],
    args_mapping: Optional[dict[str, Optional[str]]],
    args_extra: Optional[dict[str, Any]],
) -> Union[classmethod, staticmethod, property, cached_property]:
    """Wrap descriptor sources (classmethod/staticmethod/property/cached_property).

    Handles order-agnostic descriptor dispatch: unwraps the descriptor, applies
    *packing_fn* to the underlying callable, and rewraps in the same descriptor type.
    The ``property`` path validates that incompatible ``@deprecated`` options are not
    mixed with property decoration (:func:`_reject_property_options`, then
    :func:`_reject_deprecated_accessors`).

    Args:
        source: The descriptor being decorated; ``packing`` routes only the
            ``_DESCRIPTOR_SOURCE_TYPES`` kinds here.
        packing_fn: The ``packing`` closure from the enclosing ``deprecated()`` call.
            Passed explicitly so this module-level helper can recurse without capturing
            a closure variable.
        target: Raw ``target`` argument from ``@deprecated``; used for ``property``
            validation guards only.
        args_mapping: Raw ``args_mapping`` argument; used for ``property`` validation only.
        args_extra: Raw ``args_extra`` argument; used for ``property`` validation only.

    Returns:
        The rewrapped descriptor, of the same kind as *source* (a ``_DeprecatedProperty``
        for a ``property``).

    Raises:
        TypeError: When incompatible ``@deprecated`` options are combined with a ``property``
            source, or when ``@deprecated`` is applied twice to an already-deprecated
            property or accessor.

    """
    if isinstance(source, (classmethod, staticmethod)):
        # Order-agnostic: unwrap → deprecate inner function → rewrap.
        # Both @classmethod orders produce classmethod(deprecated_wrapper);
        # both @staticmethod orders produce staticmethod(deprecated_wrapper).
        # A staticmethod receives no ``self``, so the cross-class guard's rationale ("self would carry
        # the wrong type") does not apply — thread ``_is_static`` through so ``packing`` skips the guard
        # for the unwrapped function, whose qualname alone cannot reveal it was a staticmethod.
        _is_static = isinstance(source, staticmethod)
        wrapped_inner = packing_fn(source.__func__, _is_static=_is_static)
        return classmethod(wrapped_inner) if isinstance(source, classmethod) else staticmethod(wrapped_inner)

    if isinstance(source, property):
        # Order-agnostic @property: unwrap → deprecate fget/fset/fdel → rewrap preserving doc.
        # All three accessors are wrapped so attribute read, write, and delete each fire the warning.
        _reject_property_options(source, target, args_mapping, args_extra)
        _reject_deprecated_accessors(source)
        # Preserve explicit doc only when it differs from fget's doc (author override)
        # or when fget is absent (setter/deleter-only property with doc= supplied).
        # Otherwise pass None so property() inherits the deprecation-injected fget.__doc__.
        explicit_doc = source.__doc__ if (source.fget is None or source.__doc__ != source.fget.__doc__) else None

        # ``packing_fn`` is captured on the returned ``_DeprecatedProperty`` so chain-style
        # ``@value.setter`` / ``@value.deleter`` can re-wrap freshly-supplied accessors
        # with the same packing config (message_template, stream, deprecated_in, remove_in,
        # num_warns, skip_if). args_mapping / args_extra / callable target are
        # blocked above by TypeError guards and are never reachable here.
        # Without this, ``property.setter(fn)`` would build a plain ``property`` whose new
        # accessor is raw — silently dropping the deprecation warning on attribute writes.
        return _DeprecatedProperty(
            packing_fn(source.fget) if source.fget is not None else None,
            packing_fn(source.fset) if source.fset is not None else None,
            packing_fn(source.fdel) if source.fdel is not None else None,
            explicit_doc,
            _wrap=packing_fn,
        )

    # Order-agnostic @cached_property: unwrap → deprecate func → rewrap.
    return cached_property(packing_fn(source.func))


def _validate_callable_source(
    source: Callable[..., Any],
    target: Union[bool, None, Callable, TargetMode, staticmethod, classmethod],
    *,
    is_static: bool,
    message_template: Optional[str],
    deprecated_in: str,
    stream: Optional[Callable[..., Any]],
) -> None:
    """Run the decoration-time guards of :func:`deprecated_callable` on a non-descriptor source.

    The guards run in this order, so the first problem found is the one reported: the missing-parentheses check
    (:func:`~deprecate._validate._reject_bare_decorator`), the class rejection, the ``message_template`` probe, the
    missing-``deprecated_in`` notice, the cross-class method guard and the ``TargetMode.ATTRS_REMAP`` rejection.

    Args:
        source: The object ``packing`` received. A descriptor never reaches here: ``packing`` hands it to
            :func:`_packing_descriptor`, whose recursion validates the unwrapped function instead.
        target: Raw ``target`` argument, before :func:`~deprecate._validate._normalize_target`.
        is_static: ``True`` when *source* is the function unwrapped from a ``staticmethod``; skips the cross-class
            guard, since no ``self`` is passed.
        message_template: The resolved ``message_template`` (after the ``template_mgs`` alias).
        deprecated_in: The ``deprecated_in`` version; an empty value triggers the missing-version notice.
        stream: Warning stream; ``None`` silences the missing-version notice.

    Raises:
        TypeError: When ``@deprecated`` was applied without parentheses, when *source* is a class, when a method
            source forwards to a method of a different class, or when *target* is ``TargetMode.ATTRS_REMAP``.
        ValueError: When ``message_template`` is malformed.

    Warns:
        UserWarning: When ``deprecated_in`` is empty, ``stream`` is not ``None`` and no ``message_template`` is set.

    """
    _reject_bare_decorator(source)
    # Strict callable-only contract: a class source is rejected up front rather than delegated to
    # `deprecated_class`. `deprecated()` is the dispatcher that routes classes; this is its callable arm.
    if inspect.isclass(source):
        raise TypeError(
            f"`@deprecated_callable` cannot decorate class `{source.__name__}` — it is the strict"
            " callable-only form. Use `@deprecated_class(...)` to deprecate a class, or `@deprecated`"
            " for automatic class/callable dispatch."
        )
    # Probe ``message_template`` against every documented placeholder so typos and malformed
    # conversion specifiers fail at decoration time instead of inside ``wrapped_fn``.
    _validate_message_template(message_template)
    # Note: message_template intentionally bypasses this guard — callers with custom templates
    # control their own messaging and may not rely on deprecated_in being present.
    if not deprecated_in and stream is not None and not message_template:
        warnings.warn(
            f"`@deprecated` on `{source.__name__}` has no `deprecated_in` set."
            " Deprecation notices and generated documentation will omit the `deprecated_in` version."
            " Pass `deprecated_in` for a meaningful deprecation notice.",
            UserWarning,
            stacklevel=_caller_stacklevel(),
        )
    # Cross-class guard runs before ``_normalize_target``; class targets skip it because
    # constructor forwarding (target=NewCls on __init__) is always valid.
    # Descriptor targets: unwrap __func__ so the guard can inspect the qualname;
    # raw staticmethod/classmethod descriptors lack __qualname__ on the instance.
    # A staticmethod *source* skips the guard: no ``self`` is passed, so cross-class forwarding
    # cannot carry a wrong-typed receiver (see ``is_static``).  A staticmethod *target* is NOT a
    # skip signal — an instance-method source forwarding to a staticmethod would still leak ``self``
    # across classes, so that case must keep raising.
    _guard_target = _unwrap_descriptor_target(target)
    if callable(_guard_target) and not inspect.isclass(_guard_target) and not is_static:
        _check_cross_class_method_target(source, _guard_target)
    # ATTRS_REMAP is a proxy-only mode — it is meaningless on @deprecated functions/methods
    # because there is no attribute-access surface to intercept. Raise at decoration time
    # rather than silently producing a wrapper whose stored target has no runtime effect.
    # The raw ``target`` is checked: ``_normalize_target`` passes a ``TargetMode`` through unchanged and maps no
    # legacy sentinel to ``ATTRS_REMAP``, so this equals the check on the normalised target.
    if target is TargetMode.ATTRS_REMAP:
        raise TypeError(
            f"`target=TargetMode.ATTRS_REMAP` is not valid for `@deprecated` on `{source.__name__}`. "
            "`TargetMode.ATTRS_REMAP` is a proxy-only mode — use "
            "`deprecated_class(attrs_mapping=...)` to deprecate class attribute names."
        )


def _build_callable_config(
    *,
    source: Callable[..., Any],
    target: Union[bool, None, Callable, TargetMode, staticmethod, classmethod],
    facts: _CallFacts,
    function_misconfigured: bool,
    deprecated_in: str = "",
    remove_in: str = "",
    args_mapping: Optional[dict[str, Optional[str]]] = None,
    args_extra: Optional[dict[str, Any]] = None,
    docstring_style: Literal["rst", "mkdocs"] = "rst",
    message_template: Optional[str] = None,
    as_exception: Optional[bool] = None,
) -> DeprecationConfig:
    """Build the frozen :class:`~deprecate._types.DeprecationConfig` a ``@deprecated`` wrapper carries.

    Derives the per-source fields: ``name`` from *source*, the stored ``target``
    (:func:`~deprecate._validate._resolve_stored_target`), ``misconfigured`` (legacy ``target=False`` or
    *function_misconfigured*) and the eight signature cache fields from *facts*. ``args_mapping`` and ``args_extra``
    are copied, so mutating the caller's dicts after decoration cannot reach the frozen config and silently change
    forwarding at call time. The keyword defaults equal the ``DeprecationConfig`` field defaults.

    Args:
        source: The decorated callable.
        target: Raw ``target`` argument, before :func:`~deprecate._validate._normalize_target`.
        facts: Signature facts from :func:`~deprecate._dispatch._build_call_facts`.
        function_misconfigured: Result of :func:`~deprecate._validate._validate_callable_mode` for an explicit mode.
        deprecated_in: Version the source was deprecated in.
        remove_in: Version the source will be removed in.
        args_mapping: Caller's ``args_mapping``; stored as a copy.
        args_extra: Caller's ``args_extra``; stored as a copy.
        docstring_style: Normalised docstring style.
        message_template: Resolved ``message_template``.
        as_exception: Per-decorator fatal switch.

    Returns:
        The config stored as ``wrapper.__deprecation_config__``.

    Example:
        >>> def old_func(old_arg=0): pass
        >>> mapping = {"old_arg": "new_arg"}
        >>> cfg = _build_callable_config(
        ...     source=old_func, target=TargetMode.ARGS_REMAP, facts=_CallFacts(), function_misconfigured=False,
        ...     args_mapping=mapping,
        ... )
        >>> mapping["old_arg"] = "other"
        >>> cfg.name, cfg.args_mapping
        ('old_func', {'old_arg': 'new_arg'})

    """
    return DeprecationConfig(
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        name=source.__name__,
        target=_resolve_stored_target(target),
        args_mapping=dict(args_mapping) if isinstance(args_mapping, dict) else args_mapping,
        args_extra=dict(args_extra) if isinstance(args_extra, dict) else args_extra,
        misconfigured=target is False or function_misconfigured,
        docstring_style=docstring_style,
        message_template=message_template,
        as_exception=as_exception,
        target_positional_only=facts.target_positional_only,
        target_positional_only_order=facts.target_positional_only_order,
        source_positional_only=facts.source_positional_only,
        source_positional_only_order=facts.source_positional_only_order,
        source_var_positional_prefix=facts.source_var_positional_prefix,
        target_all_param_names=facts.target_all_param_names,
        target_accepts_var_positional=facts.target_accepts_var_positional,
        target_accepts_var_keyword=facts.target_accepts_var_keyword,
    )


class _WrapperKind(Enum):
    """The shape of the ``@deprecated`` wrapper, matching the kind of its source so the ``inspect`` flags survive.

    Frameworks pick how to run a callable from :func:`inspect.iscoroutinefunction`, :func:`inspect.isgeneratorfunction`
    and :func:`inspect.isasyncgenfunction` (pytest's fixture runner, for one), and those flags come from the wrapper's
    own code object, so each source kind needs a wrapper of the same kind.

    """

    FUNCTION = "function"
    COROUTINE = "coroutine"
    GENERATOR = "generator"
    ASYNC_GENERATOR = "async_generator"


def _wrapper_kind(source: Callable) -> _WrapperKind:
    """Pick the wrapper shape for ``source`` from its own ``inspect`` flags.

    An async generator function is not a coroutine function, so the generator checks come first only for clarity.

    Args:
        source: The callable being deprecated.

    Returns:
        The matching :class:`_WrapperKind`.

    Examples:
        >>> def gen():
        ...     yield 1
        >>> async def agen():
        ...     yield 1
        >>> async def coro(): ...
        >>> [_wrapper_kind(fn).value for fn in (gen, agen, coro, len)]
        ['generator', 'async_generator', 'coroutine', 'function']

    """
    if inspect.isasyncgenfunction(source):
        return _WrapperKind.ASYNC_GENERATOR
    if inspect.isgeneratorfunction(source):
        return _WrapperKind.GENERATOR
    if inspect.iscoroutinefunction(source):
        return _WrapperKind.COROUTINE
    return _WrapperKind.FUNCTION


def _sync_call_path(ctx: _WrapperContext) -> Callable[..., Any]:
    """Return the synchronous call path every non-coroutine wrapper runs: ``skip_if``, the fast path, the planned call.

    A call that ``skip_if`` skips runs the source unchanged; so does a call that warns nothing and needs no rewrite
    (:func:`~deprecate._dispatch._skips_call_plan`, for example a ``TargetMode.NOTIFY`` call after the warn budget is
    spent), which only counts the call; any other call enters the target-chain cycle guard, builds the call plan and
    invokes it, and leaves the guard before it returns. The function wrapper is this closure itself; the generator
    wrappers call it on their first step and delegate to the (async) generator it returns.

    Args:
        ctx: The decoration-time context the wrapper hands to every call.

    Returns:
        The call path, taking the caller's arguments and returning what the source or target returned.

    """

    def wrapped_fn(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if _eval_skip_if(ctx.skip_if):
            return ctx.source(*args, **kwargs)
        if _skips_call_plan(ctx, args, kwargs):
            return ctx.source(*args, **kwargs)
        handle = _enter_cycle_guard(ctx)
        try:
            return _invoke_sync(ctx, _build_call_plan(ctx, args, kwargs), args)
        finally:
            _exit_cycle_guard(ctx, handle)

    return wrapped_fn


def _function_wrapper(ctx: _WrapperContext) -> Callable[..., Any]:
    """Build the plain ``def`` wrapper: the sync call path itself, with the source's ``functools.wraps`` metadata."""
    return wraps(ctx.source)(_sync_call_path(ctx))


def _coroutine_wrapper(ctx: _WrapperContext) -> Callable[..., Any]:
    """Build the ``async def`` wrapper, whose call path matches :func:`_sync_call_path` but awaits the source."""

    @wraps(ctx.source)
    async def async_wrapped_fn(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if _eval_skip_if(ctx.skip_if):
            return await ctx.source(*args, **kwargs)
        if _skips_call_plan(ctx, args, kwargs):
            return await ctx.source(*args, **kwargs)
        handle = _enter_cycle_guard(ctx)
        try:
            return await _invoke_async(ctx, _build_call_plan(ctx, args, kwargs), args)
        finally:
            _exit_cycle_guard(ctx, handle)

    return async_wrapped_fn


def _guarded_first_step(
    ctx: _WrapperContext, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[Iterator[Any], Any, Optional[StopIteration]]:
    """Run a generator wrapper's call plan and take the first item of what it returns, under the cycle guard.

    The guard spans the inner iterator's creation and its first step, so a ``target=`` cycle made only of generator
    wrappers re-enters it and raises the cycle ``RuntimeError`` instead of nesting generators until ``RecursionError``.
    It is released before the wrapper yields, so a generator suspended at a ``yield`` holds no guard entry and two live
    generators of one wrapper on one thread cannot raise a false cycle.

    Returns:
        Tuple of ``(inner, first_item, stop)``: ``stop`` is the ``StopIteration`` of an inner iterator that ended on
        its first step (``first_item`` is then ``None``), otherwise ``None``.

    """
    handle = _enter_cycle_guard(ctx)
    try:
        inner = iter(_invoke_sync(ctx, _build_call_plan(ctx, args, kwargs), args))
        try:
            return inner, next(inner), None
        except StopIteration as stop:
            return inner, None, stop
    finally:
        _exit_cycle_guard(ctx, handle)


def _resume_generator(inner: Iterator[Any], item: Any) -> Generator[Any, Any, Any]:  # noqa: ANN401
    """Yield ``item``, then delegate to ``inner`` as ``yield from`` would, forwarding ``send``, ``throw`` and ``close``.

    ``item`` was already taken from ``inner``, so the consumer's resumption after a held item is forwarded by hand: a
    sent value goes on through ``send``, a thrown exception through ``throw`` (re-raised here when ``inner`` has no
    ``throw``, as for a list iterator), and ``close`` reaches ``inner``; each of those yields a new held item. The first
    plain ``next()`` holds nothing, so ``yield from`` takes over there and returns ``inner``'s return value.

    """
    while True:
        try:
            sent = yield item
        except GeneratorExit:
            close = getattr(inner, "close", None)
            if close is not None:
                close()
            raise
        except BaseException as exc:
            throw = getattr(inner, "throw", None)
            if throw is None:
                raise
            try:
                item = throw(exc)
            except StopIteration as stop:
                return stop.value
            continue
        if sent is None:
            return (yield from inner)
        try:
            item = inner.send(sent)  # type: ignore[attr-defined]
        except StopIteration as stop:
            return stop.value


def _generator_wrapper(ctx: _WrapperContext) -> Callable[..., Any]:
    """Build the generator wrapper of a generator function: the call path runs on the first ``next()``.

    Calling the wrapper only creates the generator object, so the warning, the ``num_warns`` budget and an
    :func:`~deprecate.as_exceptions` error all wait for the first step, and a generator that is never iterated never
    warns. ``send``/``throw``/``close`` and the return value are forwarded; a target that returns any other iterable (a
    list, an iterator) has its items yielded. A call that ``skip_if`` skips, or that needs no call plan, delegates to
    the source with ``yield from`` and no cycle guard, as the function wrapper runs it.

    """

    @wraps(ctx.source)
    def generator_wrapped_fn(*args: Any, **kwargs: Any) -> Generator[Any, Any, Any]:  # noqa: ANN401
        if _eval_skip_if(ctx.skip_if) or _skips_call_plan(ctx, args, kwargs):
            return (yield from ctx.source(*args, **kwargs))
        inner, item, stop = _guarded_first_step(ctx, args, kwargs)
        if stop is not None:
            return stop.value
        return (yield from _resume_generator(inner, item))

    return generator_wrapped_fn


async def _await_step(step: Awaitable[Any]) -> tuple[Any, bool]:
    """Await one step of an async iterator; return ``(item, done)`` with ``done`` set when it ended instead."""
    try:
        return await step, False
    except StopAsyncIteration:
        return None, True


async def _guarded_first_async_step(
    ctx: _WrapperContext, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[AsyncIterator[Any], Any, bool]:
    """Async twin of :func:`_guarded_first_step`: create the inner async iterator and await its first item, guarded.

    Returns:
        Tuple of ``(inner, first_item, done)``; ``done`` is set when ``inner`` ended on its first step.

    """
    handle = _enter_cycle_guard(ctx)
    try:
        inner = _invoke_sync(ctx, _build_call_plan(ctx, args, kwargs), args).__aiter__()
        item, done = await _await_step(inner.__anext__())
        return inner, item, done
    finally:
        _exit_cycle_guard(ctx, handle)


async def _open_async_generator(
    ctx: _WrapperContext, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[AsyncIterator[Any], Any, bool]:
    """Take an async generator wrapper's first step, guarded only when the call runs its call plan.

    A call that ``skip_if`` skips, or that needs no call plan, starts the source unguarded; any other call goes through
    :func:`_guarded_first_async_step`.

    Returns:
        Tuple of ``(inner, first_item, done)``; ``done`` is set when ``inner`` ended on its first step.

    """
    if _eval_skip_if(ctx.skip_if) or _skips_call_plan(ctx, args, kwargs):
        inner = ctx.source(*args, **kwargs).__aiter__()
        item, done = await _await_step(inner.__anext__())
        return inner, item, done
    return await _guarded_first_async_step(ctx, args, kwargs)


def _async_generator_wrapper(ctx: _WrapperContext) -> Callable[..., Any]:
    """Build the async generator wrapper of an async generator function: the call path runs on the first step.

    Timing and the cycle guard match :func:`_generator_wrapper`: nothing runs until the first ``async for`` step, and
    the guard covers only the first step. Async generators have no ``yield from``, so the inner async iterator is driven
    by hand, duck-typed as the sync wrapper does: each value the consumer sends goes on through ``asend``, each
    exception thrown in through ``athrow`` (re-raised here when the inner iterator has none), and ``aclose`` reaches it,
    when it has one, as the wrapper is closed. A plain ``__anext__`` serves a resumption that sends nothing.

    """

    @wraps(ctx.source)
    async def async_generator_wrapped_fn(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, Any]:  # noqa: ANN401
        inner, item, done = await _open_async_generator(ctx, args, kwargs)
        while not done:
            try:
                sent = yield item
            except GeneratorExit:
                aclose = getattr(inner, "aclose", None)
                if aclose is not None:
                    await aclose()
                raise
            # Not swallowed: ``athrow`` raises the exception inside the inner iterator, which handles or re-raises it.
            except BaseException as exc:
                athrow = getattr(inner, "athrow", None)
                if athrow is None:
                    raise
                step = athrow(exc)
            else:
                step = inner.__anext__() if sent is None else cast(AsyncGenerator[Any, Any], inner).asend(sent)
            item, done = await _await_step(step)

    return async_generator_wrapped_fn


#: Wrapper builder per source kind; :func:`_make_wrapper` attaches the deprecation metadata to what they return.
_WRAPPER_BUILDERS: dict[_WrapperKind, Callable[[_WrapperContext], Callable[..., Any]]] = {
    _WrapperKind.FUNCTION: _function_wrapper,
    _WrapperKind.COROUTINE: _coroutine_wrapper,
    _WrapperKind.GENERATOR: _generator_wrapper,
    _WrapperKind.ASYNC_GENERATOR: _async_generator_wrapper,
}


def _make_wrapper(ctx: _WrapperContext, *, kind: _WrapperKind, update_docstring: bool) -> _DeprecatedCallable:
    """Build the ``@deprecated`` wrapper around ``ctx.source`` and attach its deprecation metadata.

    ``kind`` selects the wrapper shape so the wrapper reports the same ``inspect`` flags as the source: a plain
    function and a coroutine function run the call path (:func:`_sync_call_path` or its awaiting twin) on every call,
    while a generator and an async generator function run it on the first step and then delegate to the generator it
    returns. The wrapper carries the ``functools.wraps`` metadata of the source (``__wrapped__`` included),
    ``__deprecation_config__`` (``ctx.cfg``), the PEP 702 ``__deprecated__`` message rendered from ``ctx.cfg``, and
    ``_state`` — the very ``ctx.state`` object, so a counter reset through the wrapper reaches the call path.

    Args:
        ctx: The decoration-time context the wrapper hands to every call.
        kind: The wrapper shape; ``packing`` passes :func:`_wrapper_kind` of the source.
        update_docstring: Inject the deprecation notice into the wrapper's docstring.

    Returns:
        The wrapper.

    """
    wrapper = cast(_DeprecatedCallable, _WRAPPER_BUILDERS[kind](ctx))
    # The PEP-702-conformant ``__deprecated__`` message string, computed statically at decoration time from the
    # config alone.
    source_name = _source_display_name(ctx.source)
    wrapper.__deprecation_config__ = ctx.cfg
    wrapper.__deprecated__ = _render_static_deprecation_message(
        ctx.cfg, source_name, f"{ctx.source.__module__}.{source_name}"
    )
    wrapper._state = ctx.state

    if update_docstring:
        _update_docstring_with_deprecation(wrapper)

    return wrapper


def deprecated_callable(
    target: Union[bool, None, Callable, TargetMode, staticmethod, classmethod] = TargetMode.NOTIFY,
    deprecated_in: str = "",
    remove_in: str = "",
    stream: Optional[Callable] = deprecation_warning,
    num_warns: int = 1,
    message_template: Optional[str] = None,
    args_mapping: Optional[dict[str, Optional[str]]] = None,
    args_extra: Optional[dict[str, Any]] = None,
    skip_if: Union[bool, Callable] = False,
    update_docstring: bool = False,
    docstring_style: Literal["auto", "rst", "mkdocs", "markdown"] = "auto",
    template_mgs: Optional[str] = None,
    *,
    as_exception: Optional[bool] = None,
) -> Callable[[_SourceT], _SourceT]:
    """Decorate a function/method with warning message and forward calls to target — the strict callable form.

    This is the canonical callable-only implementation.  It behaves like :func:`deprecated` for functions,
    methods, lambdas, and descriptors (``classmethod`` / ``staticmethod`` / ``property``): same call
    forwarding, argument mapping, warning control, and ``__deprecation_config__`` metadata.  It differs in one way:
    applying it to a **class** raises :class:`TypeError` at decoration time instead of delegating to
    :func:`~deprecate.proxy.deprecated_class`.  :func:`deprecated` is the friendly front door that dispatches
    classes for you and routes callables here; reach for ``deprecated_callable`` at a call site that must
    never silently accept a class.

    This decorator marks a function or method as deprecated and can automatically forward all calls to a replacement
    implementation.  It supports argument mapping, custom warning messages, and flexible warning control.

    For **generator functions** (``def gen(): yield``) and **async generator functions** (``async def gen(): yield``),
    the wrapper is itself a generator (async generator) function, so :func:`inspect.isgeneratorfunction` /
    :func:`inspect.isasyncgenfunction` stay ``True`` and frameworks such as pytest's fixture runner treat it like the
    original.  Calling it only creates the generator object: the deprecation warning, the ``num_warns`` budget and an
    ``as_exception`` / :func:`~deprecate.as_exceptions` error come on the first ``next()`` / ``async for`` step, and a
    generator that is never iterated never warns.  ``send``/``throw``/``close`` (``asend``/``athrow``/``aclose``) and
    the return value reach the inner generator; a sync generator's target returning another iterable has its items
    yielded, while an async generator's target must return an async iterable.  The wrapper kind follows the source,
    so a generator stub must itself contain ``yield``.

    Args:
        target: How to handle the deprecation. Defaults to :attr:`~deprecate.TargetMode.NOTIFY` (warn-only; source
            body executes unchanged) — the strict form always uses an explicit mode and rejects
            :attr:`~deprecate.TargetMode.AUTO` (front-door-only inference) with :class:`TypeError`. Pass an
            explicit value to forward calls or remap arguments:

            - ``Callable``: Forward all calls to this callable (function, method, or class target). The
              decorated function's body is **not executed** under normal forwarding — use ``pass`` or ``...``
              as the body. **Exception**: when ``skip_if`` evaluates ``True`` at call time, the source body
              executes as a fallback, so keep a working implementation if you combine ``target=Callable``
              with ``skip_if``.
            - :attr:`~deprecate.TargetMode.ARGS_REMAP` (or legacy ``True``): Self-deprecation — deprecate argument
              names only, remapping them within the same function body
            - :attr:`~deprecate.TargetMode.NOTIFY` (default): Warning-only mode — no forwarding, source body executes
              normally. Combining it with ``args_mapping`` is contradictory: the mapping is ignored and a
              :class:`UserWarning` fires (:class:`TypeError` in ``v1.0``).

            Passing ``target=None`` is a legacy synonym that also resolves to
            :attr:`~deprecate.TargetMode.NOTIFY` but emits a :class:`FutureWarning` directing you to use the
            enum form.

        deprecated_in: Version when the function was deprecated (e.g., "1.0.0"). Default is empty string.
        remove_in: Version when the function will be removed (e.g., "2.0.0"). Default is empty string.
        stream: Function to output warnings (default: :func:`~deprecate.messaging.deprecation_warning`, which is
            :func:`warnings.warn` with ``FutureWarning`` category). Set to ``None`` to disable warnings entirely.
            A custom ``stream`` is called once per warning, with ``stacklevel=`` only when its signature declares a
            ``stacklevel`` parameter or ``**kwargs`` (:func:`warnings.warn` and its partials always qualify); a
            C-level callable without an introspectable signature gets the message alone and loses caller attribution.
            The ``stacklevel`` passed is counted from the frame that calls ``stream``, so it points at the caller
            for :func:`warnings.warn` and a C-level :func:`functools.partial` of it; a pure-Python stream, and the
            module-level :func:`logging.warning` before Python 3.11, land one frame short (see
            ``deprecate._frames._caller_stacklevel`` for the full rule).
        num_warns: Number of times to show warning per function or per deprecated argument:
            - ``1`` (default): Show warning once per function/argument
            - ``-1``: Show warning on every call
            - ``0``: Suppress deprecation warnings emitted for the decorated function/argument
            - ``N > 1``: Show warning N times total
        message_template: Custom warning message template with format specifiers:
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
        template_mgs: Deprecated alias for ``message_template`` (renamed in ``v0.12``; the old spelling was a
            typo). Supplying it emits a :class:`FutureWarning` and its value is used as ``message_template``;
            supplying both raises :class:`TypeError`. Removed in ``v1.0``.
        as_exception: Promote this deprecation from a warning to a raised
            :class:`~deprecate._fatal.DeprecatedError` — the warn/raise/delete lifecycle step before the symbol
            goes away. The raised message is the same text the warning would have carried, the replacement
            target is never invoked, and the source body never runs (so ``TargetMode.NOTIFY`` and
            ``TargetMode.ARGS_REMAP`` stop instead of falling through). A fatal deprecation ignores
            ``num_warns`` and raises on **every** call, and ``stream=None`` silences only the message, never
            the raise; ``skip_if`` still suppresses it entirely. ``None`` (default) follows the open
            :func:`~deprecate._fatal.as_exceptions` scope, else ``deprecate.AS_EXCEPTIONS`` (seeded from the
            ``DEPRECATE_AS_EXCEPTIONS`` environment variable); ``True`` is always fatal; ``False`` means "not
            fatal by default" and still yields to a ``True`` global, so a consumer's strict run cannot be opted out
            of by an upstream wrapper.
            Python's own ``-W error::FutureWarning`` is the alternative when you want *every* library's
            ``FutureWarning`` to raise; this flag is scoped to pyDeprecate's own deprecations and also works
            when ``stream`` is not a warnings function (e.g. :func:`logging.warning`).

    Returns:
        Decorator function that wraps the source function/method.

    Warns:
        UserWarning: If ``deprecated_in`` is absent, ``stream`` is not ``None``, and no ``message_template`` is set.
            Fired at decoration time (not call time) to catch missing version metadata early. Suppressed by
            passing ``stream=None`` or ``message_template``.

    Raises:
        TypeError: If applied to a class. The strict form rejects a class source at decoration time (naming
            ``deprecated_class`` and ``deprecated`` as the alternatives) instead of delegating to
            :func:`~deprecate.proxy.deprecated_class`.
        TypeError: If the source is a class method and target is a method on a *different* class (cross-class
            method forwarding detected at decoration time via ``__qualname__`` comparison). Skipped silently
            when the target's qualname prefix names a class absent from the target's module globals.

        TypeError: If skip_if is a callable that doesn't return a bool.
        TypeError: If arguments in args_mapping don't exist in target function and target doesn't accept **kwargs.

    Example:
        >>> # Basic forwarding
        >>> def new_func(x: int) -> int:
        ...     return x * 2
        >>> @deprecated_callable(target=new_func, deprecated_in="1.0", remove_in="2.0")
        ... def old_func(x: int) -> int:
        ...     pass

        >>> # Argument mapping
        >>> @deprecated_callable(
        ...     target=new_func,
        ...     args_mapping={'old_name': 'new_name', 'unused': None}
        ... )
        ... def old_func(old_name: int, unused: str) -> int:
        ...     pass

        >>> # Self-deprecation
        >>> from deprecate import TargetMode
        >>> @deprecated_callable(target=TargetMode.ARGS_REMAP, args_mapping={'old_arg': 'new_arg'})
        ... def my_func(old_arg: int = 0, new_arg: int = 0) -> int:
        ...     return new_arg * 2

        >>> # Warn-only (default — no target needed)
        >>> @deprecated_callable(deprecated_in="1.0", remove_in="2.0")
        ... def legacy_func(x: int) -> int:
        ...     return x

        >>> # A class source is rejected up front (use `deprecated_class` or `deprecated` instead)
        >>> @deprecated_callable(deprecated_in="1.0", remove_in="2.0")  # doctest: +IGNORE_EXCEPTION_DETAIL
        ... class OldClass:
        ...     pass
        Traceback (most recent call last):
        TypeError: `@deprecated_callable` cannot decorate class `OldClass` ...

    """
    message_template = _resolve_message_template_alias(message_template, template_mgs)
    # ``TargetMode.AUTO`` is the ``@deprecated`` front-door default only — the strict form requires an
    # explicit mode so the decoration site documents its own intent.
    if target is TargetMode.AUTO:
        raise TypeError(
            "`TargetMode.AUTO` is only valid on the `@deprecated` front door, which infers the mode from "
            "the configuration. With `deprecated_callable` pass an explicit `target` — "
            "`TargetMode.ARGS_REMAP` with `args_mapping`, `TargetMode.NOTIFY` for warn-only, or a callable."
        )
    normalized_docstring_style = normalize_docstring_style(docstring_style)

    def packing(
        source: Union[Callable, classmethod, staticmethod, property, cached_property],
        _is_static: bool = False,
    ) -> Union[Callable, classmethod, staticmethod, property, cached_property]:
        # A descriptor is unwrapped and its function re-enters ``packing``, which validates that function; the
        # source guards have nothing to reject on the descriptor object itself.  The ``isinstance`` check also
        # narrows ``source`` to a plain callable for everything below.
        if isinstance(source, _DESCRIPTOR_SOURCE_TYPES):
            return _packing_descriptor(source, packing, target, args_mapping, args_extra)
        _validate_callable_source(
            source,
            target,
            is_static=_is_static,
            message_template=message_template,
            deprecated_in=deprecated_in,
            stream=stream,
        )
        _target = _normalize_target(source, target)
        _source_is_stacked = _has_deprecation_meta(source)
        if _source_is_stacked:
            _warn_stacking_misconfiguration(source, _target)
        # Skip for legacy sentinels: _normalize_target already fired a FutureWarning;
        # re-running the guard here would report the wrong migration path.
        _function_misconfigured = False
        if isinstance(_target, TargetMode) and isinstance(target, TargetMode):
            _function_misconfigured = _validate_callable_mode(
                _target, source.__name__, args_mapping=args_mapping, args_extra=args_extra
            )
        # Source and target signature facts, read once here so no call re-inspects either signature; this also fires
        # the POSITIONAL_ONLY target warning.
        _facts = _build_call_facts(source, _target, stream)
        dep_meta = _build_callable_config(
            source=source,
            target=target,
            facts=_facts,
            function_misconfigured=_function_misconfigured,
            deprecated_in=deprecated_in,
            remove_in=remove_in,
            args_mapping=args_mapping,
            args_extra=args_extra,
            docstring_style=normalized_docstring_style,
            message_template=message_template,
            as_exception=as_exception,
        )
        # One context per wrapper; its ``state`` is the very object ``_make_wrapper`` installs as ``wrapper._state``,
        # so counter resets through the wrapper reach the call path.  Which source defaults a forwarded call must
        # drop depends only on the target and the (copied) mapping, so it is derived once here; ``None`` (unreadable
        # target signature) defers it to call time.
        ctx = _WrapperContext(
            source=source,
            raw_target=target,
            target=_target,
            cfg=dep_meta,
            facts=_facts,
            state=_WrapperState(),
            stream=stream,
            num_warns=num_warns,
            skip_if=skip_if,
            is_stacked=_source_is_stacked,
            forward_defaults=_precompute_forward_defaults(_target, dep_meta.args_mapping),
        )
        # The wrapper kind follows the source's own ``inspect`` flags.  Known false-negatives of
        # ``inspect.iscoroutinefunction`` — these sources silently receive the plain sync wrapper, meaning
        # ``await wrapper(...)`` will fail or return a bare coroutine:
        #   • async function wrapped by a decorator that does NOT propagate ``__wrapped__`` / use
        #     ``functools.wraps`` (``inspect.iscoroutinefunction`` walks ``__wrapped__``, not ``__call__``).
        #   • callable objects whose ``__call__`` is ``async def`` — use ``async def`` thin wrapper instead.
        # Workaround for both: wrap the callable in a plain ``async def my_wrapper(*a, **kw): return
        # await callable(*a, **kw)`` before applying ``@deprecated``.
        return _make_wrapper(ctx, kind=_wrapper_kind(source), update_docstring=update_docstring)

    # ``packing`` also takes a private ``_is_static`` argument the front door passes; the public
    # contract is the identity signature above.
    return cast(Callable[[_SourceT], _SourceT], packing)
