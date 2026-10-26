"""Resolve proxy targets and validate attribute mappings without importing the runtime proxy class.

The nominal proxy type is supplied by the runtime class's static compatibility methods, so stacked proxies retain exact
type checks while this module depends only on lower-level types and utilities. Target resolution, dataclass argument
expansion, and positional-only constructor diagnostics run during proxy construction.

"""

import inspect
import warnings
from typing import Any, Optional

from deprecate._frames import _caller_stacklevel
from deprecate._types import TargetMode, get_deprecation_config
from deprecate.utils import _is_dataclass_target


def _get_static_attr_owner(proxy_type: Any, obj: Any) -> Any:  # noqa: ANN401
    """Return the object whose attributes should be validated without dynamic lookup.

    For proxy targets, validate against the same "active" object attribute access will ultimately reach (callable
    target when configured, otherwise the wrapped source).

    Defensive cycle guard: stacked-proxy chains are linear in normal use (the frozen
    :class:`~deprecate._types.DeprecationConfig` prevents reassignment of ``target`` after construction),
    but the guard breaks the loop on the unlikely case of a circular chain so this helper never blocks.

    """
    seen: set[int] = set()
    while isinstance(obj, proxy_type):
        if id(obj) in seen:
            break
        seen.add(id(obj))
        dep = get_deprecation_config(obj)
        if dep is None:
            break
        cfg = object.__getattribute__(obj, "_DeprecatedProxy__config")
        target = dep.target
        obj = target if target is not None and not isinstance(target, TargetMode) else cfg.obj
    return obj


def _validate_attrs_mapping(
    proxy_type: Any,  # noqa: ANN401
    attrs_mapping: dict[str, Optional[str]],
    obj: Any,  # noqa: ANN401
    attr_check_obj: Any,  # noqa: ANN401
) -> None:
    """Validate ``attrs_mapping`` at decoration time.

    Runs three checks against the configured mapping:

    1. Reject true cycles in the redirect chain.
    2. Confirm every non-``None`` redirect target attribute exists on *attr_check_obj* so
       accessing a deprecated alias never raises :class:`AttributeError` on the first warning.
    3. Confirm every warn-only key (``None`` value) exists on at least one of *attr_check_obj*
       or *obj* — a key on neither would always raise :class:`AttributeError` on access.

    Args:
        proxy_type: Nominal runtime proxy class supplied by the static compatibility method.
        attrs_mapping: The redirect map from deprecated attribute names to canonical names
            (or ``None`` for warn-only).
        obj: The wrapped source object — used as a fallback for warn-only key existence.
        attr_check_obj: The class against which redirect targets are validated; the caller
            pre-computes this from a concrete callable target, falling back to *obj* for
            ``TargetMode`` values and legacy boolean sentinels.

    Raises:
        ValueError: If *attrs_mapping* contains a cycle, a missing redirect target, or a
            warn-only key absent from both classes.

    """
    # Reject true cycles in ``attrs_mapping`` at decoration time. Non-cyclic chains are allowed at runtime
    # and surfaced by audit as mapping chains.
    cycle_starters: list[str] = []
    for start in attrs_mapping:
        visited: set[str] = {start}
        current = attrs_mapping[start]
        while current is not None and current in attrs_mapping:
            if current in visited:
                cycle_starters.append(start)
                break
            visited.add(current)
            current = attrs_mapping[current]
    if cycle_starters:
        raise ValueError(
            f"`attrs_mapping` has circular redirects — the redirect chain starting from"
            f" {sorted(set(cycle_starters))} loops back to a previously visited key."
            " Redirect chains must terminate at an attribute name that does not loop."
        )
    proxy_type._validate_attrs_redirect_targets(attrs_mapping, attr_check_obj)
    proxy_type._validate_attrs_warn_only_keys(attrs_mapping, obj, attr_check_obj)


def _validate_attrs_redirect_targets(
    proxy_type: Any,  # noqa: ANN401
    attrs_mapping: dict[str, Optional[str]],
    attr_check_obj: Any,  # noqa: ANN401
) -> None:
    """Validate that every non-``None`` redirect target attribute exists on the class.

    Ensures that accessing a deprecated alias never raises :class:`AttributeError` on the
    first warning — every redirect target must resolve to an existing attribute on
    *attr_check_obj* at decoration time.

    Args:
        proxy_type: Nominal runtime proxy class supplied by the static compatibility method.
        attrs_mapping: The redirect map from deprecated attribute names to canonical names
            (or ``None`` for warn-only).
        attr_check_obj: The class against which non-``None`` redirect targets are validated.

    Raises:
        ValueError: If any non-``None`` redirect target is absent from *attr_check_obj*.

    """
    # For @dataclass targets, required fields (no default) are not visible via getattr_static
    # because they have no class-level value — but they DO exist as valid constructor params
    # and instance attributes.  Check __dataclass_fields__ as a fallback.
    _owner = proxy_type._get_static_attr_owner(attr_check_obj)
    _dc_field_names: set[str] = set(getattr(_owner, "__dataclass_fields__", {}))
    _resolved_targets = list(attrs_mapping.items())
    missing_targets = [
        f"{k!r} -> {target!r}"
        for k, target in _resolved_targets
        if target is not None
        and not proxy_type._has_static_attribute(attr_check_obj, target)
        and target not in _dc_field_names
    ]
    if missing_targets:
        raise ValueError(
            f"`attrs_mapping` redirect targets not found on the active class: {missing_targets}."
            " Each non-None value must be an existing attribute name on the target class when `target` is"
            " provided, or on the wrapped class otherwise."
        )


def _validate_attrs_warn_only_keys(
    proxy_type: Any,  # noqa: ANN401
    attrs_mapping: dict[str, Optional[str]],
    obj: Any,  # noqa: ANN401
    attr_check_obj: Any,  # noqa: ANN401
) -> None:
    """Validate that every warn-only key (``None``-value entry) exists on at least one class.

    A ``None`` redirect means "warn but still serve the attribute value" — the attribute can
    live on the target (same-name warning: kept in new API) or only on the source
    (being-removed attribute that falls back to the source value in ``__getattr__``). Raise
    only when absent from both classes; a key on neither would always raise
    :class:`AttributeError` on access.

    Args:
        proxy_type: Nominal runtime proxy class supplied by the static compatibility method.
        attrs_mapping: The redirect map from deprecated attribute names to canonical names
            (or ``None`` for warn-only).
        obj: The wrapped source object — used as a fallback for warn-only key existence.
        attr_check_obj: The class against which warn-only keys are first checked before
            falling back to *obj*.

    Raises:
        ValueError: If any warn-only key is absent from both *attr_check_obj* and *obj*.

    """
    missing_keys = [
        k
        for k, v in attrs_mapping.items()
        if v is None
        and not proxy_type._has_static_attribute(attr_check_obj, k)
        and not proxy_type._has_static_attribute(obj, k)
    ]
    if missing_keys:
        raise ValueError(
            f"`attrs_mapping` warn-only keys not found on either class: {missing_keys}."
            " Each key with a `None` value must be an existing attribute name on the target class"
            " when `target` is provided, or on the wrapped class."
        )


def _expand_dc_attrs_to_args(
    dc_check: Any,  # noqa: ANN401
    attrs_mapping: Optional[dict[str, Optional[str]]],
    args_mapping: Optional[dict[str, Optional[str]]],
) -> tuple[Optional[dict[str, Optional[str]]], list[str]]:
    """Auto-expand ``attrs_mapping`` entries into ``args_mapping`` for dataclass dual-surface deprecation.

    When the target (or wrapped object) is a ``@dataclass``, copies ``attrs_mapping`` entries whose redirect value names
    a dataclass ``__init__`` parameter into ``args_mapping`` so that both ``instance.old_field`` (attribute access) and
    ``DC(old_field=5)`` (constructor kwarg) emit a ``FutureWarning`` from a single ``deprecated_class()`` call.

    Returns ``(updated_args_mapping, auto_expanded_keys)``.  Returns the original ``args_mapping`` unchanged (possibly
    ``None``) when ``attrs_mapping`` is empty/``None`` or ``dc_check`` is not a dataclass.  Keys already present in
    ``args_mapping`` are never overwritten.

    """
    if not (attrs_mapping and _is_dataclass_target(dc_check)):
        return args_mapping, []
    auto_expanded: list[str] = []
    _init_param_names: set[str] = set(inspect.signature(dc_check).parameters)
    for _old_key, _mapping_val in attrs_mapping.items():
        if _mapping_val in _init_param_names:
            if args_mapping is None:
                args_mapping = {}
            if _old_key not in args_mapping:
                args_mapping[_old_key] = _mapping_val
                auto_expanded.append(_old_key)
    return args_mapping, auto_expanded


def _resolve_proxy_target(
    obj: Any,  # noqa: ANN401
    target: Any,  # noqa: ANN401
    args_mapping: Optional[dict[str, Optional[str]]],
    attrs_mapping: Optional[dict[str, Optional[str]]],
    dc_check: Any,  # noqa: ANN401
) -> "tuple[Any, Optional[dict[str, Optional[str]]], list[str], bool]":
    """Resolve the unset factory default (``None``) against the configured mappings.

    Only ``None`` — the ``deprecated_class`` default, meaning "no explicit target" — auto-resolves: a mapping present
    selects :attr:`~deprecate._types.TargetMode.ARGS_REMAP` / :attr:`~deprecate._types.TargetMode.ATTRS_REMAP` (or the
    wrapped object itself when dataclass auto-expand activated both surfaces); no mapping keeps ``None`` (a proxy that
    warns on access).  An **explicit** ``TargetMode.NOTIFY`` is a deliberate caller choice and is never overridden —
    with ``attrs_mapping`` present the pair is contradictory, so dataclass auto-expand is skipped and the flag in the
    returned tuple tells the proxy to ignore the mapping at runtime (the validators emit the misconfig ``UserWarning``).

    Returns ``(resolved_target, args_mapping, auto_expanded_keys, explicit_notify_attrs)``.

    """
    explicit_notify_attrs = target is TargetMode.NOTIFY and bool(attrs_mapping)
    # Dataclass dual-surface auto-expand: copy attrs_mapping entries that name a dataclass field
    # into args_mapping so both attribute access and constructor kwarg paths emit a FutureWarning.
    # Skipped under an explicit-NOTIFY conflict — the ignored mapping must not grow ``args_mapping``.
    auto_expanded: list[str] = []
    if not explicit_notify_attrs:
        args_mapping, auto_expanded = _expand_dc_attrs_to_args(dc_check, attrs_mapping, args_mapping)
    # When auto-expand produced a non-empty args_mapping and the user provided no explicit target,
    # set target = obj so the callable-target + both-mappings path activates both surfaces.
    if auto_expanded and target is None:
        target = obj
    # Auto-resolve: no explicit target (``None``, the factory default) but a mapping provided → the
    # matching remap mode. An explicit ``TargetMode.NOTIFY`` never auto-resolves — it is flagged instead.
    if target is None and args_mapping:
        target = TargetMode.ARGS_REMAP
    if target is None and attrs_mapping:
        target = TargetMode.ATTRS_REMAP
    return target, args_mapping, auto_expanded, explicit_notify_attrs


def _detect_proxy_positional_only(
    dc_check: Any,  # noqa: ANN401
    name: str,
    args_mapping: Optional[dict[str, Optional[str]]],
    ctor_positional_only: frozenset[str],
) -> tuple[str, ...]:
    """Detect ``args_mapping`` keys that remap to POSITIONAL_ONLY constructor parameters.

    When ``deprecated_class(args_mapping={"old": "new"}, ...)`` targets a class whose constructor declares ``new`` as
    positional-only (``def __init__(self, new, /): ...``), a call with ``old=value`` cannot be forwarded as
    ``new=value``.  Emits a ``UserWarning`` at decoration time and returns the incompatible old-key names so they can
    be recorded in :class:`~deprecate._types.DeprecationConfig` for audit surfacing.  A ``None`` mapping value (drop)
    is never incompatible.  Returns an empty tuple when no incompatibilities are found.

    Args:
        dc_check: The resolved active object; only a class is checked.
        name: Display name of the deprecated object, used in the warning message.
        args_mapping: The ``args_mapping`` being validated.
        ctor_positional_only: POSITIONAL_ONLY parameter names of the constructor of ``dc_check``.

    """
    if not (args_mapping and isinstance(dc_check, type)):
        return ()
    incompatible = tuple(old_key for old_key, new_key in args_mapping.items() if new_key in ctor_positional_only)
    if incompatible:
        warnings.warn(
            f"`args_mapping` on `{name}` remaps {list(incompatible)!r} to POSITIONAL_ONLY "
            f"constructor parameter(s) of `{dc_check.__name__}`. "
            "Calls using those deprecated names will pass the remapped values positionally "
            "to the constructor — consider removing `/` from the target signature or using "
            "`attrs_mapping` instead.",
            UserWarning,
            stacklevel=_caller_stacklevel(),
        )
    return incompatible
