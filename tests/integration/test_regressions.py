"""Regression tests for three bug fixes in `src/deprecate/deprecation.py`.

Fix 1: Stale source default for the deprecated argument silently overrode the
target callable's own default when the caller supplied neither name.

Fix 2: ``args_extra`` was not injected for migrated callers using
:class:`TargetMode.ARGS_REMAP` because the early-return short-circuit fired
before the merge step.

Fix 3: The class branch of ``@deprecated`` collapsed legacy ``target=None`` and
``target=False`` sentinels to ``None`` before delegating to
:func:`deprecate.proxy.deprecated_class`, erasing both the NOTIFY-intent
misconfig signal (3a) and the ``misconfigured=True`` audit flag (3b).

Three-layer rule: the deprecated wrappers and target callables live in
``tests/collection_deprecate.py``, ``tests/collection_misconfigured.py``, and
``tests/collection_targets.py``. This module only contains assertions.
"""

import warnings

import pytest

from deprecate import TargetMode
from deprecate.audit import validate_deprecation_wrapper
from tests.collection_deprecate import fn_old_default, fn_remap_with_extra
from tests.collection_misconfigured import (
    make_class_target_false,
    make_class_target_false_with_args_mapping,
    make_class_target_none_with_args_mapping,
)


class TestFix1StaleSourceDefault:
    """Fix 1 — source default for the deprecated arg must not shadow the target's default."""

    def test_no_args_uses_target_default(self) -> None:
        """When caller supplies neither name, target's own default wins."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = fn_old_default()
        assert result == 99

    def test_old_arg_renamed_to_new(self) -> None:
        """Caller using the deprecated name still has the rename applied."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = fn_old_default(old_arg=5)
        assert result == 5

    def test_new_arg_passed_directly(self) -> None:
        """Caller using the new name passes through to the target unchanged."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = fn_old_default(new_arg=7)
        assert result == 7


class TestFix2ArgsExtraOnArgsRemap:
    """Fix 2 — ``args_extra`` must be injected on ARGS_REMAP regardless of caller arg name."""

    def test_old_name_merges_args_extra(self) -> None:
        """Caller using the deprecated name receives ``args_extra``."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = fn_remap_with_extra(old_arg=5)
        assert result == 105

    def test_new_name_merges_args_extra(self) -> None:
        """Caller using the new name still receives ``args_extra`` (regression case)."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = fn_remap_with_extra(new_arg=5)
        assert result == 105


class TestFix3aTargetNoneWithArgsMappingOnClass:
    """Fix 3a — ``target=None`` + ``args_mapping`` on a class must not auto-promote to ARGS_REMAP."""

    def test_construction_emits_user_warning(self) -> None:
        """NOTIFY+args_mapping misconfig UserWarning must fire at decoration time."""
        with pytest.warns(UserWarning, match="args_mapping"):
            make_class_target_none_with_args_mapping()

    def test_proxy_target_normalised_to_notify(self) -> None:
        """Resulting proxy stores TargetMode.NOTIFY (not ARGS_REMAP) on __deprecated__."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls = make_class_target_none_with_args_mapping()
        dep = object.__getattribute__(cls, "__deprecated__")
        assert dep.target is TargetMode.NOTIFY

    def test_args_mapping_stripped_from_proxy(self) -> None:
        """args_mapping is stripped before delegation so the proxy carries no mapping."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls = make_class_target_none_with_args_mapping()
        dep = object.__getattribute__(cls, "__deprecated__")
        assert dep.args_mapping is None

    def test_instantiation_emits_future_warning(self) -> None:
        """NOTIFY proxy emits FutureWarning on every instantiation."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            cls = make_class_target_none_with_args_mapping()
        with pytest.warns(FutureWarning):
            cls(new=1)


class TestFix3bTargetFalseOnClass:
    """Fix 3b — invalid ``target=False`` must surface as ``misconfigured=True``."""

    def test_misconfigured_flag_set(self) -> None:
        """target=False on a class normalises to NOTIFY but keeps misconfigured=True."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls = make_class_target_false()
        dep = object.__getattribute__(cls, "__deprecated__")
        assert dep.misconfigured is True

    def test_audit_reports_misconfigured_target(self) -> None:
        """validate_deprecation_wrapper surfaces the misconfigured_target flag."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls = make_class_target_false()
        info = validate_deprecation_wrapper(cls)
        assert info.misconfigured_target is True

    def test_misconfigured_flag_set_with_args_mapping(self) -> None:
        """target=False combined with args_mapping still surfaces misconfigured=True."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls = make_class_target_false_with_args_mapping()
        dep = object.__getattribute__(cls, "__deprecated__")
        assert dep.misconfigured is True

    def test_audit_reports_misconfigured_with_args_mapping(self) -> None:
        """Audit surfaces misconfigured_target when target=False + args_mapping."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls = make_class_target_false_with_args_mapping()
        info = validate_deprecation_wrapper(cls)
        assert info.misconfigured_target is True
