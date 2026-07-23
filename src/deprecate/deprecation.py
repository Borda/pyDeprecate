"""Front-door `deprecated` decorator — dispatches functions/methods and classes.

:func:`deprecated` is the friendly umbrella decorator: it routes callable sources to
:func:`~deprecate.routine.deprecated_callable` and class sources to :func:`~deprecate.proxy.deprecated_class` (emitting
a ``UserWarning``).  It is defined here so the historical import path ``from deprecate.deprecation import deprecated``
keeps resolving natively.

The specialized decorators live in their target modules: :func:`~deprecate.routine.deprecated_callable`
(functions/methods), :func:`~deprecate.proxy.deprecated_class` / :func:`~deprecate.proxy.deprecated_instance`
(classes/objects), and :func:`~deprecate.module.deprecated_module` (modules).  The front door exposes only the arguments
common to both dispatch shapes; the class-only ``attrs_mapping`` lives on :func:`~deprecate.proxy.deprecated_class`
alone.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import inspect
import warnings
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Callable, Literal, Optional, Union

from deprecate._dispatch import _reject_non_callable_source
from deprecate._types import TargetMode
from deprecate.messaging import _resolve_message_template_alias, _validate_message_template, deprecation_warning
from deprecate.routine import deprecated_callable

# Classes that have already emitted the one-time ``@deprecated``-on-class dispatch notice.
# Keyed by ``f"{__module__}.{__qualname__}"`` so a decoration loop warns once per class, not per
# iteration, while distinct classes sharing a bare qualname across modules still warn independently.
# Remove in 1.0.
_CLASS_DISPATCH_NOTIFIED: set[str] = set()


@dataclass
class _PackingClassArgs:
    """Grouped keyword arguments for :func:`~deprecate.deprecation._packing_class_source`."""

    deprecated_in: str
    remove_in: str
    num_warns: int
    stream: Optional[Callable]
    message_template: Optional[str]
    args_mapping: Optional[dict[str, Optional[str]]]
    args_extra: Optional[dict[str, Any]]
    skip_if: Union[bool, Callable]
    update_docstring: bool
    docstring_style: str
    _stacklevel: int


def _packing_class_source(
    source: type,
    target: Union[bool, None, Callable, TargetMode, staticmethod, classmethod],
    pack_args: _PackingClassArgs,
) -> Callable:
    """Delegate class-source deprecation to :func:`~deprecate.proxy.deprecated_class`.

    Class dispatch is first-class: this resolves legacy ``target`` sentinels, emits the one-time
    informational ``UserWarning`` (class dispatch now routes to ``deprecated_class``), and forwards
    ``args_mapping`` through so the proxy auto-resolves ``NOTIFY + mapping``. Extracted from
    the ``inspect.isclass(source)`` branch of ``packing``.

    Args:
        source: The class being decorated with ``@deprecated``.
        target: Raw ``target`` argument from ``@deprecated``.
        pack_args: Grouped keyword arguments passed through to ``deprecated_class`` plus the
            stacklevel for ``warnings.warn`` calls; caller passes ``packing``'s
            ``_stacklevel + 1`` to account for the extra frame.

    Returns:
        Result of ``deprecated_class(...)(source)``.

    """
    import importlib

    proxy_module = importlib.import_module("deprecate.proxy")
    deprecated_class_fn = proxy_module.deprecated_class

    # One-time informational dispatch notice — class dispatch is now first-class, so the old v0.6.0
    # "will become a TypeError" wart is retired. Emit at most once per class qualname per process so a
    # decoration loop does not spam; suppressed when ``stream=None``. Remove in 1.0.
    dispatch_key = f"{source.__module__}.{source.__qualname__}"
    if pack_args.stream is not None and dispatch_key not in _CLASS_DISPATCH_NOTIFIED:
        _CLASS_DISPATCH_NOTIFIED.add(dispatch_key)
        message = f"`@deprecated` on class `{source.__name__}` now dispatches to `@deprecated_class`."
        if target is not None and not inspect.isclass(target) and not isinstance(target, TargetMode):
            # The non-class-``target``-ignored hint stays — it is a real misconfig signal for the class path.
            message += (
                " Note: non-class `target` values are ignored when deprecating classes;"
                " use `@deprecated_class(target=...)` instead."
            )
        warnings.warn(message, UserWarning, stacklevel=pack_args._stacklevel)

    # Resolve legacy ``target`` sentinels (None/True/False) to a ``TargetMode`` so their migration
    # ``FutureWarning`` fires at the user's decoration site; the raw ``target=False`` stays a genuine
    # class misconfiguration signal forwarded via ``_misconfigured_override``.
    class_misconfigured = target is False
    if target is TargetMode.AUTO:
        # The AUTO front-door default means "no explicit target" — forward the proxy's own unset value
        # (``None``) so a configured mapping auto-resolves exactly like a direct ``deprecated_class(...)``
        # call with ``target`` omitted. ``TargetMode.AUTO`` itself never reaches the strict factories.
        forward_target: Any = None
    elif isinstance(target, TargetMode) or callable(target) and inspect.isclass(target):
        forward_target = target
    elif target is None or isinstance(target, bool):
        forward_target = TargetMode._from_legacy(target, stacklevel=pack_args._stacklevel + 1)
        # Legacy ``None`` meant "no target", and invalid ``False`` is treated the same after its warning —
        # forward the unset value so a mapping still auto-resolves; an explicitly typed
        # ``TargetMode.NOTIFY`` is the only value validated against a mapping.
        if target is None or target is False:
            forward_target = None
    else:
        # Non-class ``target`` values are ignored on the class path (hint emitted above) — forward the
        # unset value so a configured mapping is not falsely reported as contradicting an explicit NOTIFY.
        forward_target = None

    # An omitted target + mapping auto-resolves; an explicit ``NOTIFY + mapping`` is a misconfiguration.
    # Pass ``args_mapping``/``args_extra`` through untouched so ``deprecated_class`` and its
    # proxy auto-resolve the unset target to ARGS_REMAP (identical to a direct
    # ``deprecated_class(...)`` call). The proxy also owns misconfig validation (e.g. NOTIFY + bare
    # ``args_extra``), so the dispatcher no longer pre-validates or strips anything here.
    # Class-only knobs such as ``attrs_mapping`` are deliberately absent from the front door —
    # ``deprecated()`` exposes only the arguments common to ``deprecated_callable`` and
    # ``deprecated_class``; reach for those directly for the full per-shape scope.
    return deprecated_class_fn(
        target=forward_target,
        deprecated_in=pack_args.deprecated_in,
        remove_in=pack_args.remove_in,
        num_warns=pack_args.num_warns,
        stream=pack_args.stream,
        message_template=pack_args.message_template,
        args_mapping=pack_args.args_mapping,
        args_extra=pack_args.args_extra,
        skip_if=pack_args.skip_if,
        update_docstring=pack_args.update_docstring,
        docstring_style=pack_args.docstring_style,
        _misconfigured_override=class_misconfigured,
        # The dispatcher inserts two frames beyond a direct ``deprecated_class(...)`` call
        # (``packing`` and this function) before reaching ``decorator(cls)`` — without this offset
        # proxy misconfig warnings would point into library internals instead of the user's
        # `@deprecated`-on-class decoration site.
        _stacklevel_extra=2,
    )(source)


def deprecated(
    target: Union[bool, None, Callable, TargetMode, staticmethod, classmethod] = TargetMode.AUTO,
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
    """Deprecate a function, method, or class — the friendly front door.

    For a **callable** source (function, method, lambda, or descriptor) this forwards to the strict
    :func:`deprecated_callable` implementation: identical call forwarding, argument mapping, warning control,
    and ``__deprecated__`` metadata.  For a **class** source it delegates to
    :func:`~deprecate.proxy.deprecated_class`, emitting a ``UserWarning`` (suppressed when ``stream=None``);
    prefer ``@deprecated_class()`` directly for classes.

    ``deprecated()`` deliberately exposes only the arguments **common** to both shapes.  The one
    shape-specific option, ``attrs_mapping`` (selective attribute deprecation, class-only), lives on
    :func:`~deprecate.proxy.deprecated_class` — reach for it directly when you need the full class scope.

    Args:
        target: How to handle the deprecation. Defaults to :attr:`~deprecate.TargetMode.AUTO`, a decoration-time
            inference value that is resolved before metadata is stored. Pass an explicit value to forward calls
            or select a fixed mode:

            - ``Callable``: Forward all calls to this callable (function, method, or class). The decorated
              source's body is **not executed** under normal forwarding — use ``pass`` or ``...`` as the body.
            - :attr:`~deprecate.TargetMode.ARGS_REMAP` (or legacy ``True``): Self-deprecation — deprecate argument
              names only, remapping them within the same function body (callable source) or constructor
              (class source).
            - :attr:`~deprecate.TargetMode.NOTIFY`: Warning-only mode — no forwarding. The source body executes
              unchanged for a callable; a class proxy emits class-wide warnings. An explicitly selected
              ``NOTIFY`` is never rewritten: combining it with ``args_mapping`` is a misconfiguration, leaves
              the mapping inert, and emits :class:`UserWarning`.
            - :attr:`~deprecate.TargetMode.AUTO` (default): Infer the mode only when ``target`` is omitted.
              ``args_mapping`` resolves to :attr:`~deprecate.TargetMode.ARGS_REMAP`; no mapping resolves to
              warning-only behavior. The resolved value, never ``AUTO``, is stored in
              :class:`~deprecate.DeprecationConfig`.

            Passing ``target=None`` is a legacy warn-only synonym that emits :class:`FutureWarning` directing
            callers to use :attr:`~deprecate.TargetMode.NOTIFY`.
        deprecated_in: Version when the source was deprecated (e.g., "1.0.0"). Default is empty string.
        remove_in: Version when the source will be removed (e.g., "2.0.0"). Default is empty string.
        stream: Function to output warnings (default: :func:`~deprecate.deprecation.deprecation_warning`, which is
            :func:`warnings.warn` with ``FutureWarning`` category). Set to ``None`` to disable warnings entirely —
            this also silences the one-time class-dispatch notice described under ``Warns`` below.
        num_warns: Number of times to show the warning, per callable/attribute name or per proxy access:
            - ``1`` (default): Show warning once
            - ``-1``: Show warning on every call/access
            - ``0``: Suppress deprecation warnings
            - ``N > 1``: Show warning N times total
        message_template: Custom warning message template with format specifiers (``source_name``, ``source_path``,
            ``target_name``, ``target_path``, ``deprecated_in``, ``remove_in``, ``argument_map``); see
            :func:`deprecated_callable` for the full specifier reference.
        args_mapping: Map or skip arguments when forwarding — ``{"old_arg": "new_arg"}`` renames, ``{"old_arg":
            None}`` drops. When ``target`` is omitted, a mapping auto-resolves a callable or class source to
            :attr:`~deprecate.TargetMode.ARGS_REMAP`. An explicit :attr:`~deprecate.TargetMode.NOTIFY` leaves the
            mapping inert and emits :class:`UserWarning`.
        args_extra: Additional keyword arguments merged into the forwarded call after ``args_mapping`` is applied.
            Ignored under :attr:`~deprecate.TargetMode.NOTIFY`.
        skip_if: Conditionally deactivate the deprecation machinery — a ``bool``, or a zero-argument ``Callable``
            returning ``bool``. When it evaluates ``True``, a callable source executes its body with no warning
            and no forwarding; a class source is served as-is by the proxy with no warning, no mapping, and no
            target forwarding.
        update_docstring: If ``True``, inject a deprecation notice into the docstring — function or class — at
            decoration time.
        docstring_style: Output style for the injected notice when ``update_docstring=True`` — ``"auto"``
            (default, chosen from the active doc engine), ``"rst"``, or ``"mkdocs"`` / ``"markdown"``.
        template_mgs: Deprecated alias for ``message_template`` (renamed in ``v0.12``; the old spelling was a
            typo). Supplying it emits a :class:`FutureWarning` and its value is used as ``message_template``;
            supplying both raises :class:`TypeError`. Removed in ``v1.0``.

    Returns:
        Decorator that wraps the source callable, or the class proxy for a class source.

    Warns:
        UserWarning: If applied directly to a class. The decorator delegates to
            :func:`~deprecate.proxy.deprecated_class` and emits this informational notice once per class
            qualname per process. Use ``@deprecated_class()`` directly to skip it entirely. Suppressed when
            ``stream=None``.
        UserWarning: If ``deprecated_in`` is absent, ``stream`` is not ``None``, no ``message_template`` is set,
            and the decorated source is not a class. Fired at decoration time (not call time) to catch missing
            version metadata early. Suppressed by passing ``stream=None`` or ``message_template``.

    Example:
        >>> # Basic forwarding
        >>> def new_func(x: int) -> int:
        ...     return x * 2
        >>> @deprecated(target=new_func, deprecated_in="1.0", remove_in="2.0")
        ... def old_func(x: int) -> int:
        ...     pass

        >>> # Warn-only (default — no target needed)
        >>> @deprecated(deprecated_in="1.0", remove_in="2.0")
        ... def legacy_func(x: int) -> int:
        ...     return x

    """
    # Fold the deprecated ``template_mgs`` alias into ``message_template`` here at the front door so both
    # dispatch arms receive only the canonical name and the deprecation notice fires at most once.
    message_template = _resolve_message_template_alias(message_template, template_mgs)
    # Resolve the AUTO front-door default for the callable arm: a mapping present selects ARGS_REMAP,
    # otherwise warn-only NOTIFY. Only AUTO resolves — an explicit ``TargetMode.NOTIFY`` is a deliberate
    # choice and is validated against the mapping by ``deprecated_callable`` instead.
    _callable_target = (
        (TargetMode.ARGS_REMAP if args_mapping else TargetMode.NOTIFY) if target is TargetMode.AUTO else target
    )
    _callable_pack = deprecated_callable(
        target=_callable_target,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        stream=stream,
        num_warns=num_warns,
        message_template=message_template,
        args_mapping=args_mapping,
        args_extra=args_extra,
        skip_if=skip_if,
        update_docstring=update_docstring,
        docstring_style=docstring_style,
    )

    def packing(
        source: Union[Callable, classmethod, staticmethod, property, cached_property],
        _stacklevel: int = 2,
        _is_static: bool = False,
    ) -> Callable:
        # Class sources delegate to ``deprecated_class`` (Phase 1 warn-and-delegate); every callable routes
        # to the strict ``deprecated_callable`` arm.  Delegating adds one frame between the user's decoration
        # site and ``deprecated_callable``'s ``packing``, so the callable path bumps ``_stacklevel`` by one.
        if inspect.isclass(source):
            # ``ATTRS_REMAP`` needs ``attrs_mapping`` to have any effect, but the front door does not expose
            # that class-only knob (it raises ``TypeError`` as an unexpected kwarg). The mode is therefore
            # unreachable here for any source — reject it loudly instead of building a no-op proxy. The
            # callable path already rejects it via the proxy-only guard in ``deprecated_callable``.
            if target is TargetMode.ATTRS_REMAP:
                raise TypeError(
                    f"`target=TargetMode.ATTRS_REMAP` is not valid for `@deprecated` on class `{source.__name__}`. "
                    "`ATTRS_REMAP` requires `attrs_mapping`, which the `@deprecated` front door does not expose. "
                    "Use `deprecated_class(attrs_mapping=...)` directly — a bare mapping auto-resolves the mode, "
                    "or pass `target=TargetMode.ATTRS_REMAP` together with `attrs_mapping` there."
                )
            # Preserve decoration-time template validation for the class path (was eager in the old flow).
            _validate_message_template(message_template)
            return _packing_class_source(
                source,
                target,
                _PackingClassArgs(
                    deprecated_in=deprecated_in,
                    remove_in=remove_in,
                    num_warns=num_warns,
                    stream=stream,
                    message_template=message_template,
                    args_mapping=args_mapping,
                    args_extra=args_extra,
                    skip_if=skip_if,
                    update_docstring=update_docstring,
                    docstring_style=docstring_style,
                    _stacklevel=_stacklevel + 1,
                ),
            )
        # Non-class source from here — the decoration-time guard lives in ``_dispatch.py``
        # alongside ``_reject_bare_decorator``, the sibling guard this dispatcher also uses.
        _reject_non_callable_source(source, target)
        # ``_stacklevel``/``_is_static`` are internal parameters of ``deprecated_callable``'s ``packing``,
        # intentionally omitted from its public return annotation (hence the call-arg ignore).
        return _callable_pack(source, _stacklevel + 1, _is_static)  # type: ignore[call-arg, arg-type]

    return packing
