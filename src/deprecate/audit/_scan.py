"""Discovery: walk a module or package and collect every deprecated wrapper into :class:`DeprecationWrapperInfo`.

:func:`~deprecate.audit.find_deprecation_wrappers` is the entry point every other audit function scans through.

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

import fnmatch
import importlib
import inspect
import pkgutil
import warnings
from collections.abc import Iterator, Sequence
from contextlib import suppress
from dataclasses import replace
from functools import cached_property
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:
    pass

from deprecate._properties import _DeprecatedProperty
from deprecate._types import _has_deprecation_meta, get_deprecation_config
from deprecate.audit._wrappers import (
    DeprecationWrapperInfo,
    _classify_wrapper_api_type,
    _scan_module_meta,
    validate_deprecation_wrapper,
)
from deprecate.proxy import _DeprecatedProxy


# Note: Proxy objects are discoverable via the generic ``callable(obj)`` +
# ``_has_deprecation_meta(obj)`` scan in :func:`find_deprecation_wrappers` and
# :func:`~deprecate.audit.validate_deprecation_expiry`. The ``__deprecation_config__`` schema is unified
# across ``@deprecated`` and :class:`~deprecate.proxy._DeprecatedProxy` via
# :class:`~deprecate._types.DeprecationConfig` — both always populate the ``name`` field,
# so ``validate_deprecation_wrapper`` can read it correctly for proxy objects too.
def _getmembers_static_compat(obj: Any) -> list[tuple[str, Any]]:  # noqa: ANN401
    """Return members without triggering dynamic ``getattr`` side effects.

    Uses ``inspect.getmembers_static`` when available (Python 3.11+). For Python
    3.9/3.10 compatibility, falls back to ``dir()`` + ``inspect.getattr_static``.

    """
    getmembers_static = getattr(inspect, "getmembers_static", None)
    if callable(getmembers_static):
        return getmembers_static(obj)

    names = dir(obj)
    members: list[tuple[str, Any]] = []
    for name in names:
        with suppress(AttributeError):
            members.append((name, inspect.getattr_static(obj, name)))
    return sorted(members, key=lambda item: item[0])


def _scan_callable(
    obj: Any,  # noqa: ANN401
    module_name: str,
    qualified_name: str,
    *,
    member_name: Optional[str] = None,
    descriptor_kind: Optional[str] = None,
) -> Optional[DeprecationWrapperInfo]:
    """Emit a result if ``obj`` carries ``__deprecation_config__`` metadata."""
    if _has_deprecation_meta(obj):
        info = validate_deprecation_wrapper(obj)
        api_type = _classify_wrapper_api_type(obj, info, member_name=member_name, descriptor_kind=descriptor_kind)
        return replace(info, module=module_name, function=qualified_name, api_type=api_type)
    return None


def _descriptor_underlying_callables(obj: Any) -> tuple[Any, ...]:  # noqa: ANN401
    """Return the underlying callable(s) from a descriptor, or a 1-tuple of *obj* for plain callables.

    Centralises the descriptor-unwrapping logic shared by :func:`_member_has_deprecation_meta` and
    :func:`_scan_class` so each descriptor type is handled in exactly one place.

    Args:
        obj: A raw class member — may be a ``classmethod``, ``staticmethod``, ``property``,
            ``cached_property``, or a plain callable.

    Returns:
        A tuple of the underlying callable objects.  Empty when ``obj`` is a ``cached_property``
        with no ``func`` attribute (degenerate case).

    Examples:
        >>> import warnings
        >>> _descriptor_underlying_callables(classmethod(warnings.warn))  # doctest: +ELLIPSIS
        (<built-in function warn>,)
        >>> _descriptor_underlying_callables(staticmethod(warnings.warn))
        (<built-in function warn>,)

    """
    if isinstance(obj, (classmethod, staticmethod)):
        return (obj.__func__,)
    if isinstance(obj, property):
        return tuple(a for a in (obj.fget, obj.fset, obj.fdel) if a is not None)
    if isinstance(obj, cached_property):
        func = getattr(obj, "func", None)
        return (func,) if func is not None else ()
    return (obj,)


def _member_has_deprecation_meta(obj: Any) -> bool:  # noqa: ANN401
    """Return whether a class member carries deprecation metadata through its descriptor.

    Descriptors store the deprecation metadata on the underlying callable (``classmethod``/``staticmethod``
    ``__func__``, ``property`` accessors, ``cached_property`` ``func``), so a plain :func:`_has_deprecation_meta`
    on the raw member would miss deprecated descriptors.

    Args:
        obj: The raw class member (possibly a descriptor) to inspect.

    """
    return any(_has_deprecation_meta(c) for c in _descriptor_underlying_callables(obj))


def _scan_class(cls: Any, module_name: str, cls_name: str) -> list[DeprecationWrapperInfo]:  # noqa: ANN401
    """Scan class members, peeking through descriptors."""
    results: list[DeprecationWrapperInfo] = []
    try:
        members = _getmembers_static_compat(cls)
    except (AttributeError, TypeError):
        return results
    for attr_name, obj in members:
        # Skip private/dunder members that are *not* themselves deprecated. Deprecated private or dunder
        # members (e.g. a deprecated ``_legacy`` method or ``__eq__``) still carry ``__deprecation_config__``
        # and must be surfaced so they can expire — only ``__init__`` is exempt.
        if attr_name.startswith("_") and attr_name != "__init__" and not _member_has_deprecation_meta(obj):
            continue
        qualified = f"{cls_name}.{attr_name}"
        result: Optional[DeprecationWrapperInfo] = None
        if isinstance(obj, (classmethod, staticmethod)):
            kind = "classmethod" if isinstance(obj, classmethod) else "staticmethod"
            result = _scan_callable(obj.__func__, module_name, qualified, member_name=attr_name, descriptor_kind=kind)
        elif isinstance(obj, property):
            _prop_accessor = next(
                (c for c in _descriptor_underlying_callables(obj) if _has_deprecation_meta(c)),
                None,
            )
            if _prop_accessor is not None:
                result = _scan_callable(_prop_accessor, module_name, qualified, member_name=attr_name)
                # Inner-order ``@property @deprecated``: a *plain* ``property`` (not ``_DeprecatedProperty``)
                # whose ``fget`` is deprecation-wrapped. Only ``fget`` warns; any setter/deleter rebound
                # afterwards is built from the plain ``property`` base and stays silent. Flag every such
                # wrapper (getter-only included) since the canonical form is outer ``@deprecated(...) @property``.
                if (
                    result is not None
                    and not isinstance(obj, _DeprecatedProperty)
                    and obj.fget is not None
                    and _has_deprecation_meta(obj.fget)
                ):
                    result = replace(result, inner_order_property=True)
        elif isinstance(obj, cached_property):
            result = _scan_callable(obj.func, module_name, qualified, member_name=attr_name)
        else:
            result = _scan_callable(obj, module_name, qualified, member_name=attr_name)
        if result is not None:
            results.append(result)
    return results


def _reexport_module(obj: Any) -> Optional[str]:  # noqa: ANN401
    """Return the defining module of a non-proxy deprecated wrapper, for re-export attribution.

    ``functools.wraps`` preserves ``__module__`` on ``@deprecated`` functions, so a wrapper re-exported through another
    package's ``__init__`` still reports the module it was defined in and can be attributed there rather than counted
    once per importing module.

    Proxies (:class:`~deprecate.proxy._DeprecatedProxy`) have no reliable defining module — normal lookup returns the
    proxy class's own ``__module__`` and the wrapped object commonly lives in a different module than where the proxy
    was created — so this returns ``None`` for them and the caller falls back to id-based dedup.

    """
    if isinstance(obj, _DeprecatedProxy):
        return None
    return getattr(obj, "__module__", None)


def _same_top_package(mod_a: str, mod_b: str) -> bool:
    """Return True when ``mod_a`` and ``mod_b`` share the same top-level package."""
    return mod_a.split(".")[0] == mod_b.split(".")[0]


def _should_skip_reexported_wrapper(
    obj: Any,  # noqa: ANN401
    mod_name: str,
    attribute_to_defining_module: bool,
) -> bool:
    """Return True when a re-exported wrapper should be attributed elsewhere."""
    if not attribute_to_defining_module:
        return False
    defining_module = _reexport_module(obj)
    return defining_module is not None and defining_module != mod_name and _same_top_package(defining_module, mod_name)


def _scan_module_member(
    obj: Any,  # noqa: ANN401
    *,
    mod_name: str,
    name: str,
    include_members: bool,
    attribute_to_defining_module: bool,
    seen: set[int],
) -> list[DeprecationWrapperInfo]:
    """Scan one module member for deprecated wrappers or nested class members."""
    if name.startswith("_") or inspect.ismodule(obj):
        return []
    if _has_deprecation_meta(obj):
        if _should_skip_reexported_wrapper(obj, mod_name, attribute_to_defining_module):
            return []
        if id(obj) in seen:
            return []
        seen.add(id(obj))
        result = _scan_callable(obj, mod_name, name)
        return [result] if result is not None else []
    if include_members and inspect.isclass(obj) and getattr(obj, "__module__", None) == mod_name:
        return _scan_class(obj, mod_name, name)
    return []


def _scan_module(
    mod: Any,  # noqa: ANN401
    *,
    include_members: bool,
    seen: set[int],
    attribute_to_defining_module: bool = True,
) -> list[DeprecationWrapperInfo]:
    """Scan a single module for deprecated functions and class members.

    ``seen`` accumulates ``id()`` of every wrapper already recorded across the whole :func:`find_deprecation_wrappers`
    run so a re-exported wrapper (the same object bound in more than one module) is reported once, not once per
    importing module.

    When ``attribute_to_defining_module`` is True (the default), wrappers whose ``__module__`` differs from ``mod_name``
    but belongs to the same top-level package are skipped — they will be counted when the recursive walk reaches their
    defining submodule.  Pass False when the defining submodule will not be visited (package scanned with
    ``recursive=False``) so that re-exports visible on the public surface are not silently dropped.

    """
    results: list[DeprecationWrapperInfo] = []

    # Pre-loop: if the module itself is deprecated (via deprecated_module()), record it first.
    # Use __dict__.get to avoid triggering foreign PEP 562 __getattr__ hooks on third-party modules.
    if get_deprecation_config(mod) is not None:
        results.append(_scan_module_meta(mod))

    try:
        members = _getmembers_static_compat(mod)
    except (AttributeError, TypeError, ImportError):
        return results

    mod_name = mod.__name__ if hasattr(mod, "__name__") else str(mod)
    for name, obj in members:
        results.extend(
            _scan_module_member(
                obj,
                mod_name=mod_name,
                name=name,
                include_members=include_members,
                attribute_to_defining_module=attribute_to_defining_module,
                seen=seen,
            )
        )
    return results


def _is_excluded(modname: str, exclude: Sequence[str]) -> bool:
    """Return True if *modname* or any package above it matches one of the *exclude* glob patterns.

    Patterns are :func:`fnmatch.fnmatchcase` globs over the full dotted module name; a pattern that matches a
    package excludes its whole subtree, so ``"pkg.tests"`` covers ``pkg.tests.fixtures`` too.

    Examples:
        >>> _is_excluded("pkg.tests.fixtures", ["pkg.tests"])
        True
        >>> _is_excluded("pkg.legacy_api", ["*.legacy*"])
        True
        >>> _is_excluded("pkg.core", ["pkg.tests", "*._*"])
        False

    """
    parts = modname.split(".")
    prefixes = [".".join(parts[:end]) for end in range(1, len(parts) + 1)]
    return any(fnmatch.fnmatchcase(prefix, pattern) for prefix in prefixes for pattern in exclude)


def _walk_submodules(package: Any, exclude: Sequence[str]) -> Iterator[Any]:  # noqa: ANN401
    """Import and yield every submodule of *package* depth-first, never entering an excluded subtree.

    Replaces :func:`pkgutil.walk_packages`, which imports a package before it can be told to skip it; here the walk
    neither imports an excluded package nor descends into it (the package's own imports may still load it). Submodule
    top-level code can raise anything at import time (``RuntimeError``, ``OSError``, ``KeyError`` from env lookups,
    ...), not just ``ImportError``. One broken submodule must not abort the whole scan — and with it every audit gate
    built on top — so the failure is kept as a warning and the walk continues with the remaining submodules.

    """
    for _finder, modname, ispkg in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
        if _is_excluded(modname, exclude):
            continue
        try:
            submod = importlib.import_module(modname)
        except Exception as exc:
            warnings.warn(f"audit: skipped {modname}: {exc!r}", stacklevel=3)
            continue
        yield submod
        if ispkg and hasattr(submod, "__path__"):
            yield from _walk_submodules(submod, exclude)


def find_deprecation_wrappers(
    module: Union[Any, str],  # noqa: ANN401
    recursive: bool = True,
    include_members: bool = True,
    *,
    exclude: Optional[Sequence[str]] = None,
) -> list[DeprecationWrapperInfo]:
    """Scan a module or package for deprecated wrappers and validate them.

    This is a development/CI tool to scan a codebase for all wrappers created with :func:`~deprecate.deprecated`,
    :func:`~deprecate.deprecated_class`, :func:`~deprecate.deprecated_instance`, or
    :func:`~deprecate.module.deprecated_module` (module-level deprecation) and validate that each wrapper
    configuration is meaningful.
    Returns comprehensive information about each deprecated wrapper including validation results that help identify
    misconfigured wrappers.

    Args:
        module: A Python module or package to scan for deprecated wrappers. Can be:
            - Imported module object (e.g., ``import my_package; find_deprecation_wrappers(my_package)``)
            - String module path (e.g., ``find_deprecation_wrappers("my_package.submodule")``)
        recursive: If True (default), recursively scan submodules. If False, only scan the top-level module.
        include_members: If True, also scan deprecated methods and constructors defined on classes.
        exclude: Glob patterns (:func:`fnmatch.fnmatchcase`) over full dotted module names, e.g.
            ``["my_package.tests", "*._legacy*"]``. The walk itself never imports a matching submodule nor
            descends into a matching package (another module importing it still loads it), and no wrapper whose
            reported ``module`` matches is included (on a
            ``recursive=False`` package scan a re-export is attributed to the package itself, so it stays).
            ``None`` (default) excludes nothing.

    Returns:
        List of :class:`~deprecate.audit.DeprecationWrapperInfo` dataclasses, one per deprecated wrapper found.
        Each contains:
            - module: Module name where the wrapper is defined
            - function: Wrapper name
            - deprecated_info: DeprecationConfig metadata from the decorator (``__deprecation_config__`` attribute)
            - invalid_args: List of args_mapping keys not in wrapper signature
            - empty_args_mapping: True if args_mapping is None or empty
            - identity_args_mapping: List of identity mappings (key == value)
            - self_reference: True if target points to same wrapper
            - no_effect: True if wrapper has zero impact

    Example:
        >>> from deprecate import find_deprecation_wrappers
        >>> from tests import collection_deprecate as my_package
        >>>
        >>> results = find_deprecation_wrappers(my_package)
        >>> print(len(results) > 0)  # Should find deprecated wrappers
        True
        >>> # Also works with string module paths
        >>> results = find_deprecation_wrappers("tests.collection_deprecate")
        >>> print(len(results) > 0)
        True

        >>> # Filter to find only problematic wrappers
        >>> problematic = [r for r in results if r.invalid_args or r.no_effect]
        >>> print(len(results) > 0)  # May or may not have problematic ones
        True

    Note:
        - Requires that the module be importable
        - Recursive scans import every submodule of the package, executing their module-level code; packages
          with heavy import-time work (GPU init, network access) make the scan correspondingly expensive
        - Skips submodules that fail to import — any exception raised by module-level code is reported as a
          ``UserWarning`` (``audit: skipped <module>: ...``) and the scan continues
        - Inspects the ``__deprecation_config__`` attribute set by the :func:`~deprecate.deprecated` decorator
        - Skips private/magic attributes and imports from other modules
        - Uses static member inspection to avoid scan-time side effects from dynamic attribute access

    """
    results: list[DeprecationWrapperInfo] = []
    # Track object identity across the whole scan so a wrapper re-exported through several modules
    # (the standard packaging pattern) is recorded once, not once per importing module.
    seen: set[int] = set()

    # Handle string module path
    if isinstance(module, str):
        module = importlib.import_module(module)

    # Disable the attribution filter only when the top module is a package AND recursive=False.
    # In that case the submodules that own the re-exported wrappers will never be visited, so
    # skipping re-exports would silently drop them.  For single-file modules or recursive package
    # scans the old behaviour (attribute re-exports to their defining submodule) is preserved.
    _is_package = hasattr(module, "__path__")
    results.extend(
        _scan_module(
            module,
            include_members=include_members,
            seen=seen,
            attribute_to_defining_module=not (_is_package and not recursive),
        )
    )

    patterns = list(exclude or ())
    if recursive and _is_package:
        try:
            for submod in _walk_submodules(module, patterns):
                results.extend(_scan_module(submod, include_members=include_members, seen=seen))
        except (OSError, ImportError) as exc:
            # ``_walk_submodules`` already warns per broken submodule import; this catches the walk itself failing
            # (unreadable package dir, a member scan triggering an import) — keep what was found, say why it stopped.
            warnings.warn(f"audit: submodule walk of {module.__name__} stopped early: {exc!r}", stacklevel=2)

    # Belt for the paths the walk does not cover — a ``deprecated_module`` entry, a per-file directory scan,
    # or the top module itself — judged by the module each wrapper is reported under.
    return [info for info in results if not _is_excluded(info.module, patterns)]


def validate_deprecation_chains(
    module: Union[Any, str],  # noqa: ANN401
    recursive: bool = True,
    *,
    exclude: Optional[Sequence[str]] = None,
) -> list[DeprecationWrapperInfo]:
    """Validate that deprecated functions don't form chains with other deprecated code.

    This is a developer utility that scans a module or package for deprecated functions that form chains in two ways:

    1. **TARGET chains**: The ``target`` argument points to another deprecated callable instead of the final
       non-deprecated implementation.
    2. **STACKED chains**: Multiple ``@deprecated(target=TargetMode.ARGS_REMAP, ...)`` decorators are stacked on the
       same function with argument mappings that should be collapsed, or a callable ``target`` is itself a
       self-deprecation (``target=TargetMode.ARGS_REMAP``) requiring mapping composition.

    Both types are wasteful: wrappers should point directly to the final (non-deprecated) implementation with
    composed argument mappings.

    Detection is based purely on decorator metadata (``__deprecation_config__`` attributes) — no source-code or AST
    inspection is performed.

    Args:
        module: A Python module or package to scan for deprecation chains. Can be:
            - Imported module object (e.g., ``import my_package; validate_deprecation_chains(my_package)``)
            - String module path (e.g., ``validate_deprecation_chains("my_package.submodule")``)
        recursive: If True (default), recursively scan submodules. If False, only scan the top-level module.
        exclude: Glob patterns over full dotted module names to leave out of the scan, as in
            :func:`~deprecate.audit.find_deprecation_wrappers` (e.g. ``["my_package.tests"]``); ``None`` excludes
            nothing.

    Returns:
        List of :class:`~deprecate.audit.DeprecationWrapperInfo` where ``chain_type`` is not ``None``, i.e. every
        deprecated wrapper that forms a chain (``ChainType.TARGET`` or ``ChainType.STACKED``).

    Example:
        >>> from deprecate import validate_deprecation_chains
        >>> import tests.collection_chains as test_module
        >>>
        >>> issues = validate_deprecation_chains(test_module, recursive=False)
        >>> len(issues) > 0  # Should find chains
        True

    Note:
        - Only flags callees using the :func:`~deprecate.deprecated` decorator
        - Uses :func:`~deprecate.audit.find_deprecation_wrappers` and inspects ``chain_type`` to detect chains

    """
    wrappers = find_deprecation_wrappers(module, recursive=recursive, exclude=exclude)
    return [info for info in wrappers if info.chain_type is not None]


def validate_mapping_compatibility(
    module: Union[Any, str],  # noqa: ANN401
    recursive: bool = True,
    *,
    exclude: Optional[Sequence[str]] = None,
) -> list[DeprecationWrapperInfo]:
    """Return wrappers whose ``args_mapping`` remaps deprecated names to POSITIONAL_ONLY constructor params.

    A non-empty ``args_mapping_positional_only`` on the returned ``DeprecationWrapperInfo`` means the
    proxy falls back to ``setattr`` at call time instead of forwarding the remapped kwarg.  Use this
    validator in CI to detect :func:`~deprecate.proxy.deprecated_class` configurations that silently
    degrade to attribute assignment and may not behave as expected on all target class types.

    Args:
        module: A Python module or package to scan.  Accepts an imported module object or a dotted
            module path string.
        recursive: When ``True`` (default) recursively scan submodules.
        exclude: Glob patterns over full dotted module names to leave out of the scan, as in
            :func:`~deprecate.audit.find_deprecation_wrappers` (e.g. ``["my_package.tests"]``); ``None`` excludes
            nothing.

    Returns:
        List of ``DeprecationWrapperInfo`` instances whose ``args_mapping_positional_only`` field is
        non-empty.  Returns an empty list when no incompatibilities are found.

    Examples:
        >>> from deprecate import validate_mapping_compatibility
        >>> import tests.collection_deprecate as col
        >>> results = validate_mapping_compatibility(col, recursive=False)
        >>> len(results) > 0  # DepPositionalOnly remaps to a POSITIONAL_ONLY param
        True
        >>> results[0].function
        'DepPositionalOnly'

    """
    return [
        info
        for info in find_deprecation_wrappers(module, recursive=recursive, exclude=exclude)
        if info.args_mapping_positional_only
    ]
