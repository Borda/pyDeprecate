"""Decoration-time target resolution and validation for pyDeprecate.

Legacy target sentinels resolve to explicit modes before callable wrappers or proxies are built. This module also holds
the source guards, the check that a method target belongs to the source's class, target normalization, stacking
compatibility and the positional-only target warnings, so the call-time dispatcher only handles call facts and
forwarding.

The decorator and proxy entry points consume these private helpers at decoration time. Validation emits the existing
configuration warnings or raises the existing errors; every warning uses the caller-frame walk to report user code.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import inspect
import sys
import warnings
from collections.abc import Mapping
from functools import cached_property, partial, partialmethod, singledispatchmethod
from typing import Any, Callable, Optional, Union

from deprecate._frames import _caller_stacklevel
from deprecate._types import TargetMode, get_deprecation_config

#: Release that removes the legacy spellings and turns misconfiguration warnings into ``TypeError``.
#: Every runtime notice naming that sunset renders this value, so the version is set in one place.
_MAJOR_BREAK_VERSION = "v1.0"


def _resolve_legacy_target(
    target: Union[None, bool],
    *,
    emit: bool = True,
) -> "TargetMode":
    """Convert ``target`` sentinel (``None`` / ``True`` / ``False``) to :class:`~deprecate._types.TargetMode`.

    Backwards-compatibility shim for the :func:`~deprecate.deprecated` decorator.  Each sentinel is mapped to its
    modern equivalent and a deprecation warning is emitted.  Pass ``emit=False`` to suppress all warnings.

    Args:
        target: Legacy sentinel — ``None`` → :attr:`~deprecate._types.TargetMode.NOTIFY`,
            ``True`` → :attr:`~deprecate._types.TargetMode.ARGS_REMAP`,
            ``False`` → :attr:`~deprecate._types.TargetMode.NOTIFY` (not a valid mode; :class:`TypeError` in v1.0).
        emit: Whether to warn about the legacy sentinel.  Pass ``False`` to convert silently.  The warning points
            at the first frame outside the ``deprecate`` package.  Defaults to ``True``.

    Returns:
        The corresponding :class:`~deprecate._types.TargetMode` member.

    Raises:
        TypeError: If ``target`` is anything other than ``None``, ``True``, or ``False``.

    Examples:
        >>> _resolve_legacy_target(None, emit=False)
        <TargetMode.NOTIFY: 'notify'>
        >>> _resolve_legacy_target(True, emit=False)
        <TargetMode.ARGS_REMAP: 'args_remap'>
        >>> _resolve_legacy_target(False, emit=False)
        <TargetMode.NOTIFY: 'notify'>

    """
    if target is None:
        if emit:
            warnings.warn(
                "target=None is deprecated since `v0.8`; use `TargetMode.NOTIFY` instead."
                f" Will be removed in `{_MAJOR_BREAK_VERSION}`.",
                FutureWarning,
                stacklevel=_caller_stacklevel(),
            )
        return TargetMode.NOTIFY
    if target is True:
        if emit:
            warnings.warn(
                "target=True is deprecated since `v0.8`; use `TargetMode.ARGS_REMAP` instead."
                f" Will be removed in `{_MAJOR_BREAK_VERSION}`.",
                FutureWarning,
                stacklevel=_caller_stacklevel(),
            )
        return TargetMode.ARGS_REMAP
    if target is False:
        if emit:
            warnings.warn(
                "`target=False` is not a valid deprecation mode and will be treated as `TargetMode.NOTIFY`."
                f" This will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
                UserWarning,
                stacklevel=_caller_stacklevel(),
            )
        return TargetMode.NOTIFY
    raise TypeError(f"Legacy `target` accepts only None, True, or False; got {type(target).__name__}: {target!r}.")


def _resolve_legacy_proxy_target(
    target: Any,  # noqa: ANN401
    *,
    args_mapping: Optional[Mapping[str, Optional[str]]] = None,
    emit: bool = True,
) -> "Union[Callable[..., Any], TargetMode, None]":
    """Normalise a proxy-specific legacy ``target`` sentinel for :func:`~deprecate.proxy.deprecated_class`.

    ``True`` and ``False`` are invalid for the proxy context and normalise to ``None`` — the proxy's
    unset-target value (or :attr:`~deprecate._types.TargetMode.ARGS_REMAP` when ``args_mapping`` is
    non-empty) after emitting a warning, so any configured mapping still auto-resolves like an omitted
    target. All other values pass through unchanged.  Pass ``emit=False`` to suppress warnings.

    Args:
        target: Raw ``target`` value from the caller.  Only ``True`` and ``False`` trigger normalisation;
            any other value is returned as-is.
        args_mapping: The ``args_mapping`` dict supplied to :func:`~deprecate.deprecated_class`, or ``None`` when
            not provided. When ``target=True`` and ``args_mapping`` is non-empty the result is
            :attr:`~deprecate._types.TargetMode.ARGS_REMAP` instead of
            :attr:`~deprecate._types.TargetMode.NOTIFY`, and a :class:`FutureWarning` is emitted.
        emit: Whether to warn about the legacy sentinel.  Pass ``False`` to convert silently.  The warning points
            at the first frame outside the ``deprecate`` package.  Defaults to ``True``.

    Returns:
        :attr:`~deprecate._types.TargetMode.ARGS_REMAP` when ``target=True`` and ``args_mapping`` is non-empty;
        ``None`` (the proxy's unset-target value) when ``target`` was ``True`` (without ``args_mapping``) or
        ``False``; otherwise ``target`` unchanged.

    Examples:
        >>> _resolve_legacy_proxy_target(True, emit=False) is None
        True
        >>> _resolve_legacy_proxy_target(True, args_mapping={"old": "new"}, emit=False)
        <TargetMode.ARGS_REMAP: 'args_remap'>
        >>> _resolve_legacy_proxy_target(False, emit=False) is None
        True
        >>> _resolve_legacy_proxy_target(None) is None
        True
        >>> _resolve_legacy_proxy_target(TargetMode.NOTIFY)
        <TargetMode.NOTIFY: 'notify'>

    """
    if target is True:
        if args_mapping:
            if emit:
                warnings.warn(
                    "`target=True` with `args_mapping` will resolve to `TargetMode.ARGS_REMAP`."
                    f" Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
                    FutureWarning,
                    stacklevel=_caller_stacklevel(),
                )
            return TargetMode.ARGS_REMAP
        if emit:
            warnings.warn(
                "`target=True` without `args_mapping` resolves to `TargetMode.NOTIFY`"
                f" (warns on every access). Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
                FutureWarning,
                stacklevel=_caller_stacklevel(),
            )
        # ``None`` (the proxy's unset value), not NOTIFY: legacy sentinels behave like an omitted
        # target, so a configured ``attrs_mapping`` still auto-resolves instead of being flagged as an
        # explicit-NOTIFY conflict.
        return None
    if target is False:
        if emit:
            warnings.warn(
                "`target=False` is not valid for `deprecated_class()`."
                f" Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`.",
                UserWarning,
                stacklevel=_caller_stacklevel(),
            )
        return None
    return target


def _validate_callable_mode(
    mode: "TargetMode",
    source_name: str,
    args_mapping: Optional[Mapping[str, Optional[str]]] = None,
    args_extra: Optional[dict[str, Any]] = None,
    *,
    emit: bool = True,
) -> bool:
    """Validate :class:`~deprecate._types.TargetMode` against supplied configuration and emit misconfig warnings.

    Checks three misconfiguration combinations and emits a :class:`UserWarning` for each one found.
    These warnings will become :class:`TypeError` in ``v1.0``.

    Misconfiguration warnings are always emitted via :func:`warnings.warn` with ``UserWarning`` — they are
    construction-time guards independent of the runtime ``stream`` callable used for deprecation notices.

    Args:
        mode: The resolved :class:`~deprecate._types.TargetMode` to validate.
        source_name: ``__name__`` of the decorated source callable, used in warning messages.
        args_mapping: The ``args_mapping`` dict supplied to :func:`~deprecate.deprecated`, or ``None`` when not
            provided.
        args_extra: The ``args_extra`` dict supplied to :func:`~deprecate.deprecated`, or ``None`` when not
            provided.
        emit: Whether to warn about each misconfiguration found.  Pass ``False`` to only report it through the
            return value.  The warning points at the first frame outside the ``deprecate`` package — the
            decorator application site.  Defaults to ``True``.

    Returns:
        ``True`` if any misconfiguration was detected, ``False`` if the configuration is valid.

    Examples:
        >>> _validate_callable_mode(TargetMode.ARGS_REMAP, "my_func", args_mapping=None, emit=False)
        True
        >>> _validate_callable_mode(TargetMode.NOTIFY, "my_func", args_mapping={"old": "new"}, emit=False)
        True
        >>> _validate_callable_mode(TargetMode.NOTIFY, "my_func", args_extra={"bias": 1}, emit=False)
        True
        >>> _validate_callable_mode(TargetMode.NOTIFY, "my_func", emit=False)
        False

    """
    messages = []
    if mode is TargetMode.ARGS_REMAP and not args_mapping:
        messages.append(
            f"`@deprecated(target=TargetMode.ARGS_REMAP)` on `{source_name}` requires "
            "`args_mapping` to specify which arguments are being renamed. Without it the "
            f"decorator has zero effect. This will be `TypeError` in `{_MAJOR_BREAK_VERSION}`."
        )
    if mode is TargetMode.NOTIFY and args_mapping:
        messages.append(
            f"`@deprecated(target=TargetMode.NOTIFY)` on `{source_name}` ignores "
            "`args_mapping`. Omit `target` to auto-resolve to `TargetMode.ARGS_REMAP`, pass "
            "`TargetMode.ARGS_REMAP` explicitly, or pass a callable target to forward the call. "
            f"This will be `TypeError` in `{_MAJOR_BREAK_VERSION}`."
        )
    if mode is TargetMode.NOTIFY and args_extra:
        messages.append(
            f"`@deprecated(target=TargetMode.NOTIFY)` on `{source_name}` ignores "
            "`args_extra`. Use a callable target to forward with extra arguments. "
            f"This will be `TypeError` in `{_MAJOR_BREAK_VERSION}`."
        )
    if emit:
        for msg in messages:
            warnings.warn(msg, UserWarning, stacklevel=_caller_stacklevel())
    return bool(messages)


def _validate_proxy_mode(
    mode: "Union[TargetMode, Callable[..., Any], None]",
    source_name: str,
    attrs_mapping: Optional[Mapping[str, Optional[str]]] = None,
    args_mapping: Optional[Mapping[str, Optional[str]]] = None,
    args_extra: Optional[dict[str, Any]] = None,
    *,
    emit: bool = True,
) -> bool:
    """Validate proxy-specific configuration: ``attrs_mapping`` combinations.

    Catches misconfiguration pairs involving ``attrs_mapping`` that the existing
    :func:`~deprecate._validate._validate_callable_mode` does not cover (it handles only ``args_mapping`` /
    ``args_extra`` and is used by the function-decorator path as well).

    Misconfiguration warnings will become :class:`TypeError` in ``v1.0``.

    Args:
        mode: Resolved target (:class:`~deprecate._types.TargetMode` member, callable, or ``None``).
        source_name: ``__name__`` of the decorated class, for warning messages.
        attrs_mapping: The ``attrs_mapping`` dict passed to :func:`~deprecate.proxy.deprecated_class`, or
            ``None``.
        args_mapping: The ``args_mapping`` dict, forwarded here so we can detect the
            ``ATTRS_REMAP + args_mapping`` combination.
        args_extra: The ``args_extra`` dict, forwarded here so we can detect the
            ``ATTRS_REMAP + args_extra`` combination — ``ATTRS_REMAP`` governs attribute access only and
            does not honour call-time kwarg injection.
        emit: Whether to warn about each misconfiguration found.  Pass ``False`` to only report it through the
            return value.  Defaults to ``True``.

    Returns:
        ``True`` if any misconfiguration was detected, ``False`` otherwise.

    Examples:
        >>> # Explicit NOTIFY + attrs_mapping is contradictory — only TargetMode.AUTO (the factory
        >>> # default, i.e. an omitted ``target``) auto-resolves a mapping to ATTRS_REMAP.
        >>> _validate_proxy_mode(TargetMode.NOTIFY, "Cls", attrs_mapping={"a": "b"}, emit=False)
        True
        >>> _validate_proxy_mode(TargetMode.ATTRS_REMAP, "Cls", attrs_mapping=None, emit=False)
        True
        >>> _validate_proxy_mode(TargetMode.ATTRS_REMAP, "Cls", attrs_mapping={}, emit=False)
        True
        >>> _validate_proxy_mode(TargetMode.ATTRS_REMAP, "Cls", attrs_mapping={"a": "b"}, emit=False)
        False
        >>> _validate_proxy_mode(
        ...     TargetMode.ARGS_REMAP, "Cls",
        ...     args_mapping={"old": "new"}, attrs_mapping={"a": "b"}, emit=False,
        ... )
        True
        >>> _validate_proxy_mode(
        ...     TargetMode.ATTRS_REMAP, "Cls",
        ...     args_mapping={"old": "new"}, attrs_mapping={"a": "b"}, emit=False,
        ... )
        True
        >>> _validate_proxy_mode(
        ...     TargetMode.ATTRS_REMAP, "Cls",
        ...     attrs_mapping={"a": "b"}, args_extra={"bias": 1}, emit=False,
        ... )
        True

    """
    messages = []
    # Explicit NOTIFY + attrs_mapping is contradictory: only TargetMode.AUTO (the factory default)
    # auto-resolves a mapping — an explicitly chosen NOTIFY must not be silently overridden, and the
    # mapping is ignored at runtime instead.
    if mode is TargetMode.NOTIFY and attrs_mapping:
        messages.append(
            f"`deprecated_class(target=TargetMode.NOTIFY)` on `{source_name}` ignores "
            "`attrs_mapping`. Omit `target` to auto-resolve to `TargetMode.ATTRS_REMAP`, or pass "
            f"`TargetMode.ATTRS_REMAP` explicitly. This will be `TypeError` in `{_MAJOR_BREAK_VERSION}`."
        )
    if mode is TargetMode.ARGS_REMAP and args_mapping and attrs_mapping:
        messages.append(
            f"`deprecated_class` on `{source_name}` has both `args_mapping` and `attrs_mapping` "
            "configured with `target=TargetMode.ARGS_REMAP`. `ARGS_REMAP` governs call-time argument "
            "renames only; `attrs_mapping` is inactive in this mode. "
            "`DeprecationConfig.target` no longer reflects that `attrs_mapping` is also active. "
            "Pass an explicit callable `target=<class>` to activate both mappings on the same proxy. "
            f"This will be `TypeError` in `{_MAJOR_BREAK_VERSION}`."
        )
    if mode is TargetMode.ATTRS_REMAP and not attrs_mapping:
        messages.append(
            f"`deprecated_class(target=TargetMode.ATTRS_REMAP)` on `{source_name}` requires "
            "`attrs_mapping` to specify which attribute names are deprecated. Without it the "
            f"proxy has zero selective effect. This will be `TypeError` in `{_MAJOR_BREAK_VERSION}`."
        )
    if mode is TargetMode.ATTRS_REMAP and args_mapping:
        messages.append(
            f"`deprecated_class(target=TargetMode.ATTRS_REMAP)` on `{source_name}` ignores `args_mapping`. "
            "`ATTRS_REMAP` only governs attribute access; argument renames on `__call__` are not applied. "
            "Use `target=<class>` (with both mappings) or `TargetMode.ARGS_REMAP` (with `args_mapping` only). "
            f"This will be `TypeError` in `{_MAJOR_BREAK_VERSION}`."
        )
    if mode is TargetMode.ATTRS_REMAP and args_extra:
        messages.append(
            f"`deprecated_class(target=TargetMode.ATTRS_REMAP)` on `{source_name}` ignores `args_extra`. "
            "`ATTRS_REMAP` only governs attribute access; call-time kwarg injection is not applied. "
            "Use `target=<class>` to forward calls with extra arguments. "
            f"This will be `TypeError` in `{_MAJOR_BREAK_VERSION}`."
        )
    if attrs_mapping is not None and len(attrs_mapping) == 0:
        messages.append(
            f"`deprecated_class` on `{source_name}` received `attrs_mapping={{}}` (empty dict). "
            "An empty mapping has no effect — remove it or add deprecated attribute names. "
            f"This will be `TypeError` in `{_MAJOR_BREAK_VERSION}`."
        )
    if emit:
        for msg in messages:
            warnings.warn(msg, UserWarning, stacklevel=_caller_stacklevel())
    return bool(messages)


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
    if not callable(source) and not isinstance(source, _DESCRIPTOR_SOURCE_TYPES):
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


_STACKING_CALLABLE = "callable"
_STACKING_NOTIFY = "notify"
_STACKING_ARGS_REMAP = "args_remap"
_STACKING_OTHER = "other"

_UNSUPPORTED_STACKING_MESSAGE = (
    f"'{{name}}' has an unsupported @deprecated stacking combination. Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`."
)
_STACKING_MESSAGES: dict[tuple[str, str], Optional[str]] = {
    (_STACKING_CALLABLE, _STACKING_CALLABLE): (
        "'{name}' has a callable target stacked over another callable-target @deprecated. Stacking a callable "
        "target over another callable target is not supported. This will raise `TypeError` at call time. Will be "
        f"`TypeError` in `{_MAJOR_BREAK_VERSION}`."
    ),
    (_STACKING_CALLABLE, _STACKING_NOTIFY): (
        "'{name}' has a callable target stacked over @deprecated(NOTIFY). The inner function-deprecated warning "
        "will not fire at call time; the inner layer is bypassed while the callable target is still invoked. "
        "Collapse to a single @deprecated(target=<callable>) and remove the inner @deprecated(NOTIFY). Will be "
        f"`TypeError` in `{_MAJOR_BREAK_VERSION}`."
    ),
    (_STACKING_CALLABLE, _STACKING_ARGS_REMAP): (
        "'{name}' has a callable target stacked over @deprecated(ARGS_REMAP). The arg-rename warning will not fire "
        "at call time; the inner layer is bypassed. Collapse to: @deprecated(target=<callable>, "
        f"args_mapping={{{{...}}}}). Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`."
    ),
    (_STACKING_CALLABLE, _STACKING_OTHER): _UNSUPPORTED_STACKING_MESSAGE,
    (_STACKING_NOTIFY, _STACKING_CALLABLE): None,
    (_STACKING_NOTIFY, _STACKING_NOTIFY): (
        "'{name}' has duplicate @deprecated(NOTIFY) layers. Update the existing decorator's `deprecated_in`, "
        f"`remove_in`, or `message_template` instead. Will be `TypeError` in `{_MAJOR_BREAK_VERSION}`."
    ),
    (_STACKING_NOTIFY, _STACKING_ARGS_REMAP): (
        "'{name}' has @deprecated(NOTIFY) stacked over @deprecated(ARGS_REMAP). Reverse the decorator order: put "
        "@deprecated(ARGS_REMAP, ...) outermost (on top) and @deprecated(NOTIFY, ...) below it. Will be "
        f"`TypeError` in `{_MAJOR_BREAK_VERSION}`."
    ),
    (_STACKING_NOTIFY, _STACKING_OTHER): _UNSUPPORTED_STACKING_MESSAGE,
    (_STACKING_ARGS_REMAP, _STACKING_CALLABLE): (
        "'{name}' has @deprecated(ARGS_REMAP) stacked over a callable-target @deprecated. Update the inner "
        "@deprecated(target=<callable>, args_mapping={{...}}) instead of stacking. Will be `TypeError` in "
        f"`{_MAJOR_BREAK_VERSION}`."
    ),
    (_STACKING_ARGS_REMAP, _STACKING_NOTIFY): None,
    (_STACKING_ARGS_REMAP, _STACKING_ARGS_REMAP): None,
    (_STACKING_ARGS_REMAP, _STACKING_OTHER): _UNSUPPORTED_STACKING_MESSAGE,
    (_STACKING_OTHER, _STACKING_CALLABLE): _UNSUPPORTED_STACKING_MESSAGE,
    (_STACKING_OTHER, _STACKING_NOTIFY): _UNSUPPORTED_STACKING_MESSAGE,
    (_STACKING_OTHER, _STACKING_ARGS_REMAP): _UNSUPPORTED_STACKING_MESSAGE,
    (_STACKING_OTHER, _STACKING_OTHER): _UNSUPPORTED_STACKING_MESSAGE,
}


def _stacking_kind(target: object) -> str:
    """Classify a normalized target for the stacking compatibility table."""
    if callable(target):
        return _STACKING_CALLABLE
    if target is TargetMode.NOTIFY:
        return _STACKING_NOTIFY
    if target is TargetMode.ARGS_REMAP:
        return _STACKING_ARGS_REMAP
    return _STACKING_OTHER


def _warn_stacking_misconfiguration(source: Callable, outer_target: Union[TargetMode, Callable]) -> None:
    """Emit ``UserWarning`` at decoration time for unsupported stacking combinations.

    Only called when ``source`` already carries ``__deprecation_config__`` metadata (i.e. is itself a
    ``@deprecated`` wrapper).  Supported combinations are silently accepted:

    - ``ARGS_REMAP`` (outer) + ``ARGS_REMAP`` (inner): multi-step arg renames across versions.
    - ``ARGS_REMAP`` (outer) + ``NOTIFY`` (inner): lifecycle pattern — rename args first, deprecate
      the whole function later.
    - ``NOTIFY`` (outer) + ``callable`` (inner): outer NOTIFY warns callers the function is going
      away; inner callable handles forwarding.

    All other combinations produce ``UserWarning`` at decoration time.

    """
    inner_config = get_deprecation_config(source)
    if inner_config is None:
        raise TypeError(f"'{source.__name__}' no longer carries valid deprecation metadata.")
    message = _STACKING_MESSAGES[(_stacking_kind(outer_target), _stacking_kind(inner_config.target))]
    if message is not None:
        warnings.warn(message.format(name=source.__name__), UserWarning, stacklevel=_caller_stacklevel())


def _has_leading_positional(source: Callable) -> bool:
    """Tell whether ``source`` has a leading parameter that can receive a class positionally.

    A ``classmethod`` binds the class as the first positional argument, so a source forwarding to a ``classmethod``
    descriptor needs one, whatever its name.  A ``*args``-first or keyword-only-first source has no such slot, and a
    source whose signature cannot be read is treated as having none.

    Args:
        source: The callable being decorated with ``@deprecated``.

    Returns:
        ``True`` when the first parameter is positional-only or positional-or-keyword.

    Example:
        >>> def old(klass, value): pass
        >>> _has_leading_positional(old)
        True
        >>> def old_static(*, value): pass
        >>> _has_leading_positional(old_static)
        False

    """
    try:
        params = list(inspect.signature(source).parameters.values())
    except (ValueError, TypeError):
        return False
    return bool(params) and params[0].kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )


def _receiver_name_clash(source: Callable, target: Callable) -> Optional[str]:
    """Name a later source parameter that shares the receiver name of a ``classmethod`` descriptor target.

    The forwarded call renames the source's receiver to the target's receiver name, so a later source parameter with
    that same name would hand the caller's value to the target as ``cls`` and the class under another keyword.

    Args:
        source: The callable being decorated with ``@deprecated``.
        target: The function unwrapped from the ``classmethod`` descriptor.

    Returns:
        The clashing parameter name, or ``None`` when the receivers share a name, either signature is unreadable, or
        no later source parameter uses the target's receiver name.

    Example:
        >>> def make(cls, value): pass
        >>> def old(klass, cls=None): pass
        >>> _receiver_name_clash(old, make)
        'cls'
        >>> def old_ok(klass, value): pass
        >>> print(_receiver_name_clash(old_ok, make))
        None

    """
    try:
        source_names = list(inspect.signature(source).parameters)
        target_names = list(inspect.signature(target).parameters)
    except (ValueError, TypeError):
        return None
    if not source_names or not target_names or source_names[0] == target_names[0]:
        return None
    return target_names[0] if target_names[0] in source_names[1:] else None


def _classmethod_target_error(source_name: str, target: Callable, problem: str) -> TypeError:
    """Build the ``TypeError`` raised when a source cannot hand a class to its ``classmethod`` descriptor target.

    Raised at decoration by :func:`_normalize_target` for a source with no leading positional parameter or with a later
    parameter named like the target's receiver (:func:`_receiver_name_clash`), and on a
    forwarded call by ``_dispatch._check_classmethod_receiver`` when that parameter received something other than a
    class.  Both share one message, so the remedy reads the same whichever check fires.

    Args:
        source_name: ``__name__`` of the decorated source.
        target: The function unwrapped from the ``classmethod`` descriptor.
        problem: What was wrong, completing the sentence "..., but <problem>.".

    Returns:
        The error, for the caller to raise.

    Example:
        >>> def make(cls): pass
        >>> print(_classmethod_target_error("old", make, "the first argument was 1"))  # doctest: +NORMALIZE_WHITESPACE
        @deprecated(target=<classmethod descriptor>) on 'old': descriptor targets require the source to accept a
        leading class argument so it can be forwarded to the replacement, but the first argument was 1. Either make
        'old' a @classmethod, or pass a bound target like 'target=<YourClass>.make' after the class is defined.

    """
    func_name = getattr(target, "__name__", "target")
    return TypeError(
        f"@deprecated(target=<classmethod descriptor>) on '{source_name}': "
        "descriptor targets require the source to accept a leading class argument "
        f"so it can be forwarded to the replacement, but {problem}. "
        f"Either make '{source_name}' a @classmethod, or pass a bound target like "
        f"'target=<YourClass>.{func_name}' after the class is defined."
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
      has a leading positional parameter to receive the class, whatever it is named (``cls``, ``klass``, ``mcs``).
      A source with no such parameter raises :exc:`TypeError` at decoration time.  Whether the value that parameter
      receives is a class is checked on each forwarded call instead: a static or plain source pointed at a
      ``classmethod`` descriptor by mistake raises the same :exc:`TypeError` on its first call, before any warning.

    Note on the ``classmethod`` guard: a source whose signature cannot be read, or a ``functools.partial`` whose
    first argument is already bound, is rejected too.  In that case pass the unwrapped target explicitly:
    ``target=your_classmethod.__func__``; the receiver is then forwarded by name, so the source must name it as the
    target does.  A bound target such as ``YourClass.method`` is not an alternative for
    a genuine classmethod source: the bound target has no receiver parameter, so the source's receiver argument
    is rejected.

    Args:
        source: The callable being decorated with ``@deprecated``.
        target: Raw ``target`` argument from the ``@deprecated`` call.

    Returns:
        Normalised target suitable for use inside ``wrapped_fn``.

    Raises:
        TypeError: When a class target is used on a non-``__init__`` class method, or when ``target`` is a
            ``classmethod`` descriptor and ``source`` has no leading positional parameter.

    """
    # --- Legacy sentinel conversion (v0.8 compat shim; removed in v1.0) ---
    if target is None or isinstance(target, bool):
        return _resolve_legacy_target(target)

    # --- TargetMode enum pass-through ---
    if isinstance(target, TargetMode):
        return target

    # --- Descriptor unwrap (staticmethod/classmethod passed from class body before binding) ---
    if isinstance(target, (staticmethod, classmethod)):
        # Guard: classmethod.__func__ expects the class as its first argument.  A source with no leading positional
        # parameter can never supply it, so raise now; whether the value a source does supply is a class is checked
        # on each forwarded call (``_dispatch._check_classmethod_receiver``), because the receiver's name is free.
        if isinstance(target, classmethod) and not _has_leading_positional(source):
            raise _classmethod_target_error(
                source.__name__, target.__func__, f"no leading positional parameter was found on '{source.__name__}'"
            )
        clash = _receiver_name_clash(source, target.__func__) if isinstance(target, classmethod) else None
        if clash is not None:
            raise _classmethod_target_error(
                source.__name__,
                target.__func__,
                f"its later parameter `{clash}` shares the target's receiver name; rename that parameter",
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


def _warn_target_positional_only(
    target: Union[Callable, TargetMode],
    source: Callable,
    target_positional_only: frozenset[str],
    stream: Optional[Callable],
) -> None:
    """Emit a ``UserWarning`` when a forwarding target declares POSITIONAL_ONLY parameters.

    Called at decoration time by :func:`~deprecate._dispatch._build_call_facts` so the warning fires once; the call-time
    dispatcher then passes those values positionally.  ``self`` and ``cls`` are exempt: they are positional by nature.

    Args:
        target: Normalised target from :func:`_normalize_target` — may be a :class:`TargetMode` or a callable;
            used only in the warning message.
        source: The decorated callable; used only in the warning message.
        target_positional_only: The target's POSITIONAL_ONLY parameter names (empty for a :class:`TargetMode`).
        stream: Warning stream; when ``None`` the warning is suppressed.

    """
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
        return _resolve_legacy_target(target, emit=False)
    if isinstance(target, TargetMode):
        return target
    if isinstance(target, (staticmethod, classmethod)):
        return target.__func__  # audit sees the function, not the descriptor
    return target
