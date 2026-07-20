"""Regression tests for three bug fixes in `src/deprecate/deprecation.py`.

Fix 1: Stale source default for the deprecated argument silently overrode the target callable's own default when the
caller supplied neither name.

Fix 2: ``args_extra`` was not injected for migrated callers using :class:`TargetMode.ARGS_REMAP` because the early-
return short-circuit fired before the merge step.

Fix 3: The class branch of ``@deprecated`` collapsed legacy ``target=None`` and ``target=False`` sentinels to ``None``
before delegating to :func:`deprecate.proxy.deprecated_class`, erasing both the NOTIFY-intent misconfig signal (3a) and
the ``misconfigured=True`` audit flag (3b).

Three-layer rule: the deprecated wrappers and target callables live in ``tests/collection_deprecate.py``,
``tests/collection_misconfigured.py``, and ``tests/collection_targets.py``. This module only contains assertions.

"""

import warnings
from typing import Callable, cast

import pytest

from deprecate import TargetMode, assert_no_warnings
from deprecate._types import _DeprecatedCallable
from deprecate.audit import validate_deprecation_wrapper
from tests.collection_depr_legacy import fn_remap_with_extra as legacy_fn_remap_with_extra
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

    @pytest.fixture(autouse=True)
    def _reset_deprecation_state(self) -> None:
        """Reset warning state before each test."""
        for func in (fn_remap_with_extra, legacy_fn_remap_with_extra):
            state = cast(_DeprecatedCallable, func)._state
            state.warned_calls = 0
            state.warned_args.clear()

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(fn_remap_with_extra, id="modern"),
            pytest.param(legacy_fn_remap_with_extra, id="legacy"),
        ],
    )
    def test_old_name_merges_args_extra(self, func: Callable[..., int]) -> None:
        """Caller using the deprecated name receives ``args_extra``."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = func(old_arg=5)
        assert result == 105

    @pytest.mark.parametrize(
        "func",
        [
            pytest.param(fn_remap_with_extra, id="modern"),
            pytest.param(legacy_fn_remap_with_extra, id="legacy"),
        ],
    )
    def test_new_name_merges_args_extra(self, func: Callable[..., int]) -> None:
        """Caller using the new name still receives ``args_extra`` (regression case)."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = func(new_arg=5)
        assert result == 105


class TestFix3aTargetNoneWithArgsMappingOnClass:
    """Fix 3a / option C — legacy ``target=None`` + ``args_mapping`` on a class auto-promotes to ARGS_REMAP.

    Option C (Q5, 2026-07-20) superseded the original Fix 3a behaviour: the legacy ``target=None`` sentinel
    still normalises to ``TargetMode.NOTIFY`` (unchanged FutureWarning), but the class branch no longer
    treats NOTIFY+``args_mapping`` as a misconfiguration to strip — it passes the mapping through so the
    proxy auto-promotes to :class:`TargetMode.ARGS_REMAP`, exactly like ``target=None`` has always done.
    """

    def test_legacy_sentinel_future_warning_still_fires(self) -> None:
        """The legacy ``target=None`` sentinel still emits its own FutureWarning at decoration time (unchanged)."""
        with pytest.warns(FutureWarning, match="TargetMode.NOTIFY"):
            make_class_target_none_with_args_mapping()

    def test_construction_does_not_emit_misconfig_warning(self) -> None:
        """NOTIFY+args_mapping no longer emits a misconfig UserWarning (option C, 2026-07-20)."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            make_class_target_none_with_args_mapping()

        misconfig_warns = [w for w in caught if "ignores `args_mapping`" in str(w.message)]
        assert not misconfig_warns

    def test_proxy_target_auto_promotes_to_args_remap(self) -> None:
        """Resulting proxy stores TargetMode.ARGS_REMAP (not NOTIFY) on __deprecated__."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls = make_class_target_none_with_args_mapping()
        dep = object.__getattribute__(cls, "__deprecated__")
        assert dep.target is TargetMode.ARGS_REMAP

    def test_args_mapping_preserved_on_proxy(self) -> None:
        """args_mapping is preserved (not stripped) so the proxy carries the mapping."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls = make_class_target_none_with_args_mapping()
        dep = object.__getattribute__(cls, "__deprecated__")
        assert dep.args_mapping == {"old": "new"}

    def test_instantiation_with_old_arg_warns_and_remaps(self) -> None:
        """ARGS_REMAP proxy emits FutureWarning and remaps ``old`` -> ``new`` when called with the old keyword."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls = make_class_target_none_with_args_mapping()
        with pytest.warns(FutureWarning):
            instance = cls(old=1)
        assert instance.new == 1

    def test_instantiation_with_new_arg_is_silent(self) -> None:
        """ARGS_REMAP proxy does not warn when called with the new keyword directly."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls = make_class_target_none_with_args_mapping()
        with assert_no_warnings(FutureWarning):
            instance = cls(new=1)
        assert instance.new == 1


class TestFix3bTargetFalseOnClass:
    """Fix 3b — invalid ``target=False`` must surface as ``misconfigured=True``."""

    def test_misconfigured_flag_set(self) -> None:
        """``target=False`` on a class normalises to NOTIFY but keeps ``misconfigured=True``."""
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
        """``target=False`` combined with args_mapping still surfaces ``misconfigured=True``."""
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
