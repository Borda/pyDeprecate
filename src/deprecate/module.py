"""Module-level deprecation via ``__getattribute__`` interception.

Call :func:`deprecated_module` once at module level to mark an entire module deprecated. The function
changes the module's ``__class__`` to :class:`_DeprecatedModuleWrapper` so that every public attribute
access on the module emits a :class:`FutureWarning` — including real attributes already in ``__dict__``.
PEP 562 ``__getattr__`` only sees missing names, so the module subclass is required to catch existing
attributes too. It also attaches ``__deprecated__`` metadata so that
:func:`~deprecate.audit.find_deprecation_wrappers` can discover it like any other deprecated wrapper.

Three deprecation modes are supported:

* **Mode 1 — in-place warn**: the module stays at its original path; a :class:`FutureWarning` is emitted
  on every public attribute access (real or missing).
* **Mode 2 — redirect**: only missing public attribute access is forwarded to a replacement module; a
  :class:`FutureWarning` is emitted on every public attribute access.
* **Mode 3 — parent alias**: use :func:`~deprecate.proxy.deprecated_instance` on the parent package's
  ``__init__.py`` to expose the deprecated module name as an attribute.  No new API needed; documented
  as a usage pattern.

"""

import sys
import threading
import types
import warnings
from typing import Any, Callable, Optional

from deprecate._types import DeprecationConfig, TargetMode
from deprecate.messaging import _validate_message_template

#: Thread-local set of ``(module_name, attr_name)`` pairs currently being resolved through a redirect
#: ``target``. Guards against cyclic redirects (e.g. ``A`` redirects to ``B`` and ``B`` back to ``A``):
#: without it, a missing-attribute lookup recurses A -> B -> A -> ... until ``RecursionError``. The
#: decoration-time ``target is mod`` guard only rejects the trivial length-1 self-cycle; cycles of
#: length >= 2 can only be detected at resolution time because the second module may be deprecated
#: after the first.
_redirect_guard = threading.local()

#: Default warning template for a deprecated module (no target).
_TEMPLATE_MODULE_NO_TARGET = (
    "The `%(source_name)s` module was deprecated since v%(deprecated_in)s. It will be removed in v%(remove_in)s."
)

#: Default warning template for a deprecated module redirected to a replacement.
_TEMPLATE_MODULE_REDIRECT = (
    "The `%(source_name)s` module was deprecated since v%(deprecated_in)s"
    " in favor of `%(target_name)s`."
    " It will be removed in v%(remove_in)s."
)


def _build_module_warn_msg(
    module_name: str,
    deprecated_in: str,
    remove_in: str,
    target: Optional[types.ModuleType],
    message_template: Optional[str],
) -> str:
    target_name = getattr(target, "__name__", None) if target is not None else None
    # A caller-supplied ``message_template`` replaces the built-in notice (consistent with the other
    # factories); plain text renders verbatim, while ``%``-placeholders are substituted. Otherwise pick the
    # redirect or no-target built-in template.
    args = {
        "source_name": module_name,
        "deprecated_in": deprecated_in,
        "remove_in": remove_in,
        "target_name": target_name or "",
    }
    if message_template:
        return message_template % args
    if target is not None and target_name:
        return _TEMPLATE_MODULE_REDIRECT % args
    return _TEMPLATE_MODULE_NO_TARGET % args


def _config_identity(config: DeprecationConfig) -> tuple[Any, ...]:
    """Return the user-facing configuration fields that define a module deprecation's identity.

    Used by the idempotency guard in :func:`deprecated_module` to decide whether a repeat call
    requests the *same* deprecation (a safe silent no-op) or a *different* one (a reconfiguration
    that must be reported rather than silently dropped).  Only fields a caller controls are
    compared: the redirect ``target`` (or the :attr:`~deprecate._types.TargetMode.NOTIFY` sentinel),
    both version strings, the per-attribute mapping, and the fully-rendered warning message (which
    already folds in the caller's ``message_template`` argument).  The runtime ``stream`` callable is
    intentionally excluded — a differing ``stream`` alone does not constitute a configuration
    difference.

    Args:
        config: The :class:`~deprecate._types.DeprecationConfig` attached to a module by a prior call.

    Returns:
        A hashable tuple of the identity-defining fields, comparable with ``==``.

    """
    attrs_mapping = config.attrs_mapping
    # attrs_mapping is a plain dict (unhashable and order-sensitive); freeze to an order-independent
    # form so two configs with the same mapping compare equal regardless of key insertion order.
    # An empty dict normalizes to None (truthiness check, not `is not None`): an empty mapping is
    # semantically identical to no mapping, so `{}` and `None` must not read as a config difference.
    frozen_mapping = frozenset(attrs_mapping.items()) if attrs_mapping else None
    return (config.target, config.deprecated_in, config.remove_in, frozen_mapping, config.message_template)


def _emit_module_warning(config: DeprecationConfig, stream: Optional[Callable[..., Any]]) -> None:
    """Emit the module deprecation warning via ``stream`` or :func:`warnings.warn`."""
    warn_msg: str = config.message_template or ""
    if stream is not None:
        try:
            stream(warn_msg, stacklevel=3)
        except TypeError:
            # stream does not accept stacklevel (e.g. print, logging.warning).
            stream(warn_msg)
    else:
        warnings.warn(warn_msg, FutureWarning, stacklevel=3)


def _resolve_missing_attr(
    name: str,
    d: dict[str, Any],
    config: Optional[DeprecationConfig],
) -> Any:  # noqa: ANN401
    """Resolve a public attribute that is absent from the module's ``__dict__``.

    Resolution order:

    1. If ``name`` is in ``attrs_mapping``: apply the mapping (rename or raise ``AttributeError`` for ``None`` values).
    2. If a redirect ``target`` module is set: delegate via :func:`getattr` on the target.  When the target lacks
       the name, fall back to a preserved PEP 562 ``__getattr__`` (step 3) before raising ``AttributeError``.
    3. If a pre-existing PEP 562 ``__getattr__`` was preserved: delegate to it.
    4. Otherwise: raise ``AttributeError``.

    """
    attrs_mapping: Optional[dict[str, Optional[str]]] = config.attrs_mapping if config else None
    # target is types.ModuleType in redirect mode; a TargetMode sentinel in in-place mode.
    target: Optional[types.ModuleType] = (
        config.target if config and isinstance(config.target, types.ModuleType) else None
    )
    module_name: str = config.name if config else d.get("__name__", "?")

    if attrs_mapping is not None and name in attrs_mapping:
        return _resolve_mapped(name, attrs_mapping[name], target, d, module_name)

    existing_getattr: Optional[Callable[[str], Any]] = d.get("__deprecated_existing_getattr__")

    if target is not None:
        # Cyclic-redirect guard: track this (module, name) resolution on a thread-local set so a
        # redirect cycle (A -> B -> A) raises a clean AttributeError instead of recursing to
        # RecursionError with a warning emitted on every frame.
        active: Optional[set[tuple[str, str]]] = getattr(_redirect_guard, "active", None)
        if active is None:
            active = set()
            _redirect_guard.active = active
        key = (module_name, name)
        if key in active:
            raise AttributeError(f"module {module_name!r} has no attribute {name!r}") from None
        active.add(key)
        try:
            return getattr(target, name)
        except AttributeError:
            # Redirect target lacks the name: fall back to a preserved PEP 562 __getattr__
            # (bespoke module-level routing) before giving up, so both resolution paths run.
            if existing_getattr is not None:
                return existing_getattr(name)
            raise AttributeError(f"module {module_name!r} has no attribute {name!r}") from None
        finally:
            active.discard(key)

    if existing_getattr is not None:
        return existing_getattr(name)

    raise AttributeError(f"module {module_name!r} has no attribute {name!r}")


def _resolve_mapped(
    name: str,
    mapped: Optional[str],
    target: Optional[types.ModuleType],
    d: dict[str, Any],
    module_name: str,
) -> Any:  # noqa: ANN401
    """Resolve a name that appears in ``attrs_mapping``."""
    if mapped is None:
        raise AttributeError(f"module {module_name!r} has no attribute {name!r}")
    if target is not None:
        return getattr(target, mapped)
    # No redirect target: read the new name from this module's dict directly to avoid
    # re-triggering __getattribute__ and double-counting the warning.
    if mapped in d:
        return d[mapped]
    raise AttributeError(f"module {module_name!r} has no attribute {mapped!r}")


class _DeprecatedModuleWrapper(types.ModuleType):
    """Module subclass that emits a deprecation warning on every public attribute access.

    Installed via ``mod.__class__ = _DeprecatedModuleWrapper`` in :func:`deprecated_module` so that
    real attributes already in ``__dict__`` (functions, classes, constants) are also covered — Python's
    PEP 562 ``__getattr__`` fires only for names *missing* from ``__dict__``, so changing ``__class__``
    is the only way to intercept accesses to names that exist in the module namespace.
    """

    def __getattribute__(self, name: str) -> Any:  # noqa: ANN401
        d = object.__getattribute__(self, "__dict__")

        # Emit warning for every non-private attribute access (real or missing).
        if not name.startswith("_"):
            config = d.get("__deprecated__")
            if config is not None:
                _emit_module_warning(config, d.get("__deprecated_stream__"))

                # attrs_mapping takes precedence for listed names — BEFORE the __dict__ fast path
                # below. A rename/removal marker must win even when the old body still lives in
                # __dict__ (the normal transition state), otherwise `{"old": "new"}` would silently
                # return the stale local and `{"old": None}` would silently return the real attr
                # instead of raising. This block is deliberately gated inside `not name.startswith("_")`:
                # every dunder starts with `_`, so `__class__`/`__spec__`/etc. can never be diverted
                # here, and only names actually in the mapping are rerouted — unmapped real attrs
                # still fall through to the fast path below and return their real value.
                attrs_mapping = config.attrs_mapping
                if attrs_mapping is not None and name in attrs_mapping:
                    target = config.target if isinstance(config.target, types.ModuleType) else None
                    return _resolve_mapped(name, attrs_mapping[name], target, d, config.name)

        # Fast path: attribute present in __dict__ (real, unmapped attribute).
        if name in d:
            return d[name]

        # Private names absent from __dict__: delegate to the standard ModuleType resolution
        # (covers class-level attrs like __class__, __doc__, __name__ when not in __dict__).
        if name.startswith("_"):
            return super().__getattribute__(name)

        # Public attribute missing from __dict__: apply redirect / raise logic.
        # Warning already fired above; no second warning needed here.
        return _resolve_missing_attr(name, d, d.get("__deprecated__"))


def _resolve_module_name(name: Optional[str], caller_frame: types.FrameType) -> str:
    """Resolve the target module name, auto-detecting from the caller frame when ``name`` is omitted.

    Args:
        name: Explicit ``__name__`` of the module being deprecated, or ``None`` to auto-detect.
        caller_frame: The frame of the direct caller of :func:`deprecated_module` (its ``sys._getframe(1)``),
            passed in so detection is independent of this helper's own call depth.

    Returns:
        The resolved module name.

    Raises:
        TypeError: If ``name`` is omitted and the call is made from inside a function or class body
            (``f_locals`` differs from ``f_globals``) rather than at module top level.
        ValueError: If ``name`` is omitted and the caller frame's ``__name__`` cannot be determined.

    """
    if name is not None:
        return name
    # Auto-detection is only meaningful from a module's top level, where the caller frame's ``f_locals`` IS
    # its ``f_globals`` (both are the module namespace). Inside a function or class body the two are distinct
    # dicts, yet ``f_globals["__name__"]`` still names the ENCLOSING module — so a naive auto-detect from a
    # nested call would silently deprecate that whole module as a side effect. Reject it with a ``TypeError``
    # naming the fix; passing ``name`` explicitly bypasses this guard entirely.
    if caller_frame.f_locals is not caller_frame.f_globals:
        raise TypeError(
            "`deprecated_module()` was called without `name` from inside a function or class body."
            " Call it at module top level, or pass `name` explicitly."
        )
    caller_name: Optional[str] = caller_frame.f_globals.get("__name__")
    if caller_name is None:
        raise ValueError("`deprecated_module()` called without `name` and caller frame `__name__` not found.")
    return caller_name


def deprecated_module(
    name: Optional[str] = None,
    *,
    target: Optional[types.ModuleType] = None,
    attrs_mapping: Optional[dict[str, Optional[str]]] = None,
    deprecated_in: str = "",
    remove_in: str = "",
    stream: Optional[Callable[..., Any]] = None,
    message_template: Optional[str] = None,
) -> None:
    """Mark a module as deprecated by intercepting all public attribute accesses.

    Call this function once at module level (typically at the bottom of an ``old_module.py``). It changes
    the module's ``__class__`` to :class:`_DeprecatedModuleWrapper` so that every public attribute access
    emits a :class:`FutureWarning` — including real attributes already in ``__dict__``. It also attaches
    ``__deprecated__`` metadata to the module so that :func:`~deprecate.audit.find_deprecation_wrappers`
    can discover it.

    Note:
        This design uses ``__getattribute__`` rather than PEP 562 ``__getattr__`` so real
        ``__dict__`` attributes are covered too. With the default warnings path, every public access
        still runs the full warning machinery (stacklevel walk plus warning registry/filter checks).
        That overhead is intentional and not free: it is a documented tradeoff, not a bug. In tight
        loops, repeated reads of a deprecated-module constant can dwarf the underlying dictionary
        fetch by orders of magnitude. Cache the value locally instead of reading it in a hot loop.

    Args:
        name: The ``__name__`` of the module being deprecated.  When omitted (or ``None``), the caller's
            ``__name__`` is detected automatically via ``sys._getframe()``, so calling ``deprecated_module(
            deprecated_in="1.0", remove_in="2.0")`` from inside a module body works without explicitly passing
            ``__name__``.  Auto-detection reads the *direct* caller frame, so it must be called straight from the
            module body — not from a helper function (which would deprecate the helper's module) and not from a
            script run as ``__main__`` (which would deprecate ``"__main__"``).  Pass ``name`` explicitly to
            avoid both pitfalls.
        target: Optional replacement module.  When given, missing-attribute access is forwarded to this module (Mode 2).
        attrs_mapping: Optional per-attribute mapping ``{"old_name": "new_name"}`` or ``{"old_name": None}`` to
            raise :class:`AttributeError` for that attribute.  The mapping takes precedence for listed names even when
            the old name still exists in the module's ``__dict__`` (the normal transition state where old and new
            bodies coexist): a listed name is always resolved via the mapping, never returned as its stale local
            value.  When supplied alongside ``target``, listed names resolve via the mapping and all other names fall
            through to ``target``.
        deprecated_in: Version string when this module was deprecated (e.g. ``"1.0"``).  Defaults to ``""``;
            when omitted a decoration-time :class:`UserWarning` fires (the notice omits the version and expiry
            audits cannot gate the module), matching :func:`~deprecate.deprecated`.
        remove_in: Version string when this module will be removed (e.g. ``"2.0"``).  Defaults to ``""``.
        stream: Callable used to emit the warning instead of :func:`warnings.warn`.  Pass ``None`` (default) to use
            the standard :mod:`warnings` machinery.  Note: there is no warn budget — unlike ``@deprecated``'s
            ``num_warns``, a warning fires on *every* public attribute access.  The default warnings path is
            de-duplicated per call site by Python's ``__warningregistry__``, but a custom ``stream`` (e.g.
            ``logging.warning``) is invoked on every access; cap or throttle it on your side if a hot loop reads a
            deprecated-module attribute repeatedly.
        message_template: Optional custom warning message.  When supplied it *replaces* the built-in
            redirect/version notice entirely — it is not appended to it.  Plain text without any ``%`` renders
            verbatim; a literal ``%`` must be escaped as ``%%``.  The ``%``-style placeholders ``%(source_name)s``
            (the module name), ``%(deprecated_in)s``, ``%(remove_in)s``, and ``%(target_name)s`` (empty when no
            ``target``) are substituted.  A malformed conversion or an unknown placeholder raises
            :class:`ValueError` at decoration time, matching the other four factories exactly (this call goes
            through the same ``_validate_message_template`` validator).  ``None`` (default) keeps the built-in notice.

    Raises:
        ValueError: If the resolved module ``name`` is not found in :data:`sys.modules`; if ``name`` is omitted
            and the caller frame's ``__name__`` cannot be determined; if ``target`` points at the module being
            deprecated itself (a self-redirect would recurse indefinitely on every missing-attribute lookup); or if
            ``message_template`` contains a bare ``%``-conversion or an unknown ``%(name)s`` placeholder.
        TypeError: If ``name`` is omitted and the call is made from inside a function or class body
            rather than at module top level (auto-detection would otherwise deprecate the enclosing module);
            pass ``name`` explicitly to call from a non-module scope.  Also raised if the module's type
            declares ``__slots__`` (incompatible memory layout prevents ``__class__`` reassignment) — wrap in a
            plain :class:`types.ModuleType` first if needed.

    Examples:
        >>> import sys, types
        >>> _m = types.ModuleType("demo_old")
        >>> sys.modules["demo_old"] = _m
        >>> import deprecate
        >>> deprecate.deprecated_module("demo_old", deprecated_in="1.0", remove_in="2.0")
        >>> import warnings
        >>> with warnings.catch_warnings(record=True) as _w:
        ...     warnings.simplefilter("always")
        ...     _ = getattr(sys.modules["demo_old"], "any_attr", None)
        >>> print(len(_w) == 1 and issubclass(_w[0].category, FutureWarning))
        True
        >>> del sys.modules["demo_old"]

    """
    module_name = _resolve_module_name(name, sys._getframe(1))

    if module_name not in sys.modules:
        raise ValueError(f"`deprecated_module()` called with {module_name!r} which is not in `sys.modules`.")

    # Missing-version notice, mirroring `@deprecated`: a deprecation without a `deprecated_in` version
    # produces a vaguer notice and cannot be gated by expiry audits. Fired once here at call time (module
    # deprecation runs once at import) so the gap surfaces during development, not silently at runtime.
    if not deprecated_in:
        warnings.warn(
            f"`deprecated_module` on {module_name!r} has no `deprecated_in` set."
            " Deprecation notices and expiry audits will omit the `deprecated_in` version."
            " Pass `deprecated_in` for a meaningful deprecation notice.",
            UserWarning,
            stacklevel=2,
        )

    mod = sys.modules[module_name]

    # Reject a self-target redirect: forwarding the module to itself would make every missing
    # attribute lookup recurse through _resolve_missing_attr -> getattr(target, name) forever.
    if target is mod:
        raise ValueError(f"`deprecated_module()` called with `target` pointing at {module_name!r} itself.")

    # Validate the raw template at decoration time (module deprecation runs once at import), matching the
    # other factories: a bare `%`-conversion or unknown `%(name)s` key raises a clear ValueError here
    # instead of a cryptic TypeError/KeyError (or silent dict-dump corruption) on the first warn emit.
    _validate_message_template(message_template)

    warn_msg = _build_module_warn_msg(module_name, deprecated_in, remove_in, target, message_template)

    # Build the incoming config first so the idempotency guard can compare it against any config a
    # prior call already installed.
    new_config = DeprecationConfig(
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        name=module_name,
        target=target if target is not None else TargetMode.NOTIFY,
        message_template=warn_msg,
        attrs_mapping=attrs_mapping,
    )

    # Idempotency guard: a module already deprecated must not be silently re-wrapped (handles
    # importlib.reload and accidental double-calls). A repeat call with the *same* user-facing
    # configuration is a safe no-op; a repeat call with a *different* configuration (e.g. switching
    # from in-place warn to a redirect target, or changing versions/message_template/attrs_mapping) is a
    # reconfiguration that would otherwise vanish without trace — emit a UserWarning and keep the
    # original config rather than silently dropping the second call. The `stream` callable is
    # excluded from the comparison (see _config_identity).
    existing_config = vars(mod).get("__deprecated__")
    if isinstance(existing_config, DeprecationConfig):
        if _config_identity(existing_config) != _config_identity(new_config):
            warnings.warn(
                f"`deprecated_module`: module {module_name!r} is already deprecated with a different"
                " configuration; second call ignored. The original deprecation config is retained.",
                UserWarning,
                stacklevel=2,
            )
        return

    # Warn when the module has a custom __class__ (e.g. installed by a lazy-loader like
    # importlib.util.LazyLoader). Overwriting it silently would discard its behaviour.
    # Capture this *before* the reassignment below, while the original class is still installed.
    if type(mod) is not types.ModuleType:
        warnings.warn(
            f"`deprecated_module`: module {module_name!r} already has a custom `__class__`"
            f" ({type(mod).__name__!r}) — overwriting with `_DeprecatedModuleWrapper`;"
            " any behaviour from the prior subclass is lost.",
            UserWarning,
            stacklevel=2,
        )

    # Read any pre-existing PEP 562 __getattr__ hook so bespoke module-level routing survives.
    # Read from the dict only (no attribute set yet); it is attached below, after the class swap.
    existing_getattr = vars(mod).get("__getattr__")

    # Change __class__ FIRST so the whole install is atomic. This is the only step that can fail
    # (e.g. `TypeError: __class__ assignment` for a module type declaring __slots__ — see Raises).
    # Doing it before attaching any `__deprecated__`/`__deprecated_stream__`/`__deprecated_existing_getattr__`
    # metadata guarantees that a failure leaves the module completely unmodified: no half-deprecated
    # state, and — critically — no stale `__deprecated__` for the idempotency guard to mistake for a
    # completed install and silently short-circuit a retry on.
    # __class__ reassignment is valid when the new class is a subclass with the same memory layout;
    # it enables __getattribute__ interception of ALL public attribute accesses, including real
    # attributes already in __dict__ that PEP 562 __getattr__ cannot reach.
    mod.__class__ = _DeprecatedModuleWrapper

    # Class swap succeeded: attach metadata. Underscore-prefixed names never trigger the wrapper's
    # warning (see _DeprecatedModuleWrapper.__getattribute__), so ordering here is warning-free and
    # static scanners (e.g. find_deprecation_wrappers) read __deprecated__ cleanly.
    vars(mod)["__deprecated_stream__"] = stream
    if existing_getattr is not None:
        warnings.warn(
            f"`deprecated_module`: pre-existing `__getattr__` found on {module_name!r} — chaining.",
            UserWarning,
            stacklevel=2,
        )
        vars(mod)["__deprecated_existing_getattr__"] = existing_getattr
    mod.__deprecated__ = new_config  # type: ignore[attr-defined]
