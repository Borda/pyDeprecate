"""Front-door `deprecated` decorator — dispatches functions/methods and classes.

:func:`deprecated` is the friendly umbrella decorator: it routes callable sources to
:func:`~deprecate.routine.deprecated_callable` and class sources to :func:`~deprecate.proxy.deprecated_class` (emitting
a ``UserWarning``).  It is defined here so the historical import path ``from deprecate.deprecation import deprecated``
keeps resolving natively.

The specialized decorators live in their target modules: :func:`~deprecate.routine.deprecated_callable`
(functions/methods), :func:`~deprecate.proxy.deprecated_class` / :func:`~deprecate.proxy.deprecated_instance`
(classes/objects), and :func:`~deprecate.module.deprecated_module` (modules).

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import inspect
import warnings
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Callable, Literal, Optional, Union

from deprecate._types import TargetMode
from deprecate.messaging import _validate_template_mgs, deprecation_warning
from deprecate.routine import deprecated_callable


@dataclass
class _PackingClassArgs:
    """Grouped keyword arguments for :func:`~deprecate.deprecation._packing_class_source`."""

    deprecated_in: str
    remove_in: str
    num_warns: int
    stream: Optional[Callable]
    args_mapping: Optional[dict[str, Optional[str]]]
    args_extra: Optional[dict[str, Any]]
    update_docstring: bool
    docstring_style: str
    _stacklevel: int


def _packing_class_source(
    source: type,
    target: Union[bool, None, Callable, TargetMode, staticmethod, classmethod],
    pack_args: _PackingClassArgs,
) -> Callable:
    """Delegate class-source deprecation to :func:`~deprecate.proxy.deprecated_class`.

    Handles legacy ``target`` sentinel resolution, misconfig detection, and the
    ``UserWarning`` emitted when ``@deprecated`` is applied directly to a class
    (deprecated itself since v0.6.0). Extracted from the ``inspect.isclass(source)``
    branch of ``packing``.

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

    message = (
        f"Direct use of `@deprecated` on class `{source.__name__}` is deprecated since `v0.6.0`."
        " Use `@deprecated_class(...)` instead. This will become a `TypeError` in a future release."
    )
    if target is not None and not inspect.isclass(target) and not isinstance(target, TargetMode):
        message += (
            " Note: non-class `target` values are ignored when deprecating classes;"
            " use `@deprecated_class(target=...)` instead."
        )
    if pack_args.stream is not None:
        warnings.warn(message, UserWarning, stacklevel=pack_args._stacklevel)

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
        forward_target = TargetMode._from_legacy(target, stacklevel=pack_args._stacklevel + 1)
    else:
        forward_target = TargetMode.NOTIFY

    # Capture misconfig signals *before* nulling args_mapping/args_extra — NOTIFY + either
    # field is a misconfig the proxy can no longer detect once we strip those fields.
    notify_misconfig = forward_target is TargetMode.NOTIFY and bool(pack_args.args_mapping or pack_args.args_extra)
    force_misconfigured = class_misconfigured or notify_misconfig

    if forward_target is TargetMode.NOTIFY:
        TargetMode._validate(
            forward_target,
            source.__name__,
            args_mapping=pack_args.args_mapping,
            args_extra=pack_args.args_extra,
            stacklevel=pack_args._stacklevel + 1,
        )
        pack_args.args_mapping = None
        pack_args.args_extra = None

    return deprecated_class_fn(
        target=forward_target,
        deprecated_in=pack_args.deprecated_in,
        remove_in=pack_args.remove_in,
        num_warns=pack_args.num_warns,
        stream=pack_args.stream,
        args_mapping=pack_args.args_mapping,
        args_extra=pack_args.args_extra,
        update_docstring=pack_args.update_docstring,
        docstring_style=pack_args.docstring_style,
        _misconfigured_override=force_misconfigured,
    )(source)


def deprecated(
    target: Union[bool, None, Callable, TargetMode, staticmethod, classmethod] = TargetMode.NOTIFY,
    deprecated_in: str = "",
    remove_in: str = "",
    stream: Optional[Callable] = deprecation_warning,
    num_warns: int = 1,
    template_mgs: Optional[str] = None,
    args_mapping: Optional[dict[str, Optional[str]]] = None,
    args_extra: Optional[dict[str, Any]] = None,
    skip_if: Union[bool, Callable] = False,
    update_docstring: bool = False,
    docstring_style: Literal["auto", "rst", "mkdocs", "markdown"] = "auto",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Deprecate a function, method, or class — the friendly front door.

    For a **callable** source (function, method, lambda, or descriptor) this forwards to the strict
    :func:`deprecated_callable` implementation: identical call forwarding, argument mapping, warning control,
    and ``__deprecated__`` metadata.  For a **class** source it delegates to
    :func:`~deprecate.proxy.deprecated_class`, emitting a ``UserWarning`` (suppressed when ``stream=None``);
    prefer ``@deprecated_class()`` directly for classes.

    Every parameter has the same meaning as in :func:`deprecated_callable`, which documents them in full.

    Args:
        target: See :func:`deprecated_callable`.
        deprecated_in: See :func:`deprecated_callable`.
        remove_in: See :func:`deprecated_callable`.
        stream: See :func:`deprecated_callable`.
        num_warns: See :func:`deprecated_callable`.
        template_mgs: See :func:`deprecated_callable`.
        args_mapping: See :func:`deprecated_callable`.
        args_extra: See :func:`deprecated_callable`.
        skip_if: See :func:`deprecated_callable`.
        update_docstring: See :func:`deprecated_callable`.
        docstring_style: See :func:`deprecated_callable`.

    Returns:
        Decorator that wraps the source callable, or the class proxy for a class source.

    Warns:
        UserWarning: If applied directly to a class. The decorator delegates to
            :func:`~deprecate.proxy.deprecated_class` and emits this warning. Use ``@deprecated_class()`` directly
            to suppress it. Suppressed when ``stream=None``.
        UserWarning: If ``deprecated_in`` is absent, ``stream`` is not ``None``, no ``template_mgs`` is set,
            and the decorated source is not a class. Fired at decoration time (not call time) to catch missing
            version metadata early. Suppressed by passing ``stream=None`` or ``template_mgs``.

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
    _callable_pack = deprecated_callable(
        target=target,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        stream=stream,
        num_warns=num_warns,
        template_mgs=template_mgs,
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
            # Preserve decoration-time template validation for the class path (was eager in the old flow).
            _validate_template_mgs(template_mgs)
            return _packing_class_source(
                source,
                target,
                _PackingClassArgs(
                    deprecated_in=deprecated_in,
                    remove_in=remove_in,
                    num_warns=num_warns,
                    stream=stream,
                    args_mapping=args_mapping,
                    args_extra=args_extra,
                    update_docstring=update_docstring,
                    docstring_style=docstring_style,
                    _stacklevel=_stacklevel + 1,
                ),
            )
        # ``_stacklevel``/``_is_static`` are internal parameters of ``deprecated_callable``'s ``packing``,
        # intentionally omitted from its public return annotation (hence the call-arg ignore).
        return _callable_pack(source, _stacklevel + 1, _is_static)  # type: ignore[call-arg, arg-type]

    return packing
