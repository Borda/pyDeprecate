"""Demo / contract tests for TargetMode enum and misconfiguration guards.

These tests were written BEFORE the implementation to crystallise the API contract.
They define the expected behaviour for:
- TargetMode.NOTIFY  (replaces target=None)
- TargetMode.ARGS_REMAP (replaces target=True)
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
    make_target_mode_args_only_legacy_args_extra,
    make_target_mode_args_only_without_args_mapping_warns,
    make_target_mode_target_false_warns,
    make_target_mode_target_none_sentinel_emits_future_warning,
    make_target_mode_target_true_sentinel_emits_future_warning,
    make_target_mode_whole_with_args_extra_warns,
    make_target_mode_whole_with_args_mapping_warns,
)
from tests.collection_targets import tracked_identity_calls


class TestTargetModeContract:
    """TargetMode enum public contract — membership and importability."""

    def test_members_exist(self) -> None:
        """TargetMode exposes exactly NOTIFY and ARGS_REMAP members."""
        assert hasattr(TargetMode, "NOTIFY")
        assert hasattr(TargetMode, "ARGS_REMAP")
        assert len(list(TargetMode)) == 2

    def test_importable_from_top_level(self) -> None:
        """TargetMode is part of the public API surface."""
        assert top_level_TargetMode is TargetMode


class TestNotifyMode:
    """TargetMode.NOTIFY — warns every call; executes original body."""

    def test_warns_on_every_call(self) -> None:
        """NOTIFY mode emits a FutureWarning on every call up to num_warns."""
        with pytest.warns(FutureWarning):
            result = depr_target_mode_whole_warns_on_every_call(3)

        assert result == 6

    def test_executes_original_body(self) -> None:
        """NOTIFY mode executes the original function body unchanged."""
        tracked_identity_calls.clear()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = depr_target_mode_whole_executes_original_body(42)

        assert result == 42
        assert tracked_identity_calls == [42]

    def test_construction_warns_with_args_mapping(self) -> None:
        """NOTIFY ignores args_mapping — should warn at decoration time."""
        with pytest.warns(UserWarning, match="args_mapping"):
            make_target_mode_whole_with_args_mapping_warns()

    def test_construction_warns_with_args_extra(self) -> None:
        """NOTIFY ignores args_extra — should warn at decoration time."""
        with pytest.warns(UserWarning, match="args_extra"):
            make_target_mode_whole_with_args_extra_warns()

    def test_args_mapping_is_runtime_noop(self) -> None:
        """NOTIFY + args_mapping: function body executes correctly at runtime despite misconfiguration."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            fn = make_target_mode_whole_with_args_mapping_warns()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = fn(7)  # positional — Callable[[int], int] doesn't expose param name
        assert result == 7

    def test_args_extra_is_runtime_noop(self) -> None:
        """NOTIFY + args_extra: function body executes correctly at runtime despite misconfiguration."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            fn = make_target_mode_whole_with_args_extra_warns()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = fn(3)  # positional — Callable[[int], int] doesn't expose param name
        assert result == 3


class TestArgsRemapMode:
    """TargetMode.ARGS_REMAP — warns only when old arg names passed; remaps kwargs."""

    def test_warns_when_old_arg_passed(self) -> None:
        """ARGS_REMAP emits FutureWarning only when deprecated arg name is used."""
        with pytest.warns(FutureWarning):
            result = depr_target_mode_args_only_warns_when_old_arg_passed(old_x=5)

        assert result == 6

    def test_silent_when_new_arg_passed(self) -> None:
        """ARGS_REMAP does NOT warn when caller uses the new argument name."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning would raise
            result = depr_target_mode_args_only_silent_when_new_arg_passed(x=5)

        assert result == 6

    def test_remaps_kwargs(self) -> None:
        """ARGS_REMAP renames old arg to new name before calling the function body."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = depr_target_mode_args_only_remaps_kwargs(2.0, coef=3.0)

        assert result == 8.0

    def test_with_args_extra_injects_kwargs(self) -> None:
        """ARGS_REMAP with args_extra merges extra kwargs after remapping."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            result = depr_target_mode_args_only_with_args_extra_injects_kwargs(old_x=5)

        assert result == 15

    def test_construction_warns_without_args_mapping(self) -> None:
        """ARGS_REMAP without args_mapping has zero effect — should warn at decoration time."""
        with pytest.warns(UserWarning, match="args_mapping"):
            make_target_mode_args_only_without_args_mapping_warns()

    def test_positional_passthrough(self) -> None:
        """ARGS_REMAP with all-positional args bypasses mapping and emits no warning."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            result = depr_target_mode_args_only_remaps_kwargs(2.0, 3.0)
        assert result == 8.0

    @pytest.mark.parametrize(("old_x", "expected"), [(5, 15), (0, 10), (-3, 7)])
    def test_args_extra_equivalence_with_legacy(self, old_x: int, expected: int) -> None:
        """args_extra injects kwargs identically for TargetMode.ARGS_REMAP and legacy target=True."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            enum_result = depr_target_mode_args_only_with_args_extra_injects_kwargs(old_x=old_x)
            legacy_result = make_target_mode_args_only_legacy_args_extra()(old_x=old_x)  # type: ignore[call-arg]
        assert enum_result == legacy_result == expected


class TestLegacySentinels:
    """Backward-compat: legacy sentinels emit FutureWarning at decoration time."""

    def test_none_emits_future_warning(self) -> None:
        """target=None triggers FutureWarning at decoration time; use TargetMode.NOTIFY."""
        with pytest.warns(FutureWarning, match="TargetMode\\.NOTIFY"):
            make_target_mode_target_none_sentinel_emits_future_warning()

    def test_true_emits_future_warning(self) -> None:
        """target=True triggers FutureWarning at decoration time; use TargetMode.ARGS_REMAP."""
        with pytest.warns(FutureWarning, match="TargetMode.ARGS_REMAP"):
            make_target_mode_target_true_sentinel_emits_future_warning()

    def test_false_emits_user_warning(self) -> None:
        """target=False is not valid — should warn at decoration time."""
        with pytest.warns(UserWarning, match="target=False' is not a valid deprecation mode"):
            make_target_mode_target_false_warns()
