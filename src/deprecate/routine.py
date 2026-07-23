"""Function/method deprecation decorators.

This module hosts :func:`deprecated_callable` (the strict callable-only decorator) and :func:`deprecated`
(the friendly front door that dispatches classes to :func:`~deprecate.proxy.deprecated_class` and routes
callables to :func:`deprecated_callable`), plus the decoration-time ``packing`` descriptors.  The target
resolution and call-plan machinery lives in :mod:`deprecate._dispatch`; the warning templates and emitters
in :mod:`deprecate.messaging`.  The legacy import path :mod:`deprecate.deprecation` re-exports the public
decorators for backward compatibility.

Key Components:
    - :func:`~deprecate.routine.deprecated_callable`: Canonical strict decorator (rejects classes)
    - :func:`~deprecate.routine.deprecated`: Front-door dispatcher over callables and classes
    - Warning templates for different deprecation scenarios
    - Internal helpers for argument mapping and warning management

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import inspect
import warnings
from contextvars import ContextVar
from functools import cached_property, wraps
from typing import Any, Callable, Literal, Optional, Union, cast

from deprecate._dispatch import (
    _build_call_plan,
    _check_cross_class_method_target,
    _detect_positional_only,
    _invoke_async,
    _invoke_sync,
    _normalize_target,
    _precompute_target_facts,
    _reject_bare_decorator,
    _resolve_stored_target,
    _warn_stacking_misconfiguration,
)
from deprecate._properties import _DeprecatedProperty
from deprecate._types import (
    DeprecationConfig,
    TargetMode,
    _DeprecatedCallable,
    _has_deprecation_meta,
    _WrapperState,
)
from deprecate.docstring.inject import _update_docstring_with_deprecation, normalize_docstring_style
from deprecate.messaging import _resolve_message_template_alias, _validate_message_template, deprecation_warning
from deprecate.utils import _get_signature, _unwrap_descriptor_target

# ContextVar storing the active-wrapper id-set for the current async task or sync call stack.
# Each asyncio.Task inherits a snapshot of the parent context at creation time; because this
# ContextVar defaults to None and is only set() to a fresh set() inside the wrapper call, tasks
# spawned from user code (e.g. asyncio.gather) see None and create independent sets — no sharing.
# A synchronous recursive chain (same task/stack) shares one set — correct for cycle detection.
# Lives here (not in _dispatch) because only the ``wrapped_fn`` closures below read/write it.
_cycle_detection: ContextVar[Optional[set[int]]] = ContextVar("_cycle_detection", default=None)


def _packing_descriptor(  # noqa: C901 — property-path guards (fget/fset/fdel validation + TypeError raises) are one coherent story; splitting further adds indirection without reducing real complexity
    source: Union[Callable, classmethod, staticmethod, "property", cached_property],
    packing_fn: Callable,
    target: Union[bool, None, Callable, TargetMode, staticmethod, classmethod],
    args_mapping: Optional[dict[str, Optional[str]]],
    args_extra: Optional[dict[str, Any]],
    _stacklevel: int,
) -> Optional[Callable]:
    """Wrap descriptor sources (classmethod/staticmethod/property/cached_property).

    Handles order-agnostic descriptor dispatch: unwraps the descriptor, applies
    *packing_fn* to the underlying callable, and rewraps in the same descriptor type.
    The ``property`` path validates that incompatible ``@deprecated`` options are not
    mixed with property decoration.

    Args:
        source: The callable or descriptor being decorated.
        packing_fn: The ``packing`` closure from the enclosing ``deprecated()`` call.
            Passed explicitly so this module-level helper can recurse without capturing
            a closure variable.
        target: Raw ``target`` argument from ``@deprecated``; used for ``property``
            validation guards only.
        args_mapping: Raw ``args_mapping`` argument; used for ``property`` validation only.
        args_extra: Raw ``args_extra`` argument; used for ``property`` validation only.
        _stacklevel: Threaded through to recursive *packing_fn* calls so the decoration-site
            stacklevel stays correct across nesting levels.

    Returns:
        Wrapped descriptor when *source* is a descriptor, or ``None`` when *source* is a
        plain callable (caller should continue to the regular callable path).

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
        wrapped_inner = packing_fn(source.__func__, _stacklevel + 2, _is_static=_is_static)
        return classmethod(wrapped_inner) if isinstance(source, classmethod) else staticmethod(wrapped_inner)  # type: ignore[return-value]

    if isinstance(source, property):
        # Order-agnostic @property: unwrap → deprecate fget/fset/fdel → rewrap preserving doc.
        # All three accessors are wrapped so attribute read, write, and delete each fire the warning.
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
        # with the same packing config (message_template, stream, deprecated_in, remove_in,
        # num_warns, skip_if, stacklevel). args_mapping / args_extra / callable target are
        # blocked above by TypeError guards and are never reachable here.
        # Without this, ``property.setter(fn)`` would build a plain ``property`` whose new
        # accessor is raw — silently dropping the deprecation warning on attribute writes.
        _accessor_sl = _stacklevel + 2

        def _wrap_accessor(fn: Callable) -> Callable:
            """Apply packing_fn to a property accessor with the adjusted stacklevel."""
            return packing_fn(fn, _accessor_sl)

        return _DeprecatedProperty(  # type: ignore[return-value]
            packing_fn(source.fget, _stacklevel + 2) if source.fget is not None else None,
            packing_fn(source.fset, _stacklevel + 2) if source.fset is not None else None,
            packing_fn(source.fdel, _stacklevel + 2) if source.fdel is not None else None,
            explicit_doc,
            _wrap=_wrap_accessor,
        )

    if isinstance(source, cached_property):
        # Order-agnostic @cached_property: unwrap → deprecate func → rewrap.
        return cached_property(packing_fn(source.func, _stacklevel + 2))  # type: ignore[return-value]

    return None


def deprecated_callable(  # noqa: C901
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
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate a function/method with warning message and forward calls to target — the strict callable form.

    This is the canonical callable-only implementation.  It behaves like :func:`deprecated` for functions,
    methods, lambdas, and descriptors (``classmethod`` / ``staticmethod`` / ``property``): same call
    forwarding, argument mapping, warning control, and ``__deprecated__`` metadata.  It differs in one way:
    applying it to a **class** raises :class:`TypeError` at decoration time instead of delegating to
    :func:`~deprecate.proxy.deprecated_class`.  :func:`deprecated` is the friendly front door that dispatches
    classes for you and routes callables here; reach for ``deprecated_callable`` at a call site that must
    never silently accept a class.

    This decorator marks a function or method as deprecated and can automatically forward all calls to a replacement
    implementation.  It supports argument mapping, custom warning messages, and flexible warning control.

    For **generator functions** (``def gen(): yield``) and **async generator functions** (``async def gen(): yield``),
    the deprecation warning fires at call time — when the (async) generator object is created — not at first
    iteration.  The generator body executes lazily as normal when iterated (``next()`` / ``async for``).

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
        stream: Function to output warnings (default: :func:`~deprecate.deprecation.deprecation_warning`, which is
            :func:`warnings.warn` with ``FutureWarning`` category). Set to ``None`` to disable warnings entirely.
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

    def packing(  # noqa: C901
        source: Union[Callable, classmethod, staticmethod, property, cached_property],
        _stacklevel: int = 2,
        _is_static: bool = False,
    ) -> Callable:
        _reject_bare_decorator(source)
        # Strict callable-only contract: a class source is rejected up front rather than delegated to
        # `deprecated_class`. `deprecated()` is the dispatcher that routes classes; this is its callable arm.
        if inspect.isclass(source):
            raise TypeError(
                f"`@deprecated_callable` cannot decorate class `{source.__name__}` — it is the strict"
                " callable-only form. Use `@deprecated_class(...)` to deprecate a class, or `@deprecated`"
                " for automatic class/callable dispatch."
            )
        _descriptor_result = _packing_descriptor(source, packing, target, args_mapping, args_extra, _stacklevel)
        if _descriptor_result is not None:
            return _descriptor_result
        # mypy narrowing: _packing_descriptor handles all descriptor types via early return;
        # remaining code (including captured closures) only executes for plain Callable.
        # isinstance guard is required — cast() does not propagate narrowing into closure bodies.
        if isinstance(source, (classmethod, staticmethod, property, cached_property)):  # pragma: no cover
            raise AssertionError(  # pragma: no cover
                f"unreachable: {type(source)!r} was not handled by _packing_descriptor"
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
                stacklevel=_stacklevel,
            )
        # Cross-class guard runs before remapping; class targets skip it because
        # constructor forwarding (target=NewCls on __init__) is always valid.
        # Descriptor targets: unwrap __func__ so the guard can inspect the qualname;
        # raw staticmethod/classmethod descriptors lack __qualname__ on the instance.
        # A staticmethod *source* skips the guard: no ``self`` is passed, so cross-class forwarding
        # cannot carry a wrong-typed receiver (see ``_is_static``).  A staticmethod *target* is NOT a
        # skip signal — an instance-method source forwarding to a staticmethod would still leak ``self``
        # across classes, so that case must keep raising.
        _guard_target = _unwrap_descriptor_target(target)
        if callable(_guard_target) and not inspect.isclass(_guard_target) and not _is_static:
            _check_cross_class_method_target(source, _guard_target)
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
            # +1: this warning has one packing frame above it; when routed through the `deprecated`
            # dispatcher a second frame is added, so the site-pointing stacklevel tracks `_stacklevel`.
            _warn_stacking_misconfiguration(source, _target, _stacklevel + 1)
        else:
            _source_is_stacked = False

        # Skip for legacy sentinels: _normalize_target already fired a FutureWarning;
        # re-running the guard here would report the wrong migration path.
        _function_misconfigured = False
        if isinstance(_target, TargetMode) and isinstance(target, TargetMode):
            _function_misconfigured = TargetMode._validate(
                _target, source.__name__, args_mapping=args_mapping, args_extra=args_extra, stacklevel=_stacklevel + 1
            )

        _source_params = list(_get_signature(source).parameters.values())
        source_has_var_positional = any(param.kind is inspect.Parameter.VAR_POSITIONAL for param in _source_params)
        # Named positional params preceding *args — the wrapper forwards args[prefix:] as the
        # surplus positional tail when forwarding to a callable target.
        _source_var_positional_prefix = (
            next(i for i, param in enumerate(_source_params) if param.kind is inspect.Parameter.VAR_POSITIONAL)
            if source_has_var_positional
            else 0
        )
        # Source-side POSITIONAL_ONLY params: the wrapper converts positional args to kwargs
        # internally, so the dispatcher must split these back out before invoking the source
        # body (NOTIFY / ARGS_REMAP / migrated-caller short-circuit) — mirror of the
        # target-side machinery below.
        _source_positional_only = frozenset(
            param.name for param in _source_params if param.kind is inspect.Parameter.POSITIONAL_ONLY
        )
        _source_positional_only_order: tuple[str, ...] = (
            tuple(param.name for param in _source_params) if _source_positional_only else ()
        )

        _target_positional_only, _target_positional_only_order = _detect_positional_only(
            _target, source, stream, _stacklevel + 1
        )
        # Precompute the target signature facts the call-time kwarg validation needs (accepted-name set
        # and var-arg flags) so no forwarded call re-inspects the target via inspect.getfullargspec.
        (
            _target_all_names,
            _target_accepts_var_positional,
            _target_accepts_var_keyword,
        ) = _precompute_target_facts(_target)

        stored_target = _resolve_stored_target(target)
        misconfigured = target is False or _function_misconfigured
        # Copy caller-owned mutable mappings so the frozen ``DeprecationConfig`` cannot be mutated through the
        # caller's original dict after decoration (which would silently change forwarding behavior at call time).
        _args_mapping = dict(args_mapping) if isinstance(args_mapping, dict) else args_mapping
        _args_extra = dict(args_extra) if isinstance(args_extra, dict) else args_extra
        dep_meta = DeprecationConfig(
            deprecated_in=deprecated_in,
            remove_in=remove_in,
            name=source.__name__,
            target=stored_target,
            args_mapping=_args_mapping,
            args_extra=_args_extra,
            misconfigured=misconfigured,
            docstring_style=normalized_docstring_style,
            message_template=message_template,
            target_positional_only=_target_positional_only,
            target_positional_only_order=_target_positional_only_order,
            source_positional_only=_source_positional_only,
            source_positional_only_order=_source_positional_only_order,
            source_var_positional_prefix=_source_var_positional_prefix,
            target_all_param_names=_target_all_names,
            target_accepts_var_positional=_target_accepts_var_positional,
            target_accepts_var_keyword=_target_accepts_var_keyword,
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

                _active_async: Optional[set[int]] = None
                _token_async = None
                if callable(_target):
                    _active_async = _cycle_detection.get()
                    if _active_async is None:
                        _active_async = set()
                        _token_async = _cycle_detection.set(_active_async)
                    if id(source) in _active_async:
                        _source_name = getattr(source, "__qualname__", repr(source))
                        raise RuntimeError(
                            f"Circular deprecation cycle detected: `{_source_name}` re-entered"
                            " via its own target chain. Point to a non-deprecated final implementation."
                        )
                    _active_async.add(id(source))

                try:
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
                    return await _invoke_async(source, plan, _dep_cfg, source_has_var_positional, args)
                finally:
                    if _active_async is not None:
                        _active_async.discard(id(source))
                        if _token_async is not None:
                            _cycle_detection.reset(_token_async)

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

            _active: Optional[set[int]] = None
            _token = None
            if callable(_target):
                _active = _cycle_detection.get()
                if _active is None:
                    _active = set()
                    _token = _cycle_detection.set(_active)
                if id(source) in _active:
                    _source_name = getattr(source, "__qualname__", repr(source))
                    raise RuntimeError(
                        f"Circular deprecation cycle detected: `{_source_name}` re-entered"
                        " via its own target chain. Point to a non-deprecated final implementation."
                    )
                _active.add(id(source))

            try:
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
                return _invoke_sync(source, plan, _dep_cfg, source_has_var_positional, args)
            finally:
                if _active is not None:
                    _active.discard(id(source))
                    if _token is not None:
                        _cycle_detection.reset(_token)

        wrapped_fn_typed = cast(_DeprecatedCallable, wrapped_fn)
        wrapped_fn_typed.__deprecated__ = dep_meta
        wrapped_fn_typed._state = _WrapperState()

        if update_docstring:
            _update_docstring_with_deprecation(wrapped_fn)

        return wrapped_fn

    return packing
