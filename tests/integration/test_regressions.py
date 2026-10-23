"""Regression tests for four bug fixes in the ``@deprecated`` call path.

Fix 1: Stale source default for the deprecated argument silently overrode the target callable's own default when the
caller supplied neither name.

Fix 2: ``args_extra`` was not injected for migrated callers using :class:`TargetMode.ARGS_REMAP` because the early-
return short-circuit fired before the merge step.

Fix 3: The class branch of ``@deprecated`` collapsed legacy ``target=None`` and ``target=False`` sentinels to ``None``
before delegating to :func:`deprecate.proxy.deprecated_class`, erasing both the NOTIFY-intent misconfig signal (3a) and
the ``misconfigured=True`` audit flag (3b).

Fix 4: The one-time call-time misconfiguration ``UserWarning`` checked and set its flag outside the state lock, so
concurrent first calls to a misconfigured wrapper could each emit it.

Three-layer rule: the deprecated wrappers and target callables live in ``tests/collection_deprecate.py``,
``tests/collection_misconfigured.py``, and ``tests/collection_targets.py``. This module only contains assertions.

"""

import concurrent.futures
import sys
import threading
import warnings
from collections.abc import Iterator
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
    make_notify_mapping_no_budget,
)

#: Threads released together into one wrapper by the concurrency regression test.
_RACE_THREADS = 8
#: Calls each of those threads makes.
_RACE_CALLS_PER_THREAD = 2000


@pytest.fixture
def _rapid_thread_switching() -> Iterator[None]:
    """Hand the GIL over after almost every bytecode so a check-then-set window is hit, then restore the interval."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(previous)


def _call_from_threads(func: Callable[[int], int], threads: int, calls: int) -> None:
    """Call *func* ``calls`` times from each of ``threads`` workers released together; re-raise any worker error."""
    barrier = threading.Barrier(threads, timeout=30)

    def hammer() -> None:
        """Wait for every worker, then call *func* back to back."""
        barrier.wait()
        for value in range(calls):
            func(value)

    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
        futures = [executor.submit(hammer) for _ in range(threads)]
        for future in futures:
            future.result()


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
    """Fix 3a — legacy ``target=None`` + ``args_mapping`` on a class auto-promotes to ARGS_REMAP.

    The NOTIFY+mapping auto-resolve behavior (2026-07-20) superseded the original Fix 3a behaviour: the
    legacy ``target=None`` sentinel still normalises to ``TargetMode.NOTIFY`` (unchanged FutureWarning), but
    the class branch no longer
    treats NOTIFY+``args_mapping`` as a misconfiguration to strip — it passes the mapping through so the
    proxy auto-promotes to :class:`TargetMode.ARGS_REMAP`, exactly like ``target=None`` has always done.
    """

    def test_legacy_sentinel_future_warning_still_fires(self) -> None:
        """The legacy ``target=None`` sentinel still emits its own FutureWarning at decoration time (unchanged)."""
        with pytest.warns(FutureWarning, match="TargetMode.NOTIFY"):
            make_class_target_none_with_args_mapping()

    def test_construction_does_not_emit_misconfig_warning(self) -> None:
        """NOTIFY+args_mapping no longer emits a misconfig UserWarning (since 2026-07-20)."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            make_class_target_none_with_args_mapping()

        misconfig_warns = [w for w in caught if "ignores `args_mapping`" in str(w.message)]
        assert not misconfig_warns

    def test_proxy_target_auto_promotes_to_args_remap(self) -> None:
        """Promote a legacy null target with a mapping to ``ARGS_REMAP``.

        Existing class declarations combined ``target=None`` with ``args_mapping``; migration must preserve their
        remapping behavior instead of silently reducing the proxy to warn-only mode.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls = make_class_target_none_with_args_mapping()
        dep = object.__getattribute__(cls, "__deprecation_config__")
        assert dep.target is TargetMode.ARGS_REMAP

    def test_args_mapping_preserved_on_proxy(self) -> None:
        """Preserve ``args_mapping`` on the auto-promoted proxy.

        The proxy contract requires the original rename map at call time and in audit metadata, so normalization
        cannot discard it while converting the legacy target sentinel.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls = make_class_target_none_with_args_mapping()
        dep = object.__getattribute__(cls, "__deprecation_config__")
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
        dep = object.__getattribute__(cls, "__deprecation_config__")
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
        dep = object.__getattribute__(cls, "__deprecation_config__")
        assert dep.misconfigured is True

    def test_audit_reports_misconfigured_with_args_mapping(self) -> None:
        """Audit surfaces misconfigured_target when target=False + args_mapping."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls = make_class_target_false_with_args_mapping()
        info = validate_deprecation_wrapper(cls)
        assert info.misconfigured_target is True


class TestFix4MisconfigNoticeUnderThreads:
    """Fix 4 — the one-time misconfiguration ``UserWarning`` stays one-time when first calls race."""

    @pytest.mark.usefixtures("_rapid_thread_switching")
    def test_concurrent_first_calls_warn_once(self) -> None:
        """Eight threads calling a fresh misconfigured wrapper 2000 times each get exactly one misconfiguration warning.

        A service starts a pool of worker threads that all call the same misconfigured deprecated function at once.
        Every first call sees the notice still due; without the lock each of them emits it, flooding the log with
        copies of a notice documented as one-time. The wrapper has ``num_warns=0``, so the misconfiguration notice is
        the only warning any call can emit. The workers start inside the capture so that, on interpreters where
        warning capture is context-local, every worker reports into it.
        """
        wrapper = make_notify_mapping_no_budget()
        with pytest.warns(UserWarning, match="invalid deprecation configuration") as record:
            _call_from_threads(wrapper, _RACE_THREADS, _RACE_CALLS_PER_THREAD)
        assert len(record) == 1
