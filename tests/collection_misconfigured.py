"""Collection of misconfigured deprecated functions for testing validation utilities.

These functions intentionally have invalid or ineffective deprecation configurations.
They simulate common mistakes developers make when setting up deprecation wrappers:

- Referencing arguments that don't exist in the function signature
- Providing an empty or None args_mapping (no-op deprecation)
- Mapping an argument to itself (identity mapping, no actual rename)
- Mixing identity and valid mappings
- Creating a self-referencing deprecation (wrapper targets itself)
- Using target=False (invalid sentinel)
- Using TargetMode.NOTIFY with args_mapping (ignored and emits a construction-time
  UserWarning at decoration time; TypeError planned in v1.0 — misconfigured)
- Using TargetMode.ARGS_REMAP without args_mapping (no-op — misconfigured)

Used by `validate_deprecated_wrapper()` and `find_deprecation_wrappers()` to verify
that the validation tooling correctly detects these misconfigurations.

Copyright (C) 2020-2026 Jiri Borovec <...>.

"""

from dataclasses import replace
from typing import cast

from deprecate import deprecated, void
from deprecate._types import TargetMode, _DeprecatedCallable

# Construction-time UserWarning is expected for empty/None args_mapping; it is suppressed via the
# targeted `filterwarnings` entries in pyproject.toml (scoped by message + `deprecate._types` module).


@deprecated(
    target=TargetMode.ARGS_REMAP,
    deprecated_in="0.1",
    remove_in="0.5",
    args_mapping={"nonexistent_arg": "new_arg"},
)
def invalid_args_deprecation(real_arg: int = 1) -> int:
    """Nonexistent argument name in args_mapping.

    Examples:
        Developer typos an arg name — `nonexistent_arg` is not a parameter
        of this function, so the mapping has no effect.

    """
    return real_arg


@deprecated(target=TargetMode.ARGS_REMAP, deprecated_in="0.1", remove_in="0.5", args_mapping={})
def empty_mapping_deprecation(arg1: int = 1, arg2: int = 2) -> int:
    """Empty args_mapping dict.

    Examples:
        Developer passes an empty `dict` — the decorator does nothing useful.

    """
    return arg1 + arg2


@deprecated(target=TargetMode.ARGS_REMAP, deprecated_in="0.1", remove_in="0.5", args_mapping=None)
def none_mapping_deprecation(arg1: int = 1, arg2: int = 2) -> int:
    """`None` passed as `args_mapping`.

    Examples:
        Developer passes `None` instead of a `dict` — equivalent to no mapping.

    """
    return arg1 + arg2


@deprecated(
    target=TargetMode.ARGS_REMAP,
    deprecated_in="0.1",
    remove_in="0.5",
    args_mapping={"arg1": "arg1"},
)
def identity_mapping_deprecation(arg1: int = 1, arg2: int = 2) -> int:
    """Single identity mapping (arg mapped to itself).

    Examples:
        Developer maps `arg1` -> `arg1` — no actual rename happens.

    """
    return arg1 + arg2


@deprecated(
    target=TargetMode.ARGS_REMAP,
    deprecated_in="0.1",
    remove_in="0.5",
    args_mapping={"arg1": "arg1", "arg2": "arg2"},
)
def all_identity_mapping_deprecation(arg1: int = 1, arg2: int = 2) -> int:
    """All arguments are identity mapped.

    Examples:
        Every arg is mapped to itself — the entire mapping is a no-op.

    """
    return arg1 + arg2


@deprecated(
    target=TargetMode.ARGS_REMAP,
    deprecated_in="0.1",
    remove_in="0.5",
    args_mapping={"arg1": "arg1", "arg2": "new_arg2"},
)
def partial_identity_mapping_deprecation(arg1: int = 1, arg2: int = 0, new_arg2: int = 2) -> int:
    """Mix of identity and valid mappings.

    Examples:
        `arg1` -> `arg1` is a no-op, but `arg2` -> `new_arg2` is valid.
        The deprecation still has some effect despite the identity mapping.

    """
    return arg1 + new_arg2


def _self_ref_target(a: int = 1, b: int = 2) -> int:
    """Target function for self-reference test."""
    return a + b


@deprecated(target=_self_ref_target, deprecated_in="0.1", remove_in="0.5", args_mapping={"old_arg": "new_arg"})
def self_referencing_deprecation(old_arg: int = 1, new_arg: int = 2) -> int:
    """Self-referencing deprecation (wrapper targets itself).

    The deprecation is created using a workaround:
    We define a function that targets another function, but then we manually
    set the target to itself after decoration.

    Examples:
        The `__deprecated__` target is manually set to the wrapper itself after
        decoration, creating a circular reference that makes the deprecation meaningless.

    """
    return void(old_arg, new_arg)


# Manually update the __deprecated__ attribute to make it self-referencing
self_ref_typed = cast(_DeprecatedCallable, self_referencing_deprecation)
deprecated_info = self_ref_typed.__deprecated__
self_ref_typed.__deprecated__ = replace(deprecated_info, target=self_referencing_deprecation)


# ---------------------------------------------------------------------------
# TargetMode misconfiguration fixtures
# Construction-time UserWarnings/FutureWarnings are expected; they are suppressed via the
# targeted `filterwarnings` entries in pyproject.toml (scoped by message + `deprecate._types`
# module) so they do not pollute test output at module import time.
# ---------------------------------------------------------------------------


@deprecated(target=False, deprecated_in="0.1", remove_in="0.5")
def target_false_deprecation(x: int = 1) -> int:
    """``target=False`` is not valid — audit should flag as misconfigured_target."""
    return x


@deprecated(
    target=TargetMode.NOTIFY,
    deprecated_in="0.1",
    remove_in="0.5",
    args_mapping={"old_x": "x"},
)
def whole_with_mapping_deprecation(x: int = 1) -> int:
    """NOTIFY + args_mapping — mapping ignored; emits UserWarning at decoration time.

    TypeError planned in v1.0; audit flags misconfigured_target.

    """
    return x


@deprecated(target=TargetMode.ARGS_REMAP, deprecated_in="0.1", remove_in="0.5")
def args_only_no_mapping_deprecation(x: int = 1) -> int:
    """ARGS_REMAP without args_mapping — no-op; audit flags misconfigured_target."""
    return x


@deprecated(
    target=TargetMode.NOTIFY,
    deprecated_in="0.1",
    remove_in="0.5",
    args_extra={"bias": 1},
)
def whole_with_args_extra_deprecation(x: int = 1) -> int:
    """NOTIFY + args_extra — extra ignored; emits UserWarning at decoration time.

    TypeError planned in v1.0; audit flags misconfigured_target.

    """
    return x


@deprecated(target=TargetMode.NOTIFY, deprecated_in="0.1", remove_in="0.5")
def whole_clean_deprecation(x: int = 1) -> int:
    """NOTIFY with no args_mapping — correctly configured; audit should not flag."""
    return x


@deprecated(
    target=TargetMode.ARGS_REMAP,
    deprecated_in="0.1",
    remove_in="0.5",
    args_mapping={"old_x": "x"},
)
def args_only_clean_deprecation(x: int = 1) -> int:
    """ARGS_REMAP with args_mapping — correctly configured; audit should not flag."""
    return x


# ---------------------------------------------------------------------------
# Fix 3 regression fixtures — class branch of @deprecated must normalise legacy
# sentinels (None / True / False) before delegating to deprecated_class. Two
# misconfig scenarios are covered:
#   * target=None with args_mapping (NOTIFY-intent) → must emit UserWarning at
#     decoration time and strip args_mapping (proxy must not auto-promote to
#     ARGS_REMAP).
#   * target=False (invalid) with or without args_mapping → must surface as
#     misconfigured=True so audit reports it.
#
# Construction-time UserWarnings are expected — suppress during module load and
# expose factories so tests can re-invoke and assert on warnings explicitly.
# ---------------------------------------------------------------------------


def make_class_target_none_with_args_mapping() -> type:
    """Build a class deprecation with legacy ``target=None`` + ``args_mapping``.

    Fix 3a: the class branch must normalise ``None`` to :class:`TargetMode.NOTIFY`,
    emit the NOTIFY+args_mapping misconfig UserWarning, and strip ``args_mapping``
    before delegating to :func:`~deprecate.proxy.deprecated_class` so the proxy
    does not auto-promote ``None+args_mapping`` to :class:`TargetMode.ARGS_REMAP`.

    """

    @deprecated(
        target=None,
        deprecated_in="0.1",
        remove_in="0.5",
        args_mapping={"old": "new"},
        num_warns=-1,
    )
    class _NoneArgsMappingClass:
        """Source class — deprecated_class delegates to this body unchanged."""

        def __init__(self, new: int = 0) -> None:
            self.new = new

    return _NoneArgsMappingClass


def make_class_target_false() -> type:
    """Build a class deprecation with the invalid ``target=False`` sentinel.

    Fix 3b: ``target=False`` was previously collapsed to ``None`` before the
    proxy's ``misconfigured = target is False`` check ran, so the flag never
    fired. The fix tracks the raw sentinel separately and forces
    ``misconfigured=True`` on the resulting proxy's :class:`DeprecationConfig`.

    """

    @deprecated(target=False, deprecated_in="0.1", remove_in="0.5")
    class _FalseTargetClass:
        """Source class with no replacement target — invalid configuration."""

        def __init__(self, x: int = 0) -> None:
            self.x = x

    return _FalseTargetClass


@deprecated(target=TargetMode.NOTIFY, remove_in="1.0")
def no_version_deprecation(x: int = 1) -> int:
    """NOTIFY with no ``deprecated_in`` — audit should report ``empty_deprecated_in=True``.

    Examples:
        Developer omits the ``deprecated_in`` version; audit tooling detects the gap.

    """
    return x


def make_class_target_false_with_args_mapping() -> type:
    """Build a class deprecation with ``target=False`` and ``args_mapping``.

    Fix 3b: combining the invalid ``target=False`` sentinel with ``args_mapping``
    must still surface ``misconfigured=True`` to audit.

    """

    @deprecated(
        target=False,
        deprecated_in="0.1",
        remove_in="0.5",
        args_mapping={"old": "new"},
    )
    class _FalseTargetWithArgsMappingClass:
        """Source class with invalid target sentinel and args_mapping."""

        def __init__(self, new: int = 0) -> None:
            self.new = new

    return _FalseTargetWithArgsMappingClass
