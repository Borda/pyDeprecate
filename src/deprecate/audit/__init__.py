"""Audit tools for deprecation lifecycle management.

This module provides several complementary utilities for verifying the health of deprecated callables across a
codebase. All are designed to be called from pytest or a CI script against an imported package.

**Wrapper configuration** (:func:`~deprecate.audit.validate_deprecation_wrapper`,
:func:`~deprecate.audit.find_deprecation_wrappers`):
    Detect wrappers that have zero impact — invalid ``args_mapping`` keys, identity mappings, empty mappings, or a
    ``target`` pointing back to the same wrapper.

**Mapping compatibility** (:func:`~deprecate.audit.validate_mapping_compatibility`):
    Detect :func:`~deprecate.proxy.deprecated_class` wrappers whose ``args_mapping`` remaps to POSITIONAL_ONLY
    constructor params, silently degrading to ``setattr`` at call time.

**Expiry enforcement** (:func:`~deprecate.audit.validate_deprecation_expiry`):
    Detect wrappers whose ``remove_in`` version has been reached or passed, preventing zombie code from shipping past
    its scheduled removal deadline.

**Policy lint** (:func:`~deprecate.audit.validate_deprecation_policy`):
    Detect wrappers that were *scheduled* irresponsibly rather than merely left too long — a removal deadline
    that leaves callers too short a grace window or lands off a release boundary, or a warning that never names
    a replacement. Each rule reports under its own :class:`~deprecate.audit.PolicyRule` slug and can be disabled
    independently.

**Chain detection** (:func:`~deprecate.audit.validate_deprecation_chains`):
    Detect wrappers whose ``target`` is itself a deprecated callable, forming a chain that users traverse
    unnecessarily. Two chain kinds are reported via :class:`~deprecate.audit.ChainType`: ``TARGET`` (forwarding chain)
    and ``STACKED`` (composed argument or attribute mappings).

**Report generation** (:func:`~deprecate.audit.generate_deprecation_table`):
    Generate a docs-friendly markdown summary from wrapper metadata.

The package is split by concern — ``_wrappers`` (single-wrapper inspection), ``_scan`` (discovery and the
chain/mapping filters), ``_lifecycle`` (version arithmetic and expiry), ``_policy`` (governance rules), ``_report``
(markdown output) — and this module re-exports the public surface, so ``deprecate.audit.<name>`` is the only path to
import from.

Results are returned as :class:`~deprecate.audit.DeprecationWrapperInfo` dataclasses, which carry both
identification info and structured validation results for programmatic processing.

!!! note
    :func:`~deprecate.audit.validate_deprecation_expiry` requires the ``packaging`` library for PEP 440 version
    comparison. :func:`~deprecate.audit.validate_deprecation_policy` requires it only for its version-dependent
    ``min_grace`` rule — ``message_required`` runs without it.
    Install with: ``pip install pyDeprecate[audit]``

Copyright (C) 2020-2026 Jiri Borovec <6035284+Borda@users.noreply.github.com>

"""

from typing import Any, Callable, Union

from deprecate.audit._lifecycle import DeprecationStatus, validate_deprecation_expiry
from deprecate.audit._policy import GraceWindow, GraceWindowSpec, PolicyRule, VersionBump, validate_deprecation_policy
from deprecate.audit._report import TableStyle, generate_deprecation_table
from deprecate.audit._scan import find_deprecation_wrappers, validate_deprecation_chains, validate_mapping_compatibility
from deprecate.audit._wrappers import ChainType, DeprecationWrapperInfo, validate_deprecation_wrapper
from deprecate.proxy import deprecated_class

__all__ = [
    "ChainType",
    "DeprecatedCallableInfo",
    "DeprecationStatus",
    "DeprecationWrapperInfo",
    "GraceWindow",
    "GraceWindowSpec",
    "PolicyRule",
    "TableStyle",
    "VersionBump",
    "find_deprecated_callables",
    "find_deprecation_wrappers",
    "generate_deprecation_table",
    "validate_deprecated_callable",
    "validate_deprecation_chains",
    "validate_deprecation_expiry",
    "validate_deprecation_policy",
    "validate_deprecation_wrapper",
    "validate_mapping_compatibility",
]

# ---------------------------------------------------------------------------
# Backward-compatibility shims — deprecated since 0.6, removed in 1.0
# ---------------------------------------------------------------------------

# Import delayed to avoid a module-level circular import cycle:
# this package is imported by ``deprecate/__init__.py`` before ``deprecate.deprecation`` is available.
from deprecate.deprecation import deprecated


@deprecated(target=validate_deprecation_wrapper, deprecated_in="0.6", remove_in="1.0")
def validate_deprecated_callable(func: Callable) -> DeprecationWrapperInfo:
    """Use :func:`~deprecate.audit.validate_deprecation_wrapper` instead."""
    return validate_deprecation_wrapper(func)


@deprecated(target=find_deprecation_wrappers, deprecated_in="0.6", remove_in="1.0")
def find_deprecated_callables(
    module: Union[Any, str],  # noqa: ANN401
    recursive: bool = True,
) -> list[DeprecationWrapperInfo]:
    """Use :func:`~deprecate.audit.find_deprecation_wrappers` instead."""
    return find_deprecation_wrappers(module, recursive)


@deprecated_class(target=DeprecationWrapperInfo, deprecated_in="0.6", remove_in="1.0")
class DeprecatedCallableInfo:
    """Deprecated name for :class:`~deprecate.audit.DeprecationWrapperInfo`, use that instead."""
