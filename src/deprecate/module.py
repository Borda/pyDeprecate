"""Module-level deprecation via ``__getattribute__`` interception.

Call :func:`deprecated_module` once at module level to mark an entire module deprecated.  The function
changes the module's ``__class__`` to :class:`_DeprecatedModuleWrapper` so that every public attribute
access on the module emits a :class:`FutureWarning` — including real attributes already in ``__dict__``
that PEP 562 ``__getattr__`` cannot reach.  It also attaches ``__deprecated__`` metadata so that
:func:`~deprecate.audit.find_deprecation_wrappers` can discover it like any other deprecated wrapper.

Three deprecation modes are supported:

* **Mode 1 — in-place warn**: the module stays at its original path; a :class:`FutureWarning` is emitted
  on every public attribute access (real or missing).
* **Mode 2 — redirect**: attribute access is forwarded to a replacement module; a :class:`FutureWarning`
  is emitted on every public attribute access.
* **Mode 3 — parent alias**: use :func:`~deprecate.proxy.deprecated_instance` on the parent package's
  ``__init__.py`` to expose the deprecated module name as an attribute.  No new API needed; documented
  as a usage pattern.

"""

import sys
import types
import warnings
from typing import Any, Callable, Optional

from deprecate._types import DeprecationConfig

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
    message: str,
) -> str:
    target_name = getattr(target, "__name__", None) if target is not None else None
    if target is not None and target_name:
        template = _TEMPLATE_MODULE_REDIRECT % {
            "source_name": module_name,
            "deprecated_in": deprecated_in,
            "remove_in": remove_in,
            "target_name": target_name,
        }
    else:
        template = _TEMPLATE_MODULE_NO_TARGET % {
            "source_name": module_name,
            "deprecated_in": deprecated_in,
            "remove_in": remove_in,
        }
    return f"{template} {message}" if message else template


def _emit_module_warning(config: DeprecationConfig, stream: Optional[Callable[..., Any]]) -> None:
    """Emit the module deprecation warning via ``stream`` or :func:`warnings.warn`."""
    warn_msg: str = config.template_mgs or ""
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
    """Resolve a public attribute that is absent from ``__dict__``; raise ``AttributeError`` when unresolvable."""
    attrs_mapping: Optional[dict[str, Optional[str]]] = config.attrs_mapping if config else None
    # target is types.ModuleType in redirect mode; a TargetMode sentinel in in-place mode.
    target: Optional[types.ModuleType] = (
        config.target if config and isinstance(config.target, types.ModuleType) else None
    )
    module_name: str = config.name if config else d.get("__name__", "?")

    if attrs_mapping is not None and name in attrs_mapping:
        return _resolve_mapped(name, attrs_mapping[name], target, d, module_name)

    if target is not None:
        try:
            return getattr(target, name)
        except AttributeError:
            raise AttributeError(f"module {module_name!r} has no attribute {name!r}") from None

    existing_getattr: Optional[Callable[[str], Any]] = d.get("__deprecated_existing_getattr__")
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

        # Fast path: attribute present in __dict__ (real attribute).
        if name in d:
            return d[name]

        # Private names absent from __dict__: delegate to the standard ModuleType resolution
        # (covers class-level attrs like __class__, __doc__, __name__ when not in __dict__).
        if name.startswith("_"):
            return super().__getattribute__(name)

        # Public attribute missing from __dict__: apply redirect / raise logic.
        # Warning already fired above; no second warning needed here.
        return _resolve_missing_attr(name, d, d.get("__deprecated__"))


def deprecated_module(
    module_name: str,
    *,
    target: Optional[types.ModuleType] = None,
    attrs_mapping: Optional[dict[str, Optional[str]]] = None,
    deprecated_in: str,
    remove_in: str,
    stream: Optional[Callable[..., Any]] = None,
    message: str = "",
) -> None:
    """Mark a module as deprecated by intercepting all public attribute accesses.

    Call this function once at module level (typically at the bottom of an ``old_module.py``).  It changes
    the module's ``__class__`` to :class:`_DeprecatedModuleWrapper` so that every public attribute access
    emits a :class:`FutureWarning` — including real attributes already in ``__dict__`` that PEP 562
    ``__getattr__`` cannot reach.  It also attaches ``__deprecated__`` metadata to the module so that
    :func:`~deprecate.audit.find_deprecation_wrappers` can discover it.

    Args:
        module_name: The ``__name__`` of the module being deprecated (pass ``__name__`` when calling at module level).
        target: Optional replacement module.  When given, missing-attribute access is forwarded to this module (Mode 2).
        attrs_mapping: Optional per-attribute mapping ``{"old_name": "new_name"}`` or ``{"old_name": None}`` to
            raise :class:`AttributeError` for that attribute.  When supplied alongside ``target``, the mapping takes
            precedence for listed names; all other names fall through to ``target``.
        deprecated_in: Version string when this module was deprecated (e.g. ``"1.0"``).
        remove_in: Version string when this module will be removed (e.g. ``"2.0"``).
        stream: Callable used to emit the warning instead of :func:`warnings.warn`.  Pass ``None`` (default) to use
            the standard :mod:`warnings` machinery.
        message: Optional extra text appended to the generated warning message.

    Raises:
        ValueError: If ``module_name`` is not found in :data:`sys.modules`.

    Examples:
        >>> import sys, types
        >>> _m = types.ModuleType("demo_old")
        >>> sys.modules["demo_old"] = _m
        >>> import deprecate
        >>> deprecate.deprecated_module("demo_old", deprecated_in="1.0", remove_in="2.0")
        >>> import warnings
        >>> with warnings.catch_warnings(record=True) as _w:
        ...     warnings.simplefilter("always")
        ...     _ = sys.modules["demo_old"].any_attr
        >>> print(len(_w) == 1 and issubclass(_w[0].category, FutureWarning))
        True
        >>> del sys.modules["demo_old"]

    """
    # Lazy import to avoid circular dependency (_types imports nothing from deprecate).
    from deprecate._types import TargetMode

    if module_name not in sys.modules:
        raise ValueError(f"`deprecated_module()` called with {module_name!r} which is not in `sys.modules`.")

    mod = sys.modules[module_name]

    # Idempotency guard: skip silently if already installed (handles importlib.reload and
    # accidental double-calls without installing a second wrapper).
    if isinstance(vars(mod).get("__deprecated__"), DeprecationConfig):
        return

    warn_msg = _build_module_warn_msg(module_name, deprecated_in, remove_in, target, message)

    # Attach audit metadata before changing __class__ so that static scanners
    # (e.g. find_deprecation_wrappers) can read it without triggering the warning.
    mod.__deprecated__ = DeprecationConfig(  # type: ignore[attr-defined]
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        name=module_name,
        target=target if target is not None else TargetMode.NOTIFY,
        template_mgs=warn_msg,
        attrs_mapping=attrs_mapping,
    )

    # Store stream separately (not part of DeprecationConfig).
    vars(mod)["__deprecated_stream__"] = stream

    # Chain any pre-existing PEP 562 __getattr__ hook so bespoke module-level routing survives.
    existing_getattr = vars(mod).get("__getattr__")
    if existing_getattr is not None:
        warnings.warn(
            f"`deprecated_module`: pre-existing `__getattr__` found on {module_name!r} — chaining.",
            UserWarning,
            stacklevel=2,
        )
        vars(mod)["__deprecated_existing_getattr__"] = existing_getattr

    # Change __class__ to enable __getattribute__ interception of ALL public attribute accesses,
    # including real attributes already in __dict__ that PEP 562 __getattr__ cannot reach.
    # __class__ reassignment is valid when the new class is a subclass with the same memory layout.
    mod.__class__ = _DeprecatedModuleWrapper
