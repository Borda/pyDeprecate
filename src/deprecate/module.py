"""Module-level deprecation via PEP 562 ``__getattr__``.

Call :func:`deprecated_module` once at module level to mark an entire module deprecated.  The function installs a
PEP 562 ``__getattr__`` on the target module and attaches ``__deprecated__`` metadata so that
:func:`~deprecate.audit.find_deprecation_wrappers` can discover it like any other deprecated wrapper.

Three deprecation modes are supported:

* **Mode 1 — in-place warn**: the module stays at its original path; a :class:`FutureWarning` is emitted whenever
  an attribute that is *not* already in the module's ``__dict__`` is accessed (PEP 562 limitation — real attributes
  do not trigger ``__getattr__``).
* **Mode 2 — redirect**: attribute access is forwarded to a replacement module; a :class:`FutureWarning` is emitted
  on every access to attributes not present in the module's ``__dict__`` (PEP 562 limitation).
* **Mode 3 — parent alias**: use :func:`~deprecate.proxy.deprecated_instance` on the parent package's
  ``__init__.py`` to expose the deprecated module name as an attribute.  No new API needed; documented as a usage
  pattern.

"""

import sys
import types
import warnings
from typing import Any, Callable, Optional

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


def _make_module_getattr(
    module_name: str,
    mod: types.ModuleType,
    target: Optional[types.ModuleType],
    attrs_mapping: Optional[dict[str, Optional[str]]],
    stream: Optional[Callable[..., Any]],
    warn_msg: str,
    existing_getattr: Optional[Callable[..., Any]],
) -> Callable[[str], Any]:
    """Return the PEP 562 ``__getattr__`` closure for a deprecated module."""

    def _hook(name: str) -> Any:  # noqa: ANN401
        if stream is not None:
            try:
                stream(warn_msg, FutureWarning, stacklevel=2)
            except TypeError:
                stream(warn_msg, FutureWarning)
        else:
            warnings.warn(warn_msg, FutureWarning, stacklevel=2)

        if attrs_mapping is not None and name in attrs_mapping:
            mapped = attrs_mapping[name]
            if mapped is None:
                raise AttributeError(f"module {module_name!r} has no attribute {name!r}")
            return getattr(target if target is not None else mod, mapped)

        if target is not None:
            return getattr(target, name)

        if existing_getattr is not None:
            return existing_getattr(name)

        raise AttributeError(f"module {module_name!r} has no attribute {name!r}")

    return _hook


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
    """Mark a module as deprecated using PEP 562 ``__getattr__``.

    Call this function once at module level (typically at the bottom of an ``old_module.py``).  It attaches
    ``__deprecated__`` metadata to the module so that :func:`~deprecate.audit.find_deprecation_wrappers` can
    discover it, and installs a PEP 562 ``__getattr__`` that emits a :class:`FutureWarning` on attribute access.

    Args:
        module_name: The ``__name__`` of the module being deprecated (pass ``__name__`` when calling at module level).
        target: Optional replacement module.  When given, attribute access is forwarded to this module (Mode 2).
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
        ...     getattr(sys.modules["demo_old"], "missing_attr", None)
        >>> print(len(_w) == 1 and issubclass(_w[0].category, FutureWarning))
        True
        >>> del sys.modules["demo_old"]

    """
    # Lazy import to avoid circular dependency (_types imports nothing from deprecate).
    from deprecate._types import DeprecationConfig, TargetMode

    if module_name not in sys.modules:
        raise ValueError(f"`deprecated_module()` called with {module_name!r} which is not in `sys.modules`.")

    mod = sys.modules[module_name]

    # Idempotency guard: skip silently if already installed (handles importlib.reload and
    # accidental double-calls without chaining a second __getattr__ hook).
    if isinstance(vars(mod).get("__deprecated__"), DeprecationConfig):
        return

    warn_msg = _build_module_warn_msg(module_name, deprecated_in, remove_in, target, message)

    # Attach audit metadata before installing __getattr__ so that static scanners
    # (e.g. find_deprecation_wrappers) can read it without triggering the warning.
    mod.__deprecated__ = DeprecationConfig(  # type: ignore[attr-defined]
        deprecated_in=deprecated_in,
        remove_in=remove_in,
        name=module_name,
        target=target if target is not None else TargetMode.NOTIFY,
        template_mgs=warn_msg,
        attrs_mapping=attrs_mapping,
    )

    existing_getattr = vars(mod).get("__getattr__")
    if existing_getattr is not None:
        warnings.warn(
            f"`deprecated_module`: pre-existing `__getattr__` found on {module_name!r} — chaining.",
            UserWarning,
            stacklevel=2,
        )

    # Use vars() assignment to avoid mypy method-assign error on ModuleType.__getattr__.
    vars(mod)["__getattr__"] = _make_module_getattr(
        module_name=module_name,
        mod=mod,
        target=target,
        attrs_mapping=attrs_mapping,
        stream=stream,
        warn_msg=warn_msg,
        existing_getattr=existing_getattr,
    )
