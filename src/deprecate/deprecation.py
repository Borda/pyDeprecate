"""Backward-compatibility shim — the deprecation engine moved to sibling modules.

Every public and internal name is re-exported here so existing ``from deprecate.deprecation import ...`` imports and
Sphinx/Griffe ``:func:`~deprecate.deprecation.X``` cross-references keep resolving unchanged. The real homes are now
:mod:`deprecate.routine` (packing + public decorators), :mod:`deprecate._dispatch` (target resolution + call-plan
engine), :mod:`deprecate.messaging` (warning templates + emitters), :mod:`deprecate._properties` (property descriptors),
and :mod:`deprecate.utils` (shared low-level helpers).

New code should import from those modules (or the top-level :mod:`deprecate` package).

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

from deprecate._dispatch import (
    _V1_BREAK_VERSION,
    POSITIONAL_ONLY,
    POSITIONAL_OR_KEYWORD,
    _build_call_plan,
    _check_cross_class_method_target,
    _cycle_detection,
    _detect_positional_only,
    _find_class_body_qualname,
    _get_positional_params,
    _invoke_async,
    _invoke_sync,
    _normalize_target,
    _precompute_target_facts,
    _prepare_target_call,
    _reject_bare_decorator,
    _reorder_kwargs_for_surplus,
    _resolve_source_call_shape,
    _resolve_stored_target,
    _resolve_target_call_shape,
    _split_positional_only_kwargs,
    _update_kwargs_with_args,
    _update_kwargs_with_defaults,
    _warn_stacking_misconfiguration,
)
from deprecate._properties import _DeprecatedProperty, _StrictProperty
from deprecate.messaging import (
    _DEFAULT_STACKLEVEL_TO_CALLER,
    _TEMPLATE_MGS_PROBE_ARGS,
    TEMPLATE_ARGUMENT_MAPPING,
    TEMPLATE_WARNING_ARGUMENTS,
    TEMPLATE_WARNING_CALLABLE,
    TEMPLATE_WARNING_NO_TARGET,
    _consume_warn_budget,
    _raise_warn,
    _raise_warn_arguments,
    _raise_warn_callable,
    _source_display_name,
    _validate_template_mgs,
    deprecation_warning,
)
from deprecate.routine import (
    _packing_class_source,
    _packing_descriptor,
    _PackingClassArgs,
    deprecated,
    deprecated_callable,
)
from deprecate.utils import _unwrap_descriptor_target

__all__ = [
    "POSITIONAL_ONLY",
    "POSITIONAL_OR_KEYWORD",
    "TEMPLATE_ARGUMENT_MAPPING",
    "TEMPLATE_WARNING_ARGUMENTS",
    "TEMPLATE_WARNING_CALLABLE",
    "TEMPLATE_WARNING_NO_TARGET",
    "_DEFAULT_STACKLEVEL_TO_CALLER",
    "_DeprecatedProperty",
    "_PackingClassArgs",
    "_StrictProperty",
    "_TEMPLATE_MGS_PROBE_ARGS",
    "_V1_BREAK_VERSION",
    "_build_call_plan",
    "_check_cross_class_method_target",
    "_consume_warn_budget",
    "_cycle_detection",
    "_detect_positional_only",
    "_find_class_body_qualname",
    "_get_positional_params",
    "_invoke_async",
    "_invoke_sync",
    "_normalize_target",
    "_packing_class_source",
    "_packing_descriptor",
    "_precompute_target_facts",
    "_prepare_target_call",
    "_raise_warn",
    "_raise_warn_arguments",
    "_raise_warn_callable",
    "_reject_bare_decorator",
    "_reorder_kwargs_for_surplus",
    "_resolve_source_call_shape",
    "_resolve_stored_target",
    "_resolve_target_call_shape",
    "_source_display_name",
    "_split_positional_only_kwargs",
    "_unwrap_descriptor_target",
    "_update_kwargs_with_args",
    "_update_kwargs_with_defaults",
    "_validate_template_mgs",
    "_warn_stacking_misconfiguration",
    "deprecated",
    "deprecated_callable",
    "deprecation_warning",
]
