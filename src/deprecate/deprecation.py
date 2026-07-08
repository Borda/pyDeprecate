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
import re
import sys
import warnings
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cached_property, partial, wraps
from inspect import Parameter
from typing import Any, Callable, Literal, Optional, Union, cast
from warnings import warn

from deprecate._types import (
    DeprecationConfig,
    TargetMode,
    _CallPlan,
    _DeprecatedCallable,
    _has_deprecation_meta,
    _HasDeprecationMeta,
    _WrapperState,
)
from deprecate.docstring.inject import _update_docstring_with_deprecation, normalize_docstring_style
from deprecate.utils import _apply_args_mapping_collisions, _get_signature, get_func_arguments_types_defaults

_V1_BREAK_VERSION = "v1.0"
# caller → wrapped_fn → _raise_warn_callable/_raise_warn_arguments → _raise_warn → warnings.warn
_DEFAULT_STACKLEVEL_TO_CALLER: int = 4
# ContextVar storing the active-wrapper id-set for the current async task or sync call stack.
# Each asyncio.Task inherits a snapshot of the parent context at creation time; because this
# ContextVar defaults to None and is only set() to a fresh set() inside the wrapper call, tasks
# spawned from user code (e.g. asyncio.gather) see None and create independent sets — no sharing.
# A synchronous recursive chain (same task/stack) shares one set — correct for cycle detection.
_cycle_detection: ContextVar[Optional[set[int]]] = ContextVar("_cycle_detection", default=None)

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
    # Reject bare ``%``-conversions (``%s``, ``%d``, a trailing ``%``) that are not part of a
    # ``%(name)s`` mapping key.  ``"...%s..." % {mapping}`` does not raise — it renders the whole
    # mapping dict into the message — so the probe below cannot catch them.  ``%%`` (escaped percent)
    # is legitimate and stripped before the search.
    if re.search(r"%(?!\()", template_mgs.replace("%%", "")):
        raise ValueError(
            f"Invalid template_mgs: bare `%`-conversion found in {template_mgs!r}; only mapping keys of the "
            f"form `%(name)s` are supported. Available placeholders: {list(_TEMPLATE_MGS_PROBE_ARGS)}"
        )
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


class _StrictProperty(property):
    """Strict ``property`` replacement that rejects inner-order ``@deprecated`` at class-body evaluation time.

    Import as ``from deprecate import property`` to opt a module into a guard against the accidental *inner order*
    ``@property`` over ``@deprecated`` (``@deprecated`` closer to ``def``). That order wraps only ``fget``; any
    setter or deleter added afterwards is built from the plain :class:`property` base and never warns, so writes
    and deletes silently bypass the deprecation notice. ``_StrictProperty`` raises :class:`TypeError` the moment it
    is handed a getter that already carries ``__deprecated__`` metadata — before any instance is created — steering
    authors to the canonical *outer order* ``@deprecated(...) @property``.

    Because it subclasses the builtin :class:`property`, every ``isinstance(obj, property)`` branch in the decorator
    and audit machinery treats it transparently: the outer ``@deprecated`` converts it to a
    :class:`_DeprecatedProperty` exactly as it would a builtin ``property``.

    Modules that do not import the strict ``property`` keep the builtin behaviour untouched — the strictness is
    purely opt-in.

    Example:
        >>> from deprecate import deprecated, property as strict_property
        >>> @deprecated(deprecated_in="1.0", remove_in="2.0")
        ... def old_getter(self):
        ...     '''Already-deprecated getter.'''
        ...     return 42
        >>> try:
        ...     strict_property(old_getter)  # inner-order detected
        ... except TypeError:
        ...     print("TypeError raised")
        TypeError raised

    """

    def __init__(
        self,
        fget: Optional[Callable] = None,
        fset: Optional[Callable] = None,
        fdel: Optional[Callable] = None,
        doc: Optional[str] = None,
    ) -> None:
        """Construct the property, rejecting an already-deprecated getter.

        Args:
            fget: Getter callable, or ``None``. A :class:`TypeError` is raised when it carries ``__deprecated__``
                metadata (the inner-order signature). The guard fires on ``fget`` only; ``fset`` and ``fdel``
                are accepted without inspection — the decorator-stacking inner-order bug is structurally a
                getter-ordering issue.
            fset: Setter callable, or ``None``.
            fdel: Deleter callable, or ``None``.
            doc: Property docstring; ``None`` defers to ``fget.__doc__``.

        Raises:
            TypeError: When ``fget`` is already ``@deprecated``-decorated (inner-order ``@property @deprecated``).

        """
        if fget is not None and _has_deprecation_meta(fget):
            name = getattr(fget, "__qualname__", repr(fget))
            raise TypeError(
                f"Inner-order `@property @deprecated` detected on `{name}`. Only `fget` will warn —"
                " setter and deleter remain silent."
                " This check is active because `property` in this module is `deprecate.deprecation._StrictProperty`"
                " (imported via `from deprecate import property`)."
                " Swap the decorator order to the canonical outer order:"
                " `@deprecated(deprecated_in=..., remove_in=...) @property`."
            )
        super().__init__(fget, fset, fdel, doc)


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


def _unwrap_descriptor_target(
    target: Union[bool, None, Callable, TargetMode, staticmethod, classmethod],
) -> Union[bool, None, Callable, TargetMode]:
    """Return ``target.__func__`` when *target* is a descriptor; pass through otherwise."""
    if isinstance(target, (staticmethod, classmethod)):
        return target.__func__
    return target


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
    except TypeError as _exc:
        if "stacklevel" in str(_exc) or "keyword" in str(_exc):
            stream(msg)
        else:
            raise


def _source_display_name(source: Callable) -> str:
    """Return display name: class name for ``__init__``, function name otherwise."""
    return source.__qualname__.split(".")[-2] if source.__name__ == "__init__" else source.__name__


def _raise_warn_callable(
    stream: Callable,
    source: Callable,
    target: Union[None, bool, Callable, TargetMode, staticmethod, classmethod],
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
    # Unwrap descriptor: _build_call_plan passes the raw (pre-normalization) target so
    # the warning can name the class rather than __init__.  For descriptor targets,
    # callable(staticmethod(fn)) is False on Python 3.9 and callable(classmethod(fn))
    # is always False, so without this unwrap the no-target template fires incorrectly.
    target = _unwrap_descriptor_target(target)
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
    arguments: Mapping[str, Optional[str]],
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
    args_map = ", ".join(TEMPLATE_ARGUMENT_MAPPING % {"old_arg": a, "new_arg": str(b)} for a, b in arguments.items())
    _raise_warn(
        stream,
        source,
        template_mgs or TEMPLATE_WARNING_ARGUMENTS,
        stacklevel=stacklevel,
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        argument_map=args_map,
    )


def _consume_warn_budget(
    state: _WrapperState,
    num_warns: int,
    reason_callable: bool,
    reason_argument: dict[str, Optional[str]],
) -> bool:
    """Check the warn budget and consume one unit from it when a warning may fire.

    Must be called while holding ``state.lock`` — the read-check-increment sequence is exactly
    what the lock protects (see the thread-safety note at the call site).

    Args:
        state: Mutable per-wrapper counters.
        num_warns: Configured budget; negative means unlimited.
        reason_callable: Warning is for the deprecated callable itself — consumes ``warned_calls``.
        reason_argument: Deprecated argument names present in this call — consumes per-argument
            budgets in ``warned_args``; takes precedence over the call counter for the check.

    Returns:
        True when the caller should emit the warning (budget available and now consumed).

    """
    if reason_argument:
        nb_warned = min((state.warned_args.get(arg, 0) for arg in reason_argument), default=0)
    else:
        nb_warned = state.warned_calls
    if num_warns >= 0 and nb_warned >= num_warns:
        return False
    if reason_callable:
        state.warned_calls += 1
    elif reason_argument:
        for arg in reason_argument:
            state.warned_args[arg] = state.warned_args.get(arg, 0) + 1
    return True


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
            ``deprecated_in``, ``remove_in``, and ``template_mgs`` are all read from this object.
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
            f" verify your `@deprecated(target=...)` arguments. Will be TypeError in {_V1_BREAK_VERSION}.",
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
                template_mgs=dep_cfg.template_mgs,
                stacklevel=_stacklevel_to_caller,
            )
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
        # with the same packing config (template_mgs, stream, deprecated_in, remove_in,
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


def deprecated(  # noqa: C901
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

    def packing(  # noqa: C901
        source: Union[Callable, classmethod, staticmethod, property, cached_property],
        _stacklevel: int = 2,
        _is_static: bool = False,
    ) -> Callable:
        _reject_bare_decorator(source)
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
            template_mgs=template_mgs,
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
