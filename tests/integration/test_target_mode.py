"""Demo / contract tests for TargetMode enum and misconfiguration guards.

These tests were written BEFORE the implementation to crystallise the API contract.
They define the expected behaviour for:
- TargetMode.WHOLE  (replaces target=None)
- TargetMode.ARGS_ONLY (replaces target=True)
- Construction-time UserWarning for misconfigurations
- FutureWarning for legacy target=None / target=True sentinels
"""

import warnings

import pytest

from deprecate import TargetMode
from deprecate import TargetMode as top_level_TargetMode
from tests.collection_deprecate import (
    depr_target_mode_args_only_remaps_kwargs,
    depr_target_mode_args_only_silent_when_new_arg_passed,
    depr_target_mode_args_only_warns_when_old_arg_passed,
    depr_target_mode_args_only_with_args_extra_injects_kwargs,
    depr_target_mode_whole_executes_original_body,
    depr_target_mode_whole_warns_on_every_call,
    make_target_mode_args_only_without_args_mapping_warns,
    make_target_mode_target_false_warns,
    make_target_mode_target_none_sentinel_emits_future_warning,
    make_target_mode_target_true_sentinel_emits_future_warning,
    make_target_mode_whole_with_args_extra_warns,
    make_target_mode_whole_with_args_mapping_warns,
)
from tests.collection_targets import tracked_identity_calls

# ---------------------------------------------------------------------------
# TargetMode enum contract
# ---------------------------------------------------------------------------


def test_target_mode_members_exist() -> None:
    """TargetMode exposes exactly WHOLE and ARGS_ONLY members."""
    assert hasattr(TargetMode, "WHOLE")
    assert hasattr(TargetMode, "ARGS_ONLY")
    assert len(list(TargetMode)) == 2


def test_target_mode_importable_from_top_level() -> None:
    """TargetMode is part of the public API surface."""
    assert top_level_TargetMode is TargetMode


# ---------------------------------------------------------------------------
# TargetMode.WHOLE — warns every call; exec original body
# ---------------------------------------------------------------------------


def test_whole_warns_on_every_call() -> None:
    """WHOLE mode emits a FutureWarning on every call up to num_warns."""

    with pytest.warns(FutureWarning):
        result = depr_target_mode_whole_warns_on_every_call(3)

    assert result == 6


def test_whole_executes_original_body() -> None:
    """WHOLE mode executes the original function body unchanged."""
    tracked_identity_calls.clear()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        result = depr_target_mode_whole_executes_original_body(42)

    assert result == 42
    assert tracked_identity_calls == [42]


# ---------------------------------------------------------------------------
# TargetMode.ARGS_ONLY — warns only when old arg names passed; remaps kwargs
# ---------------------------------------------------------------------------


def test_args_only_warns_when_old_arg_passed() -> None:
    """ARGS_ONLY emits FutureWarning only when deprecated arg name is used."""

    with pytest.warns(FutureWarning):
        result = depr_target_mode_args_only_warns_when_old_arg_passed(old_x=5)

    assert result == 6


def test_args_only_silent_when_new_arg_passed() -> None:
    """ARGS_ONLY does NOT warn when caller uses the new argument name."""

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning would raise
        result = depr_target_mode_args_only_silent_when_new_arg_passed(x=5)

    assert result == 6


def test_args_only_remaps_kwargs() -> None:
    """ARGS_ONLY renames old arg to new name before calling the function body."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        result = depr_target_mode_args_only_remaps_kwargs(2.0, coef=3.0)

    assert result == 8.0


def test_args_only_with_args_extra_injects_kwargs() -> None:
    """ARGS_ONLY with args_extra merges extra kwargs after remapping."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        result = depr_target_mode_args_only_with_args_extra_injects_kwargs(old_x=5)

    assert result == 15


# ---------------------------------------------------------------------------
# Construction-time UserWarning for misconfigurations
# ---------------------------------------------------------------------------


def test_args_only_without_args_mapping_warns() -> None:
    """ARGS_ONLY without args_mapping has zero effect — should warn at decoration time."""
    with pytest.warns(UserWarning, match="args_mapping"):
        make_target_mode_args_only_without_args_mapping_warns()


def test_whole_with_args_mapping_warns() -> None:
    """WHOLE ignores args_mapping — should warn at decoration time."""
    with pytest.warns(UserWarning, match="args_mapping"):
        make_target_mode_whole_with_args_mapping_warns()


def test_whole_with_args_extra_warns() -> None:
    """WHOLE ignores args_extra — should warn at decoration time."""
    with pytest.warns(UserWarning, match="args_extra"):
        make_target_mode_whole_with_args_extra_warns()


def test_target_false_warns() -> None:
    """target=False is not valid — should warn at decoration time."""
    with pytest.warns(UserWarning, match="target=False' is not a valid deprecation mode"):
        make_target_mode_target_false_warns()


# ---------------------------------------------------------------------------
# Backward-compat: legacy sentinels emit FutureWarning at decoration time
# ---------------------------------------------------------------------------


def test_target_none_sentinel_emits_future_warning() -> None:
    """target=None triggers FutureWarning at decoration time; use TargetMode.WHOLE."""
    with pytest.warns(FutureWarning, match="TargetMode.WHOLE"):
        make_target_mode_target_none_sentinel_emits_future_warning()


def test_target_true_sentinel_emits_future_warning() -> None:
    """target=True triggers FutureWarning at decoration time; use TargetMode.ARGS_ONLY."""
    with pytest.warns(FutureWarning, match="TargetMode.ARGS_ONLY"):
        make_target_mode_target_true_sentinel_emits_future_warning()
